"""Strict local-only closure checks for v4 candidate, review, and release artifacts."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anachron.data.v4_registry import canonical_json_bytes, strict_json_loads
from anachron.v4_candidate_common import (
    generated_arxiv_metadata,
    validate_candidate_projection,
)
from anachron.v4_contract import (
    V4_LOCAL_RELEASE_ALLOWLIST,
    V4_SOURCE_ARCHIVE_ALLOWLIST,
    validate_authority_contract,
)
from anachron.v4_paths import (
    V4PathError,
    admit_create_only_external_output,
    admit_evidence_directory,
    admit_evidence_regular_file,
    admit_evidence_root,
    admit_repository_regular_file,
    admit_repository_root,
)

CANDIDATE_FILES = (
    "arxiv_metadata.json",
    "candidate.pdf",
    "candidate_receipt.json",
    "paper_source_manifest.json",
    "projection.json",
    "qa_renders",
    "qa_render_manifest.json",
    "source",
    "source.zip",
)
REVIEW_MANIFEST_SCHEMA = "anachron-v4-candidate-review-set-v1"
LOCAL_RELEASE_RECEIPT_SCHEMA = "anachron-v4-local-release-receipt-v1"
UNSENT_OUTREACH_SCHEMA = "anachron-v4-unsent-outreach-v1"


class CandidateReleaseError(ValueError):
    """Raised when a v4 local-only candidate closure is invalid."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hex(value: object, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise CandidateReleaseError(f"{label} differs")
    return value


def _string(value: object, label: str, maximum: int) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > maximum:
        raise CandidateReleaseError(f"{label} differs")
    return value


def sha256(path: Path, maximum: int | None = None) -> str:
    """Hash one admitted regular file without an unbounded pre-read."""

    try:
        metadata = path.stat()
        if maximum is not None and metadata.st_size > maximum:
            raise CandidateReleaseError("artifact exceeds the contract byte cap")
        digest = hashlib.sha256()
        consumed = 0
        with path.open("rb") as stream:
            while chunk := stream.read(65536):
                consumed += len(chunk)
                if maximum is not None and consumed > maximum:
                    raise CandidateReleaseError("artifact exceeds the contract byte cap")
                digest.update(chunk)
    except OSError as error:
        raise CandidateReleaseError("artifact cannot be read") from error
    return digest.hexdigest()


def hash_size(path: Path, maximum: int, label: str) -> tuple[str, int]:
    """Return a streaming digest and exact observed size under one hard cap."""

    try:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(65536):
                size += len(chunk)
                if size > maximum:
                    raise CandidateReleaseError(f"{label} exceeds the contract byte cap")
                digest.update(chunk)
    except OSError as error:
        raise CandidateReleaseError(f"{label} cannot be read") from error
    return digest.hexdigest(), size


def bounded_bytes(path: Path, maximum: int, label: str) -> bytes:
    """Read one regular artifact with an exact byte cap before parsing it."""

    try:
        with path.open("rb") as stream:
            raw = stream.read(maximum + 1)
    except OSError as error:
        raise CandidateReleaseError(f"{label} cannot be read") from error
    if len(raw) > maximum:
        raise CandidateReleaseError(f"{label} exceeds the contract byte cap")
    return raw


def bounded_json(path: Path, maximum: int, label: str) -> tuple[dict[str, Any], bytes]:
    """Read one canonical JSON object after a bounded pre-read."""

    try:
        raw = bounded_bytes(path, maximum, label)
        value = strict_json_loads(raw, label)
    except ValueError as error:
        if isinstance(error, CandidateReleaseError):
            raise
        raise CandidateReleaseError(f"{label} cannot be read") from error
    if type(value) is not dict or raw != canonical_json_bytes(value):
        raise CandidateReleaseError(f"{label} is not canonical JSON")
    return value, raw


