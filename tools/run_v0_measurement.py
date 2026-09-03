"""Run a frozen Anachron v0 measurement plan against local Ollama only."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from anachron.v0_measurement import run_measurement, run_static_controls


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one append-only Anachron v0 measurement plan against loopback Ollama."
    )
    parser.add_argument("--plan", type=Path, help="Frozen plan JSON")
    parser.add_argument("--output", type=Path, help="New evidence directory")
    parser.add_argument("--controls", action="store_true", help="Run offline static controls only")
    parser.add_argument("--falsifier-evidence", type=Path)
    parser.add_argument("--falsifier-receipt", type=Path)
    parser.add_argument("--full-go", type=Path)
    parser.add_argument(
        "--repository-root",
        type=Path,
        help="Detached tagged checkout used to admit and later verify governed source bytes",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.controls:
        print(json.dumps(run_static_controls(), indent=2, sort_keys=True))
        return 0
    if args.plan is None or args.output is None:
        raise SystemExit("--plan and --output are required unless --controls is set")
    analysis = run_measurement(
        args.plan,
        args.output,
        falsifier_evidence=args.falsifier_evidence,
        falsifier_receipt=args.falsifier_receipt,
        full_go=args.full_go,
        repository_root=args.repository_root,
    )
    print(json.dumps(analysis, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
