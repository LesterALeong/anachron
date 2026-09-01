import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


class TestRoutesV2Cli(unittest.TestCase):
    def test_module_help_routes_are_supported(self):
        for module in ("tools.render_routes_results", "tools.build_routes_v2_paper"):
            with self.subTest(module=module):
                result = subprocess.run(
                    [sys.executable, "-m", module, "--help"],
                    cwd=ROOT,
                    capture_output=True,
                    check=False,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_direct_script_routes_explain_module_only_contract(self):
        for script, module in (
            ("tools/render_routes_results.py", "tools.render_routes_results"),
            ("tools/build_routes_v2_paper.py", "tools.build_routes_v2_paper"),
        ):
            with self.subTest(script=script):
                result = subprocess.run(
                    [sys.executable, script, "--help"],
                    cwd=ROOT,
                    capture_output=True,
                    check=False,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"python -m {module}", result.stderr)


if __name__ == "__main__":
    unittest.main()
