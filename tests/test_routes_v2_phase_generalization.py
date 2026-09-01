import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from anachron.routes.v2 import load_contract
from anachron.routes.v2.admission import (
    canonical_json_sha256,
    revalidate_raw_source,
    write_create_only,
)
from anachron.routes.v2.analysis import AnalysisValidationError, build_audit_plan
from anachron.routes.v2.manifest import (
    ManifestValidationError,
    prepare_pending_draft,
    seal_manifest,
    validate_manifest,
)
from anachron.routes.v2.runner import _require_phase_prerequisite, _schedule_rows
from anachron.routes.v2.sources import validate_sampling_frame

ROOT = Path(__file__).parents[1]
CONTRACT_PATH = ROOT / "research" / "routes-v2" / "contract.json"
FRAME_PATH = ROOT / "research" / "routes-v2" / "sampling_frame.json"


class TestRoutesV2PhaseGeneralization(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.contract = load_contract(CONTRACT_PATH)
        self.frame = json.loads(FRAME_PATH.read_text(encoding="utf-8"))

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _revision(oldid, content, timestamp):
        return {
            "revision_id": oldid,
            "revision_url": f"https://en.wikipedia.org/w/index.php?title=Fixture&oldid={oldid}",
            "timestamp": timestamp,
            "content": content,
            "content_sha256": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }

    def _manifest(self, phase):
        items = self.frame["phases"][phase]
        phase_root = self.root / phase
        paths, mappings = [], []
        for index, topic in enumerate(items):
            pre, post = f"OLD-{phase}-{index}", f"VALUE-{phase}-{index}"
            raw_path = phase_root / f"raw-{index}.json"
            receipt_path = phase_root / f"receipt-{index}.json"
            write_create_only(raw_path, {
                "schema_version": "routes-v1-source-discovery", "title": topic["title"], "cutoff_year": topic["cutoff_year"],
                "strict_revision": self._revision(10000 + index, pre, f"{topic['cutoff_year']}-12-30T00:00:00Z"),
                "post_snapshot": self._revision(20000 + index, post, f"{topic['cutoff_year'] + 1}-01-02T00:00:00Z"),
            })
            item_id = f"routes-v2:{phase}:{index}"
            revalidate_raw_source(contract_path=CONTRACT_PATH, sampling_frame_path=FRAME_PATH, raw_artifact_path=raw_path, phase=phase, item_id=item_id, output_path=receipt_path)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            paths.append(receipt_path)
            mappings.append({"item_id": item_id, "question": f"Question {phase} {index}", "pre_content": pre, "post_content": post, "pre_opaque_citation_id": f"PRE{index}", "opaque_citation_id": f"DOC{index}", "raw_discovery_artifact_sha256": receipt["raw_discovery_artifact_sha256"]})
        mapping_path = phase_root / "mapping.json"
        write_create_only(mapping_path, {"schema_version": "routes-v2-source-mapping-input", "study_phase": phase, "contract_sha256": canonical_json_sha256(self.contract), "sampling_frame_sha256": canonical_json_sha256(self.frame), "items": mappings})
        draft = prepare_pending_draft(phase=phase, contract_path=CONTRACT_PATH, sampling_frame_path=FRAME_PATH, revalidation_receipt_paths=paths, source_mapping_input_path=mapping_path, output_path=phase_root / "draft.json")
        decisions = {"schema_version": "routes-v2-source-decisions", "study_phase": phase, "pending_draft_sha256": canonical_json_sha256(draft), "validator_id": "fixture-human", "decisions": [{"item_id": pair["item_id"], "decision": "PASS", "reason": "fixture"} for pair in draft["pairs"]]}
        return seal_manifest(phase=phase, contract=self.contract, sampling_frame=self.frame, draft=draft, source_decisions=decisions, revalidation_receipt_paths=paths)

    def test_frame_has_the_exact_disjoint_6_18_36_inventory(self):
        validate_sampling_frame(self.frame, self.contract)
        self.assertEqual([len(self.frame["phases"][phase]) for phase in ("development", "pilot", "confirmatory")], [6, 18, 36])
        identities = {(item["title"], item["cutoff_year"]) for phase in self.frame["phases"].values() for item in phase}
        self.assertEqual(len(identities), 60)
        self.assertEqual(self.frame["phases"]["confirmatory"][24]["title"], "Beyonc\u00e9")

    def test_frame_rejects_parent_artifact_pin_membership_and_unicode_drift(self):
        parent = self.root / "research" / "routes-v1"
        parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "research" / "routes-v1" / "sampling_frame.json", parent / "sampling_frame.json")
        validate_sampling_frame(self.frame, self.contract, repository=self.root)
        tampered_parent = json.loads((parent / "sampling_frame.json").read_text(encoding="utf-8"))
        tampered_parent["topics"][0]["title"] = "different"
        (parent / "sampling_frame.json").write_text(json.dumps(tampered_parent), encoding="utf-8")
        with self.assertRaises(ValueError):
            validate_sampling_frame(self.frame, self.contract, repository=self.root)
        shutil.copyfile(ROOT / "research" / "routes-v1" / "sampling_frame.json", parent / "sampling_frame.json")
        wrong_pin = json.loads(json.dumps(self.frame))
        wrong_pin["parent_artifact"]["huggingface_etag"] = "wrong"
        with self.assertRaises(ValueError):
            validate_sampling_frame(wrong_pin, self.contract, repository=self.root)
        wrong_membership = json.loads(json.dumps(self.frame))
        wrong_membership["phases"]["development"][0]["parent_row_index"] = 1
        with self.assertRaises(ValueError):
            validate_sampling_frame(wrong_membership, self.contract, repository=self.root)

    def test_phase_manifests_and_schedules_cannot_swap_or_reuse_items(self):
        manifest = self._manifest("development")
        self.assertEqual(len(_schedule_rows(manifest, self.contract)), 24)
        self.assertEqual({pair["study_phase"] for pair in manifest["pairs"]}, {"development"})
        self.assertEqual({row["study_phase"] for row in _schedule_rows(manifest, self.contract)}, {"development"})
        swapped = json.loads(json.dumps(manifest))
        swapped["study_phase"] = "confirmatory"
        with self.assertRaises(ManifestValidationError):
            validate_manifest(swapped, self.contract)
        with self.assertRaises(ValueError):
            self._manifest("pilot")

    def test_audit_populations_are_phase_local_and_downstream_gate_fails_closed(self):
        for phase, expected in (("pilot", 36), ("confirmatory", 144)):
            spec = self.contract["evaluation"][phase]
            ledger = [{"trajectory_id": f"{phase}-{topic}-{model}-{condition}", "study_phase": phase, "topic_id": f"topic-{topic}", "condition": condition, "model_id": model, "seed": 17, "status": "ok", "parsed_answer": "VALUE", "sanitized_payload": "VALUE", "response_sha256": "sha256:" + "a" * 64, "machine_label": "post_only"} for topic in range(spec["topic_count"]) for model in spec["models"] for condition in self.contract["evaluation"]["primary_arms"]]
            with self.assertRaises(AnalysisValidationError):
                build_audit_plan(self.contract, phase, ledger)
            with self.assertRaises(AnalysisValidationError):
                build_audit_plan(self.contract, phase, [{**ledger[0], "study_phase": "development"}, *ledger[1:]])
        with self.assertRaises(ValueError):
            _require_phase_prerequisite("pilot", None)
        with self.assertRaises(ValueError):
            _require_phase_prerequisite("confirmatory", None)


if __name__ == "__main__":
    unittest.main()
