from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from shutil import copyfile, copytree, which

from anachron.v5_contract import (
    EXPECTED_RUNTIME_IDENTITY,
    V5_GOVERNED_SOURCE_PATHS,
    V5_PROTOCOL_BRANCH,
    V5_PROTOCOL_TAG,
)
from anachron.v5_measurement import _PENDING_STATEMENT
from anachron.v5_registry import canonical_json_bytes
from tests import test_v5_carry_forward as carry_forward_tests
from tools.build_v5_source_manifest import (
    V5SourceManifestError,
    build,
    derive,
    validate,
)


def _git(root: Path, *arguments: str, environment: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
        timeout=30,
    ).stdout.strip()


class V5SourceManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def _clean_child_environment(self) -> dict[str, str]:
        environment = {
            name: value
            for name, value in os.environ.items()
            if name not in {"PYTHONHOME", "PYTHONPATH"} and not name.startswith("GIT_")
        }
        environment["GIT_ALLOW_PROTOCOL"] = "file"
        environment["GIT_CONFIG_GLOBAL"] = os.devnull
        environment["GIT_CONFIG_NOSYSTEM"] = "1"
        return environment

    def _run(self, arguments: list[str], *, cwd: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            arguments,
            capture_output=True,
            check=False,
            cwd=cwd,
            env=environment,
            text=True,
            timeout=90,
        )

    def _test_only_go(self, materialization: Path, output: Path, destination: Path, *, pending: bool) -> Path:
        full_raw = (materialization / "full_plan.json").read_bytes()
        full = json.loads(full_raw)
        component = full["component_sha256"]
        source_raw = (materialization / "source_manifest.json").read_bytes()
        value = {
            "acceptance_matrix_sha256": hashlib.sha256((self.root / "research/v5_measurement/ACCEPTANCE_MATRIX.md").read_bytes()).hexdigest(),
            "analyzer_sha256": component["analyzer"],
            "authority_contract_sha256": hashlib.sha256((self.root / "research/v5_measurement/authority_binding_contract.json").read_bytes()).hexdigest(),
            "authorized_at_utc": "" if pending else "2026-09-06T00:00:00Z",
            "authorized_by": "" if pending else "TEST-ONLY integration fixture",
            "carry_forward_sha256": hashlib.sha256((materialization / "carry_forward.json").read_bytes()).hexdigest(),
            "compatibility_plan_sha256": hashlib.sha256((materialization / "compatibility_plan.json").read_bytes()).hexdigest(),
            "decision": "PENDING" if pending else "GO",
            "expected_runtime": full["expected_runtime"],
            "full_plan_sha256": hashlib.sha256(full_raw).hexdigest(),
            "kind": "anachron-v5-conditional-measurement-authorization",
            "materialization_receipt_sha256": hashlib.sha256((materialization / "materialization_receipt.json").read_bytes()).hexdigest(),
            "output_root": str(output.resolve()),
            "protocol_commit": full["protocol_release"]["commit"],
            "protocol_tag": full["protocol_release"]["tag"],
            "protocol_tag_object": full["protocol_release"]["tag_object"],
            "runner_sha256": component["runner"],
            "source_manifest_sha256": hashlib.sha256(source_raw).hexdigest(),
            "statement": _PENDING_STATEMENT if pending else "TEST-ONLY disposable preflight authorization.",
            "v4_included_count": 0,
            "wrapper_sha256": component["wrapper"],
        }
        destination.write_bytes(canonical_json_bytes(value))
        return destination

    @contextmanager
    def _test_only_release(self) -> Iterator[dict[str, object]]:
        with tempfile.TemporaryDirectory(prefix="anachron-v5-test-only-") as temporary:
            workspace = Path(temporary)
            environment = self._clean_child_environment()
            bare = workspace / "test-only-remote.git"
            source = workspace / "test-only-source"
            detached = workspace / "test-only-detached"
            manifest = workspace / "test-only-source-manifest.json"
            runtime = workspace / "test-only-runtime-identity.json"
            materialization = workspace / "test-only-materialization"
            evidence = workspace / "test-only-evidence"
            operation = workspace / "test-only-operation"
            subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True, env=environment, timeout=30)
            subprocess.run(["git", "init", "-b", V5_PROTOCOL_BRANCH, str(source)], check=True, capture_output=True, env=environment, timeout=30)
            _git(source, "config", "user.email", "test@example.invalid", environment=environment)
            _git(source, "config", "user.name", "V5 Test", environment=environment)
            _git(source, "config", "core.autocrlf", "false", environment=environment)
            _git(source, "config", "protocol.file.allow", "always", environment=environment)
            _git(source, "config", f"url.{bare.resolve().as_uri()}.insteadOf", "https://github.com/LesterALeong/anachron.git", environment=environment)
            _git(source, "remote", "add", "origin", "https://github.com/LesterALeong/anachron.git", environment=environment)
            governed_bytes = {relative: (self.root / relative).read_bytes() for relative in V5_GOVERNED_SOURCE_PATHS}
            v4_registry = json.loads((self.root / "research/v4_measurement/case_registry.json").read_text(encoding="utf-8"))
            v4_bytes = {
                relative: (self.root / relative).read_bytes()
                for relative in [
                    "research/v4_measurement/case_registry.json",
                    *(f"research/v4_measurement/{row['case_card']}" for row in v4_registry["cases"]),
                ]
            }
            for relative, raw in governed_bytes.items():
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            for relative, raw in v4_bytes.items():
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            _git(source, "add", ".", environment=environment)
            _git(source, "commit", "-m", "test-only v5 release", environment=environment)
            _git(source, "tag", "-a", V5_PROTOCOL_TAG, "-m", "test-only v5 release", environment=environment)
            _git(source, "push", "origin", V5_PROTOCOL_BRANCH, "--tags", environment=environment)
            _git(source, "worktree", "add", "--detach", str(detached), V5_PROTOCOL_TAG, environment=environment)
            self.assertEqual(_git(detached, "config", "--get", "remote.origin.url", environment=environment), "https://github.com/LesterALeong/anachron.git")
            self.assertEqual(_git(detached, "status", "--porcelain", "--untracked-files=all", environment=environment), "")
            self.assertEqual({relative: (detached / relative).read_bytes() for relative in V5_GOVERNED_SOURCE_PATHS}, governed_bytes)
            self.assertEqual({relative: (detached / relative).read_bytes() for relative in v4_bytes}, v4_bytes)
            self.assertEqual(build(detached, manifest), validate(detached, manifest))

            carry_forward_fixture = carry_forward_tests.V5CarryForwardTests()
            carry_forward_fixture.root = self.root
            accepted_audit, v4_manifest = carry_forward_fixture._v4_fixture(workspace)
            runtime_value = {"models": list(EXPECTED_RUNTIME_IDENTITY["models"]), "version": EXPECTED_RUNTIME_IDENTITY["version"]}
            runtime_raw = canonical_json_bytes(runtime_value)
            runtime.write_bytes(runtime_raw)
            materializer = self._run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "tools.materialize_v5_inputs",
                    "--repository-root",
                    str(detached),
                    "--accepted-audit",
                    str(accepted_audit),
                    "--v4-source-manifest",
                    str(v4_manifest),
                    "--v5-source-manifest",
                    str(manifest),
                    "--runtime-identity",
                    str(runtime),
                    "--output",
                    str(materialization),
                    "--evidence-output-root",
                    str(evidence),
                ],
                cwd=detached,
                environment=environment,
            )
            self.assertEqual(materializer.returncode, 0, materializer.stderr + materializer.stdout)
            self.assertEqual(
                {path.name for path in materialization.iterdir()},
                {"carry_forward.json", "compatibility_plan.json", "full_plan.json", "materialization_receipt.json", "runtime_identity.json", "schedule.json", "source_manifest.json"},
            )
            self.assertEqual((materialization / "runtime_identity.json").read_bytes(), runtime_raw)
            self.assertFalse(evidence.exists())
            self.assertFalse(operation.exists())
            yield {
                "detached": detached,
                "environment": environment,
                "evidence": evidence,
                "manifest": manifest,
                "materialization": materialization,
                "operation": operation,
                "runtime": runtime,
                "runtime_raw": runtime_raw,
                "runtime_value": runtime_value,
                "workspace": workspace,
            }
            self.assertEqual(_git(source, "status", "--porcelain", "--untracked-files=all", environment=environment), "")
            self.assertEqual(_git(detached, "status", "--porcelain", "--untracked-files=all", environment=environment), "")
            self.assertEqual({relative: (source / relative).read_bytes() for relative in V5_GOVERNED_SOURCE_PATHS}, governed_bytes)
            self.assertEqual({relative: (source / relative).read_bytes() for relative in v4_bytes}, v4_bytes)

    def _runner(self, fixture: dict[str, object], authorization: Path, mode: str) -> subprocess.CompletedProcess[str]:
        detached = fixture["detached"]
        materialization = fixture["materialization"]
        evidence = fixture["evidence"]
        environment = fixture["environment"]
        assert isinstance(detached, Path)
        assert isinstance(materialization, Path)
        assert isinstance(evidence, Path)
        assert isinstance(environment, dict)
        return self._run(
            [
                sys.executable,
                "-B",
                "-m",
                "tools.run_v5_recovery",
                "--repository-root",
                str(detached),
                "--full-plan",
                str(materialization / "full_plan.json"),
                "--conditional-go",
                str(authorization),
                "--output",
                str(evidence),
                mode,
            ],
            cwd=detached,
            environment=environment,
        )

    def test_derives_real_annotated_tag_objects_and_blobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            remote = workspace / "remote.git"
            root = workspace / "source"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", "-b", V5_PROTOCOL_BRANCH, str(root)], check=True, capture_output=True)
            _git(root, "config", "user.email", "test@example.invalid")
            _git(root, "config", "user.name", "V5 Test")
            _git(root, "config", "core.autocrlf", "false")
            _git(root, "remote", "add", "origin", str(remote))
            (root / "governed.txt").write_bytes(b"frozen\n")
            _git(root, "add", "governed.txt")
            _git(root, "commit", "-m", "freeze")
            _git(root, "tag", "-a", V5_PROTOCOL_TAG, "-m", "freeze")
            _git(root, "push", "origin", V5_PROTOCOL_BRANCH, "--tags")
            commit = _git(root, "rev-parse", "HEAD")
            with self.assertRaises(V5SourceManifestError):
                derive(root, expected_origin=str(remote), governed_paths=("governed.txt",))
            detached = workspace / "detached"
            _git(root, "worktree", "add", "--detach", str(detached), V5_PROTOCOL_TAG)
            manifest = derive(
                detached,
                expected_origin=str(remote),
                expected_release={
                    "branch_ref": commit,
                    "commit": commit,
                    "tag_object": _git(detached, "rev-parse", f"refs/tags/{V5_PROTOCOL_TAG}^{{tag}}"),
                    "tag_peeled": commit,
                },
                governed_paths=("governed.txt",),
            )
            self.assertEqual(manifest["release"]["tag_peeled"], commit)
            self.assertEqual(manifest["governed_files"][0]["tag_blob_oid"], _git(detached, "rev-parse", "HEAD:governed.txt"))

    def test_builds_and_validates_default_closure_from_clean_detached_local_bare_git(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            remote = workspace / "remote.git"
            source = workspace / "source"
            detached = workspace / "detached"
            output = workspace / "source-manifest.json"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(
                ["git", "init", "-b", V5_PROTOCOL_BRANCH, str(source)],
                check=True,
                capture_output=True,
            )
            _git(source, "config", "user.email", "test@example.invalid")
            _git(source, "config", "user.name", "V5 Test")
            _git(source, "config", "core.autocrlf", "true")
            _git(source, "remote", "add", "origin", str(remote))
            for relative in V5_GOVERNED_SOURCE_PATHS:
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                copyfile(repository / relative, target)
            _git(source, "add", ".")
            _git(source, "commit", "-m", "freeze default closure")
            _git(source, "tag", "-a", V5_PROTOCOL_TAG, "-m", "freeze default closure")
            _git(source, "push", "origin", V5_PROTOCOL_BRANCH, "--tags")
            _git(source, "worktree", "add", "--detach", str(detached), V5_PROTOCOL_TAG)

            self.assertEqual(_git(detached, "status", "--porcelain", "--untracked-files=all"), "")
            self.assertEqual(_git(detached, "branch", "--show-current"), "")
            self.assertEqual(
                _git(detached, "check-attr", "eol", "--", "tools/.gitattributes"),
                "tools/.gitattributes: eol: lf",
            )
            self.assertEqual(
                _git(detached, "check-attr", "eol", "--", "tools/run_v5_conditional_campaign.ps1"),
                "tools/run_v5_conditional_campaign.ps1: eol: lf",
            )
            manifest = build(detached, output, expected_origin=str(remote))

            self.assertEqual(validate(detached, output, expected_origin=str(remote)), manifest)
            self.assertEqual(manifest["governed_paths"], list(V5_GOVERNED_SOURCE_PATHS))
            self.assertEqual(len(manifest["governed_files"]), len(V5_GOVERNED_SOURCE_PATHS))
            self.assertEqual(manifest["release"]["tag"], V5_PROTOCOL_TAG)
            self.assertEqual(manifest["release"]["commit"], _git(detached, "rev-parse", "HEAD"))

    def test_test_only_release_runs_real_materializer_and_go_pending_preflights(self) -> None:
        with self._test_only_release() as fixture:
            workspace = fixture["workspace"]
            materialization = fixture["materialization"]
            evidence = fixture["evidence"]
            operation = fixture["operation"]
            assert isinstance(workspace, Path)
            assert isinstance(materialization, Path)
            assert isinstance(evidence, Path)
            assert isinstance(operation, Path)
            go = self._test_only_go(materialization, evidence, workspace / "test-only-go.json", pending=False)
            pending = self._test_only_go(materialization, evidence, workspace / "test-only-pending.json", pending=True)

            go_preflight = self._runner(fixture, go, "--preflight-only")
            self.assertEqual(go_preflight.returncode, 0, go_preflight.stderr + go_preflight.stdout)
            self.assertEqual(go_preflight.stdout, '{"status": "PREFLIGHT_OK"}\n')
            pending_validation = self._runner(fixture, pending, "--pending-only")
            self.assertEqual(pending_validation.returncode, 0, pending_validation.stderr + pending_validation.stdout)
            self.assertEqual(pending_validation.stdout, '{"status": "PENDING_VALID"}\n')
            self.assertNotEqual(self._runner(fixture, pending, "--preflight-only").returncode, 0)
            self.assertNotEqual(self._runner(fixture, go, "--pending-only").returncode, 0)
            self.assertFalse(evidence.exists())
            self.assertFalse(operation.exists())

    @unittest.skipUnless(os.name == "nt", "requires Windows PowerShell validation")
    def test_windows_wrapper_validate_only_rejects_test_only_closure_drift(self) -> None:
        executable = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if not Path(executable).is_file():
            self.skipTest("Windows PowerShell is unavailable")
        with self._test_only_release() as fixture:
            detached = fixture["detached"]
            environment = fixture["environment"]
            evidence = fixture["evidence"]
            materialization = fixture["materialization"]
            operation = fixture["operation"]
            runtime = fixture["runtime"]
            runtime_raw = fixture["runtime_raw"]
            runtime_value = fixture["runtime_value"]
            workspace = fixture["workspace"]
            assert isinstance(detached, Path)
            assert isinstance(environment, dict)
            assert isinstance(evidence, Path)
            assert isinstance(materialization, Path)
            assert isinstance(operation, Path)
            assert isinstance(runtime, Path)
            assert isinstance(runtime_raw, bytes)
            assert isinstance(runtime_value, dict)
            assert isinstance(workspace, Path)
            self.assertNotEqual(runtime.resolve(), (materialization / "runtime_identity.json").resolve())
            self.assertEqual(
                len(json.loads((materialization / "source_manifest.json").read_text(encoding="utf-8"))["governed_files"]),
                47,
            )
            go = self._test_only_go(materialization, evidence, workspace / "test-only-go.json", pending=False)
            wrapper_environment = dict(environment)
            wrapper_environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + wrapper_environment["PATH"]
            shells = [("Windows PowerShell", executable)]
            if pwsh := which("pwsh"):
                shells.append(("pwsh", pwsh))

            def validate(
                materialization_root: Path,
                captured_runtime: Path,
                conditional_go: Path = go,
                *,
                shell: str = executable,
                environment_override: dict[str, str] | None = None,
            ) -> subprocess.CompletedProcess[str]:
                return self._run(
                    [
                        shell,
                        "-NoProfile",
                        "-File",
                        str(detached / "tools" / "run_v5_conditional_campaign.ps1"),
                        "-ProtocolRoot",
                        str(detached),
                        "-MaterializationRoot",
                        str(materialization_root),
                        "-RuntimeIdentity",
                        str(captured_runtime),
                        "-MaterializationReceipt",
                        str(materialization_root / "materialization_receipt.json"),
                        "-ConditionalGo",
                        str(conditional_go),
                        "-EvidenceRoot",
                        str(evidence),
                        "-OperationRoot",
                        str(operation),
                        "-ValidateOnly",
                    ],
                    cwd=detached,
                    environment=environment_override or wrapper_environment,
                )

            unrelated_sibling = workspace / "unrelated-evidence-sibling"
            unrelated_sibling.write_bytes(b"test-only sibling")
            Path(str(unrelated_sibling) + ":ignored").write_bytes(b"test-only ADS")
            shadowed_module_path = workspace / "test-only-shadowed-modules"
            shadowed_module_path.mkdir()
            for shell_label, shell in shells:
                for module_path_label, module_path in (("empty", ""), ("shadowed", str(shadowed_module_path))):
                    with self.subTest(shell=shell_label, ps_module_path=module_path_label):
                        module_environment = dict(wrapper_environment)
                        module_environment["PSModulePath"] = module_path
                        result = validate(
                            materialization,
                            runtime,
                            shell=shell,
                            environment_override=module_environment,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                        self.assertIn("V5 wrapper validation passed without process, network, or filesystem mutation", result.stdout)
            self.assertFalse(evidence.exists())
            self.assertFalse(operation.exists())

            python_tripwire = workspace / "python-preflight-tripwire"
            python_tripwire.mkdir()
            (python_tripwire / "sitecustomize.py").write_text(
                "import os\nfrom pathlib import Path\nPath(os.environ['ANACHRON_V5_PYTHON_PREFLIGHT_MARKER']).write_text('invoked', encoding='utf-8')\n",
                encoding="utf-8",
            )
            for shell_label, shell in shells:
                with self.subTest(shell=shell_label, git_index_failure=True):
                    marker = workspace / f"python-preflight-{shell_label.replace(' ', '-').lower()}.marker"
                    invalid_index = workspace / f"invalid-{shell_label.replace(' ', '-').lower()}.index"
                    invalid_index.write_bytes(b"not a Git index")
                    hostile_environment = dict(wrapper_environment)
                    hostile_environment.update(
                        {
                            "ANACHRON_V5_PYTHON_PREFLIGHT_MARKER": str(marker),
                            "GIT_INDEX_FILE": str(invalid_index),
                            "PYTHONPATH": str(python_tripwire),
                        }
                    )
                    result = validate(
                        materialization,
                        runtime,
                        shell=shell,
                        environment_override=hostile_environment,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("protocol git status failed", result.stderr + result.stdout)
                    self.assertFalse(marker.exists())
                    self.assertFalse(evidence.exists())
                    self.assertFalse(operation.exists())

            member_ads_root = workspace / "test-only-hostile-member-ads"
            copytree(materialization, member_ads_root)
            Path(str(member_ads_root / "schedule.json") + ":blocked").write_bytes(b"test-only ADS")
            member_ads_result = validate(member_ads_root, runtime)
            self.assertNotEqual(member_ads_result.returncode, 0)
            self.assertIn("materialized schedule contains an alternate data stream", member_ads_result.stderr + member_ads_result.stdout)

            runtime_junction = workspace / "test-only-runtime-junction"
            junction = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(runtime_junction), str(workspace)],
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
            )
            self.assertEqual(junction.returncode, 0, junction.stderr + junction.stdout)
            try:
                reparse_result = validate(materialization, runtime_junction / runtime.name)
                self.assertNotEqual(reparse_result.returncode, 0)
                self.assertIn("runtime identity contains a reparse point", reparse_result.stderr + reparse_result.stdout)
            finally:
                runtime_junction.rmdir()

            semantic_equal_runtime = workspace / "test-only-runtime-with-whitespace.json"
            semantic_equal_runtime.write_bytes(json.dumps(runtime_value, indent=4, sort_keys=True).encode("utf-8") + b"\n")
            self.assertNotEqual(semantic_equal_runtime.read_bytes(), runtime_raw)
            self.assertNotEqual(validate(materialization, semantic_equal_runtime).returncode, 0)

            oversized_runtime = workspace / "test-only-oversized-runtime.json"
            oversized_runtime.write_bytes(b"x" * 1_048_577)
            oversized_runtime_result = validate(materialization, oversized_runtime)
            self.assertNotEqual(oversized_runtime_result.returncode, 0)
            self.assertIn("runtime identity exceeds the fixed byte cap", oversized_runtime_result.stderr + oversized_runtime_result.stdout)
            oversized_go = workspace / "test-only-oversized-go.json"
            oversized_go.write_bytes(b"x" * 1_048_577)
            oversized_go_result = validate(materialization, runtime, oversized_go)
            self.assertNotEqual(oversized_go_result.returncode, 0)
            self.assertIn("conditional GO exceeds the fixed byte cap", oversized_go_result.stderr + oversized_go_result.stdout)

            for label, member, replacement in (
                ("runtime", "runtime_identity.json", b"{}\n"),
                ("receipt", "materialization_receipt.json", b"{}\n"),
                ("schedule", "schedule.json", b"{}\n"),
                ("missing", "schedule.json", None),
                ("extra", "unexpected.json", b"{}\n"),
            ):
                hostile_root = workspace / f"test-only-hostile-{label}"
                copytree(materialization, hostile_root)
                target = hostile_root / member
                if replacement is None:
                    target.unlink()
                else:
                    target.write_bytes(replacement)
                with self.subTest(label=label):
                    result = validate(hostile_root, runtime)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(evidence.exists())
                    self.assertFalse(operation.exists())

            oversized_receipt_root = workspace / "test-only-hostile-oversized-receipt"
            copytree(materialization, oversized_receipt_root)
            (oversized_receipt_root / "materialization_receipt.json").write_bytes(b"x" * 1_048_577)
            oversized_receipt_result = validate(oversized_receipt_root, runtime)
            self.assertNotEqual(oversized_receipt_result.returncode, 0)
            self.assertIn("materialization receipt exceeds the fixed byte cap", oversized_receipt_result.stderr + oversized_receipt_result.stdout)
            self.assertFalse(evidence.exists())
            self.assertFalse(operation.exists())


if __name__ == "__main__":
    unittest.main()
