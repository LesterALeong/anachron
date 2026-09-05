"""Create a byte-copy local v4 release after Lester's exact bound approval."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from typing import Any

from anachron.data.v4_registry import canonical_json_bytes
from anachron.v4_candidate_release_common import (
    LOCAL_RELEASE_RECEIPT_SCHEMA,
    CandidateReleaseError,
    _file_child,
    bounded_json,
    candidate_closure,
    create_staging_directory,
    publish_staging_directory,
    remove_staging,
    sha256,
    strict_utc,
)
from anachron.v4_paths import (
    V4PathError,
    admit_external_regular_input,
    admit_repository_root,
)
from tools.verify_v4_measurement_candidate_reviews import validate_review_set_manifest


def _copy(source: Path, target: Path, maximum: int) -> None:
    try:
        with source.open("rb") as reader, target.open("xb") as writer:
            remaining = maximum
            while chunk := reader.read(min(65536, remaining + 1)):
                if len(chunk) > remaining:
                    raise CandidateReleaseError("candidate artifact exceeds the contract byte cap")
                writer.write(chunk)
                remaining -= len(chunk)
            writer.flush()
            os.fsync(writer.fileno())
    except OSError as error:
        raise CandidateReleaseError("candidate artifact cannot be copied") from error


def _approval(
    root: Path,
    closure: dict[str, Any],
    bindings: dict[str, str],
    review_manifest: Path,
    approval: Path,
) -> tuple[dict[str, Any], bytes]:
    try:
        approval = admit_external_regular_input(approval, root, "author approval")
    except V4PathError as error:
        raise CandidateReleaseError(str(error)) from error
    maximum = closure["contract"]["resource_policy"]["candidate_projection_max_bytes"]
    value, raw = bounded_json(approval, maximum, "author approval")
    review_manifest_value, _ = bounded_json(
        review_manifest, maximum, "review-set manifest"
    )
    template_path = root / "paper/v4_measurement/author_approval.template.json"
    template, _ = bounded_json(template_path, maximum, "author approval template")
    expected = {
        "abstract_sha256": hashlib.sha256(
            closure["metadata"]["abstract"].encode("utf-8")
        ).hexdigest(),
        "ai_assistance_disclosure_sha256": hashlib.sha256(
            closure["metadata"]["ai_assistance_disclosure"].encode("utf-8")
        ).hexdigest(),
        "approval": "APPROVED",
        "approved_by": "Lester Leong",
        "archive_sha256": bindings["archive_sha256"],
        "arxiv_metadata_sha256": bindings["arxiv_metadata_sha256"],
        "attestation": template["attestation"],
        "candidate_receipt_sha256": bindings["candidate_receipt_sha256"],
        "paper_pdf_sha256": bindings["paper_pdf_sha256"],
        "projection_sha256": bindings["projection_sha256"],
        "review_set_manifest_sha256": sha256(review_manifest, maximum),
        "schema_version": "anachron-v4-candidate-author-approval-v1",
        "status": "APPROVED",
        "v3_included_count": 0,
    }
    if set(value) != set(template) or any(value.get(key) != expected[key] for key in expected):
        raise CandidateReleaseError("author approval binding differs")
    approved_at = strict_utc(value.get("approved_at_utc"), "approved_at_utc")
    review_times = [
        strict_utc(report["reviewed_at_utc"], "reviewed_at_utc")
        for report in review_manifest_value["review_reports"]
    ]
    if approved_at <= max(review_times):
        raise CandidateReleaseError("author approval must occur after every review")
    return value, raw


def release(
    repository_root: Path,
    candidate: Path,
    reports: Path,
    review_manifest: Path,
    approval: Path,
    output: Path,
) -> dict[str, Any]:
    """Create the exact four-file local release; no artifact is rebuilt."""

    root = admit_repository_root(repository_root)
    closure, bindings = candidate_closure(root, candidate)
    try:
        review_manifest = admit_external_regular_input(
            review_manifest, root, "review-set manifest"
        )
    except V4PathError as error:
        raise CandidateReleaseError(str(error)) from error
    manifest = validate_review_set_manifest(root, candidate, reports, review_manifest)
    approval_value, approval_raw = _approval(
        root, closure, bindings, review_manifest, approval
    )
    output, staging = create_staging_directory(
        output, root, closure["candidate"], reports, review_manifest, approval
    )
    policy = closure["contract"]["resource_policy"]
    try:
        for name, maximum in (
            ("candidate.pdf", policy["pdf_max_bytes"]),
            ("source.zip", policy["source_archive_max_bytes"]),
            ("arxiv_metadata.json", policy["candidate_projection_max_bytes"]),
        ):
            _copy(_file_child(closure["candidate"], name, "candidate artifact"), staging / name, maximum)
        current_closure, current_bindings = candidate_closure(root, candidate)
        current_manifest = validate_review_set_manifest(
            root, candidate, reports, review_manifest
        )
        current_approval, current_approval_raw = _approval(
            root, current_closure, current_bindings, review_manifest, approval
        )
        if (
            current_bindings != bindings
            or current_manifest != manifest
            or current_approval != approval_value
            or current_approval_raw != approval_raw
        ):
            raise CandidateReleaseError("candidate review or approval changed during release")
        files = {
            "arxiv_metadata.json": sha256(
                staging / "arxiv_metadata.json", policy["candidate_projection_max_bytes"]
            ),
            "candidate.pdf": sha256(staging / "candidate.pdf", policy["pdf_max_bytes"]),
            "source.zip": sha256(staging / "source.zip", policy["source_archive_max_bytes"]),
        }
        if files != {
            "arxiv_metadata.json": bindings["arxiv_metadata_sha256"],
            "candidate.pdf": bindings["paper_pdf_sha256"],
            "source.zip": bindings["archive_sha256"],
        }:
            raise CandidateReleaseError("local release bytes differ from the candidate")
        receipt = {
            "approval_sha256": hashlib.sha256(approval_raw).hexdigest(),
            "candidate_receipt_sha256": bindings["candidate_receipt_sha256"],
            "local_release_files": files,
            "review_set_manifest_sha256": sha256(review_manifest, policy["candidate_projection_max_bytes"]),
            "schema_version": LOCAL_RELEASE_RECEIPT_SCHEMA,
            "v3_included_count": 0,
        }
        (staging / "local_release_receipt.json").write_bytes(canonical_json_bytes(receipt))
        if tuple(sorted(item.name for item in staging.iterdir())) != (
            "arxiv_metadata.json",
            "candidate.pdf",
            "local_release_receipt.json",
            "source.zip",
        ):
            raise CandidateReleaseError("local release completion set differs")
        publish_staging_directory(staging, output)
        return receipt
    except Exception:
        remove_staging(staging)
        raise


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--review-manifest", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    release(**vars(parser.parse_args(arguments)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
