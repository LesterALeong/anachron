# Anachron v5 measurement

This directory is an offline successor protocol, not an authorization to invoke a model. Run the static checks with:

```text
python -m tools.validate_v5_contract --repository-root <repo>
python -m tools.materialize_v5_inputs --repository-root <repo> --carry-forward <receipt>
python -m tools.analyze_v5_measurement <evidence>
```

`v5-measurement-protocol-v1` was unexecuted and never produced a valid source manifest, so it is manifest-ineligible. `v5-measurement-protocol-v2` is a technical-only prospective repair; it has not run an identity preflight or a model and does not authorize either action.
The v2 release gate rehearses the unchanged source-manifest builder against the exact production governed closure in a clean detached local-Git checkout, then requires the real tagged checkout to repeat build and validation before any runtime operation.

## Implementation boundary

A single bounded custody transaction now captures the nine authority inputs
before staging or transport, writes only to a private sibling staging root,
and atomically publishes either the complete success topology or the mutually
exclusive operational-failure topology. Replay and projection stream bounded
regular-file captures through the same topology policy. The scientific
measurement manifest is GO-bound; paper rendering, PDF review, release, and
UNSENT outreach are downstream presentation artifacts and cannot alter it.

A2a supplies the GO-gated Python lifecycle and replay surface. It preserves raw
wire bytes under ordinal-only filenames, records two excluded opaque compatibility
traces, continues the exact 64-row primary schedule after model non-adherence,
and seals a typed terminal receipt for operational failures. Its tests use only
injected local transports; this source has not run a model.

A2b supplies a fixed PowerShell isolated-server wrapper. It is not an operator
authorization: a later GO must bind the tagged wrapper, runner, and analyzer
bytes plus the post-tag manifest, carry-forward receipt, materialization
receipt, plans, and external output roots. The tag holds templates only, so the
authority sequence is acyclic: tag, then M/C/F, then the actual GO.

The wrapper preserves native child exit codes and uses the runner's fixed wire
response limits. Its redirected operational logs receive a post-child 1 MiB
disk guard rather than an active stream cap: an active PowerShell cap would
require altered child argv/exit handling or a kill-polling loop. A zero runner
exit is insufficient; the fixed primary evidence analyzer must also exit zero
before a campaign is recorded as successful.

## Candidate-paper boundary

After a future complete replay-valid v5 root, the governed local-only paper
pipeline may create an answer-free projection, candidate PDF, source archive,
metadata, ten byte-bound review reports, a Lester-bound local release, and an
explicitly `UNSENT` outreach draft. It cannot run a model, contact a
researcher, transmit an endorsement code, upload, or submit. The projection
reports every first/final non-adherence category, first-tool adherence, and
conditional exposure only among adherent calls. It excludes both compatibility
traces and every v4 empirical row (`v4_included_count = 0`).
