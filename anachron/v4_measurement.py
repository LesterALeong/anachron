"""Create and replay local v4 evidence; only ``run_measurement`` may chat."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from anachron.data.v4_registry import (
    REGISTRY_PATH,
    canonical_json_bytes,
    eligible_records,
    load_compatibility_case,
    load_v4_registry,
    sha256_bytes,
    strict_json_loads,
)
from anachron.v4_comparison import V4ComparisonError, derive_bytes
from anachron.v4_contract import (
    V4_GOVERNED_SOURCE_PATHS,
    V4ContractError,
    _admit_repository_root,
    _read_file,
    validate_authority_contract,
)
from anachron.v4_paths import (
    V4PathError,
    admit_create_only_external_output,
    admit_evidence_directory,
    admit_evidence_regular_file,
    admit_evidence_root,
    admit_external_regular_input,
)
from tools.build_v4_source_manifest import V4SourceManifestError
from tools.build_v4_source_manifest import derive as derive_source_manifest
from tools.build_v4_source_manifest import validate as validate_source_manifest

_HEX = re.compile(r"^[0-9a-f]{64}$")
_ISO = re.compile(
    r"^(?P<d>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?P<f>\.\d+)?(?P<o>Z|[+-]\d{2}:\d{2})$"
)
_TOOL = "anachron_search"
_MODES = ("unrestricted", "enforced")
_SOURCE_MANIFEST_SCHEMA = "anachron-v4-source-manifest-v1"
_RUNTIME_IDENTITY_SCHEMA = "anachron-v4-runtime-identity-v3"
_MAX_RESPONSE_BYTES = 1_048_576
_MAX_CAMPAIGN_RESPONSE_BYTES = 8_388_608
_RESPONSE_PREFIX_BYTES = 4_096


class V4MeasurementError(ValueError):
    """Raised for invalid v4 inputs, evidence, or native responses."""


class _TraceFailure(V4MeasurementError):
    """A fail-closed scientific-chat failure with an auditable position."""

    def __init__(
        self,
        *,
        phase: str,
        failed_step: int,
        last_completed_step: int,
        fault_code: str,
        trajectory_id: str | None,
        failure_stage: str,
        failure_cause: str | None = None,
        raw_response_state: str = "absent",
        resource_limit_class: str | None = None,
        retained_prefix_bytes: int | None = None,
        response_size_lower_bound: int | None = None,
        campaign_response_bytes_before: int | None = None,
        observed_response_bytes: int | None = None,
    ) -> None:
        super().__init__(fault_code)
        self.phase = phase
        self.failed_step = failed_step
        self.last_completed_step = last_completed_step
        self.fault_code = fault_code
        self.trajectory_id = trajectory_id
        self.failure_stage = failure_stage
        self.failure_cause = failure_cause
        self.raw_response_state = raw_response_state
        self.resource_limit_class = resource_limit_class
        self.retained_prefix_bytes = retained_prefix_bytes
        self.response_size_lower_bound = response_size_lower_bound
        self.campaign_response_bytes_before = campaign_response_bytes_before
        self.observed_response_bytes = observed_response_bytes


class _ResponseLimitError(V4MeasurementError):
    """Raised after retaining only a bounded prefix of an oversized response."""

    def __init__(
        self,
        prefix: bytes,
        *,
        limit_class: str,
        response_size_lower_bound: int | None = None,
        campaign_response_bytes_before: int | None = None,
        observed_response_bytes: int | None = None,
    ) -> None:
        super().__init__("resource")
        self.prefix = prefix
        self.limit_class = limit_class
        self.response_size_lower_bound = response_size_lower_bound
        self.campaign_response_bytes_before = campaign_response_bytes_before
        self.observed_response_bytes = observed_response_bytes


class _ResponseBudget:
    """Apply response and campaign byte limits to every transport result."""

    def __init__(self) -> None:
        self.total = 0

    def admit(self, raw: object) -> bytes:
        if type(raw) is not bytes:
            raise V4MeasurementError("transport response type differs")
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise _ResponseLimitError(
                raw[:_RESPONSE_PREFIX_BYTES],
                limit_class="per_response",
                response_size_lower_bound=_MAX_RESPONSE_BYTES + 1,
            )
        if self.total + len(raw) > _MAX_CAMPAIGN_RESPONSE_BYTES:
            raise _ResponseLimitError(
                raw[:_RESPONSE_PREFIX_BYTES],
                limit_class="campaign",
                campaign_response_bytes_before=self.total,
                observed_response_bytes=len(raw),
            )
        self.total += len(raw)
        return raw


_FAULT_CODES = frozenset(
    {"identity", "native_envelope", "protocol", "resource", "transport"}
)
_FAILURE_STAGES = frozenset(
    {
        "identity_before_compatibility_version",
        "identity_before_compatibility_tags",
        "identity_between_phases_version",
        "identity_between_phases_tags",
        "identity_after_main_version",
        "identity_after_main_tags",
        "first_chat_transport",
        "first_chat_resource",
        "first_chat_envelope",
        "final_chat_transport",
        "final_chat_resource",
        "final_chat_envelope",
        "protocol",
    }
)


def _type(value: object, wanted: type, label: str) -> Any:
    if type(value) is not wanted:
        raise V4MeasurementError(f"{label} has wrong JSON type")
    return value


def _map(value: object, keys: set[str], label: str) -> dict[str, Any]:
    value = _type(value, dict, label)
    if set(value) != keys:
        raise V4MeasurementError(f"{label} has unexpected or missing keys")
    return value


def _str(value: object, label: str) -> str:
    value = _type(value, str, label)
    if not value:
        raise V4MeasurementError(f"{label} must be nonempty")
    return value


def _sha(value: object, label: str) -> str:
    value = _str(value, label)
    if _HEX.fullmatch(value) is None:
        raise V4MeasurementError(f"{label} is not SHA-256")
    return value


def _models(value: object, label: str) -> list[dict[str, str]]:
    rows = _type(value, list, label)
    if len(rows) != 2:
        raise V4MeasurementError(f"{label} count differs")
    models = []
    for index, row in enumerate(rows):
        row = _map(row, {"digest", "name"}, f"{label}[{index}]")
        models.append(
            {
                "digest": _sha(row["digest"], f"{label}[{index}].digest"),
                "name": _str(row["name"], f"{label}[{index}].name"),
            }
        )
    if models != sorted(models, key=lambda item: item["name"]):
        raise V4MeasurementError(f"{label} order differs")
    if len({item["name"] for item in models}) != 2:
        raise V4MeasurementError(f"{label} identities differ")
    return models


def _utc(value: object, label: str, *, z: bool = True) -> str:
    value = _str(value, label)
    match = _ISO.fullmatch(value)
    if match is None or (z and match["o"] != "Z"):
        raise V4MeasurementError(f"{label} is not ISO-8601")
    if match["f"] is not None and not 1 <= len(match["f"]) - 1 <= 9:
        raise V4MeasurementError(f"{label} is not ISO-8601")
    fraction = "" if match["f"] is None else f".{match['f'][1:7]}"
    try:
        datetime.fromisoformat(
            f"{match['d']}{fraction}{'+00:00' if match['o'] == 'Z' else match['o']}"
        )
    except ValueError as error:
        raise V4MeasurementError(f"{label} is not ISO-8601") from error
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _obj(raw: bytes, label: str, canonical: bool = True) -> dict[str, Any]:
    value = _type(strict_json_loads(raw, label), dict, label)
    if canonical and raw != canonical_json_bytes(value):
        raise V4MeasurementError(f"{label} is not canonical JSON")
    return value


def _load(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    _admit_regular_file(path, label)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise V4MeasurementError(f"{label} cannot be read") from error
    return _obj(raw, label), raw


def _repo(root: Path, path: str, label: str) -> bytes:
    try:
        return _read_file(root, path, label)
    except V4ContractError as error:
        raise V4MeasurementError(str(error)) from error


def _path_components(path: Path) -> tuple[Path, ...]:
    current = Path(path.anchor)
    components = [current]
    for part in path.parts[1:]:
        current /= part
        components.append(current)
    return tuple(components)


def _admit_regular_file(path: Path, label: str) -> Path:
    candidate = Path(os.path.abspath(path))
    try:
        for component in _path_components(candidate):
            metadata = component.lstat()
            attributes = getattr(metadata, "st_file_attributes", 0)
            if stat.S_ISLNK(metadata.st_mode) or attributes & 0x400:
                raise V4MeasurementError(f"{label} has symlink or reparse component")
    except FileNotFoundError as error:
        raise V4MeasurementError(f"{label} cannot be read") from error
    except OSError as error:
        raise V4MeasurementError(f"{label} cannot be admitted") from error
    if not candidate.is_file():
        raise V4MeasurementError(f"{label} must be a regular file")
    return candidate


class _CampaignState:
    def __init__(self) -> None:
        self.attempted_chats = 0
        self.completed_chats = 0


class _Writer:
    def __init__(
        self,
        root: Path,
        repository_root: Path,
        label: str,
        state: _CampaignState | None = None,
    ):
        try:
            self.root = admit_create_only_external_output(root, repository_root, label)
        except V4PathError as error:
            raise V4MeasurementError(str(error)) from error
        self.root.mkdir()
        self.repository_root = repository_root
        self.state = state or _CampaignState()

    def _directory(self, path: Path) -> None:
        if path.exists():
            return
        try:
            destination = admit_create_only_external_output(
                path, self.repository_root, "evidence directory"
            )
        except V4PathError as error:
            raise V4MeasurementError(str(error)) from error
        destination.mkdir()

    def bytes(self, relative: str, raw: bytes) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise V4MeasurementError("evidence path differs")
        path = self.root / candidate
        self._directory(path.parent)
        try:
            destination = admit_create_only_external_output(
                path, self.repository_root, "evidence output"
            )
        except V4PathError as error:
            raise V4MeasurementError(str(error)) from error
        with destination.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return destination

    def json(self, relative: str, value: object) -> Path:
        return self.bytes(relative, canonical_json_bytes(value))

    def journal(self, value: dict[str, Any]) -> None:
        with (self.root / "journal.jsonl").open("ab") as stream:
            stream.write(
                json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
                + b"\n"
            )
            stream.flush()
            os.fsync(stream.fileno())

    def chat_attempted(self) -> int:
        self.state.attempted_chats += 1
        return self.state.attempted_chats

    def chat_completed(self, trajectory_id: str, chat_index: int) -> None:
        self.state.completed_chats += 1
        self.journal(
            {
                "chat_index": chat_index,
                "kind": "chat_completed",
                "timestamp_utc": _now(),
                "trajectory_id": trajectory_id,
            }
        )


def _manifest(writer: _Writer) -> None:
    files = sorted(
        path
        for path in writer.root.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "manifest.sha256"}
    )
    writer.json(
        "manifest.json",
        {
            "files": [
                {
                    "path": path.relative_to(writer.root).as_posix(),
                    "sha256": sha256_bytes(path.read_bytes()),
                }
                for path in files
            ],
            "schema_version": 1,
        },
    )
    writer.bytes(
        "manifest.sha256",
        f"{sha256_bytes((writer.root / 'manifest.json').read_bytes())}  manifest.json\n".encode(),
    )


def _chat_attempt_count(root: Path) -> int:
    return len(list((root / "raw").glob("*.request.json")))


def _verify_manifest(root: Path) -> None:
    manifest, raw = _load(root / "manifest.json", "manifest")
    manifest_digest = _admit_regular_file(root / "manifest.sha256", "manifest digest")
    if manifest_digest.read_bytes() != f"{sha256_bytes(raw)}  manifest.json\n".encode():
        raise V4MeasurementError("manifest digest differs")
    manifest = _map(manifest, {"files", "schema_version"}, "manifest")
    rows = _type(manifest["files"], list, "manifest.files")
    actual = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "manifest.sha256"}
    )
    if (
        manifest["schema_version"] != 1
        or len(rows) != len(actual)
        or [row.get("path") if type(row) is dict else None for row in rows] != actual
    ):
        raise V4MeasurementError("manifest inventory differs")
    for row in rows:
        row = _map(row, {"path", "sha256"}, "manifest row")
        target = _admit_regular_file(root / row["path"], "manifest target")
        if sha256_bytes(target.read_bytes()) != _sha(row["sha256"], "manifest sha"):
            raise V4MeasurementError("manifest file digest differs")


def _registry_hash(root: Path) -> str:
    return sha256_bytes(_repo(root, REGISTRY_PATH, "registry"))


def _comparison(
    raw: bytes, release: dict[str, str] | None = None
) -> dict[str, Any]:
    value = _obj(raw, "comparison projection")
    value = _map(
        value,
        {
            "intersections",
            "no_overlap_assertions",
            "pinned_refs",
            "schema_version",
            "v3",
            "v4",
        },
        "comparison projection",
    )
    intersections = _type(value["intersections"], dict, "comparison intersections")
    assertions = _type(value["no_overlap_assertions"], dict, "comparison assertions")
    expected = {
        "case_ids_empty": True,
        "corpus_ids_empty": True,
        "entity_identifiers_empty": True,
        "prompt_sha256_empty": True,
        "record_text_sha256_empty": True,
    }
    if (
        value["schema_version"] != "anachron-v4-tag-blob-comparison-v2"
        or set(intersections)
        != {
            "case_ids",
            "corpus_ids",
            "entity_identifiers",
            "prompt_sha256",
            "record_text_sha256",
        }
        or any(
            _type(items, list, "comparison intersection")
            for items in intersections.values()
        )
        or assertions != expected
    ):
        raise V4MeasurementError("comparison projection differs")
    pinned = _map(value["pinned_refs"], {"v3", "v4"}, "comparison pinned refs")
    for side in ("v3", "v4"):
        pinned[side] = _map(
            pinned[side], {"tag", "tag_object", "tag_peeled"}, f"comparison {side} ref"
        )
        if (
            not _str(pinned[side]["tag"], f"comparison {side} tag")
            or not re.fullmatch(r"[0-9a-f]{40}", pinned[side]["tag_object"])
            or not re.fullmatch(r"[0-9a-f]{40}", pinned[side]["tag_peeled"])
        ):
            raise V4MeasurementError("comparison pinned refs differ")
    if release is not None and (
        pinned["v4"]
        != {
            "tag": release["tag"],
            "tag_object": release["tag_object"],
            "tag_peeled": release["tag_peeled"],
        }
        or pinned["v3"]
        != {
            "tag": release["v3_tag"],
            "tag_object": release["v3_tag_object"],
            "tag_peeled": release["v3_tag_peeled"],
        }
    ):
        raise V4MeasurementError("comparison release binding differs")
    return value


def _authoritative_comparison(
    root: Path,
    source_manifest_raw: bytes,
    comparison_raw: bytes,
    *,
    expected_source_origin: str | None = None,
    expected_v3: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Require X to be the fresh tag-blob derivation admitted by M."""

    release, _ = _source_manifest(source_manifest_raw)
    comparison = _comparison(comparison_raw, release)
    try:
        source = derive_source_manifest(
            root,
            **(
                {"expected_origin": expected_source_origin}
                if expected_source_origin is not None
                else {}
            ),
            **({"expected_v3": expected_v3} if expected_v3 is not None else {}),
        )
        if source_manifest_raw != canonical_json_bytes(source):
            raise V4MeasurementError("source manifest is not the tagged derivation")
        derived = derive_bytes(root, v3_tag=release["v3_tag"], v4_tag=release["tag"])
    except (V4ComparisonError, V4SourceManifestError, V4PathError) as error:
        raise V4MeasurementError("authoritative comparison derivation differs") from error
    if comparison_raw != derived:
        raise V4MeasurementError("comparison projection is not the tagged derivation")
    return release, comparison


