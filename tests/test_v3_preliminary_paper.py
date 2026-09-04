"""Focused tests for the Anachron v3 pre-full-results paper preview."""

from __future__ import annotations

import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "tools" / "build_v3_measurement_preliminary_paper.py"
TECTONIC = Path(r"C:\Users\leste\.codex\tools\tectonic-0.17.0\bin\tectonic.exe")
SPEC = importlib.util.spec_from_file_location("v3_preliminary_builder", BUILDER_PATH)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class TestV3PreliminaryPaper(unittest.TestCase):
    def test_frozen_contract_and_canonical_manuscript_validate(self):
        contract = builder.validate_contract(ROOT)
        manuscript = builder.validate_manuscript(ROOT)
        self.assertEqual(contract["state"], "preliminary")
        self.assertEqual(manuscript["pages"][0]["paragraphs"][0], builder.EXACT_STATUS_SENTENCE)
        body = "\n".join(
            paragraph for page in manuscript["pages"] for paragraph in page["paragraphs"]
        )
        self.assertIn("336", body)
        self.assertIn("264", body)
        self.assertIn("72", body)

    def test_empirical_and_external_action_prose_is_rejected(self):
        original = builder.load_canonical_json(ROOT / "paper/v3_measurement/manuscript.json")
        for prohibited in (
            "TCLR was 0.83.",
            "The falsifier was positive.",
            "Qwen returned a post-cutoff item.",
        ):
            changed = copy.deepcopy(original)
            changed["pages"][0]["paragraphs"].append(prohibited)
            with self.subTest(prohibited=prohibited), self.assertRaisesRegex(
                builder.PreliminaryPaperError, "forbidden empirical or external-action claim"
            ):
                builder.validate_manuscript(ROOT, manuscript=changed)

    def test_forbidden_empirical_prose_fails_before_output_creation(self):
        original = builder.load_canonical_json(ROOT / "paper/v3_measurement/manuscript.json")
        source = ROOT / "paper/v3_measurement/manuscript.json"
        original_loader = builder.load_canonical_json
        original_hash = builder.sha256_path
        contract = builder.validate_contract(ROOT)
        for prohibited in (
            "TCLR was 0.83.",
            "The falsifier was positive.",
            "Qwen returned a post-cutoff item.",
        ):
            changed = copy.deepcopy(original)
            changed["pages"][0]["paragraphs"].append(prohibited)

            def load_override(path):
                return changed if Path(path) == source else original_loader(path)

            def hash_override(path):
                return contract["manuscript_sha256"] if Path(path) == source else original_hash(path)

            with (
                self.subTest(prohibited=prohibited),
                mock.patch.object(builder, "load_canonical_json", side_effect=load_override),
                mock.patch.object(builder, "sha256_path", side_effect=hash_override),
                mock.patch.object(builder.tempfile, "TemporaryDirectory") as temporary_directory,
                mock.patch.object(builder, "ensure_build_directory") as ensure_build_directory,
                self.assertRaisesRegex(builder.PreliminaryPaperError, "forbidden empirical or external-action claim"),
            ):
                builder.build_preliminary(ROOT, TECTONIC)
            temporary_directory.assert_not_called()
            ensure_build_directory.assert_not_called()

    def test_manuscript_hash_is_bound_before_build(self):
        source = ROOT / "paper/v3_measurement/manuscript.json"
        original_hash = builder.sha256_path

        def hash_override(path):
            return "0" * 64 if Path(path) == source else original_hash(path)

        with mock.patch.object(builder, "sha256_path", side_effect=hash_override), self.assertRaisesRegex(
            builder.PreliminaryPaperError, "bound file hash drifted"
        ):
            builder.validate_contract(ROOT)

    def test_final_state_arguments_fail_closed(self):
        for argument in (
            "--evidence=root",
            "--receipt=receipt.json",
            "--go=GO",
            "--review=approved",
            "--final",
            "--submission=arxiv",
            "--archive=source.zip",
            "--upload",
        ):
            with self.subTest(argument=argument), self.assertRaisesRegex(
                builder.PreliminaryPaperError, "rejects final-state input"
            ):
                builder.reject_final_state_arguments([argument])

    def test_tclr_and_terminal_answer_boundaries_are_preserved(self):
        manuscript = builder.load_canonical_json(ROOT / "paper/v3_measurement/manuscript.json")
        trace_page = next(page for page in manuscript["pages"] if page["heading"] == "4. Trace-level measurement")
        trace_text = "\n".join(trace_page["paragraphs"])
        self.assertIn("Tool-Call Leakage Rate", trace_text)
        self.assertIn("result-leak tool interactions divided by total tool interactions", trace_text)
        self.assertIn("not folded into TCLR", trace_text)
        self.assertIn("outside the TCLR estimand", trace_text)
        self.assertNotIn("influence its terminal response", trace_text)

    def test_tex_source_uses_real_newlines(self):
        manuscript = builder.validate_manuscript(ROOT)
        contract = builder.validate_contract(ROOT)
        tex = builder.build_tex(manuscript, contract)
        self.assertNotIn(r"\nFULL STUDY", tex)
        self.assertIn("\nFULL STUDY NOT YET AUTHORIZED OR RUN.", tex)

    def test_tex_source_delegates_the_references_heading_to_thebibliography(self):
        manuscript = builder.validate_manuscript(ROOT)
        contract = builder.validate_contract(ROOT)
        tex = builder.build_tex(manuscript, contract)
        self.assertNotIn(r"\subsection*{References}", tex)

    def test_even_pages_report_an_intact_banner_rectangle(self):
        if not TECTONIC.is_file():
            self.skipTest("pinned Tectonic executable is unavailable")
        receipt = builder.build_preliminary(ROOT, TECTONIC)
        reportlab_banners = receipt["preview"]["pdf_verification"]["banner_rectangles"]
        tectonic_banners = receipt["preview"]["tex_verification"]["verification"]["banner_rectangles"]
        for banners in (reportlab_banners, tectonic_banners):
            self.assertEqual(len(banners), 7)
            self.assertTrue(banners[1]["intact"])
            self.assertTrue(banners[3]["intact"])
            self.assertTrue(banners[5]["intact"])
        self.assertEqual(receipt["preview"]["tex_verification"]["verification"]["references_heading_count"], 1)

    def test_even_page_banner_occlusion_is_rejected(self):
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF is unavailable")
        if not TECTONIC.is_file():
            self.skipTest("pinned Tectonic executable is unavailable")
        builder.build_preliminary(ROOT, TECTONIC)
        source = ROOT / "paper/v3_measurement/build/anachron_v3_preliminary.pdf"
        with tempfile.TemporaryDirectory() as temporary:
            altered = Path(temporary) / "occluded.pdf"
            document = fitz.open(source)
            page = document[1]
            phrase_rectangle = page.search_for(builder.REQUIRED_BANNER)[0]
            page.draw_rect(phrase_rectangle, color=None, fill=(1, 1, 1), overlay=True)
            document.save(altered)
            document.close()
            with self.assertRaisesRegex(builder.PreliminaryPaperError, "lacks visible red glyphs"):
                builder.verify_pdf(altered, Path(temporary) / "render")

    def test_wrong_tectonic_hash_refuses_before_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "wrong.exe"
            executable.write_bytes(b"wrong")
            with self.assertRaisesRegex(builder.PreliminaryPaperError, "SHA-256 mismatch"):
                builder.verify_tectonic(executable)

    def test_full_preview_build_is_deterministic_and_visible(self):
        try:
            import fitz  # noqa: F401
            import pdfplumber  # noqa: F401
            import reportlab  # noqa: F401
        except ImportError:
            self.skipTest("PDF preview dependencies are unavailable")
        if not TECTONIC.is_file():
            self.skipTest("pinned Tectonic executable is unavailable")
        receipt = builder.build_preliminary(ROOT, TECTONIC)
        output = ROOT / "paper/v3_measurement/build"
        self.assertTrue(receipt["deterministic_double_build"])
        self.assertEqual(receipt["preview"]["pdf_verification"]["page_count"], 7)
        self.assertTrue(all(value >= 1 for value in receipt["preview"]["pdf_verification"]["red_banner_pixels"]))
        glyphs = receipt["preview"]["pdf_verification"]["banner_glyph_pixels"]
        self.assertEqual(len(glyphs[::2]), 4)
        self.assertEqual(len(glyphs[1::2]), 3)
        self.assertTrue(all(all(value >= 1 for value in page) for page in glyphs[::2]))
        self.assertTrue(all(all(value >= 1 for value in page) for page in glyphs[1::2]))
        self.assertEqual(
            tuple(sorted(path.name for path in (output / "preview_source").iterdir())),
            builder.PREVIEW_TREE_FILES,
        )
        self.assertTrue((output / "anachron_v3_preliminary.pdf").is_file())
        self.assertTrue((output / "preview_source.zip").is_file())
        self.assertTrue((output / "build_receipt.json").is_file())


if __name__ == "__main__":
    unittest.main()
