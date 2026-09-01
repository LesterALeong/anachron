"""Deterministic, byte-bounded source excerpts for Routes v2."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from anachron.routes.v2.admission import canonical_json_sha256, load_json_object
from anachron.routes.v2.schema import validate_contract


class ExcerptValidationError(ValueError):
    """Raised when an excerpt cannot be reproduced from its raw revision."""


_ARMS = {
    "pre": "strict_revision",
    "post": "post_snapshot",
}


def _sha_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mapping(value: Any, fields: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ExcerptValidationError(f"{name} has missing or extra fields")
    return value


def _text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ExcerptValidationError(f"{name} is empty or exceeds the frozen UTF-8 bound")
    return value


def _unique_offset(content: str, anchor: str, name: str) -> int:
    if content.count(anchor) != 1:
        raise ExcerptValidationError(f"{name} must occur exactly once in its raw revision")
    return content.index(anchor)


def _take_left(text: str, budget: int) -> str:
    selected: list[str] = []
    used = 0
    for character in reversed(text):
        width = len(character.encode("utf-8"))
        if used + width > budget:
            break
        selected.append(character)
        used += width
    return "".join(reversed(selected))


def _take_right(text: str, budget: int) -> str:
    selected: list[str] = []
    used = 0
    for character in text:
        width = len(character.encode("utf-8"))
        if used + width > budget:
            break
        selected.append(character)
        used += width
    return "".join(selected)


def _excerpt(content: str, anchor: str, maximum: int) -> tuple[str, int, int]:
    anchor_offset = _unique_offset(content, anchor, "anchor")
    anchor_bytes = len(anchor.encode("utf-8"))
    if anchor_bytes > maximum:
        raise ExcerptValidationError("anchor exceeds the frozen excerpt byte cap")
    remaining = maximum - anchor_bytes
    left = _take_left(content[:anchor_offset], remaining // 2)
    right = _take_right(content[anchor_offset + len(anchor) :], remaining - len(left.encode("utf-8")))
    if len(right.encode("utf-8")) < remaining - len(left.encode("utf-8")):
        left = _take_left(content[:anchor_offset], remaining - len(right.encode("utf-8")))
    excerpt = left + anchor + right
    start = len(content[: anchor_offset - len(left)].encode("utf-8"))
    end = start + len(excerpt.encode("utf-8"))
    if len(excerpt.encode("utf-8")) > maximum or content.encode("utf-8")[start:end].decode("utf-8") != excerpt:
        raise ExcerptValidationError("excerpt is not a Unicode-safe bounded raw-revision window")
    return excerpt, start, end


def _receipt(
    *,
    contract: dict[str, Any],
    revalidation_receipt: dict[str, Any],
    raw: dict[str, Any],
    mapping_item: dict[str, Any],
    arm: str,
) -> dict[str, Any]:
    revision_name = _ARMS[arm]
    revision = raw.get(revision_name)
    bound = revalidation_receipt.get(arm)
    if not isinstance(revision, dict) or not isinstance(bound, dict):
        raise ExcerptValidationError("raw/revalidation revision is unavailable")
    content = revision.get("content")
    if not isinstance(content, str) or _sha_text(content) != bound.get("content_sha256"):
        raise ExcerptValidationError("raw revision content no longer matches the revalidation hash")
    if str(revision.get("revision_id")) != bound.get("oldid") or revision.get("revision_url") != bound.get("immutable_url") or revision.get("timestamp") != bound.get("timestamp"):
        raise ExcerptValidationError("raw revision identity no longer matches revalidation")
    anchor = mapping_item[f"{arm}_anchor"]
    excerpt, start, end = _excerpt(content, anchor, contract["source_bounds"]["max_excerpt_utf8_bytes"])
    receipt = {
        "schema_version": "routes-v2-excerpt-receipt-v1",
        "contract_sha256": canonical_json_sha256(contract),
        "revalidation_receipt_sha256": revalidation_receipt["receipt_sha256"],
        "item_id": mapping_item["item_id"],
        "arm": arm,
        "revision": {
            "oldid": bound["oldid"],
            "immutable_url": bound["immutable_url"],
            "timestamp": bound["timestamp"],
            "full_content_sha256": bound["content_sha256"],
        },
        "anchor": {
            "text": anchor,
            "sha256": _sha_text(anchor),
            "utf8_start": len(content[: content.index(anchor)].encode("utf-8")),
            "utf8_end": len(content[: content.index(anchor)].encode("utf-8")) + len(anchor.encode("utf-8")),
        },
        "excerpt": {
            "text": excerpt,
            "sha256": _sha_text(excerpt),
            "utf8_start": start,
            "utf8_end": end,
            "utf8_bytes": len(excerpt.encode("utf-8")),
        },
    }
    receipt["receipt_sha256"] = canonical_json_sha256(receipt)
    return receipt


def build_excerpt_receipts(
    *,
    contract: dict[str, Any],
    revalidation_receipt: dict[str, Any],
    raw_artifact_path: str | Path,
    mapping_item: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive exact pre/post bounded excerpts from one ignored raw artifact."""
    contract = validate_contract(contract)
    raw = load_json_object(raw_artifact_path)
    fields = {
        "item_id", "question", "pre_anchor", "post_anchor", "pre_aliases", "post_aliases",
        "pre_opaque_citation_id", "post_opaque_citation_id", "raw_discovery_artifact_sha256",
        "revalidation_receipt_sha256",
    }
    item = _mapping(mapping_item, fields, "source mapping item")
    bound = contract["source_bounds"]
    for name in ("question", "pre_anchor", "post_anchor", "pre_opaque_citation_id", "post_opaque_citation_id"):
        _text(item[name], name, bound["max_question_utf8_bytes"] if name == "question" else bound["max_anchor_utf8_bytes"])
    if item["pre_anchor"] == item["post_anchor"]:
        raise ExcerptValidationError("pre and post anchors must be distinct")
    for name in ("pre_aliases", "post_aliases"):
        aliases = item[name]
        if not isinstance(aliases, list) or not aliases or len(aliases) > bound["max_aliases_per_answer_set"]:
            raise ExcerptValidationError("answer aliases are outside frozen bounds")
        for alias in aliases:
            _text(alias, name, bound["max_alias_utf8_bytes"])
    if canonical_json_sha256(raw) != item["raw_discovery_artifact_sha256"] or item["revalidation_receipt_sha256"] != revalidation_receipt.get("receipt_sha256"):
        raise ExcerptValidationError("mapping does not bind the exact raw/revalidation artifacts")
    pre_content = raw.get("strict_revision", {}).get("content")
    post_content = raw.get("post_snapshot", {}).get("content")
    if not isinstance(pre_content, str) or not isinstance(post_content, str) or item["pre_anchor"] in post_content or item["post_anchor"] in pre_content:
        raise ExcerptValidationError("anchors must be absent from the opposite revision")
    return (
        _receipt(contract=contract, revalidation_receipt=revalidation_receipt, raw=raw, mapping_item=item, arm="pre"),
        _receipt(contract=contract, revalidation_receipt=revalidation_receipt, raw=raw, mapping_item=item, arm="post"),
    )


