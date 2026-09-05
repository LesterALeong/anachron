"""Validate the offline v4 authority-binding contract and document blocks."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from typing import Any

from anachron.v4_paths import (
    V4PathError,
    admit_repository_regular_file,
    admit_repository_root,
)

AUTHORITY_CONTRACT_PATH = "research/v4_measurement/authority_binding_contract.json"
EXPECTED_AUTHORITY_CONTRACT_SHA256 = (
    "403e0686f3d61e6b4313eb66268809aa4666888747714d3b1f4286ecac9bc329"
)
_BEGIN_AUTHORITY_BLOCK = "<!-- BEGIN V4 AUTHORITY BINDING -->"
_END_AUTHORITY_BLOCK = "<!-- END V4 AUTHORITY BINDING -->"
_CANDIDATE_ACCEPTANCE_MATRIX_PATH = (
    "paper/v4_measurement/CANDIDATE_ACCEPTANCE_MATRIX.md"
)
_CANONICAL_MATRIX_EQUALITY = (
    "compatibility_plan.acceptance_matrix_sha256 == "
    "full_plan.acceptance_matrix_sha256 == "
    "conditional_go.acceptance_matrix_sha256 == SHA256(ACCEPTANCE_MATRIX.md)"
)
_LEGACY_TWO_CARRIER_WORDING = "The full plan and GO must carry the same acceptance-matrix hash before compatibility."
SOURCE_MANIFEST_PLACEHOLDER = "REPLACE_AFTER_REVIEWED_TAG_FREEZE"
V4_PROTOCOL_BRANCH = "protocol/v4-recovery-v1"
V4_GOVERNED_SOURCE_PATHS = (
    ".github/workflows/tests.yml",
    "anachron/data/v4_registry.py",
    "anachron/v4_candidate_common.py",
    "anachron/v4_candidate_release_common.py",
    "anachron/v4_comparison.py",
    "anachron/v4_contract.py",
    "anachron/v4_measurement.py",
    "anachron/v4_paths.py",
    "paper/v4_measurement/CANDIDATE_ACCEPTANCE_MATRIX.md",
    "paper/v4_measurement/CANDIDATE_CLAIM_EVIDENCE_MAP.md",
    "paper/v4_measurement/CANDIDATE_SUBMISSION_METADATA.md",
    "paper/v4_measurement/README.md",
    "paper/v4_measurement/archive_allowlist.json",
    "paper/v4_measurement/author_approval.template.json",
    "paper/v4_measurement/candidate_contract.json",
    "paper/v4_measurement/candidate_manuscript_template.json",
    "paper/v4_measurement/candidate_references.bib",
    "paper/v4_measurement/outreach.template.md",
    "paper/v4_measurement/reviews/review.template.json",
    "research/v4_measurement/ACCEPTANCE_MATRIX.md",
    "research/v4_measurement/CLAIM_EVIDENCE_MAP.md",
    "research/v4_measurement/PROTOCOL.md",
    "research/v4_measurement/README.md",
    "research/v4_measurement/authority_binding_contract.json",
    "research/v4_measurement/case_registry.json",
    "research/v4_measurement/cases/fin-aster-2020-06-future.json",
    "research/v4_measurement/cases/fin-bramble-2023-03-current.json",
    "research/v4_measurement/cases/fin-drift-2022-04-restatement-original.json",
    "research/v4_measurement/cases/fin-fable-2012-06-not-listed.json",
    "research/v4_measurement/cases/fin-granite-2026-01-delisted.json",
    "research/v4_measurement/cases/gen-archipelago-2021-04-future.json",
    "research/v4_measurement/cases/gen-civic-2024-02-current.json",
    "research/v4_measurement/cases/gen-estuary-2025-01-restatement-later.json",
    "research/v4_measurement/compatibility_case.json",
    "research/v4_measurement/compatibility_plan.template.json",
    "research/v4_measurement/conditional_go.template.json",
    "research/v4_measurement/full_plan.template.json",
    "research/v4_measurement/source_audit.template.json",
    "tests/test_v4_candidate_outreach.py",
    "tests/test_v4_candidate_paper.py",
    "tests/test_v4_candidate_projection.py",
    "tests/test_v4_candidate_review_release.py",
    "tests/test_v4_ci_workflow.py",
    "tests/test_v4_contract.py",
    "tests/test_v4_materialization.py",
    "tests/test_v4_operational.py",
    "tests/test_v4_source_manifest.py",
    "tools/analyze_v4_measurement.py",
    "tools/build_v4_measurement_candidate_paper.py",
    "tools/build_v4_source_audit_ui.py",
    "tools/build_v4_source_manifest.py",
    "tools/capture_v4_runtime_identity.py",
    "tools/finalize_v4_source_audit.py",
    "tools/materialize_v4_inputs.py",
    "tools/project_v4_measurement_candidate.py",
    "tools/release_v4_measurement_candidate.py",
    "tools/render_v4_contract_docs.py",
    "tools/render_v4_measurement_unsent_outreach.py",
    "tools/run_v4_recovery.py",
    "tools/validate_v4_contract.py",
    "tools/verify_v4_measurement_candidate_reviews.py",
)

V4_CANDIDATE_STATIC_ARTIFACT_PATHS = (
    "paper/v4_measurement/CANDIDATE_ACCEPTANCE_MATRIX.md",
    "paper/v4_measurement/CANDIDATE_CLAIM_EVIDENCE_MAP.md",
    "paper/v4_measurement/CANDIDATE_SUBMISSION_METADATA.md",
    "paper/v4_measurement/README.md",
    "paper/v4_measurement/archive_allowlist.json",
    "paper/v4_measurement/author_approval.template.json",
    "paper/v4_measurement/candidate_manuscript_template.json",
    "paper/v4_measurement/candidate_references.bib",
    "paper/v4_measurement/outreach.template.md",
    "paper/v4_measurement/reviews/review.template.json",
)
V4_CANDIDATE_REVIEW_LENS_IDS = (
    "claim-evidence-anti-fabrication",
    "experimental-design-primary-development",
    "trace-protocol-leakage-definition",
    "finite-panel-statistical-reporting",
    "reproducibility-provenance-determinism",
    "related-work-novelty",
    "plain-language-readability-abstract",
    "adversarial-overclaim-limitations",
    "authorship-ai-licensing-integrity",
    "pdf-latex-arxiv-metadata-layout",
)
V4_SOURCE_ARCHIVE_ALLOWLIST = (
    "README.md",
    "figures/primary_tclr.tex",
    "main.tex",
    "references.bib",
)
V4_LOCAL_RELEASE_ALLOWLIST = (
    "arxiv_metadata.json",
    "candidate.pdf",
    "local_release_receipt.json",
    "source.zip",
)
V4_TECTONIC = {
    "linux_archive_sha256": "1a715688baf591e650c8aeb160ae934e181685eecbb38b317de30b269ac5d606",
    "linux_executable_sha256": "2b3a86250906c92ed0a3ae8aaa454ec55bd6cede8593b3e549640177f6aecaa3",
    "version": "0.17.0",
    "windows_executable_sha256": "99ffcfdbf1ebf8bdda9e791942e3d06aedb12463fddc33f07de6f5211c8bf08d",
}
V4_CANDIDATE_RESOURCE_POLICY = {
    "candidate_projection_max_bytes": 1048576,
    "pdf_max_bytes": 2097152,
    "pdf_max_pages": 8,
    "render_max_bytes": 4194304,
    "render_max_pixels": 16777216,
    "source_archive_max_bytes": 1048576,
    "source_file_max_bytes": 262144,
    "source_manifest_max_bytes": 1048576,
    "string_max_bytes": 512,
    "tectonic_archive_max_bytes": 67108864,
    "tectonic_executable_max_bytes": 67108864,
    "tectonic_log_max_bytes": 65536,
    "tectonic_output_max_bytes": 4194304,
    "tectonic_timeout_seconds": 120,
}


class V4ContractError(ValueError):
    """Raised when a v4 contract byte or dependency edge is invalid."""


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_json_loads(raw: bytes, label: str) -> object:
    def reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise V4ContractError(f"{label} contains a duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_nonfinite(value: str) -> object:
        raise V4ContractError(f"{label} contains a non-finite JSON constant: {value}")

    def reject_overflow(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise V4ContractError(f"{label} contains a non-finite JSON number: {value}")
        return parsed

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=reject_nonfinite,
            parse_float=reject_overflow,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise V4ContractError(f"{label} is not valid UTF-8 JSON") from error


def _require_type(value: object, expected: type, label: str) -> Any:
    if type(value) is not expected:
        raise V4ContractError(f"{label} has the wrong JSON type")
    return value


def _require_mapping(value: object, keys: set[str], label: str) -> dict[str, Any]:
    mapping = _require_type(value, dict, label)
    if set(mapping) != keys:
        raise V4ContractError(f"{label} has unexpected or missing fields")
    return mapping


def _relative_path(value: object, label: str) -> str:
    path = _require_type(value, str, label)
    candidate = PurePosixPath(path)
    if (
        not path
        or "\\" in path
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise V4ContractError(f"{label} is not a canonical relative path")
    return path


def _admit_repository_root(repository_root: Path) -> Path:
    try:
        return admit_repository_root(repository_root)
    except V4PathError as error:
        raise V4ContractError(str(error)) from error


def _admit_target(repository_root: Path, relative: str, label: str) -> Path:
    root = _admit_repository_root(repository_root)
    try:
        return admit_repository_regular_file(
            root / _relative_path(relative, label), root, label
        )
    except V4PathError as error:
        raise V4ContractError(str(error)) from error


def _read_file(repository_root: Path, relative: str, label: str) -> bytes:
    path = _admit_target(repository_root, relative, label)
    return path.read_bytes()


def _load_canonical_json(
    repository_root: Path, relative: str, label: str
) -> tuple[dict[str, Any], bytes]:
    raw = _read_file(repository_root, relative, label)
    value = _strict_json_loads(raw, label)
    mapping = _require_type(value, dict, label)
    if raw != canonical_json_bytes(mapping):
        raise V4ContractError(f"{label} must use canonical JSON bytes")
    return mapping, raw


def _hash_binding(value: object, label: str) -> dict[str, str]:
    binding = _require_mapping(value, {"path", "sha256"}, label)
    path = _relative_path(binding["path"], f"{label}.path")
    digest = _require_type(binding["sha256"], str, f"{label}.sha256")
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise V4ContractError(f"{label}.sha256 is not a lowercase SHA-256")
    return {"path": path, "sha256": digest}


def _require_binding_target(
    repository_root: Path, binding: dict[str, str], label: str
) -> bytes:
    raw = _read_file(repository_root, binding["path"], label)
    if _sha256(raw) != binding["sha256"]:
        raise V4ContractError(f"{label} does not match its raw target")
    return raw


def _authority_block(contract: dict[str, Any]) -> str:
    value = _require_type(
        contract["authority_block"], str, "authority contract.authority_block"
    )
    return f"{_BEGIN_AUTHORITY_BLOCK}\n{value}\n{_END_AUTHORITY_BLOCK}"


def _render_document(raw: str, block: str, label: str) -> str:
    if raw.count(_BEGIN_AUTHORITY_BLOCK) != 1 or raw.count(_END_AUTHORITY_BLOCK) != 1:
        raise V4ContractError(f"{label} has an invalid authority block marker count")
    begin = raw.index(_BEGIN_AUTHORITY_BLOCK)
    end = raw.index(_END_AUTHORITY_BLOCK, begin) + len(_END_AUTHORITY_BLOCK)
    return f"{raw[:begin]}{block}{raw[end:]}"


def _validate_document_language(relative: str, raw: str, label: str) -> None:
    if relative != _CANDIDATE_ACCEPTANCE_MATRIX_PATH:
        return
    if raw.count(_CANONICAL_MATRIX_EQUALITY) != 1:
        raise V4ContractError(
            f"{label} must contain exactly one canonical three-carrier equality"
        )
    if _LEGACY_TWO_CARRIER_WORDING in raw:
        raise V4ContractError(f"{label} retains stale two-carrier equality wording")


def load_authority_contract(repository_root: Path) -> tuple[dict[str, Any], bytes]:
    repository_root = _admit_repository_root(repository_root)
    contract, raw = _load_canonical_json(
        repository_root,
        AUTHORITY_CONTRACT_PATH,
        "authority binding contract",
    )
    if _sha256(raw) != EXPECTED_AUTHORITY_CONTRACT_SHA256:
        raise V4ContractError(
            "authority binding contract SHA-256 differs from the exact consumer"
        )
    required = {
        "authority_block",
        "documents",
        "kind",
        "protocol_matrix",
        "required_actual_go",
        "source_manifest",
        "schema_version",
        "templates",
    }
    contract = _require_mapping(contract, required, "authority binding contract")
    if contract["kind"] != "anachron-v4-authority-binding-contract":
        raise V4ContractError("authority binding contract kind differs")
    if (
        _require_type(
            contract["schema_version"], int, "authority binding contract.schema_version"
        )
        != 1
    ):
        raise V4ContractError("authority binding contract schema version differs")
    _require_type(
        contract["authority_block"], str, "authority binding contract.authority_block"
    )
    documents = _require_type(
        contract["documents"], list, "authority binding contract.documents"
    )
    expected_documents = [
        "research/v4_measurement/PROTOCOL.md",
        "research/v4_measurement/ACCEPTANCE_MATRIX.md",
        "research/v4_measurement/README.md",
        "research/v4_measurement/CLAIM_EVIDENCE_MAP.md",
        "paper/v4_measurement/CANDIDATE_ACCEPTANCE_MATRIX.md",
    ]
    if documents != expected_documents:
        raise V4ContractError("authority binding contract document topology differs")
    for index, document in enumerate(documents):
        _relative_path(document, f"authority binding contract.documents[{index}]")
    matrix = _hash_binding(
        contract["protocol_matrix"], "authority binding contract.protocol_matrix"
    )
    if matrix["path"] != "research/v4_measurement/ACCEPTANCE_MATRIX.md":
        raise V4ContractError("authority binding contract matrix path differs")
    _require_binding_target(
        repository_root, matrix, "authority binding contract matrix"
    )
    actual_go = _require_mapping(
        contract["required_actual_go"],
        {
            "candidate_receipt",
            "comparison_projection_sha256",
            "runtime_identity_sha256",
            "source_audit_sha256",
            "source_manifest_sha256",
        },
        "authority binding contract.required_actual_go",
    )
    candidate_receipt = _require_mapping(
        actual_go["candidate_receipt"],
        {"field", "requires_exact_binding"},
        "authority binding contract.required_actual_go.candidate_receipt",
    )
    if candidate_receipt != {
        "field": "actual_go_sha256",
        "requires_exact_binding": True,
    }:
        raise V4ContractError(
            "authority binding contract candidate receipt requirement differs"
        )
    runtime = _require_mapping(
        actual_go["runtime_identity_sha256"],
        {"required", "type"},
        "authority binding contract.required_actual_go.runtime_identity_sha256",
    )
    if runtime != {"required": True, "type": "sha256"}:
        raise V4ContractError("authority binding contract runtime requirement differs")
    comparison = _require_mapping(
        actual_go["comparison_projection_sha256"],
        {"required", "type"},
        "authority binding contract.required_actual_go.comparison_projection_sha256",
    )
    if comparison != {"required": True, "type": "sha256"}:
        raise V4ContractError(
            "authority binding contract comparison requirement differs"
        )
    audit = _require_mapping(
        actual_go["source_audit_sha256"],
        {"required", "type"},
        "authority binding contract.required_actual_go.source_audit_sha256",
    )
    if audit != {"required": True, "type": "sha256"}:
        raise V4ContractError("authority binding contract audit requirement differs")
    source_manifest = _require_mapping(
        actual_go["source_manifest_sha256"],
        {"required", "type"},
        "authority binding contract.required_actual_go.source_manifest_sha256",
    )
    if source_manifest != {"required": True, "type": "sha256"}:
        raise V4ContractError(
            "authority binding contract source manifest requirement differs"
        )
    source_manifest_contract = _require_mapping(
        contract["source_manifest"],
        {"governed_paths", "schema_version"},
        "authority binding contract.source_manifest",
    )
    if source_manifest_contract != {
        "governed_paths": list(V4_GOVERNED_SOURCE_PATHS),
        "schema_version": "anachron-v4-source-manifest-v1",
    }:
        raise V4ContractError(
            "authority binding contract source manifest topology differs"
        )
    templates = _require_mapping(
        contract["templates"],
        {"candidate_contract", "compatibility_plan", "conditional_go", "full_plan"},
        "authority binding contract.templates",
    )
    expected_templates = {
        "candidate_contract": "paper/v4_measurement/candidate_contract.json",
        "compatibility_plan": "research/v4_measurement/compatibility_plan.template.json",
        "conditional_go": "research/v4_measurement/conditional_go.template.json",
        "full_plan": "research/v4_measurement/full_plan.template.json",
    }
    if templates != expected_templates:
        raise V4ContractError("authority binding contract template topology differs")
    return contract, raw


def _require_string_field(mapping: dict[str, Any], field: str, label: str) -> str:
    if field not in mapping:
        raise V4ContractError(f"{label}.{field} is missing")
    return _require_type(mapping[field], str, f"{label}.{field}")


def _validate_plan_graph(
    repository_root: Path,
    contract: dict[str, Any],
    contract_raw: bytes,
) -> tuple[dict[str, Any], bytes, dict[str, Any], bytes, dict[str, Any], bytes]:
    templates = contract["templates"]
    matrix_digest = contract["protocol_matrix"]["sha256"]
    contract_digest = _sha256(contract_raw)
    compatibility, compatibility_raw = _load_canonical_json(
        repository_root,
        templates["compatibility_plan"],
        "compatibility plan",
    )
    full, full_raw = _load_canonical_json(
        repository_root, templates["full_plan"], "full plan"
    )
    go, go_raw = _load_canonical_json(
        repository_root, templates["conditional_go"], "conditional GO"
    )
    for label, plan in (
        ("compatibility plan", compatibility),
        ("full plan", full),
        ("conditional GO", go),
    ):
        if (
            _require_string_field(plan, "authority_binding_contract_sha256", label)
            != contract_digest
        ):
            raise V4ContractError(f"{label} authority binding hash differs")
        if (
            _require_string_field(plan, "acceptance_matrix_sha256", label)
            != matrix_digest
        ):
            raise V4ContractError(f"{label} acceptance matrix hash differs")
        if (
            _require_string_field(plan, "source_manifest_sha256", label)
            != SOURCE_MANIFEST_PLACEHOLDER
        ):
            raise V4ContractError(f"{label} source manifest placeholder differs")
        if (
            _require_string_field(plan, "comparison_projection_sha256", label)
            != "REPLACE_AFTER_PROTOCOL_FREEZE"
        ):
            raise V4ContractError(f"{label} comparison projection placeholder differs")
    if _require_type(full.get("compatibility"), dict, "full plan.compatibility").get(
        "plan_sha256"
    ) != _sha256(compatibility_raw):
        raise V4ContractError("full plan compatibility plan hash differs")
    if _require_string_field(
        go, "compatibility_plan_sha256", "conditional GO"
    ) != _sha256(compatibility_raw):
        raise V4ContractError("conditional GO compatibility plan hash differs")
    if _require_string_field(go, "full_plan_sha256", "conditional GO") != _sha256(
        full_raw
    ):
        raise V4ContractError("conditional GO full plan hash differs")
    if (
        _require_type(full.get("v3_included_count"), int, "full plan.v3_included_count")
        != 0
    ):
        raise V4ContractError("full plan v3 inclusion count differs")
    return compatibility, compatibility_raw, full, full_raw, go, go_raw


def _validate_candidate_graph(
    repository_root: Path,
    contract: dict[str, Any],
    contract_raw: bytes,
    compatibility_raw: bytes,
    full_raw: bytes,
) -> None:
    candidate, candidate_raw = _load_canonical_json(
        repository_root,
        contract["templates"]["candidate_contract"],
        "candidate contract",
    )
    required_fields = {
        "archive_allowlist_equality_required",
        "archive_allowlist_path",
        "archive_allowlist_sha256",
        "authority_binding_contract",
        "authority_graph",
        "comparison_projection_sha256",
        "compatibility_plan",
        "completion_sets",
        "dynamic_receipt_schema",
        "full_plan",
        "local_release_allowlist",
        "outcome_semantics",
        "projection_policy",
        "protocol_identity",
        "protocol_matrix",
        "resource_policy",
        "review_lens_ids",
        "schema_version",
        "self_custody_limitation",
        "source_archive_allowlist",
        "source_manifest_sha256",
        "states",
        "static_artifact_hashes",
        "tectonic",
        "topology",
        "v3_included_count",
    }
    if set(candidate) != required_fields:
        raise V4ContractError("candidate contract schema differs")
    if candidate_raw != canonical_json_bytes(candidate):
        raise V4ContractError("candidate contract is not canonical")
    if "conditional_go_sha256" in candidate:
        raise V4ContractError(
            "candidate contract cannot pre-bind a future conditional GO"
        )
    authority = _hash_binding(
        candidate.get("authority_binding_contract"),
        "candidate contract.authority_binding_contract",
    )
    if authority != {"path": AUTHORITY_CONTRACT_PATH, "sha256": _sha256(contract_raw)}:
        raise V4ContractError("candidate contract authority binding differs")
    matrix = _hash_binding(
        candidate.get("protocol_matrix"), "candidate contract.protocol_matrix"
    )
    if matrix != contract["protocol_matrix"]:
        raise V4ContractError("candidate contract protocol matrix binding differs")
    compatibility = _hash_binding(
        candidate.get("compatibility_plan"), "candidate contract.compatibility_plan"
    )
    if compatibility != {
        "path": contract["templates"]["compatibility_plan"],
        "sha256": _sha256(compatibility_raw),
    }:
        raise V4ContractError("candidate contract compatibility plan binding differs")
    full = _hash_binding(candidate.get("full_plan"), "candidate contract.full_plan")
    if full != {
        "path": contract["templates"]["full_plan"],
        "sha256": _sha256(full_raw),
    }:
        raise V4ContractError("candidate contract full plan binding differs")
    if (
        _require_string_field(candidate, "source_manifest_sha256", "candidate contract")
        != SOURCE_MANIFEST_PLACEHOLDER
    ):
        raise V4ContractError("candidate contract source manifest placeholder differs")
    if (
        _require_string_field(
            candidate, "comparison_projection_sha256", "candidate contract"
        )
        != "REPLACE_AFTER_PROTOCOL_FREEZE"
    ):
        raise V4ContractError(
            "candidate contract comparison projection placeholder differs"
        )
    static_hashes = _require_mapping(
        candidate["static_artifact_hashes"],
        set(V4_CANDIDATE_STATIC_ARTIFACT_PATHS),
        "candidate contract.static_artifact_hashes",
    )
    for relative in V4_CANDIDATE_STATIC_ARTIFACT_PATHS:
        digest = _sha256(
            _read_file(repository_root, relative, f"candidate static artifact {relative}")
        )
        if static_hashes[relative] != digest:
            raise V4ContractError(
                f"candidate contract static artifact hash differs: {relative}"
            )
    if candidate["archive_allowlist_path"] != "archive_allowlist.json":
        raise V4ContractError("candidate contract archive allowlist path differs")
    if candidate["archive_allowlist_equality_required"] is not True:
        raise V4ContractError("candidate contract archive allowlist equality differs")
    if candidate["archive_allowlist_sha256"] != static_hashes[
        "paper/v4_measurement/archive_allowlist.json"
    ]:
        raise V4ContractError("candidate contract archive allowlist hash differs")
    archive = _load_canonical_json(
        repository_root,
        "paper/v4_measurement/archive_allowlist.json",
        "candidate archive allowlist",
    )[0]
    if archive != {
        "schema_version": "anachron-v4-source-archive-allowlist-v1",
        "source_archive_allowlist": list(V4_SOURCE_ARCHIVE_ALLOWLIST),
    }:
        raise V4ContractError("candidate archive allowlist topology differs")
    if candidate["source_archive_allowlist"] != list(V4_SOURCE_ARCHIVE_ALLOWLIST):
        raise V4ContractError("candidate contract source archive topology differs")
    if candidate["local_release_allowlist"] != list(V4_LOCAL_RELEASE_ALLOWLIST):
        raise V4ContractError("candidate contract local release topology differs")
    if candidate["review_lens_ids"] != list(V4_CANDIDATE_REVIEW_LENS_IDS):
        raise V4ContractError("candidate contract review lenses differ")
    if candidate["tectonic"] != V4_TECTONIC:
        raise V4ContractError("candidate contract Tectonic identity differs")
    if candidate["resource_policy"] != V4_CANDIDATE_RESOURCE_POLICY:
        raise V4ContractError("candidate contract resource policy differs")
    if candidate["states"] != ["candidate", "local_release", "unsent_outreach"]:
        raise V4ContractError("candidate contract states differ")
    if candidate["v3_included_count"] != 0 or type(candidate["v3_included_count"]) is not int:
        raise V4ContractError("candidate contract v3 inclusion differs")
    if candidate["topology"] != {
        "compatibility_chats": 4,
        "compatibility_traces": 2,
        "development_trajectories": 0,
        "main_chats": 128,
        "primary_cases": 8,
        "primary_trajectories": 64,
        "repetitions": 2,
    }:
        raise V4ContractError("candidate contract topology differs")
    if candidate["outcome_semantics"] != {
        "analysis_go": "report_only",
        "paired_difference": "unrestricted_tclr_minus_enforced_tclr",
        "sign_classes": ["positive", "zero", "negative"],
    }:
        raise V4ContractError("candidate contract outcome semantics differ")
    if candidate["projection_policy"] != "generated_only_from_verified_answer_free_projection":
        raise V4ContractError("candidate contract projection policy differs")
    if candidate["schema_version"] != "anachron-v4-candidate-contract-pre-freeze-v1":
        raise V4ContractError("candidate contract schema version differs")
    expected_identity = {
        "commit": "REPLACE_WITH_FROZEN_PEELED_COMMIT",
        "tag": "v4-measurement-protocol-v2",
        "tag_object": "REPLACE_WITH_ANNOTATED_TAG_OBJECT",
    }
    if candidate["protocol_identity"] != expected_identity:
        raise V4ContractError("candidate contract protocol identity differs")
    expected_completion_sets = {
        "candidate": [
            "arxiv_metadata.json",
            "candidate.pdf",
            "candidate_receipt.json",
            "paper_source_manifest.json",
            "projection.json",
            "qa_renders",
            "qa_render_manifest.json",
            "source",
            "source.zip",
        ],
        "local_release": list(V4_LOCAL_RELEASE_ALLOWLIST),
        "review_set": ["review_set_manifest.json"],
        "unsent_outreach": ["UNSENT.md", "outreach_receipt.json"],
    }
    if candidate["completion_sets"] != expected_completion_sets:
        raise V4ContractError("candidate contract completion sets differ")
    required_actual_go = _require_mapping(
        contract["required_actual_go"],
        {
            "candidate_receipt",
            "comparison_projection_sha256",
            "runtime_identity_sha256",
            "source_audit_sha256",
            "source_manifest_sha256",
        },
        "authority binding contract.required_actual_go",
    )
    candidate_receipt_requirement = _require_mapping(
        required_actual_go["candidate_receipt"],
        {"field", "requires_exact_binding"},
        "authority binding contract.required_actual_go.candidate_receipt",
    )
    candidate_receipt_field = _require_type(
        candidate_receipt_requirement["field"],
        str,
        "authority binding contract.required_actual_go.candidate_receipt.field",
    )
    if candidate_receipt_requirement["requires_exact_binding"] is not True:
        raise V4ContractError("candidate receipt authority requirement differs")
    expected_receipt_schema = {
        "candidate_receipt": [
            candidate_receipt_field,
            "arxiv_metadata_sha256",
            "candidate_contract_sha256",
            "evidence_manifest_sha256",
            "paper_pdf_sha256",
            "paper_source_manifest_sha256",
            "projection_sha256",
            "qa_render_manifest_sha256",
            "source_archive_sha256",
            "v3_included_count",
        ],
        "local_release_receipt": [
            "approval_sha256",
            "candidate_receipt_sha256",
            "local_release_files",
            "review_set_manifest_sha256",
            "v3_included_count",
        ],
        "review_set_manifest": [
            "archive_sha256",
            "arxiv_metadata_sha256",
            "candidate_contract_sha256",
            "candidate_receipt_sha256",
            "evidence_manifest_sha256",
            "paper_pdf_sha256",
            "paper_source_manifest_sha256",
            "projection_sha256",
            "review_lens_ids",
            "review_reports",
            "schema_version",
            "v3_included_count",
        ],
        "unsent_outreach_receipt": [
            "arxiv_metadata_sha256",
            "candidate_pdf_sha256",
            "candidate_receipt_sha256",
            "local_release_receipt_sha256",
            "schema_version",
            "source_archive_sha256",
            "status",
            "v3_included_count",
        ],
    }
    if candidate["dynamic_receipt_schema"] != expected_receipt_schema:
        raise V4ContractError("candidate contract receipt schema differs")
    actual_go = _require_mapping(
        _require_mapping(
            candidate.get("authority_graph"),
            {"actual_go"},
            "candidate contract.authority_graph",
        )["actual_go"],
        {
            "candidate_receipt_must_bind",
            "required_comparison_binding",
            "required_runtime_binding",
            "required_source_audit_binding",
            "required_source_manifest_binding",
        },
        "candidate contract.authority_graph.actual_go",
    )
    if actual_go != {
        "candidate_receipt_must_bind": {
            "field": candidate_receipt_field,
            "value": "SHA-256 of the exact actual GO bytes",
        },
        "required_runtime_binding": {
            "field": "runtime_identity_sha256",
            "value": "SHA-256 of the read-only runtime identity bytes",
        },
        "required_comparison_binding": {
            "field": "comparison_projection_sha256",
            "value": "SHA-256 of the exact tag-blob comparison bytes",
        },
        "required_source_audit_binding": {
            "field": "source_audit_sha256",
            "value": "SHA-256 of the completed eight-card source-audit bytes",
        },
        "required_source_manifest_binding": {
            "field": "source_manifest_sha256",
            "value": "SHA-256 of the exact external source manifest bytes",
        },
    }:
        raise V4ContractError("candidate contract actual GO requirements differ")


def validate_authority_contract(repository_root: Path) -> dict[str, Any]:
    """Validate every local authority dependency without performing external work."""

    root = _admit_repository_root(repository_root)
    contract, contract_raw = load_authority_contract(root)
    _, compatibility_raw, full, full_raw, _, _ = _validate_plan_graph(
        root,
        contract,
        contract_raw,
    )
    _validate_candidate_graph(root, contract, contract_raw, compatibility_raw, full_raw)
    block = _authority_block(contract)
    for relative in contract["documents"]:
        raw = _read_file(root, relative, f"authority document {relative}").decode(
            "utf-8"
        )
        _validate_document_language(relative, raw, f"authority document {relative}")
        if _render_document(raw, block, f"authority document {relative}") != raw:
            raise V4ContractError(
                f"authority document {relative} has a stale authority block"
            )
    return {
        "kind": contract["kind"],
        "protocol_matrix_sha256": contract["protocol_matrix"]["sha256"],
        "compatibility_plan_sha256": _sha256(compatibility_raw),
        "full_plan_sha256": _sha256(full_raw),
        "conditional_go_template_sha256": _sha256(
            _load_canonical_json(
                root, contract["templates"]["conditional_go"], "conditional GO"
            )[1]
        ),
        "v3_included_count": full["v3_included_count"],
    }


def render_authority_documents(repository_root: Path, check: bool) -> bool:
    """Check or render the five authority blocks from the exact local contract."""

    root = _admit_repository_root(repository_root)
    contract, _ = load_authority_contract(root)
    block = _authority_block(contract)
    clean = True
    for relative in contract["documents"]:
        path = root / relative
        raw = _read_file(root, relative, f"authority document {relative}").decode(
            "utf-8"
        )
        _validate_document_language(relative, raw, f"authority document {relative}")
        rendered = _render_document(raw, block, f"authority document {relative}")
        if raw != rendered:
            clean = False
            if not check:
                path.write_text(rendered, encoding="utf-8", newline="\n")
    return clean
