"""Render a local candidate-bound UNSENT outreach draft with no external capability."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from anachron.data.v4_registry import canonical_json_bytes
from anachron.v4_candidate_release_common import (
    UNSENT_OUTREACH_SCHEMA,
    CandidateReleaseError,
    create_staging_directory,
    local_release_closure,
    publish_staging_directory,
    remove_staging,
)
from anachron.v4_paths import admit_repository_root


def _draft(metadata: dict[str, Any]) -> str:
    return "\n".join(
        (
            "# UNSENT outreach draft",
            "",
            "Status: UNSENT. This local artifact has no recipient, dispatch instruction, or upload target.",
            "",
            f"Title: {metadata['title']}",
            "",
            metadata["abstract"],
            "",
            "This draft is not authorization to contact anyone, request an endorsement, upload files, or submit the paper.",
            "",
        )
    )


def render(repository_root: Path, local_release: Path, output: Path) -> dict[str, str | int]:
    """Create exactly one local UNSENT draft and its candidate-bound receipt."""

    root = admit_repository_root(repository_root)
    closure, bindings = local_release_closure(root, local_release)
    output, staging = create_staging_directory(output, root, closure["local_release"])
    try:
        draft = _draft(closure["metadata"])
        maximum = 262144
        if len(draft.encode("utf-8")) > maximum:
            raise CandidateReleaseError("UNSENT draft exceeds the contract byte cap")
        (staging / "UNSENT.md").write_text(draft, encoding="utf-8", newline="\n")
        receipt: dict[str, str | int] = {
            "arxiv_metadata_sha256": bindings["arxiv_metadata_sha256"],
            "candidate_pdf_sha256": bindings["candidate_pdf_sha256"],
            "candidate_receipt_sha256": bindings["candidate_receipt_sha256"],
            "local_release_receipt_sha256": bindings["local_release_receipt_sha256"],
            "schema_version": UNSENT_OUTREACH_SCHEMA,
            "source_archive_sha256": bindings["source_archive_sha256"],
            "status": "UNSENT",
            "v3_included_count": 0,
        }
        (staging / "outreach_receipt.json").write_bytes(canonical_json_bytes(receipt))
        if tuple(sorted(item.name for item in staging.iterdir())) != (
            "UNSENT.md",
            "outreach_receipt.json",
        ):
            raise CandidateReleaseError("UNSENT outreach completion set differs")
        publish_staging_directory(staging, output)
        return receipt
    except Exception:
        remove_staging(staging)
        raise


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--local-release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    render(**vars(parser.parse_args(arguments)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
