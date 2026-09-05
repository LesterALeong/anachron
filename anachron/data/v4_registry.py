"""Strict local loader for the eight-card v4 synthetic panel."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any

from anachron.v4_contract import V4ContractError, _admit_repository_root, _read_file

REGISTRY_PATH = "research/v4_measurement/case_registry.json"
COMPATIBILITY_CASE_PATH = "research/v4_measurement/compatibility_case.json"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CATEGORIES = {
    "future-information-finance",
    "future-information-general",
    "current-anchor-finance",
    "current-anchor-general",
    "restatement-original",
    "restatement-later",
    "survivorship-not-yet-listed",
    "survivorship-delisted",
}


class V4RegistryError(ValueError):
    """Raised when a v4 case-card or its no-overlap binding is invalid."""


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def strict_json_loads(raw: bytes, label: str) -> object:
    def duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise V4RegistryError(f"{label} contains duplicate key {key}")
            result[key] = value
        return result

    def nonfinite(value: str) -> object:
        raise V4RegistryError(f"{label} contains non-finite value {value}")

    def decimal(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise V4RegistryError(f"{label} contains non-finite number")
        return parsed

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=duplicates,
            parse_constant=nonfinite,
            parse_float=decimal,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise V4RegistryError(f"{label} is not UTF-8 JSON") from error


def _type(value: object, expected: type, label: str) -> Any:
    if type(value) is not expected:
        raise V4RegistryError(f"{label} has wrong JSON type")
    return value


def _mapping(value: object, keys: set[str], label: str) -> dict[str, Any]:
    mapping = _type(value, dict, label)
    if set(mapping) != keys:
        raise V4RegistryError(f"{label} has unexpected or missing keys")
    return mapping


def _string(value: object, label: str) -> str:
    result = _type(value, str, label)
    if not result:
        raise V4RegistryError(f"{label} must be nonempty")
    return result


def _date(value: object, label: str) -> str:
    result = _string(value, label)
    if not _DATE.fullmatch(result):
        raise V4RegistryError(f"{label} is not an exact date")
    try:
        date.fromisoformat(result)
    except ValueError as error:
        raise V4RegistryError(f"{label} is not a calendar date") from error
    return result


def _canonical_object(
    root: Path, relative: str, label: str, *, require_canonical: bool = True
) -> tuple[dict[str, Any], bytes]:
    try:
        raw = _read_file(root, relative, label)
    except V4ContractError as error:
        raise V4RegistryError(str(error)) from error
    value = strict_json_loads(raw, label)
    mapping = _type(value, dict, label)
    if require_canonical and raw != canonical_json_bytes(mapping):
        raise V4RegistryError(f"{label} is not canonical JSON")
    return mapping, raw


def _validate_record(value: object, category: str, label: str) -> dict[str, Any]:
    mapping = _type(value, dict, label)
    keys = {"id", "publish_date", "text"}
    if category == "survivorship-not-yet-listed":
        keys.add("listed_date")
    elif category == "survivorship-delisted":
        keys.update({"delisted_date", "listed_date"})
    elif "restates" in mapping:
        keys.add("restates")
    mapping = _mapping(mapping, keys, label)
    _string(mapping["id"], f"{label}.id")
    _date(mapping["publish_date"], f"{label}.publish_date")
    _string(mapping["text"], f"{label}.text")
    if "listed_date" in mapping:
        _date(mapping["listed_date"], f"{label}.listed_date")
    if "delisted_date" in mapping:
        _date(mapping["delisted_date"], f"{label}.delisted_date")
    if "restates" in mapping:
        _string(mapping["restates"], f"{label}.restates")
    return mapping


def _validate_card(card: object, entry: dict[str, Any], label: str) -> dict[str, Any]:
    required = {
        "as_of",
        "case_id",
        "category",
        "corpus_records",
        "entity",
        "entity_identifier",
        "expected_mechanism",
        "expected_point_in_time_record",
        "prompt",
    }
    card = _mapping(card, required, label)
    for key in (
        "case_id",
        "category",
        "entity",
        "entity_identifier",
        "expected_mechanism",
        "prompt",
    ):
        _string(card[key], f"{label}.{key}")
    _date(card["as_of"], f"{label}.as_of")
    if (
        card["case_id"] != entry["id"]
        or card["entity"] != entry["entity"]
        or card["entity_identifier"] != entry["entity_identifier"]
    ):
        raise V4RegistryError(f"{label} does not match registry identity")
    if card["as_of"] != entry["as_of"] or card["category"] != entry["category"]:
        raise V4RegistryError(f"{label} does not match registry cutoff")
    if card["category"] not in _CATEGORIES:
        raise V4RegistryError(f"{label} has an unknown category")
    records = _type(card["corpus_records"], list, f"{label}.corpus_records")
    if len(records) != 2:
        raise V4RegistryError(f"{label} must contain exactly two corpus records")
    parsed = [
        _validate_record(record, card["category"], f"{label}.corpus_records[{index}]")
        for index, record in enumerate(records)
    ]
    identifiers = [record["id"] for record in parsed]
    if len(set(identifiers)) != 2:
        raise V4RegistryError(f"{label} corpus identifiers differ")
    expected_record = card["expected_point_in_time_record"]
    if card["category"] == "survivorship-not-yet-listed":
        if expected_record is not None:
            raise V4RegistryError(f"{label} not-yet-listed record must be null")
        if any(record["listed_date"] <= card["as_of"] for record in parsed):
            raise V4RegistryError(f"{label} listing boundary differs")
    else:
        _string(expected_record, f"{label}.expected_point_in_time_record")
        if expected_record not in identifiers:
            raise V4RegistryError(f"{label} expected record differs")
    if card["category"] == "survivorship-delisted" and any(
        record["listed_date"] > card["as_of"] or record["delisted_date"] > card["as_of"]
        for record in parsed
    ):
        raise V4RegistryError(f"{label} delisting boundary differs")
    for record in parsed:
        if "restates" in record and record["restates"] not in identifiers:
            raise V4RegistryError(f"{label} restatement target differs")
    return card


def load_v4_registry(
    repository_root: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Return the canonical registry and its verified cards keyed by case id."""

    root = _admit_repository_root(repository_root)
    registry, _ = _canonical_object(root, REGISTRY_PATH, "v4 case registry")
    registry = _mapping(
        registry,
        {"case_count", "cases", "schema_version", "source_audit_status"},
        "v4 case registry",
    )
    if (
        registry["schema_version"] != "anachron-v4-case-registry-v1"
        or registry["source_audit_status"] != "PENDING"
    ):
        raise V4RegistryError("v4 case registry identity differs")
    if _type(registry["case_count"], int, "v4 case registry.case_count") != 8:
        raise V4RegistryError("v4 case registry case count differs")
    entries = _type(registry["cases"], list, "v4 case registry.cases")
    if len(entries) != 8:
        raise V4RegistryError("v4 case registry row count differs")
    cards: dict[str, dict[str, Any]] = {}
    for index, raw_entry in enumerate(entries):
        entry = _mapping(
            raw_entry,
            {"as_of", "case_card", "category", "entity", "entity_identifier", "id"},
            f"v4 case registry.cases[{index}]",
        )
        for key in ("case_card", "category", "entity", "entity_identifier", "id"):
            _string(entry[key], f"v4 case registry.cases[{index}].{key}")
        _date(entry["as_of"], f"v4 case registry.cases[{index}].as_of")
        if not entry["case_card"].startswith("cases/") or ".." in entry[
            "case_card"
        ].split("/"):
            raise V4RegistryError("v4 case-card path differs")
        card, _ = _canonical_object(
            root,
            f"research/v4_measurement/{entry['case_card']}",
            f"v4 case card {entry['id']}",
        )
        cards[entry["id"]] = _validate_card(card, entry, f"v4 case card {entry['id']}")
    if len(cards) != 8:
        raise V4RegistryError("v4 case identifiers are not unique")
    return registry, cards


