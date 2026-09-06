"""Static v5 paper-builder boundary checks; PDF creation is a separately gated action."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import build_v5_measurement_candidate_paper as builder


class V5CandidatePaperStaticTests(unittest.TestCase):
    def test_template_and_contract_prohibit_overclaiming(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = json.loads((root / "paper/v5_measurement/candidate_manuscript_template.json").read_text(encoding="utf-8"))
        self.assertEqual(set(template), {"title", "author", "categories", "ai_assistance_disclosure"})
        text = "\n".join((root / "paper/v5_measurement" / name).read_text(encoding="utf-8") for name in ("CANDIDATE_ACCEPTANCE_MATRIX.md", "CANDIDATE_CLAIM_EVIDENCE_MAP.md", "README.md")) .lower()
        for required in ("v4", "excluded", "conditional", "self-custody"):
            self.assertIn(required, text)
        for forbidden in ("endorsement bait", "upload capability", "send capability"):
            self.assertNotIn(forbidden, (root / "tools/build_v5_measurement_candidate_paper.py").read_text(encoding="utf-8").lower())

    def test_builder_has_no_model_or_network_import(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "tools/build_v5_measurement_candidate_paper.py").read_text(encoding="utf-8").lower()
        for forbidden in ("import requests", "import socket", "import urllib", "import smtplib", "ollama"):
            self.assertNotIn(forbidden, source)

    def test_builder_requires_a_dedicated_offline_tectonic_cache(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "tools/build_v5_measurement_candidate_paper.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("TECTONIC_CACHE_DIR")', source)
        self.assertIn('"SOURCE_DATE_EPOCH": "0"', source)
        self.assertNotIn("os.environ.copy()", source)
        self.assertIn('"--only-cached"', source)

    def test_tectonic_subprocess_environment_is_explicit_and_ambient_free(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            cache.mkdir()
            with patch.dict(
                os.environ,
                {
                    "ANACHRON_AMBIENT_SENTINEL": "must-not-propagate",
                    "TECTONIC_CACHE_DIR": str(cache),
                },
                clear=False,
            ):
                environment = builder._tectonic_environment()
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import json, os; print(json.dumps({key: os.environ.get(key) for key in ('ANACHRON_AMBIENT_SENTINEL', 'HOME', 'SOURCE_DATE_EPOCH', 'TECTONIC_CACHE_DIR', 'XDG_CACHE_HOME')}))",
                ],
                capture_output=True,
                check=True,
                env=environment,
                text=True,
            )
            observed = json.loads(result.stdout)
            self.assertIsNone(observed["ANACHRON_AMBIENT_SENTINEL"])
            self.assertEqual(observed["TECTONIC_CACHE_DIR"], str(cache))
            self.assertEqual(observed["HOME"], str(cache.parent))
            self.assertEqual(observed["XDG_CACHE_HOME"], str(cache.parent))
            self.assertEqual(observed["SOURCE_DATE_EPOCH"], "0")

    def test_candidate_completion_refuses_transient_source_or_rendered_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in (
                "arxiv_metadata.json",
                "candidate.pdf",
                "candidate_receipt.json",
                "paper_source_manifest.json",
                "projection.json",
                "qa_render_manifest.json",
                "source.zip",
            ):
                (root / name).write_bytes(b"fixture")
            (root / "qa_renders").mkdir()
            builder._candidate_completion(root)
            for transient in ("source", "rendered"):
                with self.subTest(transient=transient):
                    path = root / transient
                    path.mkdir()
                    with self.assertRaisesRegex(builder.CandidatePaperError, "topology"):
                        builder._candidate_completion(root)
                    path.rmdir()


if __name__ == "__main__":
    unittest.main()
