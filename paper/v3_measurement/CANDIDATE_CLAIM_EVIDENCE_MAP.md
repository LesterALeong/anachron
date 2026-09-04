# Anachron v3 candidate claim and evidence contract

This contract applies only after a complete, authorized, replay-valid full
study is projected through the frozen candidate tooling. It is outcome-neutral:
a false scientific gate, a zero difference, or a negative difference remains a
candidate result and must be reported rather than suppressed.

| Generated claim | Projection field | Evidence boundary |
| --- | --- | --- |
| Primary TCLR by model and mode | `cells[*]` where `split` is `primary` and `metric` is `tclr` | Manifest-bound first-response and tool-result bytes replayed by the detached frozen protocol. |
| Query, restatement, and survivorship diagnostics | `cells[*]` for the named metric | Same replayed traces; these diagnostics are separate from TCLR. |
| Paired direction | `paired_tclr_reductions[*].sign_class` | Exact pairing key `(split, model, sample_id, repetition)`. |
| Scientific gate status | `scientific_gates` and `analysis_go` | Native frozen-analyzer output, reconciled with the exact projection. |
| Cohort accounting | `split_counts` | Frozen full plan and replayed trajectory identities. |

Forbidden: terminal answers, raw model text, trace excerpts, execution GO,
falsifier receipt content, review content, approval content, external-contact
requests, upload instructions, or generalized population claims.
