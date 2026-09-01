import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anachron.routes.v2.admission import revalidate_raw_source, write_create_only
from anachron.routes.v2.human_review import decision_template, render_review_markdown
from anachron.routes.v2.manifest import ManifestValidationError
from anachron.routes.v2.scoring import score_response
from anachron.routes.v2.source_excerpt import (
    ExcerptValidationError,
    build_excerpt_receipts,
    validate_excerpt_receipt,
)
from tests.test_routes_v2_production_scale_source_boundary import (
    TestRoutesV2ProductionScaleSourceBoundary,
)

ROOT = Path(__file__).parents[1]


class TestRoutesV2FixedRawRoot(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("research/routes-v2/artifacts/raw/\n", encoding="utf-8")
        self.raw_root = self.root / "research" / "routes-v2" / "artifacts" / "raw" / "development"
        self.raw_root.mkdir(parents=True)
        for index in range(6):
            (self.raw_root / f"routes-v2-development-{index}.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def _paths(self, repository=None):
        from anachron.routes.v2.admission import phase_raw_artifact_paths

        return phase_raw_artifact_paths(self.root if repository is None else repository, "development")

    def _assert_repository_capability_rejected_before_git(self, repository):
        from anachron.routes.v2 import admission

        with patch.object(admission.subprocess, "run", side_effect=AssertionError("Git must not run")) as run, self.assertRaises(ValueError):
            self._paths(repository)
        run.assert_not_called()

    def test_exact_ignored_fixed_root_passes(self):
        self.assertEqual([path.name for path in self._paths()], [f"routes-v2-development-{index}.json" for index in range(6)])

    def test_tracked_unignored_and_unexpected_paths_reject(self):
        tracked = self.raw_root / "routes-v2-development-0.json"
        subprocess.run(["git", "-C", str(self.root), "add", "-f", str(tracked.relative_to(self.root))], check=True)
        with self.assertRaises(ValueError):
            self._paths()
        subprocess.run(["git", "-C", str(self.root), "reset", "-q"], check=True)
        (self.root / ".gitignore").write_text("", encoding="utf-8")
        with self.assertRaises(ValueError):
            self._paths()
        (self.root / ".gitignore").write_text("research/routes-v2/artifacts/raw/\n", encoding="utf-8")
        (self.raw_root / "wrong.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            self._paths()

    def test_nested_and_linked_paths_reject(self):
        (self.raw_root / "nested").mkdir()
        with self.assertRaises(ValueError):
            self._paths()
        (self.raw_root / "nested").rmdir()
        target = self.raw_root / "routes-v2-development-0.json"
        target.unlink()
        try:
            os.symlink(self.root / ".gitignore", target)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        with self.assertRaises(ValueError):
            self._paths()

    def test_broken_symlink_rejects_when_link_creation_is_available(self):
        target = self.raw_root / "routes-v2-development-0.json"
        target.unlink()
        try:
            os.symlink(self.root / "missing-raw-artifact.json", target)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        with self.assertRaises(ValueError):
            self._paths()

    def test_repository_symlink_and_broken_target_reject_before_raw_or_git_access(self):
        link = self.root.parent / "repository-root-link"
        try:
            os.symlink(self.root, link, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        self._assert_repository_capability_rejected_before_git(link)
        self._assert_repository_capability_rejected_before_git(self.root.parent / "missing-repository-root")

    def test_windows_junction_rejects_when_the_platform_exposes_junctions(self):
        if os.name != "nt":
            self.skipTest("Windows junctions are not available on this platform")
        target = self.root / "junction-target"
        shutil.copytree(self.raw_root, target)
        shutil.rmtree(self.raw_root)
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(self.raw_root), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            self.skipTest("junction creation is unavailable")
        with self.assertRaises(ValueError):
            self._paths()

    def test_windows_repository_junction_rejects_before_raw_or_git_access(self):
        if os.name != "nt":
            self.skipTest("Windows junctions are not available on this platform")
        link = self.root.parent / "repository-root-junction"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            self.skipTest("junction creation is unavailable")
        self._assert_repository_capability_rejected_before_git(link)

    def test_reparse_attribute_and_obsolete_configuration_reject(self):
        from anachron.routes.v2 import admission
        from anachron.routes.v2.analysis import (
            AnalysisValidationError,
            replay_phase_root,
        )

        original = admission._is_reparse
        with patch.object(admission, "_is_reparse", side_effect=lambda path: path == self.raw_root or original(path)), self.assertRaises(ValueError):
            self._paths()
        analysis = self.root / "analysis"
        analysis.mkdir()
        (analysis / "raw_source_root.json").write_text(json.dumps({"schema_version": "routes-v2-raw-source-root-v1", "raw_directory": ".."}), encoding="utf-8")
        with self.assertRaises(AnalysisValidationError):
            replay_phase_root(analysis, self.root, phase="development")


class TestRoutesV2Core(unittest.TestCase):
    def setUp(self):
        self.fixture = TestRoutesV2ProductionScaleSourceBoundary()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.draft, self.manifest = self.fixture._prepare_draft(production_scale=False)
        self.contract = self.fixture.contract

    def test_human_template_binds_full_review_projection_and_markdown_is_bounded(self):
        template = decision_template(self.draft, self.contract)
        self.assertEqual(template["schema_version"], self.contract["source_gate"]["decision_schema"])
        self.assertEqual(template["decisions"][0]["reviewed_projection"], self.draft["pairs"][0])
        packet = render_review_markdown(self.draft, self.contract)
        self.assertIn("Routes v2 source review", packet)
        self.assertNotIn("strict_revision", packet)
        self.assertTrue(
            all(
                pair["pre_excerpt"]["utf8_bytes"] <= 4096
                and pair["post_excerpt"]["utf8_bytes"] <= 4096
                for pair in self.draft["pairs"]
            )
        )

    def test_recomputed_forged_excerpt_and_unsupported_aliases_reject_before_seal(self):
        forged = copy.deepcopy(self.draft["excerpt_receipts"][0])
        forged["excerpt"]["text"] = "Z" * len(forged["excerpt"]["text"])
        forged["excerpt"]["sha256"] = "sha256:" + hashlib.sha256(forged["excerpt"]["text"].encode("utf-8")).hexdigest()
        unsigned = {key: value for key, value in forged.items() if key != "receipt_sha256"}
        from anachron.routes.v2.admission import canonical_json_sha256

        forged["receipt_sha256"] = canonical_json_sha256(unsigned)
        with self.assertRaises(ExcerptValidationError):
            validate_excerpt_receipt(forged, contract=self.contract)
        unsupported = copy.deepcopy(self.draft)
        unsupported["source_mapping"]["items"][0]["pre_aliases"] = ["OLD", "VALUE", "ALTERNATE"]
        unsupported["source_mapping_sha256"] = canonical_json_sha256(unsupported["source_mapping"])
        with self.assertRaises(ManifestValidationError):
            from anachron.routes.v2.manifest import validate_pending_draft

            revalidations = {item["item_id"]: item for item in unsupported["revalidation_receipts"]}
            excerpts = {(item["item_id"], item["arm"]): item for item in unsupported["excerpt_receipts"]}
            validate_pending_draft(
                unsupported,
                repository=self.fixture.repository,
                contract=self.contract,
                sampling_frame=self.fixture.frame,
                revalidation_receipts=revalidations,
                excerpt_receipts=excerpts,
                phase="development",
            )

    def test_manifest_rejects_excerpt_alias_and_gate_tampering(self):
        for mutate in (
            lambda value: value["pairs"][0]["post_excerpt"].update({"text": "tampered"}),
            lambda value: value["pairs"][0].update({"post_aliases": ["not sealed"]}),
            lambda value: value["source_gate_receipt"].update({"excerpt_receipts_sha256": "sha256:" + "0" * 64}),
        ):
            with self.subTest(mutate=mutate):
                changed = copy.deepcopy(self.manifest)
                mutate(changed)
                from anachron.routes.v2.manifest import validate_manifest

                with self.assertRaises(ManifestValidationError):
                    validate_manifest(changed, self.contract)

    def test_exact_five_label_scorer_supports_alternate_post_aliases_and_citation(self):
        pair = self.manifest["pairs"][0]
        rules = {
            "pre_aliases": pair["pre_aliases"],
            "post_aliases": pair["post_aliases"],
            "abstention_aliases": self.contract["answer_rules"]["abstention_aliases"],
        }
        citation = pair["post_opaque_citation_id"]
        cases = {
            "post_only": {"answer": pair["post_aliases"][1], "citation_id": citation},
            "pre_only": {"answer": pair["pre_aliases"][0], "citation_id": citation},
            "abstain": {"answer": "ABSTAIN", "citation_id": citation},
            "other": {"answer": "unrelated", "citation_id": citation},
        }
        for label, response in cases.items():
            with self.subTest(label=label):
                self.assertEqual(score_response(json.dumps(response), answer_rules=rules, expected_citation_id=citation)["answer_label"], label)
        self.assertEqual(score_response(json.dumps({"answer": "x", "citation_id": "wrong"}), answer_rules=rules, expected_citation_id=citation)["answer_label"], "invalid_output")

    def test_unicode_anchor_windows_are_byte_bounded_and_cross_arm_or_raw_tampering_rejects(self):
        topic = self.contract["development"]["topics"][0]
        pre, post = "é" * 3000 + " PRE-UNICODE-ANCHOR " + "x" * 3000, "é" * 3000 + " POST-UNICODE-ANCHOR " + "y" * 3000
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_path = root / "raw.json"
            raw = {
                "schema_version": "routes-v1-source-discovery", "title": topic["title"], "cutoff_year": topic["cutoff_year"],
                "strict_revision": self.fixture._revision(77_001, pre, f"{topic['cutoff_year']}-12-30T00:00:00Z"),
                "post_snapshot": self.fixture._revision(77_002, post, f"{topic['cutoff_year'] + 1}-01-02T00:00:00Z"),
            }
            write_create_only(raw_path, raw)
            receipt = revalidate_raw_source(contract_path=ROOT / "research/routes-v2/contract.json", sampling_frame_path=ROOT / "research/routes-v2/sampling_frame.json", raw_artifact_path=raw_path, phase="development", item_id="routes-v2:development:0", output_path=root / "receipt.json")
            mapping = {"item_id": "routes-v2:development:0", "question": "Which value?", "pre_anchor": "PRE-UNICODE-ANCHOR", "post_anchor": "POST-UNICODE-ANCHOR", "pre_aliases": ["old"], "post_aliases": ["new"], "pre_opaque_citation_id": "PRE", "post_opaque_citation_id": "POST", "raw_discovery_artifact_sha256": receipt["raw_discovery_artifact_sha256"], "revalidation_receipt_sha256": receipt["receipt_sha256"]}
            before, after = build_excerpt_receipts(contract=self.contract, revalidation_receipt=receipt, raw_artifact_path=raw_path, mapping_item=mapping)
            self.assertEqual(before["excerpt"]["utf8_bytes"], 4096)
            self.assertEqual(after["excerpt"]["text"].encode("utf-8").decode("utf-8"), after["excerpt"]["text"])
            wrong_anchor = dict(mapping, post_anchor="PRE-UNICODE-ANCHOR")
            with self.assertRaises(ExcerptValidationError):
                build_excerpt_receipts(contract=self.contract, revalidation_receipt=receipt, raw_artifact_path=raw_path, mapping_item=wrong_anchor)
            raw["strict_revision"]["content"] += "tamper"
            raw["strict_revision"]["content_sha256"] = "sha256:" + hashlib.sha256(raw["strict_revision"]["content"].encode("utf-8")).hexdigest()
            (root / "raw-tampered.json").write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ExcerptValidationError):
                build_excerpt_receipts(contract=self.contract, revalidation_receipt=receipt, raw_artifact_path=root / "raw-tampered.json", mapping_item=mapping)


if __name__ == "__main__":
    unittest.main()
