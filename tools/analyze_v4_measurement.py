"""Verify the deterministic answer-free v4 projection without producing chats."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from anachron.v4_measurement import analyze_measurement


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--phase", choices=("compatibility", "full", "failure"), required=True)
    parser.add_argument("--repository-root", required=True, type=Path)
    values = parser.parse_args(arguments)
    print(
        json.dumps(
            analyze_measurement(
                values.evidence, repository_root=values.repository_root, phase=values.phase
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