def load_compatibility_case(repository_root: Path) -> dict[str, Any]:
    """Load the distinct, excluded compatibility fixture without enlarging the panel."""

    root = _admit_repository_root(repository_root)
    card, _ = _canonical_object(root, COMPATIBILITY_CASE_PATH, "v4 compatibility case")
    entry = {
        "as_of": card.get("as_of"),
        "category": card.get("category"),
        "entity": card.get("entity"),
        "entity_identifier": card.get("entity_identifier"),
        "id": card.get("case_id"),
    }
    card = _validate_card(card, entry, "v4 compatibility case")
    registry, cards = load_v4_registry(root)
    if card["case_id"] in cards or card["entity"] in {
        item["entity"] for item in cards.values()
    }:
        raise V4RegistryError("v4 compatibility fixture overlaps the primary panel")
    if registry["case_count"] != 8:
        raise V4RegistryError(
            "v4 compatibility fixture must not alter primary panel size"
        )
    return card


def eligible_records(
    card: dict[str, Any], mode: str, query: str = ""
) -> list[dict[str, Any]]:
    """Return a deterministic query-ranked subset using the case cutoff only."""

    if mode not in {"unrestricted", "enforced"}:
        raise V4RegistryError("v4 retrieval mode differs")
    query_tokens = {
        token for token in re.findall(r"[a-z0-9]+", query.lower()) if len(token) > 2
    }
    candidates = list(card["corpus_records"])
    if mode == "enforced":
        candidates = [
            record for record in candidates if record["publish_date"] <= card["as_of"]
        ]
    ranked = sorted(
        candidates,
        key=lambda record: (
            -sum(token in record["text"].lower() for token in query_tokens),
            record["publish_date"],
            record["id"],
        ),
    )
    matching = [
        record
        for record in ranked
        if any(token in record["text"].lower() for token in query_tokens)
    ]
    return matching or ranked[:1]
