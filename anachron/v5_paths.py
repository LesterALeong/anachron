"""Portable, reparse-safe filesystem admission for v5 evidence."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
from pathlib import Path, PurePosixPath

from anachron.v5_custody import (
    FAILURE_MAX_ENTRIES,
    V5CustodyError,
    bounded_tree_paths,
)


class V5PathError(ValueError):
    """Raised when a v5 evidence path is ambiguous or unsafe."""


_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_FORBIDDEN_COMPONENT_CHARACTERS = set('<>:"\\/|?*')


def portable_component(value: str, label: str) -> str:
    """Return one portable filename component or reject it before filesystem use."""

    if type(value) is not str or not value or value in {".", ".."}:
        raise V5PathError(f"{label} component differs")
    if value[-1] in {".", " "} or any(
        character in _FORBIDDEN_COMPONENT_CHARACTERS or ord(character) < 32
        for character in value
    ):
        raise V5PathError(f"{label} component is not portable")
    stem = value.split(".", 1)[0].upper()
    if stem in _RESERVED:
        raise V5PathError(f"{label} component is reserved")
    return value


def portable_relative_path(value: str, label: str) -> PurePosixPath:
    """Validate a slash-separated relative evidence path."""

    if type(value) is not str or not value or "\\" in value or value.startswith("/"):
        raise V5PathError(f"{label} path differs")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise V5PathError(f"{label} path differs")
    for component in path.parts:
        portable_component(component, label)
    if path.as_posix() != value:
        raise V5PathError(f"{label} path differs")
    return path


def _absolute(path: Path, label: str) -> Path:
    try:
        return Path(os.path.abspath(path))
    except TypeError as error:
        raise V5PathError(f"{label} is not a path") from error


def _components(path: Path) -> tuple[Path, ...]:
    current = Path(path.anchor)
    components = [current]
    for part in path.parts[1:]:
        current /= part
        components.append(current)
    return tuple(components)


def _is_reparse(path: Path) -> bool:
    metadata = path.lstat()
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & reparse
    )


def admit_existing(path: Path, label: str) -> Path:
    """Admit an existing path only when every component is non-reparse."""

    absolute = _absolute(path, label)
    try:
        for component in _components(absolute):
            if _is_reparse(component):
                raise V5PathError(f"{label} has a symlink or reparse-point component")
        resolved = absolute.resolve(strict=True)
        for component in _components(resolved):
            if _is_reparse(component):
                raise V5PathError(f"{label} resolves through a reparse-point component")
    except OSError as error:
        raise V5PathError(f"{label} cannot be inspected safely") from error
    return resolved


def admit_repository_root(path: Path) -> Path:
    root = admit_existing(path, "repository root")
    if not root.is_dir():
        raise V5PathError("repository root must be a directory")
    return root


def admit_external_regular_input(path: Path, repository_root: Path, label: str) -> Path:
    """Admit one regular input that is physically outside the checkout."""

    root = admit_repository_root(repository_root)
    target = admit_existing(path, label)
    if not target.is_file():
        raise V5PathError(f"{label} must be a regular file")
    try:
        target.relative_to(root)
    except ValueError:
        return target
    raise V5PathError(f"{label} must be external to the repository root")


def admit_repository_regular_file(path: Path, repository_root: Path, label: str) -> Path:
    """Admit one regular file physically contained by the checkout."""

    root = admit_repository_root(repository_root)
    target = admit_existing(path, label)
    if not target.is_file():
        raise V5PathError(f"{label} must be a regular file")
    try:
        target.relative_to(root)
    except ValueError as error:
        raise V5PathError(f"{label} escapes repository root") from error
    return target


def admit_create_only_external_output(path: Path, repository_root: Path, label: str) -> Path:
    """Return an absent, reparse-safe output path outside the checkout."""

    root = admit_repository_root(repository_root)
    target = _absolute(path, label)
    if target.exists() or target.is_symlink():
        raise V5PathError(f"{label} must be absent")
    parent = admit_existing(target.parent, f"{label} parent")
    try:
        parent.relative_to(root)
    except ValueError:
        portable_component(target.name, label)
        return parent / target.name
    raise V5PathError(f"{label} must be external to the repository root")


def fsync_directory(path: Path, label: str) -> None:
    """Flush a newly-created directory entry when the platform supports it.

    File bytes are fsynced by their create-only writers before this best-effort
    directory-entry flush. Windows does not expose a POSIX-readable directory
    handle, so only its documented access-denied directory-open result is
    accepted there. Every missing-path or unrelated I/O error remains fatal.
    """

    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError as error:
        if os.name == "nt" and error.errno in {errno.EACCES, errno.EPERM}:
            return
        raise V5PathError(f"{label} cannot be flushed") from error
    try:
        os.fsync(descriptor)
    except OSError as error:
        if os.name == "nt" and error.errno == errno.EINVAL:
            return
        raise V5PathError(f"{label} cannot be flushed") from error
    finally:
        os.close(descriptor)


def _windows_streams(path: Path) -> tuple[str, ...]:
    """Enumerate NTFS streams with FindFirstStreamW; non-Windows has none."""

    if os.name != "nt":
        return ()

    class _FindStreamData(ctypes.Structure):
        _fields_ = [("StreamSize", ctypes.c_longlong), ("cStreamName", ctypes.c_wchar * 296)]

    kernel32 = ctypes.windll.kernel32
    find_first = kernel32.FindFirstStreamW
    find_first.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.POINTER(_FindStreamData), ctypes.c_uint]
    find_first.restype = ctypes.c_void_p
    find_next = kernel32.FindNextStreamW
    find_next.argtypes = [ctypes.c_void_p, ctypes.POINTER(_FindStreamData)]
    find_next.restype = ctypes.c_int
    find_close = kernel32.FindClose
    find_close.argtypes = [ctypes.c_void_p]
    find_close.restype = ctypes.c_int
    data = _FindStreamData()
    handle = find_first(str(path), 0, ctypes.byref(data), 0)
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        error = ctypes.get_last_error()
        if error in {0, 2, 38}:  # no streams / file absent are handled by admission
            return ()
        raise V5PathError("alternate stream inspection failed")
    values = [data.cStreamName]
    try:
        while find_next(handle, ctypes.byref(data)):
            values.append(data.cStreamName)
        error = ctypes.get_last_error()
        if error not in {0, 38}:
            raise V5PathError("alternate stream inspection failed")
    finally:
        find_close(handle)
    return tuple(values)


def assert_no_alternate_data_streams(root: Path) -> None:
    """Reject every non-default NTFS stream below an admitted evidence root."""

    admitted = admit_existing(root, "evidence root")
    try:
        candidates = (admitted, *(
            admitted / relative
            for relative, _ in bounded_tree_paths(
                admitted,
                "evidence root",
                maximum_entries=FAILURE_MAX_ENTRIES,
                maximum_depth=2,
            )
        ))
    except V5CustodyError as error:
        raise V5PathError("evidence root topology differs") from error
    for candidate in candidates:
        if _is_reparse(candidate):
            raise V5PathError("evidence root has a symlink or reparse-point component")
        for stream in _windows_streams(candidate):
            if stream != "::$DATA":
                raise V5PathError("evidence root contains an alternate data stream")


def ordinal_evidence_path(root: Path, ordinal: int, role: str) -> Path:
    """Return the only allowed physical name for a v5 transcript role."""

    if type(ordinal) is not int or ordinal < 1:
        raise V5PathError("trajectory ordinal differs")
    if role not in {"first-request", "first-response", "tool-result", "final-request", "final-response"}:
        raise V5PathError("evidence role differs")
    admitted = admit_existing(root, "evidence root")
    raw = admitted / "raw"
    if not raw.is_dir():
        raise V5PathError("evidence raw directory is absent")
    relative = f"raw/t{ordinal:04d}-{role}.json"
    portable_relative_path(relative, "evidence")
    destination = admitted.joinpath(*PurePosixPath(relative).parts)
    if destination.exists() or destination.is_symlink():
        raise V5PathError("evidence destination must be absent")
    return destination
