"""Phase-bound source admission and sealing for Routes v2."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

from anachron.routes.v2.admission import (
    AdmissionError,
    canonical_json_sha256,
    load_json_object,
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
from anachron.routes.v2.sources import validate_sampling_frame


class ManifestValidationError(ValueError):
    """Raised when a v2 phase artifact is not admissible."""


def _mapping(value: Any, path: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ManifestValidationError(f"{path} has missing or extra fields")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestValidationError(f"{path} must be non-empty text")
    return value


def _phase(contract: dict[str, Any], phase: str) -> dict[str, Any]:
    validate_contract(contract)
    try:
        return phase_spec(contract, phase)
    except ContractValidationError as error:
        raise ManifestValidationError("study phase is invalid") from error


def _item_ids(contract: dict[str, Any], phase: str) -> set[str]:
    return {f"routes-v2:{phase}:{index}" for index in range(_phase(contract, phase)["topic_count"])}


def _receipt_index(receipts: list[dict[str, Any]], contract: dict[str, Any], frame: dict[str, Any], phase: str) -> dict[str, dict[str, Any]]:
    expected = _item_ids(contract, phase)
    if not isinstance(receipts, list) or len(receipts) != len(expected):
        raise ManifestValidationError("receipt count does not match the exact phase item count")
    output: dict[str, dict[str, Any]] = {}
    for receipt in receipts:
        try:
            checked = validate_revalidation_receipt(receipt, contract=contract, sampling_frame=frame)
        except AdmissionError as error:
            raise ManifestValidationError("revalidation receipt is invalid") from error
        if checked["study_phase"] != phase or checked["item_id"] in output:
            raise ManifestValidationError("revalidation receipt is from another phase or duplicated")
        output[checked["item_id"]] = checked
    if set(output) != expected:
        raise ManifestValidationError("revalidation receipts do not cover the exact selected phase items")
    return output


def _receipts_from_paths(paths: list[str | Path], contract: dict[str, Any], frame: dict[str, Any], phase: str) -> dict[str, dict[str, Any]]:
    if not isinstance(paths, list):
        raise ManifestValidationError("revalidation receipt paths must be a list")
    return _receipt_index([load_json_object(path) for path in paths], contract, frame, phase)


def _predecessor_binding(phase: str, evidence: Any) -> str | None:
    """Validate and project the create-only predecessor evidence for one phase."""
    return validate_phase_predecessor(evidence, phase=phase).get("evidence_sha256")


def _mapping_input(value: Any, contract: dict[str, Any], frame: dict[str, Any], phase: str) -> dict[str, dict[str, Any]]:
    document = _mapping(value, "source mapping input", {"schema_version", "study_phase", "contract_sha256", "sampling_frame_sha256", "items"})
    expected = _item_ids(contract, phase)
    if document["schema_version"] != "routes-v2-source-mapping-input" or document["study_phase"] != phase or document["contract_sha256"] != canonical_json_sha256(contract) or document["sampling_frame_sha256"] != canonical_json_sha256(frame) or not isinstance(document["items"], list) or len(document["items"]) != len(expected):
        raise ManifestValidationError("source mapping input binding is invalid")
    fields = {"item_id", "question", "pre_content", "post_content", "pre_opaque_citation_id", "opaque_citation_id", "raw_discovery_artifact_sha256"}
    output: dict[str, dict[str, Any]] = {}
    for item in document["items"]:
        checked = _mapping(item, "source mapping item", fields)
        item_id = _text(checked["item_id"], "source mapping item ID")
        if item_id in output or any(not _text(checked[name], f"source mapping {name}") for name in fields - {"item_id", "raw_discovery_artifact_sha256"}) or not isinstance(checked["raw_discovery_artifact_sha256"], str) or len(checked["raw_discovery_artifact_sha256"]) != 71 or checked["pre_opaque_citation_id"] == checked["opaque_citation_id"]:
            raise ManifestValidationError("source mapping item is incomplete or duplicate")
        output[item_id] = checked
    if set(output) != expected:
        raise ManifestValidationError("source mapping must cover only the exact selected phase items")
    return output


def _pair_from_receipt(item: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    pre, post = receipt["pre"], receipt["post"]
    if "sha256:" + hashlib.sha256(item["pre_content"].encode("utf-8")).hexdigest() != pre["content_sha256"] or "sha256:" + hashlib.sha256(item["post_content"].encode("utf-8")).hexdigest() != post["content_sha256"]:
        raise ManifestValidationError("mapping content does not match revalidated receipt bytes")
    return {
        "item_id": receipt["item_id"], "study_phase": receipt["study_phase"], "topic": receipt["title"], "cutoff_year": receipt["cutoff_year"], "question": item["question"],
        "pre_content": item["pre_content"], "pre_content_sha256": pre["content_sha256"], "pre_opaque_citation_id": item["pre_opaque_citation_id"], "strict_document_date": pre["timestamp"][:10],
        "post_content": item["post_content"], "post_content_sha256": post["content_sha256"], "opaque_citation_id": item["opaque_citation_id"], "truthful_document_date": post["timestamp"][:10], "misdated_eligible_document_date": f"{receipt['cutoff_year']}-12-31",
        "source_provenance": {"revalidation_receipt_sha256": receipt["receipt_sha256"], "pre_revision_id": pre["oldid"], "pre_revision_url": pre["immutable_url"], "post_revision_id": post["oldid"], "post_revision_url": post["immutable_url"]},
    }


def prepare_pending_draft(*, phase: str, contract_path: str | Path, sampling_frame_path: str | Path, revalidation_receipt_paths: list[str | Path], source_mapping_input_path: str | Path, output_path: str | Path, predecessor_evidence: Any = None) -> dict[str, Any]:
    """Create a phase-tagged pending draft from that phase's revalidated receipts."""
    contract = load_contract(contract_path)
    predecessor_sha256 = _predecessor_binding(phase, predecessor_evidence)
    frame = validate_sampling_frame(load_json_object(sampling_frame_path), contract)
    receipts = _receipts_from_paths(revalidation_receipt_paths, contract, frame, phase)
    mapping = _mapping_input(load_json_object(source_mapping_input_path), contract, frame, phase)
    if any(mapping[item_id]["raw_discovery_artifact_sha256"] != receipts[item_id]["raw_discovery_artifact_sha256"] for item_id in receipts):
        raise ManifestValidationError("source mapping raw-artifact binding drifted")
    if any(receipt["predecessor_evidence_sha256"] != predecessor_sha256 for receipt in receipts.values()):
        raise ManifestValidationError("revalidation receipts do not bind the phase predecessor")
    draft = {"schema_version": "routes-v2-pending-draft", "study_phase": phase, "contract_sha256": canonical_json_sha256(contract), "sampling_frame_sha256": canonical_json_sha256(frame), "predecessor_evidence_sha256": predecessor_sha256, "revalidation_receipts": [receipts[item_id] for item_id in sorted(receipts)], "pairs": [_pair_from_receipt(mapping[item_id], receipts[item_id]) for item_id in sorted(receipts)]}
    validated = validate_pending_draft(draft, contract=contract, sampling_frame=frame, revalidation_receipts=receipts, phase=phase, predecessor_evidence=predecessor_evidence)
    write_create_only(output_path, validated)
    return validated


