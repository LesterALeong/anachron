"""Run the single GO-bound v5 local measurement; no outreach or upload exists here."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from anachron.v5_measurement import (
    V5MeasurementError,
    V5OperationalFailure,
    run_measurement,
    validate_pending_inputs,
    validate_run_inputs,
)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--full-plan", required=True, type=Path)
    parser.add_argument("--conditional-go", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--preflight-only", action="store_true")
    modes.add_argument("--pending-only", action="store_true")
    values = parser.parse_args(arguments)
    try:
        if values.preflight_only:
            validate_run_inputs(
                values.full_plan,
                values.conditional_go,
                values.output,
                repository_root=values.repository_root,
            )
            print(json.dumps({"status": "PREFLIGHT_OK"}, sort_keys=True))
            return 0
        if values.pending_only:
            validate_pending_inputs(
                values.full_plan,
                values.conditional_go,
                values.output,
                repository_root=values.repository_root,
            )
            print(json.dumps({"status": "PENDING_VALID"}, sort_keys=True))
            return 0
        result = run_measurement(values.full_plan, values.conditional_go, values.output, repository_root=values.repository_root, endpoint=values.endpoint)
    except V5OperationalFailure as error:
        print(json.dumps({"fault_code": error.fault_code, "status": "TERMINAL_OPERATIONAL_FAILURE"}, sort_keys=True))
        return 1
    except V5MeasurementError as error:
        print(f"V5 measurement invalid: {error}")
        return 1
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
