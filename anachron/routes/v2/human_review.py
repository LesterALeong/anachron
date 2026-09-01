"""Explicit v2 source-decision templates; no code path infers approval."""

from __future__ import annotations

from typing import Any

from anachron.routes.v2.manifest import canonical_json_sha256


def decision_template(draft: Any, contract: dict[str, Any]) -> dict[str, Any]:
    """Return a blank direct-binding PASS/REJECT template for one pending draft."""
    phase = draft.get("study_phase") if isinstance(draft, dict) else None
    expected = 6 if phase == "development" else 18 if phase == "pilot" else 36 if phase == "confirmatory" else 0
    if not isinstance(draft, dict) or draft.get("schema_version") != "routes-v2-pending-draft" or draft.get("contract_sha256") != canonical_json_sha256(contract) or not isinstance(draft.get("pairs"), list) or len(draft["pairs"]) != expected or any(pair.get("study_phase") != phase for pair in draft["pairs"]):
        raise ValueError("decision template requires the exact selected phase pending draft")
    return {
        "schema_version": "routes-v2-source-decisions",
        "study_phase": phase,
        "pending_draft_sha256": canonical_json_sha256(draft),
        "validator_id": "",
        "decisions": [
            {"item_id": pair["item_id"], "decision": "", "reason": ""}
            for pair in sorted(draft["pairs"], key=lambda pair: pair["item_id"])
        ],
    }
