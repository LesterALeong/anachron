"""Fail-closed blinded audit and receipt-bound finite-set analysis for Routes v2."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from anachron.routes.v2.admission import ValidatedExecution
from anachron.routes.v2.manifest import canonical_json_sha256


class AnalysisValidationError(ValueError):
    """Raised when an audit, analysis, or provenance binding is incomplete."""


def _legacy_free_form_disabled() -> None:
    """Prevent pre-review free-form ledger/result APIs from ever producing evidence."""
    raise AnalysisValidationError("legacy free-form Routes v2 analysis is permanently disabled; replay an analysis root")


class PrivateAuditJoin:
    """Opaque private mapping between blind packet rows and machine outcomes."""

    __slots__ = ("_execution", "_join", "_packet", "_phase")

    def __init__(self, token: object, execution: ValidatedExecution, phase: str, packet: dict[str, Any], join: dict[str, dict[str, Any]]):
        if token is not _PRIVATE_AUDIT_TOKEN:
            raise TypeError("PrivateAuditJoin must be created by build_private_audit_join")
        self._execution = execution
        self._phase = phase
        self._packet = packet
        self._join = join


class ValidatedAudit:
    """Opaque, coverage-checked public ratings joined only inside the reducer."""

    __slots__ = ("_execution", "_join", "_packet", "_phase", "_report", "_submissions")

    def __init__(self, token: object, private: PrivateAuditJoin, submissions: tuple[dict[str, Any], dict[str, Any]], report: dict[str, Any]):
        if token is not _VALIDATED_AUDIT_TOKEN:
            raise TypeError("ValidatedAudit must be created by validate_public_audit")
        self._execution = private._execution
        self._phase = private._phase
        self._packet = private._packet
        self._join = private._join
        self._submissions = submissions
        self._report = report

    @property
    def report(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._report, ensure_ascii=False, sort_keys=True))


class FiniteSetResult:
    """Reducer-owned result: callers cannot supply an effect, gates, or prose."""

    __slots__ = ("_value",)

    def __init__(self, token: object, value: dict[str, Any]):
        if token is not _FINITE_SET_TOKEN:
            raise TypeError("FiniteSetResult must be created by reduce_finite_set")
        self._value = value

    @property
    def value(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._value, ensure_ascii=False, sort_keys=True))


_PRIVATE_AUDIT_TOKEN = object()
_VALIDATED_AUDIT_TOKEN = object()
_FINITE_SET_TOKEN = object()


_SEMANTIC_STATUSES = {"ok", "invalid_output"}
_FORBIDDEN_AUDIT_TEXT = (
    "strict_pre_truthful", "post_truthful", "post_misdated_eligible", "oldid=",
    "routes-v", "citation_id", "presented_document_date", "model", "seed",
    "revision_url", "document_date",
)
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_MALFORMED_PREVIEW = "[response bytes withheld: malformed response envelope]"
_BLIND_DIGEST_PREFIX = "blind:"
_PUBLIC_PARSE_ERROR_CLASSES = {"response_envelope_unavailable", "response_content_unavailable"}
_PRIVATE_VALUE_REDACTION = "[private value redacted]"


def _mapping(value: Any, path: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise AnalysisValidationError(f"{path} has missing or extra fields")
    return value


def _sha(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise AnalysisValidationError(f"{path} must be a SHA-256 receipt")
    return value


def _phase_spec(contract: dict[str, Any], phase: str) -> dict[str, Any]:
    if phase not in {"pilot", "confirmatory"}:
        raise AnalysisValidationError("phase must be pilot or confirmatory")
    return contract["evaluation"][phase]


def build_audit_plan(contract: dict[str, Any], phase: str, ledger: list[dict[str, Any]]) -> dict[str, Any]:
    """Select the predeclared fixed-seed, route-balanced audit population."""
    _legacy_free_form_disabled()
    spec = _phase_spec(contract, phase)
    required = {"trajectory_id", "study_phase", "topic_id", "condition", "model_id", "seed", "status", "parsed_answer", "sanitized_payload", "response_sha256", "machine_label"}
    candidates: list[dict[str, Any]] = []
    for index, row in enumerate(ledger):
        value = _mapping(row, f"ledger[{index}]", required)
        if value["study_phase"] != phase:
            raise AnalysisValidationError("audit builder never accepts pooled phase ledger rows")
        if value["study_phase"] == phase and value["condition"] in contract["evaluation"]["primary_arms"] and value["seed"] == spec["fixed_audit_seed"] and value["model_id"] in spec["models"]:
            candidates.append(value)
    expected_count = spec["topic_count"] * len(spec["models"]) * 2
    if len(candidates) != expected_count:
        raise AnalysisValidationError("audit population does not have exact frozen route-balanced coverage")
    identities = {(row["topic_id"], row["model_id"], row["condition"]) for row in candidates}
    expected_identities = {(topic, model, condition) for topic in {row["topic_id"] for row in candidates} for model in spec["models"] for condition in contract["evaluation"]["primary_arms"]}
    if len({row["topic_id"] for row in candidates}) != spec["topic_count"] or identities != expected_identities:
        raise AnalysisValidationError("audit population is not complete for every topic, model, and arm")
    selected = sorted(candidates, key=lambda row: row["trajectory_id"])
    return {"schema_version": "routes-v2-audit-plan", "phase": phase, "fixed_seed": spec["fixed_audit_seed"], "raters": contract["evaluation"]["raters"], "ledger_sha256": canonical_json_sha256(ledger), "selected_trajectory_ids": [row["trajectory_id"] for row in selected]}


def _scan_audit_text(value: str) -> None:
    lowered = value.casefold()
    if any(marker in lowered for marker in _FORBIDDEN_AUDIT_TEXT) or "http://" in lowered or "https://" in lowered:
        raise AnalysisValidationError("audit packet leaks a prohibited route or provenance marker")
    if _DATE.search(value):
        raise AnalysisValidationError("audit packet leaks a document date")


def build_audit_packet(plan: dict[str, Any], ledger: list[dict[str, Any]], *, questions: dict[str, str], rubrics: dict[str, list[str]]) -> dict[str, Any]:
    """Build a route-redacted packet containing only inspectable answer evidence."""
    _legacy_free_form_disabled()
    selected = set(plan["selected_trajectory_ids"])
    items: list[dict[str, Any]] = []
    for row in ledger:
        if row["trajectory_id"] not in selected:
            continue
        topic = row["topic_id"]
        question = questions.get(topic)
        rubric = rubrics.get(topic)
        if not isinstance(question, str) or not isinstance(rubric, list) or not all(isinstance(item, str) for item in rubric):
            raise AnalysisValidationError("audit packet lacks a question or alias rubric")
        parsed_answer = row["parsed_answer"]
        sanitized_payload = row["sanitized_payload"]
        if not isinstance(parsed_answer, str) or not isinstance(sanitized_payload, str):
            raise AnalysisValidationError("audit output must be inspectable text")
        for text in [question, parsed_answer, sanitized_payload, *rubric]:
            _scan_audit_text(text)
        audit_id = "audit:" + hashlib.sha256(row["trajectory_id"].encode("utf-8")).hexdigest()[:24]
        items.append({"audit_id": audit_id, "question": question, "alias_rubric": rubric, "parsed_answer": parsed_answer, "sanitized_payload": sanitized_payload, "machine_label": row["machine_label"], "semantic_eligible": row["status"] in _SEMANTIC_STATUSES})
    if len(items) != len(selected) or len({item["audit_id"] for item in items}) != len(items):
        raise AnalysisValidationError("audit packet coverage is incomplete or duplicated")
    packet = {"schema_version": "routes-v2-route-redacted-audit-packet", "audit_plan_sha256": canonical_json_sha256(plan), "items": sorted(items, key=lambda item: item["audit_id"])}
    scan_audit_packet(packet)
    return packet


def scan_audit_packet(packet: Any) -> dict[str, Any]:
    """Reject a packet that exposes route, model, seed, date, or envelope identity."""
    _legacy_free_form_disabled()
    document = _mapping(packet, "audit packet", {"schema_version", "audit_plan_sha256", "items"})
    if document["schema_version"] != "routes-v2-route-redacted-audit-packet":
        raise AnalysisValidationError("audit packet schema is invalid")
    _sha(document["audit_plan_sha256"], "audit packet plan hash")
    if not isinstance(document["items"], list) or not document["items"]:
        raise AnalysisValidationError("audit packet items are invalid")
    fields = {"audit_id", "question", "alias_rubric", "parsed_answer", "sanitized_payload", "machine_label", "semantic_eligible"}
    for item in document["items"]:
        value = _mapping(item, "audit item", fields)
        for text in [value["question"], value["parsed_answer"], value["sanitized_payload"], *value["alias_rubric"]]:
            if not isinstance(text, str):
                raise AnalysisValidationError("audit text is invalid")
            _scan_audit_text(text)
        if not isinstance(value["semantic_eligible"], bool):
            raise AnalysisValidationError("audit semantic eligibility is invalid")
    return document


def audit_template(packet: dict[str, Any], rater_id: str, plan: dict[str, Any]) -> dict[str, Any]:
    """Create a complete, bound blank submission template for one predeclared rater."""
    _legacy_free_form_disabled()
    scan_audit_packet(packet)
    if rater_id not in plan["raters"]:
        raise AnalysisValidationError("rater is not predeclared")
    return {"schema_version": "routes-v2-audit-submission", "audit_packet_sha256": canonical_json_sha256(packet), "audit_plan_sha256": canonical_json_sha256(plan), "rater_id": rater_id, "labels": [{"audit_id": item["audit_id"], "label": ""} for item in packet["items"]]}


def _validate_submission(submission: Any, packet: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    value = _mapping(submission, "audit submission", {"schema_version", "audit_packet_sha256", "audit_plan_sha256", "rater_id", "labels"})
    if value["schema_version"] != "routes-v2-audit-submission" or value["audit_packet_sha256"] != canonical_json_sha256(packet) or value["audit_plan_sha256"] != canonical_json_sha256(plan):
        raise AnalysisValidationError("audit submission binding drifted")
    if value["rater_id"] not in plan["raters"] or not isinstance(value["labels"], list):
        raise AnalysisValidationError("audit submission identity is invalid")
    expected = {item["audit_id"] for item in packet["items"]}
    observed: set[str] = set()
    for row in value["labels"]:
        item = _mapping(row, "audit label", {"audit_id", "label"})
        if item["audit_id"] in observed or item["audit_id"] not in expected or item["label"] not in {"post_only", "not_post_only"}:
            raise AnalysisValidationError("audit submission labels are incomplete or invalid")
        observed.add(item["audit_id"])
    if observed != expected:
        raise AnalysisValidationError("audit submission must cover every packet item")
    return value


def _kappa(left: list[str], right: list[str]) -> float | None:
    if not left:
        return None
    observed = sum(a == b for a, b in zip(left, right)) / len(left)
    labels = {"post_only", "not_post_only"}
    expected = sum((left.count(label) / len(left)) * (right.count(label) / len(right)) for label in labels)
    return None if expected == 1 else (observed - expected) / (1 - expected)


def analyze_audit(plan: dict[str, Any], packet: dict[str, Any], first: Any, second: Any) -> dict[str, Any]:
    """Report audit agreement without replacing deterministic machine outcomes."""
    _legacy_free_form_disabled()
    scan_audit_packet(packet)
    left = _validate_submission(first, packet, plan)
    right = _validate_submission(second, packet, plan)
    if left["rater_id"] == right["rater_id"]:
        raise AnalysisValidationError("two distinct predeclared raters are required")
    by_id = {item["audit_id"]: item for item in packet["items"]}
    left_labels = {row["audit_id"]: row["label"] for row in left["labels"]}
    right_labels = {row["audit_id"]: row["label"] for row in right["labels"]}
    semantic_ids = sorted(identifier for identifier, item in by_id.items() if item["semantic_eligible"])
    first_values = [left_labels[identifier] for identifier in semantic_ids]
    second_values = [right_labels[identifier] for identifier in semantic_ids]
    machine_disagreements = sum(left_labels[identifier] != ("post_only" if by_id[identifier]["machine_label"] == "post_only" else "not_post_only") for identifier in by_id)
    rater_disagreements = sum(left_labels[identifier] != right_labels[identifier] for identifier in by_id)
    return {"schema_version": "routes-v2-audit-analysis", "audit_plan_sha256": canonical_json_sha256(plan), "audit_packet_sha256": canonical_json_sha256(packet), "submission_sha256s": [canonical_json_sha256(left), canonical_json_sha256(right)], "audit_population": len(by_id), "semantic_kappa_denominator": len(semantic_ids), "semantic_kappa": _kappa(first_values, second_values), "coverage": {left["rater_id"]: len(left_labels), right["rater_id"]: len(right_labels)}, "machine_human_disagreements": machine_disagreements, "rater_rater_disagreements": rater_disagreements, "primary_label_source": "deterministic_machine_score"}


def source_code_hashes(root: str | Path) -> dict[str, str]:
    """Hash every v2 source file, including this analysis module."""
    directory = Path(root)
    names = ("schema.py", "manifest.py", "retrieval.py", "runtime.py", "runner.py", "scoring.py", "analysis.py", "source_integrity.py", "sources.py", "curation.py", "human_review.py")
    return {name: "sha256:" + hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names}


def repository_provenance(repository: str | Path) -> dict[str, str]:
    """Capture the exact local Git commit, tree, and configured origin."""
    root = Path(repository)
    def run(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()
    return {"commit": run("rev-parse", "HEAD"), "tree": run("rev-parse", "HEAD^{tree}"), "remote": run("config", "--get", "remote.origin.url")}


def build_analysis_receipt(*, contract: dict[str, Any], frame: dict[str, Any], source_decisions: dict[str, Any], source_gate: dict[str, Any], manifest: dict[str, Any], freeze_receipt: dict[str, Any], calibration: dict[str, Any], schedule: dict[str, Any], ledger: list[dict[str, Any]], audit_plan: dict[str, Any], first_submission: dict[str, Any], second_submission: dict[str, Any], audit_analysis: dict[str, Any], repository: str | Path, source_directory: str | Path) -> dict[str, Any]:
    """Emit a finite-set receipt binding every analysis input and generated output."""
    _legacy_free_form_disabled()
    provenance = repository_provenance(repository)
    inputs = {"contract": canonical_json_sha256(contract), "frame": canonical_json_sha256(frame), "source_decisions": canonical_json_sha256(source_decisions), "source_gate": canonical_json_sha256(source_gate), "manifest": canonical_json_sha256(manifest), "freeze_receipt": canonical_json_sha256(freeze_receipt), "calibration": canonical_json_sha256(calibration), "schedule": canonical_json_sha256(schedule), "ledger": canonical_json_sha256(ledger), "audit_plan": canonical_json_sha256(audit_plan), "first_submission": canonical_json_sha256(first_submission), "second_submission": canonical_json_sha256(second_submission)}
    outputs = {"audit_analysis": canonical_json_sha256(audit_analysis)}
    receipt = {"schema_version": "routes-v2-analysis-receipt", "result_mode": "finite_set", "inputs": inputs, "source_code": source_code_hashes(source_directory), "outputs": outputs, "repository": provenance, "gates": {"source_gate": source_gate.get("status"), "primary_label_source": audit_analysis.get("primary_label_source"), "audit_coverage": audit_analysis.get("coverage")}}
    receipt["receipt_sha256"] = canonical_json_sha256(receipt)
    return receipt


def validate_analysis_receipt(receipt: Any, **inputs: Any) -> dict[str, Any]:
    """Recompute every bound input/code/output receipt and reject any drift."""
    _legacy_free_form_disabled()
    if not isinstance(receipt, dict) or receipt.get("schema_version") != "routes-v2-analysis-receipt":
        raise AnalysisValidationError("analysis receipt schema is invalid")
    expected = build_analysis_receipt(**inputs)
    if receipt != expected:
        raise AnalysisValidationError("analysis receipt input, code, output, or repository binding drifted")
    return receipt


def _audit_count(contract: dict[str, Any], phase: str) -> int:
    spec = _phase_spec(contract, phase)
    return spec["topic_count"] * len(spec["models"]) * len(contract["evaluation"]["primary_arms"])


def _malformed_public_payload(
    raw: bytes,
    private_blind_key: bytes,
    parse_error_class: str,
) -> dict[str, Any]:
    """Describe unusable bytes without publishing a response-joinable identity."""
    digest = hmac.new(
        private_blind_key,
        b"routes-v2-public-malformed-response\0" + raw,
        hashlib.sha256,
    ).hexdigest()
    return {
        "kind": "malformed_response",
        "parse_error_class": parse_error_class,
        "preview": _MALFORMED_PREVIEW,
        "byte_length": len(raw),
        "blinded_digest": _BLIND_DIGEST_PREFIX + digest,
    }


def _sanitized_response_content(content: str) -> str | None:
    """Keep a declared answer value, never its citation or response envelope."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return content
    if (
        isinstance(parsed, dict)
        and set(parsed) == {"answer", "citation_id"}
        and isinstance(parsed["answer"], str)
        and isinstance(parsed["citation_id"], str)
    ):
        return parsed["answer"]
    return None


