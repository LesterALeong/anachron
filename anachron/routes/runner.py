"""Append-only, fail-closed executor for frozen Routes v1 trajectories."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anachron.routes.manifest import canonical_json_sha256, validate_manifest
from anachron.routes.retrieval import retrieve, validate_retrieval_result
from anachron.routes.runtime import (
    OllamaHttpClient,
    OllamaRuntimeError,
    TimeoutAfterDispatch,
    TransportFailureBeforeResponse,
    build_chat_request,
    classify_chat_response,
    request_sha256,
    verify_declared_model_inventory,
)
from anachron.routes.schema import ContractValidationError, load_contract
from anachron.routes.scoring import score_response


class RunnerValidationError(ValueError):
    """Raised when a runner invocation could change the frozen experiment."""


_TERMINAL_STATUSES = frozenset(
    {"ok", "timeout_after_dispatch", "malformed_response", "returned_error", "invalid_output"}
)
_ALL_STATUSES = _TERMINAL_STATUSES | {"transport_failure_before_response"}
_CALIBRATION_PAIR = {
    "item_id": "routes-v1-calibration",
    "question": "Return the exact token CALIBRATION.",
    "cutoff_year": 2000,
    "pre_answer_aliases": ["CALIBRATION"],
    "post_answer_aliases": ["CALIBRATION_POST"],
}


@dataclass(frozen=True)
class ScheduledTrajectory:
    """One deterministic model invocation, independent of retry attempt."""

    study_phase: str
    item_id: str
    topic: str
    cutoff_year: int
    model_id: str
    model_digest: str
    seed: int
    condition: str
    trajectory_id: str


def utc_now() -> str:
    """Return canonical UTC timestamps for append-only receipts."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def source_code_sha256() -> str:
    """Hash the exact Routes modules that define routing and runtime behavior."""
    root = Path(__file__).resolve().parent
    filenames = ("schema.py", "manifest.py", "retrieval.py", "scoring.py", "runtime.py", "runner.py")
    digest = hashlib.sha256()
    for filename in filenames:
        payload = (root / filename).read_bytes()
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _trajectory_id(
    phase: str, item_id: str, model_digest: str, seed: int, condition: str
) -> str:
    identity = f"{phase}\0{item_id}\0{model_digest}\0{seed}\0{condition}"
    return "routes-v1-trajectory:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def build_schedule(
    manifest: dict[str, Any], contract: dict[str, Any], phase: str
) -> list[ScheduledTrajectory]:
    """Expand the frozen phase schedule in item, model, condition, seed order."""
    if phase not in {"pilot", "full"}:
        raise RunnerValidationError("phase must be pilot or full")
    models = {entry["id"]: entry["digest"] for entry in contract["models"]}
    phase_pairs = [pair for pair in manifest["pairs"] if pair["study_phase"] == phase]
    if not phase_pairs:
        raise RunnerValidationError("sealed manifest contains no accepted pairs for phase")
    scheduled: list[ScheduledTrajectory] = []
    for pair in sorted(phase_pairs, key=lambda item: item["item_id"]):
        for model_id in contract["sampling"][f"{phase}_models"]:
            for condition in contract["conditions"]:
                for seed in contract["execution"]["seeds"]:
                    scheduled.append(
                        ScheduledTrajectory(
                            study_phase=phase,
                            item_id=pair["item_id"],
                            topic=pair["topic"],
                            cutoff_year=pair["cutoff_year"],
                            model_id=model_id,
                            model_digest=models[model_id],
                            seed=seed,
                            condition=condition,
                            trajectory_id=_trajectory_id(
                                phase, pair["item_id"], models[model_id], seed, condition
                            ),
                        )
                    )
    if len({item.trajectory_id for item in scheduled}) != len(scheduled):
        raise RunnerValidationError("schedule contains duplicate trajectory identities")
    return scheduled


def execution_identity(
    contract: dict[str, Any], manifest: dict[str, Any], sampling_frame: dict[str, Any]
) -> dict[str, str]:
    """Bind every receipt to the exact frozen source, contract, and code inputs."""
    return {
        "contract_sha256": canonical_json_sha256(contract),
        "manifest_sha256": canonical_json_sha256(manifest),
        "sampling_frame_sha256": canonical_json_sha256(sampling_frame),
        "code_sha256": source_code_sha256(),
    }


