"""Create one local answer-free v4 candidate projection."""

from __future__ import annotations

import argparse
from pathlib import Path

from anachron.v4_candidate_common import project_and_write_candidate


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--comparison", required=True, type=Path)
    parser.add_argument("--source-audit", required=True, type=Path)
    parser.add_argument("--runtime-identity", required=True, type=Path)
    parser.add_argument("--conditional-go", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    values = parser.parse_args(arguments)
    project_and_write_candidate(
        values.repository_root,
        source_manifest=values.source_manifest,
        comparison=values.comparison,
        source_audit=values.source_audit,
        runtime_identity=values.runtime_identity,
        conditional_go=values.conditional_go,
        evidence=values.evidence,
        output=values.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
