"""Verify and reduce sealed v3 measurement evidence without model calls."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from anachron.v3_measurement import analyze_evidence


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--repository-root", type=Path)
    values = parser.parse_args(arguments)
    repository_root = values.repository_root or Path(__file__).resolve().parent.parent
    print(json.dumps(analyze_evidence(values.evidence, repository_root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
