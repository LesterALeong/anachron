import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from anachron.routes.v2 import load_contract
from anachron.routes.v2.admission import canonical_json_sha256, write_create_only
from anachron.routes.v2.manifest import (
    ManifestValidationError,
    _mapping_input,
    _pair,
    validate_manifest,
)
from anachron.routes.v2.runner import (
    RunnerValidationError,
    _require_phase_prerequisite,
    derive_schedule,
)
from anachron.routes.v2.schema import ContractValidationError, phase_topics
from anachron.routes.v2.source_excerpt import build_excerpt_receipts

ROOT = Path(__file__).parents[1]


class TestRoutesV2PhaseGeneralization(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.contract = load_contract(ROOT / "research" / "routes-v2" / "contract.json")

    def tearDown(self):
        self.temporary.cleanup()

    def test_frame_has_exact_disjoint_phase_inventory_and_frozen_bounds(self):
        self.assertEqual(
            [len(phase_topics(self.contract, phase)) for phase in ("development", "pilot", "confirmatory")],
            [6, 18, 36],
        )
        self.assertEqual(self.contract["source_bounds"]["max_excerpt_utf8_bytes"], 4096)
        self.assertEqual(self.contract["answer_rules"]["labels"], ["post_only", "pre_only", "abstain", "other", "invalid_output"])

    def test_old_contract_schema_is_rejected(self):
        source = json.loads((ROOT / "research" / "routes-v2" / "contract.json").read_text(encoding="utf-8"))
        source.pop("source_bounds")
        with self.assertRaises(ContractValidationError):
            from anachron.routes.v2.schema import validate_contract

            validate_contract(source)

    @staticmethod
    def _revision(oldid, content, timestamp):
        return {
            "revision_id": oldid,
            "revision_url": f"https://en.wikipedia.org/w/index.php?title=Fixture&oldid={oldid}",
            "timestamp": timestamp,
            "content": content,
            "content_sha256": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }

    def _phase_manifest(self, phase, *, validate=True):
        """Build a valid sealed projection for schedule testing without execution authority."""
        frame = json.loads((ROOT / "research" / "routes-v2" / "sampling_frame.json").read_text(encoding="utf-8"))
        raw_root = Path(self.temporary.name)
        revalidations, excerpts, mapping_items = [], [], []
        predecessor_sha256 = None if phase == "development" else "sha256:" + "a" * 64
        for index, topic in enumerate(frame["phases"][phase]):
            item_id = f"routes-v2:{phase}:{index}"
            pre_anchor, post_anchor = f"PRE-{phase}-{index}", f"POST-{phase}-{index}"
            pre_content = f"{pre_anchor}\nOLD-{phase}-{index}"
            post_content = f"{post_anchor}\nVALUE-{phase}-{index}"
            raw = {
                "schema_version": "routes-v1-source-discovery",
                "title": topic["title"],
                "cutoff_year": topic["cutoff_year"],
                "strict_revision": self._revision(10_000 + index, pre_content, f"{topic['cutoff_year']}-12-30T00:00:00Z"),
                "post_snapshot": self._revision(20_000 + index, post_content, f"{topic['cutoff_year'] + 1}-01-02T00:00:00Z"),
            }
            raw_path = raw_root / f"{phase}-{index}.json"
            write_create_only(raw_path, raw)
            receipt = {
                "schema_version": "routes-v2-source-revalidation",
                "contract_sha256": canonical_json_sha256(self.contract),
                "sampling_frame_sha256": canonical_json_sha256(frame),
                "predecessor_evidence_sha256": predecessor_sha256,
                "study_phase": phase,
                "item_id": item_id,
                "title": topic["title"],
                "cutoff_year": topic["cutoff_year"],
                "raw_discovery_artifact_sha256": canonical_json_sha256(raw),
                "revalidator_code_closure_sha256": "sha256:" + "b" * 64,
                "pre": {
                    "oldid": str(10_000 + index),
                    "immutable_url": raw["strict_revision"]["revision_url"],
                    "timestamp": raw["strict_revision"]["timestamp"],
                    "content_sha256": raw["strict_revision"]["content_sha256"],
                },
                "post": {
                    "oldid": str(20_000 + index),
                    "immutable_url": raw["post_snapshot"]["revision_url"],
                    "timestamp": raw["post_snapshot"]["timestamp"],
                    "content_sha256": raw["post_snapshot"]["content_sha256"],
                },
            }
            receipt["receipt_sha256"] = canonical_json_sha256(receipt)
            item = {
                "item_id": item_id,
                "question": f"Which value is documented for {phase} item {index}?",
                "pre_anchor": pre_anchor,
                "post_anchor": post_anchor,
                "pre_aliases": [f"OLD-{phase}-{index}"],
                "post_aliases": [f"VALUE-{phase}-{index}"],
                "pre_opaque_citation_id": f"PRE-{phase}-{index}",
                "post_opaque_citation_id": f"POST-{phase}-{index}",
                "raw_discovery_artifact_sha256": receipt["raw_discovery_artifact_sha256"],
                "revalidation_receipt_sha256": receipt["receipt_sha256"],
            }
            pre_excerpt, post_excerpt = build_excerpt_receipts(
                contract=self.contract,
                revalidation_receipt=receipt,
                raw_artifact_path=raw_path,
                mapping_item=item,
            )
            revalidations.append(receipt)
            excerpts.extend((pre_excerpt, post_excerpt))
            mapping_items.append(item)
        source_mapping = {
            "schema_version": "routes-v2-source-mapping-input-v2",
            "study_phase": phase,
            "contract_sha256": canonical_json_sha256(self.contract),
            "sampling_frame_sha256": canonical_json_sha256(frame),
            "items": mapping_items,
        }
        mapping = _mapping_input(source_mapping, self.contract, frame, phase)
        revalidation_index = {item["item_id"]: item for item in revalidations}
        excerpt_index = {(item["item_id"], item["arm"]): item for item in excerpts}
        pairs = [
            _pair(mapping[item_id], revalidation_index[item_id], excerpt_index, self.contract)
            for item_id in sorted(mapping)
        ]
        source_gate = {
            "schema_version": "routes-v2-source-gate-receipt-v2",
            "study_phase": phase,
            "contract_sha256": canonical_json_sha256(self.contract),
            "sampling_frame_sha256": canonical_json_sha256(frame),
            "predecessor_evidence_sha256": predecessor_sha256,
            "pending_draft_sha256": "sha256:" + "c" * 64,
            "decisions_sha256": "sha256:" + "d" * 64,
            "source_mapping_sha256": canonical_json_sha256(source_mapping),
            "revalidation_receipts_sha256": canonical_json_sha256({key: value["receipt_sha256"] for key, value in sorted(revalidation_index.items())}),
            "excerpt_receipts_sha256": canonical_json_sha256({f"{key[0]}:{key[1]}": value["receipt_sha256"] for key, value in sorted(excerpt_index.items())}),
            "reviewed_pairs_sha256": canonical_json_sha256(pairs),
            "accepted_item_ids": sorted(mapping),
            "excluded_item_ids": [],
            "status": "PASS",
        }
        manifest = {
            "schema_version": "routes-v2-source-manifest-v2",
            "study_phase": phase,
            "contract_sha256": canonical_json_sha256(self.contract),
            "sampling_frame_sha256": canonical_json_sha256(frame),
            "predecessor_evidence": None,
            "pending_draft_sha256": source_gate["pending_draft_sha256"],
            "source_gate_receipt": source_gate,
            "source_mapping": source_mapping,
            "source_mapping_sha256": canonical_json_sha256(source_mapping),
            "revalidation_receipts": revalidations,
            "excerpt_receipts": excerpts,
            "pairs": pairs,
            "answer_rules": self.contract["answer_rules"],
            "answer_rules_sha256": canonical_json_sha256(self.contract["answer_rules"]),
        }
        return validate_manifest(manifest, self.contract) if validate else manifest

    def test_phase_schedules_have_exact_production_trajectory_counts(self):
        closure = {"schema_version": "routes-v2-code-closure", "closure_sha256": "sha256:" + "e" * 64}
        manifest = self._phase_manifest("development")
        freeze = {
            "schema_version": "routes-v2-freeze-receipt",
            "study_phase": "development",
            "commit": "fixture-commit",
            "tree": "fixture-tree",
            "branch": "fixture-branch",
            "remote": "fixture-remote",
            "closure_sha256": closure["closure_sha256"],
        }
        schedule = derive_schedule(manifest, self.contract, source_gate=manifest["source_gate_receipt"], freeze_receipt=freeze, closure_lock=closure)
        self.assertEqual(len(schedule["trajectories"]), 24)
        self.assertEqual({row["study_phase"] for row in schedule["trajectories"]}, {"development"})

    def test_forged_downstream_manifests_cannot_schedule_without_predecessor_evidence(self):
        closure = {"schema_version": "routes-v2-code-closure", "closure_sha256": "sha256:" + "e" * 64}
        for phase, expected in (("pilot", 108), ("confirmatory", 432)):
            with self.subTest(phase=phase):
                manifest = self._phase_manifest(phase, validate=False)
                self.assertEqual(
                    len(manifest["pairs"])
                    * len(self.contract["evaluation"][phase]["models"])
                    * len(self.contract["conditions"])
                    * len(self.contract["execution"]["seeds"]),
                    expected,
                )
                freeze = {
                    "schema_version": "routes-v2-freeze-receipt",
                    "study_phase": phase,
                    "commit": "fixture-commit",
                    "tree": "fixture-tree",
                    "branch": "fixture-branch",
                    "remote": "fixture-remote",
                    "closure_sha256": closure["closure_sha256"],
                }
                with self.assertRaises(ManifestValidationError):
                    derive_schedule(manifest, self.contract, source_gate=manifest["source_gate_receipt"], freeze_receipt=freeze, closure_lock=closure)

    def test_downstream_execution_requires_the_correct_positive_predecessor(self):
        with self.assertRaises(RunnerValidationError):
            _require_phase_prerequisite("pilot", None)
        with self.assertRaises(RunnerValidationError):
            _require_phase_prerequisite("confirmatory", None)


if __name__ == "__main__":
    unittest.main()
