"""Strict, dependency-free schemas for frozen Routes v1 study artifacts."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


class ContractValidationError(ValueError):
    """Raised when a Routes v1 contract or result record is not admissible."""


_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GIT_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_RESPONSE_STATUSES = frozenset(
    {
        "ok",
        "transport_failure_before_response",
        "timeout_after_dispatch",
        "malformed_response",
        "returned_error",
        "invalid_output",
    }
)
_NON_REPLACEABLE_STATUSES = frozenset(
    {
        "timeout_after_dispatch",
        "malformed_response",
        "returned_error",
        "invalid_output",
    }
)
_IDENTITY_FIELDS = (
    "schema_version",
    "record_type",
    "run_id",
    "topic",
    "cutoff_year",
    "model_id",
    "model_digest",
    "seed",
    "condition",
    "attempt",
    "study_phase",
)


def _require_mapping(value: Any, path: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError(f"{path} must be an object")
    actual = set(value)
    if actual != fields:
        missing = sorted(fields - actual)
        extra = sorted(actual - fields)
        raise ContractValidationError(f"{path} fields differ; missing={missing}, extra={extra}")
    return value


def _require_string(value: Any, path: str, *, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise ContractValidationError(f"{path} must be a non-empty string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ContractValidationError(f"{path} has invalid format")
    return value


def _require_int(value: Any, path: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractValidationError(f"{path} must be an integer")
    if minimum is not None and value < minimum:
        raise ContractValidationError(f"{path} must be >= {minimum}")
    return value


def _require_number(value: Any, path: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractValidationError(f"{path} must be a number")
    number = float(value)
    if not minimum <= number <= maximum:
        raise ContractValidationError(f"{path} must be in [{minimum}, {maximum}]")
    return number


def _require_utc_timestamp(value: Any, path: str) -> datetime:
    timestamp = _require_string(value, path, pattern=_UTC_TIMESTAMP)
    parsed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if parsed.isoformat().replace("+00:00", "Z") != timestamp:
        raise ContractValidationError(f"{path} must be a canonical UTC timestamp")
    return parsed


def _require_unique_strings(values: Any, path: str) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ContractValidationError(f"{path} must be a non-empty list")
    validated = [_require_string(value, f"{path}[{index}]") for index, value in enumerate(values)]
    if len(set(validated)) != len(validated):
        raise ContractValidationError(f"{path} contains duplicates")
    return validated


def _require_immutable_upstream(url: Any, revision: Any, path: str) -> None:
    pinned_url = _require_string(url, f"{path}.url")
    pinned_revision = _require_string(revision, f"{path}.revision", pattern=_GIT_REVISION)
    parsed = urlparse(pinned_url)
    if parsed.scheme != "https" or parsed.query or parsed.fragment:
        raise ContractValidationError(f"{path}.url must be an immutable HTTPS revision URL")
    if not parsed.netloc or not parsed.path.endswith(f"/{pinned_revision}"):
        raise ContractValidationError(f"{path}.url must end in its pinned revision")


def _require_exact_artifact_url(name: str, artifact_url: Any, revision: str) -> None:
    value = _require_string(artifact_url, f"contract.upstreams.{name}.artifact_url")
    expected = {
        "exante_github": (
            "https://raw.githubusercontent.com/yachuan/ExAnte/"
            f"{revision}/wiki/README.md"
        ),
        "exante_huggingface": (
            "https://huggingface.co/datasets/yachuanliu/ExAnte/resolve/"
            f"{revision}/exante_wiki.csv"
        ),
    }[name]
    if value != expected:
        raise ContractValidationError(
            f"contract.upstreams.{name}.artifact_url must be its exact immutable pin"
        )


def _validate_topics(value: Any, path: str, expected_count: int) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != expected_count:
        raise ContractValidationError(f"{path} must contain exactly {expected_count} topics")
    topics: list[dict[str, Any]] = []
    identities: set[tuple[str, int]] = set()
    titles: set[str] = set()
    for index, topic in enumerate(value):
        item = _require_mapping(topic, f"{path}[{index}]", {"title", "cutoff_year"})
        title = _require_string(item["title"], f"{path}[{index}].title")
        cutoff_year = _require_int(item["cutoff_year"], f"{path}[{index}].cutoff_year", minimum=1)
        if cutoff_year > 2025:
            raise ContractValidationError(f"{path}[{index}].cutoff_year must not be after 2025")
        identity = (title, cutoff_year)
        if identity in identities or title in titles:
            raise ContractValidationError(f"{path} contains a duplicate topic title or title-year pair")
        identities.add(identity)
        titles.add(title)
        topics.append(item)
    return topics


def validate_contract_document(document: Any) -> dict[str, Any]:
    """Validate and return an exact Routes v1 contract document.

    The validator intentionally rejects unknown fields. A new experimental
    degree of freedom must therefore be introduced through a reviewed contract
    version rather than silently accepted by a permissive parser.
    """
    contract = _require_mapping(
        document,
        "contract",
        {
            "schema_version",
            "created_at",
            "study",
            "upstreams",
            "models",
            "conditions",
            "source_selection",
            "execution",
            "sampling",
            "labels",
            "analysis",
            "pilot_gates",
            "full_gates",
        },
    )
    if contract["schema_version"] != "routes-v1":
        raise ContractValidationError("contract.schema_version must be routes-v1")
    _require_utc_timestamp(contract["created_at"], "contract.created_at")

    study = _require_mapping(contract["study"], "contract.study", {"id", "primary_estimand", "scope_statement"})
    if study["id"] != "anachron-routes-v1":
        raise ContractValidationError("contract.study.id must be anachron-routes-v1")
    _require_string(study["primary_estimand"], "contract.study.primary_estimand")
    scope = _require_string(study["scope_statement"], "contract.study.scope_statement")
    if "Wikipedia" not in scope or "no generality claim" not in scope:
        raise ContractValidationError("contract.study.scope_statement must limit claims to Wikipedia")

    upstreams = _require_mapping(contract["upstreams"], "contract.upstreams", {"exante_github", "exante_huggingface"})
    for name, upstream in upstreams.items():
        source = _require_mapping(
            upstream, f"contract.upstreams.{name}", {"url", "revision", "artifact_url"}
        )
        _require_immutable_upstream(source["url"], source["revision"], f"contract.upstreams.{name}")
        _require_exact_artifact_url(name, source["artifact_url"], source["revision"])

    if not isinstance(contract["models"], list) or len(contract["models"]) != 2:
        raise ContractValidationError("contract.models must contain exactly two models")
    models: dict[str, str] = {}
    for index, model in enumerate(contract["models"]):
        entry = _require_mapping(model, f"contract.models[{index}]", {"id", "digest"})
        model_id = _require_string(entry["id"], f"contract.models[{index}].id")
        digest = _require_string(entry["digest"], f"contract.models[{index}].digest", pattern=_SHA256)
        if model_id in models or digest in models.values():
            raise ContractValidationError("contract.models contains duplicate ids or digests")
        models[model_id] = digest

    conditions = _require_unique_strings(contract["conditions"], "contract.conditions")
    if conditions != ["no_tool", "strict", "misdated"]:
        raise ContractValidationError("contract.conditions must be no_tool, strict, misdated in that order")

    source_selection = _require_mapping(
        contract["source_selection"],
        "contract.source_selection",
        {
            "post_snapshot_horizon_days",
            "snippet_max_chars",
            "snippet_context_chars_each_side",
        },
    )
    if (
        _require_int(
            source_selection["post_snapshot_horizon_days"],
            "contract.source_selection.post_snapshot_horizon_days",
            minimum=1,
        )
        != 365
    ):
        raise ContractValidationError(
            "contract.source_selection.post_snapshot_horizon_days must be 365"
        )
    if (
        _require_int(
            source_selection["snippet_max_chars"],
            "contract.source_selection.snippet_max_chars",
            minimum=1,
        )
        != 4_000
    ):
        raise ContractValidationError(
            "contract.source_selection.snippet_max_chars must be 4000"
        )
    if (
        _require_int(
            source_selection["snippet_context_chars_each_side"],
            "contract.source_selection.snippet_context_chars_each_side",
            minimum=0,
        )
        != 800
    ):
        raise ContractValidationError(
            "contract.source_selection.snippet_context_chars_each_side must be 800"
        )

    execution = _require_mapping(contract["execution"], "contract.execution", {"seeds", "temperature", "num_predict", "think", "request_timeout_seconds", "retry_policy"})
    seeds = execution["seeds"]
    if not isinstance(seeds, list) or len(seeds) != 2:
        raise ContractValidationError("contract.execution.seeds must contain exactly two seeds")
    if len({_require_int(seed, f"contract.execution.seeds[{index}]", minimum=0) for index, seed in enumerate(seeds)}) != len(seeds):
        raise ContractValidationError("contract.execution.seeds contains duplicates")
    _require_number(execution["temperature"], "contract.execution.temperature", minimum=0.0, maximum=2.0)
    _require_int(execution["num_predict"], "contract.execution.num_predict", minimum=1)
    if execution["think"] is not False:
        raise ContractValidationError("contract.execution.think must be false")
    _require_int(execution["request_timeout_seconds"], "contract.execution.request_timeout_seconds", minimum=1)
    retry = _require_mapping(execution["retry_policy"], "contract.execution.retry_policy", {"max_retries", "retryable_status", "non_replaceable_statuses"})
    if _require_int(retry["max_retries"], "contract.execution.retry_policy.max_retries", minimum=0) != 1:
        raise ContractValidationError("contract.execution.retry_policy.max_retries must be 1")
    if retry["retryable_status"] != "transport_failure_before_response":
        raise ContractValidationError("contract.execution.retry_policy.retryable_status is invalid")
    if set(_require_unique_strings(retry["non_replaceable_statuses"], "contract.execution.retry_policy.non_replaceable_statuses")) != _NON_REPLACEABLE_STATUSES:
        raise ContractValidationError("contract.execution.retry_policy.non_replaceable_statuses is invalid")

    sampling = _require_mapping(contract["sampling"], "contract.sampling", {"pilot_topic_count", "extension_topic_count", "pilot_models", "full_models", "topics"})
    if _require_int(sampling["pilot_topic_count"], "contract.sampling.pilot_topic_count", minimum=1) != 20:
        raise ContractValidationError("contract.sampling.pilot_topic_count must be 20")
    if _require_int(sampling["extension_topic_count"], "contract.sampling.extension_topic_count", minimum=1) != 40:
        raise ContractValidationError("contract.sampling.extension_topic_count must be 40")
    pilot_models = _require_unique_strings(sampling["pilot_models"], "contract.sampling.pilot_models")
    full_models = _require_unique_strings(sampling["full_models"], "contract.sampling.full_models")
    if pilot_models != ["qwen2.5:7b"] or full_models != ["qwen2.5:7b", "qwen3:14b-q4_K_M"]:
        raise ContractValidationError("contract sampling model lists are invalid")
    if not set(full_models).issubset(models) or not set(pilot_models).issubset(models):
        raise ContractValidationError("contract sampling names an undeclared model")
    topics = _require_mapping(sampling["topics"], "contract.sampling.topics", {"pilot", "extension"})
    pilot_topics = _validate_topics(topics["pilot"], "contract.sampling.topics.pilot", 20)
    extension_topics = _validate_topics(topics["extension"], "contract.sampling.topics.extension", 40)
    if {topic["title"] for topic in pilot_topics} & {topic["title"] for topic in extension_topics}:
        raise ContractValidationError("contract sampling repeats a topic across pilot and extension")

    labels = _require_unique_strings(contract["labels"], "contract.labels")
    expected_labels = ["pre_only", "post_only", "mixed", "abstain_or_other", "invalid_output"]
    if labels != expected_labels:
        raise ContractValidationError("contract.labels is invalid")

    analysis = _require_mapping(contract["analysis"], "contract.analysis", {"analysis_seed", "cluster_unit", "primary_contrast", "interval", "secondary_outcomes"})
    if _require_int(analysis["analysis_seed"], "contract.analysis.analysis_seed", minimum=0) != 20_260_901:
        raise ContractValidationError("contract.analysis.analysis_seed must be 20260901")
    if analysis["cluster_unit"] != "topic":
        raise ContractValidationError("contract.analysis.cluster_unit must be topic")
    contrast = _require_mapping(analysis["primary_contrast"], "contract.analysis.primary_contrast", {"treatment", "control", "outcome"})
    if contrast != {"treatment": "misdated", "control": "strict", "outcome": "post_only"}:
        raise ContractValidationError("contract.analysis.primary_contrast is invalid")
    interval = _require_mapping(analysis["interval"], "contract.analysis.interval", {"method", "confidence_level", "resamples"})
    if interval["method"] != "paired_topic_cluster_bootstrap":
        raise ContractValidationError("contract.analysis.interval.method is invalid")
    if _require_number(interval["confidence_level"], "contract.analysis.interval.confidence_level", minimum=0.0, maximum=1.0) != 0.95:
        raise ContractValidationError("contract.analysis.interval.confidence_level must be 0.95")
    if _require_int(interval["resamples"], "contract.analysis.interval.resamples", minimum=1) != 10_000:
        raise ContractValidationError("contract.analysis.interval.resamples must be 10000")
    if _require_unique_strings(analysis["secondary_outcomes"], "contract.analysis.secondary_outcomes") != ["citation_effect", "trace_validity"]:
        raise ContractValidationError("contract.analysis.secondary_outcomes is invalid")

    gates = _require_mapping(contract["pilot_gates"], "contract.pilot_gates", {"minimum_source_valid_pairs", "minimum_forced_retrieval_trace_validity", "minimum_blinded_two_rater_kappa", "primary_point_estimate_must_be_positive", "primary_ci_lower_bound_must_be_positive", "secondary_outcomes_cannot_rescue_primary_failure"})
    if _require_int(gates["minimum_source_valid_pairs"], "contract.pilot_gates.minimum_source_valid_pairs", minimum=1) != 18:
        raise ContractValidationError("contract.pilot_gates.minimum_source_valid_pairs must be 18")
    if _require_number(gates["minimum_forced_retrieval_trace_validity"], "contract.pilot_gates.minimum_forced_retrieval_trace_validity", minimum=0.0, maximum=1.0) != 0.9:
        raise ContractValidationError("contract.pilot_gates.minimum_forced_retrieval_trace_validity must be 0.9")
    if _require_number(gates["minimum_blinded_two_rater_kappa"], "contract.pilot_gates.minimum_blinded_two_rater_kappa", minimum=-1.0, maximum=1.0) != 0.7:
        raise ContractValidationError("contract.pilot_gates.minimum_blinded_two_rater_kappa must be 0.7")
    for name in ("primary_point_estimate_must_be_positive", "primary_ci_lower_bound_must_be_positive", "secondary_outcomes_cannot_rescue_primary_failure"):
        if gates[name] is not True:
            raise ContractValidationError(f"contract.pilot_gates.{name} must be true")
    full_gates = _require_mapping(
        contract["full_gates"],
        "contract.full_gates",
        {
            "minimum_source_valid_pairs",
            "maximum_invalid_output_fraction",
            "per_model_primary_point_estimate_must_be_positive",
            "pooled_primary_ci_lower_bound_minimum",
            "pilot_data_excluded_from_confirmatory",
        },
    )
    if _require_int(full_gates["minimum_source_valid_pairs"], "contract.full_gates.minimum_source_valid_pairs", minimum=1) != 36:
        raise ContractValidationError("contract.full_gates.minimum_source_valid_pairs must be 36")
    if _require_number(full_gates["maximum_invalid_output_fraction"], "contract.full_gates.maximum_invalid_output_fraction", minimum=0.0, maximum=1.0) != 0.1:
        raise ContractValidationError("contract.full_gates.maximum_invalid_output_fraction must be 0.1")
    if full_gates["per_model_primary_point_estimate_must_be_positive"] is not True:
        raise ContractValidationError("contract.full_gates.per_model_primary_point_estimate_must_be_positive must be true")
    if _require_number(full_gates["pooled_primary_ci_lower_bound_minimum"], "contract.full_gates.pooled_primary_ci_lower_bound_minimum", minimum=-1.0, maximum=1.0) != 0.05:
        raise ContractValidationError("contract.full_gates.pooled_primary_ci_lower_bound_minimum must be 0.05")
    if full_gates["pilot_data_excluded_from_confirmatory"] is not True:
        raise ContractValidationError("contract.full_gates.pilot_data_excluded_from_confirmatory must be true")
    return contract


def load_contract(path: str | Path) -> dict[str, Any]:
    """Load one JSON contract and reject malformed or permissive content."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractValidationError(f"unable to load contract: {error}") from error
    return validate_contract_document(document)


