# Anachron Routes v2 protocol

Routes v1 is blocked and unexecuted. It selected different source snapshots, so a response difference could be simple evidence copying. Routes v2 instead holds the post-cutoff source content, opaque citation ID, question, model, seed, decoding parameters, and output schema fixed. Its primary development intervention changes only the visible document date between `post_truthful` and `post_misdated_eligible`.

The model is told the temporal cutoff and instructed to return `ABSTAIN` exactly when the supplied document is insufficient or conflicts with the cutoff. No condition name, source arm, oldid URL, revision ID, actual timestamp, or route marker is model-visible. A scanner rejects them.

The frozen frame names three disjoint exact title/year sets: development (6), pilot (18), and confirmatory (36), for 60 unique records. Every v2 row proves its exact membership by parent row index and canonical parent-row hash against the tracked `research/routes-v1/sampling_frame.json`, whose GitHub/Hugging Face URLs, byte hashes, ETag, and revisions are retained in the v2 frame. No v1 approval, draft, manifest, or result is reused. Each record is revalidated afresh as a v2 phase-tagged artifact. Development is exactly 24 trajectories (6 topics x one model x two seeds x two primary arms) and its mean paired `post_only` threshold is >= 0.25, subject to source, calibration, schedule, envelope, hash, and trace-integrity gates. It is a development screen, not an evaluation result or paper claim. Pilot is exactly 108 trajectories and may not begin source review or execution until a create-only, replayable positive development evidence artifact passes every gate. Confirmatory is exactly 432 trajectories and may not begin until a replayable positive pilot evidence artifact passes every gate. Development, pilot, and confirmatory each use separate phase roots, receipts, drafts, decisions, manifests, freezes, schedules, calibrations, journals, and reducers; no phase may be reused or pooled.

Raw Wikipedia discovery material from v1 may be used only as an input to fresh v2 revalidation. A pending draft is derived from the exact receipt paths for one selected phase plus a separately bound phase-tagged source-mapping input; its content hashes, oldids, immutable URLs, and visible dates must exactly match those receipts. Human source decisions bind that exact draft hash and explicitly mark every item in that phase PASS or REJECT. Only all-PASS yields the runnable manifest for that phase. Any REJECT yields a durable phase-local FAIL receipt and no manifest. No mutable per-row approval field exists or is honored.

The create-only `routes-v2-schedule` uses algorithm `routes-v2-counterbalance-v3`, fixed seed `20260901`, and the exact phase-local ordered trajectory count (24, 108, or 432). It is re-derived from the exact contract, phase manifest/PASS source gate, matching phase freeze receipt, and transitive code-closure lock. The lock also hashes its `.gitattributes` file and requires explicit `text eol=lf` governance for every closure source and exact-bound text artifact, so a Windows `core.autocrlf=true` checkout must retain the Git-blob bytes. A pre-existing schedule is accepted only when its canonical bytes are identical. No executor may substitute an alternate order or combine journals.

`admit_execution_session(...)` is the sole model-execution entrypoint. It first requires a clean, pushed checkout with an exact closure lock and verifies that every already loaded Routes v2, renderer, and builder module resolves from that same Git-blob-verified checkout. It admits only the contract's local `http://127.0.0.1:11434` Ollama endpoint, inventories the frozen models through that same client, and sends a separate canonical synthetic calibration request through the same client/session immediately before that model's first scientific claim. Each create-only calibration receipt retains exact endpoint/configuration, inventory, model ID/digest, closure, code, session nonce, and request/response bytes. A receipt from another model, client, endpoint, closure, or session is invalid.

The exclusive, fsynced JSONL journal writes a full `dispatch_claim` before every scientific network call, then a typed `terminal_outcome`. Both bind the schedule prefix, all admission hashes, exact request bytes, model-visible delivery hash, exact response bytes, and record/prefix hash chain. A claimed trajectory without a terminal record is `UNKNOWN_AFTER_CLAIM` and permanently halts redispatch. Any response object, including HTTP/header/body-read/reset/timeout failure, ends retry authority. Only a persisted transport failure with no response object may receive exactly one immediate retry before any other trajectory.

For any future pilot, the output audit population is exactly one fixed seed, both
primary arms, and every accepted pilot topic: 36 outputs after the planned
18-topic source gate. Confirmatory audit uses the same fixed seed, both primary
arms, all accepted 36 topics, and both declared models: 144 outputs. Private
selection retains the blind join and deterministic machine label. The public
packet contains only a blind audit ID, question, alias rubric, value-sanitized
inspectable payload, and rating instructions. It withholds the machine label,
trajectory, condition, model identity, seed, source dates, citation IDs,
immutable URLs, unredacted envelopes, and private join hash.

Two distinct predeclared raters must submit complete, hash-bound labels. Cohen's
kappa is semantic-audit information only and excludes transport/malformed rows.
Human labels never replace the deterministic machine score used by the primary
finite-set result. There is no adjudication claim. The only reducer accepts
guarded validated execution and audit objects and derives its effect, stability,
gates, and positive/negative result mode itself. A final analysis receipt binds
the contract, source decisions/gate/manifest, freeze and session-calibration receipts,
schedule, claimed-dispatch journal, audit plan/submissions, source code, Git provenance, and
generated outputs. Final TeX and the arXiv archive are never assembled from a
free-form ledger, result object, gate flag, effect, or prose. They are built only
by replaying the exact confirmatory analysis root against the clean pushed frozen
checkout immediately before TeX compilation.
