"""Human review packets bound to the exact bounded source projections."""

from __future__ import annotations

import json
from typing import Any

from anachron.routes.v2.manifest import _projection, canonical_json_sha256

_CERTIFICATION = "I inspected every listed bounded excerpt and its immutable receipt projection."


def decision_template(draft: Any, contract: dict[str, Any]) -> dict[str, Any]:
    """Return a blank decision template that cannot approve an unseen projection."""
    if not isinstance(draft, dict) or draft.get("schema_version") != "routes-v2-pending-draft-v2" or draft.get("contract_sha256") != canonical_json_sha256(contract) or not isinstance(draft.get("pairs"), list):
        raise ValueError("decision template requires a bounded v2 pending draft")
    return {
        "schema_version": contract["source_gate"]["decision_schema"],
        "study_phase": draft["study_phase"],
        "pending_draft_sha256": canonical_json_sha256(draft),
        "validator_id": "",
        "reviewed_at": "",
        "certification": _CERTIFICATION,
        "decisions": [
            {
                "item_id": pair["item_id"],
                "decision": "",
                "reason": "",
                "reviewed_projection": _projection(pair),
                "reviewed_projection_sha256": canonical_json_sha256(_projection(pair)),
            }
            for pair in sorted(draft["pairs"], key=lambda pair: pair["item_id"])
        ],
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def render_review_markdown(draft: Any, contract: dict[str, Any]) -> str:
    """Render only the complete already bounded human-review projection as Markdown."""
    template = decision_template(draft, contract)
    lines = [
        "# Routes v2 source review", "",
        "Review every projection below. Select PASS only when the bounded excerpts support the mapped answer sets and every immutable binding is correct; otherwise select REJECT.",
        "", "Required certification:", "", _CERTIFICATION, "",
    ]
    for pair in sorted(draft["pairs"], key=lambda item: item["item_id"]):
        lines.extend([
            f"## {pair['item_id']}", "", "Question:", "", pair["question"], "",
            "Pre anchor:", "", _json(pair["pre_anchor"]), "", "Post anchor:", "", _json(pair["post_anchor"]), "",
            "Pre aliases:", "", _json(pair["pre_aliases"]), "", "Post aliases:", "", _json(pair["post_aliases"]), "",
            "Pre bounded excerpt:", "", "```text", pair["pre_excerpt"]["text"], "```", "",
            "Post bounded excerpt:", "", "```text", pair["post_excerpt"]["text"], "```", "",
            "Pre immutable revision (oldid, URL, timestamp, full-content hash):", "", _json(pair["pre_revision"]), "",
            "Post immutable revision (oldid, URL, timestamp, full-content hash):", "", _json(pair["post_revision"]), "",
            "Revalidation and excerpt receipt hashes:", "", _json(pair["source_provenance"]), "",
            f"Mapping item SHA-256: {pair['mapping_item_sha256']}", "",
            f"Answer-rules SHA-256: {pair['answer_rules_sha256']}", "",
            f"Complete reviewed-projection SHA-256: {canonical_json_sha256(_projection(pair))}", "",
        ])
    lines.extend(["## Blank decision template", "", "Set `validator_id` and canonical UTC `reviewed_at`, then set every decision to exact `PASS` or `REJECT` with a reason. Do not alter a reviewed projection or its hash.", "", "```json", _json(template), "```", ""])
    return "\n".join(lines)
