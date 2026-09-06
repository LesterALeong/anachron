"""Answer-free v5 candidate projection admission and manuscript metadata."""

from __future__ import annotations

import hashlib
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from anachron.v5_contract import AUTHORITY_CONTRACT_PATH, validate_authority_contract
from anachron.v5_custody import (
    SUCCESS_MAX_ENTRIES,
    ByteBudget,
    V5CustodyError,
    bounded_regular_files,
    capture_regular,
    write_create_only,
)
from anachron.v5_measurement import (
    FINAL_CATEGORIES,
    FIRST_CATEGORIES,
    V5MeasurementError,
    analyze_measurement,
    bounded_evidence_bytes,
)
from anachron.v5_paths import (
    V5PathError,
    admit_create_only_external_output,
    admit_existing,
    admit_external_regular_input,
    admit_repository_root,
    assert_no_alternate_data_streams,
    fsync_directory,
)
from anachron.v5_registry import canonical_json_bytes, strict_json_loads


class CandidateProjectionError(ValueError):
    """Raised when v5 evidence cannot support a candidate projection."""


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path, label: str, *, maximum: int = 1_048_576) -> tuple[dict[str, Any], bytes]:
    try:
        raw = capture_regular(path, label, maximum).raw
    except V5CustodyError as error:
        raise CandidateProjectionError(str(error)) from error
    try:
        value = strict_json_loads(raw, label)
    except ValueError as error:
        raise CandidateProjectionError(f"{label} is not JSON") from error
    if type(value) is not dict or raw != canonical_json_bytes(value):
        raise CandidateProjectionError(f"{label} is not canonical JSON")
    return value, raw


