"""Fail-closed clean-checkout admission for date-shift bundle operations."""

from __future__ import annotations

import ast
import importlib.machinery
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from anachron.date_shift import DateShiftValidationError, bytes_sha256, canonical_sha256
from tools import build_date_shift_items as _build_date_shift_items

_BOUND_TEXT = (
    ".gitattributes",
    "research/date-shift/ACCEPTANCE_MATRIX.md",
    "research/date-shift/PROTOCOL.md",
    "research/date-shift/README.md",
    "research/date-shift/execution_plan.json",
    "research/date-shift/proposed_frame.json",
    "research/date-shift/proposed_items.json",
)
_CLOSURE = (
    "anachron/__init__.py",
    "anachron/date_shift.py",
    "anachron/date_shift_bundle.py",
    "anachron/date_shift_provenance.py",
    "tools/build_date_shift_items.py",
    "tools/build_date_shift_audit_scaffold_release.py",
    "tools/finalize_date_shift_audit.py",
    "tools/capture_date_shift_runtime.py",
    "tools/seal_date_shift_execution_bundle.py",
    "tools/run_date_shift.py",
    "tools/analyze_date_shift.py",
    "tools/audit_date_shift_other_outputs.py",
)

_RESOLVABLE_CLOSURE = {
    "anachron": "anachron/__init__.py",
    "anachron.date_shift": "anachron/date_shift.py",
    "anachron.date_shift_bundle": "anachron/date_shift_bundle.py",
    "anachron.date_shift_provenance": "anachron/date_shift_provenance.py",
    "tools.build_date_shift_items": "tools/build_date_shift_items.py",
    "tools.build_date_shift_audit_scaffold_release": "tools/build_date_shift_audit_scaffold_release.py",
    "tools.finalize_date_shift_audit": "tools/finalize_date_shift_audit.py",
    "tools.capture_date_shift_runtime": "tools/capture_date_shift_runtime.py",
    "tools.seal_date_shift_execution_bundle": "tools/seal_date_shift_execution_bundle.py",
    "tools.run_date_shift": "tools/run_date_shift.py",
    "tools.analyze_date_shift": "tools/analyze_date_shift.py",
    "tools.audit_date_shift_other_outputs": "tools/audit_date_shift_other_outputs.py",
}

_REQUIRED_LOADED_MODULES = frozenset(
    {
        "anachron",
        "anachron.date_shift",
        "anachron.date_shift_provenance",
    }
)


def _command(repository: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise DateShiftValidationError("git provenance command failed") from error
    return completed.stdout.strip()


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DateShiftValidationError("audit scaffold receipt is invalid") from error
    if not isinstance(value, dict):
        raise DateShiftValidationError("audit scaffold receipt must be an object")
    return value


def _tracked_bytes(repository: Path, commit: str, relative: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), "show", f"{commit}:{relative}"],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise DateShiftValidationError(
            f"cannot read committed artifact: {relative}"
        ) from error
    return completed.stdout


def _is_sys_path(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
        and node.attr == "path"
    )


def _is_pathfinder_find_spec(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "find_spec"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "PathFinder"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "importlib"
    )


def _validate_static_python(repository: Path, paths: tuple[str, ...]) -> None:
    """Allow only the read-only resolver's audited sys.path access."""
    for relative in paths:
        source = (repository / relative).read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError as error:
            raise DateShiftValidationError(
                f"closure source does not parse: {relative}"
            ) from error
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        functions = {
            node: node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.ImportFrom, ast.Import)):
                continue
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"__import__", "exec", "eval"}
            ):
                raise DateShiftValidationError(
                    "dynamic execution is forbidden in the date-shift closure"
                )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
            ):
                raise DateShiftValidationError(
                    "dynamic imports are forbidden in the date-shift closure"
                )
            if not _is_sys_path(node):
                continue
            parent = parents.get(node)
            function = next(
                (
                    functions[ancestor]
                    for ancestor in ast.walk(tree)
                    if ancestor in functions and node in ast.walk(ancestor)
                ),
                None,
            )
            if (
                relative == "anachron/date_shift_provenance.py"
                and function == "_pathfinder_origin"
                and isinstance(parent, ast.Assign)
                and parent.value is node
                and len(parent.targets) == 1
                and isinstance(parent.targets[0], ast.Name)
                and parent.targets[0].id == "search"
            ):
                continue
            raise DateShiftValidationError(
                "sys.path may only be read by the audited PathFinder resolver"
            )


