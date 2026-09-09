# Anachron v5 successor protocol

V5 repairs V4's failed isolated-startup classification. It requires a clean
annotated `v5-measurement-protocol-v5` release: the tag object, peeled commit,
branch, remote references, governed blob IDs, and worktree bytes must agree. V4
remains immutable failed-capture evidence, and V3/V2 remain historical evidence;
none is an execution authorization.

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
It creates a private Windows Job Object with `KILL_ON_JOB_CLOSE`, starts the
isolated root with `DETACHED_PROCESS | CREATE_SUSPENDED`, assigns that root to
the Job before binding its identity or resuming its sole thread, and never grants
breakaway. Before either isolated API call, V5 permits only identity-bound,
no-model startup helpers, then requires two continuous seconds without
descendants and exactly one active Job member. Cleanup terminates the Job and
requires its active-member count to remain zero for two seconds; any API,
termination, query, timeout, or handle-close fault blocks restoration.

The caller allocates one monotonic isolated lifecycle before any native action.
Each helper records its attempted and succeeded facts and stores acquired handles,
processes, and drains before another fallible operation. The `finally` path stops
that lifecycle exactly once from those facts, preserves the primary failure, and
adds cleanup receipts without replacing it. A pure restoration predicate permits
normal restoration only for a resource-free, closed Job-only, baseline-clean
unassigned root, or fully terminated and continuously empty assigned Job row.

The controller harness has 25 literal ID-to-obligation rows. J01--J04 cover
typed Win32 declarations, detached suspended launch, non-breakaway containment,
and caller-owned ordering. J05--J10 cover Job creation/configuration, root
launch/post-launch custody, PID/open/assignment, and assignment-handle-close
faults. J11--J15 cover binding, drains, thread discovery/open, owner/resume
validation, and successful `ResumeThread` followed by failed `CloseHandle`.
J16--J18 execute the actual no-root/Job-only, unassigned suspended-root, and
assigned-Job teardown dispatches. J19--J21 inject terminate/root-wait,
accounting/zero-window, and pipe/drain/Job-close cleanup faults; J22 proves the
monotonic-fact predicate; J23 replays the exact observed production helper tree.
J24 persists every pre-return failure status under edge-named subtests, and J25
is the successful assigned-Job capture/restoration row. The test derives IDs and
count from this literal ordered tuple and requires every row to execute.

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
