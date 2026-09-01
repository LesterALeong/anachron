# Anachron Routes v1 protocol

> **BLOCKED and unexecuted.** Routes v1 selected different source snapshots by
> experimental arm, so it cannot answer the v2 visible-date question without
> conflating date presentation and source-content exposure. Do not run, source
> approve, seal, analyze, or submit v1. It is retained only as historical
> pre-outcome provenance; the active pre-outcome design is `research/routes-v2/`.

This protocol freezes a narrow experiment before any model execution. Source
eligibility is audited before model outcomes and is not changed in response to
them. It asks a simple question: if a controlled runner is forced to retrieve a
Wikipedia revision from after an as-of year, does that post-cutoff material
reach its final answer more often than when the same runner is forced to retrieve
an eligible revision? The measured failure is route transmission, not a claim
that the model parameters contain future information.

`contract.json` is the machine-readable source of truth. Its exact title-year
registry, upstream revisions, local model digests, conditions, seeds, decoding
settings, timeout, retry rule, analysis, and pilot gates are frozen together.
This document does not override that file.

## Scope and intervention

The only source family is English Wikipedia revision history. The findings make
no claim about general web retrieval, proprietary corpora, arbitrary tool-using
agents, or other tools. Every topic is a paired cluster. The local runner
injects exactly the declared revision, or no evidence, before one model response;
it is not a live-search or general-agent benchmark. It will execute each
accepted topic under each condition and seed, using the model set declared for
the pilot or full run.

| Condition | Tool access | Required returned revision |
| --- | --- | --- |
| `no_tool` | No retrieval tool | None |
| `strict` | One forced Wikipedia retrieval | The last revision at or before 23:59:59Z on the cutoff year’s final day |
| `misdated` | One forced Wikipedia retrieval | The last revision at or before the fixed boundary plus 365 days, which must be strictly after the cutoff |

`strict` and `misdated` are experimental routes. The contaminated route is a
fixed one-year endpoint, not a cherry-picked first post-cutoff revision. A run
in either condition is trace-valid only if its structured trace records the
required forced retrieval and its returned immutable `oldid` URL respects the
relevant time boundary.
`no_tool` has a separately reported trace-validity denominator and is not
evidence of parametric leakage.

## Outcomes and labels

The frozen answer-label set is exactly `pre_only`, `post_only`, `mixed`,
`abstain_or_other`, and `invalid_output`. The deterministic exact scorer
assigns the primary answer label from the strict JSON answer and frozen aliases;
`post_only` is the primary binary outcome. A failed request is retained as an
outcome record and labels as `invalid_output`; it is never silently rerun or
substituted. The response and trace validators require digest binding, UTC
timestamps, declared model/seed/topic/condition identities, and immutable
Wikipedia revision URLs.

The primary estimand is the paired topic-cluster difference in `post_only`
rate, `misdated - strict`. Analysis uses the predeclared paired topic-cluster
bootstrap interval in the contract. Citation and trace-validity effects are
secondary and cannot rescue a failed primary gate.

## Candidate accounting and pilot decision rule

The frozen sampling frame contains 20 pilot and 40 extension source-audit
candidates. Exact revision discovery and semantic curation, completed before
any model response, identified 18 pilot and 36 extension pairs that meet the
source criteria. The pending source-curation drafts are not runnable: a human
must personally validate every accepted pair and acknowledge every rejection
before a sealed manifest can exist. No source decision may be changed after a
model outcome.

If every listed pair receives that human validation, the pilot schedule is 18
pairs x 3 conditions x 2 seeds with `qwen2.5:7b`, or 108 maximum trajectories.
It proceeds to the independent extension study only if all contract gates pass:

- at least 18 of 20 source-valid topic pairs;
- at least 0.90 valid forced-retrieval traces among `strict` and `misdated`
  runs, with `no_tool` reported separately;