def _contract_topics(contract: dict[str, Any], phase: str) -> dict[str, int]:
    topics = contract["sampling"]["topics"]
    groups = ("pilot",) if phase == "pilot" else ("extension",)
    return {
        entry["title"]: entry["cutoff_year"]
        for group in groups
        for entry in topics[group]
    }


def _validate_run_identity(
    record: Any, path: str, contract: dict[str, Any], record_type: str
) -> dict[str, Any]:
    value = _require_mapping(record, path, set(_IDENTITY_FIELDS))
    if value["schema_version"] != "routes-v1" or value["record_type"] != record_type:
        raise ContractValidationError(f"{path} has an invalid schema or record type")
    _require_string(value["run_id"], f"{path}.run_id", pattern=_RUN_ID)
    phase = _require_string(value["study_phase"], f"{path}.study_phase")
    if phase not in {"pilot", "full"}:
        raise ContractValidationError(f"{path}.study_phase must be pilot or full")
    topic = _require_string(value["topic"], f"{path}.topic")
    cutoff_year = _require_int(value["cutoff_year"], f"{path}.cutoff_year", minimum=1)
    if _contract_topics(contract, phase).get(topic) != cutoff_year:
        raise ContractValidationError(f"{path} topic and cutoff_year are not declared together")
    model_id = _require_string(value["model_id"], f"{path}.model_id")
    declared_models = {model["id"]: model["digest"] for model in contract["models"]}
    if declared_models.get(model_id) != value["model_digest"]:
        raise ContractValidationError(f"{path} model identity is not pinned by the contract")
    if model_id not in contract["sampling"][f"{phase}_models"]:
        raise ContractValidationError(f"{path}.model_id is not declared for its study phase")
    if value["seed"] not in contract["execution"]["seeds"]:
        raise ContractValidationError(f"{path}.seed is not declared")
    if value["condition"] not in contract["conditions"]:
        raise ContractValidationError(f"{path}.condition is not declared")
    attempt = _require_int(value["attempt"], f"{path}.attempt", minimum=1)
    if attempt > contract["execution"]["retry_policy"]["max_retries"] + 1:
        raise ContractValidationError(f"{path}.attempt exceeds the frozen retry limit")
    return value


