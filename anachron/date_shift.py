"""Standalone core for the frozen visible-document-date experiment.

This module intentionally shares no scheduler, admission, or analysis code with
the larger Routes v2 study.  It validates one fixed finite-set contract, builds
the two date-only packets, records every dispatch before it occurs, and reduces
the resulting journal without making a claim when the journal is incomplete.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class DateShiftValidationError(ValueError):
    """Raised when a frozen artifact or runtime boundary is invalid."""


class UnknownAfterClaimError(RuntimeError):
    """Raised when a process may have dispatched a claimed trajectory."""


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_ISO_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T")
_URL = re.compile(r"https?://|oldid(?:=|/)|\brevid\b", re.IGNORECASE)
_FORBIDDEN_PROMPT_WORDS = re.compile(
    r"\b(?:post_truthful|post_backdated_eligible|truthful|backdated|condition|arm)\b",
    re.IGNORECASE,
)
_ARMS = ("post_truthful", "post_backdated_eligible")
_ABSTENTION_ALIASES = {"abstain"}
_DECODING_KEYS = {
    "temperature",
    "seed",
    "num_predict",
    "top_k",
    "top_p",
    "min_p",
    "repeat_penalty",
    "num_ctx",
}


def canonical_bytes(value: Any) -> bytes:
    """Encode JSON deterministically for every hash binding in this study."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def bytes_sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical_sha256(value: Any) -> str:
    return bytes_sha256(canonical_bytes(value))


