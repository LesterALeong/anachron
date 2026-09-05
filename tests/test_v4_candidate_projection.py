"""Focused answer-free candidate-projection admission tests."""

from __future__ import annotations

import copy
import hashlib
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anachron import v4_candidate_common as candidate
from anachron.data.v4_registry import canonical_json_bytes, load_v4_registry


class V4CandidateProjectionTests(unittest.TestCase):
    @staticmethod
    def _release() -> dict[str, str]:
        digest = "a" * 40
        return {
            "branch_ref": digest,
            "commit": digest,
            "master_local": digest,
            "master_remote": digest,
            "origin": "https://example.invalid/LesterALeong/anachron.git",
            "remote_branch": digest,
            "remote_tag_object": digest,
            "remote_tag_peeled": digest,
            "tag": "v4-measurement-protocol-v1",
            "tag_object": digest,
            "tag_peeled": digest,
            "v3_commit": digest,
            "remote_v3_tag_object": digest,
            "remote_v3_tag_peeled": digest,
            "v3_tag": "v3-test",
            "v3_tag_object": digest,
            "v3_tag_peeled": digest,
        }

    def _projection(self, root: Path) -> dict:
        registry, _ = load_v4_registry(root)
        case_ids = [entry["id"] for entry in registry["cases"]]
        models = ("model-alpha", "model-beta")
        modes = ("enforced", "unrestricted")
        diagnostics = []
        pairs = []
        for model_index, model in enumerate(models):
            for case_index, case_id in enumerate(case_ids):
                for repetition in (1, 2):
                    unrestricted = (model_index + case_index + repetition) % 2
                    enforced = 1 - unrestricted
                    pairs.append(
                        {
                            "case_id": case_id,
                            "enforced_denominator": 1,
                            "enforced_numerator": enforced,
                            "model": model,
                            "repetition": repetition,
                            "sign_class": "positive" if unrestricted > enforced else "negative",
                            "unrestricted_denominator": 1,
                            "unrestricted_numerator": unrestricted,
                        }
                    )
                    for mode in modes:
                        diagnostics.append(
                            {
                                "case_id": case_id,
                                "mode": mode,
                                "model": model,
                                "query_nonblank": True,
                                "repetition": repetition,
                                "restatement_returned": False,
                                "survivorship_case": False,
                                "trajectory_id": f"{case_id}-{model}-{mode}-{repetition}",
                            }
                        )
        cells = []
        for model in (*models, "pooled"):
            for mode in modes:
                denominator = 32 if model == "pooled" else 16
                numerator = sum(
                    pair[f"{mode}_numerator"]
                    for pair in pairs
                    if model == "pooled" or pair["model"] == model
                )
                cells.append(
                    {
                        "denominator": denominator,
                        "metric": "tclr",
                        "model": model,
                        "mode": mode,
                        "numerator": numerator,
                        "rate_fixed_decimal": f"{numerator / denominator:.6f}",
                        "split": "primary",
                    }
                )
        return {
            "cells": cells,
            "diagnostics": diagnostics,
            "paired_tclr_reductions": pairs,
            "schema_version": "anachron-v4-answer-free-projection-v3",
            "split_counts": {
                "compatibility_trajectories": 2,
                "development_trajectories": 0,
                "primary_cases": 8,
                "primary_trajectories": 64,
            },
            "topology": {
                "compatibility_chats": 4,
                "main_chats": 128,
                "models": 2,
                "modes": 2,
                "repetitions": 2,
                "total_chats": 132,
            },
            "v3_included_count": 0,
        }

    def test_projected_fixture_binds_fixed_analyzer_and_revalidates_write(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            external = Path(temporary)
            source = {"release": self._release()}
            source_path = external / "M.json"
            source_path.write_bytes(canonical_json_bytes(source))
            comparison_path = external / "X.json"
            comparison_path.write_bytes(b"comparison")
            inputs = {}
            for name in ("A.json", "I.json", "G.json"):
                path = external / name
                path.write_bytes(canonical_json_bytes({"name": name}))
                inputs[name] = path
            evidence = external / "evidence"
            compatibility = evidence / "compatibility"
            full = evidence / "full"
            compatibility.mkdir(parents=True)
            full.mkdir()
            for source_name, relative in (
                (source_path, "source_manifest.json"),
                (comparison_path, "comparison.json"),
                (inputs["A.json"], "source_audit.json"),
                (inputs["I.json"], "runtime_identity.json"),
                (inputs["G.json"], "conditional_go.json"),
            ):
                (compatibility / relative).write_bytes(source_name.read_bytes())
            (full / "projection.json").write_bytes(canonical_json_bytes({"sealed": True}))
            with patch.object(candidate, "validate_authority_contract", return_value={}), patch.object(
                candidate, "derive_source_manifest", return_value=source
            ), patch.object(candidate, "validate_source_manifest", return_value=source), patch.object(
                candidate, "derive_bytes", return_value=b"comparison"
            ), patch.object(candidate, "analyze_measurement", return_value=self._projection(root)) as analyzer:
                result = candidate.project_candidate(
                    root,
                    source_manifest=source_path,
                    comparison=comparison_path,
                    source_audit=inputs["A.json"],
                    runtime_identity=inputs["I.json"],
                    conditional_go=inputs["G.json"],
                    evidence=evidence,
                )
                analyzer.assert_called_once_with(evidence, repository_root=root, phase="full")
                self.assertNotIn("analyzer", inspect.signature(candidate.project_candidate).parameters)
                self.assertTrue(result["complete"])
                self.assertEqual(result["v3_included_count"], 0)
                self.assertEqual(
                    result["authority"]["actual_go_sha256"],
                    hashlib.sha256(inputs["G.json"].read_bytes()).hexdigest(),
                )
                output = external / "candidate-projection.json"
                written = candidate.project_and_write_candidate(
                    root,
                    source_manifest=source_path,
                    comparison=comparison_path,
                    source_audit=inputs["A.json"],
                    runtime_identity=inputs["I.json"],
                    conditional_go=inputs["G.json"],
                    evidence=evidence,
                    output=output,
                )
                self.assertTrue(output.is_file())
                self.assertEqual(written, result)
                self.assertFalse(hasattr(candidate, "write_projection"))
                self.assertNotIn(
                    "value",
                    inspect.signature(candidate.project_and_write_candidate).parameters,
                )
                with self.assertRaises(TypeError):
                    candidate.project_and_write_candidate(
                        root,
                        source_manifest=source_path,
                        comparison=comparison_path,
                        source_audit=inputs["A.json"],
                        runtime_identity=inputs["I.json"],
                        conditional_go=inputs["G.json"],
                        evidence=evidence,
                        output=external / "forged.json",
                        value=copy.deepcopy(result),
                    )
                with self.assertRaisesRegex(candidate.CandidateProjectionError, "absent"):
                    candidate.project_and_write_candidate(
                        root,
                        source_manifest=source_path,
                        comparison=comparison_path,
                        source_audit=inputs["A.json"],
                        runtime_identity=inputs["I.json"],
                        conditional_go=inputs["G.json"],
                        evidence=evidence,
                        output=output,
                    )
                with self.assertRaisesRegex(candidate.CandidateProjectionError, "external"):
                    candidate.project_and_write_candidate(
                        root,
                        source_manifest=source_path,
                        comparison=comparison_path,
                        source_audit=inputs["A.json"],
                        runtime_identity=inputs["I.json"],
                        conditional_go=inputs["G.json"],
                        evidence=evidence,
                        output=root / "projection.json",
                    )
                with self.assertRaisesRegex(candidate.CandidateProjectionError, "external"):
                    candidate.project_candidate(
                        root,
                        source_manifest=root / "research/v4_measurement/authority_binding_contract.json",
                        comparison=comparison_path,
                        source_audit=inputs["A.json"],
                        runtime_identity=inputs["I.json"],
                        conditional_go=inputs["G.json"],
                        evidence=evidence,
                    )
                with patch.object(
                    candidate,
                    "analyze_measurement",
                    side_effect=candidate.V4MeasurementError("failed evidence"),
                ), self.assertRaisesRegex(candidate.CandidateProjectionError, "replay"):
                    candidate.project_candidate(
                        root,
                        source_manifest=source_path,
                        comparison=comparison_path,
                        source_audit=inputs["A.json"],
                        runtime_identity=inputs["I.json"],
                        conditional_go=inputs["G.json"],
                        evidence=evidence,
                    )
                with patch.object(
                    candidate,
                    "validate_authority_contract",
                    side_effect=candidate.V4ContractError("mutated authority"),
                ), self.assertRaisesRegex(candidate.CandidateProjectionError, "authority"):
                    candidate.project_candidate(
                        root,
                        source_manifest=source_path,
                        comparison=comparison_path,
                        source_audit=inputs["A.json"],
                        runtime_identity=inputs["I.json"],
                        conditional_go=inputs["G.json"],
                        evidence=evidence,
                    )

    def test_projection_schema_rejects_nested_prose_and_topology_mutations(self) -> None:
        root = Path(__file__).resolve().parents[1]
        mutations = (
            ("missing", lambda value: value.pop("cells")),
            ("extra", lambda value: value.update({"unexpected": True})),
            ("wrong-type", lambda value: value["diagnostics"][0].update({"query_nonblank": 1})),
            ("cardinality", lambda value: value.update({"diagnostics": value["diagnostics"][:-1]})),
            ("duplicate-pair", lambda value: value["paired_tclr_reductions"].__setitem__(-1, copy.deepcopy(value["paired_tclr_reductions"][0]))),
            ("topology", lambda value: value["topology"].update({"total_chats": 131})),
            ("split-bool-zero", lambda value: value["split_counts"].update({"development_trajectories": False})),
            ("topology-bool-positive", lambda value: value["topology"].update({"models": True})),
            ("pair-bool-positive", lambda value: value["paired_tclr_reductions"][0].update({"unrestricted_numerator": True})),
            ("v3-bool-zero", lambda value: value.update({"v3_included_count": False})),
            ("terminal-answer", lambda value: value["diagnostics"][0].update({"terminal_answer": "prose"})),
            ("go-prose", lambda value: value["paired_tclr_reductions"][0].update({"go_prose": "prose"})),
            ("source-audit", lambda value: value["cells"][0].update({"source_audit": "prose"})),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                value = self._projection(root)
                mutate(value)
                with self.assertRaises(candidate.CandidateProjectionError):
                    candidate._projection(value, root)

    def test_rejects_prohibited_evidence_topology(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "evidence"
            evidence.mkdir()
            (evidence / "failed").mkdir()
            with self.assertRaisesRegex(candidate.CandidateProjectionError, "topology"):
                candidate._closure(evidence)
            (evidence / "failed").rmdir()
            (evidence / "foreign").write_text("forbidden", encoding="utf-8")
            with self.assertRaisesRegex(candidate.CandidateProjectionError, "topology"):
                candidate._closure(evidence)

    def test_projection_source_has_no_v3_candidate_import(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "anachron/v4_candidate_common.py"
        ).read_text(encoding="utf-8").lower()
        self.assertNotIn("v3_candidate_common", source)
        self.assertNotIn("v3_measurement_candidate", source)


if __name__ == "__main__":
    unittest.main()
