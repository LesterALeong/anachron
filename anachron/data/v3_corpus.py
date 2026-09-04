"""Frozen v3 corpus with an independent point-in-time closure."""

from __future__ import annotations

from datetime import date

from anachron.core.leakage import CorpusItem

_CORPUS = (
    CorpusItem("fin-001", "Acme Corp reports Q1 earnings beating analyst estimates.", date(2021, 4, 28), "ACME", date(2010, 6, 1)),
    CorpusItem("fin-002", "Acme Corp raises full-year revenue guidance on cloud demand.", date(2022, 7, 19), "ACME", date(2010, 6, 1)),
    CorpusItem("fin-003", "Borealis Mining is delisted from the exchange after bankruptcy filing.", date(2019, 11, 5), "BORX", date(2008, 3, 12), date(2019, 11, 5)),
    CorpusItem("fin-004", "Borealis Mining shares slide as creditors push for restructuring.", date(2018, 9, 14), "BORX", date(2008, 3, 12), date(2019, 11, 5)),
    CorpusItem("fin-005", "Cygnus Robotics completes its initial public offering.", date(2023, 2, 9), "CYGN", date(2023, 2, 9)),
    CorpusItem("fin-006", "Cygnus Robotics expands its warehouse automation contracts.", date(2024, 5, 22), "CYGN", date(2023, 2, 9)),
    CorpusItem("fin-007", "Delta Pharma announces a stock split effective next quarter.", date(2020, 8, 3), "DLTA", date(2014, 1, 15)),
    CorpusItem("fin-008", "Delta Pharma reports Q4 2020 revenue of $412 million.", date(2021, 2, 4), "DLTA", date(2014, 1, 15)),
    CorpusItem("fin-009", "Delta Pharma restates Q4 2020 revenue down to $377 million after an accounting review.", date(2021, 9, 17), "DLTA", date(2014, 1, 15), None, "fin-008"),
    CorpusItem("gen-001", "A total solar eclipse crosses North America.", date(2017, 8, 21)),
    CorpusItem("gen-002", "A new deep-water port opens to commercial shipping.", date(2019, 6, 10)),
    CorpusItem("gen-003", "An international summit agrees on updated climate targets.", date(2021, 11, 13)),
    CorpusItem("gen-004", "A landmark suspension bridge is opened to traffic.", date(2022, 3, 30)),
    CorpusItem("gen-005", "A long-running space probe transmits its final data set.", date(2024, 9, 18)),
    CorpusItem("gen-006", "The statistics office estimates industrial output grew 2.1 percent in 2022.", date(2023, 1, 27)),
    CorpusItem("gen-007", "The statistics office revises 2022 industrial output growth down to 1.4 percent.", date(2023, 7, 14), None, None, None, "gen-006"),
    CorpusItem("fin-010", "Equinox Retail is delisted after bankruptcy.", date(2020, 2, 14), "EQRX", date(2011, 5, 6), date(2020, 2, 14)),
)


def get_v3_corpus() -> list[CorpusItem]:
    """Return a fresh list over the immutable v3 corpus."""
    return list(_CORPUS)


def format_search_results(items: list[CorpusItem]) -> str:
    if not items:
        return "No results."
    return "\n".join(
        f"[{item.id}] ({item.publish_date.isoformat()}) {item.text}" for item in items
    )


def search_v3(query: str, enforce_as_of: date | None = None) -> list[CorpusItem]:
    """Naively retrieve matching v3 records, optionally applying the cutoff."""
    tokens = [token for token in query.lower().split() if token]
    return [
        item
        for item in _CORPUS
        if (enforce_as_of is None or item.publish_date <= enforce_as_of)
        and any(token in f"{item.text} {item.id} {item.entity or ''}".lower() for token in tokens)
    ]


__all__ = ["format_search_results", "get_v3_corpus", "search_v3"]
