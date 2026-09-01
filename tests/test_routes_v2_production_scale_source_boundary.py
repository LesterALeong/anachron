import hashlib
import inspect
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from anachron.routes.v2 import load_contract
from anachron.routes.v2.admission import (
    canonical_json_sha256,
    revalidate_raw_source,
    write_create_only,
)
from anachron.routes.v2.human_review import decision_template
from anachron.routes.v2.manifest import prepare_pending_draft, seal_manifest
from anachron.routes.v2.retrieval import delivery_packet
from anachron.routes.v2.source_excerpt import build_excerpt_receipts

ROOT = Path(__file__).parents[1]
CONTRACT_PATH = ROOT / "research" / "routes-v2" / "contract.json"
FRAME_PATH = ROOT / "research" / "routes-v2" / "sampling_frame.json"
MAX_MODEL_VISIBLE_UTF8_BYTES = 4_096
PRODUCTION_SCALE_REVISION_BYTES = 636 * 1_024


class TestRoutesV2ProductionScaleSourceBoundary(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.repository = self.directory / "repository"
        shutil.copytree(ROOT / "research", self.repository / "research")
        shutil.copy2(ROOT / ".gitignore", self.repository / ".gitignore")
        subprocess.run(["git", "init", "-q", str(self.repository)], check=True)
        self.contract_path = self.repository / "research" / "routes-v2" / "contract.json"
        self.frame_path = self.repository / "research" / "routes-v2" / "sampling_frame.json"
        self.contract = load_contract(self.contract_path)
        self.frame = json.loads(self.frame_path.read_text(encoding="utf-8"))

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _revision(oldid, content, timestamp):
        return {
            "revision_id": oldid,
            "revision_url": f"https://en.wikipedia.org/w/index.php?title=Fixture&oldid={oldid}",
            "timestamp": timestamp,
            "content": content,
            "content_sha256": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }

    @staticmethod
    def _production_scale_content(anchor, answer, fill):
        prefix = f"{anchor}\n{answer}\n"
        return prefix + fill * (PRODUCTION_SCALE_REVISION_BYTES - len(prefix.encode("utf-8")))

    def _prepare_draft(self, *, production_scale):
        receipt_paths, excerpt_paths, mappings = [], [], []
        for index, topic in enumerate(self.contract["development"]["topics"]):
            pre_anchor, post_anchor = f"PRE-UNIQUE-ANCHOR-{index}", f"POST-UNIQUE-ANCHOR-{index}"
            pre = self._production_scale_content(pre_anchor, f"OLD-{index}", "P") if production_scale else f"{pre_anchor}\nOLD-{index}"
            post = self._production_scale_content(post_anchor, f"VALUE-{index}", "Q") if production_scale else f"{post_anchor}\nVALUE-{index}"
            raw_path = self.repository / "research" / "routes-v2" / "artifacts" / "raw" / "development" / f"routes-v2-development-{index}.json"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path = self.directory / f"receipt-{index}.json"
            write_create_only(raw_path, {"schema_version": "routes-v1-source-discovery", "title": topic["title"], "cutoff_year": topic["cutoff_year"], "strict_revision": self._revision(10_000 + index, pre, f"{topic['cutoff_year']}-12-30T00:00:00Z"), "post_snapshot": self._revision(20_000 + index, post, f"{topic['cutoff_year'] + 1}-01-02T00:00:00Z")})
            receipt = revalidate_raw_source(contract_path=self.contract_path, sampling_frame_path=self.frame_path, raw_artifact_path=raw_path, phase="development", item_id=f"routes-v2:development:{index}", output_path=receipt_path)
            mapping = {"item_id": f"routes-v2:development:{index}", "question": f"Which value is documented for item {index}?", "pre_anchor": pre_anchor, "post_anchor": post_anchor, "pre_aliases": [f"OLD-{index}"], "post_aliases": [f"VALUE-{index}", f"ALTERNATE-{index}"], "pre_opaque_citation_id": f"PRE{index:02d}", "post_opaque_citation_id": f"POST{index:02d}", "raw_discovery_artifact_sha256": receipt["raw_discovery_artifact_sha256"], "revalidation_receipt_sha256": receipt["receipt_sha256"]}
            pre_excerpt, post_excerpt = build_excerpt_receipts(contract=self.contract, revalidation_receipt=receipt, raw_artifact_path=raw_path, mapping_item=mapping)
            for arm, excerpt in (("pre", pre_excerpt), ("post", post_excerpt)):
                path = self.directory / f"{index}.{arm}.excerpt.json"
                write_create_only(path, excerpt)
                excerpt_paths.append(path)
            receipt_paths.append(receipt_path)
            mappings.append(mapping)
        mapping_path = self.directory / "mapping.json"
        write_create_only(mapping_path, {"schema_version": "routes-v2-source-mapping-input-v2", "study_phase": "development", "contract_sha256": canonical_json_sha256(self.contract), "sampling_frame_sha256": canonical_json_sha256(self.frame), "items": mappings})
        draft = prepare_pending_draft(phase="development", repository=self.repository, contract_path=self.contract_path, sampling_frame_path=self.frame_path, revalidation_receipt_paths=receipt_paths, excerpt_receipt_paths=excerpt_paths, source_mapping_input_path=mapping_path, output_path=self.directory / "draft.json")
        decisions = decision_template(draft, self.contract)
        decisions["validator_id"] = "fixture-human"
        decisions["reviewed_at"] = "2026-09-01T00:00:00Z"
        for decision in decisions["decisions"]:
            decision["decision"] = "PASS"
            decision["reason"] = "fixture"
        manifest = seal_manifest(phase="development", repository=self.repository, contract=self.contract, sampling_frame=self.frame, draft=draft, source_decisions=decisions, revalidation_receipt_paths=receipt_paths, excerpt_receipt_paths=excerpt_paths)
        return draft, manifest

    def _write_preflight_mapping(self):
        mappings = []
        for index, topic in enumerate(self.contract["development"]["topics"]):
            pre_anchor, post_anchor = f"PREFLIGHT-PRE-{index}", f"PREFLIGHT-POST-{index}"
            raw_path = self.repository / "research" / "routes-v2" / "artifacts" / "raw" / "development" / f"routes-v2-development-{index}.json"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            write_create_only(raw_path, {
                "schema_version": "routes-v1-source-discovery",
                "title": topic["title"],
                "cutoff_year": topic["cutoff_year"],
                "strict_revision": self._revision(30_000 + index, f"{pre_anchor}\nOLD-PREFLIGHT-{index}", f"{topic['cutoff_year']}-12-30T00:00:00Z"),
                "post_snapshot": self._revision(40_000 + index, f"{post_anchor}\nVALUE-PREFLIGHT-{index}", f"{topic['cutoff_year'] + 1}-01-02T00:00:00Z"),
            })
            receipt = revalidate_raw_source(
                contract_path=self.contract_path,
                sampling_frame_path=self.frame_path,
                raw_artifact_path=raw_path,
                phase="development",
                item_id=f"routes-v2:development:{index}",
                output_path=self.directory / f"preflight-{index}.receipt.json",
            )
            mappings.append({
                "item_id": f"routes-v2:development:{index}",
                "question": f"Which preflight value is documented for item {index}?",
                "pre_anchor": pre_anchor,
                "post_anchor": post_anchor,
                "pre_aliases": [f"OLD-PREFLIGHT-{index}"],
                "post_aliases": [f"VALUE-PREFLIGHT-{index}"],
                "pre_opaque_citation_id": f"PREFLIGHT-PRE-{index}",
                "post_opaque_citation_id": f"PREFLIGHT-POST-{index}",
                "raw_discovery_artifact_sha256": receipt["raw_discovery_artifact_sha256"],
                "revalidation_receipt_sha256": receipt["receipt_sha256"],
            })
        mapping_path = self.directory / "preflight.mapping.json"
        write_create_only(mapping_path, {
            "schema_version": "routes-v2-source-mapping-input-v2",
            "study_phase": "development",
            "contract_sha256": canonical_json_sha256(self.contract),
            "sampling_frame_sha256": canonical_json_sha256(self.frame),
            "items": mappings,
        })
        return mapping_path

    def test_pending_draft_and_model_visible_packets_cap_raw_revision_content(self):
        draft, manifest = self._prepare_draft(production_scale=True)
        draft_sizes = {pair["item_id"]: {"pre": pair["pre_excerpt"]["utf8_bytes"], "post": pair["post_excerpt"]["utf8_bytes"]} for pair in draft["pairs"]}
        model_visible_sizes = {pair["item_id"]: len(delivery_packet(manifest, self.contract, item_id=pair["item_id"], condition="post_truthful")["document"]["content"].encode("utf-8")) for pair in manifest["pairs"]}
        self.assertTrue(all(size <= MAX_MODEL_VISIBLE_UTF8_BYTES for side_sizes in draft_sizes.values() for size in side_sizes.values()) and all(size <= MAX_MODEL_VISIBLE_UTF8_BYTES for size in model_visible_sizes.values()), f"draft_utf8_bytes={draft_sizes}; model_visible_utf8_bytes={model_visible_sizes}")
        self.assertTrue(all(size == MAX_MODEL_VISIBLE_UTF8_BYTES for size in model_visible_sizes.values()))

    def test_execution_has_no_caller_supplied_scoring_alias_parameter(self):
        from anachron.routes.v2.admission import open_validated_execution
        from anachron.routes.v2.runner import ExecutionSession

        self.assertNotIn("sealed_aliases", inspect.signature(open_validated_execution).parameters)
        self.assertNotIn("expected_answers", inspect.signature(ExecutionSession.dispatch_next).parameters)
        self.assertNotIn("raw_artifact_paths", inspect.signature(prepare_pending_draft).parameters)
        self.assertNotIn("raw_artifact_paths", inspect.signature(seal_manifest).parameters)

    def test_offline_preflight_reads_six_sources_without_creating_a_decision(self):
        from tools.validate_routes_v2_source_construction import (
            validate_source_construction,
        )

        signature = inspect.signature(validate_source_construction)
        self.assertEqual(tuple(signature.parameters), ("repository", "mapping_path"))
        self.assertNotIn("raw_directory", signature.parameters)

    def test_offline_preflight_executes_against_fixed_ignored_sources_without_outputs(self):
        from tools.validate_routes_v2_source_construction import (
            validate_source_construction,
        )

        mapping_path = self._write_preflight_mapping()
        raw_root = self.repository / "research" / "routes-v2" / "artifacts" / "raw" / "development"
        self.assertEqual(
            {path.name for path in raw_root.iterdir()},
            {f"routes-v2-development-{index}.json" for index in range(6)},
        )
        before = {path.relative_to(self.repository) for path in self.repository.rglob("*")}
        result = validate_source_construction(self.repository, mapping_path)
        self.assertEqual(result["phase"], "development")
        self.assertFalse(result["decision_created"])
        self.assertEqual(len(result["excerpt_receipt_sha256s"]), 12)
        self.assertEqual(len(set(result["excerpt_receipt_sha256s"])), 12)
        self.assertEqual({path.relative_to(self.repository) for path in self.repository.rglob("*")}, before)


if __name__ == "__main__":
    unittest.main()
