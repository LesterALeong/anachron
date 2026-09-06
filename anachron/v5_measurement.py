"""Bounded v5 measurement lifecycle, evidence custody, and semantic replay."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from anachron.v5_contract import (
    AUTHORITY_CONTRACT_PATH,
    EXPECTED_RUNTIME_IDENTITY,
    V5_PROTOCOL_TAG,
    V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS,
    V5_SEED_NAMESPACE,
)
from anachron.v5_custody import (
    AUTHORITY_MEMBER_MAX_BYTES,
    FAILURE_MAX_BYTES,
    FAILURE_MAX_FILES,
    SUCCESS_MAX_BYTES,
    SUCCESS_MAX_ENTRIES,
    SUCCESS_MAX_FILES,
    ByteBudget,
    V5CustodyError,
    bounded_regular_files,
    capture_regular,
    discard_staging_root,
    physical_inventory,
    publish_staging_root,
    write_create_only,
)
from anachron.v5_paths import (
    V5PathError,
    admit_create_only_external_output,
    assert_no_alternate_data_streams,
    fsync_directory,
    ordinal_evidence_path,
    portable_relative_path,
)
from anachron.v5_registry import (
    V5RegistryError,
    canonical_json_bytes,
    eligible_records,
    load_compatibility_case_snapshot,
    load_v5_registry_snapshot,
    strict_json_loads,
)

MODES = ("unrestricted", "enforced")
REPETITION_SEEDS = (1477205243, 106139663)
REPETITION_SEED_NAMESPACE = V5_SEED_NAMESPACE
FIRST_CATEGORIES = ("valid", "no_tool_call", "tool_call_count_invalid", "tool_name_invalid", "tool_arguments_unparseable_or_nonobject", "tool_arguments_duplicate_key", "tool_keyset_invalid", "query_invalid", "date_invalid_type", "date_mismatch", "first_native_response_invalid")
FINAL_CATEGORIES = ("valid", "final_native_response_invalid", "final_content_invalid")
_MAX_RESPONSE_BYTES = 1_048_576
_MAX_CAMPAIGN_RESPONSE_BYTES = 8_388_608
_MAX_GENERATED_TRACE_BYTES = 262_144
_MAX_AUTHORITY_BYTES = AUTHORITY_MEMBER_MAX_BYTES
_MAX_REPLAY_METADATA_BYTES = 1_048_576
_PRIMARY_TRAJECTORY_COUNT = 64
_COMPATIBILITY_TRACE_COUNT = 2
_AUTHORITY_FILE_COUNT = 9
_REPLAY_METADATA_FILE_COUNT = 4
_MAX_REPLAY_EVIDENCE_BYTES = (
    _AUTHORITY_FILE_COUNT * _MAX_AUTHORITY_BYTES
    + _REPLAY_METADATA_FILE_COUNT * _MAX_REPLAY_METADATA_BYTES
    + _COMPATIBILITY_TRACE_COUNT * _MAX_GENERATED_TRACE_BYTES
    + _PRIMARY_TRAJECTORY_COUNT * 3 * _MAX_GENERATED_TRACE_BYTES
    + _MAX_CAMPAIGN_RESPONSE_BYTES
)
_FIXED_ENDPOINT = "http://127.0.0.1:11434"
_AUTHORITY_PATHS = (
    "authority/acceptance-matrix.md",
    "authority/authority-contract.json",
    "authority/carry-forward.json",
    "authority/compatibility-plan.json",
    "authority/conditional-go.json",
    "authority/full-plan.json",
    "authority/materialization-receipt.json",
    "authority/runtime-identity.json",
    "authority/source-manifest.json",
)
_AUTHORITY_WRITE_PATHS = (
    "authority/full-plan.json",
    "authority/conditional-go.json",
    "authority/compatibility-plan.json",
    "authority/carry-forward.json",
    "authority/source-manifest.json",
    "authority/materialization-receipt.json",
    "authority/runtime-identity.json",
    "authority/authority-contract.json",
    "authority/acceptance-matrix.md",
)
_IDENTITY_WRITE_PATHS = (
    "identity/version.wire",
    "identity/tags.wire",
    "identity/pre-primary-version.wire",
    "identity/pre-primary-tags.wire",
    "identity/post-primary-version.wire",
    "identity/post-primary-tags.wire",
)
_METADATA_WRITE_PATHS = (
    "rows.json",
    "runtime_identity.json",
    "summary.json",
    "journal.json",
)
_IDENTITY_PATHS = (
    "identity/post-primary-tags.wire",
    "identity/post-primary-version.wire",
    "identity/pre-primary-tags.wire",
    "identity/pre-primary-version.wire",
    "identity/tags.wire",
    "identity/version.wire",
)
_METADATA_PATHS = ("journal.json", "rows.json", "runtime_identity.json", "summary.json")
_SUCCESS_SIDECARS = ("manifest.json", "manifest.sha256")
_FAILURE_SIDECARS = (
    "failure_inventory.json",
    "failure_inventory.sha256",
    "failure_receipt.json",
)
_RAW_PATH = re.compile(r"raw/t(?P<ordinal>\d{4})-(?P<role>first-request|first-response|tool-result|final-request|final-response)\.json")
_COMPATIBILITY_PATH = re.compile(r"compatibility/raw/t(?P<ordinal>000[12])-(?P<role>first-request\.json|first-response\.wire)")
_TERMINAL_SIDECAR_RESERVE = 2 * _MAX_REPLAY_METADATA_BYTES + 128


class V5MeasurementError(ValueError):
    """Raised for malformed inputs or replay evidence."""


class V5OperationalFailure(V5MeasurementError):
    """A terminal infrastructure/custody failure which cannot resume under this GO."""

    def __init__(self, fault_code: str, *, phase: str, failed_step: int, last_completed_step: int, ordinal: int | None = None) -> None:
        super().__init__(fault_code)
        self.fault_code = fault_code
        self.phase = phase
        self.failed_step = failed_step
        self.last_completed_step = last_completed_step
        self.ordinal = ordinal


class _DuplicateArguments(Exception):
    pass


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _expected_evidence_directories(files: set[str]) -> tuple[str, ...]:
    directories = {"raw"}
    for relative in files:
        parent = Path(relative).parent
        while parent != Path("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return tuple(sorted(directories))


def _validate_scientific_source_manifest(repository_root: Path, raw: bytes) -> dict[str, bytes]:
    """Bind scientific replay inputs to the immutable materialized source closure."""

    value = strict_json_loads(raw, "source manifest")
    if type(value) is not dict or raw != canonical_json_bytes(value):
        raise V5MeasurementError("source manifest differs")
    governed = value.get("governed_files")
    if value.get("governed_paths") != list(V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS) or type(governed) is not list:
        raise V5MeasurementError("source manifest topology differs")
    rows = {row.get("path"): row for row in governed if type(row) is dict}
    if set(rows) != set(V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS) or len(rows) != len(governed):
        raise V5MeasurementError("source manifest topology differs")
    captured: dict[str, bytes] = {}
    for relative in V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS:
        row = rows[relative]
        if set(row) != {"path", "sha256", "tag_blob_oid"} or type(row["sha256"]) is not str:
            raise V5MeasurementError("source manifest row differs")
        actual = capture_regular(repository_root / relative, "governed scientific source", _MAX_AUTHORITY_BYTES)
        if actual.sha256 != row["sha256"]:
            raise V5MeasurementError("source manifest checkout differs")
        captured[relative] = actual.raw
    return captured


def evidence_file_cap(relative: str) -> int:
    """Return the producer-derived replay cap for one manifest-listed file."""

    if relative in _AUTHORITY_PATHS:
        return _MAX_AUTHORITY_BYTES
    if relative in _IDENTITY_PATHS:
        return _MAX_RESPONSE_BYTES
    compatibility = _COMPATIBILITY_PATH.fullmatch(relative)
    if compatibility is not None:
        return _MAX_RESPONSE_BYTES if compatibility["role"] == "first-response.wire" else _MAX_GENERATED_TRACE_BYTES
    raw = _RAW_PATH.fullmatch(relative)
    if raw is not None and 1 <= int(raw["ordinal"]) <= _PRIMARY_TRAJECTORY_COUNT:
        return _MAX_RESPONSE_BYTES if raw["role"] in {"first-response", "final-response"} else _MAX_GENERATED_TRACE_BYTES
    if relative in _METADATA_PATHS or relative in {"failure_receipt.json", "failure_inventory.json", "manifest.json"}:
        return _MAX_REPLAY_METADATA_BYTES
    if relative in {"failure_inventory.sha256", "manifest.sha256"}:
        return 128
    raise V5MeasurementError(f"{relative} is not an exact v5 evidence path")


def bounded_evidence_bytes(evidence: Path, relative: str, label: str, *, expected_sha256: str | None = None) -> bytes:
    """Read one replay file only after stat and streaming cap enforcement."""

    try:
        portable_relative_path(relative, label)
        path = evidence.joinpath(*Path(relative).parts)
        cap = evidence_file_cap(relative)
        return capture_regular(path, label, cap, expected_sha256=expected_sha256).raw
    except V5CustodyError as error:
        if "byte cap" in str(error):
            raise V5MeasurementError(f"{label} exceeds its replay byte cap") from error
        raise V5MeasurementError(f"{label} cannot be read") from error
    except OSError as error:
        raise V5MeasurementError(f"{label} cannot be read") from error


def _strict_arguments(raw: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise _DuplicateArguments
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    except _DuplicateArguments:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise V5MeasurementError("arguments are not an object JSON") from error
    if type(value) is not dict:
        raise V5MeasurementError("arguments are not an object JSON")
    return value


def classify_first_response(response: object, card: Mapping[str, Any]) -> dict[str, Any]:
    """Classify exactly one first response; supplied dates never control retrieval."""

    invalid = {"category": "first_native_response_invalid", "tool": None}
    if type(response) is not dict:
        return invalid
    message = response.get("message")
    if type(message) is not dict or message.get("role") != "assistant":
        return invalid
    calls = message.get("tool_calls")
    if calls is None:
        return {"category": "no_tool_call", "tool": None}
    if type(calls) is not list or len(calls) != 1:
        return {"category": "tool_call_count_invalid", "tool": None}
    call = calls[0]
    if type(call) is not dict or type(call.get("function")) is not dict:
        return invalid
    function = call["function"]
    if function.get("name") != "anachron_search":
        return {"category": "tool_name_invalid", "tool": None}
    arguments = function.get("arguments")
    try:
        parsed = arguments if type(arguments) is dict else _strict_arguments(arguments) if type(arguments) is str else None
    except _DuplicateArguments:
        return {"category": "tool_arguments_duplicate_key", "tool": None}
    except V5MeasurementError:
        return {"category": "tool_arguments_unparseable_or_nonobject", "tool": None}
    if type(parsed) is not dict:
        return {"category": "tool_arguments_unparseable_or_nonobject", "tool": None}
    if set(parsed) not in ({"query"}, {"query", "date"}):
        return {"category": "tool_keyset_invalid", "tool": None}
    if type(parsed["query"]) is not str or not parsed["query"].strip():
        return {"category": "query_invalid", "tool": None}
    if "date" in parsed and type(parsed["date"]) is not str:
        return {"category": "date_invalid_type", "tool": None}
    tool = {"query": parsed["query"]}
    if "date" in parsed:
        tool["date"] = parsed["date"]
        if parsed["date"] != card["as_of"]:
            return {"category": "date_mismatch", "tool": tool}
    return {"category": "valid", "tool": tool}


def classify_final_response(response: object) -> str:
    if type(response) is not dict:
        return "final_native_response_invalid"
    message = response.get("message")
    if type(message) is not dict or message.get("role") != "assistant":
        return "final_native_response_invalid"
    return "valid" if type(message.get("content")) is str and message["content"].strip() else "final_content_invalid"


def build_schedule(cards: Mapping[str, Mapping[str, Any]], models: Sequence[str]) -> list[dict[str, Any]]:
    if type(models) not in {list, tuple} or len(models) != 2 or len(set(models)) != 2 or any(type(model) is not str or not model for model in models):
        raise V5MeasurementError("v5 requires exactly two distinct models")
    if len(cards) != 8:
        raise V5MeasurementError("v5 requires exactly eight primary cards")
    rows, ordinal = [], 1
    for case_id in sorted(cards):
        for mode in MODES:
            for model in models:
                for repetition, seed in enumerate(REPETITION_SEEDS, 1):
                    rows.append({"case_id": case_id, "mode": mode, "model": model, "ordinal": ordinal, "repetition": repetition, "seed": seed, "trajectory_id": f"v5-t{ordinal:04d}"})
                    ordinal += 1
    return rows


def first_request(trajectory: Mapping[str, Any], card: Mapping[str, Any]) -> dict[str, Any]:
    return {"keep_alive": 0, "messages": [{"content": card["prompt"], "role": "user"}], "model": trajectory["model"], "options": {"num_ctx": 8192, "num_predict": 256, "seed": trajectory["seed"], "temperature": 0}, "stream": False, "tools": [{"function": {"description": "Search the synthetic card corpus.", "name": "anachron_search", "parameters": {"properties": {"date": {"type": "string"}, "query": {"type": "string"}}, "required": ["query"], "type": "object"}}, "type": "function"}]}


def final_request(trajectory: Mapping[str, Any], card: Mapping[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    return {"keep_alive": 0, "messages": [{"content": card["prompt"], "role": "user"}, {"content": json.dumps(records, sort_keys=True), "role": "tool"}], "model": trajectory["model"], "options": {"num_ctx": 8192, "num_predict": 256, "seed": trajectory["seed"], "temperature": 0}, "stream": False}


def _parse_wire(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = strict_json_loads(raw, label)
    except V5RegistryError as error:
        raise V5MeasurementError(f"{label} is not native JSON") from error
    if type(value) is not dict:
        raise V5MeasurementError(f"{label} native envelope differs")
    return value


class _Budget:
    def __init__(self) -> None:
        self.total = 0

    def admit(self, raw: object, *, phase: str, step: int, completed: int, ordinal: int | None) -> bytes:
        if type(raw) is not bytes:
            raise V5OperationalFailure("transport", phase=phase, failed_step=step, last_completed_step=completed, ordinal=ordinal)
        if len(raw) > _MAX_RESPONSE_BYTES or self.total + len(raw) > _MAX_CAMPAIGN_RESPONSE_BYTES:
            raise V5OperationalFailure("resource", phase=phase, failed_step=step, last_completed_step=completed, ordinal=ordinal)
        self.total += len(raw)
        return raw

    def capture_response(self, stream: object, *, phase: str, step: int, completed: int, ordinal: int | None) -> bytes:
        """Stream one native response without growing beyond either response budget."""

        raw = bytearray()
        try:
            while chunk := stream.read(65_536):
                if type(chunk) is not bytes or len(chunk) > 65_536:
                    raise V5OperationalFailure(
                        "transport",
                        phase=phase,
                        failed_step=step,
                        last_completed_step=completed,
                        ordinal=ordinal,
                    )
                if (
                    len(raw) + len(chunk) > _MAX_RESPONSE_BYTES
                    or self.total + len(raw) + len(chunk) > _MAX_CAMPAIGN_RESPONSE_BYTES
                ):
                    raise V5OperationalFailure(
                        "resource",
                        phase=phase,
                        failed_step=step,
                        last_completed_step=completed,
                        ordinal=ordinal,
                    )
                raw.extend(chunk)
        except V5OperationalFailure:
            raise
        except Exception as error:
            raise V5OperationalFailure(
                "transport",
                phase=phase,
                failed_step=step,
                last_completed_step=completed,
                ordinal=ordinal,
            ) from error
        self.total += len(raw)
        return bytes(raw)


class _Writer:
    def __init__(self, root: Path) -> None:
        self.output, self.files, self.events = Path(root), {}, []
        self.budget = ByteBudget(FAILURE_MAX_BYTES, FAILURE_MAX_FILES)
        self.publication_state = "STAGING"
        if self.output.exists():
            raise V5OperationalFailure("state", phase="setup", failed_step=1, last_completed_step=0)
        try:
            self.root = Path(tempfile.mkdtemp(prefix=f".{self.output.name}.staging-", dir=self.output.parent))
            (self.root / "raw").mkdir(mode=0o700)
        except OSError as error:
            raise V5OperationalFailure("writer", phase="setup", failed_step=1, last_completed_step=0) from error

    def publish(self, failure: V5OperationalFailure | None = None) -> None:
        try:
            publish_staging_root(self.root, self.output, "evidence output")
            self.publication_state = "RENAMED"
        except (OSError, V5CustodyError, V5PathError) as error:
            raise V5OperationalFailure("writer", phase="seal", failed_step=0, last_completed_step=0) from error
        try:
            fsync_directory(self.output.parent, "evidence output parent")
        except (OSError, V5PathError) as error:
            raise V5OperationalFailure(
                "publication_durability_unknown",
                phase="seal" if failure is None else failure.phase,
                failed_step=0 if failure is None else failure.failed_step,
                last_completed_step=0 if failure is None else failure.last_completed_step,
                ordinal=None if failure is None else failure.ordinal,
            ) from error

    def discard(self) -> None:
        if self.publication_state != "STAGING" or not self.root.exists():
            return
        discard_staging_root(
            self.root,
            "evidence staging",
            maximum_entries=FAILURE_MAX_FILES + 5,
            maximum_depth=2,
        )

    def _write(self, relative: str, raw: bytes) -> None:
        try:
            portable_relative_path(relative, "evidence")
            self._reconcile_physical_tree("evidence staging")
            cap = evidence_file_cap(relative)
            if len(raw) > cap:
                raise V5CustodyError(f"{relative} exceeds its byte cap")
            if (
                relative not in _FAILURE_SIDECARS + _SUCCESS_SIDECARS
                and self.budget.bytes_used + len(raw) + _TERMINAL_SIDECAR_RESERVE
                > FAILURE_MAX_BYTES
            ):
                raise V5CustodyError(f"{relative} exceeds terminal custody reserve")
            destination = self.root.joinpath(*Path(relative).parts)
            if destination.exists():
                raise V5PathError("evidence destination must be absent")
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.files[relative] = write_create_only(
                destination,
                raw,
                relative,
                self.budget,
            )
        except (OSError, V5CustodyError, V5PathError) as error:
            raise V5OperationalFailure("writer", phase="primary", failed_step=0, last_completed_step=0) from error

    def raw(self, ordinal: int, role: str, raw: bytes) -> None:
        try:
            relative = ordinal_evidence_path(self.root, ordinal, role).relative_to(self.root).as_posix()
        except V5PathError as error:
            raise V5OperationalFailure("path", phase="primary", failed_step=0, last_completed_step=0, ordinal=ordinal) from error
        self._write(relative, raw)

    def json(self, relative: str, value: object) -> None:
        self._write(relative, canonical_json_bytes(value))

    def journal(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def _expected_directories(self, files: set[str]) -> tuple[str, ...]:
        directories = {"raw"}
        for relative in files:
            parent = Path(relative).parent
            while parent != Path("."):
                directories.add(parent.as_posix())
                parent = parent.parent
        return tuple(sorted(directories))

    def _reconcile_physical_tree(self, label: str) -> None:
        inventory = physical_inventory(
            self.root,
            label,
            maximum_entries=FAILURE_MAX_FILES + 5,
            maximum_depth=2,
            byte_limit=SUCCESS_MAX_BYTES if label == "success evidence" else FAILURE_MAX_BYTES,
            member_cap=evidence_file_cap,
        )
        expected_files = tuple(sorted(self.files))
        if (
            inventory.directories != self._expected_directories(set(expected_files))
            or tuple(member.path for member in inventory.files) != expected_files
            or any(self.files[member.path] != member.sha256 for member in inventory.files)
            or sum(member.size_bytes for member in inventory.files) != self.budget.bytes_used
            or len(inventory.files) != self.budget.members_used
        ):
            raise V5OperationalFailure("writer", phase="seal", failed_step=0, last_completed_step=0)

    def seal(self, rows: list[dict[str, Any]], identity: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
        self.json("rows.json", rows)
        self.json("runtime_identity.json", identity)
        self.json("summary.json", summary)
        self.json("journal.json", {"events": self.events, "schema_version": "anachron-v5-journal-v1", "v4_included_count": 0})
        manifest = {"files": [{"path": path, "sha256": digest} for path, digest in sorted(self.files.items())], "row_count": len(rows), "schema_version": "anachron-v5-evidence-manifest-v2", "v4_included_count": 0}
        try:
            raw = canonical_json_bytes(manifest)
            self._write("manifest.json", raw)
            digest = f"{sha256_bytes(raw)}  manifest.json\n".encode("ascii")
            self._write("manifest.sha256", digest)
            if self.budget.bytes_used > SUCCESS_MAX_BYTES or self.budget.members_used > SUCCESS_MAX_FILES:
                raise V5OperationalFailure("resource", phase="seal", failed_step=0, last_completed_step=0)
            assert_no_alternate_data_streams(self.root)
            bounded_regular_files(
                self.root,
                "success evidence",
                maximum_entries=SUCCESS_MAX_ENTRIES,
                maximum_depth=2,
            )
            self._reconcile_physical_tree("success evidence")
            self.publish()
        except (V5CustodyError, V5PathError, OSError) as error:
            raise V5OperationalFailure("manifest", phase="seal", failed_step=0, last_completed_step=0) from error
        return manifest

    def _custody_state(self) -> dict[str, int]:
        paths = tuple(self.files)
        first_complete = {int(path[5:9]) for path in paths if path.endswith("-first-response.json")}
        final_complete = {int(path[5:9]) for path in paths if path.endswith("-final-response.json")}
        raw_members = sum(path.startswith("raw/t") for path in paths)
        compatibility_complete = sum(path.endswith("-first-response.wire") for path in paths)
        compatibility_members = sum(path.startswith("compatibility/raw/") for path in paths)
        partial_members = (
            compatibility_members
            - 2 * compatibility_complete
            + raw_members
            - 2 * len(first_complete)
            - 3 * len(final_complete)
        )
        return {
            "authority_completed": sum(path.startswith("authority/") for path in paths),
            "compatibility_completed": compatibility_complete,
            "identity_completed": sum(path.startswith("identity/") for path in paths),
            "metadata_completed": sum(path in {"rows.json", "runtime_identity.json", "summary.json", "journal.json"} for path in paths),
            "partial_current": partial_members,
            "primary_completed": len(first_complete),
            "valid_primary_completed": len(final_complete),
        }

    def _remove_success_sidecars(self) -> None:
        self._reconcile_physical_tree("success evidence")
        for relative in _SUCCESS_SIDECARS:
            path = self.root / relative
            if not path.exists():
                continue
            size = path.stat().st_size
            path.unlink()
            self.files.pop(relative, None)
            self.budget.bytes_used -= size
            self.budget.members_used -= 1

    def terminal(self, failure: V5OperationalFailure) -> dict[str, Any]:
        receipt = {
            "custody_state": self._custody_state(),
            "fault_code": failure.fault_code,
            "failed_step": failure.failed_step,
            "last_completed_step": failure.last_completed_step,
            "ordinal": failure.ordinal,
            "phase": failure.phase,
            "raw_prefix": None if failure.ordinal is None else f"raw/t{failure.ordinal:04d}-",
            "schema_version": "anachron-v5-operational-failure-v2",
            "v4_included_count": 0,
        }
        try:
            self._remove_success_sidecars()
            self.json("failure_receipt.json", receipt)
            inventory = {
                "files": [{"path": path, "sha256": digest} for path, digest in sorted(self.files.items())],
                "schema_version": "anachron-v5-failure-inventory-v2",
                "v4_included_count": 0,
            }
            raw = canonical_json_bytes(inventory)
            self._write("failure_inventory.json", raw)
            self._write("failure_inventory.sha256", f"{sha256_bytes(raw)}  failure_inventory.json\n".encode("ascii"))
            if self.budget.bytes_used > FAILURE_MAX_BYTES or self.budget.members_used > FAILURE_MAX_FILES:
                raise V5OperationalFailure("resource", phase=failure.phase, failed_step=failure.failed_step, last_completed_step=failure.last_completed_step, ordinal=failure.ordinal)
            assert_no_alternate_data_streams(self.root)
            bounded_regular_files(self.root, "failure evidence", maximum_entries=FAILURE_MAX_FILES + 5, maximum_depth=2)
            self._reconcile_physical_tree("failure evidence")
            self.publish(failure)
        except (OSError, V5CustodyError, V5PathError, V5OperationalFailure) as error:
            if (
                self.publication_state == "RENAMED"
                and isinstance(error, V5OperationalFailure)
                and error.fault_code == "publication_durability_unknown"
            ):
                raise
            try:
                self.discard()
            except (OSError, V5CustodyError) as cleanup_error:
                raise V5OperationalFailure(
                    "terminalization_cleanup",
                    phase=failure.phase,
                    failed_step=failure.failed_step,
                    last_completed_step=failure.last_completed_step,
                    ordinal=failure.ordinal,
                ) from cleanup_error
            raise V5OperationalFailure(
                "terminalization",
                phase=failure.phase,
                failed_step=failure.failed_step,
                last_completed_step=failure.last_completed_step,
                ordinal=failure.ordinal,
            ) from error
        return receipt


def _default_transport(
    endpoint: str,
    path: str,
    payload: bytes | None,
    timeout: int,
    *,
    budget: _Budget,
    phase: str,
    step: int,
    completed: int,
    ordinal: int | None,
) -> bytes:
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"} or parsed.port is None:
        raise V5MeasurementError("endpoint must be explicit loopback HTTP")
    request = Request(f"{endpoint.rstrip('/')}{path}", data=payload, headers={"Content-Type": "application/json"} if payload is not None else {}, method="POST" if payload is not None else "GET")
    with urlopen(request, timeout=timeout) as response:
        return budget.capture_response(
            response,
            phase=phase,
            step=step,
            completed=completed,
            ordinal=ordinal,
        )


def _call(transport: Callable[[str, str, bytes | None, int], object], endpoint: str, path: str, payload: bytes | None, budget: _Budget, *, phase: str, step: int, completed: int, ordinal: int | None) -> bytes:
    if transport is _default_transport:
        try:
            return _default_transport(
                endpoint,
                path,
                payload,
                30,
                budget=budget,
                phase=phase,
                step=step,
                completed=completed,
                ordinal=ordinal,
            )
        except V5OperationalFailure:
            raise
        except Exception as error:
            raise V5OperationalFailure(
                "transport",
                phase=phase,
                failed_step=step,
                last_completed_step=completed,
                ordinal=ordinal,
            ) from error
    try:
        raw = transport(endpoint, path, payload, 30)
    except Exception as error:
        raise V5OperationalFailure("transport", phase=phase, failed_step=step, last_completed_step=completed, ordinal=ordinal) from error
    return budget.admit(raw, phase=phase, step=step, completed=completed, ordinal=ordinal)


def _identity(version_raw: bytes, tags_raw: bytes, models: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    try:
        version, tags = _parse_wire(version_raw, "version"), _parse_wire(tags_raw, "tags")
    except V5MeasurementError as error:
        raise V5OperationalFailure("identity", phase="identity", failed_step=2, last_completed_step=0) from error
    expected = {model["name"]: model["digest"] for model in models}
    model_rows = tags.get("models")
    if set(version) != {"version"} or type(model_rows) is not list or len(model_rows) != 2:
        raise V5OperationalFailure("identity", phase="identity", failed_step=2, last_completed_step=0)
    observed = {}
    for row in model_rows:
        if type(row) is not dict or type(row.get("name")) is not str or type(row.get("digest")) is not str or len(row["digest"]) != 64 or row["name"] in observed:
            raise V5OperationalFailure("identity", phase="identity", failed_step=2, last_completed_step=0)
        observed[row["name"]] = row["digest"]
    if version.get("version") != "0.33.2" or observed != expected:
        raise V5OperationalFailure("identity", phase="identity", failed_step=2, last_completed_step=0)
    return {"models": [{"digest": expected[name], "name": name} for name in sorted(expected)], "tags_sha256": sha256_bytes(tags_raw), "version": "0.33.2", "version_sha256": sha256_bytes(version_raw), "v4_included_count": 0}


def _plan(value: object, cards: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    required = {"authority_contract_sha256", "carry_forward_sha256", "compatibility_plan_sha256", "component_sha256", "evidence_output_root", "expected_runtime", "protocol_release", "schedule", "schema_version", "seeds", "v4_included_count", "v5_source_manifest_sha256"}
    if type(value) is not dict or set(value) != required or value.get("schema_version") != "anachron-v5-full-plan-v2" or value.get("v4_included_count") != 0:
        raise V5MeasurementError("full plan differs")
    runtime = value["expected_runtime"]
    if runtime != {"models": list(EXPECTED_RUNTIME_IDENTITY["models"]), "version": EXPECTED_RUNTIME_IDENTITY["version"]}:
        raise V5MeasurementError("full plan expected runtime differs")
    models = runtime["models"]
    if len(models) != 2 or any(type(row) is not dict or set(row) != {"digest", "name"} or type(row["name"]) is not str or type(row["digest"]) is not str or len(row["digest"]) != 64 for row in models):
        raise V5MeasurementError("full plan models differ")
    if value["seeds"] != list(REPETITION_SEEDS) or type(value["evidence_output_root"]) is not str or not value["evidence_output_root"]:
        raise V5MeasurementError("full plan topology differs")
    if type(value["component_sha256"]) is not dict or set(value["component_sha256"]) != {"analyzer", "runner", "wrapper"} or any(type(digest) is not str or len(digest) != 64 for digest in value["component_sha256"].values()):
        raise V5MeasurementError("full plan component identity differs")
    release = value["protocol_release"]
    if type(release) is not dict or set(release) != {"commit", "tag", "tag_object"} or release["tag"] != V5_PROTOCOL_TAG:
        raise V5MeasurementError("full plan release differs")
    schedule = build_schedule(cards, [row["name"] for row in models])
    if value.get("schedule") != schedule:
        raise V5MeasurementError("full plan schedule differs")
    return schedule, models


def _go(value: object, plan: Mapping[str, Any], full_plan_raw: bytes, compatibility_raw: bytes, carry_raw: bytes, source_raw: bytes, authority_raw: bytes, acceptance_raw: bytes, output: Path) -> None:
    required = {"acceptance_matrix_sha256", "analyzer_sha256", "authority_contract_sha256", "authorized_at_utc", "authorized_by", "carry_forward_sha256", "compatibility_plan_sha256", "decision", "expected_runtime", "full_plan_sha256", "kind", "materialization_receipt_sha256", "output_root", "protocol_commit", "protocol_tag", "protocol_tag_object", "runner_sha256", "source_manifest_sha256", "statement", "v4_included_count", "wrapper_sha256"}
    if type(value) is not dict or set(value) != required or value.get("kind") != "anachron-v5-conditional-measurement-authorization" or value.get("decision") != "GO" or value.get("v4_included_count") != 0:
        raise V5MeasurementError("conditional GO differs")
    expected = {
        "acceptance_matrix_sha256": sha256_bytes(acceptance_raw),
        "analyzer_sha256": plan["component_sha256"]["analyzer"],
        "authority_contract_sha256": sha256_bytes(authority_raw),
        "carry_forward_sha256": sha256_bytes(carry_raw),
        "compatibility_plan_sha256": sha256_bytes(compatibility_raw),
        "expected_runtime": plan["expected_runtime"],
        "full_plan_sha256": sha256_bytes(full_plan_raw),
        "output_root": str(Path(output).resolve()),
        "protocol_commit": plan["protocol_release"]["commit"],
        "protocol_tag": plan["protocol_release"]["tag"],
        "protocol_tag_object": plan["protocol_release"]["tag_object"],
        "runner_sha256": plan["component_sha256"]["runner"],
        "source_manifest_sha256": sha256_bytes(source_raw),
        "wrapper_sha256": plan["component_sha256"]["wrapper"],
    }
    if any(value[key] != expected_value for key, expected_value in expected.items()) or any(type(value[key]) is not str or len(value[key]) != 64 for key in ("materialization_receipt_sha256",)) or type(value["authorized_at_utc"]) is not str or type(value["authorized_by"]) is not str or not value["authorized_by"].strip() or type(value["statement"]) is not str or not value["statement"].strip():
        raise V5MeasurementError("conditional GO binding differs")


def validate_run_inputs(full_plan: Path, conditional_go: Path, output: Path, *, repository_root: Path) -> dict[str, Any]:
    """Validate the GO-bound offline closure without opening a network connection."""

    try:
        root = Path(repository_root)
        output = admit_create_only_external_output(output, root, "evidence output")
        full_plan_raw = capture_regular(full_plan, "full plan", _MAX_AUTHORITY_BYTES).raw
        go_raw = capture_regular(conditional_go, "conditional GO", _MAX_AUTHORITY_BYTES).raw
        plan = strict_json_loads(full_plan_raw, "full plan")
        go = strict_json_loads(go_raw, "conditional GO")
        plan_root = full_plan.parent
        compatibility_raw = capture_regular(plan_root / "compatibility_plan.json", "compatibility plan", _MAX_AUTHORITY_BYTES).raw
        carry_raw = capture_regular(plan_root / "carry_forward.json", "carry-forward receipt", _MAX_AUTHORITY_BYTES).raw
        source_raw = capture_regular(plan_root / "source_manifest.json", "source manifest", _MAX_AUTHORITY_BYTES).raw
        snapshot = _validate_scientific_source_manifest(root, source_raw)
        _, cards = load_v5_registry_snapshot(snapshot)
        schedule, models = _plan(plan, cards)
        runtime_raw = capture_regular(plan_root / "runtime_identity.json", "runtime identity", _MAX_AUTHORITY_BYTES).raw
        materialization_raw = capture_regular(plan_root / "materialization_receipt.json", "materialization receipt", _MAX_AUTHORITY_BYTES).raw
        authority_raw = capture_regular(root / AUTHORITY_CONTRACT_PATH, "authority contract", _MAX_AUTHORITY_BYTES).raw
        acceptance_raw = capture_regular(root / "research/v5_measurement/ACCEPTANCE_MATRIX.md", "acceptance matrix", _MAX_AUTHORITY_BYTES).raw
        if (
            plan["authority_contract_sha256"] != sha256_bytes(authority_raw)
            or plan["carry_forward_sha256"] != sha256_bytes(carry_raw)
            or plan["compatibility_plan_sha256"] != sha256_bytes(compatibility_raw)
            or plan["v5_source_manifest_sha256"] != sha256_bytes(source_raw)
            or plan["evidence_output_root"] != str(output)
        ):
            raise V5MeasurementError("full plan input binding differs")
        materialization = strict_json_loads(materialization_raw, "materialization receipt")
        runtime = strict_json_loads(runtime_raw, "runtime identity")
        expected_materialization = {
            "authority_contract_sha256": sha256_bytes(authority_raw),
            "carry_forward_sha256": sha256_bytes(carry_raw),
            "compatibility_plan_sha256": sha256_bytes(compatibility_raw),
            "full_plan_sha256": sha256_bytes(full_plan_raw),
            "runtime_identity_sha256": materialization.get("runtime_identity_sha256") if type(materialization) is dict else None,
            "schedule_sha256": materialization.get("schedule_sha256") if type(materialization) is dict else None,
            "schema_version": "anachron-v5-materialization-receipt-v2",
            "v4_included_count": 0,
            "v5_source_manifest_sha256": sha256_bytes(source_raw),
        }
        if runtime != plan["expected_runtime"] or materialization != expected_materialization or go["materialization_receipt_sha256"] != sha256_bytes(materialization_raw):
            raise V5MeasurementError("materialization receipt binding differs")
        _go(go, plan, full_plan_raw, compatibility_raw, carry_raw, source_raw, authority_raw, acceptance_raw, output)
        compatibility_card = load_compatibility_case_snapshot(snapshot, cards)
    except V5CustodyError as error:
        raise V5MeasurementError(str(error)) from error
    except (OSError, V5PathError, V5RegistryError) as error:
        raise V5MeasurementError("runner inputs differ") from error
    return {
        "cards": cards,
        "compatibility_card": compatibility_card,
        "compatibility_raw": compatibility_raw,
        "full_plan_raw": full_plan_raw,
        "go_raw": go_raw,
        "materialization_raw": materialization_raw,
        "runtime_raw": runtime_raw,
        "plan": plan,
        "source_raw": source_raw,
        "authority_raw": authority_raw,
        "acceptance_raw": acceptance_raw,
        "carry_raw": carry_raw,
        "models": models,
        "schedule": schedule,
    }


def run_measurement(full_plan: Path, conditional_go: Path, output: Path, *, repository_root: Path, endpoint: str = _FIXED_ENDPOINT, transport: Callable[[str, str, bytes | None, int], object] | None = None) -> dict[str, Any]:
    """Execute the one-shot campaign. Callers must supply a fresh accepted GO."""

    if endpoint != _FIXED_ENDPOINT:
        raise V5MeasurementError("endpoint differs from fixed loopback endpoint")
    inputs = validate_run_inputs(full_plan, conditional_go, output, repository_root=repository_root)
    writer, budget, completed = _Writer(output), _Budget(), 0
    caller = transport or _default_transport
    try:
        writer._write("authority/full-plan.json", inputs["full_plan_raw"])
        writer._write("authority/conditional-go.json", inputs["go_raw"])
        writer._write("authority/compatibility-plan.json", inputs["compatibility_raw"])
        writer._write("authority/carry-forward.json", inputs["carry_raw"])
        writer._write("authority/source-manifest.json", inputs["source_raw"])
        writer._write("authority/materialization-receipt.json", inputs["materialization_raw"])
        writer._write("authority/runtime-identity.json", inputs["runtime_raw"])
        writer._write("authority/authority-contract.json", inputs["authority_raw"])
        writer._write("authority/acceptance-matrix.md", inputs["acceptance_raw"])
        version_raw = _call(caller, endpoint, "/api/version", None, budget, phase="identity", step=1, completed=0, ordinal=None)
        tags_raw = _call(caller, endpoint, "/api/tags", None, budget, phase="identity", step=2, completed=0, ordinal=None)
        identity = _identity(version_raw, tags_raw, inputs["models"])
        writer._write("identity/version.wire", version_raw)
        writer._write("identity/tags.wire", tags_raw)
        writer.journal({"kind": "identity_complete", "tags_sha256": identity["tags_sha256"], "version_sha256": identity["version_sha256"]})
        for ordinal, model in enumerate(inputs["models"], 1):
            request = canonical_json_bytes(first_request({"model": model["name"], "seed": REPETITION_SEEDS[ordinal - 1]}, inputs["compatibility_card"]))
            writer._write(f"compatibility/raw/t{ordinal:04d}-first-request.json", request)
            response = _call(caller, endpoint, "/api/chat", request, budget, phase="compatibility", step=completed + 1, completed=completed, ordinal=None)
            writer._write(f"compatibility/raw/t{ordinal:04d}-first-response.wire", response)
            _parse_wire(response, "compatibility response")
            completed += 1
            writer.journal({"kind": "compatibility_complete", "ordinal": ordinal})
        pre_primary_version = _call(caller, endpoint, "/api/version", None, budget, phase="identity", step=completed + 1, completed=completed, ordinal=None)
        pre_primary_tags = _call(caller, endpoint, "/api/tags", None, budget, phase="identity", step=completed + 2, completed=completed, ordinal=None)
        if _identity(pre_primary_version, pre_primary_tags, inputs["models"]) != identity:
            raise V5OperationalFailure("identity", phase="identity", failed_step=completed + 2, last_completed_step=completed)
        writer._write("identity/pre-primary-version.wire", pre_primary_version)
        writer._write("identity/pre-primary-tags.wire", pre_primary_tags)
        writer.journal({"kind": "pre_primary_identity_complete", "tags_sha256": sha256_bytes(pre_primary_tags), "version_sha256": sha256_bytes(pre_primary_version)})
        rows = []
        for trajectory in inputs["schedule"]:
            ordinal, card = trajectory["ordinal"], inputs["cards"][trajectory["case_id"]]
            request = canonical_json_bytes(first_request(trajectory, card))
            writer.raw(ordinal, "first-request", request)
            raw = _call(caller, endpoint, "/api/chat", request, budget, phase="primary", step=completed + 1, completed=completed, ordinal=ordinal)
            writer.raw(ordinal, "first-response", raw)
            try:
                first = classify_first_response(_parse_wire(raw, "first response"), card)
            except V5MeasurementError:
                first = {"category": "first_native_response_invalid", "tool": None}
            row = {**trajectory, "exposure": None, "final_category": None, "first_category": first["category"]}
            completed += 1
            writer.journal({"first_category": row["first_category"], "kind": "first_complete", "ordinal": ordinal})
            if first["category"] == "valid":
                records = eligible_records(dict(card), trajectory["mode"], first["tool"]["query"])
                row["exposure"] = any(record["publish_date"] > card["as_of"] for record in records)
                writer.raw(ordinal, "tool-result", canonical_json_bytes({"records": records}))
                final = canonical_json_bytes(final_request(trajectory, card, records))
                writer.raw(ordinal, "final-request", final)
                final_raw = _call(caller, endpoint, "/api/chat", final, budget, phase="primary", step=completed + 1, completed=completed, ordinal=ordinal)
                writer.raw(ordinal, "final-response", final_raw)
                try:
                    row["final_category"] = classify_final_response(_parse_wire(final_raw, "final response"))
                except V5MeasurementError:
                    row["final_category"] = "final_native_response_invalid"
                completed += 1
                writer.journal({"final_category": row["final_category"], "kind": "final_complete", "ordinal": ordinal})
            rows.append(row)
        post_primary_version = _call(caller, endpoint, "/api/version", None, budget, phase="identity", step=completed + 1, completed=completed, ordinal=None)
        post_primary_tags = _call(caller, endpoint, "/api/tags", None, budget, phase="identity", step=completed + 2, completed=completed, ordinal=None)
        if _identity(post_primary_version, post_primary_tags, inputs["models"]) != identity:
            raise V5OperationalFailure("identity", phase="identity", failed_step=completed + 2, last_completed_step=completed)
        writer._write("identity/post-primary-version.wire", post_primary_version)
        writer._write("identity/post-primary-tags.wire", post_primary_tags)
        writer.journal({"kind": "post_primary_identity_complete", "tags_sha256": sha256_bytes(post_primary_tags), "version_sha256": sha256_bytes(post_primary_version)})
        summary = summarize(rows)
        manifest = writer.seal(rows, identity, summary)
        return {"manifest": manifest, "rows": rows, "summary": summary}
    except V5OperationalFailure as error:
        if writer.publication_state == "RENAMED":
            raise
        try:
            writer.terminal(error)
        except V5OperationalFailure as terminal_error:
            raise terminal_error from error
        raise


def _pairs(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    pairs: dict[tuple[str, str, int], dict[str, Mapping[str, Any]]] = {}
    for row in rows:
        pairs.setdefault((row["case_id"], row["model"], row["repetition"]), {})[row["mode"]] = row
    return [{"case_id": key[0], "exposure_delta_unrestricted_minus_enforced": int(bool(pair["unrestricted"]["exposure"])) - int(bool(pair["enforced"]["exposure"])), "model": key[1], "repetition": key[2]} for key, pair in sorted(pairs.items()) if set(pair) == set(MODES) and pair["unrestricted"]["first_category"] == pair["enforced"]["first_category"] == "valid"]


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = groups.setdefault(f"{row['model']}::{row['mode']}", {"adherent": 0, "conditional_exposure_denominator": 0, "conditional_exposure_numerator": 0, "final_categories": {key: 0 for key in FINAL_CATEGORIES}, "first_categories": {key: 0 for key in FIRST_CATEGORIES}, "scheduled": 0})
        group["scheduled"] += 1
        group["first_categories"][row["first_category"]] += 1
        if row["first_category"] == "valid":
            group["adherent"] += 1
            group["conditional_exposure_denominator"] += 1
            group["conditional_exposure_numerator"] += int(bool(row["exposure"]))
            if row["final_category"] is not None:
                group["final_categories"][row["final_category"]] += 1
    for group in groups.values():
        group["adherence_rate"] = group["adherent"] / group["scheduled"]
        denominator = group["conditional_exposure_denominator"]
        group["conditional_exposure_rate"] = None if not denominator else group["conditional_exposure_numerator"] / denominator
    return {"groups": groups, "paired_contrasts": _pairs(rows), "scheduled": len(rows), "v4_included_count": 0}


def _success_paths(rows: object) -> tuple[str, ...]:
    if type(rows) is not list or len(rows) != _PRIMARY_TRAJECTORY_COUNT:
        raise V5MeasurementError("success evidence row topology differs")
    paths = set(_AUTHORITY_PATHS) | set(_IDENTITY_PATHS) | set(_METADATA_PATHS)
    paths.update(
        f"compatibility/raw/t{ordinal:04d}-{role}"
        for ordinal in (1, 2)
        for role in ("first-request.json", "first-response.wire")
    )
    valid = 0
    for expected_ordinal, row in enumerate(rows, 1):
        if type(row) is not dict or row.get("ordinal") != expected_ordinal:
            raise V5MeasurementError("success evidence row topology differs")
        paths.update(
            {
                f"raw/t{expected_ordinal:04d}-first-request.json",
                f"raw/t{expected_ordinal:04d}-first-response.json",
            }
        )
        if row.get("first_category") == "valid":
            valid += 1
            paths.update(
                {
                    f"raw/t{expected_ordinal:04d}-tool-result.json",
                    f"raw/t{expected_ordinal:04d}-final-request.json",
                    f"raw/t{expected_ordinal:04d}-final-response.json",
                }
            )
    if len(paths) != 151 + 3 * valid:
        raise V5MeasurementError("success evidence topology differs")
    return tuple(sorted(paths))


def _manifest(evidence: Path) -> dict[str, Any]:
    try:
        assert_no_alternate_data_streams(evidence)
        raw = bounded_evidence_bytes(evidence, "manifest.json", "manifest")
        digest = bounded_evidence_bytes(evidence, "manifest.sha256", "manifest digest")
        physical = physical_inventory(
            evidence,
            "success evidence",
            maximum_entries=SUCCESS_MAX_ENTRIES,
            maximum_depth=2,
            byte_limit=SUCCESS_MAX_BYTES,
            member_cap=evidence_file_cap,
        )
        actual = tuple(
            member.path
            for member in physical.files
            if member.path not in {"manifest.json", "manifest.sha256"}
        )
    except (OSError, V5CustodyError, V5PathError) as error:
        if "byte cap" in str(error):
            raise V5MeasurementError("manifest replay byte cap differs") from error
        raise V5MeasurementError("manifest unavailable") from error
    if digest != f"{sha256_bytes(raw)}  manifest.json\n".encode("ascii"):
        raise V5MeasurementError("manifest digest differs")
    value = _parse_wire(raw, "manifest")
    if set(value) != {"files", "row_count", "schema_version", "v4_included_count"} or value["schema_version"] != "anachron-v5-evidence-manifest-v2" or value["v4_included_count"] != 0 or type(value["files"]) is not list:
        raise V5MeasurementError("manifest differs")
    rows = strict_json_loads(bounded_evidence_bytes(evidence, "rows.json", "rows"), "rows")
    expected = _success_paths(rows)
    expected_directories = _expected_evidence_directories(set(expected) | set(_SUCCESS_SIDECARS))
    if actual != expected or physical.directories != expected_directories:
        raise V5MeasurementError("manifest inventory differs")
    if [row.get("path") if type(row) is dict else None for row in value["files"]] != list(actual):
        raise V5MeasurementError("manifest inventory differs")
    total = 0
    for row in value["files"]:
        if type(row) is not dict or set(row) != {"path", "sha256"} or type(row["path"]) is not str or type(row["sha256"]) is not str:
            raise V5MeasurementError("manifest bytes differ")
        raw = bounded_evidence_bytes(evidence, row["path"], "manifest-listed evidence", expected_sha256=row["sha256"])
        total += len(raw)
        if total > _MAX_REPLAY_EVIDENCE_BYTES:
            raise V5MeasurementError("manifest bytes differ")
    return value


def analyze_measurement(evidence: Path, *, repository_root: Path) -> dict[str, Any]:
    manifest = _manifest(evidence)
    try:
        snapshot = _validate_scientific_source_manifest(
            repository_root,
            bounded_evidence_bytes(evidence, "authority/source-manifest.json", "embedded source manifest"),
        )
        _, cards = load_v5_registry_snapshot(snapshot)
        rows = strict_json_loads(bounded_evidence_bytes(evidence, "rows.json", "rows"), "rows")
        identity = strict_json_loads(bounded_evidence_bytes(evidence, "runtime_identity.json", "identity"), "identity")
        summary = strict_json_loads(bounded_evidence_bytes(evidence, "summary.json", "summary"), "summary")
    except (OSError, V5RegistryError) as error:
        raise V5MeasurementError("replay input differs") from error
    if type(rows) is not list or len(rows) != 64 or manifest["row_count"] != 64 or type(identity) is not dict:
        raise V5MeasurementError("replay cardinality differs")
    models = [row["name"] for row in identity.get("models", []) if type(row) is dict and type(row.get("name")) is str]
    schedule = build_schedule(cards, models)
    expected_models = identity.get("models")
    if type(expected_models) is not list:
        raise V5MeasurementError("replay identity differs")
    checkpoints = (("identity/version.wire", "identity/tags.wire"), ("identity/pre-primary-version.wire", "identity/pre-primary-tags.wire"), ("identity/post-primary-version.wire", "identity/post-primary-tags.wire"))
    for version_relative, tags_relative in checkpoints:
        checkpoint = _identity(bounded_evidence_bytes(evidence, version_relative, "identity version"), bounded_evidence_bytes(evidence, tags_relative, "identity tags"), expected_models)
        if checkpoint != identity:
            raise V5MeasurementError("replay identity checkpoint differs")
    compatibility_card = load_compatibility_case_snapshot(snapshot, cards)
    for ordinal, model in enumerate(expected_models, 1):
        expected = canonical_json_bytes(first_request({"model": model["name"], "seed": REPETITION_SEEDS[ordinal - 1]}, compatibility_card))
        if bounded_evidence_bytes(evidence, f"compatibility/raw/t{ordinal:04d}-first-request.json", "compatibility request") != expected:
            raise V5MeasurementError("replay compatibility request differs")
        _parse_wire(bounded_evidence_bytes(evidence, f"compatibility/raw/t{ordinal:04d}-first-response.wire", "compatibility response"), "compatibility response")
    if [{key: row[key] for key in schedule[0]} for row in rows] != schedule:
        raise V5MeasurementError("replay schedule differs")
    for row in rows:
        ordinal, card = row["ordinal"], cards[row["case_id"]]
        if bounded_evidence_bytes(evidence, f"raw/t{ordinal:04d}-first-request.json", "first request") != canonical_json_bytes(first_request(row, card)):
            raise V5MeasurementError("replay first request differs")
        try:
            first = classify_first_response(_parse_wire(bounded_evidence_bytes(evidence, f"raw/t{ordinal:04d}-first-response.json", "first response"), "first"), card)
        except V5MeasurementError:
            first = {"category": "first_native_response_invalid", "tool": None}
        if first["category"] != row["first_category"]:
            raise V5MeasurementError("replay first category differs")
        if first["category"] != "valid":
            if row["exposure"] is not None or row["final_category"] is not None:
                raise V5MeasurementError("replay terminal outcome differs")
            continue
        records = eligible_records(dict(card), row["mode"], first["tool"]["query"])
        if bounded_evidence_bytes(evidence, f"raw/t{ordinal:04d}-tool-result.json", "tool result") != canonical_json_bytes({"records": records}):
            raise V5MeasurementError("replay tool result differs")
        if row["exposure"] != any(record["publish_date"] > card["as_of"] for record in records):
            raise V5MeasurementError("replay exposure differs")
        if bounded_evidence_bytes(evidence, f"raw/t{ordinal:04d}-final-request.json", "final request") != canonical_json_bytes(final_request(row, card, records)):
            raise V5MeasurementError("replay final request differs")
        try:
            final_category = classify_final_response(_parse_wire(bounded_evidence_bytes(evidence, f"raw/t{ordinal:04d}-final-response.json", "final response"), "final"))
        except V5MeasurementError:
            final_category = "final_native_response_invalid"
        if row["final_category"] != final_category:
            raise V5MeasurementError("replay final category differs")
    if summary != summarize(rows):
        raise V5MeasurementError("replay summary differs")
    return summary


def analyze_failure(evidence: Path) -> dict[str, Any]:
    try:
        assert_no_alternate_data_streams(evidence)
        receipt = _parse_wire(bounded_evidence_bytes(evidence, "failure_receipt.json", "failure receipt"), "failure receipt")
        inventory_raw = bounded_evidence_bytes(evidence, "failure_inventory.json", "failure inventory")
        inventory = _parse_wire(inventory_raw, "failure inventory")
        inventory_digest = bounded_evidence_bytes(evidence, "failure_inventory.sha256", "failure inventory digest")
        physical = physical_inventory(
            evidence,
            "failure evidence",
            maximum_entries=FAILURE_MAX_FILES + 5,
            maximum_depth=2,
            byte_limit=FAILURE_MAX_BYTES,
            member_cap=evidence_file_cap,
        )
        all_paths = tuple(member.path for member in physical.files)
        if any(path in _SUCCESS_SIDECARS for path in all_paths):
            raise V5MeasurementError("failure evidence has success sidecars")
        actual = tuple(path for path in all_paths if path not in {"failure_inventory.json", "failure_inventory.sha256"})
    except (OSError, V5CustodyError, V5PathError) as error:
        raise V5MeasurementError("failure replay differs") from error
    if inventory_digest != f"{sha256_bytes(inventory_raw)}  failure_inventory.json\n".encode("ascii"):
        raise V5MeasurementError("failure inventory digest differs")
    state = receipt.get("custody_state")
    state_fields = {
        "authority_completed",
        "compatibility_completed",
        "identity_completed",
        "metadata_completed",
        "partial_current",
        "primary_completed",
        "valid_primary_completed",
    }
    if (
        receipt.get("schema_version") != "anachron-v5-operational-failure-v2"
        or receipt.get("v4_included_count") != 0
        or type(receipt.get("failed_step")) is not int
        or type(receipt.get("last_completed_step")) is not int
        or receipt["failed_step"] < receipt["last_completed_step"]
        or receipt.get("raw_prefix") != (None if receipt.get("ordinal") is None else f"raw/t{receipt['ordinal']:04d}-")
        or type(state) is not dict
        or set(state) != state_fields
        or any(type(value) is not int or value < 0 for value in state.values())
        or state["authority_completed"] > _AUTHORITY_FILE_COUNT
        or state["compatibility_completed"] > _COMPATIBILITY_TRACE_COUNT
        or state["identity_completed"] > 6
        or state["metadata_completed"] > _REPLAY_METADATA_FILE_COUNT
        or state["primary_completed"] > _PRIMARY_TRAJECTORY_COUNT
        or state["valid_primary_completed"] > state["primary_completed"]
        or inventory.get("schema_version") != "anachron-v5-failure-inventory-v2"
        or inventory.get("v4_included_count") != 0
        or type(inventory.get("files")) is not list
    ):
        raise V5MeasurementError("failure receipt differs")
    expected = _failure_expected_paths(evidence, receipt, state)
    expected_directories = _expected_evidence_directories(set(all_paths))
    if (
        physical.directories != expected_directories
        or [row.get("path") if type(row) is dict else None for row in inventory["files"]] != list(actual)
        or set(actual) != expected
    ):
        raise V5MeasurementError("failure inventory topology differs")
    for row in inventory["files"]:
        if type(row) is not dict or set(row) != {"path", "sha256"} or type(row["path"]) is not str or type(row["sha256"]) is not str:
            raise V5MeasurementError("failure inventory differs")
        bounded_evidence_bytes(evidence, row["path"], "failure inventory evidence", expected_sha256=row["sha256"])
    return receipt


def _has_structural_tool_call(raw: bytes) -> bool:
    try:
        value = _parse_wire(raw, "failure first response")
        message = value.get("message")
        calls = message.get("tool_calls") if type(message) is dict else None
        return type(calls) is list and len(calls) == 1 and type(calls[0]) is dict
    except V5MeasurementError:
        return False


def _failure_expected_paths(evidence: Path, receipt: dict[str, Any], state: dict[str, int]) -> set[str]:
    """Derive the sole producer-prefix topology for one failure receipt."""

    expected = set(_AUTHORITY_WRITE_PATHS[: state["authority_completed"]])
    expected.update(_IDENTITY_WRITE_PATHS[: state["identity_completed"]])
    for ordinal in range(1, state["compatibility_completed"] + 1):
        expected.update(
            {
                f"compatibility/raw/t{ordinal:04d}-first-request.json",
                f"compatibility/raw/t{ordinal:04d}-first-response.wire",
            }
        )
    full_primary = 0
    for ordinal in range(1, state["primary_completed"] + 1):
        request = f"raw/t{ordinal:04d}-first-request.json"
        response = f"raw/t{ordinal:04d}-first-response.json"
        expected.update({request, response})
        suffix = {
            f"raw/t{ordinal:04d}-tool-result.json",
            f"raw/t{ordinal:04d}-final-request.json",
            f"raw/t{ordinal:04d}-final-response.json",
        }
        present = suffix & set(
            bounded_regular_files(
                evidence,
                "failure evidence",
                maximum_entries=FAILURE_MAX_FILES + 5,
                maximum_depth=2,
            )
        )
        if present == suffix:
            if not _has_structural_tool_call(
                bounded_evidence_bytes(evidence, response, "failure first response")
            ):
                raise V5MeasurementError("failure primary suffix topology differs")
            expected.update(suffix)
            full_primary += 1
        elif present:
            tool_result = f"raw/t{ordinal:04d}-tool-result.json"
            final_request_path = f"raw/t{ordinal:04d}-final-request.json"
            if (
                receipt.get("phase") != "primary"
                or receipt.get("ordinal") != ordinal
                or present not in ({tool_result}, {tool_result, final_request_path})
            ):
                raise V5MeasurementError("failure primary suffix topology differs")
    if full_primary != state["valid_primary_completed"]:
        raise V5MeasurementError("failure valid-primary topology differs")
    expected.update(_METADATA_WRITE_PATHS[: state["metadata_completed"]])
    expected.add("failure_receipt.json")
    phase, ordinal, partial = receipt.get("phase"), receipt.get("ordinal"), state["partial_current"]
    extras: tuple[str, ...]
    if phase == "compatibility":
        if ordinal is not None or state["compatibility_completed"] >= _COMPATIBILITY_TRACE_COUNT:
            raise V5MeasurementError("failure compatibility receipt differs")
        extras = (f"compatibility/raw/t{state['compatibility_completed'] + 1:04d}-first-request.json",)
    elif phase == "primary":
        if type(ordinal) is not int or not 1 <= ordinal <= _PRIMARY_TRAJECTORY_COUNT:
            raise V5MeasurementError("failure primary receipt differs")
        if ordinal == state["primary_completed"] + 1:
            extras = (f"raw/t{ordinal:04d}-first-request.json",)
        elif ordinal <= state["primary_completed"]:
            extras = (
                f"raw/t{ordinal:04d}-tool-result.json",
                f"raw/t{ordinal:04d}-final-request.json",
            )
        else:
            raise V5MeasurementError("failure primary ordinal differs")
    else:
        if ordinal is not None:
            raise V5MeasurementError("failure non-primary ordinal differs")
        extras = ()
    if partial > len(extras):
        raise V5MeasurementError("failure partial topology differs")
    expected.update(extras[:partial])
    return expected
