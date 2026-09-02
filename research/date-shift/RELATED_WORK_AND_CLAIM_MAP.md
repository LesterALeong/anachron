# Related work and claim map

## Narrow claim

The planned study does not claim that dates broadly control LLM reasoning. It measures, for an author-audited finite set and two exact local Qwen builds, whether falsifying the displayed eligibility date of byte-identical post-cutoff document content changes temporal answer acceptance. The intervention is a document-metadata integrity failure, not retrieval quality, a search-engine filter, an agent benchmark, or a reproduction of ExAnte.

## Closest work

Chiang and Lee (2024) manipulate webpage metadata and appearance in retrieval-augmented reasoning. Their study establishes that retrieved-page presentation can affect reasoning; this study instead holds a single document fixed and changes only whether its displayed date appears admissible under an explicit cutoff. It therefore does not claim novelty for the broad proposition that metadata matters.

Ding et al. (2026) study temporal critique for Ex-Ante reasoning, including prompt and fine-tuning approaches to temporal cutoff compliance and parametric post-cutoff knowledge. This study does not train a model or estimate parametric knowledge; it isolates a falsified retrieved-document eligibility signal while keeping the retrieved content identical.

ExAnte supplies only the pre-existing title/year candidate frame. No ExAnte labels, answers, scores, or benchmark comparisons are used.

## Claim-to-evidence map

| Planned claim | Required evidence | Forbidden overreach |
| --- | --- | --- |
| A visible eligibility date can change temporal answer acceptance in this protocol. | Immutable journal replay, audited sample, exact model/runtime/decoding bindings, finite-set analysis. | A statement about all LLMs, web retrieval, agents, or deployment frequency. |
| The observed effect is attributable to displayed-date manipulation within the paired packets. | Byte-identical treatment packet check after normalizing the displayed date. | A claim that models believe timestamps or that metadata is the only relevant causal path. |
| Grounded citation-compliant behavior differs from answer-only behavior. | Separate answer-class, citation-compliance, and joint-outcome reducers. | Treating citation failure as absence of an answer or silently discarding invalids. |