def _redact_private_values(content: str, private_values: set[str]) -> str:
    """Replace only full retained private values, preserving ordinary language."""
    redacted = content
    for private_value in sorted(
        (value for value in private_values if value),
        key=lambda value: (-len(value), value),
    ):
        redacted = redacted.replace(private_value, _PRIVATE_VALUE_REDACTION)
    return redacted


def _public_payload(
    response: dict[str, Any],
    private_values: set[str],
    private_blind_key: bytes,
) -> dict[str, Any]:
    """Expose response content only after checking exact private values cannot escape.

    The sanitizer is value-driven.  It rejects values actually present in the
    private join rather than banning ordinary words such as ``model`` or an
    unrelated year such as ``1999``.
    """
    from anachron.routes.v2.runtime import validate_bytes_receipt

    raw = validate_bytes_receipt(response)
    try:
        envelope = json.loads(raw.decode("utf-8"))
        content = envelope["message"]["content"]
        if not isinstance(content, str):
            raise TypeError
        sanitized = _sanitized_response_content(content)
        value: dict[str, Any] = (
            {"kind": "response_content", "content": _redact_private_values(sanitized, private_values)}
            if sanitized is not None
            else _malformed_public_payload(raw, private_blind_key, "response_content_unavailable")
        )
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        value = _malformed_public_payload(raw, private_blind_key, "response_envelope_unavailable")
    return value


