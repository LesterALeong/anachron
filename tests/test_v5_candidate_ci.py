"""Presentation-only CI and cache-seeding contract tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anachron.v5_contract import (
    V5_PRESENTATION_SOURCE_PATHS,
    V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS,
)
from tests.v5_presentation_fixture import materialize_fixture


class V5CandidateCiTests(unittest.TestCase):
    def test_fixture_materializes_the_exact_four_candidate_source_files(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            source = materialize_fixture(root, Path(temporary) / "seed")
            self.assertEqual(
                {
                    path.relative_to(source).as_posix()
                    for path in source.rglob("*")
                    if path.is_file()
                },
                {
                    "README.md",
                    "figures/primary_adherence.tex",
                    "main.tex",
                    "references.bib",
                },
            )

    def test_presentation_workflow_seeds_then_removes_its_exact_source_workspace(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
        ).read_text(encoding="utf-8")
        seed_command = '${{ steps.candidate-python.outputs.python-path }} -m tests.v5_presentation_fixture --repository-root . --output "$seed"'
        self.assertIn(seed_command, workflow)
        self.assertNotIn("printf '%s\\n'", workflow)
        self.assertNotIn("\\documentclass", workflow)
        self.assertIn('SOURCE_DATE_EPOCH=0 "$RUNNER_TEMP/tectonic-v5/tectonic"', workflow)
        self.assertIn('rm -rf "$seed"', workflow)
        self.assertIn('test ! -e "$seed"', workflow)
        self.assertLess(workflow.index(seed_command), workflow.index('rm -rf "$seed"'))
        self.assertLess(
            workflow.index('test ! -e "$seed"'),
            workflow.index("Run required v5 candidate lifecycle gate"),
        )
        self.assertIn("ANACHRON_V5_TECTONIC_CACHE_SOURCE", workflow)
        bundle_hash_file = 'bundle_hash_file="$cache/bundles/hashes/https,58,,47,,47,relay.fullyjustified.net,47,default_bundle_v33.tar"'
        self.assertIn(bundle_hash_file, workflow)
        self.assertIn('test -f "$bundle_hash_file"', workflow)
        self.assertIn(
            'test "$(tr -d \'\\r\\n\' < "$bundle_hash_file")" = "$cache_content_identifier"',
            workflow,
        )
        self.assertNotIn('find "$cache/bundles/hashes" -type f -exec cat {} \\;', workflow)
        self.assertNotIn("tests/test_v5_candidate_ci.py", V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS)
        self.assertIn("tests/test_v5_candidate_ci.py", V5_PRESENTATION_SOURCE_PATHS)
        self.assertNotIn(".github/workflows/tests.yml", V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS)
        self.assertIn(".github/workflows/tests.yml", V5_PRESENTATION_SOURCE_PATHS)


if __name__ == "__main__":
    unittest.main()
