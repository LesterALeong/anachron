from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_PATH = ROOT / "tools" / "capture_read_only_v5_identity.py"
SPEC = importlib.util.spec_from_file_location("v5_identity_controller", CONTROLLER_PATH)
assert SPEC is not None and SPEC.loader is not None
controller = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = controller
SPEC.loader.exec_module(controller)


CANDIDATE04_ACCEPTANCE_ROWS = frozenset(
    [*(f"P{index:02d}" for index in range(1, 21))]
    + [*(f"O{index:02d}" for index in range(1, 6))]
    + [*(f"R{index:02d}" for index in range(1, 19))]
    + [*(f"C{index:02d}" for index in range(1, 7))]
    + [*(f"D{index:02d}" for index in range(1, 8)), "D08a", "D08b", "D08c", "D08d", "D09", "D10"]
    + [*(f"T{index:02d}" for index in range(1, 15))]
)
BIRTH_TOKEN_ROWS = frozenset(f"B{index:02d}" for index in range(1, 9))
RESTORATION_FAULT_ROWS = frozenset(f"F{index:02d}" for index in range(1, 7))
STARTUP_ROWS = frozenset(f"S{index:02d}" for index in range(1, 10))
JOB_ROWS = (
    ("J01", "typed Win32 Job structures and declarations"),
    ("J02", "detached suspended root launch"),
    ("J03", "non-breakaway kill-on-close Job containment"),
    ("J04", "caller-owned lifecycle ordering"),
    ("J05", "CreateJobObject and Job configuration custody"),
    ("J06", "Popen and post-Popen root custody"),
    ("J07", "suspended-root PID validation"),
    ("J08", "OpenProcess assignment handle acquisition"),
    ("J09", "AssignProcessToJobObject failure custody"),
    ("J10", "assignment-handle close failure custody"),
    ("J11", "identity bind failure custody"),
    ("J12", "stdout and stderr drain acquisition custody"),
    ("J13", "suspended thread cardinality and OpenThread"),
    ("J14", "thread owner and ResumeThread validation"),
    ("J15", "successful ResumeThread then failed CloseHandle"),
    ("J16", "no-root and Job-only teardown dispatch"),
    ("J17", "unassigned suspended-root teardown dispatch"),
    ("J18", "assigned-Job teardown dispatch"),
    ("J19", "Job terminate and root-wait cleanup failures"),
    ("J20", "Job accounting and zero-window cleanup failures"),
    ("J21", "pipe, drain, and Job-close cleanup failures"),
    ("J22", "monotonic cleanup facts and pure restoration predicate"),
    ("J23", "exact production startup helper trace admission"),
    ("J24", "persisted pre-return lifecycle failure matrix"),
    ("J25", "successful assigned-Job run-capture restoration"),
)
JOB_ROW_IDS = tuple(row_id for row_id, _obligation in JOB_ROWS)
JOB_ROW_SET = frozenset(JOB_ROW_IDS)
PREPUBLICATION_FAULT_STAGES = (
    "after-process-observation",
    "after-version-read",
    "after-tags-read",
    "after-raw-comparison",
    "after-final-topology",
    "before-publication",
)
DECLARED_EXECUTED_ROWS = (
    CANDIDATE04_ACCEPTANCE_ROWS
    | BIRTH_TOKEN_ROWS
    | RESTORATION_FAULT_ROWS
    | STARTUP_ROWS
    | JOB_ROW_SET
)


def birth_token(value: Any) -> str:
    return controller.encode_birth_token_hex(value)


def forged_process_record(
    pid: int,
    ppid: int,
    name: str,
    executable: Path,
    token: object,
) -> controller.ProcessRecord:
    record = object.__new__(controller.ProcessRecord)
    object.__setattr__(record, "pid", pid)
    object.__setattr__(record, "ppid", ppid)
    object.__setattr__(record, "name", name)
    object.__setattr__(record, "exe", executable)
    object.__setattr__(record, "birth_token_hex", token)
    return record


def idle_process_info() -> dict[str, object]:
    return {
        "pid": 0,
        "ppid": 0,
        "name": "System Idle Process",
        "exe": controller.PROCESS_FIELD_UNAVAILABLE,
        "create_time": 0.0,
        "cmdline": [],
    }


def process_info(**overrides: object) -> dict[str, object]:
    info: dict[str, object] = {
        "pid": 41,
        "ppid": None,
        "name": "service.exe",
        "exe": None,
        "create_time": None,
        "cmdline": None,
    }
    info.update(overrides)
    return info


def test_owned_executable(root: Path, label: str, leaf: str) -> Path:
    separators = (os.sep,) if os.altsep is None else (os.sep, os.altsep)
    if (
        not leaf
        or leaf in {".", ".."}
        or Path(leaf).is_absolute()
        or any(separator in leaf for separator in separators)
    ):
        raise ValueError("executable leaf must be a host-native filename")
    directory = root / label
    directory.mkdir(exist_ok=True)
    executable = directory / leaf
    with executable.open("xb") as stream:
        stream.write(b"test executable\n")
    resolved = executable.resolve(strict=True)
    assert resolved.is_relative_to(root.resolve(strict=True))
    assert resolved.is_absolute()
    assert resolved.is_file()
    assert resolved.name == leaf
    return resolved


