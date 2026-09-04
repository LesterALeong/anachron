"""Outcome-neutral projection helpers for a sealed Anachron v3 full study."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from typing import Any

PROTOCOL_TAG = "v3-measurement-protocol-v1"
PROTOCOL_TAG_OBJECT = "1f4b5088f6dda5134fe3d21176b9274c0165d94a"
PROTOCOL_COMMIT = "cc28c9890455c5bde09ee5710c411ee229e0f9e5"
PROTOCOL_ORIGIN = "https://github.com/LesterALeong/anachron.git"
FULL_PLAN_SHA256 = "23b2dfc70437826579b4c97c8caf5bff59540dfb41ce2c15aa852c39f6888b90"
_REPARSE = 0x400
_METRICS = ("tclr", "query_leakage", "restatement_leakage", "survivorship_leakage")
REVIEW_LENS_IDS = (
    "claim-evidence-anti-fabrication",
    "experimental-design-primary-development",
    "trace-protocol-leakage-definition",
    "finite-panel-statistical-reporting",
    "reproducibility-provenance-determinism",
    "related-work-novelty",
    "plain-language-readability-abstract",
    "adversarial-overclaim-limitations",
    "authorship-ai-licensing-integrity",
    "pdf-latex-arxiv-metadata-layout",
)
_ANALYZER_LAUNCHER = (
    "import runpy, sys; "
    "protocol_root, evidence = sys.argv[1:3]; "
    "sys.path.insert(0, protocol_root); "
    "sys.argv = ['tools.analyze_v3_measurement', evidence, '--repository-root', protocol_root]; "
    "runpy.run_module('tools.analyze_v3_measurement', run_name='__main__')"
)


class CandidateProjectionError(ValueError):
    """Raised when evidence cannot produce an outcome-neutral candidate projection."""


def canonical_json(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def sha256_path(path: Path) -> str:
    return hashlib.sha256(_read_regular_file(path, "hash input")).hexdigest()


def validate_candidate_contract(repository_root: Path) -> dict[str, Any]:
    """Validate every pre-outcome candidate input against the frozen contract."""
    path = repository_root / "paper" / "v3_measurement" / "candidate_contract.json"
    raw = path.read_bytes()
    contract = _strict_json(raw, "candidate contract")
    expected_keys = {
        "author_approval_template_sha256",
        "candidate_acceptance_matrix_sha256",
        "candidate_claim_evidence_map_sha256",
        "candidate_manuscript_template_sha256",
        "candidate_submission_metadata_sha256",
        "frozen_protocol_commit",
        "frozen_protocol_matrix_sha256",
        "frozen_protocol_tag",
        "frozen_protocol_tag_object",
        "full_plan_sha256",
        "local_release_allowlist",
        "outcome_semantics",
        "references_sha256",
        "review_template_sha256",
        "review_lens_ids",
        "schema_version",
        "source_archive_allowlist",
        "states",
        "tectonic",
        "topology",
    }
    if type(contract) is not dict or set(contract) != expected_keys or raw != canonical_json(contract):
        raise CandidateProjectionError("candidate contract has the wrong canonical schema")
    expected_hashes = {
        "author_approval_template_sha256": repository_root / "paper/v3_measurement/author_approval.template.json",
        "candidate_acceptance_matrix_sha256": repository_root / "paper/v3_measurement/CANDIDATE_ACCEPTANCE_MATRIX.md",
        "candidate_claim_evidence_map_sha256": repository_root / "paper/v3_measurement/CANDIDATE_CLAIM_EVIDENCE_MAP.md",
        "candidate_manuscript_template_sha256": repository_root / "paper/v3_measurement/candidate_manuscript_template.json",
        "candidate_submission_metadata_sha256": repository_root / "paper/v3_measurement/CANDIDATE_SUBMISSION_METADATA.md",
        "frozen_protocol_matrix_sha256": repository_root / "research/v3_measurement/ACCEPTANCE_MATRIX.md",
        "full_plan_sha256": repository_root / "research/v3_measurement/full_plan.json",
        "references_sha256": repository_root / "paper/v3_measurement/candidate_references.bib",
        "review_template_sha256": repository_root / "paper/v3_measurement/reviews/review.template.json",
    }
    if any(contract[key] != sha256_path(path) for key, path in expected_hashes.items()):
        raise CandidateProjectionError("candidate contract bound file hash drifted")
    if (
        contract["schema_version"] != "anachron-v3-candidate-contract-v1"
        or contract["frozen_protocol_commit"] != PROTOCOL_COMMIT
        or contract["frozen_protocol_tag"] != PROTOCOL_TAG
        or contract["frozen_protocol_tag_object"] != PROTOCOL_TAG_OBJECT
        or contract["states"] != ["candidate", "local_release"]
        or contract["topology"] != {"development_trajectories": 72, "primary_trajectories": 264, "total_trajectories": 336}
        or contract["tectonic"] != {
            "linux_archive_sha256": "1a715688baf591e650c8aeb160ae934e181685eecbb38b317de30b269ac5d606",
            "linux_executable_sha256": "2b3a86250906c92ed0a3ae8aaa454ec55bd6cede8593b3e549640177f6aecaa3",
            "version": "0.17.0",
            "windows_executable_sha256": "99ffcfdbf1ebf8bdda9e791942e3d06aedb12463fddc33f07de6f5211c8bf08d",
        }
        or contract["source_archive_allowlist"] != ["README.md", "figures/primary_tclr.tex", "main.tex", "references.bib"]
        or contract["local_release_allowlist"] != ["arxiv_metadata.json", "candidate.pdf", "local_release_receipt.json", "source.zip"]
        or contract["outcome_semantics"] != {"analysis_go": "report_only", "paired_difference": "unrestricted_tclr_minus_enforced_tclr", "sign_classes": ["positive", "zero", "negative"]}
    ):
        raise CandidateProjectionError("candidate contract frozen values differ")
    topology = contract["topology"]
    tectonic = contract["tectonic"]
    outcome = contract["outcome_semantics"]
    if (
        type(topology) is not dict
        or any(type(topology[key]) is not int for key in ("development_trajectories", "primary_trajectories", "total_trajectories"))
        or type(tectonic) is not dict
        or set(tectonic) != {"linux_archive_sha256", "linux_executable_sha256", "version", "windows_executable_sha256"}
        or any(type(value) is not str for value in tectonic.values())
        or type(outcome) is not dict
        or set(outcome) != {"analysis_go", "paired_difference", "sign_classes"}
        or type(outcome["analysis_go"]) is not str
        or type(outcome["paired_difference"]) is not str
        or type(outcome["sign_classes"]) is not list
        or any(type(value) is not str for value in outcome["sign_classes"])
    ):
        raise CandidateProjectionError("candidate contract nested types differ")
    if contract["review_lens_ids"] != list(REVIEW_LENS_IDS):
        raise CandidateProjectionError("candidate contract review lenses differ")
    return contract


def _strict_json(raw: bytes, label: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise CandidateProjectionError(f"{label} contains a duplicate key")
            result[key] = value
        return result

    def reject_nonfinite(value: str) -> object:
        raise CandidateProjectionError(f"{label} contains non-finite JSON: {value}")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates, parse_constant=reject_nonfinite)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CandidateProjectionError(f"{label} is not valid UTF-8 JSON") from error


def _is_unsafe(path: Path) -> bool:
    metadata = os.lstat(path)
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE)


def _require_real_directory(path: Path, label: str) -> None:
    metadata = os.lstat(path)
    if _is_unsafe(path) or not stat.S_ISDIR(metadata.st_mode):
        raise CandidateProjectionError(f"{label} must be a real non-reparse directory")


def _normalized_absolute(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _paths_overlap(first: Path, second: Path) -> bool:
    first_absolute, second_absolute = _normalized_absolute(first), _normalized_absolute(second)
    try:
        return os.path.commonpath((first_absolute, second_absolute)) in {first_absolute, second_absolute}
    except ValueError:
        return False


def _require_safe_existing_ancestors(path: Path, label: str) -> None:
    current = path
    while True:
        if os.path.lexists(current):
            _require_real_directory(current, f"{label} ancestor")
        parent = current.parent
        if parent == current:
            return
        current = parent


def require_create_only_output(output: Path, inputs: tuple[Path, ...]) -> None:
    if os.path.lexists(output):
        raise FileExistsError("candidate projection output must not already exist")
    if any(_paths_overlap(output, item) for item in inputs):
        raise CandidateProjectionError("candidate projection output must not overlap an input")
    _require_safe_existing_ancestors(output.parent, "candidate projection output")


def _safe_files(root: Path, label: str) -> tuple[str, ...]:
    _require_real_directory(root, label)
    files: list[str] = []

    def walk(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = Path(entry.path)
                metadata = os.lstat(path)
                relative = path.relative_to(root).as_posix()
                if _is_unsafe(path):
                    raise CandidateProjectionError(f"{label} contains a link or reparse point")
                if stat.S_ISDIR(metadata.st_mode):
                    walk(path)
                elif stat.S_ISREG(metadata.st_mode):
                    files.append(relative)
                else:
                    raise CandidateProjectionError(f"{label} contains a non-regular file")

    walk(root)
    return tuple(files)


def _read_regular_file(path: Path, label: str) -> bytes:
    """Read one admitted regular file without following a late link swap."""
    before = os.lstat(path)
    if _is_unsafe(path) or not stat.S_ISREG(before.st_mode):
        raise CandidateProjectionError(f"{label} must be a regular non-reparse file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise CandidateProjectionError(f"{label} could not be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise CandidateProjectionError(f"{label} changed while it was opened")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.lstat(path)
        if _is_unsafe(path) or not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size):
            raise CandidateProjectionError(f"{label} changed while it was read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _git(root: Path, *arguments: str, allow_failure: bool = False) -> str:
    result = subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True, check=False)
    if result.returncode and not allow_failure:
        raise CandidateProjectionError(f"protocol git {' '.join(arguments)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def verify_detached_protocol_root(protocol_root: Path) -> None:
    """Require the exact frozen tag in a clean detached worktree."""
    _require_real_directory(protocol_root, "protocol root")
    if _git(protocol_root, "status", "--porcelain"):
        raise CandidateProjectionError("protocol root must be clean")
    symbolic = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=protocol_root, capture_output=True, text=True, check=False)
    if symbolic.returncode == 0:
        raise CandidateProjectionError("protocol root must be detached")
    if symbolic.returncode != 1:
        raise CandidateProjectionError("protocol detached-head check failed")
    if _git(protocol_root, "rev-parse", "HEAD") != PROTOCOL_COMMIT:
        raise CandidateProjectionError("protocol root commit differs from the frozen protocol")
    if _git(protocol_root, "rev-parse", f"{PROTOCOL_TAG}^{{tag}}") != PROTOCOL_TAG_OBJECT:
        raise CandidateProjectionError("protocol annotated tag object differs from the frozen protocol")
    if _git(protocol_root, "rev-parse", f"{PROTOCOL_TAG}^{{}}") != PROTOCOL_COMMIT:
        raise CandidateProjectionError("protocol annotated tag does not peel to the frozen commit")
    if _git(protocol_root, "config", "--get", "remote.origin.url") != PROTOCOL_ORIGIN:
        raise CandidateProjectionError("protocol origin URL differs from the frozen protocol")
    if _git(protocol_root, "rev-parse", "refs/heads/master") != PROTOCOL_COMMIT:
        raise CandidateProjectionError("protocol local master differs from the frozen commit")
    remote = _git(protocol_root, "ls-remote", "origin", "refs/heads/master")
    if remote.split(maxsplit=1)[0] != PROTOCOL_COMMIT:
        raise CandidateProjectionError("protocol remote master differs from the frozen commit")
    if not (protocol_root / "tools" / "analyze_v3_measurement.py").is_file():
        raise CandidateProjectionError("protocol analyzer module is missing")


def invoke_frozen_analyzer(protocol_root: Path, evidence: Path) -> dict[str, Any]:
    """Invoke the frozen module CLI; this verifier alone may inspect terminal artifacts."""
    command = [
        sys.executable,
        "-I",
        "-c",
        _ANALYZER_LAUNCHER,
        str(protocol_root),
        str(evidence),
    ]
    result = subprocess.run(command, cwd=protocol_root, capture_output=True, check=False)
    if result.returncode:
        raise CandidateProjectionError(
            f"frozen analyzer failed: {result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    value = _strict_json(result.stdout, "frozen analyzer output")
    if not isinstance(value, dict):
        raise CandidateProjectionError("frozen analyzer output must be an object")
    return value


def _copy_snapshot(source: Path, destination: Path) -> None:
    if destination.exists():
        raise CandidateProjectionError("snapshot destination must not exist")
    files = _safe_files(source, "evidence")
    destination.mkdir()
    for relative in files:
        origin = source / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        contents = _read_regular_file(origin, f"evidence file {relative}")
        with target.open("xb") as writer:
            writer.write(contents)
            writer.flush()
            os.fsync(writer.fileno())


def _file_hashes(root: Path, label: str) -> dict[str, str]:
    return {
        relative: hashlib.sha256(_read_regular_file(root / relative, f"{label} file {relative}")).hexdigest()
        for relative in _safe_files(root, label)
    }


@contextmanager
def admitted_snapshot(protocol_root: Path, evidence: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Verify, create-only copy, and re-verify a caller-owned evidence tree."""
    verify_detached_protocol_root(protocol_root)
    original_hashes = _file_hashes(evidence, "evidence")
    original_analysis = invoke_frozen_analyzer(protocol_root, evidence)
    with tempfile.TemporaryDirectory(prefix="anachron-v3-candidate-") as temporary:
        snapshot = Path(temporary) / "evidence"
        _copy_snapshot(evidence, snapshot)
        if _file_hashes(evidence, "evidence") != original_hashes:
            raise CandidateProjectionError("evidence changed while the snapshot was copied")
        if _file_hashes(snapshot, "evidence snapshot") != original_hashes:
            raise CandidateProjectionError("evidence snapshot does not match the admitted closure")
        analysis = invoke_frozen_analyzer(protocol_root, snapshot)
        if analysis != original_analysis:
            raise CandidateProjectionError("evidence snapshot analysis differs from admitted evidence")
        yield snapshot, analysis


