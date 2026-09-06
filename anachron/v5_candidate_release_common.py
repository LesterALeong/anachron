"""Strict local-only closure checks for v5 paper, reviews, and release artifacts."""

from __future__ import annotations

import hashlib
import io
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from anachron.v5_candidate_common import (
    CandidateProjectionError,
    validate_candidate_projection,
)
from anachron.v5_contract import presentation_source_closure
from anachron.v5_custody import (
    RootProfile,
    V5CustodyError,
    bounded_regular_files,
    capture_regular,
    discard_staging_root,
    publish_staging_root,
    scandir_exact,
)
from anachron.v5_paths import (
    V5PathError,
    admit_create_only_external_output,
    admit_existing,
    admit_repository_root,
    assert_no_alternate_data_streams,
    fsync_directory,
)
from anachron.v5_registry import canonical_json_bytes, strict_json_loads

V5_CANDIDATE_REVIEW_LENS_IDS = (
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
REVIEW_MANIFEST_SCHEMA = "anachron-v5-candidate-review-set-v1"
LOCAL_RELEASE_RECEIPT_SCHEMA = "anachron-v5-local-release-v1"
UNSENT_OUTREACH_SCHEMA = "anachron-v5-unsent-outreach-v1"
_JSON_MAX_BYTES = 1_048_576
_LOCAL_RELEASE_MAX_BYTES = 5_242_880


def candidate_root_profile(policy: dict[str, int]) -> RootProfile:
    """Return exact direct-member limits for a published candidate root."""

    caps = {
        "arxiv_metadata.json": _JSON_MAX_BYTES,
        "candidate.pdf": policy["pdf_max_bytes"],
        "candidate_receipt.json": _JSON_MAX_BYTES,
        "paper_source_manifest.json": policy["source_manifest_max_bytes"],
        "projection.json": policy["candidate_projection_max_bytes"],
        "qa_render_manifest.json": policy["candidate_projection_max_bytes"],
        "source.zip": policy["source_archive_max_bytes"],
    }
    return RootProfile(tuple(caps), policy["candidate_root_max_bytes"], len(caps), caps)


def local_release_root_profile() -> RootProfile:
    caps = {
        "arxiv_metadata.json": _JSON_MAX_BYTES,
        "candidate.pdf": 2_097_152,
        "local_release_receipt.json": _JSON_MAX_BYTES,
        "source.zip": _JSON_MAX_BYTES,
    }
    return RootProfile(tuple(caps), _LOCAL_RELEASE_MAX_BYTES, len(caps), caps)


def outreach_root_profile() -> RootProfile:
    caps = {"UNSENT.md": _JSON_MAX_BYTES, "outreach_receipt.json": _JSON_MAX_BYTES}
    return RootProfile(tuple(caps), 2_097_152, len(caps), caps)


class CandidateReleaseError(ValueError):
    """Raised when a local v5 candidate closure is incomplete or changed."""


def sha256(path: Path, maximum: int = _JSON_MAX_BYTES) -> str:
    try:
        return capture_regular(path, "candidate artifact", maximum).sha256
    except V5CustodyError as error:
        if "byte cap" in str(error):
            raise CandidateReleaseError("candidate artifact exceeds the contract byte cap") from error
        raise CandidateReleaseError("candidate artifact cannot be read") from error


def bounded_json(path: Path, maximum: int, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = capture_regular(path, label, maximum).raw
    except V5CustodyError as error:
        raise CandidateReleaseError(str(error)) from error
    try:
        value = strict_json_loads(raw, label)
    except ValueError as error:
        raise CandidateReleaseError(f"{label} is not JSON") from error
    if type(value) is not dict or raw != canonical_json_bytes(value):
        raise CandidateReleaseError(f"{label} is not canonical JSON")
    return value, raw


def _external_directory(root: Path, directory: Path, label: str) -> Path:
    try:
        directory = admit_existing(directory, label)
        directory.relative_to(root)
    except ValueError:
        pass
    except V5PathError as error:
        raise CandidateReleaseError(str(error)) from error
    else:
        raise CandidateReleaseError(f"{label} must be external to the repository root")
    if not directory.is_dir():
        raise CandidateReleaseError(f"{label} must be a directory")
    try:
        assert_no_alternate_data_streams(directory)
    except V5PathError as error:
        raise CandidateReleaseError(f"{label} has unsafe paths") from error
    return directory


def _file_child(directory: Path, name: str, label: str) -> Path:
    path = directory / name
    try:
        admitted = admit_existing(path, label)
    except V5PathError as error:
        raise CandidateReleaseError(str(error)) from error
    if not admitted.is_file() or admitted.parent != directory:
        raise CandidateReleaseError(f"{label} topology differs")
    return admitted


def _completion(directory: Path, expected: tuple[str, ...], label: str) -> None:
    normalized_expected = tuple(sorted(expected))
    if len(normalized_expected) != len(set(normalized_expected)):
        raise CandidateReleaseError(f"{label} completion set differs")
    try:
        scandir_exact(directory, normalized_expected, len(normalized_expected), label)
    except V5CustodyError as error:
        raise CandidateReleaseError(f"{label} completion set differs") from error


def _hex(value: object, label: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise CandidateReleaseError(f"{label} differs")
    return value


def _source_archive(path: Path, manifest: dict[str, Any], allowlist: list[str], policy: dict[str, int]) -> str:
    try:
        captured = capture_regular(path, "source archive", policy["source_archive_max_bytes"])
        with zipfile.ZipFile(io.BytesIO(captured.raw)) as archive:
            infos = archive.infolist()
            if type(manifest) is not dict or set(manifest) != {"files", "schema_version", "v4_included_count"} or manifest["schema_version"] != "anachron-v5-paper-source-manifest-v1" or manifest["v4_included_count"] != 0 or type(manifest["files"]) is not list or len(manifest["files"]) != len(allowlist):
                raise CandidateReleaseError("source archive manifest differs")
            rows = manifest["files"]
            if [row.get("path") if type(row) is dict else None for row in rows] != allowlist or any(type(row) is not dict or set(row) != {"path", "sha256"} or type(row["sha256"]) is not str or len(row["sha256"]) != 64 or any(character not in "0123456789abcdef" for character in row["sha256"]) for row in rows):
                raise CandidateReleaseError("source archive manifest differs")
            names = [info.filename for info in infos]
            if names != allowlist or any(info.is_dir() or "\\" in name or name.startswith("/") or ".." in Path(name).parts or info.date_time != (1980, 1, 1, 0, 0, 0) or info.external_attr != 0o100644 << 16 for info, name in zip(infos, names)):
                raise CandidateReleaseError("source archive topology differs")
            digests = {row["path"]: row["sha256"] for row in rows}
            for name in names:
                info = archive.getinfo(name)
                if info.file_size > policy["source_file_max_bytes"] or info.compress_size > policy["source_file_max_bytes"]:
                    raise CandidateReleaseError("source archive member exceeds the contract byte cap")
                digest, total = hashlib.sha256(), 0
                with archive.open(info) as stream:
                    while chunk := stream.read(65536):
                        total += len(chunk)
                        if total > policy["source_file_max_bytes"]:
                            raise CandidateReleaseError("source archive member exceeds the contract byte cap")
                        digest.update(chunk)
                if digest.hexdigest() != digests[name]:
                    raise CandidateReleaseError("source archive member digest differs")
    except (OSError, zipfile.BadZipFile) as error:
        raise CandidateReleaseError("source archive cannot be read") from error
    except V5CustodyError as error:
        raise CandidateReleaseError("source archive exceeds the contract byte cap") from error
    return captured.sha256


def candidate_closure(repository_root: Path, candidate: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate one candidate artifact directory and return its exact bindings."""

    root = admit_repository_root(repository_root)
    candidate = _external_directory(root, candidate, "candidate")
    _completion(candidate, ("arxiv_metadata.json", "candidate.pdf", "candidate_receipt.json", "paper_source_manifest.json", "projection.json", "qa_render_manifest.json", "qa_renders", "source.zip"), "candidate")
    renders = candidate / "qa_renders"
    contract, _ = bounded_json(root / "paper/v5_measurement/candidate_contract.json", _JSON_MAX_BYTES, "candidate contract")
    allowlist = contract.get("source_archive_allowlist")
    policy = contract.get("resource_policy")
    if type(allowlist) is not list or any(type(name) is not str for name in allowlist) or type(policy) is not dict:
        raise CandidateReleaseError("candidate contract archive allowlist differs")
    receipt, receipt_raw = bounded_json(_file_child(candidate, "candidate_receipt.json", "candidate receipt"), _JSON_MAX_BYTES, "candidate receipt")
    candidate_projection, projection_raw = bounded_json(_file_child(candidate, "projection.json", "candidate projection"), policy["candidate_projection_max_bytes"], "candidate projection")
    source_manifest, source_manifest_raw = bounded_json(_file_child(candidate, "paper_source_manifest.json", "paper source manifest"), policy["source_manifest_max_bytes"], "paper source manifest")
    try:
        validate_candidate_projection(candidate_projection)
    except CandidateProjectionError as error:
        raise CandidateReleaseError("candidate projection differs") from error
    metadata, metadata_raw = bounded_json(_file_child(candidate, "arxiv_metadata.json", "arXiv metadata"), _JSON_MAX_BYTES, "arXiv metadata")
    required_metadata = {"abstract", "ai_assistance_disclosure", "author", "categories", "schema_version", "title", "v4_included_count"}
    if set(metadata) != required_metadata or metadata["schema_version"] != "anachron-v5-local-arxiv-metadata-v1" or type(metadata["categories"]) is not list or "cs.AI" not in metadata["categories"] or metadata["v4_included_count"] != 0 or any(type(metadata[key]) is not str or not metadata[key] for key in ("abstract", "ai_assistance_disclosure", "author", "title")):
        raise CandidateReleaseError("arXiv metadata differs")
    try:
        render_paths = bounded_regular_files(
            renders,
            "candidate renders",
            maximum_entries=policy["pdf_max_pages"],
            maximum_depth=0,
        )
    except (KeyError, V5CustodyError) as error:
        raise CandidateReleaseError("candidate render inventory differs") from error
    if not render_paths or any(not path.startswith("page-") or not path.endswith(".png") for path in render_paths):
        raise CandidateReleaseError("candidate render inventory differs")
    archive = _file_child(candidate, "source.zip", "source archive")
    archive_sha256 = _source_archive(archive, source_manifest, allowlist, policy)
    pdf = _file_child(candidate, "candidate.pdf", "candidate PDF")
    try:
        pdf_capture = capture_regular(pdf, "candidate PDF", policy["pdf_max_bytes"])
    except V5CustodyError as error:
        raise CandidateReleaseError("candidate PDF differs") from error
    if pdf_capture.size_bytes == 0:
        raise CandidateReleaseError("candidate PDF differs")
    qa, qa_raw = bounded_json(_file_child(candidate, "qa_render_manifest.json", "candidate QA render manifest"), policy["candidate_projection_max_bytes"], "candidate QA render manifest")
    if set(qa) != {"page_count", "renders", "schema_version"} or qa["schema_version"] != "anachron-v5-pdf-render-manifest-v1" or type(qa["page_count"]) is not int or not 1 <= qa["page_count"] <= policy["pdf_max_pages"] or type(qa["renders"]) is not list or len(qa["renders"]) != qa["page_count"]:
        raise CandidateReleaseError("candidate render manifest differs")
    if tuple(row.get("path") for row in qa["renders"] if type(row) is dict) != tuple(f"page-{index}.png" for index in range(1, qa["page_count"] + 1)):
        raise CandidateReleaseError("candidate render manifest differs")
    aggregate = 0
    for row in qa["renders"]:
        if type(row) is not dict or set(row) != {"height", "path", "sha256", "size_bytes", "width"} or type(row["size_bytes"]) is not int or type(row["width"]) is not int or type(row["height"]) is not int or row["width"] * row["height"] > policy["render_max_pixels"]:
            raise CandidateReleaseError("candidate render manifest differs")
        target = _file_child(renders, row["path"], "candidate PDF render")
        try:
            capture = capture_regular(target, "candidate PDF render", policy["render_max_bytes"])
        except V5CustodyError as error:
            raise CandidateReleaseError("candidate render manifest differs") from error
        aggregate += capture.size_bytes
        if capture.size_bytes != row["size_bytes"] or aggregate > policy["render_aggregate_max_bytes"] or capture.sha256 != row["sha256"]:
            raise CandidateReleaseError("candidate render manifest differs")
    bindings = {
        "archive_sha256": archive_sha256,
        "arxiv_metadata_sha256": hashlib.sha256(metadata_raw).hexdigest(),
        "candidate_contract_sha256": sha256(root / "paper/v5_measurement/candidate_contract.json", policy["candidate_projection_max_bytes"]),
        "candidate_receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
        "evidence_manifest_sha256": candidate_projection["evidence_manifest_sha256"],
        "paper_pdf_sha256": pdf_capture.sha256,
        "paper_source_manifest_sha256": hashlib.sha256(source_manifest_raw).hexdigest(),
        "projection_sha256": hashlib.sha256(projection_raw).hexdigest(),
    }
    required_receipt = {"actual_go_sha256", "archive_sha256", "arxiv_metadata_sha256", "authority_contract_sha256", "candidate_contract_sha256", "carry_forward_sha256", "compatibility_plan_sha256", "evidence_manifest_sha256", "full_plan_sha256", "materialization_receipt_sha256", "paper_pdf_sha256", "paper_source_manifest_sha256", "presentation_source_closure_sha256", "projection_sha256", "qa_render_manifest_sha256", "runtime_identity_sha256", "schema_version", "source_manifest_sha256", "v4_included_count"}
    authority = candidate_projection["authority"]
    expected_receipt = {"actual_go_sha256": authority["conditional_go_sha256"], "archive_sha256": bindings["archive_sha256"], "arxiv_metadata_sha256": bindings["arxiv_metadata_sha256"], "authority_contract_sha256": authority["authority_contract_sha256"], "candidate_contract_sha256": bindings["candidate_contract_sha256"], "carry_forward_sha256": authority["carry_forward_sha256"], "compatibility_plan_sha256": authority["compatibility_plan_sha256"], "evidence_manifest_sha256": candidate_projection["evidence_manifest_sha256"], "full_plan_sha256": authority["full_plan_sha256"], "materialization_receipt_sha256": authority["materialization_receipt_sha256"], "paper_pdf_sha256": bindings["paper_pdf_sha256"], "paper_source_manifest_sha256": bindings["paper_source_manifest_sha256"], "presentation_source_closure_sha256": presentation_source_closure(root), "projection_sha256": bindings["projection_sha256"], "qa_render_manifest_sha256": hashlib.sha256(qa_raw).hexdigest(), "runtime_identity_sha256": authority["runtime_identity_sha256"], "schema_version": "anachron-v5-candidate-receipt-v2", "source_manifest_sha256": authority["source_manifest_sha256"], "v4_included_count": 0}
    if set(receipt) != required_receipt or receipt != expected_receipt:
        raise CandidateReleaseError("candidate receipt differs")
    return {"candidate": candidate, "contract": contract, "metadata": metadata, "projection": candidate_projection, "receipt": receipt}, bindings


def create_staging_directory(output: Path, root: Path, *inputs: Path) -> tuple[Path, Path]:
    try:
        target = admit_create_only_external_output(output, root, "local output")
        parent = admit_existing(target.parent, "local output parent")
        for input_path in inputs:
            admitted_input = admit_existing(input_path, "local input")
            try:
                target.relative_to(admitted_input)
                overlaps = True
            except ValueError:
                try:
                    admitted_input.relative_to(target)
                    overlaps = True
                except ValueError:
                    overlaps = False
            if overlaps:
                raise CandidateReleaseError("local output overlaps an input")
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=parent))
        return target, staging
    except V5PathError as error:
        raise CandidateReleaseError(str(error)) from error
    except OSError as error:
        raise CandidateReleaseError("local staging directory cannot be created") from error


def publish_staging_directory(staging: Path, output: Path) -> None:
    try:
        publish_staging_root(staging, output, "local output")
        fsync_directory(output.parent, "local output parent")
    except (OSError, V5CustodyError, V5PathError) as error:
        raise CandidateReleaseError("local output cannot be published") from error


def remove_staging(staging: Path) -> None:
    if staging.exists():
        try:
            discard_staging_root(
                staging,
                "candidate staging",
                maximum_entries=32,
                maximum_depth=2,
            )
        except V5CustodyError as error:
            raise CandidateReleaseError("local staging cannot be removed") from error


def strict_utc(value: object, label: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise CandidateReleaseError(f"{label} differs")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise CandidateReleaseError(f"{label} differs") from error
    if parsed.tzinfo is None:
        raise CandidateReleaseError(f"{label} differs")
    return parsed


def substantive_text(value: object, label: str) -> str:
    if type(value) is not str or len(value.strip()) < 24 or "replace_with" in value.lower() or value.strip().lower() in {"tbd", "none", "n/a"}:
        raise CandidateReleaseError(f"{label} differs")
    return value


def named_reviewer(value: object) -> str:
    if type(value) is not str or not value.strip() or "replace_with" in value.lower():
        raise CandidateReleaseError("reviewer differs")
    return value


def local_release_closure(repository_root: Path, release: Path) -> tuple[dict[str, Any], dict[str, str]]:
    root = admit_repository_root(repository_root)
    release = _external_directory(root, release, "local release")
    _completion(release, ("arxiv_metadata.json", "candidate.pdf", "local_release_receipt.json", "source.zip"), "local release")
    receipt, raw = bounded_json(_file_child(release, "local_release_receipt.json", "local release receipt"), _JSON_MAX_BYTES, "local release receipt")
    expected = {"approval_sha256", "candidate_receipt_sha256", "local_release_files", "review_set_manifest_sha256", "review_snapshot_sha256", "schema_version", "v4_included_count"}
    if set(receipt) != expected or receipt["schema_version"] != LOCAL_RELEASE_RECEIPT_SCHEMA or receipt["v4_included_count"] != 0 or type(receipt["local_release_files"]) is not dict:
        raise CandidateReleaseError("local release receipt differs")
    _hex(receipt["review_snapshot_sha256"], "review snapshot SHA-256")
    try:
        metadata_capture = capture_regular(_file_child(release, "arxiv_metadata.json", "local metadata"), "local metadata", _JSON_MAX_BYTES)
        pdf_capture = capture_regular(_file_child(release, "candidate.pdf", "local PDF"), "local PDF", 2_097_152)
        archive_capture = capture_regular(_file_child(release, "source.zip", "local archive"), "local archive", _JSON_MAX_BYTES)
    except V5CustodyError as error:
        raise CandidateReleaseError("local release artifact exceeds the contract byte cap") from error
    if metadata_capture.size_bytes + pdf_capture.size_bytes + archive_capture.size_bytes + len(raw) > _LOCAL_RELEASE_MAX_BYTES:
        raise CandidateReleaseError("local release exceeds the aggregate byte cap")
    bindings = {
        "arxiv_metadata_sha256": metadata_capture.sha256,
        "candidate_pdf_sha256": pdf_capture.sha256,
        "candidate_receipt_sha256": _hex(receipt["candidate_receipt_sha256"], "candidate receipt SHA-256"),
        "local_release_receipt_sha256": hashlib.sha256(raw).hexdigest(),
        "source_archive_sha256": archive_capture.sha256,
    }
    if receipt["local_release_files"] != {"arxiv_metadata.json": bindings["arxiv_metadata_sha256"], "candidate.pdf": bindings["candidate_pdf_sha256"], "source.zip": bindings["source_archive_sha256"]}:
        raise CandidateReleaseError("local release bytes differ")
    return {"local_release": release, "metadata": bounded_json(release / "arxiv_metadata.json", _JSON_MAX_BYTES, "local metadata")[0], "receipt": receipt}, bindings
