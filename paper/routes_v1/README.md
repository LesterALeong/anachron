# BLOCKED -- Anachron Routes v1 historical manuscript scaffold

Do not build, send, upload, or use this scaffold for outreach. Routes v1 is
unexecuted and its source-content intervention is confounded for the visible-date
research question. It is retained as historical provenance only.

`routes_v1.tex` is a pre-results manuscript. It must not be updated with an empirical claim until the frozen source-curation, run, blinded-rating, and analysis gates have completed.

## Build

From the repository root on this Windows host:

```powershell
python tools/build_routes_paper.py --tectonic C:\Users\leste\.codex\tools\tectonic-0.17.0\bin\tectonic.exe
```

The builder writes a PDF, compilation receipt, and a flat source-only `.tar.gz` archive under `paper/routes_v1/dist/`. It rejects non-ASCII or nested archive names and excludes code, raw data, outputs, and TeX build products.

## Boundaries

- This directory has no arXiv submission automation.
- `OUTREACH_TEMPLATE.md` is unsent draft text only.
- `SUBMISSION_METADATA.md` is a checklist, not a representation of current arXiv eligibility.
- The archive is a manuscript source bundle, not the project’s replication package.
