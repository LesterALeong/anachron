"""Dependency-free checks for the date-shift pre-results preview source."""

from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "tools" / "build_date_shift_pre_results_paper.py"
TECTONIC_PATH = Path(
    r"C:\Users\leste\AppData\Local\Temp\codex-date-shift-tectonic-0.17.0\bin\tectonic.exe"
)
SPEC = importlib.util.spec_from_file_location("date_shift_pre_results_builder", BUILDER_PATH)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class TestDateShiftPreResultsPaper(unittest.TestCase):
    def load_manuscript(self):
        return builder.load_canonical_json(ROOT / "paper/date_shift/manuscript.json")

    def test_canonical_source_has_bound_inputs_and_no_results_status(self):
        manuscript = self.load_manuscript()
        summary = builder.validate_manuscript(manuscript, ROOT)
        labels = builder.citation_labels(
            ROOT / "paper/date_shift/references.bib", manuscript["citations"]
        )
        rendered = builder.render_sections(manuscript, summary, labels)
        self.assertEqual(summary["candidate_count"], 60)
        self.assertEqual(summary["proposed_count"], 54)
        self.assertEqual(summary["excluded_count"], 6)
        self.assertEqual(summary["maximum_scientific_calls"], 216)
        self.assertEqual([model["id"] for model in summary["models"]], ["qwen2.5:7b", "qwen3:14b-q4_K_M"])
        self.assertGreaterEqual(builder.word_count(rendered), 1800)
        self.assertLessEqual(builder.word_count(rendered), 2400)

    def test_unsupported_citation_is_rejected(self):
        manuscript = self.load_manuscript()
        manuscript["citations"].append("invented-work")
        with self.assertRaisesRegex(builder.PreResultsPaperError, "unsupported citation"):
            builder.validate_manuscript(manuscript, ROOT)

    def test_prohibited_prose_is_rejected(self):
        manuscript = self.load_manuscript()
        raw_excerpt = builder.load_json(
            ROOT / "research/date-shift/proposed_items.json"
        )["proposed_items"][0]["document_content"]["text"]
        normalized_slice = builder.normalize_overlap_text(raw_excerpt)[100:180]
        cases = (
            ("We find a significant effect.", "empirical outcome claim"),
            ("TODO: add an outcome.", "prohibited placeholder"),
            ("Please archive this preview.", "prohibited external request"),
            (normalized_slice, "protected raw-source overlap"),
        )
        for forbidden, expected in cases:
            changed = copy.deepcopy(manuscript)
            changed["sections"][0]["text"] += " " + forbidden
            with self.subTest(forbidden=forbidden), self.assertRaisesRegex(builder.PreResultsPaperError, expected):
                builder.validate_manuscript(changed, ROOT)

    def test_noncanonical_json_is_rejected(self):
        manuscript = self.load_manuscript()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manuscript.json"
            path.write_text(json.dumps(manuscript, indent=4) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(builder.PreResultsPaperError, "not canonical"):
                builder.load_canonical_json(path)

    def test_inline_citations_and_tex_page_furniture(self):
        manuscript = self.load_manuscript()
        summary = builder.validate_manuscript(manuscript, ROOT)
        labels = builder.citation_labels(
            ROOT / "paper/date_shift/references.bib", manuscript["citations"]
        )
        rendered = builder.render_sections(manuscript, summary, labels)
        tex = builder.build_tex(manuscript, summary)
        self.assertIn(builder.REQUIRED_BANNER, tex)
        self.assertIn(r"\usepackage{fancyhdr}", tex)
        self.assertIn(r"\newcommand{\DateShiftPreResultsBanner}{", tex)
        self.assertIn(r"\fancyhead[C]{\DateShiftPreResultsBanner}", tex)
        self.assertIn(r"\fancypagestyle{plain}{", tex)
        self.assertIn(r"\bibliography{references}", tex)
        self.assertNotIn(r"\nocite{", tex)
        self.assertNotIn("https://en.wikipedia.org", tex)
        self.assertNotIn("[[cite:", tex)
        self.assertNotIn("[[cite:", "\n".join(paragraph for _, paragraphs in rendered for paragraph in paragraphs))
        for key in manuscript["citations"]:
            self.assertIn(rf"\cite{{{key}}}", tex)
        self.assertEqual(
            set(builder.bibliography_records(ROOT / "paper/date_shift/references.bib")),
            set(manuscript["citations"]),
        )
        self.assertEqual(
            len(builder.bibliography_lines(ROOT / "paper/date_shift/references.bib", manuscript["citations"])),
            len(manuscript["citations"]),
        )

    def test_unknown_or_missing_inline_citation_is_rejected(self):
        manuscript = self.load_manuscript()
        unknown = copy.deepcopy(manuscript)
        unknown["sections"][2]["text"] += " [[cite:invented-work]]"
        with self.assertRaisesRegex(builder.PreResultsPaperError, "unsupported inline citation"):
            builder.validate_manuscript(unknown, ROOT)
        missing = copy.deepcopy(manuscript)
        missing["citations"].pop()
        with self.assertRaisesRegex(builder.PreResultsPaperError, "does not match"):
            builder.validate_manuscript(missing, ROOT)

    def test_tex_verification_requires_an_explicit_existing_executable(self):
        with self.assertRaisesRegex(FileNotFoundError, "Tectonic executable not found"):
            builder.verify_tex_source(ROOT / "paper/date_shift/build/arxiv_source", ROOT / "missing-tectonic")

    def test_tectonic_hash_mismatch_refuses_before_executable_side_effect(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_tree = root / "source"
            source_tree.mkdir()
            (source_tree / "main.tex").write_text("\\documentclass{article}\n", encoding="ascii")
            executable = root / "wrong-tectonic.exe"
            executable.write_bytes(b"not the pinned Tectonic executable")
            side_effect = root / "subprocess-was-called"

            def unexpected_subprocess(*args, **kwargs):
                side_effect.write_text("unexpected", encoding="ascii")
                raise AssertionError("hash mismatch invoked Tectonic")

            with (
                mock.patch.object(builder.subprocess, "run", side_effect=unexpected_subprocess) as run,
                self.assertRaisesRegex(builder.PreResultsPaperError, "SHA-256 mismatch"),
            ):
                builder.verify_tex_source(source_tree, executable)
            run.assert_not_called()
            self.assertFalse(side_effect.exists())

    def test_complete_explicit_tectonic_builds_are_deterministic(self):
        if importlib.util.find_spec("fitz") is None or importlib.util.find_spec("reportlab") is None:
            self.skipTest("PDF preview dependencies are unavailable for this interpreter")
        if not TECTONIC_PATH.is_file():
            self.skipTest("pinned Tectonic executable is unavailable")
        first = builder.build(ROOT, verify=True, tectonic=TECTONIC_PATH)
        second = builder.build(ROOT, verify=True, tectonic=TECTONIC_PATH)
        self.assertEqual(builder.canonical_bytes(first), builder.canonical_bytes(second))
        self.assertEqual(
            first["tex_compile_verification"]["compiled_pdf_sha256"],
            second["tex_compile_verification"]["compiled_pdf_sha256"],
        )

    def test_pdf_banner_is_raster_visible_on_every_page(self):
        try:
            import fitz
            import reportlab  # noqa: F401
        except ImportError:
            self.skipTest("PDF preview dependencies are unavailable for this interpreter")
        receipt = builder.build(ROOT, verify=True)
        pixels = []
        document = fitz.open(ROOT / "paper/date_shift/build/date_shift_pre_results.pdf")
        try:
            for page in document:
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                pixels.append(builder.red_banner_pixel_count(pixmap))
        finally:
            document.close()
        self.assertEqual(receipt["verification"]["page_count"], 4)
        self.assertEqual(len(pixels), 4)
        self.assertEqual(
            receipt["verification"]["raster_banner_red_pixels_by_page"], pixels
        )
        self.assertTrue(all(value >= builder.MIN_RED_BANNER_PIXELS for value in pixels), pixels)
        self.assertEqual(
            receipt["verification"]["rendered_pages"],
            ["page-01.png", "page-02.png", "page-03.png", "page-04.png"],
        )


if __name__ == "__main__":
    unittest.main()
