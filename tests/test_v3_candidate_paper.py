"""Focused rendering tests for the Anachron v3 candidate-paper builder."""

from __future__ import annotations

import copy
import importlib.util
import os
import tempfile
import unittest
import zipfile
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TECTONIC = Path(os.environ.get("ANACHRON_V3_TECTONIC", r"C:\Users\leste\.codex\tools\tectonic-0.17.0\bin\tectonic.exe"))
REQUIRE_PAPER_QA = os.environ.get("ANACHRON_V3_REQUIRE_PAPER_QA") == "1"
BUILDER_PATH = ROOT / "tools" / "build_v3_measurement_candidate_paper.py"
SPEC = importlib.util.spec_from_file_location("v3_candidate_paper", BUILDER_PATH)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def _cell(split: str, model: str, mode: str, metric: str, numerator: int, denominator: int) -> dict:
    trajectories = 132 if split == "primary" and model == "pooled" else 66 if split == "primary" else 36 if model == "pooled" else 18
    fraction = Fraction(numerator, denominator) if denominator else None
    rate = {"undefined": "no_finance_interactions"} if fraction is None else {"numerator": fraction.numerator, "denominator": fraction.denominator}
    return {
        "case_count": 22 if split == "primary" else 6,
        "count": numerator,
        "denominator_count": denominator,
        "denominator_text": "finance-returning tool interactions" if metric == "survivorship_leakage" else "tool interactions",
        "metric": metric,
        "mode": mode,
        "model": model,
        "model_count": 2 if model == "pooled" else 1,
        "rate": rate,
        "repetition_n": 3,
        "scope_text": "finite synthetic panel; descriptive only",
        "split": split,
        "trajectory_count": trajectories,
    }


def _projection(sign: str = "positive") -> dict:
    result_counts = {
        "positive": {"unrestricted": 1, "enforced": 0},
        "zero": {"unrestricted": 0, "enforced": 0},
        "negative": {"unrestricted": 0, "enforced": 1},
    }[sign]
    cells = []
    for split in ("primary", "development"):
        denominator = 132 if split == "primary" else 36
        for model in ("qwen2.5:7b", "qwen3:14b-q4_K_M", "pooled"):
            model_denominator = denominator if model == "pooled" else denominator // 2
            for mode in ("unrestricted", "enforced"):
                for metric in ("tclr", "query_leakage", "restatement_leakage", "survivorship_leakage"):
                    metric_denominator = 0 if metric == "survivorship_leakage" else model_denominator
                    cells.append(_cell(split, model, mode, metric, 0 if metric != "tclr" else result_counts[mode] * (2 if model == "pooled" else 1), metric_denominator))
    direction = {"positive": 1, "zero": 0, "negative": -1}[sign]
    return {
        "analysis_go": False,
        "cells": cells,
        "equinox_enforced_survivorship": {"qwen2.5:7b": True, "qwen3:14b-q4_K_M": True},
        "paired_tclr_reductions": [
            {"model": model, "rate": {"numerator": direction, "denominator": 1 if direction == 0 else (66 if split == "primary" else 18)}, "sign_class": sign, "split": split, "trajectory_pair_count": 132 if split == "primary" and model == "pooled" else 66 if split == "primary" else 36 if model == "pooled" else 18}
            for split in ("primary", "development")
            for model in ("qwen2.5:7b", "qwen3:14b-q4_K_M", "pooled")
        ],
        "schema_version": "anachron-v3-candidate-projection-v1",
        "scientific_gates": {"all_trajectories_valid": True, "enforced_equinox_survivorship_each_model": True, "minimum_primary_reduction": False, "no_model_negative": sign != "negative"},
        "split_counts": {"development": 72, "primary": 264, "total": 336},
    }