def build_private_audit_join(
    execution: ValidatedExecution,
    *,
    phase: str,
    private_blind_key: bytes,
    questions: dict[str, str],
    alias_rubrics: dict[str, list[str]],
    instructions: str,
) -> PrivateAuditJoin:
    """Select the frozen audit slice privately, then derive a blind public packet."""
    if not isinstance(execution, ValidatedExecution):
        raise AnalysisValidationError("audit selection requires a ValidatedExecution")
    if not isinstance(private_blind_key, bytes) or len(private_blind_key) < 16:
        raise AnalysisValidationError("private blind key must be at least 128 bits")
    if not isinstance(instructions, str) or not instructions:
        raise AnalysisValidationError("audit instructions are required")
    contract = execution._contract
    spec = _phase_spec(contract, phase)
    selected = [
        row for row in execution._outcomes
        if row["study_phase"] == phase and row["condition"] in contract["evaluation"]["primary_arms"]
        and row["seed"] == spec["fixed_audit_seed"] and row["model_id"] in spec["models"]
    ]
    if len(selected) != _audit_count(contract, phase):
        raise AnalysisValidationError("validated execution does not contain the exact frozen audit population")
    identities = {(row["topic_id"], row["model_id"], row["condition"]) for row in selected}
    topics = {row["topic_id"] for row in selected}
    expected = {(topic, model, condition) for topic in topics for model in spec["models"] for condition in contract["evaluation"]["primary_arms"]}
    if len(topics) != spec["topic_count"] or identities != expected:
        raise AnalysisValidationError("validated execution audit slice is not route-balanced")
    packet_items: list[dict[str, Any]] = []
    join: dict[str, dict[str, Any]] = {}
    for row in sorted(selected, key=lambda value: value["trajectory_id"]):
        topic = row["topic_id"]
        question, aliases = questions.get(topic), alias_rubrics.get(topic)
        if not isinstance(question, str) or not question or not isinstance(aliases, list) or not aliases or not all(isinstance(alias, str) and alias for alias in aliases):
            raise AnalysisValidationError("audit packet requires a question and sealed alias rubric for each topic")
        digest = hashlib.sha256(private_blind_key + b"\0" + row["trajectory_id"].encode("utf-8")).hexdigest()[:24]
        audit_id = "audit:" + digest
        if audit_id in join:
            raise AnalysisValidationError("private blind key produced a duplicate audit ID")
        private_values = set(execution._private_values) | {
            row["trajectory_id"], row["condition"], row["model_id"],
            row["request_sha256"], row["delivery_sha256"], row["response_sha256"], row["terminal_record_sha256"],
        }
        payload = _public_payload(row["response"], private_values, private_blind_key)
        packet_items.append({
            "audit_id": audit_id,
            "question": question,
            "alias_rubric": aliases,
            "inspectable_payload": payload,
            "instructions": instructions,
        })
        join[audit_id] = {
            "trajectory_id": row["trajectory_id"],
            "machine_label": row["machine_label"],
            "semantic_eligible": row["status"] in _SEMANTIC_STATUSES,
            "response_sha256": row["response_sha256"],
        }
    packet = {
        "schema_version": "routes-v2-public-audit-packet",
        "phase": phase,
        "items": sorted(packet_items, key=lambda item: item["audit_id"]),
    }
    scan_public_audit_packet(packet)
    return PrivateAuditJoin(_PRIVATE_AUDIT_TOKEN, execution, phase, packet, join)