def _validate_revision_url(url: Any, path: str, topic: str) -> None:
    value = _require_string(url, path)
    parsed = urlparse(value)
    try:
        query = parse_qs(parsed.query, strict_parsing=True)
    except ValueError as error:
        raise ContractValidationError(
            f"{path} must be an immutable English Wikipedia oldid URL"
        ) from error
    if parsed.scheme != "https" or parsed.netloc != "en.wikipedia.org" or parsed.path != "/w/index.php":
        raise ContractValidationError(f"{path} must be an immutable English Wikipedia oldid URL")
    if set(query) != {"title", "oldid"} or len(query["title"]) != 1 or len(query["oldid"]) != 1:
        raise ContractValidationError(f"{path} must contain exactly title and oldid parameters")
    if query["title"][0] != topic or not query["oldid"][0].isdigit() or int(query["oldid"][0]) <= 0:
        raise ContractValidationError(f"{path} does not bind an immutable revision to its topic")


def validate_trace_record(record: Any, contract: dict[str, Any]) -> dict[str, Any]:
    """Validate one structured route trace against a frozen contract."""
    required = set(_IDENTITY_FIELDS) | {
        "started_at",
        "completed_at",
        "status",
        "trace_valid",
        "calls",
    }
    value = _require_mapping(record, "trace", required)
    identity = _validate_run_identity(
        {key: value[key] for key in _IDENTITY_FIELDS}, "trace", contract, "trace"
    )
    started = _require_utc_timestamp(value["started_at"], "trace.started_at")
    completed = _require_utc_timestamp(value["completed_at"], "trace.completed_at")
    if completed < started:
        raise ContractValidationError("trace.completed_at precedes trace.started_at")
    status = _require_string(value["status"], "trace.status")
    if status not in _RESPONSE_STATUSES:
        raise ContractValidationError("trace.status is invalid")
    if not isinstance(value["trace_valid"], bool):
        raise ContractValidationError("trace.trace_valid must be boolean")
    if not isinstance(value["calls"], list):
        raise ContractValidationError("trace.calls must be a list")
    condition = identity["condition"]
    if condition == "no_tool":
        if value["calls"] or value["trace_valid"]:
            raise ContractValidationError("no_tool traces must have no calls and trace_valid=false")
        return value
    if status != "ok" and value["trace_valid"]:
        raise ContractValidationError("a non-ok forced-retrieval trace cannot be trace-valid")
    if status == "ok" and len(value["calls"]) != 1:
        raise ContractValidationError("an ok forced-retrieval trace must contain exactly one call")
    if status != "ok" and value["calls"]:
        raise ContractValidationError("a failed forced-retrieval trace must not invent calls")
    if not value["calls"]:
        return value
    call = _require_mapping(value["calls"][0], "trace.calls[0]", {"tool", "title", "revision_timestamp", "revision_url"})
    if call["tool"] != "wikipedia_revision" or call["title"] != identity["topic"]:
        raise ContractValidationError("trace call is not bound to the declared Wikipedia topic")
    revision_time = _require_utc_timestamp(call["revision_timestamp"], "trace.calls[0].revision_timestamp")
    _validate_revision_url(call["revision_url"], "trace.calls[0].revision_url", identity["topic"])
    boundary = datetime(identity["cutoff_year"], 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    if condition == "strict" and revision_time > boundary:
        raise ContractValidationError("strict trace returned a post-cutoff revision")
    if condition == "misdated" and revision_time <= boundary:
        raise ContractValidationError("misdated trace did not return a post-cutoff revision")
    if not value["trace_valid"]:
        raise ContractValidationError("an ok, boundary-valid forced-retrieval trace must be trace_valid")
    return value


def validate_response_record(record: Any, contract: dict[str, Any]) -> dict[str, Any]:
    """Validate one response receipt, including non-replaceable failures."""
    required = set(_IDENTITY_FIELDS) | {
        "completed_at",
        "status",
        "response_text",
        "response_sha256",
    }
    value = _require_mapping(record, "response", required)
    _validate_run_identity(
        {key: value[key] for key in _IDENTITY_FIELDS}, "response", contract, "response"
    )
    _require_utc_timestamp(value["completed_at"], "response.completed_at")
    status = _require_string(value["status"], "response.status")
    if status not in _RESPONSE_STATUSES:
        raise ContractValidationError("response.status is invalid")
    response_text = value["response_text"]
    if not isinstance(response_text, str):
        raise ContractValidationError("response.response_text must be a string")
    digest = value["response_sha256"]
    if response_text:
        _require_string(digest, "response.response_sha256", pattern=_SHA256)
    elif digest is not None:
        raise ContractValidationError("an empty response must have response_sha256=null")
    if status == "ok" and not response_text:
        raise ContractValidationError("an ok response must contain response_text")
    if status != "ok" and response_text:
        raise ContractValidationError("a non-ok response must not contain response_text")
    return value


def validate_label_record(record: Any, contract: dict[str, Any]) -> dict[str, Any]:
    """Validate one blinded response label and its response identity binding."""
    required = set(_IDENTITY_FIELDS) | {
        "labeler_id",
        "labeled_at",
        "answer_label",
        "response_sha256",
    }
    value = _require_mapping(record, "label", required)
    _validate_run_identity(
        {key: value[key] for key in _IDENTITY_FIELDS}, "label", contract, "label"
    )
    _require_string(value["labeler_id"], "label.labeler_id")
    _require_utc_timestamp(value["labeled_at"], "label.labeled_at")
    if value["answer_label"] not in contract["labels"]:
        raise ContractValidationError("label.answer_label is invalid")
    response_digest = value["response_sha256"]
    if value["answer_label"] == "invalid_output":
        if response_digest is not None:
            raise ContractValidationError(
                "an invalid_output label must bind a null response digest"
            )
    else:
        _require_string(response_digest, "label.response_sha256", pattern=_SHA256)
    return value


def validate_experiment_records(records: list[dict[str, Any]], contract: dict[str, Any]) -> None:
    """Validate a complete set of trace, response, and blinded-label records.

    This cross-record check rejects duplicate run artifacts, labels that do not
    bind to an accepted response, and retries not preceded by the sole eligible
    transport failure.
    """
    if not isinstance(records, list) or not records:
        raise ContractValidationError("records must be a non-empty list")
    traces: dict[tuple[str, int], dict[str, Any]] = {}
    responses: dict[tuple[str, int], dict[str, Any]] = {}
    labels: set[tuple[str, int, str]] = set()
    run_identities: set[tuple[Any, ...]] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ContractValidationError("each record must be an object")
        record_type = record.get("record_type")
        key = (record.get("run_id"), record.get("attempt"))
        if record_type == "trace":
            if key in traces:
                raise ContractValidationError("duplicate trace record")
            trace = validate_trace_record(record, contract)
            identity = tuple(
                trace[field]
                for field in (
                    "study_phase",
                    "topic",
                    "cutoff_year",
                    "model_id",
                    "model_digest",
                    "seed",
                    "condition",
                    "attempt",
                )
            )
            if identity in run_identities:
                raise ContractValidationError("duplicate experimental run identity")
            run_identities.add(identity)
            traces[key] = trace
        elif record_type == "response":
            if key in responses:
                raise ContractValidationError("duplicate response record")
            responses[key] = validate_response_record(record, contract)
        elif record_type == "label":
            label = validate_label_record(record, contract)
            label_key = (label["run_id"], label["attempt"], label["labeler_id"])
            if label_key in labels:
                raise ContractValidationError("duplicate label record")
            labels.add(label_key)
        else:
            raise ContractValidationError("record_type must be trace, response, or label")
    if set(traces) != set(responses):
        raise ContractValidationError("each response must have exactly one matching trace")
    for key, response in responses.items():
        trace = traces[key]
        if response["status"] != trace["status"]:
            raise ContractValidationError("matching trace and response statuses differ")
        if key[1] == 2:
            prior = responses.get((key[0], 1))
            if prior is None or prior["status"] != "transport_failure_before_response":
                raise ContractValidationError("retry lacks a preceding transport failure")
    for record in records:
        if record["record_type"] != "label":
            continue
        response = responses.get((record["run_id"], record["attempt"]))
        if response is None or response["response_sha256"] != record["response_sha256"]:
            raise ContractValidationError("label does not bind to its response")
        expected = "invalid_output" if response["status"] != "ok" else None
        if expected is not None and record["answer_label"] != expected:
            raise ContractValidationError("a non-ok response must be labeled invalid_output")
