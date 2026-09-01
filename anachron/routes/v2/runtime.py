"""Canonical Routes v2 transport envelopes and session-calibration receipts."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from anachron.routes.v2.manifest import canonical_json_sha256
from anachron.routes.v2.retrieval import scan_prompt_packet
from anachron.routes.v2.scoring import score_response


class RuntimeValidationError(ValueError):
    """Raised when a runtime response or calibration receipt is inadmissible."""


_NO_RESPONSE = "transport_failure_no_response_object"
_RESPONSE_STATUSES = {
    "ok", "http_error", "read_error", "returned_error", "timeout_after_dispatch",
    "malformed_response", "invalid_output",
}


@dataclass(frozen=True)
class TransportResult:
    """One typed chat transport result with a durable response-object boundary."""

    status: str
    response_bytes: bytes
    response_object_exists: bool
    error_kind: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _RESPONSE_STATUSES | {_NO_RESPONSE}:
            raise RuntimeValidationError("transport status is not declared")
        if not isinstance(self.response_bytes, bytes) or not isinstance(self.response_object_exists, bool):
            raise RuntimeValidationError("transport result has invalid response fields")
        if self.status == _NO_RESPONSE and self.response_object_exists:
            raise RuntimeValidationError("no-response transport failure cannot have a response object")
        if self.status in _RESPONSE_STATUSES and not self.response_object_exists:
            raise RuntimeValidationError("response-bearing terminal status requires response-object evidence")


class OllamaHttpClient:
    """Stdlib transport that records the response-object boundary at response headers."""

    def __init__(self, endpoint: str = "http://127.0.0.1:11434") -> None:
        self.endpoint = validate_loopback_endpoint(endpoint)
        self.configuration = {"api": "ollama-chat-v2", "stream": False}

    def _request(self, path: str, payload: dict[str, Any] | None, timeout_seconds: int) -> TransportResult:
        data = None if payload is None else canonical_json_bytes(payload)
        request = Request(self.endpoint + path, data=data, headers={"Content-Type": "application/json"} if data is not None else {}, method="POST" if data is not None else "GET")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                try:
                    body = response.read()
                except (OSError, TimeoutError) as error:
                    return TransportResult("read_error", b"", True, type(error).__name__)
                return TransportResult("ok" if response.status == 200 else "http_error", body, True, None if response.status == 200 else f"http_{response.status}")
        except HTTPError as error:
            try:
                body = error.read()
            except (OSError, TimeoutError):
                body = b""
            return TransportResult("http_error", body, True, f"http_{error.code}")
        except (URLError, OSError, TimeoutError) as error:
            return TransportResult(_NO_RESPONSE, b"", False, type(error).__name__)

    def inventory(self, timeout_seconds: int) -> dict[str, str]:
        result = self._request("/api/tags", None, timeout_seconds)
        if result.status != "ok":
            raise RuntimeValidationError(f"Ollama model inventory failed: {result.status}")
        try:
            models = json.loads(result.response_bytes.decode("utf-8"))["models"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise RuntimeValidationError("Ollama model inventory response is malformed") from error
        if not isinstance(models, list):
            raise RuntimeValidationError("Ollama model inventory models must be a list")
        inventory: dict[str, str] = {}
        for entry in models:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not isinstance(entry.get("digest"), str) or entry["name"] in inventory:
                raise RuntimeValidationError("Ollama model inventory contains an invalid identity")
            inventory[entry["name"]] = entry["digest"]
        return inventory

    def chat(self, request: dict[str, Any], timeout_seconds: int) -> TransportResult:
        return self._request("/api/chat", request, timeout_seconds)


def source_code_sha256() -> str:
    """Hash every runtime behavior file bound by a session calibration."""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in (
        "admission.py", "schema.py", "manifest.py", "retrieval.py", "scoring.py",
        "runtime.py", "runner.py",
    ):
        digest.update(name.encode("utf-8") + b"\0" + (root / name).read_bytes() + b"\0")
    return "sha256:" + digest.hexdigest()


def validate_loopback_endpoint(endpoint: Any, *, expected: str | None = None) -> str:
    """Admit only the frozen local Ollama endpoint, never remote HTTP(S)."""
    if not isinstance(endpoint, str) or not endpoint:
        raise RuntimeValidationError("Ollama endpoint must be non-empty text")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise RuntimeValidationError("Ollama endpoint must be a plain local HTTP origin")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeValidationError("Ollama endpoint must be loopback-only")
    normalized = endpoint.rstrip("/")
    if expected is not None and normalized != expected.rstrip("/"):
        raise RuntimeValidationError("Ollama endpoint does not match the frozen contract")
    return normalized


def canonical_json_bytes(value: dict[str, Any]) -> bytes:
    """Encode one request or packet with stable bytes, not just a stable hash."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def bytes_receipt(value: bytes) -> dict[str, Any]:
    """Preserve all bytes needed for an independently replayable record."""
    return {
        "base64": base64.b64encode(value).decode("ascii"),
        "length": len(value),
        "sha256": "sha256:" + hashlib.sha256(value).hexdigest(),
    }