def scan_public_audit_packet(packet: Any) -> dict[str, Any]:
    """Reject any public audit packet field beyond the blind-rater allowlist."""
    document = _mapping(packet, "public audit packet", {"schema_version", "phase", "items"})
    if document["schema_version"] != "routes-v2-public-audit-packet" or document["phase"] not in {"pilot", "confirmatory"} or not isinstance(document["items"], list) or not document["items"]:
        raise AnalysisValidationError("public audit packet schema is invalid")
    ids: set[str] = set()
    fields = {"audit_id", "question", "alias_rubric", "inspectable_payload", "instructions"}
    for item in document["items"]:
        value = _mapping(item, "public audit item", fields)
        if not isinstance(value["audit_id"], str) or value["audit_id"] in ids or not isinstance(value["question"], str) or not isinstance(value["alias_rubric"], list) or not all(isinstance(alias, str) for alias in value["alias_rubric"]) or not isinstance(value["inspectable_payload"], dict) or not isinstance(value["instructions"], str):
            raise AnalysisValidationError("public audit item is invalid")
        payload = value["inspectable_payload"]
        if payload.get("kind") == "response_content":
            content = _mapping(payload, "public response content", {"kind", "content"})
            if not isinstance(content["content"], str):
                raise AnalysisValidationError("public response content is invalid")
        elif payload.get("kind") == "malformed_response":
            malformed = _mapping(
                payload,
                "public malformed response",
                {"kind", "parse_error_class", "preview", "byte_length", "blinded_digest"},
            )
            if (
                malformed["parse_error_class"] not in _PUBLIC_PARSE_ERROR_CLASSES
                or malformed["preview"] != _MALFORMED_PREVIEW
                or isinstance(malformed["byte_length"], bool)
                or not isinstance(malformed["byte_length"], int)
                or malformed["byte_length"] < 0
                or not isinstance(malformed["blinded_digest"], str)
                or not malformed["blinded_digest"].startswith(_BLIND_DIGEST_PREFIX)
                or len(malformed["blinded_digest"]) != len(_BLIND_DIGEST_PREFIX) + 64
            ):
                raise AnalysisValidationError("public malformed response is invalid")
        else:
            raise AnalysisValidationError("public inspectable payload kind is invalid")
        ids.add(value["audit_id"])
    return document


