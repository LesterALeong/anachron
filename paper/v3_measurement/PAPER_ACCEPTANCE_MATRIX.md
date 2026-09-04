# Anachron v3 preliminary-paper acceptance matrix

This matrix governs only the pre-full-results preview. It does not authorize a
full experiment, results manuscript, archive upload, endorsement request, or
submission. Its SHA-256 is frozen before the preview source is created.

| Gate | Pass condition |
| --- | --- |
| Canonical scope | The builder accepts exactly one state, `preliminary`, and rejects evidence, receipt, GO, review, final, or submission arguments. |
| Canonical prose | `manuscript.json` is canonical JSON and the sole prose source for the generated preview. |
| Frozen provenance | The generated receipt binds the protocol tag, tag object, commit, full-plan hash, v3 frozen-matrix hash, and this matrix hash. |
| No-results boundary | The abstract contains the exact sentence `FULL STUDY NOT YET AUTHORIZED OR RUN.` The manuscript contains no observed values, model answers, falsifier outcomes, empirical prose, or authorization claim. |
| Planned design only | The only study counts are the planned 336 trajectories, 264 primary trajectories, and 72 disclosed development trajectories. |
| Literature | Every related-work marker resolves to a primary-source bibliography entry. TCLR is explicitly defined as an operational metric, not claimed as a standard metric. |
| Required layout | The body occupies exactly six pages; an appendix begins on page 7; every page contains the exact banner `PRE-FULL-RESULTS MANUSCRIPT - NO EMPIRICAL CLAIMS - NOT FOR SUBMISSION`. |
| Required design artifacts | The manuscript includes planned-design Table 1, Figure 1 (two-request topology), and Figure 2 (authority and evidence flow). |
| TeX preview tree | The generated source tree is preview-only and contains exactly `main.tex`, `references.bib`, and `README.txt`; it contains no data, evidence, receipts, GO, review, or archive. |
| Build isolation | Each build uses a fresh temporary root. The builder verifies the supplied Tectonic executable is v0.17.0 SHA-256 `99ffcfdbf1ebf8bdda9e791942e3d06aedb12463fddc33f07de6f5211c8bf08d` before execution. |
| PDF verification | The builder extracts text and renders every page. The banner must be text-extractable and raster-visible on every page. |
| Determinism | Two fresh builds must produce byte-identical PDF, TeX, and source-tree archive bytes. |
| Archive boundary | The preview archive allowlist contains only the three TeX-tree files and is labelled not for submission. |
| Anti-fabrication | Tests reject empirical claims, observed values, model answers, falsifier language, missing exact status sentence, unsupported citations, and forbidden final-state inputs. |
