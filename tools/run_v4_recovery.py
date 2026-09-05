"""The sole CLI entrypoint that can invoke the conditional v4 chat runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from anachron.v4_measurement import run_measurement


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--compatibility-plan", required=True, type=Path)
    parser.add_argument("--full-plan", required=True, type=Path)
    parser.add_argument("--conditional-go", required=True, type=Path)
    parser.add_argument("--source-audit", required=True, type=Path)
    parser.add_argument("--runtime-identity", required=True, type=Path)
    parser.add_argument("--comparison", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    values = parser.parse_args(arguments)
    result = run_measurement(values.full_plan, values.conditional_go, values.source_audit, values.runtime_identity, values.output, repository_root=values.repository_root, compatibility_plan=values.compatibility_plan, comparison=values.comparison, source_manifest=values.source_manifest, preflight_only=values.preflight_only)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
