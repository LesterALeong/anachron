"""Materialize create-only v5 plans from exact external authority inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from anachron.v5_carry_forward import V5CarryForwardError, derive_carry_forward
from anachron.v5_contract import (
    AUTHORITY_CONTRACT_PATH,
    EXPECTED_RUNTIME_IDENTITY,
    validate_authority_contract,
)
from anachron.v5_custody import (
    ByteBudget,
    RootProfile,
    V5CustodyError,
    capture_regular,
    discard_staging_root,
    publish_staging_root,
    scandir_exact,
    write_create_only,
)
from anachron.v5_measurement import REPETITION_SEEDS, build_schedule
from anachron.v5_paths import (
    V5PathError,
    admit_create_only_external_output,
    admit_external_regular_input,
    admit_repository_root,
    fsync_directory,
)
from anachron.v5_registry import (
    canonical_json_bytes,
    load_v5_registry,
    strict_json_loads,
)
from tools.build_v5_source_manifest import V5SourceManifestError
from tools.build_v5_source_manifest import validate as validate_source_manifest


class V5MaterializationError(ValueError):
    """Raised when v5 authority inputs are stale, malformed, or non-create-only."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_external(path: Path, root: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        source = admit_external_regular_input(path, root, label)
        raw = capture_regular(source, label, 1_048_576).raw
    except (OSError, V5CustodyError, V5PathError) as error:
        raise V5MaterializationError(f"{label} cannot be read") from error
    value = strict_json_loads(raw, label)
    if type(value) is not dict or raw != canonical_json_bytes(value):
        raise V5MaterializationError(f"{label} is not canonical JSON")
    return value, raw


def _expected_identity(value: object) -> dict[str, Any]:
    expected = {"models": list(EXPECTED_RUNTIME_IDENTITY["models"]), "version": EXPECTED_RUNTIME_IDENTITY["version"]}
    if value != expected:
        raise V5MaterializationError("runtime identity differs from the frozen expected runtime")
    return expected


_MATERIALIZATION_MEMBERS = (
    "carry_forward.json",
    "compatibility_plan.json",
    "full_plan.json",
    "materialization_receipt.json",
    "schedule.json",
    "source_manifest.json",
)


def _materialization_profile() -> RootProfile:
    caps = {name: 1_048_576 for name in _MATERIALIZATION_MEMBERS}
    return RootProfile(_MATERIALIZATION_MEMBERS, sum(caps.values()), len(caps), caps)


def _write(destination: Path, value: object, label: str, budget: ByteBudget, maximum: int) -> bytes:
    raw = canonical_json_bytes(value)
    if len(raw) > maximum:
        raise V5MaterializationError(f"{label} exceeds the contract byte cap")
    try:
        write_create_only(destination, raw, label, budget)
    except V5CustodyError as error:
        raise V5MaterializationError(f"{label} cannot be written") from error
    return raw


