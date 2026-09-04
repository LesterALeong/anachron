"""Verify exact local candidate review bindings and create a review-set manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.build_v3_measurement_candidate_paper import (
    ARCHIVE_FILES,
    CANDIDATE_COMPLETION,
    _manifest,
    _metadata,
    validate_projection,
    validate_receipt,
    validate_template,
)
from tools.v3_candidate_common import (
    REVIEW_LENS_IDS,
    canonical_json,
    sha256_path,
    validate_candidate_contract,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_REPARSE = 0x400


class CandidateReviewError(ValueError):
    """Raised when a local review cannot be bound to the exact candidate."""


def _json(path: Path) -> dict[str, Any]:
    def duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise CandidateReviewError(f"duplicate JSON key: {path}")
            result[key] = value
        return result

    def nonfinite(value: str) -> object:
        raise CandidateReviewError(f"non-finite JSON value {value}: {path}")

    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=duplicates, parse_constant=nonfinite)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CandidateReviewError(f"invalid JSON: {path}") from error
    if type(value) is not dict:
        raise CandidateReviewError(f"JSON object required: {path}")
    if raw != canonical_json(value):
        raise CandidateReviewError(f"noncanonical JSON: {path}")
    return value


def _safe_directory(path: Path, label: str) -> None:
    metadata = os.lstat(path)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE):
        raise CandidateReviewError(f"{label} must be a real directory")


def _utc(value: object, label: str) -> str:
    if type(value) is not str or not value.endswith("Z"):
        raise CandidateReviewError(f"{label} must be an explicit UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise CandidateReviewError(f"{label} is not an ISO-8601 timestamp") from error
    if parsed.tzinfo != timezone.utc:
        raise CandidateReviewError(f"{label} must be UTC-aware")
    return value


def _hashes(candidate: Path) -> dict[str, str]:
    required = ("candidate_receipt.json", "projection.json", "paper_source_manifest.json", "candidate.pdf", "source.zip", "arxiv_metadata.json")
    if any(not (candidate / name).is_file() for name in required):
        raise CandidateReviewError("candidate is missing a required artifact")
    return {name: sha256_path(candidate / name) for name in required}


def _require_candidate_completion(candidate: Path) -> None:
    _safe_directory(candidate, "candidate")
    if tuple(sorted(entry.name for entry in candidate.iterdir())) != tuple(sorted(CANDIDATE_COMPLETION)):
        raise CandidateReviewError("candidate completion set differs or receipt is absent")


def revalidate_candidate(candidate: Path) -> tuple[dict[str, Any], dict[str, str]]:
    _require_candidate_completion(candidate)
    contract = validate_candidate_contract(REPOSITORY_ROOT)
    projection = _json(candidate / "projection.json")
    try:
        validate_projection(projection)
    except ValueError as error:
        raise CandidateReviewError("candidate projection differs") from error
    hashes = _hashes(candidate)
    receipt = _json(candidate / "candidate_receipt.json")
    try:
        validate_receipt(receipt, contract)
    except ValueError as error:
        raise CandidateReviewError("candidate receipt differs") from error
    expected = {
        "projection_sha256": hashes["projection.json"],
        "paper_source_manifest_sha256": hashes["paper_source_manifest.json"],
        "candidate_pdf_sha256": hashes["candidate.pdf"],
        "archive_sha256": hashes["source.zip"],
        "arxiv_metadata_sha256": hashes["arxiv_metadata.json"],
    }
    if any(receipt[key] != value for key, value in expected.items()):
        raise CandidateReviewError("candidate receipt no longer binds current bytes")
    source = candidate / "source"
    _safe_directory(source, "candidate source")
    if tuple(sorted(path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file())) != ARCHIVE_FILES:
        raise CandidateReviewError("paper source allowlist differs")
    if any(path.is_symlink() or bool(getattr(os.lstat(path), "st_file_attributes", 0) & _REPARSE) for path in source.rglob("*")):
        raise CandidateReviewError("paper source contains a link")
    manifest = _json(candidate / "paper_source_manifest.json")
    if manifest != _manifest(source):
        raise CandidateReviewError("paper source manifest differs")
    with zipfile.ZipFile(candidate / "source.zip") as archive:
        if tuple(sorted(item.filename for item in archive.infolist())) != ARCHIVE_FILES:
            raise CandidateReviewError("source archive allowlist differs")
        for name in ARCHIVE_FILES:
            item = archive.getinfo(name)
            mode = item.external_attr >> 16 & 0o170000
            if item.is_dir() or mode == 0o120000 or Path(name).is_absolute() or ".." in Path(name).parts or archive.read(name) != (source / name).read_bytes():
                raise CandidateReviewError("source archive bytes differ")
    metadata = _json(candidate / "arxiv_metadata.json")
    try:
        expected_metadata = _metadata(validate_template(REPOSITORY_ROOT), projection)
        metadata_hashes_match = hashlib.sha256(metadata["abstract"].encode("utf-8")).hexdigest() == receipt["abstract_sha256"] and hashlib.sha256(metadata["ai_assistance_disclosure"].encode("utf-8")).hexdigest() == receipt["ai_assistance_disclosure_sha256"]
    except (KeyError, TypeError, AttributeError) as error:
        raise CandidateReviewError("candidate metadata schema differs") from error
    if metadata != expected_metadata or not metadata_hashes_match:
        raise CandidateReviewError("candidate metadata differs")
    _require_candidate_completion(candidate)
    if hashes != _hashes(candidate):
        raise CandidateReviewError("candidate bytes changed during validation")
    return receipt, hashes


def verify_reviews(candidate: Path, reviews: Path) -> list[dict[str, Any]]:
    receipt, hashes = revalidate_candidate(candidate)
    _safe_directory(reviews, "reviews")
    expected = tuple(f"{lens}.json" for lens in REVIEW_LENS_IDS)
    observed = tuple(sorted(entry.name for entry in reviews.iterdir()))
    if observed != tuple(sorted(expected)):
        raise CandidateReviewError("reviews directory must contain exactly the frozen review reports")
    reports = []
    expected_keys = {"archive_sha256", "candidate_receipt_sha256", "evidence_manifest_sha256", "findings", "lens_id", "paper_source_manifest_sha256", "paper_pdf_sha256", "projection_sha256", "resolutions", "reviewed_at_utc", "reviewer", "schema_version", "status"}
    bindings = {
        "candidate_receipt_sha256": hashes["candidate_receipt.json"],
        "projection_sha256": receipt["projection_sha256"],
        "paper_source_manifest_sha256": receipt["paper_source_manifest_sha256"],
        "evidence_manifest_sha256": receipt["evidence_manifest_sha256"],
        "paper_pdf_sha256": receipt["candidate_pdf_sha256"],
        "archive_sha256": receipt["archive_sha256"],
    }
    for lens in REVIEW_LENS_IDS:
        path = reviews / f"{lens}.json"
        if path.is_symlink() or not path.is_file():
            raise CandidateReviewError("review reports must be regular files")
        report = _json(path)
        if set(report) != expected_keys or report.get("schema_version") != "anachron-v3-candidate-review-v1" or report.get("lens_id") != lens or report.get("status") != "APPROVED" or report.get("findings") != [] or report.get("resolutions") != [] or type(report.get("reviewer")) is not str or not report["reviewer"].strip() or report["reviewer"].startswith("REPLACE_"):
            raise CandidateReviewError("review report schema or approval differs")
        _utc(report["reviewed_at_utc"], "reviewed_at_utc")
        if any(type(report[key]) is not str or report[key] != value for key, value in bindings.items()):
            raise CandidateReviewError("review report binding differs")
        reports.append({"filename": path.name, "reviewed_at_utc": report["reviewed_at_utc"], "reviewer": report["reviewer"], "sha256": sha256_path(path)})
    return reports


def create_review_set_manifest(candidate: Path, reviews: Path, output: Path) -> dict[str, Any]:
    if os.path.lexists(output):
        raise FileExistsError("review-set manifest output must not already exist")
    _safe_directory(output.parent, "review-set manifest parent")
    if any(os.path.commonpath((os.path.abspath(output), os.path.abspath(path))) == os.path.abspath(path) for path in (candidate, reviews)):
        raise CandidateReviewError("review-set manifest output must not overlap an input")
    reports = verify_reviews(candidate, reviews)
    receipt, hashes = revalidate_candidate(candidate)
    manifest = {"archive_sha256": receipt["archive_sha256"], "candidate_receipt_sha256": hashes["candidate_receipt.json"], "evidence_manifest_sha256": receipt["evidence_manifest_sha256"], "lens_ids": list(REVIEW_LENS_IDS), "paper_pdf_sha256": receipt["candidate_pdf_sha256"], "paper_source_manifest_sha256": receipt["paper_source_manifest_sha256"], "projection_sha256": receipt["projection_sha256"], "reports": reports, "schema_version": "anachron-v3-candidate-review-set-v1"}
    with output.open("xb") as handle:
        handle.write(canonical_json(manifest))
        handle.flush()
        os.fsync(handle.fileno())
    return manifest


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    values = parser.parse_args(arguments)
    print(json.dumps(create_review_set_manifest(values.candidate, values.reviews, values.output), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
