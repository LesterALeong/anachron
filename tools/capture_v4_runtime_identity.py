"""Create local v4 runtime identity evidence from supplied non-chat responses."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from anachron.v4_measurement import capture_runtime_identity


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--version-response", required=True, type=Path)
    parser.add_argument("--tags-response", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--comparison", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    values = parser.parse_args(arguments)
    print(
        json.dumps(
            capture_runtime_identity(
                values.repository_root,
                values.version_response,
                values.tags_response,
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
