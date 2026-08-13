"""Command-line interface for paired Anachron mode comparisons."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from anachron.core.comparison import compare_modes


def _load_scores(path: str) -> dict[str, float]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object mapping sample ids to TCLR scores")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare paired TCLR scores from unrestricted and enforced Anachron runs."
    )
    parser.add_argument("unrestricted", help="JSON object mapping sample ids to TCLR scores")
    parser.add_argument("enforced", help="JSON object mapping sample ids to TCLR scores")
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--resamples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--format", choices=("table", "json"), default="table")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the paired comparison CLI."""
    args = _parser().parse_args(argv)
    report = compare_modes(
        _load_scores(args.unrestricted),
        _load_scores(args.enforced),
        confidence=args.confidence,
        n_resamples=args.resamples,
        seed=args.seed,
    )
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(report.table())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
