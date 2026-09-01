"""Deterministic evidence routing from a sealed Routes v1 source manifest."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from anachron.routes.manifest import ManifestValidationError, validate_manifest


class RetrievalValidationError(ValueError):
    """Raised when a retrieval request or receipt is not bound to a sealed pair."""


def _utc_timestamp(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RetrievalValidationError(f"{path} must be a canonical UTC timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise RetrievalValidationError(f"{path} must be a canonical UTC timestamp") from error
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise RetrievalValidationError(f"{path} must be a canonical UTC timestamp")
    return value


def _evidence(item_id: str, arm: str, source: dict[str, Any]) -> dict[str, Any]:
    return {
        "citation_id": f"{item_id}:{arm}",
        "arm": arm,
        "revision_id": source["revision_id"],
        "timestamp": source["timestamp"],
        "revision_url": source["revision_url"],
        "content_sha256": source["content_sha256"],
        "snippet": source["snippet"],
        "snippet_sha256": source["snippet_sha256"],
        "displayed_document_date": source["displayed_document_date"],
    }


def retrieve(
    manifest: dict[str, Any],
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
    *,
    item_id: str,
    condition: str,
    retrieved_at: str,
) -> dict[str, Any]:
    """Return only the contract-selected evidence and one retrieval-time trace event."""
    try:
        checked = validate_manifest(manifest, contract, sampling_frame)
    except ManifestValidationError as error:
        raise RetrievalValidationError(f"sealed manifest is invalid: {error}") from error
    if not isinstance(item_id, str) or not item_id:
        raise RetrievalValidationError("item_id must be a non-empty string")
    if condition not in {"no_tool", "strict", "misdated"}:
        raise RetrievalValidationError("condition is invalid")
    retrieved_at = _utc_timestamp(retrieved_at, "retrieved_at")
    matches = [pair for pair in checked["pairs"] if pair["item_id"] == item_id]
    if len(matches) != 1:
        raise RetrievalValidationError("item_id does not identify exactly one sealed pair")
    pair = matches[0]
    evidence = []
    if condition == "strict":
        evidence = [_evidence(item_id, "pre", pair["pre"])]
    elif condition == "misdated":
        evidence = [_evidence(item_id, "post", pair["post"])]
    return {
        "item_id": item_id,
        "condition": condition,
        "evidence": evidence,
        "trace_event": {
            "event_type": "routes_retrieval",
            "created_at": retrieved_at,
            "item_id": item_id,
            "condition": condition,
            "evidence_ids": [item["citation_id"] for item in evidence],
        },
    }


def validate_retrieval_result(result: Any, pair: dict[str, Any]) -> dict[str, Any]:
    """Reject a receipt unless it exactly matches the deterministic route selection."""
    if not isinstance(result, dict) or set(result) != {
        "item_id",
        "condition",
        "evidence",
        "trace_event",
    }:
        raise RetrievalValidationError("retrieval result has missing or extra fields")
    item_id = pair.get("item_id")
    if result["item_id"] != item_id:
        raise RetrievalValidationError("retrieval result item_id does not match pair")
    condition = result["condition"]
    if condition not in {"no_tool", "strict", "misdated"}:
        raise RetrievalValidationError("retrieval result condition is invalid")
    trace = result["trace_event"]
    if not isinstance(trace, dict) or set(trace) != {
        "event_type",
        "created_at",
        "item_id",
        "condition",
        "evidence_ids",
    }:
        raise RetrievalValidationError("retrieval trace event has missing or extra fields")
    if (
        trace["event_type"] != "routes_retrieval"
        or trace["item_id"] != item_id
        or trace["condition"] != condition
    ):
        raise RetrievalValidationError("retrieval trace event does not bind the route")
    _utc_timestamp(trace["created_at"], "retrieval trace created_at")
    expected = []
    if condition == "strict":
        expected = [_evidence(item_id, "pre", pair["pre"])]
    elif condition == "misdated":
        expected = [_evidence(item_id, "post", pair["post"])]
    if result["evidence"] != expected:
        raise RetrievalValidationError("retrieval evidence is not the exact routed source snippet")
    if trace["evidence_ids"] != [item["citation_id"] for item in expected]:
        raise RetrievalValidationError("retrieval trace evidence ids do not match evidence")
    return result
