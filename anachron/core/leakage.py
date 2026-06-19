"""Point-in-time leakage detection — the Anachron core.

This module is pure stdlib: no third-party imports, no I/O, no network. It is the
product's moat and is unit-tested in isolation. Given a set of agent tool
interactions and an as-of date ``T``, it decides — exactly and by construction —
whether each interaction touched information that did not exist (or was not
point-in-time valid) as of ``T``.

Leakage is defined relative to the *publish date* carried by every corpus item,
so detection is deterministic rather than fuzzy: an interaction leaks iff it
surfaces or consumes an item dated after ``T`` (result leakage), reaches toward a
date after ``T`` in its query (query/intent leakage), or returns a finance entity
that was not point-in-time valid as of ``T`` (survivorship leakage).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class CorpusItem:
    """A single date-stamped document the retrieval tool can return.

    The optional finance fields encode a point-in-time entity-validity window.
    ``entity_valid_to is None`` means the entity is still valid (e.g. not
    delisted) as of the latest known date. Items with no ``entity`` carry no
    survivorship semantics and are ignored by survivorship checks.
    """

    id: str
    text: str
    publish_date: date
    entity: str | None = None
    entity_valid_from: date | None = None
    entity_valid_to: date | None = None


@dataclass
class ToolInteraction:
    """One tool call paired with the result it produced.

    ``query`` is the stringified tool arguments. ``query_dates`` holds any dates
    parsed out of those arguments (an intent-to-leak signal, possibly empty).
    ``returned_items`` holds the corpus items the tool surfaced back to the agent
    (possibly empty).
    """

    tool: str
    query: str
    query_dates: list[date] = field(default_factory=list)
    returned_items: list[CorpusItem] = field(default_factory=list)


@dataclass
class LeakageResult:
    """The scored outcome of a run.

    ``tclr`` is the primary metric (Tool-Call Leakage Rate). ``query_leaks`` and
    survivorship figures are reported alongside it but are not folded into TCLR.
    ``survivorship_rate`` is ``None`` when no interaction returned a
    finance/entity-bearing item. ``offenders`` are human-readable strings, and
    ``flags`` carries caveats (e.g. ``"no_tool_interactions"``).
    """

    tclr: float
    total_interactions: int
    result_leaks: int
    query_leaks: int
    survivorship_leaks: int
    survivorship_rate: float | None
    offenders: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def is_result_leak(it: ToolInteraction, as_of: date) -> bool:
    """True iff any returned item was published strictly after ``as_of``.

    Boundary rule: ``publish_date == as_of`` is NOT a leak; only strictly
    greater dates leak.
    """
    return any(item.publish_date > as_of for item in it.returned_items)


def is_query_leak(it: ToolInteraction, as_of: date) -> bool:
    """True iff the query reaches toward a date strictly after ``as_of``.

    This is an intent-to-leak signal: the agent asked for the future even if no
    future item came back. Boundary rule matches ``is_result_leak`` — equality
    is not a leak.
    """
    return any(qd > as_of for qd in it.query_dates)


def is_survivorship_leak(it: ToolInteraction, as_of: date) -> bool:
    """True iff a returned finance entity was not point-in-time valid at ``as_of``.

    An item carries survivorship semantics only when it names an ``entity``. The
    entity is point-in-time valid when ``as_of`` falls within
    ``[entity_valid_from, entity_valid_to]``; it leaks when ``as_of`` is before
    the entity existed (not yet listed) or after it ceased to be valid
    (delisted). A missing bound is treated as open-ended on that side. Items with
    no entity are ignored.
    """
    for item in it.returned_items:
        if item.entity is None:
            continue
        if item.entity_valid_from is not None and as_of < item.entity_valid_from:
            return True
        if item.entity_valid_to is not None and as_of > item.entity_valid_to:
            return True
    return False


def _has_finance_item(it: ToolInteraction) -> bool:
    """True iff the interaction returned at least one entity-bearing item."""
    return any(item.entity is not None for item in it.returned_items)


def _result_offenders(it: ToolInteraction, as_of: date) -> list[str]:
    """Human-readable offender strings for the post-T items in an interaction."""
    return [
        f"{it.tool}: result item {item.id} dated {item.publish_date.isoformat()} > {as_of.isoformat()}"
        for item in it.returned_items
        if item.publish_date > as_of
    ]


def _survivorship_offenders(it: ToolInteraction, as_of: date) -> list[str]:
    """Human-readable offender strings for PIT-invalid entities in an interaction."""
    offenders: list[str] = []
    for item in it.returned_items:
        if item.entity is None:
            continue
        if item.entity_valid_from is not None and as_of < item.entity_valid_from:
            offenders.append(
                f"{it.tool}: entity {item.entity!r} (item {item.id}) not yet valid at "
                f"{as_of.isoformat()} (valid_from {item.entity_valid_from.isoformat()})"
            )
        elif item.entity_valid_to is not None and as_of > item.entity_valid_to:
            offenders.append(
                f"{it.tool}: entity {item.entity!r} (item {item.id}) no longer valid at "
                f"{as_of.isoformat()} (valid_to {item.entity_valid_to.isoformat()})"
            )
    return offenders


def score_interactions(
    interactions: list[ToolInteraction], as_of: date
) -> LeakageResult:
    """Score a run's tool interactions against an as-of date ``T``.

    The primary metric is ``TCLR = result_leaks / total_interactions``, where an
    interaction counts as a result-leak iff :func:`is_result_leak`. With zero
    interactions, ``tclr`` is ``0.0`` and the ``no_tool_interactions`` flag is
    set. Query leaks and survivorship are reported separately:

    * ``query_leaks`` — interactions with :func:`is_query_leak` (intent signal,
      not folded into TCLR).
    * survivorship — over interactions that returned at least one finance
      (entity-bearing) item, the fraction with :func:`is_survivorship_leak`;
      ``survivorship_rate`` is ``None`` when that denominator is zero.
    """
    total = len(interactions)
    flags: list[str] = []
    offenders: list[str] = []

    if total == 0:
        flags.append("no_tool_interactions")
        return LeakageResult(
            tclr=0.0,
            total_interactions=0,
            result_leaks=0,
            query_leaks=0,
            survivorship_leaks=0,
            survivorship_rate=None,
            offenders=offenders,
            flags=flags,
        )

    result_leaks = 0
    query_leaks = 0
    survivorship_leaks = 0
    finance_interactions = 0

    for it in interactions:
        if is_result_leak(it, as_of):
            result_leaks += 1
            offenders.extend(_result_offenders(it, as_of))
        if is_query_leak(it, as_of):
            query_leaks += 1
            offenders.append(
                f"{it.tool}: query {it.query!r} references a date after {as_of.isoformat()}"
            )
        if _has_finance_item(it):
            finance_interactions += 1
            if is_survivorship_leak(it, as_of):
                survivorship_leaks += 1
                offenders.extend(_survivorship_offenders(it, as_of))

    survivorship_rate = (
        survivorship_leaks / finance_interactions if finance_interactions else None
    )

    return LeakageResult(
        tclr=result_leaks / total,
        total_interactions=total,
        result_leaks=result_leaks,
        query_leaks=query_leaks,
        survivorship_leaks=survivorship_leaks,
        survivorship_rate=survivorship_rate,
        offenders=offenders,
        flags=flags,
    )
