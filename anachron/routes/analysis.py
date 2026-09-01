"""Deterministic, fail-closed analysis for the frozen Routes v1 protocol.

This module consumes recorded trajectories only.  It never calls a model and it
does not select, replace, or infer missing observations.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import math
import random
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anachron.routes.manifest import (
    ManifestValidationError,
    canonical_json_sha256,
    validate_manifest,
)
from anachron.routes.retrieval import retrieve
from anachron.routes.runner import source_code_sha256
from anachron.routes.schema import (
    ContractValidationError,
    validate_experiment_records,
)
from anachron.routes.scoring import score_response


class AnalysisValidationError(ValueError):
    """Raised when recorded data cannot support the frozen analysis."""


_LABELS = (
    "pre_only",
    "post_only",
    "mixed",
    "abstain_or_other",
    "invalid_output",
)
_FORCED_CONDITIONS = ("strict", "misdated")
_RUNNER_RECORD_FIELDS = {
    "schema_version", "record_type", "run_id", "trajectory_id", "attempt", "study_phase",
    "item_id", "topic", "cutoff_year", "model_id", "model_digest", "seed", "condition",
    "started_at", "completed_at", "status", "contract_sha256", "manifest_sha256",
    "sampling_frame_sha256", "code_sha256", "request", "retrieval", "response", "error",
}
_AUDIT_LABEL_FIELDS = {
    "schema_version", "record_type", "audit_id", "labeler_id", "labeled_at", "answer_label",
    "response_sha256",
}


def _utc_timestamp(value: Any, path: str) -> None:
    if not isinstance(value, str):
        raise AnalysisValidationError(f"{path} must be a canonical UTC timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise AnalysisValidationError(f"{path} must be a canonical UTC timestamp") from error
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise AnalysisValidationError(f"{path} must be a canonical UTC timestamp")


def _identity(record: dict[str, Any]) -> tuple[str, int, str, str, int, str]:
    return (
        record["topic"],
        record["cutoff_year"],
        record["model_id"],
        record["condition"],
        record["seed"],
        record["study_phase"],
    )


def _audit_id(
    trajectory_id: str, run_id: str, attempt: int, response_sha256: str | None
) -> str:
    material = f"{trajectory_id}:{run_id}:{attempt}:{response_sha256}".encode()
    return "routes-v1-" + hashlib.sha256(material).hexdigest()


def _terminal_attempts(records: list[dict[str, Any]]) -> dict[tuple[str, int, str, str, int, str], int]:
    responses = {
        (record["run_id"], record["attempt"]): record
        for record in records
        if record["record_type"] == "response"
    }
    grouped: dict[tuple[str, int, str, str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for response in responses.values():
        grouped[_identity(response)].append(response)
    terminal: dict[tuple[str, int, str, str, int, str], int] = {}
    for identity, attempts in grouped.items():
        attempts.sort(key=lambda record: record["attempt"])
        first = attempts[0]
        if first["attempt"] != 1:
            raise AnalysisValidationError("trajectory is missing its first attempt")
        if first["status"] == "transport_failure_before_response":
            if len(attempts) != 2 or attempts[1]["attempt"] != 2:
                raise AnalysisValidationError("transport failure lacks its required sole retry")
            terminal[identity] = 2
        elif len(attempts) != 1:
            raise AnalysisValidationError("non-transport result was retried")
        else:
            terminal[identity] = 1
    return terminal


def _expected_identities(
    contract: dict[str, Any], manifest: dict[str, Any], phase: str
) -> set[tuple[str, int, str, str, int, str]]:
    if phase not in {"pilot", "full"}:
        raise AnalysisValidationError("phase must be pilot or full")
    models = contract["sampling"][f"{phase}_models"]
    pairs = [pair for pair in manifest["pairs"] if pair["study_phase"] == phase]
    expected: set[tuple[str, int, str, str, int, str]] = set()
    for pair in pairs:
        for model_id in models:
            for condition in contract["conditions"]:
                for seed in contract["execution"]["seeds"]:
                    expected.add(
                        (
                            pair["topic"],
                            pair["cutoff_year"],
                            model_id,
                            condition,
                            seed,
                            phase,
                        )
                    )
    return expected


def _labels_by_terminal(
    records: list[dict[str, Any]], terminal: dict[tuple[str, int, str, str, int, str], int]
) -> dict[tuple[str, int, str, str, int, str], list[dict[str, Any]]]:
    responses = {
        (record["run_id"], record["attempt"]): record
        for record in records
        if record["record_type"] == "response"
    }
    labels: dict[tuple[str, int, str, str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record["record_type"] != "label":
            continue
        response = responses[(record["run_id"], record["attempt"])]
        identity = _identity(response)
        if terminal[identity] == record["attempt"]:
            labels[identity].append(record)
    return labels


def _validate_complete_phase(
    contract: dict[str, Any],
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    phase: str,
    *,
    require_labels: bool,
) -> tuple[
    dict[tuple[str, int, str, str, int, str], int],
    dict[tuple[str, int, str, str, int, str], list[dict[str, Any]]],
]:
    try:
        validate_experiment_records(records, contract)
    except ContractValidationError as error:
        raise AnalysisValidationError(f"experiment records are invalid: {error}") from error
    phase_records = [record for record in records if record["study_phase"] == phase]
    if len(phase_records) != len(records):
        raise AnalysisValidationError("analysis input must contain exactly one study phase")
    terminal = _terminal_attempts(phase_records)
    expected = _expected_identities(contract, manifest, phase)
    actual = set(terminal)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise AnalysisValidationError(
            f"trajectory schedule differs from manifest; missing={missing}, extra={extra}"
        )
    labels = _labels_by_terminal(phase_records, terminal)
    if require_labels:
        labelers: set[str] = set()
        for identity in sorted(expected):
            labels_for_run = labels.get(identity, [])
            if len(labels_for_run) != 2:
                raise AnalysisValidationError("each terminal trajectory requires exactly two labels")
            ids = {record["labeler_id"] for record in labels_for_run}
            if len(ids) != 2:
                raise AnalysisValidationError("a trajectory has duplicate rater labels")
            labelers.update(ids)
        if len(labelers) != 2:
            raise AnalysisValidationError("analysis requires exactly two raters across the phase")
    elif any(labels.values()):
        raise AnalysisValidationError("audit-packet input must not contain partially supplied labels")
    return terminal, labels


def _kappa(labels: Iterable[tuple[str, str]]) -> float:
    pairs = list(labels)
    if not pairs:
        raise AnalysisValidationError("kappa requires at least one paired label")
    if any(left not in _LABELS or right not in _LABELS for left, right in pairs):
        raise AnalysisValidationError("kappa received an invalid frozen label")
    observed = sum(left == right for left, right in pairs) / len(pairs)
    left_counts = {label: sum(left == label for left, _ in pairs) for label in _LABELS}
    right_counts = {label: sum(right == label for _, right in pairs) for label in _LABELS}
    expected = sum(
        (left_counts[label] / len(pairs)) * (right_counts[label] / len(pairs))
        for label in _LABELS
    )
    if math.isclose(expected, 1.0):
        if math.isclose(observed, 1.0):
            return 1.0
        raise AnalysisValidationError("kappa is undefined when expected agreement is one")
    return (observed - expected) / (1.0 - expected)


def cohen_kappa(labels: Iterable[tuple[str, str]]) -> float:
    """Compute strict two-rater Cohen's kappa over the five frozen labels."""
    return _kappa(labels)


