# V5 candidate claim and evidence contract

| Generated claim | Projection field | Evidence boundary |
| --- | --- | --- |
| Scheduled cohort accounting | `scheduled`, `groups[*].scheduled` | Frozen v5 schedule, materialized full plan, and verified evidence manifest. |
| First-tool adherence by model and mode | `groups[*].first_tool_adherence` | Replayed first-response classifications from the verified v5 answer-free projection. |
| First/final non-adherence counts | `groups[*].first_category_counts`, `groups[*].final_category_counts` | The same verified v5 projection. First and final taxonomy are reported separately. |
| Conditional post-cutoff exposure | `groups[*].conditional_exposure` | Adherent first calls only; denominator is the count of adherent first calls, never all scheduled calls. |
| Eligible paired contrast | `paired_contrasts[*]` | Predeclared pairing rule applied only to pairs satisfying the declared adherence rule. |
| V4 exclusion | `v4_included_count` | Required value is exactly zero in every candidate-stage artifact. |
| Operational completeness | Complete compatibility, primary, custody, and replay receipts | Only a complete replay-valid v5 campaign is candidate-eligible. |

Forbidden: any v4 empirical output; terminal answers; raw model text;
source-audit rationales; GO, review, or approval content; endorsement requests;
upload instructions; generalized population claims; answer-quality claims; and
any candidate generated from an operationally invalid campaign.

Self-custody evidence supports internal consistency and byte replay. It can
detect missing, partial, malformed, or inconsistent artifacts, but does not
provide independent raw-response provenance.