def _source_manifest(raw: bytes) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    value = _obj(raw, "source manifest")
    value = _map(
        value,
        {"governed_files", "governed_paths", "release", "schema_version"},
        "source manifest",
    )
    if value["schema_version"] != _SOURCE_MANIFEST_SCHEMA:
        raise V4MeasurementError("source manifest schema differs")
    paths = _type(value["governed_paths"], list, "source manifest governed paths")
    if paths != list(V4_GOVERNED_SOURCE_PATHS):
        raise V4MeasurementError("source manifest governed paths differ")
    release = _map(
        value["release"],
        {
            "branch_ref",
            "commit",
            "master_local",
            "master_remote",
            "origin",
            "remote_branch",
            "remote_tag_object",
            "remote_tag_peeled",
            "remote_v3_tag_object",
            "remote_v3_tag_peeled",
            "tag",
            "tag_object",
            "tag_peeled",
            "v3_commit",
            "v3_tag",
            "v3_tag_object",
            "v3_tag_peeled",
        },
        "source manifest release",
    )
    for key in (
        "branch_ref",
        "commit",
        "master_local",
        "master_remote",
        "remote_branch",
        "remote_tag_object",
        "remote_tag_peeled",
        "remote_v3_tag_object",
        "remote_v3_tag_peeled",
        "tag_object",
        "tag_peeled",
        "v3_commit",
        "v3_tag_object",
        "v3_tag_peeled",
    ):
        if not re.fullmatch(
            r"[0-9a-f]{40}", _str(release[key], f"source manifest release.{key}")
        ):
            raise V4MeasurementError("source manifest release differs")
    if release["tag"] != "v4-measurement-protocol-v2" or not _str(
        release["v3_tag"], "source manifest v3 tag"
    ):
        raise V4MeasurementError("source manifest release differs")
    files = _type(value["governed_files"], list, "source manifest governed files")
    rows: dict[str, dict[str, str]] = {}
    for index, item in enumerate(files):
        item = _map(
            item, {"path", "sha256", "tag_blob_oid"}, f"source manifest file[{index}]"
        )
        path = _str(item["path"], f"source manifest file[{index}].path")
        if path in rows:
            raise V4MeasurementError("source manifest governed files duplicate")
        rows[path] = {
            "sha256": _sha(item["sha256"], f"source manifest file[{index}].sha256"),
            "tag_blob_oid": _str(
                item["tag_blob_oid"], f"source manifest file[{index}].tag_blob_oid"
            ),
        }
        if not re.fullmatch(r"[0-9a-f]{40}", rows[path]["tag_blob_oid"]):
            raise V4MeasurementError("source manifest governed file differs")
    if tuple(rows) != V4_GOVERNED_SOURCE_PATHS:
        raise V4MeasurementError("source manifest governed file topology differs")
    return release, rows


def _audit(
    root: Path,
    path: Path,
    source_manifest_raw: bytes,
    comparison_raw: bytes,
) -> tuple[dict[str, Any], bytes]:
    registry, _ = load_v4_registry(root)
    audit, raw = _load(path, "source audit")
    release, files = _source_manifest(source_manifest_raw)
    _comparison(comparison_raw, release)
    audit = _map(
        audit,
        {
            "attestation",
            "audited_at_utc",
            "audited_by",
            "case_audits",
            "comparison_projection_sha256",
            "decision",
            "registry_sha256",
            "registry_tag_blob_oid",
            "registry_tag_blob_sha256",
            "schema_version",
            "source_manifest_sha256",
            "v4_protocol_commit",
            "v4_protocol_tag",
            "v4_protocol_tag_object",
        },
        "source audit",
    )
    registry_row = files.get(REGISTRY_PATH)
    _str(audit["attestation"], "audit attestation")
    _utc(audit["audited_at_utc"], "audit time")
    if (
        audit["schema_version"] != "anachron-v4-source-audit-v1"
        or audit["audited_by"] != "Lester Leong"
        or audit["decision"] != "ACCEPT"
        or _sha(audit["source_manifest_sha256"], "audit source manifest")
        != sha256_bytes(source_manifest_raw)
        or _sha(audit["comparison_projection_sha256"], "audit comparison projection")
        != sha256_bytes(comparison_raw)
        or _sha(audit["registry_sha256"], "audit registry") != _registry_hash(root)
        or registry_row is None
        or _sha(audit["registry_tag_blob_sha256"], "audit registry tagged blob")
        != registry_row["sha256"]
        or audit["registry_sha256"] != audit["registry_tag_blob_sha256"]
        or _str(audit["registry_tag_blob_oid"], "audit registry tagged blob OID")
        != registry_row["tag_blob_oid"]
        or audit["v4_protocol_commit"] != release["tag_peeled"]
        or audit["v4_protocol_tag"] != release["tag"]
        or audit["v4_protocol_tag_object"] != release["tag_object"]
    ):
        raise V4MeasurementError("source audit differs")
    rows = _type(audit["case_audits"], list, "audit rows")
    if len(rows) != 8:
        raise V4MeasurementError("source audit count differs")
    for item, expected in zip(rows, registry["cases"]):
        item = _map(
            item,
            {
                "case_id",
                "decision",
                "reason",
                "reviewed_at_utc",
                "tag_blob_oid",
                "tag_blob_sha256",
            },
            "audit row",
        )
        card_path = f"research/v4_measurement/{expected['case_card']}"
        card_row = files.get(card_path)
        if (
            item["case_id"] != expected["id"]
            or item["decision"] != "ACCEPT"
            or _str(item["reason"], "audit reason").startswith("REPLACE_")
            or item["case_id"] not in item["reason"]
            or card_row is None
            or _sha(item["tag_blob_sha256"], "audit card tagged blob")
            != card_row["sha256"]
            or _sha(item["tag_blob_sha256"], "audit card bytes")
            != sha256_bytes(_repo(root, card_path, "audit case card"))
            or _str(item["tag_blob_oid"], "audit card tagged blob OID")
            != card_row["tag_blob_oid"]
        ):
            raise V4MeasurementError("source audit row differs")
        _utc(item["reviewed_at_utc"], "audit reviewed time")
    return audit, raw


def finalize_source_audit(
    repository_root: Path,
    source: Path,
    source_manifest: Path,
    comparison: Path,
    output: Path,
) -> dict[str, Any]:
    root = _admit_repository_root(repository_root)
    validate_authority_contract(root)
    try:
        source = admit_external_regular_input(source, root, "source audit input")
        source_manifest = admit_external_regular_input(
            source_manifest, root, "source manifest"
        )
        comparison = admit_external_regular_input(
            comparison, root, "comparison projection"
        )
        output = admit_create_only_external_output(output, root, "source audit output")
    except V4PathError as error:
        raise V4MeasurementError(str(error)) from error
    audit, _ = _audit(
        root, source, source_manifest.read_bytes(), comparison.read_bytes()
    )
    with output.open("xb") as stream:
        stream.write(canonical_json_bytes(audit))
        stream.flush()
        os.fsync(stream.fileno())
    return audit


def build_source_audit_packet(
    repository_root: Path,
    output: Path,
    *,
    expected_origin: str | None = None,
    expected_v3: dict[str, str] | None = None,
) -> dict[str, str]:
    """Create the sole external M/X audit packet from a tagged checkout."""

    root = _admit_repository_root(repository_root)
    validate_authority_contract(root)
    registry, cards = load_v4_registry(root)
    try:
        output = admit_create_only_external_output(
            output, root, "source audit packet output"
        )
    except V4PathError as error:
        raise V4MeasurementError(str(error)) from error
    try:
        source = derive_source_manifest(
            root,
            **({"expected_origin": expected_origin} if expected_origin else {}),
            **({"expected_v3": expected_v3} if expected_v3 else {}),
        )
        source_raw = canonical_json_bytes(source)
        release, _ = _source_manifest(source_raw)
        comparison_raw = derive_bytes(
            root, v3_tag=release["v3_tag"], v4_tag=release["tag"]
        )
        _comparison(comparison_raw, release)
    except (V4ComparisonError, V4SourceManifestError) as error:
        raise V4MeasurementError("source audit packet derivation differs") from error
    worksheet_raw = _repo(
        root,
        "research/v4_measurement/source_audit.template.json",
        "source audit worksheet",
    )
    html = (
        '<!doctype html><meta charset="utf-8"><title>V4 source audit</title>'
        + "".join(
            f"<section><h2>{cards[x['id']]['case_id']}</h2><p>{cards[x['id']]['prompt']}</p></section>"
            for x in registry["cases"]
        )
        + "\n"
    )
    output.mkdir()
    for path, raw in (
        (output / "M.json", source_raw),
        (output / "X.json", comparison_raw),
        (output / "source_audit_worksheet.json", worksheet_raw),
        (output / "source_audit_worksheet.html", html.encode()),
    ):
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    receipt = {
        "comparison_projection_sha256": sha256_bytes(comparison_raw),
        "schema_version": "anachron-v4-source-audit-packet-v1",
        "source_manifest_sha256": sha256_bytes(source_raw),
        "worksheet_html_sha256": sha256_bytes(html.encode()),
        "worksheet_json_sha256": sha256_bytes(worksheet_raw),
    }
    receipt_raw = canonical_json_bytes(receipt)
    with (output / "receipt.json").open("xb") as stream:
        stream.write(receipt_raw)
        stream.flush()
        os.fsync(stream.fileno())
    return receipt


def _validate_identity(
    version: object, tags: object, models: list[dict[str, Any]]
) -> None:
    version = _map(version, {"version"}, "Ollama version")
    if version["version"] != "0.33.2":
        raise V4MeasurementError("Ollama version differs")
    tags = _map(tags, {"models"}, "Ollama tags")
    found = {}
    for value in _type(tags["models"], list, "Ollama models"):
        value = _map(
            value,
            {
                "name",
                "model",
                "modified_at",
                "size",
                "digest",
                "details",
                "capabilities",
            },
            "Ollama model",
        )
        _str(value["name"], "Ollama name")
        _str(value["model"], "Ollama model name")
        _utc(value["modified_at"], "Ollama modified", z=False)
        if _type(value["size"], int, "Ollama size") < 0:
            raise V4MeasurementError("Ollama size differs")
        details = _map(
            value["details"],
            {
                "parent_model",
                "format",
                "family",
                "families",
                "parameter_size",
                "quantization_level",
                "context_length",
                "embedding_length",
            },
            "Ollama details",
        )
        for key in (
            "parent_model",
            "format",
            "family",
            "parameter_size",
            "quantization_level",
        ):
            _type(details[key], str, "Ollama detail")
        for key in ("context_length", "embedding_length"):
            if _type(details[key], int, "Ollama detail") < 0:
                raise V4MeasurementError("Ollama detail differs")
        for text in [
            *_type(details["families"], list, "Ollama families"),
            *_type(value["capabilities"], list, "Ollama capabilities"),
        ]:
            _str(text, "Ollama list")
        found[value["name"]] = _str(value["digest"], "Ollama digest").removeprefix(
            "sha256:"
        )
        if _HEX.fullmatch(found[value["name"]]) is None:
            raise V4MeasurementError("Ollama digest differs")
    if found != {model["name"]: model["digest"] for model in models}:
        raise V4MeasurementError("Ollama models differ")