def _repository_json(
    root: Path, relative: str, maximum: int, label: str, *, canonical: bool = True
) -> dict[str, Any]:
    try:
        path = admit_repository_regular_file(root / relative, root, label)
    except V4PathError as error:
        raise CandidateReleaseError(str(error)) from error
    if canonical:
        return bounded_json(path, maximum, label)[0]
    try:
        value = strict_json_loads(bounded_bytes(path, maximum, label), label)
    except ValueError as error:
        raise CandidateReleaseError(f"{label} cannot be read") from error
    if type(value) is not dict:
        raise CandidateReleaseError(f"{label} schema differs")
    return value


def _external_directory(root: Path, path: Path, label: str) -> Path:
    try:
        return admit_evidence_root(path, root, create=False)
    except V4PathError as error:
        raise CandidateReleaseError(f"{label} {error}") from error


def _directory_child(directory: Path, relative: str, label: str) -> Path:
    try:
        return admit_evidence_directory(directory, relative, label)
    except V4PathError as error:
        raise CandidateReleaseError(str(error)) from error


def _file_child(directory: Path, relative: str, label: str) -> Path:
    try:
        return admit_evidence_regular_file(directory, relative, label)
    except V4PathError as error:
        raise CandidateReleaseError(str(error)) from error


def _completion(directory: Path, expected: tuple[str, ...], label: str) -> None:
    try:
        entries = tuple(sorted(entry.name for entry in os.scandir(directory)))
    except OSError as error:
        raise CandidateReleaseError(f"{label} cannot be read") from error
    if entries != tuple(sorted(expected)):
        raise CandidateReleaseError(f"{label} completion set differs")


def paths_overlap(first: Path, second: Path) -> bool:
    """Return whether two absolute existing-or-planned paths overlap."""

    try:
        first_text = os.path.normcase(os.path.abspath(first))
        second_text = os.path.normcase(os.path.abspath(second))
        common = os.path.commonpath((first_text, second_text))
    except (TypeError, ValueError):
        return False
    return common in {first_text, second_text}


def reject_output_overlap(output: Path, *inputs: Path) -> None:
    if any(paths_overlap(output, path) for path in inputs):
        raise CandidateReleaseError("output must not overlap an input")


def _publish_no_replace(staging: Path, output: Path) -> None:
    """Atomically publish a sibling file or directory only when output is absent."""

    if os.name == "nt":
        try:
            move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
            move.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint)
            move.restype = ctypes.c_int
        except (AttributeError, OSError) as error:
            raise CandidateReleaseError("atomic no-replace publication is unavailable") from error
        if move(str(staging), str(output), 0):
            return
        error_number = ctypes.get_last_error()
        if error_number in {80, 183}:
            raise FileExistsError(error_number, "output already exists", str(output))
        raise CandidateReleaseError(
            f"atomic no-replace publication failed: winerror {error_number}"
        )
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError) as error:
        raise CandidateReleaseError("atomic no-replace publication is unavailable") from error
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    if renameat2(-100, os.fsencode(staging), -100, os.fsencode(output), 1) == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(error_number, "output already exists", str(output))
    raise CandidateReleaseError(
        f"atomic no-replace publication failed: errno {error_number}"
    )


def write_create_only(
    path: Path,
    root: Path,
    value: dict[str, Any],
    *,
    before_publish: Callable[[], None] | None = None,
) -> None:
    """Publish one canonical JSON file through a sibling no-replace rename."""

    try:
        target = admit_create_only_external_output(path, root, "output")
    except V4PathError as error:
        raise CandidateReleaseError(str(error)) from error
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.stage-", dir=target.parent
    )
    staging = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        if before_publish is not None:
            before_publish()
        _publish_no_replace(staging, target)
    except Exception:
        try:
            staging.unlink()
        except OSError:
            pass
        raise


def create_staging_directory(output: Path, root: Path, *inputs: Path) -> tuple[Path, Path]:
    try:
        target = admit_create_only_external_output(output, root, "output")
    except V4PathError as error:
        raise CandidateReleaseError(str(error)) from error
    reject_output_overlap(target, *inputs)
    return target, Path(tempfile.mkdtemp(prefix=f".{target.name}.stage-", dir=target.parent))


