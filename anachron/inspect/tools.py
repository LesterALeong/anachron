"""Date-aware retrieval tool for Inspect (guarded ``inspect_ai`` import).

The tool wraps :func:`anachron.data.corpus.search` and tags every returned item
with its ``id`` and ``publish_date`` so the scorer can recover dates exactly from
the tool result text. A Mode A / Mode B switch controls enforcement:

* **Mode A (unrestricted):** results may include post-T items.
* **Mode B (enforced):** a server-side date filter drops items published after
  the sample's ``as_of`` date, so the scorer measures only *residual* leakage.
"""

from __future__ import annotations

from datetime import date

from anachron.data.corpus import search

try:
    from inspect_ai.tool import Tool, ToolError, tool

    _INSPECT_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without inspect_ai
    _INSPECT_AVAILABLE = False

_IMPORT_HINT = (
    "anachron.inspect requires the optional 'inspect_ai' dependency. "
    "Install it with: pip install 'anachron[inspect]'"
)


def _require_inspect() -> None:
    """Raise a clear error if ``inspect_ai`` is not installed."""
    if not _INSPECT_AVAILABLE:
        raise ImportError(_IMPORT_HINT)


def _format_results(items) -> str:
    """Render corpus items as a date-stamped block the scorer can parse.

    Each line carries the item id and ISO publish date so dates are recoverable
    from the tool result without re-querying the corpus.
    """
    if not items:
        return "No results."
    return "\n".join(
        f"[{item.id}] ({item.publish_date.isoformat()}) {item.text}" for item in items
    )


def anachron_search(enforce_as_of: date | None = None) -> "Tool":
    """Build the date-aware ``anachron_search`` tool.

    Args:
        enforce_as_of: Mode B date filter. When set, the tool drops items
            published after this date (server-side enforcement). When ``None``
            (Mode A), all matching items are returned regardless of date.

    Returns:
        An Inspect ``Tool`` that searches the date-stamped corpus.
    """
    _require_inspect()

    @tool
    def _anachron_search() -> "Tool":
        async def execute(query: str) -> str:
            """Search a date-stamped corpus of news and world events.

            Each result is prefixed with its item id and publication date in the
            form ``[id] (YYYY-MM-DD) text``.

            Args:
                query: Keywords to search for.

            Returns:
                A newline-separated list of matching, date-stamped items.
            """
            if not query.strip():
                raise ToolError("query must be a non-empty string")
            items = search(query, enforce_as_of=enforce_as_of)
            return _format_results(items)

        return execute

    return _anachron_search()