def public_audit_template(private: PrivateAuditJoin, rater_id: str) -> dict[str, Any]:
    """Return the only accepted packet-bound submission shape for one rater."""
    if not isinstance(private, PrivateAuditJoin):
        raise AnalysisValidationError("audit submission requires a private audit join")
    if rater_id not in private._execution._contract["evaluation"]["raters"]:
        raise AnalysisValidationError("rater is not predeclared")
    return {
        "schema_version": "routes-v2-public-audit-submission",
        "audit_packet_sha256": canonical_json_sha256(private._packet),
        "rater_id": rater_id,
        "labels": [{"audit_id": item["audit_id"], "label": ""} for item in private._packet["items"]],
    }


def _validate_public_submission(submission: Any, private: PrivateAuditJoin) -> dict[str, Any]:
    expected_ids = {item["audit_id"] for item in private._packet["items"]}
    value = _mapping(submission, "public audit submission", {"schema_version", "audit_packet_sha256", "rater_id", "labels"})
    if value["schema_version"] != "routes-v2-public-audit-submission" or value["audit_packet_sha256"] != canonical_json_sha256(private._packet) or value["rater_id"] not in private._execution._contract["evaluation"]["raters"] or not isinstance(value["labels"], list):
        raise AnalysisValidationError("public audit submission binding is invalid")
    found: set[str] = set()
    for item in value["labels"]:
        label = _mapping(item, "public audit label", {"audit_id", "label"})
        if label["audit_id"] in found or label["audit_id"] not in expected_ids or label["label"] not in {"post_only", "not_post_only"}:
            raise AnalysisValidationError("public audit labels are invalid or incomplete")
        found.add(label["audit_id"])
    if found != expected_ids:
        raise AnalysisValidationError("public audit submission must cover every public audit ID")
    return value


