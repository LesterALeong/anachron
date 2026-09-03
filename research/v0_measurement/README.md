# Anachron v0 measurement

This directory freezes a small, local, synthetic measurement study. It asks two
exactly identified Ollama models through a two-request tool loop per trajectory, then
scores the returned date-stamped synthetic records. It does not grade answers,
rank models, or make claims about live web retrieval.

The pre-falsifier plan is 6 samples × 2 modes × 2 models × 1 repetition = 24
trajectories. The full plan, 27 samples × 2 modes × 2 models × 3 repetitions =
324 trajectories, is not authorized by this repository or its pending template.
It can begin only after a fully valid, passing pre-falsifier has been analyzed,
its create-only canonical receipt has been sealed, and a later human writes a
separate, strict `GO` artifact bound to the exact full-plan and receipt bytes.
That artifact must name Lester Leong, include a timezone-aware UTC authorization
time, and use the fixed authorization statement enforced by the runner.

Run the pre-falsifier only after reviewing `PROTOCOL.md` and
`ACCEPTANCE_MATRIX.md`:

```text
python -m tools.run_v0_measurement --plan research/v0_measurement/falsifier_plan.json --output evidence/v0-pre-falsifier
python -m tools.analyze_v0_measurement evidence/v0-pre-falsifier
python -m tools.seal_v0_falsifier_receipt --evidence evidence/v0-pre-falsifier --plan research/v0_measurement/falsifier_plan.json --output evidence/v0-pre-falsifier-receipt.json
```

The runner accepts only a loopback `http://HOST:PORT` Ollama endpoint, checks the
exact plan digests via `/api/tags`, makes no retries, and refuses to overwrite an
existing evidence directory. A completed directory contains `runtime.json`,
`journal.jsonl`, `analysis.json`, generated `README.md`, `manifest.json`, and raw
request/response artifacts. The manifest seals every generated
file except itself; its inventory includes `runtime.json` and `analysis.json`.
`manifest.sha256` seals the manifest itself.

Source admission v2 binds the exact plan, Python runtime, annotated release-tag
objects, and governed Git blobs. Use `--repository-root` with run, seal, or
analysis when verifying an evidence tree against a preserved tagged checkout.
The raw directory is a fixed inventory of two server responses plus five files
per planned trajectory; extra or missing raw files invalidate evidence.

`full_go.template.json` is deliberately pending and records neither user
authorization nor a permission to run the 324-trajectory plan. A full run
also copies the exact falsifier receipt, GO artifact, and required falsifier
evidence bytes into its own sealed manifest before it contacts Ollama.
The full evidence tree preserves the entire sealed falsifier subtree and the
analyzer recursively verifies that embedded tree before accepting the full run.
