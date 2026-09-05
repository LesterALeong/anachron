# Anachron v4 candidate-paper contract

This directory is a static, pre-results paper contract. It contains no model
output, candidate PDF, local release, researcher contact, endorsement code,
upload, or submission capability.

After a separately authorized and complete replay-valid v4 campaign, the only
planned local lifecycle is:

```powershell
python -m tools.build_v4_measurement_candidate_paper --protocol-root <clean-detached-v4-tag> --source-manifest <external-M.json> --projection <verified-answer-free-candidate-projection.json> --output <absent-candidate-directory> --tectonic <pinned-tectonic-0.17-executable>
```

```powershell
python -m tools.verify_v4_measurement_candidate_reviews --repository-root <clean-detached-v4-tag> --candidate <candidate-directory> --reports <review-directory> --output <absent-review-set-manifest.json>
```

```powershell
python -m tools.release_v4_measurement_candidate --repository-root <clean-detached-v4-tag> --candidate <candidate-directory> --reports <review-directory> --review-manifest <review-set-manifest.json> --approval <approval.json> --output <absent-local-release-directory>
```

```powershell
python -m tools.render_v4_measurement_unsent_outreach --repository-root <clean-detached-v4-tag> --local-release <local-release-directory> --output <absent-unsent-outreach-directory>
```

The builder revalidates the detached protocol root, external source manifest,
and verified answer-free projection before creating local candidate artifacts.
Its receipt binds the exact generated metadata and a canonical page-by-page
PNG render inventory, so later metadata or visual-QA changes fail closure.
It never receives raw evidence, terminal answers, source-audit or authorization
prose, reviews, or approvals. The review verifier accepts only exactly ten
human-authored, substantive `APPROVED` reports with unchanged candidate,
source archive, metadata, and PDF bindings; every report must use the pooled
direction derived from sealed projection cells. It snapshots and re-admits the
reports immediately before create-only publication, and can only scaffold a
manifest, never manufacture approved reviews. The release command rechecks that exact
review set and Lester's post-review attestation before making a byte-for-byte
local copy. The final renderer accepts only that local release and creates an
explicitly `UNSENT` draft with no recipient or dispatch capability. None of
these commands may call Ollama, contact a researcher, request or transmit an
endorsement code, upload to arXiv, or submit a paper.

The source archive will contain only `README.md`, `figures/primary_tclr.tex`,
`main.tex`, and `references.bib`; it excludes raw traces, terminal answers,
authorization records, source-audit prose, reviews, and approvals.

The `v4-paper` CI job runs the documented module gates on Python 3.10, 3.11,
and 3.12 against disposable detached annotated-tag fixtures with fake local
origins. Its only network operation is downloading the candidate contract's
pinned Linux Tectonic 0.17.0 archive; it does not start a model or contact any
external researcher or submission service.

The builder imports PyMuPDF and Pillow only while rendering PDF QA. Without
the optional `[paper]` extras, pure v4 discovery remains importable and marks
only PDF-dependent candidate, review, and release tests as skipped.

Self-custody evidence supports internal consistency and byte replay; it detects
missing, partial, malformed, or inconsistent artifacts, including re-signed
artifacts without corresponding authority. It provides no independent
raw-response provenance and cannot detect a coherent rewrite of every locally
held artifact.
