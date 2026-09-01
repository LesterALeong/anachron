"""Strict local Ollama request construction and response classification."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from anachron.routes.manifest import canonical_json_sha256
from anachron.routes.retrieval import (
    RetrievalValidationError,
    validate_retrieval_result,
)
from anachron.routes.scoring import ScoringValidationError, score_response


class OllamaRuntimeError(RuntimeError):
    """Raised when the local Ollama service violates the Routes runtime contract."""


class TransportFailureBeforeResponse(OllamaRuntimeError):
    """Raised only when a request failed before any response bytes arrived."""


class TimeoutAfterDispatch(OllamaRuntimeError):
    """Raised when a dispatched request times out without a complete response."""


@dataclass(frozen=True)
class ChatResult:
    """The classified outcome of one already-dispatched local chat request."""

    status: str
    response_bytes: bytes
    model_response_text: str | None
    error_kind: str | None


_SYSTEM_PROMPT = (
    "Return exactly one JSON object with exactly these fields: answer and citation_ids. "
    "answer must contain only the shortest answer value, with no sentence or explanation. "
    "citation_ids must be an array. "
    "Use a supplied citation id only when the supplied evidence supports the answer. "
    "Do not add prose, markdown, or fields."
)
_RESPONSE_FORMAT = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "citation_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "citation_ids"],
    "additionalProperties": False,
}


def build_chat_request(
    pair: dict[str, Any],
    retrieval_result: dict[str, Any],
    contract: dict[str, Any],
    *,
    model_id: str,
    seed: int,
) -> dict[str, Any]:
    """Build the sole allowed Ollama /api/chat payload for one trajectory."""
    try:
        retrieval = validate_retrieval_result(retrieval_result, pair)
    except RetrievalValidationError as error:
        raise OllamaRuntimeError(f"retrieval receipt is not runnable: {error}") from error
    if model_id not in {model["id"] for model in contract["models"]}:
        raise OllamaRuntimeError("model_id is not declared in the frozen contract")
    if seed not in contract["execution"]["seeds"]:
        raise OllamaRuntimeError("seed is not declared in the frozen contract")
    evidence = [
        {"citation_id": item["citation_id"], "snippet": item["snippet"]}
        for item in retrieval["evidence"]
    ]
    user_payload = {
        "question": pair["question"],
        "cutoff_date": f"{pair['cutoff_year']}-12-31",
        "evidence": evidence,
        "citation_rule": (
            "citation_ids must be [] when evidence is empty; otherwise citation_ids may "
            "contain only the supplied citation_id."
        ),
    }
    return {
        "model": model_id,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    user_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
            },
        ],
        "stream": False,
        "think": contract["execution"]["think"],
        "format": _RESPONSE_FORMAT,
        "options": {
            "temperature": contract["execution"]["temperature"],
            "num_predict": contract["execution"]["num_predict"],
            "seed": seed,
        },
    }


def classify_chat_response(
    response_bytes: bytes,
    pair: dict[str, Any],
    retrieval_result: dict[str, Any],
    *,
    requested_model_id: str,
) -> ChatResult:
    """Preserve response bytes while distinguishing malformed from invalid output."""
    try:
        document = json.loads(response_bytes.decode("utf-8"))
        model_id = document["model"]
        done = document["done"]
        message = document["message"]
        role = message["role"]
        content = message["content"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        return ChatResult("malformed_response", response_bytes, None, "malformed_ollama_envelope")
    thinking = message.get("thinking") if isinstance(message, dict) else None
    if (
        model_id != requested_model_id
        or done is not True
        or role != "assistant"
        or not isinstance(content, str)
        or not content
        or (thinking is not None and thinking != "")
    ):
        return ChatResult("malformed_response", response_bytes, None, "malformed_ollama_envelope")
    try:
        score = score_response(pair, retrieval_result, content)
    except ScoringValidationError as error:
        raise OllamaRuntimeError(f"response cannot be scored against its retrieval trace: {error}") from error
    status = "invalid_output" if score["answer_label"] == "invalid_output" else "ok"
    return ChatResult(status, response_bytes, content, None)


class OllamaHttpClient:
    """Small stdlib-only client whose failures preserve the retry boundary."""

    def __init__(self, endpoint: str = "http://127.0.0.1:11434") -> None:
        if not endpoint.startswith("http://") and not endpoint.startswith("https://"):
            raise OllamaRuntimeError("Ollama endpoint must be an HTTP(S) URL")
        self._endpoint = endpoint.rstrip("/")

    def inventory(self, timeout_seconds: int) -> dict[str, str]:
        """Read model names and digests from Ollama without dispatching a trajectory."""
        response = self._request("/api/tags", None, timeout_seconds)
        if response.status != "ok":
            raise OllamaRuntimeError(f"Ollama model inventory failed: {response.status}")
        try:
            document = json.loads(response.response_bytes.decode("utf-8"))
            models = document["models"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise OllamaRuntimeError("Ollama model inventory response is malformed") from error
        if not isinstance(models, list):
            raise OllamaRuntimeError("Ollama model inventory models must be a list")
        inventory: dict[str, str] = {}
        for entry in models:
            if not isinstance(entry, dict):
                raise OllamaRuntimeError("Ollama model inventory contains a non-object")
            name = entry.get("name")
            digest = entry.get("digest")
            if not isinstance(name, str) or not isinstance(digest, str) or name in inventory:
                raise OllamaRuntimeError("Ollama model inventory contains an invalid model identity")
            inventory[name] = digest
        return inventory

    def chat(self, request: dict[str, Any], timeout_seconds: int) -> ChatResult:
        """Dispatch exactly one chat request and retain all returned response bytes."""
        return self._request("/api/chat", request, timeout_seconds)

    def _request(
        self, path: str, payload: dict[str, Any] | None, timeout_seconds: int
    ) -> ChatResult:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"{self._endpoint}{path}", data=data, headers=headers, method="POST" if data else "GET")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                body = response.read()
                if not 200 <= response.status < 300:
                    return ChatResult("returned_error", body, None, f"http_{response.status}")
                return ChatResult("ok", body, None, None)
        except HTTPError as error:
            return ChatResult("returned_error", error.read(), None, f"http_{error.code}")
        except TimeoutError as error:
            raise TimeoutAfterDispatch("Ollama request timed out after dispatch") from error
        except (URLError, ConnectionError, OSError) as error:
            raise TransportFailureBeforeResponse("Ollama transport failed before response") from error


def verify_declared_model_inventory(
    contract: dict[str, Any], inventory: dict[str, str]
) -> dict[str, str]:
    """Reject dispatch unless every frozen model digest exactly matches Ollama's inventory."""
    if not isinstance(inventory, dict):
        raise OllamaRuntimeError("Ollama model inventory must be a mapping")
    declared = {entry["id"]: entry["digest"] for entry in contract["models"]}
    for model_id, expected_digest in declared.items():
        observed_digest = inventory.get(model_id)
        if observed_digest != expected_digest:
            raise OllamaRuntimeError(
                f"Ollama model digest drift for {model_id}: expected {expected_digest}, got {observed_digest}"
            )
    return declared


def request_sha256(request: dict[str, Any]) -> str:
    """Return the canonical identity of the exact dispatched chat payload."""
    return canonical_json_sha256(request)
