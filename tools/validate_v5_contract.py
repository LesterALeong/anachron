"""Validate the offline v5 authority closure."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from anachron.v5_contract import (
    V5ContractError,
    validate_authority_contract,
    validate_candidate_contract,
)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    values = parser.parse_args(arguments)
    try:
        result = validate_authority_contract(values.repository_root)
        validate_candidate_contract(values.repository_root)
        print(json.dumps(result, indent=2, sort_keys=True))
    except V5ContractError as error:
        print(f"V5 authority contract invalid: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