def capture_runtime_identity(
    repository_root: Path,
    version_response: Path,
    tags_response: Path,
    source_manifest: Path,
    comparison: Path,
    output: Path,
) -> dict[str, Any]:
    root = _admit_repository_root(repository_root)
    try:
        version_response = admit_external_regular_input(
            version_response, root, "version response"
        )
        tags_response = admit_external_regular_input(
            tags_response, root, "tags response"
        )
        source_manifest = admit_external_regular_input(
            source_manifest, root, "source manifest"
        )
        comparison = admit_external_regular_input(
            comparison, root, "comparison projection"
        )
        output = admit_create_only_external_output(
            output, root, "runtime identity output"
        )
    except V4PathError as error:
        raise V4MeasurementError(str(error)) from error
    version, version_raw = _load(version_response, "version response")
    tags, tags_raw = _load(tags_response, "tags response")
    source_manifest_raw = source_manifest.read_bytes()
    comparison_raw = comparison.read_bytes()
    release, _ = _source_manifest(source_manifest_raw)
    _comparison(comparison_raw, release)
    models = sorted(
        [
            {"name": item["name"], "digest": item["digest"].removeprefix("sha256:")}
            for item in tags["models"]
        ],
        key=lambda item: item["name"],
    )
    _validate_identity(version, tags, models)
    identity = {
        "capture_phase": "pre_go_read_only",
        "comparison_projection_sha256": sha256_bytes(comparison_raw),
        "models": models,
        "protocol_commit": release["tag_peeled"],
        "protocol_tag": release["tag"],
        "protocol_tag_object": release["tag_object"],
        "schema_version": _RUNTIME_IDENTITY_SCHEMA,
        "source_manifest_sha256": sha256_bytes(source_manifest_raw),
        "tags_response_sha256": sha256_bytes(tags_raw),
        "version": version["version"],
        "version_response_sha256": sha256_bytes(version_raw),
    }
    with output.open("xb") as stream:
        stream.write(canonical_json_bytes(identity))
        stream.flush()
        os.fsync(stream.fileno())
    return identity


def _identity(
    path: Path,
    source_manifest_raw: bytes | None = None,
    comparison_raw: bytes | None = None,
) -> tuple[dict[str, Any], bytes]:
    value, raw = _load(path, "runtime identity")
    value = _map(
        value,
        {
            "capture_phase",
            "comparison_projection_sha256",
            "models",
            "protocol_commit",
            "protocol_tag",
            "protocol_tag_object",
            "schema_version",
            "source_manifest_sha256",
            "tags_response_sha256",
            "version",
            "version_response_sha256",
        },
        "runtime identity",
    )
    if (
        value["schema_version"] != _RUNTIME_IDENTITY_SCHEMA
        or value["capture_phase"] != "pre_go_read_only"
        or value["version"] != "0.33.2"
        or len(value["models"]) != 2
    ):
        raise V4MeasurementError("runtime identity differs")
    _models(value["models"], "runtime identity.models")
    for field in (
        "comparison_projection_sha256",
        "source_manifest_sha256",
        "tags_response_sha256",
        "version_response_sha256",
    ):
        _sha(value[field], f"runtime identity.{field}")
    if (
        value["protocol_tag"] != "v4-measurement-protocol-v2"
        or not re.fullmatch(r"[0-9a-f]{40}", value["protocol_commit"])
        or not re.fullmatch(r"[0-9a-f]{40}", value["protocol_tag_object"])
    ):
        raise V4MeasurementError("runtime identity release differs")
    if source_manifest_raw is not None or comparison_raw is not None:
        if source_manifest_raw is None or comparison_raw is None:
            raise V4MeasurementError("runtime identity bindings are incomplete")
        release, _ = _source_manifest(source_manifest_raw)
        _comparison(comparison_raw, release)
        if (
            value["source_manifest_sha256"] != sha256_bytes(source_manifest_raw)
            or value["comparison_projection_sha256"] != sha256_bytes(comparison_raw)
            or value["protocol_commit"] != release["tag_peeled"]
            or value["protocol_tag"] != release["tag"]
            or value["protocol_tag_object"] != release["tag_object"]
        ):
            raise V4MeasurementError("runtime identity binding differs")
    return value, raw


def _compatibility_plan(
    path: Path,
    root: Path,
    audit_raw: bytes,
    identity_raw: bytes,
    source_manifest_raw: bytes,
    comparison_raw: bytes,
) -> tuple[dict[str, Any], bytes]:
    value, raw = _load(path, "compatibility plan")
    required = {
        "acceptance_matrix_sha256",
        "authority_binding_contract_sha256",
        "chat_requests_per_trace",
        "comparison_projection_sha256",
        "excluded_from_metrics",
        "generation",
        "kind",
        "models",
        "no_retry",
        "plan_id",
        "protocol_version",
        "production_schema",
        "release",
        "runtime_identity_sha256",
        "sample_ids",
        "source_audit_sha256",
        "source_manifest_sha256",
        "trace_count",
        "v3_included_count",
    }
    value = _map(value, required, "compatibility plan")
    authority = validate_authority_contract(root)
    release, _ = _source_manifest(source_manifest_raw)
    identity = _obj(identity_raw, "runtime identity")
    if (
        value["acceptance_matrix_sha256"] != authority["protocol_matrix_sha256"]
        or value["authority_binding_contract_sha256"]
        != sha256_bytes(
            _repo(
                root,
                "research/v4_measurement/authority_binding_contract.json",
                "authority",
            )
        )
        or value["kind"] != "anachron-v4-production-schema-compatibility-template"
        or value["protocol_version"] != "v4-measurement-protocol-v2"
        or value["no_retry"] is not True
        or value["excluded_from_metrics"] is not True
        or value["chat_requests_per_trace"] != 2
        or value["trace_count"] != 2
        or value["sample_ids"] != ["compat-lantern-2022-08-schema"]
        or value["generation"]
        != {
            "num_ctx": 8192,
            "num_predict": 512,
            "seed": 0,
            "temperature": 0,
            "think": False,
        }
        or value["production_schema"]
        != {
            "date": "optional_exact_case_as_of_YYYY_MM_DD_string",
            "query": "required_nonempty_string",
            "unknown_keys": "reject",
        }
        or _sha(value["source_audit_sha256"], "compatibility audit")
        != sha256_bytes(audit_raw)
        or _sha(value["runtime_identity_sha256"], "compatibility identity")
        != sha256_bytes(identity_raw)
        or _sha(value["source_manifest_sha256"], "compatibility manifest")
        != sha256_bytes(source_manifest_raw)
        or _sha(
            value["comparison_projection_sha256"], "compatibility comparison projection"
        )
        != sha256_bytes(comparison_raw)
        or value["v3_included_count"] != 0
    ):
        raise V4MeasurementError("compatibility plan differs")
    if _models(value["models"], "compatibility models") != _models(
        identity["models"], "runtime identity.models"
    ) or value["release"] != {
        "commit": release["tag_peeled"],
        "tag": release["tag"],
        "tag_object": release["tag_object"],
    }:
        raise V4MeasurementError("compatibility plan release binding differs")
    return value, raw


def _plan(
    path: Path,
    root: Path,
    audit_raw: bytes,
    identity_raw: bytes,
    source_manifest_raw: bytes,
    comparison_raw: bytes,
    compatibility_raw: bytes,
) -> tuple[dict[str, Any], bytes]:
    value, raw = _load(path, "full plan")
    required = {
        "acceptance_matrix_sha256",
        "authority_binding_contract_sha256",
        "comparison_projection_sha256",
        "compatibility",
        "endpoint",
        "generation",
        "kind",
        "main",
        "models",
        "no_retry",
        "plan_id",
        "protocol_version",
        "registry_sha256",
        "release",
        "runtime_identity_sha256",
        "source_audit_sha256",
        "source_manifest_sha256",
        "v3_included_count",
    }
    value = _map(value, required, "full plan")
    authority = validate_authority_contract(root)
    release_from_manifest, _ = _source_manifest(source_manifest_raw)
    identity = _obj(identity_raw, "runtime identity")
    if (
        value["acceptance_matrix_sha256"] != authority["protocol_matrix_sha256"]
        or value["authority_binding_contract_sha256"]
        != sha256_bytes(
            _repo(
                root,
                "research/v4_measurement/authority_binding_contract.json",
                "authority",
            )
        )
        or value["kind"]
        != "anachron-v4-conditional-compatibility-then-primary-template"
        or value["no_retry"] is not True
        or value["v3_included_count"] != 0
    ):
        raise V4MeasurementError("full plan differs")
    _str(value["plan_id"], "full plan.plan_id")
    if value["protocol_version"] != "v4-measurement-protocol-v2":
        raise V4MeasurementError("full plan protocol version differs")
    _models(value["models"], "full plan.models")
    release = _map(
        value["release"], {"commit", "tag", "tag_object"}, "full plan.release"
    )
    if (
        not re.fullmatch(
            r"[0-9a-f]{40}", _str(release["commit"], "full plan release commit")
        )
        or release["tag"] != "v4-measurement-protocol-v2"
        or not re.fullmatch(
            r"[0-9a-f]{40}", _str(release["tag_object"], "full plan release tag object")
        )
    ):
        raise V4MeasurementError("full plan release differs")
    _loopback(value["endpoint"])
    if value["generation"] != {
        "num_ctx": 8192,
        "num_predict": 512,
        "seed": 0,
        "temperature": 0,
        "think": False,
    } or value["main"] != {
        "case_count": 8,
        "chat_requests": 128,
        "development_trajectories": 0,
        "modes": ["unrestricted", "enforced"],
        "primary_trajectories": 64,
        "repetitions": 2,
        "requires_compatibility_pass": True,
        "total_trajectories": 64,
    }:
        raise V4MeasurementError("full plan topology differs")
    if (
        value["compatibility"].get("chat_requests") != 4
        or value["compatibility"].get("excludes_from_metrics") is not True
        or value["compatibility"].get("required_valid_traces") != 2
    ):
        raise V4MeasurementError("compatibility plan differs")
    if (
        _sha(value["registry_sha256"], "registry") != _registry_hash(root)
        or _sha(value["source_audit_sha256"], "audit") != sha256_bytes(audit_raw)
        or _sha(value["runtime_identity_sha256"], "identity")
        != sha256_bytes(identity_raw)
        or _sha(value["source_manifest_sha256"], "source manifest")
        != sha256_bytes(source_manifest_raw)
        or _sha(value["comparison_projection_sha256"], "comparison projection")
        != sha256_bytes(comparison_raw)
    ):
        raise V4MeasurementError("plan binding differs")
    if value["release"] != {
        "commit": release_from_manifest["tag_peeled"],
        "tag": release_from_manifest["tag"],
        "tag_object": release_from_manifest["tag_object"],
    } or _models(value["models"], "full plan.models") != _models(
        identity["models"], "runtime identity.models"
    ):
        raise V4MeasurementError("plan runtime identity binding differs")
    if value["compatibility"]["plan_sha256"] != sha256_bytes(compatibility_raw):
        raise V4MeasurementError("full plan compatibility binding differs")
    return value, raw


def _go(
    path: Path,
    root: Path,
    plan: dict[str, Any],
    plan_raw: bytes,
    audit_raw: bytes,
    identity_raw: bytes,
    compatibility_raw: bytes,
    comparison_raw: bytes,
) -> dict[str, Any]:
    value, _ = _load(path, "conditional GO")
    keys = {
        "acceptance_matrix_sha256",
        "authority_binding_contract_sha256",
        "authorized_at_utc",
        "comparison_projection_sha256",
        "authorized_by",
        "compatibility_plan_sha256",
        "decision",
        "full_plan_sha256",
        "kind",
        "model_digests",
        "protocol_commit",
        "protocol_tag",
        "protocol_tag_object",
        "registry_sha256",
        "runtime_identity_sha256",
        "schema_version",
        "source_audit_sha256",
        "source_manifest_sha256",
        "statement",
        "v3_included_count",
    }
    value = _map(value, keys, "conditional GO")
    if (
        value["decision"] != "GO"
        or value["authorized_by"] != "Lester Leong"
        or value["full_plan_sha256"] != sha256_bytes(plan_raw)
        or value["source_audit_sha256"] != sha256_bytes(audit_raw)
        or value["runtime_identity_sha256"] != sha256_bytes(identity_raw)
        or value["compatibility_plan_sha256"] != sha256_bytes(compatibility_raw)
        or value["source_manifest_sha256"] != plan["source_manifest_sha256"]
        or value["comparison_projection_sha256"] != plan["comparison_projection_sha256"]
        or value["acceptance_matrix_sha256"] != plan["acceptance_matrix_sha256"]
        or value["authority_binding_contract_sha256"]
        != plan["authority_binding_contract_sha256"]
        or value["v3_included_count"] != 0
    ):
        raise V4MeasurementError("conditional GO differs")
    for field in (
        "acceptance_matrix_sha256",
        "authority_binding_contract_sha256",
        "compatibility_plan_sha256",
        "full_plan_sha256",
        "registry_sha256",
        "runtime_identity_sha256",
        "source_audit_sha256",
        "source_manifest_sha256",
        "comparison_projection_sha256",
    ):
        _sha(value[field], f"conditional GO.{field}")
    if (
        value["compatibility_plan_sha256"] != plan["compatibility"]["plan_sha256"]
        or value["registry_sha256"] != plan["registry_sha256"]
        or value["comparison_projection_sha256"] != plan["comparison_projection_sha256"]
        or value["protocol_commit"] != plan["release"]["commit"]
        or value["protocol_tag"] != plan["release"]["tag"]
        or value["protocol_tag_object"] != plan["release"]["tag_object"]
        or _models(
            [
                {"name": model["name"], "digest": digest}
                for model, digest in zip(
                    plan["models"],
                    _type(value["model_digests"], list, "conditional GO.model_digests"),
                )
            ],
            "conditional GO.model_digests",
        )
        != plan["models"]
        or value["kind"] != "anachron-v4-conditional-measurement-authorization"
        or value["schema_version"] != 1
        or not _str(value["statement"], "conditional GO.statement")
    ):
        raise V4MeasurementError("conditional GO binding differs")
    _utc(value["authorized_at_utc"], "GO time")
    return value


def _loopback(endpoint: str) -> str:
    parsed = urlparse(_str(endpoint, "endpoint"))
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.port != 11434
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise V4MeasurementError("endpoint must be loopback")
    return endpoint.rstrip("/")


