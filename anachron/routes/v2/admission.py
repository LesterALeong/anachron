"""Fail-closed Routes v2 source and clean-checkout admission primitives."""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
import stat
import subprocess
import sys
from importlib.machinery import PathFinder
from pathlib import Path
from typing import Any

from anachron.routes.v2.schema import load_contract, phase_topics


class AdmissionError(ValueError):
    """Raised when an admission-chain arrow cannot be proven."""


def _is_reparse(path: Path) -> bool:
    """Reject a link without following it or treating a failed stat as absence."""
    try:
        status = path.lstat()
    except OSError as error:
        raise AdmissionError(f"unable to inspect raw artifact path: {path}") from error
    attributes = getattr(status, "st_file_attributes", 0)
    return stat.S_ISLNK(status.st_mode) or bool(attributes & 0x400)


def _repository_root(repository: str | Path) -> Path:
    """Resolve a repository only after every lexical path component is safe."""
    try:
        candidate = Path(os.path.abspath(os.fspath(repository)))
    except (TypeError, ValueError) as error:
        raise AdmissionError("repository path is invalid") from error
    current = Path(candidate.anchor)
    for component in candidate.parts[1:]:
        current = current / component
        if _is_reparse(current):
            raise AdmissionError("repository path traverses a symlink or reparse point")
    try:
        return candidate.resolve(strict=True)
    except OSError as error:
        raise AdmissionError("repository path is unavailable") from error


def phase_raw_artifact_paths(repository: str | Path, phase: str) -> list[Path]:
    """Return only the fixed ignored raw artifacts for one frozen phase."""
    root = _repository_root(repository)
    if phase not in {"development", "pilot", "confirmatory"}:
        raise AdmissionError("raw artifact phase is invalid")
    raw_root = root / "research" / "routes-v2" / "artifacts" / "raw" / phase
    current = root
    for part in raw_root.relative_to(root).parts:
        current = current / part
        if _is_reparse(current):
            raise AdmissionError("raw artifact root traverses a symlink or reparse point")
    try:
        raw_status = raw_root.lstat()
    except OSError as error:
        raise AdmissionError("fixed ignored raw artifact root is unavailable") from error
    if not stat.S_ISDIR(raw_status.st_mode):
        raise AdmissionError("fixed ignored raw artifact root is unavailable")
    paths = []
    for index in range({"development": 6, "pilot": 18, "confirmatory": 36}[phase]):
        path = raw_root / f"routes-v2-{phase}-{index}.json"
        if _is_reparse(path):
            raise AdmissionError("raw artifact is not a regular direct-child file")
        try:
            status = path.lstat()
        except OSError as error:
            raise AdmissionError("raw artifact is not a regular direct-child file") from error
        if not stat.S_ISREG(status.st_mode):
            raise AdmissionError("raw artifact is not a regular direct-child file")
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise AdmissionError("raw artifact cannot be resolved") from error
        if raw_root not in resolved.parents or resolved.parent != raw_root:
            raise AdmissionError("raw artifact escapes its fixed root")
        relative = path.relative_to(root).as_posix()
        if subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative], capture_output=True, check=False).returncode == 0:
            raise AdmissionError("raw artifact must not be tracked")
        if subprocess.run(["git", "-C", str(root), "check-ignore", "--quiet", "--", relative], capture_output=True, check=False).returncode != 0:
            raise AdmissionError("raw artifact must be ignored")
        paths.append(path)
    try:
        observed_names = {path.name for path in raw_root.iterdir()}
    except OSError as error:
        raise AdmissionError("fixed raw artifact root cannot be enumerated") from error
    if observed_names != {path.name for path in paths}:
        raise AdmissionError("fixed raw artifact root contains unexpected files")
    return paths


class ValidatedExecution:
    """Opaque execution evidence that can only be created by admission replay."""

    __slots__ = ("_artifacts", "_contract", "_outcomes", "_private_values", "_source_pairs")

    def __init__(self, token: object, artifacts: dict[str, Any], outcomes: tuple[dict[str, Any], ...], contract: dict[str, Any], private_values: frozenset[str], source_pairs: tuple[dict[str, Any], ...] = ()):
        if token is not _VALIDATED_EXECUTION_TOKEN:
            raise TypeError("ValidatedExecution must be opened by open_validated_execution")
        self._artifacts = artifacts
        self._outcomes = outcomes
        self._contract = contract
        self._private_values = private_values
        self._source_pairs = tuple(json.loads(json.dumps(pair, ensure_ascii=False)) for pair in source_pairs)

    @property
    def artifacts(self) -> dict[str, Any]:
        """Return a detached projection, never a mutable admission authority."""
        return json.loads(json.dumps(self._artifacts, ensure_ascii=False, sort_keys=True))

    @property
    def outcomes(self) -> tuple[dict[str, Any], ...]:
        """Return detached deterministic outcome rows in frozen schedule order."""
        return tuple(json.loads(json.dumps(row, ensure_ascii=False, sort_keys=True)) for row in self._outcomes)


_VALIDATED_EXECUTION_TOKEN = object()


