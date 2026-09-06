"""Create one answer-free v5 candidate projection from replay-verified evidence."""

from __future__ import annotations

import argparse
from pathlib import Path

from anachron.v5_candidate_common import project_and_write_candidate


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--carry-forward", required=True, type=Path)
    parser.add_argument("--runtime-identity", required=True, type=Path)
    parser.add_argument("--conditional-go", required=True, type=Path)
    parser.add_argument("--materialization-receipt", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    values = parser.parse_args(arguments)
    project_and_write_candidate(**vars(values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
