"""Focused review-set and local-release boundary tests using synthetic paths."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / filename)
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


reviews_tool = _module("v3_candidate_reviews", "verify_v3_measurement_candidate_reviews.py")
release_tool = _module("v3_candidate_release", "release_v3_measurement_candidate.py")


def _receipt() -> dict:
    return {
        "abstract_sha256": "a" * 64,
        "ai_assistance_disclosure_sha256": "b" * 64,
        "archive_sha256": "c" * 64,
        "candidate_pdf_sha256": "d" * 64,
        "evidence_manifest_sha256": "e" * 64,
        "paper_source_manifest_sha256": "f" * 64,
        "projection_sha256": "1" * 64,
    }


def _hashes() -> dict:
    return {
        "candidate_receipt.json": "2" * 64,
        "projection.json": "1" * 64,
        "paper_source_manifest.json": "f" * 64,
        "candidate.pdf": "d" * 64,
        "source.zip": "c" * 64,
        "arxiv_metadata.json": "3" * 64,
    }


class TestV3CandidateReviewRelease(unittest.TestCase):
    def _review(self, lens: str) -> dict:
        return {
            "archive_sha256": "c" * 64,
            "candidate_receipt_sha256": "2" * 64,
            "evidence_manifest_sha256": "e" * 64,
            "findings": [],
            "lens_id": lens,
            "paper_source_manifest_sha256": "f" * 64,
            "paper_pdf_sha256": "d" * 64,
            "projection_sha256": "1" * 64,
            "resolutions": [],
            "reviewed_at_utc": "2026-09-04T00:00:00Z",
            "reviewer": f"internal-{lens}",
            "schema_version": "anachron-v3-candidate-review-v1",
            "status": "APPROVED",
        }

    def test_review_set_requires_exact_frozen_reports(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(reviews_tool, "revalidate_candidate", return_value=(_receipt(), _hashes())):
            root = Path(temporary)
            reports = root / "reviews"
            reports.mkdir()
            for lens in reviews_tool.REVIEW_LENS_IDS:
                (reports / f"{lens}.json").write_bytes(reviews_tool.canonical_json(self._review(lens)))
            candidate = root / "candidate"
            candidate.mkdir()
            manifest = reviews_tool.create_review_set_manifest(candidate, reports, root / "manifest.json")
            self.assertEqual(len(manifest["reports"]), 10)
            self.assertEqual([row["filename"] for row in manifest["reports"]], [f"{lens}.json" for lens in reviews_tool.REVIEW_LENS_IDS])
            (reports / "unknown.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(reviews_tool.CandidateReviewError, "exactly"):
                reviews_tool.verify_reviews(Path(temporary) / "candidate", reports)

    def test_review_rejects_open_findings_and_malformed_time(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(reviews_tool, "revalidate_candidate", return_value=(_receipt(), _hashes())):
            reports = Path(temporary) / "reviews"
            reports.mkdir()
            for lens in reviews_tool.REVIEW_LENS_IDS:
                review = self._review(lens)
                if lens == reviews_tool.REVIEW_LENS_IDS[0]:
                    review["findings"] = ["open"]
                (reports / f"{lens}.json").write_bytes(reviews_tool.canonical_json(review))
            with self.assertRaisesRegex(reviews_tool.CandidateReviewError, "approval"):
                reviews_tool.verify_reviews(Path(temporary), reports)
            review = self._review(reviews_tool.REVIEW_LENS_IDS[0])
            review["reviewed_at_utc"] = "2026-09-04"
            (reports / f"{reviews_tool.REVIEW_LENS_IDS[0]}.json").write_bytes(reviews_tool.canonical_json(review))
            with self.assertRaisesRegex(reviews_tool.CandidateReviewError, "UTC"):
                reviews_tool.verify_reviews(Path(temporary), reports)

    def test_review_rejects_a_stale_candidate_hash_binding(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(reviews_tool, "revalidate_candidate", return_value=(_receipt(), _hashes())):
            reports = Path(temporary) / "reviews"
            reports.mkdir()
            for lens in reviews_tool.REVIEW_LENS_IDS:
                review = self._review(lens)
                if lens == reviews_tool.REVIEW_LENS_IDS[0]:
                    review["candidate_receipt_sha256"] = "stale" * 16
                (reports / f"{lens}.json").write_bytes(reviews_tool.canonical_json(review))
            with self.assertRaisesRegex(reviews_tool.CandidateReviewError, "binding"):
                reviews_tool.verify_reviews(Path(temporary), reports)

    def test_local_release_copies_exact_bytes_and_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            candidate.mkdir()
            (candidate / "candidate.pdf").write_bytes(b"pdf")
            (candidate / "arxiv_metadata.json").write_bytes(release_tool.canonical_json({"metadata": "synthetic"}))
            with zipfile.ZipFile(candidate / "source.zip", "x") as archive:
                for name in reviews_tool.ARCHIVE_FILES:
                    archive.writestr(name, b"synthetic")
            hashes = {
                "candidate_receipt.json": "2" * 64,
                "projection.json": "1" * 64,
                "paper_source_manifest.json": "f" * 64,
                "candidate.pdf": release_tool.sha256_path(candidate / "candidate.pdf"),
                "source.zip": release_tool.sha256_path(candidate / "source.zip"),
                "arxiv_metadata.json": release_tool.sha256_path(candidate / "arxiv_metadata.json"),
            }
            receipt = _receipt()
            receipt.update({"archive_sha256": hashes["source.zip"], "candidate_pdf_sha256": hashes["candidate.pdf"]})
            reports = [{"filename": f"{lens}.json", "reviewed_at_utc": "2026-09-04T00:00:00Z", "reviewer": lens, "sha256": str(index) * 64} for index, lens in enumerate(reviews_tool.REVIEW_LENS_IDS, start=1)]
            with patch.object(release_tool, "revalidate_candidate", return_value=(receipt, hashes)), patch.object(release_tool, "verify_reviews", return_value=reports):
                manifest = {"archive_sha256": receipt["archive_sha256"], "candidate_receipt_sha256": hashes["candidate_receipt.json"], "evidence_manifest_sha256": receipt["evidence_manifest_sha256"], "lens_ids": list(reviews_tool.REVIEW_LENS_IDS), "paper_pdf_sha256": receipt["candidate_pdf_sha256"], "paper_source_manifest_sha256": receipt["paper_source_manifest_sha256"], "projection_sha256": receipt["projection_sha256"], "reports": reports, "schema_version": "anachron-v3-candidate-review-set-v1"}
                manifest_path = root / "manifest.json"
                manifest_path.write_bytes(release_tool.canonical_json(manifest))
                approval = {"abstract_sha256": receipt["abstract_sha256"], "ai_assistance_disclosure_sha256": receipt["ai_assistance_disclosure_sha256"], "approval": "APPROVED", "approved_at_utc": "2026-09-04T00:00:01Z", "approved_by": "Lester Leong", "archive_sha256": receipt["archive_sha256"], "arxiv_metadata_sha256": hashes["arxiv_metadata.json"], "attestation": release_tool.ATTESTATION, "candidate_receipt_sha256": hashes["candidate_receipt.json"], "paper_pdf_sha256": receipt["candidate_pdf_sha256"], "review_set_manifest_sha256": release_tool.sha256_path(manifest_path), "schema_version": "anachron-v3-candidate-author-approval-v1", "status": "APPROVED"}
                approval_path = root / "approval.json"
                approval_path.write_bytes(release_tool.canonical_json(approval))
                stale_approval = approval | {"candidate_receipt_sha256": "stale" * 16}
                stale_approval_path = root / "stale-approval.json"
                stale_approval_path.write_bytes(release_tool.canonical_json(stale_approval))
                with self.assertRaisesRegex(release_tool.LocalReleaseError, "binding"):
                    release_tool.release_candidate(candidate, root / "reviews", manifest_path, stale_approval_path, root / "stale-release")
                output = root / "release"
                release_tool.release_candidate(candidate, root / "reviews", manifest_path, approval_path, output)
                self.assertEqual((output / "candidate.pdf").read_bytes(), b"pdf")
                release_tool.revalidate_local_release(output, hashes["candidate_receipt.json"], release_tool.sha256_path(manifest_path), release_tool.sha256_path(approval_path))
                (output / "rogue.txt").write_text("rogue", encoding="utf-8")
                with self.assertRaisesRegex(release_tool.LocalReleaseError, "completion set"):
                    release_tool.revalidate_local_release(output, hashes["candidate_receipt.json"], release_tool.sha256_path(manifest_path), release_tool.sha256_path(approval_path))
                with self.assertRaisesRegex(FileExistsError, "already exist"):
                    release_tool.release_candidate(candidate, root / "reviews", manifest_path, approval_path, output)


if __name__ == "__main__":
    unittest.main()
