"""Create the deterministic local v4 source-audit worksheet."""

from __future__ import annotations

import argparse
from pathlib import Path

from anachron.v4_measurement import build_source_audit_packet


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="Absent external packet directory")
    values = parser.parse_args(arguments)
    print(build_source_audit_packet(values.repository_root, values.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
