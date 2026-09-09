# Anachron v5 measurement

V4 repairs V3's failed preflight loopback transport request. It remains offline and does not authorize model execution, process control, outreach, upload, or submission. V3 is preserved failed-capture evidence, and V2 is preserved historical evidence; neither is relabeled or retagged.

```text
python -m tools.validate_v5_contract --repository-root <repo>
python -m tools.materialize_v5_inputs --repository-root <repo> --accepted-audit <accepted-audit> --v4-source-manifest <v4-manifest> --v5-source-manifest <v5-manifest> --runtime-identity <captured-runtime> --output <external-materialization-root> --evidence-output-root <external-evidence-root>
python -m tools.run_v5_recovery --repository-root <repo> --full-plan <materialization-root>/full_plan.json --conditional-go <authorization> --output <external-evidence-root> --preflight-only
python -m tools.run_v5_recovery --repository-root <repo> --full-plan <materialization-root>/full_plan.json --conditional-go <pending-record> --output <external-evidence-root> --pending-only
```

Materialization publishes exactly seven create-only members: `carry_forward.json`, `compatibility_plan.json`, `full_plan.json`, `materialization_receipt.json`, `runtime_identity.json`, `schedule.json`, and `source_manifest.json`. Receipt schema `anachron-v5-materialization-receipt-v3` binds actual raw runtime and schedule hashes. The runner checks those bytes and canonical runtime/schedule semantics before transport or output.

GO and PENDING are disjoint. `--preflight-only` accepts only GO. `--pending-only` accepts only a fully bound PENDING record and prints `PENDING_VALID`; it cannot run measurement. Campaign Execute mode remains behind future explicit GO and is not qualified by this repair.
