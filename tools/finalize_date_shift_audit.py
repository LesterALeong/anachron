"""Validate and canonically seal a completed personal date-shift audit."""

from __future__ import annotations

import argparse
from pathlib import Path

from anachron.date_shift import bytes_sha256, validate_author_audit
from anachron.date_shift_bundle import load_object, write_create_only


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a completed personal audit and create canonical JSON bytes."
    )
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    repository = args.repository.resolve()
    frame = load_object(repository / "research/date-shift/proposed_frame.json")
    items = load_object(repository / "research/date-shift/proposed_items.json")
    audit = load_object(args.input)
    validate_author_audit(frame, items, audit)
    write_create_only(args.output, audit)
    print(bytes_sha256(args.output.read_bytes()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
