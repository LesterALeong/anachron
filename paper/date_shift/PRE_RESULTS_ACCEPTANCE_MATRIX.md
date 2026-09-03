# Date-shift pre-results paper acceptance matrix

This matrix governs the pre-results preview only. It does not replace the frozen study acceptance matrix.

| Gate | Pass condition |
| --- | --- |
| Canonical source | `manuscript.json` is canonical JSON and is the sole prose source. |
| Bound inputs | The builder recomputes cohort counts, maximum planned calls, model names, digests, and decoding settings from the tracked date-shift inputs. |
| No-results boundary | The prose makes no empirical outcome claim, includes a real no-results status section, and does not include model responses or source excerpts. |
| Citation boundary | Every related-work claim carries an inline canonical citation marker; every marker resolves to `references.bib`, and no unused bibliography reference is rendered. |
| Source-excerpt boundary | The manuscript rejects a normalized contiguous overlap of 80 characters or more with protected proposed-item source text, while permitting ordinary short phrases. |
| Scope | The paper distinguishes metadata presentation, ExAnte sampling, temporal knowledge, temporal conflict, and search-engine date filtering without claiming reproduction or a live-system result. |
| Submission boundary | Every ReportLab and generated-TeX PDF page carries the exact banner `PRE-RESULTS MANUSCRIPT - NOT FOR SUBMISSION` in page furniture; the generated tree is a preview, not an archive or upload request. |
| PDF verification | The builder verifies text extraction and page count with pdfplumber, renders first, middle, and last pages with PyMuPDF, and rejects a document outside four to six pages. |
| Optional TeX verification | An explicitly supplied Tectonic executable is SHA-256-pinned before subprocess launch to the documented 0.17.0 Windows identity; the receipt records the verified executable hash and raster-verifies every compiled page. |
| Preview source | The generated TeX tree contains only a main TeX file, the tracked bibliography, and a short build note; it contains no raw source excerpts, audit decisions, outputs, or archive. |
