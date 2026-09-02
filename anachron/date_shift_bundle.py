"""Sealed-bundle core for the standalone date-shift study.

Tracked proposed materials are intentionally not executable.  This module only
accepts an external bundle created after a complete author audit and a runtime
preflight from the released scaffold checkout.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from anachron.date_shift import (
    _ARMS,
    DateShiftValidationError,
    TransportOutcome,
    _response_content,
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    invalid_score,
    score_response,
    validate_author_audit,
)


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DateShiftValidationError("JSON artifact contains a duplicate key")
        result[key] = value
    return result


def _load_object_bytes(path: Path, *, require_canonical: bool) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_pairs)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, DateShiftValidationError) as error:
        raise DateShiftValidationError(f"invalid JSON artifact: {path}") from error
    if not isinstance(value, dict):
        raise DateShiftValidationError(f"artifact must be an object: {path}")
    if require_canonical and raw != canonical_bytes(value):
        raise DateShiftValidationError(f"artifact is not canonical JSON bytes: {path}")
    return value, raw


def load_object(path: Path) -> dict[str, Any]:
    """Load one tracked JSON object while rejecting ambiguous duplicate keys."""
    return _load_object_bytes(path, require_canonical=False)[0]


def load_canonical_object(path: Path) -> tuple[dict[str, Any], bytes]:
    """Load one sealed bundle object whose on-disk bytes are canonical."""
    return _load_object_bytes(path, require_canonical=True)


def write_create_only(path: Path, value: Mapping[str, Any]) -> None:
    """Durably create one JSON file and never replace an existing artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(value)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as error:
        raise DateShiftValidationError(
            f"refusing to overwrite existing artifact: {path}"
        ) from error


