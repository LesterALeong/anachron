"""Append-only, loopback-only runner primitives for the Anachron v0 study.

This module deliberately avoids Inspect and answer grading. It asks an Ollama
model for one ``anachron_search`` tool call, executes that retrieval exactly
once, and scores only the returned synthetic records against the sample's
point-in-time boundary. Every request and response is preserved as evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import sys
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from anachron.core.leakage import ToolInteraction, score_interactions
from anachron.data.corpus import format_search_results, search
from anachron.data.v0_samples import V0Sample, v0_samples_by_id

_MODES = ("unrestricted", "enforced")
_TOOL_NAME = "anachron_search"
_PROTOCOL_VERSION = "v0-measurement-protocol-v1"
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_APPROVED_MODELS = (
    ("qwen2.5:7b", "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"),
    ("qwen3:14b-q4_K_M", "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8"),
)
_GOVERNED_CLOSURE = {
    "anachron/core/leakage.py",
    "anachron/data/corpus.py",
    "anachron/data/v0_samples.py",
    "anachron/v0_measurement.py",
}
_FALSIFIER_PRECONDITIONS = {
    "requires_falsifier_evidence": False,
    "requires_falsifier_receipt": False,
    "requires_human_go": False,
}
_FULL_PRECONDITIONS = {
    "requires_falsifier_evidence": True,
    "requires_falsifier_receipt": True,
    "requires_human_go": True,
}
_FULL_GO_KIND = "anachron-v0-full-measurement-authorization"
_FULL_GO_STATEMENT = (
    "I authorize this exact frozen full v0 measurement plan after reviewing the "
    "bound passing falsifier receipt."
)
_SOURCE_ADMISSION_SCHEMA_VERSION = 2
_EVIDENCE_MANIFEST_SCHEMA_VERSION = 1
_PREREQUISITE_PATH_BUDGET = 240
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_identity() -> dict[str, Any]:
    return {
        "implementation": "CPython",
        "major": sys.version_info.major,
        "minor": sys.version_info.minor,
        "version": sys.version.split()[0],
    }


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_git_oid(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(
        character in "0123456789abcdef" for character in value
    )


def _canonical_relative_path(value: str) -> Path:
    path = Path(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"evidence path is not canonical and contained: {value!r}")
    return path


def _is_link_or_reparse(path: Path) -> bool:
    metadata = os.lstat(path)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT
    )


def _require_regular_file(path: Path, label: str) -> None:
    if _is_link_or_reparse(path) or not stat.S_ISREG(os.lstat(path).st_mode):
        raise ValueError(f"{label} must be a regular non-reparse file: {path}")


def _require_real_directory(path: Path, label: str) -> None:
    if _is_link_or_reparse(path) or not stat.S_ISDIR(os.lstat(path).st_mode):
        raise ValueError(f"{label} must be a real non-reparse directory: {path}")


def _safe_regular_files(root: Path, label: str) -> set[str]:
    """Enumerate only normal files, rejecting links and reparse points before descent."""
    _require_real_directory(root, label)
    files: set[str] = set()

    def walk(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda candidate: candidate.name):
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                _canonical_relative_path(relative)
                metadata = os.lstat(path)
                attributes = getattr(metadata, "st_file_attributes", 0)
                if stat.S_ISLNK(metadata.st_mode) or attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError(f"{label} contains a symlink or reparse point: {relative}")
                if stat.S_ISDIR(metadata.st_mode):
                    walk(path)
                elif stat.S_ISREG(metadata.st_mode):
                    files.add(relative)
                else:
                    raise ValueError(f"{label} contains a non-file: {relative}")

    walk(root)
    return files


def _normalized_absolute(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _paths_overlap(first: Path, second: Path) -> bool:
    first_absolute = _normalized_absolute(first)
    second_absolute = _normalized_absolute(second)
    return os.path.commonpath((first_absolute, second_absolute)) in {
        first_absolute,
        second_absolute,
    }


def _require_safe_existing_ancestors(path: Path, label: str) -> None:
    current = path
    while True:
        if os.path.lexists(current):
            _require_real_directory(current, f"{label} ancestor")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _require_absent_output(output: Path, inputs: tuple[Path, ...] = ()) -> None:
    if os.path.lexists(output):
        raise FileExistsError(f"evidence output already exists: {output}")
    if any(_paths_overlap(output, input_path) for input_path in inputs):
        raise ValueError("evidence output must not overlap a full-run input")
    _require_safe_existing_ancestors(output.parent, "evidence output")


def _git_text(repo_root: Path, *args: str, error_type: type[Exception] = RuntimeError) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise error_type(
            f"source admission git {' '.join(args)} failed: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def _git_bytes(repo_root: Path, *args: str, error_type: type[Exception] = RuntimeError) -> bytes:
    completed = subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, check=False
    )
    if completed.returncode != 0:
        raise error_type(
            f"source admission git {' '.join(args)} failed: {completed.stderr.decode(errors='replace').strip()}"
        )
    return completed.stdout


def _commit_blob_oids(repo_root: Path, commit: str, error_type: type[Exception] = RuntimeError) -> dict[str, str]:
    entries = _git_bytes(repo_root, "ls-tree", "-r", "-z", commit, error_type=error_type)
    blobs: dict[str, str] = {}
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        try:
            metadata, raw_path = entry.split(b"\t", maxsplit=1)
            mode, object_type, oid = metadata.decode("ascii").split()
            path = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as error:
            raise error_type("source admission commit tree is malformed") from error
        if object_type != "blob" or mode == "120000" or not _valid_git_oid(oid):
            continue
        blobs[path] = oid
    return blobs


def _governed_blob_receipt(
    plan: dict[str, Any], repo_root: Path, commit: str, error_type: type[Exception] = RuntimeError
) -> dict[str, dict[str, str]]:
    tree = _commit_blob_oids(repo_root, commit, error_type=error_type)
    governed: dict[str, dict[str, str]] = {}
    for relative, expected_sha256 in sorted(plan["source_hashes"].items()):
        _canonical_relative_path(relative)
        oid = tree.get(relative)
        if oid is None:
            raise error_type(f"source admission governed blob is absent from tag commit: {relative}")
        blob = _git_bytes(repo_root, "cat-file", "blob", oid, error_type=error_type)
        working = repo_root / relative
        if working.is_symlink() or not working.is_file():
            raise error_type(f"source admission governed path is not a regular file: {relative}")
        if _sha256_bytes(blob) != expected_sha256 or working.read_bytes() != blob:
            raise error_type(f"source admission governed bytes mismatch: {relative}")
        governed[relative] = {"oid": oid, "sha256": _sha256_bytes(blob)}
    return governed


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_plan(path: Path) -> tuple[dict[str, Any], bytes]:
    """Load and validate a frozen measurement plan without changing it."""
    raw = path.read_bytes()
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"plan is not valid JSON: {path}") from error
    if not isinstance(plan, dict):
        raise TypeError("plan must be a JSON object")
    if raw != _canonical_json(plan):
        raise ValueError("plan must use canonical JSON bytes")
    _validate_plan(plan)
    return plan, raw


def _validate_plan(plan: dict[str, Any]) -> None:
    required = {
        "plan_id", "kind", "models", "sample_ids", "modes", "repetitions", "acceptance",
        "protocol_version", "trajectory_count", "endpoint", "timeout_seconds",
        "generation", "registry_sha256", "corpus_sha256", "no_retry", "release",
        "source_hashes", "python", "preconditions",
    }
    missing = sorted(required - plan.keys())
    if missing:
        raise ValueError(f"plan is missing required fields: {missing}")
    if set(plan) != required:
        raise ValueError("plan schema contains unexpected fields")
    if not isinstance(plan["plan_id"], str) or not plan["plan_id"]:
        raise ValueError("plan_id must be a non-empty string")
    models = plan["models"]
    if not isinstance(models, list) or not models:
        raise ValueError("models must be a non-empty list")
    names: set[str] = set()
    for model in models:
        if not isinstance(model, dict):
            raise TypeError("each model must be an object")
        name = model.get("name")
        digest = model.get("digest")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("model names must be unique non-empty strings")
        if not isinstance(digest, str) or len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ValueError(f"model {name!r} must have a lowercase SHA-256 digest")
        names.add(name)
    sample_ids = plan["sample_ids"]
    known_samples = v0_samples_by_id()
    if not isinstance(sample_ids, list) or not sample_ids:
        raise ValueError("sample_ids must be a non-empty list")
    if len(sample_ids) != len(set(sample_ids)) or any(sample_id not in known_samples for sample_id in sample_ids):
        raise ValueError("sample_ids must be unique canonical v0 sample ids")
    if tuple(plan["modes"]) != _MODES:
        raise ValueError(f"modes must be exactly {list(_MODES)}")
    if not isinstance(plan["repetitions"], int) or plan["repetitions"] < 1:
        raise ValueError("repetitions must be a positive integer")
    if not isinstance(plan["acceptance"], dict):
        raise TypeError("acceptance must be an object")
    if plan["protocol_version"] != _PROTOCOL_VERSION or plan["no_retry"] is not True:
        raise ValueError("plan must freeze the supported protocol version and no-retry semantics")
    if plan["trajectory_count"] != len(plan["models"]) * len(plan["modes"]) * len(sample_ids) * plan["repetitions"]:
        raise ValueError("trajectory_count does not match the frozen design")
    if plan["endpoint"] != "http://127.0.0.1:11434" or type(plan["timeout_seconds"]) is not int or plan["timeout_seconds"] != 120:
        raise ValueError("plan must freeze the loopback endpoint and timeout")
    generation = plan["generation"]
    if generation != {"temperature": 0, "seed": 0, "num_ctx": 8192, "num_predict": 512, "think": False}:
        raise ValueError("plan must freeze generation with think=false")
    for key in ("temperature", "seed", "num_ctx", "num_predict"):
        if key not in generation:
            raise ValueError(f"generation must freeze {key}")
    release = plan["release"]
    if release != {
        "tag": "v0-measurement-protocol-v1",
        "ref": "refs/tags/v0-measurement-protocol-v1",
        "origin": "https://github.com/LesterALeong/anachron.git",
        "branch": "master",
        "remote": "origin",
    }:
        raise ValueError("plan release admission is not the approved frozen release")
    for key in ("registry_sha256", "corpus_sha256"):
        value = plan[key]
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError(f"{key} must be a SHA-256 digest")
    hashes = plan["source_hashes"]
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("source_hashes must be a complete non-empty closure")
    for path, value in hashes.items():
        if not isinstance(path, str) or not isinstance(value, str) or len(value) != 64:
            raise ValueError("source_hashes must map paths to SHA-256 digests")
    if plan["python"] != {
        "implementation": "CPython",
        "major": 3,
        "minor": 12,
        "version": "3.12.10",
    }:
        raise ValueError("plan must freeze the Python runtime identity")
    designs = {
        "anachron-v0-pre-falsifier-2026-09-03": (
            "pre-falsifier",
            ["fin-acme-2021-01-future", "fin-borealis-2020-06-survivorship", "fin-cygnus-2022-06-future-survivorship", "fin-delta-2021-06-restatement", "gen-eclipse-2017-01-future", "gen-industrial-2023-04-restatement"],
            1,
            24,
            {"all_trajectories_valid": True, "enforced_finance_survivorship": True, "minimum_pooled_reduction": 0.2},
            _FALSIFIER_PRECONDITIONS,
        ),
        "anachron-v0-full-measurement-2026-09-03": (
            "full-measurement",
            list(known_samples),
            3,
            324,
            {"all_trajectories_valid": True, "enforced_finance_survivorship": True, "minimum_pooled_reduction": 0.2},
            _FULL_PRECONDITIONS,
        ),
    }
    expected = designs.get(plan["plan_id"])
    if expected is None:
        raise ValueError("plan_id is not an approved frozen design")
    kind, expected_ids, repetitions, trajectories, acceptance, preconditions = expected
    if plan.get("kind") != kind or plan["sample_ids"] != expected_ids or plan["repetitions"] != repetitions or plan["trajectory_count"] != trajectories or plan["acceptance"] != acceptance or plan["preconditions"] != preconditions:
        raise ValueError("plan design differs from the approved frozen design")
    if [(model["name"], model["digest"]) for model in plan["models"]] != list(_APPROVED_MODELS):
        raise ValueError("plan model identities differ from the approved frozen design")
    if set(plan["source_hashes"]) != _GOVERNED_CLOSURE:
        raise ValueError("plan governed closure differs from the approved frozen design")


def _require_loopback(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("base_url must be a bare http loopback URL")
    if parsed.username or parsed.password or parsed.port is None:
        raise ValueError("base_url must include a loopback host and explicit port")
    host = parsed.hostname
    if host is None:
        raise ValueError("base_url must include a loopback host")
    try:
        resolved = {entry[4][0] for entry in socket.getaddrinfo(host, parsed.port)}
    except socket.gaierror as error:
        raise ValueError(f"base_url host cannot be resolved: {host}") from error
    if not resolved or any(address not in {"127.0.0.1", "::1"} for address in resolved):
        raise ValueError("base_url must resolve only to loopback addresses")
    return base_url.rstrip("/")


def admit_committed_source(plan: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    """Fail closed unless this exact candidate runs from its pushed annotated tag."""
    release = plan["release"]
    if sys.implementation.name != "cpython" or _runtime_identity() != plan["python"]:
        raise RuntimeError("source admission Python runtime identity mismatch")
    if _git_text(repo_root, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("source admission requires a clean tracked and untracked checkout")
    if _git_text(repo_root, "rev-parse", "--abbrev-ref", "HEAD") != "HEAD":
        raise RuntimeError("source admission requires a detached checkout")
    tag = release["tag"]
    if _git_text(repo_root, "describe", "--exact-match", "--tags", "HEAD") != tag:
        raise RuntimeError("source admission requires the exact annotated release tag")
    if _git_text(repo_root, "cat-file", "-t", f"refs/tags/{tag}") != "tag":
        raise RuntimeError("source admission requires an annotated, not lightweight, tag")
    if _git_text(repo_root, "config", "--get", f"remote.{release['remote']}.url") != release["origin"]:
        raise RuntimeError("source admission origin URL mismatch")
    head = _git_text(repo_root, "rev-parse", "HEAD")
    if _git_text(repo_root, "rev-parse", f"refs/heads/{release['branch']}") != head:
        raise RuntimeError("source admission local master must equal HEAD")
    local_tag = _git_text(repo_root, "rev-parse", f"refs/tags/{tag}^{{}}")
    if local_tag != head:
        raise RuntimeError("source admission peeled annotated tag must equal HEAD")
    local_tag_object = _git_text(repo_root, "rev-parse", f"refs/tags/{tag}")
    tag_object_lines = _git_text(repo_root, "cat-file", "-p", local_tag_object).splitlines()
    if tag_object_lines[:2] != [f"object {head}", "type commit"]:
        raise RuntimeError("source admission annotated tag object must directly name HEAD as a commit")

    def remote_ref(ref: str) -> str:
        args = (
            ("ls-remote", "--tags", release["remote"], ref)
            if ref.startswith("refs/tags/")
            else ("ls-remote", release["remote"], ref)
        )
        lines = _git_text(repo_root, *args).splitlines()
        if len(lines) != 1:
            raise RuntimeError(f"source admission remote ref is missing or ambiguous: {ref}")
        fields = lines[0].split("\t")
        if len(fields) != 2 or fields[1] != ref or len(fields[0]) != 40:
            raise RuntimeError(f"source admission remote ref is malformed: {ref}")
        return fields[0]

    remote_tag = remote_ref(f"{release['ref']}^{{}}")
    if remote_tag != local_tag:
        raise RuntimeError("source admission remote annotated tag parity mismatch")
    remote_tag_object = remote_ref(release["ref"])
    if remote_tag_object != local_tag_object:
        raise RuntimeError("source admission remote tag-object parity mismatch")
    remote_head = remote_ref("refs/heads/master")
    if remote_head != head:
        raise RuntimeError("source admission remote master parity mismatch")
    governed_blobs = _governed_blob_receipt(plan, repo_root, local_tag)
    if _sha256_file(repo_root / "anachron" / "data" / "v0_samples.py") != plan["registry_sha256"]:
        raise RuntimeError("source admission registry hash mismatch")
    if _sha256_file(repo_root / "anachron" / "data" / "corpus.py") != plan["corpus_sha256"]:
        raise RuntimeError("source admission corpus hash mismatch")
    return {
        "schema_version": _SOURCE_ADMISSION_SCHEMA_VERSION,
        "release": release,
        "plan_sha256": _sha256_bytes(_canonical_json(plan)),
        "tag_commit": local_tag,
        "tag": {
            "ref": release["ref"],
            "local_object": local_tag_object,
            "remote_object": remote_tag_object,
            "local_peeled_commit": local_tag,
            "remote_peeled_commit": remote_tag,
        },
        "governed_blobs": governed_blobs,
        "python": plan["python"],
    }


def verify_source_admission(plan: dict[str, Any], admission: dict[str, Any], repo_root: Path) -> None:
    """Verify a recorded source receipt from local Git objects and governed bytes only."""
    if (
        not isinstance(admission, dict)
        or admission.get("schema_version") != _SOURCE_ADMISSION_SCHEMA_VERSION
        or admission.get("release") != plan["release"]
        or admission.get("plan_sha256") != _sha256_bytes(_canonical_json(plan))
        or admission.get("python") != plan["python"]
    ):
        raise ValueError("source admission release mismatch")
    tag = plan["release"]["tag"]
    try:
        local_tag_object = _git_text(repo_root, "rev-parse", f"refs/tags/{tag}", error_type=ValueError)
        if _git_text(repo_root, "cat-file", "-t", local_tag_object, error_type=ValueError) != "tag":
            raise ValueError("source admission tag is not annotated")
        commit = _git_text(repo_root, "rev-parse", f"refs/tags/{tag}^{{}}", error_type=ValueError)
        tag_lines = _git_text(repo_root, "cat-file", "-p", local_tag_object, error_type=ValueError).splitlines()
    except ValueError as error:
        raise ValueError("source admission tag is unavailable locally") from error
    tag_receipt = admission.get("tag")
    if (
        not isinstance(tag_receipt, dict)
        or set(tag_receipt) != {
            "ref", "local_object", "remote_object", "local_peeled_commit", "remote_peeled_commit"
        }
        or tag_receipt.get("ref") != plan["release"]["ref"]
        or any(not _valid_git_oid(tag_receipt.get(field)) for field in tag_receipt if field != "ref")
        or tag_receipt["local_object"] != local_tag_object
        or tag_receipt["local_peeled_commit"] != commit
        or tag_receipt["remote_object"] != local_tag_object
        or tag_receipt["remote_peeled_commit"] != commit
        or tag_lines[:2] != [f"object {commit}", "type commit"]
        or admission.get("tag_commit") != commit
    ):
        raise ValueError("source admission tag commit mismatch")
    try:
        actual = _governed_blob_receipt(plan, repo_root, commit, error_type=ValueError)
    except ValueError as error:
        raise ValueError("source admission governed bytes mismatch") from error
    if admission.get("governed_blobs") != actual:
        raise ValueError("source admission governed bytes mismatch")


def _http_json(base_url: str, path: str, payload: dict[str, Any] | None, timeout: float) -> tuple[bytes, dict[str, Any]]:
    body = None if payload is None else _canonical_json(payload)
    request = Request(
        f"{base_url}{path}",
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f"Ollama request failed for {path}: {error}") from error
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Ollama returned non-JSON for {path}") from error
    if not isinstance(decoded, dict):
        raise TypeError(f"Ollama returned a non-object JSON response for {path}")
    return raw, decoded


class _EvidenceWriter:
    """Create evidence files once and append journal records without truncation."""

    def __init__(self, root: Path):
        _require_absent_output(root)
        root.mkdir(parents=True)
        _require_real_directory(root, "evidence output")
        self.root = root
        self.write_bytes("journal.jsonl", b"")

    def _path(self, relative: str) -> Path:
        path = _canonical_relative_path(relative)
        return self.root / path

    def write_bytes(self, relative: str, content: bytes) -> Path:
        path = self._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        return path

    def write_json(self, relative: str, content: object) -> Path:
        return self.write_bytes(relative, _canonical_json(content))

    def append_journal(self, record: dict[str, Any]) -> None:
        journal = self._path("journal.jsonl")
        with journal.open("ab") as destination:
            destination.write(
                json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8")
                + b"\n"
            )
            destination.flush()
            os.fsync(destination.fileno())


def _verify_server_identity(
    base_url: str,
    plan: dict[str, Any],
    timeout: float,
    transport,
) -> tuple[bytes, dict[str, Any], bytes, dict[str, Any]]:
    version_raw, version = transport(base_url, "/api/version", None, timeout)
    tags_raw, tags = transport(base_url, "/api/tags", None, timeout)
    tagged_models = tags.get("models")
    if not isinstance(tagged_models, list):
        raise TypeError("Ollama /api/tags response has no models list")
    by_name = {
        model.get("name"): model.get("digest")
        for model in tagged_models
        if isinstance(model, dict) and isinstance(model.get("name"), str)
    }
    for expected in plan["models"]:
        observed = by_name.get(expected["name"])
        if observed != expected["digest"]:
            raise RuntimeError(
                f"model digest mismatch for {expected['name']!r}: "
                f"expected {expected['digest']}, observed {observed!r}"
            )
    return version_raw, version, tags_raw, tags


def _tool_schema() -> list[dict[str, Any]]:
    return [{
        "type": "function",
        "function": {
            "name": _TOOL_NAME,
            "description": "Search the fixed date-stamped synthetic corpus.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }]


def _chat_request(model: str, sample: V0Sample, generation: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": model,
        "stream": False,
        "think": generation["think"],
        "options": {
            "temperature": generation["temperature"],
            "seed": generation["seed"],
            "num_ctx": generation["num_ctx"],
            "num_predict": generation["num_predict"],
        },
        "messages": [{"role": "user", "content": sample.prompt()}],
        "tools": _tool_schema(),
    }


def _extract_query(response: dict[str, Any], model: str) -> tuple[str | None, str | None]:
    if response.get("model") != model or response.get("done") is not True:
        return None, "response must echo the exact model and set done=true"
    if response.get("done_reason") not in {"stop", "tool_calls"}:
        return None, "response has an unsupported done_reason"
    message = response.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return None, "response has no message object"
    if message.get("content") not in ("", None) or message.get("thinking") not in ("", None):
        return None, "tool-call response must not contain content or thinking"
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        return None, "response must contain exactly one tool call"
    call = calls[0]
    if not isinstance(call, dict):
        return None, "tool call must be an object"
    function = call.get("function")
    if not isinstance(function, dict) or function.get("name") != _TOOL_NAME:
        return None, f"tool call must name {_TOOL_NAME}"
    arguments = function.get("arguments")
    if not isinstance(arguments, dict) or set(arguments) != {"query"}:
        return None, "tool call arguments must contain only query"
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return None, "tool query must be a non-empty string"
    return query, None


def _query_dates(query: str) -> list:
    dates = []
    for match in _ISO_DATE_RE.finditer(query):
        try:
            dates.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            continue
    return dates


def _final_request(
    model: str,
    sample: V0Sample,
    generation: dict[str, Any],
    first_message: dict[str, Any],
    tool_result: str,
) -> dict[str, Any]:
    request = _chat_request(model, sample, generation)
    request["messages"].extend([
        first_message,
        {"role": "tool", "tool_name": _TOOL_NAME, "content": tool_result},
    ])
    return request


def _validate_final_response(response: dict[str, Any], model: str) -> str | None:
    if response.get("model") != model or response.get("done") is not True:
        return "final response must echo the exact model and set done=true"
    if response.get("done_reason") != "stop":
        return "final response must have done_reason=stop"
    message = response.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return "final response must contain an assistant message"
    if message.get("tool_calls") not in (None, []):
        return "final response must not contain a second tool call"
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return "final response must contain a non-empty answer"
    if message.get("thinking") not in (None, ""):
        return "final response must not contain thinking"
    return None


def _trajectory_id(model_index: int, mode: str, sample_id: str, repetition: int) -> str:
    return f"m{model_index:02d}-{mode}-{sample_id}-r{repetition:02d}"


def expected_trajectories(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand a frozen plan into its deterministic trajectory order."""
    samples = v0_samples_by_id()
    trajectories: list[dict[str, Any]] = []
    for model_index, model in enumerate(plan["models"], start=1):
        for mode in plan["modes"]:
            for sample_id in plan["sample_ids"]:
                for repetition in range(1, plan["repetitions"] + 1):
                    trajectories.append({
                        "id": _trajectory_id(model_index, mode, sample_id, repetition),
                        "model": model["name"],
                        "model_digest": model["digest"],
                        "mode": mode,
                        "sample": samples[sample_id],
                        "repetition": repetition,
                        "generation": plan["generation"],
                    })
    return trajectories


