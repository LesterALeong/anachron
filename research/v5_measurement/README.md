# Anachron v5 measurement

V5 successor v6 repairs the root-thread access needed to classify V4's failed isolated startup. It remains offline and does not authorize model execution, process control, outreach, upload, or submission. V4 and the consumed V5 failed attempt are preserved immutable evidence, and V3/V2 are preserved historical evidence; none is relabeled or retagged.

```text
python -m tools.validate_v5_contract --repository-root <repo>
python -m tools.materialize_v5_inputs --repository-root <repo> --accepted-audit <accepted-audit> --v4-source-manifest <v4-manifest> --v5-source-manifest <v5-manifest> --runtime-identity <captured-runtime> --output <external-materialization-root> --evidence-output-root <external-evidence-root>
python -m tools.run_v5_recovery --repository-root <repo> --full-plan <materialization-root>/full_plan.json --conditional-go <authorization> --output <external-evidence-root> --preflight-only
python -m tools.run_v5_recovery --repository-root <repo> --full-plan <materialization-root>/full_plan.json --conditional-go <pending-record> --output <external-evidence-root> --pending-only
```

Materialization publishes exactly seven create-only members: `carry_forward.json`, `compatibility_plan.json`, `full_plan.json`, `materialization_receipt.json`, `runtime_identity.json`, `schedule.json`, and `source_manifest.json`. Receipt schema `anachron-v5-materialization-receipt-v3` binds actual raw runtime and schedule hashes. The runner checks those bytes and canonical runtime/schedule semantics before transport or output.

GO and PENDING are disjoint. `--preflight-only` accepts only GO. `--pending-only` accepts only a fully bound PENDING record and prints `PENDING_VALID`; it cannot run measurement. Science, schemas, models, seeds, and PENDING semantics remain unchanged. Campaign Execute mode and any future identity capture remain behind fresh explicit authorization after implementation, review, QA, CI, tag, and external-artifact gates.
