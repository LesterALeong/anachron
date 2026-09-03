# Protocol

## Question

On a fixed synthetic corpus, does server-side date filtering reduce the rate at
which a model's single retrieval call returns post-cutoff records, while leaving
at least one finance survivorship failure visible under the date-only filter?

## Fixed design

- Registry: `anachron/data/v0_samples.py`, 27 immutable sample IDs.
- Retrieval: the deterministic substring search in `anachron/data/corpus.py`.
- Modes: unrestricted retrieval and enforced retrieval, where only records dated
  strictly after each sample's as-of date are removed.
- Models: the exact names and SHA-256 digests in the selected plan.
- Generation: a native two-turn `/api/chat` tool loop at frozen decoding settings.
  The first assistant response must contain exactly one `anachron_search(query)`
  call. The runner performs that retrieval once, then sends the assistant tool
  call and `role=tool` result to a second request. The final answer is retained
  but never graded.
- Scoring: TCLR and secondary axes from `anachron.core.leakage` over the one
  returned interaction. There is no answer-quality score.

## Evidence and stop rules

Source admission v2 records the canonical plan hash, exact CPython identity,
annotated-tag object and peeled-commit OIDs, and every governed Git blob OID
and SHA-256. Offline verification uses the preserved tag/blob objects rather
than current `HEAD`.

Raw evidence is also a frozen closure: the plan derives exactly two server
responses and five files per trajectory. The producer and analyzer use the
same inventory and reject any missing, extra, symlink, junction, reparse-point,
or non-regular raw entry before raw artifacts are parsed.

Before each trajectory, the runner appends a claim to `journal.jsonl`; it appends
one terminal record after the two-request tool loop. It never retries an invalid or
failed trajectory. Raw request, raw response, and local tool result are saved.
`runtime.json` lists the execution record. `analysis.json` is recomputed from
the plan, runtime, and journal. `manifest.json` hashes every evidence file other
than itself; the analyzer rejects altered, missing, or extra files.

Run the 24-trajectory falsifier first. Stop if its gates fail. Do not edit either
plan after seeing an outcome. Only `tools/seal_v0_falsifier_receipt.py` can
create the canonical, create-only falsifier receipt, and only after independent
analysis verifies that all 24 trajectories are valid and every falsifier gate
passes. The 324-trajectory full plan remains frozen until a later human creates
a separate strict `GO` artifact whose full-plan ID and two hashes bind it to the
exact full-plan bytes and exact receipt bytes. The GO schema also requires the
fixed authorization statement, `authorized_by: Lester Leong`, and a timezone-aware
UTC authorization time. The checked-in template is `PENDING` and is not
authorization. Before source admission, output creation, or any `/api/chat`
request, the full runner confirms the falsifier evidence's `plan.json` is
byte-exactly the canonical falsifier plan, reconstructs the receipt,
validates the GO artifact, and retains exact prerequisite bytes in its manifest.
For the full plan, this is the complete sealed falsifier subtree, including its
inner manifest, receipt, and GO artifact; analysis recursively verifies it and
rebuilds the receipt byte-for-byte.
