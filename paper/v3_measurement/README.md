# Anachron v3 preliminary paper preview

This is a readable design preview, not a results paper and not an arXiv upload
bundle. It has one allowed build state: `preliminary`.

From the repository root, build only with the pinned Tectonic executable:

```powershell
python -m tools.build_v3_measurement_preliminary_paper --tectonic C:\Users\leste\.codex\tools\tectonic-0.17.0\bin\tectonic.exe
```

The builder emits one preview PDF, a preview-only TeX source tree, a deterministic
preview archive, and a receipt under `paper/v3_measurement/build/`. It rejects
evidence, receipts, GO, review, final-state, and submission arguments.

## Candidate review and local-release workflow

Build a local candidate only from a complete admitted evidence tree and the
clean detached frozen protocol worktree:

```powershell
python -m tools.build_v3_measurement_candidate_paper --protocol-root <clean-detached-v3-measurement-protocol-v1> --evidence <complete-evidence-dir> --output <absent-candidate-dir> --tectonic C:\Users\leste\.codex\tools\tectonic-0.17.0\bin\tectonic.exe
```

The candidate workflow is separate from this preliminary preview. It operates
only on an already-built local candidate, creates no model evidence, and has no
contact, endorsement, upload, or submission capability. It does not change the
preliminary build state above.

Create an exact review-set manifest only after the ten canonical internal
review reports exist in a separate directory:

```powershell
python -m tools.verify_v3_measurement_candidate_reviews --candidate <candidate-dir> --reviews <review-dir> --output <absent-review-set-manifest.json>
```

Create an exact byte-for-byte local release only after that manifest and a
distinct Lester Leong approval bind the unchanged candidate:

```powershell
python -m tools.release_v3_measurement_candidate --candidate <candidate-dir> --reviews <review-dir> --review-manifest <review-set-manifest.json> --approval <approval.json> --output <absent-local-release-dir>
```

Neither command rebuilds a paper, performs outreach, transmits an endorsement
code, uploads to arXiv, or submits anything externally.
