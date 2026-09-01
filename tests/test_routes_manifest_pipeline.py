import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from anachron.routes import load_contract
from anachron.routes.manifest import (
    ManifestValidationError,
    canonical_json_sha256,
    seal_manifest,
    stable_item_id,
    validate_curation_draft,
    validate_manifest,
    validate_manifest_with_discovery,
)
from anachron.routes.manifest import main as manifest_main
from anachron.routes.retrieval import (
    RetrievalValidationError,
    retrieve,
    validate_retrieval_result,
)
from anachron.routes.scoring import score_response
from anachron.routes.sources import discover_topic, write_sampling_frame

ROOT = Path(__file__).parents[1]
CONTRACT_PATH = ROOT / "research" / "routes-v1" / "contract.json"
FIXTURES = Path(__file__).parent / "fixtures" / "routes"


class TestRoutesManifestPipeline(unittest.TestCase):
    def setUp(self):
        self.contract = load_contract(CONTRACT_PATH)
        self.strict_raw = (FIXTURES / "strict_revision.json").read_bytes()
        self.post_raw = (FIXTURES / "post_revisions.json").read_bytes()
        self.frame = {
            "schema_version": "routes-v1-exante-sampling-frame",
            "github_revision": self.contract["upstreams"]["exante_github"]["revision"],
            "github_artifact_url": self.contract["upstreams"]["exante_github"]["artifact_url"],
            "github_source_sha256": "sha256:" + "a" * 64,
            "huggingface_revision": self.contract["upstreams"]["exante_huggingface"]["revision"],
            "huggingface_artifact_url": self.contract["upstreams"]["exante_huggingface"]["artifact_url"],
            "huggingface_resolved_url": (
                "https://huggingface.co/api/resolve-cache/datasets/yachuanliu/ExAnte/"
                "4e30593e1aff7360fef5aee865117c5c8e05114e/exante_wiki.csv"
                "?etag=fixture"
            ),
            "huggingface_etag": "fixture",
            "huggingface_source_sha256": "sha256:" + "b" * 64,
            "observed_row_count": 60,
            "observed_unique_pair_count": 60,
            "topics": [
                topic
                for group in self.contract["sampling"]["topics"].values()
                for topic in group
            ],
        }
        self.draft = self._draft()

    def _fetcher(self, url, _timeout):
        if "2013-12-31T23%3A59%3A59Z" in url:
            return self.strict_raw
        if "2014-12-31T23%3A59%3A59Z" in url:
            return self.post_raw
        self.fail(f"unexpected fixture request: {url}")

    def _artifact(self):
        return discover_topic(
            self.contract,
            self.frame,
            phase="pilot",
            title="YouTube",
            fetcher=self._fetcher,
        ).to_dict()

    @staticmethod
    def _evidence(revision, snippet, cutoff_year):
        return {
            "revision_id": revision["revision_id"],
            "timestamp": revision["timestamp"],
            "revision_url": revision["revision_url"],
            "mediawiki_sha1": revision["mediawiki_sha1"],
            "raw_response_sha256": revision["raw_response_sha256"],
            "content_sha256": revision["content_sha256"],
            "snippet": snippet,
            "snippet_sha256": "sha256:"
            + hashlib.sha256(snippet.encode("utf-8")).hexdigest(),
            "displayed_document_date": f"{cutoff_year}-12-31",
        }

    def _draft(self, human_validated=False):
        artifact = self._artifact()
        item_id = stable_item_id("pilot", "YouTube", 2013)
        curation_input_sha256 = "sha256:" + "c" * 64
        curation = {
            "status": "human_validated" if human_validated else "codex_prepared_pending_human",
            "human_validator_id": "fixture-reviewer" if human_validated else None,
            "human_validated_at": "2026-09-01T00:00:00Z" if human_validated else None,
        }
        pair = {
            "item_id": item_id,
            "topic_cluster_id": item_id,
            "study_phase": "pilot",
            "topic": "YouTube",
            "cutoff_year": 2013,
            "sampling_frame_sha256": canonical_json_sha256(self.frame),
            "curation_input_sha256": curation_input_sha256,
            "discovery_artifact_sha256": canonical_json_sha256(artifact),
            "discovery_artifact_file": "youtube.json",
            "source_status": "source_valid",
            "post_snapshot_horizon_days": 365,
            "pre": self._evidence(artifact["strict_revision"], "Before revision", 2013),
            "post": self._evidence(artifact["post_snapshot"], "After revision two", 2013),
            "pre_anchor": "Before revision",
            "post_anchor": "After revision two",
            "question": "Which version-specific value is documented for this topic?",
            "pre_answer_aliases": ["Earlier answer"],
            "post_answer_aliases": ["Later answer"],
            "change_type": "event_status",
            "semantic_strength": "clean",
            "notes": "Synthetic fixture only.",
            "license_attribution": {
                "license": "CC BY-SA 4.0",
                "source_family": "English Wikipedia",
                "attribution_text": "English Wikipedia contributors, YouTube revision history.",
            },
            "curation": curation,
        }
        return {
            "schema_version": "routes-v1-curation-draft",
            "sampling_frame_sha256": canonical_json_sha256(self.frame),
            "curation_input_sha256": curation_input_sha256,
            "pairs": [pair],
            "rejected_topics": [
                {
                    "study_phase": "pilot",
                    "title": topic["title"],
                    "reason": "Synthetic fixture rejection.",
                }
                for topic in self.contract["sampling"]["topics"]["pilot"]
                if topic["title"] != "YouTube"
            ],
        }

    @staticmethod
    def _write_artifact(directory, artifact):
        (Path(directory) / "youtube.json").write_text(json.dumps(artifact), encoding="utf-8")

    def _seal(self, draft):
        with tempfile.TemporaryDirectory() as directory:
            self._write_artifact(directory, self._artifact())
            return seal_manifest(draft, self.contract, self.frame, directory)

    def test_draft_requires_truthful_curation_and_sealing_requires_human_validation(self):
        validate_curation_draft(self.draft, self.contract, self.frame)
        with self.assertRaises(ManifestValidationError):
            self._seal(self.draft)
        sealed = self._seal(self._draft(human_validated=True))
        validate_manifest(sealed, self.contract, self.frame)
        self.assertEqual(sealed["schema_version"], "routes-v1-source-manifest")

    def test_pair_validation_rejects_provenance_leakage_and_identity_mutations(self):
        human_draft = self._draft(human_validated=True)
        cases = {
            "altered_frame_hash": lambda draft: draft.update(
                {"sampling_frame_sha256": "sha256:" + "0" * 64}
            ),
            "altered_artifact_hash": lambda draft: draft["pairs"][0].update(
                {"discovery_artifact_sha256": "sha256:" + "0" * 64}
            ),
            "snippet_not_in_source": lambda draft: draft["pairs"][0]["pre"].update(
                {"snippet": "invented", "snippet_sha256": "sha256:" + "0" * 64}
            ),
            "answer_leaks_into_question": lambda draft: draft["pairs"][0].update(
                {"question": "Is the answer Later answer?"}
            ),
            "overlapping_aliases": lambda draft: draft["pairs"][0].update(
                {"post_answer_aliases": ["Earlier answer"]}
            ),
            "mutable_oldid_url": lambda draft: draft["pairs"][0]["pre"].update(
                {"revision_url": "https://en.wikipedia.org/wiki/YouTube"}
            ),
            "source_ineligible": lambda draft: draft["pairs"][0].update(
                {"source_status": "source_ineligible"}
            ),
            "extra_pair_field": lambda draft: draft["pairs"][0].update({"extra": True}),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                candidate = copy.deepcopy(human_draft)
                mutate(candidate)
                with self.assertRaises(ManifestValidationError):
                    self._seal(candidate)
        duplicate = copy.deepcopy(human_draft)
        duplicate["pairs"].append(copy.deepcopy(duplicate["pairs"][0]))
        with self.assertRaises(ManifestValidationError):
            self._seal(duplicate)

    def test_sealing_rechecks_local_provenance_and_strips_raw_discovery_content(self):
        draft = self._draft(human_validated=True)
        with tempfile.TemporaryDirectory() as directory:
            artifact = self._artifact()
            self._write_artifact(directory, artifact)
            sealed = seal_manifest(draft, self.contract, self.frame, directory)
            validate_manifest_with_discovery(sealed, self.contract, self.frame, directory)
            self.assertNotIn("discovery_artifact_file", sealed["pairs"][0])
            self.assertNotIn("discovery_artifact", sealed["pairs"][0])
            self.assertNotIn('"content"', json.dumps(sealed))
            artifact["strict_revision"]["content"] = "tampered local artifact"
            self._write_artifact(directory, artifact)
            with self.assertRaises(ManifestValidationError):
                seal_manifest(draft, self.contract, self.frame, directory)
            with self.assertRaises(ManifestValidationError):
                validate_manifest_with_discovery(
                    sealed, self.contract, self.frame, directory
                )
        validate_manifest(sealed, self.contract, self.frame)

    def test_retrieval_routes_exact_evidence_and_one_trace_event(self):
        manifest = self._seal(self._draft(human_validated=True))
        item_id = manifest["pairs"][0]["item_id"]
        for condition, expected_arm in (("no_tool", None), ("strict", "pre"), ("misdated", "post")):
            with self.subTest(condition=condition):
                result = retrieve(
                    manifest,
                    self.contract,
                    self.frame,
                    item_id=item_id,
                    condition=condition,
                    retrieved_at="2026-09-01T01:02:03Z",
                )
                validate_retrieval_result(result, manifest["pairs"][0])
                self.assertEqual(len(result["evidence"]), 0 if expected_arm is None else 1)
                self.assertEqual(result["trace_event"]["created_at"], "2026-09-01T01:02:03Z")
                if expected_arm is not None:
                    self.assertEqual(result["evidence"][0]["arm"], expected_arm)
        tampered = retrieve(
            manifest,
            self.contract,
            self.frame,
            item_id=item_id,
            condition="strict",
            retrieved_at="2026-09-01T01:02:03Z",
        )
        tampered["evidence"][0]["snippet"] = "invented"
        with self.assertRaises(RetrievalValidationError):
            validate_retrieval_result(tampered, manifest["pairs"][0])

    def test_scoring_is_json_only_alias_exact_and_trace_descriptive(self):
        manifest = self._seal(self._draft(human_validated=True))
        pair = manifest["pairs"][0]
        item_id = pair["item_id"]
        strict = retrieve(
            manifest,
            self.contract,
            self.frame,
            item_id=item_id,
            condition="strict",
            retrieved_at="2026-09-01T01:02:03Z",
        )
        strict_score = score_response(
            pair,
            strict,
            json.dumps({"answer": "Earlier answer", "citation_ids": [f"{item_id}:pre"]}),
        )
        self.assertEqual(strict_score["answer_label"], "pre_only")
        self.assertFalse(strict_score["post_citation"])
        misdated = retrieve(
            manifest,
            self.contract,
            self.frame,
            item_id=item_id,
            condition="misdated",
            retrieved_at="2026-09-01T01:02:03Z",
        )
        post_score = score_response(
            pair,
            misdated,
            json.dumps({"answer": "Later answer", "citation_ids": [f"{item_id}:post"]}),
        )
        self.assertEqual(post_score["answer_label"], "post_only")
        self.assertTrue(post_score["trace_backed_transmission"])
        no_tool = retrieve(
            manifest,
            self.contract,
            self.frame,
            item_id=item_id,
            condition="no_tool",
            retrieved_at="2026-09-01T01:02:03Z",
        )
        off_trace = score_response(
            pair,
            no_tool,
            json.dumps({"answer": "Later answer", "citation_ids": []}),
        )
        self.assertTrue(off_trace["off_trace_transmission"])
        mixed = score_response(
            pair,
            misdated,
            json.dumps(
                {"answer": "Earlier answer; Later answer", "citation_ids": [f"{item_id}:post"]}
            ),
        )
        self.assertEqual(mixed["answer_label"], "mixed")
        for invalid in (
            "not json",
            json.dumps({"answer": "Later answer", "citation_ids": [], "extra": True}),
            json.dumps({"answer": "Later answer", "citation_ids": ["unknown"]}),
            json.dumps(
                {
                    "answer": "Later answer",
                    "citation_ids": [f"{item_id}:post", f"{item_id}:post"],
                }
            ),
        ):
            self.assertEqual(score_response(pair, misdated, invalid)["answer_label"], "invalid_output")

    def test_offline_manifest_cli_seals_and_validates_only_human_reviewed_drafts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_path = root / "contract.json"
            frame_path = root / "sampling_frame.json"
            draft_path = root / "draft.json"
            manifest_path = root / "manifest.json"
            discovery_directory = root / "discovery"
            discovery_directory.mkdir()
            self._write_artifact(discovery_directory, self._artifact())
            contract_path.write_bytes(CONTRACT_PATH.read_bytes())
            write_sampling_frame(frame_path, self.frame)
            draft_path.write_text(
                json.dumps(self._draft(human_validated=True)), encoding="utf-8"
            )
            self.assertEqual(
                manifest_main(
                    [
                        "seal",
                        "--contract",
                        str(contract_path),
                        "--sampling-frame",
                        str(frame_path),
                        "--draft",
                        str(draft_path),
                        "--discovery-directory",
                        str(discovery_directory),
                        "--output",
                        str(manifest_path),
                    ]
                ),
                0,
            )
            self.assertEqual(
                manifest_main(
                    [
                        "validate-manifest",
                        "--contract",
                        str(contract_path),
                        "--sampling-frame",
                        str(frame_path),
                        "--manifest",
                        str(manifest_path),
                        "--discovery-directory",
                        str(discovery_directory),
                    ]
                ),
                0,
            )


if __name__ == "__main__":
    unittest.main()
