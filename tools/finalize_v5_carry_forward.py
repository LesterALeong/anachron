"""Create the external v5 carry-forward receipt from accepted v4 authority."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from anachron.v5_carry_forward import V5CarryForwardError, derive_carry_forward
from anachron.v5_custody import ByteBudget, V5CustodyError, write_create_only
from anachron.v5_paths import (
    V5PathError,
    admit_create_only_external_output,
    admit_repository_root,
    fsync_directory,
)
from anachron.v5_registry import canonical_json_bytes


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--accepted-audit", required=True, type=Path)
    parser.add_argument("--v4-source-manifest", required=True, type=Path)
    parser.add_argument("--v5-source-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    values = parser.parse_args(arguments)
    try:
        root = admit_repository_root(values.repository_root)
        output = admit_create_only_external_output(values.output, root, "carry-forward output")
        result = derive_carry_forward(values.repository_root, accepted_audit=values.accepted_audit, v4_source_manifest=values.v4_source_manifest, v5_source_manifest=values.v5_source_manifest)
        write_create_only(
            output,
            canonical_json_bytes(result),
            "carry-forward output",
            ByteBudget(1_048_576, 1),
        )
        fsync_directory(output.parent, "carry-forward output parent")
    except (OSError, V5CarryForwardError, V5CustodyError, V5PathError) as error:
        print(f"V5 carry-forward invalid: {error}")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
