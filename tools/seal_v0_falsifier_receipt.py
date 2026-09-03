"""Create a canonical, create-only receipt for a passing v0 falsifier."""

from __future__ import annotations

import argparse
from pathlib import Path

from anachron.v0_measurement import seal_falsifier_receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path)
    args = parser.parse_args()
    seal_falsifier_receipt(args.evidence, args.plan, args.output, args.repository_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
