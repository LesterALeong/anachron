"""Disposable offline end-to-end closure for the v5 paper lifecycle.

This test is intentionally skip-gated on a caller-supplied, already-installed
pinned Tectonic executable. It never starts Ollama or opens a network socket:
the measurement transport is an injected byte-returning function.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from anachron.v5_candidate_common import CandidateProjectionError, project_candidate
from anachron.v5_candidate_release_common import (
    V5_CANDIDATE_REVIEW_LENS_IDS,
    CandidateReleaseError,
    candidate_closure,
)
from anachron.v5_measurement import run_measurement, sha256_bytes
from anachron.v5_registry import canonical_json_bytes, load_v5_registry
from tests.test_v5_measurement import measurement_plan_and_go
from tests.v5_presentation_fixture import injected_transport
from tools import build_v5_measurement_candidate_paper as builder
from tools import project_v5_measurement_candidate as projector
from tools import release_v5_measurement_candidate as release_tool
from tools import render_v5_measurement_unsent_outreach as outreach_tool
from tools import verify_v5_measurement_candidate_reviews as review_tool


def _tectonic() -> Path | None:
    value = os.environ.get("ANACHRON_V5_TECTONIC")
    return Path(value) if value and Path(value).is_file() else None


def _paper_dependencies_available() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in ("fitz", "PIL"))


class V5CandidateEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tectonic = _tectonic()
        if cls.tectonic is None or not _paper_dependencies_available():
            raise unittest.SkipTest("requires ANACHRON_V5_TECTONIC and the local [paper] extras")
        source_cache = os.environ.get("ANACHRON_V5_TECTONIC_CACHE_SOURCE")
        cls._original_cache = os.environ.get("TECTONIC_CACHE_DIR")
        cls._cache_temporary = None
        if source_cache is not None:
            source = Path(source_cache)
            if not source.is_dir():
                raise unittest.SkipTest("ANACHRON_V5_TECTONIC_CACHE_SOURCE is unavailable")
            cls._cache_temporary = tempfile.TemporaryDirectory()
            cache = Path(cls._cache_temporary.name) / "tectonic-cache"
            shutil.copytree(source, cache)
            os.environ["TECTONIC_CACHE_DIR"] = str(cache)
        if not os.environ.get("TECTONIC_CACHE_DIR"):
            raise unittest.SkipTest("requires a dedicated TECTONIC_CACHE_DIR")
        cls.root = Path(__file__).resolve().parents[1]
        _, cls.cards = load_v5_registry(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._cache_temporary is not None:
            cls._cache_temporary.cleanup()
            if cls._original_cache is None:
                os.environ.pop("TECTONIC_CACHE_DIR", None)
            else:
                os.environ["TECTONIC_CACHE_DIR"] = cls._original_cache

    def _transport(self, _endpoint: str, path: str, payload: bytes | None, _timeout: int) -> bytes:
        return injected_transport(_endpoint, path, payload, _timeout)

    def _authority_inputs(self, root: Path, plan: Path, go: Path) -> dict[str, Path]:
        plans = plan.parent
        return {
            "source_manifest": plans / "source_manifest.json",
            "carry_forward": plans / "carry_forward.json",
            "runtime_identity": plans / "runtime_identity.json",
            "conditional_go": go,
            "materialization_receipt": plans / "materialization_receipt.json",
        }

    def _reports(self, candidate: Path, output: Path) -> Path:
        output.mkdir()
        _, bindings = candidate_closure(self.root, candidate)
        for index, lens in enumerate(V5_CANDIDATE_REVIEW_LENS_IDS):
            report = {
                **bindings,
                "findings": [f"The {lens} reviewer checked each bound candidate artifact and the finite-panel limitation."],
                "lens_id": lens,
                "resolutions": ["The reviewer found the replay-bound v5 scope and zero-v4 inclusion statement adequate."],
                "reviewed_at_utc": f"2026-09-06T00:00:{index:02d}Z",
                "reviewer": f"Internal Reviewer {index + 1}",
                "schema_version": "anachron-v5-candidate-review-v1",
                "status": "APPROVED",
                "v4_included_count": 0,
            }
            (output / f"{lens}.json").write_bytes(canonical_json_bytes(report))
        return output

    def _approval(self, candidate: Path, review_manifest: Path, output: Path) -> Path:
        closure, bindings = candidate_closure(self.root, candidate)
        template = json.loads((self.root / "paper/v5_measurement/author_approval.template.json").read_text(encoding="utf-8"))
        template.update({
            "abstract_sha256": hashlib.sha256(closure["metadata"]["abstract"].encode("utf-8")).hexdigest(),
            "ai_assistance_disclosure_sha256": hashlib.sha256(closure["metadata"]["ai_assistance_disclosure"].encode("utf-8")).hexdigest(),
            "approval": "APPROVED",
            "approved_at_utc": "2026-09-06T00:02:00Z",
            "archive_sha256": bindings["archive_sha256"],
            "arxiv_metadata_sha256": bindings["arxiv_metadata_sha256"],
            "candidate_receipt_sha256": bindings["candidate_receipt_sha256"],
            "paper_pdf_sha256": bindings["paper_pdf_sha256"],
            "projection_sha256": bindings["projection_sha256"],
            "review_set_manifest_sha256": sha256_bytes(review_manifest.read_bytes()),
            "status": "APPROVED",
        })
        output.write_bytes(canonical_json_bytes(template))
        return output

    def test_disposable_end_to_end_candidate_review_release_and_unsent_outreach(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            plan, go = measurement_plan_and_go(self.root, self.cards, temporary_root)
            evidence = temporary_root / "evidence"
            run_measurement(plan, go, evidence, repository_root=self.root, transport=self._transport)
            inputs = self._authority_inputs(temporary_root, plan, go)
            for name, source in inputs.items():
                tampered = temporary_root / f"tampered-{name}.json"
                tampered.write_bytes(canonical_json_bytes({"tampered": name}))
                with self.subTest(tampered_authority=name), self.assertRaises(CandidateProjectionError):
                    project_candidate(self.root, evidence=evidence, **{**inputs, name: tampered})
            projection = temporary_root / "projection.json"
            projector.project_and_write_candidate(self.root, evidence=evidence, output=projection, **inputs)
            candidate = temporary_root / "candidate"
            builder.build_candidate(self.root, projection, candidate, self.tectonic)
            closure, _bindings = candidate_closure(self.root, candidate)
            self.assertEqual(closure["projection"]["v4_included_count"], 0)
            self.assertTrue((candidate / "candidate.pdf").is_file())
            self.assertTrue((candidate / "source.zip").is_file())
            self.assertEqual(closure["metadata"]["v4_included_count"], 0)
            candidate_members = tuple(path.name for path in candidate.iterdir())
            for member in candidate_members:
                rejected = temporary_root / f"candidate-without-{member}"
                shutil.copytree(candidate, rejected)
                target = rejected / member
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                with self.subTest(missing_candidate_member=member), self.assertRaises(CandidateReleaseError):
                    candidate_closure(self.root, rejected)
            receipt = json.loads((candidate / "candidate_receipt.json").read_text(encoding="utf-8"))
            for binding in (
                "actual_go_sha256", "authority_contract_sha256", "carry_forward_sha256",
                "compatibility_plan_sha256", "full_plan_sha256", "materialization_receipt_sha256",
                "runtime_identity_sha256", "source_manifest_sha256", "candidate_contract_sha256",
                "presentation_source_closure_sha256",
            ):
                rejected = temporary_root / f"candidate-with-bad-{binding}"
                shutil.copytree(candidate, rejected)
                altered = dict(receipt)
                altered[binding] = "0" * 64
                (rejected / "candidate_receipt.json").write_bytes(canonical_json_bytes(altered))
                with self.subTest(tampered_candidate_binding=binding), self.assertRaises(CandidateReleaseError):
                    candidate_closure(self.root, rejected)
            reports = self._reports(candidate, temporary_root / "reports")
            mutation_manifest = temporary_root / "review-set-mutation.json"

            def mutate_report() -> None:
                path = reports / f"{V5_CANDIDATE_REVIEW_LENS_IDS[0]}.json"
                path.write_bytes(canonical_json_bytes({"mutated": True}))

            with self.assertRaisesRegex(Exception, "review reports changed during manifest publication"):
                review_tool.verify(self.root, candidate, reports, mutation_manifest, before_publish=mutate_report)
            self.assertFalse(mutation_manifest.exists())
            shutil.rmtree(reports)
            reports = self._reports(candidate, temporary_root / "reports")
            review_manifest = temporary_root / "review-set.json"
            review = review_tool.verify(self.root, candidate, reports, review_manifest)
            self.assertEqual(review["review_lens_ids"], list(V5_CANDIDATE_REVIEW_LENS_IDS))
            approval = self._approval(candidate, review_manifest, temporary_root / "approval.json")
            release = temporary_root / "release"
            receipt = release_tool.release(self.root, candidate, reports, review_manifest, approval, release)
            self.assertEqual(receipt["v4_included_count"], 0)
            outreach = temporary_root / "outreach"
            unsent = outreach_tool.render(self.root, release, outreach)
            self.assertEqual(unsent["status"], "UNSENT")
            self.assertEqual({path.name for path in outreach.iterdir()}, {"UNSENT.md", "outreach_receipt.json"})
            for relative in (
                "tools/build_v5_measurement_candidate_paper.py",
                "tools/verify_v5_measurement_candidate_reviews.py",
                "tools/release_v5_measurement_candidate.py",
                "tools/render_v5_measurement_unsent_outreach.py",
            ):
                source = (self.root / relative).read_text(encoding="utf-8").lower()
                for forbidden in ("import requests", "import socket", "import smtplib", "import urllib", "import webbrowser", "ollama"):
                    self.assertNotIn(forbidden, source, relative)
        self.assertFalse(temporary_root.exists())

    def test_empty_cache_fails_closed_before_candidate_publication(self) -> None:
        original_cache = os.environ.get("TECTONIC_CACHE_DIR")
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            empty_cache = temporary_root / "empty-cache"
            empty_cache.mkdir()
            os.environ["TECTONIC_CACHE_DIR"] = str(empty_cache)
            try:
                plan, go = measurement_plan_and_go(self.root, self.cards, temporary_root)
                evidence = temporary_root / "evidence"
                run_measurement(plan, go, evidence, repository_root=self.root, transport=self._transport)
                inputs = self._authority_inputs(temporary_root, plan, go)
                projection = temporary_root / "projection.json"
                projector.project_and_write_candidate(
                    self.root, evidence=evidence, output=projection, **inputs
                )
                candidate = temporary_root / "candidate"
                with self.assertRaises(builder.CandidatePaperError):
                    builder.build_candidate(self.root, projection, candidate, self.tectonic)
                self.assertFalse(candidate.exists())
            finally:
                if original_cache is None:
                    os.environ.pop("TECTONIC_CACHE_DIR", None)
                else:
                    os.environ["TECTONIC_CACHE_DIR"] = original_cache


if __name__ == "__main__":
    unittest.main()
