"""Inspect adapters for the canonical synthetic v0 sample registry."""

from __future__ import annotations

from datetime import date

from anachron.data.v0_samples import get_v0_samples
from anachron.inspect.scorer import tool_call_leakage
from anachron.inspect.tools import anachron_search

try:
    from inspect_ai import Task, task
    from inspect_ai.dataset import Sample
    from inspect_ai.solver import Generate, TaskState, generate, solver, use_tools

    _INSPECT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _INSPECT_AVAILABLE = False

    def task(func):
        return func


_IMPORT_HINT = "anachron.inspect requires the optional 'inspect_ai' dependency. Install it with: pip install 'anachron[inspect]'"


def _parse_iso(value: str) -> date:
    return date.fromisoformat(value)


def _samples() -> list[Sample]:
    """Build Inspect samples directly from the sole v0 registry."""
    return [
        Sample(id=sample.id, input=sample.prompt(), target=sample.target, metadata={"as_of": sample.as_of.isoformat()})
        for sample in get_v0_samples()
    ]


if _INSPECT_AVAILABLE:

    @solver
    def _enforced_search():
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            as_of_raw = state.metadata.get("as_of")
            if as_of_raw is None:
                raise ValueError("anachron_enforced requires metadata['as_of']")
            state.tools = [anachron_search(enforce_as_of=_parse_iso(as_of_raw))]
            return state

        return solve


@task
def anachron() -> Task:
    if not _INSPECT_AVAILABLE:
        raise ImportError(_IMPORT_HINT)
    return Task(dataset=_samples(), solver=[use_tools(anachron_search()), generate()], scorer=tool_call_leakage())


@task
def anachron_enforced() -> Task:
    if not _INSPECT_AVAILABLE:
        raise ImportError(_IMPORT_HINT)
    return Task(dataset=_samples(), solver=[_enforced_search(), generate()], scorer=tool_call_leakage())