def _quantile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values:
        raise AnalysisValidationError("bootstrap produced no values")
    return sorted_values[math.floor((len(sorted_values) - 1) * probability)]


def paired_topic_cluster_bootstrap(
    topic_effects: dict[str, float], *, resamples: int, seed: int
) -> dict[str, Any]:
    """Return the frozen percentile interval by resampling topic clusters only."""
    if not topic_effects:
        raise AnalysisValidationError("paired bootstrap requires at least one topic")
    if resamples != 10_000:
        raise AnalysisValidationError("Routes v1 requires exactly 10000 bootstrap resamples")
    topics = sorted(topic_effects)
    values = [topic_effects[topic] for topic in topics]
    if any(not -1.0 <= value <= 1.0 or not math.isfinite(value) for value in values):
        raise AnalysisValidationError("topic effects must be finite paired differences in [-1, 1]")
    randomizer = random.Random(seed)
    samples = []
    for _ in range(resamples):
        samples.append(sum(values[randomizer.randrange(len(values))] for _ in topics) / len(topics))
    samples.sort()
    return {
        "method": "paired_topic_cluster_bootstrap",
        "analysis_seed": seed,
        "resamples": resamples,
        "confidence_level": 0.95,
        "lower": _quantile(samples, 0.025),
        "upper": _quantile(samples, 0.975),
    }


def _rate(values: Iterable[bool]) -> float:
    observations = list(values)
    if not observations:
        raise AnalysisValidationError("rate denominator is empty")
    return sum(observations) / len(observations)