def _read_response(response: Any) -> bytes:
    """Read a native HTTP response without allowing an unbounded allocation."""

    chunks: list[bytes] = []
    received = 0
    while True:
        remaining = _MAX_RESPONSE_BYTES - received
        chunk = response.read(min(65_536, remaining + 1))
        if not chunk:
            return b"".join(chunks)
        if type(chunk) is not bytes:
            raise V4MeasurementError("HTTP response type differs")
        if len(chunk) > remaining:
            prefix = (b"".join(chunks) + chunk)[:_RESPONSE_PREFIX_BYTES]
            raise _ResponseLimitError(
                prefix,
                limit_class="per_response",
                response_size_lower_bound=_MAX_RESPONSE_BYTES + 1,
            )
        chunks.append(chunk)
        received += len(chunk)


def _http(endpoint: str, path: str, raw: bytes | None, timeout: int) -> bytes:
    if path in {"/api/version", "/api/tags"} and raw is None:
        request = Request(endpoint + path, method="GET")
    elif path == "/api/chat" and type(raw) is bytes:
        request = Request(
            endpoint + path,
            data=raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    else:
        raise V4MeasurementError("transport shape differs")
    with urlopen(request, timeout=timeout) as response:
        return _read_response(response)


def _bounded_transport(transport: Callable) -> Callable:
    budget = _ResponseBudget()

    def bounded(endpoint: str, path: str, raw: bytes | None, timeout: int) -> bytes:
        return budget.admit(transport(endpoint, path, raw, timeout))

    return bounded


def validate_source_admission(
    plan: dict[str, Any],
    receipt: object,
    repository_root: Path,
    *,
    expected_source_origin: str | None = None,
    expected_v3: dict[str, str] | None = None,
    ) -> None:
    _admit_repository_root(repository_root)
    keys = {
        "branch_ref",
        "head_commit",
        "master_local",
        "master_remote",
        "origin",
        "protocol_tag",
        "protocol_tag_object",
        "protocol_tag_peeled",
        "remote_branch",
        "remote_tag_object",
        "remote_tag_peeled",
        "remote_v3_tag_object",
        "remote_v3_tag_peeled",
        "v3_commit",
        "v3_tag",
        "v3_tag_object",
        "source_manifest_sha256",
    }
    receipt = _map(receipt, keys, "source admission")
    for key in keys - {"origin", "protocol_tag", "source_manifest_sha256", "v3_tag"}:
        if not re.fullmatch(r"[0-9a-f]{40}", _str(receipt[key], key)):
            raise V4MeasurementError("source admission hash differs")
    _sha(receipt["source_manifest_sha256"], "source admission source manifest")
    if (
        receipt["head_commit"] != plan["release"]["commit"]
        or receipt["protocol_tag"] != plan["release"]["tag"]
        or receipt["protocol_tag_object"] != plan["release"]["tag_object"]
        or len(
            {
                receipt["branch_ref"],
                receipt["head_commit"],
                receipt["protocol_tag_peeled"],
                receipt["remote_branch"],
                receipt["remote_tag_peeled"],
            }
        )
        != 1
        or receipt["protocol_tag_object"] != receipt["remote_tag_object"]
        or receipt["master_local"] != receipt["v3_commit"]
        or receipt["master_remote"] != receipt["v3_commit"]
        or not _str(receipt["v3_tag"], "source admission v3 tag")
        or receipt["remote_v3_tag_object"] != receipt["v3_tag_object"]
        or receipt["remote_v3_tag_peeled"] != receipt["v3_commit"]
        or receipt["origin"]
        != (
            expected_source_origin
            if expected_source_origin is not None
            else "https://github.com/LesterALeong/anachron.git"
        )
        or receipt["source_manifest_sha256"] != plan["source_manifest_sha256"]
    ):
        raise V4MeasurementError("source admission differs")


def admit_source(
    plan: dict[str, Any],
    repository_root: Path,
    source_manifest: Path,
    *,
    expected_source_origin: str | None = None,
    expected_v3: dict[str, str] | None = None,
) -> dict[str, Any]:
    root = _admit_repository_root(repository_root)
    try:
        source_manifest = admit_external_regular_input(
            source_manifest, root, "source manifest"
        )
    except V4PathError as error:
        raise V4MeasurementError(str(error)) from error
    try:
        manifest = validate_source_manifest(
            root,
            source_manifest,
            **(
                {"expected_origin": expected_source_origin}
                if expected_source_origin is not None
                else {}
            ),
            **({"expected_v3": expected_v3} if expected_v3 is not None else {}),
        )
    except V4SourceManifestError as error:
        raise V4MeasurementError(str(error)) from error
    raw = source_manifest.read_bytes()
    if sha256_bytes(raw) != plan["source_manifest_sha256"]:
        raise V4MeasurementError("source manifest plan binding differs")
    release = manifest["release"]
    receipt = {
        "branch_ref": release["branch_ref"],
        "head_commit": release["commit"],
        "master_local": release["master_local"],
        "master_remote": release["master_remote"],
        "origin": release["origin"],
        "protocol_tag": release["tag"],
        "protocol_tag_object": release["tag_object"],
        "protocol_tag_peeled": release["tag_peeled"],
        "remote_branch": release["remote_branch"],
        "remote_tag_object": release["remote_tag_object"],
        "remote_tag_peeled": release["remote_tag_peeled"],
        "remote_v3_tag_object": release["remote_v3_tag_object"],
        "remote_v3_tag_peeled": release["remote_v3_tag_peeled"],
        "source_manifest_sha256": sha256_bytes(raw),
        "v3_commit": release["v3_commit"],
        "v3_tag": release["v3_tag"],
        "v3_tag_object": release["v3_tag_object"],
    }
    validate_source_admission(
        plan,
        receipt,
        root,
        expected_source_origin=expected_source_origin,
    )
    return receipt


def _schema() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": _TOOL,
                "parameters": {
                    "additionalProperties": False,
                    "properties": {
                        "date": {"type": "string"},
                        "query": {"type": "string"},
                    },
                    "required": ["query"],
                    "type": "object",
                },
            },
        }
    ]


def first_request(
    model: str, prompt: str, generation: dict[str, Any]
) -> dict[str, Any]:
    return {
        "messages": [{"content": prompt, "role": "user"}],
        "model": model,
        "options": {
            x: generation[x] for x in ("temperature", "seed", "num_ctx", "num_predict")
        },
        "stream": False,
        "think": generation["think"],
        "tools": _schema(),
    }


def final_request(
    first: dict[str, Any], assistant: dict[str, Any], result: str
) -> dict[str, Any]:
    value = {key: item for key, item in first.items() if key != "tools"}
    value["messages"] = [
        *first["messages"],
        assistant,
        {"content": result, "role": "tool", "tool_name": _TOOL},
    ]
    return value


def validate_tool_arguments(arguments: object, card: dict[str, Any]) -> str:
    arguments = _type(arguments, dict, "tool arguments")
    if set(arguments) not in ({"query"}, {"query", "date"}):
        raise V4MeasurementError("tool arguments differ")
    query = _str(arguments["query"], "tool query").strip()
    if not query:
        raise V4MeasurementError("tool query must contain non-whitespace")
    if "date" in arguments and _str(arguments["date"], "tool date") != card["as_of"]:
        raise V4MeasurementError("tool date differs")
    return query


def _envelope(
    raw: bytes, model: str, card: dict[str, Any] | None
) -> tuple[dict[str, Any], str | None]:
    value = _map(
        _obj(raw, "chat response", False),
        {
            "created_at",
            "done",
            "done_reason",
            "eval_count",
            "eval_duration",
            "load_duration",
            "message",
            "model",
            "prompt_eval_count",
            "prompt_eval_duration",
            "total_duration",
        },
        "chat response",
    )
    _utc(value["created_at"], "created_at")
    if (
        value["model"] != model
        or value["done"] is not True
        or value["done_reason"] != "stop"
    ):
        raise V4MeasurementError("native response differs")
    for key in (
        "eval_count",
        "eval_duration",
        "load_duration",
        "prompt_eval_count",
        "prompt_eval_duration",
        "total_duration",
    ):
        if _type(value[key], int, key) < 0:
            raise V4MeasurementError("native metric differs")
    if card is None:
        message = _map(value["message"], {"role", "content"}, "final message")
        if (
            message["role"] != "assistant"
            or not _str(message["content"], "final content").strip()
        ):
            raise V4MeasurementError("final response differs")
        return message, None
    message = _map(value["message"], {"role", "content", "tool_calls"}, "first message")
    if message["role"] != "assistant" or message["content"] != "":
        raise V4MeasurementError("first response differs")
    calls = _type(message["tool_calls"], list, "tool calls")
    if len(calls) != 1:
        raise V4MeasurementError("tool calls differ")
    call = _map(calls[0], {"id", "function"}, "tool call")
    function = _map(call["function"], {"arguments", "index", "name"}, "tool function")
    if (
        not _str(call["id"], "tool id")
        or type(function["index"]) is not int
        or function["index"] != 0
        or function["name"] != _TOOL
    ):
        raise V4MeasurementError("tool call identity differs")
    return message, validate_tool_arguments(function["arguments"], card)


def _result(
    card: dict[str, Any], mode: str, query: str
) -> tuple[str, list[dict[str, Any]]]:
    records = eligible_records(card, mode, query)
    text = canonical_json_bytes(
        {
            "as_of": card["as_of"],
            "mode": mode,
            "query": query,
            "records": [
                {"id": x["id"], "publish_date": x["publish_date"], "text": x["text"]}
                for x in records
            ],
        }
    ).decode()
    return text, records


def _trace(
    writer: _Writer,
    endpoint: str,
    transport: Callable,
    plan: dict[str, Any],
    card: dict[str, Any],
    model: dict[str, Any],
    mode: str,
    repeat: int,
    ident: str,
    primary: bool,
) -> dict[str, Any]:
    row = {
        "as_of": card["as_of"],
        "case_id": card["case_id"],
        "mode": mode,
        "model": model["name"],
        "model_digest": model["digest"],
        "primary": primary,
        "repetition": repeat,
        "trajectory_id": ident,
        "valid": False,
    }
    writer.journal(
        {"kind": "trajectory_claim", "timestamp_utc": _now(), "trajectory_id": ident}
    )
    phase = "main" if primary else "compatibility"
    response_received = False
    response_validated = False
    response_name = "first"
    failure_stage = "protocol"
    try:
        first = first_request(model["name"], card["prompt"], plan["generation"])
        rp = writer.bytes(
            f"raw/{ident}.first.request.json", canonical_json_bytes(first)
        )
        failed_step = writer.chat_attempted()
        raw = transport(endpoint, "/api/chat", rp.read_bytes(), 30)
        response_received = True
        fp = writer.bytes(f"raw/{ident}.first.response.json", raw)
        try:
            message, query = _envelope(raw, model["name"], card)
        except V4MeasurementError:
            failure_stage = "first_chat_envelope"
            raise
        response_validated = True
        writer.chat_completed(ident, failed_step)
        text, records = _result(card, mode, query)
        tp = writer.bytes(f"raw/{ident}.tool_result.json", text.encode())
        final = final_request(first, message, text)
        if "tools" in final:
            raise V4MeasurementError("final request tools differ")
        qp = writer.bytes(
            f"raw/{ident}.final.request.json", canonical_json_bytes(final)
        )
        failed_step = writer.chat_attempted()
        response_name = "final"
        response_received = False
        response_validated = False
        end = transport(endpoint, "/api/chat", qp.read_bytes(), 30)
        response_received = True
        ep = writer.bytes(f"raw/{ident}.final.response.json", end)
        try:
            _envelope(end, model["name"], None)
        except V4MeasurementError:
            failure_stage = "final_chat_envelope"
            raise
        response_validated = True
        writer.chat_completed(ident, failed_step)
        row.update(
            {
                "first_request_sha256": sha256_bytes(rp.read_bytes()),
                "first_response_sha256": sha256_bytes(fp.read_bytes()),
                "final_request_sha256": sha256_bytes(qp.read_bytes()),
                "final_response_sha256": sha256_bytes(ep.read_bytes()),
                "tool_result_sha256": sha256_bytes(tp.read_bytes()),
                "query": query,
                "returned_ids": [x["id"] for x in records],
                "query_nonblank": bool(query),
                "restatement_returned": any("restates" in x for x in records),
                "survivorship_case": card["category"].startswith("survivorship-"),
                "tclr": any(x["publish_date"] > card["as_of"] for x in records),
                "valid": True,
            }
        )
    except _TraceFailure:
        raise
    except _ResponseLimitError as error:
        if error.prefix:
            writer.bytes(f"raw/{ident}.{response_name}.response.prefix.bin", error.prefix)
        failure_stage = f"{response_name}_chat_resource"
        writer.journal(
            {
                "attempted_chat_count": writer.state.attempted_chats,
                "completed_chat_count": writer.state.completed_chats,
                "failed_chat_index": writer.state.attempted_chats,
                "failure_stage": failure_stage,
                "fault_code": "resource",
                "kind": "trajectory_terminal",
                "last_completed_chat_index": (
                    writer.state.completed_chats or None
                ),
                "timestamp_utc": _now(),
                "trajectory_id": ident,
                "valid": False,
            }
        )
        raise _TraceFailure(
            phase=phase,
            failed_step=writer.state.attempted_chats,
            last_completed_step=writer.state.completed_chats,
            fault_code="resource",
            trajectory_id=ident,
            failure_stage=failure_stage,
            failure_cause="resource",
            raw_response_state="prefix",
            resource_limit_class=error.limit_class,
            retained_prefix_bytes=len(error.prefix),
            response_size_lower_bound=error.response_size_lower_bound,
            campaign_response_bytes_before=error.campaign_response_bytes_before,
            observed_response_bytes=error.observed_response_bytes,
        ) from error
    except Exception as error:
        if failure_stage == "protocol":
            if response_received and not response_validated:
                failure_stage = f"{response_name}_chat_envelope"
                fault_code = "native_envelope"
            elif not response_received:
                failure_stage = f"{response_name}_chat_transport"
                fault_code = "transport"
            else:
                fault_code = "protocol"
        else:
            fault_code = "native_envelope"
        writer.journal(
            {
                "attempted_chat_count": writer.state.attempted_chats,
                "completed_chat_count": writer.state.completed_chats,
                "failed_chat_index": writer.state.attempted_chats,
                "failure_stage": failure_stage,
                "fault_code": fault_code,
                "kind": "trajectory_terminal",
                "last_completed_chat_index": (
                    writer.state.completed_chats or None
                ),
                "timestamp_utc": _now(),
                "trajectory_id": ident,
                "valid": False,
            }
        )
        raise _TraceFailure(
            phase=phase,
            failed_step=writer.state.attempted_chats,
            last_completed_step=writer.state.completed_chats,
            fault_code=fault_code,
            trajectory_id=ident,
            failure_stage=failure_stage,
            failure_cause=(
                "transport"
                if fault_code == "transport"
                else "envelope_or_drift"
                if fault_code == "native_envelope"
                else "protocol"
            ),
            raw_response_state=(
                "absent" if fault_code in {"transport", "protocol"} else "complete"
            ),
        ) from error
    writer.journal(
        {
            "kind": "trajectory_terminal",
            "timestamp_utc": _now(),
            "trajectory_id": ident,
            "valid": row["valid"],
        }
    )
    return row


