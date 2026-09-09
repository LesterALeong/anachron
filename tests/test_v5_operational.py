from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
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
            '"v5-measurement-protocol-v5"',
            '"anachron-v5-materialization-receipt-v3"',
            "materialization_receipt_sha256",
            "runtime_identity_sha256",
            "schedule_sha256",
            "wrapper_sha256",
            "runner_sha256",
            "analyzer_sha256",
            "source_manifest_sha256",
            "carry_forward_sha256",
            "compatibility_plan_sha256",
            "full_plan_sha256",
            "Assert-FrozenProtocol",
            "--preflight-only",
            "python.exe -B @runnerArguments",
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

    def test_wrapper_requires_the_exact_seven_member_materialization_closure(self) -> None:
        for member in (
            "compatibility_plan.json",
            "carry_forward.json",
            "full_plan.json",
            "materialization_receipt.json",
            "runtime_identity.json",
            "schedule.json",
            "source_manifest.json",
        ):
            with self.subTest(member=member):
                self.assertIn(member, self.source)
        self.assertIn("$members.Count -ne 7", self.source)
        self.assertIn("materialization root topology or byte budget differs", self.source)
        self.assertIn('Assert-ExistingDirectory $MaterializationRoot "materialization root"', self.source)
        self.assertNotIn("runtime identity path differs", self.source)
        self.assertIn('Assert-Sha256 $topology.runtime (Get-JsonString $receipt "runtime_identity_sha256" "materialization receipt") "materialized runtime identity"', self.source)
        self.assertIn('Assert-Sha256 $RuntimeIdentity (Get-JsonString $receipt "runtime_identity_sha256" "materialization receipt") "captured runtime identity"', self.source)
        self.assertIn("materialized schedule", self.source)

    def test_wrapper_bounds_external_json_without_recursing_through_git_or_output_parents(self) -> None:
        self.assertIn("function Assert-SafePathComponents", self.source)
        self.assertIn("function Assert-SafeTree", self.source)
        self.assertIn("function Get-CanonicalJson", self.source)
        self.assertIn('$item.Length -gt $MaximumLogBytes', self.source)
        self.assertIn('$capturedRuntime = Get-CanonicalJson $RuntimeIdentity "runtime identity"', self.source)
        self.assertIn('$go = Get-CanonicalJson $ConditionalGo "conditional GO"', self.source)
        self.assertIn('$receipt = Get-CanonicalJson $MaterializationReceipt "materialization receipt"', self.source)
        self.assertNotIn("Assert-SafeTree $ProtocolRoot", self.source)
        self.assertNotIn("Assert-SafeTree $EvidenceRoot", self.source)
        self.assertLess(
            self.source.index('Assert-SafeTree $ExpectedIsolatedModels "isolated model store"'),
            self.source.index("$before = Get-OllamaProcessSnapshot"),
        )
        self.assertGreater(
            self.source.index('Assert-SafeTree $ExpectedIsolatedModels "isolated model store"'),
            self.source.index("if (-not $Execute.IsPresent)"),
        )

    def test_wrapper_compares_equal_model_arrays_without_strict_mode_scalar_failures(self) -> None:
        self.assertIn("@(Compare-Object $observed $expected).Count", self.source)
        self.assertIn("@(Compare-Object @($fullPlan.expected_runtime.models", self.source)

    def test_wrapper_hashes_with_disposed_dotnet_streams(self) -> None:
        self.assertNotIn("Get-FileHash", self.source)
        self.assertIn("[IO.File]::Open((Normalize-FullPath $Path)", self.source)
        self.assertIn("[IO.FileAccess]::Read", self.source)
        self.assertIn("[IO.FileShare]::Read", self.source)
        self.assertIn("[Security.Cryptography.SHA256]::Create()", self.source)
        self.assertIn("finally", self.source)
        self.assertIn("$hasher.Dispose()", self.source)
        self.assertIn("$stream.Dispose()", self.source)

    @unittest.skipUnless(os.name == "nt", "requires Windows PowerShell validation")
    def test_wrapper_hash_function_matches_known_empty_and_nonempty_digests(self) -> None:
        windows_powershell = Path(os.environ.get("SystemRoot", r"C:\\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        shells = [("Windows PowerShell", windows_powershell)]
        if pwsh := shutil.which("pwsh"):
            shells.append(("pwsh", Path(pwsh)))
        normalize_start = self.source.index("function Normalize-FullPath")
        safe_entry_start = self.source.index("function Assert-SafeEntry", normalize_start)
        hash_start = self.source.index("function Get-Sha256")
        assert_hash_start = self.source.index("function Assert-Sha256", hash_start)
        hash_functions = self.source[normalize_start:safe_entry_start] + self.source[hash_start:assert_hash_start]

        with tempfile.TemporaryDirectory(prefix="anachron-v5-hash-") as temporary:
            workspace = Path(temporary)
            empty = workspace / "empty.bin"
            nonempty = workspace / "nonempty.bin"
            harness = workspace / "hash-harness.ps1"
            empty.write_bytes(b"")
            nonempty.write_bytes(b"anachron-v5\n")
            harness.write_text(
                hash_functions + "\nWrite-Output (Get-Sha256 -Path $args[0])\nWrite-Output (Get-Sha256 -Path $args[1])\n",
                encoding="utf-8",
            )
            expected = [hashlib.sha256(empty.read_bytes()).hexdigest(), hashlib.sha256(nonempty.read_bytes()).hexdigest()]
            for shell_label, shell in shells:
                with self.subTest(shell=shell_label):
                    if not shell.is_file():
                        self.skipTest(f"{shell_label} is unavailable")
                    result = subprocess.run(
                        [str(shell), "-NoProfile", "-File", str(harness), str(empty), str(nonempty)],
                        capture_output=True,
                        check=False,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    self.assertEqual(result.stdout.splitlines(), expected)

    def test_validate_only_checks_shared_closure_before_host_specific_execution_guards(self) -> None:
        validate_only = self.source.index("if ($ValidateOnly.IsPresent)")
        execute = self.source.index("if (-not $Execute.IsPresent)")
        first_operation = self.source.index("$before = Get-OllamaProcessSnapshot")
        for guard in (
            'Assert-ExistingFile $ExpectedIsolatedExe "isolated executable"',
            'Assert-ExistingDirectory $ExpectedIsolatedModels "isolated model store"',
            'Assert-ExistingFile $ExpectedNormalApp "normal Ollama app"',
            'Assert-ExistingFile $ExpectedNormalServer "normal Ollama server"',
            'Assert-ImmediateCreateOnlyChild $EvidenceRoot "evidence root"',
            'Assert-ImmediateCreateOnlyChild $OperationRoot "operation root"',
        ):
            with self.subTest(guard=guard):
                position = self.source.rindex(guard)
                self.assertGreater(position, validate_only)
                self.assertGreater(position, execute)
                self.assertLess(position, first_operation)
        self.assertLess(self.source.index("Assert-ExternalCreateOnlyChild $EvidenceRoot"), validate_only)
        self.assertLess(self.source.index("Assert-ExternalCreateOnlyChild $OperationRoot"), validate_only)

    @unittest.skipUnless(os.name == "nt", "PowerShell wrapper semantics are Windows-specific")
    def test_validate_only_path_is_present_for_harmless_preflight_fixture(self) -> None:
        self.assertIn("V5 wrapper validation passed without process, network, or filesystem mutation", self.source)


if __name__ == "__main__":
    unittest.main()
