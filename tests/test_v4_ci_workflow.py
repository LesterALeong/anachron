"""Static coverage for the isolated v4 paper workflow."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

from anachron.v4_contract import V4_TECTONIC


class V4CiWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.workflow = (self.root / ".github/workflows/tests.yml").read_text(
            encoding="utf-8"
        )

    def test_v4_push_tag_matrix_and_pinned_tectonic_are_exact(self) -> None:
        for job in ("core", "inspect", "paper", "v4-paper"):
            self.assertIn(f"  {job}:\n", self.workflow)
        self.assertIn("branches: [master, main, protocol/v4-recovery-v1]", self.workflow)
        self.assertIn("tags: [v4-measurement-protocol-v1]", self.workflow)
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertIn("  v4-paper:\n", self.workflow)
        self.assertIn('python-version: ["3.10", "3.11", "3.12"]', self.workflow)
        self.assertIn("ANACHRON_V4_TECTONIC", self.workflow)
        self.assertIn(V4_TECTONIC["linux_archive_sha256"], self.workflow)
        self.assertIn(V4_TECTONIC["linux_executable_sha256"], self.workflow)
        self.assertIn("tectonic%400.17.0", self.workflow)
        self.assertIn("env -u LD_LIBRARY_PATH -u PYTHONHOME -u PYTHONPATH", self.workflow)
        self.assertNotIn("ANACHRON_V4_PROTOCOL_PYTHON", self.workflow)
        self.assertNotIn(r"C:\\Users", self.workflow)
        candidate_test = (self.root / "tests/test_v4_candidate_paper.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("ANACHRON_V4_TECTONIC", candidate_test)
        self.assertNotIn(r"C:\\Users", candidate_test)

    def test_v4_lane_runs_only_disposable_local_module_gates(self) -> None:
        for module in (
            "tests.test_v4_candidate_projection",
            "tests.test_v4_candidate_paper",
            "tests.test_v4_candidate_review_release",
            "tests.test_v4_candidate_outreach",
            "tests.test_v4_ci_workflow",
            "tests.test_v4_contract",
            "tests.test_v4_source_manifest",
            "tests.test_v4_materialization",
        ):
            self.assertIn(module, self.workflow)
        self.assertNotIn("ollama", self.workflow.lower())
        for command in (
            "python -m tools.build_v4_measurement_candidate_paper",
            "python -m tools.verify_v4_measurement_candidate_reviews",
            "python -m tools.release_v4_measurement_candidate",
            "python -m tools.render_v4_measurement_unsent_outreach",
        ):
            self.assertIn(
                command,
                (self.root / "paper/v4_measurement/README.md").read_text(
                    encoding="utf-8"
                ),
            )

    def test_no_site_packages_candidate_discovery_skips_paper_cases(self) -> None:
        environment = dict(os.environ)
        for name in (
            "ANACHRON_V4_TECTONIC",
            "LD_LIBRARY_PATH",
            "PYTHONHOME",
            "PYTHONPATH",
        ):
            environment.pop(name, None)
        discovery = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                (
                    "import unittest; "
                    "suite = unittest.defaultTestLoader.discover('tests', "
                    "pattern='test_v4_*.py'); "
                    "print(f'V4_TESTS={suite.countTestCases()}')"
                ),
            ],
            cwd=self.root,
            capture_output=True,
            check=False,
            env=environment,
            text=True,
        )
        discovery_output = discovery.stdout + discovery.stderr
        self.assertEqual(discovery.returncode, 0, discovery_output)
        self.assertRegex(discovery_output, r"V4_TESTS=[1-9][0-9]*")
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_v4_candidate_*.py",
                "-v",
            ],
            cwd=self.root,
            capture_output=True,
            check=False,
            env=environment,
            text=True,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("skipped", output)
        self.assertIn("test_archive_rejects_extra_and_unsafe_members", output)
        self.assertNotIn("ModuleNotFoundError", output)


if __name__ == "__main__":
    unittest.main()
