from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anachron.v5_carry_forward import V5CarryForwardError, derive_carry_forward
from anachron.v5_contract import V5_PROTOCOL_TAG
from anachron.v5_registry import canonical_json_bytes
from tools import materialize_v5_inputs as materializer
from tools.materialize_v5_inputs import V5MaterializationError, materialize


class V5CarryForwardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def _v4_fixture(self, destination: Path) -> tuple[Path, Path]:
        registry_path = "research/v4_measurement/case_registry.json"
        registry = json.loads((self.root / registry_path).read_text(encoding="utf-8"))
        governed_paths = [registry_path]
        governed_paths.extend(
            f"research/v4_measurement/{row['case_card']}"
            for row in registry["cases"]
        )
        governed_files = [
            {
                "path": path,
                "sha256": hashlib.sha256((self.root / path).read_bytes()).hexdigest(),
                "tag_blob_oid": f"{index:040x}",
            }
            for index, path in enumerate(governed_paths, start=101)
        ]
        manifest = {
            "governed_files": governed_files,
            "governed_paths": governed_paths,
            "release": {
                "tag": "v4-measurement-protocol-v3",
                "tag_object": "f981b1b50ee47566b71e4f44ff597ef76cb71f4b",
                "tag_peeled": "499f656e98f85c97dbe21e5f0773a2b0bb9782c8",
            },
            "schema_version": "anachron-v4-source-manifest-v1",
        }
        manifest_path = destination / "M.json"
        manifest_raw = canonical_json_bytes(manifest)
        manifest_path.write_bytes(manifest_raw)
        bindings = {row["path"]: row for row in governed_files}
        registry_binding = bindings[registry_path]
        audit = {
            "attestation": "Self-contained v4 carry-forward fixture.",
            "audited_at_utc": "2026-09-06T00:00:00Z",
            "audited_by": "Fixture",
            "case_audits": [
                {
                    "case_id": row["id"],
                    "decision": "ACCEPT",
                    "reason": f"Fixture acceptance for {row['id']}.",
                    "reviewed_at_utc": "2026-09-06T00:00:00Z",
                    "tag_blob_oid": bindings[
                        f"research/v4_measurement/{row['case_card']}"
                    ]["tag_blob_oid"],
                    "tag_blob_sha256": bindings[
                        f"research/v4_measurement/{row['case_card']}"
                    ]["sha256"],
                }
                for row in registry["cases"]
            ],
            "comparison_projection_sha256": "0" * 64,
            "decision": "ACCEPT",
            "registry_sha256": registry_binding["sha256"],
            "registry_tag_blob_oid": registry_binding["tag_blob_oid"],
            "registry_tag_blob_sha256": registry_binding["sha256"],
            "schema_version": "anachron-v4-source-audit-v1",
            "source_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "v4_protocol_commit": "499f656e98f85c97dbe21e5f0773a2b0bb9782c8",
            "v4_protocol_tag": "v4-measurement-protocol-v3",
            "v4_protocol_tag_object": "f981b1b50ee47566b71e4f44ff597ef76cb71f4b",
        }
        audit_path = destination / "A.json"
        audit_path.write_bytes(canonical_json_bytes(audit))
        return audit_path, manifest_path

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
            "tag": V5_PROTOCOL_TAG,
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

    def test_self_contained_accepted_audit_and_manifest_bind_all_eight_cards(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            audit, v4_manifest = self._v4_fixture(temporary_root)
            v5_manifest = self._v5_manifest(temporary_root / "v5-manifest.json")
            receipt = derive_carry_forward(self.root, accepted_audit=audit, v4_source_manifest=v4_manifest, v5_source_manifest=v5_manifest)
        self.assertEqual(receipt["case_count"], 8)
        self.assertEqual(receipt["v4_included_count"], 0)
        self.assertTrue(all(row["reason"] and row["v4_tag_blob_oid"] != row["v5_tag_blob_oid"] for row in receipt["cards"]))

    def test_audit_manifest_or_v5_tag_mutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            accepted_audit, v4_manifest = self._v4_fixture(root)
            audit = json.loads(accepted_audit.read_text(encoding="utf-8"))
            audit["case_audits"][0]["decision"] = "REJECT"
            forged_audit = root / "A.json"
            forged_audit.write_bytes(canonical_json_bytes(audit))
            v5_manifest = self._v5_manifest(root / "v5-manifest.json")
            with self.assertRaises(V5CarryForwardError):
                derive_carry_forward(self.root, accepted_audit=forged_audit, v4_source_manifest=v4_manifest, v5_source_manifest=v5_manifest)
            v5_value = json.loads(v5_manifest.read_text(encoding="utf-8"))
            v5_value["governed_files"][0]["sha256"] = "0" * 64
            v5_manifest.write_bytes(canonical_json_bytes(v5_value))
            with self.assertRaises(V5CarryForwardError):
                derive_carry_forward(self.root, accepted_audit=accepted_audit, v4_source_manifest=v4_manifest, v5_source_manifest=v5_manifest)
            source = json.loads(v4_manifest.read_text(encoding="utf-8"))
            source["release"]["tag_object"] = "0" * 40
            forged_source = root / "M.json"
            forged_source.write_bytes(canonical_json_bytes(source))
            self._v5_manifest(v5_manifest)
            with self.assertRaises(V5CarryForwardError):
                derive_carry_forward(self.root, accepted_audit=accepted_audit, v4_source_manifest=forged_source, v5_source_manifest=v5_manifest)

    def test_create_only_materialization_binds_runtime_schedule_and_carry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            audit, v4_manifest = self._v4_fixture(temporary_root)
            v5_manifest = self._v5_manifest(temporary_root / "v5-manifest.json")
            identity = temporary_root / "identity.json"
            identity.write_bytes(canonical_json_bytes({"models": [{"digest": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e", "name": "qwen2.5:7b"}, {"digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8", "name": "qwen3:14b-q4_K_M"}], "version": "0.33.2"}))
            output = temporary_root / "materialized"
            with self.assertRaises(V5MaterializationError):
                materialize(self.root, accepted_audit=audit, v4_source_manifest=v4_manifest, v5_source_manifest=v5_manifest, runtime_identity=identity, output=output, evidence_output_root=temporary_root / "evidence")
            with self.assertRaises(V5MaterializationError):
                materialize(self.root, accepted_audit=audit, v4_source_manifest=v4_manifest, v5_source_manifest=v5_manifest, runtime_identity=identity, output=output)

    def test_materialization_stages_all_members_and_refuses_raced_final(self) -> None:
        runtime = {"models": [{"digest": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e", "name": "qwen2.5:7b"}, {"digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8", "name": "qwen3:14b-q4_K_M"}], "version": "0.33.2"}
        source = {"release": {"commit": "1" * 40, "tag": V5_PROTOCOL_TAG, "tag_object": "2" * 40}}
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