def preflight_plan(
    manifest: dict[str, Any], contract: dict[str, Any], sampling_frame: dict[str, Any], phase: str
) -> tuple[dict[str, str], list[ScheduledTrajectory]]:
    """Validate sources and construct a schedule without reading or calling Ollama."""
    try:
        checked_manifest = validate_manifest(manifest, contract, sampling_frame)
    except Exception as error:
        raise RunnerValidationError(f"sealed manifest is not runnable: {error}") from error
    return execution_identity(contract, checked_manifest, sampling_frame), build_schedule(
        checked_manifest, contract, phase
    )


def run_calibration(
    contract: dict[str, Any],
    phase: str,
    *,
    client: OllamaHttpClient | Any | None = None,
    clock: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    """Exercise each phase-declared model on one synthetic non-scientific request."""
    if phase not in {"pilot", "full"}:
        raise RunnerValidationError("calibration phase must be pilot or full")
    runtime = client if client is not None else OllamaHttpClient()
    verify_declared_model_inventory(
        contract, runtime.inventory(contract["execution"]["request_timeout_seconds"])
    )
    created_at = clock()
    _canonical_utc_timestamp(created_at, "calibration created_at")
    retrieval = {
        "item_id": _CALIBRATION_PAIR["item_id"],
        "condition": "no_tool",
        "evidence": [],
        "trace_event": {
            "event_type": "routes_retrieval",
            "created_at": created_at,
            "item_id": _CALIBRATION_PAIR["item_id"],
            "condition": "no_tool",
            "evidence_ids": [],
        },
    }
    declared_digests = {entry["id"]: entry["digest"] for entry in contract["models"]}
    models: list[dict[str, Any]] = []
    for model_id in contract["sampling"][f"{phase}_models"]:
        request = build_chat_request(
            _CALIBRATION_PAIR,
            retrieval,
            contract,
            model_id=model_id,
            seed=contract["execution"]["seeds"][0],
        )
        transport_result = runtime.chat(request, contract["execution"]["request_timeout_seconds"])
        if transport_result.status != "ok":
            raise OllamaRuntimeError("calibration request did not return an Ollama success envelope")
        classified = classify_chat_response(
            transport_result.response_bytes,
            _CALIBRATION_PAIR,
            retrieval,
            requested_model_id=model_id,
        )
        if classified.status != "ok" or classified.model_response_text is None:
            raise OllamaRuntimeError("calibration response failed the frozen JSON and scoring contract")
        score = score_response(_CALIBRATION_PAIR, retrieval, classified.model_response_text)
        if score["answer_label"] != "pre_only" or score["citation_ids"]:
            raise OllamaRuntimeError("calibration response did not produce the deterministic synthetic score")
        models.append(
            {
                "model_id": model_id,
                "model_digest": declared_digests[model_id],
                "request_sha256": request_sha256(request),
                "response_sha256": "sha256:"
                + hashlib.sha256(transport_result.response_bytes).hexdigest(),
                "answer_label": score["answer_label"],
                "citation_ids": score["citation_ids"],
            }
        )
    return {
        "schema_version": "routes-v1-calibration-receipt",
        "phase": phase,
        "created_at": created_at,
        "contract_sha256": canonical_json_sha256(contract),
        "code_sha256": source_code_sha256(),
        "models": models,
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if not path.is_file():
        raise RunnerValidationError("ledger path must be a regular file")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line:
            raise RunnerValidationError(f"ledger line {line_number} is blank")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise RunnerValidationError(f"ledger line {line_number} is not JSON") from error
        if not isinstance(record, dict):
            raise RunnerValidationError(f"ledger line {line_number} is not an object")
        records.append(record)
    return records


def _canonical_utc_timestamp(value: Any, path: str) -> datetime:
    if not isinstance(value, str):
        raise RunnerValidationError(f"{path} must be a canonical UTC timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise RunnerValidationError(f"{path} must be a canonical UTC timestamp") from error
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise RunnerValidationError(f"{path} must be a canonical UTC timestamp")
    return parsed


def _response_bytes(response: Any) -> bytes:
    if not isinstance(response, dict) or set(response) != {
        "sha256", "body_base64", "received_bytes"
    }:
        raise RunnerValidationError("ledger response receipt is invalid")
    body_base64 = response["body_base64"]
    if body_base64 is None:
        if (
            response["sha256"] is not None
            or isinstance(response["received_bytes"], bool)
            or response["received_bytes"] != 0
        ):
            raise RunnerValidationError("empty response receipt is inconsistent")
        return b""
    if (
        not isinstance(body_base64, str)
        or isinstance(response["received_bytes"], bool)
        or not isinstance(response["received_bytes"], int)
    ):
        raise RunnerValidationError("ledger response receipt has invalid types")
    try:
        response_bytes = base64.b64decode(body_base64.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as error:
        raise RunnerValidationError("ledger response body is not valid base64") from error
    expected_digest = "sha256:" + hashlib.sha256(response_bytes).hexdigest()
    if (
        response["sha256"] != expected_digest
        or response["received_bytes"] != len(response_bytes)
    ):
        raise RunnerValidationError("ledger response bytes do not match their receipt")
    return response_bytes


def _validate_record_payload(
    record: dict[str, Any],
    trajectory: ScheduledTrajectory,
    pair: dict[str, Any],
    manifest: dict[str, Any],
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
) -> None:
    started_at = _canonical_utc_timestamp(record["started_at"], "ledger started_at")
    completed_at = _canonical_utc_timestamp(record["completed_at"], "ledger completed_at")
    if completed_at < started_at:
        raise RunnerValidationError("ledger completed_at precedes started_at")
    retrieval = record["retrieval"]
    if not isinstance(retrieval, dict) or set(retrieval) != {"sha256", "result"}:
        raise RunnerValidationError("ledger retrieval receipt is invalid")
    try:
        expected_retrieval = retrieve(
            manifest,
            contract,
            sampling_frame,
            item_id=trajectory.item_id,
            condition=trajectory.condition,
            retrieved_at=record["started_at"],
        )
    except Exception as error:
        raise RunnerValidationError(
            f"ledger retrieval receipt cannot be reconstructed: {error}"
        ) from error
    try:
        validate_retrieval_result(retrieval["result"], pair)
    except Exception as error:
        raise RunnerValidationError(f"ledger retrieval receipt is not route-valid: {error}") from error
    if (
        retrieval["result"] != expected_retrieval
        or retrieval["sha256"] != canonical_json_sha256(retrieval["result"])
    ):
        raise RunnerValidationError("ledger retrieval receipt does not match the exact route")
    request = record["request"]
    if not isinstance(request, dict) or set(request) != {"sha256", "body"}:
        raise RunnerValidationError("ledger request receipt is invalid")
    expected_request = build_chat_request(
        pair,
        expected_retrieval,
        contract,
        model_id=trajectory.model_id,
        seed=trajectory.seed,
    )
    if request["body"] != expected_request or request["sha256"] != request_sha256(request["body"]):
        raise RunnerValidationError("ledger request receipt does not match the exact trajectory request")
    response_bytes = _response_bytes(record["response"])
    error = record["error"]
    if not isinstance(error, dict) or set(error) != {"kind", "message_sha256"}:
        raise RunnerValidationError("ledger error receipt is invalid")
    error_kind = error["kind"]
    if error_kind is None:
        if error["message_sha256"] is not None:
            raise RunnerValidationError("ledger empty error has a message digest")
    elif (
        not isinstance(error_kind, str)
        or error["message_sha256"]
        != "sha256:" + hashlib.sha256(error_kind.encode("utf-8")).hexdigest()
    ):
        raise RunnerValidationError("ledger error digest does not match error kind")
    status = record["status"]
    if status in {"ok", "invalid_output", "malformed_response"}:
        if not response_bytes:
            raise RunnerValidationError("a model response status must retain response bytes")
        classified = classify_chat_response(
            response_bytes,
            pair,
            expected_retrieval,
            requested_model_id=trajectory.model_id,
        )
        if classified.status != status or classified.error_kind != error_kind:
            raise RunnerValidationError("ledger response classification does not match response bytes")
    elif status == "returned_error":
        if not isinstance(error_kind, str) or not error_kind.startswith("http_"):
            raise RunnerValidationError("returned error receipt must identify its HTTP status")
    elif status in {"timeout_after_dispatch", "transport_failure_before_response"}:
        if response_bytes or error_kind != status:
            raise RunnerValidationError("transport failure receipt is inconsistent")


def _validate_existing_records(
    records: Iterable[dict[str, Any]],
    identity: dict[str, str],
    schedule: Iterable[ScheduledTrajectory],
    manifest: dict[str, Any],
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    expected = {item.trajectory_id: item for item in schedule}
    pairs = {pair["item_id"]: pair for pair in manifest["pairs"]}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        required = {
            "schema_version", "record_type", "run_id", "trajectory_id", "attempt", "study_phase",
            "item_id", "topic", "cutoff_year", "model_id", "model_digest", "seed", "condition",
            "started_at", "completed_at", "status", "contract_sha256", "manifest_sha256",
            "sampling_frame_sha256", "code_sha256", "request", "retrieval", "response", "error",
        }
        if set(record) != required or record["schema_version"] != "routes-v1-runner-record" or record["record_type"] != "trajectory_attempt":
            raise RunnerValidationError("ledger record schema is invalid")
        trajectory_id = record["trajectory_id"]
        trajectory = expected.get(trajectory_id)
        if trajectory is None:
            raise RunnerValidationError("ledger contains a trajectory outside the current schedule")
        if any(record[key] != value for key, value in identity.items()):
            raise RunnerValidationError("ledger identity drift prevents resume")
        expected_fields = {
            "study_phase": trajectory.study_phase,
            "item_id": trajectory.item_id,
            "topic": trajectory.topic,
            "cutoff_year": trajectory.cutoff_year,
            "model_id": trajectory.model_id,
            "model_digest": trajectory.model_digest,
            "seed": trajectory.seed,
            "condition": trajectory.condition,
        }
        if any(record[key] != value for key, value in expected_fields.items()):
            raise RunnerValidationError("ledger trajectory identity does not match its schedule")
        if record["run_id"] != f"routes-v1:{trajectory.study_phase}":
            raise RunnerValidationError("ledger run_id is invalid")
        if record["attempt"] not in {1, 2} or record["status"] not in _ALL_STATUSES:
            raise RunnerValidationError("ledger attempt or status is invalid")
        _validate_record_payload(
            record,
            trajectory,
            pairs[trajectory.item_id],
            manifest,
            contract,
            sampling_frame,
        )
        grouped.setdefault(trajectory_id, []).append(record)
    for records_for_trajectory in grouped.values():
        records_for_trajectory.sort(key=lambda item: item["attempt"])
        if len(records_for_trajectory) > 2 or records_for_trajectory[0]["attempt"] != 1:
            raise RunnerValidationError("ledger has duplicate or missing first attempts")
        if len(records_for_trajectory) == 2:
            if records_for_trajectory[0]["status"] != "transport_failure_before_response" or records_for_trajectory[1]["attempt"] != 2:
                raise RunnerValidationError("ledger retry does not follow the sole eligible transport failure")
        elif records_for_trajectory[0]["status"] == "transport_failure_before_response":
            continue
    return grouped


def _append_record(path: Path, record: dict[str, Any]) -> None:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _record(
    trajectory: ScheduledTrajectory,
    attempt: int,
    identity: dict[str, str],
    request: dict[str, Any],
    retrieval_result: dict[str, Any],
    started_at: str,
    completed_at: str,
    status: str,
    response_bytes: bytes,
    error_kind: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": "routes-v1-runner-record",
        "record_type": "trajectory_attempt",
        "run_id": f"routes-v1:{trajectory.study_phase}",
        "trajectory_id": trajectory.trajectory_id,
        "attempt": attempt,
        "study_phase": trajectory.study_phase,
        "item_id": trajectory.item_id,
        "topic": trajectory.topic,
        "cutoff_year": trajectory.cutoff_year,
        "model_id": trajectory.model_id,
        "model_digest": trajectory.model_digest,
        "seed": trajectory.seed,
        "condition": trajectory.condition,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": status,
        **identity,
        "request": {"sha256": request_sha256(request), "body": request},
        "retrieval": {
            "sha256": canonical_json_sha256(retrieval_result),
            "result": retrieval_result,
        },
        "response": {
            "sha256": "sha256:" + hashlib.sha256(response_bytes).hexdigest() if response_bytes else None,
            "body_base64": base64.b64encode(response_bytes).decode("ascii") if response_bytes else None,
            "received_bytes": len(response_bytes),
        },
        "error": {
            "kind": error_kind,
            "message_sha256": None if error_kind is None else "sha256:" + hashlib.sha256(error_kind.encode("utf-8")).hexdigest(),
        },
    }


def _next_attempt(records: list[dict[str, Any]]) -> int | None:
    if not records:
        return 1
    if len(records) == 1 and records[0]["status"] == "transport_failure_before_response":
        return 2
    return None


def execute_phase(
    manifest: dict[str, Any],
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
    phase: str,
    ledger_path: str | Path,
    *,
    client: OllamaHttpClient | Any | None = None,
    clock: Callable[[], str] = utc_now,
    retriever: Callable[..., dict[str, Any]] = retrieve,
) -> list[dict[str, Any]]:
    """Run remaining serial trajectories, retaining every attempted result in JSONL."""
    identity, schedule = preflight_plan(manifest, contract, sampling_frame, phase)
    path = Path(ledger_path)
    existing = _validate_existing_records(
        _load_jsonl(path), identity, schedule, manifest, contract, sampling_frame
    )
    runtime = client if client is not None else OllamaHttpClient()
    verify_declared_model_inventory(
        contract, runtime.inventory(contract["execution"]["request_timeout_seconds"])
    )
    records_written: list[dict[str, Any]] = []
    pairs = {pair["item_id"]: pair for pair in manifest["pairs"]}
    for trajectory in schedule:
        attempt = _next_attempt(existing.get(trajectory.trajectory_id, []))
        while attempt is not None:
            pair = pairs[trajectory.item_id]
            started_at = clock()
            try:
                retrieval_result = retriever(
                    manifest,
                    contract,
                    sampling_frame,
                    item_id=trajectory.item_id,
                    condition=trajectory.condition,
                    retrieved_at=started_at,
                )
                validate_retrieval_result(retrieval_result, pair)
                request = build_chat_request(
                    pair,
                    retrieval_result,
                    contract,
                    model_id=trajectory.model_id,
                    seed=trajectory.seed,
                )
            except Exception as error:
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                raise RunnerValidationError(
                    f"trajectory routing or prompt binding failed: {error}"
                ) from error
            status = "transport_failure_before_response"
            response_bytes = b""
            error_kind: str | None = None
            try:
                transport_result = runtime.chat(
                    request, contract["execution"]["request_timeout_seconds"]
                )
                if transport_result.status == "returned_error":
                    status = "returned_error"
                    response_bytes = transport_result.response_bytes
                    error_kind = transport_result.error_kind
                elif transport_result.status != "ok":
                    raise OllamaRuntimeError("Ollama chat returned an unknown transport status")
                else:
                    classified = classify_chat_response(
                    transport_result.response_bytes,
                    pair,
                    retrieval_result,
                    requested_model_id=trajectory.model_id,
                    )
                    status = classified.status
                    response_bytes = classified.response_bytes
                    error_kind = classified.error_kind
            except TransportFailureBeforeResponse:
                status = "transport_failure_before_response"
                error_kind = "transport_failure_before_response"
            except TimeoutAfterDispatch:
                status = "timeout_after_dispatch"
                error_kind = "timeout_after_dispatch"
            completed_at = clock()
            record = _record(
                trajectory,
                attempt,
                identity,
                request,
                retrieval_result,
                started_at,
                completed_at,
                status,
                response_bytes,
                error_kind,
            )
            _append_record(path, record)
            existing.setdefault(trajectory.trajectory_id, []).append(record)
            records_written.append(record)
            attempt = 2 if status == "transport_failure_before_response" and attempt == 1 else None
    return records_written


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RunnerValidationError(f"unable to load JSON: {error}") from error
    if not isinstance(value, dict):
        raise RunnerValidationError("JSON document must be an object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or dry-run frozen Routes v1 trajectories")
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--sampling-frame", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--phase", required=True, choices=("pilot", "full"))
    parser.add_argument("--ledger", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--calibration", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Provide a no-network dry-run and an explicit local execution entrypoint."""
    args = _parser().parse_args(argv)
    try:
        contract = load_contract(args.contract)
        if args.calibration:
            print(json.dumps(run_calibration(contract, args.phase), sort_keys=True))
            return 0
        if args.sampling_frame is None or args.manifest is None or args.ledger is None:
            raise RunnerValidationError(
                "sampling-frame, manifest, and ledger are required outside calibration"
            )
        sampling_frame = _load_json(args.sampling_frame)
        manifest = _load_json(args.manifest)
        identity, schedule = preflight_plan(manifest, contract, sampling_frame, args.phase)
        if args.dry_run:
            print(json.dumps({"identity": identity, "scheduled_trajectories": len(schedule)}, sort_keys=True))
            return 0
        execute_phase(manifest, contract, sampling_frame, args.phase, args.ledger)
    except (ContractValidationError, RunnerValidationError, OllamaRuntimeError) as error:
        raise SystemExit(f"routes runner failed: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
