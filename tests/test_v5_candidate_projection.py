"""Offline schema checks for the v5 answer-free candidate projection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anachron.v5_candidate_common import (
    CandidateProjectionError,
    _evidence_closure,
    validate_candidate_projection,
)
from anachron.v5_measurement import FINAL_CATEGORIES, FIRST_CATEGORIES


def _group(model: str, mode: str) -> dict[str, object]:
    first = {category: 0 for category in FIRST_CATEGORIES}
    final = {category: 0 for category in FINAL_CATEGORIES}
    first["valid"] = 16
    final["valid"] = 16
    return {"model": model, "mode": mode, "scheduled": 16, "adherence_numerator": 16, "adherence_denominator": 16, "adherence_rate_fixed_decimal": "1.000000", "conditional_exposure_numerator": 8, "conditional_exposure_denominator": 16, "conditional_exposure_rate_fixed_decimal": "0.500000", "first_categories": first, "final_categories": final}


def _candidate() -> dict[str, object]:
    files = [{"path": "manifest.json", "sha256": "0" * 64}]
    from anachron.v5_candidate_common import sha256_bytes
    from anachron.v5_registry import canonical_json_bytes

    return {"authority": {key: "0" * 64 for key in ("authority_contract_sha256", "carry_forward_sha256", "compatibility_plan_sha256", "conditional_go_sha256", "full_plan_sha256", "materialization_receipt_sha256", "runtime_identity_sha256", "source_manifest_sha256")}, "complete": True, "evidence_closure": {"files": files, "sha256": sha256_bytes(canonical_json_bytes(files)), "schema_version": "anachron-v5-whole-evidence-closure-v1"}, "evidence_manifest_sha256": "0" * 64, "projection": {"groups": [_group("model-a", mode) for mode in ("enforced", "unrestricted")] + [_group("model-b", mode) for mode in ("enforced", "unrestricted")], "paired_contrasts": [], "scheduled": 64, "v4_included_count": 0}, "schema_version": "anachron-v5-candidate-answer-free-projection-v1", "v4_included_count": 0}


class V5CandidateProjectionTests(unittest.TestCase):
    def test_projection_requires_all_denominators_categories_and_zero_v4(self) -> None:
        self.assertEqual(validate_candidate_projection(_candidate())["v4_included_count"], 0)
        for field, value in (("v4_included_count", 1), ("complete", False)):
            with self.subTest(field=field):
                candidate = _candidate()
                candidate[field] = value
                with self.assertRaises(CandidateProjectionError):
                    validate_candidate_projection(candidate)

    def test_nonadherence_is_not_exposure_zero(self) -> None:
        candidate = _candidate()
        group = candidate["projection"]["groups"][0]
        group["adherence_numerator"] = 15
        group["first_categories"]["valid"] = 15
        group["first_categories"]["date_mismatch"] = 1
        group["conditional_exposure_denominator"] = 16
        with self.assertRaises(CandidateProjectionError):
            validate_candidate_projection(candidate)

    def test_candidate_closure_rejects_success_entry_cap_plus_one(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            for index in range(351):
                (evidence / f"extra-{index:03d}.json").write_bytes(b"{}\n")
            with self.assertRaisesRegex(CandidateProjectionError, "too many entries"):
                _evidence_closure(evidence)


if __name__ == "__main__":
    unittest.main()
