# Date-shift pre-results manuscript

`manuscript.json` is the canonical prose source for the pre-results preview. It is not evidence of a completed experiment and must not be used for a submission, outreach, or archive.

From the repository root:

```powershell
python -m tools.build_date_shift_pre_results_paper --verify
```

If a local Tectonic executable is already available, an explicit path also verifies the generated
TeX in an isolated temporary directory and raster-checks its page furniture:

```powershell
python -m tools.build_date_shift_pre_results_paper --verify --tectonic C:\path\to\tectonic.exe
```

The TeX check is identity-pinned before it starts a subprocess. It accepts only Tectonic 0.17.0
with the official Windows asset ZIP SHA-256
`f61ce51f0b0ade1015b7de7ef368541c5424e9756ecbd0d7af97d6d48030845f` and unpacked
`tectonic.exe` SHA-256 `99ffcfdbf1ebf8bdda9e791942e3d06aedb12463fddc33f07de6f5211c8bf08d`.
The receipt records the verified executable identity used for an accepted local TeX compilation.

The command writes ignored outputs under `paper/date_shift/build/`:

- `date_shift_pre_results.pdf`, a readable four-to-six-page preview with a non-submission banner on every page;
- `arxiv_source/`, a conservative TeX source tree for later manual review;
- `build_receipt.json`, input and output hashes, counts, and verification evidence;
- `render/`, PNG renders of the first, middle, and last PDF pages.

The builder rejects noncanonical manuscript JSON, unsupported citations, empirical outcome language,
and requests to archive, upload, or submit the preview. It also rejects any normalized contiguous
80-character overlap with a protected proposed-item excerpt: this is long enough to catch copied
source prose while not treating ordinary short phrases as an excerpt.
