"""Capture machine and Ollama evidence before the sealed date-shift bundle exists."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from anachron.date_shift import DateShiftValidationError, bytes_sha256, canonical_sha256
from anachron.date_shift_bundle import load_object, write_create_only
from anachron.date_shift_provenance import admit_scaffold_repository


def _command(arguments: list[str]) -> bytes:
    try:
        completed = subprocess.run(
            arguments, check=True, capture_output=True, timeout=30
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise DateShiftValidationError("runtime capture command failed") from error
    output = completed.stdout or completed.stderr
    if not output:
        raise DateShiftValidationError("runtime capture command produced no evidence")
    return output


def _api(endpoint: str, suffix: str) -> dict:
    try:
        with urlopen(endpoint.rstrip("/") + suffix, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DateShiftValidationError("Ollama API runtime capture failed") from error
    if not isinstance(payload, dict):
        raise DateShiftValidationError("Ollama API response is invalid")
    return payload


def _ram_bytes() -> int:
    if os.name == "nt":
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
            ]

        status = Status()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise DateShiftValidationError("Windows RAM capture failed")
        return int(status.total_physical)
    return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))


def _video_adapters() -> tuple[list[dict[str, str]], str]:
    if os.name != "nt":
        raise DateShiftValidationError(
            "the sealed date-shift runtime capture requires Windows CIM evidence"
        )
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,PNPDeviceID | ConvertTo-Json -Compress",
    ]
    raw = _command(command)
    try:
        parsed = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DateShiftValidationError(
            "Windows video adapter evidence was not JSON"
        ) from error
    rows = parsed if isinstance(parsed, list) else [parsed]
    adapters = []
    for row in rows:
        if not isinstance(row, dict) or not all(
            isinstance(row.get(key), str) and row[key]
            for key in ("Name", "DriverVersion", "PNPDeviceID")
        ):
            raise DateShiftValidationError(
                "Windows video adapter evidence is incomplete"
            )
        adapters.append(
            {
                "name": row["Name"],
                "driver_version": row["DriverVersion"],
                "pnp_device_id": row["PNPDeviceID"],
            }
        )
    return adapters, bytes_sha256(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture create-only date-shift runtime preflight from a released clean scaffold."
    )
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--context-tokens", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.context_tokens <= 0:
        raise DateShiftValidationError("context tokens must be positive")
    provenance = admit_scaffold_repository(args.repository)
    plan = load_object(args.repository / "research/date-shift/execution_plan.json")
    if (
        args.endpoint != plan["endpoint"]
        or args.context_tokens != plan["decoding"]["num_ctx"]
    ):
        raise DateShiftValidationError(
            "runtime capture arguments do not match the static execution plan"
        )
    cli = shutil.which("ollama")
    if not cli:
        raise DateShiftValidationError("Ollama CLI is unavailable")
    tags = _api(args.endpoint, "/api/tags")
    version = _api(args.endpoint, "/api/version")
    if (
        not isinstance(tags.get("models"), list)
        or not isinstance(version.get("version"), str)
        or not version["version"]
    ):
        raise DateShiftValidationError("Ollama API inventory is invalid")
    adapters, adapter_digest = _video_adapters()
    models = []
    for row in tags["models"]:
        if (
            isinstance(row, dict)
            and isinstance(row.get("name"), str)
            and isinstance(row.get("digest"), str)
        ):
            digest = (
                row["digest"]
                if row["digest"].startswith("sha256:")
                else "sha256:" + row["digest"]
            )
            models.append({"name": row["name"], "digest": digest})
    value = {
        "schema_version": "date-shift-runtime-preflight-v3",
        "capture_provenance": provenance,
        "captured_at_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "endpoint": args.endpoint,
        "ollama": {
            "cli_path": str(Path(cli).resolve()),
            "cli_sha256": bytes_sha256(Path(cli).read_bytes()),
            "cli_version_raw": _command([cli, "--version"])
            .decode("utf-8", errors="strict")
            .strip(),
            "api_version": version["version"],
            "tags_response_sha256": canonical_sha256(tags),
            "models": models,
        },
        "host": {
            "os": platform.platform(),
            "python": sys.version,
            "cpu": platform.processor() or platform.machine(),
            "ram_bytes": _ram_bytes(),
            "video_adapters": adapters,
            "video_adapter_capture_sha256": adapter_digest,
        },
        "context_tokens": args.context_tokens,
    }
    write_create_only(args.output, value)
    print(canonical_sha256(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
