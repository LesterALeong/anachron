"""Inspect entry-point registry.

Importing this module surfaces the task, scorer, and tool so the
``inspect_ai`` entry point can discover them. It requires ``inspect_ai`` and is
only loaded by Inspect's plugin machinery, never by the leakage core.
"""

from anachron.inspect.scorer import tool_call_leakage
from anachron.inspect.task import anachron, anachron_enforced
from anachron.inspect.tools import anachron_search

__all__ = ["anachron", "anachron_enforced", "anachron_search", "tool_call_leakage"]
