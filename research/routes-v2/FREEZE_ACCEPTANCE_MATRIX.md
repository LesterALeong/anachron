# Routes v2 Step A acceptance matrix

| Requirement | Executable boundary |
| --- | --- |
| v1 isolation | Every v2 loader requires `routes-v2-*`; v1 documents fail closed. |
| Date-only primary treatment | `primary_packets` normalizes the displayed date and requires byte-identical remainder. |
| No route leakage | `scan_prompt_packet` rejects condition names, pre/post markers, dates-as-provenance, oldids, URLs, revisions, and Routes markers. |
| Phase separation | Development, pilot, and confirmatory require separate source artifacts, freeze receipts, schedules, journals, and reducers; no pooling is admissible. |
| Frozen phase partition | `validate_contract` and `validate_sampling_frame` bind three disjoint title/year inventories: development=6, pilot=18, confirmatory=36, 60 unique total, each with a parent-row index/hash against the tracked v1 frame and all source pins. |
| Phase schedules | `_schedule_rows` emits exactly 24 development, 108 pilot, or 432 confirmatory rows, with only that phase's selected items, models, seeds, and conditions. |
| 0.25 threshold | The contract validator accepts only the named paired `post_only` threshold. |
| Direct source approval | `prepare_pending_draft` requires the exact selected phase receipt count; `source_gate_receipt` binds its phase-tagged receipts, draft, and decisions; `seal_manifest` requires all-PASS for that phase. Every pilot/confirmatory source boundary also requires a replayable positive predecessor-evidence artifact. |
| Bounded source construction | `build_excerpt_receipts` derives only unique-anchor, Unicode-safe UTF-8 windows under the frozen 4096-byte cap. Source mappings and pending drafts reject full-content fields; `validate_routes_v2_source_construction` reads six ignored development artifacts without creating a decision. |
| Clean code provenance | `build_code_closure` resolves the transitive AST local-import closure, hashes `.gitattributes`, and requires explicit `text eol=lf` governance for every closure source and exact-bound text artifact. `admit_clean_checkout` requires clean HEAD/tree/origin/remote parity and exact Git blobs; `validate_loaded_code_closure` additionally rejects already-loaded code from any other checkout. |
| Canonical counterbalance | `create_schedule` create-only persists only the re-derived `routes-v2-counterbalance-v3` phase-local order bound to contract, manifest, PASS source gate, matching phase freeze, and closure. |
| Phase gates | `admit_execution_session` and `open_validated_execution(phase=...)` fail closed for pilot without replayable positive development evidence and confirmatory without replayable positive pilot evidence. |
| Session calibration before outcomes | `admit_execution_session` requires clean pushed provenance, the exact local loopback endpoint, and inventory; `ExecutionSession` calibrates each model through its owned client/session before that model's first claim. |
| Claim-before-dispatch journal | `ExecutionJournal` locks one fsynced canonical JSONL journal, validates every append/replay, requires strict prefix ordering, and hard-halts `UNKNOWN_AFTER_CLAIM`. |
| Retry boundary | Only a persisted `transport_failure_no_response_object` can produce attempt two at the same schedule index; all response-object/read/HTTP/timeout/malformed states are terminal. |
| Trace validity | Terminal evidence derives from the claimed exact request bytes, model-visible delivery hash, response bytes, valid `ok` envelope, and chain hashes. |
| Route-redacted audit | Private selection derives blind IDs from a supplied private key. The public packet exposes only its blind ID, question, aliases, sanitized inspectable payload, and instructions; the fixed-seed primary-arm population is complete before either rater labels it. |
| Human-label boundary | Two predeclared raters require 100% coverage; semantic kappa excludes transport/malformed rows and never replaces machine labels. |
| Analysis provenance | The replay receipt hashes the exact analysis root, execution artifacts, audit report, result, and clean pushed checkout provenance. |
| Paper boundary | Final results and the archive are regenerated only by replaying the exact analysis root in a clean pushed checkout; no free-form ledger/result/gate/effect/prose input exists. The archive excludes raw sources, unredacted records, and blocked v1 material. |

No source draft is yet revalidated or human-decided, no calibration has been run, and no model outcome is present.
