from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path


class V5OperationalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.wrapper = self.root / "tools/run_v5_conditional_campaign.ps1"
        self.source = self.wrapper.read_text(encoding="utf-8")

    def test_wrapper_parses_without_executing(self) -> None:
        executable = shutil.which("powershell.exe") or shutil.which("pwsh")
        if executable is None:
            self.skipTest("PowerShell AST parser is unavailable")
        command = (
            "$tokens = $null; $errors = $null; "
            f"[System.Management.Automation.Language.Parser]::ParseFile('{self.wrapper.as_posix()}', [ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count) { $errors | ForEach-Object { $_.ToString() }; exit 1 }"
        )
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", command],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_wrapper_has_no_dynamic_execution_or_external_capability(self) -> None:
        lowered = self.source.lower()
        for forbidden in (
            "[scriptblock]",
            "invoke-expression",
            "start-job",
            "invoke-webrequest",
            "invoke-restmethod",
            "upload",
            "submit",
            "outreach",
            "arxiv",
            "endorsement",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)
        self.assertNotIn("& $", self.source)
        self.assertIn('"-m", "tools.run_v5_recovery"', self.source)

    def test_wrapper_requires_bounded_identity_only_and_exact_pid_lifecycle(self) -> None:
        self.assertIn('if ($Path -notin @("/api/version", "/api/tags"))', self.source)
        self.assertIn("--connect-timeout 5 --max-time 20 --request GET", self.source)
        self.assertIn("$MaximumLogBytes = 1048576", self.source)
        self.assertNotIn("Stop-Process -Name", self.source)
        self.assertIn("Stop-Process -Id $normalTree.app.ProcessId", self.source)
        self.assertIn("Stop-Process -Id $isolatedProcess.Id", self.source)
        start_lines = [line for line in self.source.splitlines() if "Start-Process" in line]
        self.assertGreaterEqual(len(start_lines), 2)
        self.assertTrue(all("-WindowStyle Hidden" in line for line in start_lines))

    def test_wrapper_restores_every_isolated_runtime_invariant(self) -> None:
        for invariant in (
            "OLLAMA_HOST",
            "OLLAMA_MODELS",
            "OLLAMA_NO_CLOUD",
            "OLLAMA_NOPRUNE",
        ):
            with self.subTest(invariant=invariant):
                self.assertIn(f'"{invariant}"', self.source)
                self.assertIn(f'$env:{invariant} = "1"' if invariant in {"OLLAMA_NO_CLOUD", "OLLAMA_NOPRUNE"} else f'$env:{invariant}', self.source)
        self.assertIn("foreach ($name in $environmentNames)", self.source)
        self.assertIn("Remove-Item -Path (\"Env:\" + $name)", self.source)
        self.assertIn("Set-Item -Path (\"Env:\" + $name) -Value $savedEnvironment[$name].Value", self.source)

    def test_runner_success_requires_one_fixed_primary_replay(self) -> None:
        self.assertIn('"-m", "tools.analyze_v5_measurement"', self.source)
        self.assertIn('"--phase", "primary"', self.source)
        self.assertIn("if ($analyzerExitCode -ne 0)", self.source)
        self.assertIn("analyzer_exit_code = $analyzerExitCode", self.source)
        self.assertIn("analyzer_stdout_sha256", self.source)
        self.assertIn("analyzer_stderr_sha256", self.source)
        analyzer_call = self.source.index("& python.exe @analyzerArguments")
        analyzer_gate = self.source.index("if ($analyzerExitCode -ne 0)")
        success = self.source.index("$campaignSucceeded = $true", analyzer_gate)
        self.assertEqual(self.source.count("& python.exe @analyzerArguments"), 1)
        self.assertLess(analyzer_call, analyzer_gate)
        self.assertLess(analyzer_gate, success)

    def test_log_guard_is_explicitly_post_child_not_an_active_stream_cap(self) -> None:
        self.assertIn("function Assert-PostHocLogBound", self.source)
        self.assertIn("post-child disk guard", self.source)
        self.assertNotIn("function Assert-LogBound", self.source)

    def test_wrapper_binds_all_post_tag_authorities_without_a_tag_cycle(self) -> None:
        for required in (
            "materialization_receipt_sha256",
            "wrapper_sha256",
            "runner_sha256",
            "analyzer_sha256",
            "source_manifest_sha256",
            "carry_forward_sha256",
            "compatibility_plan_sha256",
            "full_plan_sha256",
            "Assert-FrozenProtocol",
            "--preflight-only",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.source)
        self.assertLess(
            self.source.index("if ($ValidateOnly.IsPresent)"),
            self.source.index("New-Item -ItemType Directory -Path $OperationRoot"),
        )
        self.assertLess(
            self.source.index("if ($ValidateOnly.IsPresent)"),
            self.source.index("Stop-Process -Id $normalTree.app.ProcessId"),
        )
        self.assertIn("finally", self.source)
        self.assertIn("Restore-NormalOllama", self.source)
        self.assertIn("restored normal identity bytes differ from baseline", self.source)

    @unittest.skipUnless(os.name == "nt", "PowerShell wrapper semantics are Windows-specific")
    def test_validate_only_path_is_present_for_harmless_preflight_fixture(self) -> None:
        self.assertIn("V5 wrapper validation passed without process, network, or filesystem mutation", self.source)


if __name__ == "__main__":
    unittest.main()
