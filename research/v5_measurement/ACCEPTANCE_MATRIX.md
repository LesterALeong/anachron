# V5 successor v3 acceptance matrix

V3 is an offline repair candidate. A future campaign requires a separate,
fresh, explicit GO and is not operationally qualified by this matrix.

| Control | Required evidence |
| --- | --- |
| Release identity | Clean annotated `v5-measurement-protocol-v3` tag, peeled commit, branch, remote references, governed blob IDs, and worktree bytes agree. |
| Governed closure | The sorted 47-member scientific closure includes the controller, Authenticode helper, CIM process-identity helper, controller test, and Python package bootstrap; the authority contract matches it byte-for-byte. |
| Carry-forward | All eight registry/card bytes, hashes, and accepted-v4 tag blob IDs match. |
| Materialization | Exactly seven create-only members pass bounded topology, ADS, reparse, member, total-byte, and atomic-publication checks. |
| Runtime and schedule binding | Receipt v3 binds the actual raw runtime and schedule SHA-256 values; admission checks the raw bytes and canonical runtime and schedule semantics. |
| GO and PENDING | GO validation remains GO-only. PENDING has empty authorization metadata and the fixed non-authorizing statement; `--pending-only` validates it without measurement. |
| Identity controller | The controller validates its tagged authority, binds retained task-owned process identity, bounds helper output and time, and restores normal topology atomically. |
| Measurement science | Date isolation, continuation, custody, replay, compatibility exclusion, and the frozen seed namespace remain governed; `v4_included_count = 0`. |
| Release rehearsal | Before tagging, a clean detached local-Git rehearsal builds and validates a create-only source manifest from the actual governed tuple. After tagging, the remote tag repeats that validation before runtime identity capture. |
