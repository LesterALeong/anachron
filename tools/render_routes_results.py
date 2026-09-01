"""Render Routes v2 results only by replaying a frozen analysis root."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

if __package__ != "tools":
    raise SystemExit("Run this renderer as a module: python -m tools.render_routes_results ...")

from anachron.routes.v2.analysis import replay_analysis_root


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def render_verified(analysis_root: Path, frozen_root: Path) -> tuple[str, dict]:
    """Re-run the sole reducer immediately before rendering immutable TeX."""
    result, analysis_receipt = replay_analysis_root(analysis_root, frozen_root)
    value = result.value
    difference = f"{float(value['paired_misdated_minus_truthful']):.3f}"
    conclusion = "met" if value["result_mode"] == "positive" else "did not meet"
    text = "\n".join((
        "% Generated only by the verified Routes v2 analysis-root replay.",
        "\\section{Finite-set confirmatory result}",
        "This finite, frozen-set analysis " + conclusion + " its pre-specified gate.",
        "The paired primary difference was $" + difference + "$.",
        "This result is limited to the frozen source set and declared local models; it is not a general leakage estimate.",
        "",
    ))
    receipt = {
        "schema_version": "routes-v2-render-receipt",
        "analysis_replay_receipt_sha256": analysis_receipt["receipt_sha256"],
        "result_sha256": value["result_sha256"],
        "output_sha256": _sha256(text.encode("utf-8")),
    }
    receipt["receipt_sha256"] = "sha256:" + hashlib.sha256(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return text, {"analysis_replay": analysis_receipt, "render": receipt}


def main() -> None:
    parser = argparse.ArgumentParser(description="Render only a replay-verified Routes v2 result.")
    parser.add_argument("--analysis-root", required=True, type=Path)
    parser.add_argument("--frozen-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--render-receipt", required=True, type=Path)
    args = parser.parse_args()
    text, receipts = render_verified(args.analysis_root, args.frozen_root)
    args.output.write_bytes(text.encode("utf-8"))
    args.render_receipt.write_text(
        json.dumps(receipts, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
