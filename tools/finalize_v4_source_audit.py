"""Create canonical, accepted v4 source-audit bytes from Lester's local review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from anachron.v4_measurement import finalize_source_audit


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--comparison", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    values = parser.parse_args(arguments)
    print(
        json.dumps(
            finalize_source_audit(
                values.repository_root,
                values.input,
                values.source_manifest,
                values.comparison,
                values.output,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