def _rate_row(
    rows: list[dict[str, Any]], *, condition: str, model_id: str, phase: str
) -> dict[str, Any]:
    selected = [row for row in rows if row["condition"] == condition and row["model_id"] == model_id]
    if not selected:
        raise AnalysisValidationError("requested condition/model has no trajectories")
    return {
        "study_phase": phase,
        "model_id": model_id,
        "condition": condition,
        "n": len(selected),
        "post_only_rate": _rate(row["answer_label"] == "post_only" for row in selected),
        "invalid_output_fraction": _rate(
            row["answer_label"] == "invalid_output" for row in selected
        ),
        "post_citation_rate": _rate(row["post_citation"] for row in selected),
        "trace_backed_transmission_rate": _rate(
            row["trace_backed_transmission"] for row in selected
        ),
        "off_trace_transmission_rate": _rate(
            row["off_trace_transmission"] for row in selected
        ),
        "trace_validity_rate": _rate(row["trace_valid"] for row in selected),
    }


def _topic_effects(rows: list[dict[str, Any]], model_id: str | None) -> dict[str, float]:
    filtered = [row for row in rows if model_id is None or row["model_id"] == model_id]
    by_topic: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in filtered:
        if row["condition"] in _FORCED_CONDITIONS:
            by_topic[row["topic"]][row["condition"]].append(row)
    effects: dict[str, float] = {}
    for topic, conditions in by_topic.items():
        if set(conditions) != set(_FORCED_CONDITIONS):
            raise AnalysisValidationError("a topic lacks a paired primary condition")
        strict = _rate(row["answer_label"] == "post_only" for row in conditions["strict"])
        misdated = _rate(row["answer_label"] == "post_only" for row in conditions["misdated"])
        effects[topic] = misdated - strict
    return effects


def _effect_summary(rows: list[dict[str, Any]], contract: dict[str, Any], model_id: str | None) -> dict[str, Any]:
    effects = _topic_effects(rows, model_id)
    interval = paired_topic_cluster_bootstrap(
        effects,
        resamples=contract["analysis"]["interval"]["resamples"],
        seed=contract["analysis"]["analysis_seed"],
    )
    return {
        "model_id": model_id or "pooled_confirmatory_models",
        "topic_clusters": len(effects),
        "point_estimate": sum(effects.values()) / len(effects),
        "interval": interval,
    }


def _descriptive_strength_rows(rows: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["semantic_strength"], row["condition"])].append(row)
    return [
        {
            "study_phase": phase,
            "semantic_strength": strength,
            "condition": condition,
            "n": len(group),
            "post_only_rate": _rate(row["answer_label"] == "post_only" for row in group),
            "descriptive_only": True,
        }
        for (strength, condition), group in sorted(grouped.items())
    ]


def _secondary_effects(rows: list[dict[str, Any]], models: list[str], phase: str) -> list[dict[str, Any]]:
    outcomes = (
        "post_citation",
        "trace_backed_transmission",
        "off_trace_transmission",
    )
    effects = []
    for model_id in models:
        for outcome in outcomes:
            strict = _rate(
                row[outcome]
                for row in rows
                if row["model_id"] == model_id and row["condition"] == "strict"
            )
            misdated = _rate(
                row[outcome]
                for row in rows
                if row["model_id"] == model_id and row["condition"] == "misdated"
            )
            effects.append(
                {
                    "study_phase": phase,
                    "model_id": model_id,
                    "outcome": outcome,
                    "contrast": "misdated_minus_strict",
                    "point_estimate": misdated - strict,
                    "descriptive_only": True,
                }
            )
    return effects


def _validate_trace_against_pair(
    trace: dict[str, Any], pair: dict[str, Any], condition: str
) -> None:
    if condition == "no_tool":
        if trace["calls"] or trace["trace_valid"]:
            raise AnalysisValidationError("no_tool trace does not remain tool-free")
        return
    if trace["status"] != "ok":
        if trace["trace_valid"] or trace["calls"]:
            raise AnalysisValidationError("failed forced-retrieval trace has route evidence")
        return
    source = pair["pre"] if condition == "strict" else pair["post"]
    call = trace["calls"][0]
    if (
        not trace["trace_valid"]
        or call["revision_timestamp"] != source["timestamp"]
        or call["revision_url"] != source["revision_url"]
    ):
        raise AnalysisValidationError("trace does not bind the manifest-selected revision")


