"""External-audit carry-forward admission for the byte-identical v5 panel."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from anachron.v5_contract import V5_PROTOCOL_TAG
from anachron.v5_custody import V5CustodyError, capture_regular
from anachron.v5_paths import (
    V5PathError,
    admit_external_regular_input,
    admit_repository_root,
)
from anachron.v5_registry import canonical_json_bytes, strict_json_loads

V4_TAG = "v4-measurement-protocol-v3"
V4_TAG_OBJECT = "f981b1b50ee47566b71e4f44ff597ef76cb71f4b"
V4_TAG_PEELED = "499f656e98f85c97dbe21e5f0773a2b0bb9782c8"
V5_TAG = V5_PROTOCOL_TAG
_HEX40 = set("0123456789abcdef")


class V5CarryForwardError(ValueError):
    """Raised when accepted v4 authority cannot admit the v5 copied panel."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _mapping(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise V5CarryForwardError(f"{label} fields differ")
    return value


def _hex(value: object, length: int, label: str) -> str:
    if type(value) is not str or len(value) != length or any(character not in "0123456789abcdef" for character in value):
        raise V5CarryForwardError(f"{label} differs")
    return value


def _read_external(path: Path, root: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        admitted = admit_external_regular_input(path, root, label)
        raw = capture_regular(admitted, label, 1_048_576).raw
    except (OSError, V5CustodyError, V5PathError) as error:
        raise V5CarryForwardError(f"{label} path differs") from error
    value = strict_json_loads(raw, label)
    if type(value) is not dict or raw != canonical_json_bytes(value):
        raise V5CarryForwardError(f"{label} is not canonical JSON")
    return value, raw


def _file_bindings(value: object, paths: object, label: str) -> dict[str, dict[str, str]]:
    if type(value) is not list or type(paths) is not list or len(value) != len(paths):
        raise V5CarryForwardError(f"{label} topology differs")
    files: dict[str, dict[str, str]] = {}
    for expected_path, item in zip(paths, value, strict=True):
        item = _mapping(item, {"path", "sha256", "tag_blob_oid"}, f"{label} file")
        if type(expected_path) is not str or item["path"] != expected_path or item["path"] in files:
            raise V5CarryForwardError(f"{label} path differs")
        files[item["path"]] = {
            "sha256": _hex(item["sha256"], 64, f"{label} hash"),
            "tag_blob_oid": _hex(item["tag_blob_oid"], 40, f"{label} blob"),
        }
    return files


def validate_v5_source_manifest(value: object) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """Validate the canonical v5 tag-manifest schema without reading other modules."""

    manifest = _mapping(value, {"governed_files", "governed_paths", "release", "schema_version"}, "v5 source manifest")
    release = _mapping(
        manifest["release"],
        {
            "branch",
            "branch_ref",
            "commit",
            "origin",
            "remote_branch",
            "remote_tag_object",
            "remote_tag_peeled",
            "tag",
            "tag_object",
            "tag_peeled",
        },
        "v5 release",
    )
    if (
        manifest["schema_version"] != "anachron-v5-source-manifest-v2"
        or release["tag"] != V5_TAG
        or type(release["branch"]) is not str
        or type(release["origin"]) is not str
        or any(_hex(release[key], 40, f"v5 release {key}") != release[key] for key in ("branch_ref", "commit", "remote_branch", "remote_tag_object", "remote_tag_peeled", "tag_object", "tag_peeled"))
        or len({release["branch_ref"], release["commit"], release["tag_peeled"], release["remote_branch"], release["remote_tag_peeled"]}) != 1
        or release["tag_object"] != release["remote_tag_object"]
    ):
        raise V5CarryForwardError("v5 source manifest identity differs")
    return manifest, _file_bindings(manifest["governed_files"], manifest["governed_paths"], "v5 source manifest")


def _v4_manifest(value: object) -> dict[str, dict[str, str]]:
    manifest = _mapping(value, {"governed_files", "governed_paths", "release", "schema_version"}, "v4 source manifest")
    release = manifest["release"]
    if (
        manifest["schema_version"] != "anachron-v4-source-manifest-v1"
        or type(release) is not dict
        or release.get("tag") != V4_TAG
        or release.get("tag_object") != V4_TAG_OBJECT
        or release.get("tag_peeled") != V4_TAG_PEELED
    ):
        raise V5CarryForwardError("v4 source manifest identity differs")
    return _file_bindings(manifest["governed_files"], manifest["governed_paths"], "v4 source manifest")


def _accepted_rows(
    audit: object,
    manifest: dict[str, dict[str, str]],
    registry: dict[str, Any],
    manifest_sha: str,
) -> list[dict[str, str]]:
    audit = _mapping(
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
        "accepted v4 audit",
    )
    registry_binding = manifest.get("research/v4_measurement/case_registry.json")
    if (
        audit["schema_version"] != "anachron-v4-source-audit-v1"
        or audit["decision"] != "ACCEPT"
        or audit["source_manifest_sha256"] != manifest_sha
        or audit["v4_protocol_tag"] != V4_TAG
        or audit["v4_protocol_tag_object"] != V4_TAG_OBJECT
        or audit["v4_protocol_commit"] != V4_TAG_PEELED
        or registry_binding is None
        or audit["registry_sha256"] != registry_binding["sha256"]
        or audit["registry_tag_blob_sha256"] != registry_binding["sha256"]
        or audit["registry_tag_blob_oid"] != registry_binding["tag_blob_oid"]
        or type(audit["case_audits"]) is not list
        or len(audit["case_audits"]) != 8
    ):
        raise V5CarryForwardError("accepted v4 audit identity differs")
    accepted = []
    for entry, row in zip(registry["cases"], audit["case_audits"], strict=True):
        row = _mapping(
            row,
            {"case_id", "decision", "reason", "reviewed_at_utc", "tag_blob_oid", "tag_blob_sha256"},
            "accepted v4 audit row",
        )
        binding = manifest.get(f"research/v4_measurement/{entry['case_card']}")
        if (
            binding is None
            or row["case_id"] != entry["id"]
            or row["decision"] != "ACCEPT"
            or type(row["reason"]) is not str
            or not row["reason"].strip()
            or row["tag_blob_oid"] != binding["tag_blob_oid"]
            or row["tag_blob_sha256"] != binding["sha256"]
        ):
            raise V5CarryForwardError(f"accepted v4 audit row differs: {entry['id']}")
        accepted.append({"case_id": entry["id"], "reason": row["reason"], **binding})
    return accepted


def derive_carry_forward(
    repository_root: Path,
    *,
    accepted_audit: Path,
    v4_source_manifest: Path,
    v5_source_manifest: Path,
) -> dict[str, Any]:
    """Bind accepted v4 A/M and canonical v5 tag blobs to the copied panel bytes."""

    try:
        root = admit_repository_root(repository_root)
        audit, audit_raw = _read_external(accepted_audit, root, "accepted v4 audit")
        v4_source, v4_source_raw = _read_external(v4_source_manifest, root, "v4 source manifest")
        v5_source, v5_source_raw = _read_external(v5_source_manifest, root, "v5 source manifest")
        v4_files = _v4_manifest(v4_source)
        _, v5_files = validate_v5_source_manifest(v5_source)
        v4_registry_raw = capture_regular(root / "research/v4_measurement/case_registry.json", "v4 registry", 1_048_576).raw
        v5_registry_raw = capture_regular(root / "research/v5_measurement/case_registry.json", "v5 registry", 1_048_576).raw
    except (OSError, V5CustodyError, V5PathError) as error:
        raise V5CarryForwardError("carry-forward repository input differs") from error
    if v5_registry_raw != v4_registry_raw:
        raise V5CarryForwardError("v5 registry bytes differ")
    registry = strict_json_loads(v5_registry_raw, "v5 registry")
    if type(registry) is not dict or type(registry.get("cases")) is not list or len(registry["cases"]) != 8:
        raise V5CarryForwardError("v5 registry identity differs")
    accepted = _accepted_rows(audit, v4_files, registry, _sha(v4_source_raw))
    registry_v4 = v4_files.get("research/v4_measurement/case_registry.json")
    registry_v5 = v5_files.get("research/v5_measurement/case_registry.json")
    if (
        registry_v4 is None
        or registry_v5 is None
        or registry_v4["sha256"] != _sha(v5_registry_raw)
        or registry_v5["sha256"] != _sha(v5_registry_raw)
    ):
        raise V5CarryForwardError("registry tag binding differs")
    cards = []
    for entry, accepted_row in zip(registry["cases"], accepted, strict=True):
        relative = entry.get("case_card")
        if type(relative) is not str or accepted_row["case_id"] != entry.get("id"):
            raise V5CarryForwardError("registry case order differs")
        try:
            v4_card_raw = capture_regular(root / "research/v4_measurement" / relative, "v4 carried card", 1_048_576).raw
            v5_card_raw = capture_regular(root / "research/v5_measurement" / relative, "v5 carried card", 1_048_576).raw
        except (OSError, V5CustodyError) as error:
            raise V5CarryForwardError(f"v5 carried card is unavailable: {entry['id']}") from error
        v5_binding = v5_files.get(f"research/v5_measurement/{relative}")
        if (
            v5_card_raw != v4_card_raw
            or _sha(v5_card_raw) != accepted_row["sha256"]
            or v5_binding is None
            or v5_binding["sha256"] != accepted_row["sha256"]
        ):
            raise V5CarryForwardError(f"v5 carried card differs: {entry['id']}")
        cards.append(
            {
                "case_id": entry["id"],
                "reason": accepted_row["reason"],
                "sha256": accepted_row["sha256"],
                "v4_tag_blob_oid": accepted_row["tag_blob_oid"],
                "v5_tag_blob_oid": v5_binding["tag_blob_oid"],
            }
        )
    return {
        "accepted_v4_audit_sha256": _sha(audit_raw),
        "case_count": 8,
        "cards": cards,
        "registry": {
            "sha256": _sha(v5_registry_raw),
            "v4_tag_blob_oid": registry_v4["tag_blob_oid"],
            "v5_tag_blob_oid": registry_v5["tag_blob_oid"],
        },
        "schema_version": "anachron-v5-carry-forward-v3",
        "v4_included_count": 0,
        "v4_source_manifest_sha256": _sha(v4_source_raw),
        "v4_tag": V4_TAG,
        "v4_tag_object": V4_TAG_OBJECT,
        "v4_tag_peeled": V4_TAG_PEELED,
        "v5_source_manifest_sha256": _sha(v5_source_raw),
    }
