"""Phase-bound source admission and sealing for bounded Routes v2 excerpts."""

from __future__ import annotations

import argparse
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anachron.routes.v2.admission import (
    AdmissionError,
    canonical_json_sha256,
    load_json_object,
    phase_raw_artifact_paths,
    validate_phase_predecessor,
    validate_revalidation_receipt,
    write_create_only,
)
from anachron.routes.v2.schema import (
    ContractValidationError,
    load_contract,
    phase_spec,
    validate_contract,
)
from anachron.routes.v2.source_excerpt import (
    ExcerptValidationError,
    build_excerpt_receipts,
    validate_excerpt_receipt,
)
from anachron.routes.v2.sources import validate_sampling_frame


class ManifestValidationError(ValueError):
    """Raised when a v2 phase artifact is not admissible."""


_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_MAPPING_FIELDS = {"item_id", "question", "pre_anchor", "post_anchor", "pre_aliases", "post_aliases", "pre_opaque_citation_id", "post_opaque_citation_id", "raw_discovery_artifact_sha256", "revalidation_receipt_sha256"}
_PAIR_FIELDS = {"item_id", "study_phase", "topic", "cutoff_year", "question", "pre_excerpt", "post_excerpt", "pre_anchor", "post_anchor", "pre_revision", "post_revision", "pre_aliases", "post_aliases", "answer_rules_sha256", "mapping_item_sha256", "pre_opaque_citation_id", "post_opaque_citation_id", "strict_document_date", "truthful_document_date", "misdated_eligible_document_date", "source_provenance"}