def _trajectory_rows(
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    phase: str,
) -> tuple[list[dict[str, Any]], float, list[dict[str, Any]]]:
    terminal, labels = _validate_complete_phase(
        contract, manifest, records, phase, require_labels=True
    )
    responses = {
        (record["run_id"], record["attempt"]): record
        for record in records
        if record["record_type"] == "response"
    }
    responses_by_identity = {
        (_identity(record), record["attempt"]): record for record in responses.values()
    }
    traces = {
        (record["run_id"], record["attempt"]): record
        for record in records
        if record["record_type"] == "trace"
    }
    pairs = {(pair["topic"], pair["cutoff_year"]): pair for pair in manifest["pairs"]}
    all_label_pairs: list[tuple[str, str]] = []
    disagreements: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for identity in sorted(terminal):
        topic, cutoff_year, model_id, condition, seed, _ = identity
        attempt = terminal[identity]
        response = responses_by_identity[(identity, attempt)]
        trace = traces[(response["run_id"], attempt)]
        label_records = labels[identity]
        ordered_labels = sorted(label_records, key=lambda item: item["labeler_id"])
        all_label_pairs.append(tuple(record["answer_label"] for record in ordered_labels))
        pair = pairs.get((topic, cutoff_year))
        if pair is None:
            raise AnalysisValidationError("trajectory does not bind a source-valid manifest pair")
        _validate_trace_against_pair(trace, pair, condition)
        retrieval = retrieve(
            manifest,
            contract,
            sampling_frame,
            item_id=pair["item_id"],
            condition=condition,
            retrieved_at=response["completed_at"],
        )
        score = score_response(pair, retrieval, response["response_text"])
        if response["status"] != "ok":
            score = {
                "post_citation": False,
                "trace_backed_transmission": False,
                "off_trace_transmission": False,
            }
        label = score["answer_label"]
        if any(record["answer_label"] != label for record in ordered_labels):
            disagreements.append(
                {
                    "audit_id": _audit_id(response["run_id"], response["run_id"], attempt, response["response_sha256"]),
                    "program_label": label,
                    "rater_a_label": ordered_labels[0]["answer_label"],
                    "rater_b_label": ordered_labels[1]["answer_label"],
                }
            )
        rows.append(
            {
                "topic": topic,
                "cutoff_year": cutoff_year,
                "model_id": model_id,
                "condition": condition,
                "seed": seed,
                "answer_label": label,
                "rater_a_label": ordered_labels[0]["answer_label"],
                "rater_b_label": ordered_labels[1]["answer_label"],
                "response_status": response["status"],
                "trace_valid": trace["trace_valid"],
                "post_citation": score["post_citation"],
                "trace_backed_transmission": score["trace_backed_transmission"],
                "off_trace_transmission": score["off_trace_transmission"],
                "semantic_strength": pair["semantic_strength"],
                "change_type": pair["change_type"],
            }
        )
    return rows, _kappa(all_label_pairs), disagreements


def _gate_summary(
    contract: dict[str, Any], phase: str, rows: list[dict[str, Any]], kappa: float, effect: dict[str, Any], source_valid_pairs: int, per_model: list[dict[str, Any]]) -> dict[str, bool]:
    forced = [row for row in rows if row["condition"] in _FORCED_CONDITIONS]
    if phase == "pilot":
        gates = contract["pilot_gates"]
        return {
            "minimum_source_valid_pairs": source_valid_pairs >= gates["minimum_source_valid_pairs"],
            "minimum_forced_retrieval_trace_validity": _rate(row["trace_valid"] for row in forced) >= gates["minimum_forced_retrieval_trace_validity"],
            "minimum_blinded_two_rater_kappa": kappa >= gates["minimum_blinded_two_rater_kappa"],
            "primary_point_estimate_must_be_positive": effect["point_estimate"] > 0.0,
            "primary_ci_lower_bound_must_be_positive": effect["interval"]["lower"] > 0.0,
        }
    gates = contract["full_gates"]
    return {
        "minimum_source_valid_pairs": source_valid_pairs >= gates["minimum_source_valid_pairs"],
        "maximum_invalid_output_fraction": _rate(
            row["answer_label"] == "invalid_output" for row in rows
        ) <= gates["maximum_invalid_output_fraction"],
        "per_model_primary_point_estimate_must_be_positive": all(
            item["point_estimate"] > 0.0 for item in per_model
        ),
        "pooled_primary_ci_lower_bound_minimum": effect["interval"]["lower"] >= gates[
            "pooled_primary_ci_lower_bound_minimum"
        ],
        "pilot_data_excluded_from_confirmatory": True,
    }


def _runner_response_text(record: dict[str, Any]) -> str:
    response = record["response"]
    if not isinstance(response, dict) or set(response) != {"sha256", "body_base64", "received_bytes"}:
        raise AnalysisValidationError("runner response receipt has an invalid schema")
    body = response["body_base64"]
    if not isinstance(body, str):
        raise AnalysisValidationError("runner response body is unavailable")
    try:
        raw = base64.b64decode(body, validate=True)
        envelope = json.loads(raw.decode("utf-8"))
        content = envelope["message"]["content"]
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise AnalysisValidationError("ok runner response does not contain an Ollama message") from error
    if not isinstance(content, str) or not content:
        raise AnalysisValidationError("ok runner response lacks model content")
    if response["received_bytes"] != len(raw) or response["sha256"] != "sha256:" + hashlib.sha256(raw).hexdigest():
        raise AnalysisValidationError("runner response hash or byte count does not bind its bytes")
    return content


