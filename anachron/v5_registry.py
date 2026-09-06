"""Strict v5 loader over the accepted byte-identical eight-card panel."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from anachron.v5_custody import V5CustodyError, capture_regular
from anachron.v5_paths import V5PathError, admit_existing, admit_repository_root

REGISTRY_PATH = "research/v5_measurement/case_registry.json"
COMPATIBILITY_CASE_PATH = "research/v5_measurement/compatibility_case.json"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class V5RegistryError(ValueError):
    """Raised when the carried panel is not exact and locally valid."""


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def strict_json_loads(raw: bytes, label: str) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise V5RegistryError(f"{label} contains duplicate key {key}")
            result[key] = value
        return result

    def nonfinite(value: str) -> object:
        raise V5RegistryError(f"{label} contains non-finite value {value}")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise V5RegistryError(f"{label} is not UTF-8 JSON") from error


def _mapping(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise V5RegistryError(f"{label} has unexpected or missing keys")
    return value


def _string(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise V5RegistryError(f"{label} must be a nonempty string")
    return value


def _date(value: object, label: str) -> str:
    value = _string(value, label)
    if not _DATE.fullmatch(value):
        raise V5RegistryError(f"{label} is not an exact date")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise V5RegistryError(f"{label} is not a calendar date") from error
    return value


def _read(root: Path, relative: str, label: str) -> bytes:
    if type(relative) is not str or not relative or "\\" in relative or relative.startswith("/"):
        raise V5RegistryError(f"{label} path differs")
    candidate = Path(relative)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise V5RegistryError(f"{label} path differs")
    try:
        target = admit_existing(root / candidate, label)
    except V5PathError as error:
        raise V5RegistryError(str(error)) from error
    try:
        target.relative_to(root)
    except ValueError as error:
        raise V5RegistryError(f"{label} escapes repository root") from error
    if not target.is_file():
        raise V5RegistryError(f"{label} must be a regular file")
    try:
        return capture_regular(target, label, 1_048_576).raw
    except V5CustodyError as error:
        raise V5RegistryError(f"{label} cannot be read") from error


def _card(raw: bytes, entry: dict[str, Any], label: str) -> dict[str, Any]:
    card = strict_json_loads(raw, label)
    if type(card) is not dict or raw != canonical_json_bytes(card):
        raise V5RegistryError(f"{label} is not canonical JSON")
    required = {"as_of", "case_id", "category", "corpus_records", "entity", "entity_identifier", "expected_mechanism", "expected_point_in_time_record", "prompt"}
    card = _mapping(card, required, label)
    for key in ("case_id", "category", "entity", "entity_identifier", "expected_mechanism", "prompt"):
        _string(card[key], f"{label}.{key}")
    _date(card["as_of"], f"{label}.as_of")
    if any(card[key] != entry[key] for key in ("as_of", "case_id", "category", "entity", "entity_identifier")):
        raise V5RegistryError(f"{label} does not match registry identity")
    if type(card["corpus_records"]) is not list or len(card["corpus_records"]) != 2:
        raise V5RegistryError(f"{label} must have exactly two corpus records")
    for index, record in enumerate(card["corpus_records"]):
        if type(record) is not dict or set(record) - {"id", "publish_date", "text", "restates", "listed_date", "delisted_date"}:
            raise V5RegistryError(f"{label}.corpus_records[{index}] differs")
        for key in ("id", "publish_date", "text"):
            if key not in record:
                raise V5RegistryError(f"{label}.corpus_records[{index}] differs")
        _string(record["id"], f"{label}.corpus_records[{index}].id")
        _date(record["publish_date"], f"{label}.corpus_records[{index}].publish_date")
        _string(record["text"], f"{label}.corpus_records[{index}].text")
    return card


def load_v5_registry(repository_root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Load the exact eight v5 cards without accepting a larger panel."""

    try:
        root = admit_repository_root(repository_root)
    except V5PathError as error:
        raise V5RegistryError(str(error)) from error
    raw = _read(root, REGISTRY_PATH, "v5 case registry")
    registry = strict_json_loads(raw, "v5 case registry")
    if type(registry) is not dict or raw != canonical_json_bytes(registry):
        raise V5RegistryError("v5 case registry is not canonical JSON")
    registry = _mapping(registry, {"case_count", "cases", "schema_version", "source_audit_status"}, "v5 case registry")
    if registry["schema_version"] != "anachron-v4-case-registry-v1" or registry["source_audit_status"] != "PENDING" or type(registry["case_count"]) is not int or registry["case_count"] != 8:
        raise V5RegistryError("v5 registry identity differs")
    if type(registry["cases"]) is not list or len(registry["cases"]) != 8:
        raise V5RegistryError("v5 registry case count differs")
    cards: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(registry["cases"]):
        entry = _mapping(value, {"as_of", "case_card", "category", "entity", "entity_identifier", "id"}, f"v5 case registry.cases[{index}]")
        for key in ("case_card", "category", "entity", "entity_identifier", "id"):
            _string(entry[key], f"v5 case registry.cases[{index}].{key}")
        _date(entry["as_of"], f"v5 case registry.cases[{index}].as_of")
        if not entry["case_card"].startswith("cases/"):
            raise V5RegistryError("v5 case-card path differs")
        card = _card(_read(root, f"research/v5_measurement/{entry['case_card']}", f"v5 card {entry['id']}"), {**entry, "case_id": entry["id"]}, f"v5 card {entry['id']}")
        if entry["id"] in cards:
            raise V5RegistryError("v5 case identifiers are not unique")
        cards[entry["id"]] = card
    return registry, cards