def expected_raw_inventory(plan: dict[str, Any]) -> set[str]:
    """Return the complete frozen raw-artifact closure for one sealed run."""
    inventory = {
        "server.version.response.json",
        "server.tags.response.json",
    }
    for trajectory in expected_trajectories(plan):
        trajectory_id = trajectory["id"]
        inventory.update({
            f"{trajectory_id}.first.request.json",
            f"{trajectory_id}.first.response.json",
            f"{trajectory_id}.tool_result.txt",
            f"{trajectory_id}.final.request.json",
            f"{trajectory_id}.final.response.json",
        })
    return inventory


def validate_exact_raw_inventory(root: Path, plan: dict[str, Any]) -> None:
    """Reject raw artifact additions, omissions, links, and non-files before parsing."""
    actual = _safe_regular_files(root / "raw", "raw evidence root")
    expected = expected_raw_inventory(plan)
    if actual != expected:
        raise ValueError("raw evidence inventory does not match the frozen plan")


def _run_trajectory(
    writer: _EvidenceWriter,
    base_url: str,
    trajectory: dict[str, Any],
    timeout: float,
    transport,
) -> dict[str, Any]:
    sample = trajectory["sample"]
    trajectory_id = trajectory["id"]
    claim = {
        "kind": "trajectory_claim",
        "trajectory_id": trajectory_id,
        "timestamp_utc": _utc_now(),
        "model": trajectory["model"],
        "model_digest": trajectory["model_digest"],
        "mode": trajectory["mode"],
        "sample_id": sample.id,
        "repetition": trajectory["repetition"],
    }
    writer.append_journal(claim)
    record: dict[str, Any] = {
        "trajectory_id": trajectory_id,
        "model": trajectory["model"],
        "model_digest": trajectory["model_digest"],
        "mode": trajectory["mode"],
        "sample_id": sample.id,
        "as_of": sample.as_of.isoformat(),
        "repetition": trajectory["repetition"],
        "valid": False,
    }
    try:
        request_payload = _chat_request(trajectory["model"], sample, trajectory["generation"])
        request_path = writer.write_bytes(f"raw/{trajectory_id}.first.request.json", _canonical_json(request_payload))
        record["request_path"] = str(request_path.relative_to(writer.root))
        record["request_sha256"] = _sha256_file(request_path)
        response_raw, response = transport(base_url, "/api/chat", request_payload, timeout)
        response_path = writer.write_bytes(f"raw/{trajectory_id}.first.response.json", response_raw)
        record["first_response_path"] = str(response_path.relative_to(writer.root))
        record["first_response_sha256"] = _sha256_file(response_path)
        query, error = _extract_query(response, trajectory["model"])
        if error is not None:
            record["invalid_reason"] = error
        else:
            enforce_as_of = sample.as_of if trajectory["mode"] == "enforced" else None
            returned_items = search(query, enforce_as_of=enforce_as_of)
            tool_text = format_search_results(returned_items)
            tool_path = writer.write_bytes(f"raw/{trajectory_id}.tool_result.txt", tool_text.encode("utf-8"))
            final_payload = _final_request(
                trajectory["model"], sample, trajectory["generation"], response["message"], tool_text
            )
            final_request_path = writer.write_bytes(
                f"raw/{trajectory_id}.final.request.json", _canonical_json(final_payload)
            )
            final_raw, final_response = transport(base_url, "/api/chat", final_payload, timeout)
            final_response_path = writer.write_bytes(
                f"raw/{trajectory_id}.final.response.json", final_raw
            )
            final_error = _validate_final_response(final_response, trajectory["model"])
            if final_error is not None:
                record["invalid_reason"] = final_error
                raise RuntimeError(final_error)
            result = score_interactions(
                [
                    ToolInteraction(
                        tool=_TOOL_NAME,
                        query=query,
                        query_dates=_query_dates(query),
                        returned_items=returned_items,
                    )
                ],
                sample.as_of,
            )
            record.update({
                "valid": True,
                "query": query,
                "tool_result_path": str(tool_path.relative_to(writer.root)),
                "tool_result_sha256": _sha256_file(tool_path),
                "final_request_path": str(final_request_path.relative_to(writer.root)),
                "final_request_sha256": _sha256_file(final_request_path),
                "final_response_path": str(final_response_path.relative_to(writer.root)),
                "final_response_sha256": _sha256_file(final_response_path),
                "final_answer": final_response["message"]["content"],
                "returned_item_ids": [item.id for item in returned_items],
                "score": asdict(result),
            })
    except Exception as error:  # noqa: BLE001 - a post-claim path must always append one terminal record.
        record["invalid_reason"] = record.get("invalid_reason") or f"{type(error).__name__}: {error}"
    terminal = {
        "kind": "trajectory_terminal",
        "trajectory_id": trajectory_id,
        "timestamp_utc": _utc_now(),
        "valid": record["valid"],
        "reason": record.get("invalid_reason"),
    }
    writer.append_journal(terminal)
    return record