def _runner_trajectories(
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    phase: str,
) -> dict[tuple[str, int, str, str, int, str], dict[str, Any]]:
    if not isinstance(records, list) or not records:
        raise AnalysisValidationError("runner ledger must be a non-empty record list")
    pairs = {pair["item_id"]: pair for pair in manifest["pairs"] if pair["study_phase"] == phase}
    expected = _expected_identities(contract, manifest, phase)
    expected_by_identity = {
        identity: next(
            pair for pair in pairs.values()
            if pair["topic"] == identity[0] and pair["cutoff_year"] == identity[1]
        )
        for identity in expected
    }
    expected_contract = canonical_json_sha256(contract)
    expected_manifest = canonical_json_sha256(manifest)
    expected_frame = canonical_json_sha256(sampling_frame)
    expected_code = source_code_sha256()
    grouped: dict[tuple[str, int, str, str, int, str], list[dict[str, Any]]] = defaultdict(list)
    seen_ids: set[tuple[str, int]] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != _RUNNER_RECORD_FIELDS:
            raise AnalysisValidationError("runner ledger record schema is invalid")
        if record["schema_version"] != "routes-v1-runner-record" or record["record_type"] != "trajectory_attempt":
            raise AnalysisValidationError("runner ledger record type is invalid")
        if record["status"] not in {
            "ok", "transport_failure_before_response", "timeout_after_dispatch",
            "malformed_response", "returned_error", "invalid_output",
        }:
            raise AnalysisValidationError("runner ledger status is invalid")
        if not isinstance(record["trajectory_id"], str) or not record["trajectory_id"]:
            raise AnalysisValidationError("runner trajectory_id is invalid")
        _utc_timestamp(record["started_at"], "runner.started_at")
        _utc_timestamp(record["completed_at"], "runner.completed_at")
        if record["study_phase"] != phase:
            raise AnalysisValidationError("runner analysis cannot pool phases")
        identity = _identity(record)
        if identity not in expected or record["item_id"] != expected_by_identity[identity]["item_id"]:
            raise AnalysisValidationError("runner ledger contains an undeclared trajectory")
        key = (record["trajectory_id"], record["attempt"])
        if key in seen_ids or record["attempt"] not in {1, 2}:
            raise AnalysisValidationError("runner ledger has a duplicate or invalid attempt")
        seen_ids.add(key)
        if (
            record["contract_sha256"] != expected_contract
            or record["manifest_sha256"] != expected_manifest
            or record["sampling_frame_sha256"] != expected_frame
            or record["code_sha256"] != expected_code
        ):
            raise AnalysisValidationError("runner ledger input binding drifted from analysis inputs")
        request = record["request"]
        if not isinstance(request, dict) or set(request) != {"sha256", "body"}:
            raise AnalysisValidationError("runner request receipt has an invalid schema")
        if request["sha256"] != canonical_json_sha256(request["body"]):
            raise AnalysisValidationError("runner request hash does not bind its body")
        response = record["response"]
        if not isinstance(response, dict) or set(response) != {"sha256", "body_base64", "received_bytes"}:
            raise AnalysisValidationError("runner response receipt has an invalid schema")
        if isinstance(response["received_bytes"], bool) or not isinstance(response["received_bytes"], int) or response["received_bytes"] < 0:
            raise AnalysisValidationError("runner response byte count is invalid")
        if response["body_base64"] is None:
            if response["sha256"] is not None or response["received_bytes"] != 0:
                raise AnalysisValidationError("empty runner response has inconsistent hash or byte count")
        elif isinstance(response["body_base64"], str):
            try:
                response_bytes = base64.b64decode(response["body_base64"], validate=True)
            except ValueError as error:
                raise AnalysisValidationError("runner response body is not base64") from error
            if (
                response["received_bytes"] != len(response_bytes)
                or response["sha256"] != "sha256:" + hashlib.sha256(response_bytes).hexdigest()
            ):
                raise AnalysisValidationError("runner response hash or byte count does not bind its bytes")
        else:
            raise AnalysisValidationError("runner response body is invalid")
        error = record["error"]
        if not isinstance(error, dict) or set(error) != {"kind", "message_sha256"}:
            raise AnalysisValidationError("runner error receipt has an invalid schema")
        if error["kind"] is None:
            if error["message_sha256"] is not None:
                raise AnalysisValidationError("empty runner error has a message hash")
        elif (
            not isinstance(error["kind"], str)
            or error["message_sha256"] != "sha256:" + hashlib.sha256(error["kind"].encode()).hexdigest()
        ):
            raise AnalysisValidationError("runner error receipt does not bind its kind")
        if record["status"] == "ok" and (response["body_base64"] is None or error["kind"] is not None):
            raise AnalysisValidationError("ok runner result has inconsistent response or error state")
        retrieval = record["retrieval"]
        if not isinstance(retrieval, dict) or set(retrieval) != {"sha256", "result"}:
            raise AnalysisValidationError("runner retrieval receipt has an invalid schema")
        if retrieval["sha256"] != canonical_json_sha256(retrieval["result"]):
            raise AnalysisValidationError("runner retrieval receipt hash does not bind its result")
        try:
            validated = retrieve(
                manifest, contract, sampling_frame,
                item_id=record["item_id"], condition=record["condition"],
                retrieved_at=record["started_at"],
            )
        except Exception as error:
            raise AnalysisValidationError(f"runner route cannot be reconstructed: {error}") from error
        if retrieval["result"] != validated:
            raise AnalysisValidationError("runner retrieval receipt differs from the frozen route")
        grouped[identity].append(record)
    if set(grouped) != expected:
        missing = sorted(expected - set(grouped))
        extra = sorted(set(grouped) - expected)
        raise AnalysisValidationError(
            f"runner schedule differs from manifest; missing={missing}, extra={extra}"
        )
    terminal: dict[tuple[str, int, str, str, int, str], dict[str, Any]] = {}
    for identity, attempts in grouped.items():
        attempts.sort(key=lambda item: item["attempt"])
        if attempts[0]["attempt"] != 1 or len(attempts) > 2:
            raise AnalysisValidationError("runner trajectory has an invalid attempt sequence")
        if len(attempts) == 2:
            if attempts[0]["status"] != "transport_failure_before_response" or attempts[1]["attempt"] != 2:
                raise AnalysisValidationError("runner retry violates the frozen transport-only policy")
            terminal[identity] = attempts[1]
        elif attempts[0]["status"] == "transport_failure_before_response":
            raise AnalysisValidationError("runner trajectory is incomplete after transport failure")
        else:
            terminal[identity] = attempts[0]
    return terminal