def _hex(value: object, label: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise CandidateProjectionError(f"{label} differs")
    return value


def _evidence_closure(evidence: Path) -> dict[str, Any]:
    try:
        assert_no_alternate_data_streams(evidence)
    except V5PathError as error:
        raise CandidateProjectionError("v5 evidence has unsafe paths") from error
    try:
        paths = bounded_regular_files(
            evidence,
            "v5 evidence closure",
            maximum_entries=SUCCESS_MAX_ENTRIES,
            maximum_depth=2,
        )
    except V5CustodyError as error:
        raise CandidateProjectionError(str(error)) from error
    rows = [
        {
            "path": relative,
            "sha256": sha256_bytes(
                bounded_evidence_bytes(evidence, relative, "candidate evidence")
            ),
        }
        for relative in paths
    ]
    if not rows or "failure_receipt.json" in {row["path"] for row in rows}:
        raise CandidateProjectionError("v5 evidence is incomplete or operationally failed")
    return {
        "files": rows,
        "sha256": sha256_bytes(canonical_json_bytes(rows)),
        "schema_version": "anachron-v5-whole-evidence-closure-v1",
    }


def _closure_bytes(evidence: Path, relative: str, label: str) -> bytes:
    path = evidence / relative
    try:
        admitted = admit_existing(path, label)
    except V5PathError as error:
        raise CandidateProjectionError(f"{label} is absent from v5 evidence") from error
    if not admitted.is_file() or admitted.parent != (evidence / Path(relative).parent):
        raise CandidateProjectionError(f"{label} has unsafe evidence topology")
    return bounded_evidence_bytes(evidence, relative, label)


def _same_bytes(external: bytes, copied: bytes, label: str) -> None:
    if external != copied:
        raise CandidateProjectionError(f"{label} differs from the evidence authority copy")


def _fixed_rate(numerator: int, denominator: int) -> str | None:
    if not denominator:
        return None
    return str((Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _validated_summary(summary: object) -> dict[str, Any]:
    if type(summary) is not dict or set(summary) != {"groups", "paired_contrasts", "scheduled", "v4_included_count"}:
        raise CandidateProjectionError("v5 replay summary schema differs")
    if summary["v4_included_count"] != 0 or type(summary["scheduled"]) is not int or summary["scheduled"] != 64:
        raise CandidateProjectionError("v5 replay summary inclusion or schedule differs")
    groups = summary["groups"]
    if type(groups) is not dict or len(groups) != 4:
        raise CandidateProjectionError("v5 replay summary group topology differs")
    projection_groups: list[dict[str, Any]] = []
    total_scheduled = 0
    for key in sorted(groups):
        if "::" not in key:
            raise CandidateProjectionError("v5 replay summary group key differs")
        model, mode = key.split("::", 1)
        group = groups[key]
        required = {"adherence_rate", "adherent", "conditional_exposure_denominator", "conditional_exposure_numerator", "conditional_exposure_rate", "final_categories", "first_categories", "scheduled"}
        if type(group) is not dict or set(group) != required or mode not in {"enforced", "unrestricted"} or not model:
            raise CandidateProjectionError("v5 replay summary group schema differs")
        if type(group["scheduled"]) is not int or group["scheduled"] != 16:
            raise CandidateProjectionError("v5 replay summary group schedule differs")
        for field in ("adherent", "conditional_exposure_denominator", "conditional_exposure_numerator"):
            if type(group[field]) is not int or not 0 <= group[field] <= 16:
                raise CandidateProjectionError("v5 replay summary group denominator differs")
        if group["adherent"] != group["conditional_exposure_denominator"]:
            raise CandidateProjectionError("v5 conditional denominator differs")
        if group["adherence_rate"] != group["adherent"] / 16:
            raise CandidateProjectionError("v5 adherence rate differs")
        expected_rate = None if not group["conditional_exposure_denominator"] else group["conditional_exposure_numerator"] / group["conditional_exposure_denominator"]
        if group["conditional_exposure_rate"] != expected_rate:
            raise CandidateProjectionError("v5 conditional exposure rate differs")
        for field, categories, total in (("first_categories", FIRST_CATEGORIES, 16), ("final_categories", FINAL_CATEGORIES, group["adherent"])):
            counts = group[field]
            if type(counts) is not dict or set(counts) != set(categories) or any(type(count) is not int or count < 0 for count in counts.values()) or sum(counts.values()) != total:
                raise CandidateProjectionError(f"v5 {field} differs")
        if group["first_categories"]["valid"] != group["adherent"]:
            raise CandidateProjectionError("v5 first-category adherence differs")
        total_scheduled += 16
        projection_groups.append({
            "adherence_denominator": 16,
            "adherence_numerator": group["adherent"],
            "adherence_rate_fixed_decimal": _fixed_rate(group["adherent"], 16),
            "conditional_exposure_denominator": group["conditional_exposure_denominator"],
            "conditional_exposure_numerator": group["conditional_exposure_numerator"],
            "conditional_exposure_rate_fixed_decimal": _fixed_rate(group["conditional_exposure_numerator"], group["conditional_exposure_denominator"]),
            "final_categories": group["final_categories"],
            "first_categories": group["first_categories"],
            "mode": mode,
            "model": model,
            "scheduled": 16,
        })
    if total_scheduled != summary["scheduled"]:
        raise CandidateProjectionError("v5 replay total differs")
    contrasts = summary["paired_contrasts"]
    if type(contrasts) is not list or len(contrasts) > 32:
        raise CandidateProjectionError("v5 paired contrasts differ")
    for row in contrasts:
        if type(row) is not dict or set(row) != {"case_id", "exposure_delta_unrestricted_minus_enforced", "model", "repetition"} or type(row["case_id"]) is not str or type(row["model"]) is not str or type(row["repetition"]) is not int or row["repetition"] not in {1, 2} or row["exposure_delta_unrestricted_minus_enforced"] not in {-1, 0, 1}:
            raise CandidateProjectionError("v5 paired contrast row differs")
    return {"groups": projection_groups, "paired_contrasts": contrasts, "scheduled": 64, "v4_included_count": 0}


def validate_candidate_projection(value: object) -> dict[str, Any]:
    """Validate an answer-free candidate envelope without opening raw traces."""

    required = {"authority", "complete", "evidence_closure", "evidence_manifest_sha256", "projection", "schema_version", "v4_included_count"}
    if type(value) is not dict or set(value) != required or value["schema_version"] != "anachron-v5-candidate-answer-free-projection-v1" or value["complete"] is not True or value["v4_included_count"] != 0:
        raise CandidateProjectionError("v5 candidate projection schema differs")
    authority = value["authority"]
    if type(authority) is not dict or set(authority) != {"authority_contract_sha256", "carry_forward_sha256", "compatibility_plan_sha256", "conditional_go_sha256", "full_plan_sha256", "materialization_receipt_sha256", "runtime_identity_sha256", "source_manifest_sha256"}:
        raise CandidateProjectionError("v5 candidate authority differs")
    for key, digest in authority.items():
        _hex(digest, f"v5 candidate authority {key}")
    closure = value["evidence_closure"]
    if type(closure) is not dict or set(closure) != {"files", "schema_version", "sha256"} or closure["schema_version"] != "anachron-v5-whole-evidence-closure-v1" or type(closure["files"]) is not list:
        raise CandidateProjectionError("v5 candidate evidence closure differs")
    if closure["sha256"] != sha256_bytes(canonical_json_bytes(closure["files"])):
        raise CandidateProjectionError("v5 candidate evidence closure digest differs")
    for row in closure["files"]:
        if type(row) is not dict or set(row) != {"path", "sha256"} or type(row["path"]) is not str:
            raise CandidateProjectionError("v5 candidate evidence closure row differs")
        _hex(row["sha256"], "v5 candidate evidence closure SHA-256")
    _hex(value["evidence_manifest_sha256"], "v5 candidate evidence manifest SHA-256")
    _validated_summary({
        "groups": {
            f"{row['model']}::{row['mode']}": {
                "adherence_rate": row["adherence_numerator"] / 16,
                "adherent": row["adherence_numerator"],
                "conditional_exposure_denominator": row["conditional_exposure_denominator"],
                "conditional_exposure_numerator": row["conditional_exposure_numerator"],
                "conditional_exposure_rate": None if not row["conditional_exposure_denominator"] else row["conditional_exposure_numerator"] / row["conditional_exposure_denominator"],
                "final_categories": row["final_categories"],
                "first_categories": row["first_categories"],
                "scheduled": row["scheduled"],
            }
            for row in value["projection"].get("groups", [])
        },
        "paired_contrasts": value["projection"].get("paired_contrasts"),
        "scheduled": value["projection"].get("scheduled"),
        "v4_included_count": value["projection"].get("v4_included_count"),
    })
    return value


def project_candidate(repository_root: Path, *, source_manifest: Path, carry_forward: Path, runtime_identity: Path, conditional_go: Path, materialization_receipt: Path, evidence: Path) -> dict[str, Any]:
    """Replay only complete v5 evidence and derive the sole empirical projection."""

    try:
        root = admit_repository_root(repository_root)
        admitted = {
            label: admit_external_regular_input(path, root, label)
            for label, path in (("source manifest", source_manifest), ("carry-forward receipt", carry_forward), ("runtime identity", runtime_identity), ("conditional GO", conditional_go), ("materialization receipt", materialization_receipt))
        }
        evidence_root = admit_external_regular_input(evidence / "manifest.json", root, "v5 evidence manifest").parent
    except V5PathError as error:
        raise CandidateProjectionError(str(error)) from error
    inputs = {label: _read_json(path, label)[1] for label, path in admitted.items()}
    try:
        summary = analyze_measurement(evidence_root, repository_root=root)
    except V5MeasurementError as error:
        raise CandidateProjectionError("v5 evidence replay failed") from error
    closure = _evidence_closure(evidence_root)
    manifest = next((row for row in closure["files"] if row["path"] == "manifest.json"), None)
    if manifest is None:
        raise CandidateProjectionError("v5 evidence manifest is absent")
    authority_files = {
        "conditional GO": "authority/conditional-go.json",
        "carry-forward receipt": "authority/carry-forward.json",
        "source manifest": "authority/source-manifest.json",
        "materialization receipt": "authority/materialization-receipt.json",
        "runtime identity": "authority/runtime-identity.json",
    }
    for label, relative in authority_files.items():
        _same_bytes(inputs[label], _closure_bytes(evidence_root, relative, label), label)
    full_plan_raw = _closure_bytes(evidence_root, "authority/full-plan.json", "full plan")
    compatibility_raw = _closure_bytes(evidence_root, "authority/compatibility-plan.json", "compatibility plan")
    authority_raw = _closure_bytes(evidence_root, "authority/authority-contract.json", "authority contract")
    try:
        repository_authority = capture_regular(root / AUTHORITY_CONTRACT_PATH, "authority contract", 1_048_576).raw
    except V5CustodyError as error:
        raise CandidateProjectionError("authority contract cannot be read") from error
    _same_bytes(repository_authority, authority_raw, "authority contract")
    try:
        full_plan = strict_json_loads(full_plan_raw, "full plan")
        materialization = strict_json_loads(inputs["materialization receipt"], "materialization receipt")
        runtime = strict_json_loads(inputs["runtime identity"], "runtime identity")
    except ValueError as error:
        raise CandidateProjectionError("candidate authority cannot be parsed") from error
    if type(full_plan) is not dict or type(materialization) is not dict or type(runtime) is not dict:
        raise CandidateProjectionError("candidate authority schema differs")
    if runtime != full_plan.get("expected_runtime"):
        raise CandidateProjectionError("runtime identity differs from full plan")
    expected = {
        "authority_contract_sha256": sha256_bytes(authority_raw),
        "carry_forward_sha256": sha256_bytes(inputs["carry-forward receipt"]),
        "compatibility_plan_sha256": sha256_bytes(compatibility_raw),
        "full_plan_sha256": sha256_bytes(full_plan_raw),
        "runtime_identity_sha256": sha256_bytes(inputs["runtime identity"]),
        "v5_source_manifest_sha256": sha256_bytes(inputs["source manifest"]),
    }
    if any(full_plan.get(key) != expected[key] for key in ("authority_contract_sha256", "carry_forward_sha256", "compatibility_plan_sha256", "v5_source_manifest_sha256")):
        raise CandidateProjectionError("full plan authority binding differs")
    if materialization.get("runtime_identity_sha256") != expected["runtime_identity_sha256"] or any(materialization.get(key) != value for key, value in expected.items() if key != "runtime_identity_sha256" and key != "v5_source_manifest_sha256") or materialization.get("v5_source_manifest_sha256") != expected["v5_source_manifest_sha256"]:
        raise CandidateProjectionError("materialization receipt authority binding differs")
    go, _ = _read_json(admitted["conditional GO"], "conditional GO")
    if go.get("full_plan_sha256") != expected["full_plan_sha256"] or go.get("materialization_receipt_sha256") != sha256_bytes(inputs["materialization receipt"]):
        raise CandidateProjectionError("conditional GO authority binding differs")
    validate_authority_contract(root)
    candidate = {
        "authority": {
            "authority_contract_sha256": sha256_bytes(authority_raw),
            "carry_forward_sha256": sha256_bytes(inputs["carry-forward receipt"]),
            "compatibility_plan_sha256": sha256_bytes(compatibility_raw),
            "conditional_go_sha256": sha256_bytes(inputs["conditional GO"]),
            "full_plan_sha256": sha256_bytes(full_plan_raw),
            "materialization_receipt_sha256": sha256_bytes(inputs["materialization receipt"]),
            "runtime_identity_sha256": sha256_bytes(inputs["runtime identity"]),
            "source_manifest_sha256": sha256_bytes(inputs["source manifest"]),
        },
        "complete": True,
        "evidence_closure": closure,
        "evidence_manifest_sha256": manifest["sha256"],
        "projection": _validated_summary(summary),
        "schema_version": "anachron-v5-candidate-answer-free-projection-v1",
        "v4_included_count": 0,
    }
    return validate_candidate_projection(candidate)


def project_and_write_candidate(repository_root: Path, *, source_manifest: Path, carry_forward: Path, runtime_identity: Path, conditional_go: Path, materialization_receipt: Path, evidence: Path, output: Path) -> dict[str, Any]:
    """Write one canonical create-only v5 projection outside the repository."""

    root = admit_repository_root(repository_root)
    value = project_candidate(root, source_manifest=source_manifest, carry_forward=carry_forward, runtime_identity=runtime_identity, conditional_go=conditional_go, materialization_receipt=materialization_receipt, evidence=evidence)
    try:
        destination = admit_create_only_external_output(output, root, "candidate projection output")
        write_create_only(
            destination,
            canonical_json_bytes(value),
            "candidate projection output",
            ByteBudget(1_048_576, 1),
        )
        fsync_directory(destination.parent, "candidate projection output parent")
    except (OSError, V5CustodyError, V5PathError) as error:
        raise CandidateProjectionError("candidate projection cannot be published") from error
    return value


def generated_arxiv_metadata(template: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Derive local-only metadata from the validated projection and frozen prose."""

    validate_candidate_projection(candidate)
    if type(template) is not dict or set(template) != {"ai_assistance_disclosure", "author", "categories", "title"} or any(type(template[key]) is not str or not template[key] for key in ("ai_assistance_disclosure", "author", "title")) or type(template["categories"]) is not list or not template["categories"] or any(type(category) is not str or not category for category in template["categories"]) or "cs.AI" not in template["categories"]:
        raise CandidateProjectionError("v5 metadata template differs")
    abstract = (
        f"{template['title']} We report first-tool adherence and conditional post-cutoff exposure "
        f"for 64 traces in a controlled eight-card synthetic finite panel. "
        f"Compatibility traces and all v4 empirical rows are excluded."
    )
    return {"abstract": abstract, "ai_assistance_disclosure": template["ai_assistance_disclosure"], "author": template["author"], "categories": template["categories"], "schema_version": "anachron-v5-local-arxiv-metadata-v1", "title": template["title"], "v4_included_count": 0}