def _identity_checkpoint(
    writer: _Writer,
    endpoint: str,
    transport: Callable,
    models: list[dict[str, Any]],
    identity: dict[str, Any],
    name: str,
    phase: str,
) -> dict[str, str]:
    response_name = "version"
    response_written = False
    stages = {
        "before_compatibility": {
            "version": "identity_before_compatibility_version",
            "tags": "identity_before_compatibility_tags",
        },
        "between_phases": {
            "version": "identity_between_phases_version",
            "tags": "identity_between_phases_tags",
        },
        "after_main": {
            "version": "identity_after_main_version",
            "tags": "identity_after_main_tags",
        },
    }
    try:
        vr = transport(endpoint, "/api/version", None, 30)
        vp = writer.bytes(f"raw/identity.{name}.version.json", vr)
        response_written = True
        response_name = "tags"
        response_written = False
        tr = transport(endpoint, "/api/tags", None, 30)
        tp = writer.bytes(f"raw/identity.{name}.tags.json", tr)
        response_written = True
        _validate_identity(_obj(vr, "version", False), _obj(tr, "tags", False), models)
    except _ResponseLimitError as error:
        if error.prefix:
            writer.bytes(
                f"raw/identity.{name}.{response_name}.response.prefix.bin",
                error.prefix,
            )
        raise _TraceFailure(
            phase=phase,
            failed_step=writer.state.attempted_chats,
            last_completed_step=writer.state.completed_chats,
            fault_code="identity",
            trajectory_id=None,
            failure_stage=stages[name][response_name],
            failure_cause="resource",
            raw_response_state="prefix",
            resource_limit_class=error.limit_class,
            retained_prefix_bytes=len(error.prefix),
            response_size_lower_bound=error.response_size_lower_bound,
            campaign_response_bytes_before=error.campaign_response_bytes_before,
            observed_response_bytes=error.observed_response_bytes,
        ) from error
    except Exception as error:
        raise _TraceFailure(
            phase=phase,
            failed_step=writer.state.attempted_chats,
            last_completed_step=writer.state.completed_chats,
            fault_code="identity",
            trajectory_id=None,
            failure_stage=stages[name][response_name],
            failure_cause=("envelope_or_drift" if response_written else "transport"),
            raw_response_state=("complete" if response_written else "absent"),
        ) from error
    result = {
        "version_sha256": sha256_bytes(vp.read_bytes()),
        "tags_sha256": sha256_bytes(tp.read_bytes()),
    }
    if result != {
        "version_sha256": identity["version_response_sha256"],
        "tags_sha256": identity["tags_response_sha256"],
    }:
        raise _TraceFailure(
            phase=phase,
            failed_step=writer.state.attempted_chats,
            last_completed_step=writer.state.completed_chats,
            fault_code="identity",
            trajectory_id=None,
            failure_stage=stages[name][response_name],
            failure_cause="envelope_or_drift",
            raw_response_state="complete",
        )
    return result


def _failure_receipt(
    writer: _Writer,
    failure: _TraceFailure,
) -> dict[str, Any]:
    phase_directory = "compatibility" if failure.phase == "compatibility" else "full"
    receipt = {
        "attempted_chat_count": writer.state.attempted_chats,
        "campaign_status": "operationally_invalid",
        "campaign_response_bytes_before": failure.campaign_response_bytes_before,
        "completed_chat_count": writer.state.completed_chats,
        "failed_chat_index": (
            writer.state.attempted_chats
            if failure.trajectory_id is not None and failure.fault_code != "protocol"
            else None
        ),
        "failure_manifest_sha256": "0" * 64,
        "failure_cause": failure.failure_cause,
        "failure_stage": failure.failure_stage,
        "fault_code": failure.fault_code,
        "last_completed_chat_index": (
            writer.state.completed_chats if writer.state.completed_chats else None
        ),
        "phase": failure.phase,
        "phase_directory": phase_directory,
        "observed_response_bytes": failure.observed_response_bytes,
        "raw_response_state": failure.raw_response_state,
        "resource_limit_class": failure.resource_limit_class,
        "response_size_lower_bound": failure.response_size_lower_bound,
        "retained_prefix_bytes": failure.retained_prefix_bytes,
        "resume_allowed": False,
        "schema_version": "anachron-v4-failure-state-v3",
        "scientific_result_available": False,
        "trajectory_id": failure.trajectory_id,
        "v3_included_count": 0,
    }
    _validate_failure_receipt(receipt, state=writer.state)
    return receipt


def _validate_failure_receipt(
    value: object, *, state: _CampaignState | None = None
) -> dict[str, Any]:
    receipt = _map(
        value,
        {
            "attempted_chat_count",
            "campaign_response_bytes_before",
            "campaign_status",
            "completed_chat_count",
            "failed_chat_index",
            "failure_manifest_sha256",
            "failure_cause",
            "failure_stage",
            "fault_code",
            "last_completed_chat_index",
            "phase",
            "phase_directory",
            "observed_response_bytes",
            "raw_response_state",
            "resource_limit_class",
            "response_size_lower_bound",
            "retained_prefix_bytes",
            "resume_allowed",
            "schema_version",
            "scientific_result_available",
            "trajectory_id",
            "v3_included_count",
        },
        "failure receipt",
    )
    attempted = _type(receipt["attempted_chat_count"], int, "failure attempted")
    completed = _type(receipt["completed_chat_count"], int, "failure completed")
    failed = receipt["failed_chat_index"]
    last = receipt["last_completed_chat_index"]
    trajectory = receipt["trajectory_id"]
    if (
        receipt["schema_version"] != "anachron-v4-failure-state-v3"
        or receipt["campaign_status"] != "operationally_invalid"
        or receipt["scientific_result_available"] is not False
        or receipt["resume_allowed"] is not False
        or receipt["v3_included_count"] != 0
        or receipt["phase"] not in {"compatibility", "main"}
        or receipt["phase_directory"]
        != ("compatibility" if receipt["phase"] == "compatibility" else "full")
        or receipt["fault_code"] not in _FAULT_CODES
        or receipt["failure_stage"] not in _FAILURE_STAGES
        or attempted < 0
        or completed < 0
        or completed > attempted
        or receipt["raw_response_state"] not in {"absent", "prefix", "complete"}
    ):
        raise V4MeasurementError("failure receipt differs")
    _sha(receipt["failure_manifest_sha256"], "failure manifest binding")
    cause = receipt["failure_cause"]
    raw_state = receipt["raw_response_state"]
    if cause not in {"transport", "resource", "envelope_or_drift", "protocol"}:
        raise V4MeasurementError("failure cause differs")
    if receipt["failure_stage"] == "protocol" or cause == "protocol":
        if (
            receipt["failure_stage"] != "protocol"
            or receipt["fault_code"] != "protocol"
            or cause != "protocol"
            or raw_state != "absent"
            or trajectory is not None
            or failed is not None
            or attempted != completed
            or last != (completed if completed else None)
            or any(
                receipt[field] is not None
                for field in (
                    "resource_limit_class",
                    "retained_prefix_bytes",
                    "response_size_lower_bound",
                    "campaign_response_bytes_before",
                    "observed_response_bytes",
                )
            )
        ):
            raise V4MeasurementError("protocol failure state differs")
        expected_counts = (
            {0, 4} if receipt["phase"] == "compatibility" else {4, 132}
        )
        if completed not in expected_counts:
            raise V4MeasurementError("protocol failure count differs")
        if state is not None and (
            attempted != state.attempted_chats or completed != state.completed_chats
        ):
            raise V4MeasurementError("failure state differs")
        return receipt
    if cause == "resource":
        retained = _type(
            receipt["retained_prefix_bytes"], int, "resource retained prefix"
        )
        if raw_state != "prefix" or retained < 1 or retained > _RESPONSE_PREFIX_BYTES:
            raise V4MeasurementError("resource prefix metadata differs")
        if receipt["resource_limit_class"] == "per_response":
            if (
                retained != _RESPONSE_PREFIX_BYTES
                or receipt["response_size_lower_bound"] != _MAX_RESPONSE_BYTES + 1
                or receipt["campaign_response_bytes_before"] is not None
                or receipt["observed_response_bytes"] is not None
            ):
                raise V4MeasurementError("per-response resource metadata differs")
        elif receipt["resource_limit_class"] == "campaign":
            before = _type(
                receipt["campaign_response_bytes_before"],
                int,
                "campaign response bytes before",
            )
            observed = _type(
                receipt["observed_response_bytes"], int, "observed response bytes"
            )
            if (
                before < 0
                or observed < retained
                or before + observed <= _MAX_CAMPAIGN_RESPONSE_BYTES
                or receipt["response_size_lower_bound"] is not None
            ):
                raise V4MeasurementError("campaign resource metadata differs")
        else:
            raise V4MeasurementError("resource limit class differs")
    elif (
        receipt["resource_limit_class"] is not None
        or receipt["retained_prefix_bytes"] is not None
        or receipt["response_size_lower_bound"] is not None
        or receipt["campaign_response_bytes_before"] is not None
        or receipt["observed_response_bytes"] is not None
    ):
        raise V4MeasurementError("non-resource metadata differs")
    if trajectory is None:
        if receipt["failure_stage"] == "protocol":
            if (
                failed is not None
                or last != (completed if completed else None)
                or attempted != completed
                or receipt["fault_code"] != "protocol"
            ):
                raise V4MeasurementError("protocol failure counters differ")
        else:
            if (
                failed is not None
                or last != (completed if completed else None)
                or attempted != completed
                or receipt["fault_code"] != "identity"
                or not receipt["failure_stage"].startswith("identity_")
                or cause not in {"transport", "resource", "envelope_or_drift"}
                or raw_state
                != {
                    "transport": "absent",
                    "resource": "prefix",
                    "envelope_or_drift": "complete",
                }[cause]
            ):
                raise V4MeasurementError("identity failure counters differ")
            expected_counts = {
                "identity_before_compatibility_version": 0,
                "identity_before_compatibility_tags": 0,
                "identity_between_phases_version": 4,
                "identity_between_phases_tags": 4,
                "identity_after_main_version": 132,
                "identity_after_main_tags": 132,
            }
            if completed != expected_counts[receipt["failure_stage"]]:
                raise V4MeasurementError("identity failure phase differs")
    else:
        _str(trajectory, "failure trajectory")
        if receipt["failure_stage"] == "protocol":
            if (
                failed is not None
                or last != (completed if completed else None)
                or attempted != completed
                or receipt["fault_code"] != "protocol"
            ):
                raise V4MeasurementError("trajectory protocol failure differs")
        elif (
            type(failed) is not int
            or type(last) is not int and last is not None
            or failed != attempted
            or completed != attempted - 1
            or last != (completed if completed else None)
            or not receipt["failure_stage"].endswith(
                ("_transport", "_resource", "_envelope")
            )
        ):
            raise V4MeasurementError("trajectory failure counters differ")
        if receipt["failure_stage"] != "protocol":
            expected_fault = {
                "transport": "transport",
                "resource": "resource",
                "envelope": "native_envelope",
            }[receipt["failure_stage"].rsplit("_", 1)[1]]
            if receipt["fault_code"] != expected_fault:
                raise V4MeasurementError("trajectory failure fault differs")
            expected_cause = {
                "transport": "transport",
                "resource": "resource",
                "envelope": "envelope_or_drift",
            }[receipt["failure_stage"].rsplit("_", 1)[1]]
            if (
                cause != expected_cause
                or raw_state
                != {
                    "transport": "absent",
                    "resource": "prefix",
                    "envelope_or_drift": "complete",
                }[cause]
            ):
                raise V4MeasurementError("trajectory failure response state differs")
    low, high = (0, 4) if receipt["phase"] == "compatibility" else (4, 132)
    if not low <= completed <= high or not low <= attempted <= high:
        raise V4MeasurementError("failure phase bounds differ")
    if state is not None and (
        attempted != state.attempted_chats or completed != state.completed_chats
    ):
        raise V4MeasurementError("failure state differs")
    return receipt


