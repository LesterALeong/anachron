import copy
import json
import tempfile
import unittest
from pathlib import Path

from anachron.routes import load_contract
from anachron.routes.curation import prepare_draft
from anachron.routes.human_review import (
    _PERSONAL_CHECK_CERTIFICATION,
    HumanReviewError,
    apply_human_decisions,
    build_decision_template,
    build_review_packet,
)
from anachron.routes.manifest import canonical_json_sha256
from anachron.routes.manifest import main as manifest_main
from anachron.routes.sources import discover_topic, write_sampling_frame

ROOT = Path(__file__).parents[1]
CONTRACT_PATH = ROOT / "research" / "routes-v1" / "contract.json"
FIXTURES = Path(__file__).parent / "fixtures" / "routes"


class TestRoutesHumanReview(unittest.TestCase):
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

    def _input(self):
        return {
            "schema_version": "routes-v1-curation-input",
            "study_phase": "pilot",
            "rejected_topics": [
                {"title": topic["title"], "reason": "Synthetic fixture rejection."}
                for topic in self.contract["sampling"]["topics"]["pilot"]
                if topic["title"] != "YouTube"
            ],
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

    def _artifact(self):
        return discover_topic(
            self.contract,
            self.frame,
            phase="pilot",
            title="YouTube",
            fetcher=self._fetcher,
        ).to_dict()

    @staticmethod
    def _write_artifact(directory, artifact):
        (Path(directory) / "youtube.json").write_text(json.dumps(artifact), encoding="utf-8")

    def _draft(self, directory):
        self._write_artifact(directory, self._artifact())
        return prepare_draft(self._input(), self.contract, self.frame, directory)

    @staticmethod
    def _approved_decisions(draft):
        decisions = build_decision_template(draft)
        decisions["validator_id"] = "fixture-reviewer"
        decisions["validated_at"] = "2026-09-01T12:34:56Z"
        decisions["overall_certification"] = _PERSONAL_CHECK_CERTIFICATION
        for decision in decisions["pair_decisions"]:
            decision["decision"] = "PASS"
        for acknowledgement in decisions["rejection_acknowledgements"]:
            acknowledgement["acknowledged"] = True
        return decisions

    def test_packet_and_template_expose_all_required_human_review_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            draft = self._draft(directory)
        packet = build_review_packet(draft)
        template = build_decision_template(draft)
        pair = draft["pairs"][0]
        self.assertIn(pair["item_id"], packet)
        self.assertIn(pair["pre"]["revision_url"], packet)
        self.assertIn(pair["post"]["revision_url"], packet)
        self.assertIn(pair["question"], packet)
        self.assertIn("PASS", packet)
        self.assertIn("ACKNOWLEDGE REJECTION", packet)
        self.assertEqual(template["draft_sha256"], canonical_json_sha256(draft))
        self.assertEqual(template["pair_decisions"][0]["item_id"], pair["item_id"])
        self.assertEqual(template["validator_id"], "")
        self.assertEqual(template["overall_certification"], "")

    def test_apply_requires_explicit_complete_human_decisions_and_preserves_pending_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            draft = self._draft(directory)
            reviewed = apply_human_decisions(
                draft,
                self._approved_decisions(draft),
                self.contract,
                self.frame,
                self._input(),
                directory,
            )
        self.assertEqual(draft["pairs"][0]["curation"]["status"], "codex_prepared_pending_human")
        self.assertEqual(reviewed["pairs"][0]["curation"]["status"], "human_validated")
        self.assertEqual(reviewed["pairs"][0]["curation"]["human_validator_id"], "fixture-reviewer")
        self.assertEqual(reviewed["pairs"][0]["curation"]["human_validated_at"], "2026-09-01T12:34:56Z")

    def test_apply_rejects_partial_rejected_duplicate_and_tampered_decisions_or_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            draft = self._draft(directory)
            cases = {}
            partial = self._approved_decisions(draft)
            partial["pair_decisions"].pop()
            cases["partial"] = (partial, self._input(), draft)
            rejected = self._approved_decisions(draft)
            rejected["pair_decisions"][0]["decision"] = "REJECT"
            cases["reject"] = (rejected, self._input(), draft)
            duplicate = self._approved_decisions(draft)
            duplicate["pair_decisions"].append(copy.deepcopy(duplicate["pair_decisions"][0]))
            cases["duplicate"] = (duplicate, self._input(), draft)
            unacknowledged = self._approved_decisions(draft)
            unacknowledged["rejection_acknowledgements"][0]["acknowledged"] = False
            cases["unacknowledged"] = (unacknowledged, self._input(), draft)
            uncertified = self._approved_decisions(draft)
            uncertified["overall_certification"] = "yes"
            cases["uncertified"] = (uncertified, self._input(), draft)
            tampered_input = copy.deepcopy(self._input())
            tampered_input["entries"][0]["notes"] = "Changed after review packet generation."
            cases["input_changed"] = (self._approved_decisions(draft), tampered_input, draft)
            tampered_draft = copy.deepcopy(draft)
            tampered_draft["pairs"][0]["notes"] = "Changed after review packet generation."
            cases["draft_changed"] = (self._approved_decisions(tampered_draft), self._input(), tampered_draft)
            for name, (decisions, curation_input, candidate) in cases.items():
                with self.subTest(name=name), self.assertRaises(HumanReviewError):
                    apply_human_decisions(
                        candidate,
                        decisions,
                        self.contract,
                        self.frame,
                        curation_input,
                        directory,
                    )

    def test_cli_generates_blank_review_artifacts_and_writes_separate_reviewed_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            discovery_directory = root / "discovery"
            discovery_directory.mkdir()
            draft = self._draft(discovery_directory)
            contract_path = root / "contract.json"
            frame_path = root / "sampling_frame.json"
            input_path = root / "input.json"
            draft_path = root / "pending.json"
            packet_path = root / "review.md"
            template_path = root / "template.json"
            decisions_path = root / "decisions.json"
            reviewed_path = root / "reviewed.json"
            contract_path.write_bytes(CONTRACT_PATH.read_bytes())
            write_sampling_frame(frame_path, self.frame)
            input_path.write_text(json.dumps(self._input()), encoding="utf-8")
            draft_path.write_text(json.dumps(draft), encoding="utf-8")
            self.assertEqual(
                manifest_main(["review-packet", "--draft", str(draft_path), "--output", str(packet_path)]),
                0,
            )
            self.assertEqual(
                manifest_main(["decision-template", "--draft", str(draft_path), "--output", str(template_path)]),
                0,
            )
            template = json.loads(template_path.read_text(encoding="utf-8"))
            self.assertEqual(template["validator_id"], "")
            decisions_path.write_text(json.dumps(self._approved_decisions(draft)), encoding="utf-8")
            self.assertEqual(
                manifest_main(
                    [
                        "apply-human-decisions",
                        "--contract",
                        str(contract_path),
                        "--sampling-frame",
                        str(frame_path),
                        "--curation-input",
                        str(input_path),
                        "--discovery-directory",
                        str(discovery_directory),
                        "--draft",
                        str(draft_path),
                        "--decisions",
                        str(decisions_path),
                        "--output",
                        str(reviewed_path),
                    ]
                ),
                0,
            )
            self.assertEqual(json.loads(draft_path.read_text(encoding="utf-8")), draft)
            self.assertEqual(
                json.loads(reviewed_path.read_text(encoding="utf-8"))["pairs"][0]["curation"]["status"],
                "human_validated",
            )


if __name__ == "__main__":
    unittest.main()
