"""Check or render local v4 authority blocks from their contract."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from anachron.v4_contract import V4ContractError, render_authority_documents


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    values = _parser().parse_args(arguments)
    try:
        clean = render_authority_documents(values.repository_root, values.check)
    except V4ContractError as error:
        print(f"V4 authority documents invalid: {error}")
        return 1
    if values.check and not clean:
        print("V4 authority documents are stale")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