def validate_bytes_receipt(value: Any) -> bytes:
    """Decode an exact byte receipt and reject plausible-looking hash drift."""
    if not isinstance(value, dict) or set(value) != {"base64", "length", "sha256"}:
        raise RuntimeValidationError("byte receipt schema is invalid")
    if not isinstance(value["base64"], str) or not isinstance(value["length"], int) or value["length"] < 0 or not isinstance(value["sha256"], str):
        raise RuntimeValidationError("byte receipt values are invalid")
    try:
        decoded = base64.b64decode(value["base64"], validate=True)
    except ValueError as error:
        raise RuntimeValidationError("byte receipt base64 is invalid") from error
    if len(decoded) != value["length"] or bytes_receipt(decoded)["sha256"] != value["sha256"]:
        raise RuntimeValidationError("byte receipt length or hash drifted")
    return decoded


def build_request(packet: dict[str, Any], contract: dict[str, Any], *, model_id: str, seed: int) -> dict[str, Any]:
    """Create the exact opaque v2 request with an explicit abstention instruction."""
    scan_prompt_packet(packet)
    if model_id not in {model["id"] for model in contract["models"]} or seed not in contract["execution"]["seeds"]:
        raise RuntimeValidationError("request model or seed is outside the frozen development design")
    user_payload = {
        "question": packet["question"], "document": packet["document"],
        "temporal_cutoff_date": packet["temporal_cutoff_date"],
        "instruction": "Answer from the document only. If the document is insufficient or conflicts with the temporal cutoff, return ABSTAIN exactly.",
        "abstention_token": "ABSTAIN",
    }
    return {
        "model": model_id, "stream": False, "think": False,
        "format": {"type": "object", "properties": {"answer": {"type": "string"}, "citation_id": {"type": "string"}}, "required": ["answer", "citation_id"], "additionalProperties": False},
        "options": {"temperature": contract["execution"]["temperature"], "num_predict": contract["execution"]["num_predict"], "seed": seed},
        "messages": [{"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))}],
    }


def calibration_packet() -> dict[str, Any]:
    """Return the model-visible synthetic packet used by every fresh session."""
    packet = {
        "question": "What exact token is in the document?",
        "document": {"citation_id": "CAL", "content": "CALIBRATION", "presented_document_date": "2000-01-01"},
        "temporal_cutoff_date": "2000-12-31", "abstention_token": "ABSTAIN",
    }
    scan_prompt_packet(packet)
    return packet


def request_sha256(request: dict[str, Any]) -> str:
    return canonical_json_sha256(request)


def classify_response(result: TransportResult, *, requested_model: str, expected_answer: str) -> dict[str, Any]:
    """Classify a response without masking transport or response-object failures."""
    receipt = bytes_receipt(result.response_bytes)
    if result.status != "ok":
        return {"status": result.status, "response": receipt, "envelope_valid": False, "score": None}
    try:
        envelope = json.loads(result.response_bytes.decode("utf-8"))
        message = envelope["message"]
        if (
            envelope["model"] != requested_model or envelope["done"] is not True
            or message["role"] != "assistant" or not isinstance(message["content"], str)
            or not message["content"] or "thinking" in message
        ):
            raise ValueError
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return {"status": "malformed_response", "response": receipt, "envelope_valid": False, "score": None}
    score = score_response(message["content"], expected_answer=expected_answer)
    status = "invalid_output" if score["answer_label"] == "invalid_output" else "ok"
    return {"status": status, "response": receipt, "envelope_valid": True, "score": score}


def inventory_sha256(inventory: dict[str, str]) -> str:
    """Validate and hash the complete frozen model inventory projection."""
    if not isinstance(inventory, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in inventory.items()):
        raise RuntimeValidationError("model inventory is invalid")
    return canonical_json_sha256(inventory)


def validate_inventory(contract: dict[str, Any], inventory: dict[str, str]) -> dict[str, str]:
    """Require exact declared model digests before a session can calibrate."""
    inventory_sha256(inventory)
    expected = {model["id"]: model["digest"] for model in contract["models"]}
    if inventory != expected:
        raise RuntimeValidationError("model inventory does not exactly match frozen identities")
    return inventory


def session_calibration_receipt(
    contract: dict[str, Any], *, inventory: dict[str, str], client_binding: dict[str, Any],
    closure_sha256: str, session_nonce: str, model_id: str, request: dict[str, Any], result: TransportResult,
) -> dict[str, Any]:
    """Create one client- and session-bound receipt after a valid synthetic reply."""
    models = {model["id"]: model for model in contract["models"]}
    model = models.get(model_id)
    if model is None:
        raise RuntimeValidationError("calibration model is outside the frozen inventory")
    validate_inventory(contract, inventory)
    if not isinstance(client_binding, dict) or set(client_binding) != {"endpoint", "configuration"} or not isinstance(client_binding.get("configuration"), dict):
        raise RuntimeValidationError("client binding requires an exact endpoint")
    validate_loopback_endpoint(client_binding["endpoint"], expected=contract["execution"]["endpoint"])
    if not isinstance(closure_sha256, str) or not isinstance(session_nonce, str) or not session_nonce:
        raise RuntimeValidationError("calibration closure or session binding is invalid")
    classified = classify_response(result, requested_model=model["id"], expected_answer=contract["calibration"]["expected_answer"])
    if classified["status"] != "ok" or classified["score"] != {"answer_label": "post_only", "post_only": 1}:
        raise RuntimeValidationError("calibration did not produce the required deterministic score")
    receipt = {
        "schema_version": "routes-v2-session-calibration-receipt", "contract_sha256": canonical_json_sha256(contract),
        "code_sha256": source_code_sha256(), "closure_sha256": closure_sha256,
        "inventory": inventory, "inventory_sha256": inventory_sha256(inventory), "client_binding": client_binding,
        "client_binding_sha256": canonical_json_sha256(client_binding), "session_nonce": session_nonce,
        "model_id": model["id"], "model_digest": model["digest"], "request": bytes_receipt(canonical_json_bytes(request)),
        "response": classified["response"], "expected_score": classified["score"],
    }
    receipt["receipt_sha256"] = canonical_json_sha256(receipt)
    return receipt


def validate_session_calibration(
    receipt: Any, contract: dict[str, Any], *, inventory: dict[str, str], client_binding: dict[str, Any],
    closure_sha256: str, session_nonce: str, model_id: str,
) -> dict[str, Any]:
    """Reject a calibration from another client, endpoint, closure, or session."""
    fields = {
        "schema_version", "contract_sha256", "code_sha256", "closure_sha256", "inventory", "inventory_sha256",
        "client_binding", "client_binding_sha256", "session_nonce", "model_id", "model_digest", "request", "response", "expected_score", "receipt_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != fields or receipt["schema_version"] != "routes-v2-session-calibration-receipt":
        raise RuntimeValidationError("session calibration receipt schema is invalid")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if receipt["receipt_sha256"] != canonical_json_sha256(unsigned):
        raise RuntimeValidationError("session calibration receipt hash is invalid")
    validate_inventory(contract, inventory)
    models = {model["id"]: model for model in contract["models"]}
    model = models.get(model_id)
    if model is None:
        raise RuntimeValidationError("calibration model is outside the frozen inventory")
    validate_loopback_endpoint(client_binding.get("endpoint"), expected=contract["execution"]["endpoint"])
    expected_request = build_request(calibration_packet(), contract, model_id=model_id, seed=contract["execution"]["seeds"][0])
    if (
        receipt["contract_sha256"] != canonical_json_sha256(contract) or receipt["code_sha256"] != source_code_sha256()
        or receipt["closure_sha256"] != closure_sha256 or receipt["inventory"] != inventory or receipt["inventory_sha256"] != inventory_sha256(inventory)
        or receipt["client_binding"] != client_binding or receipt["client_binding_sha256"] != canonical_json_sha256(client_binding)
        or receipt["session_nonce"] != session_nonce or receipt["model_id"] != model_id or receipt["model_digest"] != model["digest"]
        or validate_bytes_receipt(receipt["request"]) != canonical_json_bytes(expected_request)
        or receipt["expected_score"] != {"answer_label": "post_only", "post_only": 1}
    ):
        raise RuntimeValidationError("session calibration receipt binding drifted")
    response = TransportResult("ok", validate_bytes_receipt(receipt["response"]), True)
    classified = classify_response(response, requested_model=model_id, expected_answer=contract["calibration"]["expected_answer"])
    if classified["status"] != "ok" or classified["score"] != receipt["expected_score"]:
        raise RuntimeValidationError("session calibration response is invalid")
    return receipt