def _mapping(value: Any, name: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ManifestValidationError(f"{name} has missing or extra fields")
    return value


def _text(value: Any, name: str, maximum: int | None = None) -> str:
    if not isinstance(value, str) or not value or maximum is not None and len(value.encode("utf-8")) > maximum:
        raise ManifestValidationError(f"{name} must be non-empty bounded text")
    return value


def _normal(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def _phase(contract: dict[str, Any], phase: str) -> dict[str, Any]:
    try:
        return phase_spec(validate_contract(contract), phase)
    except ContractValidationError as error:
        raise ManifestValidationError("study phase is invalid") from error


def _item_ids(contract: dict[str, Any], phase: str) -> set[str]:
    return {f"routes-v2:{phase}:{index}" for index in range(_phase(contract, phase)["topic_count"])}


def _predecessor(phase: str, evidence: Any) -> str | None:
    return validate_phase_predecessor(evidence, phase=phase).get("evidence_sha256")


def _revalidations(receipts: list[dict[str, Any]], contract: dict[str, Any], frame: dict[str, Any], phase: str) -> dict[str, dict[str, Any]]:
    expected, output = _item_ids(contract, phase), {}
    if not isinstance(receipts, list) or len(receipts) != len(expected):
        raise ManifestValidationError("revalidation receipt count does not match phase")
    for receipt in receipts:
        try:
            checked = validate_revalidation_receipt(receipt, contract=contract, sampling_frame=frame)
        except AdmissionError as error:
            raise ManifestValidationError("revalidation receipt is invalid") from error
        if checked["study_phase"] != phase or checked["item_id"] in output:
            raise ManifestValidationError("revalidation receipt phase coverage is invalid")
        output[checked["item_id"]] = checked
    if set(output) != expected:
        raise ManifestValidationError("revalidation receipts do not cover exact phase")
    return output


def _excerpts(receipts: list[dict[str, Any]], contract: dict[str, Any], revalidations: dict[str, dict[str, Any]], phase: str) -> dict[tuple[str, str], dict[str, Any]]:
    expected = {(item_id, arm) for item_id in _item_ids(contract, phase) for arm in ("pre", "post")}
    output: dict[tuple[str, str], dict[str, Any]] = {}
    if not isinstance(receipts, list) or len(receipts) != len(expected):
        raise ManifestValidationError("excerpt receipt count does not match phase arms")
    for receipt in receipts:
        try:
            checked = validate_excerpt_receipt(receipt, contract=contract)
        except ExcerptValidationError as error:
            raise ManifestValidationError("excerpt receipt is invalid") from error
        key, revalidation = (checked["item_id"], checked["arm"]), revalidations.get(checked["item_id"])
        if key in output or revalidation is None or checked["revalidation_receipt_sha256"] != revalidation["receipt_sha256"]:
            raise ManifestValidationError("excerpt receipt does not bind revalidated source")
        arm = checked["arm"]
        if checked["revision"] != {"oldid": revalidation[arm]["oldid"], "immutable_url": revalidation[arm]["immutable_url"], "timestamp": revalidation[arm]["timestamp"], "full_content_sha256": revalidation[arm]["content_sha256"]}:
            raise ManifestValidationError("excerpt revision projection drifted")
        output[key] = checked
    if set(output) != expected:
        raise ManifestValidationError("excerpt receipts do not cover every source arm")
    return output


def _mapping_input(value: Any, contract: dict[str, Any], frame: dict[str, Any], phase: str) -> dict[str, dict[str, Any]]:
    document = _mapping(value, "source mapping input", {"schema_version", "study_phase", "contract_sha256", "sampling_frame_sha256", "items"})
    if document["schema_version"] != "routes-v2-source-mapping-input-v2" or document["study_phase"] != phase or document["contract_sha256"] != canonical_json_sha256(contract) or document["sampling_frame_sha256"] != canonical_json_sha256(frame) or not isinstance(document["items"], list):
        raise ManifestValidationError("source mapping identity is invalid or legacy")
    bounds, output = contract["source_bounds"], {}
    for row in document["items"]:
        item = _mapping(row, "source mapping item", _MAPPING_FIELDS)
        item_id = _text(item["item_id"], "item ID")
        for name in ("question", "pre_anchor", "post_anchor", "pre_opaque_citation_id", "post_opaque_citation_id"):
            _text(item[name], name, bounds["max_question_utf8_bytes"] if name == "question" else bounds["max_anchor_utf8_bytes"])
        aliases: dict[str, list[str]] = {}
        for name in ("pre_aliases", "post_aliases"):
            values = item[name]
            if not isinstance(values, list) or not values or len(values) > bounds["max_aliases_per_answer_set"]:
                raise ManifestValidationError("aliases violate frozen bounds")
            normalized = [_normal(_text(alias, name, bounds["max_alias_utf8_bytes"])) for alias in values]
            if len(normalized) != len(set(normalized)):
                raise ManifestValidationError("aliases are normalization-duplicated")
            aliases[name] = normalized
        abstentions = {_normal(alias) for alias in contract["answer_rules"]["abstention_aliases"]}
        if set(aliases["pre_aliases"]) & set(aliases["post_aliases"]) or (set(aliases["pre_aliases"]) | set(aliases["post_aliases"])) & abstentions:
            raise ManifestValidationError("pre, post, and abstention aliases must be normalization-disjoint")
        if item_id in output or item["pre_anchor"] == item["post_anchor"] or item["pre_opaque_citation_id"] == item["post_opaque_citation_id"] or not all(isinstance(item[name], str) and len(item[name]) == 71 for name in ("raw_discovery_artifact_sha256", "revalidation_receipt_sha256")):
            raise ManifestValidationError("source mapping item is invalid")
        output[item_id] = item
    if set(output) != _item_ids(contract, phase):
        raise ManifestValidationError("source mapping must cover exact phase")
    return output


def _raw_paths(repository: str | Path, phase: str, mapping: dict[str, dict[str, Any]]) -> dict[str, Path]:
    try:
        raw_artifact_paths = phase_raw_artifact_paths(repository, phase)
    except AdmissionError as error:
        raise ManifestValidationError("fixed ignored raw artifacts are unavailable") from error
    if len(raw_artifact_paths) != len(mapping):
        raise ManifestValidationError("exactly one ignored raw artifact is required per source item")
    expected, output = {item["raw_discovery_artifact_sha256"] for item in mapping.values()}, {}
    for candidate in raw_artifact_paths:
        path = Path(candidate).resolve()
        try:
            digest = canonical_json_sha256(load_json_object(path))
        except AdmissionError as error:
            raise ManifestValidationError("raw artifact is unreadable") from error
        if digest in output or digest not in expected:
            raise ManifestValidationError("raw artifact paths do not exactly match mapping bindings")
        output[digest] = path
    if set(output) != expected:
        raise ManifestValidationError("raw artifact paths do not cover every mapping binding")
    return output


def _rederived_excerpts(*, repository: str | Path, phase: str, mapping: dict[str, dict[str, Any]], revalidations: dict[str, dict[str, Any]], contract: dict[str, Any], stored: dict[tuple[str, str], dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    paths, output = _raw_paths(repository, phase, mapping), {}
    for item_id, item in mapping.items():
        try:
            pair = build_excerpt_receipts(contract=contract, revalidation_receipt=revalidations[item_id], raw_artifact_path=paths[item["raw_discovery_artifact_sha256"]], mapping_item=item)
        except ExcerptValidationError as error:
            raise ManifestValidationError("raw source cannot rederive its bounded excerpt receipt") from error
        for receipt in pair:
            key = (item_id, receipt["arm"])
            if stored.get(key) != receipt:
                raise ManifestValidationError("stored excerpt receipt differs from raw-source rederivation")
            output[key] = receipt
    return output


def _require_alias_source_support(item: dict[str, Any], pre: dict[str, Any], post: dict[str, Any]) -> None:
    pre_text, post_text = _normal(pre["excerpt"]["text"]), _normal(post["excerpt"]["text"])
    pre_aliases, post_aliases = [_normal(alias) for alias in item["pre_aliases"]], [_normal(alias) for alias in item["post_aliases"]]
    if not any(alias in pre_text for alias in pre_aliases) or not any(alias in post_text for alias in post_aliases):
        raise ManifestValidationError("each answer set requires visible source support in its own excerpt")
    if any(alias in post_text for alias in pre_aliases) or any(alias in pre_text for alias in post_aliases):
        raise ManifestValidationError("opposite answer-set aliases must be absent from the other excerpt")


def _pair(item: dict[str, Any], revalidation: dict[str, Any], excerpts: dict[tuple[str, str], dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    pre, post = excerpts[(item["item_id"], "pre")], excerpts[(item["item_id"], "post")]
    if item["raw_discovery_artifact_sha256"] != revalidation["raw_discovery_artifact_sha256"] or item["revalidation_receipt_sha256"] != revalidation["receipt_sha256"] or item["pre_anchor"] != pre["anchor"]["text"] or item["post_anchor"] != post["anchor"]["text"]:
        raise ManifestValidationError("mapping provenance/anchor binding drifted")
    _require_alias_source_support(item, pre, post)
    return {"item_id": revalidation["item_id"], "study_phase": revalidation["study_phase"], "topic": revalidation["title"], "cutoff_year": revalidation["cutoff_year"], "question": item["question"], "pre_excerpt": pre["excerpt"], "post_excerpt": post["excerpt"], "pre_anchor": pre["anchor"], "post_anchor": post["anchor"], "pre_revision": pre["revision"], "post_revision": post["revision"], "pre_aliases": item["pre_aliases"], "post_aliases": item["post_aliases"], "answer_rules_sha256": canonical_json_sha256(contract["answer_rules"]), "mapping_item_sha256": canonical_json_sha256(item), "pre_opaque_citation_id": item["pre_opaque_citation_id"], "post_opaque_citation_id": item["post_opaque_citation_id"], "strict_document_date": revalidation["pre"]["timestamp"][:10], "truthful_document_date": revalidation["post"]["timestamp"][:10], "misdated_eligible_document_date": f"{revalidation['cutoff_year']}-12-31", "source_provenance": {"raw_discovery_artifact_sha256": item["raw_discovery_artifact_sha256"], "revalidation_receipt_sha256": revalidation["receipt_sha256"], "pre_excerpt_receipt_sha256": pre["receipt_sha256"], "post_excerpt_receipt_sha256": post["receipt_sha256"], "pre_full_content_sha256": pre["revision"]["full_content_sha256"], "post_full_content_sha256": post["revision"]["full_content_sha256"]}}


def _projection(pair: dict[str, Any]) -> dict[str, Any]:
    return pair


def _draft_mapping(document: dict[str, Any], contract: dict[str, Any], frame: dict[str, Any], phase: str) -> dict[str, dict[str, Any]]:
    if document["source_mapping_sha256"] != canonical_json_sha256(document["source_mapping"]):
        raise ManifestValidationError("pending draft source mapping hash drifted")
    return _mapping_input(document["source_mapping"], contract, frame, phase)


def prepare_pending_draft(*, phase: str, repository: str | Path, contract_path: str | Path, sampling_frame_path: str | Path, revalidation_receipt_paths: list[str | Path], excerpt_receipt_paths: list[str | Path], source_mapping_input_path: str | Path, output_path: str | Path, predecessor_evidence: Any = None) -> dict[str, Any]:
    contract = load_contract(contract_path)
    predecessor = _predecessor(phase, predecessor_evidence)
    frame = validate_sampling_frame(load_json_object(sampling_frame_path), contract)
    revalidations = _revalidations([load_json_object(path) for path in revalidation_receipt_paths], contract, frame, phase)
    excerpts = _excerpts([load_json_object(path) for path in excerpt_receipt_paths], contract, revalidations, phase)
    mapping_document = load_json_object(source_mapping_input_path)
    mapping = _mapping_input(mapping_document, contract, frame, phase)
    _rederived_excerpts(repository=repository, phase=phase, mapping=mapping, revalidations=revalidations, contract=contract, stored=excerpts)
    if any(receipt["predecessor_evidence_sha256"] != predecessor for receipt in revalidations.values()):
        raise ManifestValidationError("revalidation predecessor binding drifted")
    draft = {"schema_version": "routes-v2-pending-draft-v2", "study_phase": phase, "contract_sha256": canonical_json_sha256(contract), "sampling_frame_sha256": canonical_json_sha256(frame), "predecessor_evidence_sha256": predecessor, "source_mapping": mapping_document, "source_mapping_sha256": canonical_json_sha256(mapping_document), "revalidation_receipts": [revalidations[key] for key in sorted(revalidations)], "excerpt_receipts": [excerpts[key] for key in sorted(excerpts)], "pairs": [_pair(mapping[key], revalidations[key], excerpts, contract) for key in sorted(mapping)]}
    validated = validate_pending_draft(draft, repository=repository, contract=contract, sampling_frame=frame, revalidation_receipts=revalidations, excerpt_receipts=excerpts, phase=phase, predecessor_evidence=predecessor_evidence)
    write_create_only(output_path, validated)
    return validated


def validate_pending_draft(draft: Any, *, repository: str | Path, contract: dict[str, Any], sampling_frame: dict[str, Any], revalidation_receipts: dict[str, dict[str, Any]], excerpt_receipts: dict[tuple[str, str], dict[str, Any]], phase: str, predecessor_evidence: Any = None) -> dict[str, Any]:
    frame, predecessor = validate_sampling_frame(sampling_frame, contract), _predecessor(phase, predecessor_evidence)
    document = _mapping(draft, "pending draft", {"schema_version", "study_phase", "contract_sha256", "sampling_frame_sha256", "predecessor_evidence_sha256", "source_mapping", "source_mapping_sha256", "revalidation_receipts", "excerpt_receipts", "pairs"})
    if document["schema_version"] != "routes-v2-pending-draft-v2" or document["study_phase"] != phase or document["contract_sha256"] != canonical_json_sha256(contract) or document["sampling_frame_sha256"] != canonical_json_sha256(frame) or document["predecessor_evidence_sha256"] != predecessor:
        raise ManifestValidationError("pending draft identity is invalid or legacy")
    mapping = _draft_mapping(document, contract, frame, phase)
    embedded_revalidations = _revalidations(document["revalidation_receipts"], contract, frame, phase)
    embedded_excerpts = _excerpts(document["excerpt_receipts"], contract, embedded_revalidations, phase)
    if embedded_revalidations != revalidation_receipts or embedded_excerpts != excerpt_receipts or not isinstance(document["pairs"], list):
        raise ManifestValidationError("pending draft receipt bindings drifted")
    _rederived_excerpts(repository=repository, phase=phase, mapping=mapping, revalidations=embedded_revalidations, contract=contract, stored=embedded_excerpts)
    observed = set()
    for pair in document["pairs"]:
        item_id = pair.get("item_id") if isinstance(pair, dict) else None
        if item_id in observed or item_id not in mapping or not isinstance(pair, dict) or set(pair) != _PAIR_FIELDS:
            raise ManifestValidationError("pending draft pair coverage is invalid")
        observed.add(item_id)
        if pair != _pair(mapping[item_id], revalidation_receipts[item_id], embedded_excerpts, contract):
            raise ManifestValidationError("pending pair is not exactly bounded-excerpt derived")
    if observed != _item_ids(contract, phase):
        raise ManifestValidationError("pending draft does not cover phase")
    return document


def _canonical_utc(value: Any) -> bool:
    if not isinstance(value, str) or _UTC.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return True


def _decisions(decisions: Any, draft: dict[str, Any], contract: dict[str, Any], phase: str) -> dict[str, Any]:
    document = _mapping(decisions, "source decisions", {"schema_version", "study_phase", "pending_draft_sha256", "validator_id", "reviewed_at", "certification", "decisions"})
    pairs = {pair["item_id"]: pair for pair in draft["pairs"]}
    if document["schema_version"] != contract["source_gate"]["decision_schema"] or document["study_phase"] != phase or document["pending_draft_sha256"] != canonical_json_sha256(draft) or not isinstance(document["validator_id"], str) or not document["validator_id"] or not _canonical_utc(document["reviewed_at"]) or document["certification"] != "I inspected every listed bounded excerpt and its immutable receipt projection." or not isinstance(document["decisions"], list):
        raise ManifestValidationError("source decision header is invalid")
    seen = set()
    for row in document["decisions"]:
        value = _mapping(row, "source decision", {"item_id", "decision", "reason", "reviewed_projection", "reviewed_projection_sha256"})
        expected = pairs.get(value["item_id"])
        if value["item_id"] in seen or expected is None or value["decision"] not in {"PASS", "REJECT"} or not isinstance(value["reason"], str) or value["reviewed_projection"] != _projection(expected) or value["reviewed_projection_sha256"] != canonical_json_sha256(expected):
            raise ManifestValidationError("source decision does not bind the complete reviewed projection")
        seen.add(value["item_id"])
    if seen != set(pairs):
        raise ManifestValidationError("source decisions do not cover phase")
    return document


def source_gate_receipt(*, draft: Any, source_decisions: Any, repository: str | Path, contract: dict[str, Any], sampling_frame: dict[str, Any], revalidation_receipts: dict[str, dict[str, Any]], excerpt_receipts: dict[tuple[str, str], dict[str, Any]], phase: str, predecessor_evidence: Any = None) -> dict[str, Any]:
    checked = validate_pending_draft(draft, repository=repository, contract=contract, sampling_frame=sampling_frame, revalidation_receipts=revalidation_receipts, excerpt_receipts=excerpt_receipts, phase=phase, predecessor_evidence=predecessor_evidence)
    decisions = _decisions(source_decisions, checked, contract, phase)
    accepted = sorted(row["item_id"] for row in decisions["decisions"] if row["decision"] == "PASS")
    return {"schema_version": "routes-v2-source-gate-receipt-v2", "study_phase": phase, "contract_sha256": canonical_json_sha256(contract), "sampling_frame_sha256": canonical_json_sha256(sampling_frame), "predecessor_evidence_sha256": _predecessor(phase, predecessor_evidence), "pending_draft_sha256": canonical_json_sha256(checked), "decisions_sha256": canonical_json_sha256(decisions), "source_mapping_sha256": checked["source_mapping_sha256"], "revalidation_receipts_sha256": canonical_json_sha256({key: value["receipt_sha256"] for key, value in sorted(revalidation_receipts.items())}), "excerpt_receipts_sha256": canonical_json_sha256({f"{key[0]}:{key[1]}": value["receipt_sha256"] for key, value in sorted(excerpt_receipts.items())}), "reviewed_pairs_sha256": canonical_json_sha256([_projection(pair) for pair in sorted(checked["pairs"], key=lambda pair: pair["item_id"])]), "accepted_item_ids": accepted, "excluded_item_ids": sorted(set(_item_ids(contract, phase)) - set(accepted)), "status": "PASS" if len(accepted) == len(_item_ids(contract, phase)) else "FAIL"}


def seal_manifest(*, phase: str, repository: str | Path, contract: dict[str, Any], sampling_frame: dict[str, Any], draft: Any, source_decisions: Any, revalidation_receipt_paths: list[str | Path], excerpt_receipt_paths: list[str | Path], predecessor_evidence: Any = None) -> dict[str, Any]:
    frame = validate_sampling_frame(sampling_frame, contract)
    revalidations = _revalidations([load_json_object(path) for path in revalidation_receipt_paths], contract, frame, phase)
    excerpts = _excerpts([load_json_object(path) for path in excerpt_receipt_paths], contract, revalidations, phase)
    checked = validate_pending_draft(draft, repository=repository, contract=contract, sampling_frame=frame, revalidation_receipts=revalidations, excerpt_receipts=excerpts, phase=phase, predecessor_evidence=predecessor_evidence)
    gate = source_gate_receipt(draft=checked, source_decisions=source_decisions, repository=repository, contract=contract, sampling_frame=frame, revalidation_receipts=revalidations, excerpt_receipts=excerpts, phase=phase, predecessor_evidence=predecessor_evidence)
    if gate["status"] != "PASS":
        raise ManifestValidationError("source gate failed")
    return {"schema_version": "routes-v2-source-manifest-v2", "study_phase": phase, "contract_sha256": canonical_json_sha256(contract), "sampling_frame_sha256": canonical_json_sha256(frame), "predecessor_evidence": predecessor_evidence, "pending_draft_sha256": canonical_json_sha256(checked), "source_gate_receipt": gate, "source_mapping": checked["source_mapping"], "source_mapping_sha256": checked["source_mapping_sha256"], "revalidation_receipts": checked["revalidation_receipts"], "excerpt_receipts": checked["excerpt_receipts"], "pairs": checked["pairs"], "answer_rules": contract["answer_rules"], "answer_rules_sha256": canonical_json_sha256(contract["answer_rules"])}


def validate_manifest(manifest: Any, contract: dict[str, Any], *, repository: str | Path | None = None) -> dict[str, Any]:
    fields = {"schema_version", "study_phase", "contract_sha256", "sampling_frame_sha256", "predecessor_evidence", "pending_draft_sha256", "source_gate_receipt", "source_mapping", "source_mapping_sha256", "revalidation_receipts", "excerpt_receipts", "pairs", "answer_rules", "answer_rules_sha256"}
    document = _mapping(manifest, "manifest", fields)
    phase = document["study_phase"]
    if document["schema_version"] != "routes-v2-source-manifest-v2" or document["contract_sha256"] != canonical_json_sha256(contract) or document["sampling_frame_sha256"] != contract["sampling_frame_sha256"] or document["answer_rules"] != contract["answer_rules"] or document["answer_rules_sha256"] != canonical_json_sha256(contract["answer_rules"]):
        raise ManifestValidationError("manifest identity is invalid or legacy")
    frame_path = Path(__file__).parents[3] / "research" / "routes-v2" / "sampling_frame.json"
    frame = validate_sampling_frame(load_json_object(frame_path), contract)
    revalidations = _revalidations(document["revalidation_receipts"], contract, frame, phase)
    try:
        predecessor_sha256 = _predecessor(phase, document["predecessor_evidence"])
    except AdmissionError as error:
        raise ManifestValidationError("manifest predecessor evidence is invalid") from error
    if any(receipt["predecessor_evidence_sha256"] != predecessor_sha256 for receipt in revalidations.values()):
        raise ManifestValidationError("manifest revalidation predecessor binding drifted")
    excerpts = _excerpts(document["excerpt_receipts"], contract, revalidations, phase)
    mapping = _draft_mapping(document, contract, frame, phase)
    if repository is not None:
        _rederived_excerpts(repository=repository, phase=phase, mapping=mapping, revalidations=revalidations, contract=contract, stored=excerpts)
    if not isinstance(document["pairs"], list) or len(document["pairs"]) != len(_item_ids(contract, phase)):
        raise ManifestValidationError("manifest pair count is invalid")
    seen = set()
    for pair in document["pairs"]:
        if not isinstance(pair, dict) or pair.get("item_id") in seen or pair.get("item_id") not in mapping or set(pair) != _PAIR_FIELDS:
            raise ManifestValidationError("manifest pair coverage is invalid")
        seen.add(pair["item_id"])
        if pair != _pair(mapping[pair["item_id"]], revalidations[pair["item_id"]], excerpts, contract):
            raise ManifestValidationError("manifest pair is not exactly bounded-excerpt derived")
    gate = document["source_gate_receipt"]
    gate_fields = {"schema_version", "study_phase", "contract_sha256", "sampling_frame_sha256", "predecessor_evidence_sha256", "pending_draft_sha256", "decisions_sha256", "source_mapping_sha256", "revalidation_receipts_sha256", "excerpt_receipts_sha256", "reviewed_pairs_sha256", "accepted_item_ids", "excluded_item_ids", "status"}
    if seen != _item_ids(contract, phase) or not isinstance(gate, dict) or set(gate) != gate_fields or gate["schema_version"] != "routes-v2-source-gate-receipt-v2" or gate["status"] != "PASS" or gate["study_phase"] != phase or gate["predecessor_evidence_sha256"] != predecessor_sha256 or gate["pending_draft_sha256"] != document["pending_draft_sha256"] or gate["source_mapping_sha256"] != document["source_mapping_sha256"] or set(gate["accepted_item_ids"]) != seen or gate["excluded_item_ids"] or gate["revalidation_receipts_sha256"] != canonical_json_sha256({key: item["receipt_sha256"] for key, item in sorted(revalidations.items())}) or gate["excerpt_receipts_sha256"] != canonical_json_sha256({f"{key[0]}:{key[1]}": item["receipt_sha256"] for key, item in sorted(excerpts.items())}) or gate["reviewed_pairs_sha256"] != canonical_json_sha256([_projection(pair) for pair in sorted(document["pairs"], key=lambda pair: pair["item_id"])]):
        raise ManifestValidationError("manifest source gate binding is invalid")
    return document


def load_json(path: str | Path) -> dict[str, Any]:
    return load_json_object(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args(argv)
    validate_manifest(load_json(args.manifest), load_contract(args.contract))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
