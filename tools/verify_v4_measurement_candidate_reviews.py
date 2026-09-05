"""Validate ten human-authored v4 reviews and create one bound local manifest."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

from anachron.v4_candidate_common import pooled_tclr_direction
from anachron.v4_candidate_release_common import (
    REVIEW_MANIFEST_SCHEMA,
    CandidateReleaseError,
    _completion,
    _external_directory,
    _file_child,
    bounded_json,
    candidate_closure,
    named_reviewer,
    sha256,
    strict_utc,
    substantive_text,
    write_create_only,
)
from anachron.v4_contract import V4_CANDIDATE_REVIEW_LENS_IDS
from anachron.v4_paths import (
    V4PathError,
    admit_external_regular_input,
    admit_repository_root,
)


def _reports(
    root: Path, candidate: Path, reports: Path
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, tuple[bytes, str]]]:
    closure, bindings = candidate_closure(root, candidate)
    reports = _external_directory(root, reports, "review reports")
    _completion(
        reports,
        tuple(f"{lens}.json" for lens in V4_CANDIDATE_REVIEW_LENS_IDS),
        "review reports",
    )
    maximum = closure["contract"]["resource_policy"]["candidate_projection_max_bytes"]
    string_maximum = closure["contract"]["resource_policy"]["string_max_bytes"]
    required = {
        "archive_sha256",
        "arxiv_metadata_sha256",
        "candidate_contract_sha256",
        "candidate_receipt_sha256",
        "evidence_manifest_sha256",
        "findings",
        "lens_id",
        "paper_source_manifest_sha256",
        "paper_pdf_sha256",
        "projection_sha256",
        "result_direction",
        "resolutions",
        "reviewed_at_utc",
        "reviewer",
        "schema_version",
        "status",
        "v3_included_count",
    }
    rows: list[dict[str, Any]] = []
    snapshots: dict[str, tuple[bytes, str]] = {}
    direction = pooled_tclr_direction(closure["projection"])
    for lens in V4_CANDIDATE_REVIEW_LENS_IDS:
        path = _file_child(reports, f"{lens}.json", "review report")
        value, raw = bounded_json(path, maximum, "review report")
        if (
            set(value) != required
            or value["schema_version"] != "anachron-v4-candidate-review-v1"
            or value["status"] != "APPROVED"
            or value["lens_id"] != lens
            or value["v3_included_count"] != 0
            or value["result_direction"] != direction
        ):
            raise CandidateReleaseError("review report status differs")
        named_reviewer(value["reviewer"], string_maximum)
        strict_utc(value["reviewed_at_utc"], "reviewed_at_utc")
        for field in ("findings", "resolutions"):
            if type(value[field]) is not list or not value[field]:
                raise CandidateReleaseError("review report findings differ")
            for index, item in enumerate(value[field]):
                substantive_text(item, f"review report {field}[{index}]", string_maximum)
        if any(value[key] != digest for key, digest in bindings.items()):
            raise CandidateReleaseError("review report binding differs")
        digest = hashlib.sha256(raw).hexdigest()
        if sha256(path, maximum) != digest:
            raise CandidateReleaseError("review report changed during validation")
        snapshots[lens] = (raw, digest)
        rows.append(
            {
                "lens_id": lens,
                "reviewed_at_utc": value["reviewed_at_utc"],
                "reviewer": value["reviewer"],
                "sha256": digest,
            }
        )
    _, current = candidate_closure(root, candidate)
    if current != bindings:
        raise CandidateReleaseError("candidate changed during review validation")
    return rows, bindings, snapshots


def _review_manifest(
    root: Path, candidate: Path, reports: Path
) -> tuple[dict[str, Any], dict[str, tuple[bytes, str]]]:
    rows, bindings, snapshots = _reports(root, candidate, reports)
    return (
        {
            "archive_sha256": bindings["archive_sha256"],
            "arxiv_metadata_sha256": bindings["arxiv_metadata_sha256"],
            "candidate_contract_sha256": bindings["candidate_contract_sha256"],
            "candidate_receipt_sha256": bindings["candidate_receipt_sha256"],
            "evidence_manifest_sha256": bindings["evidence_manifest_sha256"],
            "paper_pdf_sha256": bindings["paper_pdf_sha256"],
            "paper_source_manifest_sha256": bindings["paper_source_manifest_sha256"],
            "projection_sha256": bindings["projection_sha256"],
            "review_lens_ids": list(V4_CANDIDATE_REVIEW_LENS_IDS),
            "review_reports": rows,
            "schema_version": REVIEW_MANIFEST_SCHEMA,
            "v3_included_count": 0,
        },
        snapshots,
    )


def _revalidate_snapshot(
    root: Path,
    candidate: Path,
    reports: Path,
    manifest: dict[str, Any],
    snapshots: dict[str, tuple[bytes, str]],
) -> None:
    current, current_snapshots = _review_manifest(root, candidate, reports)
    if current != manifest or current_snapshots != snapshots:
        raise CandidateReleaseError("review reports changed during manifest publication")


def expected_review_set_manifest(
    repository_root: Path, candidate: Path, reports: Path
) -> dict[str, Any]:
    """Return the one manifest that a complete approved review directory may claim."""

    root = admit_repository_root(repository_root)
    return _review_manifest(root, candidate, reports)[0]


def validate_review_set_manifest(
    repository_root: Path, candidate: Path, reports: Path, manifest: Path
) -> dict[str, Any]:
    """Revalidate an existing manifest against current report and candidate bytes."""

    root = admit_repository_root(repository_root)
    expected = expected_review_set_manifest(root, candidate, reports)
    try:
        manifest = admit_external_regular_input(manifest, root, "review-set manifest")
    except V4PathError as error:
        raise CandidateReleaseError(str(error)) from error
    observed, _ = bounded_json(manifest, 1048576, "review-set manifest")
    if observed != expected:
        raise CandidateReleaseError("review-set manifest differs from current reviews")
    return observed


def verify(repository_root: Path, candidate: Path, reports: Path, output: Path) -> dict[str, Any]:
    """Create one create-only manifest; this command never creates review reports."""

    root = admit_repository_root(repository_root)
    manifest, snapshots = _review_manifest(root, candidate, reports)
    write_create_only(
        output,
        root,
        manifest,
        before_publish=lambda: _revalidate_snapshot(
            root, candidate, reports, manifest, snapshots
        ),
    )
    return manifest


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    verify(**vars(parser.parse_args(arguments)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
