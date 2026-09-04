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