- blinded two-rater kappa of at least 0.70;
- a positive `misdated - strict` primary point estimate and a strictly positive
  lower bound of its 95% paired topic-cluster bootstrap interval.

The confirmatory study is the separately audited 36 extension pairs x 3
conditions x 2 seeds x 2 pinned local models, or 432 maximum confirmatory
trajectories. The two stages therefore have at most 540 trajectories across 54
accepted source pairs. Pilot results and confirmatory results are reported
separately: the confirmatory primary confidence interval never pools pilot data.
The two models are two Qwen generations, not distinct model families, so results make no
cross-family generality claim.

The frozen confirmatory gates are at least 36 of 40 source-valid pairs, at most
0.10 invalid outputs, a positive primary point estimate for each model, and a
pooled confirmatory primary 95% interval lower bound of at least 0.05. No
statistical result is entered into this protocol. Any gate failure is a negative
result for the planned claim, not an invitation to edit the gates.

## Runtime, provenance, and retry rule

The runner makes one non-streaming local Ollama `/api/chat` request per attempt.
It sends `think: false`, the frozen temperature, seed, and `num_predict`, and a
strict JSON response schema with exactly `answer` and `citation_ids`. The
forced-retrieval trace is constructed and validated before dispatch. A response
that does not satisfy the response envelope or frozen answer/citation rules is
retained as `malformed_response` or `invalid_output`; it is not repaired.

The ExAnte code and dataset revisions in `contract.json` are source pins, not
live branches. The sampling-frame builder fetches only the exact raw pinned
GitHub `wiki/README.md` and pinned Hugging Face `exante_wiki.csv` URLs. It
records their raw SHA-256 hashes and proves the frozen title-year pairs occur in
the CSV after only NFC Unicode normalization and trimming leading or trailing
whitespace. The CSV schema is exactly `Title,Cutoff_Year`. GitHub may not
redirect. Hugging Face may redirect only to its same-host official
`/api/resolve-cache/datasets/yachuanliu/ExAnte/<pinned revision>/exante_wiki.csv`
path with one non-empty `etag` query value; the resolved URL and ETag are
recorded in the sampling frame.
Every retrieved Wikipedia revision must be represented by an immutable
`https://en.wikipedia.org/w/index.php?...&oldid=<numeric id>` URL.

Full revision text and snapshot diffs are local, ignored discovery inputs. A
pending review draft records their local artifact filename and canonical
SHA-256; sealing re-reads that artifact, re-verifies every bound revision,
snippet, and anchor, then removes the filename and all raw text from the
runnable manifest. The sealed manifest retains immutable revision metadata,
evidence snippets, attribution, semantic mapping, and the discovery-artifact
SHA-256. Runtime validation is self-contained; an explicit discovery directory
is required only for the stricter offline provenance re-validation command.

At most one retry is permitted, and only after a transport failure before any
response bytes. A timeout after dispatch, malformed response, returned error,
or invalid output is recorded and never replaced. The request timeout is 120
seconds. The append-only JSONL ledger binds each record to the contract,
sampling frame, sealed manifest, source-code hash, model digest, and deterministic
trajectory identity; a resume may write only the single permitted transport retry.

## Human gates and reporting boundary

Human source curation is a release gate, not a formality: no pending draft can
be sealed or scheduled. After a complete phase finishes, two independent raters
must label a condition-blinded response packet using the frozen five-label set.
Their Cohen's kappa and every disagreement are reported as a human-audit result;
they do not replace or alter the deterministic primary labels. The pilot still
requires kappa at least 0.70. Neither Codex-generated curation, a source audit,
a dry run, nor an unblinded model response counts as human validation or as a
paper result.

The pinned local paper-build input is Tectonic 0.17.0, ZIP SHA-256
`f61ce51f0b0ade1015b7de7ef368541c5424e9756ecbd0d7af97d6d48030845f`.
This records a reproducibility pin only; it does not establish arXiv build
compatibility.
