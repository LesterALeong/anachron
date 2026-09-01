"""Deterministic scorer derived only from sealed manifest answer rules."""

from __future__ import annotations

import json
import unicodedata
from typing import Any


def _normal(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def _matches(answer: str, aliases: list[str]) -> bool:
    return _normal(answer) in {_normal(alias) for alias in aliases}


def score_response(
    response_text: str,
    *,
    answer_rules: dict[str, Any],
    expected_citation_id: str,
) -> dict[str, Any]:
    """Return one frozen label using only sealed aliases and citation identity."""
    invalid = {"answer_label": "invalid_output", "post_only": 0}
    try:
        value = json.loads(response_text)
    except (TypeError, json.JSONDecodeError):
        return invalid
    if not isinstance(value, dict) or set(value) != {"answer", "citation_id"} or not isinstance(value["answer"], str) or value["citation_id"] != expected_citation_id:
        return invalid
    try:
        pre = answer_rules["pre_aliases"]
        post = answer_rules["post_aliases"]
        abstentions = answer_rules["abstention_aliases"]
        if not all(isinstance(group, list) and all(isinstance(alias, str) for alias in group) for group in (pre, post, abstentions)):
            return invalid
    except KeyError:
        return invalid
    if _matches(value["answer"], post):
        return {"answer_label": "post_only", "post_only": 1}
    if _matches(value["answer"], pre):
        return {"answer_label": "pre_only", "post_only": 0}
    if _matches(value["answer"], abstentions):
        return {"answer_label": "abstain", "post_only": 0}
    return {"answer_label": "other", "post_only": 0}