def _journal_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"journal line {line_number} is not valid JSON") from error
        if not isinstance(record, dict):
            raise TypeError(f"journal line {line_number} is not an object")
        records.append(record)
    return records


def _validate_journal(records: list[dict[str, Any]], trajectory_ids: set[str]) -> None:
    claims: set[str] = set()
    terminals: set[str] = set()
    for record in records:
        kind = record.get("kind")
        trajectory_id = record.get("trajectory_id")
        if trajectory_id not in trajectory_ids:
            raise ValueError(f"journal contains unknown trajectory {trajectory_id!r}")
        if kind == "trajectory_claim":
            if trajectory_id in claims:
                raise ValueError(f"journal contains duplicate claim for {trajectory_id}")
            if trajectory_id in terminals:
                raise ValueError(f"journal claims trajectory after terminal: {trajectory_id}")
            claims.add(trajectory_id)
        elif kind == "trajectory_terminal":
            if trajectory_id not in claims or trajectory_id in terminals:
                raise ValueError(f"journal terminal is not paired to one claim: {trajectory_id}")
            terminals.add(trajectory_id)
        else:
            raise ValueError(f"journal contains unknown record kind {kind!r}")
    if claims != trajectory_ids or terminals != trajectory_ids:
        raise ValueError("journal must contain one claim and one terminal for every trajectory")


