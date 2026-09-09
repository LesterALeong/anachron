from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from anachron.v5_contract import (
    V5_GOVERNED_SOURCE_PATHS,
    V5_PROTOCOL_TAG,
    V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS,
    V5_SEED_NAMESPACE,
    presentation_source_closure,
    scientific_source_closure,
    validate_authority_contract,
)
from anachron.v5_registry import canonical_json_bytes, strict_json_loads


class V5ContractTests(unittest.TestCase):
    def test_scientific_governed_source_paths_are_literal_sorted_and_unique(self) -> None:
        self.assertEqual(
            V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS,
            tuple(sorted(V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS)),
        )
        self.assertEqual(
            len(V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS),
            len(set(V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS)),
        )
        self.assertEqual(V5_PROTOCOL_TAG, "v5-measurement-protocol-v4")
        self.assertEqual(len(V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS), 47)
        self.assertEqual(V5_SEED_NAMESPACE, "anachron-v5-measurement-protocol-v1")

    def test_governed_closure_contains_every_a1_runtime_and_tool(self) -> None:
        root = Path(__file__).resolve().parents[1]
        required = {".gitattributes", "anachron/__init__.py", "anachron/v5_registry.py", "tests/test_v5_identity_controller.py", "tests/test_v5_operational.py", "tools/.gitattributes", "tools/analyze_v5_measurement.py", "tools/build_v5_source_manifest.py", "tools/capture_read_only_v5_identity.py", "tools/finalize_v5_carry_forward.py", "tools/materialize_v5_inputs.py", "tools/read_v5_authenticode_identity.ps1", "tools/read_v5_process_identity.ps1", "tools/run_v5_conditional_campaign.ps1", "tools/run_v5_recovery.py"}
        self.assertTrue(required.issubset(V5_GOVERNED_SOURCE_PATHS))
        self.assertEqual(set(validate_authority_contract(root)), set(V5_GOVERNED_SOURCE_PATHS))

    def test_v5_runtime_has_no_v4_import(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative in ("anachron/v5_contract.py", "anachron/v5_measurement.py", "anachron/v5_paths.py", "anachron/v5_registry.py", "tools/materialize_v5_inputs.py"):
            self.assertNotIn("anachron.v4", (root / relative).read_text(encoding="utf-8"), relative)

    def test_presentation_change_does_not_change_scientific_closure(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "science.txt").write_bytes(b"science\n")
            (root / "paper.txt").write_bytes(b"paper one\n")
            with (
                patch("anachron.v5_contract.V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS", ("science.txt",)),
                patch("anachron.v5_contract.V5_PRESENTATION_SOURCE_PATHS", ("paper.txt",)),
            ):
                scientific_before = scientific_source_closure(root)
                presentation_before = presentation_source_closure(root)
                (root / "paper.txt").write_bytes(b"paper two\n")
                self.assertEqual(scientific_source_closure(root), scientific_before)
                self.assertNotEqual(presentation_source_closure(root), presentation_before)

    def test_every_governed_v5_json_asset_uses_exact_canonical_bytes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        json_paths = [relative for relative in V5_GOVERNED_SOURCE_PATHS if relative.endswith(".json")]
        self.assertTrue(json_paths)
        for relative in json_paths:
            with self.subTest(relative=relative):
                raw = (root / relative).read_bytes()
                self.assertEqual(raw, canonical_json_bytes(strict_json_loads(raw, relative)))

if __name__ == "__main__":
    unittest.main()
