"""Create a byte-copy v5 local release after ten approvals and Lester's approval."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

from anachron.v5_candidate_release_common import (
    LOCAL_RELEASE_RECEIPT_SCHEMA,
    CandidateReleaseError,
    bounded_json,
    candidate_closure,
    create_staging_directory,
    local_release_root_profile,
    publish_staging_directory,
    remove_staging,
    sha256,
    strict_utc,
)
from anachron.v5_custody import (
    ByteBudget,
    V5CustodyError,
    capture_regular,
    write_create_only,
)
from anachron.v5_paths import (
    V5PathError,
    admit_external_regular_input,
    admit_repository_root,
)
from anachron.v5_registry import canonical_json_bytes
from tools.verify_v5_measurement_candidate_reviews import validate_review_set_manifest


def _copy(source: Path, target: Path, maximum: int, budget: ByteBudget) -> None:
    try:
        raw = capture_regular(source, "candidate artifact", maximum).raw
        write_create_only(target, raw, "local release artifact", budget)
    except V5CustodyError as error:
        raise CandidateReleaseError("candidate artifact cannot be copied") from error


def _approval(root: Path, closure: dict[str, Any], bindings: dict[str, str], manifest: Path, approval: Path) -> tuple[dict[str, Any], bytes]:
    try:
        approval = admit_external_regular_input(approval, root, "author approval")
    except V5PathError as error:
        raise CandidateReleaseError(str(error)) from error
    value, raw = bounded_json(approval, 1_048_576, "author approval")
    template, _ = bounded_json(root / "paper/v5_measurement/author_approval.template.json", 1_048_576, "author approval template")
    required = {"abstract_sha256", "ai_assistance_disclosure_sha256", "approval", "approved_at_utc", "approved_by", "archive_sha256", "arxiv_metadata_sha256", "attestation", "candidate_receipt_sha256", "paper_pdf_sha256", "projection_sha256", "review_set_manifest_sha256", "schema_version", "status", "v4_included_count"}
    expected = {"abstract_sha256": hashlib.sha256(closure["metadata"]["abstract"].encode("utf-8")).hexdigest(), "ai_assistance_disclosure_sha256": hashlib.sha256(closure["metadata"]["ai_assistance_disclosure"].encode("utf-8")).hexdigest(), "approval": "APPROVED", "approved_by": "Lester Leong", "archive_sha256": bindings["archive_sha256"], "arxiv_metadata_sha256": bindings["arxiv_metadata_sha256"], "attestation": template.get("attestation"), "candidate_receipt_sha256": bindings["candidate_receipt_sha256"], "paper_pdf_sha256": bindings["paper_pdf_sha256"], "projection_sha256": bindings["projection_sha256"], "review_set_manifest_sha256": sha256(manifest), "schema_version": "anachron-v5-candidate-author-approval-v1", "status": "APPROVED", "v4_included_count": 0}
    if set(value) != required or any(value.get(key) != expected[key] for key in expected):
        raise CandidateReleaseError("author approval binding differs")
    strict_utc(value["approved_at_utc"], "approved_at_utc")
    return value, raw


def release(repository_root: Path, candidate: Path, reports: Path, review_manifest: Path, approval: Path, output: Path) -> dict[str, Any]:
    root = admit_repository_root(repository_root)
    closure, bindings = candidate_closure(root, candidate)
    manifest = validate_review_set_manifest(root, candidate, reports, review_manifest)
    approval_value, approval_raw = _approval(root, closure, bindings, review_manifest, approval)
    target, staging = create_staging_directory(output, root, Path(candidate), Path(reports), Path(review_manifest), Path(approval))
    try:
        profile = local_release_root_profile()
        budget = profile.budget()
        for name in ("candidate.pdf", "source.zip", "arxiv_metadata.json"):
            _copy(Path(candidate) / name, staging / name, profile.member_cap(name), budget)
        files = {"arxiv_metadata.json": sha256(staging / "arxiv_metadata.json"), "candidate.pdf": sha256(staging / "candidate.pdf"), "source.zip": sha256(staging / "source.zip")}
        if files != {"arxiv_metadata.json": bindings["arxiv_metadata_sha256"], "candidate.pdf": bindings["paper_pdf_sha256"], "source.zip": bindings["archive_sha256"]}:
            raise CandidateReleaseError("local release bytes differ from candidate")
        receipt = {"approval_sha256": hashlib.sha256(approval_raw).hexdigest(), "candidate_receipt_sha256": bindings["candidate_receipt_sha256"], "local_release_files": files, "review_set_manifest_sha256": sha256(review_manifest), "review_snapshot_sha256": manifest["review_snapshot_sha256"], "schema_version": LOCAL_RELEASE_RECEIPT_SCHEMA, "v4_included_count": 0}
        write_create_only(
            staging / "local_release_receipt.json",
            canonical_json_bytes(receipt),
            "local release receipt",
            budget,
        )
        fresh_closure, fresh_bindings = candidate_closure(root, candidate)
        fresh_manifest = validate_review_set_manifest(root, candidate, reports, review_manifest)
        fresh_approval, fresh_approval_raw = _approval(root, fresh_closure, fresh_bindings, review_manifest, approval)
        if fresh_bindings != bindings or fresh_manifest != manifest or fresh_approval != approval_value or fresh_approval_raw != approval_raw:
            raise CandidateReleaseError("candidate review or approval changed during release")
        publish_staging_directory(staging, target)
        return receipt
    except Exception:
        remove_staging(staging)
        raise


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--reports", required=True, type=Path)
    parser.add_argument("--review-manifest", required=True, type=Path)
    parser.add_argument("--approval", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    values = parser.parse_args(arguments)
    release(**vars(values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
