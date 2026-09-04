"""Create a byte-for-byte local release from an approved candidate review set."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.v3_candidate_common import (
    REVIEW_LENS_IDS,
    canonical_json,
    require_create_only_output,
    sha256_path,
    validate_candidate_contract,
)
from tools.verify_v3_measurement_candidate_reviews import (
    ARCHIVE_FILES,
    CandidateReviewError,
    _json,
    _utc,
    revalidate_candidate,
    verify_reviews,
)

ATTESTATION = "I have read the exact candidate manuscript and all ten bound internal review reports. I accept responsibility for the paper's claims, confirm the citations and attribution, confirm the AI-assistance disclosure, and approve the bound arXiv metadata for local release only. This does not authorize outreach, upload, or submission."


class LocalReleaseError(ValueError):
    """Raised when a local release would not bind the approved candidate exactly."""


def _safe_regular(path: Path, label: str) -> None:
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & 0x400):
        raise LocalReleaseError(f"{label} must be a regular non-reparse file")


def revalidate_local_release(
    output: Path,
    candidate_receipt_sha256: str,
    review_set_manifest_sha256: str,
    approval_sha256: str,
) -> dict[str, Any]:
    try:
        metadata = os.lstat(output)
    except OSError as error:
        raise LocalReleaseError("local release output is unavailable") from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & 0x400):
        raise LocalReleaseError("local release output must be a real directory")
    allowlist = tuple(validate_candidate_contract(Path(__file__).resolve().parents[1])["local_release_allowlist"])
    if tuple(sorted(path.name for path in output.iterdir())) != tuple(sorted(allowlist)):
        raise LocalReleaseError("local release completion set differs or receipt is absent")
    for name in allowlist:
        _safe_regular(output / name, f"local release {name}")
    try:
        receipt = _json(output / "local_release_receipt.json")
    except CandidateReviewError as error:
        raise LocalReleaseError("local release receipt is invalid") from error
    expected = {
        "approval_sha256": approval_sha256,
        "archive_sha256": sha256_path(output / "source.zip"),
        "candidate_receipt_sha256": candidate_receipt_sha256,
        "candidate_pdf_sha256": sha256_path(output / "candidate.pdf"),
        "external_authorization_status": "none; local release only; no outreach, upload, or submission authorized",
        "metadata_sha256": sha256_path(output / "arxiv_metadata.json"),
        "review_set_manifest_sha256": review_set_manifest_sha256,
        "schema_version": "anachron-v3-local-release-receipt-v1",
        "state": "local_release",
    }
    if receipt != expected:
        raise LocalReleaseError("local release receipt no longer binds current bytes")
    try:
        with zipfile.ZipFile(output / "source.zip") as archive:
            if tuple(sorted(item.filename for item in archive.infolist())) != ARCHIVE_FILES:
                raise LocalReleaseError("local release source archive allowlist differs")
            for item in archive.infolist():
                name = Path(item.filename)
                mode = item.external_attr >> 16 & 0o170000
                if item.is_dir() or mode == 0o120000 or item.filename != name.as_posix() or name.is_absolute() or ".." in name.parts:
                    raise LocalReleaseError("local release source archive contains an unsafe member")
    except zipfile.BadZipFile as error:
        raise LocalReleaseError("local release source archive is invalid") from error
    try:
        _json(output / "arxiv_metadata.json")
    except CandidateReviewError as error:
        raise LocalReleaseError("local release metadata is invalid") from error
    if tuple(sorted(path.name for path in output.iterdir())) != tuple(sorted(allowlist)):
        raise LocalReleaseError("local release completion set changed during validation")
    return receipt


def _copy(source: Path, target: Path) -> None:
    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
        writer.flush()
        os.fsync(writer.fileno())


def _overlaps(first: Path, second: Path) -> bool:
    try:
        common = os.path.commonpath((os.path.abspath(first), os.path.abspath(second)))
    except ValueError:
        return False
    return common in {os.path.abspath(first), os.path.abspath(second)}


def _approval(approval: Path, receipt: dict[str, Any], candidate_hashes: dict[str, str], review_manifest: Path, reports: list[dict[str, Any]]) -> dict[str, Any]:
    value = _json(approval)
    required = {"abstract_sha256", "ai_assistance_disclosure_sha256", "approval", "approved_at_utc", "approved_by", "archive_sha256", "arxiv_metadata_sha256", "attestation", "candidate_receipt_sha256", "paper_pdf_sha256", "review_set_manifest_sha256", "schema_version", "status"}
    if set(value) != required or value.get("schema_version") != "anachron-v3-candidate-author-approval-v1" or value.get("approved_by") != "Lester Leong" or value.get("status") != "APPROVED" or value.get("approval") != "APPROVED" or value.get("attestation") != ATTESTATION:
        raise LocalReleaseError("author approval schema or identity differs")
    approved_at = _utc(value["approved_at_utc"], "approved_at_utc")
    latest_review = max(datetime.fromisoformat(report["reviewed_at_utc"][:-1] + "+00:00") for report in reports)
    if datetime.fromisoformat(approved_at[:-1] + "+00:00") <= latest_review:
        raise LocalReleaseError("author approval must occur after every review")
    bindings = {
        "abstract_sha256": receipt["abstract_sha256"],
        "ai_assistance_disclosure_sha256": receipt["ai_assistance_disclosure_sha256"],
        "archive_sha256": receipt["archive_sha256"],
        "arxiv_metadata_sha256": candidate_hashes["arxiv_metadata.json"],
        "candidate_receipt_sha256": candidate_hashes["candidate_receipt.json"],
        "paper_pdf_sha256": receipt["candidate_pdf_sha256"],
        "review_set_manifest_sha256": sha256_path(review_manifest),
    }
    if any(type(value[key]) is not str or value[key] != expected for key, expected in bindings.items()):
        raise LocalReleaseError("author approval binding differs")
    return value


def release_candidate(candidate: Path, reviews: Path, review_manifest: Path, approval: Path, output: Path) -> dict[str, Any]:
    require_create_only_output(output, (candidate, reviews, review_manifest, approval))
    try:
        receipt, hashes = revalidate_candidate(candidate)
        reports = verify_reviews(candidate, reviews)
    except CandidateReviewError as error:
        raise LocalReleaseError("candidate or review verification failed") from error
    manifest = _json(review_manifest)
    expected_manifest = {"archive_sha256": receipt["archive_sha256"], "candidate_receipt_sha256": hashes["candidate_receipt.json"], "evidence_manifest_sha256": receipt["evidence_manifest_sha256"], "lens_ids": list(REVIEW_LENS_IDS), "paper_pdf_sha256": receipt["candidate_pdf_sha256"], "paper_source_manifest_sha256": receipt["paper_source_manifest_sha256"], "projection_sha256": receipt["projection_sha256"], "reports": reports, "schema_version": "anachron-v3-candidate-review-set-v1"}
    if manifest != expected_manifest:
        raise LocalReleaseError("review-set manifest differs from current review reports")
    approval_value = _approval(approval, receipt, hashes, review_manifest, reports)
    output.mkdir()
    _copy(candidate / "candidate.pdf", output / "candidate.pdf")
    _copy(candidate / "source.zip", output / "source.zip")
    _copy(candidate / "arxiv_metadata.json", output / "arxiv_metadata.json")
    try:
        current_receipt, current_hashes = revalidate_candidate(candidate)
        current_reports = verify_reviews(candidate, reviews)
    except CandidateReviewError as error:
        raise LocalReleaseError("candidate or reviews changed during release") from error
    if current_receipt != receipt or current_hashes != hashes or current_reports != reports or _json(review_manifest) != manifest or _json(approval) != approval_value:
        raise LocalReleaseError("release inputs changed during copy")
    if (
        sha256_path(output / "candidate.pdf") != receipt["candidate_pdf_sha256"]
        or sha256_path(output / "source.zip") != receipt["archive_sha256"]
        or sha256_path(output / "arxiv_metadata.json") != hashes["arxiv_metadata.json"]
        or tuple(sorted(path.name for path in output.iterdir())) != ("arxiv_metadata.json", "candidate.pdf", "source.zip")
    ):
        raise LocalReleaseError("local release bytes or pre-receipt completion set differs")
    release_receipt = {
            "approval_sha256": sha256_path(approval),
            "archive_sha256": sha256_path(output / "source.zip"),
            "candidate_receipt_sha256": hashes["candidate_receipt.json"],
            "candidate_pdf_sha256": sha256_path(output / "candidate.pdf"),
            "external_authorization_status": "none; local release only; no outreach, upload, or submission authorized",
            "metadata_sha256": sha256_path(output / "arxiv_metadata.json"),
            "review_set_manifest_sha256": sha256_path(review_manifest),
            "schema_version": "anachron-v3-local-release-receipt-v1",
            "state": "local_release",
    }
    with (output / "local_release_receipt.json").open("xb") as handle:
        handle.write(canonical_json(release_receipt))
        handle.flush()
        os.fsync(handle.fileno())
    revalidate_local_release(
        output,
        hashes["candidate_receipt.json"],
        sha256_path(review_manifest),
        sha256_path(approval),
    )
    return {"approval": approval_value, "receipt": release_receipt}


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--review-manifest", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    values = parser.parse_args(arguments)
    print(json.dumps(release_candidate(values.candidate, values.reviews, values.review_manifest, values.approval, values.output), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