_WORKER = r'''
import json
import sys
from dataclasses import asdict
from pathlib import Path

protocol_root = Path(sys.argv[1]).resolve()
root = Path(sys.argv[2])
sys.path.insert(0, str(protocol_root))

from anachron.core.leakage import ToolInteraction, score_interactions
from anachron.data.v3_corpus import format_search_results, search_v3
import anachron.core.leakage as leakage
import anachron.data.v3_corpus as corpus
import anachron.v3_measurement as measurement

for module in (leakage, corpus, measurement):
    try:
        Path(module.__file__).resolve().relative_to(protocol_root)
    except ValueError as error:
        raise RuntimeError("answer-free worker imported outside the explicit protocol root") from error

plan, _ = measurement.load_plan(root / "plan.json")
rows = []
for trajectory in measurement.expected_trajectories(plan):
    identifier = trajectory["id"]
    response = measurement._strict_json_loads((root / "raw" / (identifier + ".first.response.json")).read_bytes(), "first response")
    query = measurement._validate_first_tool_response(response, trajectory["model"])
    items = corpus.search_v3(query, trajectory["sample"].as_of if trajectory["mode"] == "enforced" else None)
    recorded = (root / "raw" / (identifier + ".tool_result.txt")).read_text(encoding="utf-8")
    if recorded != corpus.format_search_results(items):
        raise ValueError("tool result reconstruction mismatch")
    score = asdict(leakage.score_interactions([leakage.ToolInteraction("anachron_search", query, measurement._query_dates(query), items)], trajectory["sample"].as_of))
    finance_interactions = 1 if score["survivorship_rate"] is not None else 0
    rows.append({
        "mode": trajectory["mode"],
        "model": trajectory["model"],
        "split": "primary" if trajectory["primary"] else "development",
        "repetition": trajectory["repetition"],
        "sample_id": trajectory["sample"].id,
        "score": {
            "finance_interactions": finance_interactions,
            "query_leaks": score["query_leaks"],
            "restatement_leaks": score["restatement_leaks"],
            "result_leaks": score["result_leaks"],
            "survivorship_leaks": score["survivorship_leaks"],
            "total_interactions": score["total_interactions"],
        },
    })
print(json.dumps({"rows": rows}, allow_nan=False, sort_keys=True))
'''


