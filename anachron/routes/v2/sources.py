"""Strict validation of the phase-separated Routes v2 sampling frame."""

from __future__ import annotations

from typing import Any

from anachron.routes.v2.admission import AdmissionError, _validate_frame
from anachron.routes.v2.schema import (
    ContractValidationError,
    validate_contract,
)


class SamplingFrameValidationError(ValueError):
    """Raised when the v2 frame is not the exact 6/18/36 ExAnte selection."""


def validate_sampling_frame(frame: Any, contract: dict[str, Any], *, repository: str | None = None) -> dict[str, Any]:
    """Bind all 60 title/year choices and their ExAnte provenance to the contract."""
    try:
        checked = validate_contract(contract)
    except ContractValidationError as error:
        raise SamplingFrameValidationError("sampling frame cannot bind an invalid contract") from error
    try:
        return _validate_frame(frame, checked, repository=repository)
    except AdmissionError as error:
        raise SamplingFrameValidationError("sampling frame parent/provenance/membership validation failed") from error
