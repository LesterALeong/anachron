"""Render a candidate-bound local UNSENT draft with no transport capability."""

from __future__ import annotations

import argparse
from pathlib import Path

from anachron.v5_candidate_release_common import (
    UNSENT_OUTREACH_SCHEMA,
    create_staging_directory,
    local_release_closure,
    outreach_root_profile,
    publish_staging_directory,
    remove_staging,
)
from anachron.v5_custody import V5CustodyError, write_create_only
from anachron.v5_paths import admit_repository_root
from anachron.v5_registry import canonical_json_bytes


def render(repository_root: Path, local_release: Path, output: Path) -> dict[str, str | int]:
    root = admit_repository_root(repository_root)
    closure, bindings = local_release_closure(root, local_release)
    target, staging = create_staging_directory(output, root, Path(local_release))
    try:
        profile = outreach_root_profile()
        budget = profile.budget()
        metadata = closure["metadata"]
        draft = "\n".join(("# UNSENT outreach draft", "", "Status: UNSENT. This local artifact has no recipient, dispatch instruction, upload target, or sending capability.", "", f"Title: {metadata['title']}", "", metadata["abstract"], "", "This draft does not authorize contact, endorsement-code transmission, upload, or submission.", ""))
        write_create_only(
            staging / "UNSENT.md",
            draft.encode("utf-8"),
            "UNSENT outreach draft",
            budget,
        )
        receipt: dict[str, str | int] = {**bindings, "schema_version": UNSENT_OUTREACH_SCHEMA, "status": "UNSENT", "v4_included_count": 0}
        write_create_only(
            staging / "outreach_receipt.json",
            canonical_json_bytes(receipt),
            "outreach receipt",
            budget,
        )
        publish_staging_directory(staging, target)
        return receipt
    except (OSError, V5CustodyError):
        remove_staging(staging)
        raise
    except Exception:
        remove_staging(staging)
        raise


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--local-release", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    values = parser.parse_args(arguments)
    render(**vars(values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