def publish_staging_directory(staging: Path, output: Path) -> None:
    _publish_no_replace(staging, output)


def remove_staging(staging: Path) -> None:
    if staging.exists():
        shutil.rmtree(staging)


def strict_utc(value: object, label: str) -> datetime:
    if type(value) is not str or len(value) != 20 or not value.endswith("Z"):
        raise CandidateReleaseError(f"{label} must be an exact UTC timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise CandidateReleaseError(f"{label} must be an exact UTC timestamp") from error
    return parsed


def substantive_text(value: object, label: str, maximum: int) -> str:
    text = _string(value, label, maximum).strip()
    if len(text) < 12 or any(
        marker in text.casefold()
        for marker in ("replace", "placeholder", "pending", "tbd", "n/a")
    ):
        raise CandidateReleaseError(f"{label} must be substantive")
    return text


def named_reviewer(value: object, maximum: int) -> str:
    reviewer = substantive_text(value, "reviewer", maximum)
    if reviewer.casefold() in {"reviewer", "internal reviewer", "reviewer identity"}:
        raise CandidateReleaseError("reviewer must be named")
    return reviewer


def _metadata(value: dict[str, Any], maximum: int) -> dict[str, Any]:
    expected = {
        "abstract",
        "ai_assistance_disclosure",
        "author",
        "categories",
        "schema_version",
        "title",
        "v3_included_count",
    }
    if set(value) != expected or value["schema_version"] != "anachron-v4-local-arxiv-metadata-v1":
        raise CandidateReleaseError("arXiv metadata schema differs")
    for key in ("abstract", "ai_assistance_disclosure", "author", "title"):
        _string(value[key], f"arXiv metadata {key}", maximum)
    if value["categories"] != ["cs.AI"] or value["v3_included_count"] != 0:
        raise CandidateReleaseError("arXiv metadata topology differs")
    return value


def _source_closure(candidate: Path, policy: dict[str, int]) -> str:
    source = _directory_child(candidate, "source", "candidate source")
    _completion(source, ("README.md", "figures", "main.tex", "references.bib"), "candidate source")
    figures = _directory_child(source, "figures", "candidate figures")
    _completion(figures, ("primary_tclr.tex",), "candidate figures")
    rows = []
    for relative in V4_SOURCE_ARCHIVE_ALLOWLIST:
        path = _file_child(source, relative, "candidate source file")
        rows.append({"path": relative, "sha256": sha256(path, policy["source_file_max_bytes"])})
    manifest_path = _file_child(candidate, "paper_source_manifest.json", "paper source manifest")
    manifest, _ = bounded_json(
        manifest_path, policy["source_manifest_max_bytes"], "paper source manifest"
    )
    if manifest != {"files": rows, "schema_version": "anachron-v4-paper-source-manifest-v1"}:
        raise CandidateReleaseError("paper source manifest differs")
    archive_path = _file_child(candidate, "source.zip", "source archive")
    archive_digest, _ = hash_size(
        archive_path, policy["source_archive_max_bytes"], "source archive"
    )
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if tuple(member.filename for member in members) != V4_SOURCE_ARCHIVE_ALLOWLIST:
                raise CandidateReleaseError("source archive allowlist differs")
            for member in members:
                member_path = Path(member.filename)
                mode = member.external_attr >> 16 & 0o170000
                if (
                    member.is_dir()
                    or mode == 0o120000
                    or member_path.is_absolute()
                    or ".." in member_path.parts
                    or member.file_size > policy["source_file_max_bytes"]
                    or archive.read(member.filename)
                    != bounded_bytes(
                        _file_child(source, member.filename, "candidate source file"),
                        policy["source_file_max_bytes"],
                        "candidate source file",
                    )
                ):
                    raise CandidateReleaseError("source archive differs")
    except (OSError, zipfile.BadZipFile) as error:
        if isinstance(error, CandidateReleaseError):
            raise
        raise CandidateReleaseError("source archive cannot be read") from error
    return archive_digest


def candidate_closure(root: Path, candidate: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """Revalidate every candidate byte that may advance to human review."""

    root = admit_repository_root(root)
    validate_authority_contract(root)
    candidate = _external_directory(root, candidate, "candidate")
    _completion(candidate, CANDIDATE_FILES, "candidate")
    contract = _repository_json(
        root,
        "paper/v4_measurement/candidate_contract.json",
        1048576,
        "candidate contract",
    )
    policy = contract["resource_policy"]
    projection_path = _file_child(candidate, "projection.json", "candidate projection")
    projection, projection_raw = bounded_json(
        projection_path, policy["candidate_projection_max_bytes"], "candidate projection"
    )
    validate_candidate_projection(projection, root)
    metadata_path = _file_child(candidate, "arxiv_metadata.json", "arXiv metadata")
    metadata, metadata_raw = bounded_json(
        metadata_path, policy["candidate_projection_max_bytes"], "arXiv metadata"
    )
    _metadata(metadata, policy["string_max_bytes"])
    template = _repository_json(
        root,
        "paper/v4_measurement/candidate_manuscript_template.json",
        policy["candidate_projection_max_bytes"],
        "candidate manuscript template",
        canonical=False,
    )
    try:
        expected_metadata = generated_arxiv_metadata(template, projection)
    except ValueError as error:
        raise CandidateReleaseError("arXiv metadata differs") from error
    if metadata != expected_metadata:
        raise CandidateReleaseError("arXiv metadata differs")
    pdf = _file_child(candidate, "candidate.pdf", "candidate PDF")
    qa_renders = _directory_child(candidate, "qa_renders", "candidate PDF renders")
    render_manifest_path = _file_child(
        candidate, "qa_render_manifest.json", "candidate render manifest"
    )
    render_manifest, _ = bounded_json(
        render_manifest_path,
        policy["candidate_projection_max_bytes"],
        "candidate render manifest",
    )
    if (
        set(render_manifest) != {"page_count", "renders", "schema_version"}
        or render_manifest["schema_version"] != "anachron-v4-pdf-render-manifest-v1"
        or type(render_manifest["page_count"]) is not int
        or not 1 <= render_manifest["page_count"] <= policy["pdf_max_pages"]
        or type(render_manifest["renders"]) is not list
        or len(render_manifest["renders"]) != render_manifest["page_count"]
    ):
        raise CandidateReleaseError("candidate render manifest differs")
    expected_render_names = tuple(
        f"page-{index}.png" for index in range(1, render_manifest["page_count"] + 1)
    )
    _completion(qa_renders, expected_render_names, "candidate PDF renders")
    for expected_name, row in zip(expected_render_names, render_manifest["renders"]):
        if type(row) is not dict or set(row) != {"path", "sha256", "size_bytes"}:
            raise CandidateReleaseError("candidate render manifest differs")
        if row["path"] != expected_name or type(row["size_bytes"]) is not int:
            raise CandidateReleaseError("candidate render manifest differs")
        digest, size = hash_size(
            _file_child(qa_renders, expected_name, "candidate PDF render"),
            policy["render_max_bytes"],
            "candidate PDF render",
        )
        if row["sha256"] != digest or row["size_bytes"] != size:
            raise CandidateReleaseError("candidate render manifest differs")
    source_archive_digest = _source_closure(candidate, policy)
    receipt_path = _file_child(candidate, "candidate_receipt.json", "candidate receipt")
    receipt, receipt_raw = bounded_json(
        receipt_path, policy["candidate_projection_max_bytes"], "candidate receipt"
    )
    expected_receipt = {
        "actual_go_sha256": projection["authority"]["actual_go_sha256"],
        "arxiv_metadata_sha256": _sha(metadata_raw),
        "candidate_contract_sha256": sha256(
            admit_repository_regular_file(
                root / "paper/v4_measurement/candidate_contract.json", root, "candidate contract"
            )
        ),
        "evidence_manifest_sha256": projection["evidence_closure"]["sha256"],
        "paper_pdf_sha256": sha256(pdf, policy["pdf_max_bytes"]),
        "paper_source_manifest_sha256": sha256(
            _file_child(candidate, "paper_source_manifest.json", "paper source manifest"),
            policy["source_manifest_max_bytes"],
        ),
        "projection_sha256": _sha(projection_raw),
        "qa_render_manifest_sha256": sha256(
            render_manifest_path, policy["candidate_projection_max_bytes"]
        ),
        "schema_version": "anachron-v4-candidate-receipt-v1",
        "source_archive_sha256": source_archive_digest,
        "v3_included_count": 0,
    }
    if receipt != expected_receipt:
        raise CandidateReleaseError("candidate receipt binding differs")
    bindings = {
        "archive_sha256": expected_receipt["source_archive_sha256"],
        "arxiv_metadata_sha256": _sha(metadata_raw),
        "candidate_contract_sha256": expected_receipt["candidate_contract_sha256"],
        "candidate_receipt_sha256": _sha(receipt_raw),
        "evidence_manifest_sha256": expected_receipt["evidence_manifest_sha256"],
        "paper_pdf_sha256": expected_receipt["paper_pdf_sha256"],
        "paper_source_manifest_sha256": expected_receipt["paper_source_manifest_sha256"],
        "projection_sha256": expected_receipt["projection_sha256"],
    }
    return {
        "candidate": candidate,
        "contract": contract,
        "metadata": metadata,
        "projection": projection,
        "receipt": receipt,
    }, bindings


def local_release_closure(root: Path, local_release: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate one exact local release without opening a candidate directory."""

    root = admit_repository_root(root)
    validate_authority_contract(root)
    release = _external_directory(root, local_release, "local release")
    _completion(release, V4_LOCAL_RELEASE_ALLOWLIST, "local release")
    contract = _repository_json(
        root,
        "paper/v4_measurement/candidate_contract.json",
        1048576,
        "candidate contract",
    )
    policy = contract["resource_policy"]
    pdf = _file_child(release, "candidate.pdf", "local release candidate PDF")
    archive = _file_child(release, "source.zip", "local release source archive")
    metadata_path = _file_child(release, "arxiv_metadata.json", "local release metadata")
    metadata, metadata_raw = bounded_json(
        metadata_path, policy["candidate_projection_max_bytes"], "local release metadata"
    )
    _metadata(metadata, policy["string_max_bytes"])
    receipt_path = _file_child(release, "local_release_receipt.json", "local release receipt")
    receipt, receipt_raw = bounded_json(
        receipt_path, policy["candidate_projection_max_bytes"], "local release receipt"
    )
    expected_keys = {
        "approval_sha256",
        "candidate_receipt_sha256",
        "local_release_files",
        "review_set_manifest_sha256",
        "schema_version",
        "v3_included_count",
    }
    files = {
        "arxiv_metadata.json": _sha(metadata_raw),
        "candidate.pdf": sha256(pdf, policy["pdf_max_bytes"]),
        "source.zip": sha256(archive, policy["source_archive_max_bytes"]),
    }
    if (
        set(receipt) != expected_keys
        or receipt["schema_version"] != LOCAL_RELEASE_RECEIPT_SCHEMA
        or receipt["v3_included_count"] != 0
        or receipt["local_release_files"] != files
    ):
        raise CandidateReleaseError("local release receipt differs")
    for key in ("approval_sha256", "candidate_receipt_sha256", "review_set_manifest_sha256"):
        _hex(receipt[key], f"local release receipt {key}")
    return {"local_release": release, "metadata": metadata, "receipt": receipt}, {
        "arxiv_metadata_sha256": files["arxiv_metadata.json"],
        "candidate_pdf_sha256": files["candidate.pdf"],
        "candidate_receipt_sha256": receipt["candidate_receipt_sha256"],
        "local_release_receipt_sha256": _sha(receipt_raw),
        "source_archive_sha256": files["source.zip"],
    }
