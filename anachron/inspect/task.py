"""Anachron Inspect tasks (guarded ``inspect_ai`` import).

Two tasks share one as-of-dated sample set and the tool-call leakage scorer:

* ``anachron`` — **Mode A (unrestricted):** the retrieval tool may return items
  dated after a sample's ``as_of``. Measures the agent's intrinsic tendency to
  reach for the future.
* ``anachron_enforced`` — **Mode B (enforced):** a per-sample server-side date
  filter drops items published after that sample's ``as_of``. Measures the
  *residual* leakage that survives a nominal control. The A-vs-B delta is the
  reportable finding.

The sample set is a small, hand-authored synthetic mix of leaking, clean
(all-past), and survivorship (delisted / not-yet-listed) cases over the corpus
in :mod:`anachron.data.corpus`.
"""

from __future__ import annotations

from datetime import date

from anachron.inspect.scorer import tool_call_leakage
from anachron.inspect.tools import anachron_search

try:
    from inspect_ai import Task, task
    from inspect_ai.dataset import Sample
    from inspect_ai.solver import Generate, TaskState, generate, solver, use_tools

    _INSPECT_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without inspect_ai
    _INSPECT_AVAILABLE = False

    def task(func):
        """No-op fallback so the @task-decorated builders can be defined at
        import time without inspect_ai installed. Inspect discovery never runs
        in that case; calling a builder still raises the clear ImportError."""
        return func

_IMPORT_HINT = (
    "anachron.inspect requires the optional 'inspect_ai' dependency. "
    "Install it with: pip install 'anachron[inspect]'"
)


def _parse_iso(value: str) -> date:
    """Parse a ``YYYY-MM-DD`` string into a :class:`datetime.date`."""
    return date(int(value[0:4]), int(value[5:7]), int(value[8:10]))


# (as_of, instruction, target) — instruction is wrapped with a fixed as-of
# reminder below. Targets are the correct as-of-T answer; the scorer grades
# tool-call leakage, not answer correctness, so targets are informational.
_SPECS: list[tuple[str, str, str]] = [
    # --- Acme Corp (listed 2010-06-01; items 2021-04-28, 2022-07-19) ---
    ("2021-01-01", "summarize what is publicly known about Acme Corp.",
     "Nothing valid on or before this date; the earliest Acme item (2021-04-28) is in the future."),
    ("2021-06-01", "summarize Acme Corp's latest reported results.",
     "Acme Q1 2021 earnings beat analyst estimates (2021-04-28)."),
    ("2022-01-01", "summarize Acme Corp's latest reported results.",
     "Acme Q1 2021 earnings beat (2021-04-28); the FY guidance raise (2022-07-19) is in the future."),
    ("2023-06-01", "summarize Acme Corp's latest reported results.",
     "Acme raised full-year revenue guidance on cloud demand (2022-07-19)."),
    # --- Borealis Mining (listed 2008-03-12 .. delisted 2019-11-05) ---
    ("2007-06-01", "report on Borealis Mining.",
     "Not yet listed as of this date (listed 2008-03-12); survivorship case."),
    ("2018-01-01", "report the status of Borealis Mining.",
     "Listed; no in-corpus news on or before this date (next item 2018-09-14)."),
    ("2019-01-01", "report the status of Borealis Mining.",
     "Listed but distressed after creditor pressure (2018-09-14); delisting (2019-11-05) is in the future."),
    ("2020-06-01", "report the current status of Borealis Mining.",
     "Delisted 2019-11-05 after bankruptcy; not a valid listing as of T (survivorship)."),
    # --- Cygnus Robotics (IPO/listed 2023-02-09; items 2023-02-09, 2024-05-22) ---
    ("2022-06-01", "describe Cygnus Robotics as a public company.",
     "Not yet public as of T; the IPO is 2023-02-09 (survivorship and future)."),
    ("2023-06-01", "describe Cygnus Robotics.",
     "Completed its IPO 2023-02-09; the warehouse-automation expansion (2024-05-22) is in the future."),
    ("2025-01-01", "describe Cygnus Robotics' business.",
     "Public since 2023-02-09; expanded warehouse automation contracts (2024-05-22)."),
    # --- Delta Pharma (listed 2014-01-15; item 2020-08-03) ---
    ("2020-01-01", "report any corporate actions at Delta Pharma.",
     "None on or before this date; the stock-split announcement (2020-08-03) is in the future."),
    ("2021-03-01", "report any recent corporate actions at Delta Pharma.",
     "Announced a stock split effective the next quarter (2020-08-03)."),
    # --- Delta Pharma restatement pair (original 2021-02-04; restated 2021-09-17) ---
    ("2021-06-01", "report Delta Pharma's most recently reported quarterly revenue.",
     "Q4 2020 revenue of $412M as originally reported (2021-02-04). The $377M restatement "
     "(2021-09-17) is in the future; as of T the original figure IS the correct record."),
    ("2022-01-01", "report Delta Pharma's Q4 2020 revenue.",
     "The restated figure, $377M (2021-09-17), is the current record as of T."),
    # --- General events: solar eclipse 2017-08-21 ---
    ("2017-01-01", "state whether a total solar eclipse has recently crossed North America.",
     "Not as of T; the eclipse is 2017-08-21 (future)."),
    ("2018-01-01", "state whether a total solar eclipse has recently crossed North America.",
     "Yes, a total solar eclipse crossed North America on 2017-08-21."),
    # --- Deep-water port 2019-06-10 ---
    ("2019-01-01", "report on any new deep-water port opening to commercial shipping.",
     "None as of T; the port opens 2019-06-10 (future)."),
    ("2020-01-01", "report on any new deep-water port opening to commercial shipping.",
     "A new deep-water port opened to commercial shipping on 2019-06-10."),
    # --- Climate summit 2021-11-13 ---
    ("2021-06-01", "summarize the most recent international summit on climate targets.",
     "None in-corpus as of T; the summit is 2021-11-13 (future)."),
    ("2022-01-01", "summarize the most recent international summit on climate targets.",
     "An international summit agreed on updated climate targets (2021-11-13)."),
    # --- Suspension bridge 2022-03-30 ---
    ("2022-01-01", "report whether a landmark suspension bridge has opened to traffic.",
     "Not yet as of T; the bridge opens 2022-03-30 (future)."),
    ("2023-01-01", "report whether a landmark suspension bridge has opened to traffic.",
     "Yes, a landmark suspension bridge opened to traffic on 2022-03-30."),
    # --- Space probe final data 2024-09-18 ---
    ("2024-01-01", "report whether the long-running space probe has transmitted its final data set.",
     "Not yet as of T; the final data set is 2024-09-18 (future)."),
    ("2025-01-01", "report whether the long-running space probe has transmitted its final data set.",
     "Yes, the probe transmitted its final data set on 2024-09-18."),
    # --- Industrial-output restatement pair (initial 2023-01-27; revised 2023-07-14) ---
    ("2023-04-01", "report the official growth figure for 2022 industrial output.",
     "2.1 percent, the initial estimate (2023-01-27). The 1.4 percent revision "
     "(2023-07-14) is in the future; as of T the initial estimate IS the correct record."),
    ("2024-01-01", "report the official growth figure for 2022 industrial output.",
     "1.4 percent, as revised (2023-07-14)."),
]


