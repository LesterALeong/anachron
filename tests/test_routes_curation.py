import copy
import difflib
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from anachron.routes import load_contract
from anachron.routes.curation import CurationInputError, prepare_draft
from anachron.routes.manifest import canonical_json_sha256
from anachron.routes.manifest import main as manifest_main
from anachron.routes.sources import discover_topic, write_sampling_frame

ROOT = Path(__file__).parents[1]
CONTRACT_PATH = ROOT / "research" / "routes-v1" / "contract.json"
FIXTURES = Path(__file__).parent / "fixtures" / "routes"


class TestRoutesCuration(unittest.TestCase):
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
                "4e30593e1aff7360fef5aee865117c5c8e05114e/exante_wiki.csv?etag=fixture"
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

    def _input(self):
        rejected_topics = [
            {"title": topic["title"], "reason": "Synthetic fixture rejection."}
            for topic in self.contract["sampling"]["topics"]["pilot"]
            if topic["title"] != "YouTube"
        ]
        return {
            "schema_version": "routes-v1-curation-input",
            "study_phase": "pilot",
            "rejected_topics": rejected_topics,
            "entries": [
                {
                    "title": "YouTube",
                    "question": "Which version-specific value is documented for this topic?",
                    "pre_answer_aliases": ["Earlier answer"],
                    "post_answer_aliases": ["Later answer"],
                    "pre_anchor": "Before revision",
                    "post_anchor": "After revision two",
                    "change_type": "event_status",
                    "semantic_strength": "clean",
                    "notes": "Synthetic fixture only.",
                }
            ],
        }

    @staticmethod
    def _rehash_artifact(artifact):
        for field in ("strict_revision", "post_snapshot"):
            revision = artifact[field]
            content = revision["content"]
            revision["content_sha256"] = "sha256:" + hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest()
            revision["mediawiki_sha1"] = hashlib.sha1(content.encode("utf-8")).hexdigest()
        strict = artifact["strict_revision"]
        post = artifact["post_snapshot"]
        artifact["snapshot_diff"] = "".join(
            difflib.unified_diff(
                strict["content"].splitlines(keepends=True),
                post["content"].splitlines(keepends=True),
                fromfile=f"oldid:{strict['revision_id']}",
                tofile=f"oldid:{post['revision_id']}",
            )
        )

    @staticmethod
    def _write_artifact(directory, artifact):
        path = Path(directory) / "youtube.json"
        path.write_text(json.dumps(artifact), encoding="utf-8")

    def test_prepare_draft_is_deterministic_accounted_and_pending_human(self):
        with tempfile.TemporaryDirectory() as directory:
            self._write_artifact(directory, self._artifact())
            first = prepare_draft(self._input(), self.contract, self.frame, directory)
            second = prepare_draft(self._input(), self.contract, self.frame, directory)
        self.assertEqual(first, second)
        self.assertEqual(first["curation_input_sha256"], canonical_json_sha256(self._input()))
        self.assertEqual(len(first["pairs"]), 1)
        self.assertEqual(len(first["rejected_topics"]), 19)
        self.assertEqual(first["pairs"][0]["curation"]["status"], "codex_prepared_pending_human")
        self.assertIn("Before revision", first["pairs"][0]["pre"]["snippet"])

    def test_prepare_draft_rejects_missing_duplicate_and_unexpected_topic_accounting(self):
        cases = {}
        missing = self._input()
        missing["rejected_topics"].pop()
        cases["missing"] = missing
        duplicate = self._input()
        duplicate["rejected_topics"].append(copy.deepcopy(duplicate["rejected_topics"][0]))
        cases["duplicate"] = duplicate
        unexpected = self._input()
        unexpected["rejected_topics"][0]["title"] = "Not a declared topic"
        cases["unexpected"] = unexpected
        with tempfile.TemporaryDirectory() as directory:
            self._write_artifact(directory, self._artifact())
            for name, curation_input in cases.items():
                with self.subTest(name=name), self.assertRaises(CurationInputError):
                    prepare_draft(curation_input, self.contract, self.frame, directory)

    def test_prepare_draft_rejects_anchor_multiplicity_and_cross_side_anchors(self):
        repeated = self._artifact()
        repeated["strict_revision"]["content"] = "unique anchor\nunique anchor\n"
        repeated["post_snapshot"]["content"] = "post anchor\n"
        self._rehash_artifact(repeated)
        repeated_input = self._input()
        repeated_input["entries"][0]["pre_anchor"] = "unique anchor"
        repeated_input["entries"][0]["post_anchor"] = "post anchor"
        cross_side = self._input()
        cross_side["entries"][0]["post_anchor"] = "Before revision"
        for name, artifact, curation_input in (
            ("multiplicity", repeated, repeated_input),
            ("cross_side", self._artifact(), cross_side),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                self._write_artifact(directory, artifact)
                with self.assertRaises(CurationInputError):
                    prepare_draft(curation_input, self.contract, self.frame, directory)

    def test_prepare_draft_keeps_the_bounded_window_when_line_expansion_exceeds_maximum(self):
        oversized = self._artifact()
        oversized["strict_revision"]["content"] = (
            "\n" + "x" * 2_000 + "unique anchor" + "y" * 2_000 + "\n"
        )
        oversized["post_snapshot"]["content"] = "post anchor\n"
        self._rehash_artifact(oversized)
        oversized_input = self._input()
        oversized_input["entries"][0]["pre_anchor"] = "unique anchor"
        oversized_input["entries"][0]["post_anchor"] = "post anchor"
        with tempfile.TemporaryDirectory() as directory:
            self._write_artifact(directory, oversized)
            draft = prepare_draft(oversized_input, self.contract, self.frame, directory)
        snippet = draft["pairs"][0]["pre"]["snippet"]
        self.assertIn("unique anchor", snippet)
        self.assertLessEqual(len(snippet), 4_000)

    def test_curation_input_hash_drift_and_prepare_draft_cli(self):
        changed = self._input()
        changed["entries"][0]["notes"] = "Changed synthetic fixture note."
        with tempfile.TemporaryDirectory() as directory:
            discovery_directory = Path(directory) / "discovery"
            discovery_directory.mkdir()
            self._write_artifact(discovery_directory, self._artifact())
            first = prepare_draft(self._input(), self.contract, self.frame, discovery_directory)
            second = prepare_draft(changed, self.contract, self.frame, discovery_directory)
            self.assertNotEqual(
                first["curation_input_sha256"], second["curation_input_sha256"]
            )
            contract_path = Path(directory) / "contract.json"
            frame_path = Path(directory) / "sampling_frame.json"
            input_path = Path(directory) / "curation_input.json"
            output_path = Path(directory) / "draft.json"
            contract_path.write_bytes(CONTRACT_PATH.read_bytes())
            write_sampling_frame(frame_path, self.frame)
            input_path.write_text(json.dumps(self._input()), encoding="utf-8")
            self.assertEqual(
                manifest_main(
                    [
                        "prepare-draft",
                        "--contract",
                        str(contract_path),
                        "--sampling-frame",
                        str(frame_path),
                        "--input",
                        str(input_path),
                        "--discovery-directory",
                        str(discovery_directory),
                        "--output",
                        str(output_path),
                    ]
                ),
                0,
            )
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8")), first)


if __name__ == "__main__":
    unittest.main()
