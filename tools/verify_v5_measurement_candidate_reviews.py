"""Verify ten byte-bound v5 review reports and create one review-set manifest."""

from __future__ import annotations

import argparse
import hashlib
import tempfile
from pathlib import Path
from typing import Any

from anachron.v5_candidate_release_common import (
    REVIEW_MANIFEST_SCHEMA,
    V5_CANDIDATE_REVIEW_LENS_IDS,
    CandidateReleaseError,
    _completion,
    _external_directory,
    _file_child,
    bounded_json,
    candidate_closure,
    named_reviewer,
    remove_staging,
    sha256,
    strict_utc,
    substantive_text,
)
from anachron.v5_custody import (
    ByteBudget,
    V5CustodyError,
    capture_regular,
    write_create_only,
)
from anachron.v5_paths import (
    V5PathError,
    admit_create_only_external_output,
    admit_repository_root,
    fsync_directory,
)
from anachron.v5_registry import canonical_json_bytes


def _snapshot_reports(root: Path, reports: Path, parent: Path) -> tuple[Path, dict[str, str]]:
    reports = _external_directory(root, reports, "review reports")
    _completion(reports, tuple(f"{lens}.json" for lens in V5_CANDIDATE_REVIEW_LENS_IDS), "review reports")
    snapshot = Path(tempfile.mkdtemp(prefix=".v5-review-snapshot-", dir=parent))
    digests = {}
    budget = ByteBudget(10 * 1_048_576, len(V5_CANDIDATE_REVIEW_LENS_IDS))
    try:
        for lens in V5_CANDIDATE_REVIEW_LENS_IDS:
            source = _file_child(reports, f"{lens}.json", "review report")
            target = snapshot / source.name
            raw = capture_regular(source, "review report", 1_048_576).raw
            write_create_only(target, raw, "review snapshot", budget)
            digests[lens] = sha256(target)
        if digests != {lens: sha256(_file_child(reports, f"{lens}.json", "review report")) for lens in V5_CANDIDATE_REVIEW_LENS_IDS}:
            raise CandidateReleaseError("review reports changed during snapshot")
        return snapshot, digests
    except Exception:
        remove_staging(snapshot)
        raise


def _reports(root: Path, candidate: Path, reports: Path) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    _, bindings = candidate_closure(root, candidate)
    reports = _external_directory(root, reports, "review reports")
    _completion(reports, tuple(f"{lens}.json" for lens in V5_CANDIDATE_REVIEW_LENS_IDS), "review reports")
    required = {"archive_sha256", "arxiv_metadata_sha256", "candidate_contract_sha256", "candidate_receipt_sha256", "evidence_manifest_sha256", "findings", "lens_id", "paper_pdf_sha256", "paper_source_manifest_sha256", "projection_sha256", "resolutions", "reviewed_at_utc", "reviewer", "schema_version", "status", "v4_included_count"}
    rows, snapshots = [], {}
    for lens in V5_CANDIDATE_REVIEW_LENS_IDS:
        path = _file_child(reports, f"{lens}.json", "review report")
        report, raw = bounded_json(path, 1_048_576, "review report")
        if set(report) != required or report["schema_version"] != "anachron-v5-candidate-review-v1" or report["status"] != "APPROVED" or report["lens_id"] != lens or report["v4_included_count"] != 0:
            raise CandidateReleaseError("review report status differs")
        named_reviewer(report["reviewer"])
        strict_utc(report["reviewed_at_utc"], "reviewed_at_utc")
        for field in ("findings", "resolutions"):
            if type(report[field]) is not list or not report[field]:
                raise CandidateReleaseError("review report substance differs")
            for index, value in enumerate(report[field]):
                substantive_text(value, f"review report {field}[{index}]")
        if any(report[key] != digest for key, digest in bindings.items()):
            raise CandidateReleaseError("review report binding differs")
        digest = hashlib.sha256(raw).hexdigest()
        if sha256(path) != digest:
            raise CandidateReleaseError("review report changed during validation")
        snapshots[lens] = digest
        rows.append({"lens_id": lens, "reviewed_at_utc": report["reviewed_at_utc"], "reviewer": report["reviewer"], "sha256": digest})
    _, fresh = candidate_closure(root, candidate)
    if fresh != bindings:
        raise CandidateReleaseError("candidate changed during review validation")
    return rows, bindings, snapshots


def expected_review_set_manifest(repository_root: Path, candidate: Path, reports: Path) -> dict[str, Any]:
    root = admit_repository_root(repository_root)
    rows, bindings, snapshots = _reports(root, candidate, reports)
    return {**bindings, "review_lens_ids": list(V5_CANDIDATE_REVIEW_LENS_IDS), "review_reports": rows, "review_snapshot_sha256": hashlib.sha256(canonical_json_bytes(snapshots)).hexdigest(), "schema_version": REVIEW_MANIFEST_SCHEMA, "v4_included_count": 0}


def validate_review_set_manifest(repository_root: Path, candidate: Path, reports: Path, manifest: Path) -> dict[str, Any]:
    root = admit_repository_root(repository_root)
    expected = expected_review_set_manifest(root, candidate, reports)
    try:
        manifest = Path(manifest).resolve(strict=True)
        manifest.relative_to(root)
        raise CandidateReleaseError("review-set manifest must be external")
    except ValueError:
        pass
    observed, _ = bounded_json(manifest, 1_048_576, "review-set manifest")
    if observed != expected:
        raise CandidateReleaseError("review-set manifest differs from current reviews")
    return observed


def verify(repository_root: Path, candidate: Path, reports: Path, output: Path, *, before_publish: Any = None) -> dict[str, Any]:
    root = admit_repository_root(repository_root)
    try:
        output = admit_create_only_external_output(output, root, "review-set manifest output")
        snapshot, source_digests = _snapshot_reports(root, reports, output.parent)
        try:
            rows, bindings, snapshot_digests = _reports(root, candidate, snapshot)
            manifest = {**bindings, "review_lens_ids": list(V5_CANDIDATE_REVIEW_LENS_IDS), "review_reports": rows, "review_snapshot_sha256": hashlib.sha256(canonical_json_bytes(snapshot_digests)).hexdigest(), "schema_version": REVIEW_MANIFEST_SCHEMA, "v4_included_count": 0}
            if source_digests != snapshot_digests:
                raise CandidateReleaseError("review reports changed during snapshot")
            if before_publish is not None:
                before_publish()
            if source_digests != {lens: sha256(Path(reports) / f"{lens}.json") for lens in V5_CANDIDATE_REVIEW_LENS_IDS}:
                raise CandidateReleaseError("review reports changed during manifest publication")
            if candidate_closure(root, candidate)[1] != bindings:
                raise CandidateReleaseError("candidate changed during manifest publication")
            write_create_only(
                output,
                canonical_json_bytes(manifest),
                "review-set manifest output",
                ByteBudget(1_048_576, 1),
            )
            fsync_directory(output.parent, "review-set manifest output parent")
        finally:
            remove_staging(snapshot)
    except (OSError, V5CustodyError, V5PathError) as error:
        raise CandidateReleaseError("review-set manifest cannot be published") from error
    return manifest


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--reports", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    values = parser.parse_args(arguments)
    verify(**vars(values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