def validate_pending_draft(draft: Any, *, contract: dict[str, Any], sampling_frame: dict[str, Any], revalidation_receipts: dict[str, dict[str, Any]], phase: str, predecessor_evidence: Any = None) -> dict[str, Any]:
    frame = validate_sampling_frame(sampling_frame, contract)
    expected = _item_ids(contract, phase)
    predecessor_sha256 = _predecessor_binding(phase, predecessor_evidence)
    document = _mapping(draft, "pending draft", {"schema_version", "study_phase", "contract_sha256", "sampling_frame_sha256", "predecessor_evidence_sha256", "revalidation_receipts", "pairs"})
    if document["schema_version"] != "routes-v2-pending-draft" or document["study_phase"] != phase or document["contract_sha256"] != canonical_json_sha256(contract) or document["sampling_frame_sha256"] != canonical_json_sha256(frame) or document["predecessor_evidence_sha256"] != predecessor_sha256:
        raise ManifestValidationError("pending draft identity binding is invalid")
    embedded = _receipt_index(document["revalidation_receipts"], contract, frame, phase)
    if set(revalidation_receipts) != expected or embedded != revalidation_receipts or any(receipt["predecessor_evidence_sha256"] != predecessor_sha256 for receipt in embedded.values()) or not isinstance(document["pairs"], list) or len(document["pairs"]) != len(expected):
        raise ManifestValidationError("pending draft phase receipts or pair count drifted")
    fields = {"item_id", "study_phase", "topic", "cutoff_year", "question", "pre_content", "pre_content_sha256", "pre_opaque_citation_id", "strict_document_date", "post_content", "post_content_sha256", "opaque_citation_id", "truthful_document_date", "misdated_eligible_document_date", "source_provenance"}
    observed: set[str] = set()
    for pair in document["pairs"]:
        value = _mapping(pair, "pending draft pair", fields)
        item_id = _text(value["item_id"], "pending draft item ID")
        if item_id in observed or item_id not in embedded or value["study_phase"] != phase:
            raise ManifestValidationError("pending draft pair crosses phases or duplicates an item")
        observed.add(item_id)
        projection = {name: value[name] for name in ("question", "pre_content", "post_content", "pre_opaque_citation_id", "opaque_citation_id")}
        if value != _pair_from_receipt({"item_id": item_id, **projection}, embedded[item_id]):
            raise ManifestValidationError("pending draft pair metadata is not exactly receipt-derived")
    if observed != expected:
        raise ManifestValidationError("pending draft does not cover every selected phase item")
    return document


