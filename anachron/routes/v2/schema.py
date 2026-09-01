"""Strict phase-separated contract validation for Routes v2."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class ContractValidationError(ValueError):
    """Raised when a document is not the exact frozen Routes v2 contract."""


_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PHASE_COUNTS = {"development": 6, "pilot": 18, "confirmatory": 36}
_PRIMARY_ARMS = ["post_truthful", "post_misdated_eligible"]
_CONDITIONS = ["strict_pre_truthful", *_PRIMARY_ARMS]


def _mapping(value: Any, path: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ContractValidationError(f"{path} has missing or extra fields")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractValidationError(f"{path} must be non-empty text")
    return value


def _integer(value: Any, path: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractValidationError(f"{path} must be an integer >= {minimum}")
    return value


def _sha(value: Any, path: str) -> str:
    value = _string(value, path)
    if _SHA256.fullmatch(value) is None:
        raise ContractValidationError(f"{path} must be a SHA-256 receipt")
    return value


def phase_topics(contract: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    """Return the one exact title/year set assigned to a study phase."""
    if phase == "development":
        return contract["development"]["topics"]
    if phase in {"pilot", "confirmatory"}:
        return contract["evaluation"][phase]["topics"]
    raise ContractValidationError("phase is not declared by Routes v2")


def phase_spec(contract: dict[str, Any], phase: str) -> dict[str, Any]:
    """Return the frozen execution specification for one phase."""
    if phase == "development":
        return {
            "topic_count": 6,
            "topics": contract["development"]["topics"],
            "models": [contract["models"][0]["id"]],
            "conditions": contract["development"]["primary_arms"],
            "fixed_audit_seed": None,
        }
    if phase in {"pilot", "confirmatory"}:
        item = contract["evaluation"][phase]
        return {
            "topic_count": item["topic_count"], "topics": item["topics"],
            "models": item["models"], "conditions": contract["conditions"],
            "fixed_audit_seed": item["fixed_audit_seed"],
        }
    raise ContractValidationError("phase is not declared by Routes v2")


def validate_contract(document: Any) -> dict[str, Any]:
    """Validate the closed, phase-separated v2 design."""
    contract = _mapping(document, "contract", {
        "schema_version", "study_id", "status", "sampling_frame_sha256", "models", "execution",
        "conditions", "development", "source_gate", "evaluation", "schedule", "calibration",
    })
    if contract["schema_version"] != "routes-v2-contract" or contract["study_id"] != "anachron-routes-v2" or contract["status"] != "pre_outcome":
        raise ContractValidationError("contract identity or pre-outcome status is invalid")
    _sha(contract["sampling_frame_sha256"], "contract.sampling_frame_sha256")
    models = contract["models"]
    if not isinstance(models, list) or len(models) != 2:
        raise ContractValidationError("contract.models must contain exactly two frozen model identities")
    model_ids: set[str] = set()
    for index, value in enumerate(models):
        model = _mapping(value, f"contract.models[{index}]", {"id", "digest"})
        model_id = _string(model["id"], f"contract.models[{index}].id")
        if model_id in model_ids:
            raise ContractValidationError("contract model IDs must be unique")
        model_ids.add(model_id)
        _sha(model["digest"], f"contract.models[{index}].digest")
    execution = _mapping(contract["execution"], "contract.execution", {"endpoint", "seeds", "temperature", "num_predict", "think", "timeout_seconds", "retry_policy"})
    if execution["endpoint"] != "http://127.0.0.1:11434" or execution["seeds"] != [17, 29] or execution["temperature"] != 0.2 or execution["num_predict"] != 160 or execution["think"] is not False:
        raise ContractValidationError("v2 decoding parameters are invalid")
    _integer(execution["timeout_seconds"], "contract.execution.timeout_seconds", 1)
    if _mapping(execution["retry_policy"], "contract.execution.retry_policy", {"max_retries", "only_status"}) != {"max_retries": 1, "only_status": "transport_failure_no_response_object"}:
        raise ContractValidationError("v2 retry policy must forbid retries after response initiation")
    if contract["conditions"] != _CONDITIONS:
        raise ContractValidationError("v2 conditions are invalid")
    development = _mapping(contract["development"], "contract.development", {"topics", "primary_arms", "threshold"})
    if development["primary_arms"] != _PRIMARY_ARMS:
        raise ContractValidationError("development primary arms are invalid")
    if _mapping(development["threshold"], "contract.development.threshold", {"metric", "minimum_mean_paired_difference"}) != {"metric": "post_only", "minimum_mean_paired_difference": 0.25}:
        raise ContractValidationError("development threshold is not the frozen 0.25 paired difference")
    if _mapping(contract["source_gate"], "contract.source_gate", {"decision_schema"}) != {"decision_schema": "routes-v2-source-decisions"}:
        raise ContractValidationError("source gate is invalid")
    evaluation = _mapping(contract["evaluation"], "contract.evaluation", {"primary_arms", "pilot", "confirmatory", "raters"})
    if evaluation["primary_arms"] != _PRIMARY_ARMS or evaluation["raters"] != ["rater-a", "rater-b"]:
        raise ContractValidationError("evaluation primary arms or raters are invalid")
    for phase, expected_count, expected_models in (("pilot", 18, [models[0]["id"]]), ("confirmatory", 36, [models[0]["id"], models[1]["id"]])):
        value = _mapping(evaluation[phase], f"contract.evaluation.{phase}", {"topics", "topic_count", "fixed_audit_seed", "models"})
        if value["topic_count"] != expected_count or value["fixed_audit_seed"] != 17 or value["models"] != expected_models:
            raise ContractValidationError(f"contract evaluation {phase} is invalid")
    seen: set[tuple[str, int]] = set()
    for phase, expected_count in _PHASE_COUNTS.items():
        topics = phase_topics(contract, phase)
        if not isinstance(topics, list) or len(topics) != expected_count:
            raise ContractValidationError(f"{phase} must contain exactly {expected_count} title/year items")
        for index, topic in enumerate(topics):
            item = _mapping(topic, f"contract.{phase}.topics[{index}]", {"title", "cutoff_year"})
            identity = (_string(item["title"], "topic title"), _integer(item["cutoff_year"], "topic cutoff", 1))
            if identity in seen:
                raise ContractValidationError("development, pilot, and confirmatory title/year items must be disjoint")
            seen.add(identity)
    schedule = _mapping(contract["schedule"], "contract.schedule", {"version", "seed", "development_orders", "evaluation_orders"})
    if schedule["version"] != "routes-v2-counterbalance-v3" or schedule["seed"] != 20260901:
        raise ContractValidationError("counterbalance identity is invalid")
    development_tokens = {"s17:post_truthful", "s17:post_misdated_eligible", "s29:post_truthful", "s29:post_misdated_eligible"}
    evaluation_tokens = {f"s{seed}:{condition}" for seed in execution["seeds"] for condition in contract["conditions"]}
    for name, tokens, size in (("development_orders", development_tokens, 4), ("evaluation_orders", evaluation_tokens, 6)):
        orders = schedule[name]
        if not isinstance(orders, list) or len(orders) != 6 or any(not isinstance(order, list) or len(order) != size or set(order) != tokens for order in orders) or len({tuple(order) for order in orders}) != 6:
            raise ContractValidationError(f"{name} must freeze six distinct complete orders")
    calibration = _mapping(contract["calibration"], "contract.calibration", {"schema_version", "expected_answer", "required_before_execution"})
    if calibration != {"schema_version": "routes-v2-calibration-receipt", "expected_answer": "CALIBRATION", "required_before_execution": True}:
        raise ContractValidationError("calibration contract is invalid")
    return contract


def load_contract(path: str | Path) -> dict[str, Any]:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractValidationError(f"unable to load contract: {error}") from error
    return validate_contract(document)
