"""Build and validate the external, post-tag v4 governed-source manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from anachron.v4_contract import V4_GOVERNED_SOURCE_PATHS, V4_PROTOCOL_BRANCH
from anachron.v4_paths import (
    V4PathError,
    admit_create_only_external_output,
    admit_external_regular_input,
    admit_repository_regular_file,
    admit_repository_root,
)

_HEX40 = set("0123456789abcdef")
_ORIGIN = "https://github.com/LesterALeong/anachron.git"
_SCHEMA_VERSION = "anachron-v4-source-manifest-v1"


class V4SourceManifestError(ValueError):
    """Raised when a v4 source manifest is not an exact release closure."""


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise V4SourceManifestError("source manifest git check failed") from error


def _git_bytes(root: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise V4SourceManifestError("source manifest blob check failed") from error


def _hex40(value: object, label: str) -> str:
    if type(value) is not str or len(value) != 40 or any(char not in _HEX40 for char in value):
        raise V4SourceManifestError(f"{label} is not a lowercase Git object ID")
    return value


def _mapping(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise V4SourceManifestError(f"{label} has unexpected or missing fields")
    return value


def _remote(root: Path, reference: str) -> str:
    fields = _git(root, "ls-remote", "origin", reference).split()
    if len(fields) != 2:
        raise V4SourceManifestError("source manifest remote reference differs")
    return _hex40(fields[0], f"remote {reference}")


def _expected_v3(root: Path) -> dict[str, str]:
    tag = "v3-measurement-protocol-v1"
    return {
        "commit": _hex40(_git(root, "rev-parse", f"refs/tags/{tag}^{{}}"), "v3 protocol commit"),
        "tag": tag,
        "tag_object": _hex40(
            _git(root, "rev-parse", f"refs/tags/{tag}^{{tag}}"),
            "v3 protocol tag object",
        ),
    }


def _release(
    root: Path, tag: str, expected_origin: str, expected_v3: dict[str, str]
) -> dict[str, str]:
    reference = f"refs/tags/{tag}"
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise V4SourceManifestError("source manifest requires a clean checkout")
    if _git(root, "rev-parse", "--abbrev-ref", "HEAD") != "HEAD":
        raise V4SourceManifestError("source manifest requires a detached checkout")
    if _git(root, "cat-file", "-t", reference) != "tag":
        raise V4SourceManifestError("source manifest requires an annotated release tag")
    release = {
        "branch_ref": _hex40(_git(root, "rev-parse", f"refs/heads/{V4_PROTOCOL_BRANCH}"), "branch ref"),
        "commit": _hex40(_git(root, "rev-parse", "HEAD"), "head commit"),
        "master_local": _hex40(_git(root, "rev-parse", "master"), "local master"),
        "master_remote": _remote(root, "refs/heads/master"),
        "origin": _git(root, "config", "--get", "remote.origin.url"),
        "remote_branch": _remote(root, f"refs/heads/{V4_PROTOCOL_BRANCH}"),
        "remote_tag_object": _remote(root, reference),
        "remote_tag_peeled": _remote(root, f"{reference}^{{}}"),
        "tag": tag,
        "tag_object": _hex40(_git(root, "rev-parse", f"{reference}^{{tag}}"), "tag object"),
        "tag_peeled": _hex40(_git(root, "rev-parse", f"{reference}^{{}}"), "tag peeled"),
        "v3_commit": _hex40(_git(root, "rev-parse", f"refs/tags/{expected_v3['tag']}^{{}}"), "v3 peeled"),
        "remote_v3_tag_object": _remote(root, f"refs/tags/{expected_v3['tag']}"),
        "remote_v3_tag_peeled": _remote(root, f"refs/tags/{expected_v3['tag']}^{{}}"),
        "v3_tag": expected_v3["tag"],
        "v3_tag_object": _hex40(_git(root, "rev-parse", f"refs/tags/{expected_v3['tag']}^{{tag}}"), "v3 tag object"),
        "v3_tag_peeled": _hex40(_git(root, "rev-parse", f"refs/tags/{expected_v3['tag']}^{{}}"), "v3 tag peeled"),
    }
    if (
        release["origin"] != expected_origin
        or len({release["branch_ref"], release["commit"], release["tag_peeled"], release["remote_branch"], release["remote_tag_peeled"]}) != 1
        or release["tag_object"] != release["remote_tag_object"]
        or release["master_local"] != expected_v3["commit"]
        or release["master_remote"] != expected_v3["commit"]
        or release["v3_commit"] != expected_v3["commit"]
        or release["v3_tag_object"] != expected_v3["tag_object"]
        or release["v3_tag_peeled"] != expected_v3["commit"]
        or release["remote_v3_tag_object"] != expected_v3["tag_object"]
        or release["remote_v3_tag_peeled"] != expected_v3["commit"]
    ):
        raise V4SourceManifestError("source manifest release closure differs")
    return release


def _files(root: Path, commit: str, paths: tuple[str, ...]) -> list[dict[str, str]]:
    if tuple(sorted(paths)) != paths or len(set(paths)) != len(paths):
        raise V4SourceManifestError("governed path set is not exact and sorted")
    files = []
    for relative in paths:
        try:
            path = admit_repository_regular_file(root / relative, root, "governed path")
        except V4PathError as error:
            raise V4SourceManifestError(str(error)) from error
        blob = _hex40(_git(root, "rev-parse", f"{commit}:{relative}"), "governed blob")
        raw = path.read_bytes()
        if _git_bytes(root, "cat-file", "blob", blob) != raw:
            raise V4SourceManifestError("governed worktree bytes differ from tag blob")
        files.append({"path": relative, "sha256": _sha256(raw), "tag_blob_oid": blob})
    return files


def derive(
    repository_root: Path,
    *,
    expected_origin: str = _ORIGIN,
    expected_v3: dict[str, str] | None = None,
) -> dict[str, object]:
    """Derive an exact manifest from a clean detached v4 tag without writing."""

    try:
        root = admit_repository_root(repository_root)
    except V4PathError as error:
        raise V4SourceManifestError(str(error)) from error
    expected_v3 = expected_v3 or _expected_v3(root)
    release = _release(root, "v4-measurement-protocol-v2", expected_origin, expected_v3)
    value = {
        "governed_files": _files(root, release["commit"], V4_GOVERNED_SOURCE_PATHS),
        "governed_paths": list(V4_GOVERNED_SOURCE_PATHS),
        "release": release,
        "schema_version": _SCHEMA_VERSION,
    }
    return value


def build(
    repository_root: Path,
    output: Path,
    *,
    expected_origin: str = _ORIGIN,
    expected_v3: dict[str, str] | None = None,
) -> dict[str, object]:
    """Create a canonical external manifest from a clean detached v4 tag."""

    try:
        root = admit_repository_root(repository_root)
        destination = admit_create_only_external_output(output, root, "source manifest output")
    except V4PathError as error:
        raise V4SourceManifestError(str(error)) from error
    value = derive(root, expected_origin=expected_origin, expected_v3=expected_v3)
    raw = _canonical_json_bytes(value)
    with destination.open("xb") as stream:
        stream.write(raw)
    validate(
        repository_root,
        destination,
        expected_origin=expected_origin,
        expected_v3=expected_v3,
    )
    return value


def validate(
    repository_root: Path,
    manifest_path: Path,
    *,
    expected_origin: str = _ORIGIN,
    expected_v3: dict[str, str] | None = None,
) -> dict[str, object]:
    """Revalidate manifest bytes, release parity, and every governed blob."""

    try:
        root = admit_repository_root(repository_root)
        manifest_path = admit_external_regular_input(manifest_path, root, "source manifest")
    except V4PathError as error:
        raise V4SourceManifestError(str(error)) from error
    try:
        raw = manifest_path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise V4SourceManifestError("source manifest cannot be read") from error
    value = _mapping(value, {"governed_files", "governed_paths", "release", "schema_version"}, "source manifest")
    if raw != _canonical_json_bytes(value) or value["schema_version"] != _SCHEMA_VERSION:
        raise V4SourceManifestError("source manifest is not canonical")
    paths = value["governed_paths"]
    if paths != list(V4_GOVERNED_SOURCE_PATHS):
        raise V4SourceManifestError("source manifest governed path topology differs")
    expected_v3 = expected_v3 or _expected_v3(root)
    release = _release(root, "v4-measurement-protocol-v2", expected_origin, expected_v3)
    if value["release"] != release:
        raise V4SourceManifestError("source manifest release differs")
    rows = value["governed_files"]
    if type(rows) is not list or len(rows) != len(paths):
        raise V4SourceManifestError("source manifest governed files differ")
    if rows != _files(root, release["commit"], V4_GOVERNED_SOURCE_PATHS):
        raise V4SourceManifestError("source manifest governed file identity differs")
    return value


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--validate", action="store_true")
    values = parser.parse_args(arguments)
    manifest = validate(values.repository_root, values.output) if values.validate else build(values.repository_root, values.output)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
