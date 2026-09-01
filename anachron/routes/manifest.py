"""Fail-closed curation-draft and sealed source-pair manifest handling."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from anachron.routes.schema import (
    ContractValidationError,
    load_contract,
    validate_contract_document,
)
from anachron.routes.sources import SourceDiscoveryError, validate_exante_sampling_frame

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


class ManifestValidationError(ValueError):
    """Raised when a Routes v1 source pair cannot enter a sealed manifest."""


def canonical_json_sha256(value: Any) -> str:
    """Return the stable SHA-256 identity used to bind JSON evidence."""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _mapping(value: Any, path: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestValidationError(f"{path} must be an object")
    actual = set(value)
    if actual != fields:
        raise ManifestValidationError(
            f"{path} fields differ; missing={sorted(fields - actual)}, extra={sorted(actual - fields)}"
        )
    return value


def _string(value: Any, path: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestValidationError(f"{path} must be a non-empty string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ManifestValidationError(f"{path} has an invalid format")
    return value


def _integer(value: Any, path: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ManifestValidationError(f"{path} must be an integer >= {minimum}")
    return value


def _utc_timestamp(value: Any, path: str) -> datetime:
    timestamp = _string(value, path, _UTC_TIMESTAMP)
    try:
        parsed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise ManifestValidationError(f"{path} must be a canonical UTC timestamp") from error
    if parsed.isoformat().replace("+00:00", "Z") != timestamp:
        raise ManifestValidationError(f"{path} must be a canonical UTC timestamp")
    return parsed


def _normalized(value: Any, path: str) -> str:
    text = _string(value, path)
    normalized = " ".join(unicodedata.normalize("NFC", text).casefold().split())
    if not normalized:
        raise ManifestValidationError(f"{path} must not normalize to empty")
    return normalized


def _oldid_url(url: Any, path: str, title: str, revision_id: int) -> None:
    value = _string(url, path)
    parsed = urlparse(value)
    query = parse_qs(parsed.query, strict_parsing=True)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "en.wikipedia.org"
        or parsed.path != "/w/index.php"
        or parsed.fragment
        or set(query) != {"title", "oldid"}
        or query["title"] != [title]
        or query["oldid"] != [str(revision_id)]
    ):
        raise ManifestValidationError(f"{path} must be an immutable oldid URL")


def stable_item_id(phase: str, title: str, cutoff_year: int) -> str:
    """Derive the immutable item and topic-cluster identifier for one topic pair."""
    identity = f"{phase}\0{unicodedata.normalize('NFC', title)}\0{cutoff_year}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"routes-v1:{phase}:{cutoff_year}:{digest}"


def _contract_topics(contract: dict[str, Any], phase: str) -> dict[str, int]:
    if phase not in {"pilot", "full"}:
        raise ManifestValidationError("pair.study_phase must be pilot or full")
    group = "pilot" if phase == "pilot" else "extension"
    return {
        topic["title"]: topic["cutoff_year"]
        for topic in contract["sampling"]["topics"][group]
    }


def _validate_artifact_revision(
    revision: Any, path: str, title: str
) -> tuple[dict[str, Any], datetime]:
    value = _mapping(
        revision,
        path,
        {
            "revision_id",
            "timestamp",
            "mediawiki_sha1",
            "revision_url",
            "raw_response_sha256",
            "content_sha256",
            "content",
        },
    )
    revision_id = _integer(value["revision_id"], f"{path}.revision_id")
    timestamp = _utc_timestamp(value["timestamp"], f"{path}.timestamp")
    _string(value["mediawiki_sha1"], f"{path}.mediawiki_sha1", _SHA1)
    _oldid_url(value["revision_url"], f"{path}.revision_url", title, revision_id)
    _string(value["raw_response_sha256"], f"{path}.raw_response_sha256", _SHA256)
    _string(value["content_sha256"], f"{path}.content_sha256", _SHA256)
    content = _string(value["content"], f"{path}.content")
    if "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest() != value["content_sha256"]:
        raise ManifestValidationError(f"{path}.content_sha256 does not match content")
    if hashlib.sha1(content.encode("utf-8")).hexdigest() != value["mediawiki_sha1"]:
        raise ManifestValidationError(f"{path}.mediawiki_sha1 does not match content")
    return value, timestamp


def _validate_discovery_artifact(
    artifact: Any, contract: dict[str, Any], phase: str, title: str, cutoff_year: int
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    value = _mapping(
        artifact,
        "pair.discovery_artifact",
        {
            "schema_version",
            "title",
            "cutoff_year",
            "boundary_timestamp",
            "strict_revision",
            "post_snapshot_horizon_days",
            "post_snapshot",
            "snapshot_diff",
        },
    )
    if value["schema_version"] != "routes-v1-source-discovery":
        raise ManifestValidationError("pair.discovery_artifact.schema_version is invalid")
    if value["title"] != title or value["cutoff_year"] != cutoff_year:
        raise ManifestValidationError("pair.discovery_artifact topic binding is invalid")
    boundary = _utc_timestamp(value["boundary_timestamp"], "pair.discovery_artifact.boundary_timestamp")
    expected_boundary = datetime(cutoff_year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    if boundary != expected_boundary:
        raise ManifestValidationError("pair.discovery_artifact boundary does not match cutoff")
    if value["post_snapshot_horizon_days"] != contract["source_selection"]["post_snapshot_horizon_days"]:
        raise ManifestValidationError("pair.discovery_artifact horizon does not match contract")
    strict, strict_time = _validate_artifact_revision(
        value["strict_revision"], "pair.discovery_artifact.strict_revision", title
    )
    post, post_time = _validate_artifact_revision(
        value["post_snapshot"], "pair.discovery_artifact.post_snapshot", title
    )
    if strict_time > boundary or post_time <= boundary:
        raise ManifestValidationError("pair.discovery_artifact violates strict/post boundaries")
    if post_time > boundary + timedelta(
        days=contract["source_selection"]["post_snapshot_horizon_days"]
    ):
        raise ManifestValidationError("pair.discovery_artifact post snapshot exceeds 365-day horizon")
    if strict["revision_id"] == post["revision_id"]:
        raise ManifestValidationError("pair.discovery_artifact repeats its strict revision")
    expected_diff = "".join(
        difflib.unified_diff(
            strict["content"].splitlines(keepends=True),
            post["content"].splitlines(keepends=True),
            fromfile=f"oldid:{strict['revision_id']}",
            tofile=f"oldid:{post['revision_id']}",
        )
    )
    if value["snapshot_diff"] != expected_diff:
        raise ManifestValidationError("pair.discovery_artifact snapshot_diff does not match content")
    return value, strict, post


def _validate_evidence(
    evidence: Any, path: str, topic: str, cutoff_year: int, max_snippet_chars: int
) -> dict[str, Any]:
    value = _mapping(
        evidence,
        path,
        {
            "revision_id",
            "timestamp",
            "revision_url",
            "mediawiki_sha1",
            "raw_response_sha256",
            "content_sha256",
            "snippet",
            "snippet_sha256",
            "displayed_document_date",
        },
    )
    revision_id = _integer(value["revision_id"], f"{path}.revision_id")
    _utc_timestamp(value["timestamp"], f"{path}.timestamp")
    _oldid_url(value["revision_url"], f"{path}.revision_url", topic, revision_id)
    _string(value["mediawiki_sha1"], f"{path}.mediawiki_sha1", _SHA1)
    _string(value["raw_response_sha256"], f"{path}.raw_response_sha256", _SHA256)
    _string(value["content_sha256"], f"{path}.content_sha256", _SHA256)
    snippet = _string(value["snippet"], f"{path}.snippet")
    if len(snippet) > max_snippet_chars:
        raise ManifestValidationError(f"{path}.snippet exceeds the frozen maximum")
    expected_hash = "sha256:" + hashlib.sha256(snippet.encode("utf-8")).hexdigest()
    if value["snippet_sha256"] != expected_hash:
        raise ManifestValidationError(f"{path}.snippet_sha256 does not match snippet")
    if value["displayed_document_date"] != f"{cutoff_year}-12-31":
        raise ManifestValidationError(f"{path}.displayed_document_date does not match cutoff")
    return value


def _verify_evidence_against_source(
    evidence: dict[str, Any], source_revision: dict[str, Any], path: str
) -> None:
    for field in (
        "revision_id",
        "timestamp",
        "revision_url",
        "mediawiki_sha1",
        "raw_response_sha256",
        "content_sha256",
    ):
        if evidence[field] != source_revision[field]:
            raise ManifestValidationError(f"{path}.{field} does not bind its discovery artifact")
    if evidence["snippet"] not in source_revision["content"]:
        raise ManifestValidationError(f"{path}.snippet is not a source substring")


def _normalized_aliases(value: Any, path: str) -> set[str]:
    if not isinstance(value, list) or not value:
        raise ManifestValidationError(f"{path} must be a non-empty list")
    aliases = {_normalized(alias, f"{path}[{index}]") for index, alias in enumerate(value)}
    if len(aliases) != len(value):
        raise ManifestValidationError(f"{path} contains duplicate normalized aliases")
    return aliases


def _validate_curation(value: Any, path: str, require_human: bool) -> None:
    curation = _mapping(value, path, {"status", "human_validator_id", "human_validated_at"})
    status = curation["status"]
    if status == "codex_prepared_pending_human":
        if curation["human_validator_id"] is not None or curation["human_validated_at"] is not None:
            raise ManifestValidationError(f"{path} pending Codex curation cannot claim human validation")
        if require_human:
            raise ManifestValidationError(f"{path} requires human validation before sealing")
        return
    if status != "human_validated":
        raise ManifestValidationError(f"{path}.status is invalid")
    _string(curation["human_validator_id"], f"{path}.human_validator_id")
    _utc_timestamp(curation["human_validated_at"], f"{path}.human_validated_at")


def _validate_pair(
    pair: Any,
    contract: dict[str, Any],
    sampling_frame_sha256: str,
    require_human: bool,
    draft: bool,
) -> dict[str, Any]:
    fields = {
        "item_id",
        "topic_cluster_id",
        "study_phase",
        "topic",
        "cutoff_year",
        "sampling_frame_sha256",
        "curation_input_sha256",
        "discovery_artifact_sha256",
        "source_status",
        "post_snapshot_horizon_days",
        "pre",
        "post",
        "pre_anchor",
        "post_anchor",
        "question",
        "pre_answer_aliases",
        "post_answer_aliases",
        "change_type",
        "semantic_strength",
        "notes",
        "license_attribution",
        "curation",
    }
    if draft:
        fields.add("discovery_artifact_file")
    value = _mapping(
        pair,
        "pair",
        fields,
    )
    phase = _string(value["study_phase"], "pair.study_phase")
    topic = _string(value["topic"], "pair.topic")
    cutoff_year = _integer(value["cutoff_year"], "pair.cutoff_year")
    if _contract_topics(contract, phase).get(topic) != cutoff_year:
        raise ManifestValidationError("pair topic and cutoff_year are not declared together")
    expected_item_id = stable_item_id(phase, topic, cutoff_year)
    if value["item_id"] != expected_item_id or value["topic_cluster_id"] != expected_item_id:
        raise ManifestValidationError("pair item_id and topic_cluster_id must be stable topic ids")
    if value["sampling_frame_sha256"] != sampling_frame_sha256:
        raise ManifestValidationError("pair does not bind the validated sampling frame hash")
    _string(value["curation_input_sha256"], "pair.curation_input_sha256", _SHA256)
    _string(value["discovery_artifact_sha256"], "pair.discovery_artifact_sha256", _SHA256)
    _string(value["pre_anchor"], "pair.pre_anchor")
    _string(value["post_anchor"], "pair.post_anchor")
    if draft:
        artifact_file = _string(value["discovery_artifact_file"], "pair.discovery_artifact_file")
        if artifact_file != Path(artifact_file).name or Path(artifact_file).suffix != ".json":
            raise ManifestValidationError("pair.discovery_artifact_file must be a JSON basename")
    if value["source_status"] != "source_valid":
        raise ManifestValidationError("source-ineligible entries cannot enter a runnable manifest")
    if value["post_snapshot_horizon_days"] != contract["source_selection"]["post_snapshot_horizon_days"]:
        raise ManifestValidationError("pair post_snapshot_horizon_days does not match contract")
    max_snippet_chars = contract["source_selection"]["snippet_max_chars"]
    _validate_evidence(value["pre"], "pair.pre", topic, cutoff_year, max_snippet_chars)
    _validate_evidence(value["post"], "pair.post", topic, cutoff_year, max_snippet_chars)
    question = _normalized(value["question"], "pair.question")
    pre_aliases = _normalized_aliases(value["pre_answer_aliases"], "pair.pre_answer_aliases")
    post_aliases = _normalized_aliases(value["post_answer_aliases"], "pair.post_answer_aliases")
    if pre_aliases & post_aliases:
        raise ManifestValidationError("pair pre and post answer aliases overlap")
    if any(alias in question for alias in pre_aliases | post_aliases):
        raise ManifestValidationError("pair.question leaks an answer alias")
    if value["change_type"] not in {
        "event_status",
        "count_or_statistic",
        "estimate",
        "correction",
        "article_state",
    }:
        raise ManifestValidationError("pair.change_type is invalid")
    if value["semantic_strength"] not in {"clean", "weaker"}:
        raise ManifestValidationError("pair.semantic_strength is invalid")
    _string(value["notes"], "pair.notes")
    attribution = _mapping(
        value["license_attribution"],
        "pair.license_attribution",
        {"license", "source_family", "attribution_text"},
    )
    if attribution["license"] != "CC BY-SA 4.0" or attribution["source_family"] != "English Wikipedia":
        raise ManifestValidationError("pair must carry English Wikipedia CC BY-SA 4.0 attribution")
    _string(attribution["attribution_text"], "pair.license_attribution.attribution_text")
    _validate_curation(value["curation"], "pair.curation", require_human)
    return value


def _validate_container(
    document: Any,
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
    schema_version: str,
    require_human: bool,
) -> dict[str, Any]:
    contract = validate_contract_document(contract)
    try:
        validate_exante_sampling_frame(contract, sampling_frame)
    except SourceDiscoveryError as error:
        raise ManifestValidationError(f"sampling frame is invalid: {error}") from error
    value = _mapping(
        document,
        "manifest",
        {
            "schema_version",
            "sampling_frame_sha256",
            "curation_input_sha256",
            "pairs",
            "rejected_topics",
        },
    )
    if value["schema_version"] != schema_version:
        raise ManifestValidationError("manifest.schema_version is invalid")
    is_draft = schema_version == "routes-v1-curation-draft"
    frame_hash = canonical_json_sha256(sampling_frame)
    if value["sampling_frame_sha256"] != frame_hash:
        raise ManifestValidationError("manifest does not bind the validated sampling frame hash")
    _string(value["curation_input_sha256"], "manifest.curation_input_sha256", _SHA256)
    if not isinstance(value["pairs"], list) or not value["pairs"]:
        raise ManifestValidationError("manifest.pairs must be a non-empty list")
    item_ids: set[str] = set()
    topics_by_phase: dict[str, set[str]] = {"pilot": set(), "full": set()}
    for pair in value["pairs"]:
        checked = _validate_pair(pair, contract, frame_hash, require_human, is_draft)
        if checked["curation_input_sha256"] != value["curation_input_sha256"]:
            raise ManifestValidationError("pair curation_input_sha256 does not match manifest")
        if checked["item_id"] in item_ids:
            raise ManifestValidationError("manifest contains duplicate item ids")
        phase_topics = topics_by_phase[checked["study_phase"]]
        if checked["topic"] in phase_topics:
            raise ManifestValidationError("manifest contains pilot/full topic overlap")
        item_ids.add(checked["item_id"])
        phase_topics.add(checked["topic"])
    rejected_by_phase: dict[str, set[str]] = {"pilot": set(), "full": set()}
    if not isinstance(value["rejected_topics"], list):
        raise ManifestValidationError("manifest.rejected_topics must be a list")
    for index, rejected in enumerate(value["rejected_topics"]):
        item = _mapping(
            rejected,
            f"manifest.rejected_topics[{index}]",
            {"study_phase", "title", "reason"},
        )
        phase = _string(item["study_phase"], f"manifest.rejected_topics[{index}].study_phase")
        title = _string(item["title"], f"manifest.rejected_topics[{index}].title")
        _string(item["reason"], f"manifest.rejected_topics[{index}].reason")
        if title not in _contract_topics(contract, phase):
            raise ManifestValidationError("manifest rejection topic is not declared for its phase")
        if title in rejected_by_phase[phase] or title in topics_by_phase[phase]:
            raise ManifestValidationError("manifest contains duplicate or both accepted/rejected topics")
        rejected_by_phase[phase].add(title)
    for phase in ("pilot", "full"):
        accounted = topics_by_phase[phase] | rejected_by_phase[phase]
        if not accounted:
            continue
        expected = set(_contract_topics(contract, phase))
        if accounted != expected:
            raise ManifestValidationError("manifest does not account for every declared phase topic")
    return value


def validate_curation_draft(
    draft: Any, contract: dict[str, Any], sampling_frame: dict[str, Any]
) -> dict[str, Any]:
    """Validate a manual draft without treating pending curation as human validation."""
    return _validate_container(
        draft, contract, sampling_frame, "routes-v1-curation-draft", require_human=False
    )


def _load_local_discovery_artifact(
    directory: str | Path, filename: str
) -> dict[str, Any]:
    path = Path(directory) / filename
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestValidationError(
            f"unable to load local discovery artifact {filename}: {error}"
        ) from error
    if not isinstance(artifact, dict):
        raise ManifestValidationError("local discovery artifact must be an object")
    return artifact


def _occurrences(content: str, anchor: str) -> int:
    return sum(content.startswith(anchor, index) for index in range(len(content)))


def _verify_pair_against_artifact(
    pair: dict[str, Any], artifact: dict[str, Any], contract: dict[str, Any]
) -> None:
    checked_artifact, strict, post = _validate_discovery_artifact(
        artifact,
        contract,
        pair["study_phase"],
        pair["topic"],
        pair["cutoff_year"],
    )
    if pair["discovery_artifact_sha256"] != canonical_json_sha256(checked_artifact):
        raise ManifestValidationError("pair discovery artifact hash does not match local artifact")
    _verify_evidence_against_source(pair["pre"], strict, "pair.pre")
    _verify_evidence_against_source(pair["post"], post, "pair.post")
    if (
        _occurrences(strict["content"], pair["pre_anchor"]) != 1
        or _occurrences(post["content"], pair["post_anchor"]) != 1
        or _occurrences(post["content"], pair["pre_anchor"]) != 0
        or _occurrences(strict["content"], pair["post_anchor"]) != 0
    ):
        raise ManifestValidationError("pair anchors no longer prove a clean source split")


def validate_curation_draft_with_discovery(
    draft: Any,
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
    discovery_directory: str | Path,
) -> dict[str, Any]:
    """Re-verify a curation draft against its local, ignored source artifacts."""
    checked = validate_curation_draft(draft, contract, sampling_frame)
    directory = Path(discovery_directory)
    if not directory.is_dir():
        raise ManifestValidationError("discovery directory does not exist")
    for pair in checked["pairs"]:
        artifact = _load_local_discovery_artifact(directory, pair["discovery_artifact_file"])
        _verify_pair_against_artifact(pair, artifact, contract)
    return checked


def seal_manifest(
    draft: Any,
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
    discovery_directory: str | Path,
) -> dict[str, Any]:
    """Re-verify local discovery artifacts and return a portable runnable manifest."""
    validated = _validate_container(
        draft, contract, sampling_frame, "routes-v1-curation-draft", require_human=True
    )
    for pair in validated["pairs"]:
        artifact = _load_local_discovery_artifact(
            discovery_directory, pair["discovery_artifact_file"]
        )
        _verify_pair_against_artifact(pair, artifact, contract)
    manifest = {
        "schema_version": "routes-v1-source-manifest",
        "sampling_frame_sha256": validated["sampling_frame_sha256"],
        "curation_input_sha256": validated["curation_input_sha256"],
        "pairs": [
            {
                key: value
                for key, value in pair.items()
                if key != "discovery_artifact_file"
            }
            for pair in validated["pairs"]
        ],
        "rejected_topics": validated["rejected_topics"],
    }
    validate_manifest(manifest, contract, sampling_frame)
    return manifest


def validate_manifest(
    manifest: Any, contract: dict[str, Any], sampling_frame: dict[str, Any]
) -> dict[str, Any]:
    """Validate a sealed, runnable source-pair manifest."""
    return _validate_container(
        manifest, contract, sampling_frame, "routes-v1-source-manifest", require_human=True
    )


def validate_manifest_with_discovery(
    manifest: Any,
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
    discovery_directory: str | Path,
) -> dict[str, Any]:
    """Re-verify a portable manifest against local, ignored discovery artifacts."""
    checked = validate_manifest(manifest, contract, sampling_frame)
    directory = Path(discovery_directory)
    if not directory.is_dir():
        raise ManifestValidationError("discovery directory does not exist")
    artifacts_by_hash: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix != ".json":
            raise ManifestValidationError("discovery directory must contain only JSON artifacts")
        artifact = _load_local_discovery_artifact(directory, path.name)
        artifacts_by_hash.setdefault(canonical_json_sha256(artifact), []).append(artifact)
    for pair in checked["pairs"]:
        matches = artifacts_by_hash.get(pair["discovery_artifact_sha256"], [])
        if len(matches) != 1:
            raise ManifestValidationError(
                "sealed manifest does not bind exactly one local discovery artifact"
            )
        _verify_pair_against_artifact(pair, matches[0], contract)
    return checked


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestValidationError(f"unable to load JSON: {error}") from error
    if not isinstance(value, dict):
        raise ManifestValidationError("JSON document must be an object")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    path.write_bytes((payload + "\n").encode("utf-8"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and seal Routes v1 source manifests")
    commands = parser.add_subparsers(dest="command", required=True)
    seal = commands.add_parser("seal")
    seal.add_argument("--contract", required=True, type=Path)
    seal.add_argument("--sampling-frame", required=True, type=Path)
    seal.add_argument("--draft", required=True, type=Path)
    seal.add_argument("--discovery-directory", required=True, type=Path)
    seal.add_argument("--output", required=True, type=Path)
    prepare = commands.add_parser("prepare-draft")
    prepare.add_argument("--contract", required=True, type=Path)
    prepare.add_argument("--sampling-frame", required=True, type=Path)
    prepare.add_argument("--input", required=True, type=Path)
    prepare.add_argument("--discovery-directory", required=True, type=Path)
    prepare.add_argument("--output", required=True, type=Path)
    validate = commands.add_parser("validate-manifest")
    validate.add_argument("--contract", required=True, type=Path)
    validate.add_argument("--sampling-frame", required=True, type=Path)
    validate.add_argument("--manifest", required=True, type=Path)
    validate.add_argument("--discovery-directory", required=True, type=Path)
    review_packet = commands.add_parser("review-packet")
    review_packet.add_argument("--draft", required=True, type=Path)
    review_packet.add_argument("--output", required=True, type=Path)
    decision_template = commands.add_parser("decision-template")
    decision_template.add_argument("--draft", required=True, type=Path)
    decision_template.add_argument("--output", required=True, type=Path)
    apply_review = commands.add_parser("apply-human-decisions")
    apply_review.add_argument("--contract", required=True, type=Path)
    apply_review.add_argument("--sampling-frame", required=True, type=Path)
    apply_review.add_argument("--curation-input", required=True, type=Path)
    apply_review.add_argument("--discovery-directory", required=True, type=Path)
    apply_review.add_argument("--draft", required=True, type=Path)
    apply_review.add_argument("--decisions", required=True, type=Path)
    apply_review.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run offline manifest validation or sealing."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "review-packet":
            from anachron.routes.human_review import build_review_packet

            args.output.write_text(build_review_packet(_load_json(args.draft)), encoding="utf-8")
        elif args.command == "decision-template":
            from anachron.routes.human_review import build_decision_template

            _write_json(args.output, build_decision_template(_load_json(args.draft)))
        else:
            contract = load_contract(args.contract)
            sampling_frame = _load_json(args.sampling_frame)
            if args.command == "prepare-draft":
                from anachron.routes.curation import prepare_draft

                draft = prepare_draft(
                    _load_json(args.input),
                    contract,
                    sampling_frame,
                    args.discovery_directory,
                )
                _write_json(args.output, draft)
            elif args.command == "seal":
                manifest = seal_manifest(
                    _load_json(args.draft),
                    contract,
                    sampling_frame,
                    args.discovery_directory,
                )
                _write_json(args.output, manifest)
            elif args.command == "apply-human-decisions":
                from anachron.routes.human_review import apply_human_decisions

                reviewed = apply_human_decisions(
                    _load_json(args.draft),
                    _load_json(args.decisions),
                    contract,
                    sampling_frame,
                    _load_json(args.curation_input),
                    args.discovery_directory,
                )
                _write_json(args.output, reviewed)
            else:
                validate_manifest_with_discovery(
                    _load_json(args.manifest),
                    contract,
                    sampling_frame,
                    args.discovery_directory,
                )
    except (ContractValidationError, ManifestValidationError, ValueError) as error:
        raise SystemExit(f"manifest validation failed: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
