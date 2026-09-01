"""Routes v2's crash-safe, session-bound execution admission chain."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anachron.routes.v2.admission import (
    AdmissionError,
    admit_clean_checkout,
    canonical_json_sha256,
    load_json_object,
    validate_loaded_code_closure,
    write_create_only,
)
from anachron.routes.v2.admission import canonical_json_bytes as artifact_json_bytes
from anachron.routes.v2.manifest import validate_manifest
from anachron.routes.v2.retrieval import (
    delivered_evidence_sha256,
    delivery_packet,
    primary_packets,
)
from anachron.routes.v2.runtime import (
    RuntimeValidationError,
    TransportResult,
    build_request,
    bytes_receipt,
    calibration_packet,
    canonical_json_bytes,
    classify_response,
    session_calibration_receipt,
    validate_bytes_receipt,
    validate_inventory,
    validate_loopback_endpoint,
    validate_session_calibration,
)
from anachron.routes.v2.schema import phase_spec


class RunnerValidationError(ValueError):
    """Raised before a v2 trajectory can be dispatched or resumed."""


class UnknownAfterClaimError(RunnerValidationError):
    """A persisted claim lacks a terminal result, so redispatch is forbidden."""


_EMPTY_SHA256 = "sha256:" + hashlib.sha256(b"").hexdigest()


@dataclass(frozen=True)
class ScheduledTrajectory:
    """One immutable counterbalanced development invocation."""

    schedule_index: int
    trajectory_id: str
    study_phase: str
    item_id: str
    model_id: str
    model_digest: str
    seed: int
    condition: str
    order_index: int
    block_index: int


def _freeze_receipt(freeze_receipt: Any, closure_lock: Any, phase: str) -> dict[str, Any]:
    required = {"schema_version", "study_phase", "commit", "tree", "branch", "remote", "closure_sha256"}
    if not isinstance(freeze_receipt, dict) or set(freeze_receipt) != required or freeze_receipt["schema_version"] != "routes-v2-freeze-receipt":
        raise RunnerValidationError("freeze receipt schema is invalid")
    if not isinstance(closure_lock, dict) or closure_lock.get("schema_version") != "routes-v2-code-closure" or not isinstance(closure_lock.get("closure_sha256"), str):
        raise RunnerValidationError("code closure lock schema is invalid")
    if freeze_receipt["study_phase"] != phase or freeze_receipt["closure_sha256"] != closure_lock["closure_sha256"]:
        raise RunnerValidationError("freeze receipt does not bind the code closure")
    return freeze_receipt


def _schedule_rows(manifest: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, Any]]:
    checked = validate_manifest(manifest, contract)
    phase = checked["study_phase"]
    spec = phase_spec(contract, phase)
    orders = contract["schedule"]["development_orders" if phase == "development" else "evaluation_orders"]
    model_map = {model["id"]: model for model in contract["models"]}
    rows: list[dict[str, Any]] = []
    for topic_index, pair in enumerate(sorted(checked["pairs"], key=lambda value: value["item_id"])):
        for model_index, model_id in enumerate(spec["models"]):
            model = model_map[model_id]
            order_index = (topic_index + model_index) % len(orders)
            for block_index, token in enumerate(orders[order_index]):
                seed_text, condition = token.split(":", 1)
                seed, schedule_index = int(seed_text[1:]), len(rows)
                identity = "\0".join((phase, pair["item_id"], model["id"], model["digest"], str(seed), condition, str(order_index), str(block_index), str(schedule_index)))
                rows.append({
                    "schedule_index": schedule_index, "study_phase": phase,
                    "trajectory_id": "routes-v2-trajectory:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
                    "item_id": pair["item_id"], "model_id": model["id"], "model_digest": model["digest"], "seed": seed,
                    "condition": condition, "order_index": order_index, "block_index": block_index,
                })
    expected = spec["topic_count"] * len(spec["models"]) * len(spec["conditions"]) * len(contract["execution"]["seeds"])
    if len(rows) != expected or len({row["trajectory_id"] for row in rows}) != expected:
        raise RunnerValidationError("v2 schedule does not contain the exact phase trajectory inventory")
    return rows


def derive_schedule(manifest: dict[str, Any], contract: dict[str, Any], *, source_gate: dict[str, Any], freeze_receipt: dict[str, Any], closure_lock: dict[str, Any]) -> dict[str, Any]:
    """Derive the only valid schedule from every frozen admission input."""
    checked = validate_manifest(manifest, contract)
    if source_gate != checked["source_gate_receipt"]:
        raise RunnerValidationError("schedule source gate does not match the sealed manifest")
    phase = checked["study_phase"]
    freeze = _freeze_receipt(freeze_receipt, closure_lock, phase)
    return {
        "schema_version": "routes-v2-schedule", "study_phase": phase, "algorithm": "routes-v2-counterbalance-v3", "seed": 20260901,
        "contract_sha256": canonical_json_sha256(contract), "manifest_sha256": canonical_json_sha256(checked),
        "source_gate_sha256": canonical_json_sha256(source_gate), "freeze_receipt_sha256": canonical_json_sha256(freeze),
        "closure_sha256": closure_lock["closure_sha256"], "trajectories": _schedule_rows(checked, contract),
    }


def validate_schedule(schedule: Any, manifest: dict[str, Any], contract: dict[str, Any], *, source_gate: dict[str, Any], freeze_receipt: dict[str, Any], closure_lock: dict[str, Any]) -> dict[str, Any]:
    """Accept only byte-for-byte rederivation of the canonical schedule."""
    expected = derive_schedule(manifest, contract, source_gate=source_gate, freeze_receipt=freeze_receipt, closure_lock=closure_lock)
    if not isinstance(schedule, dict) or schedule != expected:
        raise RunnerValidationError("schedule drifted from its frozen admission inputs")
    return expected


def create_schedule(output_path: str | Path, manifest: dict[str, Any], contract: dict[str, Any], *, source_gate: dict[str, Any], freeze_receipt: dict[str, Any], closure_lock: dict[str, Any]) -> dict[str, Any]:
    """Persist the canonical schedule once; a non-identical replacement is forbidden."""
    schedule = derive_schedule(manifest, contract, source_gate=source_gate, freeze_receipt=freeze_receipt, closure_lock=closure_lock)
    write_create_only(output_path, schedule)
    return validate_schedule(load_json_object(output_path), manifest, contract, source_gate=source_gate, freeze_receipt=freeze_receipt, closure_lock=closure_lock)


def schedule_development(manifest: dict[str, Any], contract: dict[str, Any]) -> list[ScheduledTrajectory]:
    """Compatibility preview that rejects non-development manifests."""
    if manifest.get("study_phase") != "development":
        raise RunnerValidationError("schedule_development accepts development only")
    return [ScheduledTrajectory(**row) for row in _schedule_rows(manifest, contract)]


def _chain_hash(prefix: bytes) -> str:
    return "sha256:" + hashlib.sha256(prefix).hexdigest()


def _record_hash(unsigned: dict[str, Any]) -> str:
    return canonical_json_sha256(unsigned)


def _journal_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict) or "record_sha256" not in record:
        raise RunnerValidationError("journal record schema is invalid")
    unsigned = {key: value for key, value in record.items() if key != "record_sha256"}
    if record["record_sha256"] != _record_hash(unsigned):
        raise RunnerValidationError("journal record hash drifted")
    return record


class ExecutionJournal:
    """The exclusive, fsynced append-only claim/outcome journal for one schedule."""

    def __init__(self, path: str | Path, schedule: dict[str, Any]):
        self.path, self.schedule = Path(path), schedule
        self.schedule_sha256, self._closed = canonical_json_sha256(schedule), False
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock_descriptor = os.open(self.lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError as error:
            raise RunnerValidationError("execution journal is already locked or left locked after a crash") from error
        try:
            os.write(self._lock_descriptor, (self.schedule_sha256 + "\n").encode("ascii"))
            os.fsync(self._lock_descriptor)
            if not self.path.exists():
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
                os.close(descriptor)
            self.records = self._replay()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        os.close(self._lock_descriptor)
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> ExecutionJournal:  # noqa: PYI034 - Self requires Python 3.11.
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _replay(self) -> list[dict[str, Any]]:
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise RunnerValidationError("journal has a partial final record")
        records, offset = [], 0
        for line in raw.splitlines(keepends=True):
            try:
                record = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise RunnerValidationError("journal line is not canonical JSON") from error
            if artifact_json_bytes(record) != line:
                raise RunnerValidationError("journal line is not canonical bytes")
            self._validate_record(record, records, raw[:offset])
            records.append(record)
            offset += len(line)
        return records

    def _state(self, prior: list[dict[str, Any]]) -> tuple[int, dict[str, Any] | None, int]:
        index, open_claim, retry_attempt = 0, None, 0
        for record in prior:
            if record["record_type"] == "dispatch_claim":
                if open_claim is not None:
                    raise RunnerValidationError("journal contains a second claim before terminal outcome")
                open_claim = record
                continue
            if open_claim is None or record["claim_record_sha256"] != open_claim["record_sha256"]:
                raise RunnerValidationError("terminal outcome has no matching open claim")
            if record["status"] == "transport_failure_no_response_object" and open_claim["attempt"] == 1:
                retry_attempt = 2
            else:
                index, retry_attempt = index + 1, 0
            open_claim = None
        return index, open_claim, retry_attempt

    def _validate_record(self, record: Any, prior: list[dict[str, Any]], prefix: bytes) -> None:
        record = _journal_record(record)
        previous = prior[-1]["record_sha256"] if prior else None
        if record.get("previous_record_sha256") != previous or record.get("previous_prefix_sha256") != _chain_hash(prefix):
            raise RunnerValidationError("journal record chain drifted")
        if record.get("record_type") == "dispatch_claim":
            self._validate_claim(record, prior)
        elif record.get("record_type") == "terminal_outcome":
            self._validate_terminal(record, prior)
        else:
            raise RunnerValidationError("journal record type is invalid")

    def _validate_claim(self, record: dict[str, Any], prior: list[dict[str, Any]]) -> None:
        fields = {"schema_version", "record_type", "run_id", "session_nonce", "schedule_index", "attempt", "trajectory", "admission", "request", "delivery", "previous_record_sha256", "previous_prefix_sha256", "record_sha256"}
        if set(record) != fields or record["schema_version"] != "routes-v2-journal-record":
            raise RunnerValidationError("dispatch claim schema is invalid")
        index, open_claim, retry_attempt = self._state(prior)
        if open_claim is not None:
            raise UnknownAfterClaimError("UNKNOWN_AFTER_CLAIM: claimed trajectory has no terminal outcome")
        if index >= len(self.schedule["trajectories"]):
            raise RunnerValidationError("journal claims beyond the frozen schedule")
        trajectory = self.schedule["trajectories"][index]
        if record["schedule_index"] != index or record["trajectory"] != trajectory or record["attempt"] != (retry_attempt or 1):
            raise RunnerValidationError("dispatch claim violates strict schedule prefix or retry order")
        expected = {
            "contract_sha256": self.schedule["contract_sha256"], "manifest_sha256": self.schedule["manifest_sha256"],
            "source_gate_sha256": self.schedule["source_gate_sha256"], "freeze_receipt_sha256": self.schedule["freeze_receipt_sha256"],
            "closure_sha256": self.schedule["closure_sha256"], "schedule_sha256": self.schedule_sha256,
        }
        admission = record["admission"]
        if not isinstance(admission, dict) or set(admission) != set(expected) | {"calibration_sha256"} or any(admission[key] != value for key, value in expected.items()) or not isinstance(admission["calibration_sha256"], str):
            raise RunnerValidationError("dispatch claim admission binding drifted")
        request_bytes = validate_bytes_receipt(record["request"])
        try:
            request = json.loads(request_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RunnerValidationError("dispatch claim request bytes are invalid") from error
        if canonical_json_bytes(request) != request_bytes or request.get("model") != trajectory["model_id"] or request.get("options", {}).get("seed") != trajectory["seed"]:
            raise RunnerValidationError("dispatch claim request drifted")
        delivery = record["delivery"]
        if not isinstance(delivery, dict) or set(delivery) != {"packet_sha256", "model_visible_packet_sha256", "delivered_evidence_sha256"} or not all(isinstance(value, str) for value in delivery.values()):
            raise RunnerValidationError("dispatch claim delivery binding is invalid")

    def _validate_terminal(self, record: dict[str, Any], prior: list[dict[str, Any]]) -> None:
        fields = {"schema_version", "record_type", "run_id", "session_nonce", "schedule_index", "attempt", "claim_record_sha256", "status", "error", "response_object_exists", "response", "envelope_valid", "delivery_valid", "trace_valid", "score", "previous_record_sha256", "previous_prefix_sha256", "record_sha256"}
        if set(record) != fields or record["schema_version"] != "routes-v2-journal-record":
            raise RunnerValidationError("terminal outcome schema is invalid")
        _index, claim, _retry = self._state(prior)
        if claim is None or record["claim_record_sha256"] != claim["record_sha256"]:
            raise RunnerValidationError("terminal outcome does not close the open claim")
        if any(record[name] != claim[name] for name in ("run_id", "session_nonce", "schedule_index", "attempt")):
            raise RunnerValidationError("terminal outcome identity does not match its claim")
        response = validate_bytes_receipt(record["response"])
        statuses = {"ok", "transport_failure_no_response_object", "http_error", "read_error", "returned_error", "timeout_after_dispatch", "malformed_response", "invalid_output"}
        if record["status"] not in statuses or not isinstance(record["response_object_exists"], bool) or not all(isinstance(record[name], bool) for name in ("envelope_valid", "delivery_valid", "trace_valid")):
            raise RunnerValidationError("terminal outcome status or flags are invalid")
        if record["status"] == "transport_failure_no_response_object":
            if record["response_object_exists"] or response != b"" or record["envelope_valid"] or record["trace_valid"]:
                raise RunnerValidationError("no-response terminal incorrectly claims response evidence")
        elif not record["response_object_exists"]:
            raise RunnerValidationError("response-bearing terminal lacks response-object evidence")
        if not isinstance(record["error"], dict) or set(record["error"]) != {"kind", "message_sha256"} or not all(isinstance(record["error"][name], str) for name in ("kind", "message_sha256")):
            raise RunnerValidationError("terminal outcome error receipt is invalid")
        expected_trace = record["status"] == "ok" and record["envelope_valid"] and record["delivery_valid"] and record["score"] is not None
        if record["trace_valid"] != expected_trace:
            raise RunnerValidationError("trace validity does not derive from terminal evidence")

    def _append(self, candidate: dict[str, Any]) -> dict[str, Any]:
        if self._closed:
            raise RunnerValidationError("journal is closed")
        prefix = self.path.read_bytes()
        if prefix and not prefix.endswith(b"\n"):
            raise RunnerValidationError("journal has a partial final record")
        candidate = dict(candidate)
        candidate["previous_record_sha256"] = self.records[-1]["record_sha256"] if self.records else None
        candidate["previous_prefix_sha256"] = _chain_hash(prefix)
        candidate["record_sha256"] = _record_hash(candidate)
        self._validate_record(candidate, self.records, prefix)
        line = artifact_json_bytes(candidate)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0))
        try:
            if os.write(descriptor, line) != len(line):
                raise RunnerValidationError("journal append was truncated")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        replayed = self._replay()
        if not replayed or replayed[-1] != candidate:
            raise RunnerValidationError("journal append failed replay verification")
        self.records = replayed
        return candidate

    def append_claim(self, claim: dict[str, Any]) -> dict[str, Any]:
        return self._append(claim)

    def append_terminal(self, terminal: dict[str, Any]) -> dict[str, Any]:
        return self._append(terminal)

    def next_trajectory(self) -> tuple[dict[str, Any], int]:
        """Return the only admissible next trajectory or halt after an unresolved claim."""
        index, open_claim, retry_attempt = self._state(self.records)
        if open_claim is not None:
            raise UnknownAfterClaimError("UNKNOWN_AFTER_CLAIM: do not redispatch without a terminal outcome")
        if index >= len(self.schedule["trajectories"]):
            raise RunnerValidationError("frozen schedule is already exhausted")
        return self.schedule["trajectories"][index], retry_attempt or 1


class ExecutionSession:
    """One client-owned fresh-process session; calibration and next claim are inseparable."""

    def __init__(self, *, client: Any, contract: dict[str, Any], manifest: dict[str, Any], schedule: dict[str, Any], journal: ExecutionJournal, inventory: dict[str, str], client_binding: dict[str, Any], calibration_path: str | Path, session_nonce: str):
        self._client, self._contract, self._manifest, self._schedule, self._journal = client, contract, manifest, schedule, journal
        self._inventory, self._client_binding, self._calibration_path, self._session_nonce = inventory, client_binding, Path(calibration_path), session_nonce
        self._calibrations: dict[str, dict[str, Any]] = {}
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._journal.close()

    def __enter__(self) -> ExecutionSession:  # noqa: PYI034 - Self requires Python 3.11.
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _chat(self, request: dict[str, Any]) -> TransportResult:
        result = self._client.chat(request, self._contract["execution"]["timeout_seconds"])
        if not isinstance(result, TransportResult):
            raise RunnerValidationError("client must return a typed TransportResult")
        return result

    def _calibration_path_for(self, model_id: str) -> Path:
        """Give each model in the same session an immutable calibration artifact."""
        if model_id == self._contract["models"][0]["id"]:
            return self._calibration_path
        digest = hashlib.sha256(model_id.encode("utf-8")).hexdigest()[:16]
        return self._calibration_path.with_name(self._calibration_path.stem + "-" + digest + self._calibration_path.suffix)

    def _ensure_calibration(self, model_id: str) -> dict[str, Any]:
        if model_id in self._calibrations:
            return self._calibrations[model_id]
        request = build_request(calibration_packet(), self._contract, model_id=model_id, seed=self._contract["execution"]["seeds"][0])
        receipt = session_calibration_receipt(self._contract, inventory=self._inventory, client_binding=self._client_binding, closure_sha256=self._schedule["closure_sha256"], session_nonce=self._session_nonce, model_id=model_id, request=request, result=self._chat(request))
        path = self._calibration_path_for(model_id)
        write_create_only(path, receipt)
        self._calibrations[model_id] = validate_session_calibration(load_json_object(path), self._contract, inventory=self._inventory, client_binding=self._client_binding, closure_sha256=self._schedule["closure_sha256"], session_nonce=self._session_nonce, model_id=model_id)
        return self._calibrations[model_id]

    def dispatch_next(self) -> dict[str, Any]:
        """Calibrate once, then persist claim before the only permitted scientific chat call."""
        trajectory, attempt = self._journal.next_trajectory()
        if trajectory["condition"] in {"post_truthful", "post_misdated_eligible"}:
            primary_packets(self._manifest, self._contract, trajectory["item_id"])
        packet = delivery_packet(self._manifest, self._contract, item_id=trajectory["item_id"], condition=trajectory["condition"])
        pairs = [pair for pair in self._manifest["pairs"] if pair["item_id"] == trajectory["item_id"]]
        if len(pairs) != 1:
            raise RunnerValidationError("sealed manifest does not identify one next source pair")
        pair = pairs[0]
        request = build_request(packet, self._contract, model_id=trajectory["model_id"], seed=trajectory["seed"])
        packet_sha256 = canonical_json_sha256(packet)
        calibration = self._ensure_calibration(trajectory["model_id"])
        claim = self._journal.append_claim({
            "schema_version": "routes-v2-journal-record", "record_type": "dispatch_claim", "run_id": self._session_nonce, "session_nonce": self._session_nonce,
            "schedule_index": trajectory["schedule_index"], "attempt": attempt, "trajectory": trajectory,
            "admission": {"contract_sha256": self._schedule["contract_sha256"], "manifest_sha256": self._schedule["manifest_sha256"], "source_gate_sha256": self._schedule["source_gate_sha256"], "freeze_receipt_sha256": self._schedule["freeze_receipt_sha256"], "closure_sha256": self._schedule["closure_sha256"], "schedule_sha256": canonical_json_sha256(self._schedule), "calibration_sha256": calibration["receipt_sha256"]},
            "request": bytes_receipt(canonical_json_bytes(request)),
            "delivery": {"packet_sha256": packet_sha256, "model_visible_packet_sha256": packet_sha256, "delivered_evidence_sha256": delivered_evidence_sha256(packet)},
        })
        result = self._chat(request)
        classified = classify_response(
            result,
            requested_model=trajectory["model_id"],
            answer_rules={
                "pre_aliases": pair["pre_aliases"],
                "post_aliases": pair["post_aliases"],
                "abstention_aliases": self._manifest["answer_rules"]["abstention_aliases"],
            },
            expected_citation_id=packet["document"]["citation_id"],
        )
        error_kind = result.error_kind or ("none" if classified["status"] == "ok" else classified["status"])
        return self._journal.append_terminal({
            "schema_version": "routes-v2-journal-record", "record_type": "terminal_outcome", "run_id": self._session_nonce, "session_nonce": self._session_nonce,
            "schedule_index": trajectory["schedule_index"], "attempt": attempt, "claim_record_sha256": claim["record_sha256"], "status": classified["status"],
            "error": {"kind": error_kind, "message_sha256": _EMPTY_SHA256}, "response_object_exists": result.response_object_exists,
            "response": classified["response"], "envelope_valid": classified["envelope_valid"], "delivery_valid": True,
            "trace_valid": classified["status"] == "ok" and classified["envelope_valid"] and classified["score"] is not None, "score": classified["score"],
        })


def _client_binding(client: Any) -> dict[str, Any]:
    endpoint, config = getattr(client, "endpoint", None), getattr(client, "configuration", None)
    if callable(config):
        config = config()
    if not isinstance(endpoint, str) or not endpoint or not isinstance(config, dict):
        raise RunnerValidationError("client must expose immutable endpoint and configuration bindings")
    try:
        validate_loopback_endpoint(endpoint)
    except RuntimeValidationError as error:
        raise RunnerValidationError("client endpoint is not a permitted loopback origin") from error
    return {"endpoint": endpoint, "configuration": config}


def _require_phase_prerequisite(phase: str, prerequisite_result: Any, *, predecessor_evidence: Any = None) -> None:
    """Require a guarded positive result before opening downstream phase authority."""
    if phase == "development":
        if prerequisite_result is not None or predecessor_evidence is not None:
            raise RunnerValidationError("development does not accept a prior-phase result")
        return
    from anachron.routes.v2.admission import validate_phase_predecessor
    from anachron.routes.v2.analysis import FiniteSetResult, validate_finite_set_result

    required_phase = "development" if phase == "pilot" else "pilot" if phase == "confirmatory" else None
    if required_phase is None:
        raise RunnerValidationError("study phase is invalid")
    try:
        evidence = validate_phase_predecessor(predecessor_evidence, phase=phase)
    except AdmissionError as error:
        raise RunnerValidationError("downstream phase predecessor evidence is invalid") from error
    value = evidence["result"]
    if prerequisite_result is not None:
        if not isinstance(prerequisite_result, FiniteSetResult):
            raise RunnerValidationError("prior-phase result is not reducer-owned")
        try:
            if validate_finite_set_result(prerequisite_result, expected_phase=required_phase) != value:
                raise RunnerValidationError("predecessor result does not match its reloadable evidence artifact")
        except ValueError as error:
            raise RunnerValidationError("prior-phase result is invalid") from error
    if value["result_mode"] != "positive" or not all(value["gates"].values()):
        raise RunnerValidationError("downstream phase requires every prior-phase gate to pass")


def admit_execution_session(*, phase: str, prerequisite_result: Any = None, predecessor_evidence: Any = None, repository: str | Path, contract: dict[str, Any], manifest: dict[str, Any], source_gate: dict[str, Any], freeze_receipt: dict[str, Any], closure_lock: dict[str, Any], schedule_path: str | Path, journal_path: str | Path, calibration_path: str | Path, client: Any, session_nonce: str | None = None) -> ExecutionSession:
    """Admit a fresh client-bound session; no detached calibration/client authority exists."""
    checked_manifest = validate_manifest(manifest, contract, repository=repository)
    if checked_manifest["study_phase"] != phase or freeze_receipt.get("study_phase") != phase:
        raise RunnerValidationError("execution phase does not match its manifest and freeze receipt")
    if phase != "development" and predecessor_evidence != checked_manifest.get("predecessor_evidence"):
        raise RunnerValidationError("execution predecessor evidence must exactly match the sealed manifest")
    _require_phase_prerequisite(phase, prerequisite_result, predecessor_evidence=predecessor_evidence)
    try:
        client_binding = _client_binding(client)
        validate_loopback_endpoint(client_binding["endpoint"], expected=contract["execution"]["endpoint"])
    except RuntimeValidationError as error:
        raise RunnerValidationError("client endpoint does not match the frozen loopback configuration") from error
    try:
        admit_clean_checkout(repository, freeze_receipt, closure_lock)
        validate_loaded_code_closure(repository, closure_lock)
    except AdmissionError as error:
        raise RunnerValidationError(f"clean pushed provenance admission failed: {error}") from error
    schedule = create_schedule(schedule_path, checked_manifest, contract, source_gate=source_gate, freeze_receipt=freeze_receipt, closure_lock=closure_lock)
    inventory = client.inventory(contract["execution"]["timeout_seconds"])
    validate_inventory(contract, inventory)
    nonce = session_nonce or secrets.token_hex(24)
    if not isinstance(nonce, str) or not nonce:
        raise RunnerValidationError("session nonce is invalid")
    journal = ExecutionJournal(journal_path, schedule)
    try:
        return ExecutionSession(client=client, contract=contract, manifest=checked_manifest, schedule=schedule, journal=journal, inventory=inventory, client_binding=client_binding, calibration_path=calibration_path, session_nonce=nonce)
    except BaseException:
        journal.close()
        raise
