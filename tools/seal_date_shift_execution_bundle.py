"""Seal one external date-shift execution bundle after the personal audit."""

from __future__ import annotations

import argparse
import os
import shutil
import uuid
from pathlib import Path

from anachron.date_shift import DateShiftValidationError, bytes_sha256, canonical_sha256
from anachron.date_shift_bundle import (
    finalize_bundle_inputs,
    load_canonical_object,
    load_object,
    write_create_only,
)
from anachron.date_shift_provenance import admit_scaffold_repository


def _sync_tree(path: Path) -> None:
    for child in path.iterdir():
        if child.is_file():
            with child.open("r+b") as handle:
                os.fsync(handle.fileno())
    try:
        directory = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create one immutable external date-shift execution bundle."
    )
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--author-audit", required=True, type=Path)
    parser.add_argument("--runtime-preflight", required=True, type=Path)
    parser.add_argument("--bundle-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    provenance = admit_scaffold_repository(args.repository)
    bundle_dir = args.bundle_dir.resolve()
    if (
        bundle_dir.exists()
        or bundle_dir.with_name(bundle_dir.name + ".incomplete").exists()
    ):
        raise DateShiftValidationError("bundle directory already exists")
    plan = load_object(args.repository / "research/date-shift/execution_plan.json")
    proposed_frame = load_object(
        args.repository / "research/date-shift/proposed_frame.json"
    )
    proposed_items = load_object(
        args.repository / "research/date-shift/proposed_items.json"
    )
    audit, audit_raw = load_canonical_object(args.author_audit)
    runtime, runtime_raw = load_canonical_object(args.runtime_preflight)
    if runtime.get("capture_provenance") != provenance:
        raise DateShiftValidationError(
            "runtime preflight was not captured from this released scaffold"
        )
    frame, items, contract, schedule = finalize_bundle_inputs(
        proposed_frame, proposed_items, audit, plan, runtime
    )
    temporary = bundle_dir.with_name(
        bundle_dir.name + ".incomplete-" + uuid.uuid4().hex
    )
    temporary.mkdir(parents=True)
    try:
        artifacts = {
            "author_audit.json": audit,
            "runtime_preflight.json": runtime,
            "audited_frame.json": frame,
            "audited_items.json": items,
            "execution_contract.json": contract,
            "schedule.json": schedule,
            "execution_plan.json": plan,
        }
        for name, value in artifacts.items():
            write_create_only(temporary / name, value)
        raw_artifacts = {
            name: (temporary / name).read_bytes() for name in artifacts
        }
        if (
            raw_artifacts["author_audit.json"] != audit_raw
            or raw_artifacts["runtime_preflight.json"] != runtime_raw
        ):
            raise DateShiftValidationError(
                "sealed audit or runtime bytes differ from their admitted source"
            )
        hashes = {name: bytes_sha256(raw) for name, raw in raw_artifacts.items()}
        manifest_without_id = {
            "schema_version": "date-shift-execution-bundle-v2",
            "bundle_directory_name": bundle_dir.name,
            "scaffold_release_sha256": canonical_sha256(provenance),
            "author_audit_sha256": hashes["author_audit.json"],
            "runtime_preflight_sha256": hashes["runtime_preflight.json"],
            "contract_sha256": hashes["execution_contract.json"],
            "schedule_sha256": hashes["schedule.json"],
            "artifacts": hashes,
        }
        manifest = {
            **manifest_without_id,
            "bundle_id": canonical_sha256(manifest_without_id),
        }
        write_create_only(temporary / "bundle_manifest.json", manifest)
        write_create_only(
            temporary / "publication.json",
            {
                "schema_version": "date-shift-bundle-publication-v1",
                "bundle_id": manifest["bundle_id"],
                "bundle_directory_name": bundle_dir.name,
                "manifest_sha256": bytes_sha256(
                    (temporary / "bundle_manifest.json").read_bytes()
                ),
            },
        )
        _sync_tree(temporary)
        os.replace(temporary, bundle_dir)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    print(bytes_sha256((bundle_dir / "bundle_manifest.json").read_bytes()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
