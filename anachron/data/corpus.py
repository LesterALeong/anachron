"""A tiny date-stamped synthetic corpus + naive retrieval.

The corpus is deliberately small and hand-authored so leakage detection stays
exact and reproducible. It contains two slices:

* a **finance** slice with point-in-time entity-validity windows, including a
  delisted entity and an entity not yet listed as of some ``T``;
* a **general-events** slice of dated world events;
* two **restatement pairs** (one per slice): an original figure and a later
  revision marked with ``restates_id``, so the restatement axis is exercised in
  both a finance and a non-finance setting.

:func:`search` is a naive substring/keyword matcher. When ``enforce_as_of`` is
set it filters out any item published after that date — this is Mode B, the
nominal enforcement layer that Anachron scores *under*.
"""

from __future__ import annotations

from datetime import date

from anachron.core.leakage import CorpusItem

_CORPUS: list[CorpusItem] = [
    # --- Finance slice (entity-bearing, with PIT validity windows) ---
    CorpusItem(
        id="fin-001",
        text="Acme Corp reports Q1 earnings beating analyst estimates.",
        publish_date=date(2021, 4, 28),
        entity="ACME",
        entity_valid_from=date(2010, 6, 1),
        entity_valid_to=None,
    ),
    CorpusItem(
        id="fin-002",
        text="Acme Corp raises full-year revenue guidance on cloud demand.",
        publish_date=date(2022, 7, 19),
        entity="ACME",
        entity_valid_from=date(2010, 6, 1),
        entity_valid_to=None,
    ),
    CorpusItem(
        id="fin-003",
        text="Borealis Mining is delisted from the exchange after bankruptcy filing.",
        publish_date=date(2019, 11, 5),
        entity="BORX",
        entity_valid_from=date(2008, 3, 12),
        entity_valid_to=date(2019, 11, 5),
    ),
    CorpusItem(
        id="fin-004",
        text="Borealis Mining shares slide as creditors push for restructuring.",
        publish_date=date(2018, 9, 14),
        entity="BORX",
        entity_valid_from=date(2008, 3, 12),
        entity_valid_to=date(2019, 11, 5),
    ),
    CorpusItem(
        id="fin-005",
        text="Cygnus Robotics completes its initial public offering.",
        publish_date=date(2023, 2, 9),
        entity="CYGN",
        entity_valid_from=date(2023, 2, 9),
        entity_valid_to=None,
    ),
    CorpusItem(
        id="fin-006",
        text="Cygnus Robotics expands its warehouse automation contracts.",
        publish_date=date(2024, 5, 22),
        entity="CYGN",
        entity_valid_from=date(2023, 2, 9),
        entity_valid_to=None,
    ),
    CorpusItem(
        id="fin-007",
        text="Delta Pharma announces a stock split effective next quarter.",
        publish_date=date(2020, 8, 3),
        entity="DLTA",
        entity_valid_from=date(2014, 1, 15),
        entity_valid_to=None,
    ),
    # Restatement pair: the original figure, then a post-publication revision.
    CorpusItem(
        id="fin-008",
        text="Delta Pharma reports Q4 2020 revenue of $412 million.",
        publish_date=date(2021, 2, 4),
        entity="DLTA",
        entity_valid_from=date(2014, 1, 15),
        entity_valid_to=None,
    ),
    CorpusItem(
        id="fin-009",
        text="Delta Pharma restates Q4 2020 revenue down to $377 million after an accounting review.",
        publish_date=date(2021, 9, 17),
        entity="DLTA",
        entity_valid_from=date(2014, 1, 15),
        entity_valid_to=None,
        restates_id="fin-008",
    ),
    # --- General-events slice (no entity / survivorship semantics) ---
    CorpusItem(
        id="gen-001",
        text="A total solar eclipse crosses North America.",
        publish_date=date(2017, 8, 21),
    ),
    CorpusItem(
        id="gen-002",
        text="A new deep-water port opens to commercial shipping.",
        publish_date=date(2019, 6, 10),
    ),
    CorpusItem(
        id="gen-003",
        text="An international summit agrees on updated climate targets.",
        publish_date=date(2021, 11, 13),
    ),
    CorpusItem(
        id="gen-004",
        text="A landmark suspension bridge is opened to traffic.",
        publish_date=date(2022, 3, 30),
    ),
    CorpusItem(
        id="gen-005",
        text="A long-running space probe transmits its final data set.",
        publish_date=date(2024, 9, 18),
    ),
    # Restatement pair: an initial statistical estimate, then a later revision.
    CorpusItem(
        id="gen-006",
        text="The statistics office estimates industrial output grew 2.1 percent in 2022.",
        publish_date=date(2023, 1, 27),
    ),
    CorpusItem(
        id="gen-007",
        text="The statistics office revises 2022 industrial output growth down to 1.4 percent.",
        publish_date=date(2023, 7, 14),
        restates_id="gen-006",
    ),
]


def get_corpus() -> list[CorpusItem]:
    """Return a fresh copy of the corpus item list.

    The list is copied so callers cannot mutate the shared corpus; the items
    themselves are treated as immutable.
    """
    return list(_CORPUS)


def search(
    query: str,
    corpus: list[CorpusItem] | None = None,
    enforce_as_of: date | None = None,
) -> list[CorpusItem]:
    """Naive keyword retrieval over the corpus.

    An item matches when any whitespace-delimited token of ``query`` (case
    folded) appears in the item's text, id, or entity. When ``enforce_as_of`` is
    set (Mode B), items published strictly after that date are filtered out.

    Args:
        query: Free-text query; tokenized on whitespace, matched case-insensitively.
        corpus: Items to search; defaults to :func:`get_corpus`.
        enforce_as_of: If set, drop items with ``publish_date > enforce_as_of``.

    Returns:
        Matching items in corpus order.
    """
    if corpus is None:
        corpus = get_corpus()

    tokens = [tok for tok in query.lower().split() if tok]

    results: list[CorpusItem] = []
    for item in corpus:
        if enforce_as_of is not None and item.publish_date > enforce_as_of:
            continue
        haystack = f"{item.text} {item.id} {item.entity or ''}".lower()
        if any(tok in haystack for tok in tokens):
            results.append(item)
    return results
