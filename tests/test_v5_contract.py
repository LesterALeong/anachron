from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from anachron.v5_contract import (
    V5_GOVERNED_SOURCE_PATHS,
    presentation_source_closure,
    scientific_source_closure,
    validate_authority_contract,
)
from anachron.v5_registry import canonical_json_bytes, strict_json_loads


class V5ContractTests(unittest.TestCase):
    def test_governed_closure_contains_every_a1_runtime_and_tool(self) -> None:
        root = Path(__file__).resolve().parents[1]
        required = {"anachron/v5_registry.py", "tests/test_v5_operational.py", "tools/analyze_v5_measurement.py", "tools/build_v5_source_manifest.py", "tools/finalize_v5_carry_forward.py", "tools/materialize_v5_inputs.py", "tools/run_v5_conditional_campaign.ps1", "tools/run_v5_recovery.py"}
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

    def test_v5_ci_provisions_a_pinned_dedicated_cache_before_offline_lifecycle(self) -> None:
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        self.assertIn("Provision isolated pinned v5 Tectonic cache", workflow)
        self.assertIn("https://relay.fullyjustified.net/default_bundle_v33.tar", workflow)
        self.assertIn("6ffe055852f8faf66c0acbe1a7fb27f87b869a90bad1204f3bf4d9683f597c7c", workflow)
        self.assertIn('test ! -e "$cache"', workflow)
        self.assertIn("HOME: ${{ runner.temp }}/tectonic-v5-home", workflow)
        self.assertIn("TECTONIC_CACHE_DIR: ${{ runner.temp }}/tectonic-v5-cache", workflow)
        self.assertIn('"--only-cached"', (Path(__file__).resolve().parents[1] / "tools/build_v5_measurement_candidate_paper.py").read_text(encoding="utf-8"))



if __name__ == "__main__":
    unittest.main()