def materialize(
    repository_root: Path,
    *,
    accepted_audit: Path,
    v4_source_manifest: Path,
    v5_source_manifest: Path,
    runtime_identity: Path,
    output: Path,
    evidence_output_root: Path | None = None,
    before_publish: Any = None,
) -> dict[str, Any]:
    """Create bound plans and the exact ordinal schedule outside the tagged checkout."""

    try:
        root = admit_repository_root(repository_root)
        destination = admit_create_only_external_output(output, root, "materialization output")
        runtime, runtime_raw = _load_external(runtime_identity, root, "runtime identity")
        expected_runtime = _expected_identity(runtime)
        source = validate_source_manifest(root, v5_source_manifest)
        source_raw = canonical_json_bytes(source)
        if source_raw != capture_regular(admit_external_regular_input(v5_source_manifest, root, "v5 source manifest"), "v5 source manifest", 1_048_576).raw:
            raise V5MaterializationError("v5 source manifest is not the tagged derivation")
        carry = derive_carry_forward(
            root,
            accepted_audit=accepted_audit,
            v4_source_manifest=v4_source_manifest,
            v5_source_manifest=v5_source_manifest,
        )
        carry_raw = canonical_json_bytes(carry)
        authority_hashes = validate_authority_contract(root)
        authority_raw = capture_regular(root / AUTHORITY_CONTRACT_PATH, "authority contract", 1_048_576).raw
        _, cards = load_v5_registry(root)
    except (OSError, V5CustodyError, V5PathError, V5CarryForwardError, V5SourceManifestError) as error:
        raise V5MaterializationError("materialization authority differs") from error
    if evidence_output_root is None:
        raise V5MaterializationError("exact external evidence output root is required")
    try:
        evidence = admit_create_only_external_output(evidence_output_root, root, "evidence output root")
        schedule = build_schedule(cards, [row["name"] for row in expected_runtime["models"]])
        release = source["release"]
        component_hashes = {
            "analyzer": authority_hashes["tools/analyze_v5_measurement.py"],
            "runner": authority_hashes["tools/run_v5_recovery.py"],
            "wrapper": authority_hashes["tools/run_v5_conditional_campaign.ps1"],
        }
        compatibility = {
            "models": expected_runtime["models"],
            "schema_version": "anachron-v5-compatibility-plan-v2",
            "trace_count": 2,
            "trace_schedule": [
                {"model": row["name"], "ordinal": ordinal, "seed": REPETITION_SEEDS[ordinal - 1]}
                for ordinal, row in enumerate(expected_runtime["models"], 1)
            ],
            "v4_included_count": 0,
        }
        profile = _materialization_profile()
        budget = profile.budget()
        staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
        compatibility_raw = _write(staging / "compatibility_plan.json", compatibility, "compatibility plan", budget, profile.member_cap("compatibility_plan.json"))
        full = {
            "authority_contract_sha256": _sha(authority_raw),
            "carry_forward_sha256": _sha(carry_raw),
            "compatibility_plan_sha256": _sha(compatibility_raw),
            "component_sha256": component_hashes,
            "evidence_output_root": str(evidence),
            "expected_runtime": expected_runtime,
            "protocol_release": {
                "commit": release["commit"],
                "tag": release["tag"],
                "tag_object": release["tag_object"],
            },
            "schedule": schedule,
            "schema_version": "anachron-v5-full-plan-v2",
            "seeds": list(REPETITION_SEEDS),
            "v4_included_count": 0,
            "v5_source_manifest_sha256": _sha(source_raw),
        }
        full_raw = _write(staging / "full_plan.json", full, "full plan", budget, profile.member_cap("full_plan.json"))
        schedule_raw = _write(staging / "schedule.json", {"rows": schedule, "v4_included_count": 0}, "schedule", budget, profile.member_cap("schedule.json"))
        _write(staging / "source_manifest.json", source, "source manifest", budget, profile.member_cap("source_manifest.json"))
        _write(staging / "carry_forward.json", carry, "carry-forward receipt", budget, profile.member_cap("carry_forward.json"))
        receipt = {
            "authority_contract_sha256": _sha(authority_raw),
            "carry_forward_sha256": _sha(carry_raw),
            "compatibility_plan_sha256": _sha(compatibility_raw),
            "full_plan_sha256": _sha(full_raw),
            "runtime_identity_sha256": _sha(runtime_raw),
            "schedule_sha256": _sha(schedule_raw),
            "schema_version": "anachron-v5-materialization-receipt-v2",
            "v4_included_count": 0,
            "v5_source_manifest_sha256": _sha(source_raw),
        }
        _write(staging / "materialization_receipt.json", receipt, "materialization receipt", budget, profile.member_cap("materialization_receipt.json"))
        scandir_exact(staging, _MATERIALIZATION_MEMBERS, len(_MATERIALIZATION_MEMBERS), "materialization staging")
        if before_publish is not None:
            before_publish()
        publish_staging_root(staging, destination, "materialization output")
        fsync_directory(destination.parent, "materialization output parent")
    except (OSError, V5CustodyError, V5PathError) as error:
        if "staging" in locals() and staging.exists():
            discard_staging_root(staging, "materialization staging", maximum_entries=6, maximum_depth=0)
        raise V5MaterializationError("materialization output cannot be finalized") from error
    except Exception:
        if "staging" in locals() and staging.exists():
            discard_staging_root(staging, "materialization staging", maximum_entries=6, maximum_depth=0)
        raise
    return receipt


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--accepted-audit", required=True, type=Path)
    parser.add_argument("--v4-source-manifest", required=True, type=Path)
    parser.add_argument("--v5-source-manifest", required=True, type=Path)
    parser.add_argument("--runtime-identity", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--evidence-output-root", required=True, type=Path)
    values = parser.parse_args(arguments)
    try:
        result = materialize(
            values.repository_root,
            accepted_audit=values.accepted_audit,
            v4_source_manifest=values.v4_source_manifest,
            v5_source_manifest=values.v5_source_manifest,
            runtime_identity=values.runtime_identity,
            output=values.output,
            evidence_output_root=values.evidence_output_root,
        )
    except V5MaterializationError as error:
        print(f"V5 materialization invalid: {error}")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