def _seal_failure(campaign: _Writer, writer: _Writer, failure: _TraceFailure) -> None:
    """Seal the root-only invalid terminal after every phase artifact is fsynced."""

    writer.journal(
        {
            "attempted_chat_count": writer.state.attempted_chats,
            "completed_chat_count": writer.state.completed_chats,
            "failed_chat_index": (
                writer.state.attempted_chats
                if failure.trajectory_id is not None and failure.fault_code != "protocol"
                else None
            ),
            "failure_stage": failure.failure_stage,
            "fault_code": failure.fault_code,
            "kind": "phase_terminal",
            "last_completed_chat_index": (
                writer.state.completed_chats if writer.state.completed_chats else None
            ),
            "phase": failure.phase,
            "timestamp_utc": _now(),
            "valid": False,
        }
    )
    receipt = _failure_receipt(writer, failure)
    controls = {
        "failure_manifest.json",
        "failure_manifest.sha256",
        "failure_receipt.json",
    }
    files = sorted(
        path
        for path in campaign.root.rglob("*")
        if path.is_file() and path.relative_to(campaign.root).as_posix() not in controls
    )
    campaign.json(
        "failure_manifest.json",
        {
            "files": [
                {
                    "path": path.relative_to(campaign.root).as_posix(),
                    "sha256": sha256_bytes(path.read_bytes()),
                }
                for path in files
            ],
            "schema_version": "anachron-v4-failure-manifest-v2",
        },
    )
    receipt["failure_manifest_sha256"] = sha256_bytes(
        (campaign.root / "failure_manifest.json").read_bytes()
    )
    _validate_failure_receipt(receipt, state=writer.state)
    campaign.json("failure_receipt.json", receipt)
    campaign.bytes(
        "failure_manifest.sha256",
        f"{receipt['failure_manifest_sha256']}  failure_manifest.json\n".encode(),
    )
    _validate_failure_receipt(receipt, state=writer.state)


def _protocol_failure_is_sealable(writer: _Writer) -> bool:
    return not any(
        (writer.root / relative).exists()
        for relative in ("manifest.json", "manifest.sha256", "runtime.json", "projection.json")
    )


def _projection(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) != 64 or any(not row["valid"] or not row["primary"] for row in rows):
        raise V4MeasurementError("primary rows differ")
    models = sorted({row["model"] for row in rows})
    case_ids = sorted({row["case_id"] for row in rows})
    expected_schedule = {
        (case_id, mode, model, repetition)
        for case_id in case_ids
        for mode in _MODES
        for model in models
        for repetition in (1, 2)
    }
    actual_schedule = {
        (row["case_id"], row["mode"], row["model"], row["repetition"]) for row in rows
    }
    if len(models) != 2 or len(case_ids) != 8 or actual_schedule != expected_schedule:
        raise V4MeasurementError("projection model topology differs")
    if any(type(row["tclr"]) is not bool for row in rows):
        raise V4MeasurementError("projection TCLR type differs")

    def cell(
        selected: list[dict[str, Any]], model: str, mode: str, denominator: int
    ) -> dict[str, Any]:
        if len(selected) != denominator:
            raise V4MeasurementError("projection denominator differs")
        numerator = sum(row["tclr"] for row in selected)
        return {
            "denominator": denominator,
            "metric": "tclr",
            "model": model,
            "mode": mode,
            "numerator": numerator,
            "rate_fixed_decimal": str(
                (Decimal(numerator) / Decimal(denominator)).quantize(
                    Decimal("0.000001"), rounding=ROUND_HALF_UP
                )
            ),
            "split": "primary",
        }

    cells = []
    for model in models:
        for mode in _MODES:
            cells.append(
                cell(
                    [
                        row
                        for row in rows
                        if row["model"] == model and row["mode"] == mode
                    ],
                    model,
                    mode,
                    16,
                )
            )
    for mode in _MODES:
        cells.append(
            cell([row for row in rows if row["mode"] == mode], "pooled", mode, 32)
        )

    pairs = []
    for model in models:
        for case_id in sorted({row["case_id"] for row in rows}):
            for repetition in (1, 2):
                pair = {
                    row["mode"]: row
                    for row in rows
                    if row["model"] == model
                    and row["case_id"] == case_id
                    and row["repetition"] == repetition
                }
                if set(pair) != set(_MODES):
                    raise V4MeasurementError("paired trajectory topology differs")
                unrestricted = int(pair["unrestricted"]["tclr"])
                enforced = int(pair["enforced"]["tclr"])
                unrestricted_denominator = 1
                enforced_denominator = 1
                cross = (
                    unrestricted * enforced_denominator
                    - enforced * unrestricted_denominator
                )
                pairs.append(
                    {
                        "case_id": case_id,
                        "enforced_denominator": enforced_denominator,
                        "enforced_numerator": enforced,
                        "model": model,
                        "repetition": repetition,
                        "sign_class": "positive"
                        if cross > 0
                        else "negative"
                        if cross < 0
                        else "zero",
                        "unrestricted_denominator": unrestricted_denominator,
                        "unrestricted_numerator": unrestricted,
                    }
                )
    diagnostics = []
    for row in rows:
        diagnostics.append(
            {
                "case_id": row["case_id"],
                "mode": row["mode"],
                "model": row["model"],
                "query_nonblank": row["query_nonblank"],
                "repetition": row["repetition"],
                "restatement_returned": row["restatement_returned"],
                "survivorship_case": row["survivorship_case"],
                "trajectory_id": row["trajectory_id"],
            }
        )
    return {
        "cells": cells,
        "diagnostics": diagnostics,
        "paired_tclr_reductions": pairs,
        "schema_version": "anachron-v4-answer-free-projection-v3",
        "split_counts": {
            "compatibility_trajectories": 2,
            "development_trajectories": 0,
            "primary_cases": 8,
            "primary_trajectories": 64,
        },
        "topology": {
            "compatibility_chats": 4,
            "main_chats": 128,
            "models": 2,
            "modes": 2,
            "repetitions": 2,
            "total_chats": 132,
        },
        "v3_included_count": 0,
    }


def run_measurement(
    full_plan: Path,
    conditional_go: Path,
    source_audit: Path,
    runtime_identity: Path,
    output: Path,
    *,
    repository_root: Path,
    compatibility_plan: Path | None = None,
    comparison: Path | None = None,
    source_manifest: Path | None = None,
    transport: Callable | None = None,
    preflight_only: bool = False,
    expected_source_origin: str | None = None,
    expected_v3: dict[str, str] | None = None,
) -> dict[str, Any]:
    root = _admit_repository_root(repository_root)
    validate_authority_contract(root)
    registry, cards = load_v4_registry(root)
    compat_card = load_compatibility_case(root)
    if source_manifest is None:
        raise V4MeasurementError("source manifest is required")
    if compatibility_plan is None or comparison is None:
        raise V4MeasurementError("compatibility plan and comparison are required")
    try:
        compatibility_plan = admit_external_regular_input(
            compatibility_plan, root, "compatibility plan"
        )
        full_plan = admit_external_regular_input(full_plan, root, "full plan")
        conditional_go = admit_external_regular_input(
            conditional_go, root, "conditional GO"
        )
        source_audit = admit_external_regular_input(
            source_audit, root, "source audit"
        )
        runtime_identity = admit_external_regular_input(
            runtime_identity, root, "runtime identity"
        )
        source_manifest = admit_external_regular_input(
            source_manifest, root, "source manifest"
        )
        comparison = admit_external_regular_input(
            comparison, root, "comparison projection"
        )
        output = admit_create_only_external_output(output, root, "evidence root")
    except V4PathError as error:
        raise V4MeasurementError(str(error)) from error
    _, comparison_raw = _load(comparison, "comparison")
    source_manifest_raw = source_manifest.read_bytes()
    _release, _ = _authoritative_comparison(
        root,
        source_manifest_raw,
        comparison_raw,
        expected_source_origin=expected_source_origin,
        expected_v3=expected_v3,
    )
    audit, audit_raw = _audit(root, source_audit, source_manifest_raw, comparison_raw)
    identity, identity_raw = _identity(
        runtime_identity, source_manifest_raw, comparison_raw
    )
    _compatibility, compatibility_raw = _compatibility_plan(
        compatibility_plan,
        root,
        audit_raw,
        identity_raw,
        source_manifest_raw,
        comparison_raw,
    )
    plan, plan_raw = _plan(
        full_plan,
        root,
        audit_raw,
        identity_raw,
        source_manifest_raw,
        comparison_raw,
        compatibility_raw,
    )
    go = _go(
        conditional_go,
        root,
        plan,
        plan_raw,
        audit_raw,
        identity_raw,
        compatibility_raw,
        comparison_raw,
    )
    if registry["case_count"] != 8 or plan["models"] != identity["models"]:
        raise V4MeasurementError("panel identity differs")
    admission = admit_source(
        plan,
        root,
        source_manifest,
        expected_source_origin=expected_source_origin,
        expected_v3=expected_v3,
    )
    validate_source_admission(
        plan,
        admission,
        root,
        expected_source_origin=expected_source_origin,
        expected_v3=expected_v3,
    )
    if preflight_only:
        return {
            "authorized": True,
            "compatibility_plan_sha256": sha256_bytes(compatibility_raw),
            "full_plan_sha256": sha256_bytes(plan_raw),
            "preflight_only": True,
            "v3_included_count": 0,
        }
    endpoint = _loopback(plan["endpoint"])
    transport = _bounded_transport(transport or _http)
    state = _CampaignState()
    campaign = _Writer(output, root, "evidence root", state)
    comp = _Writer(campaign.root / "compatibility", root, "compatibility evidence", state)
    comp.bytes("compatibility_plan.json", compatibility_raw)
    comp.json("plan.json", plan)
    comp.json("conditional_go.json", go)
    comp.json("source_audit.json", audit)
    comp.bytes("runtime_identity.json", identity_raw)
    comp.json("source_admission.json", admission)
    comp.bytes("source_manifest.json", source_manifest_raw)
    comp.bytes("comparison.json", comparison_raw)
    try:
        before = _identity_checkpoint(
            comp,
            endpoint,
            transport,
            plan["models"],
            identity,
            "before_compatibility",
            "compatibility",
        )
        traces = []
        for i, model in enumerate(plan["models"], 1):
            trace = _trace(
                comp,
                endpoint,
                transport,
                plan,
                compat_card,
                model,
                "enforced",
                1,
                f"compatibility-{i:02d}",
                False,
            )
            traces.append(trace)
            if trace["valid"] is not True:
                raise V4MeasurementError("compatibility failed")
        comp.json(
            "runtime.json",
            {
                "chat_count": 4,
                "identity_before": before,
                "schema_version": "anachron-v4-compatibility-runtime-v2",
                "traces": traces,
                "v3_included_count": 0,
            },
        )
        _manifest(comp)
    except _TraceFailure as error:
        _seal_failure(campaign, comp, error)
        raise V4MeasurementError("compatibility failed closed") from error
    except Exception as error:
        if not _protocol_failure_is_sealable(comp):
            raise V4MeasurementError("compatibility protocol failure is not sealable") from error
        failure = _TraceFailure(
            phase="compatibility",
            failed_step=state.attempted_chats,
            last_completed_step=state.completed_chats,
            fault_code="protocol",
            trajectory_id=None,
            failure_stage="protocol",
            failure_cause="protocol",
        )
        _seal_failure(campaign, comp, failure)
        raise V4MeasurementError("compatibility failed closed") from error
    full = _Writer(campaign.root / "full", root, "main evidence", state)
    try:
        between = _identity_checkpoint(
            full,
            endpoint,
            transport,
            plan["models"],
            identity,
            "between_phases",
            "main",
        )
        rows = []
        for card in cards.values():
            for mode in _MODES:
                for model in plan["models"]:
                    for repeat in (1, 2):
                        rows.append(
                            _trace(
                                full,
                                endpoint,
                                transport,
                                plan,
                                card,
                                model,
                                mode,
                                repeat,
                                f"primary-{card['case_id']}-{mode}-{model['name']}-r{repeat}",
                                True,
                            )
                        )
        if len(rows) != 64 or any(not x["valid"] for x in rows):
            raise V4MeasurementError("main failed")
        after = _identity_checkpoint(
            full,
            endpoint,
            transport,
            plan["models"],
            identity,
            "after_main",
            "main",
        )
        if before != between or between != after:
            raise V4MeasurementError("runtime identity drift")
        projection = _projection(rows)
        full.json(
            "compatibility_receipt.json",
            {
                "manifest_sha256": sha256_bytes(
                    (comp.root / "manifest.json").read_bytes()
                ),
                "trace_count": 2,
            },
        )
        full.json(
            "runtime.json",
            {
                "chat_count": 128,
                "identity_after": after,
                "identity_between": between,
                "primary_trajectories": rows,
                "schema_version": "anachron-v4-main-runtime-v2",
                "v3_included_count": 0,
            },
        )
        full.json("projection.json", projection)
        _manifest(full)
        return projection
    except _TraceFailure as error:
        _seal_failure(campaign, full, error)
        raise V4MeasurementError("main failed closed") from error
    except Exception as error:
        if not _protocol_failure_is_sealable(full):
            raise V4MeasurementError("main protocol failure is not sealable") from error
        failure = _TraceFailure(
            phase="main",
            failed_step=state.attempted_chats,
            last_completed_step=state.completed_chats,
            fault_code="protocol",
            trajectory_id=None,
            failure_stage="protocol",
            failure_cause="protocol",
        )
        _seal_failure(campaign, full, failure)
        raise V4MeasurementError("main failed closed") from error