def _load_snapshot_plan(evidence_snapshot: Path) -> dict[str, Any]:
    raw = (evidence_snapshot / "plan.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != FULL_PLAN_SHA256:
        raise CandidateProjectionError("snapshot plan differs from the frozen full plan")
    plan = _strict_json(raw, "snapshot plan")
    if not isinstance(plan, dict) or raw != canonical_json(plan):
        raise CandidateProjectionError("snapshot plan must use canonical JSON bytes")
    return plan


def _require_exact_int(value: object, label: str, permitted: set[int] | None = None) -> int:
    if type(value) is not int or (permitted is not None and value not in permitted):
        raise CandidateProjectionError(f"{label} has an invalid integer value")
    return value


def _validate_answer_free_rows(rows: list[Any], plan: dict[str, Any]) -> None:
    if len(rows) != 336:
        raise CandidateProjectionError("answer-free projection has the wrong trajectory count")
    models = plan.get("models")
    sample_ids = plan.get("sample_ids")
    primary_ids = plan.get("primary_sample_ids")
    repetitions = plan.get("repetitions")
    if not isinstance(models, list) or not isinstance(sample_ids, list) or not isinstance(primary_ids, list) or type(repetitions) is not int:
        raise CandidateProjectionError("snapshot plan has the wrong trajectory schema")
    names = [model.get("name") for model in models if isinstance(model, dict)]
    if len(names) != 2 or any(type(name) is not str for name in names):
        raise CandidateProjectionError("snapshot plan has the wrong model identities")
    expected = {
        ("primary" if sample_id in primary_ids else "development", model, sample_id, repetition, mode)
        for model in names
        for sample_id in sample_ids
        for repetition in range(1, repetitions + 1)
        for mode in ("unrestricted", "enforced")
    }
    observed: set[tuple[str, str, str, int, str]] = set()
    row_keys = {"mode", "model", "repetition", "sample_id", "score", "split"}
    score_keys = {
        "finance_interactions",
        "query_leaks",
        "restatement_leaks",
        "result_leaks",
        "survivorship_leaks",
        "total_interactions",
    }
    for index, row in enumerate(rows):
        if type(row) is not dict or set(row) != row_keys:
            raise CandidateProjectionError("answer-free projection row shape differs")
        split, model, sample_id, repetition, mode = (
            row["split"],
            row["model"],
            row["sample_id"],
            row["repetition"],
            row["mode"],
        )
        if split not in {"primary", "development"} or model not in names or sample_id not in sample_ids or mode not in {"unrestricted", "enforced"}:
            raise CandidateProjectionError("answer-free projection row identity differs")
        _require_exact_int(repetition, f"answer-free row {index}.repetition", set(range(1, repetitions + 1)))
        if split != ("primary" if sample_id in primary_ids else "development"):
            raise CandidateProjectionError("answer-free projection split differs")
        score = row["score"]
        if type(score) is not dict or set(score) != score_keys:
            raise CandidateProjectionError("answer-free projection score shape differs")
        for name in score_keys:
            _require_exact_int(score[name], f"answer-free row {index}.score.{name}", {0, 1})
        if score["total_interactions"] != 1 or score["restatement_leaks"] > score["result_leaks"] or score["survivorship_leaks"] > score["finance_interactions"]:
            raise CandidateProjectionError("answer-free projection score semantics differ")
        identity = (split, model, sample_id, repetition, mode)
        if identity in observed:
            raise CandidateProjectionError("answer-free projection contains a duplicate trajectory identity")
        observed.add(identity)
    if observed != expected:
        raise CandidateProjectionError("answer-free projection trajectory identities differ")


def answer_free_rows(protocol_root: Path, evidence_snapshot: Path) -> list[dict[str, Any]]:
    """Reconstruct scores in a clean frozen-protocol process without terminal artifacts."""
    result = subprocess.run(
        [sys.executable, "-I", "-c", _WORKER, str(protocol_root), str(evidence_snapshot)],
        cwd=protocol_root,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise CandidateProjectionError(
            f"answer-free projection worker failed: {result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    value = _strict_json(result.stdout, "answer-free projection output")
    if not isinstance(value, dict) or set(value) != {"rows"} or not isinstance(value["rows"], list):
        raise CandidateProjectionError("answer-free projection worker returned the wrong shape")
    rows = value["rows"]
    plan = _load_snapshot_plan(evidence_snapshot)
    _validate_answer_free_rows(rows, plan)
    return rows


def _fraction(numerator: int, denominator: int) -> dict[str, int]:
    if type(numerator) is not int or type(denominator) is not int or denominator <= 0:
        raise CandidateProjectionError("projection fraction has invalid integer values")
    value = Fraction(numerator, denominator)
    return {"numerator": value.numerator, "denominator": value.denominator}


def _undefined_or_fraction(numerator: int, denominator: int) -> dict[str, int] | dict[str, str]:
    if denominator == 0:
        return {"undefined": "no_finance_interactions"}
    return _fraction(numerator, denominator)


def _metric_counts(rows: list[dict[str, Any]], metric: str) -> tuple[int, int]:
    if metric == "tclr":
        return sum(row["score"]["result_leaks"] for row in rows), sum(row["score"]["total_interactions"] for row in rows)
    if metric == "query_leakage":
        return sum(row["score"]["query_leaks"] for row in rows), sum(row["score"]["total_interactions"] for row in rows)
    if metric == "restatement_leakage":
        return sum(row["score"]["restatement_leaks"] for row in rows), sum(row["score"]["total_interactions"] for row in rows)
    return sum(row["score"]["survivorship_leaks"] for row in rows), sum(row["score"]["finance_interactions"] for row in rows)


def _rate_as_fraction(value: dict[str, int]) -> Fraction:
    return Fraction(value["numerator"], value["denominator"])


def build_projection(rows: list[dict[str, Any]], analysis: dict[str, Any]) -> dict[str, Any]:
    """Turn answer-free replay rows into the exact canonical candidate projection."""
    if not isinstance(analysis, dict) or (
        analysis.get("plan_id") != "anachron-v3-full-primary-2026-09-03"
        or analysis.get("trajectory_count") != 336
        or analysis.get("primary_trajectory_count") != 264
        or analysis.get("development_trajectory_count") != 72
    ):
        raise CandidateProjectionError("native analysis topology or plan identity differs")
    if len(rows) != 336:
        raise CandidateProjectionError("projection requires exactly 336 trajectories")
    split_rows = {
        "primary": [row for row in rows if row["split"] == "primary"],
        "development": [row for row in rows if row["split"] == "development"],
    }
    if len(split_rows["primary"]) != 264 or len(split_rows["development"]) != 72:
        raise CandidateProjectionError("projection split topology differs from the frozen plan")
    cells: list[dict[str, Any]] = []
    paired: list[dict[str, Any]] = []
    for split, members in split_rows.items():
        models = sorted({row["model"] for row in members})
        for model in [*models, "pooled"]:
            scoped = members if model == "pooled" else [row for row in members if row["model"] == model]
            for mode in ("unrestricted", "enforced"):
                mode_rows = [row for row in scoped if row["mode"] == mode]
                for metric in _METRICS:
                    numerator, denominator = _metric_counts(mode_rows, metric)
                    cells.append({
                        "case_count": len({row["sample_id"] for row in mode_rows}),
                        "count": numerator,
                        "denominator_count": denominator,
                        "denominator_text": "finance-returning tool interactions" if metric == "survivorship_leakage" else "tool interactions",
                        "metric": metric,
                        "mode": mode,
                        "model": model,
                        "model_count": len({row["model"] for row in mode_rows}),
                        "rate": _undefined_or_fraction(numerator, denominator),
                        "repetition_n": len({row["repetition"] for row in mode_rows}),
                        "scope_text": "finite synthetic panel; descriptive only",
                        "split": split,
                        "trajectory_count": len(mode_rows),
                    })
            un = {(row["model"], row["sample_id"], row["repetition"]): row for row in scoped if row["mode"] == "unrestricted"}
            en = {(row["model"], row["sample_id"], row["repetition"]): row for row in scoped if row["mode"] == "enforced"}
            if set(un) != set(en):
                raise CandidateProjectionError("paired trace topology differs between modes")
            differences = [Fraction(left["score"]["result_leaks"], left["score"]["total_interactions"]) - Fraction(en[key]["score"]["result_leaks"], en[key]["score"]["total_interactions"]) for key, left in sorted(un.items())]
            reduction = sum(differences, Fraction()) / len(differences)
            paired.append({
                "model": model,
                "rate": {"numerator": reduction.numerator, "denominator": reduction.denominator},
                "sign_class": "positive" if reduction > 0 else "negative" if reduction < 0 else "zero",
                "split": split,
                "trajectory_pair_count": len(differences),
            })
    primary_paired = {row["model"]: row for row in paired if row["split"] == "primary"}
    native_reductions = analysis.get("model_primary_reductions")
    if not isinstance(native_reductions, dict):
        raise CandidateProjectionError("native analysis does not contain model reductions")
    for model, value in native_reductions.items():
        if model not in primary_paired or type(value) not in (int, float) or float(_rate_as_fraction(primary_paired[model]["rate"])) != value:
            raise CandidateProjectionError("projection does not reconcile native model reductions")
    gates = analysis.get("gates")
    if not isinstance(gates, dict) or type(analysis.get("go")) is not bool:
        raise CandidateProjectionError("native analysis does not contain scientific gates")
    native_equinox = analysis.get("equinox_enforced_survivorship")
    equinox = {
        model: any(
            row["split"] == "primary"
            and row["model"] == model
            and row["mode"] == "enforced"
            and row["sample_id"] == "fin-equinox-2021-06-delisted-before-cutoff"
            and row["score"]["survivorship_leaks"] >= 1
            for row in rows
        )
        for model in native_reductions
    }
    if native_equinox != equinox:
        raise CandidateProjectionError("projection does not reconcile Equinox residual status")
    model_reductions = [_rate_as_fraction(primary_paired[model]["rate"]) for model in native_reductions]
    expected_gates = {
        "all_trajectories_valid": True,
        "minimum_primary_reduction": sum(model_reductions, Fraction()) / len(model_reductions) >= Fraction(1, 5),
        "no_model_negative": all(value >= 0 for value in model_reductions),
        "enforced_equinox_survivorship_each_model": all(equinox.values()),
    }
    if gates != expected_gates or analysis["go"] is not all(expected_gates.values()):
        raise CandidateProjectionError("projection does not reconcile native scientific gates")
    return {
        "analysis_go": analysis["go"],
        "cells": cells,
        "equinox_enforced_survivorship": equinox,
        "paired_tclr_reductions": paired,
        "schema_version": "anachron-v3-candidate-projection-v1",
        "scientific_gates": gates,
        "split_counts": {"development": 72, "primary": 264, "total": 336},
    }


def project_candidate(protocol_root: Path, evidence: Path) -> dict[str, Any]:
    """Admit evidence once and return a projection without retaining a raw snapshot."""
    validate_candidate_contract(Path(__file__).resolve().parent.parent)
    with admitted_snapshot(protocol_root, evidence) as (snapshot, analysis):
        return build_projection(answer_free_rows(protocol_root, snapshot), analysis)
