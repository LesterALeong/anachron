"""Offline source-construction preflight for the six development artifacts."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from anachron.routes.v2 import load_contract
from anachron.routes.v2.admission import (
    canonical_json_sha256,
    phase_raw_artifact_paths,
    revalidate_raw_source,
)
from anachron.routes.v2.manifest import _mapping_input
from anachron.routes.v2.source_excerpt import build_excerpt_receipts
from anachron.routes.v2.sources import validate_sampling_frame

ROOT = Path(__file__).parents[1]


def validate_source_construction(repository: Path, mapping_path: Path) -> dict[str, object]:
    """Read only the six development sources; never create decisions or manifests."""
    root = repository.resolve()
    phase = "development"
    contract = load_contract(root / "research" / "routes-v2" / "contract.json")
    frame = validate_sampling_frame(
        json.loads((root / "research" / "routes-v2" / "sampling_frame.json").read_text(encoding="utf-8")),
        contract,
    )
    mapping = _mapping_input(json.loads(mapping_path.read_text(encoding="utf-8")), contract, frame, phase)
    paths = phase_raw_artifact_paths(root, phase)
    receipts = []
    with tempfile.TemporaryDirectory() as temporary:
        temporary_root = Path(temporary)
        for index, raw_path in enumerate(paths):
            item_id = f"routes-v2:{phase}:{index}"
            receipt = revalidate_raw_source(
                contract_path=root / "research" / "routes-v2" / "contract.json",
                sampling_frame_path=root / "research" / "routes-v2" / "sampling_frame.json",
                raw_artifact_path=raw_path,
                phase=phase,
                item_id=item_id,
                output_path=temporary_root / f"{index}.revalidation.json",
            )
            if mapping[item_id]["raw_discovery_artifact_sha256"] != receipt["raw_discovery_artifact_sha256"]:
                raise ValueError("mapping raw source binding drifted")
            pre, post = build_excerpt_receipts(
                contract=contract,
                revalidation_receipt=receipt,
                raw_artifact_path=raw_path,
                mapping_item=mapping[item_id],
            )
            receipts.extend((pre, post))
    return {
        "schema_version": "routes-v2-source-construction-preflight-v1",
        "phase": phase,
        "mapping_sha256": canonical_json_sha256(json.loads(mapping_path.read_text(encoding="utf-8"))),
        "excerpt_receipt_sha256s": [receipt["receipt_sha256"] for receipt in receipts],
        "decision_created": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--mapping", required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(validate_source_construction(args.repository, args.mapping), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
