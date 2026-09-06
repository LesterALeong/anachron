"""Bounded, fail-closed custody primitives for v5 governed artifacts."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import io
import os
import stat
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

CHUNK_BYTES = 65_536
_MOVE_ATTEMPTS = 5
_MOVE_RETRY_SECONDS = 0.02
_WINDOWS = os.name == "nt"
AUTHORITY_MEMBER_MAX_BYTES = 1_048_576
NATIVE_RESPONSE_MAX_BYTES = 1_048_576
NATIVE_RESPONSE_TOTAL_MAX_BYTES = 8_388_608
GENERATED_MEMBER_MAX_BYTES = 262_144
METADATA_MEMBER_MAX_BYTES = 1_048_576
DIGEST_SIDECAR_MAX_BYTES = 128
SUCCESS_MAX_BYTES = 73_924_736
FAILURE_MAX_BYTES = 74_973_312
SUCCESS_MAX_FILES = 345
FAILURE_MAX_FILES = 346
SUCCESS_MAX_ENTRIES = 350
FAILURE_MAX_ENTRIES = 351


class V5CustodyError(ValueError):
    """Raised when a governed artifact violates its custody profile."""


@dataclass(frozen=True)
class CapturedFile:
    """One bounded, stable regular-file capture."""

    raw: bytes
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class PhysicalMember:
    """One bounded physical member observed beneath a governed root."""

    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class PhysicalInventory:
    """Exact bounded physical topology and streamed member identities."""

    directories: tuple[str, ...]
    files: tuple[PhysicalMember, ...]


@dataclass
class ByteBudget:
    """Aggregate byte/member accounting checked before every retained member."""

    byte_limit: int
    member_limit: int
    bytes_used: int = 0
    members_used: int = 0

    def reserve(self, size_bytes: int, label: str) -> None:
        if type(size_bytes) is not int or size_bytes < 0:
            raise V5CustodyError(f"{label} size differs")
        if self.members_used + 1 > self.member_limit or self.bytes_used + size_bytes > self.byte_limit:
            raise V5CustodyError(f"{label} exceeds custody budget")
        self.bytes_used += size_bytes
        self.members_used += 1

    def refund(self, size_bytes: int, label: str) -> None:
        """Reverse an uncommitted reservation after a create-only write fails."""

        if (
            type(size_bytes) is not int
            or size_bytes < 0
            or self.members_used < 1
            or self.bytes_used < size_bytes
        ):
            raise V5CustodyError(f"{label} budget differs")
        self.bytes_used -= size_bytes
        self.members_used -= 1


@dataclass(frozen=True)
class RootProfile:
    """Exact member and aggregate limits for one downstream local root."""

    allowed_members: tuple[str, ...]
    byte_limit: int
    member_limit: int
    member_caps: dict[str, int]

    def budget(self) -> ByteBudget:
        if len(self.allowed_members) != self.member_limit or set(self.allowed_members) != set(self.member_caps):
            raise V5CustodyError("root profile differs")
        return ByteBudget(self.byte_limit, self.member_limit)

    def member_cap(self, relative: str) -> int:
        try:
            return self.member_caps[relative]
        except KeyError as error:
            raise V5CustodyError("root profile member differs") from error


def _regular_identity(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise V5CustodyError(f"{label} cannot be inspected") from error
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise V5CustodyError(f"{label} must be a regular file")
    if getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        raise V5CustodyError(f"{label} has a reparse-point component")
    return metadata


def _same_identity(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) == (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )


def capture_regular(path: Path, label: str, maximum: int, *, expected_sha256: str | None = None) -> CapturedFile:
    """Capture a stable regular file with pre/post identity and chunked caps."""

    before = _regular_identity(path, label)
    if before.st_size > maximum:
        raise V5CustodyError(f"{label} exceeds its byte cap")
    digest, raw = hashlib.sha256(), bytearray()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(CHUNK_BYTES):
                if len(raw) + len(chunk) > maximum:
                    raise V5CustodyError(f"{label} exceeds its byte cap")
                raw.extend(chunk)
                digest.update(chunk)
    except OSError as error:
        raise V5CustodyError(f"{label} cannot be read") from error
    after = _regular_identity(path, label)
    if not _same_identity(before, after):
        raise V5CustodyError(f"{label} changed during capture")
    actual = digest.hexdigest()
    if expected_sha256 is not None and actual != expected_sha256:
        raise V5CustodyError(f"{label} digest differs")
    return CapturedFile(bytes(raw), actual, len(raw))


def capture_stream(stream: BinaryIO, label: str, maximum: int, budget: ByteBudget) -> CapturedFile:
    """Capture one bounded response before retaining it in an evidence transaction."""

    digest, raw = hashlib.sha256(), bytearray()
    while chunk := stream.read(CHUNK_BYTES):
        if len(raw) + len(chunk) > maximum:
            raise V5CustodyError(f"{label} exceeds its byte cap")
        raw.extend(chunk)
        digest.update(chunk)
    budget.reserve(len(raw), label)
    return CapturedFile(bytes(raw), digest.hexdigest(), len(raw))


def _atomic_no_replace_move(temporary: Path, path: Path) -> None:
    """Atomically move a same-directory temporary file only onto an absent path."""

    if _WINDOWS:
        for attempt in range(_MOVE_ATTEMPTS):
            try:
                os.rename(temporary, path)
                return
            except PermissionError as error:
                transient = error.errno in {errno.EACCES, errno.EPERM} or getattr(error, "winerror", None) == 32
                if not transient or attempt + 1 == _MOVE_ATTEMPTS:
                    raise
                time.sleep(_MOVE_RETRY_SECONDS)
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = libc.renameat2
    except AttributeError as error:
        raise V5CustodyError("atomic no-replace move is unavailable") from error
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    if renameat2(
        -100,
        os.fsencode(temporary),
        -100,
        os.fsencode(path),
        1,
    ) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(path))


def write_create_only(path: Path, raw: bytes, label: str, budget: ByteBudget) -> str:
    """Write one staged member only after its aggregate reservation succeeds."""

    if path.exists() or path.is_symlink():
        raise V5CustodyError(f"{label} must be absent")
    budget.reserve(len(raw), label)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    published = False
    try:
        with temporary.open("wb") as stream:
            for start in range(0, len(raw), CHUNK_BYTES):
                stream.write(raw[start : start + CHUNK_BYTES])
            stream.flush()
            os.fsync(stream.fileno())
        _atomic_no_replace_move(temporary, path)
        published = True
    except (OSError, V5CustodyError) as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        if not published:
            budget.refund(len(raw), label)
        if isinstance(error, FileExistsError) or getattr(error, "errno", None) == errno.EEXIST:
            raise V5CustodyError(f"{label} must be absent") from error
        raise V5CustodyError(f"{label} cannot be written") from error
    return hashlib.sha256(raw).hexdigest()


def scandir_exact(root: Path, expected: Iterable[str], maximum_entries: int, label: str) -> tuple[str, ...]:
    """Read a fixed direct-child topology without recursive fallback discovery."""

    observed: list[str] = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if len(observed) >= maximum_entries:
                    raise V5CustodyError(f"{label} exceeds traversal entry cap")
                observed.append(entry.name)
    except OSError as error:
        raise V5CustodyError(f"{label} cannot be traversed") from error
    if tuple(sorted(observed)) != tuple(sorted(expected)):
        raise V5CustodyError(f"{label} topology differs")
    return tuple(observed)


def bounded_tree_paths(root: Path, label: str, *, maximum_entries: int, maximum_depth: int) -> tuple[tuple[str, bool], ...]:
    """Return bounded, reparse-free relative paths and directory flags.

    Governed roots have a deliberately shallow fixed topology. The caller
    compares this inventory with its schema-derived path list.
    """

    try:
        root_metadata = root.lstat()
    except OSError as error:
        raise V5CustodyError(f"{label} cannot be inspected") from error
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or stat.S_ISLNK(root_metadata.st_mode)
        or getattr(root_metadata, "st_file_attributes", 0) & 0x400
    ):
        raise V5CustodyError(f"{label} root differs")
    pending: list[tuple[Path, int]] = [(root, 0)]
    paths: list[tuple[str, bool]] = []
    entries = 0
    while pending:
        directory, depth = pending.pop()
        try:
            with os.scandir(directory) as scan:
                for entry in scan:
                    entries += 1
                    if entries > maximum_entries:
                        raise V5CustodyError(f"{label} has too many entries")
                    try:
                        metadata = entry.stat(follow_symlinks=False)
                    except OSError as error:
                        raise V5CustodyError(f"{label} cannot be inspected") from error
                    if entry.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400:
                        raise V5CustodyError(f"{label} has a reparse component")
                    path = Path(entry.path)
                    if stat.S_ISREG(metadata.st_mode):
                        paths.append((path.relative_to(root).as_posix(), False))
                    elif stat.S_ISDIR(metadata.st_mode):
                        if depth >= maximum_depth:
                            raise V5CustodyError(f"{label} exceeds its topology depth")
                        paths.append((path.relative_to(root).as_posix(), True))
                        pending.append((path, depth + 1))
                    else:
                        raise V5CustodyError(f"{label} has a non-regular component")
        except OSError as error:
            raise V5CustodyError(f"{label} cannot be inspected") from error
    return tuple(sorted(paths))


def bounded_regular_files(root: Path, label: str, *, maximum_entries: int, maximum_depth: int) -> tuple[str, ...]:
    """Return a bounded, reparse-free relative-file inventory."""

    return tuple(
        path
        for path, is_directory in bounded_tree_paths(
            root,
            label,
            maximum_entries=maximum_entries,
            maximum_depth=maximum_depth,
        )
        if not is_directory
    )


def physical_inventory(
    root: Path,
    label: str,
    *,
    maximum_entries: int,
    maximum_depth: int,
    byte_limit: int,
    member_cap: Callable[[str], int],
) -> PhysicalInventory:
    """Capture every bounded governed member while retaining exact directories."""

    paths = bounded_tree_paths(
        root,
        label,
        maximum_entries=maximum_entries,
        maximum_depth=maximum_depth,
    )
    directories = tuple(path for path, is_directory in paths if is_directory)
    members: list[PhysicalMember] = []
    total = 0
    for path, is_directory in paths:
        if is_directory:
            continue
        try:
            cap = member_cap(path)
        except Exception as error:
            raise V5CustodyError(f"{label} member path differs") from error
        captured = capture_regular(root / path, f"{label} member", cap)
        total += captured.size_bytes
        if total > byte_limit:
            raise V5CustodyError(f"{label} exceeds custody budget")
        members.append(PhysicalMember(path, captured.sha256, captured.size_bytes))
    files = tuple(members)
    return PhysicalInventory(directories, files)


def discard_staging_root(root: Path, label: str, *, maximum_entries: int, maximum_depth: int) -> None:
    """Remove only a task-owned bounded staging tree after failed publication."""

    bounded_regular_files(
        root,
        label,
        maximum_entries=maximum_entries,
        maximum_depth=maximum_depth,
    )

    def remove(directory: Path, depth: int) -> None:
        children: list[Path] = []
        with os.scandir(directory) as scan:
            for entry in scan:
                path = Path(entry.path)
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(metadata.st_mode):
                    if depth >= maximum_depth:
                        raise V5CustodyError(f"{label} exceeds its topology depth")
                    remove(path, depth + 1)
                elif stat.S_ISREG(metadata.st_mode):
                    children.append(path)
                else:
                    raise V5CustodyError(f"{label} has a non-regular component")
        for child in children:
            child.unlink()
        directory.rmdir()

    try:
        remove(root, 0)
    except OSError as error:
        raise V5CustodyError(f"{label} cannot be removed") from error


def publish_staging_root(staging: Path, output: Path, label: str) -> None:
    """Publish a task-owned staging root only while the final target is absent."""

    if output.exists() or output.is_symlink():
        raise V5CustodyError(f"{label} final target must be absent")
    try:
        _atomic_no_replace_move(staging, output)
    except (OSError, V5CustodyError) as error:
        if isinstance(error, FileExistsError) or getattr(error, "errno", None) == errno.EEXIST:
            raise V5CustodyError(f"{label} final target must be absent") from error
        raise V5CustodyError(f"{label} cannot be published") from error


def verify_copy_archive_round_trips(
    root: Path,
    *,
    maximum_entries: int,
    maximum_files: int,
    maximum_bytes: int,
    maximum_depth: int,
) -> None:
    """Verify bounded regular members survive exact copy and ZIP round trips."""

    files = bounded_regular_files(
        root,
        "evidence root",
        maximum_entries=maximum_entries,
        maximum_depth=maximum_depth,
    )
    budget = ByteBudget(maximum_bytes, maximum_files)
    captured = {
        relative: capture_regular(root / relative, "evidence member", maximum_bytes)
        for relative in files
    }
    for relative, member in captured.items():
        budget.reserve(member.size_bytes, relative)
    with tempfile.TemporaryDirectory() as temporary:
        temporary_root = Path(temporary)
        copied = temporary_root / "copied"
        copied.mkdir()
        copied_budget = ByteBudget(maximum_bytes, maximum_files)
        for relative, member in captured.items():
            target = copied / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            write_create_only(target, member.raw, "copied evidence member", copied_budget)
        archive_path = temporary_root / "evidence.zip"
        with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative, member in captured.items():
                info = zipfile.ZipInfo(relative, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, member.raw)
        archive_capture = capture_regular(archive_path, "evidence archive", maximum_bytes)
        extracted = temporary_root / "extracted"
        extracted.mkdir()
        extracted_budget = ByteBudget(maximum_bytes, maximum_files)
        try:
            with zipfile.ZipFile(io.BytesIO(archive_capture.raw)) as archive:
                infos = archive.infolist()
                if [info.filename for info in infos] != list(files):
                    raise V5CustodyError("evidence archive topology differs")
                for info in infos:
                    if info.is_dir() or info.file_size > maximum_bytes or info.compress_size > maximum_bytes:
                        raise V5CustodyError("evidence archive member exceeds byte cap")
                    target = extracted / info.filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as stream:
                        member = capture_stream(
                            stream,
                            "extracted evidence member",
                            maximum_bytes,
                            ByteBudget(maximum_bytes, 1),
                        )
                    write_create_only(target, member.raw, "extracted evidence member", extracted_budget)
        except (OSError, zipfile.BadZipFile) as error:
            raise V5CustodyError("evidence archive cannot be verified") from error
        for candidate in (copied, extracted):
            candidate_files = bounded_regular_files(
                candidate,
                "candidate evidence",
                maximum_entries=maximum_entries,
                maximum_depth=maximum_depth,
            )
            if candidate_files != files or any(
                capture_regular(candidate / relative, "candidate evidence member", maximum_bytes).sha256
                != captured[relative].sha256
                for relative in files
            ):
                raise V5CustodyError("evidence copy or archive bytes differ")
