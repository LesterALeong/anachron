import json
import tempfile
import unittest
from pathlib import Path

from anachron.routes.v2.analysis import AnalysisValidationError, replay_analysis_root
from tools.build_routes_v2_paper import build
from tools.render_routes_results import render_verified

ROOT = Path(__file__).parents[1]
TECTONIC = Path(r"C:\\Users\\leste\\.codex\\tools\\tectonic-0.17.0\\bin\\tectonic.exe")


class TestRoutesV2StepB(unittest.TestCase):
    def test_legacy_free_form_result_path_is_absent(self):
        self.assertFalse(hasattr(__import__("tools.render_routes_results", fromlist=["render"]), "render"))

    def test_dirty_current_checkout_cannot_replay_or_build_final_paper(self):
        with tempfile.TemporaryDirectory() as directory:
            analysis_root = Path(directory) / "analysis"
            analysis_root.mkdir()
            names = {
                "pending_draft.json", "source_decisions.json", "source_gate.json", "manifest.json",
                "freeze_receipt.json", "closure_lock.json", "schedule.json", "session_calibrations.json",
                "sealed_aliases.json", "questions.json", "alias_rubrics.json", "rater-a.json", "rater-b.json",
            }
            for name in names:
                (analysis_root / name).write_text(json.dumps({}), encoding="utf-8")
            (analysis_root / "journal.jsonl").write_bytes(b"")
            (analysis_root / "audit_blind_key.bin").write_bytes(b"x" * 16)
            (analysis_root / "instructions.txt").write_text("fixture", encoding="utf-8")
            with self.assertRaises(AnalysisValidationError):
                replay_analysis_root(analysis_root, ROOT)
            with self.assertRaises(AnalysisValidationError):
                render_verified(analysis_root, ROOT)
            if TECTONIC.is_file():
                output = Path(directory) / "output"
                with self.assertRaises(AnalysisValidationError):
                    build(
                        tectonic=TECTONIC,
                        analysis_root=analysis_root,
                        frozen_root=ROOT,
                        output_dir=output,
                    )
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