def _replay_row(
    evidence: Path, row: dict[str, Any], card: dict[str, Any], plan: dict[str, Any]
) -> None:
    row = _map(
        row,
        {
            "as_of",
            "case_id",
            "final_request_sha256",
            "final_response_sha256",
            "first_request_sha256",
            "first_response_sha256",
            "mode",
            "model",
            "model_digest",
            "primary",
            "query",
            "query_nonblank",
            "repetition",
            "restatement_returned",
            "returned_ids",
            "survivorship_case",
            "tclr",
            "tool_result_sha256",
            "trajectory_id",
            "valid",
        },
        "trajectory",
    )
    identifier = _str(row["trajectory_id"], "trajectory id")
    if (
        row["as_of"] != card["as_of"]
        or row["case_id"] != card["case_id"]
        or row["mode"] not in _MODES
        or row["model_digest"]
        != next(
            model["digest"] for model in plan["models"] if model["name"] == row["model"]
        )
        or type(row["primary"]) is not bool
        or type(row["query_nonblank"]) is not bool
        or type(row["restatement_returned"]) is not bool
        or type(row["survivorship_case"]) is not bool
        or type(row["tclr"]) is not bool
        or row["repetition"] not in {1, 2}
    ):
        raise V4MeasurementError("trajectory identity differs")
    first_raw = (evidence / "raw" / f"{identifier}.first.request.json").read_bytes()
    if sha256_bytes(first_raw) != _sha(
        row["first_request_sha256"], "first request hash"
    ):
        raise V4MeasurementError("first request binding differs")
    first = _obj(first_raw, "first request")
    if first != first_request(row["model"], card["prompt"], plan["generation"]):
        raise V4MeasurementError("first request replay differs")
    first_response_raw = (
        evidence / "raw" / f"{identifier}.first.response.json"
    ).read_bytes()
    if sha256_bytes(first_response_raw) != _sha(
        row["first_response_sha256"], "first response hash"
    ):
        raise V4MeasurementError("first response binding differs")
    assistant, query = _envelope(first_response_raw, row["model"], card)
    if query != row.get("query"):
        raise V4MeasurementError("query replay differs")
    tool_result, records = _result(card, row["mode"], query)
    tool_raw = (evidence / "raw" / f"{identifier}.tool_result.json").read_bytes()
    if sha256_bytes(tool_raw) != _sha(row["tool_result_sha256"], "tool result hash"):
        raise V4MeasurementError("tool result binding differs")
    if tool_raw != tool_result.encode("utf-8"):
        raise V4MeasurementError("tool result replay differs")
    final_raw = (evidence / "raw" / f"{identifier}.final.request.json").read_bytes()
    if sha256_bytes(final_raw) != _sha(
        row["final_request_sha256"], "final request hash"
    ):
        raise V4MeasurementError("final request binding differs")
    if _obj(final_raw, "final request") != final_request(first, assistant, tool_result):
        raise V4MeasurementError("final request replay differs")
    final_response_raw = (
        evidence / "raw" / f"{identifier}.final.response.json"
    ).read_bytes()
    if sha256_bytes(final_response_raw) != _sha(
        row["final_response_sha256"], "final response hash"
    ):
        raise V4MeasurementError("final response binding differs")
    _envelope(final_response_raw, row["model"], None)
    if (
        row.get("returned_ids") != [record["id"] for record in records]
        or row["query_nonblank"] is not bool(query)
        or row["restatement_returned"]
        is not any("restates" in record for record in records)
        or row["survivorship_case"] is not card["category"].startswith("survivorship-")
        or row["tclr"]
        is not any(record["publish_date"] > card["as_of"] for record in records)
    ):
        raise V4MeasurementError("trajectory score replay differs")


def _expected_inventory(
    root: Path, rows: list[dict[str, Any]], *, compatibility: bool
) -> set[str]:
    expected = {"journal.jsonl", "manifest.json", "manifest.sha256", "runtime.json"}
    if compatibility:
        expected.update(
            {
                "conditional_go.json",
                "compatibility_plan.json",
                "comparison.json",
                "plan.json",
                "runtime_identity.json",
                "source_admission.json",
                "source_audit.json",
                "source_manifest.json",
                "raw/identity.before_compatibility.tags.json",
                "raw/identity.before_compatibility.version.json",
            }
        )
    else:
        expected.update(
            {
                "compatibility_receipt.json",
                "projection.json",
                "raw/identity.after_main.tags.json",
                "raw/identity.after_main.version.json",
                "raw/identity.between_phases.tags.json",
                "raw/identity.between_phases.version.json",
            }
        )
    for row in rows:
        identifier = _str(row["trajectory_id"], "inventory trajectory id")
        expected.update(
            {
                f"raw/{identifier}.final.request.json",
                f"raw/{identifier}.final.response.json",
                f"raw/{identifier}.first.request.json",
                f"raw/{identifier}.first.response.json",
                f"raw/{identifier}.tool_result.json",
            }
        )
    actual = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    if actual != expected:
        raise V4MeasurementError("evidence inventory differs")
    for relative in expected:
        _admit_regular_file(root / relative, "evidence file")
    return expected


def _replay_journal(
    root: Path,
    rows: list[dict[str, Any]],
    start_chat: int,
    *,
    trailing_phase_terminal: bool = False,
) -> None:
    raw = _admit_regular_file(root / "journal.jsonl", "journal").read_bytes()
    lines = raw.splitlines()
    if trailing_phase_terminal:
        lines = lines[:-1]
    if len(lines) != len(rows) * 4:
        raise V4MeasurementError("journal topology differs")
    for offset, row in enumerate(rows):
        identifier = _str(row["trajectory_id"], "journal trajectory id")
        claim, first, final, terminal = [
            _type(strict_json_loads(line, "journal"), dict, "journal")
            for line in lines[offset * 4 : offset * 4 + 4]
        ]
        if _map(claim, {"kind", "timestamp_utc", "trajectory_id"}, "journal claim") != {
            "kind": "trajectory_claim",
            "timestamp_utc": claim["timestamp_utc"],
            "trajectory_id": identifier,
        } or _map(
            terminal,
            {"kind", "timestamp_utc", "trajectory_id", "valid"},
            "journal terminal",
        ) != {
            "kind": "trajectory_terminal",
            "timestamp_utc": terminal["timestamp_utc"],
            "trajectory_id": identifier,
            "valid": True,
        }:
            raise V4MeasurementError("journal trajectory order differs")
        for record, chat_index in (
            (first, start_chat + offset * 2 + 1),
            (final, start_chat + offset * 2 + 2),
        ):
            record = _map(
                record,
                {"chat_index", "kind", "timestamp_utc", "trajectory_id"},
                "journal chat",
            )
            if (
                record["kind"] != "chat_completed"
                or record["trajectory_id"] != identifier
                or record["chat_index"] != chat_index
            ):
                raise V4MeasurementError("journal chat order differs")
        for value in (
            claim["timestamp_utc"],
            first["timestamp_utc"],
            final["timestamp_utc"],
            terminal["timestamp_utc"],
        ):
            _utc(value, "journal timestamp")


def _replay_identity_checkpoint(
    evidence: Path,
    name: str,
    models: list[dict[str, Any]],
    identity: dict[str, Any],
    recorded: dict[str, Any],
) -> None:
    version = (evidence / "raw" / f"identity.{name}.version.json").read_bytes()
    tags = (evidence / "raw" / f"identity.{name}.tags.json").read_bytes()
    _validate_identity(
        _obj(version, "checkpoint version", False),
        _obj(tags, "checkpoint tags", False),
        models,
    )
    expected = {
        "version_sha256": sha256_bytes(version),
        "tags_sha256": sha256_bytes(tags),
    }
    if recorded != expected or expected != {
        "version_sha256": identity["version_response_sha256"],
        "tags_sha256": identity["tags_response_sha256"],
    }:
        raise V4MeasurementError("identity checkpoint replay differs")


