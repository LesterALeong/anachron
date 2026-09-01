"""Prepare and apply explicit human decisions for Routes v1 source curation."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from anachron.routes.curation import CurationInputError, prepare_draft
from anachron.routes.manifest import (
    ManifestValidationError,
    canonical_json_sha256,
    validate_curation_draft_with_discovery,
)
from anachron.routes.schema import validate_contract_document
from anachron.routes.sources import SourceDiscoveryError, validate_exante_sampling_frame

_DECISION_SCHEMA_VERSION = "routes-v1-human-curation-decisions"
_PERSONAL_CHECK_CERTIFICATION = (
    "I personally checked the cited pre/post evidence and independently made every decision in this file."
)


class HumanReviewError(ValueError):
    """Raised when a human-review artifact is incomplete or does not bind its draft."""


def _mapping(value: Any, path: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise HumanReviewError(f"{path} has missing or extra fields")
    return value


def _non_empty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HumanReviewError(f"{path} must be a non-empty string")
    return value


def _canonical_utc_timestamp(value: Any, path: str) -> str:
    from anachron.routes.manifest import _utc_timestamp

    try:
        _utc_timestamp(value, path)
    except ManifestValidationError as error:
        raise HumanReviewError(str(error)) from error
    return value


def _validated_pending_draft(draft: Any) -> dict[str, Any]:
    if not isinstance(draft, dict):
        raise HumanReviewError("draft must be an object")
    for pair in draft.get("pairs", []):
        curation = pair.get("curation") if isinstance(pair, dict) else None
        if not isinstance(curation, dict) or curation.get("status") != "codex_prepared_pending_human":
            raise HumanReviewError("review artifacts can only be generated from a pending human draft")
    if not draft.get("pairs"):
        raise HumanReviewError("draft must contain at least one pair")
    return draft


def _anchor_excerpt(snippet: str, anchor: str, context: int = 180) -> str:
    """Return a compact review excerpt entirely contained in the stored snippet."""
    offset = snippet.find(anchor)
    if offset < 0:
        raise HumanReviewError("draft evidence snippet does not contain its declared anchor")
    start = max(0, offset - context)
    end = min(len(snippet), offset + len(anchor) + context)
    return snippet[start:end].replace("\r\n", "\n").replace("\r", "\n")


def _indented(text: str) -> str:
    return "\n".join(f"    {line}" for line in text.splitlines() or [""])


def build_review_packet(draft: Any) -> str:
    """Render a readable packet without converting any pending item into approval."""
    checked = _validated_pending_draft(draft)
    lines = [
        "# Routes v1 human source-curation review",
        "",
        "This packet is evidence for a human decision, not a decision itself. Do not mark an item PASS unless you personally checked the immutable revision links and the anchor-centered excerpts below. A PASS means the question, aliases, and changed fact are supported by the cited pre/post revisions. A REJECT leaves the draft pending and blocks sealing.",
        "",
        f"- Draft SHA-256: `{canonical_json_sha256(checked)}`",
        f"- Sampling-frame SHA-256: `{checked['sampling_frame_sha256']}`",
        f"- Curation-input SHA-256: `{checked['curation_input_sha256']}`",
        "- Decision file: complete the paired machine-readable template generated from this exact draft.",
        "",
        "## Accepted source pairs",
        "",
    ]
    for pair in sorted(checked["pairs"], key=lambda item: item["item_id"]):
        pre = pair["pre"]
        post = pair["post"]
        lines.extend(
            [
                f"### {pair['item_id']} — {pair['topic']} ({pair['cutoff_year']})",
                "",
                f"- Study phase: `{pair['study_phase']}`",
                f"- Change type / semantic strength: `{pair['change_type']}` / `{pair['semantic_strength']}`",
                f"- Question: {json.dumps(pair['question'], ensure_ascii=False)}",
                f"- Pre-answer aliases: {json.dumps(pair['pre_answer_aliases'], ensure_ascii=False)}",
                f"- Post-answer aliases: {json.dumps(pair['post_answer_aliases'], ensure_ascii=False)}",
                f"- Notes: {pair['notes']}",
                f"- Pre revision: [{pre['revision_id']}]({pre['revision_url']}) at `{pre['timestamp']}`",
                f"- Post revision: [{post['revision_id']}]({post['revision_url']}) at `{post['timestamp']}`",
                f"- Pre anchor: {json.dumps(pair['pre_anchor'], ensure_ascii=False)}",
                f"- Post anchor: {json.dumps(pair['post_anchor'], ensure_ascii=False)}",
                "",
                "Pre evidence excerpt:",
                "",
                _indented(_anchor_excerpt(pre["snippet"], pair["pre_anchor"])),
                "",
                "Post evidence excerpt:",
                "",
                _indented(_anchor_excerpt(post["snippet"], pair["post_anchor"])),
                "",
                f"- [ ] PASS `{pair['item_id']}`: I personally checked both immutable revisions and the claimed mapping.",
                f"- [ ] REJECT `{pair['item_id']}`: This pair must remain unapproved; record the reason in the decision file.",
                "",
            ]
        )
    lines.extend(["## Rejected topics", ""])
    for rejected in sorted(
        checked["rejected_topics"], key=lambda item: (item["study_phase"], item["title"])
    ):
        lines.extend(
            [
                f"- `{rejected['study_phase']}` / {rejected['title']}: {rejected['reason']}",
                f"  - [ ] ACKNOWLEDGE REJECTION `{rejected['study_phase']}` / {rejected['title']}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Completion instructions",
            "",
            "Use the supplied JSON template unchanged in structure. Enter your nonempty validator ID, a canonical UTC timestamp, a PASS or REJECT decision for every pair, acknowledgement for every rejection, and the exact personal-check certification. The apply command refuses partial, duplicate, rejected, tampered, or uncertified decisions. It writes a separate reviewed draft and never overwrites this pending draft.",
            "",
        ]
    )
    return "\n".join(lines)


def build_decision_template(draft: Any) -> dict[str, Any]:
    """Create a blank, stable-ID decision template bound to one pending draft."""
    checked = _validated_pending_draft(draft)
    return {
        "schema_version": _DECISION_SCHEMA_VERSION,
        "draft_sha256": canonical_json_sha256(checked),
        "sampling_frame_sha256": checked["sampling_frame_sha256"],
        "curation_input_sha256": checked["curation_input_sha256"],
        "validator_id": "",
        "validated_at": "",
        "overall_certification": "",
        "pair_decisions": [
            {"item_id": pair["item_id"], "decision": "", "notes": ""}
            for pair in sorted(checked["pairs"], key=lambda item: item["item_id"])
        ],
        "rejection_acknowledgements": [
            {
                "study_phase": rejected["study_phase"],
                "title": rejected["title"],
                "acknowledged": False,
            }
            for rejected in sorted(
                checked["rejected_topics"], key=lambda item: (item["study_phase"], item["title"])
            )
        ],
    }


def _validate_decisions(decisions: Any, draft: dict[str, Any]) -> tuple[str, str]:
    value = _mapping(
        decisions,
        "human decisions",
        {
            "schema_version",
            "draft_sha256",
            "sampling_frame_sha256",
            "curation_input_sha256",
            "validator_id",
            "validated_at",
            "overall_certification",
            "pair_decisions",
            "rejection_acknowledgements",
        },
    )
    if value["schema_version"] != _DECISION_SCHEMA_VERSION:
        raise HumanReviewError("human decisions schema_version is invalid")
    if value["draft_sha256"] != canonical_json_sha256(draft):
        raise HumanReviewError("human decisions do not bind the exact pending draft")
    if value["sampling_frame_sha256"] != draft["sampling_frame_sha256"]:
        raise HumanReviewError("human decisions do not bind the draft sampling frame")
    if value["curation_input_sha256"] != draft["curation_input_sha256"]:
        raise HumanReviewError("human decisions do not bind the draft curation input")
    validator_id = _non_empty_string(value["validator_id"], "human decisions.validator_id")
    validated_at = _canonical_utc_timestamp(value["validated_at"], "human decisions.validated_at")
    if value["overall_certification"] != _PERSONAL_CHECK_CERTIFICATION:
        raise HumanReviewError("human decisions require the explicit personal-check certification")
    expected_pairs = {pair["item_id"] for pair in draft["pairs"]}
    if not isinstance(value["pair_decisions"], list):
        raise HumanReviewError("human decisions.pair_decisions must be a list")
    seen_pairs: set[str] = set()
    for index, decision in enumerate(value["pair_decisions"]):
        item = _mapping(decision, f"human decisions.pair_decisions[{index}]", {"item_id", "decision", "notes"})
        item_id = _non_empty_string(item["item_id"], f"human decisions.pair_decisions[{index}].item_id")
        if item_id in seen_pairs or item_id not in expected_pairs:
            raise HumanReviewError("human decisions contain a duplicate or unknown pair item_id")
        seen_pairs.add(item_id)
        if item["decision"] != "PASS":
            raise HumanReviewError("every source pair must have an explicit PASS decision")
        if not isinstance(item["notes"], str):
            raise HumanReviewError("human decisions pair notes must be a string")
    if seen_pairs != expected_pairs:
        raise HumanReviewError("human decisions must account for every source pair")
    expected_rejections = {
        (rejected["study_phase"], rejected["title"])
        for rejected in draft["rejected_topics"]
    }
    if not isinstance(value["rejection_acknowledgements"], list):
        raise HumanReviewError("human decisions.rejection_acknowledgements must be a list")
    seen_rejections: set[tuple[str, str]] = set()
    for index, acknowledgement in enumerate(value["rejection_acknowledgements"]):
        item = _mapping(
            acknowledgement,
            f"human decisions.rejection_acknowledgements[{index}]",
            {"study_phase", "title", "acknowledged"},
        )
        key = (
            _non_empty_string(item["study_phase"], f"human decisions.rejection_acknowledgements[{index}].study_phase"),
            _non_empty_string(item["title"], f"human decisions.rejection_acknowledgements[{index}].title"),
        )
        if key in seen_rejections or key not in expected_rejections:
            raise HumanReviewError("human decisions contain a duplicate or unknown rejection")
        seen_rejections.add(key)
        if item["acknowledged"] is not True:
            raise HumanReviewError("every rejected topic must be explicitly acknowledged")
    if seen_rejections != expected_rejections:
        raise HumanReviewError("human decisions must account for every rejected topic")
    return validator_id, validated_at


def apply_human_decisions(
    draft: Any,
    decisions: Any,
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
    curation_input: Any,
    discovery_directory: str | Path,
) -> dict[str, Any]:
    """Return a separately reviewed draft only after explicit human certification."""
    checked_draft = _validated_pending_draft(draft)
    try:
        checked_contract = validate_contract_document(contract)
        validate_exante_sampling_frame(checked_contract, sampling_frame)
        rebuilt = prepare_draft(curation_input, checked_contract, sampling_frame, discovery_directory)
        validate_curation_draft_with_discovery(
            checked_draft, checked_contract, sampling_frame, discovery_directory
        )
    except (CurationInputError, ManifestValidationError, SourceDiscoveryError) as error:
        raise HumanReviewError(f"draft provenance validation failed: {error}") from error
    if canonical_json_sha256(curation_input) != checked_draft["curation_input_sha256"]:
        raise HumanReviewError("curation input hash does not bind the pending draft")
    if canonical_json_sha256(rebuilt) != canonical_json_sha256(checked_draft):
        raise HumanReviewError("pending draft differs from the reproducibly rebuilt curation input")
    validator_id, validated_at = _validate_decisions(decisions, checked_draft)
    reviewed = copy.deepcopy(checked_draft)
    for pair in reviewed["pairs"]:
        pair["curation"] = {
            "status": "human_validated",
            "human_validator_id": validator_id,
            "human_validated_at": validated_at,
        }
    try:
        validate_curation_draft_with_discovery(
            reviewed, checked_contract, sampling_frame, discovery_directory
        )
    except ManifestValidationError as error:
        raise HumanReviewError(f"reviewed draft provenance validation failed: {error}") from error
    return reviewed
