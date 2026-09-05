"""Derive v3/v4 exclusion dimensions from pinned Git tag blobs only."""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from anachron.v4_contract import canonical_json_bytes
from anachron.v4_paths import V4PathError, admit_repository_root


class V4ComparisonError(ValueError):
    """Raised when a tagged v3/v4 comparison cannot be derived exactly."""


def _git(root: Path, *arguments: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *arguments], check=True, capture_output=True, timeout=30
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise V4ComparisonError("comparison tag blob cannot be read") from error


def _blob(root: Path, tag: str, path: str) -> bytes:
    return _git(root, "show", f"{tag}:{path}")


def _tag_identity(root: Path, tag: str) -> dict[str, str]:
    reference = f"refs/tags/{tag}"
    if _git(root, "cat-file", "-t", reference) != b"tag\n":
        raise V4ComparisonError("comparison requires annotated tags")
    return {
        "tag": tag,
        "tag_object": _git(root, "rev-parse", f"{reference}^{{tag}}").decode().strip(),
        "tag_peeled": _git(root, "rev-parse", f"{reference}^{{}}").decode().strip(),
    }


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _v3(root: Path, tag: str) -> dict[str, list[str]]:
    samples = _blob(root, tag, "anachron/data/v3_samples.py").decode("utf-8")
    corpus = _blob(root, tag, "anachron/data/v3_corpus.py").decode("utf-8")
    sample_tree = ast.parse(samples)
    corpus_tree = ast.parse(corpus)
    sample_rows = [
        node.args
        for node in ast.walk(sample_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_sample"
    ]
    corpus_rows = [
        node.args
        for node in ast.walk(corpus_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "CorpusItem"
    ]
    if (
        not sample_rows
        or not corpus_rows
        or any(len(row) != 5 for row in sample_rows)
        or any(len(row) < 2 for row in corpus_rows)
        or any(not isinstance(item, ast.Constant) for row in sample_rows for item in row)
        or any(
            not isinstance(row[index], ast.Constant)
            for row in corpus_rows
            for index in (0, 1)
        )
        or any(len(row) > 3 and not isinstance(row[3], ast.Constant) for row in corpus_rows)
    ):
        raise V4ComparisonError("comparison v3 tagged source topology differs")
    prompts = []
    entities = set()
    for row in sample_rows:
        _identifier, cutoff, instruction, _, _target = (item.value for item in row[:5])
        prompts.append(_sha(f"As of {cutoff}, {instruction} Use the search tool exactly once and rely only on information dated on or before {cutoff}.".encode()))
    for row in corpus_rows:
        entity = row[3].value if len(row) > 3 else None
        if type(entity) is str:
            entities.add(entity)
    return {
        "case_ids": sorted(row[0].value for row in sample_rows),
        "corpus_ids": sorted(row[0].value for row in corpus_rows),
        "entity_identifiers": sorted(entities),
        "prompt_sha256": sorted(prompts),
        "record_text_sha256": sorted(_sha(row[1].value.encode()) for row in corpus_rows),
    }


def _v4(root: Path, tag: str) -> dict[str, list[str]]:
    registry = json.loads(_blob(root, tag, "research/v4_measurement/case_registry.json"))
    cards = []
    for row in registry["cases"]:
        path = f"research/v4_measurement/{row['case_card']}"
        raw = _blob(root, tag, path)
        card = json.loads(raw)
        cards.append((path.removeprefix("research/v4_measurement/"), raw, card))
    return {
        "case_ids": sorted(card[2]["case_id"] for card in cards),
        "corpus_ids": sorted(record["id"] for _, _, card in cards for record in card["corpus_records"]),
        "entity_identifiers": sorted(card[2]["entity_identifier"] for card in cards),
        "prompt_sha256": sorted(_sha(card[2]["prompt"].encode()) for card in cards),
        "record_text_sha256": sorted(_sha(record["text"].encode()) for _, _, card in cards for record in card["corpus_records"]),
    }


def derive(repository_root: Path, *, v3_tag: str, v4_tag: str) -> dict[str, Any]:
    """Return canonical dimensions/intersections from immutable tagged bytes."""

    try:
        root = admit_repository_root(repository_root)
    except V4PathError as error:
        raise V4ComparisonError(str(error)) from error
    v3_identity = _tag_identity(root, v3_tag)
    v4_identity = _tag_identity(root, v4_tag)
    v3, v4 = _v3(root, v3_tag), _v4(root, v4_tag)
    intersections = {key: sorted(set(v3[key]) & set(v4[key])) for key in v3}
    return {
        "intersections": intersections,
        "no_overlap_assertions": {f"{key}_empty": not values for key, values in intersections.items()},
        "pinned_refs": {"v3": v3_identity, "v4": v4_identity},
        "schema_version": "anachron-v4-tag-blob-comparison-v2",
        "v3": v3,
        "v4": v4,
    }


def derive_bytes(repository_root: Path, *, v3_tag: str, v4_tag: str) -> bytes:
    return canonical_json_bytes(derive(repository_root, v3_tag=v3_tag, v4_tag=v4_tag))
