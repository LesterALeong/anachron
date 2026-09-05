"""Single reparse-safe admission boundary for v4 filesystem inputs and outputs."""

from __future__ import annotations

import os
import stat
from pathlib import Path


class V4PathError(ValueError):
    """Raised when a v4 path crosses a symlink, junction, or reparse point."""


def _absolute(path: Path, label: str) -> Path:
    try:
        return Path(os.path.abspath(path))
    except TypeError as error:
        raise V4PathError(f"{label} is not a path") from error


def _components(path: Path) -> tuple[Path, ...]:
    current = Path(path.anchor)
    values = [current]
    for part in path.parts[1:]:
        current /= part
        values.append(current)
    return tuple(values)


def _is_reparse(path: Path) -> bool:
    metadata = path.lstat()
    attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & attribute
    )


def _admit_existing(path: Path, label: str) -> Path:
    absolute = _absolute(path, label)
    try:
        for component in _components(absolute):
            if _is_reparse(component):
                raise V4PathError(f"{label} has a symlink or reparse-point component")
        resolved = absolute.resolve(strict=True)
        for component in _components(resolved):
            if _is_reparse(component):
                raise V4PathError(f"{label} resolves through a reparse-point component")
    except OSError as error:
        raise V4PathError(f"{label} cannot be inspected safely") from error
    return resolved


def admit_repository_root(path: Path) -> Path:
    root = _admit_existing(path, "repository root")
    if not root.is_dir():
        raise V4PathError("repository root must be a directory")
    return root


def admit_external_regular_input(path: Path, repository_root: Path, label: str) -> Path:
    root = admit_repository_root(repository_root)
    target = _admit_existing(path, label)
    if not target.is_file():
        raise V4PathError(f"{label} must be a regular file")
    try:
        target.relative_to(root)
    except ValueError:
        return target
    raise V4PathError(f"{label} must be external to the repository root")


def admit_repository_regular_file(path: Path, repository_root: Path, label: str) -> Path:
    root = admit_repository_root(repository_root)
    target = _admit_existing(path, label)
    if not target.is_file():
        raise V4PathError(f"{label} must be a regular file")
    try:
        target.relative_to(root)
    except ValueError as error:
        raise V4PathError(f"{label} escapes the repository root") from error
    return target


def admit_create_only_external_output(path: Path, repository_root: Path, label: str) -> Path:
    root = admit_repository_root(repository_root)
    target = _absolute(path, label)
    if target.exists() or target.is_symlink():
        raise V4PathError(f"{label} must be absent")
    parent = _admit_existing(target.parent, f"{label} parent")
    try:
        parent.relative_to(root)
    except ValueError:
        return parent / target.name
    raise V4PathError(f"{label} must be external to the repository root")


def admit_evidence_root(path: Path, repository_root: Path, *, create: bool) -> Path:
    if create:
        return admit_create_only_external_output(path, repository_root, "evidence root")
    root = admit_repository_root(repository_root)
    evidence = _admit_existing(path, "evidence root")
    if not evidence.is_dir():
        raise V4PathError("evidence root must be a directory")
    try:
        evidence.relative_to(root)
    except ValueError:
        return evidence
    raise V4PathError("evidence root must be external to the repository root")


def admit_evidence_directory(
    evidence_root: Path, relative: str, label: str
) -> Path:
    root = _admit_existing(evidence_root, "evidence root")
    candidate = _admit_existing(root / relative, label)
    if not candidate.is_dir():
        raise V4PathError(f"{label} must be a directory")
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise V4PathError(f"{label} escapes the evidence root") from error
    return candidate


def admit_evidence_regular_file(
    evidence_root: Path, relative: str, label: str
) -> Path:
    """Admit one canonical, unambiguous regular file below evidence_root."""

    if (
        type(relative) is not str
        or not relative
        or "\\" in relative
        or relative.startswith("/")
    ):
        raise V4PathError(f"{label} path differs")
    candidate = Path(relative)
    if (
        candidate.is_absolute()
        or candidate.drive
        or candidate.root
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise V4PathError(f"{label} path differs")
    canonical = candidate.as_posix()
    if canonical != relative:
        raise V4PathError(f"{label} path differs")
    root = _admit_existing(evidence_root, "evidence root")
    target = _admit_existing(root / candidate, label)
    if not target.is_file():
        raise V4PathError(f"{label} must be a regular file")
    try:
        target.relative_to(root)
    except ValueError as error:
        raise V4PathError(f"{label} escapes the evidence root") from error
    return target
