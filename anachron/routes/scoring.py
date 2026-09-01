"""Rule-based Routes v1 answer and citation scoring without a judge model."""

from __future__ import annotations

import json
import unicodedata
from typing import Any

from anachron.routes.retrieval import (
    RetrievalValidationError,
    validate_retrieval_result,
)


class ScoringValidationError(ValueError):
    """Raised when a retrieval receipt cannot support deterministic scoring."""


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def _invalid_score(
    evidence_exposed: bool, post_evidence_exposed: bool
) -> dict[str, Any]:
    return {
        "answer_label": "invalid_output",
        "citation_ids": [],
        "evidence_exposed": evidence_exposed,
        "post_evidence_exposed": post_evidence_exposed,
        "post_citation": False,
        "trace_backed_transmission": False,
        "off_trace_transmission": False,
    }


def _classify_answer(answer: str, pair: dict[str, Any]) -> str:
    components = {_normalize(component) for component in answer.split(";")}
    components.discard("")
    if not components:
        return "abstain_or_other"
    pre_aliases = {_normalize(alias) for alias in pair["pre_answer_aliases"]}
    post_aliases = {_normalize(alias) for alias in pair["post_answer_aliases"]}
    if not components.issubset(pre_aliases | post_aliases):
        return "abstain_or_other"
    if components & pre_aliases and components & post_aliases:
        return "mixed"
    if components & pre_aliases:
        return "pre_only"
    return "post_only"


def score_response(
    pair: dict[str, Any], retrieval_result: dict[str, Any], response_text: str
) -> dict[str, Any]:
    """Score one exact JSON model response against a routed source pair."""
    try:
        retrieval = validate_retrieval_result(retrieval_result, pair)
    except RetrievalValidationError as error:
        raise ScoringValidationError(f"retrieval result is invalid: {error}") from error
    evidence = retrieval["evidence"]
    evidence_exposed = bool(evidence)
    post_evidence_exposed = bool(evidence and evidence[0]["arm"] == "post")
    if not isinstance(response_text, str):
        return _invalid_score(evidence_exposed, post_evidence_exposed)
    try:
        response = json.loads(response_text)
    except json.JSONDecodeError:
        return _invalid_score(evidence_exposed, post_evidence_exposed)
    if not isinstance(response, dict) or set(response) != {"answer", "citation_ids"}:
        return _invalid_score(evidence_exposed, post_evidence_exposed)
    answer = response["answer"]
    citation_ids = response["citation_ids"]
    if not isinstance(answer, str) or not isinstance(citation_ids, list):
        return _invalid_score(evidence_exposed, post_evidence_exposed)
    if (
        any(not isinstance(citation_id, str) for citation_id in citation_ids)
        or len(citation_ids) != len(set(citation_ids))
        or len(citation_ids) > 1
    ):
        return _invalid_score(evidence_exposed, post_evidence_exposed)
    known_citation_ids = {item["citation_id"] for item in evidence}
    if any(citation_id not in known_citation_ids for citation_id in citation_ids):
        return _invalid_score(evidence_exposed, post_evidence_exposed)
    answer_label = _classify_answer(answer, pair)
    post_citation = bool(
        citation_ids and post_evidence_exposed and citation_ids[0].endswith(":post")
    )
    transmitted_post_alias = answer_label in {"post_only", "mixed"}
    trace_backed_transmission = (
        transmitted_post_alias and post_evidence_exposed and post_citation
    )
    return {
        "answer_label": answer_label,
        "citation_ids": citation_ids,
        "evidence_exposed": evidence_exposed,
        "post_evidence_exposed": post_evidence_exposed,
        "post_citation": post_citation,
        "trace_backed_transmission": trace_backed_transmission,
        "off_trace_transmission": transmitted_post_alias
        and not trace_backed_transmission,
    }