def _require(value: Any, keys: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise DateShiftValidationError(f"{name} has an unexpected schema")
    return value


def validate_execution_plan(value: Any) -> dict[str, Any]:
    plan = _require(
        value,
        {
            "schema_version",
            "study_id",
            "endpoint",
            "models",
            "seed",
            "timeout_seconds",
            "think",
            "decoding",
            "bounds",
            "analysis",
            "calibration",
        },
        "execution plan",
    )
    if (
        plan["schema_version"] != "date-shift-execution-plan-v3"
        or plan["think"] is not False
    ):
        raise DateShiftValidationError(
            "execution plan schema or thinking setting is invalid"
        )
    if not isinstance(plan["models"], list) or len(plan["models"]) != 2:
        raise DateShiftValidationError("execution plan must specify exactly two models")
    names = set()
    for model in plan["models"]:
        row = _require(model, {"id", "digest"}, "execution plan model")
        if (
            not isinstance(row["id"], str)
            or not row["id"]
            or row["id"] in names
            or not isinstance(row["digest"], str)
            or not row["digest"].startswith("sha256:")
        ):
            raise DateShiftValidationError("execution plan model identity is invalid")
        names.add(row["id"])
    if (
        not isinstance(plan["seed"], int)
        or isinstance(plan["seed"], bool)
        or plan["seed"] < 0
    ):
        raise DateShiftValidationError("execution plan seed is invalid")
    if (
        plan["endpoint"] != "http://127.0.0.1:11434"
        or not isinstance(plan["timeout_seconds"], int)
        or plan["timeout_seconds"] <= 0
    ):
        raise DateShiftValidationError("execution plan endpoint or timeout is invalid")
    decoding = plan["decoding"]
    required_decoding = {
        "temperature",
        "seed",
        "num_predict",
        "top_k",
        "top_p",
        "min_p",
        "repeat_penalty",
        "num_ctx",
    }
    if (
        not isinstance(decoding, dict)
        or set(decoding) != required_decoding
        or decoding.get("temperature") != 0
        or decoding.get("seed") != plan["seed"]
    ):
        raise DateShiftValidationError("execution plan decoding is invalid")
    if (
        any(
            isinstance(decoding[key], bool)
            or not isinstance(decoding[key], (int, float))
            or decoding[key] <= 0
            for key in ("num_predict", "top_k", "top_p", "repeat_penalty", "num_ctx")
        )
        or isinstance(decoding["min_p"], bool)
        or not isinstance(decoding["min_p"], (int, float))
        or decoding["min_p"] < 0
    ):
        raise DateShiftValidationError("execution plan decoding values are invalid")
    if (
        not isinstance(plan["bounds"], dict)
        or plan["bounds"].get("max_document_utf8_bytes") != 4096
    ):
        raise DateShiftValidationError("execution plan source bound is invalid")
    if (
        not isinstance(plan["analysis"], dict)
        or plan["analysis"].get("bootstrap_replicates") != 10000
    ):
        raise DateShiftValidationError("execution plan analysis is invalid")
    calibration = plan["calibration"]
    if not isinstance(calibration, dict) or set(calibration) != {
        "question",
        "citation_id",
        "expected_answer",
        "document_content",
    }:
        raise DateShiftValidationError("execution plan calibration is invalid")
    if not all(isinstance(value, str) and value for value in calibration.values()):
        raise DateShiftValidationError("execution plan calibration values are invalid")
    return plan


def validate_runtime_preflight(value: Any, plan: Mapping[str, Any]) -> dict[str, Any]:
    runtime = _require(
        value,
        {
            "schema_version",
            "capture_provenance",
            "captured_at_utc",
            "endpoint",
            "ollama",
            "host",
            "context_tokens",
        },
        "runtime preflight",
    )
    if (
        runtime["schema_version"] != "date-shift-runtime-preflight-v3"
        or runtime["endpoint"] != plan["endpoint"]
        or runtime["context_tokens"] != plan["decoding"]["num_ctx"]
    ):
        raise DateShiftValidationError("runtime preflight plan binding is invalid")
    provenance = _require(
        runtime["capture_provenance"],
        {"scaffold_tag", "scaffold_commit", "code_closure_sha256"},
        "runtime capture provenance",
    )
    if not all(
        isinstance(provenance[key], str) and provenance[key] for key in provenance
    ):
        raise DateShiftValidationError("runtime capture provenance is invalid")
    ollama = _require(
        runtime["ollama"],
        {
            "cli_path",
            "cli_sha256",
            "cli_version_raw",
            "api_version",
            "tags_response_sha256",
            "models",
        },
        "runtime ollama",
    )
    if not all(
        isinstance(ollama[key], str) and ollama[key]
        for key in (
            "cli_path",
            "cli_sha256",
            "cli_version_raw",
            "api_version",
            "tags_response_sha256",
        )
    ):
        raise DateShiftValidationError("runtime Ollama evidence is incomplete")
    inventory = {
        row.get("name"): row.get("digest")
        for row in ollama["models"]
        if isinstance(row, dict)
    }
    if len(inventory) != len(ollama["models"]) or any(
        inventory.get(model["id"]) != model["digest"] for model in plan["models"]
    ):
        raise DateShiftValidationError(
            "runtime preflight model digests do not match the plan"
        )
    host = _require(
        runtime["host"],
        {
            "os",
            "python",
            "cpu",
            "ram_bytes",
            "video_adapters",
            "video_adapter_capture_sha256",
        },
        "runtime host",
    )
    if (
        not all(
            isinstance(host[key], str) and host[key]
            for key in ("os", "python", "cpu", "video_adapter_capture_sha256")
        )
        or not isinstance(host["ram_bytes"], int)
        or host["ram_bytes"] <= 0
    ):
        raise DateShiftValidationError("runtime host evidence is incomplete")
    if not isinstance(host["video_adapters"], list) or not host["video_adapters"]:
        raise DateShiftValidationError("runtime video adapter evidence is absent")
    for adapter in host["video_adapters"]:
        if not isinstance(adapter, dict) or not all(
            isinstance(adapter.get(key), str) and adapter[key]
            for key in ("name", "driver_version", "pnp_device_id")
        ):
            raise DateShiftValidationError("runtime video adapter evidence is invalid")
    return runtime


def finalize_bundle_inputs(
    proposed_frame: Mapping[str, Any],
    proposed_items: Mapping[str, Any],
    audit: Mapping[str, Any],
    plan: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Create the audited artifacts and contract that will live only in a sealed bundle."""
    validate_author_audit(proposed_frame, proposed_items, audit)
    plan = validate_execution_plan(plan)
    runtime = validate_runtime_preflight(runtime, plan)
    decisions = {row["item_id"]: row for row in audit["decisions"]}
    candidates, accepted = [], []
    proposed_by_id = {
        item["item_id"]: item for item in proposed_items["proposed_items"]
    }
    for candidate in proposed_frame["candidates"]:
        row = dict(candidate)
        if row["status"] != "excluded":
            decision = decisions[row["item_id"]]
            if decision["decision"] == "ACCEPT":
                row["status"] = "accepted"
                accepted.append(
                    {
                        key: value
                        for key, value in proposed_by_id[row["item_id"]].items()
                        if key != "audit_evidence"
                    }
                )
            else:
                row["status"] = "rejected"
                row["audit_reason"] = decision["reason"]
        candidates.append(row)
    if not accepted:
        raise DateShiftValidationError("author audit accepted no items")
    frame = {
        "schema_version": "date-shift-audited-frame-v3",
        "upstream": proposed_frame["upstream"],
        "candidates": candidates,
    }
    items = {
        "schema_version": "date-shift-audited-items-v3",
        "frame_sha256": canonical_sha256(frame),
        "author_audit_sha256": canonical_sha256(audit),
        "items": accepted,
    }
    contract = {
        "schema_version": "date-shift-execution-contract-v3",
        "plan_sha256": canonical_sha256(plan),
        "runtime_preflight_sha256": canonical_sha256(runtime),
        "author_audit_sha256": canonical_sha256(audit),
        "frame_sha256": canonical_sha256(frame),
        "items_sha256": canonical_sha256(items),
        "accepted_item_count": len(accepted),
        "primary_arms": list(_ARMS),
        "models": plan["models"],
        "endpoint": plan["endpoint"],
        "decoding": plan["decoding"],
        "analysis": plan["analysis"],
        "calibration": plan["calibration"],
    }
    schedule = create_schedule(contract, plan, items)
    return frame, items, contract, schedule


def create_schedule(
    contract: Mapping[str, Any], plan: Mapping[str, Any], items: Mapping[str, Any]
) -> dict[str, Any]:
    if contract.get(
        "schema_version"
    ) != "date-shift-execution-contract-v3" or contract.get(
        "accepted_item_count"
    ) != len(items.get("items", [])):
        raise DateShiftValidationError("bundle contract is invalid")
    pairs = [
        (index, item, model)
        for index, item in enumerate(items["items"])
        for model in plan["models"]
    ]
    pairs.sort(
        key=lambda row: hashlib.sha256(
            f"{plan['seed']}:{row[1]['item_id']}:{row[2]['id']}".encode()
        ).hexdigest()
    )
    trajectories = []
    for pair_index, (item_index, item, model) in enumerate(pairs):
        for arm in _ARMS if pair_index % 2 == 0 else tuple(reversed(_ARMS)):
            left = _model_payload(item, "post_truthful")
            right = _model_payload(item, "post_backdated_eligible")
            if (
                left.pop("presented_document_date")
                == right.pop("presented_document_date")
                or left != right
            ):
                raise DateShiftValidationError(
                    "date-shift treatment packets are not date-only variants"
                )
            trajectories.append(
                {
                    "schedule_index": len(trajectories),
                    "item_index": item_index,
                    "item_id": item["item_id"],
                    "topic_cluster_id": item["topic_cluster_id"],
                    "model_id": model["id"],
                    "model_digest": model["digest"],
                    "arm": arm,
                }
            )
    return {
        "schema_version": "date-shift-schedule-v2",
        "contract_sha256": canonical_sha256(contract),
        "plan_sha256": canonical_sha256(plan),
        "items_sha256": canonical_sha256(items),
        "seed": plan["seed"],
        "trajectories": trajectories,
    }


def build_request(
    plan: Mapping[str, Any], item: Mapping[str, Any], trajectory: Mapping[str, Any]
) -> dict[str, Any]:
    payload = _model_payload(item, trajectory["arm"])
    instructions = 'Use only the supplied document. If presented_document_date is later than as_of_cutoff, or the document does not support an answer, reply exactly {"answer":"ABSTAIN","citation_ids":[]}. Otherwise answer only from the document and reply with exactly one JSON object having answer and citation_ids, where citation_ids is the supplied opaque citation identifier.'
    return {
        "model": trajectory["model_id"],
        "messages": [
            {"role": "system", "content": instructions},
            {
                "role": "user",
                "content": json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
            },
        ],
        "stream": False,
        "think": False,
        "options": dict(plan["decoding"]),
    }


def _model_payload(item: Mapping[str, Any], arm: str) -> dict[str, str]:
    if arm not in _ARMS:
        raise DateShiftValidationError("unknown date-shift treatment arm")
    date_key = (
        "presented_document_date_truthful"
        if arm == "post_truthful"
        else "presented_document_date_backdated"
    )
    return {
        "question": item["question"],
        "as_of_cutoff": item["cutoff_date"],
        "presented_document_date": item[date_key],
        "document_content": item["document_content"]["text"],
        "citation_id": item["citation_id"],
    }


def calibration_request(plan: Mapping[str, Any], model_id: str) -> dict[str, Any]:
    calibration = plan["calibration"]
    payload = {
        "question": calibration["question"],
        "as_of_cutoff": "2000-01-01",
        "presented_document_date": "2000-01-01",
        "document_content": calibration["document_content"],
        "citation_id": calibration["citation_id"],
    }
    return {
        "model": model_id,
        "messages": [
            {
                "role": "system",
                "content": "Use only the supplied document and return exactly one JSON object with answer and citation_ids.",
            },
            {
                "role": "user",
                "content": json.dumps(payload, sort_keys=True, separators=(",", ":")),
            },
        ],
        "stream": False,
        "think": False,
        "options": dict(plan["decoding"]),
    }


def _bundle_manifest_sha256(bundle: Mapping[str, Any]) -> str:
    raw = bundle.get("raw_manifest")
    if isinstance(raw, bytes):
        return bytes_sha256(raw)
    return canonical_sha256(bundle)


class JournalV3:
    """Create-only run journal with an ordered, fail-closed state machine."""

    def __init__(self, path: Path, bundle: Mapping[str, Any]):
        self.path, self.bundle = Path(path), bundle

    def create(self) -> None:
        if self.path.exists() or self.path.parent.exists():
            raise DateShiftValidationError("run directory or journal already exists")
        self.path.parent.mkdir(parents=True)
        self._append(
            {
                "schema_version": "date-shift-journal-v3",
                "sequence": 0,
                "record_type": "run_header",
                "bundle_manifest_sha256": _bundle_manifest_sha256(self.bundle),
            }
        )

    def _records(self) -> list[dict[str, Any]]:
        try:
            rows = [
                json.loads(line)
                for line in self.path.read_text(encoding="utf-8").splitlines()
            ]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DateShiftValidationError("journal is invalid") from error
        if not rows or any(not isinstance(row, dict) for row in rows):
            raise DateShiftValidationError("journal has no valid records")
        for index, row in enumerate(rows):
            if (
                row.get("schema_version") != "date-shift-journal-v3"
                or row.get("sequence") != index
            ):
                raise DateShiftValidationError("journal sequence is invalid")
        return rows

    def _append(self, row: Mapping[str, Any]) -> None:
        payload = canonical_bytes(row) + b"\n"
        with self.path.open("ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    def append(self, record_type: str, **fields: Any) -> None:
        rows = self._records()
        self._append(
            {
                "schema_version": "date-shift-journal-v3",
                "sequence": len(rows),
                "record_type": record_type,
                **fields,
            }
        )

    def terminalize(self, record_type: str, **fields: Any) -> None:
        self.append(record_type, **fields)


def validate_journal_v3(path: Path, bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    journal = JournalV3(path, bundle)
    rows = journal._records()
    if rows[0] != {
        "schema_version": "date-shift-journal-v3",
        "sequence": 0,
        "record_type": "run_header",
        "bundle_manifest_sha256": _bundle_manifest_sha256(bundle),
    }:
        raise DateShiftValidationError("journal header does not bind the bundle")
    phase, claimed, calibration_models, backend_models = "admission", None, [], []
    terminals = {}
    for row in rows[1:]:
        kind = row.get("record_type")
        if kind == "admission_terminal" and phase == "admission":
            if row.get("status") != "ok":
                raise DateShiftValidationError("run admission failed")
            phase = "calibration"
        elif kind == "calibration_claim" and phase == "calibration" and claimed is None:
            model = row.get("model_id")
            if (
                model
                != bundle["execution_contract"]["models"][len(calibration_models)]["id"]
            ):
                raise DateShiftValidationError("calibration model ordering is invalid")
            request_bytes = base64.b64decode(
                row.get("request_base64", ""), validate=True
            )
            if row.get("request_sha256") != bytes_sha256(request_bytes) or json.loads(
                request_bytes.decode("utf-8")
            ) != calibration_request(bundle["execution_plan"], model):
                raise DateShiftValidationError(
                    "calibration request does not replay from the sealed bundle"
                )
            claimed = ("calibration", model)
        elif (
            kind == "calibration_terminal"
            and claimed
            and claimed[0] == "calibration"
            and row.get("model_id") == claimed[1]
        ):
            if row.get("status") != "ok":
                raise DateShiftValidationError("calibration failed")
            raw = base64.b64decode(row.get("response_base64", ""), validate=True)
            if row.get("response_sha256") != bytes_sha256(raw):
                raise DateShiftValidationError("calibration response bytes drifted")
            content = _response_content(TransportOutcome("ok", raw), claimed[1])
            calibration = bundle["execution_plan"]["calibration"]
            if json.loads(content) != {
                "answer": calibration["expected_answer"],
                "citation_ids": [calibration["citation_id"]],
            }:
                raise DateShiftValidationError(
                    "calibration response does not satisfy the sealed check"
                )
            calibration_models.append(claimed[1])
            claimed = None
        elif (
            kind == "loaded_backend_evidence"
            and phase == "calibration"
            and claimed is None
            and len(backend_models) < len(calibration_models)
        ):
            model = row.get("model_id")
            if (
                model != calibration_models[len(backend_models)]
                or not isinstance(row.get("ollama_ps_base64"), str)
                or not isinstance(row.get("ollama_ps_sha256"), str)
            ):
                raise DateShiftValidationError("backend evidence is invalid")
            raw = base64.b64decode(row["ollama_ps_base64"], validate=True)
            rendered = raw.decode("utf-8", errors="replace")
            if (
                bytes_sha256(raw) != row["ollama_ps_sha256"]
                or model not in rendered
                or "processor" not in rendered.casefold()
            ):
                raise DateShiftValidationError(
                    "backend evidence does not show the calibrated model"
                )
            backend_models.append(model)
        elif (
            kind == "phase_transition"
            and phase == "calibration"
            and claimed is None
            and len(backend_models) == 2
            and row.get("to") == "science"
        ):
            phase = "science"
        elif kind == "dispatch_claim" and phase == "science" and claimed is None:
            index = row.get("schedule_index")
            if not isinstance(index, int) or index != len(terminals):
                raise DateShiftValidationError("science claim ordering is invalid")
            request_bytes = base64.b64decode(
                row.get("request_base64", ""), validate=True
            )
            if row.get("request_sha256") != bytes_sha256(request_bytes):
                raise DateShiftValidationError("science request bytes drifted")
            expected = build_request(
                bundle["execution_plan"],
                bundle["audited_items"]["items"][
                    bundle["schedule"]["trajectories"][index]["item_index"]
                ],
                bundle["schedule"]["trajectories"][index],
            )
            if json.loads(request_bytes.decode("utf-8")) != expected:
                raise DateShiftValidationError(
                    "science request does not replay from the sealed bundle"
                )
            claimed = ("science", index)
        elif (
            kind == "terminal_outcome"
            and claimed
            and claimed[0] == "science"
            and row.get("schedule_index") == claimed[1]
        ):
            raw = base64.b64decode(row.get("response_base64", ""), validate=True)
            if row.get("response_sha256") != bytes_sha256(raw):
                raise DateShiftValidationError("science response bytes drifted")
            trajectory = bundle["schedule"]["trajectories"][claimed[1]]
            item = bundle["audited_items"]["items"][trajectory["item_index"]]
            if row.get("status") == "ok":
                if row.get("score") != score_response(
                    _response_content(
                        TransportOutcome("ok", raw), trajectory["model_id"]
                    ),
                    item,
                ):
                    raise DateShiftValidationError(
                        "science score does not replay from raw response"
                    )
            elif row.get("score") != invalid_score():
                raise DateShiftValidationError(
                    "non-successful science terminal must be invalid"
                )
            terminals[claimed[1]] = row
            claimed = None
        elif (
            kind == "run_terminal"
            and phase == "science"
            and claimed is None
            and row.get("status") == "science_complete"
        ):
            phase = "complete"
        else:
            raise DateShiftValidationError("journal record violates its lifecycle")
    if claimed is not None:
        raise DateShiftValidationError("UNKNOWN_AFTER_CLAIM")
    if phase != "complete":
        raise DateShiftValidationError("journal is incomplete")
    if len(terminals) != len(bundle["schedule"]["trajectories"]):
        raise DateShiftValidationError(
            "journal does not terminalize every planned cell"
        )
    return rows


def _measure(
    cluster_values: Mapping[str, list[float]], seed: int, replicates: int
) -> dict[str, Any]:
    if not cluster_values:
        return {"estimate": None, "ci_95": None}
    values = [sum(row) / len(row) for _, row in sorted(cluster_values.items())]
    randomizer = random.Random(seed)
    samples = sorted(
        sum(randomizer.choice(values) for _ in values) / len(values)
        for _ in range(replicates)
    )
    return {
        "estimate": sum(values) / len(values),
        "ci_95": [
            samples[int(0.025 * (replicates - 1))],
            samples[int(0.975 * (replicates - 1))],
        ],
    }


def reduce_terminals(
    bundle: Mapping[str, Any], terminals: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    schedule = bundle["schedule"]["trajectories"]
    by_index = {int(row["schedule_index"]): row for row in terminals}
    if len(by_index) != len(schedule) or set(by_index) != set(range(len(schedule))):
        raise DateShiftValidationError("analysis requires every planned terminal")
    groups: dict[tuple[str, str], dict[str, Mapping[str, Any]]] = {}
    invalids: dict[tuple[str, str], int] = {}
    for trajectory in schedule:
        terminal = by_index[trajectory["schedule_index"]]
        score = terminal.get("score", invalid_score())
        if not isinstance(score, Mapping):
            raise DateShiftValidationError("terminal score is invalid")
        groups.setdefault((trajectory["model_id"], trajectory["item_id"]), {})[
            trajectory["arm"]
        ] = score
        key = (trajectory["model_id"], trajectory["arm"])
        invalids[key] = invalids.get(key, 0) + int(
            score.get("answer_class") == "invalid_output"
        )

    def summarize(allow_invalid: bool, scalar: str) -> dict[str, Any]:
        measures = {
            name: {} for name in ("forward", "reverse", "net", "truthful_leakage")
        }
        valid_pairs = 0
        for (model, item), arms in groups.items():
            left, right = arms["post_truthful"], arms["post_backdated_eligible"]
            if not allow_invalid and (
                left.get("answer_class") == "invalid_output"
                or right.get("answer_class") == "invalid_output"
            ):
                continue
            valid_pairs += 1
            lval = int(left.get(scalar) == 1)
            rval = int(right.get(scalar) == 1)
            cluster = next(
                row["topic_cluster_id"]
                for row in schedule
                if row["model_id"] == model and row["item_id"] == item
            )
            for name, value in (
                ("forward", float(lval == 0 and rval == 1)),
                ("reverse", float(lval == 1 and rval == 0)),
                ("net", float(rval - lval)),
                ("truthful_leakage", float(lval)),
            ):
                measures[name].setdefault(cluster, []).append(value)
        analysis = bundle["execution_plan"]["analysis"]
        return {
            "included_pairs": valid_pairs,
            "included_topic_clusters": len(measures["forward"]),
            **{
                name: _measure(
                    values, analysis["bootstrap_seed"], analysis["bootstrap_replicates"]
                )
                for name, values in measures.items()
            },
        }

    denominator = len(schedule) // 2
    invalid_table = [
        {
            "model_id": model["id"],
            "arm": arm,
            "planned": bundle["execution_contract"]["accepted_item_count"],
            "invalid_count": invalids.get((model["id"], arm), 0),
            "invalid_rate": invalids.get((model["id"], arm), 0)
            / bundle["execution_contract"]["accepted_item_count"],
        }
        for model in bundle["execution_contract"]["models"]
        for arm in _ARMS
    ]
    return {
        "schema_version": "date-shift-analysis-v4",
        "planned_cells": len(schedule),
        "terminal_cells": len(terminals),
        "complete_valid_primary": summarize(False, "post_answer_exact"),
        "all_planned_cell_itt": summarize(True, "post_answer_exact"),
        "grounded_complete_valid_secondary": summarize(False, "grounded_post_exact"),
        "by_model_by_arm_invalids": invalid_table,
        "pair_denominator": denominator,
    }


def load_bundle(bundle_dir: Path) -> dict[str, Any]:
    """Load a final bundle only when every canonical artifact's raw bytes replay."""
    bundle_dir = bundle_dir.resolve()
    if (
        ".incomplete-" in bundle_dir.name
        or bundle_dir.name.startswith(".")
        or ".tmp" in bundle_dir.name
    ):
        raise DateShiftValidationError(
            "incomplete or temporary bundle directories are inadmissible"
        )
    names = (
        "author_audit.json",
        "runtime_preflight.json",
        "audited_frame.json",
        "audited_items.json",
        "execution_contract.json",
        "schedule.json",
        "execution_plan.json",
    )
    loaded = {
        name: load_canonical_object(bundle_dir / name)
        for name in names
    }
    values = {name: value for name, (value, _) in loaded.items()}
    raw_artifacts = {name: raw for name, (_, raw) in loaded.items()}
    manifest, manifest_raw = load_canonical_object(bundle_dir / "bundle_manifest.json")
    publication, _ = load_canonical_object(bundle_dir / "publication.json")
    expected = {name: bytes_sha256(raw) for name, raw in raw_artifacts.items()}
    if (
        manifest.get("schema_version") != "date-shift-execution-bundle-v2"
        or manifest.get("artifacts") != expected
        or manifest.get("author_audit_sha256") != expected["author_audit.json"]
        or manifest.get("runtime_preflight_sha256")
        != expected["runtime_preflight.json"]
        or manifest.get("contract_sha256") != expected["execution_contract.json"]
        or manifest.get("schedule_sha256") != expected["schedule.json"]
    ):
        raise DateShiftValidationError("bundle manifest artifact hashes drifted")
    without_id = {key: value for key, value in manifest.items() if key != "bundle_id"}
    if manifest.get("bundle_id") != canonical_sha256(without_id):
        raise DateShiftValidationError("bundle manifest identity drifted")
    if manifest.get("bundle_directory_name") != bundle_dir.name or publication != {
        "schema_version": "date-shift-bundle-publication-v1",
        "bundle_id": manifest["bundle_id"],
        "bundle_directory_name": bundle_dir.name,
        "manifest_sha256": bytes_sha256(manifest_raw),
    }:
        raise DateShiftValidationError("bundle publication marker is invalid")
    plan, runtime, contract, schedule = (
        values["execution_plan.json"],
        values["runtime_preflight.json"],
        values["execution_contract.json"],
        values["schedule.json"],
    )
    validate_execution_plan(plan)
    validate_runtime_preflight(runtime, plan)
    if contract.get("plan_sha256") != canonical_sha256(plan) or contract.get(
        "runtime_preflight_sha256"
    ) != canonical_sha256(runtime):
        raise DateShiftValidationError("bundle contract inputs drifted")
    if schedule != create_schedule(contract, plan, values["audited_items.json"]):
        raise DateShiftValidationError(
            "bundle schedule is not the deterministic derivation"
        )
    return {
        "manifest": manifest,
        "execution_plan": plan,
        "runtime_preflight": runtime,
        "execution_contract": contract,
        "audited_frame": values["audited_frame.json"],
        "audited_items": values["audited_items.json"],
        "schedule": schedule,
        "author_audit": values["author_audit.json"],
        "raw_artifacts": raw_artifacts,
        "raw_manifest": manifest_raw,
    }


def verify_bundle_derivation(
    bundle: Mapping[str, Any], repository: Path, provenance: Mapping[str, Any]
) -> None:
    """Rebuild bundle-only artifacts from the admitted tracked scaffold."""
    tracked_plan = load_object(repository / "research/date-shift/execution_plan.json")
    tracked_frame = load_object(repository / "research/date-shift/proposed_frame.json")
    tracked_items = load_object(repository / "research/date-shift/proposed_items.json")
    if (
        bundle["manifest"].get("scaffold_release_sha256")
        != canonical_sha256(provenance)
    ):
        raise DateShiftValidationError(
            "bundle manifest scaffold provenance drifted"
        )
    if (
        bundle["execution_plan"] != tracked_plan
        or bundle["raw_artifacts"]["execution_plan.json"]
        != canonical_bytes(tracked_plan)
        or bundle["raw_artifacts"]["author_audit.json"]
        != canonical_bytes(bundle["author_audit"])
    ):
        raise DateShiftValidationError(
            "released execution plan does not reproduce"
        )
    if bundle["runtime_preflight"].get("capture_provenance") != dict(provenance):
        raise DateShiftValidationError("bundle runtime provenance drifted")
    frame, items, contract, schedule = finalize_bundle_inputs(
        tracked_frame,
        tracked_items,
        bundle["author_audit"],
        tracked_plan,
        bundle["runtime_preflight"],
    )
    expected = {
        "audited_frame.json": frame,
        "audited_items.json": items,
        "execution_contract.json": contract,
        "schedule.json": schedule,
    }
    if any(
        bundle[name.removesuffix(".json")] != value
        or bundle["raw_artifacts"][name] != canonical_bytes(value)
        for name, value in expected.items()
    ):
        raise DateShiftValidationError(
            "bundle derived artifacts are not reproducible from the released scaffold"
        )
