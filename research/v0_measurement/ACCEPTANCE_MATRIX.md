# Pre-falsifier acceptance matrix

| Gate | Requirement | Failure action |
| --- | --- | --- |
| Static controls | Positive and negative control tests pass | Do not run Ollama |
| Model identity | `/api/tags` exactly matches each frozen name and digest | Stop before trajectories |
| Trace protocol | One claim and one terminal per trajectory; no retry | Mark the evidence invalid |
| Valid trajectories | Every planned trajectory is valid (24 for the falsifier, 324 for the full plan) | Stop |
| Primary effect | Pooled unrestricted mean TCLR minus enforced mean TCLR is at least 0.20 | Stop |
| Per-model direction | Neither model has a negative unrestricted-minus-enforced reduction | Stop |
| Residual failure | At least one valid enforced finance trace has a survivorship leak | Stop |
| Evidence integrity | Exact 2+5*N raw inventory and SHA-256 values, with no links, junctions, reparse points, or non-files, plus manifest, runtime, source-admission-v2 Git blobs/tag objects, journal, and deterministic analysis all verify | Stop |
| Full-plan authorization | A fully valid passing falsifier with byte-exact canonical `plan.json` has a canonical create-only receipt and a later strict human `GO` artifact binds that receipt to the exact full-plan bytes | Do not create output or contact Ollama |

Passing this matrix does not prove a general model property. It only supports a
claim about the exact synthetic panel, model builds, runner, and recorded run.
