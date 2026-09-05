"""Validate the offline v4 authority-binding graph."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from anachron.v4_contract import V4ContractError, validate_authority_contract


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    values = _parser().parse_args(arguments)
    try:
        result = validate_authority_contract(values.repository_root)
    except V4ContractError as error:
        print(f"V4 authority contract invalid: {error}")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
