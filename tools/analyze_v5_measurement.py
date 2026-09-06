"""Replay v5 evidence and print only the answer-free scientific summary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from anachron.v5_measurement import (
    V5MeasurementError,
    analyze_failure,
    analyze_measurement,
)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--repository-root", type=Path)
    parser.add_argument("--phase", choices=("primary", "failure"), default="primary")
    values = parser.parse_args(arguments)
    try:
        if values.phase == "failure":
            result = analyze_failure(values.evidence)
        else:
            if values.repository_root is None:
                raise V5MeasurementError("repository root is required for primary replay")
            result = analyze_measurement(values.evidence, repository_root=values.repository_root)
    except V5MeasurementError as error:
        print(f"V5 measurement replay invalid: {error}")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