def validate_public_audit(private: PrivateAuditJoin, first: Any, second: Any) -> ValidatedAudit:
    """Join two complete distinct-identity public ratings only in private memory."""
    if not isinstance(private, PrivateAuditJoin):
        raise AnalysisValidationError("audit validation requires a private audit join")
    left, right = _validate_public_submission(first, private), _validate_public_submission(second, private)
    if left["rater_id"] == right["rater_id"]:
        raise AnalysisValidationError("two distinct raters are required")
    left_labels = {row["audit_id"]: row["label"] for row in left["labels"]}
    right_labels = {row["audit_id"]: row["label"] for row in right["labels"]}
    semantic = sorted(identifier for identifier, row in private._join.items() if row["semantic_eligible"])
    left_semantic = [left_labels[identifier] for identifier in semantic]
    right_semantic = [right_labels[identifier] for identifier in semantic]
    machine = {identifier: "post_only" if row["machine_label"] == "post_only" else "not_post_only" for identifier, row in private._join.items()}
    population = len(private._join)
    report = {
        "schema_version": "routes-v2-public-audit-report",
        "audit_packet_sha256": canonical_json_sha256(private._packet),
        "coverage": {left["rater_id"]: len(left_labels), right["rater_id"]: len(right_labels)},
        "audit_population": population,
        "semantic_kappa_denominator": len(semantic),
        "semantic_kappa": _kappa(left_semantic, right_semantic),
        "rater_rater_disagreements": sum(left_labels[key] != right_labels[key] for key in left_labels),
        "machine_human_disagreements": {
            left["rater_id"]: sum(left_labels[key] != machine[key] for key in left_labels),
            right["rater_id"]: sum(right_labels[key] != machine[key] for key in right_labels),
        },
        "primary_label_source": "deterministic_machine_score",
    }
    return ValidatedAudit(_VALIDATED_AUDIT_TOKEN, private, (left, right), report)


