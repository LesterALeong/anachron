"""Static review and local-release capability tests."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from anachron.v5_candidate_release_common import (
    V5_CANDIDATE_REVIEW_LENS_IDS,
    CandidateReleaseError,
    _completion,
    _source_archive,
    create_staging_directory,
    remove_staging,
)


class V5CandidateReviewReleaseStaticTests(unittest.TestCase):
    def test_ten_frozen_lenses_and_no_external_transport(self) -> None:
        self.assertEqual(len(V5_CANDIDATE_REVIEW_LENS_IDS), 10)
        root = Path(__file__).resolve().parents[1]
        for relative in ("anachron/v5_candidate_release_common.py", "tools/verify_v5_measurement_candidate_reviews.py", "tools/release_v5_measurement_candidate.py"):
            source = (root / relative).read_text(encoding="utf-8").lower()
            for forbidden in ("import requests", "import socket", "import smtplib", "import urllib", "import webbrowser", "ollama"):
                self.assertNotIn(forbidden, source, relative)

    def test_author_approval_is_a_distinct_gate(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "tools/release_v5_measurement_candidate.py").read_text(encoding="utf-8")
        self.assertIn("author approval", source)
        self.assertIn("APPROVED", source)

    def test_completion_is_order_independent_and_rejects_topology_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("alpha.json", "beta.json"):
                (root / name).write_text("{}\n", encoding="utf-8")
            _completion(root, ("beta.json", "alpha.json"), "fixture")
            for expected, mutation in (
                (("alpha.json",), None),
                (("alpha.json", "beta.json"), "extra.json"),
                (("alpha.json", "alpha.json"), None),
            ):
                with self.subTest(expected=expected, mutation=mutation):
                    if mutation is not None:
                        (root / mutation).write_text("{}\n", encoding="utf-8")
                    with self.assertRaisesRegex(CandidateReleaseError, "completion set differs"):
                        _completion(root, expected, "fixture")
                    if mutation is not None:
                        (root / mutation).unlink()

    def test_staging_rejects_portability_and_input_overlap_and_cleans_up(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            outer = Path(temporary)
            repository = outer / "repository"
            repository.mkdir()
            inputs = outer / "inputs"
            inputs.mkdir()
            report = inputs / "report.json"
            report.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(CandidateReleaseError, "overlaps"):
                create_staging_directory(inputs / "release", repository, inputs)
            with self.assertRaisesRegex(CandidateReleaseError, "portable"):
                create_staging_directory(outer / "release:ads", repository, report)
            target, staging = create_staging_directory(outer / "release", repository, report)
            self.assertFalse(target.exists())
            self.assertTrue(staging.is_dir())
            remove_staging(staging)
            self.assertFalse(staging.exists())

    def test_source_archive_manifest_requires_exact_ordered_four_row_schema(self) -> None:
        allowlist = ["README.md", "figures/primary_adherence.tex", "main.tex", "references.bib"]
        policy = {"source_archive_max_bytes": 1_048_576, "source_file_max_bytes": 262_144}
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "source.zip"
            with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
                for name in allowlist:
                    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                    info.compress_type, info.create_system, info.external_attr = zipfile.ZIP_DEFLATED, 3, 0o100644 << 16
                    bundle.writestr(info, b"fixture")
            rows = [{"path": name, "sha256": hashlib.sha256(b"fixture").hexdigest()} for name in allowlist]
            valid = {"files": rows, "schema_version": "anachron-v5-paper-source-manifest-v1", "v4_included_count": 0}
            _source_archive(archive, valid, allowlist, policy)
            for malformed in (
                {**valid, "files": rows[:-1]},
                {**valid, "files": [rows[1], rows[0], *rows[2:]]},
                {**valid, "files": [{**rows[0], "sha256": "A" * 64}, *rows[1:]]},
                {**valid, "files": [rows[0], rows[0], *rows[2:]]},
            ):
                with self.subTest(malformed=malformed["files"]), self.assertRaisesRegex(CandidateReleaseError, "manifest"):
                    _source_archive(archive, malformed, allowlist, policy)
            with zipfile.ZipFile(archive, "a", compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.writestr("extra.tex", b"fixture")
            with self.assertRaisesRegex(CandidateReleaseError, "topology"):
                _source_archive(archive, valid, allowlist, policy)


if __name__ == "__main__":
    unittest.main()
