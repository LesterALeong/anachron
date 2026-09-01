"""V2 pending-draft boundary.

Source discovery is intentionally external to this module: raw v1 artifacts
must be revalidated into a v2 pending draft before any decision can be sealed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from anachron.routes.v2.manifest import prepare_pending_draft as _prepare_pending_draft


def prepare_pending_draft(*, phase: str, contract_path: str | Path, sampling_frame_path: str | Path, revalidation_receipt_paths: list[str | Path], source_mapping_input_path: str | Path, output_path: str | Path, predecessor_evidence: Any = None) -> dict[str, Any]:
    """Create, but never approve, one path-bound phase pending draft."""
    return _prepare_pending_draft(
        phase=phase,
        contract_path=contract_path,
        sampling_frame_path=sampling_frame_path,
        revalidation_receipt_paths=revalidation_receipt_paths,
        source_mapping_input_path=source_mapping_input_path,
        output_path=output_path,
        predecessor_evidence=predecessor_evidence,
    )