def reduce_finite_set(execution: ValidatedExecution, audit: ValidatedAudit) -> FiniteSetResult:
    """Derive the sole finite-set effect, gates, result mode, and self-hash."""
    if not isinstance(execution, ValidatedExecution) or not isinstance(audit, ValidatedAudit) or audit._execution is not execution:
        raise AnalysisValidationError("finite-set reduction requires matching validated execution and audit")
    contract, phase = execution._contract, audit._phase
    if phase not in {"pilot", "confirmatory"}:
        raise AnalysisValidationError("development has no human-audit finite-set result")
    rows = [row for row in execution._outcomes if row["study_phase"] == phase and row["condition"] in contract["evaluation"]["primary_arms"]]
    if any(row["study_phase"] != phase for row in execution._outcomes):
        raise AnalysisValidationError("finite-set reducer never pools phases")
    paired: list[float] = []
    by_key: dict[tuple[str, str, int], dict[str, int]] = {}
    for row in rows:
        by_key.setdefault((row["topic_id"], row["model_id"], row["seed"]), {})[row["condition"]] = row["post_only"]
    for key in sorted(by_key):
        arms = by_key[key]
        if set(arms) != set(contract["evaluation"]["primary_arms"]):
            raise AnalysisValidationError("finite-set primary arms are incomplete")
        paired.append(float(arms["post_misdated_eligible"] - arms["post_truthful"]))
    if not paired:
        raise AnalysisValidationError("finite-set primary comparison is empty")
    effect = sum(paired) / len(paired)
    threshold = contract["development"]["threshold"]["minimum_mean_paired_difference"]
    gates = {
        "source_gate_pass": execution._artifacts["source_gate_sha256"] is not None,
        "complete_primary_pairs": len(paired) == len(by_key),
        "audit_full_coverage": all(count == audit._report["audit_population"] for count in audit._report["coverage"].values()),
        "audit_population_exact": audit._report["audit_population"] == _audit_count(contract, phase),
        "phase_threshold": effect >= threshold,
    }
    mode = "positive" if all(gates.values()) else "negative"
    value = {
        "schema_version": "routes-v2-finite-set-result",
        "phase": phase,
        "execution_artifacts_sha256": canonical_json_sha256(execution._artifacts),
        "audit_report_sha256": canonical_json_sha256(audit._report),
        "paired_misdated_minus_truthful": effect,
        "paired_count": len(paired),
        "stability": {"minimum": min(paired), "maximum": max(paired), "mean": effect},
        "gates": gates,
        "result_mode": mode,
    }
    value["result_sha256"] = canonical_json_sha256(value)
    return FiniteSetResult(_FINITE_SET_TOKEN, value)


def reduce_development(execution: ValidatedExecution) -> FiniteSetResult:
    """Reduce the development screen without creating a development audit claim."""
    if not isinstance(execution, ValidatedExecution):
        raise AnalysisValidationError("development reduction requires guarded execution")
    rows = list(execution._outcomes)
    if not rows or any(row["study_phase"] != "development" for row in rows):
        raise AnalysisValidationError("development reducer accepts only development execution")
    arms_by_key: dict[tuple[str, str, int], dict[str, int]] = {}
    for row in rows:
        if row["condition"] not in execution._contract["development"]["primary_arms"]:
            raise AnalysisValidationError("development schedule contains an evaluation-only condition")
        arms_by_key.setdefault((row["topic_id"], row["model_id"], row["seed"]), {})[row["condition"]] = row["post_only"]
    if len(arms_by_key) != 12:
        raise AnalysisValidationError("development primary pairs are incomplete")
    paired = []
    for arms in arms_by_key.values():
        if set(arms) != set(execution._contract["development"]["primary_arms"]):
            raise AnalysisValidationError("development primary arms are incomplete")
        paired.append(float(arms["post_misdated_eligible"] - arms["post_truthful"]))
    effect = sum(paired) / len(paired)
    gates = {"source_gate_pass": execution._artifacts["source_gate_sha256"] is not None, "complete_primary_pairs": len(paired) == 12, "trace_integrity": len(rows) == 24, "development_threshold": effect >= execution._contract["development"]["threshold"]["minimum_mean_paired_difference"]}
    value = {"schema_version": "routes-v2-finite-set-result", "phase": "development", "execution_artifacts_sha256": canonical_json_sha256(execution._artifacts), "audit_report_sha256": None, "paired_misdated_minus_truthful": effect, "paired_count": len(paired), "stability": {"minimum": min(paired), "maximum": max(paired), "mean": effect}, "gates": gates, "result_mode": "positive" if all(gates.values()) else "negative"}
    value["result_sha256"] = canonical_json_sha256(value)
    return FiniteSetResult(_FINITE_SET_TOKEN, value)


def validate_finite_set_result(result: FiniteSetResult, *, expected_phase: str) -> dict[str, Any]:
    """Return only an authentic self-hashed guarded result for one exact phase."""
    if not isinstance(result, FiniteSetResult):
        raise AnalysisValidationError("finite-set result must be reducer-owned")
    value = result.value
    unsigned = {key: item for key, item in value.items() if key != "result_sha256"}
    if value.get("schema_version") != "routes-v2-finite-set-result" or value.get("phase") != expected_phase or value.get("result_sha256") != canonical_json_sha256(unsigned) or value.get("result_mode") not in {"positive", "negative"} or not isinstance(value.get("gates"), dict) or not value["gates"]:
        raise AnalysisValidationError("finite-set result is malformed")
    if (value["result_mode"] == "positive") != all(value["gates"].values()):
        raise AnalysisValidationError("finite-set result mode does not match its gates")
    return value


