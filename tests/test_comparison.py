import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from anachron.compare import main
from anachron.core import ModeComparison, compare_modes


class TestPairedComparison(unittest.TestCase):
    def test_effect_size_counts_and_interval(self):
        unrestricted = {"a": 1.0, "b": 1.0, "c": 0.0, "d": 0.5}
        enforced = {"a": 0.0, "b": 0.5, "c": 0.0, "d": 1.0}

        report = compare_modes(unrestricted, enforced, n_resamples=500, seed=7)

        self.assertIsInstance(report, ModeComparison)
        self.assertEqual(report.n, 4)
        self.assertAlmostEqual(report.unrestricted_mean, 0.625)
        self.assertAlmostEqual(report.enforced_mean, 0.375)
        self.assertAlmostEqual(report.mean_reduction, 0.25)
        self.assertLessEqual(report.ci_low, report.mean_reduction)
        self.assertGreaterEqual(report.ci_high, report.mean_reduction)
        self.assertEqual(report.improved_samples, 2)
        self.assertEqual(report.worsened_samples, 1)
        self.assertEqual(report.unchanged_samples, 1)

    def test_exact_sign_test_detects_consistent_improvement(self):
        unrestricted = {f"sample-{index}": 1.0 for index in range(10)}
        enforced = {f"sample-{index}": 0.0 for index in range(10)}

        report = compare_modes(unrestricted, enforced, n_resamples=50)

        self.assertAlmostEqual(report.sign_test_p_value, 2 / 1024)
        self.assertEqual(report.ci_low, 1.0)
        self.assertEqual(report.ci_high, 1.0)
        self.assertEqual(report.relative_reduction, 1.0)

    def test_zero_baseline_has_no_relative_reduction(self):
        report = compare_modes({"a": 0.0}, {"a": 0.0}, n_resamples=5)

        self.assertIsNone(report.relative_reduction)
        self.assertEqual(report.sign_test_p_value, 1.0)
        self.assertIn("n/a", report.table())

    def test_comparison_is_reproducible(self):
        unrestricted = {"a": 0.0, "b": 0.5, "c": 1.0}
        enforced = {"a": 0.0, "b": 0.0, "c": 0.5}

        first = compare_modes(unrestricted, enforced, n_resamples=100, seed=4)
        second = compare_modes(unrestricted, enforced, n_resamples=100, seed=4)

        self.assertEqual(first, second)

    def test_rejects_unpaired_sample_ids(self):
        with self.assertRaisesRegex(ValueError, "identical sample ids"):
            compare_modes({"a": 0.5}, {"b": 0.5})

    def test_rejects_invalid_tclr(self):
        with self.assertRaisesRegex(ValueError, r"in \[0, 1\]"):
            compare_modes({"a": 1.1}, {"a": 0.0})

    def test_cli_can_emit_machine_readable_json(self):
        output = io.StringIO()
        with (
            patch(
                "anachron.compare._load_scores",
                side_effect=[{"a": 1.0, "b": 0.0}, {"a": 0.0, "b": 0.0}],
            ),
            redirect_stdout(output),
        ):
            exit_code = main(["unrestricted.json", "enforced.json", "--format", "json"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["n"], 2)
        self.assertEqual(payload["mean_reduction"], 0.5)


if __name__ == "__main__":
    unittest.main()