def _mapping(value: Any, name: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise DateShiftValidationError(f"{name} has an unexpected schema")
    return value


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise DateShiftValidationError(f"{name} must be a sha256 digest")
    return value


def _date(value: Any, name: str) -> date:
    if not isinstance(value, str):
        raise DateShiftValidationError(f"{name} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise DateShiftValidationError(f"{name} must be an ISO date") from error


def _timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise DateShiftValidationError(f"{name} must be an ISO timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DateShiftValidationError(f"{name} must be an ISO timestamp") from error


def _nonempty_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DateShiftValidationError(f"{name} must be nonempty text")
    return value


def _normalise(value: str) -> str:
    return " ".join(value.casefold().split())


def _validate_aliases(value: Any, name: str) -> set[str]:
    if not isinstance(value, list) or not value:
        raise DateShiftValidationError(f"{name} must be a nonempty list")
    aliases = {_normalise(_nonempty_text(alias, name)) for alias in value}
    if len(aliases) != len(value):
        raise DateShiftValidationError(f"{name} contains duplicate aliases")
    return aliases


def _validate_provenance_side(value: Any, name: str) -> dict[str, Any]:
    side = _mapping(
        value,
        name,
        {
            "immutable_url",
            "timestamp",
            "full_content_sha256",
            "anchor_sha256",
            "anchor_start_offset",
            "anchor_end_offset",
            "excerpt_sha256",
            "excerpt_start_offset",
            "excerpt_end_offset",
        },
    )
    url = _nonempty_text(side["immutable_url"], f"{name}.immutable_url")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or "oldid=" not in parsed.query:
        raise DateShiftValidationError(
            f"{name}.immutable_url is not an immutable oldid URL"
        )
    _timestamp(side["timestamp"], f"{name}.timestamp")
    for field in ("full_content_sha256", "anchor_sha256", "excerpt_sha256"):
        _sha(side[field], f"{name}.{field}")
    for start_name, end_name in (
        ("anchor_start_offset", "anchor_end_offset"),
        ("excerpt_start_offset", "excerpt_end_offset"),
    ):
        start, end = side[start_name], side[end_name]
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end <= start
        ):
            raise DateShiftValidationError(f"{name} has invalid offsets")
    return side


def validate_contract(value: Any) -> dict[str, Any]:
    """Validate the frozen pre-outcome experiment contract without side effects."""
    contract = _mapping(
        value,
        "contract",
        {
            "schema_version",
            "study_id",
            "status",
            "frame_size",
            "accepted_item_count",
            "frame_sha256",
            "items_sha256",
            "endpoint",
            "models",
            "seed",
            "temperature",
            "num_predict",
            "timeout_seconds",
            "primary_arms",
            "prompt_template_version",
            "think",
            "decoding",
            "bounds",
            "analysis",
            "calibration",
            "runtime_evidence",
        },
    )
    if (
        contract["schema_version"] != "date-shift-execution-contract-v2"
        or contract["status"] != "pre_outcome_frozen"
    ):
        raise DateShiftValidationError("contract is not the frozen pre-outcome schema")
    _nonempty_text(contract["study_id"], "contract.study_id")
    if (
        contract["frame_size"] != 60
        or isinstance(contract["accepted_item_count"], bool)
        or not isinstance(contract["accepted_item_count"], int)
        or not 1 <= contract["accepted_item_count"] <= 54
    ):
        raise DateShiftValidationError(
            "contract must freeze a nonempty audited subset of the 60-candidate frame"
        )
    _sha(contract["frame_sha256"], "contract.frame_sha256")
    _sha(contract["items_sha256"], "contract.items_sha256")
    endpoint = _nonempty_text(contract["endpoint"], "contract.endpoint")
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise DateShiftValidationError(
            "contract.endpoint must be a loopback HTTP endpoint"
        )
    if not isinstance(contract["models"], list) or len(contract["models"]) != 2:
        raise DateShiftValidationError("contract must declare exactly two models")
    model_ids: set[str] = set()
    for model in contract["models"]:
        model_value = _mapping(model, "contract model", {"id", "digest"})
        model_id = _nonempty_text(model_value["id"], "contract model id")
        if model_id in model_ids:
            raise DateShiftValidationError("contract model ids must be unique")
        model_ids.add(model_id)
        _sha(model_value["digest"], "contract model digest")
    if isinstance(contract["seed"], bool) or not isinstance(contract["seed"], int):
        raise DateShiftValidationError("contract.seed must be an integer")
    if (
        contract["temperature"] != 0
        or isinstance(contract["num_predict"], bool)
        or not isinstance(contract["num_predict"], int)
        or contract["num_predict"] <= 0
    ):
        raise DateShiftValidationError("contract decoding settings are invalid")
    if (
        isinstance(contract["timeout_seconds"], bool)
        or not isinstance(contract["timeout_seconds"], int)
        or contract["timeout_seconds"] <= 0
    ):
        raise DateShiftValidationError("contract.timeout_seconds is invalid")
    if contract["primary_arms"] != list(_ARMS):
        raise DateShiftValidationError("contract primary arms are invalid")
    _nonempty_text(
        contract["prompt_template_version"], "contract.prompt_template_version"
    )
    if contract["think"] is not False:
        raise DateShiftValidationError("contract.think must freeze false")
    decoding = _mapping(contract["decoding"], "contract.decoding", _DECODING_KEYS)
    if (
        decoding["temperature"] != 0
        or decoding["seed"] != contract["seed"]
        or decoding["num_predict"] != contract["num_predict"]
    ):
        raise DateShiftValidationError(
            "contract decoding does not bind the scalar settings"
        )
    for field in ("top_k", "num_ctx"):
        if (
            isinstance(decoding[field], bool)
            or not isinstance(decoding[field], int)
            or decoding[field] <= 0
        ):
            raise DateShiftValidationError(f"contract.decoding.{field} is invalid")
    for field in ("top_p", "repeat_penalty"):
        if (
            isinstance(decoding[field], bool)
            or not isinstance(decoding[field], (int, float))
            or decoding[field] <= 0
        ):
            raise DateShiftValidationError(f"contract.decoding.{field} is invalid")
    if (
        isinstance(decoding["min_p"], bool)
        or not isinstance(decoding["min_p"], (int, float))
        or decoding["min_p"] < 0
    ):
        raise DateShiftValidationError("contract.decoding.min_p is invalid")
    bounds = _mapping(
        contract["bounds"], "contract.bounds", {"max_document_utf8_bytes"}
    )
    if (
        isinstance(bounds["max_document_utf8_bytes"], bool)
        or not isinstance(bounds["max_document_utf8_bytes"], int)
        or bounds["max_document_utf8_bytes"] <= 0
    ):
        raise DateShiftValidationError("contract document bound is invalid")
    analysis = _mapping(
        contract["analysis"],
        "contract.analysis",
        {"primary_outcome", "estimand", "bootstrap_seed", "bootstrap_replicates"},
    )
    if (
        analysis["primary_outcome"] != "forward_transition_post_exact"
        or analysis["estimand"]
        != "mean_paired_truthful_nonpost_to_backdated_post_exact_rate"
    ):
        raise DateShiftValidationError("contract analysis is invalid")
    if (
        any(
            isinstance(analysis[field], bool) or not isinstance(analysis[field], int)
            for field in ("bootstrap_seed", "bootstrap_replicates")
        )
        or analysis["bootstrap_replicates"] <= 0
    ):
        raise DateShiftValidationError("contract bootstrap settings are invalid")
    calibration = _mapping(
        contract["calibration"],
        "contract.calibration",
        {"question", "document_content", "citation_id", "expected_answer"},
    )
    for key, item in calibration.items():
        _nonempty_text(item, f"contract.calibration.{key}")
    runtime = _mapping(
        contract["runtime_evidence"],
        "contract.runtime_evidence",
        {
            "schema_version",
            "captured_at_utc",
            "ollama_cli_version",
            "ollama_api_version",
            "ollama_ps",
            "inventory_sha256",
            "os",
            "python_version",
            "cpu",
            "ram_bytes",
            "gpu",
            "gpu_driver",
            "context_tokens",
        },
    )
    if runtime["schema_version"] != "date-shift-runtime-evidence-v2":
        raise DateShiftValidationError("contract runtime evidence schema is invalid")
    _timestamp(runtime["captured_at_utc"], "contract.runtime_evidence.captured_at_utc")
    for field in (
        "ollama_cli_version",
        "ollama_api_version",
        "ollama_ps",
        "os",
        "python_version",
        "cpu",
        "gpu",
        "gpu_driver",
    ):
        text = _nonempty_text(runtime[field], f"contract.runtime_evidence.{field}")
        if "replace" in text.casefold() or "placeholder" in text.casefold():
            raise DateShiftValidationError(
                "contract runtime evidence contains a placeholder"
            )
    _sha(runtime["inventory_sha256"], "contract.runtime_evidence.inventory_sha256")
    if (
        isinstance(runtime["ram_bytes"], bool)
        or not isinstance(runtime["ram_bytes"], int)
        or runtime["ram_bytes"] <= 0
    ):
        raise DateShiftValidationError("contract runtime RAM evidence is invalid")
    if runtime["context_tokens"] != decoding["num_ctx"]:
        raise DateShiftValidationError(
            "contract runtime context does not bind decoding"
        )
    return contract


def validate_frame(value: Any) -> dict[str, Any]:
    """Validate the visible 60-candidate cohort and retained exclusions."""
    frame = _mapping(value, "frame", {"schema_version", "upstream", "candidates"})
    if (
        frame["schema_version"] != "date-shift-audited-frame-v2"
        or not isinstance(frame["candidates"], list)
        or len(frame["candidates"]) != 60
    ):
        raise DateShiftValidationError("frame must contain exactly 60 candidates")
    upstream = _mapping(
        frame["upstream"],
        "frame.upstream",
        {
            "source",
            "github_revision",
            "github_artifact_url",
            "github_source_sha256",
            "huggingface_revision",
            "huggingface_artifact_url",
            "huggingface_source_sha256",
            "legacy_sampling_frame_sha256",
        },
    )
    for field in ("source", "github_revision", "huggingface_revision"):
        _nonempty_text(upstream[field], f"frame.upstream.{field}")
    for field in ("github_artifact_url", "huggingface_artifact_url"):
        parsed = urlparse(_nonempty_text(upstream[field], f"frame.upstream.{field}"))
        if parsed.scheme != "https" or not parsed.netloc:
            raise DateShiftValidationError(
                f"frame.upstream.{field} must be an HTTPS URL"
            )
    for field in (
        "github_source_sha256",
        "huggingface_source_sha256",
        "legacy_sampling_frame_sha256",
    ):
        _sha(upstream[field], f"frame.upstream.{field}")
    indices: set[int] = set()
    accepted = 0
    rejected = 0
    excluded = 0
    for candidate in frame["candidates"]:
        if not isinstance(candidate, dict):
            raise DateShiftValidationError("frame candidate must be an object")
        required = {"frame_index", "topic", "cutoff_year", "status"}
        if not required <= set(candidate) or candidate["status"] not in {
            "accepted",
            "rejected",
            "excluded",
        }:
            raise DateShiftValidationError("frame candidate has an invalid schema")
        index = candidate["frame_index"]
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or index in indices
        ):
            raise DateShiftValidationError(
                "frame indices must be unique nonnegative integers"
            )
        indices.add(index)
        _nonempty_text(candidate["topic"], "frame candidate topic")
        if (
            isinstance(candidate["cutoff_year"], bool)
            or not isinstance(candidate["cutoff_year"], int)
            or not 1900 <= candidate["cutoff_year"] <= 2100
        ):
            raise DateShiftValidationError("frame candidate cutoff_year is invalid")
        if candidate["status"] == "accepted":
            if (
                not isinstance(candidate.get("item_id"), str)
                or not candidate["item_id"]
            ):
                raise DateShiftValidationError("admitted frame candidate needs item_id")
            accepted += 1
        elif candidate["status"] == "rejected":
            if (
                not isinstance(candidate.get("item_id"), str)
                or not candidate["item_id"]
                or not isinstance(candidate.get("audit_reason"), str)
                or not candidate["audit_reason"].strip()
            ):
                raise DateShiftValidationError(
                    "rejected frame candidate needs its binding and audit reason"
                )
            rejected += 1
        else:
            if (
                not isinstance(candidate.get("reason"), str)
                or not candidate["reason"].strip()
            ):
                raise DateShiftValidationError(
                    "excluded frame candidate needs a reason"
                )
            excluded += 1
    if indices != set(range(60)) or accepted + rejected != 54 or excluded != 6:
        raise DateShiftValidationError(
            "frame must retain 54 audited proposals and six exclusions"
        )
    return frame


def validate_item(contract: Mapping[str, Any], value: Any) -> dict[str, Any]:
    """Validate one admissible source pair; source provenance never reaches a packet."""
    item = _mapping(
        value,
        "item",
        {
            "item_id",
            "frame_index",
            "topic_cluster_id",
            "topic",
            "cutoff_date",
            "question",
            "citation_id",
            "presented_document_date_truthful",
            "presented_document_date_backdated",
            "document_content",
            "pre_answer_aliases",
            "post_answer_aliases",
            "source_provenance",
        },
    )
    for field in ("item_id", "topic_cluster_id", "topic", "question", "citation_id"):
        _nonempty_text(item[field], f"item.{field}")
    if (
        isinstance(item["frame_index"], bool)
        or not isinstance(item["frame_index"], int)
        or item["frame_index"] < 0
    ):
        raise DateShiftValidationError("item.frame_index is invalid")
    cutoff = _date(item["cutoff_date"], "item.cutoff_date")
    truthful = _date(
        item["presented_document_date_truthful"],
        "item.presented_document_date_truthful",
    )
    backdated = _date(
        item["presented_document_date_backdated"],
        "item.presented_document_date_backdated",
    )
    if truthful <= cutoff or backdated > cutoff:
        raise DateShiftValidationError(
            "item presented dates do not straddle the cutoff"
        )
    document = _mapping(
        item["document_content"],
        "item.document_content",
        {"text", "sha256", "utf8_bytes"},
    )
    text = _nonempty_text(document["text"], "item.document_content.text")
    encoded = text.encode("utf-8")
    if (
        document["sha256"] != bytes_sha256(encoded)
        or document["utf8_bytes"] != len(encoded)
        or len(encoded) > contract["bounds"]["max_document_utf8_bytes"]
    ):
        raise DateShiftValidationError("item document content binding is invalid")
    pre_aliases = _validate_aliases(
        item["pre_answer_aliases"], "item.pre_answer_aliases"
    )
    post_aliases = _validate_aliases(
        item["post_answer_aliases"], "item.post_answer_aliases"
    )
    if pre_aliases & post_aliases:
        raise DateShiftValidationError("item answer aliases must be disjoint")
    provenance = _mapping(
        item["source_provenance"],
        "item.source_provenance",
        {"legacy_raw_artifact_sha256", "pre", "post"},
    )
    _sha(
        provenance["legacy_raw_artifact_sha256"],
        "item.source_provenance.legacy_raw_artifact_sha256",
    )
    pre, post = (
        _validate_provenance_side(provenance["pre"], "item.source_provenance.pre"),
        _validate_provenance_side(provenance["post"], "item.source_provenance.post"),
    )
    if (
        _timestamp(pre["timestamp"], "pre timestamp").date() > cutoff
        or _timestamp(post["timestamp"], "post timestamp").date() <= cutoff
    ):
        raise DateShiftValidationError(
            "source provenance timestamps do not straddle the cutoff"
        )
    if post["excerpt_sha256"] != document["sha256"]:
        raise DateShiftValidationError(
            "post provenance excerpt does not bind document content"
        )
    return item


def validate_study(
    contract_value: Any, frame_value: Any, items_value: Any
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Validate all pre-outcome artifacts and their one-way hash bindings."""
    contract = validate_contract(contract_value)
    frame = validate_frame(frame_value)
    items = _mapping(
        items_value,
        "items",
        {"schema_version", "frame_sha256", "items", "author_audit_sha256"},
    )
    if (
        items["schema_version"] != "date-shift-audited-items-v2"
        or items["frame_sha256"] != canonical_sha256(frame)
        or contract["frame_sha256"] != canonical_sha256(frame)
        or contract["items_sha256"] != canonical_sha256(items)
    ):
        raise DateShiftValidationError("contract/frame/items hash bindings drifted")
    _sha(items["author_audit_sha256"], "items.author_audit_sha256")
    if (
        not isinstance(items["items"], list)
        or len(items["items"]) != contract["accepted_item_count"]
    ):
        raise DateShiftValidationError(
            "items must contain exactly the accepted audited records"
        )
    admitted = {
        candidate["frame_index"]: candidate
        for candidate in frame["candidates"]
        if candidate["status"] == "accepted"
    }
    found_ids: set[str] = set()
    found_indices: set[int] = set()
    for item_value in items["items"]:
        item = validate_item(contract, item_value)
        if item["item_id"] in found_ids or item["frame_index"] in found_indices:
            raise DateShiftValidationError(
                "items must have unique item ids and frame indices"
            )
        found_ids.add(item["item_id"])
        found_indices.add(item["frame_index"])
        candidate = admitted.get(item["frame_index"])
        if (
            candidate is None
            or candidate["item_id"] != item["item_id"]
            or candidate["topic"] != item["topic"]
            or candidate["cutoff_year"]
            != _date(item["cutoff_date"], "item.cutoff_date").year
        ):
            raise DateShiftValidationError(
                "item does not match its admitted frame candidate"
            )
    if found_indices != set(admitted):
        raise DateShiftValidationError(
            "items do not cover exactly the accepted audited frame"
        )
    return contract, frame, items


def _audit_source_binding(item: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    return {
        side: {
            "immutable_url": item["source_provenance"][side]["immutable_url"],
            "full_content_sha256": item["source_provenance"][side][
                "full_content_sha256"
            ],
        }
        for side in ("pre", "post")
    }


def validate_author_audit(
    proposed_frame_value: Any, proposed_items_value: Any, audit_value: Any
) -> dict[str, Any]:
    """Require a timestamped author decision bound to both source revisions per proposal."""
    proposed_frame = _mapping(
        proposed_frame_value,
        "proposed frame",
        {"schema_version", "upstream", "candidates"},
    )
    proposed_items = _mapping(
        proposed_items_value,
        "proposed items",
        {"schema_version", "proposed_frame_sha256", "proposed_items"},
    )
    audit = _mapping(
        audit_value,
        "author audit",
        {
            "schema_version",
            "proposed_frame_sha256",
            "proposed_items_sha256",
            "author_id",
            "attested_at_utc",
            "attestation",
            "decisions",
        },
    )
    if (
        proposed_frame["schema_version"] != "date-shift-proposed-frame-v2"
        or proposed_items["schema_version"] != "date-shift-proposed-items-v2"
        or audit["schema_version"] != "date-shift-author-audit-v1"
    ):
        raise DateShiftValidationError(
            "author audit artifacts have an unexpected schema"
        )
    if (
        proposed_items["proposed_frame_sha256"] != canonical_sha256(proposed_frame)
        or audit["proposed_frame_sha256"] != canonical_sha256(proposed_frame)
        or audit["proposed_items_sha256"] != canonical_sha256(proposed_items)
    ):
        raise DateShiftValidationError("author audit hash bindings drifted")
    _nonempty_text(audit["author_id"], "author audit author_id")
    attested_at = _timestamp(audit["attested_at_utc"], "author audit attested_at_utc")
    if (
        attested_at.tzinfo is None
        or attested_at.utcoffset() is None
        or not isinstance(audit["attested_at_utc"], str)
        or not audit["attested_at_utc"].endswith("Z")
    ):
        raise DateShiftValidationError(
            "author audit attestation must use an explicit UTC Z timestamp"
        )
    if (
        audit["attestation"]
        != "I personally reviewed the bound pre and post excerpts for every proposed item and made every ACCEPT or REJECT decision above."
    ):
        raise DateShiftValidationError("author audit attestation text is invalid")
    candidates = proposed_frame["candidates"]
    if (
        not isinstance(candidates, list)
        or len(candidates) != 60
        or not isinstance(proposed_items["proposed_items"], list)
        or len(proposed_items["proposed_items"]) != 54
        or not isinstance(audit["decisions"], list)
        or len(audit["decisions"]) != 54
    ):
        raise DateShiftValidationError(
            "author audit must retain the 60/54 proposed source set"
        )
    proposed_by_id = {
        item.get("item_id"): item
        for item in proposed_items["proposed_items"]
        if isinstance(item, dict)
    }
    if len(proposed_by_id) != 54:
        raise DateShiftValidationError("proposed item identifiers are invalid")
    decision_ids: set[str] = set()
    for decision in audit["decisions"]:
        if not isinstance(decision, dict) or set(decision) != {
            "item_id",
            "source_bindings",
            "decision",
            "reviewed_at_utc",
            "reason",
            "ai_recommendation_note",
        }:
            raise DateShiftValidationError("author audit decision schema is invalid")
        item_id = decision["item_id"]
        item = proposed_by_id.get(item_id)
        if item is None or item_id in decision_ids:
            raise DateShiftValidationError(
                "author audit decision does not bind one proposal"
            )
        decision_ids.add(item_id)
        if decision["decision"] not in {"ACCEPT", "REJECT"}:
            raise DateShiftValidationError(
                "author audit contains an unresolved or invalid decision"
            )
        reviewed_at = _timestamp(
            decision["reviewed_at_utc"], "author audit reviewed_at_utc"
        )
        if (
            reviewed_at.tzinfo is None
            or reviewed_at.utcoffset() is None
            or not isinstance(decision["reviewed_at_utc"], str)
            or not decision["reviewed_at_utc"].endswith("Z")
        ):
            raise DateShiftValidationError(
                "author audit timestamp must be an explicit UTC Z timestamp"
            )
        _nonempty_text(decision["reason"], "author audit reason")
        if not isinstance(decision["ai_recommendation_note"], str):
            raise DateShiftValidationError(
                "author audit AI recommendation note must be text"
            )
        if decision["source_bindings"] != _audit_source_binding(item):
            raise DateShiftValidationError("author audit source bindings drifted")
    if decision_ids != set(proposed_by_id):
        raise DateShiftValidationError(
            "author audit must decide every proposed item exactly once"
        )
    return audit


def finalize_author_audit(
    proposed_frame_value: Any,
    proposed_items_value: Any,
    audit_value: Any,
    execution_settings: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Reject the removed loose-finalizer interface.

    Audited execution artifacts may only be created by the external bundle
    sealer after provenance and runtime-preflight admission.
    """
    del proposed_frame_value, proposed_items_value, audit_value, execution_settings
    raise DateShiftValidationError(
        "legacy loose audit finalization is removed; use the sealed bundle workflow"
    )


def _packet_payload(item: Mapping[str, Any], arm: str) -> dict[str, str]:
    if arm not in _ARMS:
        raise DateShiftValidationError("unknown primary arm")
    date_field = (
        "presented_document_date_truthful"
        if arm == "post_truthful"
        else "presented_document_date_backdated"
    )
    return {
        "question": item["question"],
        "as_of_cutoff": item["cutoff_date"],
        "presented_document_date": item[date_field],
        "document_content": item["document_content"]["text"],
        "citation_id": item["citation_id"],
    }


def _scan_packet_hygiene(payload: Mapping[str, str], item: Mapping[str, Any]) -> None:
    controls = "\n".join((payload["question"], payload["citation_id"]))
    if (
        _URL.search(controls)
        or _FORBIDDEN_PROMPT_WORDS.search(controls)
        or _ISO_TIMESTAMP.search(controls)
    ):
        raise DateShiftValidationError(
            "forbidden metadata leaked outside document content"
        )
    aliases = _validate_aliases(
        item["pre_answer_aliases"], "item.pre_answer_aliases"
    ) | _validate_aliases(item["post_answer_aliases"], "item.post_answer_aliases")
    normalized = _normalise(controls)
    if any(
        re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", normalized)
        for alias in aliases
    ):
        raise DateShiftValidationError("answer alias leaked outside document content")


def build_packet(
    contract_value: Mapping[str, Any], item_value: Mapping[str, Any], arm: str
) -> dict[str, Any]:
    """Build a model-visible packet whose sole arm-dependent value is its displayed date."""
    contract = validate_contract(contract_value)
    item = validate_item(contract, item_value)
    payload = _packet_payload(item, arm)
    _scan_packet_hygiene(payload, item)
    instructions = (
        "Use only the supplied document. If presented_document_date is later than as_of_cutoff, "
        'or the document does not support an answer, reply exactly {"answer":"ABSTAIN","citation_ids":[]}. '
        "Otherwise answer only from the document and reply with exactly one JSON object having answer and "
        "citation_ids, where citation_ids is the supplied opaque citation identifier."
    )
    return {
        "schema_version": "date-shift-model-packet-v1",
        "messages": [
            {"role": "system", "content": instructions},
            {
                "role": "user",
                "content": json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
            },
        ],
    }


def assert_treatment_isolation(
    contract: Mapping[str, Any], item: Mapping[str, Any]
) -> bool:
    """Reject any packet drift beyond the designated presented-date scalar."""
    left = build_packet(contract, item, "post_truthful")
    right = build_packet(contract, item, "post_backdated_eligible")
    left_payload = json.loads(left["messages"][1]["content"])
    right_payload = json.loads(right["messages"][1]["content"])
    if (
        left_payload.pop("presented_document_date")
        == right_payload.pop("presented_document_date")
        or left_payload != right_payload
    ):
        raise DateShiftValidationError("treatment packets are not date-only variants")
    return True


def _order_key(seed: int, item_id: str, model_id: str) -> str:
    return hashlib.sha256(f"{seed}:{item_id}:{model_id}".encode()).hexdigest()


def create_schedule(
    contract_value: Mapping[str, Any], items_value: Mapping[str, Any]
) -> dict[str, Any]:
    """Create the exact audited-subset x 2 x 2 deterministic counterbalanced schedule."""
    contract = validate_contract(contract_value)
    items = _mapping(
        items_value,
        "items",
        {"schema_version", "frame_sha256", "items", "author_audit_sha256"},
    )
    if (
        not isinstance(items["items"], list)
        or len(items["items"]) != contract["accepted_item_count"]
    ):
        raise DateShiftValidationError(
            "schedule requires the exact audited item manifest"
        )
    for item in items["items"]:
        validate_item(contract, item)
        assert_treatment_isolation(contract, item)
    pairs = [(item, model) for item in items["items"] for model in contract["models"]]
    pairs.sort(
        key=lambda pair: _order_key(contract["seed"], pair[0]["item_id"], pair[1]["id"])
    )
    trajectories: list[dict[str, Any]] = []
    for pair_index, (item, model) in enumerate(pairs):
        arms = _ARMS if pair_index % 2 == 0 else tuple(reversed(_ARMS))
        for arm in arms:
            trajectories.append(
                {
                    "schedule_index": len(trajectories),
                    "item_index": items["items"].index(item),
                    "item_id": item["item_id"],
                    "topic_cluster_id": item["topic_cluster_id"],
                    "model_id": model["id"],
                    "model_digest": model["digest"],
                    "arm": arm,
                    "seed": contract["seed"],
                    "temperature": contract["temperature"],
                }
            )
    schedule = {
        "schema_version": "date-shift-schedule-v1",
        "algorithm": "date-shift-counterbalance-v1",
        "contract_sha256": canonical_sha256(contract),
        "items_sha256": canonical_sha256(items),
        "seed": contract["seed"],
        "trajectories": trajectories,
    }
    validate_schedule(schedule, contract, items)
    return schedule


def validate_schedule(
    value: Any, contract_value: Mapping[str, Any], items_value: Mapping[str, Any]
) -> dict[str, Any]:
    contract = validate_contract(contract_value)
    schedule = _mapping(
        value,
        "schedule",
        {
            "schema_version",
            "algorithm",
            "contract_sha256",
            "items_sha256",
            "seed",
            "trajectories",
        },
    )
    if (
        schedule["schema_version"] != "date-shift-schedule-v1"
        or schedule["algorithm"] != "date-shift-counterbalance-v1"
        or schedule["contract_sha256"] != canonical_sha256(contract)
        or schedule["items_sha256"] != canonical_sha256(items_value)
        or schedule["seed"] != contract["seed"]
    ):
        raise DateShiftValidationError("schedule binding is invalid")
    expected = create_schedule_unchecked(contract, items_value)
    if schedule != expected:
        raise DateShiftValidationError(
            "schedule does not match its deterministic derivation"
        )
    expected_count = (
        contract["accepted_item_count"] * len(contract["models"]) * len(_ARMS)
    )
    if (
        len(schedule["trajectories"]) != expected_count
        or sum(row["arm"] == "post_truthful" for row in schedule["trajectories"])
        != expected_count // 2
    ):
        raise DateShiftValidationError("schedule is not exactly counterbalanced")
    return schedule


def create_schedule_unchecked(
    contract: Mapping[str, Any], items: Mapping[str, Any]
) -> dict[str, Any]:
    """Internal deterministic derivation used to validate a supplied schedule."""
    pairs = [(item, model) for item in items["items"] for model in contract["models"]]
    pairs.sort(
        key=lambda pair: _order_key(contract["seed"], pair[0]["item_id"], pair[1]["id"])
    )
    trajectories = []
    for pair_index, (item, model) in enumerate(pairs):
        for arm in _ARMS if pair_index % 2 == 0 else tuple(reversed(_ARMS)):
            trajectories.append(
                {
                    "schedule_index": len(trajectories),
                    "item_index": items["items"].index(item),
                    "item_id": item["item_id"],
                    "topic_cluster_id": item["topic_cluster_id"],
                    "model_id": model["id"],
                    "model_digest": model["digest"],
                    "arm": arm,
                    "seed": contract["seed"],
                    "temperature": contract["temperature"],
                }
            )
    return {
        "schema_version": "date-shift-schedule-v1",
        "algorithm": "date-shift-counterbalance-v1",
        "contract_sha256": canonical_sha256(contract),
        "items_sha256": canonical_sha256(items),
        "seed": contract["seed"],
        "trajectories": trajectories,
    }


def verify_model_inventory(
    contract_value: Mapping[str, Any], inventory: Mapping[str, str]
) -> None:
    """Require the declared name-to-digest pairs, while allowing unrelated installs."""
    contract = validate_contract(contract_value)
    if not isinstance(inventory, Mapping):
        raise DateShiftValidationError("Ollama inventory is invalid")
    for model in contract["models"]:
        if inventory.get(model["id"]) != model["digest"]:
            raise DateShiftValidationError(
                "declared model digest is absent or mismatched"
            )


@dataclass(frozen=True)
class TransportOutcome:
    status: str
    body: bytes
    detail: str | None = None


class OllamaClient:
    """Minimal loopback-only JSON transport; all errors become explicit outcomes."""

    def __init__(self, endpoint: str):
        parsed = urlparse(endpoint)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}:
            raise DateShiftValidationError("Ollama client requires a loopback endpoint")
        self.endpoint = endpoint.rstrip("/")

    def _request(
        self, path: str, body: bytes | None, timeout_seconds: int
    ) -> TransportOutcome:
        request = Request(
            self.endpoint + path,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST" if body is not None else "GET",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return TransportOutcome("ok", response.read())
        except HTTPError as error:
            return TransportOutcome("http_error", error.read(), str(error.code))
        except (URLError, OSError) as error:
            return TransportOutcome("transport_error", b"", str(error))

    def inventory(self, timeout_seconds: int) -> dict[str, str]:
        outcome = self._request("/api/tags", None, timeout_seconds)
        if outcome.status != "ok":
            raise DateShiftValidationError(f"Ollama inventory failed: {outcome.status}")
        try:
            payload = json.loads(outcome.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DateShiftValidationError("Ollama inventory was not JSON") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            raise DateShiftValidationError("Ollama inventory schema is invalid")
        inventory: dict[str, str] = {}
        for model in payload["models"]:
            if (
                not isinstance(model, dict)
                or not isinstance(model.get("name"), str)
                or not isinstance(model.get("digest"), str)
                or model["name"] in inventory
            ):
                raise DateShiftValidationError("Ollama inventory model is invalid")
            digest = model["digest"]
            inventory[model["name"]] = (
                digest if digest.startswith("sha256:") else "sha256:" + digest
            )
        return inventory

    def chat(
        self, request: Mapping[str, Any], timeout_seconds: int
    ) -> TransportOutcome:
        return self._request("/api/chat", canonical_bytes(request), timeout_seconds)


def admit_client(contract_value: Mapping[str, Any], client: Any) -> Mapping[str, str]:
    """Reject the removed V2 client-admission interface before transport I/O."""
    del contract_value, client
    _legacy_execution_disabled()


class ExecutionJournal:
    """Append-only schedule journal. A claimed-but-unterminal request is unrecoverable."""

    def __init__(self, path: Path, schedule: Mapping[str, Any]):
        del path, schedule
        _legacy_execution_disabled()

    def _records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            records = [json.loads(line) for line in lines]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DateShiftValidationError("journal is not valid JSONL") from error
        if not all(isinstance(record, dict) for record in records):
            raise DateShiftValidationError("journal record is invalid")
        return records

    def _state(self) -> tuple[dict[int, dict[str, Any]], set[int]]:
        claims: dict[int, dict[str, Any]] = {}
        terminals: set[int] = set()
        for record in self._records():
            record_type = record.get("record_type")
            if record_type in {"calibration_claim", "calibration_terminal"}:
                if set(record) != (
                    {
                        "schema_version",
                        "record_type",
                        "contract_sha256",
                        "runtime_evidence_sha256",
                        "model_id",
                        "model_digest",
                        "request_sha256",
                        "request_base64",
                    }
                    if record_type == "calibration_claim"
                    else {
                        "schema_version",
                        "record_type",
                        "contract_sha256",
                        "runtime_evidence_sha256",
                        "model_id",
                        "model_digest",
                        "request_sha256",
                        "response_sha256",
                        "response_base64",
                        "status",
                    }
                ):
                    raise DateShiftValidationError(
                        "journal calibration record schema is invalid"
                    )
                continue
            index = record.get("schedule_index")
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or index < 0
                or index >= len(self.schedule["trajectories"])
            ):
                raise DateShiftValidationError("journal schedule index is invalid")
            if record_type == "dispatch_claim":
                if index in claims:
                    raise DateShiftValidationError(
                        "journal trajectory claimed more than once"
                    )
                if record.get("trajectory") != self.schedule["trajectories"][
                    index
                ] or record.get("schedule_sha256") != canonical_sha256(self.schedule):
                    raise DateShiftValidationError(
                        "journal claim does not bind the schedule"
                    )
                claims[index] = record
            elif record_type == "terminal_outcome":
                if (
                    index not in claims
                    or index in terminals
                    or not isinstance(record.get("status"), str)
                    or not isinstance(record.get("score"), dict)
                ):
                    raise DateShiftValidationError(
                        "journal terminal outcome is invalid"
                    )
                terminals.add(index)
            else:
                raise DateShiftValidationError("journal record type is invalid")
        return claims, terminals

    def _append(self, record: Mapping[str, Any]) -> None:
        payload = canonical_bytes(record) + b"\n"
        with self.path.open("ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    def next_trajectory(self) -> dict[str, Any] | None:
        claims, terminals = self._state()
        unresolved = set(claims) - terminals
        if unresolved:
            raise UnknownAfterClaimError("a claimed trajectory has no terminal outcome")
        for trajectory in self.schedule["trajectories"]:
            if trajectory["schedule_index"] not in terminals:
                return trajectory
        return None

    def append_claim(
        self, trajectory: Mapping[str, Any], request: Mapping[str, Any]
    ) -> None:
        expected = self.next_trajectory()
        if expected is None or dict(trajectory) != expected:
            raise DateShiftValidationError("claim is not the next scheduled trajectory")
        request_bytes = canonical_bytes(request)
        self._append(
            {
                "schema_version": "date-shift-journal-v1",
                "record_type": "dispatch_claim",
                "schedule_index": trajectory["schedule_index"],
                "schedule_sha256": canonical_sha256(self.schedule),
                "trajectory": dict(trajectory),
                "request_sha256": bytes_sha256(request_bytes),
                "request_base64": base64.b64encode(request_bytes).decode("ascii"),
            }
        )

    def append_terminal(
        self,
        trajectory: Mapping[str, Any],
        status: str,
        score: Mapping[str, Any],
        response: bytes = b"",
        detail: str | None = None,
    ) -> dict[str, Any]:
        claims, terminals = self._state()
        index = trajectory.get("schedule_index")
        if (
            index not in claims
            or index in terminals
            or not isinstance(status, str)
            or not isinstance(score, Mapping)
        ):
            raise DateShiftValidationError("terminal outcome has no unmatched claim")
        record = {
            "schema_version": "date-shift-journal-v1",
            "record_type": "terminal_outcome",
            "schedule_index": index,
            "status": status,
            "score": dict(score),
            "response_sha256": bytes_sha256(response),
            "response_base64": base64.b64encode(response).decode("ascii"),
            "detail": detail,
        }
        self._append(record)
        return record

    def append_calibration_claim(
        self,
        contract: Mapping[str, Any],
        model: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> None:
        request_bytes = canonical_bytes(request)
        self._append(
            {
                "schema_version": "date-shift-journal-v2",
                "record_type": "calibration_claim",
                "contract_sha256": canonical_sha256(contract),
                "runtime_evidence_sha256": canonical_sha256(
                    contract["runtime_evidence"]
                ),
                "model_id": model["id"],
                "model_digest": model["digest"],
                "request_sha256": bytes_sha256(request_bytes),
                "request_base64": base64.b64encode(request_bytes).decode("ascii"),
            }
        )

    def append_calibration_terminal(
        self,
        contract: Mapping[str, Any],
        model: Mapping[str, Any],
        request: Mapping[str, Any],
        outcome: TransportOutcome,
        status: str,
    ) -> None:
        request_bytes = canonical_bytes(request)
        self._append(
            {
                "schema_version": "date-shift-journal-v2",
                "record_type": "calibration_terminal",
                "contract_sha256": canonical_sha256(contract),
                "runtime_evidence_sha256": canonical_sha256(
                    contract["runtime_evidence"]
                ),
                "model_id": model["id"],
                "model_digest": model["digest"],
                "request_sha256": bytes_sha256(request_bytes),
                "response_sha256": bytes_sha256(outcome.body),
                "response_base64": base64.b64encode(outcome.body).decode("ascii"),
                "status": status,
            }
        )


def _request_for_trajectory(
    contract: Mapping[str, Any],
    item: Mapping[str, Any],
    trajectory: Mapping[str, Any],
    admission_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    packet = build_packet(contract, item, trajectory["arm"])
    request = {
        "model": trajectory["model_id"],
        "messages": packet["messages"],
        "stream": False,
        "think": contract["think"],
        "options": dict(contract["decoding"]),
    }
    if admission_binding is not None:
        request["study_admission"] = dict(admission_binding)
    return request


def _response_content(outcome: TransportOutcome, requested_model: str) -> str:
    try:
        payload = json.loads(outcome.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DateShiftValidationError("Ollama response was not JSON") from error
    if (
        not isinstance(payload, dict)
        or payload.get("model") != requested_model
        or payload.get("done") is not True
        or not isinstance(payload.get("message"), dict)
        or set(payload["message"]) != {"role", "content"}
        or payload["message"].get("role") != "assistant"
        or not isinstance(payload["message"].get("content"), str)
    ):
        raise DateShiftValidationError("Ollama response schema is invalid")
    return payload["message"]["content"]


def score_response(content: str, item_value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep answer class, citation compliance, and their joint outcome distinct."""
    try:
        response = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return {
            "answer_class": "invalid_output",
            "citation_compliance": "invalid_output",
            "joint_outcome": "invalid_output",
            "post_answer_exact": 0,
            "grounded_post_exact": 0,
        }
    if (
        not isinstance(response, dict)
        or set(response) != {"answer", "citation_ids"}
        or not isinstance(response["answer"], str)
        or not isinstance(response["citation_ids"], list)
        or not all(isinstance(value, str) for value in response["citation_ids"])
    ):
        return {
            "answer_class": "invalid_output",
            "citation_compliance": "invalid_output",
            "joint_outcome": "invalid_output",
            "post_answer_exact": 0,
            "grounded_post_exact": 0,
        }
    answer = _normalise(response["answer"])
    citations = response["citation_ids"]
    if answer in _ABSTENTION_ALIASES:
        citation = "not_applicable" if citations == [] else "noncompliant"
        return {
            "answer_class": "abstain",
            "citation_compliance": citation,
            "joint_outcome": "abstain"
            if citation == "not_applicable"
            else "abstain_citation_noncompliant",
            "post_answer_exact": 0,
            "grounded_post_exact": 0,
        }
    post = _validate_aliases(
        item_value["post_answer_aliases"], "item.post_answer_aliases"
    )
    pre = _validate_aliases(item_value["pre_answer_aliases"], "item.pre_answer_aliases")
    citation = (
        "compliant" if citations == [item_value["citation_id"]] else "noncompliant"
    )
    if answer in post:
        return {
            "answer_class": "post_exact",
            "citation_compliance": citation,
            "joint_outcome": "post_exact"
            if citation == "compliant"
            else "post_answer_citation_noncompliant",
            "post_answer_exact": 1,
            "grounded_post_exact": int(citation == "compliant"),
        }
    if answer in pre:
        return {
            "answer_class": "pre_exact",
            "citation_compliance": citation,
            "joint_outcome": "pre_exact"
            if citation == "compliant"
            else "pre_answer_citation_noncompliant",
            "post_answer_exact": 0,
            "grounded_post_exact": 0,
        }
    return {
        "answer_class": "other",
        "citation_compliance": citation,
        "joint_outcome": "other"
        if citation == "compliant"
        else "other_citation_noncompliant",
        "post_answer_exact": 0,
        "grounded_post_exact": 0,
    }


def invalid_score() -> dict[str, Any]:
    return {
        "answer_class": "invalid_output",
        "citation_compliance": "invalid_output",
        "joint_outcome": "invalid_output",
        "post_answer_exact": 0,
        "grounded_post_exact": 0,
    }


class DateShiftRunner:
    """Reject the removed V2 scientific-dispatch interface before side effects."""

    def __init__(
        self,
        contract: Mapping[str, Any],
        items: Mapping[str, Any],
        schedule: Mapping[str, Any],
        journal_path: Path,
        client: Any,
        calibration_receipts: Sequence[Mapping[str, Any]],
    ):
        del contract, items, schedule, journal_path, client, calibration_receipts
        _legacy_execution_disabled()


def calibration_request(
    contract_value: Mapping[str, Any], model_id: str
) -> dict[str, Any]:
    del contract_value, model_id
    _legacy_execution_disabled()


def run_calibrations(
    contract_value: Mapping[str, Any],
    client: Any,
    journal: ExecutionJournal | None = None,
) -> list[dict[str, Any]]:
    del contract_value, client, journal
    _legacy_execution_disabled()


def _paired_transitions(
    items: Mapping[str, Any],
    schedule: Mapping[str, Any],
    outcomes: Sequence[Mapping[str, Any]],
    model_id: str | None,
    scalar: str,
) -> dict[str, dict[str, float]]:
    if scalar not in {"post_answer_exact", "grounded_post_exact"}:
        raise DateShiftValidationError("paired transition scalar is invalid")
    scheduled = {row["schedule_index"]: row for row in schedule["trajectories"]}
    terminal_by_index: dict[int, Mapping[str, Any]] = {}
    for outcome in outcomes:
        trajectory = outcome.get("trajectory")
        if (
            not isinstance(trajectory, dict)
            or trajectory.get("schedule_index") not in scheduled
            or scheduled[trajectory["schedule_index"]] != trajectory
        ):
            raise DateShiftValidationError("reducer outcome trajectory is invalid")
        index = trajectory["schedule_index"]
        if (
            index in terminal_by_index
            or outcome.get("status") is None
            or not isinstance(outcome.get("score"), Mapping)
        ):
            raise DateShiftValidationError("reducer outcome is duplicated or invalid")
        terminal_by_index[index] = outcome
    grouped: dict[tuple[str, str], dict[str, int]] = {}
    for index, trajectory in scheduled.items():
        if model_id is not None and trajectory["model_id"] != model_id:
            continue
        outcome = terminal_by_index.get(index)
        if outcome is None:
            continue
        grouped.setdefault((trajectory["topic_cluster_id"], trajectory["item_id"]), {})[
            trajectory["arm"]
        ] = int(outcome["score"].get(scalar) == 1)
    cluster_values: dict[str, dict[str, list[float]]] = {}
    for (cluster, _item_id), arms in grouped.items():
        if set(arms) == set(_ARMS):
            per_cluster = cluster_values.setdefault(
                cluster,
                {"forward": [], "reverse": [], "net": [], "truthful_leakage": []},
            )
            truthful, backdated = arms["post_truthful"], arms["post_backdated_eligible"]
            per_cluster["forward"].append(float(truthful == 0 and backdated == 1))
            per_cluster["reverse"].append(float(truthful == 1 and backdated == 0))
            per_cluster["net"].append(float(backdated - truthful))
            per_cluster["truthful_leakage"].append(float(truthful))
    return {
        cluster: {name: sum(values) / len(values) for name, values in measures.items()}
        for cluster, measures in cluster_values.items()
    }


def _bootstrap(
    values: Mapping[str, float], seed: int, replicates: int
) -> tuple[float | None, list[float] | None]:
    if not values:
        return None, None
    ordered = [values[key] for key in sorted(values)]
    estimate = sum(ordered) / len(ordered)
    generator = random.Random(seed)
    samples = sorted(
        sum(generator.choice(ordered) for _ in ordered) / len(ordered)
        for _ in range(replicates)
    )
    low_index = int(0.025 * (replicates - 1))
    high_index = int(0.975 * (replicates - 1))
    return estimate, [samples[low_index], samples[high_index]]


def _measure_summary(
    values: Mapping[str, Mapping[str, float]], measure: str, seed: int, replicates: int
) -> dict[str, Any]:
    estimate, ci = _bootstrap(
        {cluster: row[measure] for cluster, row in values.items()}, seed, replicates
    )
    return {"estimate": estimate, "ci_95": ci}


def reduce_outcomes(
    contract_value: Mapping[str, Any],
    items_value: Mapping[str, Any],
    schedule_value: Mapping[str, Any],
    outcomes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compute finite-set summaries; incomplete journals yield neutral null primary output."""
    contract = validate_contract(contract_value)
    validate_schedule(schedule_value, contract, items_value)
    if not isinstance(outcomes, Sequence):
        raise DateShiftValidationError("reducer outcomes must be a sequence")
    scheduled = {row["schedule_index"]: row for row in schedule_value["trajectories"]}
    terminal_by_index: dict[int, Mapping[str, Any]] = {}
    answer_classes = {
        "post_exact": 0,
        "pre_exact": 0,
        "abstain": 0,
        "other": 0,
        "invalid_output": 0,
    }
    citation_compliance = {
        "compliant": 0,
        "noncompliant": 0,
        "not_applicable": 0,
        "invalid_output": 0,
    }
    joint_outcomes: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            raise DateShiftValidationError("reducer outcome must be an object")
        trajectory = outcome.get("trajectory")
        if not isinstance(trajectory, Mapping):
            raise DateShiftValidationError("reducer outcome trajectory is invalid")
        index = trajectory.get("schedule_index")
        if (
            index not in scheduled
            or scheduled[index] != dict(trajectory)
            or index in terminal_by_index
        ):
            raise DateShiftValidationError(
                "reducer outcome schedule binding is invalid"
            )
        score = outcome.get("score")
        answer_class = score.get("answer_class") if isinstance(score, Mapping) else None
        citation = (
            score.get("citation_compliance") if isinstance(score, Mapping) else None
        )
        joint = score.get("joint_outcome") if isinstance(score, Mapping) else None
        if (
            answer_class not in answer_classes
            or citation not in citation_compliance
            or not isinstance(joint, str)
            or outcome.get("status") is None
        ):
            raise DateShiftValidationError("reducer outcome score is invalid")
        terminal_by_index[index] = outcome
        answer_classes[answer_class] += 1
        citation_compliance[citation] += 1
        joint_outcomes[joint] = joint_outcomes.get(joint, 0) + 1
        statuses[str(outcome["status"])] = statuses.get(str(outcome["status"]), 0) + 1
    complete = len(terminal_by_index) == len(scheduled)
    values = (
        _paired_transitions(
            items_value, schedule_value, outcomes, None, "post_answer_exact"
        )
        if complete
        else {}
    )
    grounded_values = (
        _paired_transitions(
            items_value, schedule_value, outcomes, None, "grounded_post_exact"
        )
        if complete
        else {}
    )
    by_model: dict[str, Any] = {}
    for model in contract["models"]:
        model_outcomes = [
            outcome
            for outcome in outcomes
            if outcome["trajectory"]["model_id"] == model["id"]
        ]
        model_values = (
            _paired_transitions(
                items_value, schedule_value, outcomes, model["id"], "post_answer_exact"
            )
            if complete
            else {}
        )
        by_model[model["id"]] = {
            "planned": contract["accepted_item_count"] * len(_ARMS),
            "terminal": len(model_outcomes),
            "paired_topic_clusters": len(model_values),
            "forward": _measure_summary(
                model_values,
                "forward",
                contract["analysis"]["bootstrap_seed"],
                contract["analysis"]["bootstrap_replicates"],
            ),
            "reverse": _measure_summary(
                model_values,
                "reverse",
                contract["analysis"]["bootstrap_seed"],
                contract["analysis"]["bootstrap_replicates"],
            ),
            "net": _measure_summary(
                model_values,
                "net",
                contract["analysis"]["bootstrap_seed"],
                contract["analysis"]["bootstrap_replicates"],
            ),
            "truthful_leakage": _measure_summary(
                model_values,
                "truthful_leakage",
                contract["analysis"]["bootstrap_seed"],
                contract["analysis"]["bootstrap_replicates"],
            ),
            "invalid_rate": sum(
                row["score"]["answer_class"] == "invalid_output"
                for row in model_outcomes
            )
            / (contract["accepted_item_count"] * len(_ARMS)),
        }
    arm_invalid_rates = {
        arm: sum(
            outcome["trajectory"]["arm"] == arm
            and outcome["score"]["answer_class"] == "invalid_output"
            for outcome in terminal_by_index.values()
        )
        / sum(row["arm"] == arm for row in schedule_value["trajectories"])
        for arm in _ARMS
    }
    return {
        "schema_version": "date-shift-analysis-v3",
        "contract_sha256": canonical_sha256(contract),
        "items_sha256": canonical_sha256(items_value),
        "schedule_sha256": canonical_sha256(schedule_value),
        "completion": {
            "planned_count": len(scheduled),
            "terminal_count": len(terminal_by_index),
            "complete": complete,
            "missing_schedule_indices": [
                index for index in sorted(scheduled) if index not in terminal_by_index
            ],
            "statuses": statuses,
            "answer_classes": answer_classes,
            "citation_compliance": citation_compliance,
            "joint_outcomes": joint_outcomes,
            "per_arm_invalid_rate": arm_invalid_rates,
        },
        "primary": {
            "estimand": contract["analysis"]["estimand"],
            "scalar": "post_answer_exact",
            "paired_topic_clusters": len(values),
            "forward": _measure_summary(
                values,
                "forward",
                contract["analysis"]["bootstrap_seed"],
                contract["analysis"]["bootstrap_replicates"],
            ),
            "reverse": _measure_summary(
                values,
                "reverse",
                contract["analysis"]["bootstrap_seed"],
                contract["analysis"]["bootstrap_replicates"],
            ),
            "net": _measure_summary(
                values,
                "net",
                contract["analysis"]["bootstrap_seed"],
                contract["analysis"]["bootstrap_replicates"],
            ),
            "truthful_leakage": _measure_summary(
                values,
                "truthful_leakage",
                contract["analysis"]["bootstrap_seed"],
                contract["analysis"]["bootstrap_replicates"],
            ),
            "bootstrap_seed": contract["analysis"]["bootstrap_seed"],
            "bootstrap_replicates": contract["analysis"]["bootstrap_replicates"],
            "result_mode": "complete" if complete else "incomplete_neutral",
        },
        "grounded_joint_secondary": {
            "scalar": "grounded_post_exact",
            "paired_topic_clusters": len(grounded_values),
            "forward": _measure_summary(
                grounded_values,
                "forward",
                contract["analysis"]["bootstrap_seed"],
                contract["analysis"]["bootstrap_replicates"],
            ),
        },
        "itt_sensitivity": {
            "definition": "all planned cells retained; invalid outputs count as non-post",
            "forward": _measure_summary(
                values,
                "forward",
                contract["analysis"]["bootstrap_seed"],
                contract["analysis"]["bootstrap_replicates"],
            ),
        },
        "by_model": by_model,
    }


def journal_outcomes(
    journal_path: Path, schedule: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Replay terminal journal rows into the reducer's deliberately small input shape."""
    journal = ExecutionJournal(journal_path, schedule)
    claims, terminals = journal._state()
    if set(claims) - terminals:
        raise UnknownAfterClaimError(
            "journal has a claimed trajectory without a terminal outcome"
        )
    terminal_rows = {
        record["schedule_index"]: record
        for record in journal._records()
        if record["record_type"] == "terminal_outcome"
    }
    return [
        {
            "trajectory": claims[index]["trajectory"],
            "status": terminal_rows[index]["status"],
            "score": terminal_rows[index]["score"],
        }
        for index in sorted(terminals)
    ]


def verify_journal_replay(
    contract_value: Mapping[str, Any],
    items_value: Mapping[str, Any],
    schedule: Mapping[str, Any],
    journal_path: Path,
) -> list[dict[str, Any]]:
    """Reject a journal unless every scientific claim and successful result replays exactly."""
    contract = validate_contract(contract_value)
    validate_schedule(schedule, contract, items_value)
    journal = ExecutionJournal(journal_path, schedule)
    claims, terminals = journal._state()
    if set(claims) - terminals:
        raise UnknownAfterClaimError(
            "journal has a claimed trajectory without a terminal outcome"
        )
    records = journal._records()
    calibration_claims = [
        row for row in records if row["record_type"] == "calibration_claim"
    ]
    calibration_terminals = [
        row for row in records if row["record_type"] == "calibration_terminal"
    ]
    if len(calibration_claims) != len(contract["models"]) or len(
        calibration_terminals
    ) != len(contract["models"]):
        raise DateShiftValidationError("journal is missing bound calibration records")
    for model in contract["models"]:
        request = calibration_request(contract, model["id"])
        request_bytes = canonical_bytes(request)
        matching_claims = [
            row
            for row in calibration_claims
            if row["model_id"] == model["id"] and row["model_digest"] == model["digest"]
        ]
        matching_terminals = [
            row
            for row in calibration_terminals
            if row["model_id"] == model["id"] and row["model_digest"] == model["digest"]
        ]
        if (
            len(matching_claims) != 1
            or len(matching_terminals) != 1
            or matching_claims[0]["request_sha256"] != bytes_sha256(request_bytes)
            or base64.b64decode(matching_claims[0]["request_base64"], validate=True)
            != request_bytes
            or matching_terminals[0]["request_sha256"] != bytes_sha256(request_bytes)
            or matching_terminals[0]["status"] != "ok"
        ):
            raise DateShiftValidationError("journal calibration binding drifted")
        response = base64.b64decode(
            matching_terminals[0]["response_base64"], validate=True
        )
        if matching_terminals[0]["response_sha256"] != bytes_sha256(response):
            raise DateShiftValidationError("journal calibration response hash drifted")
        content = _response_content(TransportOutcome("ok", response), model["id"])
        if json.loads(content) != {
            "answer": contract["calibration"]["expected_answer"],
            "citation_ids": [contract["calibration"]["citation_id"]],
        }:
            raise DateShiftValidationError("journal calibration response drifted")
    calibration_receipts = [
        {
            "model_id": model["id"],
            "model_digest": model["digest"],
            "request_sha256": canonical_sha256(
                calibration_request(contract, model["id"])
            ),
            "response_sha256": next(
                row["response_sha256"]
                for row in calibration_terminals
                if row["model_id"] == model["id"]
            ),
            "runtime_evidence_sha256": canonical_sha256(contract["runtime_evidence"]),
        }
        for model in contract["models"]
    ]
    terminal_by_index = {
        row["schedule_index"]: row
        for row in records
        if row["record_type"] == "terminal_outcome"
    }
    for index, claim in claims.items():
        trajectory = schedule["trajectories"][index]
        item = items_value["items"][trajectory["item_index"]]
        request_bytes = base64.b64decode(claim["request_base64"], validate=True)
        if claim["request_sha256"] != bytes_sha256(request_bytes):
            raise DateShiftValidationError("journal scientific request hash drifted")
        request = json.loads(request_bytes.decode("utf-8"))
        admission = request.get("study_admission")
        if (
            not isinstance(admission, dict)
            or admission.get("contract_sha256") != canonical_sha256(contract)
            or admission.get("runtime_evidence_sha256")
            != canonical_sha256(contract["runtime_evidence"])
            or admission.get("calibration_receipts_sha256")
            != canonical_sha256(calibration_receipts)
        ):
            raise DateShiftValidationError(
                "journal scientific admission binding drifted"
            )
        expected = _request_for_trajectory(contract, item, trajectory, admission)
        if request != expected:
            raise DateShiftValidationError(
                "journal scientific request does not reconstruct"
            )
        terminal = terminal_by_index[index]
        response = base64.b64decode(terminal["response_base64"], validate=True)
        if terminal["response_sha256"] != bytes_sha256(response):
            raise DateShiftValidationError("journal scientific response hash drifted")
        if terminal["status"] == "ok":
            expected_score = score_response(
                _response_content(
                    TransportOutcome("ok", response), trajectory["model_id"]
                ),
                item,
            )
        elif terminal["status"] in {
            "http_error",
            "transport_error",
            "client_exception",
            "invalid_response",
        }:
            expected_score = invalid_score()
        else:
            raise DateShiftValidationError("journal scientific status is invalid")
        if terminal["score"] != expected_score:
            raise DateShiftValidationError("journal scientific score does not replay")
    return journal_outcomes(journal_path, schedule)


def _legacy_execution_disabled(*_args: Any, **_kwargs: Any) -> Any:
    raise DateShiftValidationError(
        "legacy V2 date-shift execution is disabled; only a sealed V3 bundle may execute"
    )


class ExecutionJournal:  # noqa: F811
    """Removed V2 journal surface; sealed V3 owns all journal writes."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        _legacy_execution_disabled()


class DateShiftRunner:  # noqa: F811
    """Removed V2 dispatch surface; sealed V3 owns all model requests."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        _legacy_execution_disabled()


# V2 accepted loose frame/items/contracts. Keep proposal/audit validators above for
# bundle sealing, but prevent every prior high-level execution/finalization route.
finalize_author_audit = _legacy_execution_disabled  # noqa: F811
build_packet = _legacy_execution_disabled  # noqa: F811
create_schedule = _legacy_execution_disabled  # noqa: F811