def _runner_labels(
    terminal: dict[tuple[str, int, str, str, int, str], dict[str, Any]],
    labels: list[dict[str, Any]],
) -> dict[tuple[str, int, str, str, int, str], list[dict[str, Any]]]:
    by_audit_id = {
        _audit_id(
            record["trajectory_id"], record["run_id"], record["attempt"], record["response"]["sha256"]
        ): (identity, record)
        for identity, record in terminal.items()
    }
    grouped: dict[tuple[str, int, str, str, int, str], list[dict[str, Any]]] = defaultdict(list)
    seen_label_keys: set[tuple[str, str]] = set()
    for label in labels:
        if not isinstance(label, dict) or set(label) != _AUDIT_LABEL_FIELDS:
            raise AnalysisValidationError("audit label schema is invalid")
        if label["schema_version"] != "routes-v1-audit-label" or label["record_type"] != "audit_label":
            raise AnalysisValidationError("audit label record type is invalid")
        if label["answer_label"] not in _LABELS or not isinstance(label["labeler_id"], str) or not label["labeler_id"]:
            raise AnalysisValidationError("audit label has an invalid label or rater")
        _utc_timestamp(label["labeled_at"], "audit_label.labeled_at")
        label_key = (label["audit_id"], label["labeler_id"])
        if label_key in seen_label_keys:
            raise AnalysisValidationError("audit labels duplicate an audit_id/rater key")
        seen_label_keys.add(label_key)
        matched = by_audit_id.get(label["audit_id"])
        if matched is None:
            raise AnalysisValidationError("audit label does not bind a terminal trajectory")
        identity, response = matched
        if label["response_sha256"] != response["response"]["sha256"]:
            raise AnalysisValidationError("audit label does not bind the response hash")
        grouped[identity].append(label)
    raters: set[str] = set()
    for identity in terminal:
        records = grouped.get(identity, [])
        if len(records) != 2 or len({item["labeler_id"] for item in records}) != 2:
            raise AnalysisValidationError("each terminal trajectory requires two distinct audit labels")
        raters.update(item["labeler_id"] for item in records)
    if len(raters) != 2:
        raise AnalysisValidationError("audit labels must come from exactly two raters")
    return grouped


