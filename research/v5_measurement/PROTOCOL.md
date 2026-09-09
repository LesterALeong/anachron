# Anachron v5 successor protocol

V4 repairs V3's failed preflight transport request. It requires a clean annotated
`v5-measurement-protocol-v4` release: the tag object, peeled commit, branch,
remote references, governed blob IDs, and worktree bytes must agree. V3 remains
immutable failed-capture evidence, and V2 remains historical technical evidence;
neither is an execution authorization.

The tagged closure includes the isolated identity controller, its Authenticode
helper, and a fixed local CIM process-identity helper. When psutil cannot read
a process name, the controller brackets the helper with fresh psutil PID,
parent, and exact binary64 creation-time observations; CIM supplies only the
nonempty name. The CIM helper never supplies command lines, executable identity,
or birth time, and
all malformed, incomplete, or mismatched observations fail closed. The
controller binds a task-owned process by PID, creation time, executable, and
parent; it uses bounded output capture, atomic restoration publication, and
recursive bounded inventory. These repairs do not
authorize a capture, model call, process control, outreach, upload, or
submission.

The controller emits one explicit `Host` header for its fixed loopback requests.
The frozen V3 controller emitted two `Host` headers and failed before process
mutation; the V4 regression verifies both the immutable V3 failure and V4's
single-header request against disposable loopback sockets.

The source tag contains templates with placeholders. After it is frozen, the
external source manifest, carry-forward receipt, captured runtime identity,
materialization receipt, and plans are created from that tag. Materialization
publishes exactly seven create-only members: `carry_forward.json`,
`compatibility_plan.json`, `full_plan.json`, `materialization_receipt.json`,
`runtime_identity.json`, `schedule.json`, and `source_manifest.json`. Receipt
schema `anachron-v5-materialization-receipt-v3` binds the actual raw runtime and
schedule bytes; later validation also checks their canonical semantics.

GO and PENDING are disjoint. A later GO binds the source manifest,
carry-forward receipt, acceptance matrix, authority contract, plans,
runner/analyzer hashes, and one external evidence root before any future
campaign. A fully bound PENDING record can be checked offline with
`--pending-only`; it prints `PENDING_VALID` and cannot run measurement. Execute
mode remains future work behind fresh explicit GO.

V5 is a prospective finite-panel measurement following v4's operational
failure. V4 is an excluded operational pilot: `v4_included_count = 0` in v5
evidence, analysis, and paper claims. The eight primary cards and registry are
byte-identical carry-forward copies of the accepted v4 tag. The compatibility
case is separate and excluded from metrics.

The panel has eight cards, two modes, two approved models, and two repetitions:
64 primary trajectories. Repetition one uses seed `1477205243`; repetition two
uses `106139663`. Each is the positive 31-bit integer formed from the first four
big-endian bytes of SHA-256 over the preserved v1 seed namespace
`anachron-v5-measurement-protocol-v1/repetition-N`, fixed before v5 output
exists.

Each trajectory receives one first chat. A valid single `anachron_search` call
with a nonempty query is adherent. A model date is checked against the card
cutoff. A mismatched date is recorded as `date_mismatch`, triggers no retrieval,
seals that trajectory, and the next scheduled trajectory proceeds without retry
or replacement. For adherent calls only, the harness retrieves with the card
cutoff and mode, then schedules a final chat. Final response validity is
separate from first-call adherence and exposure.

Evidence filenames use schedule ordinals only. No model name, case ID, or
external identifier appears in a physical filename. The custody gate rejects
reserved components, traversal, symlinks, junctions, alternate NTFS data
streams, manifest drift, and copy/archive drift. Only identity, transport,
response-limit, writer, manifest, replay, or physical-custody failures
invalidate a campaign; model non-adherence is an outcome.
