"""Project complete sealed v4 evidence without exposing answer-bearing content."""

from __future__ import annotations

import hashlib
import os
import stat
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from anachron.data.v4_registry import (
    V4RegistryError,
    canonical_json_bytes,
    load_v4_registry,
    strict_json_loads,
)
from anachron.v4_comparison import V4ComparisonError, derive_bytes
from anachron.v4_contract import (
    V4_CANDIDATE_RESOURCE_POLICY,
    V4ContractError,
    validate_authority_contract,
)
from anachron.v4_measurement import V4MeasurementError, analyze_measurement
from anachron.v4_paths import (
    V4PathError,
    admit_create_only_external_output,
    admit_evidence_regular_file,
    admit_evidence_root,
    admit_external_regular_input,
    admit_repository_root,
)
from tools.build_v4_source_manifest import V4SourceManifestError
from tools.build_v4_source_manifest import derive as derive_source_manifest
from tools.build_v4_source_manifest import validate as validate_source_manifest


class CandidateProjectionError(ValueError):
    """Raised when v4 evidence cannot produce a sealed answer-free projection."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = strict_json_loads(raw, label)
    except (OSError, ValueError) as error:
        raise CandidateProjectionError(f"{label} cannot be read") from error
    if type(value) is not dict or raw != canonical_json_bytes(value):
        raise CandidateProjectionError(f"{label} is not canonical JSON")
    return value, raw


def _safe_files(root: Path) -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []

    def walk(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as error:
            raise CandidateProjectionError("evidence closure cannot be read") from error
        for entry in entries:
            path = Path(entry.path)
            try:
                metadata = path.lstat()
            except OSError as error:
                raise CandidateProjectionError("evidence closure cannot be read") from error
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
                raise CandidateProjectionError("evidence closure has a reparse component")
            if stat.S_ISDIR(metadata.st_mode):
                walk(path)
            elif stat.S_ISREG(metadata.st_mode):
                relative = path.relative_to(root).as_posix()
                admitted = admit_evidence_regular_file(root, relative, "evidence closure")
                files.append((relative, admitted.read_bytes()))
            else:
                raise CandidateProjectionError("evidence closure contains a non-regular file")

    walk(root)
    return files


def _closure(evidence: Path) -> dict[str, Any]:
    files = _safe_files(evidence)
    paths = [path for path, _ in files]
    if not paths or any(not path.startswith(("compatibility/", "full/")) for path in paths):
        raise CandidateProjectionError("complete evidence topology differs")
    rows = [{"path": path, "sha256": _sha(raw)} for path, raw in files]
    return {
        "files": rows,
        "sha256": _sha(canonical_json_bytes(rows)),
        "schema_version": "anachron-v4-whole-evidence-closure-v1",
    }


def _mapping(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise CandidateProjectionError(f"{label} schema differs")
    return value


def _string(value: object, label: str) -> str:
    if (
        type(value) is not str
        or not value
        or len(value.encode("utf-8")) > V4_CANDIDATE_RESOURCE_POLICY["string_max_bytes"]
    ):
        raise CandidateProjectionError(f"{label} type differs")
    return value


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise CandidateProjectionError(f"{label} rational differs")
    return value


def _counts(value: object, expected: dict[str, int], label: str) -> None:
    counts = _mapping(value, set(expected), label)
    if any(type(count) is not int for count in counts.values()) or counts != expected:
        raise CandidateProjectionError(f"{label} differs")


def _fixed_rate(numerator: int, denominator: int) -> str:
    return str(
        (Decimal(numerator) / Decimal(denominator)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
    )


def _projection(value: object, repository_root: Path) -> dict[str, Any]:
    projection = _mapping(
        value,
        {
            "cells",
            "diagnostics",
            "paired_tclr_reductions",
            "schema_version",
            "split_counts",
            "topology",
            "v3_included_count",
        },
        "analyzer projection",
    )
    if projection["schema_version"] != "anachron-v4-answer-free-projection-v3":
        raise CandidateProjectionError("analyzer projection schema differs")
    if type(projection["v3_included_count"]) is not int or projection["v3_included_count"] != 0:
        raise CandidateProjectionError("analyzer projection v3 count differs")
    _counts(
        projection["split_counts"],
        {
            "compatibility_trajectories": 2,
            "development_trajectories": 0,
            "primary_cases": 8,
            "primary_trajectories": 64,
        },
        "analyzer projection split counts",
    )
    _counts(
        projection["topology"],
        {
            "compatibility_chats": 4,
            "main_chats": 128,
            "models": 2,
            "modes": 2,
            "repetitions": 2,
            "total_chats": 132,
        },
        "analyzer projection topology",
    )
    try:
        registry, _ = load_v4_registry(repository_root)
    except V4RegistryError as error:
        raise CandidateProjectionError("v4 case registry differs") from error
    case_ids = {entry["id"] for entry in registry["cases"]}
    modes = {"enforced", "unrestricted"}
    diagnostics = projection["diagnostics"]
    if type(diagnostics) is not list or len(diagnostics) != 64:
        raise CandidateProjectionError("analyzer projection diagnostics cardinality differs")
    schedules: set[tuple[str, str, str, int]] = set()
    models: set[str] = set()
    trajectory_ids: set[str] = set()
    for index, raw in enumerate(diagnostics):
        row = _mapping(
            raw,
            {
                "case_id",
                "mode",
                "model",
                "query_nonblank",
                "repetition",
                "restatement_returned",
                "survivorship_case",
                "trajectory_id",
            },
            f"analyzer projection diagnostics[{index}]",
        )
        case_id = _string(row["case_id"], f"diagnostic {index} case")
        model = _string(row["model"], f"diagnostic {index} model")
        mode = _string(row["mode"], f"diagnostic {index} mode")
        repetition = _integer(row["repetition"], f"diagnostic {index} repetition", 1, 2)
        if case_id not in case_ids or mode not in modes:
            raise CandidateProjectionError("analyzer projection diagnostic topology differs")
        if any(type(row[field]) is not bool for field in ("query_nonblank", "restatement_returned", "survivorship_case")):
            raise CandidateProjectionError("analyzer projection diagnostic type differs")
        trajectory_id = _string(row["trajectory_id"], f"diagnostic {index} trajectory")
        schedule = (case_id, model, mode, repetition)
        if schedule in schedules or trajectory_id in trajectory_ids:
            raise CandidateProjectionError("analyzer projection diagnostic duplicate differs")
        schedules.add(schedule)
        models.add(model)
        trajectory_ids.add(trajectory_id)
    if len(models) != 2 or schedules != {
        (case_id, model, mode, repetition)
        for case_id in case_ids
        for model in models
        for mode in modes
        for repetition in (1, 2)
    }:
        raise CandidateProjectionError("analyzer projection diagnostic schedule differs")
    pairs = projection["paired_tclr_reductions"]
    if type(pairs) is not list or len(pairs) != 32:
        raise CandidateProjectionError("analyzer projection pair cardinality differs")
    pair_rows: dict[tuple[str, str, int], dict[str, Any]] = {}
    for index, raw in enumerate(pairs):
        row = _mapping(
            raw,
            {
                "case_id",
                "enforced_denominator",
                "enforced_numerator",
                "model",
                "repetition",
                "sign_class",
                "unrestricted_denominator",
                "unrestricted_numerator",
            },
            f"analyzer projection pairs[{index}]",
        )
        case_id = _string(row["case_id"], f"pair {index} case")
        model = _string(row["model"], f"pair {index} model")
        repetition = _integer(row["repetition"], f"pair {index} repetition", 1, 2)
        unrestricted = _integer(row["unrestricted_numerator"], f"pair {index} unrestricted numerator", 0, 1)
        enforced = _integer(row["enforced_numerator"], f"pair {index} enforced numerator", 0, 1)
        if (
            case_id not in case_ids
            or model not in models
            or row["unrestricted_denominator"] != 1
            or row["enforced_denominator"] != 1
            or type(row["unrestricted_denominator"]) is not int
            or type(row["enforced_denominator"]) is not int
            or row["sign_class"] != ("positive" if unrestricted > enforced else "negative" if unrestricted < enforced else "zero")
        ):
            raise CandidateProjectionError("analyzer projection pair rational differs")
        key = (case_id, model, repetition)
        if key in pair_rows:
            raise CandidateProjectionError("analyzer projection pair duplicate differs")
        pair_rows[key] = row
    if set(pair_rows) != {
        (case_id, model, repetition)
        for case_id in case_ids
        for model in models
        for repetition in (1, 2)
    }:
        raise CandidateProjectionError("analyzer projection pair schedule differs")
    cells = projection["cells"]
    if type(cells) is not list or len(cells) != 6:
        raise CandidateProjectionError("analyzer projection cell cardinality differs")
    cell_rows: set[tuple[str, str]] = set()
    for index, raw in enumerate(cells):
        row = _mapping(
            raw,
            {"denominator", "metric", "model", "mode", "numerator", "rate_fixed_decimal", "split"},
            f"analyzer projection cells[{index}]",
        )
        model = _string(row["model"], f"cell {index} model")
        mode = _string(row["mode"], f"cell {index} mode")
        if model not in models | {"pooled"} or mode not in modes or row["metric"] != "tclr" or row["split"] != "primary":
            raise CandidateProjectionError("analyzer projection cell identity differs")
        denominator = 32 if model == "pooled" else 16
        numerator = _integer(row["numerator"], f"cell {index} numerator", 0, denominator)
        expected_numerator = sum(
            pair["unrestricted_numerator" if mode == "unrestricted" else "enforced_numerator"]
            for pair in pair_rows.values()
            if model == "pooled" or pair["model"] == model
        )
        key = (model, mode)
        if (
            type(row["denominator"]) is not int
            or row["denominator"] != denominator
            or numerator != expected_numerator
            or row["rate_fixed_decimal"] != _fixed_rate(numerator, denominator)
            or key in cell_rows
        ):
            raise CandidateProjectionError("analyzer projection cell rational differs")
        cell_rows.add(key)
    if cell_rows != {(model, mode) for model in models | {"pooled"} for mode in modes}:
        raise CandidateProjectionError("analyzer projection cell schedule differs")
    return projection


def _hex(value: object, label: str) -> str:
    value = _string(value, label)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise CandidateProjectionError(f"{label} SHA-256 differs")
    return value


def _candidate_envelope(value: object, repository_root: Path) -> dict[str, Any]:
    candidate = _mapping(
        value,
        {"authority", "complete", "evidence_closure", "projection", "protocol", "schema_version", "v3_included_count"},
        "candidate projection",
    )
    if candidate["schema_version"] != "anachron-v4-candidate-answer-free-projection-v1" or candidate["complete"] is not True or candidate["v3_included_count"] != 0 or type(candidate["v3_included_count"]) is not int:
        raise CandidateProjectionError("candidate projection status differs")
    authority = _mapping(
        candidate["authority"],
        {"actual_go_sha256", "authority_contract_sha256", "comparison_projection_sha256", "runtime_identity_sha256", "source_audit_sha256", "source_manifest_sha256"},
        "candidate projection authority",
    )
    for key, digest in authority.items():
        _hex(digest, f"candidate projection authority {key}")
    closure = _mapping(candidate["evidence_closure"], {"files", "sha256", "schema_version"}, "candidate projection evidence closure")
    if closure["schema_version"] != "anachron-v4-whole-evidence-closure-v1" or type(closure["files"]) is not list:
        raise CandidateProjectionError("candidate projection evidence closure differs")
    rows = closure["files"]
    if not rows:
        raise CandidateProjectionError("candidate projection evidence closure is empty")
    previous = ""
    for index, raw in enumerate(rows):
        row = _mapping(raw, {"path", "sha256"}, f"candidate projection evidence closure {index}")
        path = _string(row["path"], f"candidate projection evidence closure {index} path")
        if not path.startswith(("compatibility/", "full/")) or path <= previous:
            raise CandidateProjectionError("candidate projection evidence closure topology differs")
        previous = path
        _hex(row["sha256"], f"candidate projection evidence closure {index}")
    if closure["sha256"] != _sha(canonical_json_bytes(rows)):
        raise CandidateProjectionError("candidate projection evidence closure hash differs")
    protocol = _mapping(
        candidate["protocol"],
        {"branch_ref", "commit", "master_local", "master_remote", "origin", "remote_branch", "remote_tag_object", "remote_tag_peeled", "tag", "tag_object", "tag_peeled", "v3_commit", "remote_v3_tag_object", "remote_v3_tag_peeled", "v3_tag", "v3_tag_object", "v3_tag_peeled"},
        "candidate projection protocol",
    )
    for key, field in protocol.items():
        _string(field, f"candidate projection protocol {key}")
    for key in set(protocol) - {"origin", "tag", "v3_tag"}:
        if len(protocol[key]) != 40 or any(character not in "0123456789abcdef" for character in protocol[key]):
            raise CandidateProjectionError("candidate projection protocol hash differs")
    _projection(candidate["projection"], repository_root)
    return candidate


def validate_candidate_projection(value: object, repository_root: Path) -> dict[str, Any]:
    """Validate one complete answer-free v4 candidate projection envelope."""

    return _candidate_envelope(value, admit_repository_root(repository_root))


def pooled_tclr_direction(candidate: dict[str, Any]) -> str:
    """Derive the pooled unrestricted-minus-enforced direction from projection cells."""

    try:
        cells = candidate["projection"]["cells"]
    except (KeyError, TypeError) as error:
        raise CandidateProjectionError("candidate projection cells differ") from error
    if type(cells) is not list:
        raise CandidateProjectionError("candidate projection cells differ")
    pooled: dict[str, dict[str, Any]] = {}
    for row in cells:
        if (
            type(row) is dict
            and row.get("model") == "pooled"
            and row.get("mode") in {"unrestricted", "enforced"}
        ):
            pooled[row["mode"]] = row
    if set(pooled) != {"unrestricted", "enforced"}:
        raise CandidateProjectionError("candidate pooled cells differ")
    unrestricted = pooled["unrestricted"]
    enforced = pooled["enforced"]
    for label, row in (("unrestricted", unrestricted), ("enforced", enforced)):
        if (
            type(row.get("numerator")) is not int
            or type(row.get("denominator")) is not int
            or row["denominator"] <= 0
        ):
            raise CandidateProjectionError(f"candidate pooled {label} cell differs")
    difference = (
        unrestricted["numerator"] * enforced["denominator"]
        - enforced["numerator"] * unrestricted["denominator"]
    )
    return "positive" if difference > 0 else "negative" if difference < 0 else "zero"


def generated_arxiv_metadata(
    template: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    """Generate the only allowed candidate metadata from frozen template and projection."""

    try:
        sentence = template["sentence_forms"][pooled_tclr_direction(candidate)]
        title = template["title"]
        disclosure = template["ai_assistance_disclosure"]
        author = template["author"]
    except (KeyError, TypeError) as error:
        raise CandidateProjectionError("candidate metadata template differs") from error
    if any(type(value) is not str or not value for value in (sentence, title, disclosure, author)):
        raise CandidateProjectionError("candidate metadata template differs")
    return {
        "abstract": f"{title} {sentence}",
        "ai_assistance_disclosure": disclosure,
        "author": author,
        "categories": ["cs.AI"],
        "schema_version": "anachron-v4-local-arxiv-metadata-v1",
        "title": title,
        "v3_included_count": 0,
    }


def project_candidate(
    repository_root: Path,
    *,
    source_manifest: Path,
    comparison: Path,
    source_audit: Path,
    runtime_identity: Path,
    conditional_go: Path,
    evidence: Path,
    expected_origin: str | None = None,
    expected_v3: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a deterministic projection from one complete v4 evidence closure."""

    try:
        root = admit_repository_root(repository_root)
        inputs = {
            name: admit_external_regular_input(path, root, name)
            for name, path in {
                "source manifest": source_manifest,
                "comparison": comparison,
                "source audit": source_audit,
                "runtime identity": runtime_identity,
                "conditional GO": conditional_go,
            }.items()
        }
        evidence = admit_evidence_root(evidence, root, create=False)
    except V4PathError as error:
        raise CandidateProjectionError(str(error)) from error
    try:
        validate_authority_contract(root)
        source, source_raw = _json(inputs["source manifest"], "source manifest")
        derived_source = derive_source_manifest(
            root,
            **({"expected_origin": expected_origin} if expected_origin else {}),
            **({"expected_v3": expected_v3} if expected_v3 else {}),
        )
        if source_raw != canonical_json_bytes(derived_source):
            raise CandidateProjectionError("source manifest is not the tagged derivation")
        validate_source_manifest(
            root,
            inputs["source manifest"],
            **({"expected_origin": expected_origin} if expected_origin else {}),
            **({"expected_v3": expected_v3} if expected_v3 else {}),
        )
        comparison_raw = inputs["comparison"].read_bytes()
        expected_comparison = derive_bytes(
            root,
            v3_tag=source["release"]["v3_tag"],
            v4_tag=source["release"]["tag"],
        )
        if comparison_raw != expected_comparison:
            raise CandidateProjectionError("comparison is not the tagged derivation")
    except (V4ContractError, V4ComparisonError, V4SourceManifestError, KeyError) as error:
        raise CandidateProjectionError("tagged projection authority differs") from error
    external = {name: path.read_bytes() for name, path in inputs.items()}
    for name, relative in {
        "source manifest": "compatibility/source_manifest.json",
        "comparison": "compatibility/comparison.json",
        "source audit": "compatibility/source_audit.json",
        "runtime identity": "compatibility/runtime_identity.json",
        "conditional GO": "compatibility/conditional_go.json",
    }.items():
        if admit_evidence_regular_file(evidence, relative, name).read_bytes() != external[name]:
            raise CandidateProjectionError(f"{name} evidence binding differs")
    closure = _closure(evidence)
    try:
        projected = _projection(
            analyze_measurement(evidence, repository_root=root, phase="full"), root
        )
    except V4MeasurementError as error:
        raise CandidateProjectionError("complete evidence replay failed") from error
    return _candidate_envelope({
        "authority": {
            "actual_go_sha256": _sha(external["conditional GO"]),
            "authority_contract_sha256": _sha(
                (root / "research/v4_measurement/authority_binding_contract.json").read_bytes()
            ),
            "comparison_projection_sha256": _sha(external["comparison"]),
            "runtime_identity_sha256": _sha(external["runtime identity"]),
            "source_audit_sha256": _sha(external["source audit"]),
            "source_manifest_sha256": _sha(external["source manifest"]),
        },
        "complete": True,
        "evidence_closure": closure,
        "projection": projected,
        "protocol": source["release"],
        "schema_version": "anachron-v4-candidate-answer-free-projection-v1",
        "v3_included_count": 0,
    }, root)


def _write_projection(output: Path, repository_root: Path, value: dict[str, Any]) -> None:
    """Write one create-only external projection after validation."""

    try:
        root = admit_repository_root(repository_root)
        candidate = _candidate_envelope(value, root)
        destination = admit_create_only_external_output(output, root, "projection output")
    except V4PathError as error:
        raise CandidateProjectionError(str(error)) from error
    with destination.open("xb") as stream:
        stream.write(canonical_json_bytes(candidate))
        stream.flush()
        os.fsync(stream.fileno())


def project_and_write_candidate(
    repository_root: Path,
    *,
    source_manifest: Path,
    comparison: Path,
    source_audit: Path,
    runtime_identity: Path,
    conditional_go: Path,
    evidence: Path,
    output: Path,
    expected_origin: str | None = None,
    expected_v3: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Replay complete evidence and create one answer-free external projection."""

    value = project_candidate(
        repository_root,
        source_manifest=source_manifest,
        comparison=comparison,
        source_audit=source_audit,
        runtime_identity=runtime_identity,
        conditional_go=conditional_go,
        evidence=evidence,
        expected_origin=expected_origin,
        expected_v3=expected_v3,
    )
    _write_projection(output, repository_root, value)
    return value
