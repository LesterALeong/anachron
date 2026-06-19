"""Pure point-in-time leakage detection — no third-party dependencies."""

from anachron.core.leakage import (
    CorpusItem,
    LeakageResult,
    ToolInteraction,
    is_query_leak,
    is_result_leak,
    is_survivorship_leak,
    score_interactions,
)

__all__ = [
    "CorpusItem",
    "LeakageResult",
    "ToolInteraction",
    "is_query_leak",
    "is_result_leak",
    "is_survivorship_leak",
    "score_interactions",
]