def analyze_runner_phase(
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
    manifest: dict[str, Any],
    runner_records: list[dict[str, Any]],
    audit_labels: list[dict[str, Any]],
    *,
    phase: str,
) -> dict[str, Any]:
    """Analyze the executor's append-only ledger with blinded audit labels."""
    try:
        validate_manifest(manifest, contract, sampling_frame)
    except (ContractValidationError, ManifestValidationError) as error:
        raise AnalysisValidationError(f"source manifest is invalid: {error}") from error
    terminal = _runner_trajectories(contract, sampling_frame, manifest, runner_records, phase)
    labels = _runner_labels(terminal, audit_labels)
    pairs = {(pair["topic"], pair["cutoff_year"]): pair for pair in manifest["pairs"]}
    rows: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    kappa_pairs = []
    for identity, record in sorted(terminal.items()):
        topic, cutoff_year, model_id, condition, seed, _ = identity
        pair = pairs[(topic, cutoff_year)]
        label_records = sorted(labels[identity], key=lambda item: item["labeler_id"])
        kappa_pairs.append((label_records[0]["answer_label"], label_records[1]["answer_label"]))
        answer_label = "invalid_output"
        score = {
            "post_citation": False,
            "trace_backed_transmission": False,
            "off_trace_transmission": False,
        }
        if record["status"] in {"ok", "invalid_output"}:
            response_text = _runner_response_text(record)
            score = score_response(pair, record["retrieval"]["result"], response_text)
            answer_label = score["answer_label"]
            if (
                record["status"] == "ok" and answer_label == "invalid_output"
            ) or (
                record["status"] == "invalid_output" and answer_label != "invalid_output"
            ):
                raise AnalysisValidationError("runner status disagrees with deterministic response scoring")
        if any(label["answer_label"] != answer_label for label in label_records):
            disagreements.append(
                {
                    "audit_id": _audit_id(
                        record["trajectory_id"], record["run_id"], record["attempt"],
                        record["response"]["sha256"],
                    ),
                    "program_label": answer_label,
                    "rater_a_label": label_records[0]["answer_label"],
                    "rater_b_label": label_records[1]["answer_label"],
                }
            )
        rows.append(
            {
                "topic": topic, "cutoff_year": cutoff_year, "model_id": model_id,
                "condition": condition, "seed": seed, "answer_label": answer_label,
                "rater_a_label": label_records[0]["answer_label"],
                "rater_b_label": label_records[1]["answer_label"],
                "response_status": record["status"],
                "trace_valid": condition in _FORCED_CONDITIONS,
                "post_citation": score["post_citation"],
                "trace_backed_transmission": score["trace_backed_transmission"],
                "off_trace_transmission": score["off_trace_transmission"],
                "semantic_strength": pair["semantic_strength"], "change_type": pair["change_type"],
            }
        )
    models = contract["sampling"][f"{phase}_models"]
    condition_rates = [
        _rate_row(rows, condition=condition, model_id=model_id, phase=phase)
        for model_id in models for condition in contract["conditions"]
    ]
    per_model = [_effect_summary(rows, contract, model_id) for model_id in models]
    primary = per_model[0] if phase == "pilot" else _effect_summary(rows, contract, None)
    gates = _gate_summary(
        contract, phase, rows, _kappa(kappa_pairs), primary,
        len([pair for pair in manifest["pairs"] if pair["study_phase"] == phase]), per_model,
    )
    return {
        "schema_version": "routes-v1-analysis", "study_phase": phase,
        "analysis_seed": contract["analysis"]["analysis_seed"],
        "source_valid_pairs": len([pair for pair in manifest["pairs"] if pair["study_phase"] == phase]),
        "trajectory_count": len(rows), "two_rater_cohen_kappa": _kappa(kappa_pairs),
        "condition_rates": condition_rates, "primary_effect": primary,
        "per_model_primary_effects": per_model,
        "secondary_effects": _secondary_effects(rows, models, phase),
        "descriptive_semantic_strength_rows": _descriptive_strength_rows(rows, phase),
        "gates": gates, "all_gates_pass": all(gates.values()), "trajectory_rows": rows,
        "human_program_disagreements": disagreements,
    }


def build_runner_blinded_audit_packet(
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
    manifest: dict[str, Any],
    runner_records: list[dict[str, Any]],
    *,
    phase: str,
) -> dict[str, Any]:
    """Create a blinded packet directly from a complete executor ledger."""
    try:
        validate_manifest(manifest, contract, sampling_frame)
    except (ContractValidationError, ManifestValidationError) as error:
        raise AnalysisValidationError(f"source manifest is invalid: {error}") from error
    terminal = _runner_trajectories(contract, sampling_frame, manifest, runner_records, phase)
    items = []
    for record in terminal.values():
        response = record["response"]
        pair = next(
            pair for pair in manifest["pairs"] if pair["item_id"] == record["item_id"]
        )
        body = response["body_base64"]
        if record["status"] == "ok":
            if not isinstance(body, str):
                raise AnalysisValidationError("ok runner response body is unavailable for blinded audit")
            response_text = _runner_response_text(record)
        else:
            response_text = ""
        items.append(
            {
                "audit_id": _audit_id(
                    record["trajectory_id"], record["run_id"], record["attempt"], response["sha256"]
                ),
                "response_text": response_text,
                "question": pair["question"],
                "pre_answer_aliases": pair["pre_answer_aliases"],
                "post_answer_aliases": pair["post_answer_aliases"],
                "allowed_labels": list(_LABELS),
                "response_sha256": response["sha256"],
            }
        )
    items.sort(key=lambda item: item["audit_id"])
    return {
        "schema_version": "routes-v1-blinded-audit-packet",
        "study_phase": phase,
        "labeling_instruction": "Assign exactly one frozen answer label from allowed_labels. Do not infer route, topic, condition, or model.",
        "items": items,
    }


