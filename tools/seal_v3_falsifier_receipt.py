"""Seal a passing v3 falsifier receipt without starting a full measurement."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from anachron.v3_measurement import seal_falsifier_receipt


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--repository-root", type=Path)
    values = parser.parse_args(arguments)
    print(json.dumps(seal_falsifier_receipt(values.evidence, values.receipt, values.repository_root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