_ANALYSIS_ROOT_BASE_FILES = {
    "pending_draft.json", "source_decisions.json", "source_gate.json", "manifest.json",
    "freeze_receipt.json", "closure_lock.json", "schedule.json", "session_calibrations.json",
    "sealed_aliases.json", "journal.jsonl",
}
_ANALYSIS_ROOT_AUDIT_FILES = {
    "audit_blind_key.bin", "questions.json", "alias_rubrics.json", "instructions.txt",
    "rater-a.json", "rater-b.json",
}


def _analysis_root_file(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if root not in path.parents or not path.is_file():
        raise AnalysisValidationError(f"analysis root is missing required artifact: {name}")
    return path


def _analysis_root_object(root: Path, name: str) -> dict[str, Any]:
    try:
        value = json.loads(_analysis_root_file(root, name).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisValidationError(f"analysis root artifact is not JSON: {name}") from error
    if not isinstance(value, dict):
        raise AnalysisValidationError(f"analysis root artifact must be an object: {name}")
    return value


def replay_phase_root(analysis_root: str | Path, frozen_root: str | Path, *, phase: str) -> tuple[FiniteSetResult, dict[str, Any]]:
    """Replay one phase result from its complete stored root before downstream admission."""
    from anachron.routes.v2.admission import (
        AdmissionError,
        admit_clean_checkout,
        open_validated_execution,
        validate_loaded_code_closure,
    )
    from anachron.routes.v2.schema import load_contract

    root, repository = Path(analysis_root).resolve(), Path(frozen_root).resolve()
    expected_files = _ANALYSIS_ROOT_BASE_FILES | (_ANALYSIS_ROOT_AUDIT_FILES if phase != "development" else set())
    if phase not in {"development", "pilot", "confirmatory"} or not root.is_dir() or {path.name for path in root.iterdir()} != expected_files:
        raise AnalysisValidationError("analysis root must contain exactly the frozen replay artifact set")
    contract = load_contract(repository / "research" / "routes-v2" / "contract.json")
    frame = json.loads((repository / "research" / "routes-v2" / "sampling_frame.json").read_text(encoding="utf-8"))
    closure = _analysis_root_object(root, "closure_lock.json")
    freeze = _analysis_root_object(root, "freeze_receipt.json")
    try:
        admit_clean_checkout(repository, freeze, closure)
        validate_loaded_code_closure(repository, closure)
        execution = open_validated_execution(
            phase=phase, repository=repository, contract=contract, sampling_frame=frame,
            pending_draft=_analysis_root_object(root, "pending_draft.json"),
            source_decisions=_analysis_root_object(root, "source_decisions.json"),
            source_gate=_analysis_root_object(root, "source_gate.json"),
            manifest=_analysis_root_object(root, "manifest.json"), freeze_receipt=freeze,
            closure_lock=closure, schedule=_analysis_root_object(root, "schedule.json"),
            session_calibration_receipts=_analysis_root_object(root, "session_calibrations.json").get("receipts"),
            journal_path=_analysis_root_file(root, "journal.jsonl"),
            sealed_aliases=_analysis_root_object(root, "sealed_aliases.json"),
        )
    except (AdmissionError, OSError, ValueError) as error:
        raise AnalysisValidationError("analysis root execution replay failed") from error
    audit: ValidatedAudit | None = None
    try:
        if phase == "development":
            result = reduce_development(execution)
        else:
            private = build_private_audit_join(
                execution, phase=phase, private_blind_key=_analysis_root_file(root, "audit_blind_key.bin").read_bytes(),
                questions=_analysis_root_object(root, "questions.json"),
                alias_rubrics=_analysis_root_object(root, "alias_rubrics.json"),
                instructions=_analysis_root_file(root, "instructions.txt").read_text(encoding="utf-8"),
            )
            audit = validate_public_audit(private, _analysis_root_object(root, "rater-a.json"), _analysis_root_object(root, "rater-b.json"))
            result = reduce_finite_set(execution, audit)
    except (OSError, ValueError) as error:
        raise AnalysisValidationError("analysis root audit/reduction replay failed") from error
    receipt = {
        "schema_version": "routes-v2-analysis-replay-receipt",
        "analysis_root_sha256": "sha256:" + hashlib.sha256(b"".join(name.encode("utf-8") + b"\0" + _analysis_root_file(root, name).read_bytes() + b"\0" for name in sorted(expected_files))).hexdigest(),
        "repository": repository.as_posix(),
        "contract_sha256": canonical_json_sha256(contract),
        "execution_artifacts_sha256": canonical_json_sha256(execution.artifacts),
        "audit_report_sha256": canonical_json_sha256(audit.report) if audit is not None else None,
        "result": result.value,
    }
    receipt["receipt_sha256"] = canonical_json_sha256(receipt)
    return result, receipt


def replay_analysis_root(analysis_root: str | Path, frozen_root: str | Path) -> tuple[FiniteSetResult, dict[str, Any]]:
    """Replay only the confirmatory reducer before final-result rendering."""
    return replay_phase_root(analysis_root, frozen_root, phase="confirmatory")
