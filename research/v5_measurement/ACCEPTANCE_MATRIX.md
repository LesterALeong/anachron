# V5 acceptance matrix

| Control | Required evidence |
| --- | --- |
| Carry-forward | All eight registry/card bytes, hashes, and accepted-v4 tag blob IDs match. |
| Freshness | New v5 tag, plans, root, ordinals, and the two frozen seed derivations. |
| Date isolation | Retrieval uses the card cutoff only; a mismatched supplied date is `date_mismatch` and has no retrieval. |
| Continuation | Every model non-adherence category seals one trajectory and does not retry or replace it. |
| Custody | Ordinal files only; real Windows ADS, reparse, manifest, copy, and archive checks pass. |
| Failure closure | A terminal receipt preserves phase, step, ordinal/raw prefix, fsynced exact inventory, and inventory digest; replay rejects extra or drifted files. |
| Compatibility | Two opaque, excluded traces gate only transport, identity, bounded capture, manifest, and replay. |
| V4 exclusion | V4 empirical rows and statistics are absent; `v4_included_count = 0`. |
| Authority | A later exact hash-bound GO is required before any identity preflight or campaign; it binds the tagged wrapper, runner, analyzer, M/C/F artifacts, plans, and one external root without placing actual artifact hashes in the tag. |
| Release rehearsal | Before tagging, a clean detached local-Git rehearsal must build and validate a create-only source manifest from the exact production governed-path tuple; after tagging, the real remote tag must repeat that build and validation before runtime identity capture. |