def build_analysis(plan: dict[str, Any], runtime: dict[str, Any], journal: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministically aggregate a completed runtime without new model calls."""
    expected = expected_trajectories(plan)
    trajectory_ids = {entry["id"] for entry in expected}
    _validate_journal(journal, trajectory_ids)
    records = runtime.get("trajectories")
    if not isinstance(records, list) or len(records) != len(expected):
        raise ValueError("runtime must contain exactly the planned trajectories")
    by_id = {record.get("trajectory_id"): record for record in records if isinstance(record, dict)}
    if set(by_id) != trajectory_ids or len(by_id) != len(records):
        raise ValueError("runtime trajectory identities must exactly match the plan")
    cells: dict[str, dict[str, Any]] = {}
    valid_records: list[dict[str, Any]] = []
    for trajectory in expected:
        record = by_id[trajectory["id"]]
        if any(record.get(key) != trajectory[key] for key in ("model", "model_digest", "mode", "repetition")):
            raise ValueError(f"runtime identity mismatch for {trajectory['id']}")
        if record.get("sample_id") != trajectory["sample"].id:
            raise ValueError(f"runtime sample mismatch for {trajectory['id']}")
        cell_key = f"{trajectory['model']}::{trajectory['mode']}"
        cell = cells.setdefault(cell_key, {"model": trajectory["model"], "mode": trajectory["mode"], "valid": 0, "tclr": []})
        if record.get("valid") is True:
            score = record.get("score")
            if not isinstance(score, dict) or not isinstance(score.get("tclr"), (int, float)):
                raise ValueError(f"valid runtime record has no numeric TCLR: {trajectory['id']}")
            cell["valid"] += 1
            cell["tclr"].append(float(score["tclr"]))
            valid_records.append(record)
    for cell in cells.values():
        values = cell.pop("tclr")
        cell["mean_tclr"] = sum(values) / len(values) if values else None
    if len(valid_records) != len(expected):
        raise ValueError("sealed analysis requires every planned trajectory to be valid")
    unrestricted = [record["score"]["tclr"] for record in valid_records if record["mode"] == "unrestricted"]
    enforced = [record["score"]["tclr"] for record in valid_records if record["mode"] == "enforced"]
    unrestricted_mean = sum(unrestricted) / len(unrestricted) if unrestricted else None
    enforced_mean = sum(enforced) / len(enforced) if enforced else None
    model_reductions: dict[str, float | None] = {}
    for model in plan["models"]:
        model_name = model["name"]
        unrestricted_cell = cells[f"{model_name}::unrestricted"]["mean_tclr"]
        enforced_cell = cells[f"{model_name}::enforced"]["mean_tclr"]
        model_reductions[model_name] = (
            None if unrestricted_cell is None or enforced_cell is None else unrestricted_cell - enforced_cell
        )
    acceptance = plan["acceptance"]
    minimum_reduction = acceptance.get("minimum_pooled_reduction", 0.0)
    residual_survivorship = any(
        record["mode"] == "enforced"
        and record["sample_id"].startswith("fin-")
        and record["score"].get("survivorship_leaks", 0) >= 1
        for record in valid_records
    )
    pooled_reduction = (
        None if unrestricted_mean is None or enforced_mean is None else unrestricted_mean - enforced_mean
    )
    gates = {
        "all_trajectories_valid": len(valid_records) == len(expected),
        "pooled_reduction": pooled_reduction is not None and pooled_reduction >= minimum_reduction,
        "no_model_negative": all(value is not None and value >= 0.0 for value in model_reductions.values()),
        "enforced_finance_survivorship": residual_survivorship is acceptance["enforced_finance_survivorship"],
    }
    return {
        "plan_id": plan["plan_id"],
        "trajectory_count": len(expected),
        "valid_trajectory_count": len(valid_records),
        "cells": cells,
        "pooled_unrestricted_mean_tclr": unrestricted_mean,
        "pooled_enforced_mean_tclr": enforced_mean,
        "pooled_reduction": pooled_reduction,
        "model_reductions": model_reductions,
        "gates": gates,
        "go": all(gates.values()),
    }


def _write_manifest(writer: _EvidenceWriter, plan: dict[str, Any]) -> Path:
    validate_exact_raw_inventory(writer.root, plan)
    files = [
        {"path": relative, "sha256": _sha256_file(writer.root / relative)}
        for relative in sorted(_safe_regular_files(writer.root, "evidence root"))
        if relative != "manifest.json"
    ]
    runtime_sha256 = _sha256_file(writer.root / "runtime.json")
    manifest = writer.write_json(
        "manifest.json",
        {
            "schema_version": _EVIDENCE_MANIFEST_SCHEMA_VERSION,
            "runtime_sha256": runtime_sha256,
            "files": files,
        },
    )
    writer.write_bytes(
        "manifest.sha256",
        f"{_sha256_file(manifest)}  manifest.json\n".encode("ascii"),
    )
    return manifest


def _evidence_readme(plan: dict[str, Any]) -> bytes:
    return (
        f"# {plan['plan_id']} evidence\n\n"
        "This directory was created once by `tools/run_v0_measurement.py`. "
        "Do not edit it in place. Verify it with "
        "`python tools/analyze_v0_measurement.py <directory>`.\n"
    ).encode()


def _evidence_regular_files(root: Path) -> set[str]:
    return _safe_regular_files(root, "evidence")


def _verified_manifest_files(root: Path) -> tuple[dict[str, Any], dict[str, str]]:
    manifest_path = root / "manifest.json"
    manifest_hash_path = root / "manifest.sha256"
    try:
        _require_regular_file(manifest_path, "evidence manifest")
        _require_regular_file(manifest_hash_path, "evidence manifest hash")
    except (FileNotFoundError, ValueError) as error:
        raise ValueError("evidence manifest is missing or not a regular file") from error
    manifest_hash = manifest_hash_path.read_text(encoding="ascii")
    expected_manifest_hash = f"{_sha256_file(manifest_path)}  manifest.json\n"
    if manifest_hash != expected_manifest_hash:
        raise ValueError("manifest hash mismatch")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("evidence manifest is not JSON") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != _EVIDENCE_MANIFEST_SCHEMA_VERSION
    ):
        raise ValueError("unsupported evidence manifest")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise TypeError("manifest files must be a list")
    expected: dict[str, str] = {}
    for entry in files:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "sha256"}
            or not isinstance(entry.get("path"), str)
            or not _valid_sha256(entry.get("sha256"))
        ):
            raise TypeError("invalid manifest file entry")
        relative = entry["path"]
        _canonical_relative_path(relative)
        if relative in expected:
            raise ValueError("manifest contains a duplicate file path")
        expected[relative] = entry["sha256"]
    actual_paths = _evidence_regular_files(root) - {"manifest.json", "manifest.sha256"}
    if set(expected) != actual_paths:
        raise ValueError("manifest inventory does not match evidence directory")
    for relative, expected_sha256 in expected.items():
        path = root / relative
        if _sha256_file(path) != expected_sha256:
            raise ValueError(f"manifest hash mismatch: {relative}")
    return manifest, expected


def verify_evidence(root: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Verify manifest hashes and parse plan, runtime, and append-only journal."""
    manifest, _ = _verified_manifest_files(root)
    if manifest.get("runtime_sha256") != _sha256_file(root / "runtime.json"):
        raise ValueError("runtime hash mismatch")
    plan, _ = load_plan(root / "plan.json")
    validate_exact_raw_inventory(root, plan)
    runtime = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    if not isinstance(runtime, dict):
        raise TypeError("runtime must be an object")
    return plan, runtime, _journal_records(root / "journal.jsonl")


def analyze_evidence(root: Path, repository_root: Path | None = None) -> dict[str, Any]:
    """Verify a sealed evidence directory and return its deterministic analysis."""
    plan, runtime, journal = verify_evidence(root)
    source_admission = json.loads((root / "source_admission.json").read_text(encoding="utf-8"))
    verify_source_admission(
        plan,
        source_admission,
        repository_root or Path(__file__).resolve().parent.parent,
    )
    if plan["kind"] == "full-measurement":
        _verify_embedded_falsifier_prerequisites(root, plan, runtime, repository_root)
    _reconstruct_evidence(root, plan, runtime, journal)
    analysis = build_analysis(plan, runtime, journal)
    stored = json.loads((root / "analysis.json").read_text(encoding="utf-8"))
    if stored != analysis:
        raise ValueError("stored analysis does not match deterministic recomputation")
    return analysis


def _verify_embedded_falsifier_prerequisites(
    root: Path,
    full_plan: dict[str, Any],
    runtime: dict[str, Any],
    repository_root: Path | None,
) -> None:
    falsifier_root = root / "prerequisites" / "falsifier"
    receipt_path = root / "prerequisites" / "falsifier_pass_receipt.json"
    go_path = root / "prerequisites" / "full_go_authorization.json"
    if (
        falsifier_root.is_symlink()
        or not falsifier_root.is_dir()
        or receipt_path.is_symlink()
        or go_path.is_symlink()
        or not receipt_path.is_file()
        or not go_path.is_file()
    ):
        raise ValueError("full evidence prerequisites are missing or unsafe")
    _, inner_files = _verified_manifest_files(falsifier_root)
    expected = {
        "prerequisites/falsifier_pass_receipt.json",
        "prerequisites/full_go_authorization.json",
        *(f"prerequisites/falsifier/{relative}" for relative in inner_files),
        "prerequisites/falsifier/manifest.json",
        "prerequisites/falsifier/manifest.sha256",
    }
    actual = {
        relative
        for relative in _evidence_regular_files(root)
        if relative.startswith("prerequisites/")
    }
    if actual != expected:
        raise ValueError("full evidence prerequisite inventory is incomplete or contains extra files")
    receipt = receipt_path.read_bytes()
    expected_receipt = build_falsifier_receipt(
        falsifier_root,
        Path(__file__).resolve().parent.parent / "research" / "v0_measurement" / "falsifier_plan.json",
        repository_root,
    )
    if receipt != expected_receipt:
        raise ValueError("embedded falsifier receipt does not match the sealed falsifier tree")
    validate_full_go_authorization(
        full_plan, (root / "plan.json").read_bytes(), receipt, go_path
    )
    expected_runtime = {
        "falsifier_subtree_path": "prerequisites/falsifier",
        "falsifier_manifest_sha256": _sha256_file(falsifier_root / "manifest.json"),
        "falsifier_receipt_sha256": _sha256_bytes(receipt),
        "full_go_authorization_sha256": _sha256_file(go_path),
    }
    if runtime.get("full_prerequisites") != expected_runtime:
        raise ValueError("full runtime prerequisite reconstruction mismatch")


def build_falsifier_receipt(
    evidence: Path, plan_path: Path, repository_root: Path | None = None
) -> bytes:
    """Build the only canonical receipt for a fully passing falsifier run."""
    plan, plan_bytes = load_plan(plan_path)
    if plan["kind"] != "pre-falsifier":
        raise ValueError("receipt requires the approved falsifier plan")
    evidence_plan_bytes = (evidence / "plan.json").read_bytes()
    if evidence_plan_bytes != plan_bytes:
        raise ValueError("receipt evidence plan does not exactly match the selected falsifier plan")
    analysis = analyze_evidence(evidence, repository_root)
    if (
        analysis.get("plan_id") != plan["plan_id"]
        or analysis.get("go") is not True
        or analysis.get("valid_trajectory_count") != plan["trajectory_count"]
    ):
        raise ValueError("receipt requires a fully valid passing falsifier")
    return _canonical_json({
        "schema_version": 1,
        "kind": "anachron-v0-falsifier-pass-receipt",
        "falsifier_plan_id": plan["plan_id"],
        "falsifier_plan_sha256": _sha256_bytes(plan_bytes),
        "evidence_manifest_sha256": _sha256_file(evidence / "manifest.json"),
        "analysis_sha256": _sha256_file(evidence / "analysis.json"),
        "runtime_sha256": _sha256_file(evidence / "runtime.json"),
        "source_admission_sha256": _sha256_file(evidence / "source_admission.json"),
    })


def seal_falsifier_receipt(
    evidence: Path, plan_path: Path, output: Path, repository_root: Path | None = None
) -> Path:
    """Create a receipt once; existing receipt paths are never overwritten."""
    receipt = build_falsifier_receipt(evidence, plan_path, repository_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as destination:
        destination.write(receipt)
        destination.flush()
        os.fsync(destination.fileno())
    return output


def validate_full_go_authorization(
    full_plan: dict[str, Any], full_plan_bytes: bytes, receipt_bytes: bytes, full_go: Path
) -> dict[str, Any]:
    """Validate a separate human-authored GO artifact; pending is never authorization."""
    try:
        raw = full_go.read_bytes()
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("full GO artifact is missing, malformed, or pending") from error
    required = {
        "schema_version", "kind", "decision", "authorized_by", "authorized_at_utc",
        "authorization_statement", "full_plan_id", "full_plan_sha256", "falsifier_receipt_sha256",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != required
        or payload.get("schema_version") != 1
        or payload.get("kind") != _FULL_GO_KIND
        or payload.get("decision") != "GO"
        or payload.get("authorized_by") != "Lester Leong"
        or payload.get("authorization_statement") != _FULL_GO_STATEMENT
        or payload.get("full_plan_id") != full_plan["plan_id"]
        or raw != _canonical_json(payload)
    ):
        raise ValueError("full GO artifact is missing, malformed, or pending")
    timestamp = payload.get("authorized_at_utc")
    if not isinstance(timestamp, str):
        raise TypeError("full GO artifact must include a timezone-aware UTC authorization time")
    try:
        authorized_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("full GO artifact must include a timezone-aware UTC authorization time") from error
    if authorized_at.tzinfo is None or authorized_at.utcoffset() != timezone.utc.utcoffset(authorized_at):
        raise ValueError("full GO artifact must include a timezone-aware UTC authorization time")
    if payload.get("full_plan_sha256") != _sha256_bytes(full_plan_bytes):
        raise ValueError("full GO artifact is not bound to the full plan")
    if payload.get("falsifier_receipt_sha256") != _sha256_bytes(receipt_bytes):
        raise ValueError("full GO artifact is not bound to the falsifier receipt")
    return payload


def admit_full_preconditions(
    full_plan: dict[str, Any],
    full_plan_bytes: bytes,
    falsifier_evidence: Path,
    receipt_path: Path,
    full_go: Path,
    output: Path,
    repository_root: Path | None = None,
) -> tuple[dict[str, bytes], dict[str, str]]:
    """Reject any stale, mismatched, pending, or noncanonical full-run prerequisite."""
    if full_plan["kind"] != "full-measurement":
        raise ValueError("full preconditions are only valid for the full plan")
    _require_safe_existing_ancestors(falsifier_evidence.parent, "falsifier evidence")
    _require_real_directory(falsifier_evidence, "falsifier evidence")
    _safe_regular_files(falsifier_evidence, "falsifier evidence")
    _require_safe_existing_ancestors(receipt_path.parent, "falsifier receipt")
    _require_safe_existing_ancestors(full_go.parent, "full GO")
    _require_regular_file(receipt_path, "falsifier receipt")
    _require_regular_file(full_go, "full GO")
    inputs = (falsifier_evidence, receipt_path, full_go)
    if len({_normalized_absolute(path) for path in inputs}) != len(inputs):
        raise ValueError("full-run inputs must be distinct")
    if any(_paths_overlap(falsifier_evidence, path) for path in (receipt_path, full_go)):
        raise ValueError("receipt and GO must be outside the falsifier evidence root")
    _require_absent_output(output, inputs)
    _require_regular_file(receipt_path, "falsifier receipt")
    receipt_bytes = receipt_path.read_bytes()
    try:
        receipt = json.loads(receipt_bytes)
    except json.JSONDecodeError as error:
        raise ValueError("falsifier receipt is stale, malformed, or not byte-exact") from error
    expected = build_falsifier_receipt(
        falsifier_evidence,
        Path(__file__).resolve().parent.parent / "research" / "v0_measurement" / "falsifier_plan.json",
        repository_root,
    )
    if (
        not isinstance(receipt, dict)
        or receipt_bytes != expected
        or receipt.get("kind") != "anachron-v0-falsifier-pass-receipt"
    ):
        raise ValueError("falsifier receipt is stale, malformed, or not byte-exact")
    _require_regular_file(full_go, "full GO")
    go_bytes = full_go.read_bytes()
    validate_full_go_authorization(full_plan, full_plan_bytes, receipt_bytes, full_go)
    _, manifest_files = _verified_manifest_files(falsifier_evidence)
    source_inventory = set(manifest_files) | {"manifest.json", "manifest.sha256"}
    actual_inventory = _evidence_regular_files(falsifier_evidence)
    if actual_inventory != source_inventory:
        raise ValueError("falsifier prerequisite tree inventory is incomplete or contains extra files")
    copied = {
        "falsifier_pass_receipt.json": receipt_bytes,
        "full_go_authorization.json": go_bytes,
    }
    for relative in sorted(source_inventory):
        source = falsifier_evidence / _canonical_relative_path(relative)
        _require_regular_file(source, "falsifier prerequisite tree member")
        destination_relative = f"falsifier/{relative}"
        destination = output / "prerequisites" / destination_relative
        if len(str(destination.resolve(strict=False))) > _PREREQUISITE_PATH_BUDGET:
            raise ValueError("falsifier prerequisite destination exceeds the path budget")
        copied[destination_relative] = source.read_bytes()
    runtime = {
        "falsifier_subtree_path": "prerequisites/falsifier",
        "falsifier_manifest_sha256": _sha256_bytes(copied["falsifier/manifest.json"]),
        "falsifier_receipt_sha256": _sha256_bytes(receipt_bytes),
        "full_go_authorization_sha256": _sha256_bytes(go_bytes),
    }
    return copied, runtime


def _reconstruct_evidence(root: Path, plan: dict[str, Any], runtime: dict[str, Any], journal: list[dict[str, Any]]) -> None:
    """Rebuild valid trajectories from raw bytes; runtime is only a receipt."""
    plan_bytes = (root / "plan.json").read_bytes()
    if runtime.get("plan_id") != plan["plan_id"] or runtime.get("plan_sha256") != _sha256_bytes(plan_bytes):
        raise ValueError("runtime plan identity mismatch")
    if runtime.get("base_url") != plan["endpoint"]:
        raise ValueError("runtime endpoint mismatch")
    if runtime.get("generation_request_count_per_valid_trajectory") != 2:
        raise ValueError("runtime request count mismatch")
    version_raw = (root / "raw" / "server.version.response.json").read_bytes()
    tags_raw = (root / "raw" / "server.tags.response.json").read_bytes()
    if (
        runtime.get("server_version_sha256") != _sha256_bytes(version_raw)
        or runtime.get("server_tags_sha256") != _sha256_bytes(tags_raw)
    ):
        raise ValueError("runtime server raw hash mismatch")
    try:
        version = json.loads(version_raw)
        tags = json.loads(tags_raw)
    except json.JSONDecodeError as error:
        raise ValueError("runtime server raw response is not JSON") from error
    if runtime.get("server") != {"version": version, "tags": tags}:
        raise ValueError("runtime server reconstruction mismatch")
    tagged_models = tags.get("models") if isinstance(tags, dict) else None
    observed_models = {
        entry.get("name"): entry.get("digest")
        for entry in tagged_models
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    } if isinstance(tagged_models, list) else {}
    if any(observed_models.get(model["name"]) != model["digest"] for model in plan["models"]):
        raise ValueError("runtime model inventory mismatch")
    source_admission_raw = (root / "source_admission.json").read_bytes()
    if runtime.get("source_admission_sha256") != _sha256_bytes(source_admission_raw):
        raise ValueError("runtime source admission hash mismatch")
    source_admission = json.loads(source_admission_raw)
    if (
        not isinstance(source_admission, dict)
        or source_admission.get("schema_version") != _SOURCE_ADMISSION_SCHEMA_VERSION
        or source_admission.get("plan_sha256") != _sha256_bytes(plan_bytes)
        or not isinstance(source_admission.get("governed_blobs"), dict)
    ):
        raise ValueError("runtime source admission reconstruction mismatch")
    records = {record.get("trajectory_id"): record for record in runtime.get("trajectories", []) if isinstance(record, dict)}
    terminals = {record.get("trajectory_id"): record for record in journal if record.get("kind") == "trajectory_terminal"}
    controls = json.loads((root / "controls.json").read_text(encoding="utf-8"))
    expected_controls = _run_controls()
    if controls != expected_controls or runtime.get("controls_sha256") != _sha256_file(root / "controls.json"):
        raise ValueError("static controls reconstruction mismatch")

    def artifact(record: dict[str, Any], path_key: str, hash_key: str) -> Path:
        relative = record.get(path_key)
        expected_hash = record.get(hash_key)
        if not isinstance(relative, str) or not isinstance(expected_hash, str):
            raise TypeError(f"trajectory artifact metadata is missing: {path_key}")
        path = root / relative
        if (
            Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or path.parent != root / "raw"
            or not path.is_file()
            or path.is_symlink()
            or _sha256_file(path) != expected_hash
        ):
            raise ValueError(f"trajectory artifact is invalid: {relative}")
        return path

    for trajectory in expected_trajectories(plan):
        record = records.get(trajectory["id"])
        terminal = terminals.get(trajectory["id"], {})
        if record is None or terminal.get("valid") != record.get("valid") or terminal.get("reason") != record.get("invalid_reason"):
            raise ValueError(f"runtime and terminal mismatch for {trajectory['id']}")
        request_path = artifact(record, "request_path", "request_sha256")
        if json.loads(request_path.read_text(encoding="utf-8")) != _chat_request(trajectory["model"], trajectory["sample"], plan["generation"]):
            raise ValueError(f"first request reconstruction mismatch for {trajectory['id']}")
        if not record.get("valid"):
            if not terminal.get("reason"):
                raise ValueError(f"invalid trajectory has no terminal reason: {trajectory['id']}")
            if "first_response_path" in record:
                first = json.loads(artifact(record, "first_response_path", "first_response_sha256").read_text(encoding="utf-8"))
                query, error = _extract_query(first, trajectory["model"])
                if error is None and query is not None and "final_response_path" in record:
                    final = json.loads(artifact(record, "final_response_path", "final_response_sha256").read_text(encoding="utf-8"))
                    if _validate_final_response(final, trajectory["model"]) is None:
                        raise ValueError(f"false-invalid trajectory has valid raw artifacts: {trajectory['id']}")
            continue
        first_path = artifact(record, "first_response_path", "first_response_sha256")
        first = json.loads(first_path.read_text(encoding="utf-8"))
        query, error = _extract_query(first, trajectory["model"])
        if error or query != record.get("query"):
            raise ValueError(f"first response reconstruction mismatch for {trajectory['id']}")
        items = search(query, enforce_as_of=trajectory["sample"].as_of if trajectory["mode"] == "enforced" else None)
        tool_text = format_search_results(items)
        tool_path = artifact(record, "tool_result_path", "tool_result_sha256")
        if tool_path.read_text(encoding="utf-8") != tool_text or [item.id for item in items] != record.get("returned_item_ids"):
            raise ValueError(f"tool result reconstruction mismatch for {trajectory['id']}")
        score = asdict(score_interactions([ToolInteraction(_TOOL_NAME, query, _query_dates(query), items)], trajectory["sample"].as_of))
        if score != record.get("score"):
            raise ValueError(f"score reconstruction mismatch for {trajectory['id']}")
        final_request = artifact(record, "final_request_path", "final_request_sha256")
        expected_final = _final_request(trajectory["model"], trajectory["sample"], plan["generation"], first["message"], tool_text)
        if json.loads(final_request.read_text(encoding="utf-8")) != expected_final:
            raise ValueError(f"final request reconstruction mismatch for {trajectory['id']}")
        final_response = json.loads(artifact(record, "final_response_path", "final_response_sha256").read_text(encoding="utf-8"))
        if _validate_final_response(final_response, trajectory["model"]) or final_response["message"]["content"] != record.get("final_answer"):
            raise ValueError(f"final response reconstruction mismatch for {trajectory['id']}")


def _run_controls() -> dict[str, bool]:
    """Run deterministic positive and negative controls before model generation."""
    from datetime import date

    from anachron.core.leakage import CorpusItem

    as_of = date(2021, 1, 1)
    future = CorpusItem("future", "future", date(2022, 1, 1))
    past = CorpusItem("past", "past", date(2020, 1, 1))
    survivor = CorpusItem("dead", "dead", date(2020, 1, 1), "DEAD", date(2010, 1, 1), date(2020, 6, 1))
    restatement = CorpusItem("rev", "rev", date(2022, 1, 1), restates_id="orig")
    positive = score_interactions([ToolInteraction("tool", "2022-01-01", _query_dates("2022-01-01"), [future, survivor, restatement])], as_of)
    negative = score_interactions([ToolInteraction("tool", "2021-01-01", _query_dates("2021-01-01"), [past])], as_of)
    controls = {
        "result_positive": positive.result_leaks == 1 and positive.tclr == 1.0,
        "result_negative": negative.result_leaks == 0 and negative.tclr == 0.0,
        "query_positive": positive.query_leaks == 1,
        "query_negative": negative.query_leaks == 0,
        "survivorship_positive": positive.survivorship_leaks == 1,
        "survivorship_negative": negative.survivorship_leaks == 0,
        "restatement_positive": positive.restatement_leaks == 1,
        "restatement_negative": negative.restatement_leaks == 0,
    }
    if not all(controls.values()):
        raise RuntimeError("static scoring controls failed")
    return controls


def run_static_controls() -> dict[str, bool]:
    """Execute the offline scoring controls without constructing an Ollama client."""
    return _run_controls()


def run_measurement(
    plan_path: Path,
    output: Path,
    source_admitter=None,
    transport=None,
    falsifier_evidence: Path | None = None,
    falsifier_receipt: Path | None = None,
    full_go: Path | None = None,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    """Run one frozen plan once, writing a new sealed evidence directory."""
    plan, plan_raw = load_plan(plan_path)
    prerequisite_bytes: dict[str, bytes] = {}
    full_prerequisites: dict[str, str] | None = None
    repository_root = repository_root or Path(__file__).resolve().parent.parent
    if plan["kind"] == "full-measurement":
        if falsifier_evidence is None or falsifier_receipt is None or full_go is None:
            raise ValueError("full plan requires falsifier evidence, receipt, and a human GO artifact")
        prerequisite_bytes, full_prerequisites = admit_full_preconditions(
            plan,
            plan_raw,
            falsifier_evidence,
            falsifier_receipt,
            full_go,
            output,
            repository_root,
        )
    elif any(value is not None for value in (falsifier_evidence, falsifier_receipt, full_go)):
        raise ValueError("falsifier plan does not accept full-run prerequisites")
    base_url = _require_loopback(plan["endpoint"])
    timeout = plan["timeout_seconds"]
    transport = transport or _http_json
    admittance = (source_admitter or admit_committed_source)(plan, repository_root)
    writer = _EvidenceWriter(output)
    writer.write_bytes("plan.json", plan_raw)
    for name, content in prerequisite_bytes.items():
        writer.write_bytes(f"prerequisites/{name}", content)
    writer.write_json("source_admission.json", admittance)
    version_raw, version, tags_raw, tags = _verify_server_identity(base_url, plan, timeout, transport)
    writer.write_bytes("raw/server.version.response.json", version_raw)
    writer.write_bytes("raw/server.tags.response.json", tags_raw)
    controls = _run_controls()
    writer.write_json("controls.json", controls)
    started_utc = _utc_now()
    records = [
        _run_trajectory(writer, base_url, trajectory, timeout, transport)
        for trajectory in expected_trajectories(plan)
    ]
    if any(record["valid"] is not True for record in records):
        raise RuntimeError("incomplete trajectories cannot produce sealed evidence")
    runtime = {
        "schema_version": 1,
        "plan_id": plan["plan_id"],
        "plan_sha256": _sha256_bytes(plan_raw),
        "base_url": base_url,
        "started_utc": started_utc,
        "server": {"version": version, "tags": tags},
        "server_version_sha256": _sha256_bytes(version_raw),
        "server_tags_sha256": _sha256_bytes(tags_raw),
        "source_admission_sha256": _sha256_file(output / "source_admission.json"),
        "source_python": admittance["python"],
        "controls_sha256": _sha256_file(output / "controls.json"),
        "generation_request_count_per_valid_trajectory": 2,
        "trajectories": records,
        "completed_utc": _utc_now(),
    }
    if full_prerequisites is not None:
        runtime["full_prerequisites"] = full_prerequisites
    writer.write_json("runtime.json", runtime)
    analysis = build_analysis(plan, runtime, _journal_records(output / "journal.jsonl"))
    writer.write_json("analysis.json", analysis)
    writer.write_bytes("README.md", _evidence_readme(plan))
    _write_manifest(writer, plan)
    analyze_evidence(output, repository_root)
    return analysis
