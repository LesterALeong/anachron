# Reproducing the v2 Step A boundary

This repository contains only the pre-outcome core. Validate the frozen contract without contacting a service:

```powershell
python -c "from anachron.routes.v2 import load_contract; load_contract('research/routes-v2/contract.json'); print('valid')"
python -m unittest discover -s tests -v
```

The final-results renderer and paper builder are module-only CLIs. Use the
following forms from the repository root; direct `python tools/...` invocation
is intentionally unsupported because it cannot prove the package import route.

```powershell
python -m tools.render_routes_results --help
python -m tools.build_routes_v2_paper --help
```

Before any local model call, create freshly revalidated source receipts for exactly one phase, derive that phase's pending draft through the path-bound admission command, have a human produce the direct decision artifact, seal it, and freeze a clean pushed Git closure. Development is six topics and 24 trajectories. Its frozen threshold must pass in a create-only replayable phase-evidence artifact before pilot source revalidation, draft, decisions, sealing, scheduling, or execution is admitted. Pilot is 18 topics and 108 trajectories; only a replayed passing pilot admits the 36-topic, 432-trajectory confirmatory phase. The only execution boundary is `admit_execution_session(...)`: it create-only derives the exact phase schedule, admits only the frozen local `http://127.0.0.1:11434` endpoint, inventories the frozen model IDs/digests, calibrates each scheduled model separately in the owned session, and writes a locked fsynced claim/outcome journal. The session must retain a typed `TransportResult`; body-read or reset errors must set `response_object_exists=True`. Those authority boundaries are intentionally not crossed by this Step A checkpoint.

After an authorized, completed finite-set run, use the route-redacted audit
builder and both predeclared rater templates. The final builder accepts only the
complete confirmatory analysis root plus the clean pushed frozen checkout, then
replays the sole reducer itself before compiling TeX. Neither the audit packet
nor the paper archive may contain raw source content or unredacted model
envelopes. This repository currently contains no such outcome artifacts and this
is not an authorization to produce them.
