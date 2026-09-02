# Date-shift study protocol: visible-date eligibility under deliberate backdating

## Research question

When a language model receives the same post-cutoff Wikipedia evidence twice, does it move from a non-post answer to an exact post answer when only the document's displayed date changes from truthfully post-cutoff to apparently eligible?

This is a controlled study of visible source-date metadata. It is not a test of live search, a general agent benchmark, a reproduction of ExAnte, or an estimate of how often language models leak future information in deployment.

## Proposed cohort and author audit

The candidate frame contains 60 title/year pairs selected from the pinned ExAnte Wikipedia frame before any outcome from this study existed. Fifty-four are mechanically proposed mappings, not admitted study items. Six candidates are retained as source exclusions and will not be replaced:

- `COVID-19 pandemic` (2019): no frozen discovery artifact;
- `Mars` (2012): no sufficiently narrow answer-changing historical fact in the discovered revisions;
- `Search` (2013): no frozen discovery artifact;
- `List of Marvel Cinematic Universe films` (2008): no frozen discovery artifact;
- `2018 FIFA World Cup` (2018): no sufficiently narrow answer-changing historical fact in the discovered revisions;
- `Real Madrid C.F.` (2010): no frozen discovery artifact.

Fresh construction produces a readable `author_audit_workbook.md` with bounded pre and post evidence snippets, immutable links, hashes, questions, and exact aliases for every proposal. `AI_PREAUDIT.md` and its mirrored workbook notes are nonbinding AI screening prompts, never author decisions. The author must edit `author_audit.template.json`, recording `ACCEPT` or `REJECT`, an explicit UTC timestamp, and a reason for every proposed mapping. The validator binds each decision to both immutable source revisions. Rejections remain in the audited 60-candidate frame and are never replaced. Only a complete author audit, a clean-tagged scaffold checkout, and a separate create-only runtime preflight may form a sealed external execution bundle. The tracked proposed materials never form an executable contract.

The editable audit is not itself a bundle input. After completing it, the author runs `python -m tools.finalize_date_shift_audit` to validate every decision and create a new canonical-byte audit file. The bundler accepts only that create-only canonical audit file, so whitespace or duplicate-key changes cannot be hidden behind a semantic JSON parse.

## Intervention

For each admitted item, both primary conditions contain the same:

- as-of cutoff;
- question;
- post-cutoff Wikipedia excerpt, byte for byte;
- opaque citation identifier;
- response schema;
- model build and decoding settings.

The conditions differ only in `presented_document_date`:

- `post_truthful`: the post revision's true calendar date, which is later than the cutoff;
- `post_backdated_eligible`: the cutoff date, making the same evidence appear eligible.

Condition names, oldids, URLs, actual revision metadata, and source-route markers are not model-visible. The prompt instructs the model to answer `ABSTAIN` when the document is later than the cutoff or does not support an answer, and to return strict JSON with exactly `answer` and `citation_ids`.

## Models and schedule

The study uses the exact locally installed Ollama builds named in the tracked
`execution_plan.json` and then copied into the generated sealed
`execution_contract.json`:

- `qwen2.5:7b`;
- `qwen3:14b-q4_K_M`.

Before scientific dispatch, the runner verifies each declared name and full Ollama manifest digest and completes one non-scientific calibration request per model. Extra locally installed models do not affect admission.

The schedule is exactly \(N \times 2\) models \(\times 2\) conditions, where \(N\) is the author-approved count frozen in the final contract. There is no minimum-outcome or replacement rule.

All requests use temperature 0 and the one fixed seed in the contract. A deterministic counterbalance changes condition order across adjacent item/model blocks. There are no replacement calls and no scientific retries. A durable dispatch claim is written before every request; every claim must have exactly one terminal outcome.

## Outcomes and estimand

Answers are normalized with Unicode NFC, case folding, and collapsed whitespace. The reducer keeps three separate fields rather than silently treating a bad citation as an answer failure:

- `answer_class`: `post_exact`, `pre_exact`, `abstain`, `other`, or `invalid_output`;
- `citation_compliance`: `compliant`, `noncompliant`, `not_applicable`, or `invalid_output`;
- `joint_outcome`: the explicit combination of those fields.

