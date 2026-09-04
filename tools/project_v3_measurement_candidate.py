"""Project a complete sealed v3 study through the frozen protocol only."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from tools.v3_candidate_common import (
    canonical_json,
    project_candidate,
    require_create_only_output,
)


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol-root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    values = parser.parse_args(arguments)
    require_create_only_output(values.output, (values.protocol_root, values.evidence))
    projection = project_candidate(values.protocol_root, values.evidence)
    with values.output.open("xb") as output:
        output.write(canonical_json(projection))
        output.flush()
        os.fsync(output.fileno())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
