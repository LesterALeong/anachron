"""Reproducibly prepare pending-human Routes v1 curation drafts from anchors."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from anachron.routes.manifest import (
    ManifestValidationError,
    canonical_json_sha256,
    stable_item_id,
    validate_curation_draft,
)
from anachron.routes.schema import validate_contract_document
from anachron.routes.sources import SourceDiscoveryError, validate_exante_sampling_frame


class CurationInputError(ValueError):
    """Raised when a manual curation input cannot produce a reproducible draft."""


def _mapping(value: Any, path: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise CurationInputError(f"{path} has missing or extra fields")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise CurationInputError(f"{path} must be a non-empty string")
    return value


def _phase_topics(contract: dict[str, Any], phase: str) -> dict[str, int]:
    if phase not in {"pilot", "full"}:
        raise CurationInputError("curation input study_phase must be pilot or full")
    group = "pilot" if phase == "pilot" else "extension"
    return {
        item["title"]: item["cutoff_year"]
        for item in contract["sampling"]["topics"][group]
    }


def _read_discovery_artifacts(
    directory: str | Path, phase_topics: dict[str, int]
) -> dict[str, tuple[dict[str, Any], str]]:
    root = Path(directory)
    if not root.is_dir():
        raise CurationInputError("discovery directory does not exist")
    artifacts: dict[str, tuple[dict[str, Any], str]] = {}
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.suffix != ".json":
            raise CurationInputError("discovery directory must contain only JSON artifacts")
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CurationInputError(f"unable to load discovery artifact {path.name}: {error}") from error
        if not isinstance(artifact, dict):
            raise CurationInputError(f"discovery artifact {path.name} must be an object")
        title = artifact.get("title")
        cutoff_year = artifact.get("cutoff_year")
        if not isinstance(title, str) or phase_topics.get(title) != cutoff_year:
            raise CurationInputError(
                f"discovery artifact {path.name} is not declared for the requested phase"
            )
        if title in artifacts:
            raise CurationInputError("discovery directory contains duplicate topic artifacts")
        artifacts[title] = (artifact, path.name)
    return artifacts


def _occurrences(content: str, anchor: str) -> int:
    return sum(content.startswith(anchor, index) for index in range(len(content)))


def _snippet(content: str, anchor: str, context: int, maximum: int) -> str:
    if _occurrences(content, anchor) != 1:
        raise CurationInputError("anchor must occur exactly once in its source snapshot")
    offset = content.index(anchor)
    start = max(0, offset - context)
    end = min(len(content), offset + len(anchor) + context)
    if end - start > maximum:
        raise CurationInputError("anchor context window exceeds the frozen snippet maximum")
    prior_newline = content.rfind("\n", 0, start)
    following_newline = content.find("\n", end)
    expanded_start = start
    expanded_end = end
    if prior_newline >= 0:
        expanded_start = prior_newline + 1
    if following_newline >= 0:
        expanded_end = following_newline + 1
    if expanded_end - expanded_start <= maximum:
        start, end = expanded_start, expanded_end
    snippet = content[start:end]
    return snippet


def _evidence(revision: dict[str, Any], snippet: str, cutoff_year: int) -> dict[str, Any]:
    return {
        "revision_id": revision["revision_id"],
        "timestamp": revision["timestamp"],
        "revision_url": revision["revision_url"],
        "mediawiki_sha1": revision["mediawiki_sha1"],
        "raw_response_sha256": revision["raw_response_sha256"],
        "content_sha256": revision["content_sha256"],
        "snippet": snippet,
        "snippet_sha256": "sha256:" + hashlib.sha256(snippet.encode("utf-8")).hexdigest(),
        "displayed_document_date": f"{cutoff_year}-12-31",
    }


def _validate_input(
    curation_input: Any, contract: dict[str, Any]
) -> tuple[dict[str, Any], str, dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    value = _mapping(
        curation_input,
        "curation input",
        {"schema_version", "study_phase", "rejected_topics", "entries"},
    )
    if value["schema_version"] != "routes-v1-curation-input":
        raise CurationInputError("curation input schema_version is invalid")
    phase = _string(value["study_phase"], "curation input study_phase")
    phase_topics = _phase_topics(contract, phase)
    if not isinstance(value["entries"], list) or not isinstance(value["rejected_topics"], list):
        raise CurationInputError("curation input entries and rejected_topics must be lists")
    entries: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(value["entries"]):
        item = _mapping(
            entry,
            f"curation input entries[{index}]",
            {
                "title",
                "question",
                "pre_answer_aliases",
                "post_answer_aliases",
                "pre_anchor",
                "post_anchor",
                "change_type",
                "semantic_strength",
                "notes",
            },
        )
        title = _string(item["title"], f"curation input entries[{index}].title")
        if title not in phase_topics or title in entries:
            raise CurationInputError("curation input entries contain a duplicate or unexpected topic")
        _string(item["question"], f"curation input entries[{index}].question")
        _string(item["pre_anchor"], f"curation input entries[{index}].pre_anchor")
        _string(item["post_anchor"], f"curation input entries[{index}].post_anchor")
        _string(item["notes"], f"curation input entries[{index}].notes")
        if item["change_type"] not in {
            "event_status",
            "count_or_statistic",
            "estimate",
            "correction",
            "article_state",
        }:
            raise CurationInputError("curation input change_type is invalid")
        if item["semantic_strength"] not in {"clean", "weaker"}:
            raise CurationInputError("curation input semantic_strength is invalid")
        if not isinstance(item["pre_answer_aliases"], list) or not isinstance(
            item["post_answer_aliases"], list
        ):
            raise CurationInputError("curation input answer aliases must be lists")
        entries[title] = item
    rejected: dict[str, dict[str, Any]] = {}
    for index, rejected_topic in enumerate(value["rejected_topics"]):
        item = _mapping(
            rejected_topic,
            f"curation input rejected_topics[{index}]",
            {"title", "reason"},
        )
        title = _string(item["title"], f"curation input rejected_topics[{index}].title")
        _string(item["reason"], f"curation input rejected_topics[{index}].reason")
        if title not in phase_topics or title in rejected:
            raise CurationInputError("curation input rejections contain a duplicate or unexpected topic")
        rejected[title] = item
    if set(entries) & set(rejected) or set(entries) | set(rejected) != set(phase_topics):
        raise CurationInputError("curation input must account for every declared topic exactly once")
    return value, phase, entries, rejected


def prepare_draft(
    curation_input: Any,
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
    discovery_directory: str | Path,
) -> dict[str, Any]:
    """Build a deterministic pending-human draft from exact, unique source anchors."""
    contract = validate_contract_document(contract)
    try:
        validate_exante_sampling_frame(contract, sampling_frame)
    except SourceDiscoveryError as error:
        raise CurationInputError(f"sampling frame is invalid: {error}") from error
    value, phase, entries, rejected = _validate_input(curation_input, contract)
    phase_topics = _phase_topics(contract, phase)
    artifacts = _read_discovery_artifacts(discovery_directory, phase_topics)
    if not set(entries).issubset(artifacts):
        raise CurationInputError("each accepted curation entry requires a discovery artifact")
    frame_hash = canonical_json_sha256(sampling_frame)
    input_hash = canonical_json_sha256(value)
    context = contract["source_selection"]["snippet_context_chars_each_side"]
    maximum = contract["source_selection"]["snippet_max_chars"]
    pairs: list[dict[str, Any]] = []
    for title in sorted(entries):
        entry = entries[title]
        artifact, artifact_filename = artifacts[title]
        pre_revision = artifact.get("strict_revision")
        post_revision = artifact.get("post_snapshot")
        if not isinstance(pre_revision, dict) or not isinstance(post_revision, dict):
            raise CurationInputError("discovery artifact lacks strict or post revision content")
        pre_content = pre_revision.get("content")
        post_content = post_revision.get("content")
        if not isinstance(pre_content, str) or not isinstance(post_content, str):
            raise CurationInputError("discovery artifact revision content is invalid")
        pre_anchor = entry["pre_anchor"]
        post_anchor = entry["post_anchor"]
        if _occurrences(post_content, pre_anchor) != 0 or _occurrences(pre_content, post_anchor) != 0:
            raise CurationInputError("anchors must not appear in the opposite source snapshot")
        cutoff_year = phase_topics[title]
        item_id = stable_item_id(phase, title, cutoff_year)
        pairs.append(
            {
                "item_id": item_id,
                "topic_cluster_id": item_id,
                "study_phase": phase,
                "topic": title,
                "cutoff_year": cutoff_year,
                "sampling_frame_sha256": frame_hash,
                "curation_input_sha256": input_hash,
                "discovery_artifact_sha256": canonical_json_sha256(artifact),
                "discovery_artifact_file": artifact_filename,
                "source_status": "source_valid",
                "post_snapshot_horizon_days": contract["source_selection"]["post_snapshot_horizon_days"],
                "pre": _evidence(
                    pre_revision, _snippet(pre_content, pre_anchor, context, maximum), cutoff_year
                ),
                "post": _evidence(
                    post_revision,
                    _snippet(post_content, post_anchor, context, maximum),
                    cutoff_year,
                ),
                "pre_anchor": pre_anchor,
                "post_anchor": post_anchor,
                "question": entry["question"],
                "pre_answer_aliases": entry["pre_answer_aliases"],
                "post_answer_aliases": entry["post_answer_aliases"],
                "change_type": entry["change_type"],
                "semantic_strength": entry["semantic_strength"],
                "notes": entry["notes"],
                "license_attribution": {
                    "license": "CC BY-SA 4.0",
                    "source_family": "English Wikipedia",
                    "attribution_text": f"English Wikipedia contributors, {title} revision history.",
                },
                "curation": {
                    "status": "codex_prepared_pending_human",
                    "human_validator_id": None,
                    "human_validated_at": None,
                },
            }
        )
    draft = {
        "schema_version": "routes-v1-curation-draft",
        "sampling_frame_sha256": frame_hash,
        "curation_input_sha256": input_hash,
        "pairs": pairs,
        "rejected_topics": [
            {"study_phase": phase, "title": title, "reason": rejected[title]["reason"]}
            for title in sorted(rejected)
        ],
    }
    try:
        validate_curation_draft(draft, contract, sampling_frame)
    except ManifestValidationError as error:
        raise CurationInputError(f"prepared draft is invalid: {error}") from error
    return draft