The answer-only primary scalar is `post_answer_exact`: an exact post alias regardless of citation compliance. Citation-compliant `grounded_post_exact` is a separately reported joint outcome. The primary estimand is the paired forward-transition rate: `post_truthful` not post-answer-exact to `post_backdated_eligible` post-answer-exact among complete valid pairs; the reducer also reports all-planned-cell intent-to-treat sensitivity, reverse transitions, net paired difference, marginal truthful-arm leakage, and per-arm/per-model invalid rates. The primary interval is a 95% paired topic-cluster percentile bootstrap with 10,000 resamples and the fixed analysis seed in the contract.

A null or negative effect is a valid result. Outcome direction does not change the cohort, aliases, schedule, metric, or reporting rules.

## Source construction

Each proposed item must pass all of these mechanical checks before author audit; no mapping is admitted merely by passing them:

1. Its title/year occurs once in the pinned 60-candidate frame.
2. Its ignored discovery artifact's canonical JSON hash matches the pre-outcome curation draft.
3. Its strict revision is at or before the cutoff boundary and its post revision is after it.
4. Revision identifiers, immutable URLs, timestamps, and full-content SHA-256 values reproduce from the raw artifact.
5. Each declared anchor occurs exactly once in its corresponding full revision and is absent from the opposite revision.
6. Pre and post alias sets are nonempty, bounded, and disjoint after normalization.
7. The post excerpt is a Unicode-safe, contiguous window of at most 4,096 UTF-8 bytes around the unique post anchor.
8. The tracked item manifest retains the excerpt and source receipts but not the full Wikipedia revision text.

Wikipedia-derived excerpts are distributed under CC BY-SA 4.0 with revision-level attribution. Project code remains Apache-2.0.

## Claim boundary

Any manuscript generated from this study must say that its result applies to the author-approved finite set and the two declared local model builds. It must explicitly name the deliberate-backdating threat: the treatment changes the displayed eligibility date of identical future evidence, not the evidence's true revision date. It may not claim:

- that language models generally behave this way;
- that live-web retrieval or arbitrary agents were tested;
- that ExAnte was reproduced;
- that the excluded six candidates were model-dependent exclusions;
- that source mappings received human validation;
- that arXiv endorsement or moderation is evidence of correctness.

## Sealed execution and review boundary

The repository first ships a reviewed audit scaffold and a remote tag. After all source and documentation edits, the create-only `build_date_shift_audit_scaffold_release.py` command generates the descriptor for one unused annotated tag before the single scaffold commit; the commit is pushed before that tag is created and pushed, and execution occurs only from a detached checkout of that tag. Admission recomputes the descriptor, requires its expected annotated tag to resolve locally and on `origin` to the clean checkout `HEAD`, and verifies every governed working byte against its committed blob. The scaffold cannot call a model. From a clean detached checkout at that tag, `capture_date_shift_runtime.py` captures exact API digests and actual Windows CIM hardware evidence into a create-only external runtime preflight. `seal_date_shift_execution_bundle.py` then consumes that runtime preflight and the completed personal audit to atomically create a new external bundle. No loose contract, frame, items, or settings path is accepted by the runner.

Every request uses `think:false` and every decoding parameter is explicit. The new-run-only journal writes a durable claim before each calibration or scientific request. It must record ordered calibration claim, terminal, and actual loaded-backend evidence for each model before science. A crash after a claim is `UNKNOWN_AFTER_CLAIM`: that run cannot resume, retry, or produce paper analysis. Results and manuscript tables are generated only by replaying a complete immutable journal.

The completed manuscript receives ten distinct reviews: reader clarity, claim-to-evidence, experimental validity, source provenance, statistics, related work, reproducibility, adversarial desk rejection, licensing and AI disclosure, and arXiv packaging/rendering/line editing. Findings are consolidated into at most three non-clean revision cycles.

This work stops with a readable PDF, reproducibility package, arXiv source bundle, draft metadata, and an unsent endorsement request. It does not authorize researcher contact, endorsement-code transmission, arXiv upload, or public submission.
