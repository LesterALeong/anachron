import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from anachron.routes.v2 import ContractValidationError, load_contract
from anachron.routes.v2.admission import (
    canonical_json_sha256,
    revalidate_raw_source,
    write_create_only,
)
from anachron.routes.v2.human_review import decision_template
from anachron.routes.v2.manifest import (
    ManifestValidationError,
    prepare_pending_draft,
    seal_manifest,
    source_gate_receipt,
    validate_manifest,
)
from anachron.routes.v2.retrieval import (
    RetrievalValidationError,
    delivery_packet,
    primary_packets,
)
from anachron.routes.v2.runner import schedule_development

ROOT = Path(__file__).parents[1]
CONTRACT_PATH = ROOT / "research" / "routes-v2" / "contract.json"
FRAME_PATH = ROOT / "research" / "routes-v2" / "sampling_frame.json"
V1_CONTRACT_PATH = ROOT / "research" / "routes-v1" / "contract.json"


class TestRoutesV2Core(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.contract = load_contract(CONTRACT_PATH)
        self.frame = json.loads(FRAME_PATH.read_text(encoding="utf-8"))
        self.receipt_paths, mapping = [], []
        for index, topic in enumerate(self.contract["development"]["topics"]):
            pre, post = f"Earlier evidence for item {index} is OLD-{index}.", f"Evidence for item {index} is VALUE-{index}."
            raw = {
                "schema_version": "routes-v1-source-discovery", "title": topic["title"], "cutoff_year": topic["cutoff_year"],
                "strict_revision": self._revision(900 + index, pre, f"{topic['cutoff_year']}-12-30T00:00:00Z"),
                "post_snapshot": self._revision(1000 + index, post, f"{topic['cutoff_year'] + 1}-01-02T00:00:00Z"),
            }
            raw_path, receipt_path = self.directory / f"raw-{index}.json", self.directory / f"receipt-{index}.json"
            write_create_only(raw_path, raw)
            revalidate_raw_source(contract_path=CONTRACT_PATH, sampling_frame_path=FRAME_PATH, raw_artifact_path=raw_path, phase="development", item_id=f"routes-v2:development:{index}", output_path=receipt_path)
            self.receipt_paths.append(receipt_path)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            mapping.append({"item_id": f"routes-v2:development:{index}", "question": f"What value is documented for item {index}?", "pre_content": pre, "post_content": post, "pre_opaque_citation_id": f"PRE{index:02d}", "opaque_citation_id": f"DOC{index:02d}", "raw_discovery_artifact_sha256": receipt["raw_discovery_artifact_sha256"]})
        self.mapping_path = self.directory / "mapping.json"
        self.mapping = {"schema_version": "routes-v2-source-mapping-input", "study_phase": "development", "contract_sha256": canonical_json_sha256(self.contract), "sampling_frame_sha256": canonical_json_sha256(self.frame), "items": mapping}
        write_create_only(self.mapping_path, self.mapping)
        self.draft_path = self.directory / "draft.json"
        self.draft = prepare_pending_draft(phase="development", contract_path=CONTRACT_PATH, sampling_frame_path=FRAME_PATH, revalidation_receipt_paths=self.receipt_paths, source_mapping_input_path=self.mapping_path, output_path=self.draft_path)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _revision(oldid, content, timestamp):
        return {"revision_id": oldid, "revision_url": f"https://en.wikipedia.org/w/index.php?title=Fixture&oldid={oldid}", "timestamp": timestamp, "content": content, "content_sha256": "sha256:" + hashlib.sha256(content.encode()).hexdigest()}

    def _decisions(self, decision="PASS"):
        return {"schema_version": "routes-v2-source-decisions", "study_phase": "development", "pending_draft_sha256": canonical_json_sha256(self.draft), "validator_id": "fixture-human", "decisions": [{"item_id": pair["item_id"], "decision": decision, "reason": "fixture"} for pair in self.draft["pairs"]]}

    def _manifest(self):
        return seal_manifest(phase="development", contract=self.contract, sampling_frame=self.frame, draft=self.draft, source_decisions=self._decisions(), revalidation_receipt_paths=self.receipt_paths)

    def test_contract_freezes_the_v2_design(self):
        self.assertEqual(len(self.contract["development"]["topics"]), 6)
        with self.assertRaises(ContractValidationError):
            load_contract(V1_CONTRACT_PATH)

    def test_pending_draft_binds_every_receipt_and_rejects_source_tampering(self):
        self.assertEqual(len(self.draft["revalidation_receipts"]), 6)
        changed = copy.deepcopy(self.draft)
        changed["pairs"][0]["post_content"] += " tampered"
        with self.assertRaises(ManifestValidationError):
            seal_manifest(phase="development", contract=self.contract, sampling_frame=self.frame, draft=changed, source_decisions=self._decisions(), revalidation_receipt_paths=self.receipt_paths)

    def test_wrong_contract_frame_or_raw_receipt_binding_is_rejected(self):
        for field, replacement in (("contract_sha256", "sha256:" + "0" * 64), ("sampling_frame_sha256", "sha256:" + "1" * 64), ("raw_discovery_artifact_sha256", "sha256:" + "2" * 64)):
            receipt = json.loads(self.receipt_paths[0].read_text(encoding="utf-8"))
            receipt[field] = replacement
            receipt["receipt_sha256"] = canonical_json_sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})
            replacement_path = self.directory / f"wrong-{field}.json"
            write_create_only(replacement_path, receipt)
            paths = [replacement_path, *self.receipt_paths[1:]]
            with self.assertRaises(ManifestValidationError):
                prepare_pending_draft(phase="development", contract_path=CONTRACT_PATH, sampling_frame_path=FRAME_PATH, revalidation_receipt_paths=paths, source_mapping_input_path=self.mapping_path, output_path=self.directory / f"wrong-{field}-draft.json")
        changed = copy.deepcopy(self.draft)
        changed["pairs"][0]["source_provenance"]["post_revision_id"] = "999999"
        with self.assertRaises(ManifestValidationError):
            seal_manifest(phase="development", contract=self.contract, sampling_frame=self.frame, draft=changed, source_decisions=self._decisions(), revalidation_receipt_paths=self.receipt_paths)
        changed = copy.deepcopy(self.draft)
        changed["pairs"][0]["human_validated"] = True
        with self.assertRaises(ManifestValidationError):
            seal_manifest(phase="development", contract=self.contract, sampling_frame=self.frame, draft=changed, source_decisions=self._decisions(), revalidation_receipt_paths=self.receipt_paths)

    def test_decisions_bind_exact_draft_and_reject_produces_no_manifest(self):
        template = decision_template(self.draft, self.contract)
        self.assertEqual(template["pending_draft_sha256"], canonical_json_sha256(self.draft))
        receipts = {receipt["item_id"]: receipt for receipt in self.draft["revalidation_receipts"]}
        failed = source_gate_receipt(draft=self.draft, source_decisions=self._decisions("REJECT"), contract=self.contract, sampling_frame=self.frame, revalidation_receipts=receipts, phase="development")
        self.assertEqual(failed["status"], "FAIL")
        self.assertEqual(len(failed["excluded_item_ids"]), 6)
        with self.assertRaises(ManifestValidationError):
            seal_manifest(phase="development", contract=self.contract, sampling_frame=self.frame, draft=self.draft, source_decisions=self._decisions("REJECT"), revalidation_receipt_paths=self.receipt_paths)
        stale = self._decisions()
        stale["pending_draft_sha256"] = "sha256:" + "0" * 64
        with self.assertRaises(ManifestValidationError):
            seal_manifest(phase="development", contract=self.contract, sampling_frame=self.frame, draft=self.draft, source_decisions=stale, revalidation_receipt_paths=self.receipt_paths)

    def test_manifest_and_delivery_require_all_six_receipt_derived_pairs(self):
        manifest = self._manifest()
        validate_manifest(manifest, self.contract)
        truthful, misdated = primary_packets(manifest, self.contract, "routes-v2:development:0")
        self.assertEqual(truthful["document"]["content"], misdated["document"]["content"])
        self.assertNotEqual(truthful["document"]["presented_document_date"], misdated["document"]["presented_document_date"])
        with self.assertRaises(RetrievalValidationError):
            delivery_packet({"schema_version": "routes-v1-source-manifest"}, self.contract, item_id="routes-v2:development:0", condition="post_truthful")
        self.assertEqual(len(schedule_development(manifest, self.contract)), 24)


if __name__ == "__main__":
    unittest.main()
