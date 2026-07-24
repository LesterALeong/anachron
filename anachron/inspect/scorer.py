"""Inspect scorer that grades tool-call leakage (guarded ``inspect_ai`` import).

The scorer walks ``TaskState.messages``, pairs each assistant tool call with its
result message (matched on ``tool_call_id``), reconstructs
:class:`anachron.core.leakage.ToolInteraction` objects by parsing item ids and
ISO dates out of the tool result text, then delegates the actual leakage math to
:func:`anachron.core.leakage.score_interactions`. The score value is the primary
TCLR metric; offenders and secondary metrics ride along in the metadata.
"""

from __future__ import annotations

import re
from datetime import date

from anachron.core.leakage import CorpusItem, ToolInteraction, score_interactions
from anachron.data.corpus import get_corpus

try:
    from inspect_ai.scorer import Score, Target, mean, scorer, stderr
    from inspect_ai.solver import TaskState

    _INSPECT_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without inspect_ai
    _INSPECT_AVAILABLE = False

_IMPORT_HINT = (
    "anachron.inspect requires the optional 'inspect_ai' dependency. "
    "Install it with: pip install 'anachron[inspect]'"
)

# Matches the "[id] (YYYY-MM-DD)" prefix emitted by anachron.inspect.tools.
_RESULT_ITEM_RE = re.compile(r"\[(?P<id>[^\]]+)\]\s*\((?P<date>\d{4}-\d{2}-\d{2})\)")
# Matches any ISO date appearing in tool-call arguments (query-leak signal).
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _parse_iso(value: str) -> date:
    """Parse a ``YYYY-MM-DD`` string into a :class:`datetime.date`."""
    return date(int(value[0:4]), int(value[5:7]), int(value[8:10]))


def _parse_query_dates(arguments) -> list[date]:
    """Extract ISO dates from stringified tool-call arguments."""
    text = str(arguments)
    dates: list[date] = []
    for match in _ISO_DATE_RE.finditer(text):
        try:
            dates.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            continue
    return dates


def _parse_returned_items(content: str, corpus_by_id):
    """Rebuild returned CorpusItems by parsing ids/dates from result text.

    Ids resolve against the canonical corpus so finance/entity metadata (needed
    for survivorship) is preserved. Ids absent from the corpus are reconstructed
    from the parsed date alone (no entity, hence no survivorship semantics).
    """
    items = []
    for match in _RESULT_ITEM_RE.finditer(content):
        item_id = match.group("id")
        canonical = corpus_by_id.get(item_id)
        if canonical is not None:
            items.append(canonical)
        else:
            items.append(
                CorpusItem(id=item_id, text="", publish_date=_parse_iso(match.group("date")))
            )
    return items


def _build_interactions(state: "TaskState") -> list[ToolInteraction]:
    """Pair assistant tool calls with their result messages into interactions."""
    corpus_by_id = {item.id: item for item in get_corpus()}

    # Index tool result messages by the call id they answer.
    results_by_call_id: dict[str, str] = {}
    for message in state.messages:
        if message.role == "tool":
            call_id = getattr(message, "tool_call_id", None)
            if call_id is not None:
                results_by_call_id[call_id] = message.text

    interactions: list[ToolInteraction] = []
    for message in state.messages:
        if message.role != "assistant":
            continue
        for call in message.tool_calls or []:
            content = results_by_call_id.get(call.id, "")
            interactions.append(
                ToolInteraction(
                    tool=call.function,
                    query=str(call.arguments),
                    query_dates=_parse_query_dates(call.arguments),
                    returned_items=_parse_returned_items(content, corpus_by_id),
                )
            )
    return interactions


def tool_call_leakage():
    """Build the ``tool_call_leakage`` scorer.

    The scorer reads the sample's as-of date from ``state.metadata["as_of"]``
    (an ISO string), scores all tool interactions in the transcript, and returns
    a :class:`Score` whose value is the TCLR.
    """
    if not _INSPECT_AVAILABLE:
        raise ImportError(_IMPORT_HINT)

    @scorer(metrics=[mean(), stderr()])
    def _tool_call_leakage():
        async def score(state: "TaskState", target: "Target") -> "Score":
            as_of_raw = state.metadata.get("as_of")
            if as_of_raw is None:
                raise ValueError(
                    "Anachron's tool_call_leakage scorer requires each sample to set "
                    "metadata['as_of'] (an ISO date string); none was found."
                )
            as_of = _parse_iso(as_of_raw)
            interactions = _build_interactions(state)
            result = score_interactions(interactions, as_of)

            explanation = (
                f"TCLR={result.tclr:.3f} "
                f"({result.result_leaks}/{result.total_interactions} interactions leaked); "
                f"query_leaks={result.query_leaks}; "
                f"restatement_leaks={result.restatement_leaks}; "
                f"survivorship_leaks={result.survivorship_leaks}"
            )
            if result.offenders:
                explanation += "\nOffenders:\n" + "\n".join(result.offenders)
            if result.flags:
                explanation += "\nFlags: " + ", ".join(result.flags)

            return Score(
                value=result.tclr,
                explanation=explanation,
                metadata={
                    "total_interactions": result.total_interactions,
                    "result_leaks": result.result_leaks,
                    "query_leaks": result.query_leaks,
                    "restatement_leaks": result.restatement_leaks,
                    "survivorship_leaks": result.survivorship_leaks,
                    "survivorship_rate": result.survivorship_rate,
                    "offenders": result.offenders,
                    "flags": result.flags,
                },
            )

        return score

    return _tool_call_leakage()
