# Anachron

[![tests](https://github.com/LesterALeong/anachron/actions/workflows/tests.yml/badge.svg)](https://github.com/LesterALeong/anachron/actions/workflows/tests.yml) ![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg) ![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg) ![Core dependencies](https://img.shields.io/badge/core%20dependencies-none-brightgreen.svg)

**Measuring look-ahead leakage in LLM agents — does an agent use information it could not have had at the time?**

Anachron contains a small synthetic v0 scorer, a blocked/unexecuted Routes v1 source-route study, and the pre-outcome Routes v2 date-presentation design. The v0 scorer records leakage in a controlled, date-stamped corpus. Routes v2 holds source content fixed and tests a visible-document-date intervention. None of the tracks establishes a live-web, general-agent, or transaction-cost result.

## Research tracks and current boundary

The synthetic v0 material below is a worked mechanism demonstration, not a model benchmark. Routes v1 is **BLOCKED and unexecuted** because its source-content intervention confounds the visible-date question; it is historical provenance only. Routes v2 is the active pre-outcome design, with no revalidated sources, human decisions, calibration, model outcomes, or paper claims. See [`research/routes-v2/PROTOCOL.md`](research/routes-v2/PROTOCOL.md) and [`research/routes-v2/FREEZE_ACCEPTANCE_MATRIX.md`](research/routes-v2/FREEZE_ACCEPTANCE_MATRIX.md).

## The problem

LLM agents increasingly act over time-anchored tasks: *"analyze this company as of Q2 2023,"* *"forecast this outcome given only what was known on date `T`."* Such tasks are only valid if the agent does not peek at the future. The synthetic v0 scorer illustrates how date-stamped retrieval traces can expose a post-cutoff interaction. It does not by itself quantify leakage for deployed agents or live retrieval systems.

## What it measures

- **TCLR - Tool-Call Leakage Rate** (v0 synthetic metric): the fraction of supplied trace interactions that surface or consume an item dated after `T`. Detection is exact and by construction - every v0 corpus item carries a known publish date, so an interaction leaks iff it touches an item with `publish_date > T`. (Boundary: `publish_date == T` does not leak.)
- **Survivorship leakage**: on the finance slice, the fraction of interactions that return an entity which was not point-in-time valid as of `T` (already delisted, or not yet listed). This is the discipline standard ML evaluations skip.
- **Restatement leakage**: interactions that consume a post-`T` *restatement* of an earlier item — revised history rather than ordinary future news. As of `T` the originally reported figure is the correct record, so this is the vendor-overwritten-history problem from backtesting. By construction a labeled subset of TCLR's result leaks; reported separately because it is the worse failure mode.
- **Query-intent leakage** (secondary): whether the agent's query itself reaches for a date after `T`. Reported separately and **not** folded into TCLR.
- **Enforcement effect**: the paired mean TCLR reduction between unrestricted and enforced runs, with a bootstrap confidence interval and exact sign test so outliers are distinguishable from consistent improvement.

### Two run modes

- **Unrestricted** — tools may return post-`T` items; measures the agent's intrinsic tendency to reach for the future.
- **Enforced** — a date filter is nominally applied; measures the leakage that slips past controls. The gap between the two modes is itself a finding.

## Synthetic v0 illustration, not a Routes v1 result

A free local run with `qwen2.5:7b` (via [Ollama](https://ollama.com), no API key) over the 23-sample v0 corpus:

| | Mode A (unrestricted) | Mode B (date filter on) |
|---|---|---|
| Mean TCLR | 0.217 | 0.000 |
| Date-leak runs | 5 / 23 | 0 / 23 |
| Survivorship leaks | 3 | 1 |

Of the 8 runs in which the model actually used the search tool, **5 leaked** a post-cutoff item. A nominal date filter then removed every date-based leak (TCLR to 0.000), yet **one survivorship leak still surfaced under enforcement**: the agent returned an entity that was not point-in-time valid as of `T`, which a date filter alone cannot catch. That residual is the point.

This is an illustrative run on a small synthetic corpus, not a benchmark, a model ranking, proof about a live retrieval agent, or a Routes v1 result. The model answered without searching on roughly two-thirds of samples, so TCLR is reported over all 23 runs (0.217) and, separately, as 5 of the 8 tool-using runs. The sample set has since grown to 27 with the restatement pairs; the table reports the original v0 run.

## Quickstart

Run the leakage core and its tests with **no third-party dependencies**:

```bash
python -m unittest discover -s tests -v
```

Run the legacy synthetic Inspect adapter (requires the optional `inspect_ai` extra and a configured provider). Two tasks share one as-of-dated sample set:

```bash
pip install -e ".[inspect]"

# Mode A — unrestricted retrieval: the agent's intrinsic tendency to reach for the future
inspect eval anachron/inspect/task.py@anachron --model <provider/model>

# Mode B — a nominal date filter is on: the leakage that survives enforcement
inspect eval anachron/inspect/task.py@anachron_enforced --model <provider/model>
```

The gap between Mode A and Mode B leakage rates is an illustrative synthetic comparison, not a Routes v1 or live-agent finding.

### Compare the modes with paired inference

An average gap alone does not say whether enforcement consistently helps across tasks or whether a few outliers drive the result. Export each sample's TCLR from both runs as JSON objects with identical sample ids:

```json
{
  "finance-001": 1.0,
  "finance-002": 0.5,
  "news-001": 0.0
}
```

Then compare the same tasks as paired observations:

```bash
anachron-compare unrestricted.json enforced.json
anachron-compare unrestricted.json enforced.json --format json
```

```text
Paired mode comparison (n=27)
  unrestricted mean TCLR  0.241
  enforced mean TCLR      0.037
  mean reduction           +0.204
  95% paired bootstrap CI  [+0.074, +0.333]
  relative reduction       84.6%
  improved / worsened / tied  7 / 0 / 20
  exact sign-test p         0.0156
```

The output reports the effect size, a paired percentile-bootstrap confidence interval, and an exact two-sided sign test over non-tied samples. The values above are an illustrative output shape, not a new model result. Programmatic use is dependency-free:

```python
from anachron.core import compare_modes

report = compare_modes(unrestricted_scores, enforced_scores, seed=0)
print(report.table())
```

## Worked example

To make the metric concrete, here is the scorer's output on two finance cases, with an agent issuing a single naive entity search per task. This is an illustrative walkthrough of the mechanism on the synthetic corpus, not a model benchmark.

**Cygnus Robotics, as of 2022-06-01** (the company does not go public until its 2023-02-09 IPO). A search for `cygnus robotics` surfaces both Cygnus items:

```
MODE A (unrestricted): TCLR=1.00  survivorship=1
  ! result item fin-005 dated 2023-02-09 > 2022-06-01
  ! result item fin-006 dated 2024-05-22 > 2022-06-01
  ! entity 'CYGN' (item fin-005) not yet valid at 2022-06-01 (valid_from 2023-02-09)
MODE B (date filter):  TCLR=0.00  survivorship=0   (post-T items dropped)
```

**Borealis Mining, as of 2020-06-01** (the company was delisted 2019-11-05). Both news items predate the cutoff, so a date filter sees nothing wrong, yet the agent still surfaces a delisted entity:

```
MODE A (unrestricted): TCLR=0.00  survivorship=1
  ! entity 'BORX' (item fin-003) no longer valid at 2020-06-01 (valid_to 2019-11-05)
MODE B (date filter):  TCLR=0.00  survivorship=1   (the date filter does NOT catch this)
```

The Borealis case is the point: a nominal date filter eliminates the date-based leak but is blind to the survivorship leak. That residual, the discipline carried over from point-in-time backtesting, is exactly what Anachron is built to measure.

**Delta Pharma, as of 2021-06-01** (Q4 2020 revenue reported as $412M on 2021-02-04, restated to $377M on 2021-09-17). A search for `delta pharma` surfaces the restatement:

```
MODE A (unrestricted): TCLR=1.00  restatement=1
  ! result item fin-009 dated 2021-09-17 > 2021-06-01
  ! restatement item fin-009 dated 2021-09-17 > 2021-06-01 silently revises fin-008
    (as of 2021-06-01 the originally reported figure is the correct record)
MODE B (date filter):  TCLR=0.00  restatement=0   (the agent sees the original $412M — correct as of T)
```

The restatement case sharpens what "leak" means: the agent that consumes fin-009 is not just early to a news story, it is reporting a **figure that did not exist in that form at `T`** — the exact overwritten-history hazard that point-in-time databases exist to prevent in backtesting.

## How it works

The leakage logic lives in [`anachron/core/leakage.py`](anachron/core/leakage.py) — pure standard library, no framework, exhaustively unit-tested. It is the product. A thin adapter in [`anachron/inspect/`](anachron/inspect/) plugs it into the [Inspect](https://inspect.aisi.org.uk/) evaluation framework: a date-aware retrieval tool serves a date-stamped corpus, an agent solver runs the task, and a custom scorer reconstructs the agent's tool interactions from the transcript and delegates the math to the core. The core imports and tests cleanly without `inspect_ai` installed.

## Related work

The v0 scorer and the separate Routes v1 protocol occupy different scopes. The repository does not yet make a validated claim about arbitrary agent tool calls, live web retrieval, or a model family comparison. With that boundary, Anachron is deliberately distinct from recent temporal-leakage work:

- **WorldReasoner** (arXiv:2606.11816) builds an agent-forecasting benchmark that *enforces* the temporal boundary at query time and scores outcomes and cited evidence. The legacy v0 code instead supplies a synthetic trace scorer; Routes v1 uses controlled revision injection and outcome labels.
- **ExAnte** (arXiv:2505.19533) measures as-of-`T` leakage for non-agentic, memory-only models. Routes v1 uses ExAnte only as a pinned title-year sampling frame, not as a reproduced benchmark.
- **Temporal Leakage in Date-Filtered Web Retrieval** (arXiv:2602.00758) audits date-filter failures on a memory-only forecaster. Anachron's v0 examples share the temporal-boundary motivation; no transaction-cost implementation or live-web result is claimed here.

## Status

**Work in progress.** The synthetic v0 leakage core and its paired comparison workflow are available as a controlled demonstration. Routes v1 is blocked and unexecuted. Routes v2 has a frozen contract and fail-closed pre-outcome core; it has no revalidated source draft, human decision, calibration, model outcome, or reported result. The transaction-cost axis, a live-web mode, fuzzy/undated detection, broader agent traces, and a public leaderboard are not shipped.

## License

[Apache-2.0](LICENSE).
