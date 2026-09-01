"""Fresh v2 attestations for optionally reused raw Wikipedia discovery artifacts."""

from __future__ import annotations

from typing import Any


class SourceIntegrityError(ValueError):
    """Raised when a raw artifact cannot become a v2 revalidation receipt."""


def revalidate_discovery_artifact(artifact: Any, *, title: str, cutoff_year: int) -> dict[str, Any]:
    """Convert a raw v1-shaped artifact into an explicit v2 revalidation receipt.

    The receipt is not a human approval and cannot itself seal a v2 source
    pair. It merely proves which immutable post revision and content hash the
    pending v2 draft must bind.
    """
    del artifact, title, cutoff_year
    raise SourceIntegrityError(
        "free-form revalidation is forbidden; use admission.revalidate_raw_source with exact artifact paths"
    )
