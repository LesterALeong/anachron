"""Export non-analytic, treatment-label-blinded qualitative other-output rows."""

from __future__ import annotations

import argparse
import base64
import json
import re
from pathlib import Path

from anachron.date_shift import DateShiftValidationError, canonical_sha256
from anachron.date_shift_bundle import (
    load_bundle,
    validate_journal_v3,
    verify_bundle_derivation,
    write_create_only,
)
from anachron.date_shift_provenance import admit_scaffold_repository

_TEMPORAL_SENTENCE = re.compile(
    r"(?i)[^.?!]*(?:presented date|published|revision|as of|cutoff|before|after|eligible|later)[^.?!]*[.?!]?"
)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_YEAR = re.compile(r"\b(?:1[5-9]\d{2}|20\d{2})\b")
_MONTH_DATE = re.compile(
    r"(?i)\b(?:january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+\d{1,2}(?:,\s*\d{4})?\b|\b\d{1,2}\s+(?:january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s*\d{0,4}\b"
)


def redact_temporal_clues(text: str, clues: list[str]) -> str:
    value = _TEMPORAL_SENTENCE.sub("[TEMPORAL SENTENCE REDACTED]", text)
    for clue in sorted((clue for clue in clues if clue), key=len, reverse=True):
        value = re.sub(re.escape(clue), "[REDACTED]", value, flags=re.IGNORECASE)
    return _YEAR.sub(
        "[YEAR]", _MONTH_DATE.sub("[DATE]", _ISO_DATE.sub("[DATE]", value))
    )


def _response_content(row: dict) -> str:
    try:
        payload = json.loads(
            base64.b64decode(row["response_base64"], validate=True).decode("utf-8")
        )
        content = payload["message"]["content"]
    except (
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise DateShiftValidationError(
            "other-output response cannot be decoded"
        ) from error
    if not isinstance(content, str):
        raise DateShiftValidationError("other-output response content is invalid")
    return content


def build_qualitative_audit(bundle: dict, records: list[dict]) -> tuple[dict, dict]:
    rows, key_rows = [], []
    trajectories = bundle["schedule"]["trajectories"]
    for terminal in records:
        if (
            terminal["record_type"] != "terminal_outcome"
            or terminal.get("score", {}).get("answer_class") != "other"
        ):
            continue
        trajectory = trajectories[terminal["schedule_index"]]
        item = bundle["audited_items"]["items"][trajectory["item_index"]]
        blind_id = canonical_sha256(
            {
                "bundle": canonical_sha256(bundle["manifest"]),
                "index": trajectory["schedule_index"],
            }
        )[:28]
        clues = [
            item["topic"],
            item["citation_id"],
            item["cutoff_date"],
            item["presented_document_date_truthful"],
            item["presented_document_date_backdated"],
            trajectory["model_id"],
        ]
        rows.append(
            {
                "blind_id": blind_id,
                "response_content": redact_temporal_clues(
                    _response_content(terminal), clues
                ),
            }
        )
        key_rows.append(
            {
                "blind_id": blind_id,
                "item_id": item["item_id"],
                "model_id": trajectory["model_id"],
                "arm": trajectory["arm"],
                "schedule_index": trajectory["schedule_index"],
            }
        )
    return {
        "schema_version": "date-shift-treatment-label-blinded-qualitative-audit-v2",
        "purpose": "non_analytic_qualitative_review",
        "labels_withheld": [
            "item_id",
            "model_id",
            "arm",
            "schedule_index",
            "score",
            "citation_metadata",
        ],
        "rows": rows,
    }, {
        "schema_version": "date-shift-treatment-label-blinded-qualitative-audit-key-v2",
        "rows": key_rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a non-analytic treatment-label-blinded qualitative date-shift export."
    )
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--bundle-dir", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--key-output", required=True, type=Path)
    args = parser.parse_args(argv)
    provenance = admit_scaffold_repository(args.repository)
    bundle = load_bundle(args.bundle_dir)
    verify_bundle_derivation(bundle, args.repository.resolve(), provenance)
    if bundle["runtime_preflight"]["capture_provenance"] != provenance:
        raise DateShiftValidationError("bundle is not bound to this released scaffold")
    records = validate_journal_v3(args.run_dir / "journal.jsonl", bundle)
    blinded, key = build_qualitative_audit(bundle, records)
    write_create_only(args.output, blinded)
    write_create_only(args.key_output, key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
