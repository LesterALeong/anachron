"""Sealed v3 trace-measurement runner for a finite synthetic panel.

The protocol records tool-use traces, not answer quality. One request contains
the tool schema and the final request omits ``tools``. Two transcript-shaped
calibrations are recorded outside scientific metrics before any science call.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from anachron.core.leakage import ToolInteraction, score_interactions
from anachron.data.v3_corpus import format_search_results, get_v3_corpus, search_v3
from anachron.data.v3_samples import V3Sample, get_v3_samples, v3_samples_by_id

_MODES = ("unrestricted", "enforced")
_TOOL_NAME = "anachron_search"
_PROTOCOL_VERSION = "v3-measurement-protocol-v1"
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_APPROVED_MODELS = (
    ("qwen2.5:7b", "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"),
    ("qwen3:14b-q4_K_M", "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8"),
)
_CONTROLS = ("fin-acme-2021-01-future", "fin-borealis-2020-06-survivorship", "fin-cygnus-2022-06-future-survivorship", "fin-delta-2021-06-restatement", "gen-eclipse-2017-01-future", "gen-industrial-2023-04-restatement")
_FALSIFIER_IDS = ("fin-acme-2022-01-future", "fin-borealis-2019-01-future", "fin-delta-2022-01-restatement-current", "gen-port-2019-01-future", "gen-industrial-2024-01-restatement-current", "fin-equinox-2021-06-delisted-before-cutoff")
# ``*_plan.json`` deliberately remains outside this closure: it binds these
# hashes, so including it would create a self-referential digest.  Every other
# executable or explanatory protocol input is governed.
_GOVERNED_CLOSURE = {
    ".gitattributes",
    "anachron/core/leakage.py",
    "anachron/data/v3_corpus.py",
    "anachron/data/v3_samples.py",
    "anachron/v3_measurement.py",
    "research/v3_measurement/ACCEPTANCE_MATRIX.md",
    "research/v3_measurement/CLAIM_EVIDENCE_MAP.md",
    "research/v3_measurement/PROTOCOL.md",
    "research/v3_measurement/README.md",
    "research/v3_measurement/full_go.template.json",
    "tools/analyze_v3_measurement.py",
    "tools/run_v3_measurement.py",
    "tools/seal_v3_falsifier_receipt.py",
}
_FULL_GO_KIND = "anachron-v3-full-measurement-authorization"
_FULL_GO_STATEMENT = "I authorize this exact frozen full v3 measurement plan after reviewing the bound passing falsifier receipt."
_FROZEN_PLAN_CONTRACTS = {
    "scientific-falsifier": {
        "plan_id": "anachron-v3-scientific-falsifier-2026-09-03",
        "acceptance": {"minimum_pooled_reduction": 0.2},
        "preconditions": {
            "requires_falsifier_evidence": False,
            "requires_falsifier_receipt": False,
            "requires_human_go": False,
        },
    },
    "full-primary": {
        "plan_id": "anachron-v3-full-primary-2026-09-03",
        "acceptance": {"minimum_pooled_reduction": 0.2},
        "preconditions": {
            "requires_falsifier_evidence": True,
            "requires_falsifier_receipt": True,
            "requires_human_go": True,
        },
    },
}
_REPARSE = 0x400
_PREREQUISITE_PATH_BUDGET = 240
_EVIDENCE_README = b"# Sealed v3 measurement evidence\n\nVerify with `python -m tools.analyze_v3_measurement <directory>`.\n"


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _require_json_type(value: object, expected: type, label: str) -> None:
    if type(value) is not expected:
        raise ValueError(f"{label} has the wrong JSON type")


def _require_json_mapping(value: object, keys: set[str], label: str) -> dict[str, Any]:
    _require_json_type(value, dict, label)
    if set(value) != keys:
        raise ValueError(f"{label} has unexpected or missing fields")
    return value


def _require_json_list(value: object, label: str) -> list[Any]:
    _require_json_type(value, list, label)
    return value


def _require_json_shape(value: object, expected: object, label: str) -> None:
    if type(value) is not type(expected):
        raise ValueError(f"{label} has the wrong JSON type")
    if isinstance(expected, dict):
        _require_json_mapping(value, set(expected), label)
        for key, child in expected.items():
            _require_json_shape(value[key], child, f"{label}.{key}")
    elif isinstance(expected, list):
        actual = _require_json_list(value, label)
        if len(actual) != len(expected):
            raise ValueError(f"{label} has the wrong JSON length")
        for index, (item, child) in enumerate(zip(actual, expected)):
            _require_json_shape(item, child, f"{label}[{index}]")


def _strict_json_loads(raw: bytes, label: str) -> object:
    """Decode one untrusted JSON document without Python's permissive aliases.

    ``json.loads`` normally accepts duplicate keys and NaN/Infinity.  A
    re-signed evidence tree must not be able to turn either ambiguity into a
    different semantic object after its bytes were accepted.
    """

    def reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains a duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_nonfinite(value: str) -> object:
        raise ValueError(f"{label} contains a non-finite JSON constant: {value}")

    def reject_overflow(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"{label} contains a non-finite JSON number: {value}")
        return parsed

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=reject_nonfinite,
            parse_float=reject_overflow,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from error


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_file(path: Path) -> str:
    return _hash_bytes(path.read_bytes())


def _runtime_identity() -> dict[str, Any]:
    return {"implementation": "CPython", "major": sys.version_info.major, "minor": sys.version_info.minor, "version": sys.version.split()[0]}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_files(root: Path, label: str) -> set[str]:
    metadata = os.lstat(root)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & _REPARSE:
        raise ValueError(f"{label} must be a real directory")
    result: set[str] = set()

    def walk(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = Path(entry.path)
                entry_metadata = os.lstat(path)
                attributes = getattr(entry_metadata, "st_file_attributes", 0)
                relative = _canonical_relative_path(path.relative_to(root).as_posix()).as_posix()
                if stat.S_ISLNK(entry_metadata.st_mode) or attributes & _REPARSE:
                    raise ValueError(f"{label} contains a link or reparse point")
                if stat.S_ISDIR(entry_metadata.st_mode):
                    walk(path)
                elif stat.S_ISREG(entry_metadata.st_mode):
                    result.add(relative)
                else:
                    raise ValueError(f"{label} contains a non-regular file")

    walk(root)
    return result


def _canonical_relative_path(value: str) -> Path:
    path = Path(value)
    if not value or "\\" in value or path.is_absolute() or path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"path is not canonical and contained: {value!r}")
    return path


def _is_link_or_reparse(path: Path) -> bool:
    metadata = os.lstat(path)
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE)


def _require_regular_file(path: Path, label: str) -> None:
    metadata = os.lstat(path)
    if _is_link_or_reparse(path) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} must be a regular non-reparse file")


def _require_real_directory(path: Path, label: str) -> None:
    metadata = os.lstat(path)
    if _is_link_or_reparse(path) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} must be a real non-reparse directory")


def _normalized_absolute(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _paths_overlap(first: Path, second: Path) -> bool:
    first_absolute, second_absolute = _normalized_absolute(first), _normalized_absolute(second)
    try:
        shared = os.path.commonpath((first_absolute, second_absolute))
    except ValueError:
        return False
    return shared in {first_absolute, second_absolute}


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
    if any(_paths_overlap(output, item) for item in inputs):
        raise ValueError("evidence output must not overlap an input")
    _require_safe_existing_ancestors(output.parent, "evidence output")


def _require_safe_repository_root(repository_root: Path) -> None:
    _require_safe_existing_ancestors(repository_root.parent, "repository root")
    _require_real_directory(repository_root, "repository root")


def _read_regular_file(path: Path, label: str) -> bytes:
    _require_safe_existing_ancestors(path.parent, label)
    _require_regular_file(path, label)
    return path.read_bytes()


def _validate_prerequisite_paths(evidence: Path, output: Path) -> set[str]:
    _require_safe_existing_ancestors(evidence.parent, "falsifier evidence")
    _require_real_directory(evidence, "falsifier evidence")
    files = _safe_files(evidence, "falsifier evidence")
    for relative in files:
        source = evidence / _canonical_relative_path(relative)
        _require_regular_file(source, "falsifier evidence file")
        destination = output / "prerequisites" / "falsifier" / relative
        if len(_normalized_absolute(destination)) > _PREREQUISITE_PATH_BUDGET:
            raise ValueError("copied prerequisite path exceeds the frozen path budget")
    return files


def _validate_falsifier_receipt_inputs(evidence: Path, receipt: Path) -> None:
    _require_safe_existing_ancestors(evidence.parent, "falsifier evidence")
    _require_real_directory(evidence, "falsifier evidence")
    _safe_files(evidence, "falsifier evidence")
    if _paths_overlap(evidence, receipt):
        raise ValueError("falsifier receipt must be distinct from its evidence")


def _snapshot_full_prerequisites(
    evidence: Path, receipt: Path, full_go: Path, output: Path
) -> dict[str, bytes]:
    """Take the only authoritative prerequisite copy before any verification.

    Caller-owned inputs are never verified and then reread.  Verification is
    performed only against this output-owned snapshot before transport starts.
    """
    evidence_files = _validate_prerequisite_paths(evidence, output)
    if _paths_overlap(evidence, receipt) or _paths_overlap(evidence, full_go) or _paths_overlap(receipt, full_go):
        raise ValueError("full-run prerequisites must be pairwise distinct")
    snapshot = {
        f"prerequisites/falsifier/{relative}": _read_regular_file(
            evidence / _canonical_relative_path(relative), "falsifier evidence file"
        )
        for relative in evidence_files
    }
    snapshot["prerequisites/falsifier_receipt.json"] = _read_regular_file(receipt, "falsifier receipt")
    snapshot["prerequisites/full_go.json"] = _read_regular_file(full_go, "full GO")
    return snapshot


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=False, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"source admission git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _git_bytes(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, check=False, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"source admission git {' '.join(args)} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def _plan_hashes() -> tuple[str, str]:
    def normalize(value: object) -> object:
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: normalize(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize(child) for child in value]
        return value

    registry = _canonical_json([normalize(asdict(item)) for item in get_v3_samples()])
    corpus = _canonical_json([normalize(asdict(item)) for item in get_v3_corpus()])
    return _hash_bytes(registry), _hash_bytes(corpus)


def load_plan(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular_file(path, "plan")
    plan = _strict_json_loads(raw, "v3 plan")
    if type(plan) is not dict or raw != _canonical_json(plan):
        raise ValueError("v3 plan must use canonical JSON bytes")
    _validate_plan(plan)
    return plan, raw


def _validate_plan(plan: dict[str, Any]) -> None:
    required = {"acceptance", "calibration", "controls", "corpus_sha256", "endpoint", "generation", "kind", "models", "modes", "no_retry", "plan_id", "preconditions", "primary_sample_ids", "protocol_version", "python", "registry_sha256", "release", "repetitions", "sample_ids", "source_hashes", "timeout_seconds", "trajectory_count"}
    _require_json_mapping(plan, required, "v3 plan")
    for key in ("corpus_sha256", "endpoint", "kind", "plan_id", "protocol_version", "registry_sha256"):
        _require_json_type(plan[key], str, f"v3 plan.{key}")
    for key in ("repetitions", "timeout_seconds", "trajectory_count"):
        _require_json_type(plan[key], int, f"v3 plan.{key}")
    _require_json_type(plan["no_retry"], bool, "v3 plan.no_retry")
    for key in ("controls", "modes", "primary_sample_ids", "sample_ids"):
        for index, value in enumerate(_require_json_list(plan[key], f"v3 plan.{key}")):
            _require_json_type(value, str, f"v3 plan.{key}[{index}]")
    models = _require_json_list(plan["models"], "v3 plan.models")
    for index, model in enumerate(models):
        fields = _require_json_mapping(model, {"digest", "name"}, f"v3 plan.models[{index}]")
        _require_json_type(fields["digest"], str, f"v3 plan.models[{index}].digest")
        _require_json_type(fields["name"], str, f"v3 plan.models[{index}].name")
    _require_json_type(plan["source_hashes"], dict, "v3 plan.source_hashes")
    source_hashes = plan["source_hashes"]
    for relative, digest in source_hashes.items():
        _require_json_type(relative, str, "v3 plan.source_hashes key")
        _require_json_type(digest, str, f"v3 plan.source_hashes.{relative}")
    _require_json_shape(
        plan["generation"],
        {"temperature": 0, "seed": 0, "num_ctx": 8192, "num_predict": 512, "think": False},
        "v3 plan.generation",
    )
    _require_json_shape(
        plan["python"],
        {"implementation": "CPython", "major": 3, "minor": 12, "version": "3.12.10"},
        "v3 plan.python",
    )
    _require_json_shape(
        plan["release"],
        {"tag": "", "ref": "", "origin": "", "branch": "", "remote": ""},
        "v3 plan.release",
    )
    _require_json_shape(
        plan["calibration"],
        {"expected_response": "", "models": 2, "requests_per_model": 1},
        "v3 plan.calibration",
    )
    registry, corpus = _plan_hashes()
    if plan["registry_sha256"] != registry or plan["corpus_sha256"] != corpus:
        raise ValueError("v3 registry or corpus identity differs")
    if tuple(plan["modes"]) != _MODES or tuple(plan["controls"]) != _CONTROLS:
        raise ValueError("v3 mode or control split differs")
    if plan["protocol_version"] != _PROTOCOL_VERSION or plan["no_retry"] is not True:
        raise ValueError("v3 protocol or retry semantics differ")
    contract = _FROZEN_PLAN_CONTRACTS.get(plan["kind"])
    if contract is None:
        raise ValueError("v3 plan kind is unsupported")
    _require_json_shape(plan["acceptance"], contract["acceptance"], "v3 plan.acceptance")
    _require_json_shape(plan["preconditions"], contract["preconditions"], "v3 plan.preconditions")
    for key, expected in contract.items():
        if plan[key] != expected:
            raise ValueError(f"v3 frozen {plan['kind']} {key} differs")
    if plan["models"] != [{"name": name, "digest": digest} for name, digest in _APPROVED_MODELS]:
        raise ValueError("v3 model identities differ")
    if plan["generation"] != {"temperature": 0, "seed": 0, "num_ctx": 8192, "num_predict": 512, "think": False}:
        raise ValueError("v3 generation differs")
    if plan["python"] != {"implementation": "CPython", "major": 3, "minor": 12, "version": "3.12.10"}:
        raise ValueError("v3 Python identity differs")
    if plan["release"] != {"tag": "v3-measurement-protocol-v1", "ref": "refs/tags/v3-measurement-protocol-v1", "origin": "https://github.com/LesterALeong/anachron.git", "branch": "master", "remote": "origin"}:
        raise ValueError("v3 release identity differs")
    if plan["endpoint"] != "http://127.0.0.1:11434" or plan["timeout_seconds"] != 120:
        raise ValueError("v3 endpoint or timeout differs")
    if plan["calibration"] != {"expected_response": "CALIBRATION_OK", "models": 2, "requests_per_model": 1}:
        raise ValueError("v3 calibration differs")
    if set(plan["source_hashes"]) != _GOVERNED_CLOSURE:
        raise ValueError("v3 governed closure differs")
    if plan["trajectory_count"] != 4 * len(plan["sample_ids"]) * plan["repetitions"]:
        raise ValueError("v3 trajectory count is inconsistent")
    if plan["kind"] == "scientific-falsifier":
        if plan["sample_ids"] != list(_FALSIFIER_IDS) or plan["primary_sample_ids"] != list(_FALSIFIER_IDS) or plan["repetitions"] != 1 or plan["trajectory_count"] != 24:
            raise ValueError("v3 falsifier design differs")
    elif plan["kind"] == "full-primary":
        expected_primary = [sample.id for sample in get_v3_samples() if sample.id not in _CONTROLS]
        if plan["sample_ids"] != [sample.id for sample in get_v3_samples()] or plan["primary_sample_ids"] != expected_primary or plan["repetitions"] != 3 or plan["trajectory_count"] != 336:
            raise ValueError("v3 full design differs")


def expected_trajectories(plan: dict[str, Any]) -> list[dict[str, Any]]:
    samples = v3_samples_by_id()
    return [{"id": f"m{index:02d}-{mode}-{sample_id}-r{repeat:02d}", "model": model["name"], "model_digest": model["digest"], "mode": mode, "sample": samples[sample_id], "repetition": repeat, "primary": sample_id in plan["primary_sample_ids"]} for index, model in enumerate(plan["models"], 1) for mode in _MODES for sample_id in plan["sample_ids"] for repeat in range(1, plan["repetitions"] + 1)]


def expected_raw_inventory(plan: dict[str, Any]) -> set[str]:
    files = {"server.version.response.json", "server.tags.response.json", "calibration.m01.request.json", "calibration.m01.response.json", "calibration.m02.request.json", "calibration.m02.response.json"}
    for row in expected_trajectories(plan):
        files.update({f"{row['id']}.first.request.json", f"{row['id']}.first.response.json", f"{row['id']}.tool_result.txt", f"{row['id']}.final.request.json", f"{row['id']}.final.response.json"})
    return files


def validate_exact_raw_inventory(root: Path, plan: dict[str, Any]) -> None:
    if _safe_files(root / "raw", "raw evidence") != expected_raw_inventory(plan):
        raise ValueError("raw evidence inventory does not match the frozen plan")


def _tool_schema() -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": _TOOL_NAME, "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}}}]


def first_request(model: str, sample: V3Sample, generation: dict[str, Any]) -> dict[str, Any]:
    return {"model": model, "stream": False, "think": generation["think"], "options": {key: generation[key] for key in ("temperature", "seed", "num_ctx", "num_predict")}, "messages": [{"role": "user", "content": sample.prompt()}], "tools": _tool_schema()}


def final_request(first: dict[str, Any], first_message: dict[str, Any], tool_result: str) -> dict[str, Any]:
    request = {key: value for key, value in first.items() if key != "tools"}
    request["messages"] = [*first["messages"], first_message, {"role": "tool", "tool_name": _TOOL_NAME, "content": tool_result}]
    return request


def calibration_request(model: str, generation: dict[str, Any]) -> dict[str, Any]:
    return {"model": model, "stream": False, "think": generation["think"], "options": {key: generation[key] for key in ("temperature", "seed", "num_ctx", "num_predict")}, "messages": [{"role": "user", "content": "Return exactly CALIBRATION_OK after this recorded tool transcript."}, {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": _TOOL_NAME, "arguments": {"query": "calibration"}}}]}, {"role": "tool", "tool_name": _TOOL_NAME, "content": "No results."}]}


def _validate_first_tool_response(response: object, model: str) -> str:
    response = _validate_chat_envelope(response, "first response")
    if response["model"] != model or response["done_reason"] != "stop":
        raise ValueError("first response has the wrong terminal identity")
    message = _require_json_mapping(response["message"], {"role", "content", "tool_calls"}, "first response.message")
    if message["role"] != "assistant" or message["content"] != "":
        raise ValueError("first response message is not an empty assistant tool call")
    calls = _require_json_list(message["tool_calls"], "first response.message.tool_calls")
    if len(calls) != 1:
        raise ValueError("first response must contain one tool call")
    call = _require_json_mapping(calls[0], {"id", "function"}, "first response.message.tool_calls[0]")
    _require_json_type(call["id"], str, "first response.message.tool_calls[0].id")
    function = _require_json_mapping(call["function"], {"index", "name", "arguments"}, "first response.message.tool_calls[0].function")
    _require_json_type(function["index"], int, "first response.message.tool_calls[0].function.index")
    _require_json_type(function["name"], str, "first response.message.tool_calls[0].function.name")
    arguments = _require_json_mapping(function["arguments"], {"query"}, "first response.message.tool_calls[0].function.arguments")
    _require_json_type(arguments["query"], str, "first response.message.tool_calls[0].function.arguments.query")
    if not call["id"] or function["index"] != 0 or function["name"] != _TOOL_NAME or not arguments["query"].strip():
        raise ValueError("first response tool-call identity differs")
    return arguments["query"]


def _validate_terminal_final_response(response: object, model: str) -> str:
    response = _validate_chat_envelope(response, "final response")
    message = _require_json_mapping(response["message"], {"role", "content"}, "final response.message")
    _require_json_type(message["content"], str, "final response.message.content")
    if response["model"] != model or response["done_reason"] != "stop" or message["role"] != "assistant" or not message["content"].strip():
        raise ValueError("final response is not a non-empty assistant terminal")
    return message["content"]


def validate_calibration_response(response: object, model: str) -> None:
    response = _validate_chat_envelope(response, "calibration response")
    message = _require_json_mapping(response["message"], {"role", "content"}, "calibration response.message")
    _require_json_type(message["content"], str, "calibration response.message.content")
    if response["model"] != model or response["done_reason"] != "stop" or message["role"] != "assistant" or message["content"] != "CALIBRATION_OK":
        raise ValueError("v3 calibration response is not the exact final-only transcript")


def _require_utc_timestamp(value: object, label: str) -> None:
    _require_json_type(value, str, label)
    if not value.endswith("Z"):
        raise ValueError(f"{label} must use a UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} is not ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{label} must be UTC")


def _require_utc_offset_timestamp(value: object, label: str) -> None:
    _require_json_type(value, str, label)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label} is not ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a UTC offset")


def _validate_chat_envelope(value: object, label: str) -> dict[str, Any]:
    response = _require_json_mapping(
        value,
        {
            "created_at", "done", "done_reason", "eval_count", "eval_duration",
            "load_duration", "message", "model", "prompt_eval_count",
            "prompt_eval_duration", "total_duration",
        },
        label,
    )
    _require_json_type(response["model"], str, f"{label}.model")
    _require_utc_timestamp(response["created_at"], f"{label}.created_at")
    _require_json_type(response["done"], bool, f"{label}.done")
    _require_json_type(response["done_reason"], str, f"{label}.done_reason")
    if response["done"] is not True:
        raise ValueError(f"{label}.done must be true")
    for key in ("eval_count", "eval_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration", "total_duration"):
        _require_json_type(response[key], int, f"{label}.{key}")
        if response[key] < 0:
            raise ValueError(f"{label}.{key} must be non-negative")
    _require_json_type(response["message"], dict, f"{label}.message")
    return response


def _query_dates(query: str) -> list[date]:
    values = []
    for match in _ISO_DATE_RE.finditer(query):
        try:
            values.append(date(*map(int, match.groups())))
        except ValueError:
            pass
    return values


class _Writer:
    def __init__(self, root: Path):
        _require_absent_output(root)
        root.mkdir(parents=True)
        _require_real_directory(root, "evidence output")
        self.root = root

    def write(self, relative: str, content: bytes) -> Path:
        path = self.root / _canonical_relative_path(relative)
        _require_real_directory(self.root, "evidence output")
        _require_safe_existing_ancestors(path.parent, "evidence output")
        path.parent.mkdir(parents=True, exist_ok=True)
        _require_safe_existing_ancestors(path.parent, "evidence output")
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return path

    def json(self, relative: str, value: object) -> Path:
        return self.write(relative, _canonical_json(value))

    def journal(self, value: dict[str, Any]) -> None:
        path = self.root / "journal.jsonl"
        with path.open("ab") as stream:
            stream.write(json.dumps(value, separators=(",", ":"), sort_keys=True).encode() + b"\n")
            stream.flush()
            os.fsync(stream.fileno())


def _http(
    base_url: str, path: str, body: bytes | None, timeout: int
) -> bytes:
    """Issue the only two protocol transport shapes.

    Identity endpoints are bodyless GETs.  Chat is a POST whose bytes are
    already durably recorded by the runner; this function never serializes a
    second representation of the request.
    """
    if path in {"/api/version", "/api/tags"}:
        if body is not None:
            raise ValueError("Ollama identity requests must be bodyless GETs")
        request = Request(base_url + path, method="GET")
    elif path == "/api/chat":
        if type(body) is not bytes:
            raise ValueError("Ollama chat requests require exact recorded bytes")
        request = Request(
            base_url + path,
            body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    else:
        raise ValueError("Ollama request path is not governed")
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return raw


def _parse_response_bytes(raw: bytes, label: str) -> dict[str, Any]:
    value = _strict_json_loads(raw, label)
    if type(value) is not dict:
        raise TypeError(f"{label} is not a JSON object")
    return value


def _require_loopback(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"} or parsed.port != 11434 or parsed.path not in ("", "/"):
        raise ValueError("endpoint must be the frozen loopback Ollama URL")
    return endpoint.rstrip("/")


def admit_committed_source(plan: dict[str, Any], repository_root: Path) -> dict[str, Any]:
    """Require the clean, detached, annotated and remotely visible release tag."""
    _require_safe_repository_root(repository_root)
    if _runtime_identity() != plan["python"]:
        raise RuntimeError("source admission Python runtime identity mismatch")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("source admission requires a clean tracked and untracked checkout")
    release = plan["release"]
    if _git(repository_root, "rev-parse", "--abbrev-ref", "HEAD") != "HEAD" or _git(repository_root, "describe", "--exact-match", "--tags", "HEAD") != release["tag"]:
        raise RuntimeError("source admission requires the exact detached release tag")
    tag_ref = f"refs/tags/{release['tag']}"
    if _git(repository_root, "cat-file", "-t", tag_ref) != "tag":
        raise RuntimeError("source admission requires an annotated tag")
    head = _git(repository_root, "rev-parse", "HEAD")
    local_object = _git(repository_root, "rev-parse", tag_ref)
    local_peeled = _git(repository_root, "rev-parse", f"{tag_ref}^{{}}")
    if local_peeled != head or _git(repository_root, "rev-parse", f"refs/heads/{release['branch']}") != head:
        raise RuntimeError("source admission release does not peel to branch HEAD")
    if _git(repository_root, "cat-file", "-p", local_object).splitlines()[:2] != [f"object {head}", "type commit"]:
        raise RuntimeError("source admission tag object does not directly bind HEAD")
    if _git(repository_root, "config", "--get", f"remote.{release['remote']}.url") != release["origin"]:
        raise RuntimeError("source admission origin URL mismatch")
    remote_tag = _git(repository_root, "ls-remote", "--tags", release["remote"], release["ref"]).split()
    remote_peeled = _git(repository_root, "ls-remote", "--tags", release["remote"], f"{release['ref']}^{{}}").split()
    remote_master = _git(repository_root, "ls-remote", release["remote"], f"refs/heads/{release['branch']}").split()
    if (len(remote_tag) != 2 or remote_tag[0] != local_object or len(remote_peeled) != 2 or remote_peeled[0] != head or len(remote_master) != 2 or remote_master[0] != head):
        raise RuntimeError("source admission remote tag or branch parity mismatch")
    blobs = {}
    for relative, digest in plan["source_hashes"].items():
        path = repository_root / relative
        _require_safe_existing_ancestors(path.parent, "governed source")
        _require_regular_file(path, "governed source")
        blob_oid = _git(repository_root, "rev-parse", f"{head}:{relative}")
        blob = _git_bytes(repository_root, "cat-file", "blob", blob_oid)
        if _hash_file(path) != digest or _hash_bytes(blob) != digest or path.read_bytes() != blob:
            raise RuntimeError(f"source admission governed bytes mismatch: {relative}")
        blobs[relative] = {"sha256": digest, "oid": blob_oid}
    return {"schema_version": 2, "python": _runtime_identity(), "tag": {"name": release["tag"], "commit": head, "local_object": local_object, "local_peeled": local_peeled, "remote_object": remote_tag[0], "remote_peeled": remote_peeled[0], "remote_master": remote_master[0]}, "governed_blobs": blobs}


def verify_source_admission(plan: dict[str, Any], receipt: dict[str, Any], repository_root: Path) -> None:
    """Replay a source receipt without requiring a live remote lookup."""
    _require_safe_repository_root(repository_root)
    receipt = _require_json_mapping(
        receipt,
        {"schema_version", "python", "tag", "governed_blobs"},
        "source admission receipt",
    )
    _require_json_type(receipt["schema_version"], int, "source admission receipt.schema_version")
    _require_json_shape(receipt["python"], plan["python"], "source admission receipt.python")
    tag = _require_json_mapping(
        receipt["tag"],
        {"name", "commit", "local_object", "local_peeled", "remote_object", "remote_peeled", "remote_master"},
        "source admission receipt.tag",
    )
    for key, value in tag.items():
        _require_json_type(value, str, f"source admission receipt.tag.{key}")
    _require_json_type(receipt["governed_blobs"], dict, "source admission receipt.governed_blobs")
    blobs = receipt["governed_blobs"]
    if set(blobs) != set(plan["source_hashes"]):
        raise ValueError("source admission governed closure mismatch")
    for relative, entry in blobs.items():
        _require_json_type(relative, str, "source admission receipt.governed_blobs key")
        fields = _require_json_mapping(
            entry, {"sha256", "oid"}, f"source admission receipt.governed_blobs.{relative}"
        )
        _require_json_type(fields["sha256"], str, f"source admission receipt.governed_blobs.{relative}.sha256")
        _require_json_type(fields["oid"], str, f"source admission receipt.governed_blobs.{relative}.oid")
    if receipt["schema_version"] != 2 or receipt["python"] != plan["python"]:
        raise ValueError("source admission receipt schema mismatch")
    if tag["name"] != plan["release"]["tag"] or len({tag["commit"], tag["local_peeled"], tag["remote_peeled"], tag["remote_master"]}) != 1 or tag["local_object"] != tag["remote_object"]:
        raise ValueError("source admission receipt tag binding mismatch")
    if _git(repository_root, "cat-file", "-t", f"refs/tags/{tag['name']}") != "tag" or _git(repository_root, "rev-parse", f"refs/tags/{tag['name']}") != tag["local_object"] or _git(repository_root, "rev-parse", f"refs/tags/{tag['name']}^{{}}") != tag["commit"]:
        raise ValueError("source admission receipt tag is absent or changed")
    for relative, expected_digest in plan["source_hashes"].items():
        entry = blobs[relative]
        if entry["sha256"] != expected_digest:
            raise ValueError("source admission governed digest mismatch")
        blob = _git_bytes(repository_root, "cat-file", "blob", entry.get("oid", ""))
        path = repository_root / relative
        _require_safe_existing_ancestors(path.parent, "governed source")
        _require_regular_file(path, "governed source")
        if _hash_bytes(blob) != expected_digest or path.read_bytes() != blob:
            raise ValueError("source admission governed bytes mismatch")


def _server_identity(base_url: str, plan: dict[str, Any], transport: Callable) -> tuple[bytes, bytes]:
    version_raw = transport(base_url, "/api/version", None, plan["timeout_seconds"])
    tags_raw = transport(base_url, "/api/tags", None, plan["timeout_seconds"])
    version = _parse_response_bytes(version_raw, "Ollama version response")
    tags = _parse_response_bytes(tags_raw, "Ollama tags response")
    _validate_server_identity_responses(version, tags, plan)
    return version_raw, tags_raw


def _validate_server_identity_responses(
    version: dict[str, Any], tags: dict[str, Any], plan: dict[str, Any]
) -> None:
    version = _require_json_mapping(version, {"version"}, "Ollama version response")
    _require_json_type(version["version"], str, "Ollama version response.version")
    if version["version"] != "0.33.2":
        raise ValueError("Ollama version must be the frozen 0.33.2")
    tags = _require_json_mapping(tags, {"models"}, "Ollama tags response")
    models = _require_json_list(tags["models"], "Ollama tags response.models")
    installed = {}
    for index, item in enumerate(models):
        item = _require_json_mapping(
            item,
            {"name", "model", "modified_at", "size", "digest", "details", "capabilities"},
            f"Ollama tags response.models[{index}]",
        )
        _require_json_type(item["name"], str, f"Ollama tags response.models[{index}].name")
        _require_json_type(item["model"], str, f"Ollama tags response.models[{index}].model")
        _require_utc_offset_timestamp(item["modified_at"], f"Ollama tags response.models[{index}].modified_at")
        _require_json_type(item["digest"], str, f"Ollama tags response.models[{index}].digest")
        _require_json_type(item["size"], int, f"Ollama tags response.models[{index}].size")
        details = _require_json_mapping(
            item["details"],
            {"parent_model", "format", "family", "families", "parameter_size", "quantization_level", "context_length", "embedding_length"},
            f"Ollama tags response.models[{index}].details",
        )
        for key in ("parent_model", "format", "family", "parameter_size", "quantization_level"):
            _require_json_type(details[key], str, f"Ollama tags response.models[{index}].details.{key}")
        for key in ("context_length", "embedding_length"):
            _require_json_type(details[key], int, f"Ollama tags response.models[{index}].details.{key}")
        families = _require_json_list(details["families"], f"Ollama tags response.models[{index}].details.families")
        capabilities = _require_json_list(item["capabilities"], f"Ollama tags response.models[{index}].capabilities")
        for value in [*families, *capabilities]:
            _require_json_type(value, str, f"Ollama tags response.models[{index}] list member")
        if item["name"] in installed:
            raise ValueError("Ollama tags response contains a duplicate model name")
        installed[item["name"]] = item["digest"]
    for model in plan["models"]:
        if installed.get(model["name"]) != model["digest"]:
            raise RuntimeError(f"Ollama model digest mismatch: {model['name']}")


def _run_calibrations(writer: _Writer, base_url: str, plan: dict[str, Any], transport: Callable) -> list[dict[str, Any]]:
    records = []
    for index, model in enumerate(plan["models"], 1):
        request = calibration_request(model["name"], plan["generation"])
        request_bytes = _canonical_json(request)
        request_path = writer.write(f"raw/calibration.m{index:02d}.request.json", request_bytes)
        raw = transport(base_url, "/api/chat", request_bytes, plan["timeout_seconds"])
        response_path = writer.write(f"raw/calibration.m{index:02d}.response.json", raw)
        response = _parse_response_bytes(raw, "calibration response")
        validate_calibration_response(response, model["name"])
        records.append(
            {
                "model": model["name"],
                "request_count": 1,
                "passed": True,
                "request_sha256": _hash_file(request_path),
                "response_sha256": _hash_file(response_path),
            }
        )
    return records


def _run_trajectory(writer: _Writer, base_url: str, plan: dict[str, Any], trajectory: dict[str, Any], transport: Callable) -> dict[str, Any]:
    sample = trajectory["sample"]
    identifier = trajectory["id"]
    record = {"trajectory_id": identifier, "model": trajectory["model"], "model_digest": trajectory["model_digest"], "mode": trajectory["mode"], "sample_id": sample.id, "as_of": sample.as_of.isoformat(), "repetition": trajectory["repetition"], "primary": trajectory["primary"], "valid": False}
    writer.journal({"kind": "trajectory_claim", "trajectory_id": identifier, "timestamp_utc": _utc_now()})
    try:
        first = first_request(trajectory["model"], sample, plan["generation"])
        first_bytes = _canonical_json(first)
        first_path = writer.write(f"raw/{identifier}.first.request.json", first_bytes)
        first_raw = transport(base_url, "/api/chat", first_bytes, plan["timeout_seconds"])
        first_response_path = writer.write(f"raw/{identifier}.first.response.json", first_raw)
        first_response = _parse_response_bytes(first_raw, "first response")
        query = _validate_first_tool_response(first_response, trajectory["model"])
        items = search_v3(query, sample.as_of if trajectory["mode"] == "enforced" else None)
        tool_path = writer.write(f"raw/{identifier}.tool_result.txt", format_search_results(items).encode())
        final = final_request(first, first_response["message"], tool_path.read_text(encoding="utf-8"))
        if "tools" in final:
            raise AssertionError("final request must omit tools")
        final_bytes = _canonical_json(final)
        final_path = writer.write(f"raw/{identifier}.final.request.json", final_bytes)
        final_raw = transport(base_url, "/api/chat", final_bytes, plan["timeout_seconds"])
        final_response_path = writer.write(f"raw/{identifier}.final.response.json", final_raw)
        final_response = _parse_response_bytes(final_raw, "final response")
        _validate_terminal_final_response(final_response, trajectory["model"])
        score = asdict(score_interactions([ToolInteraction(_TOOL_NAME, query, _query_dates(query), items)], sample.as_of))
        record.update({"valid": True, "query": query, "returned_item_ids": [item.id for item in items], "score": score, "first_request_sha256": _hash_file(first_path), "first_response_sha256": _hash_file(first_response_path), "tool_result_sha256": _hash_file(tool_path), "final_request_sha256": _hash_file(final_path), "final_response_sha256": _hash_file(final_response_path), "final_answer": final_response["message"]["content"]})
    except Exception as error:  # noqa: BLE001 - every claimed trajectory needs one terminal record.
        record["invalid_reason"] = f"{type(error).__name__}: {error}"
    writer.journal({"kind": "trajectory_terminal", "trajectory_id": identifier, "timestamp_utc": _utc_now(), "valid": record["valid"], "reason": record.get("invalid_reason")})
    return record


def build_analysis(plan: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    rows = runtime.get("trajectories", [])
    expected = expected_trajectories(plan)
    if len(rows) != len(expected) or any(row.get("trajectory_id") != item["id"] for row, item in zip(rows, expected)) or any(row.get("valid") is not True for row in rows):
        raise ValueError("sealed analysis requires every planned trajectory to be valid")
    primary = [row for row in rows if row["primary"]]
    cells = {}
    for model in (entry["name"] for entry in plan["models"]):
        reductions = []
        for sample_id in plan["primary_sample_ids"]:
            un = [row["score"]["tclr"] for row in primary if row["model"] == model and row["mode"] == "unrestricted" and row["sample_id"] == sample_id]
            en = [row["score"]["tclr"] for row in primary if row["model"] == model and row["mode"] == "enforced" and row["sample_id"] == sample_id]
            reductions.extend(left - right for left, right in zip(un, en))
        cells[model] = sum(reductions) / len(reductions)
    all_reductions = list(cells.values())
    residual = {model: any(row["model"] == model and row["mode"] == "enforced" and row["sample_id"] == "fin-equinox-2021-06-delisted-before-cutoff" and row["score"]["survivorship_leaks"] >= 1 for row in rows) for model in cells}
    gates = {"all_trajectories_valid": True, "minimum_primary_reduction": sum(all_reductions) / len(all_reductions) >= plan["acceptance"]["minimum_pooled_reduction"], "no_model_negative": all(value >= 0 for value in all_reductions), "enforced_equinox_survivorship_each_model": all(residual.values())}
    return {"plan_id": plan["plan_id"], "trajectory_count": len(rows), "primary_trajectory_count": len(primary), "development_trajectory_count": len(rows) - len(primary), "model_primary_reductions": cells, "equinox_enforced_survivorship": residual, "gates": gates, "go": all(gates.values())}


def _manifest(writer: _Writer, plan: dict[str, Any]) -> None:
    validate_exact_raw_inventory(writer.root, plan)
    files = [{"path": item, "sha256": _hash_file(writer.root / item)} for item in sorted(_safe_files(writer.root, "evidence")) if item not in {"manifest.json", "manifest.sha256"}]
    manifest = writer.json("manifest.json", {"schema_version": 1, "files": files})
    writer.write("manifest.sha256", f"{_hash_file(manifest)}  manifest.json\n".encode())


def _validate_runtime_schema(
    runtime: dict[str, Any], plan: dict[str, Any], root: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    required = {
        "schema_version",
        "plan_id",
        "server",
        "calibrations",
        "generation_request_count",
        "trajectories",
    }
    runtime = _require_json_mapping(runtime, required, "runtime")
    _require_json_type(runtime["schema_version"], int, "runtime.schema_version")
    _require_json_type(runtime["plan_id"], str, "runtime.plan_id")
    _require_json_type(runtime["generation_request_count"], int, "runtime.generation_request_count")
    if runtime["schema_version"] != 1 or runtime["plan_id"] != plan["plan_id"]:
        raise ValueError("runtime schema differs from the frozen protocol")
    server = _require_json_mapping(
        runtime["server"],
        {"version_response_sha256", "tags_response_sha256"},
        "runtime.server",
    )
    for key, value in server.items():
        _require_json_type(value, str, f"runtime.server.{key}")
    expected_server = {
        "version_response_sha256": _hash_file(root / "raw" / "server.version.response.json"),
        "tags_response_sha256": _hash_file(root / "raw" / "server.tags.response.json"),
    }
    if server != expected_server:
        raise ValueError("runtime server record does not bind raw evidence")
    calibrations = _require_json_list(runtime["calibrations"], "runtime.calibrations")
    if len(calibrations) != len(plan["models"]):
        raise ValueError("runtime calibration count mismatch")
    for index, (model, record) in enumerate(zip(plan["models"], calibrations), 1):
        record = _require_json_mapping(
            record,
            {"model", "request_count", "passed", "request_sha256", "response_sha256"},
            f"runtime.calibrations[{index - 1}]",
        )
        _require_json_type(record["model"], str, f"runtime.calibrations[{index - 1}].model")
        _require_json_type(record["request_count"], int, f"runtime.calibrations[{index - 1}].request_count")
        _require_json_type(record["passed"], bool, f"runtime.calibrations[{index - 1}].passed")
        _require_json_type(record["request_sha256"], str, f"runtime.calibrations[{index - 1}].request_sha256")
        _require_json_type(record["response_sha256"], str, f"runtime.calibrations[{index - 1}].response_sha256")
        expected = {
            "model": model["name"],
            "request_count": 1,
            "passed": True,
            "request_sha256": _hash_file(root / "raw" / f"calibration.m{index:02d}.request.json"),
            "response_sha256": _hash_file(root / "raw" / f"calibration.m{index:02d}.response.json"),
        }
        if record != expected:
            raise ValueError("runtime calibration record does not bind raw evidence")
    trajectories = _require_json_list(runtime["trajectories"], "runtime.trajectories")
    return trajectories, calibrations


def run_measurement(plan_path: Path, output: Path, source_admitter: Callable | None = None, transport: Callable | None = None, repository_root: Path | None = None, falsifier_evidence: Path | None = None, falsifier_receipt: Path | None = None, full_go: Path | None = None) -> dict[str, Any]:
    _require_safe_existing_ancestors(plan_path.parent, "plan")
    _require_regular_file(plan_path, "plan")
    plan, raw = load_plan(plan_path)
    root = repository_root or Path(__file__).resolve().parent.parent
    _require_safe_repository_root(root)
    prerequisite_files: dict[str, bytes] = {}
    if plan["kind"] == "full-primary":
        if falsifier_evidence is None or falsifier_receipt is None or full_go is None:
            raise ValueError("full plan requires falsifier evidence, receipt, and human GO")
        _require_absent_output(output, (plan_path, falsifier_evidence, falsifier_receipt, full_go))
        prerequisite_files = _snapshot_full_prerequisites(
            falsifier_evidence, falsifier_receipt, full_go, output
        )
    elif any(value is not None for value in (falsifier_evidence, falsifier_receipt, full_go)):
        raise ValueError("falsifier does not accept full-run prerequisites")
    else:
        _require_absent_output(output, (plan_path,))
    base_url = _require_loopback(plan["endpoint"])
    admission = (source_admitter or admit_committed_source)(plan, root)
    writer = _Writer(output)
    writer.write("plan.json", raw)
    for relative, content in prerequisite_files.items():
        writer.write(relative, content)
    writer.json("source_admission.json", admission)
    if plan["kind"] == "full-primary":
        snapshot_root = output / "prerequisites"
        verify_falsifier_receipt(
            snapshot_root / "falsifier", snapshot_root / "falsifier_receipt.json", root
        )
        verify_full_go(
            plan,
            raw,
            snapshot_root / "falsifier_receipt.json",
            snapshot_root / "full_go.json",
        )
    transport = transport or _http
    completed_step = -1
    try:
        version_raw, tags_raw = _server_identity(base_url, plan, transport)
        version_path = writer.write("raw/server.version.response.json", version_raw)
        tags_path = writer.write("raw/server.tags.response.json", tags_raw)
        completed_step = 0
        calibrations = _run_calibrations(writer, base_url, plan, transport)
        completed_step = 1
        trajectories = [_run_trajectory(writer, base_url, plan, item, transport) for item in expected_trajectories(plan)]
        if any(row["valid"] is not True for row in trajectories):
            raise RuntimeError("incomplete trajectories cannot produce sealed evidence")
        completed_step = 2
    except Exception as error:
        writer.json(
            "terminal_failure.json",
            {
                "schema_version": 1,
                "sealed": False,
                "failed_step": completed_step + 1,
                "last_completed_step": completed_step,
                "fault_code": type(error).__name__,
            },
        )
        raise RuntimeError("v3 measurement stopped unsealed after a typed external boundary failure") from error
    runtime = {
        "schema_version": 1,
        "plan_id": plan["plan_id"],
        "server": {
            "version_response_sha256": _hash_file(version_path),
            "tags_response_sha256": _hash_file(tags_path),
        },
        "calibrations": calibrations,
        "generation_request_count": 2 * len(trajectories) + 2,
        "trajectories": trajectories,
    }
    writer.json("runtime.json", runtime)
    analysis = build_analysis(plan, runtime)
    writer.json("analysis.json", analysis)
    writer.write("README.md", _EVIDENCE_README)
    _manifest(writer, plan)
    verified = analyze_evidence(output, root)
    if verified != analysis:
        raise RuntimeError("runner self-verification did not reproduce analysis")
    return analysis


def analyze_evidence(root: Path, repository_root: Path | None = None) -> dict[str, Any]:
    _require_safe_existing_ancestors(root.parent, "evidence")
    _require_real_directory(root, "evidence")
    files = _safe_files(root, "evidence")
    manifest_path = root / "manifest.json"
    manifest_sha_path = root / "manifest.sha256"
    manifest_raw = _read_regular_file(manifest_path, "manifest")
    manifest = _parse_response_bytes(manifest_raw, "manifest")
    if manifest_raw != _canonical_json(manifest):
        raise ValueError("manifest must use canonical JSON bytes")
    if _read_regular_file(manifest_sha_path, "manifest hash").decode() != f"{_hash_file(manifest_path)}  manifest.json\n":
        raise ValueError("manifest hash mismatch")
    manifest = _require_json_mapping(manifest, {"schema_version", "files"}, "manifest")
    _require_json_type(manifest["schema_version"], int, "manifest.schema_version")
    manifest_files = _require_json_list(manifest["files"], "manifest.files")
    if manifest["schema_version"] != 1:
        raise ValueError("manifest schema mismatch")
    expected = {}
    for index, item in enumerate(manifest_files):
        item = _require_json_mapping(item, {"path", "sha256"}, f"manifest.files[{index}]")
        _require_json_type(item["path"], str, f"manifest.files[{index}].path")
        _require_json_type(item["sha256"], str, f"manifest.files[{index}].sha256")
        expected[item["path"]] = item["sha256"]
    if len(expected) != len(manifest_files):
        raise ValueError("manifest file schema mismatch")
    if files - {"manifest.json", "manifest.sha256"} != set(expected):
        raise ValueError("manifest file closure mismatch")
    for relative, digest in expected.items():
        path = root / _canonical_relative_path(relative)
        if not isinstance(digest, str) or _hash_file(path) != digest:
            raise ValueError("manifest file closure mismatch")
    plan, _ = load_plan(root / "plan.json")
    source_raw = _read_regular_file(root / "source_admission.json", "source admission")
    source_receipt = _parse_response_bytes(source_raw, "source admission")
    if source_raw != _canonical_json(source_receipt):
        raise ValueError("source admission must use canonical JSON bytes")
    source_root = repository_root or Path(__file__).resolve().parent.parent
    _require_safe_repository_root(source_root)
    verify_source_admission(
        plan,
        source_receipt,
        source_root,
    )
    validate_exact_raw_inventory(root, plan)
    if _read_regular_file(root / "README.md", "evidence README") != _EVIDENCE_README:
        raise ValueError("evidence README bytes differ from the frozen protocol")
    runtime_raw = _read_regular_file(root / "runtime.json", "runtime")
    runtime = _parse_response_bytes(runtime_raw, "runtime")
    if runtime_raw != _canonical_json(runtime):
        raise ValueError("runtime must use canonical JSON bytes")
    version = _parse_response_bytes(
        _read_regular_file(root / "raw" / "server.version.response.json", "server version"),
        "server version",
    )
    tags = _parse_response_bytes(
        _read_regular_file(root / "raw" / "server.tags.response.json", "server tags"),
        "server tags",
    )
    _validate_server_identity_responses(version, tags, plan)
    expected_trajectories_list = expected_trajectories(plan)
    records, calibrations = _validate_runtime_schema(runtime, plan, root)
    if not isinstance(records, list) or len(records) != len(expected_trajectories_list):
        raise ValueError("runtime trajectory count mismatch")
    journal_raw = _read_regular_file(root / "journal.jsonl", "journal")
    if not journal_raw.endswith(b"\n"):
        raise ValueError("journal must end with exactly one newline")
    journal = []
    for line in journal_raw.splitlines(keepends=True):
        if not line.endswith(b"\n") or line == b"\n":
            raise ValueError("journal line framing differs from the frozen protocol")
        record = _parse_response_bytes(line[:-1], "journal record")
        if line != json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n":
            raise ValueError("journal bytes are not canonical")
        journal.append(record)
    if len(journal) != 2 * len(expected_trajectories_list):
        raise ValueError("journal claim and terminal count mismatch")
    for expected_trajectory, record, claim, terminal in zip(expected_trajectories_list, records, journal[::2], journal[1::2]):
        identifier = expected_trajectory["id"]
        required_record = {
            "trajectory_id",
            "model",
            "model_digest",
            "mode",
            "sample_id",
            "as_of",
            "repetition",
            "primary",
            "valid",
            "query",
            "returned_item_ids",
            "score",
            "first_request_sha256",
            "first_response_sha256",
            "tool_result_sha256",
            "final_request_sha256",
            "final_response_sha256",
            "final_answer",
        }
        record = _require_json_mapping(record, required_record, "runtime trajectory")
        for key in (
            "trajectory_id",
            "model",
            "model_digest",
            "mode",
            "sample_id",
            "as_of",
            "query",
            "first_request_sha256",
            "first_response_sha256",
            "tool_result_sha256",
            "final_request_sha256",
            "final_response_sha256",
            "final_answer",
        ):
            _require_json_type(record[key], str, f"runtime trajectory.{key}")
        _require_json_type(record["repetition"], int, "runtime trajectory.repetition")
        _require_json_type(record["primary"], bool, "runtime trajectory.primary")
        _require_json_type(record["valid"], bool, "runtime trajectory.valid")
        returned_item_ids = _require_json_list(
            record["returned_item_ids"], "runtime trajectory.returned_item_ids"
        )
        for index, item in enumerate(returned_item_ids):
            _require_json_type(item, str, f"runtime trajectory.returned_item_ids[{index}]")
        _require_json_type(record["score"], dict, "runtime trajectory.score")
        claim = _require_json_mapping(
            claim,
            {"kind", "trajectory_id", "timestamp_utc"},
            "journal trajectory claim",
        )
        terminal = _require_json_mapping(
            terminal,
            {"kind", "trajectory_id", "timestamp_utc", "valid", "reason"},
            "journal trajectory terminal",
        )
        for key in ("kind", "trajectory_id", "timestamp_utc"):
            _require_json_type(claim[key], str, f"journal trajectory claim.{key}")
            _require_json_type(terminal[key], str, f"journal trajectory terminal.{key}")
        _require_utc_timestamp(claim["timestamp_utc"], "journal trajectory claim.timestamp_utc")
        _require_utc_timestamp(terminal["timestamp_utc"], "journal trajectory terminal.timestamp_utc")
        _require_json_type(terminal["valid"], bool, "journal trajectory terminal.valid")
        _require_json_type(terminal["reason"], type(None), "journal trajectory terminal.reason")
        if (
            claim["kind"] != "trajectory_claim"
            or terminal.get("kind") != "trajectory_terminal"
            or claim.get("trajectory_id") != identifier
            or terminal.get("trajectory_id") != identifier
            or terminal.get("valid") is not True
            or not isinstance(claim.get("timestamp_utc"), str)
            or not isinstance(terminal.get("timestamp_utc"), str)
        ):
            raise ValueError("journal claim or terminal topology mismatch")
        for key in ("model", "model_digest", "mode", "repetition"):
            if record.get(key) != expected_trajectory[key]:
                raise ValueError("runtime trajectory identity mismatch")
        if record.get("trajectory_id") != identifier or record.get("sample_id") != expected_trajectory["sample"].id or record.get("as_of") != expected_trajectory["sample"].as_of.isoformat() or record.get("primary") is not expected_trajectory["primary"] or record.get("valid") is not True:
            raise ValueError("runtime sample or group identity mismatch")
    for trajectory, record in zip(expected_trajectories_list, records):
        identifier = trajectory["id"]
        first_path = root / "raw" / f"{identifier}.first.request.json"
        first_response_path = root / "raw" / f"{identifier}.first.response.json"
        tool_path = root / "raw" / f"{identifier}.tool_result.txt"
        final_path = root / "raw" / f"{identifier}.final.request.json"
        response_path = root / "raw" / f"{identifier}.final.response.json"
        first_raw = _read_regular_file(first_path, "first request")
        first = _parse_response_bytes(first_raw, "first request")
        first_response = _parse_response_bytes(
            _read_regular_file(first_response_path, "first response"), "first response"
        )
        final_raw = _read_regular_file(final_path, "final request")
        final = _parse_response_bytes(final_raw, "final request")
        response = _parse_response_bytes(
            _read_regular_file(response_path, "final response"), "final response"
        )
        expected_first = first_request(trajectory["model"], trajectory["sample"], plan["generation"])
        if "tools" not in first or "tools" in final:
            raise ValueError("scientific request topology mismatch")
        _require_json_shape(first, expected_first, "first request")
        if first_raw != _canonical_json(first) or first != expected_first:
            raise ValueError("first request reconstruction mismatch")
        query = _validate_first_tool_response(first_response, trajectory["model"])
        items = search_v3(query, trajectory["sample"].as_of if trajectory["mode"] == "enforced" else None)
        tool_bytes = format_search_results(items).encode()
        if _read_regular_file(tool_path, "tool result") != tool_bytes:
            raise ValueError("tool result reconstruction mismatch")
        expected_final = final_request(expected_first, first_response["message"], tool_bytes.decode())
        _require_json_shape(final, expected_final, "final request")
        if final_raw != _canonical_json(final) or final != expected_final:
            raise ValueError("final request reconstruction mismatch")
        _validate_terminal_final_response(response, trajectory["model"])
        expected_score = asdict(score_interactions([ToolInteraction(_TOOL_NAME, query, _query_dates(query), items)], trajectory["sample"].as_of))
        _require_json_shape(record["score"], expected_score, "runtime trajectory.score")
        if (
            record.get("query") != query
            or record.get("returned_item_ids") != [item.id for item in items]
            or record.get("score") != expected_score
            or record.get("final_answer") != response["message"]["content"]
            or record.get("first_request_sha256") != _hash_file(first_path)
            or record.get("first_response_sha256") != _hash_file(first_response_path)
            or record.get("tool_result_sha256") != _hash_file(tool_path)
            or record.get("final_request_sha256") != _hash_file(final_path)
            or record.get("final_response_sha256") != _hash_file(response_path)
        ):
            raise ValueError("runtime trace reconstruction mismatch")
    for index, (model, _) in enumerate(zip(plan["models"], calibrations), 1):
        request_raw = _read_regular_file(
            root / "raw" / f"calibration.m{index:02d}.request.json", "calibration request"
        )
        request = _parse_response_bytes(request_raw, "calibration request")
        response = _parse_response_bytes(
            _read_regular_file(
                root / "raw" / f"calibration.m{index:02d}.response.json", "calibration response"
            ),
            "calibration response",
        )
        expected_request = calibration_request(model["name"], plan["generation"])
        _require_json_shape(request, expected_request, "calibration request")
        if request_raw != _canonical_json(request) or request != expected_request:
            raise ValueError("calibration request reconstruction mismatch")
        validate_calibration_response(response, model["name"])
    if plan["kind"] == "full-primary":
        prerequisite_root = root / "prerequisites"
        _require_safe_existing_ancestors(prerequisite_root.parent, "prerequisites")
        _require_real_directory(prerequisite_root, "prerequisites")
        verify_falsifier_receipt(
            prerequisite_root / "falsifier",
            prerequisite_root / "falsifier_receipt.json",
            repository_root,
        )
        verify_full_go(
            plan,
            _read_regular_file(root / "plan.json", "plan"),
            prerequisite_root / "falsifier_receipt.json",
            prerequisite_root / "full_go.json",
        )
    analysis = build_analysis(plan, runtime)
    recorded_analysis = _parse_response_bytes(
        _read_regular_file(root / "analysis.json", "analysis"), "analysis"
    )
    if _read_regular_file(root / "analysis.json", "analysis") != _canonical_json(recorded_analysis):
        raise ValueError("analysis must use canonical JSON bytes")
    _require_json_shape(recorded_analysis, analysis, "analysis")
    if analysis != recorded_analysis:
        raise ValueError("analysis reconstruction mismatch")
    if runtime.get("generation_request_count") != 2 * plan["trajectory_count"] + 2 or len(runtime.get("calibrations", [])) != 2:
        raise ValueError("request-count or calibration boundary mismatch")
    return analysis


def seal_falsifier_receipt(
    evidence: Path, receipt: Path, repository_root: Path | None = None
) -> dict[str, Any]:
    _validate_falsifier_receipt_inputs(evidence, receipt)
    _require_absent_output(receipt, (evidence,))
    analysis = analyze_evidence(evidence, repository_root)
    plan, raw = load_plan(evidence / "plan.json")
    if plan["kind"] != "scientific-falsifier" or not analysis["go"]:
        raise ValueError("only passing v3 falsifier evidence can be sealed")
    body = {"schema_version": 1, "kind": "anachron-v3-falsifier-receipt", "plan_sha256": _hash_bytes(raw), "analysis_sha256": _hash_file(evidence / "analysis.json"), "manifest_sha256": _hash_file(evidence / "manifest.json"), "go": True}
    _require_safe_existing_ancestors(receipt.parent, "receipt")
    with receipt.open("xb") as stream:
        stream.write(_canonical_json(body))
        stream.flush()
        os.fsync(stream.fileno())
    return body


def verify_falsifier_receipt(
    evidence: Path, receipt: Path, repository_root: Path | None = None
) -> dict[str, Any]:
    _validate_falsifier_receipt_inputs(evidence, receipt)
    receipt_raw = _read_regular_file(receipt, "falsifier receipt")
    analysis = analyze_evidence(evidence, repository_root)
    body = _parse_response_bytes(receipt_raw, "falsifier receipt")
    if receipt_raw != _canonical_json(body):
        raise ValueError("falsifier receipt must use canonical JSON bytes")
    _, raw = load_plan(evidence / "plan.json")
    expected = {"schema_version": 1, "kind": "anachron-v3-falsifier-receipt", "plan_sha256": _hash_bytes(raw), "analysis_sha256": _hash_file(evidence / "analysis.json"), "manifest_sha256": _hash_file(evidence / "manifest.json"), "go": True}
    _require_json_shape(body, expected, "falsifier receipt")
    if body != expected or not analysis["go"]:
        raise ValueError("falsifier receipt does not bind passing evidence")
    return body


def verify_full_go(plan: dict[str, Any], raw: bytes, receipt: Path, full_go: Path) -> None:
    _read_regular_file(receipt, "falsifier receipt")
    full_go_raw = _read_regular_file(full_go, "full GO")
    if _paths_overlap(receipt, full_go):
        raise ValueError("full GO must be distinct from the falsifier receipt")
    body = _parse_response_bytes(full_go_raw, "full GO")
    if full_go_raw != _canonical_json(body):
        raise ValueError("human GO must use canonical JSON bytes")
    required = {"authorized_at_utc", "authorized_by", "decision", "falsifier_receipt_sha256", "full_plan_sha256", "kind", "schema_version", "statement"}
    body = _require_json_mapping(body, required, "human GO")
    _require_json_type(body["schema_version"], int, "human GO.schema_version")
    for key in required - {"schema_version"}:
        _require_json_type(body[key], str, f"human GO.{key}")
    try:
        authorized_at = datetime.fromisoformat(body["authorized_at_utc"])
    except (TypeError, ValueError) as error:
        raise ValueError("human GO timestamp is not ISO-8601") from error
    if (
        body["schema_version"] != 1
        or body["kind"] != _FULL_GO_KIND
        or body["decision"] != "GO"
        or body["authorized_by"] != "Lester Leong"
        or authorized_at.tzinfo is None
        or authorized_at.utcoffset() != timezone.utc.utcoffset(authorized_at)
        or body["statement"] != _FULL_GO_STATEMENT
        or body["full_plan_sha256"] != _hash_bytes(raw)
        or body["falsifier_receipt_sha256"] != _hash_file(receipt)
    ):
        raise ValueError("human GO does not bind the exact full plan and falsifier receipt")