class TestV3CandidatePaper(unittest.TestCase):
    def test_paper_ci_establishes_master_from_verified_remote_before_worktree(self):
        workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        origin = 'git remote get-url origin'
        remote_check = 'git ls-remote origin refs/heads/master'
        normalize = 'git remote set-url origin https://github.com/LesterALeong/anachron.git'
        remote = 'git rev-parse refs/remotes/origin/master'
        update = 'git update-ref refs/heads/master "$frozen"'
        worktree = 'git worktree add --detach "$protocol" v3-measurement-protocol-v1'
        protocol_python = 'ANACHRON_V3_PROTOCOL_PYTHON: ${{ steps.protocol-python.outputs.python-path }}'
        candidate_command = '${{ steps.candidate-python.outputs.python-path }} -m unittest'
        self.assertIn(origin, workflow)
        self.assertIn('https://github.com/LesterALeong/anachron|https://github.com/LesterALeong/anachron.git', workflow)
        self.assertIn(remote_check, workflow)
        self.assertIn(normalize, workflow)
        self.assertIn(remote, workflow)
        self.assertIn(update, workflow)
        self.assertIn(worktree, workflow)
        self.assertIn('id: candidate-python', workflow)
        self.assertIn('id: protocol-python', workflow)
        self.assertIn('python-version: "3.12.10"', workflow)
        self.assertIn('update-environment: false', workflow)
        self.assertIn(protocol_python, workflow)
        self.assertIn(candidate_command, workflow)
        self.assertLess(workflow.index(origin), workflow.index(remote_check))
        self.assertLess(workflow.index(remote_check), workflow.index(normalize))
        self.assertLess(workflow.index(normalize), workflow.index(remote))
        self.assertLess(workflow.index(remote), workflow.index(update))
        self.assertLess(workflow.index(update), workflow.index(worktree))

    def test_template_and_contract_are_frozen(self):
        template = builder.validate_template(ROOT)
        self.assertEqual(template["title"], "Measuring Point-in-Time Leakage in Agent Tool Traces")
        self.assertIn("negative", template["sentence_forms"])
        self.assertIn("structurally zero", "\n".join(template["sections"][1]["paragraphs"]))

    def test_positive_zero_and_negative_templates_render_honestly(self):
        template = builder.validate_template(ROOT)
        for sign, marker in (("positive", "reduction was positive"), ("zero", "difference was zero"), ("negative", "difference was negative")):
            with self.subTest(sign=sign):
                tex = builder.build_tex(template, _projection(sign))
                self.assertIn(marker, tex)
                self.assertIn("Appendix A. Development results", tex)
                self.assertEqual(tex.count("Development traces are disclosed separately"), 1)
                self.assertNotIn(r"\section*{References}", tex)
                self.assertIn("0 eligible interactions", tex)
                self.assertNotIn("0/0", tex)

    def test_strict_json_rejects_duplicate_and_nonfinite_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            duplicate = Path(temporary) / "duplicate.json"
            duplicate.write_bytes(b'{"key":1,"key":2}')
            with self.assertRaisesRegex(builder.CandidatePaperError, "duplicate"):
                builder._load_canonical_json(duplicate)
            nonfinite = Path(temporary) / "nonfinite.json"
            nonfinite.write_bytes(b'{"key":NaN}')
            with self.assertRaisesRegex(builder.CandidatePaperError, "non-finite"):
                builder._load_canonical_json(nonfinite)

    def test_projection_requires_exact_topology_and_real_integers(self):
        missing_cell = _projection()
        missing_cell["cells"].pop()
        with self.assertRaisesRegex(builder.CandidatePaperError, "cell topology"):
            builder.validate_projection(missing_cell)
        missing_pair = _projection()
        missing_pair["paired_tclr_reductions"].pop()
        with self.assertRaisesRegex(builder.CandidatePaperError, "pairing topology"):
            builder.validate_projection(missing_pair)
        boolean_count = _projection()
        boolean_count["cells"][0]["count"] = True
        with self.assertRaisesRegex(builder.CandidatePaperError, "count type"):
            builder.validate_projection(boolean_count)
        pooled_contradiction = _projection()
        pooled_cell = next(cell for cell in pooled_contradiction["cells"] if cell["model"] == "pooled" and cell["metric"] == "tclr")
        pooled_cell["count"] = 1
        pooled_cell["rate"] = {"numerator": 1, "denominator": pooled_cell["denominator_count"]}
        with self.assertRaisesRegex(builder.CandidatePaperError, "pooled aggregate"):
            builder.validate_projection(pooled_contradiction)
        pair_contradiction = _projection()
        pair_contradiction["paired_tclr_reductions"][0]["rate"] = {"numerator": 1, "denominator": 2}
        with self.assertRaisesRegex(builder.CandidatePaperError, "does not equal"):
            builder.validate_projection(pair_contradiction)

    def test_publish_staging_refuses_a_late_output_collision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "staging"
            staging.mkdir()
            (staging / "candidate.txt").write_text("candidate", encoding="utf-8")
            output = root / "output"
            output.mkdir()
            with self.assertRaisesRegex(FileExistsError, "appeared"):
                builder._publish_staging(staging, output)
            self.assertTrue(staging.is_dir())
            self.assertTrue(output.is_dir())

    def test_publish_staging_requires_exact_recursive_closure_before_and_after_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "staging"
            staging.mkdir()
            for name in builder.CANDIDATE_COMPLETION:
                path = staging / name
                if name in {"source", "qa_renders"}:
                    path.mkdir()
                    (path / "nested.txt").write_text(name, encoding="utf-8")
                else:
                    path.write_text(name, encoding="utf-8")
            output = root / "output"
            builder._publish_staging(staging, output)
            self.assertEqual(
                builder._tree_snapshot(staging, builder.CANDIDATE_COMPLETION),
                builder._tree_snapshot(output, builder.CANDIDATE_COMPLETION),
            )
            for target_relative, rogue_relative in ((Path("candidate.pdf"), Path("rogue.txt")), (Path("source/nested.txt"), Path("source/rogue.txt"))):
                failed_output = root / f"failed-{target_relative.parent.name or 'top'}-{rogue_relative.name}"
                original_copy = builder._copy_create_only

                def inject(source: Path, destination: Path, original_copy=original_copy, failed_output: Path = failed_output, target_relative: Path = target_relative, rogue_relative: Path = rogue_relative) -> None:
                    original_copy(source, destination)
                    if destination == failed_output / target_relative:
                        rogue = failed_output / rogue_relative
                        rogue.parent.mkdir(parents=True, exist_ok=True)
                        rogue.write_text("rogue", encoding="utf-8")

                with patch.object(builder, "_copy_create_only", side_effect=inject), self.assertRaisesRegex(builder.CandidatePaperError, "completion set|closure differs"):
                    builder._publish_staging(staging, failed_output)
                self.assertFalse((failed_output / "candidate_receipt.json").exists())

    def test_wrong_tectonic_refuses_before_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            wrong = Path(temporary) / "wrong.exe"
            wrong.write_bytes(b"wrong")
            with self.assertRaisesRegex(builder.CandidatePaperError, "SHA-256"):
                builder.verify_tectonic(wrong)

    def test_source_archive_and_pdf_are_deterministic_and_visible(self):
        if not TECTONIC.is_file() and not REQUIRE_PAPER_QA:
            self.skipTest("pinned Tectonic is unavailable")
        self.assertTrue(TECTONIC.is_file(), "required pinned Tectonic executable is unavailable")
        template = builder.validate_template(ROOT)
        projection = _projection()
        builder.verify_tectonic(TECTONIC)
        with tempfile.TemporaryDirectory() as first_root, tempfile.TemporaryDirectory() as second_root:
            first = builder._build_once(TECTONIC, template, projection, Path(first_root))
            second = builder._build_once(TECTONIC, template, projection, Path(second_root))
            self.assertEqual(builder.sha256_path(first["archive"]), builder.sha256_path(second["archive"]))
            self.assertEqual(builder.sha256_path(first["pdf"]), builder.sha256_path(second["pdf"]))
            with zipfile.ZipFile(first["archive"]) as archive:
                self.assertEqual(tuple(sorted(archive.namelist())), builder.ARCHIVE_FILES)
            verification = builder.verify_pdf(first["pdf"], Path(first_root) / "unique-renders", projection)
            self.assertGreaterEqual(verification["page_count"], 4)
            self.assertLessEqual(verification["page_count"], 6)

    def test_development_contamination_is_not_rendered_in_primary_section(self):
        template = builder.validate_template(ROOT)
        changed = copy.deepcopy(_projection())
        changed["cells"].append(_cell("development", "pooled", "unrestricted", "tclr", 1, 36))
        with self.assertRaisesRegex(builder.CandidatePaperError, "topology"):
            builder.build_tex(template, changed)


if __name__ == "__main__":
    unittest.main()
