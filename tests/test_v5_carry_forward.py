from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anachron.v5_carry_forward import V5CarryForwardError, derive_carry_forward
from anachron.v5_registry import canonical_json_bytes
from tools import materialize_v5_inputs as materializer
from tools.materialize_v5_inputs import V5MaterializationError, materialize


class V5CarryForwardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.evidence = self.root.parents[0] / "anachron-v4-evidence" / "source-audit-v3"

    def _v5_manifest(self, destination: Path) -> Path:
        paths = ["research/v5_measurement/case_registry.json"]
        registry = json.loads((self.root / "research/v5_measurement/case_registry.json").read_text(encoding="utf-8"))
        paths.extend(f"research/v5_measurement/{row['case_card']}" for row in registry["cases"])
        release = {
            "branch": "protocol/v5-successor",
            "branch_ref": "1" * 40,
            "commit": "1" * 40,
            "origin": "https://github.com/LesterALeong/anachron.git",
            "remote_branch": "1" * 40,
            "remote_tag_object": "2" * 40,
            "remote_tag_peeled": "1" * 40,
            "tag": "v5-measurement-protocol-v1",
            "tag_object": "2" * 40,
            "tag_peeled": "1" * 40,
        }
        value = {"governed_files": [{"path": path, "sha256": hashlib.sha256((self.root / path).read_bytes()).hexdigest(), "tag_blob_oid": f"{index:040x}"[-40:]} for index, path in enumerate(paths, start=1)], "governed_paths": paths, "release": release, "schema_version": "anachron-v5-source-manifest-v2"}
        destination.write_bytes(canonical_json_bytes(value))
        return destination

    def test_all_accepted_card_and_registry_bytes_are_identical(self) -> None:
        registry = self.root / "research/v4_measurement/case_registry.json"
        self.assertEqual(registry.read_bytes(), (self.root / "research/v5_measurement/case_registry.json").read_bytes())
        for row in json.loads(registry.read_text(encoding="utf-8"))["cases"]:
            self.assertEqual((self.root / "research/v4_measurement" / row["case_card"]).read_bytes(), (self.root / "research/v5_measurement" / row["case_card"]).read_bytes())

    def test_external_accepted_audit_and_manifest_bind_all_eight_cards(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            v5_manifest = self._v5_manifest(Path(temporary) / "v5-manifest.json")
            receipt = derive_carry_forward(self.root, accepted_audit=self.evidence / "A.json", v4_source_manifest=self.evidence / "M.json", v5_source_manifest=v5_manifest)
        self.assertEqual(receipt["case_count"], 8)
        self.assertEqual(receipt["v4_included_count"], 0)
        self.assertTrue(all(row["reason"] and row["v4_tag_blob_oid"] != row["v5_tag_blob_oid"] for row in receipt["cards"]))

    def test_audit_manifest_or_v5_tag_mutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audit = json.loads((self.evidence / "A.json").read_text(encoding="utf-8"))
            audit["case_audits"][0]["decision"] = "REJECT"
            forged_audit = root / "A.json"
            forged_audit.write_bytes(canonical_json_bytes(audit))
            v5_manifest = self._v5_manifest(root / "v5-manifest.json")
            with self.assertRaises(V5CarryForwardError):
                derive_carry_forward(self.root, accepted_audit=forged_audit, v4_source_manifest=self.evidence / "M.json", v5_source_manifest=v5_manifest)
            v5_value = json.loads(v5_manifest.read_text(encoding="utf-8"))
            v5_value["governed_files"][0]["sha256"] = "0" * 64
            v5_manifest.write_bytes(canonical_json_bytes(v5_value))
            with self.assertRaises(V5CarryForwardError):
                derive_carry_forward(self.root, accepted_audit=self.evidence / "A.json", v4_source_manifest=self.evidence / "M.json", v5_source_manifest=v5_manifest)
            source = json.loads((self.evidence / "M.json").read_text(encoding="utf-8"))
            source["release"]["tag_object"] = "0" * 40
            forged_source = root / "M.json"
            forged_source.write_bytes(canonical_json_bytes(source))
            self._v5_manifest(v5_manifest)
            with self.assertRaises(V5CarryForwardError):
                derive_carry_forward(self.root, accepted_audit=self.evidence / "A.json", v4_source_manifest=forged_source, v5_source_manifest=v5_manifest)

    def test_create_only_materialization_binds_runtime_schedule_and_carry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            v5_manifest = self._v5_manifest(temporary_root / "v5-manifest.json")
            identity = temporary_root / "identity.json"
            identity.write_bytes(canonical_json_bytes({"models": [{"digest": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e", "name": "qwen2.5:7b"}, {"digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8", "name": "qwen3:14b-q4_K_M"}], "version": "0.33.2"}))
            output = temporary_root / "materialized"
            with self.assertRaises(V5MaterializationError):
                materialize(self.root, accepted_audit=self.evidence / "A.json", v4_source_manifest=self.evidence / "M.json", v5_source_manifest=v5_manifest, runtime_identity=identity, output=output, evidence_output_root=temporary_root / "evidence")
            with self.assertRaises(V5MaterializationError):
                materialize(self.root, accepted_audit=self.evidence / "A.json", v4_source_manifest=self.evidence / "M.json", v5_source_manifest=v5_manifest, runtime_identity=identity, output=output)

    def test_materialization_stages_all_members_and_refuses_raced_final(self) -> None:
        runtime = {"models": [{"digest": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e", "name": "qwen2.5:7b"}, {"digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8", "name": "qwen3:14b-q4_K_M"}], "version": "0.33.2"}
        source = {"release": {"commit": "1" * 40, "tag": "v5-measurement-protocol-v1", "tag_object": "2" * 40}}
        carry = {"schema_version": "fixture", "v4_included_count": 0}
        hashes = {"tools/analyze_v5_measurement.py": "1" * 64, "tools/run_v5_recovery.py": "2" * 64, "tools/run_v5_conditional_campaign.ps1": "3" * 64}
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            (parent / "v5.json").write_bytes(canonical_json_bytes(source))
            output = parent / "materialized"
            with (
                patch.object(materializer, "_load_external", return_value=(runtime, canonical_json_bytes(runtime))),
                patch.object(materializer, "validate_source_manifest", return_value=source),
                patch.object(materializer, "derive_carry_forward", return_value=carry),
                patch.object(materializer, "validate_authority_contract", return_value=hashes),
            ):
                receipt = materialize(self.root, accepted_audit=parent / "audit.json", v4_source_manifest=parent / "v4.json", v5_source_manifest=parent / "v5.json", runtime_identity=parent / "runtime.json", output=output, evidence_output_root=parent / "evidence")
            self.assertEqual(receipt["schema_version"], "anachron-v5-materialization-receipt-v2")
            self.assertEqual({path.name for path in output.iterdir()}, {"carry_forward.json", "compatibility_plan.json", "full_plan.json", "materialization_receipt.json", "schedule.json", "source_manifest.json"})
            raced = parent / "raced"
            def create_final() -> None:
                raced.mkdir()
                (raced / "user.txt").write_text("preserve", encoding="utf-8")
            with (
                patch.object(materializer, "_load_external", return_value=(runtime, canonical_json_bytes(runtime))),
                patch.object(materializer, "validate_source_manifest", return_value=source),
                patch.object(materializer, "derive_carry_forward", return_value=carry),
                patch.object(materializer, "validate_authority_contract", return_value=hashes),
                self.assertRaises(V5MaterializationError),
            ):
                materialize(self.root, accepted_audit=parent / "audit.json", v4_source_manifest=parent / "v4.json", v5_source_manifest=parent / "v5.json", runtime_identity=parent / "runtime.json", output=raced, evidence_output_root=parent / "evidence-raced", before_publish=create_final)
            self.assertEqual((raced / "user.txt").read_text(encoding="utf-8"), "preserve")
            self.assertFalse(any(path.name.startswith(".raced.staging-") for path in parent.iterdir()))


if __name__ == "__main__":
    unittest.main()