def validate_excerpt_receipt(value: Any, *, contract: dict[str, Any]) -> dict[str, Any]:
    """Validate a stored bounded excerpt without retaining full raw content."""
    contract = validate_contract(contract)
    receipt = _mapping(value, {"schema_version", "contract_sha256", "revalidation_receipt_sha256", "item_id", "arm", "revision", "anchor", "excerpt", "receipt_sha256"}, "excerpt receipt")
    if receipt["schema_version"] != "routes-v2-excerpt-receipt-v1" or receipt["contract_sha256"] != canonical_json_sha256(contract) or receipt["arm"] not in _ARMS:
        raise ExcerptValidationError("excerpt receipt identity is invalid")
    unsigned = {key: item for key, item in receipt.items() if key != "receipt_sha256"}
    if receipt["receipt_sha256"] != canonical_json_sha256(unsigned):
        raise ExcerptValidationError("excerpt receipt self-hash drifted")
    revision = _mapping(receipt["revision"], {"oldid", "immutable_url", "timestamp", "full_content_sha256"}, "excerpt revision")
    anchor = _mapping(receipt["anchor"], {"text", "sha256", "utf8_start", "utf8_end"}, "excerpt anchor")
    excerpt = _mapping(receipt["excerpt"], {"text", "sha256", "utf8_start", "utf8_end", "utf8_bytes"}, "excerpt")
    if not all(isinstance(value, str) and value for value in revision.values()) or anchor["sha256"] != _sha_text(anchor["text"]) or excerpt["sha256"] != _sha_text(excerpt["text"]) or excerpt["utf8_bytes"] != len(excerpt["text"].encode("utf-8")) or excerpt["utf8_bytes"] > contract["source_bounds"]["max_excerpt_utf8_bytes"] or not (0 <= excerpt["utf8_start"] <= anchor["utf8_start"] < anchor["utf8_end"] <= excerpt["utf8_end"]):
        raise ExcerptValidationError("excerpt receipt bounds or hashes are invalid")
    local_start = anchor["utf8_start"] - excerpt["utf8_start"]
    local_end = anchor["utf8_end"] - excerpt["utf8_start"]
    if excerpt["text"].encode("utf-8")[local_start:local_end] != anchor["text"].encode("utf-8"):
        raise ExcerptValidationError("excerpt does not contain its anchor at the recorded byte offsets")
    return receipt