def closure_digest(repository: Path) -> str:
    rows = {}
    for relative in _CLOSURE:
        path = repository / relative
        if not path.is_file():
            raise DateShiftValidationError(f"missing closure source: {relative}")
        rows[relative] = bytes_sha256(path.read_bytes())
    return canonical_sha256(rows)


def build_audit_scaffold_release(repository: Path, tag: str) -> dict[str, Any]:
    """Build, without writing, the tag-pinned audit scaffold descriptor."""
    repository = repository.resolve()
    if not isinstance(tag, str) or not tag:
        raise DateShiftValidationError("scaffold tag is invalid")
    remote_url = _command(repository, "remote", "get-url", "origin")
    if not remote_url:
        raise DateShiftValidationError("scaffold origin is invalid")
    return {
        "schema_version": "date-shift-audit-scaffold-release-v1",
        "git": {"remote_url": remote_url, "tag": tag},
        "code_closure_sha256": closure_digest(repository),
        "bound_text_sha256": {
            relative: bytes_sha256((repository / relative).read_bytes())
            for relative in _BOUND_TEXT
        },
    }


def _pathfinder_origin(module_name: str) -> Path:
    parts = module_name.split(".")
    search = sys.path
    spec = None
    for index in range(len(parts)):
        spec = importlib.machinery.PathFinder.find_spec(parts[index], search)
        if spec is None:
            raise DateShiftValidationError(
                f"cannot resolve imported module: {module_name}"
            )
        if index == len(parts) - 1:
            if spec.origin is None:
                raise DateShiftValidationError(
                    f"cannot resolve imported module: {module_name}"
                )
            return Path(spec.origin).resolve()
        search = list(spec.submodule_search_locations or ())
        if not search:
            raise DateShiftValidationError(
                f"cannot resolve imported module: {module_name}"
            )
    raise DateShiftValidationError(f"cannot resolve imported module: {module_name}")


def verify_imported_sources(repository: Path) -> None:
    """Reject a process importing date-shift code from a different checkout."""
    repository = repository.resolve()
    if _build_date_shift_items.__name__ != "tools.build_date_shift_items":
        raise DateShiftValidationError("date-shift builder import identity is invalid")
    head = _command(repository, "rev-parse", "HEAD")
    for module_name, relative in _RESOLVABLE_CLOSURE.items():
        expected = (repository / relative).resolve()
        loaded = sys.modules.get(module_name)
        loaded_path = Path(getattr(loaded, "__file__", "")).resolve() if loaded else None
        if _pathfinder_origin(module_name) != expected:
            raise DateShiftValidationError(
                "imported date-shift module is outside the admitted checkout"
            )
        if loaded is None:
            if module_name in _REQUIRED_LOADED_MODULES:
                raise DateShiftValidationError("required date-shift module was not loaded")
        elif loaded_path != expected:
            raise DateShiftValidationError(
                "imported date-shift module is outside the admitted checkout"
            )
        committed = _tracked_bytes(repository, head, relative)
        if loaded is not None and loaded_path.read_bytes() != expected.read_bytes():
            raise DateShiftValidationError("imported date-shift module bytes drifted")
        if expected.read_bytes() != committed:
            raise DateShiftValidationError(f"committed blob drifted: {relative}")