def _validate_decisions(decisions: Any, draft: dict[str, Any], phase: str) -> dict[str, Any]:
    document = _mapping(decisions, "source decisions", {"schema_version", "study_phase", "pending_draft_sha256", "validator_id", "decisions"})
    expected = {pair["item_id"] for pair in draft["pairs"]}
    if document["schema_version"] != "routes-v2-source-decisions" or document["study_phase"] != phase or document["pending_draft_sha256"] != canonical_json_sha256(draft) or not isinstance(document["validator_id"], str) or not document["validator_id"] or not isinstance(document["decisions"], list) or len(document["decisions"]) != len(expected):
        raise ManifestValidationError("source decisions do not bind the exact phase pending draft")
    seen: set[str] = set()
    for row in document["decisions"]:
        value = _mapping(row, "source decision", {"item_id", "decision", "reason"})
        if value["item_id"] in seen or value["item_id"] not in expected or value["decision"] not in {"PASS", "REJECT"} or not isinstance(value["reason"], str):
            raise ManifestValidationError("source decision is incomplete, duplicate, or invalid")
        seen.add(value["item_id"])
    if seen != expected:
        raise ManifestValidationError("source decisions must decide each phase item exactly once")
    return document


def source_gate_receipt(*, draft: Any, source_decisions: Any, contract: dict[str, Any], sampling_frame: dict[str, Any], revalidation_receipts: dict[str, dict[str, Any]], phase: str, predecessor_evidence: Any = None) -> dict[str, Any]:
    """Return a phase-local PASS only when every selected item has a direct PASS."""
    predecessor_sha256 = _predecessor_binding(phase, predecessor_evidence)
    checked = validate_pending_draft(draft, contract=contract, sampling_frame=sampling_frame, revalidation_receipts=revalidation_receipts, phase=phase, predecessor_evidence=predecessor_evidence)
    decisions = _validate_decisions(source_decisions, checked, phase)
    accepted = sorted(row["item_id"] for row in decisions["decisions"] if row["decision"] == "PASS")
    excluded = sorted(row["item_id"] for row in decisions["decisions"] if row["decision"] == "REJECT")
    return {"schema_version": "routes-v2-source-gate-receipt", "study_phase": phase, "contract_sha256": canonical_json_sha256(contract), "sampling_frame_sha256": canonical_json_sha256(sampling_frame), "predecessor_evidence_sha256": predecessor_sha256, "pending_draft_sha256": canonical_json_sha256(checked), "decisions_sha256": canonical_json_sha256(decisions), "revalidation_receipts_sha256": canonical_json_sha256({item_id: revalidation_receipts[item_id]["receipt_sha256"] for item_id in sorted(revalidation_receipts)}), "accepted_item_ids": accepted, "excluded_item_ids": excluded, "status": "PASS" if len(accepted) == len(_item_ids(contract, phase)) else "FAIL"}


def seal_manifest(*, phase: str, contract: dict[str, Any], sampling_frame: dict[str, Any], draft: Any, source_decisions: Any, revalidation_receipt_paths: list[str | Path], predecessor_evidence: Any = None) -> dict[str, Any]:
    """Seal one runnable phase manifest only after that phase's all-PASS gate."""
    _predecessor_binding(phase, predecessor_evidence)
    frame = validate_sampling_frame(sampling_frame, contract)
    receipts = _receipts_from_paths(revalidation_receipt_paths, contract, frame, phase)
    checked = validate_pending_draft(draft, contract=contract, sampling_frame=frame, revalidation_receipts=receipts, phase=phase, predecessor_evidence=predecessor_evidence)
    gate = source_gate_receipt(draft=checked, source_decisions=source_decisions, contract=contract, sampling_frame=frame, revalidation_receipts=receipts, phase=phase, predecessor_evidence=predecessor_evidence)
    if gate["status"] != "PASS":
        raise ManifestValidationError("source gate failed; no phase manifest may be sealed")
    return validate_manifest({"schema_version": "routes-v2-source-manifest", "study_phase": phase, "contract_sha256": canonical_json_sha256(contract), "sampling_frame_sha256": canonical_json_sha256(frame), "predecessor_evidence": predecessor_evidence, "pending_draft_sha256": canonical_json_sha256(checked), "source_gate_receipt": gate, "revalidation_receipts": checked["revalidation_receipts"], "pairs": checked["pairs"]}, contract)


