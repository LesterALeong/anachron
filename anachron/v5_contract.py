"""Offline authority closure for the v5 successor protocol."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from anachron.v5_custody import V5CustodyError, capture_regular
from anachron.v5_paths import V5PathError, admit_existing, admit_repository_root

AUTHORITY_CONTRACT_PATH = "research/v5_measurement/authority_binding_contract.json"
V5_PROTOCOL_BRANCH = "protocol/v5-successor-v3"
V5_PROTOCOL_TAG = "v5-measurement-protocol-v3"
V5_SEED_NAMESPACE = "anachron-v5-measurement-protocol-v1"
EXPECTED_RUNTIME_IDENTITY = {
    "models": (
        {
            "digest": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e",
            "name": "qwen2.5:7b",
        },
        {
            "digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8",
            "name": "qwen3:14b-q4_K_M",
        },
    ),
    "version": "0.33.2",
}
V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS = (
    ".gitattributes",
    "anachron/__init__.py",
    "anachron/data/v5_registry.py",
    "anachron/v5_carry_forward.py",
    "anachron/v5_contract.py",
    "anachron/v5_custody.py",
    "anachron/v5_measurement.py",
    "anachron/v5_paths.py",
    "anachron/v5_registry.py",
    "research/v5_measurement/ACCEPTANCE_MATRIX.md",
    "research/v5_measurement/CLAIM_EVIDENCE_MAP.md",
    "research/v5_measurement/PROTOCOL.md",
    "research/v5_measurement/README.md",
    "research/v5_measurement/authority_binding_contract.json",
    "research/v5_measurement/carry_forward.template.json",
    "research/v5_measurement/case_registry.json",
    "research/v5_measurement/cases/fin-aster-2020-06-future.json",
    "research/v5_measurement/cases/fin-bramble-2023-03-current.json",
    "research/v5_measurement/cases/fin-drift-2022-04-restatement-original.json",
    "research/v5_measurement/cases/fin-fable-2012-06-not-listed.json",
    "research/v5_measurement/cases/fin-granite-2026-01-delisted.json",
    "research/v5_measurement/cases/gen-archipelago-2021-04-future.json",
    "research/v5_measurement/cases/gen-civic-2024-02-current.json",
    "research/v5_measurement/cases/gen-estuary-2025-01-restatement-later.json",
    "research/v5_measurement/compatibility_case.json",
    "research/v5_measurement/compatibility_plan.template.json",
    "research/v5_measurement/conditional_go.template.json",
    "research/v5_measurement/full_plan.template.json",
    "tests/test_v5_carry_forward.py",
    "tests/test_v5_contract.py",
    "tests/test_v5_custody.py",
    "tests/test_v5_identity_controller.py",
    "tests/test_v5_measurement.py",
    "tests/test_v5_operational.py",
    "tests/test_v5_paths.py",
    "tests/test_v5_source_manifest.py",
    "tools/.gitattributes",
    "tools/analyze_v5_measurement.py",
    "tools/build_v5_source_manifest.py",
    "tools/capture_read_only_v5_identity.py",
    "tools/finalize_v5_carry_forward.py",
    "tools/materialize_v5_inputs.py",
    "tools/read_v5_authenticode_identity.ps1",
    "tools/read_v5_process_identity.ps1",
    "tools/run_v5_conditional_campaign.ps1",
    "tools/run_v5_recovery.py",
    "tools/validate_v5_contract.py",
)

V5_PRESENTATION_SOURCE_PATHS = (
    ".github/workflows/tests.yml",
    "anachron/v5_candidate_common.py",
    "anachron/v5_candidate_release_common.py",
    "paper/v5_measurement/CANDIDATE_ACCEPTANCE_MATRIX.md",
    "paper/v5_measurement/CANDIDATE_CLAIM_EVIDENCE_MAP.md",
    "paper/v5_measurement/CANDIDATE_SUBMISSION_METADATA.md",
    "paper/v5_measurement/README.md",
    "paper/v5_measurement/archive_allowlist.json",
    "paper/v5_measurement/author_approval.template.json",
    "paper/v5_measurement/candidate_contract.json",
    "paper/v5_measurement/candidate_manuscript_template.json",
    "paper/v5_measurement/candidate_references.bib",
    "paper/v5_measurement/outreach.template.md",
    "paper/v5_measurement/reviews/review.template.json",
    "tests/test_v5_candidate_end_to_end.py",
    "tests/test_v5_candidate_ci.py",
    "tests/test_v5_candidate_outreach.py",
    "tests/test_v5_candidate_paper.py",
    "tests/test_v5_candidate_projection.py",
    "tests/test_v5_candidate_review_release.py",
    "tests/v5_presentation_fixture.py",
    "tools/build_v5_measurement_candidate_paper.py",
    "tools/project_v5_measurement_candidate.py",
    "tools/release_v5_measurement_candidate.py",
    "tools/render_v5_measurement_unsent_outreach.py",
    "tools/verify_v5_measurement_candidate_reviews.py",
)

# Backward-compatible import name: this is the scientific GO closure only.
V5_GOVERNED_SOURCE_PATHS = V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS


class V5ContractError(ValueError):
    """Raised when v5 governed inputs are stale or malformed."""


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def strict_json_loads(raw: bytes, label: str) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise V5ContractError(f"{label} contains duplicate key {key}")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(V5ContractError(f"{label} contains non-finite value {value}")))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise V5ContractError(f"{label} is not UTF-8 JSON") from error


def _read(root: Path, relative: str, label: str) -> bytes:
    try:
        target = admit_existing(root / relative, label)
        target.relative_to(root)
    except (OSError, ValueError, V5PathError) as error:
        raise V5ContractError(f"{label} path differs") from error
    if not target.is_file():
        raise V5ContractError(f"{label} must be a regular file")
    try:
        return capture_regular(target, label, 1_048_576).raw
    except V5CustodyError as error:
        raise V5ContractError(f"{label} cannot be read") from error


def load_authority_contract(repository_root: Path) -> dict[str, Any]:
    try:
        root = admit_repository_root(repository_root)
    except V5PathError as error:
        raise V5ContractError(str(error)) from error
    raw = _read(root, AUTHORITY_CONTRACT_PATH, "v5 authority contract")
    value = strict_json_loads(raw, "v5 authority contract")
    if type(value) is not dict or raw != canonical_json_bytes(value):
        raise V5ContractError("v5 authority contract is not canonical JSON")
    required = {"expected_runtime", "kind", "protocol", "schema_version", "scientific_governed_paths", "v4_exclusion"}
    if set(value) != required or value["kind"] != "anachron-v5-authority-binding-contract" or value["schema_version"] != 1:
        raise V5ContractError("v5 authority contract identity differs")
    if value["scientific_governed_paths"] != list(V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS):
        raise V5ContractError("v5 governed path closure differs")
    if value["protocol"] != {"branch": V5_PROTOCOL_BRANCH, "tag": V5_PROTOCOL_TAG}:
        raise V5ContractError("v5 protocol identity differs")
    if value["expected_runtime"] != {"models": list(EXPECTED_RUNTIME_IDENTITY["models"]), "version": EXPECTED_RUNTIME_IDENTITY["version"]}:
        raise V5ContractError("v5 expected runtime differs")
    if value["v4_exclusion"] != {"included_count": 0, "status": "excluded-operational-pilot"}:
        raise V5ContractError("v5 v4-exclusion boundary differs")
    return value


def validate_authority_contract(repository_root: Path) -> dict[str, str]:
    """Validate static v5 closure without transport, model, or network capability."""

    root = admit_repository_root(repository_root)
    load_authority_contract(root)
    hashes = {}
    for relative in V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS:
        hashes[relative] = sha256_bytes(_read(root, relative, "v5 governed source"))
    return hashes


def validate_candidate_contract(repository_root: Path) -> None:
    """Validate presentation-only candidate policy outside scientific GO."""

    root = admit_repository_root(repository_root)
    candidate_raw = _read(root, "paper/v5_measurement/candidate_contract.json", "v5 candidate contract")
    candidate = strict_json_loads(candidate_raw, "v5 candidate contract")
    if type(candidate) is not dict or candidate_raw != canonical_json_bytes(candidate):
        raise V5ContractError("v5 candidate contract is not canonical JSON")
    completion = candidate.get("completion_sets")
    receipt_schema = candidate.get("dynamic_receipt_schema")
    expected_candidate = ["arxiv_metadata.json", "candidate.pdf", "candidate_receipt.json", "paper_source_manifest.json", "projection.json", "qa_renders", "qa_render_manifest.json", "source.zip"]
    required_receipt = ["actual_go_sha256", "archive_sha256", "arxiv_metadata_sha256", "authority_contract_sha256", "candidate_contract_sha256", "carry_forward_sha256", "compatibility_plan_sha256", "evidence_manifest_sha256", "full_plan_sha256", "materialization_receipt_sha256", "paper_pdf_sha256", "paper_source_manifest_sha256", "presentation_source_closure_sha256", "projection_sha256", "qa_render_manifest_sha256", "runtime_identity_sha256", "schema_version", "source_manifest_sha256", "v4_included_count"]
    if type(completion) is not dict or completion.get("candidate") != expected_candidate or type(receipt_schema) is not dict or receipt_schema.get("candidate_receipt") != required_receipt:
        raise V5ContractError("v5 candidate contract topology differs")
    resource_policy = candidate.get("resource_policy")
    expected_root_budgets = {
        "candidate_root_max_bytes": 25_165_824,
        "local_release_root_max_bytes": 5_242_880,
        "outreach_root_max_bytes": 2_097_152,
        "review_reports_root_max_bytes": 10_485_760,
        "review_set_manifest_max_bytes": 1_048_576,
    }
    if type(resource_policy) is not dict or any(resource_policy.get(key) != value for key, value in expected_root_budgets.items()):
        raise V5ContractError("v5 candidate root budget contract differs")
    tectonic = candidate.get("tectonic")
    if tectonic != {"bundle_url": "https://relay.fullyjustified.net/default_bundle_v33.tar", "cache_content_identifier": "6ffe055852f8faf66c0acbe1a7fb27f87b869a90bad1204f3bf4d9683f597c7c", "linux_executable_sha256": "2b3a86250906c92ed0a3ae8aaa454ec55bd6cede8593b3e549640177f6aecaa3", "version": "0.17.0", "windows_executable_sha256": "99ffcfdbf1ebf8bdda9e791942e3d06aedb12463fddc33f07de6f5211c8bf08d"}:
        raise V5ContractError("v5 Tectonic cache contract differs")


def presentation_source_closure(repository_root: Path) -> str:
    """Return the downstream renderer closure without changing scientific GO."""

    root = admit_repository_root(repository_root)
    rows = [
        {"path": relative, "sha256": sha256_bytes(_read(root, relative, "v5 presentation source"))}
        for relative in V5_PRESENTATION_SOURCE_PATHS
    ]
    return sha256_bytes(canonical_json_bytes(rows))


def scientific_source_closure(repository_root: Path) -> str:
    """Return the scientific source closure used by the GO-bound manifest."""

    root = admit_repository_root(repository_root)
    rows = [
        {"path": relative, "sha256": sha256_bytes(_read(root, relative, "v5 scientific source"))}
        for relative in V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS
    ]
    return sha256_bytes(canonical_json_bytes(rows))