def _private_execution_values(*values: Any) -> frozenset[str]:
    """Retain exact non-public identities needed to sanitize blinded audit text."""
    sensitive: set[str] = set()

    def visit(value: Any, name: str | None = None) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if name == "models" and key in {"id", "digest"} and isinstance(item, str):
                    sensitive.add(item)
                visit(item, key)
            return
        if isinstance(value, list):
            for item in value:
                visit(item, name)
            return
        if isinstance(value, str) and name is not None and (
            name in {"condition", "conditions", "primary_arms", "model", "model_id", "model_digest", "trajectory_id", "run_id", "session_nonce", "oldid", "immutable_url", "citation_id", "presented_document_date", "document_date", "timestamp", "sha256"}
            or name.endswith(("_id", "_sha256", "_url", "_date"))
        ):
            sensitive.add(value)

    for value in values:
        visit(value)
    return frozenset(sensitive)


_V2_ROOTS = (
    "anachron/routes/v2/__init__.py", "anachron/routes/v2/admission.py",
    "anachron/routes/v2/analysis.py", "anachron/routes/v2/curation.py",
    "anachron/routes/v2/human_review.py", "anachron/routes/v2/manifest.py",
    "anachron/routes/v2/retrieval.py", "anachron/routes/v2/runner.py",
    "anachron/routes/v2/runtime.py", "anachron/routes/v2/schema.py",
    "anachron/routes/v2/scoring.py", "anachron/routes/v2/source_integrity.py",
    "anachron/routes/v2/source_excerpt.py", "anachron/routes/v2/sources.py",
    "tools/validate_routes_v2_source_construction.py", "tools/render_routes_results.py",
    "tools/build_routes_v2_paper.py",
)

_V2_BOUND_TEXT_FILES = (
    ".gitattributes",
    "research/routes-v1/sampling_frame.json",
    "research/routes-v2/contract.json",
    "research/routes-v2/sampling_frame.json",
    "research/routes-v2/PROTOCOL.md",
    "paper/routes_v2/routes_v2.tex",
)


def canonical_json_sha256(value: Any) -> str:
    """Hash canonical JSON without relying on another admission-layer module."""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_json_bytes(value: dict[str, Any]) -> bytes:
    """Encode one immutable artifact exactly once."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def write_create_only(path: str | Path, value: dict[str, Any]) -> None:
    """Publish canonical JSON once, fsync it, and reject a different replacement."""
    target = Path(path)
    payload = canonical_json_bytes(value)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError:
        if target.read_bytes() != payload:
            raise AdmissionError("immutable artifact already exists with different bytes")
        return
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise AdmissionError("immutable artifact write was truncated")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if target.read_bytes() != payload:
        raise AdmissionError("immutable artifact write did not round-trip")


def load_json_object(path: str | Path) -> dict[str, Any]:
    """Load one JSON object; unreadable state is never interpreted as absence."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AdmissionError(f"unable to load artifact: {path}") from error
    if not isinstance(value, dict):
        raise AdmissionError("artifact must be an object")
    return value


def _topic_projection(value: dict[str, Any]) -> dict[str, Any]:
    """Project one provenance-bound frame row into the contract's title/year form."""
    return {"title": value.get("title"), "cutoff_year": value.get("cutoff_year")}