def analyze_compatibility(
    evidence: Path,
    *,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    if repository_root is None:
        raise V4MeasurementError("repository root is required for semantic replay")
    root = _admit_repository_root(repository_root)
    validate_authority_contract(root)
    try:
        evidence = admit_evidence_root(evidence, root, create=False)
        comp = admit_evidence_directory(evidence, "compatibility", "compatibility evidence")
    except V4PathError as error:
        raise V4MeasurementError(str(error)) from error
    _verify_manifest(comp)
    runtime, _ = _load(comp / "runtime.json", "compat runtime")
    runtime = _map(
        runtime,
        {
            "chat_count",
            "identity_before",
            "schema_version",
            "traces",
            "v3_included_count",
        },
        "compat runtime",
    )
    traces = _type(runtime["traces"], list, "compat traces")
    if (
        runtime["chat_count"] != 4
        or runtime["schema_version"] != "anachron-v4-compatibility-runtime-v2"
        or runtime["v3_included_count"] != 0
        or len(traces) != 2
        or any(
            x.get("primary") is not False or x.get("valid") is not True for x in traces
        )
    ):
        raise V4MeasurementError("compatibility replay differs")
    plan, _ = _load(comp / "plan.json", "embedded plan")
    audit_raw = (comp / "source_audit.json").read_bytes()
    identity_raw = (comp / "runtime_identity.json").read_bytes()
    source_manifest_raw = (comp / "source_manifest.json").read_bytes()
    comparison_raw = (comp / "comparison.json").read_bytes()
    compatibility_raw = (comp / "compatibility_plan.json").read_bytes()
    _authoritative_comparison(
        root,
        source_manifest_raw,
        comparison_raw,
    )
    _audit(
        root,
        comp / "source_audit.json",
        source_manifest_raw,
        comparison_raw,
    )
    identity, _ = _identity(
        comp / "runtime_identity.json", source_manifest_raw, comparison_raw
    )
    _compatibility_plan(
        comp / "compatibility_plan.json",
        root,
        audit_raw,
        identity_raw,
        source_manifest_raw,
        comparison_raw,
    )
    _plan(
        comp / "plan.json",
        root,
        audit_raw,
        identity_raw,
        source_manifest_raw,
        comparison_raw,
        compatibility_raw,
    )
    if sha256_bytes(source_manifest_raw) != plan["source_manifest_sha256"]:
        raise V4MeasurementError("source manifest replay differs")
    admission, _ = _load(comp / "source_admission.json", "source admission")
    validate_source_admission(plan, admission, root)
    _go(
        comp / "conditional_go.json",
        root,
        plan,
        (comp / "plan.json").read_bytes(),
        audit_raw,
        identity_raw,
        compatibility_raw,
        comparison_raw,
    )
    card = load_compatibility_case(root)
    _expected_inventory(comp, traces, compatibility=True)
    for row in traces:
        _replay_row(comp, row, card, plan)
    _replay_identity_checkpoint(
        comp,
        "before_compatibility",
        plan["models"],
        identity,
        _map(
            runtime["identity_before"],
            {"tags_sha256", "version_sha256"},
            "compat identity",
        ),
    )
    _replay_journal(comp, traces, 0)
    return {"chat_count": 4, "passed": True, "trace_count": 2, "v3_included_count": 0}


def analyze_measurement(
    evidence: Path,
    *,
    repository_root: Path | None = None,
    phase: str = "full",
) -> dict[str, Any]:
    if repository_root is None:
        raise V4MeasurementError("repository root is required for semantic replay")
    if phase == "compatibility":
        return analyze_compatibility(
            evidence,
            repository_root=repository_root,
        )
    if phase == "failure":
        return analyze_failure(evidence, repository_root=repository_root)
    if phase != "full":
        raise V4MeasurementError("analysis phase differs")
    root = _admit_repository_root(repository_root)
    try:
        evidence = admit_evidence_root(evidence, root, create=False)
    except V4PathError as error:
        raise V4MeasurementError(str(error)) from error
    analyze_compatibility(
        evidence,
        repository_root=repository_root,
    )
    try:
        full = admit_evidence_directory(evidence, "full", "main evidence")
    except V4PathError as error:
        raise V4MeasurementError(str(error)) from error
    _verify_manifest(full)
    runtime, _ = _load(full / "runtime.json", "main runtime")
    runtime = _map(
        runtime,
        {
            "chat_count",
            "identity_after",
            "identity_between",
            "primary_trajectories",
            "schema_version",
            "v3_included_count",
        },
        "main runtime",
    )
    rows = _type(runtime["primary_trajectories"], list, "primary trajectories")
    if (
        runtime["chat_count"] != 128
        or runtime["schema_version"] != "anachron-v4-main-runtime-v2"
        or runtime["v3_included_count"] != 0
        or len(rows) != 64
    ):
        raise V4MeasurementError("main replay differs")
    root = _admit_repository_root(repository_root)
    plan, _ = _load(evidence / "compatibility" / "plan.json", "embedded plan")
    _, cards = load_v4_registry(root)
    _expected_inventory(full, rows, compatibility=False)
    for row in rows:
        _replay_row(full, row, cards[row["case_id"]], plan)
    identity, _ = _identity(
        evidence / "compatibility" / "runtime_identity.json",
        (evidence / "compatibility" / "source_manifest.json").read_bytes(),
        (evidence / "compatibility" / "comparison.json").read_bytes(),
    )
    _replay_identity_checkpoint(
        full,
        "between_phases",
        plan["models"],
        identity,
        _map(
            runtime["identity_between"],
            {"tags_sha256", "version_sha256"},
            "between identity",
        ),
    )
    _replay_identity_checkpoint(
        full,
        "after_main",
        plan["models"],
        identity,
        _map(
            runtime["identity_after"],
            {"tags_sha256", "version_sha256"},
            "after identity",
        ),
    )
    receipt, _ = _load(full / "compatibility_receipt.json", "compatibility receipt")
    if receipt != {
        "manifest_sha256": sha256_bytes(
            (evidence / "compatibility" / "manifest.json").read_bytes()
        ),
        "trace_count": 2,
    }:
        raise V4MeasurementError("compatibility receipt replay differs")
    _replay_journal(full, rows, 4)
    projection = _projection(rows)
    recorded, raw = _load(full / "projection.json", "projection")
    if recorded != projection or raw != canonical_json_bytes(projection):
        raise V4MeasurementError("projection replay differs")
    return projection


def analyze_failure(
    evidence: Path, *, repository_root: Path | None = None
) -> dict[str, Any]:
    """Replay the invalid terminal only; failures are never scientific output."""

    if repository_root is None:
        raise V4MeasurementError("repository root is required for failure replay")
    root = _admit_repository_root(repository_root)
    try:
        evidence = admit_evidence_root(evidence, root, create=False)
    except V4PathError as error:
        raise V4MeasurementError(str(error)) from error
    controls = {
        "failure_manifest.json",
        "failure_manifest.sha256",
        "failure_receipt.json",
    }
    root_files = {
        path.relative_to(evidence).as_posix()
        for path in evidence.iterdir()
        if path.is_file()
    }
    if root_files != controls:
        raise V4MeasurementError("failure root controls differ")
    receipt_path = admit_evidence_regular_file(
        evidence, "failure_receipt.json", "failure receipt"
    )
    failure, _ = _load(receipt_path, "failure receipt")
    failure = _validate_failure_receipt(failure)
    manifest_path = admit_evidence_regular_file(
        evidence, "failure_manifest.json", "failure manifest"
    )
    manifest, manifest_raw = _load(manifest_path, "failure manifest")
    if sha256_bytes(manifest_raw) != failure["failure_manifest_sha256"]:
        raise V4MeasurementError("failure manifest binding differs")
    digest_path = admit_evidence_regular_file(
        evidence, "failure_manifest.sha256", "failure manifest digest"
    )
    if digest_path.read_bytes() != (
        f"{failure['failure_manifest_sha256']}  failure_manifest.json\n".encode()
    ):
        raise V4MeasurementError("failure manifest digest differs")
    manifest = _map(manifest, {"files", "schema_version"}, "failure manifest")
    rows = _type(manifest["files"], list, "failure manifest files")
    paths: list[str] = []
    for row in rows:
        row = _map(row, {"path", "sha256"}, "failure manifest row")
        path = _str(row["path"], "failure manifest path")
        target = admit_evidence_regular_file(evidence, path, "failure manifest target")
        if sha256_bytes(target.read_bytes()) != _sha(
            row["sha256"], "failure manifest hash"
        ):
            raise V4MeasurementError("failure manifest file differs")
        paths.append(path)
    actual = sorted(
        path.relative_to(evidence).as_posix()
        for path in evidence.rglob("*")
        if path.is_file() and path.relative_to(evidence).as_posix() not in controls
    )
    if (
        manifest["schema_version"] != "anachron-v4-failure-manifest-v2"
        or paths != sorted(paths)
        or len(set(paths)) != len(paths)
        or paths != actual
    ):
        raise V4MeasurementError("failure manifest inventory differs")
    try:
        failed = admit_evidence_directory(
            evidence, failure["phase_directory"], "failure phase evidence"
        )
    except V4PathError as error:
        raise V4MeasurementError(str(error)) from error
    for forbidden in ("manifest.json", "manifest.sha256", "runtime.json", "projection.json"):
        if (failed / forbidden).exists():
            raise V4MeasurementError("failure contains scientific output")
    if failure["phase"] == "main":
        analyze_compatibility(evidence, repository_root=repository_root)
    _validate_failure_schedule(root, evidence, failure)
    _replay_failure_journal(root, evidence, failed, failure)
    _replay_failure_raw_prefix(root, evidence, failed, failure)
    return {
        "operational_status": "invalid",
        "phase": failure["phase"],
        "scientific_result": False,
        "v3_included_count": 0,
    }


def _validate_failure_schedule(
    repository_root: Path, evidence: Path, failure: dict[str, Any]
) -> None:
    """Bind a claimed failed chat to the frozen compatibility or primary schedule."""

    trajectory = failure["trajectory_id"]
    if trajectory is None:
        return
    failed_index = failure["failed_chat_index"]
    if type(failed_index) is not int:
        raise V4MeasurementError("failure schedule index differs")
    plan, _ = _load(evidence / "compatibility" / "plan.json", "failure plan")
    models = _models(plan.get("models"), "failure plan models")
    if failure["phase"] == "compatibility":
        expected = f"compatibility-{(failed_index + 1) // 2:02d}"
    else:
        _, cards = load_v4_registry(repository_root)
        trajectories = [
            f"primary-{card['case_id']}-{mode}-{model['name']}-r{repeat}"
            for card in cards.values()
            for mode in _MODES
            for model in models
            for repeat in (1, 2)
        ]
        expected = trajectories[(failed_index - 5) // 2]
    if trajectory != expected:
        raise V4MeasurementError("failure trajectory schedule differs")


def _scheduled_failure_trajectories(
    repository_root: Path, evidence: Path
) -> list[dict[str, str]]:
    plan, _ = _load(evidence / "compatibility" / "plan.json", "failure plan")
    models = _models(plan.get("models"), "failure plan models")
    _, cards = load_v4_registry(repository_root)
    return [
        {
            "trajectory_id": (
                f"primary-{card['case_id']}-{mode}-{model['name']}-r{repeat}"
            )
        }
        for card in cards.values()
        for mode in _MODES
        for model in models
        for repeat in (1, 2)
    ]


def _protocol_completed_trajectories(
    repository_root: Path, evidence: Path, failure: dict[str, Any]
) -> tuple[list[dict[str, str]], int]:
    completed = failure["completed_chat_count"]
    if failure["phase"] == "compatibility":
        return (
            [
                {"trajectory_id": f"compatibility-{index:02d}"}
                for index in range(1, completed // 2 + 1)
            ],
            0,
        )
    phase_completed = completed - 4
    return (
        _scheduled_failure_trajectories(repository_root, evidence)[: phase_completed // 2],
        4,
    )


def _replay_failure_journal(
    repository_root: Path, evidence: Path, failed: Path, failure: dict[str, Any]
) -> None:
    journal = admit_evidence_regular_file(failed, "journal.jsonl", "failure journal")
    lines = journal.read_bytes().splitlines()
    parsed = [
        _type(strict_json_loads(line, "failure journal"), dict, "failure journal")
        for line in lines
    ]
    if not parsed:
        raise V4MeasurementError("failure journal differs")
    terminal = _map(
        parsed[-1],
        {
            "attempted_chat_count",
            "completed_chat_count",
            "failed_chat_index",
            "failure_stage",
            "fault_code",
            "kind",
            "last_completed_chat_index",
            "phase",
            "timestamp_utc",
            "valid",
        },
        "failure phase terminal",
    )
    expected_terminal = {
        key: failure[key]
        for key in (
            "attempted_chat_count",
            "completed_chat_count",
            "failed_chat_index",
            "failure_stage",
            "fault_code",
            "last_completed_chat_index",
            "phase",
        )
    }
    if (
        terminal["kind"] != "phase_terminal"
        or terminal["valid"] is not False
        or {key: terminal[key] for key in expected_terminal} != expected_terminal
    ):
        raise V4MeasurementError("failure phase terminal differs")
    _utc(terminal["timestamp_utc"], "failure phase terminal timestamp")
    if failure["trajectory_id"] is None:
        if failure["failure_stage"] == "protocol":
            rows, start_chat = _protocol_completed_trajectories(
                repository_root, evidence, failure
            )
            _replay_journal(
                failed, rows, start_chat, trailing_phase_terminal=True
            )
            if len(parsed) != len(rows) * 4 + 1:
                raise V4MeasurementError("protocol failure journal differs")
        elif failure["failure_stage"] == "identity_after_main_version" or failure[
            "failure_stage"
        ] == "identity_after_main_tags":
            _replay_journal(
                failed,
                _scheduled_failure_trajectories(repository_root, evidence),
                4,
                trailing_phase_terminal=True,
            )
            if len(parsed) != 64 * 4 + 1:
                raise V4MeasurementError("post-main identity journal differs")
        elif len(parsed) != 1:
            raise V4MeasurementError("identity failure journal differs")
        return
    if len(parsed) < 3:
        raise V4MeasurementError("trajectory failure journal differs")
    claim = _map(
        parsed[-3 if failure["failure_stage"].startswith("first_") else -4],
        {"kind", "timestamp_utc", "trajectory_id"},
        "failure claim",
    )
    if claim["kind"] != "trajectory_claim" or claim["trajectory_id"] != failure["trajectory_id"]:
        raise V4MeasurementError("failure claim differs")
    terminal_index = -2
    if failure["failure_stage"].startswith("final_"):
        completed = _map(
            parsed[-3],
            {"chat_index", "kind", "timestamp_utc", "trajectory_id"},
            "failure completed chat",
        )
        if (
            completed["kind"] != "chat_completed"
            or completed["trajectory_id"] != failure["trajectory_id"]
            or completed["chat_index"] != failure["completed_chat_count"]
        ):
            raise V4MeasurementError("failure completed chat differs")
    terminal_row = _map(
        parsed[terminal_index],
        {
            "attempted_chat_count",
            "completed_chat_count",
            "failed_chat_index",
            "failure_stage",
            "fault_code",
            "kind",
            "last_completed_chat_index",
            "timestamp_utc",
            "trajectory_id",
            "valid",
        },
        "failure trajectory terminal",
    )
    if (
        terminal_row["kind"] != "trajectory_terminal"
        or terminal_row["valid"] is not False
        or terminal_row["trajectory_id"] != failure["trajectory_id"]
        or {
            key: terminal_row[key]
            for key in expected_terminal
            if key != "phase"
        }
        != {key: failure[key] for key in expected_terminal if key != "phase"}
    ):
        raise V4MeasurementError("failure trajectory terminal differs")


def _replay_failure_raw_prefix(
    repository_root: Path, evidence: Path, failed: Path, failure: dict[str, Any]
) -> None:
    trajectory = failure["trajectory_id"]
    if trajectory is None:
        if failure["failure_stage"] == "protocol":
            _replay_protocol_failure_raw_inventory(
                repository_root, evidence, failed, failure
            )
        else:
            _replay_identity_failure_raw_inventory(
                repository_root, evidence, failed, failure
            )
        return
    stem = f"raw/{trajectory}"
    stage = failure["failure_stage"]
    required = {f"{stem}.first.request.json"}
    if stage.startswith("first_chat_"):
        if stage.endswith("resource"):
            required.add(f"{stem}.first.response.prefix.bin")
        elif stage.endswith("envelope"):
            required.add(f"{stem}.first.response.json")
    else:
        required.update(
            {
                f"{stem}.first.response.json",
                f"{stem}.tool_result.json",
                f"{stem}.final.request.json",
            }
        )
        if stage.endswith("resource"):
            required.add(f"{stem}.final.response.prefix.bin")
        elif stage.endswith("envelope"):
            required.add(f"{stem}.final.response.json")
    actual = {
        path.relative_to(failed).as_posix()
        for path in (failed / "raw").glob(f"{trajectory}.*")
        if path.is_file()
    }
    if actual != required:
        raise V4MeasurementError("failure raw prefix differs")
    for relative in required:
        target = admit_evidence_regular_file(failed, relative, "failure raw evidence")
        if relative.endswith(".prefix.bin") and target.stat().st_size != failure[
            "retained_prefix_bytes"
        ]:
            raise V4MeasurementError("failure prefix length differs")
    allowed_suffixes = {
        ".first.request.json",
        ".first.response.json",
        ".first.response.prefix.bin",
        ".tool_result.json",
        ".final.request.json",
        ".final.response.json",
        ".final.response.prefix.bin",
    }
    for path in (failed / "raw").glob("*"):
        if path.is_file() and not (
            any(path.name.endswith(suffix) for suffix in allowed_suffixes)
            or re.fullmatch(
                r"identity\.(?:before_compatibility|between_phases|after_main)\.(?:version|tags)\.json",
                path.name,
            )
            or re.fullmatch(
                r"identity\.(?:before_compatibility|between_phases|after_main)\.(?:version|tags)\.response\.prefix\.bin",
                path.name,
            )
        ):
            raise V4MeasurementError("failure raw inventory differs")


def _replay_identity_failure_raw_inventory(
    repository_root: Path, evidence: Path, failed: Path, failure: dict[str, Any]
) -> None:
    stage = failure["failure_stage"]
    expected: set[str] = set()
    checkpoint, endpoint = stage.removeprefix("identity_").rsplit("_", 1)
    if endpoint == "tags":
        expected.add(f"raw/identity.{checkpoint}.version.json")
    if failure["raw_response_state"] == "prefix":
        expected.add(f"raw/identity.{checkpoint}.{endpoint}.response.prefix.bin")
    elif failure["raw_response_state"] == "complete":
        expected.add(f"raw/identity.{checkpoint}.{endpoint}.json")
    if stage.startswith("identity_after_main_"):
        expected.update(
            {
                f"raw/{row['trajectory_id']}{suffix}"
                for row in _scheduled_failure_trajectories(repository_root, evidence)
                for suffix in (
                    ".first.request.json",
                    ".first.response.json",
                    ".tool_result.json",
                    ".final.request.json",
                    ".final.response.json",
                )
            }
        )
        expected.update(
            {
                "raw/identity.between_phases.version.json",
                "raw/identity.between_phases.tags.json",
            }
        )
    actual = {
        path.relative_to(failed).as_posix()
        for path in (failed / "raw").glob("*")
        if path.is_file()
    }
    if actual != expected:
        raise V4MeasurementError("identity failure raw inventory differs")
    for relative in expected:
        target = admit_evidence_regular_file(
            failed, relative, "identity failure raw evidence"
        )
        if relative.endswith(".prefix.bin") and target.stat().st_size != failure[
            "retained_prefix_bytes"
        ]:
            raise V4MeasurementError("identity failure prefix length differs")


def _replay_protocol_failure_raw_inventory(
    repository_root: Path, evidence: Path, failed: Path, failure: dict[str, Any]
) -> None:
    rows, _ = _protocol_completed_trajectories(repository_root, evidence, failure)
    expected = {
        f"raw/{row['trajectory_id']}{suffix}"
        for row in rows
        for suffix in (
            ".first.request.json",
            ".first.response.json",
            ".tool_result.json",
            ".final.request.json",
            ".final.response.json",
        )
    }
    if failure["phase"] == "compatibility" and rows:
        expected.update(
            {
                "raw/identity.before_compatibility.version.json",
                "raw/identity.before_compatibility.tags.json",
            }
        )
    if failure["phase"] == "main":
        expected.update(
            {
                "raw/identity.between_phases.version.json",
                "raw/identity.between_phases.tags.json",
            }
        )
        if len(rows) == 64:
            expected.update(
                {
                    "raw/identity.after_main.version.json",
                    "raw/identity.after_main.tags.json",
                }
            )
    actual = {
        path.relative_to(failed).as_posix()
        for path in (failed / "raw").glob("*")
        if path.is_file()
    }
    if actual != expected:
        raise V4MeasurementError("protocol failure raw inventory differs")
    for relative in expected:
        admit_evidence_regular_file(failed, relative, "protocol failure raw evidence")
