# V4 recovery contract candidate

<!-- BEGIN V4 AUTHORITY BINDING -->
### Authority binding

This document is governed by the v4 authority-binding contract. Before any compatibility chat, the compatibility plan, full plan, and conditional GO must bind the exact frozen acceptance-matrix hash, authority-contract hash, external source-manifest hash, tag-blob comparison hash, completed eight-card audit hash, and pre-GO read-only runtime-identity hash. Audit A and identity I each bind M/X and the identical v4 annotated tag object and peeled commit; A also binds the registry and every case-card tagged blob OID and SHA-256. The offline validator checks only local bytes and has no network, model, runner, paper-builder, review, release, or outreach action. PDF QA dependencies are optional outside the isolated v4-paper CI job.
<!-- END V4 AUTHORITY BINDING -->

This directory is an offline, pre-freeze contract for a separate v4 study.
It contains no empirical outcome. The eight case cards are proposed synthetic
materials pending Lester's source audit.

The eventual campaign has one conditional authority boundary: before any
compatibility chat, the compatibility plan, full plan, and conditional GO must
all carry the exact frozen acceptance-matrix hash and the exact external source-manifest hash. Two excluded compatibility
traces then run first, and the 64-trajectory main panel runs only if both pass.
Do not start Ollama, call a model, contact a researcher, request or transmit
an endorsement code, upload, or submit from this contract candidate.

## Release history

The pre-audit `v4-measurement-protocol-v1` tag is ineligible study authority.
Its Python 3.10 paper lane exceeded the original 30-second Tectonic cold-cache
timeout while downloading compiler support files. No source audit, runtime
identity, conditional GO, model call, result, or external action used that tag.
`v4-measurement-protocol-v2` supersedes it with a bounded 120-second compiler
allowance; only the v2 annotated tag may anchor the lifecycle below.

## Lifecycle commands

Authority order is immutable: reviewed release and external source manifest
`M`, tag-blob comparison `X`, accepted audit `A`, captured pre-GO read-only identity `I`,
materialized compatibility plan `C`, full plan `F`, and conditional GO `G`.
The source-audit UI is the sole operator command that creates the external packet
directory containing `M.json`, `X.json`, the editable worksheet JSON/HTML, and a
receipt from the clean detached annotated-tag checkout. Materialization accepts
that packet's `M/X` only after independently re-deriving and byte-verifying both.
The runner and replay repeat the derivation before creating evidence or opening
transport. These are the seven operator commands; source-manifest construction
remains an internal library.

```powershell
python -m tools.validate_v4_contract --repository-root .
```

```powershell
python -m tools.build_v4_source_audit_ui --repository-root . --output <absent-external-packet-directory>
```

```powershell
python -m tools.finalize_v4_source_audit --repository-root . --input <reviewed-audit.json> --source-manifest <packet-directory/M.json> --comparison <packet-directory/X.json> --output <external-audit.json>
```

```powershell
python -m tools.capture_v4_runtime_identity --repository-root . --version-response <version.json> --tags-response <tags.json> --source-manifest <packet-directory/M.json> --comparison <packet-directory/X.json> --output <external-identity.json>
```

```powershell
python -m tools.materialize_v4_inputs --repository-root . --source-manifest <packet-directory/M.json> --comparison <packet-directory/X.json> --source-audit <A.json> --runtime-identity <I.json> --output <external-input-directory>
```

```powershell
python -m tools.run_v4_recovery --repository-root . --compatibility-plan <C.json> --full-plan <F.json> --conditional-go <G.json> --source-audit <A.json> --runtime-identity <I.json> --comparison <packet-directory/X.json> --source-manifest <packet-directory/M.json> --output <absent-evidence-root>
```

```powershell
python -m tools.analyze_v4_measurement <evidence-root> --repository-root . --phase <compatibility|full|failure>
```

Use `--preflight-only` on the runner command to validate M/X/A/I/C/F/G with
zero transport and no evidence-root creation.

The runner accepts C/F/G/A/I/M/X only as external regular files and creates an
absent external evidence root through the shared reparse-safe admission path;
repository-local, symlink, and junction components are rejected before output
or transport. Native HTTP responses are streamed under a 1,048,576-byte
per-response limit and an 8,388,608-byte campaign limit. An over-limit response
ends the campaign with a typed `resource` failure and at most a 4,096-byte raw
prefix in the replayable failure evidence.

Self-custody evidence supports internal consistency and byte replay; it detects
missing, partial, malformed, or inconsistent artifacts, including re-signed
artifacts without corresponding authority. It provides no independent
raw-response provenance and cannot detect a coherent rewrite of every locally
held artifact.
