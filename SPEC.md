# Anachron — Design Notes

**Name:** Anachron (anachronism: using information from the wrong time). Package: `anachron`.

**One line:** legacy v0 design notes for an Inspect-pluggable, synthetic-corpus scorer. Routes v1 is BLOCKED and unexecuted; the active pre-outcome design is schema-incompatible Routes v2 in `research/routes-v2/`.

## 1. Why this exists

LLM agents increasingly operate over time-anchored tasks ("analyze this as of date T"), which are only valid if the agent does not consult the future. The legacy v0 design is an Inspect-compatible component for controlled, supplied traces; it is not evidence that any deployed agent or task has been evaluated.

The broad idea of temporal or look-ahead leakage is not new (ExAnte, Look-Ahead-Bench, WorldReasoner). The v0 code is a narrow synthetic-corpus mechanism for leakage-rate scoring. It does not, by itself, establish a result for live tools, arbitrary agents, or a model family. The transaction-cost axis remains a future design direction, not shipped functionality.

## 2. What makes it distinct

1. The metric is the leakage rate of the agent's own tool calls and queries, not outcome accuracy or cited-evidence precision.
2. The implemented v0 corpus represents retrieval, survivorship, and restatement/revision cases. Transaction cost is not implemented.
3. It is a scorer and method, not a fixed benchmark; the Inspect adapter is designed to operate over compatible supplied traces and tasks.
4. It scores under enforcement rather than only enforcing: it quantifies the residual leakage that remains even when nominal date filters are on.
5. Domain-general but finance-grounded: restatements and survivorship give a concrete, hard testbed that generalizes.

(See "Related work" in the README for how this sits next to WorldReasoner, ExAnte, and date-filtered-retrieval audits.)

## 3. Core metric

**TCLR (Tool-Call Leakage Rate), primary:** over a run, the number of tool interactions that surface or consume an item dated after T, divided by total tool interactions. A "tool interaction" counts both the agent's query arguments and the results returned to it.

**Survivorship Leakage (finance slice):** the fraction of tasks where the agent's universe or data includes entities that were not point-in-time valid as of T (delisted, not yet listed, or index-membership anachronisms).

**Restatement Leakage (shipped v0.1):** interactions that consume a post-T restatement of an earlier item (``restates_id`` on the corpus item). As of T the originally reported figure is the correct record; consuming the revision is the vendor-overwritten-history hazard from backtesting. By construction a labeled subset of result leaks; reported separately, not folded into TCLR.

**Two run modes:**
- **Unrestricted:** tools may return post-T items. Measures the agent's intrinsic tendency to reach for the future.
- **Enforced:** a date-filter layer is nominally applied. Measures the residual leakage that slips past controls. The gap between the two modes is itself a reportable finding.

## 4. Design decision: a date-stamped corpus gives exact, by-construction detection

The hard problem is recognizing post-T information in free text, not accessing the trace. v0 sidesteps fuzzy NLP entirely: every item the retrieval tool can return carries a known publication date, so leakage is exact and reproducible. An interaction leaks if and only if it touches an item dated after T. No LLM-judge, no regex guessing. Fuzzy and live-web detection (LLM-judge, semantic leakage, undated facts) is explicitly a later phase.

## 5. v0 scope

In scope:
- One Inspect eval, registered as an entry point and pip-installable.
- A small point-in-time corpus: a finance-grounded slice (entity-bearing, with validity windows) plus a general-events slice of dated world events.
- As-of-T task samples carrying `metadata={"as_of": T}`.
- A date-aware retrieval tool that tags every result with its publish date, with an Unrestricted/Enforced switch.
- A scorer emitting TCLR (exact, by construction) and Survivorship Leakage on the finance slice, with the offending calls surfaced in the explanation and metadata.
- Optional execution through the Inspect adapter when a user configures a compatible provider. This repository does not report a validated frontier-model, live-tool, or multi-agent result.

Shipped since v0: the restatements axis (v0.1) — ``restates_id`` corpus links, an exact ``is_restatement_leak`` predicate, restatement pairs in both corpus slices, and as-of samples spanning the restatement windows.

Later phases: live-web retrieval; LLM-judge and semantic leakage detection; the transaction-cost axis (designed for, not yet shipped); multi-step subagent and handoff trace coverage; a hosted leaderboard.

## 6. Architecture (Inspect)

- **Sample:** `input` framed "as of T"; `metadata={"as_of": T}`.
- **Solver:** a legacy intended `react()` agent (or `use_tools(search()) + generate()`) integration with date-aware tools; this is an adapter design, not a reported live-agent result.
- **Tool:** a retrieval tool over the date-stamped corpus; each result includes its publish date; Enforced mode applies a server-side date filter.
- **Scorer:** walks the message trace, reads the assistant's tool calls and the tool results, compares item dates to T, and returns the leakage rate with `mean()` and `stderr()`.
- **Packaging:** a setuptools entry point in the `inspect_ai` group.

## 7. Routes study boundary

Routes v1 is blocked and unexecuted historical provenance, not an extension of
the legacy v0 result table. Its differing source snapshots confound a source
content intervention with visible date. Routes v2 holds post content and opaque
citation identity fixed across its primary arms, changing only the visible
document date. It is development-only, pre-outcome, and does not benchmark live
search or arbitrary agent tool use. See [`research/routes-v2/PROTOCOL.md`](research/routes-v2/PROTOCOL.md)
for its frozen boundary. No Routes version has a model outcome claim here.

## 8. Roadmap

- **v0:** a working Inspect eval, a short technical write-up, and a public release. *(done)*
- **v0.1:** the restatements axis. *(done)*
- **Next:** an LLM-judge detector for fuzzy leakage.
- **Later:** a live-web mode and the transaction-cost axis.
