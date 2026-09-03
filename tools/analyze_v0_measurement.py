"""Verify and deterministically analyze a sealed Anachron v0 evidence run."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from anachron.v0_measurement import analyze_evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify an Anachron v0 evidence manifest and recompute its analysis."
    )
    parser.add_argument("evidence", type=Path, help="Sealed evidence directory")
    parser.add_argument(
        "--repository-root",
        type=Path,
        help="Local checkout containing the preserved annotated release tag",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    print(json.dumps(analyze_evidence(args.evidence, args.repository_root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
