"""Materialize post-tag v4 compatibility/full plans from external audited inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from anachron.v4_comparison import V4ComparisonError, derive_bytes
from anachron.v4_contract import canonical_json_bytes
from anachron.v4_measurement import (
    V4MeasurementError,
    _audit,
    _comparison,
    _identity,
    _source_manifest,
)
from anachron.v4_paths import (
    V4PathError,
    admit_create_only_external_output,
    admit_external_regular_input,
    admit_repository_root,
)
from tools.build_v4_source_manifest import V4SourceManifestError
from tools.build_v4_source_manifest import derive as derive_source_manifest


class V4MaterializationError(ValueError):
    """Raised when post-tag v4 inputs cannot be materialized exactly once."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise V4MaterializationError(f"{label} cannot be read") from error
    if type(value) is not dict or raw != canonical_json_bytes(value):
        raise V4MaterializationError(f"{label} is not canonical JSON")
    return value, raw


def _write(path: Path, value: object) -> bytes:
    raw = canonical_json_bytes(value)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
    return raw


def materialize(
    repository_root: Path,
    *,
    source_manifest: Path,
    comparison: Path,
    source_audit: Path,
    runtime_identity: Path,
    output: Path,
    expected_origin: str | None = None,
    expected_v3: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create C, F, and a receipt outside the tagged source checkout."""

    try:
        root = admit_repository_root(repository_root)
        inputs = {
            "source manifest": admit_external_regular_input(
                source_manifest, root, "source manifest"
            ),
            "comparison": admit_external_regular_input(
                comparison, root, "comparison projection"
            ),
            "source audit": admit_external_regular_input(
                source_audit, root, "source audit"
            ),
            "runtime identity": admit_external_regular_input(
                runtime_identity, root, "runtime identity"
            ),
        }
        destination = admit_create_only_external_output(
            output, root, "materialization output"
        )
    except V4PathError as error:
        raise V4MaterializationError(str(error)) from error
    try:
        source = derive_source_manifest(
            root,
            **({"expected_origin": expected_origin} if expected_origin is not None else {}),
            **({"expected_v3": expected_v3} if expected_v3 is not None else {}),
        )
        source_raw = canonical_json_bytes(source)
        if source_raw != inputs["source manifest"].read_bytes():
            raise V4MaterializationError("source manifest is not the tagged derivation")
        release, _ = _source_manifest(source_raw)
        comparison_raw = derive_bytes(
            root,
            v3_tag=release["v3_tag"],
            v4_tag=release["tag"],
        )
        _comparison(comparison_raw, release)
        if comparison_raw != inputs["comparison"].read_bytes():
            raise V4MaterializationError("comparison is not the tagged derivation")
    except (V4ComparisonError, V4MeasurementError, V4SourceManifestError) as error:
        raise V4MaterializationError("tagged materialization authority differs") from error
    try:
        _, audit_raw = _audit(
            root,
            inputs["source audit"],
            source_raw,
            comparison_raw,
        )
        identity, identity_raw = _identity(
            inputs["runtime identity"], source_raw, comparison_raw
        )
    except V4MeasurementError as error:
        raise V4MaterializationError(
            "external materialization input differs"
        ) from error
    compatibility, _ = _load(
        root / "research/v4_measurement/compatibility_plan.template.json",
        "compatibility template",
    )
    full, _ = _load(
        root / "research/v4_measurement/full_plan.template.json", "full template"
    )
    compatibility.update(
        {
            "comparison_projection_sha256": _sha(comparison_raw),
            "models": identity["models"],
            "release": {
                "commit": release["tag_peeled"],
                "tag": release["tag"],
                "tag_object": release["tag_object"],
            },
            "runtime_identity_sha256": _sha(identity_raw),
            "source_audit_sha256": _sha(audit_raw),
            "source_manifest_sha256": _sha(source_raw),
        }
    )
    destination.mkdir()
    _write(destination / "source_manifest.json", source)
    _write(destination / "comparison.json", _comparison(comparison_raw, release))
    compatibility_raw = _write(destination / "compatibility_plan.json", compatibility)
    full.update(
        {
            "comparison_projection_sha256": _sha(comparison_raw),
            "models": identity["models"],
            "registry_sha256": _sha(
                (root / "research/v4_measurement/case_registry.json").read_bytes()
            ),
            "release": {
                "commit": release["tag_peeled"],
                "tag": release["tag"],
                "tag_object": release["tag_object"],
            },
            "runtime_identity_sha256": _sha(identity_raw),
            "source_audit_sha256": _sha(audit_raw),
            "source_manifest_sha256": _sha(source_raw),
        }
    )
    full["compatibility"]["plan_sha256"] = _sha(compatibility_raw)
    full_raw = _write(destination / "full_plan.json", full)
    receipt = {
        "comparison_projection_sha256": _sha(comparison_raw),
        "compatibility_plan_sha256": _sha(compatibility_raw),
        "full_plan_sha256": _sha(full_raw),
        "runtime_identity_sha256": _sha(identity_raw),
        "schema_version": "anachron-v4-materialization-receipt-v1",
        "source_audit_sha256": _sha(audit_raw),
        "source_manifest_sha256": _sha(source_raw),
    }
    _write(destination / "materialization_receipt.json", receipt)
    return receipt


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--comparison", required=True, type=Path)
    parser.add_argument("--source-audit", required=True, type=Path)
    parser.add_argument("--runtime-identity", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    values = parser.parse_args(arguments)
    print(
        json.dumps(
            materialize(
                values.repository_root,
                source_manifest=values.source_manifest,
                comparison=values.comparison,
                source_audit=values.source_audit,
                runtime_identity=values.runtime_identity,
                output=values.output,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
