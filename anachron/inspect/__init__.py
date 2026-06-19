"""Inspect adapter layer for Anachron (optional dependency: ``inspect_ai``).

Importing this subpackage does not require ``inspect_ai``. The task, scorer, and
tool are only constructed when actually used; constructing them without
``inspect_ai`` installed raises a clear :class:`ImportError` pointing at the
optional extra.
"""

from anachron.inspect.scorer import tool_call_leakage
from anachron.inspect.task import anachron, anachron_enforced
from anachron.inspect.tools import anachron_search

__all__ = ["anachron", "anachron_enforced", "anachron_search", "tool_call_leakage"]