def load_v5_registry_snapshot(snapshot: Mapping[str, bytes]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Parse the primary panel solely from already captured governed bytes."""

    try:
        raw = snapshot[REGISTRY_PATH]
    except KeyError as error:
        raise V5RegistryError("v5 snapshot registry differs") from error
    registry = strict_json_loads(raw, "v5 case registry")
    if type(registry) is not dict or raw != canonical_json_bytes(registry):
        raise V5RegistryError("v5 case registry is not canonical JSON")
    registry = _mapping(registry, {"case_count", "cases", "schema_version", "source_audit_status"}, "v5 case registry")
    if registry["schema_version"] != "anachron-v4-case-registry-v1" or registry["source_audit_status"] != "PENDING" or type(registry["case_count"]) is not int or registry["case_count"] != 8:
        raise V5RegistryError("v5 registry identity differs")
    if type(registry["cases"]) is not list or len(registry["cases"]) != 8:
        raise V5RegistryError("v5 registry case count differs")
    cards: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(registry["cases"]):
        entry = _mapping(value, {"as_of", "case_card", "category", "entity", "entity_identifier", "id"}, f"v5 case registry.cases[{index}]")
        for key in ("case_card", "category", "entity", "entity_identifier", "id"):
            _string(entry[key], f"v5 case registry.cases[{index}].{key}")
        _date(entry["as_of"], f"v5 case registry.cases[{index}].as_of")
        relative = f"research/v5_measurement/{entry['case_card']}"
        try:
            card_raw = snapshot[relative]
        except KeyError as error:
            raise V5RegistryError("v5 snapshot card differs") from error
        card = _card(card_raw, {**entry, "case_id": entry["id"]}, f"v5 card {entry['id']}")
        if entry["id"] in cards:
            raise V5RegistryError("v5 case identifiers are not unique")
        cards[entry["id"]] = card
    return registry, cards


def load_compatibility_case(repository_root: Path) -> dict[str, Any]:
    root = admit_repository_root(repository_root)
    raw = _read(root, COMPATIBILITY_CASE_PATH, "v5 compatibility case")
    value = strict_json_loads(raw, "v5 compatibility case")
    if type(value) is not dict or raw != canonical_json_bytes(value):
        raise V5RegistryError("v5 compatibility case is not canonical JSON")
    entry = {"as_of": value.get("as_of"), "case_id": value.get("case_id"), "category": value.get("category"), "entity": value.get("entity"), "entity_identifier": value.get("entity_identifier")}
    card = _card(raw, entry, "v5 compatibility case")
    _, cards = load_v5_registry(root)
    if card["case_id"] in cards:
        raise V5RegistryError("v5 compatibility case overlaps primary panel")
    return card


def load_compatibility_case_snapshot(snapshot: Mapping[str, bytes], cards: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    """Parse the compatibility card from the same immutable snapshot as the panel."""

    try:
        raw = snapshot[COMPATIBILITY_CASE_PATH]
    except KeyError as error:
        raise V5RegistryError("v5 snapshot compatibility differs") from error
    value = strict_json_loads(raw, "v5 compatibility case")
    if type(value) is not dict or raw != canonical_json_bytes(value):
        raise V5RegistryError("v5 compatibility case is not canonical JSON")
    entry = {"as_of": value.get("as_of"), "case_id": value.get("case_id"), "category": value.get("category"), "entity": value.get("entity"), "entity_identifier": value.get("entity_identifier")}
    card = _card(raw, entry, "v5 compatibility case")
    if card["case_id"] in cards:
        raise V5RegistryError("v5 compatibility case overlaps primary panel")
    return card


def eligible_records(card: dict[str, Any], mode: str, query: str) -> list[dict[str, Any]]:
    """Retrieve only with the harness-owned card cutoff."""

    if mode not in {"unrestricted", "enforced"}:
        raise V5RegistryError("v5 retrieval mode differs")
    if type(query) is not str or not query.strip():
        raise V5RegistryError("v5 query differs")
    tokens = {token for token in re.findall(r"[a-z0-9]+", query.lower()) if len(token) > 2}
    records = list(card["corpus_records"])
    if mode == "enforced":
        records = [record for record in records if record["publish_date"] <= card["as_of"]]
    ranked = sorted(records, key=lambda record: (-sum(token in record["text"].lower() for token in tokens), record["publish_date"], record["id"]))
    matching = [record for record in ranked if any(token in record["text"].lower() for token in tokens)]
    return matching or ranked[:1]