def validate_manifest(manifest: Any, contract: dict[str, Any]) -> dict[str, Any]:
    """Validate a sealed manifest without allowing another phase's artifacts in it."""
    validate_contract(contract)
    document = _mapping(manifest, "manifest", {"schema_version", "study_phase", "contract_sha256", "sampling_frame_sha256", "predecessor_evidence", "pending_draft_sha256", "source_gate_receipt", "revalidation_receipts", "pairs"})
    phase = document["study_phase"]
    expected = _item_ids(contract, phase)
    if document["schema_version"] != "routes-v2-source-manifest" or document["contract_sha256"] != canonical_json_sha256(contract) or document["sampling_frame_sha256"] != contract["sampling_frame_sha256"]:
        raise ManifestValidationError("manifest identity is invalid")
    gate = document["source_gate_receipt"]
    try:
        predecessor_sha256 = _predecessor_binding(phase, document["predecessor_evidence"])
    except AdmissionError as error:
        raise ManifestValidationError("manifest predecessor evidence is invalid") from error
    required = {"schema_version", "study_phase", "contract_sha256", "sampling_frame_sha256", "predecessor_evidence_sha256", "pending_draft_sha256", "decisions_sha256", "revalidation_receipts_sha256", "accepted_item_ids", "excluded_item_ids", "status"}
    if not isinstance(gate, dict) or set(gate) != required or gate["schema_version"] != "routes-v2-source-gate-receipt" or gate["study_phase"] != phase or gate["status"] != "PASS" or gate["contract_sha256"] != document["contract_sha256"] or gate["sampling_frame_sha256"] != document["sampling_frame_sha256"] or gate["predecessor_evidence_sha256"] != predecessor_sha256 or gate["pending_draft_sha256"] != document["pending_draft_sha256"] or gate["excluded_item_ids"] or set(gate["accepted_item_ids"]) != expected:
        raise ManifestValidationError("manifest lacks an exact phase-local all-PASS source gate")
    receipts: dict[str, dict[str, Any]] = {}
    expected_topics = phase_spec(contract, phase)["topics"]
    if not isinstance(document["revalidation_receipts"], list) or len(document["revalidation_receipts"]) != len(expected):
        raise ManifestValidationError("manifest receipt count drifted")
    for receipt in document["revalidation_receipts"]:
        if not isinstance(receipt, dict):
            raise ManifestValidationError("manifest receipt is invalid")
        unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        item_id = receipt.get("item_id")
        prefix = f"routes-v2:{phase}:"
        index = item_id.removeprefix(prefix) if isinstance(item_id, str) else ""
        if receipt.get("schema_version") != "routes-v2-source-revalidation" or receipt.get("study_phase") != phase or receipt.get("contract_sha256") != document["contract_sha256"] or receipt.get("sampling_frame_sha256") != document["sampling_frame_sha256"] or receipt.get("predecessor_evidence_sha256") != predecessor_sha256 or receipt.get("receipt_sha256") != canonical_json_sha256(unsigned) or not index.isdigit() or int(index) >= len(expected_topics) or expected_topics[int(index)] != {"title": receipt.get("title"), "cutoff_year": receipt.get("cutoff_year")} or item_id in receipts:
            raise ManifestValidationError("manifest receipt binding crosses phases or drifted")
        receipts[item_id] = receipt
    if set(receipts) != expected:
        raise ManifestValidationError("manifest receipt set does not cover the exact phase")
    if gate["revalidation_receipts_sha256"] != canonical_json_sha256({item_id: receipts[item_id]["receipt_sha256"] for item_id in sorted(receipts)}) or not isinstance(document["pairs"], list) or len(document["pairs"]) != len(expected):
        raise ManifestValidationError("manifest receipt or pair count drifted")
    pair_ids = set()
    for pair in document["pairs"]:
        if not isinstance(pair, dict) or pair.get("item_id") in pair_ids or pair.get("item_id") not in receipts or pair.get("study_phase") != phase:
            raise ManifestValidationError("manifest pair crosses phases or duplicates an item")
        pair_ids.add(pair["item_id"])
        projection = {"item_id": pair["item_id"], **{name: pair.get(name) for name in ("question", "pre_content", "post_content", "pre_opaque_citation_id", "opaque_citation_id")}}
        if _pair_from_receipt(projection, receipts[pair["item_id"]]) != pair:
            raise ManifestValidationError("manifest pair is not exactly receipt-derived")
    if pair_ids != expected:
        raise ManifestValidationError("manifest pairs do not cover the exact phase item set")
    return document


def load_json(path: str | Path) -> dict[str, Any]:
    try:
        return load_json_object(path)
    except AdmissionError as error:
        raise ManifestValidationError(str(error)) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Routes v2 source manifest")
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        validate_manifest(load_json(args.manifest), load_contract(args.contract))
    except (ContractValidationError, ManifestValidationError) as error:
        raise SystemExit(f"routes v2 manifest failed: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
