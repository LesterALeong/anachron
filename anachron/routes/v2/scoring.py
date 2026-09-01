"""Deterministic v2 response scoring."""

from __future__ import annotations

import json
import unicodedata
from typing import Any


def _normal(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def score_response(response_text: str, *, expected_answer: str) -> dict[str, Any]:
    """Score a bounded answer without a judge model.

    Development uses the post-only indicator: an exact expected answer is 1;
    ABSTAIN, malformed output, and every other answer are 0 with a status.
    """
    try:
        value = json.loads(response_text)
    except (TypeError, json.JSONDecodeError):
        return {"answer_label": "invalid_output", "post_only": 0}
    if not isinstance(value, dict) or set(value) != {"answer", "citation_id"}:
        return {"answer_label": "invalid_output", "post_only": 0}
    answer = value["answer"]
    citation_id = value["citation_id"]
    if not isinstance(answer, str) or not isinstance(citation_id, str):
        return {"answer_label": "invalid_output", "post_only": 0}
    if answer == "ABSTAIN":
        return {"answer_label": "abstain_or_other", "post_only": 0}
    if _normal(answer) == _normal(expected_answer):
        return {"answer_label": "post_only", "post_only": 1}
    return {"answer_label": "abstain_or_other", "post_only": 0}
