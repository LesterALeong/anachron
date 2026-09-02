"""Replay a complete sealed-bundle journal into non-invented analysis products."""

from __future__ import annotations

import argparse
from pathlib import Path

from anachron.date_shift import DateShiftValidationError
from anachron.date_shift_bundle import (
    load_bundle,
    reduce_terminals,
    validate_journal_v3,
    verify_bundle_derivation,
    write_create_only,
)
from anachron.date_shift_provenance import admit_scaffold_repository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay a complete date-shift sealed bundle without loose inputs."
    )
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--bundle-dir", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    provenance = admit_scaffold_repository(args.repository)
    bundle = load_bundle(args.bundle_dir)
    verify_bundle_derivation(bundle, args.repository.resolve(), provenance)
    if bundle["runtime_preflight"]["capture_provenance"] != provenance:
        raise DateShiftValidationError("bundle is not bound to this released scaffold")
    records = validate_journal_v3(args.run_dir / "journal.jsonl", bundle)
    terminals = [row for row in records if row["record_type"] == "terminal_outcome"]
    write_create_only(args.output, reduce_terminals(bundle, terminals))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
