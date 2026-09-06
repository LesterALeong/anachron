# Anachron v5 successor protocol

## Implementation status

A2a implements the Python evidence lifecycle and semantic replay against
injected-only tests. A2b supplies the fixed isolated-server PowerShell wrapper
and its authority closure; no v5 model call is authorized by this protocol.

A2b authority hardening requires a clean annotated
`v5-measurement-protocol-v2` release: the tag object, peeled commit, branch,
remote references, governed blob IDs, and worktree bytes must agree. The
materialized v2 plan accepts only Ollama 0.33.2 and the two frozen full model
digests. Its later GO is an exact schema binding the source manifest,
carry-forward receipt, acceptance matrix, authority contract, plans,
runner/analyzer hashes, and one external evidence root. The runner copies those
bound bytes before any chat.

The hash authority is deliberately acyclic. The annotated source tag contains
only templates with placeholders. After the tag is frozen, the external source
manifest, carry-forward receipt, runtime identity, materialization receipt, and
plans are created from that tag. The later actual GO binds those external bytes
and the tagged wrapper, runner, and analyzer bytes. The tag therefore never
claims an actual GO or a post-tag artifact hash.

The v5 custody boundary is implemented rather than deferred: all nine authority
members are captured before any staging or transport, replay has bounded
topology and byte caps, and a successful root and operational-failure root are
mutually exclusive atomic publications. Candidate-paper rendering is downstream
of the sealed answer-free projection and is not scientific measurement authority.

V5 is a fresh, prospective finite-panel measurement following v4's operational failure. V4 is an excluded operational pilot: `v4_included_count = 0` in v5 evidence, analysis, and paper claims. The unexecuted `v5-measurement-protocol-v1` release never produced a valid source manifest and is manifest-ineligible. V2 is a technical-only prospective repair, not an executed measurement or an authorization.

The eight primary cards and registry are byte-identical carry-forward copies of the accepted v4 tag. A mechanical receipt must verify tag blob IDs and SHA-256 bytes before the future run. The compatibility case is separate and excluded from metrics.

The panel has eight cards, two modes, two approved models, and two repetitions: 64 primary trajectories. Repetition one uses seed `1477205243`; repetition two uses `106139663`. Each is the positive 31-bit integer formed from the first four big-endian bytes of SHA-256 over the preserved v1 seed namespace `anachron-v5-measurement-protocol-v1/repetition-N`, fixed before v5 output exists.

Each trajectory receives one first chat. A valid single `anachron_search` call with a nonempty query is adherent. A model date is only checked against the card cutoff. A mismatched date is recorded as `date_mismatch`, triggers no retrieval, seals that trajectory, and the next scheduled trajectory proceeds without retry or replacement. For adherent calls only, the harness retrieves with the card cutoff and mode, then schedules a final chat. Final response validity is separate from first-call adherence and exposure.

Evidence filenames use schedule ordinals only. No model name, case ID, or external identifier appears in a physical filename. The custody gate rejects reserved components, traversal, symlinks, junctions, alternate NTFS data streams, manifest drift, and copy/archive drift.

Only identity, transport, response-limit, writer, manifest, replay, or physical-custody failures invalidate the campaign. Model non-adherence is an outcome. No model call, outreach, upload, or submission is authorized by this source protocol.