def _require_detached_annotated_remote_tag(
    repository: Path, tag: str, head: str
) -> None:
    try:
        attached = subprocess.run(
            ["git", "-C", str(repository), "symbolic-ref", "-q", "HEAD"],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DateShiftValidationError("cannot determine checkout attachment") from error
    if attached.returncode == 0:
        raise DateShiftValidationError("date-shift operations require detached HEAD")
    if attached.returncode != 1:
        raise DateShiftValidationError("cannot determine checkout attachment")
    if _command(repository, "cat-file", "-t", f"refs/tags/{tag}") != "tag":
        raise DateShiftValidationError("scaffold tag must be an annotated local tag")
    local_target = _command(repository, "rev-parse", f"refs/tags/{tag}^{{commit}}")
    if local_target != head:
        raise DateShiftValidationError("local scaffold tag does not peel to checkout HEAD")
    remote_rows = _command(
        repository,
        "ls-remote",
        "origin",
        f"refs/tags/{tag}",
        f"refs/tags/{tag}^{{}}",
    )
    remote_refs = {
        parts[1]: parts[0]
        for line in remote_rows.splitlines()
        if len(parts := line.split()) == 2
    }
    if (
        set(remote_refs) != {f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"}
        or remote_refs[f"refs/tags/{tag}^{{}}"] != head
    ):
        raise DateShiftValidationError(
            "remote scaffold tag does not peel to checkout HEAD"
        )


def admit_scaffold_repository(repository: Path) -> dict[str, Any]:
    """Require a clean detached checkout whose pushed tag names the scaffold."""
    repository = repository.resolve()
    if not (repository / ".git").exists():
        raise DateShiftValidationError("repository root is invalid")
    if _command(repository, "status", "--porcelain", "--untracked-files=all"):
        raise DateShiftValidationError("date-shift operations require a clean checkout")
    receipt = _load(repository / "research/date-shift/audit_scaffold_release.json")
    required = {"schema_version", "git", "code_closure_sha256", "bound_text_sha256"}
    if (
        set(receipt) != required
        or receipt["schema_version"] != "date-shift-audit-scaffold-release-v1"
    ):
        raise DateShiftValidationError("audit scaffold receipt schema is invalid")
    git = receipt["git"]
    if (
        not isinstance(git, dict)
        or set(git) != {"remote_url", "tag"}
        or not all(isinstance(value, str) and value for value in git.values())
    ):
        raise DateShiftValidationError("audit scaffold receipt git binding is invalid")
    head = _command(repository, "rev-parse", "HEAD")
    remote = _command(repository, "remote", "get-url", "origin")
    if remote != git["remote_url"]:
        raise DateShiftValidationError("origin does not match the scaffold receipt")
    if receipt != build_audit_scaffold_release(repository, git["tag"]):
        raise DateShiftValidationError("audit scaffold receipt drifted")
    _require_detached_annotated_remote_tag(repository, git["tag"], head)
    _validate_static_python(repository, _CLOSURE)
    if receipt["code_closure_sha256"] != closure_digest(repository):
        raise DateShiftValidationError("date-shift code closure drifted")
    bound = receipt["bound_text_sha256"]
    if not isinstance(bound, dict) or set(bound) != set(_BOUND_TEXT):
        raise DateShiftValidationError("scaffold bound-text closure is invalid")
    for relative in _BOUND_TEXT + _CLOSURE:
        path = repository / relative
        if (
            not path.is_file()
            or path.resolve().parent != (repository / relative).resolve().parent
        ):
            raise DateShiftValidationError(f"invalid closure path: {relative}")
        local = path.read_bytes()
        if local != _tracked_bytes(repository, head, relative):
            raise DateShiftValidationError(f"committed blob drifted: {relative}")
        if relative in bound and bound[relative] != bytes_sha256(local):
            raise DateShiftValidationError(f"bound text digest drifted: {relative}")
    verify_imported_sources(repository)
    return {
        "scaffold_tag": git["tag"],
        "scaffold_commit": head,
        "code_closure_sha256": receipt["code_closure_sha256"],
    }
