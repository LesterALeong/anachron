"""Run exactly one already-sealed date-shift bundle; proposed artifacts are inadmissible."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from anachron.date_shift import (
    DateShiftValidationError,
    OllamaClient,
    TransportOutcome,
    _response_content,
    bytes_sha256,
    canonical_bytes,
    invalid_score,
    score_response,
)
from anachron.date_shift_bundle import (
    JournalV3,
    build_request,
    calibration_request,
    load_bundle,
    validate_journal_v3,
    verify_bundle_derivation,
    write_create_only,
)
from anachron.date_shift_provenance import admit_scaffold_repository


def _backend_evidence(model_id: str) -> dict:
    cli = shutil.which("ollama")
    if not cli:
        raise DateShiftValidationError("Ollama CLI is unavailable for backend capture")
    try:
        completed = subprocess.run(
            [cli, "ps"], check=True, capture_output=True, timeout=30
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise DateShiftValidationError("Ollama backend capture failed") from error
    raw = completed.stdout or completed.stderr
    if not raw or model_id.encode("utf-8") not in raw:
        raise DateShiftValidationError(
            "Ollama backend snapshot does not show the calibrated model"
        )
    return {
        "model_id": model_id,
        "ollama_ps_base64": base64.b64encode(raw).decode("ascii"),
        "ollama_ps_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
    }


def _terminal_calibration(
    journal: JournalV3, model_id: str, outcome: TransportOutcome, plan: dict
) -> None:
    status = outcome.status
    if status == "ok":
        try:
            content = _response_content(outcome, model_id)
            if json.loads(content) != {
                "answer": plan["calibration"]["expected_answer"],
                "citation_ids": [plan["calibration"]["citation_id"]],
            }:
                status = "invalid_response"
        except (DateShiftValidationError, json.JSONDecodeError):
            status = "invalid_response"
    journal.terminalize(
        "calibration_terminal",
        model_id=model_id,
        status=status,
        response_sha256="sha256:" + hashlib.sha256(outcome.body).hexdigest(),
        response_base64=base64.b64encode(outcome.body).decode("ascii"),
    )
    if status != "ok":
        raise DateShiftValidationError(f"calibration failed for {model_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one sealed date-shift bundle with no resume or retry path."
    )
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--bundle-dir", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    provenance = admit_scaffold_repository(args.repository)
    bundle = load_bundle(args.bundle_dir)
    verify_bundle_derivation(bundle, args.repository.resolve(), provenance)
    if bundle["runtime_preflight"]["capture_provenance"] != provenance:
        raise DateShiftValidationError(
            "bundle runtime preflight does not bind this checkout"
        )
    journal = JournalV3(args.run_dir / "journal.jsonl", bundle)
    journal.create()
    plan, contract = bundle["execution_plan"], bundle["execution_contract"]
    client = OllamaClient(plan["endpoint"])
    try:
        inventory = client.inventory(plan["timeout_seconds"])
        if any(
            inventory.get(model["id"]) != model["digest"]
            for model in contract["models"]
        ):
            raise DateShiftValidationError(
                "Ollama inventory does not match sealed model digests"
            )
    except Exception as error:
        journal.terminalize("admission_terminal", status="failed", detail=str(error))
        raise
    journal.terminalize("admission_terminal", status="ok")
    for model in contract["models"]:
        request = calibration_request(plan, model["id"])
        request_bytes = canonical_bytes(request)
        journal.append(
            "calibration_claim",
            model_id=model["id"],
            request_sha256=bytes_sha256(request_bytes),
            request_base64=base64.b64encode(request_bytes).decode("ascii"),
        )
        try:
            outcome = client.chat(request, plan["timeout_seconds"])
        except OSError as error:
            outcome = TransportOutcome("client_exception", b"", str(error))
        _terminal_calibration(journal, model["id"], outcome, plan)
        journal.append("loaded_backend_evidence", **_backend_evidence(model["id"]))
    journal.append("phase_transition", to="science")
    for trajectory in bundle["schedule"]["trajectories"]:
        item = bundle["audited_items"]["items"][trajectory["item_index"]]
        request = build_request(plan, item, trajectory)
        request_bytes = canonical_bytes(request)
        journal.append(
            "dispatch_claim",
            schedule_index=trajectory["schedule_index"],
            request_sha256=bytes_sha256(request_bytes),
            request_base64=base64.b64encode(request_bytes).decode("ascii"),
        )
        try:
            outcome = client.chat(request, plan["timeout_seconds"])
            if outcome.status == "ok":
                score = score_response(
                    _response_content(outcome, trajectory["model_id"]), item
                )
                status = "ok"
            else:
                score, status = invalid_score(), outcome.status
        except KeyboardInterrupt:
            raise
        except (DateShiftValidationError, OSError) as error:
            outcome, score, status = (
                TransportOutcome("client_exception", b"", str(error)),
                invalid_score(),
                "client_exception",
            )
        journal.terminalize(
            "terminal_outcome",
            schedule_index=trajectory["schedule_index"],
            status=status,
            score=score,
            response_sha256="sha256:" + hashlib.sha256(outcome.body).hexdigest(),
            response_base64=base64.b64encode(outcome.body).decode("ascii"),
        )
    journal.append("run_terminal", status="science_complete")
    validate_journal_v3(args.run_dir / "journal.jsonl", bundle)
    write_create_only(
        args.run_dir / "run_receipt.json",
        {
            "schema_version": "date-shift-run-receipt-v3",
            "bundle_manifest_sha256": bytes_sha256(bundle["raw_manifest"]),
            "journal_sha256": "sha256:"
            + hashlib.sha256((args.run_dir / "journal.jsonl").read_bytes()).hexdigest(),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
