"""Opaque, date-only v2 delivery packets.

The post-truthful and post-misdated-eligible primary arms are byte-identical
except for ``presented_document_date``.  Provenance remains in sealed storage,
never in model-visible text.
"""

from __future__ import annotations

import json
import re
from typing import Any

from anachron.routes.v2.manifest import (
    ManifestValidationError,
    canonical_json_sha256,
    validate_manifest,
)


class RetrievalValidationError(ValueError):
    """Raised when a v2 delivery packet leaks a route or differs beyond date."""


_FORBIDDEN = re.compile(r"(?:strict_pre_truthful|post_truthful|post_misdated_eligible|oldid=|revision|routes-v[12]|https?://)", re.IGNORECASE)
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _pair(manifest: dict[str, Any], item_id: str) -> dict[str, Any]:
    matches = [pair for pair in manifest["pairs"] if pair["item_id"] == item_id]
    if len(matches) != 1:
        raise RetrievalValidationError("item_id must identify exactly one sealed v2 pair")
    return matches[0]


def delivery_packet(manifest: dict[str, Any], contract: dict[str, Any], *, item_id: str, condition: str) -> dict[str, Any]:
    """Return one model-visible packet with no route names or source identifiers."""
    try:
        checked = validate_manifest(manifest, contract)
    except ManifestValidationError as error:
        raise RetrievalValidationError(f"sealed v2 manifest is invalid: {error}") from error
    if condition not in contract["conditions"]:
        raise RetrievalValidationError("condition is not declared by the v2 contract")
    pair = _pair(checked, item_id)
    if condition == "strict_pre_truthful":
        content = pair["pre_content"]
        citation_id = pair["pre_opaque_citation_id"]
        date = pair["strict_document_date"]
    elif condition == "post_truthful":
        content = pair["post_content"]
        citation_id = pair["opaque_citation_id"]
        date = pair["truthful_document_date"]
    else:
        content = pair["post_content"]
        citation_id = pair["opaque_citation_id"]
        date = pair["misdated_eligible_document_date"]
    packet = {
        "question": pair["question"],
        "document": {
            "citation_id": citation_id,
            "content": content,
            "presented_document_date": date,
        },
        "temporal_cutoff_date": f"{pair['cutoff_year']}-12-31",
        "abstention_token": "ABSTAIN",
    }
    scan_prompt_packet(packet)
    return packet


def scan_prompt_packet(packet: Any) -> None:
    """Fail closed if model-visible content contains route or provenance markers."""
    if not isinstance(packet, dict) or set(packet) != {"question", "document", "temporal_cutoff_date", "abstention_token"}:
        raise RetrievalValidationError("model-visible packet has missing or extra fields")
    document = packet["document"]
    if not isinstance(document, dict) or set(document) != {"citation_id", "content", "presented_document_date"}:
        raise RetrievalValidationError("model-visible document has missing or extra fields")
    if packet["abstention_token"] != "ABSTAIN":
        raise RetrievalValidationError("v2 abstention token is invalid")
    for name, value in (("question", packet["question"]), ("citation_id", document["citation_id"]), ("content", document["content"])):
        if not isinstance(value, str) or not value:
            raise RetrievalValidationError(f"model-visible {name} must be non-empty text")
        if _FORBIDDEN.search(value):
            raise RetrievalValidationError(f"model-visible {name} leaks a forbidden route marker")
        if _DATE.search(value):
            raise RetrievalValidationError(f"model-visible {name} leaks an actual source date")
    date = document["presented_document_date"]
    if not isinstance(date, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) is None:
        raise RetrievalValidationError("model-visible presented_document_date must be the sole YYYY-MM-DD date")
    cutoff = packet["temporal_cutoff_date"]
    if not isinstance(cutoff, str) or re.fullmatch(r"\d{4}-12-31", cutoff) is None:
        raise RetrievalValidationError("model-visible temporal cutoff must be YYYY-12-31")


def primary_packets(manifest: dict[str, Any], contract: dict[str, Any], item_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prove the primary packets differ only in the displayed document date."""
    truthful = delivery_packet(manifest, contract, item_id=item_id, condition="post_truthful")
    misdated = delivery_packet(manifest, contract, item_id=item_id, condition="post_misdated_eligible")
    left = json.loads(json.dumps(truthful, sort_keys=True))
    right = json.loads(json.dumps(misdated, sort_keys=True))
    left["document"]["presented_document_date"] = "DATE"
    right["document"]["presented_document_date"] = "DATE"
    if left != right:
        raise RetrievalValidationError("primary arms differ in more than the visible document date")
    if truthful["document"]["presented_document_date"] == misdated["document"]["presented_document_date"]:
        raise RetrievalValidationError("primary arms do not vary the visible document date")
    return truthful, misdated


def delivered_evidence_sha256(packet: dict[str, Any]) -> str:
    """Return the trace binding for exactly the document delivered to the model."""
    scan_prompt_packet(packet)
    return canonical_json_sha256(packet["document"])
