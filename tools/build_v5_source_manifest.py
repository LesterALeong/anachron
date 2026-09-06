"""Build and validate the external, post-tag v5 governed-source manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from anachron.v5_contract import (
    V5_GOVERNED_SOURCE_PATHS,
    V5_PROTOCOL_BRANCH,
    V5_PROTOCOL_TAG,
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
    admit_external_regular_input,
    admit_repository_regular_file,
    admit_repository_root,
    fsync_directory,
)
from anachron.v5_registry import canonical_json_bytes, strict_json_loads

_HEX40 = set("0123456789abcdef")
_ORIGIN = "https://github.com/LesterALeong/anachron.git"
_SCHEMA_VERSION = "anachron-v5-source-manifest-v2"


class V5SourceManifestError(ValueError):
    """Raised when a v5 source manifest is not an exact release closure."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _git(root: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise V5SourceManifestError("source manifest Git check failed") from error


def _git_bytes(root: Path, *arguments: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise V5SourceManifestError("source manifest Git blob check failed") from error


def _hex40(value: object, label: str) -> str:
    if type(value) is not str or len(value) != 40 or any(character not in _HEX40 for character in value):
        raise V5SourceManifestError(f"{label} is not a lowercase Git object ID")
    return value


def _mapping(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise V5SourceManifestError(f"{label} has unexpected or missing fields")
    return value


def _remote(root: Path, reference: str) -> str:
    fields = _git(root, "ls-remote", "origin", reference).split()
    if len(fields) != 2:
        raise V5SourceManifestError("source manifest remote reference differs")
    return _hex40(fields[0], f"remote {reference}")


def _release(root: Path, *, expected_origin: str, expected_release: Mapping[str, str] | None) -> dict[str, str]:
    """Derive the exact annotated-tag closure; overrides exist only for temporary-Git tests."""

    reference = f"refs/tags/{V5_PROTOCOL_TAG}"
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise V5SourceManifestError("source manifest requires a clean checkout")
    if _git(root, "branch", "--show-current"):
        raise V5SourceManifestError("source manifest requires a detached checkout")
    if _git(root, "cat-file", "-t", reference) != "tag":
        raise V5SourceManifestError("source manifest requires an annotated release tag")
    release = {
        "branch": V5_PROTOCOL_BRANCH,
        "branch_ref": _hex40(_git(root, "rev-parse", f"refs/heads/{V5_PROTOCOL_BRANCH}"), "branch ref"),
        "commit": _hex40(_git(root, "rev-parse", "HEAD"), "head commit"),
        "origin": _git(root, "config", "--get", "remote.origin.url"),
        "remote_branch": _remote(root, f"refs/heads/{V5_PROTOCOL_BRANCH}"),
        "remote_tag_object": _remote(root, reference),
        "remote_tag_peeled": _remote(root, f"{reference}^{{}}"),
        "tag": V5_PROTOCOL_TAG,
        "tag_object": _hex40(_git(root, "rev-parse", f"{reference}^{{tag}}"), "tag object"),
        "tag_peeled": _hex40(_git(root, "rev-parse", f"{reference}^{{}}"), "tag peeled"),
    }
    if (
        release["origin"] != expected_origin
        or len({release["branch_ref"], release["commit"], release["tag_peeled"], release["remote_branch"], release["remote_tag_peeled"]}) != 1
        or release["tag_object"] != release["remote_tag_object"]
    ):
        raise V5SourceManifestError("source manifest release closure differs")
    if expected_release is not None:
        expected = {key: _hex40(value, f"expected release {key}") for key, value in expected_release.items()}
        if set(expected) != {"branch_ref", "commit", "tag_object", "tag_peeled"} or any(release[key] != value for key, value in expected.items()):
            raise V5SourceManifestError("source manifest expected test release differs")
    return release


def _files(root: Path, commit: str, paths: tuple[str, ...]) -> list[dict[str, str]]:
    if tuple(sorted(paths)) != paths or len(set(paths)) != len(paths):
        raise V5SourceManifestError("governed path set is not exact and sorted")
    files = []
    for relative in paths:
        try:
            path = admit_repository_regular_file(root / relative, root, "governed path")
        except V5PathError as error:
            raise V5SourceManifestError(str(error)) from error
        blob = _hex40(_git(root, "rev-parse", f"{commit}:{relative}"), "governed blob")
        raw = capture_regular(path, "governed path", 1_048_576).raw
        if _git_bytes(root, "cat-file", "blob", blob) != raw:
            raise V5SourceManifestError("governed worktree bytes differ from tag blob")
        files.append({"path": relative, "sha256": _sha256(raw), "tag_blob_oid": blob})
    return files


def derive(
    repository_root: Path,
    *,
    expected_origin: str = _ORIGIN,
    expected_release: Mapping[str, str] | None = None,
    governed_paths: tuple[str, ...] | None = None,
) -> dict[str, object]:
    """Derive an exact manifest from the clean annotated v5 tag without writing."""

    try:
        root = admit_repository_root(repository_root)
    except V5PathError as error:
        raise V5SourceManifestError(str(error)) from error
    paths = V5_GOVERNED_SOURCE_PATHS if governed_paths is None else governed_paths
    release = _release(root, expected_origin=expected_origin, expected_release=expected_release)
    return {
        "governed_files": _files(root, release["commit"], paths),
        "governed_paths": list(paths),
        "release": release,
        "schema_version": _SCHEMA_VERSION,
    }


def build(repository_root: Path, output: Path, *, expected_origin: str = _ORIGIN) -> dict[str, object]:
    """Create a canonical external manifest from the immutable v5 release tag."""

    try:
        root = admit_repository_root(repository_root)
        destination = admit_create_only_external_output(output, root, "source manifest output")
        value = derive(root, expected_origin=expected_origin)
        write_create_only(
            destination,
            canonical_json_bytes(value),
            "source manifest output",
            ByteBudget(1_048_576, 1),
        )
        fsync_directory(destination.parent, "source manifest output parent")
    except (OSError, V5CustodyError, V5PathError) as error:
        raise V5SourceManifestError("source manifest cannot be finalized") from error
    validate(root, destination, expected_origin=expected_origin)
    return value


def validate(repository_root: Path, manifest_path: Path, *, expected_origin: str = _ORIGIN) -> dict[str, object]:
    """Revalidate canonical bytes, remote parity, and every governed tag blob."""

    try:
        root = admit_repository_root(repository_root)
        manifest = admit_external_regular_input(manifest_path, root, "source manifest")
        raw = capture_regular(manifest, "source manifest", 1_048_576).raw
        value = strict_json_loads(raw, "v5 source manifest")
    except (OSError, V5CustodyError, V5PathError) as error:
        raise V5SourceManifestError("source manifest cannot be read") from error
    value = _mapping(value, {"governed_files", "governed_paths", "release", "schema_version"}, "source manifest")
    if raw != canonical_json_bytes(value) or value["schema_version"] != _SCHEMA_VERSION:
        raise V5SourceManifestError("source manifest is not canonical")
    if value["governed_paths"] != list(V5_GOVERNED_SOURCE_PATHS):
        raise V5SourceManifestError("source manifest governed path topology differs")
    release = _release(root, expected_origin=expected_origin, expected_release=None)
    if value["release"] != release:
        raise V5SourceManifestError("source manifest release differs")
    if value["governed_files"] != _files(root, release["commit"], V5_GOVERNED_SOURCE_PATHS):
        raise V5SourceManifestError("source manifest governed file identity differs")
    return value


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--validate", action="store_true")
    values = parser.parse_args(arguments)
    try:
        result = validate(values.repository_root, values.output) if values.validate else build(values.repository_root, values.output)
    except V5SourceManifestError as error:
        print(f"V5 source manifest invalid: {error}")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
