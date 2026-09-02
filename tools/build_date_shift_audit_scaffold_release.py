"""Create one tag-pinned, mechanically derived date-shift scaffold descriptor."""

from __future__ import annotations

import argparse
from pathlib import Path

from anachron.date_shift_bundle import write_create_only
from anachron.date_shift_provenance import build_audit_scaffold_release


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a date-shift audit scaffold release descriptor."
    )
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    release = build_audit_scaffold_release(args.repository, args.tag)
    write_create_only(args.output, release)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
