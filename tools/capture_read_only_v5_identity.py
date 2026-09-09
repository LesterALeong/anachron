"""Read-only v5 runtime identity capture controller.

This controller has three modes: normal capture, loopback-only transport
self-test, and read-only process preflight. The two test modes never start or
stop Ollama.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import http.client
import json
import math
import os
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO

try:
    import psutil
except ModuleNotFoundError:
    psutil = None  # type: ignore[assignment]


PROTOCOL_ROOT = Path(r"C:\Users\leste\Downloads\Repos\anachron-v5-protocol-v5")
PROTOCOL_TAG = "v5-measurement-protocol-v5"
SOURCE_MANIFEST = Path(
    r"C:\Users\leste\Downloads\Repos\anachron-v5-evidence\source-manifest-v5\source_manifest.json"
)
STAGE_ROOT = Path(r"C:\Users\leste\Downloads\Repos\anachron-v4-evidence\ollama-0.33.2-isolated")
ISOLATED_EXE = STAGE_ROOT / "runtime" / "ollama.exe"
ISOLATED_EXE_SHA256 = "c79df1e0c1bfa10ed813c7030ac4c3ba38bb0e350bd7322d9bb58320343235c6"
ISOLATED_MODELS = STAGE_ROOT / "models"
IDENTITY_ROOT = Path(
    r"C:\Users\leste\Downloads\Repos\anachron-v5-evidence\runtime-identity-v5-protocol-v5"
)
V4_FAILURE_BINDING = Path("research/v5_measurement/v4_failure_binding.json")
V4_FAILURE_ROOT = Path(
    r"C:\Users\leste\Downloads\Repos\anachron-v5-evidence\runtime-identity-v5-protocol-v4"
)
NORMAL_APP = Path(r"C:\Users\leste\AppData\Local\Programs\Ollama\ollama app.exe")
NORMAL_SERVER = Path(r"C:\Users\leste\AppData\Local\Programs\Ollama\ollama.exe")
POWERSHELL_EXE = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
POWERSHELL_EXE_SHA256 = "8bb6fa8c283b4d92120b1ef249a9b311b0f804d4cabbe9981159976c8be76a5e"
EXPECTED_SIGNER_SUBJECT = (
    "CN=Ollama Inc., O=Ollama Inc., L=Toronto, S=Ontario, C=CA, "
    "SERIALNUMBER=2713355, OID.2.5.4.15=Private Organization, "
    "OID.1.3.6.1.4.1.311.60.2.1.2=Ontario, "
    "OID.1.3.6.1.4.1.311.60.2.1.3=CA"
)
EXPECTED_SIGNER_THUMBPRINT = "716CD3BC8C02361431A18F56F98C72DE88066103"
EXPECTED_PYTHON_VERSION = (3, 12, 10)
EXPECTED_PYTHON_SHA256 = "4d6f5f81a4bca11191c4c7c6b43632694d0a4ce74e068619d8fdc161d469859a"
EXPECTED_PSUTIL_VERSION = "7.2.2"
EXPECTED_PSUTIL_HASHES = {
    "__init__.py": "7b6a0675824eb1fa2ff0cb1eb36e358dc454703e51dfa4e9a0e6ccd26a159f0c",
    "_pswindows.py": "0bbd52dcb214735be4168d11a2ae192d5bc7265c8cf72c611179476479687f54",
    "_psutil_windows.pyd": "0035450801bd7d938e9e146c5ec28e619cb5a5f4a18cdc53ac7e9734c7f94f78",
}
EXPECTED_MODELS = (
    ("qwen2.5:7b", "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"),
    ("qwen3:14b-q4_K_M", "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8"),
)
EXPECTED_MANIFESTS = {
    ISOLATED_MODELS / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5" / "7b": EXPECTED_MODELS[0][1],
    ISOLATED_MODELS / "manifests" / "registry.ollama.ai" / "library" / "qwen3" / "14b-q4_K_M": EXPECTED_MODELS[1][1],
}
ADMITTED_ENDPOINTS = frozenset(("/api/version", "/api/tags"))
HOST = "127.0.0.1"
PORT = 11434
CONNECT_HEADER_SECONDS = 5.0
TOTAL_TRANSFER_SECONDS = 20.0
MAX_RESPONSE_BYTES = 1 << 20
RUNTIME_IDENTITY_BYTES = (
    b"{\n"
    b"  \"models\": [\n"
    b"    {\n"
    b"      \"digest\": \"845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e\",\n"
    b"      \"name\": \"qwen2.5:7b\"\n"
    b"    },\n"
    b"    {\n"
    b"      \"digest\": \"bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8\",\n"
    b"      \"name\": \"qwen3:14b-q4_K_M\"\n"
    b"    }\n"
    b"  ],\n"
    b"  \"version\": \"0.33.2\"\n"
    b"}\n"
)
EXPECTED_RUNTIME_IDENTITY_SHA256 = "5d3d44932b590dc57f87458f3b384605b04721bf4695a155bb9da8432797cd96"
AUTHORITY_SIDECAR = Path(__file__).with_name("controller_authority.json")
AUTHORITY_SCHEMA = "anachron-v5-controller-authority-v1"
RUNNER_TOKENS = ("run_v5_recovery.py", "run_v5_conditional_campaign.ps1")
MAX_IDENTITY_INVENTORY_BYTES = 16 << 20
RESTORATION_ATTEMPTS = 3
MAX_HELPER_OUTPUT_BYTES = 4096
MAX_AUTHENTICODE_OUTPUT_BYTES = MAX_HELPER_OUTPUT_BYTES
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0)
CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS = 1
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_JOB_ASSIGN_ACCESS = PROCESS_TERMINATE | PROCESS_SET_QUOTA
THREAD_SUSPEND_RESUME = 0x0002
JOB_TERMINATION_EXIT_CODE = 1
INVALID_DWORD = 0xFFFFFFFF
ISOLATED_STARTUP_SECONDS = 15.0
ISOLATED_CENSUS_SECONDS = 0.25
ISOLATED_QUIESCENCE_SECONDS = 2.0
ISOLATED_TEARDOWN_SECONDS = 10.0
PROCESS_FIELD_UNAVAILABLE = object()
PROCESS_IDENTITY_HELPERS = (
    "tools/read_v5_authenticode_identity.ps1",
    "tools/read_v5_process_identity.ps1",
)


class CaptureError(RuntimeError):
    """A fail-closed controller error."""


class OperationalUncertainty(CaptureError):
    """An operational observation cannot establish a safe topology."""


class OperationalCensusUncertain(OperationalUncertainty):
    """The process enumeration domain cannot be established exactly."""


class RetryableRestorationDataFault(CaptureError):
    """A bounded private-staging or endpoint operation may be retried."""


class RestorationIntegrityFailure(CaptureError):
    """Restored bytes or namespace state differ from the baseline."""


class PublicationUncertainty(CaptureError):
    """Canonical publication may have changed the destination namespace."""


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    ppid: int
    name: str
    exe: Path
    birth_token_hex: str

    def __post_init__(self) -> None:
        validate_birth_token_hex(self.birth_token_hex)


class ProcessDisposition(Enum):
    UNRELATED = "UNRELATED"
    HOST_CLEAR = "HOST_CLEAR"
    BLOCKER = "BLOCKER"
    OLLAMA = "OLLAMA"


@dataclass(frozen=True)
class ProcessObservation:
    pid: int
    ppid: int | None
    name: str
    exe: Path | None
    create_time: float | None
    birth_token_hex: str | None
    cmdline: tuple[str, ...] | None
    disposition: ProcessDisposition
    record: ProcessRecord | None


class NormalTopologyState(Enum):
    NORMAL = "NORMAL"
    EMPTY = "EMPTY"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class NormalTopologyObservation:
    state: NormalTopologyState
    app: ProcessRecord | None
    server: ProcessRecord | None
    diagnostic: str


@dataclass
class BoundedDrain:
    destination: Path
    overflow: threading.Event
    errors: list[BaseException]
    thread: threading.Thread


@dataclass(frozen=True)
class FailureReceipt:
    phase: str
    operation: str
    message: str
    recorded_at_utc: str


class IsolatedLifecyclePhase(Enum):
    NEW = "new"
    JOB_CREATED = "job-created"
    ROOT_LAUNCHED = "root-launched"
    ROOT_ASSIGNED = "root-assigned"
    ROOT_BOUND = "root-bound"
    DRAINS_STARTED = "drains-started"
    ROOT_RESUMED = "root-resumed"
    STOPPED = "stopped"


@dataclass
class IsolatedLifecycle:
    phase: IsolatedLifecyclePhase = IsolatedLifecyclePhase.NEW
    job_handle: int | None = None
    process: subprocess.Popen[bytes] | None = None
    root_identity: ProcessRecord | None = None
    stdout_drain: BoundedDrain | None = None
    stderr_drain: BoundedDrain | None = None
    assignment_process_handle: int | None = None
    root_thread_handle: int | None = None
    job_create_attempted: bool = False
    job_create_succeeded: bool = False
    root_launch_attempted: bool = False
    root_launch_succeeded: bool = False
    job_assign_attempted: bool = False
    job_assign_succeeded: bool = False
    root_bind_attempted: bool = False
    root_bind_succeeded: bool = False
    drains_started_attempted: bool = False
    drains_started_succeeded: bool = False
    root_resume_attempted: bool = False
    root_resume_succeeded: bool = False
    root_thread_handle_close_attempted: bool = False
    root_thread_handle_close_succeeded: bool = False
    startup_observer_started: bool = False
    startup_observed: bool = False
    quiescence_succeeded: bool = False
    teardown_path: str = "none"
    stop_attempted: bool = False
    job_terminate_attempted: bool = False
    job_terminate_succeeded: bool = False
    root_waited: bool = False
    job_zero_window_confirmed: bool = False
    prebind_root_stop_attempted: bool = False
    prebind_root_stop_succeeded: bool = False
    prebind_baseline_clean: bool = False
    pipes_closed: bool = False
    drains_joined: bool = False
    job_close_attempted: bool = False
    job_close_succeeded: bool = False
    cleanup_errors: list[FailureReceipt] = field(default_factory=list)
    primary_error: FailureReceipt | None = None

    def advance(self, phase: IsolatedLifecyclePhase) -> None:
        if phase.value == self.phase.value:
            return
        order = tuple(IsolatedLifecyclePhase)
        if order.index(phase) <= order.index(self.phase):
            raise CaptureError("isolated lifecycle phase regressed")
        self.phase = phase


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


@dataclass
class BoundedPipe:
    content: bytearray
    overflow: threading.Event
    errors: list[BaseException]
    thread: threading.Thread


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_create_only_bytes(path: Path, content: bytes) -> None:
    with path.open("xb") as destination:
        destination.write(content)
        destination.flush()
        os.fsync(destination.fileno())


def receipt_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")


def write_create_only_receipt(path: Path, value: Any) -> None:
    write_create_only_bytes(path, receipt_bytes(value))


def assert_regular(path: Path, label: str) -> None:
    try:
        stat_result = path.lstat()
    except FileNotFoundError as error:
        raise CaptureError(f"{label} is missing: {path}") from error
    if not path.is_file() or getattr(stat_result, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise CaptureError(f"{label} is not a regular non-reparse file: {path}")


class Win32FindStreamData(ctypes.Structure):
    _fields_ = (("stream_size", ctypes.c_longlong), ("stream_name", ctypes.c_wchar * (260 + 36)))


if os.name == "nt":
    KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    KERNEL32.FindFirstStreamW.argtypes = (ctypes.c_wchar_p, ctypes.c_int, ctypes.POINTER(Win32FindStreamData), ctypes.c_int)
    KERNEL32.FindFirstStreamW.restype = ctypes.c_void_p
    KERNEL32.FindNextStreamW.argtypes = (ctypes.c_void_p, ctypes.POINTER(Win32FindStreamData))
    KERNEL32.FindNextStreamW.restype = ctypes.c_int
    KERNEL32.FindClose.argtypes = (ctypes.c_void_p,)
    KERNEL32.FindClose.restype = ctypes.c_int
else:
    KERNEL32 = None
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_HANDLE_EOF = 38


def stream_names(path: Path) -> tuple[str, ...]:
    if KERNEL32 is None:
        return ("::$DATA",)
    data = Win32FindStreamData()
    handle = KERNEL32.FindFirstStreamW(str(path), 0, ctypes.byref(data), 0)
    if handle == INVALID_HANDLE_VALUE:
        error = ctypes.get_last_error()
        if error == ERROR_HANDLE_EOF:
            return ()
        raise CaptureError(f"cannot enumerate streams for {path}: Win32 error {error}")
    names = [data.stream_name]
    try:
        while KERNEL32.FindNextStreamW(handle, ctypes.byref(data)):
            names.append(data.stream_name)
        error = ctypes.get_last_error()
        if error != ERROR_HANDLE_EOF:
            raise CaptureError(f"cannot finish stream enumeration for {path}: Win32 error {error}")
    finally:
        KERNEL32.FindClose(handle)
    return tuple(names)


def assert_no_reparse_or_ads(root: Path, label: str) -> None:
    try:
        root_stat = root.lstat()
    except FileNotFoundError as error:
        raise CaptureError(f"{label} is missing: {root}") from error
    if getattr(root_stat, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise CaptureError(f"{label} root is a reparse point")
    entries = (root, *root.rglob("*")) if root.is_dir() else (root,)
    for entry in entries:
        entry_stat = entry.lstat()
        if getattr(entry_stat, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise CaptureError(f"{label} contains a reparse point: {entry}")
        if any(name != "::$DATA" for name in stream_names(entry)):
            raise CaptureError(f"{label} contains an alternate data stream: {entry}")


def assert_temp_root(parent: Path, root: Path, label: str, require_no_ads: bool = True) -> None:
    resolved_parent = parent.resolve(strict=True)
    if root.parent != resolved_parent:
        raise CaptureError(f"{label} root is not a direct child of its temp parent")
    root_stat = root.lstat()
    if not root.is_dir() or getattr(root_stat, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise CaptureError(f"{label} root is not a regular directory")
    if root.resolve(strict=True).parent != resolved_parent:
        raise CaptureError(f"{label} root resolved parent differs")
    if require_no_ads and any(name != "::$DATA" for name in stream_names(root)):
        raise CaptureError(f"{label} root contains an alternate data stream")


def safe_temp_cleanup(parent: Path, root: Path, known_leaves: tuple[str, ...], label: str) -> None:
    assert_temp_root(parent, root, label)
    for name in known_leaves:
        leaf = root / name
        if leaf.parent != root or Path(name).name != name:
            raise CaptureError(f"{label} cleanup leaf is not direct")
        if leaf.exists():
            assert_regular(leaf, f"{label} cleanup leaf")
            if any(stream != "::$DATA" for stream in stream_names(leaf)):
                raise CaptureError(f"{label} cleanup leaf contains an alternate data stream")
            leaf.unlink()
    if any(root.iterdir()):
        raise CaptureError(f"{label} root is not empty during cleanup")
    root.rmdir()


def read_bounded_bytes(path: Path, label: str) -> bytes:
    assert_regular(path, label)
    with path.open("rb", buffering=0) as source:
        length = os.fstat(source.fileno()).st_size
        if length > MAX_RESPONSE_BYTES:
            raise CaptureError(f"{label} exceeds {MAX_RESPONSE_BYTES} bytes")
        result = bytearray(length)
        offset = 0
        while offset < length:
            count = source.readinto(memoryview(result)[offset:])
            if not count:
                raise CaptureError(f"{label} was truncated while being read")
            offset += count
        if source.read(1):
            raise CaptureError(f"{label} grew while being read")
        return bytes(result)


def read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(read_bounded_bytes(path, label).decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CaptureError(f"{label} is not strict UTF-8 JSON") from error


def remaining_seconds(deadline: float, label: str) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CaptureError(f"{label} exceeded whole-transfer timeout")
    return remaining


def stream_loopback_get(port: int, endpoint: str, destination: Path) -> None:
    if endpoint not in ADMITTED_ENDPOINTS:
        raise CaptureError(f"endpoint is not admitted: {endpoint}")
    deadline = time.monotonic() + TOTAL_TRANSFER_SECONDS
    with destination.open("xb") as output:
        connection = http.client.HTTPConnection(HOST, port, timeout=min(CONNECT_HEADER_SECONDS, remaining_seconds(deadline, endpoint)))
        try:
            connection.putrequest("GET", endpoint, skip_host=True, skip_accept_encoding=True)
            connection.putheader("Host", f"{HOST}:{port}")
            connection.endheaders()
            response = connection.getresponse()
            if response.status != 200:
                raise CaptureError(f"loopback response status differs for {endpoint}: {response.status}")
            response_socket = connection.sock or getattr(getattr(response.fp, "raw", None), "_sock", None)
            if response_socket is None:
                raise CaptureError("loopback response socket is unavailable")
            written = 0
            while True:
                response_socket.settimeout(min(CONNECT_HEADER_SECONDS, remaining_seconds(deadline, endpoint)))
                requested = min(8192, MAX_RESPONSE_BYTES - written + 1)
                chunk = response.read(requested)
                if not chunk:
                    break
                if written + len(chunk) > MAX_RESPONSE_BYTES:
                    raise CaptureError(f"loopback response exceeds {MAX_RESPONSE_BYTES} bytes")
                output.write(chunk)
                written += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        except (OSError, http.client.HTTPException) as error:
            raise CaptureError(f"loopback GET failed for {endpoint}: {error}") from error
        finally:
            connection.close()
    read_bounded_bytes(destination, f"loopback response {endpoint}")


def controller_dependency_identity() -> dict[str, Any]:
    if psutil is None:
        raise CaptureError("psutil is unavailable")
    if sys.version_info[:3] != EXPECTED_PYTHON_VERSION:
        raise CaptureError(f"Python version differs: {sys.version_info[:3]}")
    python_exe = Path(sys.executable).resolve(strict=True)
    if sha256_file(python_exe) != EXPECTED_PYTHON_SHA256:
        raise CaptureError("Python executable SHA-256 differs")
    if psutil.__version__ != EXPECTED_PSUTIL_VERSION:
        raise CaptureError(f"psutil version differs: {psutil.__version__}")
    package_root = Path(psutil.__file__).resolve(strict=True).parent
    hashes: dict[str, str] = {}
    for name, expected_hash in EXPECTED_PSUTIL_HASHES.items():
        candidate = package_root / name
        assert_regular(candidate, f"psutil {name}")
        actual_hash = sha256_file(candidate)
        if actual_hash != expected_hash:
            raise CaptureError(f"psutil {name} SHA-256 differs")
        hashes[name] = actual_hash
    assert_regular(POWERSHELL_EXE, "PowerShell executable")
    assert_no_reparse_or_ads(POWERSHELL_EXE, "PowerShell executable")
    if sha256_file(POWERSHELL_EXE) != POWERSHELL_EXE_SHA256:
        raise CaptureError("PowerShell executable SHA-256 differs")
    return {
        "powershell_executable": str(POWERSHELL_EXE),
        "powershell_sha256": POWERSHELL_EXE_SHA256,
        "psutil_module_root": str(package_root),
        "psutil_sha256": hashes,
        "psutil_version": psutil.__version__,
        "python_executable": str(python_exe),
        "python_sha256": EXPECTED_PYTHON_SHA256,
        "python_version": ".".join(str(part) for part in EXPECTED_PYTHON_VERSION),
    }


def validate_controller_authority(controller_sha256: str) -> tuple[dict[str, str], str]:
    assert_regular(AUTHORITY_SIDECAR, "controller authority sidecar")
    assert_no_reparse_or_ads(AUTHORITY_SIDECAR, "controller authority sidecar")
    authority = read_json(AUTHORITY_SIDECAR, "controller authority sidecar")
    expected_keys = {
        "controller_sha256",
        "protocol_commit",
        "protocol_tag",
        "protocol_tag_object",
        "schema_version",
        "source_manifest_sha256",
    }
    if not isinstance(authority, dict) or set(authority) != expected_keys:
        raise CaptureError("controller authority sidecar differs")
    normalized = {key: value for key, value in authority.items() if isinstance(value, str)}
    if (
        len(normalized) != len(expected_keys)
        or normalized["controller_sha256"] != controller_sha256
        or normalized["protocol_tag"] != PROTOCOL_TAG
        or normalized["schema_version"] != AUTHORITY_SCHEMA
        or any(len(normalized[key]) != 64 or any(character not in "0123456789abcdef" for character in normalized[key]) for key in ("controller_sha256", "source_manifest_sha256"))
        or any(len(normalized[key]) != 40 or any(character not in "0123456789abcdef" for character in normalized[key]) for key in ("protocol_commit", "protocol_tag_object"))
        or read_bounded_bytes(AUTHORITY_SIDECAR, "controller authority sidecar") != receipt_bytes(normalized)
    ):
        raise CaptureError("controller authority sidecar differs")
    return normalized, sha256_file(AUTHORITY_SIDECAR)


def git_output(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(PROTOCOL_ROOT), *arguments], check=False, capture_output=True, text=True, encoding="utf-8"
    )
    if completed.returncode != 0:
        raise CaptureError(f"protocol git command failed: {' '.join(arguments)}")
    return completed.stdout.strip()


def verify_protocol(authority: dict[str, str]) -> None:
    assert_no_reparse_or_ads(PROTOCOL_ROOT, "protocol root")
    if Path(git_output("rev-parse", "--show-toplevel")).resolve(strict=True) != PROTOCOL_ROOT.resolve(strict=True):
        raise CaptureError("protocol root differs")
    if git_output("status", "--porcelain", "--untracked-files=all"):
        raise CaptureError("protocol checkout is not clean")
    detached = subprocess.run(["git", "-C", str(PROTOCOL_ROOT), "symbolic-ref", "-q", "HEAD"], check=False)
    if detached.returncode != 1:
        raise CaptureError("protocol checkout is not detached")
    if (
        git_output("rev-parse", "HEAD") != authority["protocol_commit"]
        or git_output("cat-file", "-t", f"refs/tags/{PROTOCOL_TAG}") != "tag"
        or git_output("rev-parse", f"refs/tags/{PROTOCOL_TAG}^{{tag}}") != authority["protocol_tag_object"]
        or git_output("rev-parse", f"refs/tags/{PROTOCOL_TAG}^{{}}") != authority["protocol_commit"]
    ):
        raise CaptureError("protocol tag identity differs")


def verify_source_manifest(authority: dict[str, str]) -> dict[str, Any]:
    assert_no_reparse_or_ads(SOURCE_MANIFEST, "source manifest")
    if sha256_file(SOURCE_MANIFEST) != authority["source_manifest_sha256"]:
        raise CaptureError("source manifest SHA-256 differs")
    manifest = read_json(SOURCE_MANIFEST, "source manifest")
    release = manifest.get("release", {})
    if (
        manifest.get("schema_version") != "anachron-v5-source-manifest-v2"
        or release.get("commit") != authority["protocol_commit"]
        or release.get("tag") != PROTOCOL_TAG
        or release.get("tag_object") != authority["protocol_tag_object"]
        or release.get("tag_peeled") != authority["protocol_commit"]
    ):
        raise CaptureError("source manifest release identity differs")
    return manifest


def verify_v4_failure_binding(manifest: dict[str, Any]) -> None:
    binding_path = PROTOCOL_ROOT / V4_FAILURE_BINDING
    assert_no_reparse_or_ads(binding_path, "v4 failure binding")
    binding = read_json(binding_path, "v4 failure binding")
    if not isinstance(binding, dict) or set(binding) != {"failure_root", "kind", "v4_release"}:
        raise CaptureError("v4 failure binding differs")
    release = binding["v4_release"]
    failure_root = binding["failure_root"]
    expected_release = {
        "branch": "protocol/v5-successor-v4",
        "controller_sha256": "c7b56cdc7661015f6fc4ab8796ee048c74afb1d92a989e013ab772fe7ee0ba1b",
        "commit": "c8f81bb9dc9c7b1f701f8e894b4e86a120a04a71",
        "source_manifest_sha256": "83422ab979149520c0c85c5e96a88e1f549f12fae1020be94670286571453d74",
        "tag": "v5-measurement-protocol-v4",
        "tag_object": "ad369ca0cdd667b2af305341517fc24baa3c5404",
    }
    if binding["kind"] != "anachron-v5-v4-failure-binding" or release != expected_release or not isinstance(failure_root, dict):
        raise CaptureError("v4 failure binding differs")
    members = failure_root.get("members")
    status = failure_root.get("status")
    if failure_root.get("path") != str(V4_FAILURE_ROOT) or not isinstance(members, list) or not isinstance(status, dict):
        raise CaptureError("v4 failure binding differs")
    if V4_FAILURE_BINDING.as_posix() not in {entry.get("path") for entry in manifest.get("governed_files", []) if isinstance(entry, dict)}:
        raise CaptureError("v4 failure binding is not governed")
    assert_no_reparse_or_ads(V4_FAILURE_ROOT, "v4 failure root")
    actual_paths = {entry.name for entry in V4_FAILURE_ROOT.iterdir()}
    expected_paths = {entry.get("path") for entry in members if isinstance(entry, dict)}
    if actual_paths != expected_paths or len(members) != 6:
        raise CaptureError("v4 failure root topology differs")
    for member in members:
        if not isinstance(member, dict) or set(member) != {"bytes", "path", "sha256"}:
            raise CaptureError("v4 failure binding differs")
        path = V4_FAILURE_ROOT / member["path"]
        assert_regular(path, "v4 failure root member")
        if path.stat().st_size != member["bytes"] or sha256_file(path) != member["sha256"]:
            raise CaptureError("v4 failure root member differs")
    recorded_status = read_json(V4_FAILURE_ROOT / "operation_status.json", "v4 operation status")
    if not isinstance(recorded_status, dict) or any(recorded_status.get(key) != value for key, value in status.items()):
        raise CaptureError("v4 failure status differs")


def verify_tracked_helpers(manifest: dict[str, Any]) -> dict[str, Path]:
    governed_files = manifest.get("governed_files")
    if not isinstance(governed_files, list):
        raise CaptureError("source manifest helper identity differs")
    helpers: dict[str, Path] = {}
    for relative in PROCESS_IDENTITY_HELPERS:
        helper = (PROTOCOL_ROOT / relative).resolve(strict=True)
        assert_regular(helper, f"governed helper {relative}")
        assert_no_reparse_or_ads(helper, f"governed helper {relative}")
        matched = [entry for entry in governed_files if isinstance(entry, dict) and entry.get("path") == relative]
        if len(matched) != 1 or matched[0].get("sha256") != sha256_file(helper):
            raise CaptureError("source manifest helper identity differs")
        helpers[relative] = helper
    return helpers


def verify_tracked_authenticode_helper(manifest: dict[str, Any]) -> Path:
    return verify_tracked_helpers(manifest)["tools/read_v5_authenticode_identity.ps1"]


def read_bounded_pipe(source: BinaryIO, label: str) -> BoundedPipe:
    content = bytearray()
    overflow = threading.Event()
    errors: list[BaseException] = []

    def drain() -> None:
        try:
            while chunk := source.read(8192):
                remaining = MAX_HELPER_OUTPUT_BYTES - len(content)
                if len(chunk) > remaining:
                    if remaining:
                        content.extend(chunk[:remaining])
                    overflow.set()
                    continue
                content.extend(chunk)
        except BaseException as error:  # noqa: BLE001
            errors.append(error)
        finally:
            source.close()

    thread = threading.Thread(target=drain, name=f"anachron-v5-{label}-drain", daemon=True)
    thread.start()
    return BoundedPipe(content, overflow, errors, thread)


def run_bounded_subprocess(command: list[str], label: str) -> tuple[int, bytes, bytes]:
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as error:
        raise CaptureError(f"{label} cannot start") from error
    if process.stdout is None or process.stderr is None:
        raise CaptureError(f"{label} pipes are unavailable")
    stdout = read_bounded_pipe(process.stdout, f"{label}-stdout")
    stderr = read_bounded_pipe(process.stderr, f"{label}-stderr")
    deadline = time.monotonic() + TOTAL_TRANSFER_SECONDS
    timed_out = False
    try:
        while process.poll() is None:
            if stdout.overflow.is_set() or stderr.overflow.is_set() or time.monotonic() >= deadline:
                timed_out = time.monotonic() >= deadline
                process.kill()
                break
            time.sleep(0.01)
        process.wait(timeout=CONNECT_HEADER_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        try:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=CONNECT_HEADER_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise CaptureError(f"{label} did not terminate") from error
    finally:
        for pipe in (stdout, stderr):
            pipe.thread.join(CONNECT_HEADER_SECONDS)
    if stdout.thread.is_alive() or stderr.thread.is_alive() or stdout.errors or stderr.errors:
        raise CaptureError(f"{label} output drain failed")
    if timed_out:
        raise CaptureError(f"{label} timed out")
    if stdout.overflow.is_set() or stderr.overflow.is_set():
        raise CaptureError(f"{label} output exceeds {MAX_HELPER_OUTPUT_BYTES} bytes")
    return process.returncode, bytes(stdout.content), bytes(stderr.content)


def authenticode_identity(path: Path, helper: Path) -> tuple[str, str]:
    try:
        returncode, stdout_raw, stderr_raw = run_bounded_subprocess(
            [str(POWERSHELL_EXE), "-NoProfile", "-NonInteractive", "-File", str(helper), "-LiteralPath", str(path)],
            "isolated executable Authenticode validation",
        )
        stdout = stdout_raw.decode("utf-8", errors="strict")
        stderr = stderr_raw.decode("utf-8", errors="strict")
    except (UnicodeDecodeError, CaptureError) as error:
        raise CaptureError("isolated executable Authenticode validation failed") from error
    if (
        returncode != 0
        or stderr
    ):
        raise CaptureError("isolated executable Authenticode validation failed")
    try:
        identity = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise CaptureError("isolated executable Authenticode validation failed") from error
    if not isinstance(identity, dict) or set(identity) != {"subject", "thumbprint"} or not all(isinstance(value, str) and value for value in identity.values()):
        raise CaptureError("isolated executable Authenticode validation failed")
    return identity["subject"], identity["thumbprint"].upper()


def verify_isolated_runtime_and_store(manifest: dict[str, Any]) -> None:
    assert_no_reparse_or_ads(STAGE_ROOT, "isolated stage")
    assert_no_reparse_or_ads(ISOLATED_MODELS, "isolated model store")
    assert_regular(ISOLATED_EXE, "isolated executable")
    if sha256_file(ISOLATED_EXE) != ISOLATED_EXE_SHA256:
        raise CaptureError("isolated executable SHA-256 differs")
    subject, thumbprint = authenticode_identity(ISOLATED_EXE, verify_tracked_authenticode_helper(manifest))
    if subject != EXPECTED_SIGNER_SUBJECT or thumbprint != EXPECTED_SIGNER_THUMBPRINT:
        raise CaptureError("isolated executable signer differs")
    manifests = sorted((ISOLATED_MODELS / "manifests").rglob("*"))
    manifests = [path for path in manifests if path.is_file()]
    blobs = sorted(path for path in (ISOLATED_MODELS / "blobs").iterdir() if path.is_file())
    all_files = [path for path in ISOLATED_MODELS.rglob("*") if path.is_file()]
    if len(manifests) != 2 or len(blobs) != 10 or len(all_files) != 12:
        raise CaptureError("isolated model store topology differs")
    for path, expected_hash in EXPECTED_MANIFESTS.items():
        if path not in manifests or sha256_file(path) != expected_hash:
            raise CaptureError(f"isolated model manifest differs: {path}")
    for blob in blobs:
        if not blob.name.startswith("sha256-") or len(blob.name) != 71:
            raise CaptureError(f"isolated blob name differs: {blob.name}")
        if sha256_file(blob) != blob.name.removeprefix("sha256-"):
            raise CaptureError(f"isolated blob digest differs: {blob.name}")


def is_unavailable_process_field(value: Any) -> bool:
    return value is PROCESS_FIELD_UNAVAILABLE or value is None or value == ""


def expected_process_observation_errors() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = [OSError]
    if psutil is not None:
        for name in ("Error", "AccessDenied", "NoSuchProcess"):
            error = getattr(psutil, name, None)
            if isinstance(error, type) and issubclass(error, BaseException) and error not in errors:
                errors.append(error)
    return tuple(errors)


def process_birth_identity(process: Any, label: str) -> tuple[int, int, str]:
    try:
        pid = process.pid
        parent = process.ppid()
        created = process.create_time()
    except psutil.NoSuchProcess:
        raise
    except expected_process_observation_errors() as error:
        raise OperationalUncertainty(f"cannot establish {label} identity") from error
    if type(pid) is not int or pid < 1 or type(parent) is not int or parent < 0 or type(created) not in (int, float):
        raise OperationalUncertainty(f"{label} identity differs")
    try:
        creation = float(created)
    except OverflowError as error:
        raise OperationalUncertainty(f"{label} identity differs") from error
    if not math.isfinite(creation) or creation <= 0:
        raise OperationalUncertainty(f"{label} identity differs")
    return pid, parent, encode_birth_token_hex(creation)


def cim_process_name(pid: int, helper: Path) -> str:
    if psutil is None:
        raise OperationalUncertainty("psutil is unavailable")
    try:
        before_pid, before_parent, before_creation = process_birth_identity(psutil.Process(pid), "CIM preflight")
    except expected_process_observation_errors() as error:
        raise OperationalUncertainty("CIM process identity differs") from error
    try:
        returncode, stdout_raw, stderr_raw = run_bounded_subprocess(
            [str(POWERSHELL_EXE), "-NoProfile", "-NonInteractive", "-File", str(helper), "-ProcessId", str(pid)],
            "CIM process identity",
        )
        stdout = stdout_raw.decode("utf-8", errors="strict")
        stderr = stderr_raw.decode("utf-8", errors="strict")
        identity = json.loads(stdout)
    except (UnicodeDecodeError, json.JSONDecodeError, CaptureError, OSError) as error:
        raise OperationalUncertainty("CIM process identity differs") from error
    if returncode != 0 or stderr or type(identity) is not dict or set(identity) != {"pid", "ppid", "name"}:
        raise OperationalUncertainty("CIM process identity differs")
    resolved_name = identity["name"]
    if (
        type(identity["pid"]) is not int
        or type(identity["ppid"]) is not int
        or not isinstance(resolved_name, str)
        or not resolved_name
        or resolved_name != resolved_name.strip()
    ):
        raise OperationalUncertainty("CIM process identity differs")
    try:
        after_pid, after_parent, after_creation = process_birth_identity(psutil.Process(pid), "CIM postflight")
    except expected_process_observation_errors() as error:
        raise OperationalUncertainty("CIM process identity differs") from error
    if (
        before_pid != pid
        or after_pid != pid
        or identity["pid"] != pid
        or before_parent != identity["ppid"]
        or after_parent != identity["ppid"]
        or before_creation != after_creation
    ):
        raise OperationalUncertainty("CIM process identity differs")
    return resolved_name


def usable_process_name(process: Any, info: dict[str, Any], cim_helper: Path | None) -> str:
    name = info.get("name", PROCESS_FIELD_UNAVAILABLE)
    if not is_unavailable_process_field(name):
        if not isinstance(name, str) or not name or name != name.strip():
            raise OperationalUncertainty("process name differs")
        return name
    if cim_helper is None:
        raise OperationalUncertainty("process name is unavailable")
    pid = info.get("pid", PROCESS_FIELD_UNAVAILABLE)
    if type(pid) is not int or pid < 1:
        raise OperationalUncertainty("process PID is unavailable")
    return cim_process_name(pid, cim_helper)


def process_image_identity(process: Any, info: dict[str, Any], cim_helper: Path | None) -> tuple[str, Path | None]:
    name = usable_process_name(process, info, cim_helper)
    executable = info.get("exe", PROCESS_FIELD_UNAVAILABLE)
    if is_unavailable_process_field(executable):
        return name, None
    if not isinstance(executable, str) or not executable or executable != executable.strip():
        raise OperationalUncertainty("process executable differs")
    executable_path = Path(executable)
    if not executable_path.name or executable_path.name.casefold() != name.casefold():
        raise OperationalUncertainty("process image identity differs")
    return name, executable_path


def recorded_process_timestamp(value: Any) -> float:
    if type(value) not in (int, float):
        raise OperationalCensusUncertain("process creation time differs")
    try:
        timestamp = float(value)
    except OverflowError as error:
        raise OperationalCensusUncertain("process creation time differs") from error
    if (
        not math.isfinite(timestamp)
        or timestamp < 0
        or (timestamp == 0.0 and math.copysign(1.0, timestamp) < 0)
    ):
        raise OperationalCensusUncertain("process creation time differs")
    return timestamp


def validate_birth_token_hex(value: Any) -> str:
    if (
        type(value) is not str
        or len(value) != 16
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise OperationalCensusUncertain("process birth token differs")
    decoded = struct.unpack(">d", bytes.fromhex(value))[0]
    if not math.isfinite(decoded) or decoded <= 0 or struct.pack(">d", decoded).hex() != value:
        raise OperationalCensusUncertain("process birth token differs")
    return value


def encode_birth_token_hex(value: Any) -> str:
    timestamp = recorded_process_timestamp(value)
    if timestamp <= 0:
        raise OperationalCensusUncertain("process birth token differs")
    return validate_birth_token_hex(struct.pack(">d", timestamp).hex())


def is_birth_timestamp(value: float | None) -> bool:
    return value is not None and value > 0


def normalize_process_observation(
    process: Any,
    info: dict[str, Any],
    cim_helper: Path | None,
) -> ProcessObservation:
    pid = info.get("pid", PROCESS_FIELD_UNAVAILABLE)
    if type(pid) is not int or pid < 1:
        raise OperationalUncertainty("process PID differs")
    name, executable = process_image_identity(process, info, cim_helper)

    command_line_value = info.get("cmdline", PROCESS_FIELD_UNAVAILABLE)
    if command_line_value is PROCESS_FIELD_UNAVAILABLE or command_line_value is None:
        command_line = None
    elif not isinstance(command_line_value, (list, tuple)) or not all(
        isinstance(part, str) and "\x00" not in part for part in command_line_value
    ):
        raise OperationalUncertainty("process command line differs")
    else:
        command_line = tuple(command_line_value)

    parent_value = info.get("ppid", PROCESS_FIELD_UNAVAILABLE)
    if parent_value is PROCESS_FIELD_UNAVAILABLE or parent_value is None:
        parent = None
    elif type(parent_value) is not int or parent_value < 0:
        raise OperationalUncertainty("process parent PID differs")
    else:
        parent = parent_value

    created_value = info.get("create_time", PROCESS_FIELD_UNAVAILABLE)
    if created_value is PROCESS_FIELD_UNAVAILABLE or created_value is None:
        created = None
    else:
        created = recorded_process_timestamp(created_value)
    birth_token = encode_birth_token_hex(created) if is_birth_timestamp(created) else None

    folded_name = name.casefold()
    runner_hosts = {
        "py.exe",
        "powershell",
        "powershell.exe",
        "powershell_ise.exe",
        "pwsh",
        "pwsh-preview.exe",
        "pwsh.exe",
        "python",
        "python.exe",
        "pythonw.exe",
    }
    if folded_name in {"ollama.exe", "ollama app.exe"}:
        if executable is None or parent is None or birth_token is None:
            raise OperationalUncertainty("cannot establish complete Ollama process census")
        record = ProcessRecord(pid, parent, name, executable, birth_token)
        return ProcessObservation(pid, parent, name, executable, created, birth_token, command_line, ProcessDisposition.OLLAMA, record)
    if folded_name == "llama-server.exe":
        if executable is None:
            raise OperationalUncertainty("llama-server executable is unavailable")
        return ProcessObservation(pid, parent, name, executable, created, birth_token, command_line, ProcessDisposition.BLOCKER, None)
    if folded_name in runner_hosts:
        if executable is None or not command_line or not any(part.strip() for part in command_line):
            raise OperationalUncertainty("cannot establish complete runner census")
        disposition = ProcessDisposition.BLOCKER if has_runner_token(" ".join(command_line)) else ProcessDisposition.HOST_CLEAR
        return ProcessObservation(pid, parent, name, executable, created, birth_token, command_line, disposition, None)
    disposition = (
        ProcessDisposition.BLOCKER
        if command_line is not None and has_runner_token(" ".join(command_line))
        else ProcessDisposition.UNRELATED
    )
    return ProcessObservation(pid, parent, name, executable, created, birth_token, command_line, disposition, None)


def is_idle_pseudoentry(info: dict[str, Any]) -> bool:
    expected_fields = {"pid", "ppid", "name", "exe", "create_time", "cmdline"}
    created = info.get("create_time", PROCESS_FIELD_UNAVAILABLE)
    command_line = info.get("cmdline", PROCESS_FIELD_UNAVAILABLE)
    return (
        sys.platform == "win32"
        and set(info) == expected_fields
        and type(info["pid"]) is int
        and info["pid"] == 0
        and type(info["ppid"]) is int
        and info["ppid"] == 0
        and info["name"] == "System Idle Process"
        and info["exe"] is PROCESS_FIELD_UNAVAILABLE
        and type(created) is float
        and created == 0.0
        and math.copysign(1.0, created) == 1.0
        and type(command_line) is list
        and not command_line
    )


def process_census(cim_helper: Path | None = None) -> tuple[ProcessObservation, ...]:
    if psutil is None:
        raise OperationalUncertainty("psutil is unavailable")
    try:
        processes = psutil.process_iter(
            ("pid", "ppid", "name", "exe", "create_time", "cmdline"),
            ad_value=PROCESS_FIELD_UNAVAILABLE,
        )
        ordinary_rows: list[tuple[Any, dict[str, Any]]] = []
        idle_count = 0
        for process in processes:
            info = process.info
            if not isinstance(info, dict):
                raise OperationalCensusUncertain("process information differs")
            if is_idle_pseudoentry(info):
                idle_count += 1
                continue
            pid = info.get("pid", PROCESS_FIELD_UNAVAILABLE)
            name = info.get("name", PROCESS_FIELD_UNAVAILABLE)
            if pid == 0 or name == "System Idle Process":
                raise OperationalCensusUncertain("Windows idle pseudoentry differs")
            ordinary_rows.append((process, info))
        expected_idle_count = 1 if sys.platform == "win32" else 0
        if idle_count != expected_idle_count:
            raise OperationalCensusUncertain("Windows idle pseudoentry count differs")
        observations = [
            normalize_process_observation(process, info, cim_helper)
            for process, info in ordinary_rows
        ]
        return tuple(observations)
    except OperationalUncertainty:
        raise
    except expected_process_observation_errors() as error:
        raise OperationalUncertainty("cannot establish complete process census") from error


def process_records(
    cim_helper: Path | None = None,
    census: tuple[ProcessObservation, ...] | None = None,
) -> tuple[ProcessRecord, ...]:
    observations = process_census(cim_helper) if census is None else census
    return tuple(
        observation.record
        for observation in observations
        if observation.disposition is ProcessDisposition.OLLAMA and observation.record is not None
    )


def process_record(process: Any, label: str, cim_helper: Path | None = None) -> ProcessRecord:
    try:
        pid = process.pid
        name = process.name()
        if is_unavailable_process_field(name):
            name = usable_process_name(process, {"pid": pid, "name": name}, cim_helper)
            process = psutil.Process(pid)
        if not isinstance(name, str) or not name or name != name.strip():
            raise OperationalUncertainty(f"cannot establish {label} identity before stop")
        current_pid, parent, creation = process_birth_identity(process, f"{label} pre-stop")
        executable = process.exe()
        if not isinstance(executable, str) or not executable or executable != executable.strip():
            raise OperationalUncertainty(f"cannot establish {label} identity before stop")
        executable_path = Path(executable)
        if not executable_path.name or executable_path.name.casefold() != name.casefold():
            raise OperationalUncertainty(f"cannot establish {label} identity before stop")
        return ProcessRecord(
            current_pid,
            parent,
            name,
            executable_path,
            creation,
        )
    except psutil.NoSuchProcess as error:
        raise OperationalUncertainty(f"cannot establish {label} identity before stop") from error
    except (psutil.AccessDenied, OSError) as error:
        raise OperationalUncertainty(f"cannot establish {label} identity before stop") from error


def listener_pids(port: int) -> tuple[int, ...]:
    if psutil is None:
        raise OperationalUncertainty("psutil is unavailable")
    try:
        connections = psutil.net_connections(kind="tcp")
        pids: list[int] = []
        for connection in connections:
            status = connection.status
            if not isinstance(status, str):
                raise OperationalUncertainty("listener status differs")
            if status != psutil.CONN_LISTEN:
                continue
            address = connection.laddr
            if not address:
                raise OperationalUncertainty("listener address is unavailable")
            try:
                address_ip = address.ip
                address_port = address.port
            except AttributeError:
                if not isinstance(address, (list, tuple)) or len(address) != 2:
                    raise OperationalUncertainty("listener address differs") from None
                address_ip, address_port = address
            if not isinstance(address_ip, str) or type(address_port) is not int:
                raise OperationalUncertainty("listener address differs")
            if address_port != port:
                continue
            if address_ip != HOST:
                raise OperationalUncertainty(f"listener is not loopback: {address_ip}")
            if type(connection.pid) is not int or connection.pid < 1:
                raise OperationalUncertainty("listener PID is unavailable")
            pids.append(connection.pid)
        return tuple(pids)
    except OperationalUncertainty:
        raise
    except (AttributeError, TypeError, ValueError, *expected_process_observation_errors()) as error:
        raise OperationalUncertainty("cannot establish listener census") from error


def assert_no_runner_or_llama_server(
    cim_helper: Path | None = None,
    census: tuple[ProcessObservation, ...] | None = None,
) -> None:
    observations = process_census(cim_helper) if census is None else census
    if any(observation.disposition is ProcessDisposition.BLOCKER for observation in observations):
        raise CaptureError("runner or llama-server process is present")


def has_runner_token(command_line: str) -> bool:
    folded = command_line.casefold()
    return any(token in folded for token in RUNNER_TOKENS)


def resolve_observed_executable(path: Path, label: str) -> Path:
    try:
        return path.resolve(strict=True)
    except OSError as error:
        raise OperationalUncertainty(f"{label} executable cannot be resolved") from error


def observe_normal_topology(cim_helper: Path | None = None) -> NormalTopologyObservation:
    try:
        census = process_census(cim_helper)
        assert_no_runner_or_llama_server(cim_helper, census)
        records = process_records(cim_helper, census)
        listeners = listener_pids(PORT)
        if not records and not listeners:
            return NormalTopologyObservation(NormalTopologyState.EMPTY, None, None, "no Ollama processes or listener")
        if len(records) != 2 or len(listeners) != 1:
            return NormalTopologyObservation(NormalTopologyState.PARTIAL, None, None, "normal Ollama topology differs")
        server = next((record for record in records if record.pid == listeners[0]), None)
        app = next((record for record in records if record.pid == (server.ppid if server else -1)), None)
        if server is None or app is None or server.name.casefold() != "ollama.exe" or app.name.casefold() != "ollama app.exe":
            return NormalTopologyObservation(NormalTopologyState.PARTIAL, None, None, "normal Ollama process chain differs")
        if resolve_observed_executable(server.exe, "normal server") != resolve_observed_executable(NORMAL_SERVER, "governed normal server") or resolve_observed_executable(app.exe, "normal app") != resolve_observed_executable(NORMAL_APP, "governed normal app"):
            return NormalTopologyObservation(NormalTopologyState.PARTIAL, None, None, "normal Ollama process chain differs")
    except (CaptureError, OSError) as error:
        return NormalTopologyObservation(NormalTopologyState.UNKNOWN, None, None, str(error))
    return NormalTopologyObservation(NormalTopologyState.NORMAL, app, server, "normal Ollama topology observed")


def assert_normal_state(cim_helper: Path | None = None) -> tuple[ProcessRecord, ProcessRecord]:
    observation = observe_normal_topology(cim_helper)
    if observation.state is not NormalTopologyState.NORMAL or observation.app is None or observation.server is None:
        raise OperationalUncertainty(f"normal Ollama topology differs: {observation.diagnostic}")
    return observation.app, observation.server


def assert_isolated_state(lifecycle: IsolatedLifecycle, cim_helper: Path | None = None) -> None:
    if lifecycle.root_identity is None or lifecycle.job_handle is None or not lifecycle.job_assign_succeeded or not lifecycle.root_resume_succeeded:
        raise CaptureError("isolated Job Object state is incomplete")
    pid = lifecycle.root_identity.pid
    census = process_census(cim_helper)
    assert_no_runner_or_llama_server(cim_helper, census)
    records = process_records(cim_helper, census)
    listeners = listener_pids(PORT)
    if len(records) != 1 or len(listeners) != 1 or listeners[0] != pid:
        raise CaptureError("isolated Ollama topology differs")
    record = records[0]
    try:
        if record.pid != pid or record.name.casefold() != "ollama.exe" or resolve_observed_executable(record.exe, "isolated server") != resolve_observed_executable(ISOLATED_EXE, "governed isolated server"):
            raise CaptureError("isolated executable identity differs")
        if psutil.Process(pid).children(recursive=True):
            raise CaptureError("isolated server has child processes")
        if isolated_job_active_members(lifecycle) != 1:
            raise CaptureError("isolated Job Object active member count differs")
    except expected_process_observation_errors() as error:
        raise OperationalUncertainty("isolated process observation failed") from error


def force_stop_exact(
    expected: ProcessRecord,
    expected_parent: int | None,
    label: str,
    retained_child: subprocess.Popen[bytes] | None = None,
    on_signal_issued: Callable[[], None] | None = None,
    on_preverified: Callable[[], None] | None = None,
    cim_helper: Path | None = None,
) -> bool:
    if psutil is None:
        raise CaptureError("psutil is unavailable")
    if type(expected.pid) is not int or expected.pid < 1:
        raise OperationalCensusUncertain(f"{label} expected identity is not actionable")
    try:
        validate_birth_token_hex(expected.birth_token_hex)
    except (AttributeError, OperationalCensusUncertain) as error:
        raise OperationalCensusUncertain(f"{label} expected identity is not actionable") from error
    try:
        process = psutil.Process(expected.pid)
        initial_birth = process_birth_identity(process, f"{label} first pre-stop")
    except psutil.NoSuchProcess:
        return False
    try:
        if initial_birth[2] != expected.birth_token_hex:
            raise CaptureError(f"{label} identity differs before stop")
        if on_preverified is not None:
            on_preverified()
        current = process_record(process, label, cim_helper)
        if current.birth_token_hex != expected.birth_token_hex:
            raise CaptureError(f"{label} identity differs before stop")
        final_birth = process_birth_identity(psutil.Process(expected.pid), f"{label} final pre-stop")
        if final_birth[2] != expected.birth_token_hex:
            raise CaptureError(f"{label} identity differs before stop")
        if (
            current.pid != expected.pid
            or current.name.casefold() != expected.name.casefold()
            or resolve_observed_executable(current.exe, label) != resolve_observed_executable(expected.exe, f"expected {label}")
            or (expected_parent is not None and current.ppid != expected_parent)
        ):
            raise CaptureError(f"{label} identity differs before stop")
        if retained_child is not None and retained_child.pid != expected.pid:
            raise CaptureError(f"{label} retained child identity differs before stop")
        if expected_parent is not None and current.ppid != expected_parent:
            raise CaptureError(f"{label} parent identity differs before stop")
        if retained_child is not None:
            if on_signal_issued is not None:
                on_signal_issued()
            retained_child.kill()
            retained_child.wait(timeout=CONNECT_HEADER_SECONDS)
        else:
            if on_signal_issued is not None:
                on_signal_issued()
            process.kill()
            process.wait(timeout=CONNECT_HEADER_SECONDS)
        return True
    except psutil.NoSuchProcess as error:
        raise CaptureError(f"{label} identity differs before stop") from error
    except psutil.TimeoutExpired as error:
        raise CaptureError(f"{label} did not stop") from error


def wait_for_listener_free() -> None:
    deadline = time.monotonic() + TOTAL_TRANSFER_SECONDS
    while time.monotonic() < deadline:
        if not listener_pids(PORT):
            return
        time.sleep(0.25)
    raise CaptureError("port 11434 did not become free")


def root_bound_startup_descendants(
    lifecycle: IsolatedLifecycle,
    census: tuple[ProcessObservation, ...],
) -> tuple[ProcessObservation, ...]:
    """Return complete descendants of the live bound root for startup admission only."""
    if lifecycle.root_identity is None:
        raise OperationalUncertainty("isolated child identity is unavailable")
    by_pid = {observation.pid: observation for observation in census}
    root = by_pid.get(lifecycle.root_identity.pid)
    if root is None:
        raise CaptureError("isolated server identity differs during startup")
    if root.record is None or root.record != lifecycle.root_identity:
        raise CaptureError("isolated server identity differs during startup")

    def is_descendant(observation: ProcessObservation) -> bool:
        parent = observation.ppid
        visited = {observation.pid}
        while parent != lifecycle.root_identity.pid:
            if parent is None or parent in visited:
                return False
            visited.add(parent)
            ancestor = by_pid.get(parent)
            if ancestor is None:
                return False
            parent = ancestor.ppid
        return True

    descendants: list[ProcessObservation] = []
    for observation in census:
        if observation.pid == lifecycle.root_identity.pid:
            continue
        if not is_descendant(observation):
            continue
        if observation.exe is None or observation.ppid is None or observation.birth_token_hex is None:
            raise OperationalUncertainty("isolated task descendant identity is incomplete")
        descendants.append(observation)
    return tuple(descendants)


def isolated_startup_descendants(
    lifecycle: IsolatedLifecycle,
    census: tuple[ProcessObservation, ...],
) -> tuple[ProcessRecord, ...]:
    """Return only exact production-trace helpers during startup admission."""
    descendants = root_bound_startup_descendants(lifecycle, census)
    by_pid = {observation.pid: observation for observation in census}
    isolated_executable = resolve_observed_executable(ISOLATED_EXE, "governed isolated server")
    lib_root = (isolated_executable.parent / "lib" / "ollama").resolve(strict=True)
    llama_executable = (lib_root / "llama-server.exe").resolve(strict=True)
    conhost_executable = (Path(os.environ["SystemRoot"]) / "System32" / "conhost.exe").resolve(strict=True)

    def exact_command(
        observation: ProcessObservation,
        executable: Path,
        arguments: tuple[str, ...],
        *,
        nt_namespace_prefix: bool = False,
    ) -> bool:
        if observation.cmdline is None or len(observation.cmdline) != len(arguments) + 1:
            return False
        executable_token = observation.cmdline[0]
        if nt_namespace_prefix:
            if not executable_token.startswith("\\??\\"):
                return False
            executable_token = executable_token.removeprefix("\\??\\")
        try:
            resolved_executable = Path(executable_token).resolve(strict=True)
        except OSError:
            return False
        return (
            resolved_executable == executable
            and observation.cmdline[1:] == arguments
        )

    def admitted_helper(observation: ProcessObservation) -> bool:
        if observation.exe is None or observation.cmdline is None:
            return False
        executable = resolve_observed_executable(observation.exe, "isolated startup descendant")
        if observation.name.casefold() == "ollama.exe" and executable == isolated_executable:
            return any(
                exact_command(
                    observation,
                    isolated_executable,
                    ("gpu-discover", "--lib-dir", str(lib_root), "--lib-dir", str(lib_root / variant)),
                )
                for variant in ("cuda_v12", "cuda_v13", "vulkan")
            )
        if observation.name.casefold() != "llama-server.exe" or executable != llama_executable:
            return False
        if exact_command(observation, llama_executable, ("--list-devices", "--offline", "--verbose")):
            return True
        command = observation.cmdline
        if len(command) != 8 or Path(command[0]).resolve(strict=True) != llama_executable:
            return False
        port = command[2]
        return (
            command[1] == "--port"
            and port.isdecimal()
            and 49152 <= int(port) <= 65535
            and command[3:] == ("--host", HOST, "--no-webui", "--offline", "--verbose")
        )

    def admitted_conhost(observation: ProcessObservation) -> bool:
        if observation.name.casefold() != "conhost.exe" or observation.exe is None:
            return False
        if resolve_observed_executable(observation.exe, "isolated conhost descendant") != conhost_executable:
            return False
        parent = by_pid.get(observation.ppid)
        return (
            parent is not None
            and admitted_helper(parent)
            and exact_command(observation, conhost_executable, ("0x4",), nt_namespace_prefix=True)
        )

    for observation in census:
        if observation.pid == lifecycle.root_identity.pid:
            continue
        descendant = any(candidate.pid == observation.pid for candidate in descendants)
        if observation.disposition in (ProcessDisposition.BLOCKER, ProcessDisposition.OLLAMA) and not descendant:
            raise CaptureError("foreign runner or llama-server process is present")
        if not descendant:
            continue
        if not (admitted_helper(observation) or admitted_conhost(observation)):
            raise CaptureError("isolated startup descendant differs")
    return tuple(
        ProcessRecord(
            observation.pid,
            observation.ppid,
            observation.name,
            observation.exe,
            observation.birth_token_hex,
        )
        for observation in descendants
        if observation.exe is not None and observation.ppid is not None and observation.birth_token_hex is not None
    )


def wait_for_isolated_quiescence(
    lifecycle: IsolatedLifecycle,
    stdout_overflow: threading.Event,
    stderr_overflow: threading.Event,
    cim_helper: Path | None = None,
) -> None:
    deadline = time.monotonic() + ISOLATED_STARTUP_SECONDS
    quiescent_since: float | None = None
    while time.monotonic() < deadline:
        if stdout_overflow.is_set() or stderr_overflow.is_set():
            raise CaptureError("isolated stdout or stderr exceeded the log cap")
        if lifecycle.process is None:
            raise CaptureError("isolated root is unavailable during startup quiescence")
        if lifecycle.process.poll() is not None or not psutil.pid_exists(lifecycle.process.pid):
            raise CaptureError("isolated server exited before startup quiescence")
        if listener_pids(PORT) != (lifecycle.process.pid,):
            quiescent_since = None
            time.sleep(ISOLATED_CENSUS_SECONDS)
            continue
        descendants = isolated_startup_descendants(lifecycle, process_census(cim_helper))
        if descendants:
            quiescent_since = None
        elif quiescent_since is None:
            quiescent_since = time.monotonic()
        elif time.monotonic() - quiescent_since >= ISOLATED_QUIESCENCE_SECONDS:
            lifecycle.startup_observed = True
            lifecycle.quiescence_succeeded = True
            return
        time.sleep(ISOLATED_CENSUS_SECONDS)
    raise CaptureError("isolated server did not reach continuous child-free quiescence")


def wait_for_isolated_listener(
    pid: int,
    stdout_overflow: threading.Event,
    stderr_overflow: threading.Event,
    lifecycle: IsolatedLifecycle | None = None,
    cim_helper: Path | None = None,
) -> None:
    deadline = time.monotonic() + ISOLATED_STARTUP_SECONDS
    while time.monotonic() < deadline:
        if stdout_overflow.is_set() or stderr_overflow.is_set():
            raise CaptureError("isolated stdout or stderr exceeded the log cap")
        if not psutil.pid_exists(pid):
            raise CaptureError("isolated server exited before listener opened")
        if listener_pids(PORT) == (pid,):
            if lifecycle is not None:
                lifecycle.startup_observer_started = True
                wait_for_isolated_quiescence(lifecycle, stdout_overflow, stderr_overflow, cim_helper)
            return
        time.sleep(ISOLATED_CENSUS_SECONDS)
    raise CaptureError("isolated server listener did not open")


def wait_for_prelaunch_baseline(cim_helper: Path | None = None) -> None:
    """Require the suspended-launch failure path to return to an empty isolated baseline."""
    deadline = time.monotonic() + ISOLATED_TEARDOWN_SECONDS
    absent_since: float | None = None
    isolated_runtime_root = resolve_observed_executable(ISOLATED_EXE, "governed isolated server").parent
    while time.monotonic() < deadline:
        census = process_census(cim_helper)
        assert_no_runner_or_llama_server(cim_helper, census)
        staged_runtime_present = any(
            observation.exe is not None
            and resolve_observed_executable(observation.exe, "isolated prelaunch process").is_relative_to(isolated_runtime_root)
            for observation in census
        )
        if staged_runtime_present or listener_pids(PORT):
            absent_since = None
        elif absent_since is None:
            absent_since = time.monotonic()
        elif time.monotonic() - absent_since >= ISOLATED_QUIESCENCE_SECONDS:
            return
        time.sleep(ISOLATED_CENSUS_SECONDS)
    raise CaptureError("isolated prelaunch baseline did not clear")


def wait_for_isolated_job_quiescence(lifecycle: IsolatedLifecycle) -> None:
    """Require the assigned Job Object to report zero active members continuously."""
    if lifecycle.job_handle is None or not lifecycle.job_assign_succeeded or not lifecycle.job_terminate_succeeded:
        raise CaptureError("isolated Job Object teardown state is incomplete")
    deadline = time.monotonic() + ISOLATED_TEARDOWN_SECONDS
    absent_since: float | None = None
    while time.monotonic() < deadline:
        if isolated_job_active_members(lifecycle) != 0:
            absent_since = None
        elif absent_since is None:
            absent_since = time.monotonic()
        elif time.monotonic() - absent_since >= ISOLATED_QUIESCENCE_SECONDS:
            lifecycle.job_zero_window_confirmed = True
            return
        time.sleep(ISOLATED_CENSUS_SECONDS)
    raise CaptureError("isolated Job Object did not reach zero active members")


def assert_exact_tags(tags: Any) -> None:
    models = tags.get("models") if isinstance(tags, dict) else None
    if not isinstance(models, list) or len(models) != len(EXPECTED_MODELS):
        raise CaptureError("isolated tag topology differs")
    actual = {(str(model.get("name")), str(model.get("digest", "")).removeprefix("sha256:")) for model in models if isinstance(model, dict)}
    if actual != set(EXPECTED_MODELS):
        raise CaptureError("isolated tag identity differs")


def assert_runtime_identity(path: Path) -> None:
    if sha256_file(path) != EXPECTED_RUNTIME_IDENTITY_SHA256:
        raise CaptureError("runtime identity SHA-256 differs")
    expected = {"version": "0.33.2", "models": [{"name": name, "digest": digest} for name, digest in EXPECTED_MODELS]}
    if read_json(path, "runtime identity") != expected:
        raise CaptureError("runtime identity structure differs")


def start_bounded_drain(source: BinaryIO, destination: Path, label: str) -> BoundedDrain:
    overflow = threading.Event()
    errors: list[BaseException] = []

    def drain() -> None:
        written = 0
        try:
            with destination.open("xb") as output:
                while True:
                    chunk = source.read(8192)
                    if not chunk:
                        break
                    remaining = MAX_RESPONSE_BYTES - written
                    if len(chunk) > remaining:
                        if remaining:
                            output.write(chunk[:remaining])
                            written += remaining
                        overflow.set()
                        continue
                    output.write(chunk)
                    written += len(chunk)
                output.flush()
                os.fsync(output.fileno())
        except BaseException as error:  # noqa: BLE001
            errors.append(error)
        finally:
            source.close()

    thread = threading.Thread(target=drain, name=f"anachron-v5-{label}-drain", daemon=True)
    thread.start()
    return BoundedDrain(destination, overflow, errors, thread)


def join_bounded_drain(drain: BoundedDrain, label: str) -> None:
    drain.thread.join(TOTAL_TRANSFER_SECONDS)
    if drain.thread.is_alive():
        raise CaptureError(f"{label} log drain did not finish")
    if drain.errors:
        raise CaptureError(f"{label} log drain failed: {drain.errors[0]}")
    if drain.destination.exists() and drain.destination.stat().st_size > MAX_RESPONSE_BYTES:
        raise CaptureError(f"{label} log exceeds {MAX_RESPONSE_BYTES} bytes")


def assert_no_log_overflow(lifecycle: IsolatedLifecycle) -> None:
    if lifecycle.stdout_drain is None or lifecycle.stderr_drain is None:
        raise CaptureError("isolated log drains are incomplete")
    if isolated_log_overflow(lifecycle):
        raise CaptureError("isolated stdout or stderr exceeded the log cap")


def isolated_log_overflow(lifecycle: IsolatedLifecycle | None) -> bool:
    return lifecycle is not None and (
        (lifecycle.stdout_drain is not None and lifecycle.stdout_drain.overflow.is_set())
        or (lifecycle.stderr_drain is not None and lifecycle.stderr_drain.overflow.is_set())
    )


def kernel32() -> Any:
    if os.name != "nt":
        raise CaptureError("isolated Job Object requires Windows")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    api.CreateJobObjectW.restype = wintypes.HANDLE
    api.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    api.SetInformationJobObject.restype = wintypes.BOOL
    api.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    api.AssignProcessToJobObject.restype = wintypes.BOOL
    api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    api.OpenProcess.restype = wintypes.HANDLE
    api.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    api.OpenThread.restype = wintypes.HANDLE
    api.GetProcessIdOfThread.argtypes = (wintypes.HANDLE,)
    api.GetProcessIdOfThread.restype = wintypes.DWORD
    api.ResumeThread.argtypes = (wintypes.HANDLE,)
    api.ResumeThread.restype = wintypes.DWORD
    api.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    api.TerminateJobObject.restype = wintypes.BOOL
    api.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    api.QueryInformationJobObject.restype = wintypes.BOOL
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.restype = wintypes.BOOL
    return api


def win32_error(label: str) -> CaptureError:
    return CaptureError(f"{label} failed: Win32 error {ctypes.get_last_error()}")


def create_lifecycle_job(lifecycle: IsolatedLifecycle) -> None:
    lifecycle.job_create_attempted = True
    api = kernel32()
    handle = api.CreateJobObjectW(None, None)
    if not handle:
        raise win32_error("CreateJobObjectW")
    lifecycle.job_handle = int(handle)
    limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not api.SetInformationJobObject(
        lifecycle.job_handle,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    ):
        raise win32_error("SetInformationJobObject")
    lifecycle.job_create_succeeded = True
    lifecycle.advance(IsolatedLifecyclePhase.JOB_CREATED)


def close_lifecycle_job(lifecycle: IsolatedLifecycle) -> None:
    lifecycle.job_close_attempted = True
    if lifecycle.job_handle is None or lifecycle.job_handle < 1:
        raise CaptureError("isolated Job Object handle differs")
    api = kernel32()
    if not api.CloseHandle(lifecycle.job_handle):
        raise win32_error("CloseHandle(Job Object)")
    lifecycle.job_close_succeeded = True
    lifecycle.job_handle = None


def assign_suspended_root_to_job(lifecycle: IsolatedLifecycle) -> None:
    lifecycle.job_assign_attempted = True
    if lifecycle.job_handle is None or lifecycle.process is None:
        raise CaptureError("isolated Job Object is unavailable")
    if type(lifecycle.process.pid) is not int or lifecycle.process.pid < 1:
        raise CaptureError("isolated suspended root PID differs")
    api = kernel32()
    process_handle = api.OpenProcess(PROCESS_JOB_ASSIGN_ACCESS, False, lifecycle.process.pid)
    if not process_handle:
        raise win32_error("OpenProcess(isolated root)")
    lifecycle.assignment_process_handle = int(process_handle)
    try:
        if not api.AssignProcessToJobObject(lifecycle.job_handle, lifecycle.assignment_process_handle):
            raise win32_error("AssignProcessToJobObject")
        lifecycle.job_assign_succeeded = True
        lifecycle.advance(IsolatedLifecyclePhase.ROOT_ASSIGNED)
    finally:
        if not api.CloseHandle(lifecycle.assignment_process_handle):
            raise win32_error("CloseHandle(isolated root)")
        lifecycle.assignment_process_handle = None


def resume_suspended_root(lifecycle: IsolatedLifecycle) -> None:
    lifecycle.root_resume_attempted = True
    if lifecycle.root_identity is None or lifecycle.process is None or psutil is None:
        raise CaptureError("isolated root identity is unavailable before resume")
    threads = psutil.Process(lifecycle.root_identity.pid).threads()
    if len(threads) != 1:
        raise CaptureError("isolated suspended root thread count differs")
    thread_id = threads[0].id
    api = kernel32()
    thread_handle = api.OpenThread(THREAD_SUSPEND_RESUME, False, thread_id)
    if not thread_handle:
        raise win32_error("OpenThread")
    lifecycle.root_thread_handle = int(thread_handle)
    try:
        if api.GetProcessIdOfThread(lifecycle.root_thread_handle) != lifecycle.root_identity.pid:
            raise CaptureError("isolated suspended root thread owner differs")
        prior_suspend_count = api.ResumeThread(lifecycle.root_thread_handle)
        if prior_suspend_count == INVALID_DWORD:
            raise win32_error("ResumeThread")
        if prior_suspend_count != 1:
            raise CaptureError("isolated suspended root prior suspend count differs")
        lifecycle.root_resume_succeeded = True
        lifecycle.advance(IsolatedLifecyclePhase.ROOT_RESUMED)
    finally:
        lifecycle.root_thread_handle_close_attempted = True
        if not api.CloseHandle(lifecycle.root_thread_handle):
            raise win32_error("CloseHandle(root thread)")
        lifecycle.root_thread_handle_close_succeeded = True
        lifecycle.root_thread_handle = None


def terminate_lifecycle_job(lifecycle: IsolatedLifecycle) -> None:
    lifecycle.job_terminate_attempted = True
    if lifecycle.job_handle is None or not lifecycle.job_assign_succeeded:
        raise CaptureError("isolated Job Object was not assigned")
    api = kernel32()
    if not api.TerminateJobObject(lifecycle.job_handle, JOB_TERMINATION_EXIT_CODE):
        raise win32_error("TerminateJobObject")
    lifecycle.job_terminate_succeeded = True


def isolated_job_active_members(lifecycle: IsolatedLifecycle) -> int:
    if lifecycle.job_handle is None:
        raise CaptureError("isolated Job Object handle is unavailable")
    api = kernel32()
    accounting = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    returned = wintypes.DWORD()
    if not api.QueryInformationJobObject(
        lifecycle.job_handle,
        JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS,
        ctypes.byref(accounting),
        ctypes.sizeof(accounting),
        ctypes.byref(returned),
    ):
        raise win32_error("QueryInformationJobObject")
    if returned.value != ctypes.sizeof(accounting):
        raise CaptureError("QueryInformationJobObject byte count differs")
    return int(accounting.ActiveProcesses)


def launch_suspended_isolated_root(lifecycle: IsolatedLifecycle) -> None:
    lifecycle.root_launch_attempted = True
    environment = os.environ.copy()
    environment.update({"OLLAMA_HOST": f"{HOST}:{PORT}", "OLLAMA_MODELS": str(ISOLATED_MODELS), "OLLAMA_NO_CLOUD": "1", "OLLAMA_NOPRUNE": "1"})
    process = subprocess.Popen(
        [str(ISOLATED_EXE), "serve"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        creationflags=DETACHED_PROCESS | CREATE_SUSPENDED,
    )
    lifecycle.process = process
    lifecycle.root_launch_succeeded = True
    lifecycle.advance(IsolatedLifecyclePhase.ROOT_LAUNCHED)


def bind_suspended_root_identity(lifecycle: IsolatedLifecycle, cim_helper: Path | None = None) -> None:
    lifecycle.root_bind_attempted = True
    if lifecycle.process is None:
        raise CaptureError("isolated root is unavailable before identity binding")
    if psutil is None:
        raise CaptureError("psutil is unavailable")
    try:
        process = psutil.Process(lifecycle.process.pid)
    except psutil.NoSuchProcess:
        raise
    except (psutil.AccessDenied, OSError) as error:
        raise CaptureError("cannot establish isolated child identity before stop") from error
    lifecycle.root_identity = process_record(process, "isolated child", cim_helper)
    lifecycle.root_bind_succeeded = True
    lifecycle.advance(IsolatedLifecyclePhase.ROOT_BOUND)


def start_lifecycle_drains(lifecycle: IsolatedLifecycle, stdout_path: Path, stderr_path: Path) -> None:
    lifecycle.drains_started_attempted = True
    if lifecycle.process is None or lifecycle.process.stdout is None or lifecycle.process.stderr is None:
        raise CaptureError("isolated process pipes are unavailable")
    lifecycle.stdout_drain = start_bounded_drain(lifecycle.process.stdout, stdout_path, "isolated-stdout")
    lifecycle.stderr_drain = start_bounded_drain(lifecycle.process.stderr, stderr_path, "isolated-stderr")
    lifecycle.drains_started_succeeded = True
    lifecycle.advance(IsolatedLifecyclePhase.DRAINS_STARTED)


def lifecycle_failure(lifecycle: IsolatedLifecycle, operation: str, error: BaseException) -> FailureReceipt:
    return FailureReceipt(
        lifecycle.phase.value,
        operation,
        str(error),
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


def append_cleanup_failure(lifecycle: IsolatedLifecycle, operation: str, error: BaseException) -> None:
    lifecycle.cleanup_errors.append(lifecycle_failure(lifecycle, operation, error))


def close_lifecycle_pipes(lifecycle: IsolatedLifecycle) -> None:
    if lifecycle.process is None:
        lifecycle.pipes_closed = True
        return
    for pipe, label in ((lifecycle.process.stdout, "isolated stdout pipe"), (lifecycle.process.stderr, "isolated stderr pipe")):
        try:
            if pipe is not None:
                pipe.close()
        except BaseException as error:  # noqa: BLE001
            append_cleanup_failure(lifecycle, label, error)
    lifecycle.pipes_closed = not any(receipt.operation.endswith("pipe") for receipt in lifecycle.cleanup_errors)


def join_lifecycle_drains(lifecycle: IsolatedLifecycle) -> None:
    for drain, label in ((lifecycle.stdout_drain, "isolated stdout"), (lifecycle.stderr_drain, "isolated stderr")):
        try:
            if drain is not None:
                join_bounded_drain(drain, label)
        except BaseException as error:  # noqa: BLE001
            append_cleanup_failure(lifecycle, f"{label} drain", error)
    lifecycle.drains_joined = not any(receipt.operation.endswith("drain") for receipt in lifecycle.cleanup_errors)


def stop_isolated_lifecycle(lifecycle: IsolatedLifecycle, cim_helper: Path | None = None) -> None:
    """Perform the sole lifecycle teardown from durable acquisition facts."""
    if lifecycle.stop_attempted:
        raise CaptureError("isolated lifecycle stop was already attempted")
    lifecycle.stop_attempted = True
    if lifecycle.process is None:
        lifecycle.teardown_path = "no-root"
    elif not lifecycle.job_assign_succeeded:
        lifecycle.teardown_path = "unassigned-suspended"
        lifecycle.prebind_root_stop_attempted = True
        try:
            lifecycle.process.kill()
            lifecycle.process.wait(timeout=CONNECT_HEADER_SECONDS)
            lifecycle.prebind_root_stop_succeeded = True
            lifecycle.root_waited = True
        except BaseException as error:  # noqa: BLE001
            append_cleanup_failure(lifecycle, "prebind root stop", error)
        try:
            wait_for_prelaunch_baseline(cim_helper)
            lifecycle.prebind_baseline_clean = True
        except BaseException as error:  # noqa: BLE001
            append_cleanup_failure(lifecycle, "prebind baseline", error)
    else:
        lifecycle.teardown_path = "job"
        try:
            terminate_lifecycle_job(lifecycle)
        except BaseException as error:  # noqa: BLE001
            append_cleanup_failure(lifecycle, "Job terminate", error)
        if lifecycle.process is not None:
            try:
                lifecycle.process.wait(timeout=CONNECT_HEADER_SECONDS)
                lifecycle.root_waited = True
            except BaseException as error:  # noqa: BLE001
                append_cleanup_failure(lifecycle, "isolated root wait", error)
        if lifecycle.job_terminate_succeeded:
            try:
                wait_for_isolated_job_quiescence(lifecycle)
            except BaseException as error:  # noqa: BLE001
                append_cleanup_failure(lifecycle, "Job zero window", error)
    close_lifecycle_pipes(lifecycle)
    join_lifecycle_drains(lifecycle)
    if lifecycle.job_handle is not None:
        try:
            close_lifecycle_job(lifecycle)
        except BaseException as error:  # noqa: BLE001
            append_cleanup_failure(lifecycle, "Job close", error)
    lifecycle.advance(IsolatedLifecyclePhase.STOPPED)


def normal_restore_eligible(lifecycle: IsolatedLifecycle, normal_server_resolution: str) -> bool:
    if normal_server_resolution == "uncertain" or lifecycle.primary_error is not None or lifecycle.cleanup_errors:
        return False
    if lifecycle.process is None:
        return lifecycle.job_handle is None or lifecycle.job_close_succeeded
    if not lifecycle.job_assign_succeeded:
        return (
            lifecycle.prebind_root_stop_succeeded
            and lifecycle.root_waited
            and lifecycle.prebind_baseline_clean
            and lifecycle.pipes_closed
            and lifecycle.drains_joined
            and lifecycle.job_close_succeeded
        )
    return (
        lifecycle.job_terminate_succeeded
        and lifecycle.root_waited
        and lifecycle.job_zero_window_confirmed
        and lifecycle.pipes_closed
        and lifecycle.drains_joined
        and lifecycle.job_close_succeeded
        and lifecycle.root_thread_handle_close_succeeded
    )


def restore_normal(
    baseline_version: bytes,
    baseline_tags: bytes,
    normal_hashes: dict[str, str],
    cim_helper: Path | None = None,
    fault: Callable[[str], None] | None = None,
    attempts: int = RESTORATION_ATTEMPTS,
) -> None:
    if attempts < 1:
        raise CaptureError("restoration attempts must be positive")
    launched = False
    operational_uncertainty = False

    def observe_exact_normal(phase: str) -> tuple[ProcessRecord, ProcessRecord]:
        nonlocal operational_uncertainty
        observation = observe_normal_topology(cim_helper)
        if observation.state is not NormalTopologyState.NORMAL or observation.app is None or observation.server is None:
            operational_uncertainty = True
            raise OperationalUncertainty(f"normal topology is unsafe at {phase}: {observation.diagnostic}")
        return observation.app, observation.server

    observation = observe_normal_topology(cim_helper)
    if observation.state is NormalTopologyState.EMPTY:
        observation = observe_normal_topology(cim_helper)
        if observation.state is NormalTopologyState.EMPTY:
            subprocess.Popen([str(NORMAL_APP)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
            launched = True
        elif observation.state is not NormalTopologyState.NORMAL:
            operational_uncertainty = True
            raise OperationalUncertainty(f"normal topology is unsafe before restoration launch: {observation.diagnostic}")
    elif observation.state is not NormalTopologyState.NORMAL:
        operational_uncertainty = True
        raise OperationalUncertainty(f"normal topology is unsafe before restoration launch: {observation.diagnostic}")

    if launched:
        readiness_deadline = time.monotonic() + 60.0
        while True:
            observation = observe_normal_topology(cim_helper)
            if observation.state is NormalTopologyState.NORMAL:
                break
            if observation.state not in {
                NormalTopologyState.EMPTY,
                NormalTopologyState.PARTIAL,
            }:
                operational_uncertainty = True
                raise OperationalUncertainty(f"normal topology is unsafe during restoration readiness: {observation.diagnostic}")
            if time.monotonic() >= readiness_deadline:
                operational_uncertainty = True
                raise OperationalUncertainty("normal Ollama did not become ready for restoration")
            time.sleep(0.5)

    canonical = IDENTITY_ROOT / "restoration"
    for attempt in range(attempts):
        app, server = observe_exact_normal("ATTEMPT_ENTRY")
        if operational_uncertainty:
            raise OperationalUncertainty("restoration operational uncertainty is terminal")
        staging = IDENTITY_ROOT / f".restoration-{uuid.uuid4().hex}"
        try:
            if canonical.exists() or canonical.is_symlink():
                raise RestorationIntegrityFailure("canonical restoration subtree already exists")
            try:
                staging.mkdir()
            except OSError as error:
                raise RetryableRestorationDataFault("private restoration staging cannot be created") from error
            if fault is not None:
                fault("after-process-observation")
            try:
                app_hash = sha256_file(NORMAL_APP)
                server_hash = sha256_file(NORMAL_SERVER)
            except OSError as error:
                raise RestorationIntegrityFailure("normal executable identity cannot be validated") from error
            if app_hash != normal_hashes["app"] or server_hash != normal_hashes["server"]:
                raise RestorationIntegrityFailure("normal executable identity differs after restoration")
            try:
                write_create_only_receipt(staging / "processes.json", {"app": app.__dict__ | {"exe": str(app.exe)}, "server": server.__dict__ | {"exe": str(server.exe)}})
            except OSError as error:
                raise RetryableRestorationDataFault("private restoration receipt cannot be written") from error
            restored_version = staging / "version.response.json"
            restored_tags = staging / "tags.response.json"
            try:
                stream_loopback_get(PORT, "/api/version", restored_version)
            except (CaptureError, OSError) as error:
                raise RetryableRestorationDataFault("restoration version transport failed") from error
            if fault is not None:
                fault("after-version-read")
            observe_exact_normal("AFTER_VERSION")
            try:
                stream_loopback_get(PORT, "/api/tags", restored_tags)
            except (CaptureError, OSError) as error:
                raise RetryableRestorationDataFault("restoration tags transport failed") from error
            if fault is not None:
                fault("after-tags-read")
            observe_exact_normal("AFTER_TAGS")
            try:
                restored_version_bytes = read_bounded_bytes(restored_version, "restored version")
                restored_tags_bytes = read_bounded_bytes(restored_tags, "restored tags")
            except OSError as error:
                raise RetryableRestorationDataFault("private restoration response cannot be read") from error
            if restored_version_bytes != baseline_version or restored_tags_bytes != baseline_tags:
                raise RestorationIntegrityFailure("normal raw response identity differs after restoration")
            if fault is not None:
                fault("after-raw-comparison")
            try:
                assert_restoration_staging(staging)
            except OSError as error:
                raise RetryableRestorationDataFault("private restoration staging cannot be validated") from error
            except CaptureError as error:
                raise RestorationIntegrityFailure("private restoration staging differs") from error
            observe_exact_normal("PRE_PUBLISH")
            if fault is not None:
                fault("after-final-topology")
            if fault is not None:
                fault("before-publication")
            if canonical.exists() or canonical.is_symlink():
                raise PublicationUncertainty("canonical restoration subtree appeared before publication")
            try:
                atomic_no_replace_publish(staging, canonical)
                if fault is not None:
                    fault("after-publication")
            except BaseException as error:
                if isinstance(error, PublicationUncertainty):
                    raise
                raise PublicationUncertainty("restoration publication is uncertain") from error
            return
        except RetryableRestorationDataFault:
            if staging.exists():
                cleanup_restoration_staging(staging)
            if attempt + 1 >= attempts:
                raise
            if fault is not None:
                fault("retry")
            time.sleep(0.5)
        except BaseException:
            if staging.exists():
                cleanup_restoration_staging(staging)
            raise


def cleanup_restoration_staging(staging: Path) -> None:
    assert_regular_or_directory(staging, "restoration staging")
    assert_no_reparse_or_ads(staging, "restoration staging")
    expected = {"processes.json", "version.response.json", "tags.response.json"}
    for path in staging.iterdir():
        if path.name not in expected:
            raise CaptureError("restoration staging contains an unexpected member")
        assert_regular(path, "restoration staging member")
        path.unlink()
    staging.rmdir()


def assert_regular_or_directory(path: Path, label: str) -> None:
    path_stat = path.lstat()
    if getattr(path_stat, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise CaptureError(f"{label} is a reparse point")
    if not path.is_dir():
        raise CaptureError(f"{label} is not a directory")


def assert_restoration_staging(staging: Path) -> None:
    assert_regular_or_directory(staging, "restoration staging")
    assert_no_reparse_or_ads(staging, "restoration staging")
    expected = {"processes.json", "version.response.json", "tags.response.json"}
    members = {path.name: path for path in staging.iterdir()}
    if set(members) != expected:
        raise CaptureError("restoration staging topology differs")
    total = 0
    for name in expected:
        member = members[name]
        assert_regular(member, "restoration staging member")
        size = member.stat().st_size
        if size > MAX_RESPONSE_BYTES:
            raise CaptureError("restoration staging member exceeds response cap")
        total += size
    if total > 3 * MAX_RESPONSE_BYTES:
        raise CaptureError("restoration staging exceeds total cap")


def atomic_no_replace_publish(staging: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise CaptureError("canonical restoration subtree already exists")
    try:
        if os.name == "nt":
            os.rename(staging, destination)
            return
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
        renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        renameat2.restype = ctypes.c_int
        if renameat2(-100, os.fsencode(staging), -100, os.fsencode(destination), 1) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(destination))
    except AttributeError as error:
        raise CaptureError("atomic no-replace restoration publication is unavailable") from error
    except OSError as error:
        if isinstance(error, FileExistsError) or error.errno == errno.EEXIST:
            raise CaptureError("canonical restoration subtree already exists") from error
        raise CaptureError("canonical restoration subtree cannot be published") from error


def runtime_snapshot(cim_helper: Path | None = None) -> dict[str, Any]:
    return {
        "listeners": list(listener_pids(PORT)),
        "processes": [record.__dict__ | {"exe": str(record.exe)} for record in process_records(cim_helper)],
    }


def recursive_hash_inventory(root: Path) -> list[dict[str, Any]]:
    assert_no_reparse_or_ads(root, "identity root")
    files: list[tuple[str, Path]] = []
    total_bytes = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in {"hash_inventory.json", "operation_receipt.json"}:
            continue
        assert_regular(path, "identity inventory member")
        size = path.stat().st_size
        if size > MAX_RESPONSE_BYTES:
            raise CaptureError("identity inventory member exceeds response cap")
        total_bytes += size
        if total_bytes > MAX_IDENTITY_INVENTORY_BYTES:
            raise CaptureError("identity inventory exceeds total cap")
        files.append((relative, path))
    return [
        {"bytes": path.stat().st_size, "path": relative, "sha256": sha256_file(path)}
        for relative, path in sorted(files, key=lambda item: item[0].encode("utf-8"))
    ]


def run_transport_self_test() -> None:
    parent = Path(tempfile.gettempdir()).resolve(strict=True)
    root = parent / f"anachron-v5-identity-transport-self-test-{uuid.uuid4().hex}"
    if root.exists():
        raise CaptureError("transport self-test root already exists")
    root.mkdir()
    directory_ads = Path(f"{root}:anachron-v5-directory-ads")
    listener: socket.socket | None = None
    thread: threading.Thread | None = None
    server_error: list[BaseException] = []
    spam_process: subprocess.Popen[bytes] | None = None
    spam_stdout: BoundedDrain | None = None
    spam_stderr: BoundedDrain | None = None
    primary_failure: BaseException | None = None
    cleanup_errors: list[str] = []
    try:
        write_create_only_bytes(directory_ads, b"ads")
        try:
            assert_no_reparse_or_ads(root, "transport self-test root")
        except CaptureError as error:
            if "contains an alternate data stream" not in str(error):
                raise
        else:
            raise CaptureError("directory ADS validator did not reject the task-owned ADS")
        directory_ads.unlink()
        if not has_runner_token("RUN_V5_RECOVERY.PY") or not has_runner_token("run_v5_CONDITIONAL_campaign.PS1"):
            raise CaptureError("transport self-test runner-token case folding differs")
        ready = threading.Event()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind((HOST, 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def responder() -> None:
            try:
                ready.set()
                connection, _ = listener.accept()
                with connection:
                    connection.recv(8192)
                    connection.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n")
                    remaining = MAX_RESPONSE_BYTES + 1
                    payload = b"x" * 8192
                    while remaining:
                        chunk = payload[: min(len(payload), remaining)]
                        connection.sendall(f"{len(chunk):X}\r\n".encode("ascii") + chunk + b"\r\n")
                        remaining -= len(chunk)
                    connection.sendall(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
            except BaseException as error:  # noqa: BLE001
                server_error.append(error)
            finally:
                listener.close()

        thread = threading.Thread(target=responder, name="anachron-v5-loopback-self-test", daemon=True)
        thread.start()
        if not ready.wait(CONNECT_HEADER_SECONDS):
            raise CaptureError("transport self-test responder did not start")
        existing = root / "preexisting.response"
        write_create_only_bytes(existing, b"\x01\x02\x03")
        try:
            stream_loopback_get(port, "/api/version", existing)
            raise CaptureError("transport self-test overwrote a preexisting destination")
        except FileExistsError:
            if read_bounded_bytes(existing, "self-test preexisting") != b"\x01\x02\x03":
                raise CaptureError("transport self-test changed preexisting bytes")
        capped = root / "capped.response"
        try:
            stream_loopback_get(port, "/api/version", capped)
            raise CaptureError("transport self-test accepted cap-plus-one response")
        except CaptureError as error:
            if "exceeds" not in str(error):
                raise
        if capped.stat().st_size != MAX_RESPONSE_BYTES:
            raise CaptureError("transport self-test wrote beyond response cap")
        spam_process = subprocess.Popen(
            [sys.executable, "-c", f"import sys; sys.stdout.buffer.write(b'x' * {MAX_RESPONSE_BYTES + 1})"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )
        if spam_process.stdout is None or spam_process.stderr is None:
            raise CaptureError("transport self-test spam pipes are unavailable")
        spam_stdout = start_bounded_drain(spam_process.stdout, root / "spam.stdout", "self-test-spam-stdout")
        spam_stderr = start_bounded_drain(spam_process.stderr, root / "spam.stderr", "self-test-spam-stderr")
        spam_process.wait(timeout=TOTAL_TRANSFER_SECONDS)
        join_bounded_drain(spam_stdout, "self-test spam stdout")
        join_bounded_drain(spam_stderr, "self-test spam stderr")
        if not spam_stdout.overflow.is_set() or spam_stdout.destination.stat().st_size != MAX_RESPONSE_BYTES:
            raise CaptureError("transport self-test spam drain did not reject cap-plus-one output")
    except BaseException as error:  # noqa: BLE001
        primary_failure = error

    def cleanup_phase(label: str, action: Callable[[], None]) -> None:
        try:
            action()
        except BaseException as error:  # noqa: BLE001
            cleanup_errors.append(f"{label}: {error}")

    if listener is not None:
        cleanup_phase("listener-close", listener.close)
    if thread is not None:
        cleanup_phase("responder-join", lambda: thread.join(CONNECT_HEADER_SECONDS))
        if thread.is_alive():
            cleanup_errors.append("responder-join: responder thread did not finish")
    if spam_process is not None and spam_process.poll() is None:
        def kill_spam() -> None:
            spam_process.kill()
            spam_process.wait(timeout=CONNECT_HEADER_SECONDS)

        cleanup_phase("spam-child-kill", kill_spam)
    for drain, label in ((spam_stdout, "spam-stdout-drain"), (spam_stderr, "spam-stderr-drain")):
        if drain is not None:
            cleanup_phase(label, lambda drain=drain, label=label: join_bounded_drain(drain, label))
    if directory_ads.exists():
        cleanup_phase(
            "directory-ads-remove",
            lambda: (assert_temp_root(parent, root, "transport self-test", require_no_ads=False), directory_ads.unlink()),
        )
    if root.exists():
        cleanup_phase(
            "temp-root-cleanup",
            lambda: safe_temp_cleanup(parent, root, ("preexisting.response", "capped.response", "spam.stdout", "spam.stderr"), "transport self-test"),
        )
    if server_error:
        cleanup_errors.append(f"responder: {server_error[0]}")
    if primary_failure is not None:
        if cleanup_errors:
            raise CaptureError(f"{primary_failure}; cleanup: {'; '.join(cleanup_errors)}") from primary_failure
        raise primary_failure
    if cleanup_errors:
        raise CaptureError("; ".join(cleanup_errors))


def run_capture() -> None:
    if IDENTITY_ROOT.exists():
        raise CaptureError(f"refusing to reuse identity output root: {IDENTITY_ROOT}")
    controller_path = Path(__file__).resolve(strict=True)
    assert_regular(controller_path, "controller source")
    assert_no_reparse_or_ads(controller_path, "controller source")
    controller_sha256 = sha256_file(controller_path)
    if hashlib.sha256(RUNTIME_IDENTITY_BYTES).hexdigest() != EXPECTED_RUNTIME_IDENTITY_SHA256:
        raise CaptureError("literal runtime identity SHA-256 differs")
    authority, authority_sidecar_sha256 = validate_controller_authority(controller_sha256)
    dependencies = controller_dependency_identity()
    verify_protocol(authority)
    manifest = verify_source_manifest(authority)
    helpers = verify_tracked_helpers(manifest)
    cim_helper = helpers["tools/read_v5_process_identity.ps1"]
    verify_isolated_runtime_and_store(manifest)
    verify_v4_failure_binding(manifest)
    assert_regular(NORMAL_APP, "normal app")
    assert_regular(NORMAL_SERVER, "normal server")
    normal_hashes = {"app": sha256_file(NORMAL_APP), "server": sha256_file(NORMAL_SERVER)}
    app, server = assert_normal_state(cim_helper)
    IDENTITY_ROOT.mkdir()
    cleanup_errors: list[str] = []
    normal_app_signal_issued = False
    normal_server_signal_issued = False
    normal_server_stop_resolution = "unattempted"

    def mark_app_signal_issued() -> None:
        nonlocal normal_app_signal_issued
        normal_app_signal_issued = True

    def mark_server_signal_issued() -> None:
        nonlocal normal_server_signal_issued
        normal_server_signal_issued = True
    lifecycle = IsolatedLifecycle()
    failure: BaseException | None = None
    baseline_version = b""
    baseline_tags = b""
    capture_prepared = False
    restoration_started = False
    restore_succeeded = False
    restoration_eligible = False
    started_at_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    try:
        write_create_only_receipt(IDENTITY_ROOT / "baseline-processes.json", runtime_snapshot(cim_helper))
        baseline_version_path = IDENTITY_ROOT / "baseline-version.response.json"
        baseline_tags_path = IDENTITY_ROOT / "baseline-tags.response.json"
        stream_loopback_get(PORT, "/api/version", baseline_version_path)
        stream_loopback_get(PORT, "/api/tags", baseline_tags_path)
        baseline_version = read_bounded_bytes(baseline_version_path, "baseline version")
        baseline_tags = read_bounded_bytes(baseline_tags_path, "baseline tags")
        if not force_stop_exact(app, None, "normal app", on_signal_issued=mark_app_signal_issued, cim_helper=cim_helper):
            raise CaptureError("normal app was absent before stop")
        try:
            if force_stop_exact(server, app.pid, "normal server", on_signal_issued=mark_server_signal_issued, cim_helper=cim_helper):
                normal_server_stop_resolution = "signaled"
            else:
                normal_server_stop_resolution = "absent"
        except CaptureError:
            normal_server_stop_resolution = "signaled-error" if normal_server_signal_issued else "uncertain"
            raise
        wait_for_listener_free()
        create_lifecycle_job(lifecycle)
        launch_suspended_isolated_root(lifecycle)
        assign_suspended_root_to_job(lifecycle)
        bind_suspended_root_identity(lifecycle, cim_helper)
        start_lifecycle_drains(lifecycle, IDENTITY_ROOT / "isolated-stdout.log", IDENTITY_ROOT / "isolated-stderr.log")
        if lifecycle.stdout_drain is None or lifecycle.stderr_drain is None or lifecycle.process is None:
            raise CaptureError("isolated log drains are incomplete")
        resume_suspended_root(lifecycle)
        wait_for_isolated_listener(
            lifecycle.process.pid,
            lifecycle.stdout_drain.overflow,
            lifecycle.stderr_drain.overflow,
            lifecycle,
            cim_helper,
        )
        assert_no_log_overflow(lifecycle)
        assert_isolated_state(lifecycle, cim_helper)
        write_create_only_receipt(IDENTITY_ROOT / "isolated-processes.json", runtime_snapshot(cim_helper))
        isolated_version_path = IDENTITY_ROOT / "isolated-version.response.json"
        isolated_tags_path = IDENTITY_ROOT / "isolated-tags.response.json"
        assert_isolated_state(lifecycle, cim_helper)
        stream_loopback_get(PORT, "/api/version", isolated_version_path)
        assert_isolated_state(lifecycle, cim_helper)
        stream_loopback_get(PORT, "/api/tags", isolated_tags_path)
        assert_no_log_overflow(lifecycle)
        isolated_version = read_json(isolated_version_path, "isolated version")
        if isolated_version.get("version") != "0.33.2":
            raise CaptureError("isolated version differs")
        assert_exact_tags(read_json(isolated_tags_path, "isolated tags"))
        assert_isolated_state(lifecycle, cim_helper)
        write_create_only_bytes(IDENTITY_ROOT / "runtime_identity.json", RUNTIME_IDENTITY_BYTES)
        assert_runtime_identity(IDENTITY_ROOT / "runtime_identity.json")
        capture_prepared = True
    except BaseException as error:  # noqa: BLE001
        failure = error
        lifecycle.primary_error = lifecycle_failure(lifecycle, "capture", error)
    finally:
        if normal_app_signal_issued or normal_server_signal_issued:
            try:
                stop_isolated_lifecycle(lifecycle, cim_helper)
            except BaseException as error:  # noqa: BLE001
                cleanup_errors.append(f"isolated-stop: {error}")
            cleanup_errors.extend(
                f"{receipt.operation}: {receipt.message}" for receipt in lifecycle.cleanup_errors
            )
            if isolated_log_overflow(lifecycle):
                cleanup_errors.append("isolated-log-overflow")
            try:
                if normal_server_stop_resolution == "unattempted":
                    if force_stop_exact(server, app.pid, "normal server", on_signal_issued=mark_server_signal_issued, cim_helper=cim_helper):
                        normal_server_stop_resolution = "signaled"
                    else:
                        normal_server_stop_resolution = "absent"
                if normal_server_stop_resolution == "uncertain":
                    raise CaptureError("normal server ownership is uncertain before restoration")
                restoration_eligible = normal_restore_eligible(lifecycle, normal_server_stop_resolution)
                if not restoration_eligible:
                    raise CaptureError("isolated teardown is unsafe before restoration")
                restoration_started = True
                restore_normal(baseline_version, baseline_tags, normal_hashes, cim_helper)
                restore_succeeded = True
            except BaseException as error:  # noqa: BLE001
                cleanup_errors.append(f"normal-restore: {error}")
                append_cleanup_failure(lifecycle, "normal restore", error)
        write_create_only_receipt(
            IDENTITY_ROOT / "operation_status.json",
            {
                "capture_prepared": capture_prepared,
                "completed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "isolated_log_overflow": isolated_log_overflow(lifecycle),
                "isolated_pid": lifecycle.process.pid if lifecycle.process is not None else None,
                "phase": lifecycle.phase.value,
                "job_create_attempted": lifecycle.job_create_attempted,
                "job_create_succeeded": lifecycle.job_create_succeeded,
                "root_launch_attempted": lifecycle.root_launch_attempted,
                "root_launch_succeeded": lifecycle.root_launch_succeeded,
                "job_assign_attempted": lifecycle.job_assign_attempted,
                "job_assign_succeeded": lifecycle.job_assign_succeeded,
                "root_bind_attempted": lifecycle.root_bind_attempted,
                "root_bind_succeeded": lifecycle.root_bind_succeeded,
                "drains_started_attempted": lifecycle.drains_started_attempted,
                "drains_started_succeeded": lifecycle.drains_started_succeeded,
                "root_resume_attempted": lifecycle.root_resume_attempted,
                "root_resume_succeeded": lifecycle.root_resume_succeeded,
                "root_thread_handle_close_attempted": lifecycle.root_thread_handle_close_attempted,
                "root_thread_handle_close_succeeded": lifecycle.root_thread_handle_close_succeeded,
                "teardown_path": lifecycle.teardown_path,
                "job_terminate_attempted": lifecycle.job_terminate_attempted,
                "job_terminate_succeeded": lifecycle.job_terminate_succeeded,
                "root_waited": lifecycle.root_waited,
                "job_zero_window_confirmed": lifecycle.job_zero_window_confirmed,
                "prebind_root_stop_attempted": lifecycle.prebind_root_stop_attempted,
                "prebind_root_stop_succeeded": lifecycle.prebind_root_stop_succeeded,
                "prebind_baseline_clean": lifecycle.prebind_baseline_clean,
                "pipes_closed": lifecycle.pipes_closed,
                "drains_joined": lifecycle.drains_joined,
                "job_close_attempted": lifecycle.job_close_attempted,
                "job_close_succeeded": lifecycle.job_close_succeeded,
                "cleanup_errors": [receipt.__dict__ for receipt in lifecycle.cleanup_errors],
                "normal_restore_eligible": restoration_eligible,
                "local_host_non_adversarial_limitation": "Loopback and local files are not independent provenance against a host-level adversary.",
                "model_execution_performed": False,
                "normal_app_signal_issued": normal_app_signal_issued,
                "normal_server_signal_issued": normal_server_signal_issued,
                "normal_server_stop_resolution": normal_server_stop_resolution,
                "primary_error": None if lifecycle.primary_error is None else lifecycle.primary_error.__dict__,
                "restore_succeeded": restore_succeeded,
                "restoration_started": restoration_started,
                "started_at_utc": started_at_utc,
            },
        )
    if failure is not None:
        raise failure
    if cleanup_errors:
        raise CaptureError("; ".join(cleanup_errors))
    if not capture_prepared or not restore_succeeded:
        raise CaptureError("identity capture or normal restoration did not complete")
    assert_regular(controller_path, "controller source")
    assert_no_reparse_or_ads(controller_path, "controller source")
    if sha256_file(controller_path) != controller_sha256:
        raise CaptureError("controller source changed during capture")
    if validate_controller_authority(controller_sha256)[1] != authority_sidecar_sha256:
        raise CaptureError("controller authority sidecar changed during capture")
    inventory = recursive_hash_inventory(IDENTITY_ROOT)
    write_create_only_receipt(IDENTITY_ROOT / "hash_inventory.json", {"files": inventory, "schema_version": "anachron-v5-identity-hash-inventory-v1"})
    write_create_only_receipt(
        IDENTITY_ROOT / "operation_receipt.json",
        {
            "allowed_endpoints": sorted(ADMITTED_ENDPOINTS),
            "controller_sha256": controller_sha256,
            "controller_authority_sha256": authority_sidecar_sha256,
            "dependencies": dependencies,
            "expected_models": [{"digest": digest, "name": name} for name, digest in EXPECTED_MODELS],
            "expected_runtime_identity_sha256": EXPECTED_RUNTIME_IDENTITY_SHA256,
            "hash_inventory_sha256": sha256_file(IDENTITY_ROOT / "hash_inventory.json"),
            "identity_root": str(IDENTITY_ROOT),
            "isolated_executable_sha256": ISOLATED_EXE_SHA256,
            "isolated_signer_thumbprint": EXPECTED_SIGNER_THUMBPRINT,
            "local_host_non_adversarial_limitation": "This capture is internally consistent local-host evidence, not independent provenance against a host-level adversary.",
            "protocol_commit": authority["protocol_commit"],
            "protocol_tag": PROTOCOL_TAG,
            "protocol_tag_object": authority["protocol_tag_object"],
            "runtime_identity_sha256": sha256_file(IDENTITY_ROOT / "runtime_identity.json"),
            "schema_version": "anachron-v5-identity-operation-receipt-v1",
            "source_manifest_sha256": authority["source_manifest_sha256"],
        },
    )


def run_process_preflight() -> None:
    controller_path = Path(__file__).resolve(strict=True)
    assert_regular(controller_path, "controller source")
    assert_no_reparse_or_ads(controller_path, "controller source")
    controller_sha256 = sha256_file(controller_path)
    authority, _ = validate_controller_authority(controller_sha256)
    controller_dependency_identity()
    verify_protocol(authority)
    helpers = verify_tracked_helpers(verify_source_manifest(authority))
    app, server = assert_normal_state(helpers["tools/read_v5_process_identity.ps1"])
    print(f"PROCESS_PREFLIGHT_VALID app_pid={app.pid} server_pid={server.pid}")


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--transport-self-test", action="store_true")
    mode.add_argument("--process-preflight", action="store_true")
    arguments = parser.parse_args()
    if arguments.transport_self_test:
        run_transport_self_test()
    elif arguments.process_preflight:
        run_process_preflight()
    else:
        run_capture()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CaptureError as error:
        print(f"capture failed: {error}", file=sys.stderr)
        raise SystemExit(1)
