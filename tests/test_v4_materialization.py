from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from anachron.v4_comparison import derive_bytes
from anachron.v4_contract import V4_GOVERNED_SOURCE_PATHS, canonical_json_bytes
from anachron.v4_measurement import (
    build_source_audit_packet,
    capture_runtime_identity,
    finalize_source_audit,
    run_measurement,
)
from anachron.v4_paths import V4PathError, admit_external_regular_input
from tools import build_v4_source_manifest
from tools.materialize_v4_inputs import V4MaterializationError, materialize


class V4MaterializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository_root = Path(__file__).resolve().parents[1]

    @staticmethod
    def _git(root: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _repository(
        self,
    ) -> tuple[tempfile.TemporaryDirectory[str], Path, Path, dict[str, str]]:
        temporary = tempfile.TemporaryDirectory()
        base = Path(temporary.name)
        origin = base / "origin.git"
        root = base / "repository"
        self._git(base, "init", "--bare", str(origin))
        shutil.copytree(
            self.repository_root,
            root,
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
        )
        self._git(root, "init")
        self._git(root, "config", "user.email", "test@example.com")
        self._git(root, "config", "user.name", "Test")
        self._git(root, "add", ".")
        self._git(root, "commit", "-m", "v3 source")
        v3_commit = self._git(root, "rev-parse", "HEAD")
        self._git(root, "tag", "-a", "v3-test", "-m", "v3")
        v3_tag_object = self._git(root, "rev-parse", "refs/tags/v3-test^{tag}")
        self._git(root, "remote", "add", "origin", str(origin))
        self._git(root, "push", "origin", "master", "refs/tags/v3-test")
        self._git(root, "checkout", "-b", "protocol/v4-recovery-v1")
        self._git(root, "commit", "--allow-empty", "-m", "v4 source")
        self._git(root, "tag", "-a", "v4-measurement-protocol-v2", "-m", "v4")
        self._git(
            root,
            "push",
            "origin",
            "protocol/v4-recovery-v1",
            "refs/tags/v4-measurement-protocol-v2",
        )
        self._git(root, "checkout", "--detach", "v4-measurement-protocol-v2")
        return temporary, root, origin, {
            "commit": v3_commit,
            "tag": "v3-test",
            "tag_object": v3_tag_object,
        }

    @staticmethod
    def _write(path: Path, value: object) -> Path:
        path.write_bytes(canonical_json_bytes(value))
        return path

    def test_materializes_external_inputs_and_binds_c_into_f(self) -> None:
        temporary, root, origin, expected_v3 = self._repository()
        with temporary:
            external = Path(temporary.name) / "external"
            external.mkdir()
            packet = external / "packet"
            receipt = build_source_audit_packet(
                root,
                packet,
                expected_origin=str(origin),
                expected_v3=expected_v3,
            )
            source = packet / "M.json"
            comparison = packet / "X.json"
            manifest = json.loads(source.read_text(encoding="utf-8"))
            self.assertEqual(
                receipt["source_manifest_sha256"],
                __import__("hashlib").sha256(source.read_bytes()).hexdigest(),
            )
            release = manifest["release"]
            files = manifest["governed_files"]
            template = json.loads(
                (root / "research/v4_measurement/source_audit.template.json").read_text(
                    encoding="utf-8"
                )
            )
            template.update(
                {
                    "audited_at_utc": "2026-09-04T00:00:00Z",
                    "comparison_projection_sha256": __import__("hashlib")
                    .sha256(comparison.read_bytes())
                    .hexdigest(),
                    "decision": "ACCEPT",
                    "registry_sha256": __import__("hashlib")
                    .sha256(
                        (
                            root / "research/v4_measurement/case_registry.json"
                        ).read_bytes()
                    )
                    .hexdigest(),
                    "registry_tag_blob_oid": files[
                        list(V4_GOVERNED_SOURCE_PATHS).index(
                            "research/v4_measurement/case_registry.json"
                        )
                    ]["tag_blob_oid"],
                    "registry_tag_blob_sha256": __import__("hashlib")
                    .sha256(
                        (
                            root / "research/v4_measurement/case_registry.json"
                        ).read_bytes()
                    )
                    .hexdigest(),
                    "source_manifest_sha256": __import__("hashlib")
                    .sha256(source.read_bytes())
                    .hexdigest(),
                    "v4_protocol_commit": release["tag_peeled"],
                    "v4_protocol_tag": release["tag"],
                    "v4_protocol_tag_object": release["tag_object"],
                }
            )
            registry = json.loads(
                (root / "research/v4_measurement/case_registry.json").read_text(
                    encoding="utf-8"
                )
            )
            for index, row in enumerate(template["case_audits"]):
                card = (
                    f"research/v4_measurement/{registry['cases'][index]['case_card']}"
                )
                row.update(
                    {
                        "decision": "ACCEPT",
                        "reason": f"Reviewed {row['case_id']} synthetic boundary.",
                        "reviewed_at_utc": f"2026-09-04T00:00:{index + 1:02d}Z",
                        "tag_blob_oid": files[
                            list(V4_GOVERNED_SOURCE_PATHS).index(card)
                        ]["tag_blob_oid"],
                        "tag_blob_sha256": __import__("hashlib")
                        .sha256((root / card).read_bytes())
                        .hexdigest(),
                    }
                )
            reviewed_audit = self._write(external / "reviewed-audit.json", template)
            audit = external / "audit.json"
            finalize_source_audit(root, reviewed_audit, source, comparison, audit)
            version = self._write(external / "version.json", {"version": "0.33.2"})
            models = [
                {"digest": "a" * 64, "name": "model-a"},
                {"digest": "b" * 64, "name": "model-b"},
            ]
            tags = self._write(
                external / "tags.json",
                {
                    "models": [
                        {
                            "capabilities": ["completion", "tools"],
                            "details": {
                                "context_length": 8192,
                                "embedding_length": 0,
                                "families": ["qwen2"],
                                "family": "qwen2",
                                "format": "gguf",
                                "parameter_size": "7B",
                                "parent_model": "",
                                "quantization_level": "Q4_K_M",
                            },
                            "digest": model["digest"],
                            "model": model["name"],
                            "modified_at": "2026-09-04T00:00:00.1234567+00:00",
                            "name": model["name"],
                            "size": 1,
                        }
                        for model in models
                    ]
                },
            )
            identity = external / "identity.json"
            capture_runtime_identity(root, version, tags, source, comparison, identity)
            receipt = materialize(
                root,
                source_manifest=source,
                comparison=comparison,
                source_audit=audit,
                runtime_identity=identity,
                output=external / "materialized",
                expected_origin=str(origin),
                expected_v3=expected_v3,
            )
            full = json.loads(
                (external / "materialized" / "full_plan.json").read_text(
                    encoding="utf-8"
                )
            )
            compatibility = (
                external / "materialized" / "compatibility_plan.json"
            ).read_bytes()
            self.assertEqual(
                full["compatibility"]["plan_sha256"],
                receipt["compatibility_plan_sha256"],
            )
            self.assertEqual(
                receipt["compatibility_plan_sha256"],
                __import__("hashlib").sha256(compatibility).hexdigest(),
            )

    def test_materialized_inputs_admit_to_runner_without_private_seam(self) -> None:
        temporary, root, origin, expected_v3 = self._repository()
        with temporary:
            external = Path(temporary.name) / "external"
            external.mkdir()
            source = external / "source.json"
            manifest = build_v4_source_manifest.build(
                root,
                source,
                expected_origin=str(origin),
                expected_v3=expected_v3,
            )
            comparison = self._write(
                external / "comparison.json",
                json.loads(
                    derive_bytes(
                        root,
                        v3_tag=manifest["release"]["v3_tag"],
                        v4_tag=manifest["release"]["tag"],
                    )
                ),
            )
            files = manifest["governed_files"]
            registry = json.loads(
                (root / "research/v4_measurement/case_registry.json").read_text(
                    encoding="utf-8"
                )
            )
            template = json.loads(
                (root / "research/v4_measurement/source_audit.template.json").read_text(
                    encoding="utf-8"
                )
            )
            template.update(
                {
                    "audited_at_utc": "2026-09-04T00:00:00Z",
                    "comparison_projection_sha256": __import__("hashlib")
                    .sha256(comparison.read_bytes())
                    .hexdigest(),
                    "decision": "ACCEPT",
                    "registry_sha256": __import__("hashlib")
                    .sha256(
                        (root / "research/v4_measurement/case_registry.json").read_bytes()
                    )
                    .hexdigest(),
                    "registry_tag_blob_oid": files[
                        list(V4_GOVERNED_SOURCE_PATHS).index(
                            "research/v4_measurement/case_registry.json"
                        )
                    ]["tag_blob_oid"],
                    "registry_tag_blob_sha256": __import__("hashlib")
                    .sha256(
                        (root / "research/v4_measurement/case_registry.json").read_bytes()
                    )
                    .hexdigest(),
                    "source_manifest_sha256": __import__("hashlib")
                    .sha256(source.read_bytes())
                    .hexdigest(),
                    "v4_protocol_commit": manifest["release"]["tag_peeled"],
                    "v4_protocol_tag": manifest["release"]["tag"],
                    "v4_protocol_tag_object": manifest["release"]["tag_object"],
                }
            )
            for index, row in enumerate(template["case_audits"]):
                card = f"research/v4_measurement/{registry['cases'][index]['case_card']}"
                row.update(
                    {
                        "decision": "ACCEPT",
                        "reason": f"Reviewed {row['case_id']} synthetic boundary.",
                        "reviewed_at_utc": f"2026-09-04T00:00:{index + 1:02d}Z",
                        "tag_blob_oid": files[
                            list(V4_GOVERNED_SOURCE_PATHS).index(card)
                        ]["tag_blob_oid"],
                        "tag_blob_sha256": __import__("hashlib")
                        .sha256((root / card).read_bytes())
                        .hexdigest(),
                    }
                )
            reviewed = self._write(external / "reviewed.json", template)
            audit = external / "audit.json"
            finalize_source_audit(root, reviewed, source, comparison, audit)
            version = self._write(external / "version.json", {"version": "0.33.2"})
            models = [
                {"digest": "a" * 64, "name": "model-a"},
                {"digest": "b" * 64, "name": "model-b"},
            ]
            tags = self._write(
                external / "tags.json",
                {
                    "models": [
                        {
                            "capabilities": ["completion", "tools"],
                            "details": {
                                "context_length": 8192,
                                "embedding_length": 0,
                                "families": ["qwen2"],
                                "family": "qwen2",
                                "format": "gguf",
                                "parameter_size": "7B",
                                "parent_model": "",
                                "quantization_level": "Q4_K_M",
                            },
                            "digest": model["digest"],
                            "model": model["name"],
                            "modified_at": "2026-09-04T00:00:00.1234567+00:00",
                            "name": model["name"],
                            "size": 1,
                        }
                        for model in models
                    ]
                },
            )
            identity = external / "identity.json"
            capture_runtime_identity(root, version, tags, source, comparison, identity)
            materialized = external / "materialized"
            materialize(
                root,
                source_manifest=source,
                comparison=comparison,
                source_audit=audit,
                runtime_identity=identity,
                output=materialized,
                expected_origin=str(origin),
                expected_v3=expected_v3,
            )
            compatibility = materialized / "compatibility_plan.json"
            full = materialized / "full_plan.json"
            go = json.loads(
                (root / "research/v4_measurement/conditional_go.template.json").read_text(
                    encoding="utf-8"
                )
            )
            go.update(
                {
                    "authorized_at_utc": "2026-09-04T00:01:00Z",
                    "comparison_projection_sha256": __import__("hashlib")
                    .sha256((materialized / "comparison.json").read_bytes())
                    .hexdigest(),
                    "compatibility_plan_sha256": __import__("hashlib")
                    .sha256(compatibility.read_bytes())
                    .hexdigest(),
                    "decision": "GO",
                    "full_plan_sha256": __import__("hashlib")
                    .sha256(full.read_bytes())
                    .hexdigest(),
                    "model_digests": [model["digest"] for model in models],
                    "protocol_commit": manifest["release"]["tag_peeled"],
                    "protocol_tag_object": manifest["release"]["tag_object"],
                    "registry_sha256": __import__("hashlib")
                    .sha256(
                        (root / "research/v4_measurement/case_registry.json").read_bytes()
                    )
                    .hexdigest(),
                    "runtime_identity_sha256": __import__("hashlib")
                    .sha256(identity.read_bytes())
                    .hexdigest(),
                    "source_audit_sha256": __import__("hashlib")
                    .sha256(audit.read_bytes())
                    .hexdigest(),
                    "source_manifest_sha256": __import__("hashlib")
                    .sha256((materialized / "source_manifest.json").read_bytes())
                    .hexdigest(),
                    "v3_included_count": 0,
                }
            )
            go_path = self._write(materialized / "go.json", go)
            calls = [0]

            def transport(
                _endpoint: str, path: str, payload: bytes | None, _timeout: int
            ) -> bytes:
                calls[0] += 1
                if path == "/api/version":
                    return version.read_bytes()
                if path == "/api/tags":
                    return tags.read_bytes()
                request = json.loads(payload.decode("utf-8"))
                native = {
                    "created_at": "2026-09-04T00:00:00.12345678Z",
                    "done": True,
                    "done_reason": "stop",
                    "eval_count": 1,
                    "eval_duration": 1,
                    "load_duration": 1,
                    "model": request["model"],
                    "prompt_eval_count": 1,
                    "prompt_eval_duration": 1,
                    "total_duration": 1,
                }
                if "tools" in request:
                    return canonical_json_bytes(
                        {
                            **native,
                            "message": {
                                "content": "",
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "function": {
                                            "arguments": {"query": "synthetic bulletin"},
                                            "index": 0,
                                            "name": "anachron_search",
                                        },
                                    }
                                ],
                            },
                        }
                    )
                return canonical_json_bytes(
                    {
                        **native,
                        "message": {
                            "content": "answer excluded from projection",
                            "role": "assistant",
                        },
                    }
                )

            preflight = external / "preflight"
            result = run_measurement(
                full,
                go_path,
                audit,
                identity,
                preflight,
                repository_root=root,
                compatibility_plan=compatibility,
                comparison=materialized / "comparison.json",
                source_manifest=materialized / "source_manifest.json",
                transport=transport,
                preflight_only=True,
                expected_source_origin=str(origin),
                expected_v3=expected_v3,
            )
            self.assertTrue(result["preflight_only"])
            self.assertEqual(calls[0], 0)
            self.assertFalse(preflight.exists())
            projection = run_measurement(
                full,
                go_path,
                audit,
                identity,
                external / "evidence",
                repository_root=root,
                compatibility_plan=compatibility,
                comparison=materialized / "comparison.json",
                source_manifest=materialized / "source_manifest.json",
                transport=transport,
                expected_source_origin=str(origin),
                expected_v3=expected_v3,
            )
            self.assertEqual(projection["topology"]["total_chats"], 132)
            self.assertEqual(calls[0], 138)

    @unittest.skipUnless(os.name == "nt", "junctions are Windows-specific")
    def test_external_input_rejects_windows_junction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            repository.mkdir()
            external = base / "external"
            external.mkdir()
            (external / "input.json").write_text("{}\n", encoding="utf-8")
            junction = base / "junction"
            result = subprocess.run(
                ["cmd", "/d", "/c", "mklink", "/J", str(junction), str(external)],
                capture_output=True,
                check=False,
                text=True,
            )
            if result.returncode:
                self.skipTest("Windows junction creation unavailable")
            try:
                with self.assertRaises(V4PathError):
                    admit_external_regular_input(
                        junction / "input.json", repository, "input"
                    )
            finally:
                junction.rmdir()

    def test_materialization_rejects_internal_or_existing_output(self) -> None:
        temporary, root, _, _ = self._repository()
        with temporary, self.assertRaises(V4MaterializationError):
            materialize(
                root,
                source_manifest=root / "research/v4_measurement/case_registry.json",
                comparison=root / "research/v4_measurement/case_registry.json",
                source_audit=root / "research/v4_measurement/case_registry.json",
                runtime_identity=root / "research/v4_measurement/case_registry.json",
                output=root / "output",
            )


if __name__ == "__main__":
    unittest.main()