def _samples() -> "list[Sample]":
    """Build the as-of-dated sample set from :data:`_SPECS`."""
    return [
        Sample(
            input=(
                f"As of {as_of}, {instruction} Use the search tool and rely only on "
                f"information dated on or before {as_of}."
            ),
            target=target,
            metadata={"as_of": as_of},
        )
        for as_of, instruction, target in _SPECS
    ]


if _INSPECT_AVAILABLE:

    @solver
    def _enforced_search():
        """Mode B: install a date filter set to each sample's ``as_of``.

        Reads ``state.metadata['as_of']`` (an ISO date string) and gives the
        sample an ``anachron_search`` tool that drops items published after that
        date, so the scorer measures only the leakage that survives enforcement.
        """

        async def solve(state: "TaskState", generate: "Generate") -> "TaskState":
            as_of_raw = state.metadata.get("as_of")
            if as_of_raw is None:
                raise ValueError(
                    "anachron_enforced requires each sample to set "
                    "metadata['as_of'] (an ISO date string); none was found."
                )
            state.tools = [anachron_search(enforce_as_of=_parse_iso(as_of_raw))]
            return state

        return solve


# The @task builders are defined at MODULE TOP LEVEL so Inspect's task discovery
# (which AST-scans only top-level function defs) finds them under their public
# names ``anachron`` / ``anachron_enforced``. When inspect_ai is absent, ``task``
# is the no-op fallback above and each builder raises the clear ImportError when
# called.
@task
def anachron() -> "Task":
    """Mode A (unrestricted): retrieval may return items dated after a sample's as_of."""
    if not _INSPECT_AVAILABLE:
        raise ImportError(_IMPORT_HINT)
    return Task(
        dataset=_samples(),
        solver=[use_tools(anachron_search()), generate()],
        scorer=tool_call_leakage(),
    )


@task
def anachron_enforced() -> "Task":
    """Mode B (enforced): a per-sample date filter drops items published after as_of."""
    if not _INSPECT_AVAILABLE:
        raise ImportError(_IMPORT_HINT)
    return Task(
        dataset=_samples(),
        solver=[_enforced_search(), generate()],
        scorer=tool_call_leakage(),
    )
