from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from anachron import v4_contract
from tools import render_v4_contract_docs, validate_v4_contract


class V4AuthorityContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository_root = Path(__file__).resolve().parents[1]

    def _copy_repository(self) -> tempfile.TemporaryDirectory[str]:
        temporary = tempfile.TemporaryDirectory()
        shutil.copytree(
            self.repository_root,
            Path(temporary.name) / "repository",
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
        )
        return temporary

    @staticmethod
    def _canonical_write(path: Path, value: object) -> None:
        path.write_bytes(v4_contract.canonical_json_bytes(value))

    def test_current_contract_and_document_cli_checks_pass(self) -> None:
        result = v4_contract.validate_authority_contract(self.repository_root)
        self.assertEqual(result["kind"], "anachron-v4-authority-binding-contract")
        self.assertEqual(validate_v4_contract.main(["--repository-root", str(self.repository_root)]), 0)
        self.assertEqual(render_v4_contract_docs.main(["--repository-root", str(self.repository_root), "--check"]), 0)

    def test_operator_route_and_self_custody_language_are_exact(self) -> None:
        readme = (
            self.repository_root / "research/v4_measurement/README.md"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            re.findall(r"python -m (tools\.[a-z0-9_]+)", readme),
            [
                "tools.validate_v4_contract",
                "tools.build_v4_source_audit_ui",
                "tools.finalize_v4_source_audit",
                "tools.capture_v4_runtime_identity",
                "tools.materialize_v4_inputs",
                "tools.run_v4_recovery",
                "tools.analyze_v4_measurement",
            ],
        )
        deprecated_alias = "tools/analyze_v4_" + "compatibility.py"
        self.assertFalse((self.repository_root / deprecated_alias).exists())
        self.assertNotIn(deprecated_alias, v4_contract.V4_GOVERNED_SOURCE_PATHS)

        limitation = (
            "Self-custody evidence supports internal consistency and byte replay; it "
            "detects missing, partial, malformed, or inconsistent artifacts, including "
            "re-signed artifacts without corresponding authority. It provides no "
            "independent raw-response provenance and cannot detect a coherent rewrite of "
            "every locally held artifact."
        )
        forbidden_claim = "detects a coherent rewrite of every locally held artifact"
        for relative in (
            "research/v4_measurement/PROTOCOL.md",
            "research/v4_measurement/README.md",
            "research/v4_measurement/ACCEPTANCE_MATRIX.md",
            "research/v4_measurement/CLAIM_EVIDENCE_MAP.md",
            "paper/v4_measurement/CANDIDATE_ACCEPTANCE_MATRIX.md",
            "paper/v4_measurement/CANDIDATE_CLAIM_EVIDENCE_MAP.md",
            "paper/v4_measurement/candidate_manuscript_template.json",
            "paper/v4_measurement/CANDIDATE_SUBMISSION_METADATA.md",
            "paper/v4_measurement/candidate_contract.json",
        ):
            content = (self.repository_root / relative).read_text(encoding="utf-8")
            normalized = re.sub(r"\s+", " ", content)
            self.assertIn(limitation, normalized, relative)
            self.assertNotRegex(
                normalized,
                rf"(?<!cannot ){re.escape(forbidden_claim)}",
                relative,
            )
        retired_patterns = ("v3 exclusion record", "v3_exclusion_record")
        for directory in ("research/v4_measurement", "paper/v4_measurement"):
            for path in (self.repository_root / directory).rglob("*.md"):
                content = path.read_text(encoding="utf-8").lower()
                for pattern in retired_patterns:
                    self.assertNotIn(pattern, content, str(path))

    def test_duplicate_key_and_type_aliases_fail_before_graph_equality(self) -> None:
        with self._copy_repository() as temporary:
            root = Path(temporary) / "repository"
            contract = root / "research/v4_measurement/authority_binding_contract.json"
            raw = contract.read_text(encoding="utf-8")
            contract.write_text(raw.replace('"kind":', '"kind": "forged",\n  "kind":', 1), encoding="utf-8")
            with self.assertRaisesRegex(v4_contract.V4ContractError, "duplicate"):
                v4_contract.validate_authority_contract(root)

        with self._copy_repository() as temporary:
            root = Path(temporary) / "repository"
            contract = root / "research/v4_measurement/authority_binding_contract.json"
            body = json.loads(contract.read_text(encoding="utf-8"))
            body["schema_version"] = True
            self._canonical_write(contract, body)
            with self.assertRaises(v4_contract.V4ContractError):
                v4_contract.validate_authority_contract(root)

    def test_raw_target_forgery_fails_even_when_dependents_share_the_new_hash(self) -> None:
        with self._copy_repository() as temporary:
            root = Path(temporary) / "repository"
            matrix = root / "research/v4_measurement/ACCEPTANCE_MATRIX.md"
            matrix.write_text(matrix.read_text(encoding="utf-8") + "\nforged\n", encoding="utf-8")
            forged_matrix_hash = hashlib.sha256(matrix.read_bytes()).hexdigest()
            contract_path = root / "research/v4_measurement/authority_binding_contract.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["protocol_matrix"]["sha256"] = forged_matrix_hash
            self._canonical_write(contract_path, contract)
            for relative in (
                "research/v4_measurement/compatibility_plan.template.json",
                "research/v4_measurement/full_plan.template.json",
                "research/v4_measurement/conditional_go.template.json",
            ):
                path = root / relative
                value = json.loads(path.read_text(encoding="utf-8"))
                value["acceptance_matrix_sha256"] = forged_matrix_hash
                self._canonical_write(path, value)
            candidate = root / "paper/v4_measurement/candidate_contract.json"
            candidate_value = json.loads(candidate.read_text(encoding="utf-8"))
            candidate_value["protocol_matrix"]["sha256"] = forged_matrix_hash
            self._canonical_write(candidate, candidate_value)
            with self.assertRaisesRegex(v4_contract.V4ContractError, "authority binding contract SHA-256"):
                v4_contract.validate_authority_contract(root)

    def test_every_authority_dependency_edge_and_shared_field_rejects_mutation(self) -> None:
        mutations = (
            ("research/v4_measurement/compatibility_plan.template.json", ("authority_binding_contract_sha256",), "0" * 64),
            ("research/v4_measurement/compatibility_plan.template.json", ("acceptance_matrix_sha256",), "0" * 64),
            ("research/v4_measurement/full_plan.template.json", ("authority_binding_contract_sha256",), "0" * 64),
            ("research/v4_measurement/full_plan.template.json", ("acceptance_matrix_sha256",), "0" * 64),
            ("research/v4_measurement/conditional_go.template.json", ("authority_binding_contract_sha256",), "0" * 64),
            ("research/v4_measurement/conditional_go.template.json", ("acceptance_matrix_sha256",), "0" * 64),
            ("paper/v4_measurement/candidate_contract.json", ("authority_binding_contract", "sha256"), "0" * 64),
            ("research/v4_measurement/full_plan.template.json", ("compatibility", "plan_sha256"), "0" * 64),
            ("research/v4_measurement/conditional_go.template.json", ("compatibility_plan_sha256",), "0" * 64),
            ("research/v4_measurement/conditional_go.template.json", ("full_plan_sha256",), "0" * 64),
            ("paper/v4_measurement/candidate_contract.json", ("compatibility_plan", "sha256"), "0" * 64),
            ("paper/v4_measurement/candidate_contract.json", ("full_plan", "sha256"), "0" * 64),
            ("paper/v4_measurement/candidate_contract.json", ("protocol_matrix", "sha256"), "0" * 64),
            ("paper/v4_measurement/candidate_contract.json", ("resource_policy", "candidate_projection_max_bytes"), 0),
            ("paper/v4_measurement/candidate_contract.json", ("tectonic", "version"), "forged"),
            ("paper/v4_measurement/candidate_contract.json", ("authority_graph", "actual_go", "required_runtime_binding", "field"), "forged"),
            ("research/v4_measurement/full_plan.template.json", ("v3_included_count",), True),
            ("research/v4_measurement/compatibility_plan.template.json", ("authority_binding_contract_sha256",), True),
            ("research/v4_measurement/compatibility_plan.template.json", ("acceptance_matrix_sha256",), True),
            ("research/v4_measurement/full_plan.template.json", ("authority_binding_contract_sha256",), True),
            ("research/v4_measurement/full_plan.template.json", ("acceptance_matrix_sha256",), True),
            ("research/v4_measurement/full_plan.template.json", ("compatibility", "plan_sha256"), True),
            ("research/v4_measurement/conditional_go.template.json", ("authority_binding_contract_sha256",), True),
            ("research/v4_measurement/conditional_go.template.json", ("acceptance_matrix_sha256",), True),
            ("research/v4_measurement/conditional_go.template.json", ("compatibility_plan_sha256",), True),
            ("research/v4_measurement/conditional_go.template.json", ("full_plan_sha256",), True),
            ("paper/v4_measurement/candidate_contract.json", ("authority_binding_contract", "sha256"), True),
            ("paper/v4_measurement/candidate_contract.json", ("compatibility_plan", "sha256"), True),
            ("paper/v4_measurement/candidate_contract.json", ("full_plan", "sha256"), True),
            ("paper/v4_measurement/candidate_contract.json", ("protocol_matrix", "sha256"), True),
            ("paper/v4_measurement/candidate_contract.json", ("resource_policy", "candidate_projection_max_bytes"), True),
            ("paper/v4_measurement/candidate_contract.json", ("tectonic", "version"), True),
            ("paper/v4_measurement/candidate_contract.json", ("authority_graph", "actual_go", "required_runtime_binding", "field"), True),
        )
        for relative, path_keys, replacement in mutations:
            with self.subTest(relative=relative, path_keys=path_keys), self._copy_repository() as temporary:
                root = Path(temporary) / "repository"
                path = root / relative
                value = json.loads(path.read_text(encoding="utf-8"))
                target = value
                for key in path_keys[:-1]:
                    target = target[key]
                target[path_keys[-1]] = replacement
                self._canonical_write(path, value)
                with self.assertRaises(v4_contract.V4ContractError):
                    v4_contract.validate_authority_contract(root)

    def test_stale_authority_blocks_fail_check_and_no_transport_capability_exists(self) -> None:
        with self._copy_repository() as temporary:
            root = Path(temporary) / "repository"
            protocol = root / "research/v4_measurement/PROTOCOL.md"
            protocol.write_text(protocol.read_text(encoding="utf-8").replace("authority-binding contract", "stale authority-binding contract", 1), encoding="utf-8")
            with self.assertRaisesRegex(v4_contract.V4ContractError, "authority block"):
                v4_contract.validate_authority_contract(root)
            self.assertEqual(render_v4_contract_docs.main(["--repository-root", str(root), "--check"]), 1)

        for relative in ("anachron/v4_contract.py", "tools/validate_v4_contract.py", "tools/render_v4_contract_docs.py"):
            source = (self.repository_root / relative).read_text(encoding="utf-8")
            for forbidden in ("urllib", "socket", "http", "subprocess", "ollama"):
                self.assertNotIn(forbidden, source.lower())

    def test_candidate_matrix_requires_only_the_canonical_three_carrier_equality(self) -> None:
        with self._copy_repository() as temporary:
            root = Path(temporary) / "repository"
            matrix = root / "paper/v4_measurement/CANDIDATE_ACCEPTANCE_MATRIX.md"
            content = matrix.read_text(encoding="utf-8")
            self.assertIn(v4_contract._CANONICAL_MATRIX_EQUALITY, content)
            self.assertNotIn(v4_contract._LEGACY_TWO_CARRIER_WORDING, content)
            matrix.write_text(
                content.replace(
                    v4_contract._CANONICAL_MATRIX_EQUALITY,
                    v4_contract._LEGACY_TWO_CARRIER_WORDING,
                    1,
                ),
                encoding="utf-8",
            )
            candidate = root / "paper/v4_measurement/candidate_contract.json"
            candidate_value = json.loads(candidate.read_text(encoding="utf-8"))
            candidate_value["static_artifact_hashes"][
                "paper/v4_measurement/CANDIDATE_ACCEPTANCE_MATRIX.md"
            ] = hashlib.sha256(matrix.read_bytes()).hexdigest()
            self._canonical_write(candidate, candidate_value)
            with self.assertRaisesRegex(v4_contract.V4ContractError, "canonical three-carrier"):
                v4_contract.validate_authority_contract(root)
            self.assertEqual(render_v4_contract_docs.main(["--repository-root", str(root), "--check"]), 1)

    def test_static_paper_closure_and_future_entry_points_are_outcome_free(self) -> None:
        candidate_path = self.repository_root / "paper/v4_measurement/candidate_contract.json"
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        static_hashes = candidate["static_artifact_hashes"]
        self.assertEqual(
            tuple(static_hashes),
            v4_contract.V4_CANDIDATE_STATIC_ARTIFACT_PATHS,
        )
        self.assertEqual(
            candidate["archive_allowlist_sha256"],
            static_hashes["paper/v4_measurement/archive_allowlist.json"],
        )
        self.assertEqual(
            candidate["source_manifest_sha256"],
            v4_contract.SOURCE_MANIFEST_PLACEHOLDER,
        )
        self.assertEqual(
            candidate["comparison_projection_sha256"],
            "REPLACE_AFTER_PROTOCOL_FREEZE",
        )
        self.assertNotIn("conditional_go_sha256", candidate)
        self.assertEqual(
            candidate["protocol_identity"],
            {
                "commit": "REPLACE_WITH_FROZEN_PEELED_COMMIT",
                "tag": "v4-measurement-protocol-v2",
                "tag_object": "REPLACE_WITH_ANNOTATED_TAG_OBJECT",
            },
        )
        self.assertTrue(
            set(v4_contract.V4_CANDIDATE_STATIC_ARTIFACT_PATHS).issubset(
                v4_contract.V4_GOVERNED_SOURCE_PATHS
            )
        )
        self.assertEqual(
            v4_contract.V4_GOVERNED_SOURCE_PATHS,
            tuple(sorted(v4_contract.V4_GOVERNED_SOURCE_PATHS)),
        )
        for relative in v4_contract.V4_GOVERNED_SOURCE_PATHS:
            self.assertTrue((self.repository_root / relative).is_file(), relative)
        for relative in (
            "anachron/v4_candidate_common.py",
            "tools/build_v4_measurement_candidate_paper.py",
            "tools/verify_v4_measurement_candidate_reviews.py",
            "tools/release_v4_measurement_candidate.py",
        ):
            source = (self.repository_root / relative).read_text(encoding="utf-8").lower()
            forbidden = {
                "v3_candidate_common",
                "urllib",
                "socket",
                "http",
                "ollama",
                "requests",
                "smtplib",
            }
            if relative != "tools/build_v4_measurement_candidate_paper.py":
                forbidden.add("subprocess")
            for forbidden_word in forbidden:
                self.assertNotIn(forbidden_word, source, relative)

    def test_static_artifact_mutation_or_receipt_schema_drift_fails(self) -> None:
        with self._copy_repository() as temporary:
            root = Path(temporary) / "repository"
            manuscript = root / "paper/v4_measurement/candidate_manuscript_template.json"
            value = json.loads(manuscript.read_text(encoding="utf-8"))
            value["title"] = "forged"
            self._canonical_write(manuscript, value)
            with self.assertRaisesRegex(v4_contract.V4ContractError, "static artifact"):
                v4_contract.validate_authority_contract(root)

        with self._copy_repository() as temporary:
            root = Path(temporary) / "repository"
            candidate = root / "paper/v4_measurement/candidate_contract.json"
            value = json.loads(candidate.read_text(encoding="utf-8"))
            value["dynamic_receipt_schema"]["candidate_receipt"].remove(
                "actual_go_sha256"
            )
            self._canonical_write(candidate, value)
            with self.assertRaisesRegex(v4_contract.V4ContractError, "receipt schema"):
                v4_contract.validate_authority_contract(root)

    def test_path_admission_rejects_symlink_parent_root_and_traversal(self) -> None:
        with self._copy_repository() as temporary:
            root = Path(temporary) / "repository"
            target = root / "research"
            link = root / "linked_research"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink creation unavailable: {error}")
            with self.assertRaisesRegex(v4_contract.V4ContractError, "reparse"):
                v4_contract._read_file(root, "linked_research/v4_measurement/PROTOCOL.md", "linked target")

            parent_link = Path(temporary) / "linked_parent"
            parent_link.symlink_to(Path(temporary), target_is_directory=True)
            with self.assertRaisesRegex(v4_contract.V4ContractError, "reparse"):
                v4_contract.validate_authority_contract(parent_link / "repository")

            root_link = Path(temporary) / "root_link"
            root_link.symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(v4_contract.V4ContractError, "reparse"):
                v4_contract.validate_authority_contract(root_link)

            outside = Path(temporary) / "outside.json"
            outside.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(v4_contract.V4ContractError, "canonical relative"):
                v4_contract._read_file(root, "../outside.json", "traversal target")

    @unittest.skipUnless(os.name == "nt", "junctions are Windows-specific")
    def test_path_admission_rejects_windows_junction_parent(self) -> None:
        with self._copy_repository() as temporary:
            root = Path(temporary) / "repository"
            junction = root / "junction_research"
            root_junction = Path(temporary) / "junction_root"
            parent_junction = Path(temporary) / "junction_parent"
            try:
                for link, target in (
                    (junction, root / "research"),
                    (root_junction, root),
                    (parent_junction, Path(temporary)),
                ):
                    result = subprocess.run(
                        ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
                        capture_output=True,
                        check=False,
                        text=True,
                    )
                    if result.returncode != 0:
                        self.skipTest("Windows junction creation unavailable")
                with self.assertRaisesRegex(v4_contract.V4ContractError, "reparse"):
                    v4_contract._read_file(root, "junction_research/v4_measurement/PROTOCOL.md", "junction target")
                with self.assertRaisesRegex(v4_contract.V4ContractError, "reparse"):
                    v4_contract.validate_authority_contract(root_junction)
                with self.assertRaisesRegex(v4_contract.V4ContractError, "reparse"):
                    v4_contract.validate_authority_contract(parent_junction / "repository")
            finally:
                for link in (junction, root_junction, parent_junction):
                    if link.exists():
                        link.rmdir()


if __name__ == "__main__":
    unittest.main()
