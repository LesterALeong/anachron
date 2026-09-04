"""Run a frozen v3 plan against the admitted local Ollama endpoint."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from anachron.v3_measurement import run_measurement


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--falsifier-evidence", type=Path)
    parser.add_argument("--falsifier-receipt", type=Path)
    parser.add_argument("--full-go", type=Path)
    parser.add_argument("--repository-root", type=Path)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    values = _parser().parse_args(arguments)
    result = run_measurement(values.plan, values.output, repository_root=values.repository_root, falsifier_evidence=values.falsifier_evidence, falsifier_receipt=values.falsifier_receipt, full_go=values.full_go)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