def _validate_frame(frame: Any, contract: dict[str, Any], *, repository: str | Path | None = None) -> dict[str, Any]:
    """Validate every v2 row against the tracked v1 parent artifact and pins."""
    if not isinstance(frame, dict) or set(frame) != {"schema_version", "parent_artifact", "phases"}:
        raise AdmissionError("sampling frame schema is invalid")
    if frame["schema_version"] != "routes-v2-exante-sampling-frame" or not isinstance(frame["phases"], dict) or set(frame["phases"]) != {"development", "pilot", "confirmatory"}:
        raise AdmissionError("sampling frame identity drifted")
    root = Path(repository).resolve() if repository is not None else Path(__file__).resolve().parents[3]
    expected_parent_fields = {
        "path", "artifact_sha256", "github_artifact_url", "github_revision", "github_source_sha256",
        "huggingface_artifact_url", "huggingface_resolved_url", "huggingface_revision",
        "huggingface_source_sha256", "huggingface_etag",
    }
    parent_info = frame["parent_artifact"]
    if not isinstance(parent_info, dict) or set(parent_info) != expected_parent_fields or parent_info["path"] != "research/routes-v1/sampling_frame.json":
        raise AdmissionError("sampling frame parent artifact declaration is invalid")
    parent_path = (root / parent_info["path"]).resolve()
    if root not in parent_path.parents:
        raise AdmissionError("sampling frame parent artifact escapes repository")
    try:
        parent_bytes = parent_path.read_bytes()
        parent = json.loads(parent_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AdmissionError("tracked sampling-frame parent is unavailable") from error
    if parent_info["artifact_sha256"] != "sha256:" + hashlib.sha256(parent_bytes).hexdigest():
        raise AdmissionError("sampling frame parent artifact hash drifted")
    expected_pins = {key: parent.get(key) for key in expected_parent_fields - {"path", "artifact_sha256"}}
    if any(not isinstance(value, str) or parent_info[key] != value for key, value in expected_pins.items()):
        raise AdmissionError("sampling frame parent source pins drifted")
    parent_topics = parent.get("topics") if isinstance(parent, dict) else None
    if not isinstance(parent_topics, list):
        raise AdmissionError("sampling frame parent topic inventory is invalid")
    if any([_topic_projection(item) for item in frame["phases"][phase]] != phase_topics(contract, phase) for phase in frame["phases"]):
        raise AdmissionError("sampling frame phase items drifted")
    identities: list[tuple[str, int]] = []
    indexes: set[int] = set()
    for phase in ("development", "pilot", "confirmatory"):
        for item in frame["phases"][phase]:
            if not isinstance(item, dict) or set(item) != {"title", "cutoff_year", "parent_row_index", "parent_row_sha256"} or not isinstance(item["parent_row_index"], int) or isinstance(item["parent_row_index"], bool) or not 0 <= item["parent_row_index"] < len(parent_topics):
                raise AdmissionError("sampling frame membership proof is invalid")
            parent_row = parent_topics[item["parent_row_index"]]
            parent_sha = "sha256:" + hashlib.sha256(json.dumps(parent_row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            if item["parent_row_sha256"] != parent_sha or _topic_projection(item) != parent_row:
                raise AdmissionError("sampling frame row does not exactly belong to parent")
            indexes.add(item["parent_row_index"])
            identities.append((item["title"], item["cutoff_year"]))
    if len(identities) != 60 or len(set(identities)) != 60:
        raise AdmissionError("sampling frame phase items are not exactly 60 unique title/year records")
    if len(indexes) != 60:
        raise AdmissionError("sampling frame parent membership proofs are duplicated")
    if canonical_json_sha256(frame) != contract["sampling_frame_sha256"]:
        raise AdmissionError("sampling frame hash does not match contract")
    return frame


def _raw_revision(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise AdmissionError("raw revision is invalid")
    revision_id = value.get("revision_id")
    url, timestamp, content = value.get("revision_url"), value.get("timestamp"), value.get("content")
    if not isinstance(revision_id, int) or revision_id <= 0 or not isinstance(url, str) or f"oldid={revision_id}" not in url or not isinstance(timestamp, str) or not timestamp.endswith("Z") or not isinstance(content, str):
        raise AdmissionError("raw revision lacks immutable oldid/content evidence")
    content_sha256 = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    if value.get("content_sha256") != content_sha256:
        raise AdmissionError("raw revision content hash is invalid")
    return {"oldid": str(revision_id), "immutable_url": url, "timestamp": timestamp, "content_sha256": content_sha256}


def revalidate_raw_source(*, contract_path: str | Path, sampling_frame_path: str | Path, raw_artifact_path: str | Path, phase: str, item_id: str, output_path: str | Path, predecessor_evidence: Any = None) -> dict[str, Any]:
    """Derive a v2 receipt from one ignored raw discovery artifact, once."""
    contract = load_contract(contract_path)
    predecessor = validate_phase_predecessor(predecessor_evidence, phase=phase)
    frame = _validate_frame(load_json_object(sampling_frame_path), contract)
    raw = load_json_object(raw_artifact_path)
    if raw.get("schema_version") != "routes-v1-source-discovery":
        raise AdmissionError("only a raw v1 discovery artifact can be revalidated")
    if phase not in {"development", "pilot", "confirmatory"}:
        raise AdmissionError("revalidation phase is invalid")
    expected_items = {f"routes-v2:{phase}:{index}": topic for index, topic in enumerate(frame["phases"][phase])}
    topic = expected_items.get(item_id)
    if topic is None or raw.get("title") != topic["title"] or raw.get("cutoff_year") != topic["cutoff_year"]:
        raise AdmissionError("raw artifact does not match frozen frame item")
    pre, post = _raw_revision(raw.get("strict_revision")), _raw_revision(raw.get("post_snapshot"))
    cutoff = f"{topic['cutoff_year']}-12-31T23:59:59Z"
    if pre["timestamp"] > cutoff or post["timestamp"] <= cutoff:
        raise AdmissionError("raw revision timestamps violate cutoff directions")
    receipt = {
        "schema_version": "routes-v2-source-revalidation",
        "contract_sha256": canonical_json_sha256(contract),
        "sampling_frame_sha256": canonical_json_sha256(frame),
        "predecessor_evidence_sha256": predecessor.get("evidence_sha256"),
        "study_phase": phase, "item_id": item_id, "title": topic["title"], "cutoff_year": topic["cutoff_year"],
        "raw_discovery_artifact_sha256": canonical_json_sha256(raw),
        "revalidator_code_closure_sha256": "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "pre": pre, "post": post,
    }
    receipt["receipt_sha256"] = canonical_json_sha256(receipt)
    validated = validate_revalidation_receipt(receipt, contract=contract, sampling_frame=frame)
    write_create_only(output_path, validated)
    return validated


def validate_revalidation_receipt(receipt: Any, *, contract: dict[str, Any], sampling_frame: dict[str, Any]) -> dict[str, Any]:
    """Validate every immutable binding needed by a later v2 source pair."""
    frame = _validate_frame(sampling_frame, contract)
    fields = {"schema_version", "contract_sha256", "sampling_frame_sha256", "predecessor_evidence_sha256", "study_phase", "item_id", "title", "cutoff_year", "raw_discovery_artifact_sha256", "revalidator_code_closure_sha256", "pre", "post", "receipt_sha256"}
    if not isinstance(receipt, dict) or set(receipt) != fields or receipt["schema_version"] != "routes-v2-source-revalidation":
        raise AdmissionError("revalidation receipt schema is invalid")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if receipt["receipt_sha256"] != canonical_json_sha256(unsigned) or receipt["contract_sha256"] != canonical_json_sha256(contract) or receipt["sampling_frame_sha256"] != canonical_json_sha256(frame):
        raise AdmissionError("revalidation receipt self/contract/frame binding drifted")
    phase = receipt["study_phase"]
    if phase == "development" and receipt["predecessor_evidence_sha256"] is not None:
        raise AdmissionError("development revalidation cannot bind predecessor evidence")
    if phase in {"pilot", "confirmatory"} and (not isinstance(receipt["predecessor_evidence_sha256"], str) or len(receipt["predecessor_evidence_sha256"]) != 71):
        raise AdmissionError("downstream revalidation lacks predecessor evidence binding")
    prefix = f"routes-v2:{phase}:"
    index = receipt["item_id"].removeprefix(prefix) if isinstance(receipt["item_id"], str) else ""
    if phase not in frame["phases"] or not index.isdigit() or int(index) >= len(frame["phases"][phase]) or _topic_projection(frame["phases"][phase][int(index)]) != {"title": receipt["title"], "cutoff_year": receipt["cutoff_year"]}:
        raise AdmissionError("revalidation receipt item does not belong to frame")
    for arm in ("pre", "post"):
        value = receipt[arm]
        if not isinstance(value, dict) or set(value) != {"oldid", "immutable_url", "timestamp", "content_sha256"} or not isinstance(value["oldid"], str) or not value["oldid"].isdigit() or f"oldid={value['oldid']}" not in str(value["immutable_url"]) or not isinstance(value["timestamp"], str) or not value["timestamp"].endswith("Z") or not isinstance(value["content_sha256"], str) or len(value["content_sha256"]) != 71:
            raise AdmissionError("revalidation receipt revision binding is invalid")
    cutoff = f"{receipt['cutoff_year']}-12-31T23:59:59Z"
    if receipt["pre"]["timestamp"] > cutoff or receipt["post"]["timestamp"] <= cutoff:
        raise AdmissionError("revalidation receipt timestamps violate cutoff directions")
    return receipt


def _module_path(root: Path, name: str) -> Path | None:
    relative = Path(*name.split("."))
    module, package = root / relative.with_suffix(".py"), root / relative / "__init__.py"
    return module if module.is_file() else package if package.is_file() else None


def _imported_local_modules(tree: ast.AST, path: Path, root: Path) -> set[Path]:
    package = ".".join(path.relative_to(root).with_suffix("").parts[:-1])
    modules: set[Path] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ((isinstance(node.func, ast.Name) and node.func.id == "__import__") or (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module" and not (isinstance(node.func.value, ast.Name) and node.func.value.id == "importlib"))):
            raise AdmissionError(f"dynamic import is forbidden: {path}")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "sys" and node.attr == "path":
            raise AdmissionError(f"import-path mutation is forbidden: {path}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            raise AdmissionError(f"unlisted executable local read is forbidden: {path}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"read_bytes", "read_text"} and isinstance(node.func.value, ast.Call) and isinstance(node.func.value.func, ast.Name) and node.func.value.func.id == "Path" and node.func.value.args and isinstance(node.func.value.args[0], ast.Constant) and isinstance(node.func.value.args[0].value, str):
            raise AdmissionError(f"unlisted executable local read is forbidden: {path}")
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                pieces = package.split(".") if package else []
                if node.level - 1 > len(pieces):
                    raise AdmissionError(f"unresolved relative import: {path}")
                base = ".".join(pieces[: len(pieces) - node.level + 1] + ([base] if base else []))
            names = [base] if base else []
            if base.startswith(("anachron", "tools")):
                for alias in node.names:
                    candidate = _module_path(root, f"{base}.{alias.name}")
                    if candidate is not None:
                        modules.add(candidate)
        else:
            continue
        for name in names:
            if name.startswith(("anachron", "tools")):
                module = _module_path(root, name)
                if module is None:
                    raise AdmissionError(f"unresolved local import: {name} from {path}")
                modules.add(module)
    return modules


def _assert_lf_byte_governance(root: Path, relative_paths: set[str]) -> None:
    """Require explicit LF checkout governance for every byte-bound text path."""
    attributes = root / ".gitattributes"
    if not attributes.is_file():
        raise AdmissionError("byte-governance attributes file is missing")
    try:
        output = subprocess.run(
            ["git", "-C", str(root), "check-attr", "text", "eol", "--", *sorted(relative_paths)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError as error:
        raise AdmissionError("unable to inspect Git byte-governance attributes") from error
    values: dict[str, dict[str, str]] = {}
    for line in output.splitlines():
        parts = line.split(": ", 2)
        if len(parts) != 3:
            raise AdmissionError("Git returned malformed byte-governance attributes")
        relative, attribute, value = parts
        values.setdefault(relative.replace("\\", "/"), {})[attribute] = value
    for relative in relative_paths:
        expected = values.get(relative)
        if expected is None or expected.get("text") != "set" or expected.get("eol") != "lf":
            raise AdmissionError(f"byte-governance attributes must declare text eol=lf: {relative}")


def _validate_closure_lock(closure_lock: Any) -> dict[str, Any]:
    """Validate the executable closure and its governing attribute-file identity."""
    required = {"schema_version", "files", "bound_text_files", "attributes_sha256", "closure_sha256"}
    if not isinstance(closure_lock, dict) or set(closure_lock) != required or closure_lock["schema_version"] != "routes-v2-code-closure" or not isinstance(closure_lock["files"], dict) or not isinstance(closure_lock["bound_text_files"], dict) or not isinstance(closure_lock["attributes_sha256"], str):
        raise AdmissionError("closure lock schema is invalid")
    if set(closure_lock["bound_text_files"]) != set(_V2_BOUND_TEXT_FILES):
        raise AdmissionError("closure lock does not bind every exact-bound text artifact")
    if closure_lock["attributes_sha256"] != closure_lock["bound_text_files"][".gitattributes"]:
        raise AdmissionError("closure lock attributes hash does not match its bound artifact")
    unsigned = {
        "schema_version": closure_lock["schema_version"],
        "files": closure_lock["files"],
        "bound_text_files": closure_lock["bound_text_files"],
        "attributes_sha256": closure_lock["attributes_sha256"],
    }
    if closure_lock["closure_sha256"] != canonical_json_sha256(unsigned):
        raise AdmissionError("closure lock aggregate hash is invalid")
    return closure_lock


def build_code_closure(repository: str | Path, *, roots: tuple[str, ...] = _V2_ROOTS) -> dict[str, Any]:
    """Build the AST-resolved local executable closure and its deterministic hash."""
    root, pending, files = Path(repository).resolve(), [], set()
    for relative in roots:
        pending.append(root / relative)
    while pending:
        path = pending.pop().resolve()
        if not path.is_file() or root not in path.parents:
            raise AdmissionError(f"closure root is missing or escapes repository: {path}")
        if path in files:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError) as error:
            raise AdmissionError(f"unable to parse closure source: {path}") from error
        files.add(path)
        pending.extend(_imported_local_modules(tree, path, root))
        parent = path.parent
        while parent != root:
            init = parent / "__init__.py"
            if init.is_file():
                pending.append(init)
            parent = parent.parent
    entries = {path.relative_to(root).as_posix(): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(files)}
    _assert_lf_byte_governance(root, set(entries) | set(_V2_BOUND_TEXT_FILES))
    bound_text_files = {
        relative: "sha256:" + hashlib.sha256((root / relative).read_bytes()).hexdigest()
        for relative in _V2_BOUND_TEXT_FILES
    }
    attributes_sha256 = bound_text_files[".gitattributes"]
    closure = {
        "schema_version": "routes-v2-code-closure",
        "files": entries,
        "bound_text_files": bound_text_files,
        "attributes_sha256": attributes_sha256,
    }
    closure["closure_sha256"] = canonical_json_sha256(closure)
    return closure


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def _module_name(relative: str) -> str:
    path = Path(relative)
    if path.suffix != ".py":
        raise AdmissionError("closure module path is not Python source")
    parts = path.with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _pathfinder_module_file(module_name: str, root: Path) -> Path:
    """Resolve one dotted module through PathFinder without imported-module cache state."""
    search_paths: list[str] = [str(root)]
    spec = None
    for position, part in enumerate(module_name.split(".")):
        spec = PathFinder.find_spec(part, search_paths)
        if spec is None:
            raise AdmissionError(f"PathFinder cannot resolve closure module: {module_name}")
        if position + 1 < len(module_name.split(".")):
            if spec.submodule_search_locations is None:
                raise AdmissionError(f"PathFinder resolved non-package closure prefix: {module_name}")
            search_paths = list(spec.submodule_search_locations)
    if spec is None or spec.origin in {None, "built-in", "frozen"}:
        raise AdmissionError(f"PathFinder did not resolve a file-backed closure module: {module_name}")
    return Path(spec.origin).resolve()


def validate_loaded_code_closure(repository: str | Path, closure_lock: dict[str, Any]) -> None:
    """Bind already loaded runtime modules to the exact admitted Git closure."""
    root = Path(repository).resolve()
    checked_closure = _validate_closure_lock(closure_lock)
    files = checked_closure["files"]
    importlib.invalidate_caches()
    for relative, digest in files.items():
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise AdmissionError("closure module entry is invalid")
        expected = (root / relative).resolve()
        if root not in expected.parents or not expected.is_file():
            raise AdmissionError("closure module path escapes admitted repository")
        module_name = _module_name(relative)
        resolved = _pathfinder_module_file(module_name, root)
        if resolved != expected:
            raise AdmissionError("PathFinder module resolution differs from admitted closure")
        module = sys.modules.get(module_name)
        if module is None:
            module = importlib.import_module(module_name)
        loaded_file = getattr(module, "__file__", None)
        if not isinstance(loaded_file, str) or Path(loaded_file).resolve() != expected:
            raise AdmissionError(f"loaded closure module is not imported from the admitted repository: {module_name} ({loaded_file!r} != {expected})")
        source = expected.read_bytes()
        blob = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{relative}"], check=True, capture_output=True).stdout
        if "sha256:" + hashlib.sha256(source).hexdigest() != digest or blob != source:
            raise AdmissionError("loaded closure module bytes differ from admitted Git blob")


def admit_clean_checkout(repo: str | Path, freeze_receipt: dict[str, Any], closure_lock: dict[str, Any]) -> None:
    """Admit only an exact clean, pushed checkout with Git-blob-verified closure."""
    root = Path(repo).resolve()
    if _git(root, "status", "--porcelain"):
        raise AdmissionError("repository is dirty or has untracked files")
    required = {"schema_version", "study_phase", "commit", "tree", "branch", "remote", "closure_sha256"}
    if set(freeze_receipt) != required or freeze_receipt["schema_version"] != "routes-v2-freeze-receipt" or freeze_receipt["study_phase"] not in {"development", "pilot", "confirmatory"}:
        raise AdmissionError("freeze receipt schema is invalid")
    if _git(root, "rev-parse", "HEAD") != freeze_receipt["commit"] or _git(root, "rev-parse", "HEAD^{tree}") != freeze_receipt["tree"] or _git(root, "config", "--get", "remote.origin.url") != freeze_receipt["remote"]:
        raise AdmissionError("checkout does not match frozen commit/tree/origin")
    checked_closure = _validate_closure_lock(closure_lock)
    if checked_closure["closure_sha256"] != freeze_receipt["closure_sha256"]:
        raise AdmissionError("closure lock hash does not match freeze receipt")
    remote = _git(root, "ls-remote", "origin", f"refs/heads/{freeze_receipt['branch']}").split()
    if len(remote) != 2 or remote[0] != freeze_receipt["commit"]:
        raise AdmissionError("configured remote branch is not exactly at frozen commit")
    rebuilt = build_code_closure(root)
    if rebuilt != checked_closure:
        raise AdmissionError("transitive executable code closure drifted")
    for relative, digest in checked_closure["bound_text_files"].items():
        bound_path = (root / relative).resolve()
        if root not in bound_path.parents or not bound_path.is_file():
            raise AdmissionError("exact-bound text artifact escapes admitted repository")
        working = bound_path.read_bytes()
        blob = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{relative}"], check=True, capture_output=True).stdout
        if "sha256:" + hashlib.sha256(working).hexdigest() != digest or blob != working:
            raise AdmissionError("exact-bound text artifact differs from frozen Git blob")
    for relative, digest in checked_closure["files"].items():
        if not isinstance(relative, str) or not isinstance(digest, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise AdmissionError("closure lock path is invalid")
        working = (root / relative).read_bytes()
        blob = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{relative}"], check=True, capture_output=True).stdout
        if "sha256:" + hashlib.sha256(working).hexdigest() != digest or blob != working:
            raise AdmissionError("closure file differs from frozen Git blob")


def write_phase_result_evidence(path: str | Path, result: Any, *, replay_root: str | Path, frozen_root: str | Path) -> dict[str, Any]:
    """Create the reloadable, self-hashed predecessor evidence required by later phases."""
    from anachron.routes.v2.analysis import validate_finite_set_result

    phase = getattr(result, "value", {}).get("phase") if hasattr(result, "value") else None
    if phase not in {"development", "pilot"}:
        raise AdmissionError("only development or pilot may become predecessor evidence")
    value = validate_finite_set_result(result, expected_phase=phase)
    from anachron.routes.v2.analysis import replay_phase_root

    replayed, replay_receipt = replay_phase_root(replay_root, frozen_root, phase=phase)
    if replayed.value != value:
        raise AdmissionError("phase result evidence does not match the replayed reducer output")
    evidence = {
        "schema_version": "routes-v2-phase-result-evidence",
        "phase": phase,
        "replay_root": str(Path(replay_root).resolve()),
        "frozen_root": str(Path(frozen_root).resolve()),
        "replay_receipt_sha256": replay_receipt["receipt_sha256"],
        "result": value,
        "result_sha256": value["result_sha256"],
        "execution_artifacts_sha256": value["execution_artifacts_sha256"],
        "audit_report_sha256": value["audit_report_sha256"],
    }
    evidence["evidence_sha256"] = canonical_json_sha256(evidence)
    write_create_only(path, evidence)
    return validate_phase_predecessor(load_json_object(path), phase="pilot" if phase == "development" else "confirmatory")


def validate_phase_predecessor(evidence: Any, *, phase: str) -> dict[str, Any]:
    """Reload and validate a positive predecessor result at every downstream boundary."""
    if phase == "development":
        if evidence is not None:
            raise AdmissionError("development cannot carry predecessor evidence")
        return {}
    required_phase = "development" if phase == "pilot" else "pilot" if phase == "confirmatory" else None
    required = {"schema_version", "phase", "replay_root", "frozen_root", "replay_receipt_sha256", "result", "result_sha256", "execution_artifacts_sha256", "audit_report_sha256", "evidence_sha256"}
    if required_phase is None or not isinstance(evidence, dict) or set(evidence) != required:
        raise AdmissionError("downstream phase requires a complete predecessor evidence artifact")
    unsigned = {key: value for key, value in evidence.items() if key != "evidence_sha256"}
    result = evidence["result"]
    result_unsigned = {key: value for key, value in result.items() if key != "result_sha256"} if isinstance(result, dict) else {}
    if evidence["evidence_sha256"] != canonical_json_sha256(unsigned) or evidence["phase"] != required_phase or not isinstance(result, dict) or result.get("result_sha256") != canonical_json_sha256(result_unsigned):
        raise AdmissionError("predecessor evidence hash or phase binding drifted")
    if evidence["result_sha256"] != result["result_sha256"] or evidence["execution_artifacts_sha256"] != result.get("execution_artifacts_sha256") or evidence["audit_report_sha256"] != result.get("audit_report_sha256"):
        raise AdmissionError("predecessor evidence projections drifted")
    if result.get("result_mode") != "positive" or not isinstance(result.get("gates"), dict) or not result["gates"] or not all(result["gates"].values()):
        raise AdmissionError("predecessor evidence does not contain a positive complete result")
    if required_phase == "development" and (not isinstance(result.get("paired_misdated_minus_truthful"), (int, float)) or result["paired_misdated_minus_truthful"] < 0.25):
        raise AdmissionError("development predecessor did not meet the frozen 0.25 threshold")
    try:
        from anachron.routes.v2.analysis import replay_phase_root

        replayed, replay_receipt = replay_phase_root(evidence["replay_root"], evidence["frozen_root"], phase=required_phase)
    except (OSError, ValueError, TypeError) as error:
        raise AdmissionError("predecessor evidence replay failed") from error
    if replayed.value != result or replay_receipt.get("receipt_sha256") != evidence["replay_receipt_sha256"]:
        raise AdmissionError("predecessor evidence does not replay to its stored result")
    return evidence


def open_validated_execution(
    *,
    phase: str,
    prerequisite_result: Any = None,
    repository: str | Path,
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
    pending_draft: dict[str, Any],
    source_decisions: dict[str, Any],
    source_gate: dict[str, Any],
    manifest: dict[str, Any],
    freeze_receipt: dict[str, Any],
    closure_lock: dict[str, Any],
    schedule: dict[str, Any],
    session_calibration_receipts: list[dict[str, Any]],
    journal_path: str | Path,
) -> ValidatedExecution:
    """Replay the complete source-to-response chain into guarded finite evidence.

    This is intentionally the only public constructor for ``ValidatedExecution``.
    It consumes retained response bytes and derives labels itself; callers cannot
    provide an outcome ledger, effect, or claimed result mode.
    """
    from anachron.routes.v2.manifest import (
        ManifestValidationError,
        source_gate_receipt,
        validate_manifest,
        validate_pending_draft,
    )
    from anachron.routes.v2.retrieval import delivered_evidence_sha256, delivery_packet
    from anachron.routes.v2.runner import (
        ExecutionJournal,
        RunnerValidationError,
        validate_schedule,
    )
    from anachron.routes.v2.runtime import (
        RuntimeValidationError,
        TransportResult,
        build_request,
        classify_response,
        validate_bytes_receipt,
        validate_session_calibration,
    )
    from anachron.routes.v2.schema import validate_contract

    try:
        checked_contract = validate_contract(contract)
        checked_frame = _validate_frame(sampling_frame, checked_contract)
        receipts = {receipt["item_id"]: receipt for receipt in pending_draft.get("revalidation_receipts", []) if isinstance(receipt, dict) and isinstance(receipt.get("item_id"), str)}
        excerpts = {(receipt["item_id"], receipt["arm"]): receipt for receipt in pending_draft.get("excerpt_receipts", []) if isinstance(receipt, dict) and isinstance(receipt.get("item_id"), str) and receipt.get("arm") in {"pre", "post"}}
        predecessor_evidence = manifest.get("predecessor_evidence") if isinstance(manifest, dict) else None
        checked_draft = validate_pending_draft(
            pending_draft,
            repository=repository,
            contract=checked_contract,
            sampling_frame=checked_frame,
            revalidation_receipts=receipts,
            excerpt_receipts=excerpts,
            phase=phase,
            predecessor_evidence=predecessor_evidence,
        )
        expected_gate = source_gate_receipt(
            draft=checked_draft,
            source_decisions=source_decisions,
            repository=repository,
            contract=checked_contract,
            sampling_frame=checked_frame,
            revalidation_receipts=receipts,
            excerpt_receipts=excerpts,
            phase=phase,
            predecessor_evidence=predecessor_evidence,
        )
        if source_gate != expected_gate or source_gate.get("status") != "PASS" or source_gate.get("study_phase") != phase:
            raise AdmissionError("source gate is not the exact PASS receipt for the pending draft")
        checked_manifest = validate_manifest(manifest, checked_contract, repository=repository)
        if checked_manifest.get("study_phase") != phase or checked_manifest.get("source_gate_receipt") != source_gate or checked_manifest.get("pending_draft_sha256") != canonical_json_sha256(checked_draft) or freeze_receipt.get("study_phase") != phase:
            raise AdmissionError("manifest does not bind the admitted source gate and pending draft")
        from anachron.routes.v2.runner import _require_phase_prerequisite
        _require_phase_prerequisite(phase, prerequisite_result, predecessor_evidence=predecessor_evidence)
        admit_clean_checkout(repository, freeze_receipt, closure_lock)
        validate_loaded_code_closure(repository, closure_lock)
        checked_schedule = validate_schedule(
            schedule,
            checked_manifest,
            checked_contract,
            source_gate=source_gate,
            freeze_receipt=freeze_receipt,
            closure_lock=closure_lock,
        )
        if not isinstance(session_calibration_receipts, list) or not session_calibration_receipts:
            raise AdmissionError("at least one retained session calibration receipt is required")
        calibrations: dict[str, dict[str, Any]] = {}
        for receipt in session_calibration_receipts:
            if not isinstance(receipt, dict) or receipt.get("schema_version") != "routes-v2-session-calibration-receipt":
                raise AdmissionError("session calibration receipt schema is invalid")
            try:
                validate_session_calibration(
                    receipt,
                    checked_contract,
                    inventory=receipt.get("inventory"),
                    client_binding=receipt.get("client_binding"),
                    closure_sha256=checked_schedule["closure_sha256"],
                    session_nonce=receipt.get("session_nonce"),
                    model_id=receipt.get("model_id"),
                )
            except RuntimeValidationError as error:
                raise AdmissionError("session calibration receipt binding drifted") from error
            if receipt["receipt_sha256"] in calibrations:
                raise AdmissionError("session calibration receipts are duplicated")
            calibrations[receipt["receipt_sha256"]] = receipt
        with ExecutionJournal(journal_path, checked_schedule) as journal:
            records = tuple(journal.records)
    except (ManifestValidationError, RunnerValidationError, RuntimeValidationError, AdmissionError) as error:
        raise AdmissionError(f"validated execution admission failed: {error}") from error

    claims: dict[str, dict[str, Any]] = {}
    outcomes: dict[int, dict[str, Any]] = {}
    for record in records:
        if record["record_type"] == "dispatch_claim":
            claims[record["record_sha256"]] = record
            continue
        claim = claims.get(record["claim_record_sha256"])
        if claim is None:
            raise AdmissionError("terminal outcome is missing its retained dispatch claim")
        trajectory = claim["trajectory"]
        item_id = trajectory["item_id"]
        packet = delivery_packet(checked_manifest, checked_contract, item_id=item_id, condition=trajectory["condition"])
        request = build_request(packet, checked_contract, model_id=trajectory["model_id"], seed=trajectory["seed"])
        if validate_bytes_receipt(claim["request"]) != json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"):
            raise AdmissionError("retained request bytes do not replay from sealed inputs")
        expected_delivery = {
            "packet_sha256": canonical_json_sha256(packet),
            "model_visible_packet_sha256": canonical_json_sha256(packet),
            "delivered_evidence_sha256": delivered_evidence_sha256(packet),
        }
        if claim["delivery"] != expected_delivery:
            raise AdmissionError("retained delivery receipt does not replay from the sealed manifest")
        calibration_sha = claim["admission"].get("calibration_sha256")
        if calibration_sha not in calibrations:
            raise AdmissionError("dispatch claim lacks its retained session calibration receipt")
        calibration = calibrations[calibration_sha]
        if calibration["model_id"] != trajectory["model_id"] or calibration["session_nonce"] != claim["session_nonce"] or calibration["model_digest"] != trajectory["model_digest"]:
            raise AdmissionError("dispatch claim does not bind calibration for its own model and session")
        response_bytes = validate_bytes_receipt(record["response"])
        result = TransportResult(record["status"], response_bytes, record["response_object_exists"], None if record["error"]["kind"] == "none" else record["error"]["kind"])
        pairs = [pair for pair in checked_manifest["pairs"] if pair["item_id"] == item_id]
        if len(pairs) != 1:
            raise AdmissionError("sealed manifest does not identify one replayed source pair")
        pair = pairs[0]
        replayed = classify_response(
            result,
            requested_model=trajectory["model_id"],
            answer_rules={
                "pre_aliases": pair["pre_aliases"],
                "post_aliases": pair["post_aliases"],
                "abstention_aliases": checked_manifest["answer_rules"]["abstention_aliases"],
            },
            expected_citation_id=packet["document"]["citation_id"],
        )
        if replayed["status"] != record["status"] or replayed["response"] != record["response"] or replayed["envelope_valid"] != record["envelope_valid"] or replayed["score"] != record["score"]:
            raise AdmissionError("retained response bytes do not replay to the claimed terminal outcome")
        if record["trace_valid"] != (replayed["status"] == "ok" and replayed["envelope_valid"] and replayed["score"] is not None):
            raise AdmissionError("terminal trace validity is not derived from replayed response bytes")
        if record["status"] == "transport_failure_no_response_object":
            continue
        index = trajectory["schedule_index"]
        if index in outcomes:
            raise AdmissionError("a schedule trajectory has more than one final response")
        score = replayed["score"] or {"answer_label": "invalid_output", "post_only": 0}
        outcomes[index] = {
            "trajectory_id": trajectory["trajectory_id"],
            "study_phase": phase,
            "topic_id": trajectory["item_id"],
            "condition": trajectory["condition"],
            "model_id": trajectory["model_id"],
            "seed": trajectory["seed"],
            "status": replayed["status"],
            "response": record["response"],
            "machine_label": score["answer_label"],
            "post_only": score["post_only"],
            "request_sha256": claim["request"]["sha256"],
            "delivery_sha256": claim["delivery"]["packet_sha256"],
            "response_sha256": record["response"]["sha256"],
            "terminal_record_sha256": record["record_sha256"],
        }
    if set(outcomes) != set(range(len(checked_schedule["trajectories"]))):
        raise AdmissionError("journal is not a complete finite schedule outcome set")
    artifacts = {
        "contract_sha256": canonical_json_sha256(checked_contract),
        "sampling_frame_sha256": canonical_json_sha256(checked_frame),
        "pending_draft_sha256": canonical_json_sha256(checked_draft),
        "source_decisions_sha256": canonical_json_sha256(source_decisions),
        "source_gate_sha256": canonical_json_sha256(source_gate),
        "manifest_sha256": canonical_json_sha256(checked_manifest),
        "freeze_receipt_sha256": canonical_json_sha256(freeze_receipt),
        "schedule_sha256": canonical_json_sha256(checked_schedule),
        "calibration_receipts_sha256": canonical_json_sha256(session_calibration_receipts),
        "journal_sha256": "sha256:" + hashlib.sha256(Path(journal_path).read_bytes()).hexdigest(),
        "study_phase": phase,
    }
    return ValidatedExecution(
        _VALIDATED_EXECUTION_TOKEN,
        artifacts,
        tuple(outcomes[index] for index in sorted(outcomes)),
        checked_contract,
        _private_execution_values(
            checked_contract,
            checked_manifest,
            checked_schedule,
            records,
            list(calibrations.values()),
            artifacts,
        ),
        tuple(checked_manifest["pairs"]),
    )