class IdentityControllerTests(unittest.TestCase):
    executed_rows: ClassVar[set[str]] = set()

    def subTest(self, msg: object = None, **params: object) -> Any:
        row_id = params.get("id")
        if isinstance(row_id, str) and row_id in DECLARED_EXECUTED_ROWS:
            self.executed_rows.add(row_id)
        return super().subTest(msg, **params)

    @unittest.skipUnless(os.name == "nt", "requires the pinned Windows capture host")
    def test_capture_host_dependencies_match_pinned_identity(self) -> None:
        identity = controller.controller_dependency_identity()
        self.assertEqual(identity["powershell_executable"], str(controller.POWERSHELL_EXE))
        self.assertEqual(identity["powershell_sha256"], controller.POWERSHELL_EXE_SHA256)
        self.assertEqual(
            identity["python_version"],
            ".".join(str(part) for part in controller.EXPECTED_PYTHON_VERSION),
        )
        self.assertEqual(identity["psutil_version"], controller.EXPECTED_PSUTIL_VERSION)

    def test_library_import_has_no_operational_side_effects(self) -> None:
        self.assertTrue(callable(controller.run_capture))
        self.assertEqual(controller.ADMITTED_ENDPOINTS, frozenset(("/api/version", "/api/tags")))
        self.assertEqual(controller.PROTOCOL_ROOT, Path(r"C:\Users\leste\Downloads\Repos\anachron-v5-protocol-v5"))

    def test_v4_failure_binding_rejects_a_tampered_member_before_normal_stop(self) -> None:
        binding = json.loads((ROOT / "research" / "v5_measurement" / "v4_failure_binding.json").read_text(encoding="utf-8"))
        members = binding["failure_root"]["members"]
        status = binding["failure_root"]["status"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binding_path = root / "research" / "v5_measurement" / "v4_failure_binding.json"
            binding_path.parent.mkdir(parents=True)
            failure_root = root / "v4-failure"
            failure_root.mkdir()
            binding["failure_root"]["path"] = str(failure_root)
            binding_path.write_text(json.dumps(binding), encoding="utf-8")
            for member in members:
                path = failure_root / member["path"]
                if path.name == "operation_status.json":
                    raw = json.dumps(status).encode("utf-8")
                    path.write_bytes(raw + b" " * (member["bytes"] - len(raw)))
                else:
                    path.write_bytes(b"x" * member["bytes"])
            hashes = {member["path"]: member["sha256"] for member in members}
            manifest = {"governed_files": [{"path": controller.V4_FAILURE_BINDING.as_posix()}]}
            with (
                patch.object(controller, "PROTOCOL_ROOT", root),
                patch.object(controller, "V4_FAILURE_ROOT", failure_root),
                patch.object(controller, "sha256_file", side_effect=lambda path: hashes[path.name]),
            ):
                controller.verify_v4_failure_binding(manifest)
                (failure_root / "baseline-processes.json").write_bytes(b"x" * 471)
                with self.assertRaisesRegex(controller.CaptureError, "v4 failure root member differs"):
                    controller.verify_v4_failure_binding(manifest)

    def test_loopback_request_emits_exactly_one_host_header(self) -> None:
        def host_fields(request: bytes) -> list[bytes]:
            return [line for line in request.split(b"\r\n") if line.lower().startswith(b"host:")]

        def serve_once(expected_host_count: int) -> tuple[int, threading.Event, list[bytes], list[OSError | AssertionError]]:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.addCleanup(listener.close)
            listener.bind((controller.HOST, 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            completed = threading.Event()
            requests: list[bytes] = []
            errors: list[OSError | AssertionError] = []

            def serve() -> None:
                try:
                    connection, _ = listener.accept()
                    with connection:
                        connection.settimeout(5)
                        request = bytearray()
                        while b"\r\n\r\n" not in request:
                            chunk = connection.recv(8192)
                            if not chunk:
                                raise AssertionError("loopback client closed before headers")
                            request.extend(chunk)
                        captured = bytes(request)
                        requests.append(captured)
                        host_count = len(host_fields(captured))
                        status = 200 if host_count == expected_host_count else 400
                        body = b"{}"
                        connection.sendall(
                            f"HTTP/1.1 {status} {'OK' if status == 200 else 'Bad Request'}\r\n"
                            f"Content-Length: {len(body)}\r\n"
                            "\r\n".encode("ascii")
                            + body
                        )
                        connection.recv(1)
                except (OSError, AssertionError) as error:
                    errors.append(error)
                finally:
                    completed.set()

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 5)
            return port, completed, requests, errors

        def tagged_v3_controller(directory: Path) -> object:
            tagged = subprocess.run(
                ["git", "show", "v5-measurement-protocol-v3:tools/capture_read_only_v5_identity.py"],
                cwd=ROOT,
                check=False,
                capture_output=True,
            )
            self.assertEqual(tagged.returncode, 0, tagged.stderr.decode("utf-8", errors="replace"))
            source = directory / "v3_capture_read_only_v5_identity.py"
            source.write_bytes(tagged.stdout)
            spec = importlib.util.spec_from_file_location("v5_identity_controller_v3", source)
            self.assertIsNotNone(spec)
            assert spec is not None and spec.loader is not None
            tagged_controller = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = tagged_controller
            self.addCleanup(sys.modules.pop, spec.name, None)
            spec.loader.exec_module(tagged_controller)
            return tagged_controller

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            v3_controller = tagged_v3_controller(workspace)
            v3_port, v3_completed, v3_requests, v3_errors = serve_once(expected_host_count=1)
            with self.assertRaises(v3_controller.CaptureError) as captured_v3_error:
                v3_controller.stream_loopback_get(v3_port, "/api/version", workspace / "v3.response.json")
            self.assertIn(
                "loopback response status differs for /api/version: 400",
                str(captured_v3_error.exception),
            )
            self.assertTrue(v3_completed.wait(5))
            self.assertFalse(v3_errors)
            self.assertEqual(
                host_fields(v3_requests[0]),
                [f"Host: {controller.HOST}:{v3_port}".encode("ascii")] * 2,
            )

            v4_port, v4_completed, v4_requests, v4_errors = serve_once(expected_host_count=1)
            response = workspace / "v4.response.json"
            controller.stream_loopback_get(v4_port, "/api/version", response)
            self.assertTrue(v4_completed.wait(5))
            self.assertFalse(v4_errors)
            self.assertEqual(
                host_fields(v4_requests[0]),
                [f"Host: {controller.HOST}:{v4_port}".encode("ascii")],
            )
            self.assertEqual(response.read_bytes(), b"{}")

    def test_authenticode_uses_fixed_helper_argv_for_metacharacter_path(self) -> None:
        executable = Path(tempfile.gettempdir()) / "signed & literal; path.exe"
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"subject": "CN=Test", "thumbprint": "ab12"}).encode("utf-8"),
            stderr=b"",
        )
        with patch.object(controller, "run_bounded_subprocess", return_value=(completed.returncode, completed.stdout, completed.stderr)) as run:
            self.assertEqual(controller.authenticode_identity(executable, ROOT / "tools" / "read_v5_authenticode_identity.ps1"), ("CN=Test", "AB12"))
        command = run.call_args.args[0]
        self.assertIn("-File", command)
        self.assertIn("-LiteralPath", command)
        self.assertNotIn("-ExecutionPolicy", command)
        self.assertEqual(command[-1], str(executable))
        self.assertEqual(run.call_args.args[1], "isolated executable Authenticode validation")

    def test_authenticode_rejects_nonzero_and_malformed_output(self) -> None:
        for completed in (
            SimpleNamespace(returncode=7, stdout=b"", stderr=b""),
            SimpleNamespace(returncode=0, stdout=b"not json", stderr=b""),
            SimpleNamespace(returncode=0, stdout=json.dumps({"subject": "CN=Test"}).encode("utf-8"), stderr=b""),
        ):
            with self.subTest(completed=completed), patch.object(controller, "run_bounded_subprocess", return_value=(completed.returncode, completed.stdout, completed.stderr)), self.assertRaises(controller.CaptureError):
                controller.authenticode_identity(Path(tempfile.gettempdir()) / "missing.exe", ROOT / "tools" / "read_v5_authenticode_identity.ps1")

    def test_helper_must_match_the_pinned_governed_blob(self) -> None:
        helper = ROOT / "tools" / "read_v5_authenticode_identity.ps1"
        process_helper = ROOT / "tools" / "read_v5_process_identity.ps1"
        manifest = {"governed_files": [{"path": "tools/read_v5_authenticode_identity.ps1", "sha256": controller.sha256_file(helper)}, {"path": "tools/read_v5_process_identity.ps1", "sha256": controller.sha256_file(process_helper)}]}
        with patch.object(controller, "PROTOCOL_ROOT", ROOT):
            self.assertEqual(controller.verify_tracked_authenticode_helper(manifest), helper.resolve())
            manifest["governed_files"][0]["sha256"] = "0" * 64
            with self.assertRaises(controller.CaptureError):
                controller.verify_tracked_authenticode_helper(manifest)

    def test_controller_authority_accepts_sha1_git_ids_only_when_canonical(self) -> None:
        controller_sha256 = controller.sha256_file(CONTROLLER_PATH)
        authority = {
            "controller_sha256": controller_sha256,
            "protocol_commit": "a" * 40,
            "protocol_tag": controller.PROTOCOL_TAG,
            "protocol_tag_object": "b" * 40,
            "schema_version": controller.AUTHORITY_SCHEMA,
            "source_manifest_sha256": "c" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "controller_authority.json"
            sidecar.write_bytes(controller.receipt_bytes(authority))
            with patch.object(controller, "AUTHORITY_SIDECAR", sidecar):
                self.assertEqual(controller.validate_controller_authority(controller_sha256)[0], authority)
                authority["protocol_commit"] = "a" * 64
                sidecar.write_bytes(controller.receipt_bytes(authority))
                with self.assertRaises(controller.CaptureError):
                    controller.validate_controller_authority(controller_sha256)
                authority["protocol_commit"] = "g" * 40
                sidecar.write_bytes(controller.receipt_bytes(authority))
                with self.assertRaises(controller.CaptureError):
                    controller.validate_controller_authority(controller_sha256)
                authority["protocol_commit"] = "a" * 40
                authority["controller_sha256"] = "0" * 64
                sidecar.write_bytes(controller.receipt_bytes(authority))
                with self.assertRaises(controller.CaptureError):
                    controller.validate_controller_authority(controller_sha256)

    def test_protocol_root_requires_the_resolved_expected_checkout(self) -> None:
        authority = {"protocol_commit": "a" * 40, "protocol_tag_object": "b" * 40}
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as other:
            root = Path(directory)
            responses = {
                ("rev-parse", "--show-toplevel"): str(root),
                ("status", "--porcelain", "--untracked-files=all"): "",
                ("rev-parse", "HEAD"): authority["protocol_commit"],
                ("cat-file", "-t", f"refs/tags/{controller.PROTOCOL_TAG}"): "tag",
                ("rev-parse", f"refs/tags/{controller.PROTOCOL_TAG}^{{tag}}"): authority["protocol_tag_object"],
                ("rev-parse", f"refs/tags/{controller.PROTOCOL_TAG}^{{}}"): authority["protocol_commit"],
            }
            detached = SimpleNamespace(returncode=1)
            with patch.object(controller, "PROTOCOL_ROOT", root), patch.object(controller, "git_output", side_effect=lambda *arguments: responses[arguments]), patch.object(controller.subprocess, "run", return_value=detached):
                controller.verify_protocol(authority)
                responses[("rev-parse", "--show-toplevel")] = other
                with self.assertRaises(controller.CaptureError):
                    controller.verify_protocol(authority)

    def test_protocol_rejects_dirty_attached_and_wrong_tag_identities(self) -> None:
        authority = {"protocol_commit": "a" * 40, "protocol_tag_object": "b" * 40}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            responses = {
                ("rev-parse", "--show-toplevel"): str(root),
                ("status", "--porcelain", "--untracked-files=all"): "",
                ("rev-parse", "HEAD"): authority["protocol_commit"],
                ("cat-file", "-t", f"refs/tags/{controller.PROTOCOL_TAG}"): "tag",
                ("rev-parse", f"refs/tags/{controller.PROTOCOL_TAG}^{{tag}}"): authority["protocol_tag_object"],
                ("rev-parse", f"refs/tags/{controller.PROTOCOL_TAG}^{{}}"): authority["protocol_commit"],
            }
            for key, value, detached in (
                (("status", "--porcelain", "--untracked-files=all"), "M tools/controller.py", 1),
                (("rev-parse", "HEAD"), "c" * 40, 1),
                (("rev-parse", f"refs/tags/{controller.PROTOCOL_TAG}^{{tag}}"), "c" * 40, 1),
                (("status", "--porcelain", "--untracked-files=all"), "", 0),
            ):
                original = responses[key]
                responses[key] = value
                with self.subTest(key=key, value=value), patch.object(controller, "PROTOCOL_ROOT", root), patch.object(controller, "git_output", side_effect=lambda *arguments: responses[arguments]), patch.object(controller.subprocess, "run", return_value=SimpleNamespace(returncode=detached)), self.assertRaises(controller.CaptureError):
                    controller.verify_protocol(authority)
                responses[key] = original

    def test_stop_refuses_reused_or_drifted_identity_without_kill(self) -> None:
        executable = Path(__file__).resolve()
        expected = controller.ProcessRecord(41, 7, "ollama.exe", executable, birth_token(100 / 1_000_000))

        class NoSuchProcess(Exception):
            pass

        class TimeoutExpired(Exception):
            pass

        process = SimpleNamespace(pid=41, ppid=lambda: 7, name=lambda: "ollama.exe", exe=lambda: str(executable), create_time=lambda: 200 / 1_000_000, kill=lambda: self.fail("kill must not run"), wait=lambda timeout: None)
        fake_psutil = SimpleNamespace(NoSuchProcess=NoSuchProcess, TimeoutExpired=TimeoutExpired, AccessDenied=PermissionError, Process=lambda pid: process)
        with patch.object(controller, "psutil", fake_psutil), self.assertRaises(controller.CaptureError):
            controller.force_stop_exact(expected, 7, "reused process")

    def test_stop_exact_identity_uses_retained_child_handle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = test_owned_executable(Path(directory), "isolated", "ollama.exe")
            expected = controller.ProcessRecord(41, 7, "ollama.exe", executable, birth_token(100 / 1_000_000))
            calls: list[str] = []

            class NoSuchProcess(Exception):
                pass

            class TimeoutExpired(Exception):
                pass

            retained = SimpleNamespace(pid=41, kill=lambda: calls.append("kill"), wait=lambda timeout: calls.append("wait"))
            process = SimpleNamespace(pid=41, ppid=lambda: 7, name=lambda: "ollama.exe", exe=lambda: str(executable), create_time=lambda: 100 / 1_000_000, kill=lambda: self.fail("psutil kill must not run"), wait=lambda timeout: None)
            fake_psutil = SimpleNamespace(NoSuchProcess=NoSuchProcess, TimeoutExpired=TimeoutExpired, AccessDenied=PermissionError, Process=lambda pid: process)
            with patch.object(controller, "psutil", fake_psutil):
                controller.force_stop_exact(expected, 7, "isolated child", retained_child=retained)
            self.assertEqual(calls, ["kill", "wait"])

    def test_birth_identity_preserves_binary64_bits_beyond_microsecond_key(self) -> None:
        first = 1_788_742_946.9020934
        second = 1_788_742_946.9020936
        left = SimpleNamespace(pid=1, ppid=lambda: 0, create_time=lambda: first)
        right = SimpleNamespace(pid=1, ppid=lambda: 0, create_time=lambda: second)
        self.assertNotEqual(
            controller.process_birth_identity(left, "left")[2],
            controller.process_birth_identity(right, "right")[2],
        )

    def test_exact_birth_token_acceptance_matrix(self) -> None:
        admitted_time = 1_788_742_946.9020934
        replacement_time = 1_788_742_946.9020936
        admitted_token = "41daa78348b9bbe6"
        replacement_token = "41daa78348b9bbe7"
        executable = Path(__file__).resolve()

        with self.subTest(id="B01"):
            self.assertEqual(birth_token(admitted_time), admitted_token)
            self.assertEqual(birth_token(replacement_time), replacement_token)
            self.assertNotEqual(admitted_token, replacement_token)
            self.assertEqual(round(admitted_time * 1_000_000), round(replacement_time * 1_000_000))
            source = CONTROLLER_PATH.read_text(encoding="utf-8")
            self.assertNotIn("creation_time_key", source)

        with self.subTest(id="B02"):
            observation = controller.normalize_process_observation(
                SimpleNamespace(pid=41),
                process_info(
                    ppid=7,
                    name="ollama.exe",
                    exe="C:/Ollama/ollama.exe",
                    create_time=admitted_time,
                    cmdline=[],
                ),
                None,
            )
            self.assertEqual(observation.birth_token_hex, admitted_token)
            self.assertIsNotNone(observation.record)
            assert observation.record is not None
            self.assertEqual(observation.record.birth_token_hex, admitted_token)
            parsed = json.loads(
                controller.receipt_bytes(
                    observation.record.__dict__ | {"exe": str(observation.record.exe)}
                )
            )
            self.assertEqual(parsed["birth_token_hex"], admitted_token)
            self.assertIs(type(parsed["birth_token_hex"]), str)
            self.assertNotIn("creation_time_key", parsed)

        class NoSuchProcess(Exception):
            pass

        class TimeoutExpired(Exception):
            pass

        def stop_process(created: Mock, events: list[str]) -> SimpleNamespace:
            return SimpleNamespace(
                pid=41,
                ppid=lambda: 7,
                name=lambda: executable.name,
                exe=lambda: str(executable),
                create_time=created,
                kill=lambda: events.append("psutil-kill"),
                wait=lambda timeout: events.append("psutil-wait"),
            )

        def run_stop_case(row_id: str, first_times: tuple[float, ...], final_time: float, succeeds: bool) -> None:
            with self.subTest(id=row_id):
                events: list[str] = []
                first = stop_process(Mock(side_effect=first_times), events)
                final = stop_process(Mock(return_value=final_time), events)
                process_open = Mock(side_effect=(first, final))
                fake_psutil = SimpleNamespace(
                    NoSuchProcess=NoSuchProcess,
                    TimeoutExpired=TimeoutExpired,
                    AccessDenied=PermissionError,
                    Process=process_open,
                )
                expected = controller.ProcessRecord(
                    41, 7, executable.name, executable, admitted_token
                )
                if succeeds:
                    source_observation = controller.normalize_process_observation(
                        SimpleNamespace(pid=41),
                        process_info(
                            ppid=7,
                            name=executable.name,
                            exe=str(executable),
                            create_time=admitted_time,
                            cmdline=[],
                        ),
                        None,
                    )
                    expected = source_observation.record
                    assert expected is not None
                    self.assertEqual(source_observation.birth_token_hex, admitted_token)
                    self.assertEqual(expected.birth_token_hex, admitted_token)
                    serialized = controller.receipt_bytes(
                        expected.__dict__ | {"exe": str(expected.exe)}
                    )
                    self.assertEqual(json.loads(serialized)["birth_token_hex"], admitted_token)
                cim = Mock(side_effect=self.fail)
                callback = Mock(side_effect=lambda: events.append("callback"))
                with (
                    patch.object(controller, "psutil", fake_psutil),
                    patch.object(controller, "run_bounded_subprocess", cim),
                    patch.object(controller, "stream_loopback_get") as endpoint,
                    patch.object(controller.subprocess, "Popen") as launch,
                    patch.object(controller, "atomic_no_replace_publish") as publication,
                ):
                    if succeeds:
                        self.assertTrue(
                            controller.force_stop_exact(
                                expected, 7, "birth-token", on_signal_issued=callback
                            )
                        )
                    else:
                        with self.assertRaises(controller.CaptureError):
                            controller.force_stop_exact(
                                expected, 7, "birth-token", on_signal_issued=callback
                            )
                if succeeds:
                    self.assertEqual(events, ["callback", "psutil-kill", "psutil-wait"])
                    callback.assert_called_once_with()
                    self.assertEqual(process_open.call_count, 2)
                else:
                    self.assertEqual(events, [])
                    callback.assert_not_called()
                    if row_id == "B03":
                        self.assertEqual(process_open.call_count, 1)
                        cim.assert_not_called()
                endpoint.assert_not_called()
                launch.assert_not_called()
                publication.assert_not_called()

        with tempfile.TemporaryDirectory() as directory:
            executable = test_owned_executable(Path(directory), "normal", "ollama.exe")
            run_stop_case("B03", (replacement_time,), replacement_time, False)
            run_stop_case("B04", (admitted_time, admitted_time), replacement_time, False)
            run_stop_case("B05", (admitted_time, admitted_time), admitted_time, True)

        invalid_tokens: tuple[tuple[str, object], ...] = (
            ("integer", 1),
            ("bytes", b"41daa78348b9bbe6"),
            ("none", None),
            ("short", "1" * 15),
            ("long", "1" * 17),
            ("uppercase", admitted_token.upper()),
            ("nonhex", "g" * 16),
            ("whitespace", f" {admitted_token}"),
            ("prefix", f"0x{admitted_token}"),
            ("positive-zero", "0000000000000000"),
            ("negative-zero", "8000000000000000"),
            ("negative", struct.pack(">d", -1.0).hex()),
            ("nan", "7ff8000000000000"),
            ("positive-infinity", "7ff0000000000000"),
            ("negative-infinity", "fff0000000000000"),
        )

        class TokenSubclass(str):
            pass

        invalid_tokens += (("subclass", TokenSubclass(admitted_token)),)
        for mutation, invalid in invalid_tokens:
            with self.subTest(id="B06", mutation=mutation):
                with self.assertRaises(controller.OperationalCensusUncertain):
                    controller.ProcessRecord(41, 7, executable.name, executable, invalid)
                forged = forged_process_record(41, 7, executable.name, executable, invalid)
                process_open = Mock(side_effect=self.fail)
                fake_psutil = SimpleNamespace(Process=process_open)
                with patch.object(controller, "psutil", fake_psutil), self.assertRaises(
                    controller.OperationalCensusUncertain
                ):
                    controller.force_stop_exact(forged, 7, "invalid-token")
                process_open.assert_not_called()

        helper_output = json.dumps(
            {"pid": 41, "ppid": 7, "name": "ollama.exe"}
        ).encode("utf-8")
        for mutation, pair in (
            ("first", (replacement_time, admitted_time)),
            ("second", (admitted_time, replacement_time)),
            ("matching", (admitted_time, admitted_time)),
        ):
            with self.subTest(id="B07", mutation=mutation):
                observations = [
                    SimpleNamespace(pid=41, ppid=lambda: 7, create_time=lambda value=value: value)
                    for value in pair
                ]
                fake_psutil = SimpleNamespace(
                    NoSuchProcess=NoSuchProcess,
                    AccessDenied=PermissionError,
                    Process=Mock(side_effect=observations),
                )
                with patch.object(controller, "psutil", fake_psutil), patch.object(
                    controller,
                    "run_bounded_subprocess",
                    return_value=(0, helper_output, b""),
                ):
                    if mutation == "matching":
                        normalized = controller.normalize_process_observation(
                            SimpleNamespace(pid=41),
                            process_info(
                                ppid=7,
                                name=None,
                                exe="C:/Ollama/ollama.exe",
                                create_time=admitted_time,
                                cmdline=[],
                            ),
                            ROOT / "tools" / "read_v5_process_identity.ps1",
                        )
                        self.assertEqual(normalized.birth_token_hex, admitted_token)
                        self.assertIsNotNone(normalized.record)
                        assert normalized.record is not None
                        self.assertEqual(normalized.record.birth_token_hex, admitted_token)
                    else:
                        with self.assertRaises(controller.OperationalUncertainty):
                            controller.cim_process_name(
                                41, ROOT / "tools" / "read_v5_process_identity.ps1"
                            )

        with self.subTest(id="B08"), tempfile.TemporaryDirectory() as directory:
            record = controller.ProcessRecord(
                41, 7, "ollama.exe", executable, admitted_token
            )
            process_mapping = record.__dict__ | {"exe": str(record.exe)}
            receipts = {
                "baseline-processes.json": {"listeners": [41], "processes": [process_mapping]},
                "isolated-processes.json": {"listeners": [41], "processes": [process_mapping]},
                "restoration-processes.json": {
                    "app": process_mapping,
                    "server": process_mapping,
                },
            }
            for name, expected_mapping in receipts.items():
                path = Path(directory) / name
                if "processes" in expected_mapping:
                    with patch.object(controller, "listener_pids", return_value=(41,)), patch.object(
                        controller, "process_records", return_value=(record,)
                    ):
                        expected_mapping = controller.runtime_snapshot()
                expected_bytes = controller.receipt_bytes(expected_mapping)
                controller.write_create_only_receipt(path, expected_mapping)
                self.assertEqual(path.read_bytes(), expected_bytes)
                parsed = json.loads(expected_bytes)
                records = parsed["processes"] if "processes" in parsed else parsed.values()
                self.assertTrue(
                    all(item["birth_token_hex"] == admitted_token for item in records)
                )

    def test_cim_name_requires_two_fresh_exact_psutil_birth_observations(self) -> None:
        creation = 1_788_742_946.9020934

        class NoSuchProcess(Exception):
            pass

        before = SimpleNamespace(pid=41, ppid=lambda: 7, create_time=lambda: creation)
        after = SimpleNamespace(pid=41, ppid=lambda: 7, create_time=lambda: creation)
        process = Mock(side_effect=(before, after))
        fake_psutil = SimpleNamespace(NoSuchProcess=NoSuchProcess, AccessDenied=PermissionError, Process=process)
        output = json.dumps({"pid": 41, "ppid": 7, "name": "service.exe"}).encode("utf-8")
        with patch.object(controller, "psutil", fake_psutil), patch.object(controller, "run_bounded_subprocess", return_value=(0, output, b"")) as helper:
            self.assertEqual(controller.cim_process_name(41, ROOT / "tools" / "read_v5_process_identity.ps1"), "service.exe")
        self.assertEqual(process.call_args_list, [((41,), {}), ((41,), {})])
        self.assertEqual(helper.call_args.args[0][-1], "41")

    def test_cim_name_refuses_schema_transport_and_exact_birth_drift(self) -> None:
        creation = 1_788_742_946.9020934
        different_bits = 1_788_742_946.9020936

        class NoSuchProcess(Exception):
            pass

        helper = ROOT / "tools" / "read_v5_process_identity.ps1"
        valid = json.dumps({"pid": 41, "ppid": 7, "name": "service.exe"}).encode("utf-8")
        cases = (
            ("pid-drift", (42, 7, creation), valid, (41, 7, creation)),
            ("post-pid-drift", (41, 7, creation), valid, (42, 7, creation)),
            ("helper-pid-drift", (41, 7, creation), json.dumps({"pid": 42, "ppid": 7, "name": "service.exe"}).encode(), (41, 7, creation)),
            ("helper-parent-drift", (41, 7, creation), json.dumps({"pid": 41, "ppid": 8, "name": "service.exe"}).encode(), (41, 7, creation)),
            ("parent-drift", (41, 8, creation), valid, (41, 7, creation)),
            ("creation-bit-drift", (41, 7, creation), valid, (41, 7, different_bits)),
            ("nan", (41, 7, float("nan")), valid, (41, 7, creation)),
            ("infinite", (41, 7, float("inf")), valid, (41, 7, creation)),
            ("nonpositive", (41, 7, 0.0), valid, (41, 7, creation)),
            ("boolean", (41, 7, True), valid, (41, 7, creation)),
        )
        transport_cases = (
            ("blank-name", 0, json.dumps({"pid": 41, "ppid": 7, "name": ""}).encode(), b""),
            ("extra-field", 0, json.dumps({"pid": 41, "ppid": 7, "name": "service.exe", "extra": 1}).encode(), b""),
            ("missing-field", 0, json.dumps({"pid": 41, "name": "service.exe"}).encode(), b""),
            ("wrong-types", 0, json.dumps({"pid": "41", "ppid": 7, "name": "service.exe"}).encode(), b""),
            ("stderr", 0, valid, b"warning"),
            ("nonzero", 1, valid, b""),
        )
        for name, first, output, second in cases:
            with self.subTest(name=name):
                observations = [SimpleNamespace(pid=first[0], ppid=lambda value=first[1]: value, create_time=lambda value=first[2]: value), SimpleNamespace(pid=second[0], ppid=lambda value=second[1]: value, create_time=lambda value=second[2]: value)]
                fake_psutil = SimpleNamespace(NoSuchProcess=NoSuchProcess, AccessDenied=PermissionError, Process=Mock(side_effect=observations))
                with patch.object(controller, "psutil", fake_psutil), patch.object(controller, "run_bounded_subprocess", return_value=(0, output, b"")), self.assertRaises(controller.CaptureError):
                    controller.cim_process_name(41, helper)
        for name, returncode, output, stderr in transport_cases:
            with self.subTest(name=name):
                observations = [SimpleNamespace(pid=41, ppid=lambda: 7, create_time=lambda: creation), SimpleNamespace(pid=41, ppid=lambda: 7, create_time=lambda: creation)]
                fake_psutil = SimpleNamespace(NoSuchProcess=NoSuchProcess, AccessDenied=PermissionError, Process=Mock(side_effect=observations))
                with patch.object(controller, "psutil", fake_psutil), patch.object(controller, "run_bounded_subprocess", return_value=(returncode, output, stderr)), self.assertRaises(controller.CaptureError):
                    controller.cim_process_name(41, helper)
        for error in (controller.CaptureError("overflow"), controller.CaptureError("timeout")):
            with self.subTest(error=error):
                observation = SimpleNamespace(pid=41, ppid=lambda: 7, create_time=lambda: creation)
                fake_psutil = SimpleNamespace(NoSuchProcess=NoSuchProcess, AccessDenied=PermissionError, Process=Mock(return_value=observation))
                with patch.object(controller, "psutil", fake_psutil), patch.object(controller, "run_bounded_subprocess", side_effect=error), self.assertRaises(controller.CaptureError):
                    controller.cim_process_name(41, helper)
        for error in (NoSuchProcess(), PermissionError("denied")):
            with self.subTest(error=error):
                fake_psutil = SimpleNamespace(NoSuchProcess=NoSuchProcess, AccessDenied=PermissionError, Process=Mock(side_effect=error))
                with patch.object(controller, "psutil", fake_psutil), self.assertRaises(controller.CaptureError):
                    controller.cim_process_name(41, helper)

    def test_cim_resolved_name_still_requires_runner_host_command_line(self) -> None:
        creation = 1_788_742_946.9020934

        class NoSuchProcess(Exception):
            pass

        record = SimpleNamespace(info={"pid": 41, "name": controller.PROCESS_FIELD_UNAVAILABLE, "cmdline": controller.PROCESS_FIELD_UNAVAILABLE})
        helper = ROOT / "tools" / "read_v5_process_identity.ps1"

        def check(name: str, should_refuse: bool) -> None:
            observations = [SimpleNamespace(pid=41, ppid=lambda: 7, create_time=lambda: creation), SimpleNamespace(pid=41, ppid=lambda: 7, create_time=lambda: creation)]
            fake_psutil = SimpleNamespace(NoSuchProcess=NoSuchProcess, AccessDenied=PermissionError, process_iter=lambda _attributes, ad_value: [SimpleNamespace(info=idle_process_info()), record], Process=Mock(side_effect=observations))
            output = json.dumps({"pid": 41, "ppid": 7, "name": name}).encode("utf-8")
            with patch.object(controller.sys, "platform", "win32"), patch.object(controller, "psutil", fake_psutil), patch.object(controller, "run_bounded_subprocess", return_value=(0, output, b"")):
                if should_refuse:
                    with self.assertRaises(controller.CaptureError):
                        controller.assert_no_runner_or_llama_server(helper)
                else:
                    controller.assert_no_runner_or_llama_server(helper)

        check("python.exe", True)
        check("powershell.exe", True)
        check("Windows Service Host.exe", False)

    def test_pre_stop_fallback_bracket_drift_issues_no_signal_or_kill(self) -> None:
        executable = Path(__file__).resolve()
        expected = controller.ProcessRecord(41, 7, "ollama.exe", executable, birth_token(1_788_742_946.9020934))
        calls: list[str] = []

        class NoSuchProcess(Exception):
            pass

        current = SimpleNamespace(pid=41, ppid=lambda: 7, name=lambda: "", exe=lambda: str(executable), create_time=lambda: 1_788_742_946.9020934, kill=lambda: calls.append("kill"), wait=lambda timeout: calls.append("wait"))
        before = SimpleNamespace(pid=41, ppid=lambda: 7, create_time=lambda: 1_788_742_946.9020934)
        after = SimpleNamespace(pid=41, ppid=lambda: 7, create_time=lambda: 1_788_742_946.9020936)
        post = SimpleNamespace(pid=41, ppid=lambda: 7, exe=lambda: str(executable), create_time=lambda: 1_788_742_946.9020936, kill=lambda: calls.append("kill"), wait=lambda timeout: calls.append("wait"))
        fake_psutil = SimpleNamespace(NoSuchProcess=NoSuchProcess, TimeoutExpired=TimeoutError, AccessDenied=PermissionError, Process=Mock(side_effect=(current, before, after, post)))
        output = json.dumps({"pid": 41, "ppid": 7, "name": "ollama.exe"}).encode("utf-8")
        with patch.object(controller, "psutil", fake_psutil), patch.object(controller, "run_bounded_subprocess", return_value=(0, output, b"")), self.assertRaises(controller.CaptureError):
            controller.force_stop_exact(expected, 7, "normal server", cim_helper=ROOT / "tools" / "read_v5_process_identity.ps1", on_signal_issued=lambda: calls.append("signal"))
        self.assertEqual(calls, [])

    def test_stop_timeout_has_one_signal_boundary_and_no_duplicate_kill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = test_owned_executable(Path(directory), "normal", "ollama.exe")
            expected = controller.ProcessRecord(41, 7, "ollama.exe", executable, birth_token(100 / 1_000_000))
            calls: list[str] = []

            class NoSuchProcess(Exception):
                pass

            class TimeoutExpired(Exception):
                pass

            process = SimpleNamespace(pid=41, ppid=lambda: 7, name=lambda: "ollama.exe", exe=lambda: str(executable), create_time=lambda: 100 / 1_000_000, kill=lambda: calls.append("kill"), wait=lambda timeout: (_ for _ in ()).throw(TimeoutExpired()))
            fake_psutil = SimpleNamespace(NoSuchProcess=NoSuchProcess, TimeoutExpired=TimeoutExpired, AccessDenied=PermissionError, Process=lambda pid: process)
            with patch.object(controller, "psutil", fake_psutil), self.assertRaises(controller.CaptureError):
                controller.force_stop_exact(expected, 7, "normal server", on_signal_issued=lambda: calls.append("signal"))
            self.assertEqual(calls, ["signal", "kill"])

    def test_stop_treats_only_first_birth_lookup_disappearance_as_absent(self) -> None:
        executable = Path(__file__).resolve()
        expected = controller.ProcessRecord(41, 7, "ollama.exe", executable, birth_token(100 / 1_000_000))
        calls: list[str] = []

        class NoSuchProcess(Exception):
            pass

        first_missing = SimpleNamespace(NoSuchProcess=NoSuchProcess, TimeoutExpired=TimeoutError, AccessDenied=PermissionError, Process=Mock(side_effect=NoSuchProcess()))
        with patch.object(controller, "psutil", first_missing):
            self.assertFalse(controller.force_stop_exact(expected, 7, "normal server", on_signal_issued=lambda: calls.append("signal")))
        process = SimpleNamespace(pid=41, ppid=lambda: 7, name=lambda: "ollama.exe", exe=lambda: str(executable), create_time=lambda: 100 / 1_000_000, kill=lambda: calls.append("kill"), wait=lambda timeout: calls.append("wait"))
        later_missing = SimpleNamespace(NoSuchProcess=NoSuchProcess, TimeoutExpired=TimeoutError, AccessDenied=PermissionError, Process=Mock(side_effect=(process, NoSuchProcess())))
        with patch.object(controller, "psutil", later_missing), self.assertRaises(controller.CaptureError):
            controller.force_stop_exact(expected, 7, "normal server", on_signal_issued=lambda: calls.append("signal"))
        self.assertEqual(calls, [])

    def test_post_helper_disappearance_is_operational_uncertainty(self) -> None:
        with self.subTest(id="P18"), self.subTest(id="C06"):
            executable = Path(__file__).resolve()
            expected = controller.ProcessRecord(41, 7, "ollama.exe", executable, birth_token(100 / 1_000_000))
            events: list[str] = []

            class NoSuchProcess(Exception):
                pass

            current = SimpleNamespace(
                pid=41,
                ppid=lambda: 7,
                name=lambda: "",
                exe=lambda: str(executable),
                create_time=lambda: 100 / 1_000_000,
            )
            bracket = SimpleNamespace(
                pid=41,
                ppid=lambda: 7,
                create_time=lambda: 100 / 1_000_000,
            )
            process = Mock(side_effect=(current, bracket, bracket, NoSuchProcess()))
            fake_psutil = SimpleNamespace(
                NoSuchProcess=NoSuchProcess,
                TimeoutExpired=TimeoutError,
                AccessDenied=PermissionError,
                Process=process,
            )
            helper_output = json.dumps(
                {"pid": 41, "ppid": 7, "name": "ollama.exe"}
            ).encode("utf-8")
            with (
                patch.object(controller, "psutil", fake_psutil),
                patch.object(
                    controller,
                    "run_bounded_subprocess",
                    return_value=(0, helper_output, b""),
                ) as helper,
                self.assertRaises(controller.OperationalUncertainty),
            ):
                controller.force_stop_exact(
                    expected,
                    7,
                    "normal server",
                    cim_helper=ROOT / "tools" / "read_v5_process_identity.ps1",
                    on_signal_issued=lambda: events.append("signal"),
                )
            self.assertEqual(process.call_count, 4)
            helper.assert_called_once()
            self.assertEqual(events, [])

    def test_image_classifier_rejects_available_name_basename_disagreement(self) -> None:
        class NoSuchProcess(Exception):
            pass

        process = SimpleNamespace(pid=41)
        fake_psutil = SimpleNamespace(NoSuchProcess=NoSuchProcess, AccessDenied=PermissionError)
        helper = ROOT / "tools" / "read_v5_process_identity.ps1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = test_owned_executable(root, "images", "service.exe")
            python = test_owned_executable(root, "images", "python.exe")
            ollama = test_owned_executable(root, "images", "ollama.exe")
            direct_cases = (
                ({"pid": 41, "name": "service.exe", "exe": str(service)}, False),
                ({"pid": 41, "name": "service.exe", "exe": str(python)}, True),
                ({"pid": 41, "name": "service.exe", "exe": str(ollama)}, True),
                ({"pid": 41, "name": "service.exe", "exe": 7}, True),
                ({"pid": 41, "name": "service.exe", "exe": controller.PROCESS_FIELD_UNAVAILABLE}, False),
            )
            with patch.object(controller, "psutil", fake_psutil):
                for info, should_refuse in direct_cases:
                    with self.subTest(info=info):
                        if should_refuse:
                            with self.assertRaises(controller.CaptureError):
                                controller.process_image_identity(process, info, None)
                        else:
                            controller.process_image_identity(process, info, None)
            observations = [SimpleNamespace(pid=41, ppid=lambda: 7, create_time=lambda: 1.0), SimpleNamespace(pid=41, ppid=lambda: 7, create_time=lambda: 1.0)]
            cim_psutil = SimpleNamespace(NoSuchProcess=NoSuchProcess, AccessDenied=PermissionError, Process=Mock(side_effect=observations))
            output = json.dumps({"pid": 41, "ppid": 7, "name": "service.exe"}).encode()
            info = {"pid": 41, "name": controller.PROCESS_FIELD_UNAVAILABLE, "exe": str(python)}
            with patch.object(controller, "psutil", cim_psutil), patch.object(controller, "run_bounded_subprocess", return_value=(0, output, b"")), self.assertRaises(controller.CaptureError):
                controller.process_image_identity(process, info, helper)

    def test_process_field_and_relevance_matrix(self) -> None:
        direct_cases = (
            ("P01", process_info(), controller.ProcessDisposition.UNRELATED),
            ("P02", process_info(exe="C:/Windows/service.exe", cmdline="service.exe"), None),
            ("P03", process_info(exe="C:/Windows/service.exe", cmdline=["service.exe", 7]), None),
            ("P04", process_info(name=" service.exe"), None),
            ("P04", process_info(name="   "), None),
            ("P05", process_info(exe=" C:/Windows/service.exe"), None),
            ("P05", process_info(exe="   "), None),
            ("P06", process_info(exe="C:/Windows/python.exe"), None),
            ("P06", process_info(exe="C:/Windows/ollama.exe"), None),
            ("P07", process_info(name="python.exe", cmdline=["python.exe", "-V"]), None),
            ("P08", process_info(name="python.exe", exe="C:/Windows/python.exe", cmdline=None), None),
            ("P08", process_info(name="python.exe", exe="C:/Windows/python.exe", cmdline=[]), None),
            ("P09", process_info(name="python.exe", exe="C:/Windows/python.exe", cmdline=["python.exe", "-V"]), controller.ProcessDisposition.HOST_CLEAR),
            ("P10", process_info(name="python.exe", exe="C:/Windows/python.exe", cmdline=["python.exe", "run_v5_recovery.py"]), controller.ProcessDisposition.BLOCKER),
            ("P11", process_info(name="cmd.exe", exe="C:/Windows/cmd.exe", cmdline=["cmd.exe", "/c", "run_v5_conditional_campaign.ps1"]), controller.ProcessDisposition.BLOCKER),
            ("P12", process_info(name="ollama.exe", ppid=7, create_time=1.0), None),
            ("P12", process_info(name="ollama.exe", exe="C:/Ollama/ollama.exe", create_time=1.0), None),
            ("P12", process_info(name="ollama.exe", exe="C:/Ollama/ollama.exe", ppid=7), None),
            ("P13", process_info(name="ollama.exe", exe="C:/Ollama/ollama.exe", ppid=7, create_time=1.25, cmdline=[]), controller.ProcessDisposition.OLLAMA),
            ("P14", process_info(name="llama-server.exe", exe="C:/Ollama/llama-server.exe", cmdline=[]), controller.ProcessDisposition.BLOCKER),
            ("P19", process_info(ppid="7"), None),
            ("P19", process_info(create_time=float("nan")), None),
            ("P20", process_info(exe="C:/Windows/other.exe"), None),
        )
        raw_process = SimpleNamespace(pid=41)
        for row_id, info, expected in direct_cases:
            with self.subTest(id=row_id, info=info):
                if expected is None:
                    with self.assertRaises(controller.OperationalUncertainty):
                        controller.normalize_process_observation(raw_process, info, None)
                    continue
                observation = controller.normalize_process_observation(raw_process, info, None)
                self.assertIs(observation.disposition, expected)
                census = (observation,)
                if expected is controller.ProcessDisposition.BLOCKER:
                    with self.assertRaises(controller.CaptureError):
                        controller.assert_no_runner_or_llama_server(census=census)
                else:
                    controller.assert_no_runner_or_llama_server(census=census)
                records = controller.process_records(census=census)
                if expected is controller.ProcessDisposition.OLLAMA:
                    self.assertEqual(records, (observation.record,))
                    self.assertEqual(observation.record.birth_token_hex, birth_token(1.25))
                else:
                    self.assertEqual(records, ())

        class NoSuchProcess(Exception):
            pass

        helper = ROOT / "tools" / "read_v5_process_identity.ps1"
        cim_cases = (
            ("P15", "service.exe", None, controller.ProcessDisposition.UNRELATED),
            ("P16", "python.exe", None, None),
            ("P17", " service.exe", None, None),
            ("P17", "   ", None, None),
        )
        for row_id, resolved_name, executable, expected in cim_cases:
            with self.subTest(id=row_id, resolved_name=resolved_name):
                brackets = [
                    SimpleNamespace(pid=41, ppid=lambda: 7, create_time=lambda: 1.0),
                    SimpleNamespace(pid=41, ppid=lambda: 7, create_time=lambda: 1.0),
                ]
                fake_psutil = SimpleNamespace(
                    NoSuchProcess=NoSuchProcess,
                    AccessDenied=PermissionError,
                    Process=Mock(side_effect=brackets),
                )
                output = json.dumps({"pid": 41, "ppid": 7, "name": resolved_name}).encode("utf-8")
                info = process_info(name=None, exe=executable)
                with patch.object(controller, "psutil", fake_psutil), patch.object(
                    controller, "run_bounded_subprocess", return_value=(0, output, b"")
                ):
                    if expected is None:
                        with self.assertRaises(controller.OperationalUncertainty):
                            controller.normalize_process_observation(raw_process, info, helper)
                    else:
                        observation = controller.normalize_process_observation(raw_process, info, helper)
                        self.assertIs(observation.disposition, expected)
                        controller.assert_no_runner_or_llama_server(census=(observation,))
                        self.assertEqual(controller.process_records(census=(observation,)), ())

    def test_windows_idle_pseudoentry_domain_matrix(self) -> None:
        class NoSuchProcess(Exception):
            pass

        def run_census(rows: list[dict[str, object]], platform: str) -> tuple[controller.ProcessObservation, ...]:
            processes = [SimpleNamespace(info=row) for row in rows]
            fake_psutil = SimpleNamespace(
                NoSuchProcess=NoSuchProcess,
                AccessDenied=PermissionError,
                process_iter=lambda attributes, ad_value: processes,
            )
            with (
                patch.object(controller.sys, "platform", platform),
                patch.object(controller, "psutil", fake_psutil),
                patch.object(controller, "run_bounded_subprocess") as cim,
                patch.object(controller, "stream_loopback_get") as endpoint,
                patch.object(controller, "force_stop_exact") as signal,
                patch.object(controller.subprocess, "Popen") as popen,
                patch.object(controller, "atomic_no_replace_publish") as publication,
                patch.object(controller, "cleanup_restoration_staging") as cleanup,
            ):
                try:
                    census = controller.process_census()
                finally:
                    for capability in (cim, endpoint, signal, popen, publication, cleanup):
                        capability.assert_not_called()
            return census

        with self.subTest(id="D01"):
            self.assertEqual(run_census([idle_process_info()], "win32"), ())

        with self.subTest(id="D02"), self.assertRaises(controller.OperationalCensusUncertain):
            run_census([idle_process_info()], "linux")

        class CommandLineList(list[str]):
            pass

        mutations: tuple[tuple[str, str, object], ...] = (
            ("pid-bool", "pid", True),
            ("pid-negative", "pid", -1),
            ("ppid-bool", "ppid", True),
            ("ppid-one", "ppid", 1),
            ("name-missing", "name", controller.PROCESS_FIELD_UNAVAILABLE),
            ("name-none", "name", None),
            ("name-case", "name", "System idle Process"),
            ("name-whitespace", "name", " System Idle Process"),
            ("exe-none", "exe", None),
            ("exe-empty", "exe", ""),
            ("exe-path", "exe", "C:/Windows/idle.exe"),
            ("exe-different-sentinel", "exe", object()),
            ("create-int", "create_time", 0),
            ("create-negative-zero", "create_time", -0.0),
            ("create-nonzero", "create_time", 1.0),
            ("create-nan", "create_time", float("nan")),
            ("create-infinity", "create_time", float("inf")),
            ("create-none", "create_time", None),
            ("create-sentinel", "create_time", controller.PROCESS_FIELD_UNAVAILABLE),
            ("cmdline-none", "cmdline", None),
            ("cmdline-sentinel", "cmdline", controller.PROCESS_FIELD_UNAVAILABLE),
            ("cmdline-tuple", "cmdline", ()),
            ("cmdline-nonempty", "cmdline", ["idle"]),
            ("cmdline-subclass", "cmdline", CommandLineList()),
        )
        for mutation, field, value in mutations:
            with self.subTest(id="D03", mutation=mutation):
                row = idle_process_info()
                if mutation == "name-missing":
                    del row[field]
                else:
                    row[field] = value
                with self.assertRaises(controller.OperationalCensusUncertain):
                    run_census([row], "win32")

        with self.subTest(id="D04"):
            row = process_info(
                pid=41,
                ppid=0,
                name="System Idle Process",
                exe="C:/Windows/System Idle Process",
                create_time=1.0,
                cmdline=[],
            )
            with self.assertRaises(controller.OperationalCensusUncertain):
                run_census([idle_process_info(), row], "win32")
        with self.subTest(id="D05"), self.assertRaises(controller.OperationalCensusUncertain):
            run_census([], "win32")
        with self.subTest(id="D06"), self.assertRaises(controller.OperationalCensusUncertain):
            run_census([idle_process_info(), idle_process_info()], "win32")
        with self.subTest(id="D07"):
            census = run_census([idle_process_info(), process_info()], "win32")
            self.assertEqual(len(census), 1)
            self.assertIs(census[0].disposition, controller.ProcessDisposition.UNRELATED)

        unknown_rows = (
            ("P02", process_info(exe="C:/Windows/service.exe", cmdline="service.exe")),
            ("P03", process_info(exe="C:/Windows/service.exe", cmdline=["service.exe", 7])),
            ("P04", process_info(name=" service.exe")),
            ("P05", process_info(exe=" C:/Windows/service.exe")),
            ("P06", process_info(exe="C:/Windows/python.exe")),
            ("P07", process_info(name="python.exe", cmdline=["python.exe", "-V"])),
            ("P08", process_info(name="python.exe", exe="C:/Windows/python.exe", cmdline=[])),
            ("P12", process_info(name="ollama.exe", exe="C:/Ollama/ollama.exe", ppid=7)),
        )
        for reset_row, row in unknown_rows:
            with self.subTest(id="D08a", reset_row=reset_row), self.assertRaises(
                controller.OperationalUncertainty
            ):
                run_census([idle_process_info(), row], "win32")
        admitted_rows = (
            ("P01", process_info(), controller.ProcessDisposition.UNRELATED),
            ("P09", process_info(name="python.exe", exe="C:/Windows/python.exe", cmdline=["python.exe", "-V"]), controller.ProcessDisposition.HOST_CLEAR),
        )
        for reset_row, row, disposition in admitted_rows:
            with self.subTest(id="D08b", reset_row=reset_row):
                census = run_census([idle_process_info(), row], "win32")
                self.assertEqual(len(census), 1)
                self.assertIs(census[0].disposition, disposition)
        blocker_rows = (
            ("P10", process_info(name="python.exe", exe="C:/Windows/python.exe", cmdline=["python.exe", "run_v5_recovery.py"])),
            ("P11", process_info(name="cmd.exe", exe="C:/Windows/cmd.exe", cmdline=["cmd.exe", "/c", "run_v5_recovery.py"])),
        )
        for reset_row, row in blocker_rows:
            with self.subTest(id="D08c", reset_row=reset_row):
                census = run_census([idle_process_info(), row], "win32")
                self.assertIs(census[0].disposition, controller.ProcessDisposition.BLOCKER)
                with self.assertRaises(controller.CaptureError):
                    controller.assert_no_runner_or_llama_server(census=census)
                with patch.object(controller, "process_census", return_value=census), patch.object(
                    controller, "listener_pids", side_effect=self.fail
                ):
                    topology = controller.observe_normal_topology()
                self.assertIs(topology.state, controller.NormalTopologyState.UNKNOWN)
        with self.subTest(id="D08d"):
            row = process_info(
                name="ollama.exe",
                exe="C:/Ollama/ollama.exe",
                ppid=7,
                create_time=1.25,
                cmdline=[],
            )
            census = run_census([idle_process_info(), row], "win32")
            self.assertEqual(
                census[0].record,
                controller.ProcessRecord(41, 7, "ollama.exe", Path("C:/Ollama/ollama.exe"), birth_token(1.25)),
            )
            self.assertEqual(census[0].birth_token_hex, birth_token(1.25))

        with self.subTest(id="D09"):
            process_open = Mock(side_effect=self.fail)
            fake_psutil = SimpleNamespace(Process=process_open)
            expected = controller.ProcessRecord(0, 0, "System Idle Process", Path("idle"), birth_token(1.0))
            with patch.object(controller, "psutil", fake_psutil), self.assertRaises(
                controller.OperationalCensusUncertain
            ):
                controller.force_stop_exact(expected, None, "idle")
            process_open.assert_not_called()

    def test_recorded_and_birth_timestamp_matrix(self) -> None:
        raw_process = SimpleNamespace(pid=41)
        for row_id, value in (("T01", 0), ("T02", 0.0)):
            with self.subTest(id=row_id):
                observation = controller.normalize_process_observation(
                    raw_process, process_info(create_time=value), None
                )
                self.assertEqual(observation.create_time, 0.0)
                self.assertIs(observation.disposition, controller.ProcessDisposition.UNRELATED)
                self.assertIsNone(observation.record)

        class IntSubclass(int):
            pass

        class FloatSubclass(float):
            pass

        malformed = (
            ("negative-zero", -0.0),
            ("negative-int", -1),
            ("negative-float", -1.0),
            ("true", True),
            ("false", False),
            ("string", "0"),
            ("nan", float("nan")),
            ("positive-infinity", float("inf")),
            ("negative-infinity", float("-inf")),
            ("int-subclass", IntSubclass(1)),
            ("float-subclass", FloatSubclass(1.0)),
        )
        for label, value in malformed:
            with self.subTest(id="T03", mutation=label):
                capabilities = [Mock() for _ in range(5)]
                with (
                    patch.object(controller, "run_bounded_subprocess", capabilities[0]),
                    patch.object(controller, "stream_loopback_get", capabilities[1]),
                    patch.object(controller, "force_stop_exact", capabilities[2]),
                    patch.object(controller.subprocess, "Popen", capabilities[3]),
                    patch.object(controller, "atomic_no_replace_publish", capabilities[4]),
                    self.assertRaises(controller.OperationalCensusUncertain),
                ):
                    controller.normalize_process_observation(
                        raw_process, process_info(create_time=value), None
                    )
                for capability in capabilities:
                    capability.assert_not_called()
        for value in (1, 1.25):
            with self.subTest(id="T04", value=value):
                observation = controller.normalize_process_observation(
                    raw_process, process_info(create_time=value), None
                )
                self.assertEqual(observation.create_time, float(value))
                self.assertEqual(observation.birth_token_hex, birth_token(value))

        with self.subTest(id="T05"):
            host_row = process_info(
                pid=4,
                ppid=0,
                name="System",
                exe="",
                create_time=0.0,
                cmdline=[],
            )
            processes = [
                SimpleNamespace(info=idle_process_info()),
                SimpleNamespace(info=host_row),
            ]
            fake_psutil = SimpleNamespace(
                process_iter=lambda attributes, ad_value: processes,
                NoSuchProcess=RuntimeError,
                AccessDenied=PermissionError,
            )
            with patch.object(controller.sys, "platform", "win32"), patch.object(
                controller, "psutil", fake_psutil
            ):
                census = controller.process_census()
            self.assertEqual(len(census), 1)
            self.assertEqual(census[0].pid, 4)
            self.assertIs(census[0].disposition, controller.ProcessDisposition.UNRELATED)
            self.assertIsNone(census[0].record)

        timestamp_classifications = (
            ("T06", "python.exe", "C:/Windows/python.exe", ["python.exe", "-V"], controller.ProcessDisposition.HOST_CLEAR),
            ("T06", "python.exe", "C:/Windows/python.exe", ["python.exe", "run_v5_recovery.py"], controller.ProcessDisposition.BLOCKER),
            ("T07", "llama-server.exe", "C:/Ollama/llama-server.exe", [], controller.ProcessDisposition.BLOCKER),
        )
        for row_id, name, executable, command_line, disposition in timestamp_classifications:
            with self.subTest(id=row_id, disposition=disposition.value):
                observation = controller.normalize_process_observation(
                    raw_process,
                    process_info(
                        name=name,
                        exe=executable,
                        create_time=0.0,
                        cmdline=command_line,
                    ),
                    None,
                )
                self.assertIs(observation.disposition, disposition)
                self.assertIsNone(observation.record)
        for name in ("ollama.exe", "ollama app.exe"):
            with self.subTest(id="T08", name=name), self.assertRaises(
                controller.OperationalUncertainty
            ):
                controller.normalize_process_observation(
                    raw_process,
                    process_info(
                        name=name,
                        exe=f"C:/Ollama/{name}",
                        ppid=7,
                        create_time=0.0,
                        cmdline=[],
                    ),
                    None,
                )

        zero_unrelated = process_info(
            pid=4,
            ppid=0,
            name="System",
            exe="",
            create_time=0.0,
            cmdline=[],
        )

        def composed_census(row: dict[str, object]) -> tuple[controller.ProcessObservation, ...]:
            processes = [
                SimpleNamespace(info=idle_process_info()),
                SimpleNamespace(info=dict(zero_unrelated)),
                SimpleNamespace(info=row),
            ]
            fake_psutil = SimpleNamespace(
                process_iter=lambda attributes, ad_value: processes,
                NoSuchProcess=RuntimeError,
                AccessDenied=PermissionError,
            )
            with patch.object(controller.sys, "platform", "win32"), patch.object(
                controller, "psutil", fake_psutil
            ):
                return controller.process_census()

        malformed_rows = (
            ("P02", process_info(exe="C:/Windows/service.exe", cmdline="service.exe")),
            ("P03", process_info(exe="C:/Windows/service.exe", cmdline=["service.exe", 7])),
            ("P04", process_info(name=" service.exe")),
            ("P05", process_info(exe=" C:/Windows/service.exe")),
            ("P06", process_info(exe="C:/Windows/python.exe")),
            ("P07", process_info(name="python.exe", cmdline=["python.exe", "-V"])),
            ("P08", process_info(name="python.exe", exe="C:/Windows/python.exe", cmdline=[])),
            ("P12", process_info(name="ollama.exe", exe="C:/Ollama/ollama.exe", ppid=7)),
        )
        for reset_row, row in malformed_rows:
            with self.subTest(id="T12", reset_row=reset_row), self.assertRaises(
                controller.OperationalUncertainty
            ):
                composed_census(row)
        composed_rows = (
            ("P10", process_info(name="python.exe", exe="C:/Windows/python.exe", cmdline=["python.exe", "run_v5_recovery.py"]), controller.ProcessDisposition.BLOCKER),
            ("P11", process_info(name="cmd.exe", exe="C:/Windows/cmd.exe", cmdline=["cmd.exe", "/c", "run_v5_recovery.py"]), controller.ProcessDisposition.BLOCKER),
            ("P13", process_info(name="ollama.exe", exe="C:/Ollama/ollama.exe", ppid=7, create_time=1.25, cmdline=[]), controller.ProcessDisposition.OLLAMA),
        )
        for reset_row, row, disposition in composed_rows:
            with self.subTest(id="T13", reset_row=reset_row):
                census = composed_census(row)
                self.assertEqual(census[0].pid, 4)
                self.assertIsNone(census[0].birth_token_hex)
                self.assertIs(census[1].disposition, disposition)
                self.assertIsNone(census[0].record)
                if disposition is controller.ProcessDisposition.OLLAMA:
                    self.assertIsNotNone(census[1].record)
                    self.assertEqual(census[1].birth_token_hex, birth_token(1.25))
                    assert census[1].record is not None
                    self.assertEqual(census[1].record.birth_token_hex, birth_token(1.25))

        class NoSuchProcess(Exception):
            pass

        pre_cim = SimpleNamespace(pid=41, ppid=lambda: 7, create_time=lambda: 0.0)
        fake_psutil = SimpleNamespace(
            NoSuchProcess=NoSuchProcess,
            AccessDenied=PermissionError,
            Process=Mock(return_value=pre_cim),
        )
        with self.subTest(id="T09"), patch.object(
            controller, "psutil", fake_psutil
        ), patch.object(controller, "run_bounded_subprocess") as cim, self.assertRaises(
            controller.OperationalUncertainty
        ):
            controller.normalize_process_observation(
                raw_process,
                process_info(name=None, create_time=0.0),
                ROOT / "tools" / "read_v5_process_identity.ps1",
            )
        cim.assert_not_called()

        expected_zero = forged_process_record(
            41, 7, "ollama.exe", controller.NORMAL_SERVER, "0000000000000000"
        )
        process_open = Mock(side_effect=self.fail)
        with self.subTest(id="T10", phase="expected"), patch.object(
            controller, "psutil", SimpleNamespace(Process=process_open)
        ), self.assertRaises(controller.OperationalCensusUncertain):
            controller.force_stop_exact(expected_zero, 7, "normal server")
        process_open.assert_not_called()

        def stop_process(created: Mock) -> SimpleNamespace:
            return SimpleNamespace(
                pid=41,
                ppid=lambda: 7,
                name=lambda: "ollama.exe",
                exe=lambda: str(controller.NORMAL_SERVER),
                create_time=created,
                kill=lambda: self.fail("kill must not run"),
                wait=lambda timeout: self.fail("wait must not run"),
            )

        expected = controller.ProcessRecord(
            41, 7, "ollama.exe", controller.NORMAL_SERVER, birth_token(1.0)
        )
        for phase in ("first", "second"):
            with self.subTest(id="T10", phase=phase):
                if phase == "first":
                    process = stop_process(Mock(return_value=0.0))
                    process_factory = Mock(return_value=process)
                else:
                    process = stop_process(Mock(return_value=1.0))
                    final = stop_process(Mock(return_value=0.0))
                    process_factory = Mock(side_effect=(process, final))
                fake_psutil = SimpleNamespace(
                    NoSuchProcess=NoSuchProcess,
                    TimeoutExpired=TimeoutError,
                    AccessDenied=PermissionError,
                    Process=process_factory,
                )
                callbacks: list[str] = []
                with patch.object(controller, "psutil", fake_psutil), self.assertRaises(
                    controller.OperationalUncertainty
                ):
                    controller.force_stop_exact(
                        expected,
                        7,
                        "normal server",
                        on_signal_issued=lambda callbacks=callbacks: callbacks.append("signal"),
                    )
                self.assertEqual(callbacks, [])

    @unittest.skipUnless(sys.platform == "win32" and controller.psutil is not None, "D10 requires Windows and pinned psutil")
    def test_windows_idle_pseudoentry_matches_pinned_psutil(self) -> None:
        raw_rows = [
            process.info
            for process in controller.psutil.process_iter(
                ["pid", "ppid", "name", "exe", "create_time", "cmdline"],
                ad_value=None,
            )
        ]
        with self.subTest(id="D10"):
            rows = [
                row
                for row in raw_rows
                if row.get("pid") == 0 or row.get("name") == "System Idle Process"
            ]
            self.assertEqual(
                rows,
                [{"pid": 0, "ppid": 0, "name": "System Idle Process", "exe": None, "create_time": 0.0, "cmdline": []}],
            )
        with self.subTest(id="T14"):
            system_rows = [row for row in raw_rows if row.get("pid") == 4]
            self.assertEqual(len(system_rows), 1)
            system = system_rows[0]
            self.assertEqual(
                system,
                {"pid": 4, "ppid": 0, "name": "System", "exe": "", "create_time": 0.0, "cmdline": []},
            )
            observation = controller.normalize_process_observation(
                SimpleNamespace(pid=4), system, None
            )
            self.assertIs(observation.disposition, controller.ProcessDisposition.UNRELATED)
            self.assertIsNone(observation.record)

    def test_observation_failure_normalization_matrix(self) -> None:
        app = controller.ProcessRecord(10, 0, "ollama app.exe", Path("app.exe"), birth_token(0.00001))
        server = controller.ProcessRecord(11, 10, "ollama.exe", Path("server.exe"), birth_token(0.000011))
        ollama_census = (
            controller.ProcessObservation(10, 0, app.name, app.exe, 0.00001, app.birth_token_hex, (), controller.ProcessDisposition.OLLAMA, app),
            controller.ProcessObservation(11, 10, server.name, server.exe, 0.000011, server.birth_token_hex, (), controller.ProcessDisposition.OLLAMA, server),
        )

        def assert_unknown(row_id: str, **patches: object) -> None:
            capabilities = {
                "launch": Mock(),
                "endpoint": Mock(),
                "signal": Mock(),
                "retry": Mock(),
                "publish": Mock(),
            }
            with self.subTest(id=row_id, patches=tuple(patches)), ExitStack() as stack:
                for name, value in patches.items():
                    stack.enter_context(patch.object(controller, name, value))
                stack.enter_context(patch.object(controller.subprocess, "Popen", capabilities["launch"]))
                stack.enter_context(patch.object(controller, "stream_loopback_get", capabilities["endpoint"]))
                stack.enter_context(patch.object(controller, "force_stop_exact", capabilities["signal"]))
                stack.enter_context(patch.object(controller, "atomic_no_replace_publish", capabilities["publish"]))
                observation = controller.observe_normal_topology()
                self.assertIs(observation.state, controller.NormalTopologyState.UNKNOWN)
            for capability in capabilities.values():
                capability.assert_not_called()

        assert_unknown("O01", process_census=Mock(side_effect=OSError("process_iter")))
        class ProcessWithBadInfo:
            @property
            def info(self) -> dict[str, object]:
                raise OSError("process.info")

        process_with_bad_info = ProcessWithBadInfo()

        class NoSuchProcess(Exception):
            pass

        fake_process_psutil = SimpleNamespace(
            NoSuchProcess=NoSuchProcess,
            AccessDenied=PermissionError,
            process_iter=lambda attributes, ad_value: [process_with_bad_info],
        )
        with patch.object(controller.sys, "platform", "linux"), patch.object(controller, "psutil", fake_process_psutil):
            assert_unknown("O01")
        assert_unknown("O02", process_census=Mock(return_value=()), listener_pids=Mock(side_effect=OSError("connections")))

        class Connection:
            status = "LISTEN"
            laddr = SimpleNamespace(ip="0.0.0.0", port=controller.PORT)
            pid = 11

        fake_listener_psutil = SimpleNamespace(
            CONN_LISTEN="LISTEN",
            net_connections=lambda kind: [Connection()],
        )
        with patch.object(controller, "psutil", fake_listener_psutil):
            assert_unknown("O03", process_census=Mock(return_value=()))
        malformed_connection = SimpleNamespace(status=7, laddr=None, pid=None)
        fake_listener_psutil.net_connections = lambda kind: [malformed_connection]
        with patch.object(controller, "psutil", fake_listener_psutil):
            assert_unknown("O03", process_census=Mock(return_value=()))

        failing_path = SimpleNamespace(resolve=Mock(side_effect=OSError("resolve")))
        observed_path_failure = (
            controller.ProcessObservation(10, 0, app.name, failing_path, 1.0, birth_token(1.0), (), controller.ProcessDisposition.OLLAMA, controller.ProcessRecord(10, 0, app.name, failing_path, birth_token(1.0))),
            controller.ProcessObservation(11, 10, server.name, server.exe, 1.0, birth_token(1.0), (), controller.ProcessDisposition.OLLAMA, server),
        )
        assert_unknown("O04", process_census=Mock(return_value=observed_path_failure), listener_pids=Mock(return_value=(11,)))
        with patch.object(controller, "NORMAL_SERVER", failing_path):
            assert_unknown("O05", process_census=Mock(return_value=ollama_census), listener_pids=Mock(return_value=(11,)))

    def test_runner_census_refuses_unknown_relevant_hosts_but_allows_named_unrelated_service(self) -> None:
        class NoSuchProcess(Exception):
            pass

        records = [
            SimpleNamespace(info={"pid": 1, "name": "service.exe", "cmdline": controller.PROCESS_FIELD_UNAVAILABLE}),
        ]
        fake_psutil = SimpleNamespace(
            NoSuchProcess=NoSuchProcess,
            AccessDenied=PermissionError,
            process_iter=lambda _attributes, ad_value: [SimpleNamespace(info=idle_process_info()), *records],
        )
        with patch.object(controller.sys, "platform", "win32"), patch.object(controller, "psutil", fake_psutil):
            controller.assert_no_runner_or_llama_server()
        for name, command_line in ((controller.PROCESS_FIELD_UNAVAILABLE, ()), ("", ()), ("python.exe", controller.PROCESS_FIELD_UNAVAILABLE), ("pythonw.exe", controller.PROCESS_FIELD_UNAVAILABLE), ("py.exe", ()), ("powershell.exe", ())):
            with self.subTest(name=name), patch.object(controller.sys, "platform", "win32"), patch.object(controller, "psutil", fake_psutil):
                records[:] = [SimpleNamespace(info={"pid": 1, "name": name, "cmdline": command_line})]
                with self.assertRaises(controller.CaptureError):
                    controller.assert_no_runner_or_llama_server()
        with patch.object(controller.sys, "platform", "win32"), patch.object(controller, "psutil", fake_psutil):
            records[:] = [SimpleNamespace(info={"pid": 1, "name": "python.exe", "cmdline": ["python.exe", "run_v5_recovery.py"]})]
            with self.assertRaises(controller.CaptureError):
                controller.assert_no_runner_or_llama_server()
        with patch.object(controller.sys, "platform", "win32"), patch.object(controller, "psutil", fake_psutil):
            records[:] = [SimpleNamespace(info={"pid": 1, "name": "cmd.exe", "cmdline": ["cmd.exe", "/c", "run_v5_recovery.py"]})]
            with self.assertRaises(controller.CaptureError):
                controller.assert_no_runner_or_llama_server()

    @unittest.skipUnless(os.name == "nt" and controller.psutil is not None, "requires Windows PowerShell CIM and psutil")
    def test_governed_cim_helper_matches_fresh_psutil_identity_with_shadowed_module_path(self) -> None:
        helper = ROOT / "tools" / "read_v5_process_identity.ps1"
        with patch.dict(os.environ, {"PSModulePath": r"C:\shadowed"}, clear=False):
            name = controller.cim_process_name(os.getpid(), helper)
        process = controller.psutil.Process(os.getpid())
        self.assertEqual(name.casefold(), process.name().casefold())
        missing = subprocess.run(
            [str(controller.POWERSHELL_EXE), "-NoProfile", "-NonInteractive", "-File", str(helper), "-ProcessId", "2147483647"],
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(missing.returncode, 0)

    def test_stop_refuses_parent_name_and_access_drift_without_kill(self) -> None:
        executable = Path(__file__).resolve()
        expected = controller.ProcessRecord(41, 7, "ollama.exe", executable, birth_token(100 / 1_000_000))

        class NoSuchProcess(Exception):
            pass

        class TimeoutExpired(Exception):
            pass

        for name, parent, error in (("other.exe", 7, None), ("ollama.exe", 8, None), ("ollama.exe", 7, PermissionError("denied"))):
            with self.subTest(name=name, parent=parent, error=error):
                def create_process(process_name: str, process_parent: int, creation_error: BaseException | None) -> SimpleNamespace:
                    def creation_time() -> float:
                        if creation_error is not None:
                            raise creation_error
                        return 100 / 1_000_000

                    return SimpleNamespace(pid=41, ppid=lambda: process_parent, name=lambda: process_name, exe=lambda: str(executable), create_time=creation_time, kill=lambda: self.fail("kill must not run"), wait=lambda timeout: None)

                process = create_process(name, parent, error)
                fake_psutil = SimpleNamespace(NoSuchProcess=NoSuchProcess, TimeoutExpired=TimeoutExpired, AccessDenied=PermissionError, Process=lambda pid, process=process: process)
                with patch.object(controller, "psutil", fake_psutil), self.assertRaises(controller.CaptureError):
                    controller.force_stop_exact(expected, 7, "drifted process")

    def test_stop_refuses_reuse_between_prior_census_and_stop(self) -> None:
        executable = Path(__file__).resolve()
        expected = controller.ProcessRecord(41, 7, "ollama.exe", executable, birth_token(100 / 1_000_000))
        state = {"creation": 100 / 1_000_000}

        class NoSuchProcess(Exception):
            pass

        class TimeoutExpired(Exception):
            pass

        process = SimpleNamespace(pid=41, ppid=lambda: 7, name=lambda: "ollama.exe", exe=lambda: str(executable), create_time=lambda: state["creation"], kill=lambda: self.fail("kill must not run"), wait=lambda timeout: None)
        fake_psutil = SimpleNamespace(NoSuchProcess=NoSuchProcess, TimeoutExpired=TimeoutExpired, AccessDenied=PermissionError, Process=lambda pid: process)
        with patch.object(controller, "psutil", fake_psutil), self.assertRaises(controller.CaptureError):
            controller.force_stop_exact(expected, 7, "reused process", on_preverified=lambda: state.update(creation=200 / 1_000_000))

    def _run_capture_lifecycle_failure(
        self,
        stage: str,
    ) -> tuple[dict[str, object], Mock]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity_root = root / "identity"
            executable = test_owned_executable(root, "runtime", "ollama.exe")
            app = controller.ProcessRecord(10, 0, "ollama app.exe", executable, birth_token(10.0))
            server = controller.ProcessRecord(11, 10, "ollama.exe", executable, birth_token(11.0))
            root_identity = controller.ProcessRecord(41, os.getpid(), "ollama.exe", executable, birth_token(41.0))

            class FakeChild:
                pid = 41

                def __init__(self) -> None:
                    self.stdout = io.BytesIO()
                    self.stderr = io.BytesIO()

            child = FakeChild()

            def create_job(lifecycle: controller.IsolatedLifecycle) -> None:
                lifecycle.job_create_attempted = True
                if stage == "create-job":
                    raise controller.CaptureError("injected create-job")
                lifecycle.job_handle = 97
                if stage == "set-job-info":
                    raise controller.CaptureError("injected set-job-info")
                lifecycle.job_create_succeeded = True
                lifecycle.advance(controller.IsolatedLifecyclePhase.JOB_CREATED)

            def launch(lifecycle: controller.IsolatedLifecycle) -> None:
                lifecycle.root_launch_attempted = True
                if stage == "launch-popen":
                    raise controller.CaptureError("injected launch-popen")
                lifecycle.process = child
                lifecycle.root_launch_succeeded = True
                if stage == "post-popen":
                    raise controller.CaptureError("injected post-popen")
                lifecycle.advance(controller.IsolatedLifecyclePhase.ROOT_LAUNCHED)

            def assign(lifecycle: controller.IsolatedLifecycle) -> None:
                lifecycle.job_assign_attempted = True
                if stage == "assign-pid":
                    raise controller.CaptureError("injected assign-pid")
                if stage == "assign-open":
                    raise controller.CaptureError("injected assign-open")
                lifecycle.assignment_process_handle = 91
                if stage == "assign-job":
                    raise controller.CaptureError("injected assign-job")
                lifecycle.job_assign_succeeded = True
                lifecycle.advance(controller.IsolatedLifecyclePhase.ROOT_ASSIGNED)
                if stage == "assignment-close":
                    raise controller.CaptureError("injected assignment-close")
                lifecycle.assignment_process_handle = None

            def bind(lifecycle: controller.IsolatedLifecycle, _helper: Path | None) -> None:
                lifecycle.root_bind_attempted = True
                if stage == "bind":
                    raise controller.CaptureError("injected bind")
                lifecycle.root_identity = root_identity
                lifecycle.root_bind_succeeded = True
                lifecycle.advance(controller.IsolatedLifecyclePhase.ROOT_BOUND)

            def drains(lifecycle: controller.IsolatedLifecycle, _stdout: Path, _stderr: Path) -> None:
                lifecycle.drains_started_attempted = True
                lifecycle.stdout_drain = SimpleNamespace(overflow=threading.Event())
                if stage == "stdout-drain":
                    raise controller.CaptureError("injected stdout-drain")
                if stage == "stderr-drain":
                    raise controller.CaptureError("injected stderr-drain")
                lifecycle.stderr_drain = SimpleNamespace(overflow=threading.Event())
                lifecycle.drains_started_succeeded = True
                lifecycle.advance(controller.IsolatedLifecyclePhase.DRAINS_STARTED)

            def resume(lifecycle: controller.IsolatedLifecycle) -> None:
                lifecycle.root_resume_attempted = True
                if stage == "resume-thread":
                    raise controller.CaptureError("injected resume-thread")
                lifecycle.root_resume_succeeded = True
                lifecycle.advance(controller.IsolatedLifecyclePhase.ROOT_RESUMED)
                lifecycle.root_thread_handle_close_attempted = True
                if stage == "resume-close":
                    raise controller.CaptureError("injected resume-close")
                lifecycle.root_thread_handle_close_succeeded = True

            def stop(lifecycle: controller.IsolatedLifecycle, _helper: Path | None) -> None:
                lifecycle.stop_attempted = True
                if lifecycle.process is None:
                    lifecycle.teardown_path = "no-root"
                elif lifecycle.job_assign_succeeded:
                    lifecycle.teardown_path = "job"
                    lifecycle.job_terminate_attempted = True
                    lifecycle.job_terminate_succeeded = True
                    lifecycle.root_waited = True
                    lifecycle.job_zero_window_confirmed = True
                else:
                    lifecycle.teardown_path = "unassigned-suspended"
                    lifecycle.prebind_root_stop_attempted = True
                    lifecycle.prebind_root_stop_succeeded = True
                    lifecycle.root_waited = True
                    lifecycle.prebind_baseline_clean = True
                lifecycle.pipes_closed = True
                lifecycle.drains_joined = True
                if lifecycle.job_handle is not None:
                    lifecycle.job_close_attempted = True
                    lifecycle.job_close_succeeded = True
                    lifecycle.job_handle = None
                lifecycle.advance(controller.IsolatedLifecyclePhase.STOPPED)

            def stream(_port: int, endpoint: str, destination: Path) -> None:
                destination.write_bytes(b"version" if endpoint == "/api/version" else b"tags")

            def force(
                _expected: controller.ProcessRecord,
                _parent: int | None,
                _label: str,
                **kwargs: object,
            ) -> bool:
                callback = kwargs.get("on_signal_issued")
                assert callable(callback)
                callback()
                return True

            authority = {
                "protocol_commit": "a" * 40,
                "protocol_tag": controller.PROTOCOL_TAG,
                "protocol_tag_object": "b" * 40,
                "source_manifest_sha256": "c" * 64,
            }
            with ExitStack() as stack:
                stack.enter_context(patch.object(controller, "IDENTITY_ROOT", identity_root))
                stack.enter_context(patch.object(controller, "assert_regular"))
                stack.enter_context(patch.object(controller, "assert_no_reparse_or_ads"))
                stack.enter_context(patch.object(controller, "sha256_file", return_value="d" * 64))
                stack.enter_context(patch.object(controller, "validate_controller_authority", return_value=(authority, "e" * 64)))
                stack.enter_context(patch.object(controller, "controller_dependency_identity", return_value={}))
                stack.enter_context(patch.object(controller, "verify_protocol"))
                stack.enter_context(patch.object(controller, "verify_source_manifest", return_value={}))
                stack.enter_context(patch.object(controller, "verify_v4_failure_binding"))
                stack.enter_context(patch.object(controller, "verify_tracked_helpers", return_value={"tools/read_v5_process_identity.ps1": ROOT / "tools" / "read_v5_process_identity.ps1"}))
                stack.enter_context(patch.object(controller, "verify_isolated_runtime_and_store"))
                stack.enter_context(patch.object(controller, "assert_normal_state", return_value=(app, server)))
                stack.enter_context(patch.object(controller, "runtime_snapshot", return_value={}))
                stack.enter_context(patch.object(controller, "stream_loopback_get", side_effect=stream))
                stack.enter_context(patch.object(controller, "force_stop_exact", side_effect=force))
                stack.enter_context(patch.object(controller, "wait_for_listener_free"))
                stack.enter_context(patch.object(controller, "create_lifecycle_job", side_effect=create_job))
                stack.enter_context(patch.object(controller, "launch_suspended_isolated_root", side_effect=launch))
                stack.enter_context(patch.object(controller, "assign_suspended_root_to_job", side_effect=assign))
                stack.enter_context(patch.object(controller, "bind_suspended_root_identity", side_effect=bind))
                stack.enter_context(patch.object(controller, "start_lifecycle_drains", side_effect=drains))
                stack.enter_context(patch.object(controller, "resume_suspended_root", side_effect=resume))
                stack.enter_context(patch.object(controller, "stop_isolated_lifecycle", side_effect=stop))
                restore = stack.enter_context(patch.object(controller, "restore_normal"))
                with self.assertRaisesRegex(controller.CaptureError, f"injected {stage}"):
                    controller.run_capture()
            status = json.loads((identity_root / "operation_status.json").read_text(encoding="utf-8"))
            return status, restore

    def test_windows_last_error_is_portable(self) -> None:
        with self.subTest(case="missing"), patch.object(controller, "ctypes", SimpleNamespace()):
            with self.assertRaises(AttributeError):
                controller.ctypes.get_last_error()
            self.assertEqual(controller.windows_last_error(), 0)
        with self.subTest(case="none"), patch.object(controller, "ctypes", SimpleNamespace(get_last_error=lambda: None)):
            self.assertEqual(controller.windows_last_error(), 0)
        with self.subTest(case="nonzero"), patch.object(controller, "ctypes", SimpleNamespace(get_last_error=lambda: 87)):
            self.assertEqual(controller.windows_last_error(), 87)

    def test_lifecycle_native_acquisition_edges_retain_custody(self) -> None:
        class FakeChild:
            pid = 41
            stdout = io.BytesIO()
            stderr = io.BytesIO()

        def api(**overrides: object) -> SimpleNamespace:
            defaults: dict[str, object] = {
                "CreateJobObjectW": Mock(return_value=97),
                "SetInformationJobObject": Mock(return_value=True),
                "OpenProcess": Mock(return_value=91),
                "AssignProcessToJobObject": Mock(return_value=True),
                "CloseHandle": Mock(return_value=True),
            }
            defaults.update(overrides)
            return SimpleNamespace(**defaults)

        with self.subTest(id="J01"):
            source = CONTROLLER_PATH.read_text(encoding="utf-8")
            self.assertIn("class JOBOBJECT_BASIC_LIMIT_INFORMATION", source)
            self.assertIn("class JOBOBJECT_EXTENDED_LIMIT_INFORMATION", source)
            self.assertIn("class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION", source)
            self.assertIn("api.QueryInformationJobObject.argtypes", source)
            self.assertNotIn("ctypes.c_byte * 144", source)
        with self.subTest(id="J02"):
            self.assertIn("creationflags=DETACHED_PROCESS | CREATE_SUSPENDED", CONTROLLER_PATH.read_text(encoding="utf-8"))
        with self.subTest(id="J03"):
            source = CONTROLLER_PATH.read_text(encoding="utf-8")
            self.assertIn("limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE", source)
            self.assertNotIn("BREAKAWAY_OK", source)
        with self.subTest(id="J04"):
            source = CONTROLLER_PATH.read_text(encoding="utf-8")
            self.assertNotIn("start_isolated_" + "process", source)
            self.assertLess(source.index("create_lifecycle_job(lifecycle)"), source.index("launch_suspended_isolated_root(lifecycle)"))
            self.assertLess(source.index("launch_suspended_isolated_root(lifecycle)"), source.index("assign_suspended_root_to_job(lifecycle)"))
            self.assertLess(source.index("assign_suspended_root_to_job(lifecycle)"), source.index("bind_suspended_root_identity(lifecycle, cim_helper)"))
            self.assertLess(source.index("bind_suspended_root_identity(lifecycle, cim_helper)"), source.index("resume_suspended_root(lifecycle)"))
        with self.subTest(id="J05"):
            lifecycle = controller.IsolatedLifecycle()
            with patch.object(controller, "kernel32", return_value=api(CreateJobObjectW=Mock(return_value=0))), self.assertRaisesRegex(controller.CaptureError, "CreateJobObjectW"):
                controller.create_lifecycle_job(lifecycle)
            self.assertTrue(lifecycle.job_create_attempted)
            self.assertIsNone(lifecycle.job_handle)
        with self.subTest(id="J05"):
            lifecycle = controller.IsolatedLifecycle()
            with patch.object(controller, "kernel32", return_value=api(SetInformationJobObject=Mock(return_value=False))), self.assertRaisesRegex(controller.CaptureError, "SetInformationJobObject"):
                controller.create_lifecycle_job(lifecycle)
            self.assertEqual(lifecycle.job_handle, 97)
            self.assertFalse(lifecycle.job_create_succeeded)
        with self.subTest(id="J06"):
            lifecycle = controller.IsolatedLifecycle(job_handle=97, job_create_succeeded=True)
            with patch.object(controller.subprocess, "Popen", side_effect=controller.CaptureError("injected Popen")), self.assertRaisesRegex(controller.CaptureError, "injected Popen"):
                controller.launch_suspended_isolated_root(lifecycle)
            self.assertTrue(lifecycle.root_launch_attempted)
            self.assertIsNone(lifecycle.process)
        with self.subTest(id="J06"):
            lifecycle = controller.IsolatedLifecycle(job_handle=97, job_create_succeeded=True)
            with (
                patch.object(controller.subprocess, "Popen", return_value=FakeChild()),
                patch.object(controller.IsolatedLifecycle, "advance", side_effect=controller.CaptureError("injected post-Popen")),
                self.assertRaisesRegex(controller.CaptureError, "injected post-Popen"),
            ):
                controller.launch_suspended_isolated_root(lifecycle)
            self.assertEqual(lifecycle.process.pid, 41)
            self.assertTrue(lifecycle.root_launch_succeeded)
        with self.subTest(id="J07"):
            lifecycle = controller.IsolatedLifecycle(job_handle=97, process=SimpleNamespace(pid=0))
            with self.assertRaisesRegex(controller.CaptureError, "PID differs"):
                controller.assign_suspended_root_to_job(lifecycle)
            self.assertTrue(lifecycle.job_assign_attempted)
        with self.subTest(id="J08"):
            lifecycle = controller.IsolatedLifecycle(job_handle=97, process=FakeChild())
            with patch.object(controller, "kernel32", return_value=api(OpenProcess=Mock(return_value=0))), self.assertRaisesRegex(controller.CaptureError, "OpenProcess"):
                controller.assign_suspended_root_to_job(lifecycle)
            self.assertTrue(lifecycle.job_assign_attempted)
            self.assertIsNone(lifecycle.assignment_process_handle)
        with self.subTest(id="J09"):
            lifecycle = controller.IsolatedLifecycle(job_handle=97, process=FakeChild())
            with patch.object(controller, "kernel32", return_value=api(AssignProcessToJobObject=Mock(return_value=False))), self.assertRaisesRegex(controller.CaptureError, "AssignProcessToJobObject"):
                controller.assign_suspended_root_to_job(lifecycle)
            self.assertIsNone(lifecycle.assignment_process_handle)
            self.assertFalse(lifecycle.job_assign_succeeded)
        with self.subTest(id="J10"):
            lifecycle = controller.IsolatedLifecycle(job_handle=97, process=FakeChild())
            with patch.object(controller, "kernel32", return_value=api(CloseHandle=Mock(return_value=False))), self.assertRaisesRegex(controller.CaptureError, "CloseHandle\\(isolated root\\)"):
                controller.assign_suspended_root_to_job(lifecycle)
            self.assertTrue(lifecycle.job_assign_succeeded)
            self.assertEqual(lifecycle.assignment_process_handle, 91)

    def test_lifecycle_setup_and_resume_edges_are_fail_closed(self) -> None:
        process = SimpleNamespace(pid=41, stdout=io.BytesIO(), stderr=io.BytesIO())
        identity = controller.ProcessRecord(41, os.getpid(), "ollama.exe", Path(__file__), birth_token(41.0))
        with self.subTest(id="J11"):
            lifecycle = controller.IsolatedLifecycle(process=process)
            fake_psutil = SimpleNamespace(Process=lambda _pid: SimpleNamespace())
            with patch.object(controller, "psutil", fake_psutil), patch.object(controller, "process_record", side_effect=controller.CaptureError("injected bind")), self.assertRaisesRegex(controller.CaptureError, "injected bind"):
                controller.bind_suspended_root_identity(lifecycle)
            self.assertTrue(lifecycle.root_bind_attempted)
            self.assertIsNone(lifecycle.root_identity)
        with self.subTest(id="J12"):
            lifecycle = controller.IsolatedLifecycle(process=process)
            with patch.object(controller, "start_bounded_drain", side_effect=controller.CaptureError("injected stdout")), self.assertRaisesRegex(controller.CaptureError, "injected stdout"):
                controller.start_lifecycle_drains(lifecycle, Path("stdout"), Path("stderr"))
            self.assertTrue(lifecycle.drains_started_attempted)
            self.assertIsNone(lifecycle.stdout_drain)
        with self.subTest(id="J12"):
            lifecycle = controller.IsolatedLifecycle(process=process)
            first_drain = SimpleNamespace(overflow=threading.Event())
            with patch.object(controller, "start_bounded_drain", side_effect=(first_drain, controller.CaptureError("injected stderr"))), self.assertRaisesRegex(controller.CaptureError, "injected stderr"):
                controller.start_lifecycle_drains(lifecycle, Path("stdout"), Path("stderr"))
            self.assertIs(lifecycle.stdout_drain, first_drain)
            self.assertIsNone(lifecycle.stderr_drain)
        with self.subTest(id="J13"):
            lifecycle = controller.IsolatedLifecycle(process=process, root_identity=identity)
            fake_psutil = SimpleNamespace(Process=lambda _pid: SimpleNamespace(threads=lambda: ()))
            with patch.object(controller, "psutil", fake_psutil), self.assertRaisesRegex(controller.CaptureError, "thread count differs"):
                controller.resume_suspended_root(lifecycle)
            self.assertTrue(lifecycle.root_resume_attempted)
        with self.subTest(id="J13"):
            lifecycle = controller.IsolatedLifecycle(process=process, root_identity=identity)
            api = SimpleNamespace(OpenThread=Mock(return_value=0))
            fake_psutil = SimpleNamespace(Process=lambda _pid: SimpleNamespace(threads=lambda: (SimpleNamespace(id=71),)))
            with patch.object(controller, "psutil", fake_psutil), patch.object(controller, "kernel32", return_value=api), self.assertRaisesRegex(controller.CaptureError, "OpenThread"):
                controller.resume_suspended_root(lifecycle)
            self.assertTrue(lifecycle.root_resume_attempted)
        with self.subTest(id="J14"):
            lifecycle = controller.IsolatedLifecycle(process=process, root_identity=identity)
            api = SimpleNamespace(
                OpenThread=Mock(return_value=91),
                GetProcessIdOfThread=Mock(return_value=42),
                ResumeThread=Mock(return_value=1),
                CloseHandle=Mock(return_value=True),
            )
            fake_psutil = SimpleNamespace(Process=lambda _pid: SimpleNamespace(threads=lambda: (SimpleNamespace(id=71),)))
            with patch.object(controller, "psutil", fake_psutil), patch.object(controller, "kernel32", return_value=api), self.assertRaisesRegex(controller.CaptureError, "owner differs"):
                controller.resume_suspended_root(lifecycle)
            api.GetProcessIdOfThread.return_value = 41
            api.ResumeThread.return_value = controller.INVALID_DWORD
            with patch.object(controller, "psutil", fake_psutil), patch.object(controller, "kernel32", return_value=api), self.assertRaisesRegex(controller.CaptureError, "ResumeThread"):
                controller.resume_suspended_root(controller.IsolatedLifecycle(process=process, root_identity=identity))
            api.ResumeThread.return_value = 0
            with patch.object(controller, "psutil", fake_psutil), patch.object(controller, "kernel32", return_value=api), self.assertRaisesRegex(controller.CaptureError, "prior suspend count differs"):
                controller.resume_suspended_root(controller.IsolatedLifecycle(process=process, root_identity=identity))
        with self.subTest(id="J15"):
            lifecycle = controller.IsolatedLifecycle(process=process, root_identity=identity)
            api = SimpleNamespace(
                OpenThread=Mock(return_value=91),
                GetProcessIdOfThread=Mock(return_value=41),
                ResumeThread=Mock(return_value=1),
                CloseHandle=Mock(return_value=False),
            )
            fake_psutil = SimpleNamespace(Process=lambda _pid: SimpleNamespace(threads=lambda: (SimpleNamespace(id=71),)))
            with patch.object(controller, "psutil", fake_psutil), patch.object(controller, "kernel32", return_value=api), self.assertRaisesRegex(controller.CaptureError, "CloseHandle\\(root thread\\)"):
                controller.resume_suspended_root(lifecycle)
            self.assertTrue(lifecycle.root_resume_succeeded)
            self.assertTrue(lifecycle.root_thread_handle_close_attempted)
            self.assertFalse(lifecycle.root_thread_handle_close_succeeded)
            lifecycle.primary_error = controller.lifecycle_failure(lifecycle, "capture", controller.CaptureError("thread close"))
            self.assertFalse(controller.normal_restore_eligible(lifecycle, "signaled"))
        with self.subTest(id="T11"):
            self.assertIn("root_thread_handle_close_succeeded", CONTROLLER_PATH.read_text(encoding="utf-8"))

    def test_lifecycle_teardown_paths_and_failures_are_factual(self) -> None:
        class FakeChild:
            pid = 41

            def __init__(self, *, wait_error: BaseException | None = None, pipe_error: bool = False) -> None:
                class Pipe:
                    def __init__(self, error: bool) -> None:
                        self.error = error

                    def close(self) -> None:
                        if self.error:
                            raise OSError("injected pipe close")

                self.stdout = Pipe(pipe_error)
                self.stderr = Pipe(False)
                self.wait_error = wait_error
                self.killed = False

            def kill(self) -> None:
                self.killed = True

            def wait(self, timeout: float) -> int:
                if self.wait_error is not None:
                    raise self.wait_error
                return 0

        def api(*, terminate: bool = True, close: bool = True) -> SimpleNamespace:
            return SimpleNamespace(
                TerminateJobObject=Mock(return_value=terminate),
                CloseHandle=Mock(return_value=close),
            )

        def assigned(child: FakeChild) -> controller.IsolatedLifecycle:
            return controller.IsolatedLifecycle(
                phase=controller.IsolatedLifecyclePhase.ROOT_RESUMED,
                job_handle=97,
                process=child,
                job_create_attempted=True,
                job_create_succeeded=True,
                root_launch_attempted=True,
                root_launch_succeeded=True,
                job_assign_attempted=True,
                job_assign_succeeded=True,
                root_resume_attempted=True,
                root_resume_succeeded=True,
                root_thread_handle_close_attempted=True,
                root_thread_handle_close_succeeded=True,
            )

        def stop_assigned(
            *,
            terminate: bool = True,
            close: bool = True,
            child: FakeChild | None = None,
            members: object = (0, 0),
            monotonic: tuple[float, ...] = (0.0, 0.0, 0.0, 3.0, 3.0),
            drain_error: bool = False,
        ) -> controller.IsolatedLifecycle:
            lifecycle = assigned(child or FakeChild())
            if drain_error:
                lifecycle.stdout_drain = SimpleNamespace()
            with ExitStack() as stack:
                stack.enter_context(patch.object(controller, "kernel32", return_value=api(terminate=terminate, close=close)))
                stack.enter_context(patch.object(controller, "isolated_job_active_members", side_effect=members))
                stack.enter_context(patch.object(controller.time, "monotonic", side_effect=monotonic))
                stack.enter_context(patch.object(controller.time, "sleep"))
                if drain_error:
                    stack.enter_context(patch.object(controller, "join_bounded_drain", side_effect=controller.CaptureError("injected drain")))
                controller.stop_isolated_lifecycle(lifecycle)
            return lifecycle

        with self.subTest(id="J16"):
            no_root = controller.IsolatedLifecycle()
            controller.stop_isolated_lifecycle(no_root)
            self.assertEqual(no_root.phase, controller.IsolatedLifecyclePhase.STOPPED)
            self.assertEqual(no_root.teardown_path, "no-root")
            self.assertTrue(no_root.pipes_closed)
            self.assertTrue(no_root.drains_joined)
            self.assertTrue(controller.normal_restore_eligible(no_root, "signaled"))

            job_only = controller.IsolatedLifecycle(job_handle=97, job_create_attempted=True, job_create_succeeded=True)
            with patch.object(controller, "kernel32", return_value=api()):
                controller.stop_isolated_lifecycle(job_only)
            self.assertEqual(job_only.phase, controller.IsolatedLifecyclePhase.STOPPED)
            self.assertEqual(job_only.teardown_path, "no-root")
            self.assertTrue(job_only.job_close_attempted)
            self.assertTrue(job_only.job_close_succeeded)
            self.assertTrue(controller.normal_restore_eligible(job_only, "signaled"))
        with self.subTest(id="J17"):
            child = FakeChild()
            lifecycle = controller.IsolatedLifecycle(
                phase=controller.IsolatedLifecyclePhase.ROOT_LAUNCHED,
                job_handle=97,
                process=child,
                job_create_attempted=True,
                job_create_succeeded=True,
                root_launch_attempted=True,
                root_launch_succeeded=True,
            )
            with (
                patch.object(controller, "kernel32", return_value=api()),
                patch.object(controller, "wait_for_prelaunch_baseline"),
            ):
                controller.stop_isolated_lifecycle(lifecycle)
            self.assertTrue(child.killed)
            self.assertEqual(lifecycle.phase, controller.IsolatedLifecyclePhase.STOPPED)
            self.assertEqual(lifecycle.teardown_path, "unassigned-suspended")
            self.assertTrue(lifecycle.prebind_root_stop_succeeded)
            self.assertTrue(lifecycle.prebind_baseline_clean)
            self.assertTrue(controller.normal_restore_eligible(lifecycle, "signaled"))
        with self.subTest(id="J18"):
            lifecycle = stop_assigned()
            self.assertEqual(lifecycle.phase, controller.IsolatedLifecyclePhase.STOPPED)
            self.assertEqual(lifecycle.teardown_path, "job")
            self.assertTrue(lifecycle.job_terminate_succeeded)
            self.assertTrue(lifecycle.root_waited)
            self.assertTrue(lifecycle.job_zero_window_confirmed)
            self.assertTrue(lifecycle.job_close_succeeded)
            self.assertFalse(lifecycle.cleanup_errors)
            self.assertTrue(controller.normal_restore_eligible(lifecycle, "signaled"))
        with self.subTest(id="J19", edge="terminate"):
            lifecycle = stop_assigned(terminate=False, members=(0, 0))
            self.assertTrue(lifecycle.job_terminate_attempted)
            self.assertFalse(lifecycle.job_terminate_succeeded)
            self.assertEqual(lifecycle.phase, controller.IsolatedLifecyclePhase.STOPPED)
            self.assertEqual(lifecycle.cleanup_errors[0].operation, "Job terminate")
            self.assertFalse(controller.normal_restore_eligible(lifecycle, "signaled"))
        with self.subTest(id="J19", edge="root-wait"):
            lifecycle = stop_assigned(child=FakeChild(wait_error=controller.CaptureError("injected root wait")))
            self.assertTrue(lifecycle.job_terminate_succeeded)
            self.assertFalse(lifecycle.root_waited)
            self.assertEqual(lifecycle.phase, controller.IsolatedLifecyclePhase.STOPPED)
            self.assertIn("isolated root wait", [receipt.operation for receipt in lifecycle.cleanup_errors])
            self.assertFalse(controller.normal_restore_eligible(lifecycle, "signaled"))
        with self.subTest(id="J20", edge="accounting"):
            lifecycle = stop_assigned(members=controller.CaptureError("injected accounting"))
            self.assertFalse(lifecycle.job_zero_window_confirmed)
            self.assertEqual(lifecycle.phase, controller.IsolatedLifecyclePhase.STOPPED)
            self.assertIn("Job zero window", [receipt.operation for receipt in lifecycle.cleanup_errors])
            self.assertFalse(controller.normal_restore_eligible(lifecycle, "signaled"))
        with self.subTest(id="J20", edge="zero-window"):
            lifecycle = stop_assigned(members=(1,), monotonic=(0.0, 0.0, 11.0))
            self.assertFalse(lifecycle.job_zero_window_confirmed)
            self.assertEqual(lifecycle.phase, controller.IsolatedLifecyclePhase.STOPPED)
            self.assertIn("Job zero window", [receipt.operation for receipt in lifecycle.cleanup_errors])
            self.assertFalse(controller.normal_restore_eligible(lifecycle, "signaled"))
        with self.subTest(id="J21", edge="pipe"):
            lifecycle = stop_assigned(child=FakeChild(pipe_error=True))
            self.assertFalse(lifecycle.pipes_closed)
            self.assertEqual(lifecycle.phase, controller.IsolatedLifecyclePhase.STOPPED)
            self.assertIn("isolated stdout pipe", [receipt.operation for receipt in lifecycle.cleanup_errors])
            self.assertFalse(controller.normal_restore_eligible(lifecycle, "signaled"))
        with self.subTest(id="J21", edge="drain"):
            lifecycle = stop_assigned(drain_error=True)
            self.assertFalse(lifecycle.drains_joined)
            self.assertEqual(lifecycle.phase, controller.IsolatedLifecyclePhase.STOPPED)
            self.assertIn("isolated stdout drain", [receipt.operation for receipt in lifecycle.cleanup_errors])
            self.assertFalse(controller.normal_restore_eligible(lifecycle, "signaled"))
        with self.subTest(id="J21", edge="Job-close"):
            lifecycle = stop_assigned(close=False)
            self.assertFalse(lifecycle.job_close_succeeded)
            self.assertEqual(lifecycle.phase, controller.IsolatedLifecyclePhase.STOPPED)
            self.assertIn("Job close", [receipt.operation for receipt in lifecycle.cleanup_errors])
            self.assertFalse(controller.normal_restore_eligible(lifecycle, "signaled"))
        with self.subTest(id="J22"):
            lifecycle = stop_assigned()
            immutable_facts = (
                lifecycle.job_assign_succeeded,
                lifecycle.root_resume_succeeded,
                lifecycle.job_terminate_succeeded,
                lifecycle.root_waited,
                lifecycle.job_zero_window_confirmed,
                lifecycle.pipes_closed,
                lifecycle.drains_joined,
                lifecycle.job_close_succeeded,
            )
            self.assertTrue(all(immutable_facts))
            self.assertNotIn("teardown_" + "safe", CONTROLLER_PATH.read_text(encoding="utf-8"))
            lifecycle.cleanup_errors.append(controller.lifecycle_failure(lifecycle, "late cleanup", controller.CaptureError("injected")))
            self.assertFalse(controller.normal_restore_eligible(lifecycle, "signaled"))

    def test_production_trace_replay_accepts_only_the_observed_helper_tree(self) -> None:
        with self.subTest(id="J23"), tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage_root = root / "stage"
            stage_root.mkdir()
            isolated_executable = test_owned_executable(stage_root, "runtime", "ollama.exe")
            system_root = root / "system-root"
            system_root.mkdir()
            conhost_executable = test_owned_executable(system_root, "System32", "conhost.exe")
            lib_root = isolated_executable.parent / "lib" / "ollama"
            for variant in ("cuda_v12", "cuda_v13", "vulkan"):
                (lib_root / variant).mkdir(parents=True, exist_ok=True)
            llama_executable = test_owned_executable(isolated_executable.parent / "lib", "ollama", "llama-server.exe")
            parent = controller.ProcessRecord(41, os.getpid(), "ollama.exe", isolated_executable, birth_token(41.0))
            lifecycle = controller.IsolatedLifecycle(root_identity=parent)
            parent_observation = controller.ProcessObservation(
                41, os.getpid(), "ollama.exe", isolated_executable, 41.0, birth_token(41.0), (), controller.ProcessDisposition.OLLAMA, parent
            )
            list_devices = controller.ProcessObservation(
                42, 41, "llama-server.exe", llama_executable, 42.0, birth_token(42.0),
                (str(llama_executable), "--list-devices", "--offline", "--verbose"),
                controller.ProcessDisposition.BLOCKER, None,
            )
            port_probe = controller.ProcessObservation(
                43, 41, "llama-server.exe", llama_executable, 43.0, birth_token(43.0),
                (str(llama_executable), "--port", "61956", "--host", controller.HOST, "--no-webui", "--offline", "--verbose"),
                controller.ProcessDisposition.BLOCKER, None,
            )
            gpu_records: list[controller.ProcessObservation] = []
            for index, variant in enumerate(("cuda_v12", "cuda_v13", "vulkan"), start=44):
                gpu_records.append(
                    controller.ProcessObservation(
                        index, 41, "ollama.exe", isolated_executable, float(index), birth_token(float(index)),
                        (str(isolated_executable), "gpu-discover", "--lib-dir", str(lib_root), "--lib-dir", str(lib_root / variant)),
                        controller.ProcessDisposition.OLLAMA,
                        controller.ProcessRecord(index, 41, "ollama.exe", isolated_executable, birth_token(float(index))),
                    )
                )
            conhost = controller.ProcessObservation(
                47, 44, "conhost.exe", conhost_executable, 47.0, birth_token(47.0),
                ("\\??\\" + str(conhost_executable), "0x4"), controller.ProcessDisposition.UNRELATED, None,
            )
            with (
                patch.dict(controller.os.environ, {"SystemRoot": str(system_root)}),
                patch.object(controller, "ISOLATED_EXE", isolated_executable),
                patch.object(controller, "STAGE_ROOT", stage_root),
            ):
                admitted = controller.isolated_startup_descendants(
                    lifecycle,
                    (parent_observation, list_devices, port_probe, *gpu_records, conhost),
                )
                self.assertEqual({record.pid for record in admitted}, {42, 43, 44, 45, 46, 47})
                wrong_library = controller.ProcessObservation(
                    44, 41, "ollama.exe", isolated_executable, 44.0, birth_token(44.0),
                    (str(isolated_executable), "gpu-discover", "--lib-dir", str(lib_root), "--lib-dir", str(root / "outside")),
                    controller.ProcessDisposition.OLLAMA, gpu_records[0].record,
                )
                wrong_llama_executable = test_owned_executable(root, "other", "llama-server.exe")
                wrong_path = controller.ProcessObservation(
                    42, 41, "llama-server.exe", wrong_llama_executable, 42.0, birth_token(42.0),
                    (str(wrong_llama_executable), "--list-devices", "--offline", "--verbose"),
                    controller.ProcessDisposition.BLOCKER, None,
                )
                model_probe = controller.ProcessObservation(
                    43, 41, "llama-server.exe", llama_executable, 43.0, birth_token(43.0),
                    (str(llama_executable), "--port", "61956", "--host", controller.HOST, "--no-webui", "--offline", "--verbose", "--model", "qwen2.5:7b"),
                    controller.ProcessDisposition.BLOCKER, None,
                )
                wrong_parent = controller.ProcessObservation(
                    47, 41, "conhost.exe", conhost_executable, 47.0, birth_token(47.0),
                    ("\\??\\" + str(conhost_executable), "0x4"), controller.ProcessDisposition.UNRELATED, None,
                )
                wrong_prefix = controller.ProcessObservation(
                    47, 44, "conhost.exe", conhost_executable, 47.0, birth_token(47.0),
                    (str(conhost_executable), "0x4"), controller.ProcessDisposition.UNRELATED, None,
                )
                wrong_command = controller.ProcessObservation(
                    47, 44, "conhost.exe", conhost_executable, 47.0, birth_token(47.0),
                    ("\\??\\" + str(conhost_executable), "0xffffffff", "-ForceV1"), controller.ProcessDisposition.UNRELATED, None,
                )
                foreign = controller.ProcessObservation(
                    48, 1, "python.exe", Path(sys.executable), 48.0, birth_token(48.0),
                    ("python.exe", "run_v5_recovery.py"), controller.ProcessDisposition.BLOCKER, None,
                )
                for observation in (wrong_library, wrong_path, model_probe, wrong_parent, wrong_prefix, wrong_command, foreign):
                    with self.subTest(hostile=observation.pid, cmdline=observation.cmdline), self.assertRaisesRegex(controller.CaptureError, "startup descendant differs|foreign runner"):
                        census = (parent_observation, gpu_records[0], observation) if observation.ppid == gpu_records[0].pid else (parent_observation, observation)
                        controller.isolated_startup_descendants(lifecycle, census)

    def test_run_capture_persists_all_lifecycle_failure_edges(self) -> None:
        cases = (
            ("create-job", {"job_create_attempted": True, "job_create_succeeded": False}),
            ("set-job-info", {"job_create_attempted": True, "job_create_succeeded": False}),
            ("launch-popen", {"root_launch_attempted": True, "root_launch_succeeded": False}),
            ("post-popen", {"root_launch_attempted": True, "root_launch_succeeded": True}),
            ("assign-pid", {"job_assign_attempted": True, "job_assign_succeeded": False}),
            ("assign-open", {"job_assign_attempted": True, "job_assign_succeeded": False}),
            ("assign-job", {"job_assign_attempted": True, "job_assign_succeeded": False}),
            ("assignment-close", {"job_assign_attempted": True, "job_assign_succeeded": True}),
            ("bind", {"root_bind_attempted": True, "root_bind_succeeded": False}),
            ("stdout-drain", {"drains_started_attempted": True, "drains_started_succeeded": False}),
            ("stderr-drain", {"drains_started_attempted": True, "drains_started_succeeded": False}),
            ("resume-thread", {"root_resume_attempted": True, "root_resume_succeeded": False}),
            ("resume-close", {"root_resume_attempted": True, "root_resume_succeeded": True, "root_thread_handle_close_succeeded": False}),
        )
        with self.subTest(id="J24"):
            for stage, expected in cases:
                with self.subTest(edge=stage):
                    status, restore = self._run_capture_lifecycle_failure(stage)
                    self.assertFalse(status["capture_prepared"])
                    self.assertFalse(status["normal_restore_eligible"])
                    self.assertFalse(status["restoration_started"])
                    self.assertFalse(status["restore_succeeded"])
                    self.assertFalse(status["model_execution_performed"])
                    self.assertEqual(status["primary_error"]["operation"], "capture")
                    self.assertIn(f"injected {stage}", status["primary_error"]["message"])
                    self.assertRegex(status["primary_error"]["recorded_at_utc"], r"^\d{4}-\d{2}-\d{2}T")
                    self.assertTrue(status["cleanup_errors"])
                    for key, value in expected.items():
                        self.assertEqual(status[key], value)
                    restore.assert_not_called()

    def test_initial_bind_failure_preserves_the_caller_owned_lifecycle(self) -> None:
        with self.subTest(id="C05"):
            status, restore = self._run_capture_lifecycle_failure("bind")
            self.assertTrue(status["root_launch_succeeded"])
            self.assertTrue(status["job_assign_succeeded"])
            self.assertFalse(status["root_bind_succeeded"])
            self.assertFalse(status["normal_restore_eligible"])
            self.assertIn("injected bind", status["primary_error"]["message"])
            restore.assert_not_called()

    def test_run_capture_successfully_restores_after_assigned_job_teardown(self) -> None:
        with self.subTest(id="J25"), tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity_root = root / "identity"
            executable = test_owned_executable(root, "runtime", "ollama.exe")
            app = controller.ProcessRecord(10, 0, "ollama app.exe", executable, birth_token(10.0))
            server = controller.ProcessRecord(11, 10, "ollama.exe", executable, birth_token(11.0))
            root_identity = controller.ProcessRecord(41, os.getpid(), "ollama.exe", executable, birth_token(41.0))

            class Child:
                pid = 41

                def __init__(self) -> None:
                    self.stdout = io.BytesIO()
                    self.stderr = io.BytesIO()

                def wait(self, timeout: float) -> int:
                    return 0

            child = Child()

            def create(lifecycle: controller.IsolatedLifecycle) -> None:
                lifecycle.job_create_attempted = True
                lifecycle.job_create_succeeded = True
                lifecycle.job_handle = 97
                lifecycle.advance(controller.IsolatedLifecyclePhase.JOB_CREATED)

            def launch(lifecycle: controller.IsolatedLifecycle) -> None:
                lifecycle.root_launch_attempted = True
                lifecycle.root_launch_succeeded = True
                lifecycle.process = child
                lifecycle.advance(controller.IsolatedLifecyclePhase.ROOT_LAUNCHED)

            def assign(lifecycle: controller.IsolatedLifecycle) -> None:
                lifecycle.job_assign_attempted = True
                lifecycle.job_assign_succeeded = True
                lifecycle.advance(controller.IsolatedLifecyclePhase.ROOT_ASSIGNED)

            def bind(lifecycle: controller.IsolatedLifecycle, _helper: Path | None) -> None:
                lifecycle.root_bind_attempted = True
                lifecycle.root_bind_succeeded = True
                lifecycle.root_identity = root_identity
                lifecycle.advance(controller.IsolatedLifecyclePhase.ROOT_BOUND)

            def drains(lifecycle: controller.IsolatedLifecycle, _stdout: Path, _stderr: Path) -> None:
                lifecycle.drains_started_attempted = True
                lifecycle.drains_started_succeeded = True
                lifecycle.stdout_drain = SimpleNamespace(overflow=threading.Event())
                lifecycle.stderr_drain = SimpleNamespace(overflow=threading.Event())
                lifecycle.advance(controller.IsolatedLifecyclePhase.DRAINS_STARTED)

            def resume(lifecycle: controller.IsolatedLifecycle) -> None:
                lifecycle.root_resume_attempted = True
                lifecycle.root_resume_succeeded = True
                lifecycle.root_thread_handle_close_attempted = True
                lifecycle.root_thread_handle_close_succeeded = True
                lifecycle.advance(controller.IsolatedLifecyclePhase.ROOT_RESUMED)

            def stream(_port: int, endpoint: str, destination: Path) -> None:
                destination.write_bytes(b"version" if endpoint == "/api/version" else b"tags")

            def force(_expected: controller.ProcessRecord, _parent: int | None, _label: str, **kwargs: object) -> bool:
                callback = kwargs.get("on_signal_issued")
                assert callable(callback)
                callback()
                return True

            authority = {
                "protocol_commit": "a" * 40,
                "protocol_tag": controller.PROTOCOL_TAG,
                "protocol_tag_object": "b" * 40,
                "source_manifest_sha256": "c" * 64,
            }
            api = SimpleNamespace(TerminateJobObject=Mock(return_value=True), CloseHandle=Mock(return_value=True))
            with ExitStack() as stack:
                stack.enter_context(patch.object(controller, "IDENTITY_ROOT", identity_root))
                stack.enter_context(patch.object(controller, "assert_regular"))
                stack.enter_context(patch.object(controller, "assert_no_reparse_or_ads"))
                stack.enter_context(patch.object(controller, "sha256_file", return_value="d" * 64))
                stack.enter_context(patch.object(controller, "validate_controller_authority", return_value=(authority, "e" * 64)))
                stack.enter_context(patch.object(controller, "controller_dependency_identity", return_value={}))
                stack.enter_context(patch.object(controller, "verify_protocol"))
                stack.enter_context(patch.object(controller, "verify_source_manifest", return_value={}))
                stack.enter_context(patch.object(controller, "verify_v4_failure_binding"))
                stack.enter_context(patch.object(controller, "verify_tracked_helpers", return_value={"tools/read_v5_process_identity.ps1": ROOT / "tools" / "read_v5_process_identity.ps1"}))
                stack.enter_context(patch.object(controller, "verify_isolated_runtime_and_store"))
                stack.enter_context(patch.object(controller, "assert_normal_state", return_value=(app, server)))
                stack.enter_context(patch.object(controller, "runtime_snapshot", return_value={}))
                stack.enter_context(patch.object(controller, "stream_loopback_get", side_effect=stream))
                stack.enter_context(patch.object(controller, "force_stop_exact", side_effect=force))
                stack.enter_context(patch.object(controller, "wait_for_listener_free"))
                stack.enter_context(patch.object(controller, "create_lifecycle_job", side_effect=create))
                stack.enter_context(patch.object(controller, "launch_suspended_isolated_root", side_effect=launch))
                stack.enter_context(patch.object(controller, "assign_suspended_root_to_job", side_effect=assign))
                stack.enter_context(patch.object(controller, "bind_suspended_root_identity", side_effect=bind))
                stack.enter_context(patch.object(controller, "start_lifecycle_drains", side_effect=drains))
                stack.enter_context(patch.object(controller, "resume_suspended_root", side_effect=resume))
                stack.enter_context(patch.object(controller, "wait_for_isolated_listener"))
                stack.enter_context(patch.object(controller, "assert_isolated_state"))
                stack.enter_context(patch.object(controller, "assert_exact_tags"))
                stack.enter_context(patch.object(controller, "assert_runtime_identity"))
                stack.enter_context(patch.object(controller, "read_json", return_value={"version": "0.33.2"}))
                stack.enter_context(patch.object(controller, "kernel32", return_value=api))
                stack.enter_context(patch.object(controller, "isolated_job_active_members", return_value=0))
                stack.enter_context(patch.object(controller, "join_bounded_drain"))
                stack.enter_context(patch.object(controller.time, "monotonic", side_effect=(0.0, 0.0, 0.0, 3.0, 3.0)))
                stack.enter_context(patch.object(controller.time, "sleep"))
                restore = stack.enter_context(patch.object(controller, "restore_normal"))
                controller.run_capture()
            status = json.loads((identity_root / "operation_status.json").read_text(encoding="utf-8"))
            self.assertTrue(status["capture_prepared"])
            self.assertTrue(status["normal_restore_eligible"])
            self.assertTrue(status["restoration_started"])
            self.assertTrue(status["restore_succeeded"])
            self.assertTrue(status["job_terminate_succeeded"])
            self.assertTrue(status["job_zero_window_confirmed"])
            self.assertTrue(status["job_close_succeeded"])
            self.assertIsNone(status["primary_error"])
            self.assertEqual(status["cleanup_errors"], [])
            restore.assert_called_once()

    def test_run_capture_reconciles_only_after_a_reached_normal_signal_boundary(self) -> None:
        executable = Path(__file__).resolve()
        app = controller.ProcessRecord(10, 0, "ollama app.exe", executable, birth_token(0.00001))
        server = controller.ProcessRecord(11, 10, "ollama.exe", executable, birth_token(0.000011))
        authority = {"protocol_commit": "a" * 40, "protocol_tag": controller.PROTOCOL_TAG, "protocol_tag_object": "b" * 40, "source_manifest_sha256": "c" * 64}

        for legacy_row, mode in (("C01", "app-refusal"), ("C04", "server-unknown"), ("C02", "server-absent"), ("C03", "server-exact")):
            with self.subTest(id=legacy_row, mode=mode), tempfile.TemporaryDirectory() as directory:
                identity_root = Path(directory) / "identity"
                signals: list[str] = []

                def stream(_port: int, endpoint: str, destination: Path) -> None:
                    destination.write_bytes(b"version" if endpoint == "/api/version" else b"tags")

                def force(
                    _expected: controller.ProcessRecord,
                    _parent: int | None,
                    label: str,
                    captured_mode: str = mode,
                    captured_signals: list[str] = signals,
                    **kwargs: object,
                ) -> bool:
                    if label == "normal app" and captured_mode == "app-refusal":
                        raise controller.CaptureError("app identity drift")
                    if label == "normal server" and captured_mode == "server-unknown":
                        raise controller.CaptureError("server identity drift")
                    callback = kwargs.get("on_signal_issued")
                    assert callable(callback)
                    callback()
                    captured_signals.append(label)
                    return True

                with ExitStack() as stack:
                    stack.enter_context(patch.object(controller, "IDENTITY_ROOT", identity_root))
                    stack.enter_context(patch.object(controller, "assert_regular"))
                    stack.enter_context(patch.object(controller, "assert_no_reparse_or_ads"))
                    stack.enter_context(patch.object(controller, "sha256_file", return_value="d" * 64))
                    stack.enter_context(patch.object(controller, "validate_controller_authority", return_value=(authority, "e" * 64)))
                    stack.enter_context(patch.object(controller, "controller_dependency_identity", return_value={}))
                    stack.enter_context(patch.object(controller, "verify_protocol"))
                    stack.enter_context(patch.object(controller, "verify_source_manifest", return_value={}))
                    stack.enter_context(patch.object(controller, "verify_v4_failure_binding"))
                    stack.enter_context(patch.object(controller, "verify_tracked_helpers", return_value={"tools/read_v5_process_identity.ps1": ROOT / "tools" / "read_v5_process_identity.ps1"}))
                    stack.enter_context(patch.object(controller, "verify_isolated_runtime_and_store"))
                    stack.enter_context(patch.object(controller, "assert_normal_state", return_value=(app, server)))
                    stack.enter_context(patch.object(controller, "runtime_snapshot", return_value={}))
                    stack.enter_context(patch.object(controller, "stream_loopback_get", side_effect=stream))
                    stack.enter_context(patch.object(controller, "force_stop_exact", side_effect=force))
                    stack.enter_context(patch.object(controller, "wait_for_listener_free", side_effect=controller.CaptureError("stop after normal reconciliation")))
                    restore = stack.enter_context(patch.object(controller, "restore_normal"))
                    with self.assertRaises(controller.CaptureError):
                        controller.run_capture()
                status = json.loads((identity_root / "operation_status.json").read_text(encoding="utf-8"))
                self.assertFalse(status["restoration_started"])
                self.assertFalse(status["restore_succeeded"])
                restore.assert_not_called()
                if mode == "app-refusal":
                    self.assertEqual(signals, [])
                elif mode == "server-unknown":
                    self.assertEqual(signals, ["normal app"])
                    self.assertEqual(status["normal_server_stop_resolution"], "uncertain")
                else:
                    self.assertEqual(signals, ["normal app", "normal server"])
    def test_process_preflight_is_read_only_and_cli_modes_are_exclusive(self) -> None:
        authority = {"protocol_commit": "a" * 40, "protocol_tag_object": "b" * 40}
        with (
            patch.object(controller, "assert_regular"),
            patch.object(controller, "assert_no_reparse_or_ads"),
            patch.object(controller, "sha256_file", return_value="d" * 64),
            patch.object(controller, "validate_controller_authority", return_value=(authority, "e" * 64)),
            patch.object(controller, "controller_dependency_identity", return_value={}),
            patch.object(controller, "verify_protocol"),
            patch.object(controller, "verify_source_manifest", return_value={}),
            patch.object(controller, "verify_tracked_helpers", return_value={"tools/read_v5_process_identity.ps1": ROOT / "tools" / "read_v5_process_identity.ps1"}),
            patch.object(controller, "assert_normal_state", side_effect=controller.CaptureError("unknown process")),
            patch.object(controller, "write_create_only_receipt", side_effect=self.fail),
            patch.object(controller, "stream_loopback_get", side_effect=self.fail),
            patch.object(controller, "force_stop_exact", side_effect=self.fail),
            patch.object(controller.subprocess, "Popen", side_effect=self.fail),
            patch.object(Path, "mkdir", side_effect=self.fail),
            self.assertRaises(controller.CaptureError),
            patch.object(sys, "argv", [str(CONTROLLER_PATH), "--process-preflight"]),
        ):
            controller.main()
        with (
            patch.object(controller, "run_process_preflight", side_effect=self.fail),
            patch.object(controller, "run_transport_self_test", side_effect=self.fail),
            patch.object(controller, "run_capture", side_effect=self.fail),
            self.assertRaises(SystemExit),
            patch.object(sys, "argv", [str(CONTROLLER_PATH), "--process-preflight", "--transport-self-test"]),
        ):
            controller.main()

    def test_restoration_phase_and_capability_matrix(self) -> None:
        app = controller.ProcessRecord(1, 0, "ollama app.exe", Path("app.exe"), birth_token(0.000001))
        server = controller.ProcessRecord(2, 1, "ollama.exe", Path("server.exe"), birth_token(0.000002))

        def topology(state: controller.NormalTopologyState) -> controller.NormalTopologyObservation:
            if state is controller.NormalTopologyState.NORMAL:
                return controller.NormalTopologyObservation(state, app, server, "normal")
            return controller.NormalTopologyObservation(state, None, None, state.value.lower())

        def run_case(
            row_id: str,
            states: list[controller.NormalTopologyState],
            expected_events: list[str],
            *,
            expected_exception: type[BaseException] | None = None,
            attempts: int = 3,
            version_transport_fault: bool = False,
            fault_stage: str | None = None,
            fault_type: type[BaseException] = controller.CaptureError,
            hash_matches: bool = True,
            version_matches: bool = True,
            preexisting: bool = False,
            publication_fault: bool = False,
            race: bool = False,
            monotonic: list[float] | None = None,
        ) -> None:
            with self.subTest(id=row_id, states=[state.value for state in states]), tempfile.TemporaryDirectory() as directory:
                identity_root = Path(directory) / "identity"
                identity_root.mkdir()
                canonical = identity_root / "restoration"
                if preexisting:
                    canonical.mkdir()
                    (canonical / "preserved").write_bytes(b"original")
                events: list[str] = []
                staging_roots: list[Path] = []
                remaining = [topology(state) for state in states]
                fault_issued = False
                transport_fault_issued = False

                def observe(_helper: Path | None = None) -> controller.NormalTopologyObservation:
                    self.assertTrue(remaining, "unexpected topology observation")
                    observation = remaining.pop(0)
                    events.append(f"observe:{observation.state.value}")
                    return observation

                def stream(port: int, endpoint: str, destination: Path) -> None:
                    nonlocal transport_fault_issued
                    events.append(f"endpoint:{endpoint}")
                    staging_roots.append(destination.parent)
                    if version_transport_fault and endpoint == "/api/version" and not transport_fault_issued:
                        transport_fault_issued = True
                        raise controller.CaptureError("transport")
                    destination.write_bytes(
                        (b"version" if version_matches else b"different")
                        if endpoint == "/api/version"
                        else b"tags"
                    )

                def launch(*args: object, **kwargs: object) -> SimpleNamespace:
                    events.append("launch")
                    return SimpleNamespace()

                def fault(stage: str) -> None:
                    nonlocal fault_issued
                    if stage == "retry":
                        events.append("retry")
                    if race and stage == "before-publication":
                        canonical.mkdir()
                        (canonical / "preserved").write_bytes(b"original")
                    if stage == fault_stage and not fault_issued:
                        fault_issued = True
                        raise fault_type("injected")

                def publish(staging: Path, destination: Path) -> None:
                    events.append("publish")
                    if publication_fault:
                        raise OSError("publication")
                    staging.rename(destination)

                with ExitStack() as stack:
                    stack.enter_context(patch.object(controller, "IDENTITY_ROOT", identity_root))
                    stack.enter_context(patch.object(controller, "observe_normal_topology", side_effect=observe))
                    stack.enter_context(patch.object(controller, "sha256_file", return_value="same" if hash_matches else "different"))
                    stack.enter_context(patch.object(controller, "stream_loopback_get", side_effect=stream))
                    stack.enter_context(patch.object(controller.subprocess, "Popen", side_effect=launch))
                    stack.enter_context(patch.object(controller, "atomic_no_replace_publish", side_effect=publish))
                    signal = stack.enter_context(patch.object(controller, "force_stop_exact"))
                    stack.enter_context(patch.object(controller.time, "sleep"))
                    if monotonic is not None:
                        stack.enter_context(patch.object(controller.time, "monotonic", side_effect=monotonic))
                    if expected_exception is None:
                        controller.restore_normal(
                            b"version",
                            b"tags",
                            {"app": "same", "server": "same"},
                            fault=fault,
                            attempts=attempts,
                        )
                    else:
                        with self.assertRaises(expected_exception):
                            controller.restore_normal(
                                b"version",
                                b"tags",
                                {"app": "same", "server": "same"},
                                fault=fault,
                                attempts=attempts,
                            )
                    signal.assert_not_called()
                self.assertEqual(events, expected_events)
                self.assertFalse(remaining)
                self.assertLessEqual(events.count("launch"), 1)
                self.assertFalse(list(identity_root.glob(".restoration-*")))
                distinct_attempt_roots: list[Path] = []
                for root in staging_roots:
                    if not distinct_attempt_roots or distinct_attempt_roots[-1] != root:
                        distinct_attempt_roots.append(root)
                self.assertEqual(len(distinct_attempt_roots), len(set(distinct_attempt_roots)))
                if preexisting or race:
                    self.assertEqual((canonical / "preserved").read_bytes(), b"original")
                if row_id in {"R15", "R16", "R17"}:
                    self.assertEqual(events.count("retry"), 0)
                if row_id == "R15":
                    self.assertEqual(events.count("publish"), 0)
                    self.assertEqual((canonical / "preserved").read_bytes(), b"original")
                if row_id == "R16" and publication_fault:
                    self.assertFalse(canonical.exists())
                if row_id == "R17":
                    self.assertEqual(events.count("publish"), 1)
                    self.assertEqual(events.count("endpoint:/api/version"), 1)
                    self.assertEqual(events.count("endpoint:/api/tags"), 1)
                    self.assertEqual((canonical / "processes.json").read_bytes(), controller.receipt_bytes({"app": app.__dict__ | {"exe": str(app.exe)}, "server": server.__dict__ | {"exe": str(server.exe)}}))
                    self.assertEqual((canonical / "version.response.json").read_bytes(), b"version")
                    self.assertEqual((canonical / "tags.response.json").read_bytes(), b"tags")

        success = [
            "observe:NORMAL",
            "observe:NORMAL",
            "endpoint:/api/version",
            "observe:NORMAL",
            "endpoint:/api/tags",
            "observe:NORMAL",
            "observe:NORMAL",
            "publish",
        ]
        run_case("R01", [controller.NormalTopologyState.NORMAL] * 5, success)
        run_case("R02", [controller.NormalTopologyState.EMPTY] + [controller.NormalTopologyState.NORMAL] * 5, ["observe:EMPTY", *success])
        run_case(
            "R03",
            [controller.NormalTopologyState.EMPTY, controller.NormalTopologyState.EMPTY, controller.NormalTopologyState.EMPTY] + [controller.NormalTopologyState.NORMAL] * 5,
            ["observe:EMPTY", "observe:EMPTY", "launch", "observe:EMPTY", *success],
        )
        for unsafe in (controller.NormalTopologyState.PARTIAL, controller.NormalTopologyState.UNKNOWN):
            run_case("R04", [unsafe], [f"observe:{unsafe.value}"], expected_exception=controller.OperationalUncertainty)
            run_case("R05", [controller.NormalTopologyState.EMPTY, unsafe], ["observe:EMPTY", f"observe:{unsafe.value}"], expected_exception=controller.OperationalUncertainty)
        run_case(
            "R06",
            [controller.NormalTopologyState.EMPTY, controller.NormalTopologyState.EMPTY, controller.NormalTopologyState.UNKNOWN],
            ["observe:EMPTY", "observe:EMPTY", "launch", "observe:UNKNOWN"],
            expected_exception=controller.OperationalUncertainty,
            monotonic=[0.0],
        )
        run_case(
            "R07",
            [controller.NormalTopologyState.EMPTY] * 3,
            ["observe:EMPTY", "observe:EMPTY", "launch", "observe:EMPTY"],
            expected_exception=controller.OperationalUncertainty,
            monotonic=[0.0, 61.0],
        )
        for unsafe in (controller.NormalTopologyState.EMPTY, controller.NormalTopologyState.PARTIAL, controller.NormalTopologyState.UNKNOWN):
            run_case("R08", [controller.NormalTopologyState.NORMAL, unsafe], ["observe:NORMAL", f"observe:{unsafe.value}"], expected_exception=controller.OperationalUncertainty)
            run_case(
                "R09",
                [controller.NormalTopologyState.NORMAL, controller.NormalTopologyState.NORMAL, unsafe],
                ["observe:NORMAL", "observe:NORMAL", "endpoint:/api/version", f"observe:{unsafe.value}"],
                expected_exception=controller.OperationalUncertainty,
            )
            run_case(
                "R10",
                [controller.NormalTopologyState.NORMAL, controller.NormalTopologyState.NORMAL, controller.NormalTopologyState.NORMAL, unsafe],
                ["observe:NORMAL", "observe:NORMAL", "endpoint:/api/version", "observe:NORMAL", "endpoint:/api/tags", f"observe:{unsafe.value}"],
                expected_exception=controller.OperationalUncertainty,
            )
            run_case(
                "R11",
                [controller.NormalTopologyState.NORMAL] * 4 + [unsafe],
                ["observe:NORMAL", "observe:NORMAL", "endpoint:/api/version", "observe:NORMAL", "endpoint:/api/tags", "observe:NORMAL", f"observe:{unsafe.value}"],
                expected_exception=controller.OperationalUncertainty,
            )
        run_case(
            "R12",
            [controller.NormalTopologyState.NORMAL] * 6,
            ["observe:NORMAL", "observe:NORMAL", "endpoint:/api/version", "retry", *success[1:]],
            version_transport_fault=True,
        )
        for unsafe in (controller.NormalTopologyState.PARTIAL, controller.NormalTopologyState.UNKNOWN):
            run_case(
                "R13",
                [controller.NormalTopologyState.NORMAL, controller.NormalTopologyState.NORMAL, unsafe],
                ["observe:NORMAL", "observe:NORMAL", "endpoint:/api/version", "retry", f"observe:{unsafe.value}"],
                expected_exception=controller.OperationalUncertainty,
                version_transport_fault=True,
            )
        run_case(
            "R14",
            [controller.NormalTopologyState.NORMAL] * 2,
            ["observe:NORMAL", "observe:NORMAL"],
            expected_exception=controller.RestorationIntegrityFailure,
            hash_matches=False,
        )
        run_case(
            "R14",
            [controller.NormalTopologyState.NORMAL] * 4,
            success[:6],
            expected_exception=controller.RestorationIntegrityFailure,
            version_matches=False,
        )
        run_case(
            "R15",
            [controller.NormalTopologyState.NORMAL] * 2,
            ["observe:NORMAL", "observe:NORMAL"],
            expected_exception=controller.RestorationIntegrityFailure,
            preexisting=True,
        )
        run_case(
            "R16",
            [controller.NormalTopologyState.NORMAL] * 5,
            success,
            expected_exception=controller.PublicationUncertainty,
            publication_fault=True,
        )
        run_case(
            "R16",
            [controller.NormalTopologyState.NORMAL] * 5,
            success[:-1],
            expected_exception=controller.PublicationUncertainty,
            race=True,
        )
        run_case(
            "R17",
            [controller.NormalTopologyState.NORMAL] * 5,
            success,
            expected_exception=controller.PublicationUncertainty,
            fault_stage="after-publication",
        )
        run_case(
            "R18",
            [controller.NormalTopologyState.NORMAL] * 6,
            ["observe:NORMAL", "observe:NORMAL", "retry", *success[1:]],
            fault_stage="after-process-observation",
            fault_type=controller.RetryableRestorationDataFault,
        )

    def test_restoration_fault_stage_union_and_exhaustion(self) -> None:
        app = controller.ProcessRecord(
            1, 0, "ollama app.exe", Path("app.exe"), birth_token(0.000001)
        )
        server = controller.ProcessRecord(
            2, 1, "ollama.exe", Path("server.exe"), birth_token(0.000002)
        )
        normal = controller.NormalTopologyObservation(
            controller.NormalTopologyState.NORMAL, app, server, "normal"
        )
        expected_processes = controller.receipt_bytes(
            {
                "app": app.__dict__ | {"exe": str(app.exe)},
                "server": server.__dict__ | {"exe": str(server.exe)},
            }
        )
        stage_rows = tuple(
            zip(
                sorted(RESTORATION_FAULT_ROWS),
                PREPUBLICATION_FAULT_STAGES,
                strict=True,
            )
        )
        expected_per_attempt = {
            "after-process-observation": (0, 0),
            "after-version-read": (1, 0),
            "after-tags-read": (1, 1),
            "after-raw-comparison": (1, 1),
            "after-final-topology": (1, 1),
            "before-publication": (1, 1),
        }
        executed_stages: set[str] = set()

        for row_id, target_stage in stage_rows:
            for mode in ("once-then-success", "repeat-to-exhaustion"):
                with self.subTest(id=row_id, stage=target_stage, mode=mode), tempfile.TemporaryDirectory() as directory:
                    identity_root = Path(directory) / "identity"
                    identity_root.mkdir()
                    canonical = identity_root / "restoration"
                    stage_hits = 0
                    retry_hits = 0
                    publication_hits = 0
                    endpoint_counts = {"/api/version": 0, "/api/tags": 0}
                    fault_roots: list[Path] = []
                    attempt_roots: list[Path] = []

                    def observe(_helper: Path | None = None) -> controller.NormalTopologyObservation:
                        return normal

                    def stream(
                        port: int,
                        endpoint: str,
                        destination: Path,
                        counts: dict[str, int] = endpoint_counts,
                    ) -> None:
                        self.assertEqual(port, controller.PORT)
                        counts[endpoint] += 1
                        destination.write_bytes(
                            b"version" if endpoint == "/api/version" else b"tags"
                        )

                    def fault(
                        stage: str,
                        *,
                        current_canonical: Path = canonical,
                        current_identity_root: Path = identity_root,
                        current_attempt_roots: list[Path] = attempt_roots,
                        current_target_stage: str = target_stage,
                        current_mode: str = mode,
                        current_fault_roots: list[Path] = fault_roots,
                    ) -> None:
                        nonlocal stage_hits, retry_hits
                        if stage == "retry":
                            retry_hits += 1
                            self.assertFalse(current_canonical.exists())
                            self.assertFalse(list(current_identity_root.glob(".restoration-*")))
                            return
                        if stage == "after-process-observation":
                            roots = list(current_identity_root.glob(".restoration-*"))
                            self.assertEqual(len(roots), 1)
                            self.assertNotIn(roots[0], current_attempt_roots)
                            current_attempt_roots.append(roots[0])
                            self.assertFalse(current_canonical.exists())
                        if stage != current_target_stage:
                            return
                        if current_mode == "once-then-success" and stage_hits:
                            return
                        stage_hits += 1
                        executed_stages.add(stage)
                        roots = list(current_identity_root.glob(".restoration-*"))
                        self.assertEqual(len(roots), 1)
                        root = roots[0]
                        self.assertNotIn(root, current_fault_roots)
                        current_fault_roots.append(root)
                        self.assertFalse(current_canonical.exists())
                        self.assertLessEqual(
                            sum(path.stat().st_size for path in root.rglob("*") if path.is_file()),
                            3 * controller.MAX_RESPONSE_BYTES,
                        )
                        if current_target_stage != "after-process-observation":
                            self.assertEqual((root / "processes.json").read_bytes(), expected_processes)
                            self.assertEqual((root / "version.response.json").read_bytes(), b"version")
                        if current_target_stage not in {"after-process-observation", "after-version-read"}:
                            self.assertEqual((root / "tags.response.json").read_bytes(), b"tags")
                        raise controller.RetryableRestorationDataFault(
                            f"injected {current_target_stage}"
                        )

                    def publish(staging: Path, destination: Path) -> None:
                        nonlocal publication_hits
                        publication_hits += 1
                        staging.rename(destination)

                    with ExitStack() as stack:
                        stack.enter_context(patch.object(controller, "IDENTITY_ROOT", identity_root))
                        stack.enter_context(patch.object(controller, "observe_normal_topology", side_effect=observe))
                        stack.enter_context(patch.object(controller, "sha256_file", return_value="same"))
                        stack.enter_context(patch.object(controller, "stream_loopback_get", side_effect=stream))
                        stack.enter_context(patch.object(controller, "atomic_no_replace_publish", side_effect=publish))
                        launch = stack.enter_context(patch.object(controller.subprocess, "Popen"))
                        signal = stack.enter_context(patch.object(controller, "force_stop_exact"))
                        stack.enter_context(patch.object(controller.time, "sleep"))
                        if mode == "once-then-success":
                            controller.restore_normal(
                                b"version",
                                b"tags",
                                {"app": "same", "server": "same"},
                                fault=fault,
                                attempts=2,
                            )
                        else:
                            with self.assertRaises(controller.RetryableRestorationDataFault):
                                controller.restore_normal(
                                    b"version",
                                    b"tags",
                                    {"app": "same", "server": "same"},
                                    fault=fault,
                                    attempts=controller.RESTORATION_ATTEMPTS,
                                )
                        launch.assert_not_called()
                        signal.assert_not_called()

                    version_per_attempt, tags_per_attempt = expected_per_attempt[target_stage]
                    if mode == "once-then-success":
                        self.assertEqual(stage_hits, 1)
                        self.assertEqual(retry_hits, 1)
                        self.assertEqual(len(fault_roots), 1)
                        self.assertEqual(len(attempt_roots), 2)
                        self.assertEqual(len(set(attempt_roots)), 2)
                        self.assertEqual(publication_hits, 1)
                        self.assertEqual(endpoint_counts["/api/version"], version_per_attempt + 1)
                        self.assertEqual(endpoint_counts["/api/tags"], tags_per_attempt + 1)
                        self.assertEqual((canonical / "processes.json").read_bytes(), expected_processes)
                        self.assertEqual((canonical / "version.response.json").read_bytes(), b"version")
                        self.assertEqual((canonical / "tags.response.json").read_bytes(), b"tags")
                    else:
                        self.assertEqual(stage_hits, controller.RESTORATION_ATTEMPTS)
                        self.assertEqual(retry_hits, controller.RESTORATION_ATTEMPTS - 1)
                        self.assertEqual(len(fault_roots), controller.RESTORATION_ATTEMPTS)
                        self.assertEqual(len(set(fault_roots)), controller.RESTORATION_ATTEMPTS)
                        if target_stage == "after-process-observation":
                            self.assertEqual(attempt_roots, fault_roots)
                        self.assertEqual(len(attempt_roots), controller.RESTORATION_ATTEMPTS)
                        self.assertEqual(len(set(attempt_roots)), controller.RESTORATION_ATTEMPTS)
                        self.assertEqual(publication_hits, 0)
                        self.assertEqual(
                            endpoint_counts["/api/version"],
                            version_per_attempt * controller.RESTORATION_ATTEMPTS,
                        )
                        self.assertEqual(
                            endpoint_counts["/api/tags"],
                            tags_per_attempt * controller.RESTORATION_ATTEMPTS,
                        )
                        self.assertFalse(canonical.exists())
                    self.assertFalse(list(identity_root.glob(".restoration-*")))

        self.assertEqual(executed_stages, set(PREPUBLICATION_FAULT_STAGES))

    def test_startup_readiness_acceptance_matrix(self) -> None:
        app = controller.ProcessRecord(
            1, 0, "ollama app.exe", Path("app.exe"), birth_token(1.0)
        )
        server = controller.ProcessRecord(
            2, 1, "ollama.exe", Path("server.exe"), birth_token(2.0)
        )

        def topology(state: controller.NormalTopologyState) -> controller.NormalTopologyObservation:
            if state is controller.NormalTopologyState.NORMAL:
                return controller.NormalTopologyObservation(state, app, server, "normal")
            return controller.NormalTopologyObservation(state, None, None, state.value.lower())

        traces: list[list[str]] = []

        def run_case(
            row_id: str,
            observations: list[
                controller.NormalTopologyObservation | controller.NormalTopologyState
            ],
            expected_events: list[str],
            *,
            expected_exception: type[BaseException] | None = None,
            monotonic: list[float] | None = None,
        ) -> None:
            with self.subTest(id=row_id), tempfile.TemporaryDirectory() as directory:
                identity_root = Path(directory) / "identity"
                identity_root.mkdir()
                canonical = identity_root / "restoration"
                remaining = [
                    topology(observation)
                    if isinstance(observation, controller.NormalTopologyState)
                    else observation
                    for observation in observations
                ]
                events: list[str] = []

                def observe(_helper: Path | None = None) -> controller.NormalTopologyObservation:
                    self.assertTrue(remaining)
                    observation = remaining.pop(0)
                    events.append(f"observe:{observation.state.value}")
                    return observation

                def launch(*args: object, **kwargs: object) -> SimpleNamespace:
                    events.append("launch")
                    return SimpleNamespace()

                def sleep(_seconds: float) -> None:
                    events.append("sleep")

                def stream(port: int, endpoint: str, destination: Path) -> None:
                    self.assertEqual(port, controller.PORT)
                    events.append(f"endpoint:{endpoint}")
                    destination.write_bytes(
                        b"version" if endpoint == "/api/version" else b"tags"
                    )

                def fault(stage: str) -> None:
                    if stage == "retry":
                        events.append("retry")

                def publish(staging: Path, destination: Path) -> None:
                    events.append("publish")
                    staging.rename(destination)

                with ExitStack() as stack:
                    stack.enter_context(patch.object(controller, "IDENTITY_ROOT", identity_root))
                    stack.enter_context(patch.object(controller, "observe_normal_topology", side_effect=observe))
                    stack.enter_context(patch.object(controller, "sha256_file", return_value="same"))
                    stack.enter_context(patch.object(controller, "stream_loopback_get", side_effect=stream))
                    stack.enter_context(patch.object(controller, "atomic_no_replace_publish", side_effect=publish))
                    popen = stack.enter_context(patch.object(controller.subprocess, "Popen", side_effect=launch))
                    signal = stack.enter_context(patch.object(controller, "force_stop_exact"))
                    stack.enter_context(patch.object(controller.time, "sleep", side_effect=sleep))
                    if monotonic is not None:
                        stack.enter_context(patch.object(controller.time, "monotonic", side_effect=monotonic))
                    if expected_exception is None:
                        controller.restore_normal(
                            b"version",
                            b"tags",
                            {"app": "same", "server": "same"},
                            fault=fault,
                            attempts=1,
                        )
                    else:
                        with self.assertRaises(expected_exception):
                            controller.restore_normal(
                                b"version",
                                b"tags",
                                {"app": "same", "server": "same"},
                                fault=fault,
                                attempts=1,
                            )
                    signal.assert_not_called()
                self.assertEqual(events, expected_events)
                self.assertFalse(remaining)
                self.assertLessEqual(popen.call_count, 1)
                self.assertFalse(list(identity_root.glob(".restoration-*")))
                if expected_exception is None:
                    self.assertEqual((canonical / "version.response.json").read_bytes(), b"version")
                    self.assertEqual((canonical / "tags.response.json").read_bytes(), b"tags")
                else:
                    self.assertFalse(canonical.exists())
                traces.append(events)

        successful_attempt = [
            "observe:NORMAL",
            "endpoint:/api/version",
            "observe:NORMAL",
            "endpoint:/api/tags",
            "observe:NORMAL",
            "observe:NORMAL",
            "publish",
        ]
        run_case(
            "S01",
            [
                controller.NormalTopologyState.EMPTY,
                controller.NormalTopologyState.EMPTY,
                controller.NormalTopologyState.PARTIAL,
                controller.NormalTopologyState.NORMAL,
                *([controller.NormalTopologyState.NORMAL] * 4),
            ],
            [
                "observe:EMPTY",
                "observe:EMPTY",
                "launch",
                "observe:PARTIAL",
                "sleep",
                "observe:NORMAL",
                *successful_attempt,
            ],
            monotonic=[0.0, 0.0],
        )
        run_case(
            "S02",
            [
                controller.NormalTopologyState.EMPTY,
                controller.NormalTopologyState.EMPTY,
                controller.NormalTopologyState.EMPTY,
                controller.NormalTopologyState.PARTIAL,
                controller.NormalTopologyState.EMPTY,
                controller.NormalTopologyState.PARTIAL,
                controller.NormalTopologyState.NORMAL,
                *([controller.NormalTopologyState.NORMAL] * 4),
            ],
            [
                "observe:EMPTY",
                "observe:EMPTY",
                "launch",
                "observe:EMPTY",
                "sleep",
                "observe:PARTIAL",
                "sleep",
                "observe:EMPTY",
                "sleep",
                "observe:PARTIAL",
                "sleep",
                "observe:NORMAL",
                *successful_attempt,
            ],
            monotonic=[0.0, 1.0, 2.0, 3.0, 4.0],
        )
        run_case(
            "S03",
            [
                controller.NormalTopologyState.EMPTY,
                controller.NormalTopologyState.EMPTY,
                controller.NormalTopologyState.PARTIAL,
                controller.NormalTopologyState.UNKNOWN,
            ],
            [
                "observe:EMPTY",
                "observe:EMPTY",
                "launch",
                "observe:PARTIAL",
                "sleep",
                "observe:UNKNOWN",
            ],
            expected_exception=controller.OperationalUncertainty,
            monotonic=[0.0, 0.0],
        )
        run_case(
            "S04",
            [
                controller.NormalTopologyState.EMPTY,
                controller.NormalTopologyState.EMPTY,
                controller.NormalTopologyState.PARTIAL,
                controller.NormalTopologyState.PARTIAL,
            ],
            [
                "observe:EMPTY",
                "observe:EMPTY",
                "launch",
                "observe:PARTIAL",
                "sleep",
                "observe:PARTIAL",
            ],
            expected_exception=controller.OperationalUncertainty,
            monotonic=[0.0, 0.0, 60.0],
        )

        readiness = [
            controller.NormalTopologyState.EMPTY,
            controller.NormalTopologyState.EMPTY,
            controller.NormalTopologyState.PARTIAL,
            controller.NormalTopologyState.NORMAL,
        ]
        prefixes = (
            ([], []),
            ([controller.NormalTopologyState.NORMAL], ["observe:NORMAL", "endpoint:/api/version"]),
            ([controller.NormalTopologyState.NORMAL] * 2, ["observe:NORMAL", "endpoint:/api/version", "observe:NORMAL", "endpoint:/api/tags"]),
            ([controller.NormalTopologyState.NORMAL] * 3, ["observe:NORMAL", "endpoint:/api/version", "observe:NORMAL", "endpoint:/api/tags", "observe:NORMAL"]),
        )
        readiness_events = [
            "observe:EMPTY",
            "observe:EMPTY",
            "launch",
            "observe:PARTIAL",
            "sleep",
            "observe:NORMAL",
        ]
        for preceding_states, preceding_events in prefixes:
            run_case(
                "S05",
                [*readiness, *preceding_states, controller.NormalTopologyState.PARTIAL],
                [*readiness_events, *preceding_events, "observe:PARTIAL"],
                expected_exception=controller.OperationalUncertainty,
                monotonic=[0.0, 0.0],
            )
        for state in (
            controller.NormalTopologyState.PARTIAL,
            controller.NormalTopologyState.UNKNOWN,
        ):
            run_case(
                "S06",
                [state],
                [f"observe:{state.value}"],
                expected_exception=controller.OperationalUncertainty,
            )
            run_case(
                "S07",
                [controller.NormalTopologyState.EMPTY, state],
                ["observe:EMPTY", f"observe:{state.value}"],
                expected_exception=controller.OperationalUncertainty,
            )

        with self.subTest(id="S08"):
            tree = ast.parse(CONTROLLER_PATH.read_text(encoding="utf-8"))
            restore = next(
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "restore_normal"
            )
            normal_launches = [
                node
                for node in ast.walk(restore)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "Popen"
                and node.args
                and isinstance(node.args[0], ast.List)
                and len(node.args[0].elts) == 1
                and isinstance(node.args[0].elts[0], ast.Call)
                and isinstance(node.args[0].elts[0].func, ast.Name)
                and node.args[0].elts[0].func.id == "str"
                and isinstance(node.args[0].elts[0].args[0], ast.Name)
                and node.args[0].elts[0].args[0].id == "NORMAL_APP"
            ]
            self.assertEqual(len(normal_launches), 1)
            assigned_names = [
                target.id
                for node in ast.walk(restore)
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                for target in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if isinstance(target, ast.Name)
            ]
            self.assertEqual(assigned_names.count("readiness_deadline"), 1)
            self.assertTrue(all(trace.count("launch") <= 1 for trace in traces))

        with self.subTest(id="S09"), tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_path = root / "ollama app.exe"
            server_path = root / "ollama.exe"
            app_path.write_bytes(b"app")
            server_path.write_bytes(b"server")
            app_row = process_info(
                pid=1,
                ppid=0,
                name="ollama app.exe",
                exe=str(app_path),
                create_time=1.0,
                cmdline=[],
            )
            server_row = process_info(
                pid=2,
                ppid=1,
                name="ollama.exe",
                exe=str(server_path),
                create_time=2.0,
                cmdline=[],
            )
            acquisitions = [
                [SimpleNamespace(info=idle_process_info()), SimpleNamespace(info=app_row)],
                [SimpleNamespace(info=idle_process_info()), SimpleNamespace(info=app_row), SimpleNamespace(info=server_row)],
            ]

            class NoSuchProcess(Exception):
                pass

            fake_psutil = SimpleNamespace(
                NoSuchProcess=NoSuchProcess,
                AccessDenied=PermissionError,
                process_iter=Mock(side_effect=acquisitions),
            )
            with (
                patch.object(controller.sys, "platform", "win32"),
                patch.object(controller, "psutil", fake_psutil),
                patch.object(controller, "NORMAL_APP", app_path),
                patch.object(controller, "NORMAL_SERVER", server_path),
                patch.object(controller, "listener_pids", side_effect=((), (2,))),
            ):
                partial = controller.observe_normal_topology()
                normal = controller.observe_normal_topology()
            self.assertIs(partial.state, controller.NormalTopologyState.PARTIAL)
            self.assertIs(normal.state, controller.NormalTopologyState.NORMAL)
            self.assertIsNotNone(normal.app)
            self.assertIsNotNone(normal.server)
            assert normal.app is not None and normal.server is not None
            self.assertEqual(normal.app.birth_token_hex, birth_token(1.0))
            self.assertEqual(normal.server.birth_token_hex, birth_token(2.0))
            run_case(
                "S09",
                [
                    controller.NormalTopologyState.EMPTY,
                    controller.NormalTopologyState.EMPTY,
                    partial,
                    normal,
                    *([normal] * 4),
                ],
                [
                    "observe:EMPTY",
                    "observe:EMPTY",
                    "launch",
                    "observe:PARTIAL",
                    "sleep",
                    "observe:NORMAL",
                    *successful_attempt,
                ],
                monotonic=[0.0, 0.0],
            )

    def test_idle_domain_composes_with_successful_restoration(self) -> None:
        with self.subTest(id="D01+R01"), tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity_root = root / "identity"
            identity_root.mkdir()
            app_path = root / "ollama app.exe"
            server_path = root / "ollama.exe"
            app_path.write_bytes(b"app")
            server_path.write_bytes(b"server")
            rows = [
                idle_process_info(),
                process_info(pid=1, ppid=0, name="ollama app.exe", exe=str(app_path), create_time=1.0, cmdline=[]),
                process_info(pid=2, ppid=1, name="ollama.exe", exe=str(server_path), create_time=2.0, cmdline=[]),
            ]

            class NoSuchProcess(Exception):
                pass

            process_iter = Mock(
                side_effect=lambda attributes, ad_value: [SimpleNamespace(info=dict(row)) for row in rows]
            )
            connection = SimpleNamespace(
                status="LISTEN",
                laddr=SimpleNamespace(ip=controller.HOST, port=controller.PORT),
                pid=2,
            )
            fake_psutil = SimpleNamespace(
                NoSuchProcess=NoSuchProcess,
                AccessDenied=PermissionError,
                CONN_LISTEN="LISTEN",
                process_iter=process_iter,
                net_connections=lambda kind: [connection],
            )
            events: list[str] = []
            real_observe = controller.observe_normal_topology

            def observe(helper: Path | None = None) -> controller.NormalTopologyObservation:
                observation = real_observe(helper)
                events.append(f"observe:{observation.state.value}")
                return observation

            def stream(port: int, endpoint: str, destination: Path) -> None:
                events.append(f"endpoint:{endpoint}")
                destination.write_bytes(b"version" if endpoint == "/api/version" else b"tags")

            def publish(staging: Path, destination: Path) -> None:
                events.append("publish")
                staging.rename(destination)

            with (
                patch.object(controller.sys, "platform", "win32"),
                patch.object(controller, "psutil", fake_psutil),
                patch.object(controller, "IDENTITY_ROOT", identity_root),
                patch.object(controller, "NORMAL_APP", app_path),
                patch.object(controller, "NORMAL_SERVER", server_path),
                patch.object(controller, "observe_normal_topology", side_effect=observe),
                patch.object(controller, "sha256_file", return_value="same"),
                patch.object(controller, "stream_loopback_get", side_effect=stream),
                patch.object(controller, "atomic_no_replace_publish", side_effect=publish),
                patch.object(controller.subprocess, "Popen") as launch,
                patch.object(controller, "force_stop_exact") as signal,
            ):
                controller.restore_normal(
                    b"version",
                    b"tags",
                    {"app": "same", "server": "same"},
                    attempts=1,
                )
            self.assertEqual(
                events,
                [
                    "observe:NORMAL",
                    "observe:NORMAL",
                    "endpoint:/api/version",
                    "observe:NORMAL",
                    "endpoint:/api/tags",
                    "observe:NORMAL",
                    "observe:NORMAL",
                    "publish",
                ],
            )
            self.assertEqual(process_iter.call_count, 5)
            launch.assert_not_called()
            signal.assert_not_called()
            self.assertTrue((identity_root / "restoration").is_dir())

    def test_recursive_inventory_includes_restoration_subtree_in_utf8_path_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "restoration").mkdir()
            (root / "z.json").write_bytes(b"z")
            (root / "restoration" / "a.json").write_bytes(b"a")
            inventory = controller.recursive_hash_inventory(root)
        self.assertEqual([entry["path"] for entry in inventory], ["restoration/a.json", "z.json"])

    def test_recursive_inventory_retains_nested_receipt_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            (root / "hash_inventory.json").write_bytes(b"root inventory")
            (root / "operation_receipt.json").write_bytes(b"root receipt")
            (root / "nested" / "hash_inventory.json").write_bytes(b"nested inventory")
            (root / "nested" / "operation_receipt.json").write_bytes(b"nested receipt")
            inventory = controller.recursive_hash_inventory(root)
        self.assertEqual([entry["path"] for entry in inventory], ["nested/hash_inventory.json", "nested/operation_receipt.json"])

    @unittest.skipUnless(os.name == "nt", "Authenticode is Windows-specific")
    def test_windows_helper_reads_signed_binary_and_rejects_unsigned_metacharacter_copy(self) -> None:
        helper = ROOT / "tools" / "read_v5_authenticode_identity.ps1"
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "signed & literal; copy.exe"
            shutil.copyfile(controller.POWERSHELL_EXE, copy)
            self.assertEqual(controller.authenticode_identity(controller.POWERSHELL_EXE, helper), controller.authenticode_identity(copy, helper))
            unsigned = Path(directory) / "unsigned & literal; copy.exe"
            unsigned.write_bytes(b"not signed")
            with self.assertRaises(controller.CaptureError):
                controller.authenticode_identity(unsigned, helper)

    @unittest.skipUnless(os.name == "nt" and controller.psutil is not None, "task-owned process test requires Windows and psutil")
    def test_windows_task_owned_child_is_stopped_through_retained_handle(self) -> None:
        child = controller.subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdout=controller.subprocess.DEVNULL, stderr=controller.subprocess.DEVNULL)
        try:
            expected = controller.process_record(controller.psutil.Process(child.pid), "task-owned child")
            controller.force_stop_exact(expected, os.getpid(), "task-owned child", retained_child=child)
            self.assertIsNotNone(child.returncode)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    def test_zz_declared_runtime_rows_and_production_fault_stages_are_exact(self) -> None:
        self.assertEqual(len(CANDIDATE04_ACCEPTANCE_ROWS), 76)
        self.assertEqual(len(BIRTH_TOKEN_ROWS), 8)
        self.assertEqual(len(RESTORATION_FAULT_ROWS), 6)
        self.assertEqual(len(STARTUP_ROWS), 9)
        self.assertEqual(len(JOB_ROWS), 25)
        self.assertEqual(JOB_ROW_IDS, tuple(f"J{index:02d}" for index in range(1, 26)))
        self.assertEqual(len(JOB_ROW_SET), len(JOB_ROW_IDS))
        self.assertTrue(all(isinstance(obligation, str) and obligation for _row_id, obligation in JOB_ROWS))
        row_families = (
            CANDIDATE04_ACCEPTANCE_ROWS,
            BIRTH_TOKEN_ROWS,
            RESTORATION_FAULT_ROWS,
            STARTUP_ROWS,
            JOB_ROWS,
        )
        self.assertEqual(len(DECLARED_EXECUTED_ROWS), sum(len(rows) for rows in row_families))
        expected_executed = set(DECLARED_EXECUTED_ROWS)
        if not (sys.platform == "win32" and controller.psutil is not None):
            expected_executed -= {"D10", "T14"}
        self.assertEqual(self.executed_rows, expected_executed)

        tree = ast.parse(CONTROLLER_PATH.read_text(encoding="utf-8"))
        restore = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "restore_normal"
        )
        fault_literals = [
            call.args[0].value
            for call in ast.walk(restore)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "fault"
            and len(call.args) == 1
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
        ]
        expected_fault_literals = {
            *PREPUBLICATION_FAULT_STAGES,
            "after-publication",
            "retry",
        }
        self.assertEqual(set(fault_literals), expected_fault_literals)
        self.assertEqual(len(fault_literals), len(expected_fault_literals))
        self.assertTrue(all(fault_literals.count(stage) == 1 for stage in expected_fault_literals))
        self.assertEqual(
            set(fault_literals) - {"after-publication", "retry"},
            set(PREPUBLICATION_FAULT_STAGES),
        )


if __name__ == "__main__":
    unittest.main()
