# Local build provenance

The pre-results manuscript was built on this Windows host with:

- executable: `C:\Users\leste\.codex\tools\tectonic-0.17.0\bin\tectonic.exe`
- version: `Tectonic 0.17.0`
- distribution archive: `tectonic-0.17.0-x86_64-pc-windows-msvc.zip`
- distribution archive SHA-256: `f61ce51f0b0ade1015b7de7ef368541c5424e9756ecbd0d7af97d6d48030845f`

`tools/build_routes_paper.py` records both the executable and distribution-archive digests in the ignored build receipt when the sibling ZIP is available. Generated PDFs and source archives are reproducible build outputs and are not tracked.