def analyze_phase(
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    phase: str,
) -> dict[str, Any]:
    """Analyze one complete phase without pooling pilot into confirmatory data."""
    try:
        validate_manifest(manifest, contract, sampling_frame)
    except (ContractValidationError, ManifestValidationError) as error:
        raise AnalysisValidationError(f"source manifest is invalid: {error}") from error
    phase_pairs = [pair for pair in manifest["pairs"] if pair["study_phase"] == phase]
    if not phase_pairs:
        raise AnalysisValidationError("source manifest has no valid pairs for the requested phase")
    rows, kappa, disagreements = _trajectory_rows(
        contract, sampling_frame, manifest, records, phase
    )
    models = contract["sampling"][f"{phase}_models"]
    condition_rates = [
        _rate_row(rows, condition=condition, model_id=model_id, phase=phase)
        for model_id in models
        for condition in contract["conditions"]
    ]
    per_model = [_effect_summary(rows, contract, model_id) for model_id in models]
    pooled = _effect_summary(rows, contract, None)
    primary = per_model[0] if phase == "pilot" else pooled
    gates = _gate_summary(
        contract, phase, rows, kappa, primary, len(phase_pairs), per_model
    )
    return {
        "schema_version": "routes-v1-analysis",
        "study_phase": phase,
        "analysis_seed": contract["analysis"]["analysis_seed"],
        "source_valid_pairs": len(phase_pairs),
        "trajectory_count": len(rows),
        "two_rater_cohen_kappa": kappa,
        "condition_rates": condition_rates,
        "primary_effect": primary,
        "per_model_primary_effects": per_model,
        "secondary_effects": _secondary_effects(rows, models, phase),
        "descriptive_semantic_strength_rows": _descriptive_strength_rows(rows, phase),
        "gates": gates,
        "all_gates_pass": all(gates.values()),
        "trajectory_rows": rows,
        "human_program_disagreements": disagreements,
    }


def build_blinded_audit_packet(
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    phase: str,
) -> dict[str, Any]:
    """Create a condition-blinded, pre-label packet for exactly one phase."""
    try:
        validate_manifest(manifest, contract, sampling_frame)
    except (ContractValidationError, ManifestValidationError) as error:
        raise AnalysisValidationError(f"source manifest is invalid: {error}") from error
    terminal, _ = _validate_complete_phase(
        contract, manifest, records, phase, require_labels=False
    )
    responses = {
        (record["run_id"], record["attempt"]): record
        for record in records
        if record["record_type"] == "response"
    }
    items = []
    for identity, attempt in sorted(terminal.items()):
        response = next(
            record
            for record in responses.values()
            if _identity(record) == identity and record["attempt"] == attempt
        )
        opaque = _audit_id(
            response["run_id"], response["run_id"], attempt, response["response_sha256"]
        )
        items.append(
            {
                "audit_id": opaque,
                "response_text": response["response_text"],
                "allowed_labels": list(_LABELS),
            }
        )
    items.sort(key=lambda item: item["audit_id"])
    return {
        "schema_version": "routes-v1-blinded-audit-packet",
        "study_phase": phase,
        "labeling_instruction": "Assign exactly one frozen answer label from allowed_labels. Do not infer route, topic, condition, or model.",
        "items": items,
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise AnalysisValidationError("cannot write an empty CSV table")
    fields = list(rows[0])
    if any(list(row) != fields for row in rows):
        raise AnalysisValidationError("CSV rows do not share one exact schema")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_analysis_artifacts(
    output_directory: str | Path,
    result: dict[str, Any],
    audit_packet: dict[str, Any],
) -> None:
    """Write deterministic JSON/CSV artifacts after a successful analysis only."""
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    summary = {key: value for key, value in result.items() if key != "trajectory_rows"}
    _write_json(output / "summary.json", summary)
    _write_json(output / "blinded_audit_packet.json", audit_packet)
    _write_csv(output / "condition_rates.csv", result["condition_rates"])
    _write_csv(
        output / "primary_effects.csv",
        result["per_model_primary_effects"],
    )
    _write_csv(
        output / "semantic_strength_descriptive.csv",
        result["descriptive_semantic_strength_rows"],
    )
    _write_csv(output / "secondary_effects_descriptive.csv", result["secondary_effects"])
