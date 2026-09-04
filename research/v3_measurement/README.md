# V3 trace-level measurement

V3 is a new, isolated protocol. It does not retry v2. Two model-specific,
tool-less transcript calibrations run first and are excluded from metrics. The
scientific falsifier contains six new primary cases, two modes, and two models:
24 trajectories and 50 total chat requests. The full plan is unauthorized and
contains 336 trajectories, of which 264 are primary and 72 are development.

Run only from the exact detached annotated release tag:

```bash
python -m tools.run_v3_measurement research/v3_measurement/falsifier_plan.json <empty-evidence-dir>
python -m tools.analyze_v3_measurement <evidence-dir>
```

The final request omits the `tools` key. Any invalid trajectory prevents a
sealed result. The checked-in GO template is deliberately `PENDING`. Only
after reviewing the exact passing receipt and full-plan hash may Lester replace
its placeholders, deliberately set `decision` to `GO`, and preserve canonical
JSON bytes. The full plan then needs that separately authored GO object.

The runner admits only Ollama 0.33.2 and the native `/api/tags` wire schema.
For a full run, it snapshots all falsifier prerequisites into the new evidence
root and verifies that owned snapshot before any model request; caller-owned
paths are never reread after the snapshot.
