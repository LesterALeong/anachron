"""Focused v4 review-set and local-release closure tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from anachron import v4_candidate_release_common as release_common
from anachron.v4_candidate_release_common import (
    CandidateReleaseError,
    candidate_closure,
)
from anachron.v4_contract import (
    V4_CANDIDATE_RESOURCE_POLICY,
    V4_CANDIDATE_REVIEW_LENS_IDS,
)
from tests import test_v4_candidate_paper as candidate_paper_tests
from tools import build_v4_measurement_candidate_paper as builder
from tools import build_v4_source_manifest
from tools import release_v4_measurement_candidate as release_tool
from tools import render_v4_measurement_unsent_outreach as outreach_tool
from tools import verify_v4_measurement_candidate_reviews as review_tool


def _paper_dependencies_available() -> bool:
    try:
        return all(importlib.util.find_spec(name) is not None for name in ("fitz", "PIL"))
    except (ImportError, AttributeError, ValueError):
        return False


PAPER_DEPENDENCIES_AVAILABLE = _paper_dependencies_available()
PAPER_DEPENDENCIES_REASON = "v4 review and release tests require the [paper] extras"


@unittest.skipUnless(PAPER_DEPENDENCIES_AVAILABLE, PAPER_DEPENDENCIES_REASON)
class V4CandidateReviewReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if (
            candidate_paper_tests.TECTONIC is None
            or not candidate_paper_tests.TECTONIC.is_file()
        ):
            raise unittest.SkipTest("pinned Tectonic is unavailable")
        helper = candidate_paper_tests.V4CandidatePaperTests()
        cls.temporary, cls.root, cls.origin, cls.expected_v3 = helper._repository()
        cls.manifest = cls.root.parent / "M.json"
        source = build_v4_source_manifest.build(
            cls.root,
            cls.manifest,
            expected_origin=str(cls.origin),
            expected_v3=cls.expected_v3,
        )
        cls.projection = cls.root.parent / "projection.json"
        cls.projection.write_bytes(
            builder.canonical_json_bytes(helper._envelope(cls.root, cls.manifest, source["release"]))
        )
        cls.candidate = cls.root.parent / "candidate"
        builder.build_candidate(
            cls.root,
            cls.manifest,
            cls.projection,
            cls.candidate,
            candidate_paper_tests.TECTONIC,
            expected_origin=str(cls.origin),
            expected_v3=cls.expected_v3,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def _reviews(self, name: str, direction: str = "positive") -> Path:
        reports = self.root.parent / name
        reports.mkdir()
        _, bindings = candidate_closure(self.root, self.candidate)
        for index, lens in enumerate(V4_CANDIDATE_REVIEW_LENS_IDS):
            value = {
                **bindings,
                "findings": [f"The {lens} lens checked the bound candidate bytes."],
                "lens_id": lens,
                "result_direction": direction,
                "resolutions": ["The reviewer found the stated finite-panel boundary adequate."],
                "reviewed_at_utc": f"2026-09-05T00:00:{index:02d}Z",
                "reviewer": f"Internal Reviewer {index + 1}",
                "schema_version": "anachron-v4-candidate-review-v1",
                "status": "APPROVED",
                "v3_included_count": 0,
            }
            (reports / f"{lens}.json").write_bytes(builder.canonical_json_bytes(value))
        return reports

    def _approval(self, reports: Path, manifest: Path, name: str) -> Path:
        closure, bindings = candidate_closure(self.root, self.candidate)
        template = json.loads(
            (self.root / "paper/v4_measurement/author_approval.template.json").read_text(
                encoding="utf-8"
            )
        )
        template.update(
            {
                "abstract_sha256": hashlib.sha256(
                    closure["metadata"]["abstract"].encode("utf-8")
                ).hexdigest(),
                "ai_assistance_disclosure_sha256": hashlib.sha256(
                    closure["metadata"]["ai_assistance_disclosure"].encode("utf-8")
                ).hexdigest(),
                "approval": "APPROVED",
                "approved_at_utc": "2026-09-05T00:01:00Z",
                "archive_sha256": bindings["archive_sha256"],
                "arxiv_metadata_sha256": bindings["arxiv_metadata_sha256"],
                "candidate_receipt_sha256": bindings["candidate_receipt_sha256"],
                "paper_pdf_sha256": bindings["paper_pdf_sha256"],
                "projection_sha256": bindings["projection_sha256"],
                "review_set_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "status": "APPROVED",
            }
        )
        approval = self.root.parent / name
        approval.write_bytes(builder.canonical_json_bytes(template))
        return approval

    def test_review_manifest_and_release_bind_real_synthetic_candidate(self) -> None:
        reports = self._reviews("reports-pass")
        manifest = self.root.parent / "review-set.json"
        result = review_tool.verify(self.root, self.candidate, reports, manifest)
        self.assertEqual(result["review_lens_ids"], list(V4_CANDIDATE_REVIEW_LENS_IDS))
        approval = self._approval(reports, manifest, "approval.json")
        release = self.root.parent / "local-release"
        receipt = release_tool.release(
            self.root, self.candidate, reports, manifest, approval, release
        )
        self.assertEqual(receipt["v3_included_count"], 0)
        self.assertEqual(
            {path.name for path in release.iterdir()},
            {"candidate.pdf", "source.zip", "arxiv_metadata.json", "local_release_receipt.json"},
        )
        self.assertEqual(
            (release / "candidate.pdf").read_bytes(),
            (self.candidate / "candidate.pdf").read_bytes(),
        )
        outreach = self.root.parent / "unsent-outreach"
        outreach_receipt = outreach_tool.render(self.root, release, outreach)
        self.assertEqual(outreach_receipt["status"], "UNSENT")
        self.assertEqual(
            {path.name for path in outreach.iterdir()}, {"UNSENT.md", "outreach_receipt.json"}
        )

    def test_missing_duplicate_renamed_pending_noncanonical_and_extra_reports_fail(self) -> None:
        for index, mutation in enumerate(
            ("missing", "duplicate", "renamed", "pending", "noncanonical", "extra")
        ):
            with self.subTest(mutation=mutation):
                reports = self._reviews(f"reports-{index}")
                first = reports / f"{V4_CANDIDATE_REVIEW_LENS_IDS[0]}.json"
                if mutation == "missing":
                    first.unlink()
                elif mutation == "duplicate":
                    shutil.copyfile(first, reports / "duplicate.json")
                elif mutation == "renamed":
                    first.rename(reports / "renamed.json")
                elif mutation == "pending":
                    value = json.loads(first.read_text(encoding="utf-8"))
                    value["status"] = "PENDING"
                    first.write_bytes(builder.canonical_json_bytes(value))
                elif mutation == "noncanonical":
                    first.write_bytes(first.read_bytes() + b"\n")
                else:
                    (reports / "extra.json").write_bytes(b"{}\n")
                with self.assertRaises(CandidateReleaseError):
                    review_tool.verify(self.root, self.candidate, reports, self.root.parent / f"out-{index}.json")

    def test_stale_binding_placeholder_and_substance_fail(self) -> None:
        for index, mutation in enumerate(("stale", "placeholder", "finding")):
            with self.subTest(mutation=mutation):
                reports = self._reviews(f"reports-binding-{index}")
                path = reports / f"{V4_CANDIDATE_REVIEW_LENS_IDS[0]}.json"
                value = json.loads(path.read_text(encoding="utf-8"))
                if mutation == "stale":
                    value["arxiv_metadata_sha256"] = "0" * 64
                elif mutation == "placeholder":
                    value["reviewer"] = "REPLACE_WITH_REVIEWER"
                else:
                    value["findings"] = ["TBD"]
                path.write_bytes(builder.canonical_json_bytes(value))
                with self.assertRaises(CandidateReleaseError):
                    review_tool.expected_review_set_manifest(self.root, self.candidate, reports)

    def test_review_direction_must_match_the_sealed_pooled_cells(self) -> None:
        reports = self._reviews("reports-direction-positive", "positive")
        self.assertEqual(
            review_tool.expected_review_set_manifest(self.root, self.candidate, reports)[
                "review_reports"
            ][0]["lens_id"],
            V4_CANDIDATE_REVIEW_LENS_IDS[0],
        )
        for direction in ("zero", "negative"):
            with self.subTest(direction=direction):
                reports = self._reviews(f"reports-direction-{direction}", direction)
                with self.assertRaisesRegex(CandidateReleaseError, "status differs"):
                    review_tool.expected_review_set_manifest(
                        self.root, self.candidate, reports
                    )

    def test_candidate_metadata_and_render_identity_mutations_fail(self) -> None:
        for index, field in enumerate(
            ("title", "abstract", "ai_assistance_disclosure", "categories")
        ):
            with self.subTest(field=field):
                candidate = self.root.parent / f"candidate-metadata-{index}"
                shutil.copytree(self.candidate, candidate)
                metadata_path = candidate / "arxiv_metadata.json"
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata[field] = ["cs.LG"] if field == "categories" else "changed metadata"
                metadata_path.write_bytes(builder.canonical_json_bytes(metadata))
                with self.assertRaises(CandidateReleaseError):
                    candidate_closure(self.root, candidate)

    def test_candidate_source_archive_cap_refuses_before_zipfile_open(self) -> None:
        candidate = self.root.parent / "candidate-oversized-archive"
        shutil.copytree(self.candidate, candidate)
        archive = candidate / "source.zip"
        archive.unlink()
        maximum = V4_CANDIDATE_RESOURCE_POLICY["source_archive_max_bytes"]
        with archive.open("xb") as stream:
            stream.seek(maximum)
            stream.write(b"x")
        with (
            patch.object(release_common.zipfile, "ZipFile") as opener,
            self.assertRaisesRegex(CandidateReleaseError, "source archive exceeds"),
        ):
            candidate_closure(self.root, candidate)
        opener.assert_not_called()
        for index, mutation in enumerate(("changed", "missing", "extra")):
            with self.subTest(mutation=mutation):
                candidate = self.root.parent / f"candidate-render-{index}"
                shutil.copytree(self.candidate, candidate)
                renders = candidate / "qa_renders"
                page = renders / "page-1.png"
                if mutation == "changed":
                    page.write_bytes(b"changed")
                elif mutation == "missing":
                    page.unlink()
                else:
                    (renders / "page-2.png").write_bytes(b"extra")
                with self.assertRaises(CandidateReleaseError):
                    candidate_closure(self.root, candidate)

    def test_report_mutation_between_validation_and_publication_fails(self) -> None:
        reports = self._reviews("reports-race")
        output = self.root.parent / "review-race.json"
        original = review_tool.write_create_only

        def mutate_then_publish(*arguments, **keywords):
            path = reports / f"{V4_CANDIDATE_REVIEW_LENS_IDS[0]}.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["findings"] = ["The reviewer rechecked the bound candidate bytes after validation."]
            path.write_bytes(builder.canonical_json_bytes(value))
            return original(*arguments, **keywords)

        with (
            patch.object(review_tool, "write_create_only", side_effect=mutate_then_publish),
            self.assertRaisesRegex(CandidateReleaseError, "changed during manifest publication"),
        ):
            review_tool.verify(self.root, self.candidate, reports, output)
        self.assertFalse(output.exists())

    def test_release_refuses_changed_candidate_manifest_approval_and_overlap(self) -> None:
        reports = self._reviews("reports-release")
        manifest = self.root.parent / "review-release.json"
        review_tool.verify(self.root, self.candidate, reports, manifest)
        approval = self._approval(reports, manifest, "approval-release.json")
        manifest.write_bytes(b"{}\n")
        with self.assertRaises(CandidateReleaseError):
            release_tool.release(
                self.root,
                self.candidate,
                reports,
                manifest,
                approval,
                self.root.parent / "bad-release",
            )
        manifest.unlink()
        review_tool.verify(self.root, self.candidate, reports, manifest)
        approval = self._approval(reports, manifest, "approval-release-2.json")
        approval_value = json.loads(approval.read_text(encoding="utf-8"))
        approval_value["projection_sha256"] = "0" * 64
        approval.write_bytes(builder.canonical_json_bytes(approval_value))
        with self.assertRaises(CandidateReleaseError):
            release_tool.release(
                self.root,
                self.candidate,
                reports,
                manifest,
                approval,
                self.root.parent / "bad-approval-release",
            )
        approval = self._approval(reports, manifest, "approval-release-3.json")
        with self.assertRaises(CandidateReleaseError):
            release_tool.release(
                self.root,
                self.candidate,
                reports,
                manifest,
                approval,
                self.candidate / "overlap",
            )

    def test_internal_output_oversized_review_and_changed_local_release_fail(self) -> None:
        reports = self._reviews("reports-boundaries")
        with self.assertRaises(CandidateReleaseError):
            review_tool.verify(self.root, self.candidate, reports, self.root / "internal.json")
        changed_candidate = self.root.parent / "changed-candidate"
        shutil.copytree(self.candidate, changed_candidate)
        (changed_candidate / "candidate.pdf").write_bytes(b"changed")
        with self.assertRaises(CandidateReleaseError):
            candidate_closure(self.root, changed_candidate)
        first = reports / f"{V4_CANDIDATE_REVIEW_LENS_IDS[0]}.json"
        first.write_bytes(b"{" + b" " * 1048577)
        with self.assertRaisesRegex(CandidateReleaseError, "byte cap"):
            review_tool.verify(self.root, self.candidate, reports, self.root.parent / "oversized.json")
        shutil.rmtree(reports)
        reports = self._reviews("reports-local-release")
        manifest = self.root.parent / "review-local-release.json"
        review_tool.verify(self.root, self.candidate, reports, manifest)
        approval = self._approval(reports, manifest, "approval-local-release.json")
        release = self.root.parent / "local-release-tamper"
        release_tool.release(self.root, self.candidate, reports, manifest, approval, release)
        (release / "candidate.pdf").write_bytes(b"changed")
        with self.assertRaises(CandidateReleaseError):
            outreach_tool.render(self.root, release, self.root.parent / "tampered-outreach")

class V4CandidateReviewStaticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def test_no_network_or_dispatch_imports_exist(self) -> None:
        for relative in (
            "anachron/v4_candidate_release_common.py",
            "tools/verify_v4_measurement_candidate_reviews.py",
            "tools/release_v4_measurement_candidate.py",
        ):
            source = (self.root / relative).read_text(encoding="utf-8").lower()
            for forbidden in (
                "import requests",
                "import socket",
                "import smtplib",
                "import urllib",
                "import webbrowser",
                "import subprocess",
            ):
                self.assertNotIn(forbidden, source, relative)


if __name__ == "__main__":
    unittest.main()
