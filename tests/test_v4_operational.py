from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anachron.data.v4_registry import eligible_records, load_v4_registry
from anachron.v4_comparison import derive_bytes
from anachron.v4_contract import (
    V4_GOVERNED_SOURCE_PATHS,
    V4ContractError,
    canonical_json_bytes,
)
from anachron.v4_measurement import (
    _MAX_RESPONSE_BYTES,
    _RESPONSE_PREFIX_BYTES,
    V4MeasurementError,
    _projection,
    _ResponseBudget,
    _ResponseLimitError,
    _Writer,
    analyze_compatibility,
    analyze_measurement,
    capture_runtime_identity,
    finalize_source_audit,
    run_measurement,
    validate_tool_arguments,
)
from tools import (
    analyze_v4_measurement,
    build_v4_source_audit_ui,
    build_v4_source_manifest,
    capture_v4_runtime_identity,
    finalize_v4_source_audit,
    run_v4_recovery,
)
from tools.materialize_v4_inputs import materialize

_OFFICIAL_ORIGIN = "https://github.com/LesterALeong/anachron.git"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> Path:
    path.write_bytes(canonical_json_bytes(value))
    return path


def _reseal_failure(evidence: Path) -> None:
    controls = {
        "failure_manifest.json",
        "failure_manifest.sha256",
        "failure_receipt.json",
    }
    files = sorted(
        path
        for path in evidence.rglob("*")
        if path.is_file() and path.relative_to(evidence).as_posix() not in controls
    )
    manifest = {
        "files": [
            {
                "path": path.relative_to(evidence).as_posix(),
                "sha256": _sha(path),
            }
            for path in files
        ],
        "schema_version": "anachron-v4-failure-manifest-v2",
    }
    _write(evidence / "failure_manifest.json", manifest)
    receipt = json.loads((evidence / "failure_receipt.json").read_text(encoding="utf-8"))
    receipt["failure_manifest_sha256"] = _sha(evidence / "failure_manifest.json")
    _write(evidence / "failure_receipt.json", receipt)
    (evidence / "failure_manifest.sha256").write_bytes(
        f"{receipt['failure_manifest_sha256']}  failure_manifest.json\n".encode()
    )


class _DisposableAuthorityFixture:
    """One immutable detached release and materialized external authority packet."""

    def __init__(self, source_root: Path) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.base = Path(self._temporary.name)
        self.origin = self.base / "origin.git"
        self.root = self.base / "repository"
        self.external = self.base / "external"
        self._git(self.base, "init", "--bare", str(self.origin))
        shutil.copytree(source_root, self.root, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
        self._git(self.root, "init")
        self._git(self.root, "config", "user.email", "test@example.com")
        self._git(self.root, "config", "user.name", "V4 Operational Test")
        self._git(self.root, "add", ".")
        self._git(self.root, "commit", "-m", "v3 source")
        v3_commit = self._git(self.root, "rev-parse", "HEAD")
        self._git(self.root, "tag", "-a", "v3-measurement-protocol-v1", "-m", "v3")
        self.expected_v3 = {"commit": v3_commit, "tag": "v3-measurement-protocol-v1", "tag_object": self._git(self.root, "rev-parse", "refs/tags/v3-measurement-protocol-v1^{tag}")}
        self._git(self.root, "remote", "add", "origin", _OFFICIAL_ORIGIN)
        self._git(self.root, "config", f"url.{self.origin.as_posix()}.insteadOf", _OFFICIAL_ORIGIN)
        self._git(self.root, "push", "origin", "master", "refs/tags/v3-measurement-protocol-v1")
        self._git(self.root, "checkout", "-b", "protocol/v4-recovery-v1")
        self._git(self.root, "commit", "--allow-empty", "-m", "v4 source")
        self._git(self.root, "tag", "-a", "v4-measurement-protocol-v1", "-m", "v4")
        self._git(self.root, "push", "origin", "protocol/v4-recovery-v1", "refs/tags/v4-measurement-protocol-v1")
        self._git(self.root, "checkout", "--detach", "v4-measurement-protocol-v1")
        self.external.mkdir()
        self.packet = self._materialize_packet()

    @staticmethod
    def _git(root: Path, *arguments: str) -> str:
        return subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True, text=True).stdout.strip()

    def close(self) -> None:
        self._temporary.cleanup()

    @staticmethod
    def _models() -> list[dict[str, str]]:
        return [{"digest": "a" * 64, "name": "model-a"}, {"digest": "b" * 64, "name": "model-b"}]

    def _tags(self) -> dict[str, object]:
        return {"models": [{"capabilities": ["completion", "tools"], "details": {"context_length": 8192, "embedding_length": 0, "families": ["qwen2"], "family": "qwen2", "format": "gguf", "parameter_size": "7B", "parent_model": "", "quantization_level": "Q4_K_M"}, "digest": model["digest"], "model": model["name"], "modified_at": "2026-09-04T00:00:00.1234567+00:00", "name": model["name"], "size": 1} for model in self._models()]}

    def _materialize_packet(self) -> Path:
        source = self.external / "M.json"
        manifest = build_v4_source_manifest.build(self.root, source, expected_v3=self.expected_v3)
        comparison = self.external / "X.json"
        comparison.write_bytes(derive_bytes(self.root, v3_tag=manifest["release"]["v3_tag"], v4_tag=manifest["release"]["tag"]))
        registry = json.loads((self.root / "research/v4_measurement/case_registry.json").read_text(encoding="utf-8"))
        reviewed = json.loads((self.root / "research/v4_measurement/source_audit.template.json").read_text(encoding="utf-8"))
        files = manifest["governed_files"]
        reviewed.update({"audited_at_utc": "2026-09-04T00:00:00Z", "comparison_projection_sha256": _sha(comparison), "decision": "ACCEPT", "registry_sha256": _sha(self.root / "research/v4_measurement/case_registry.json"), "registry_tag_blob_oid": files[list(V4_GOVERNED_SOURCE_PATHS).index("research/v4_measurement/case_registry.json")]["tag_blob_oid"], "registry_tag_blob_sha256": _sha(self.root / "research/v4_measurement/case_registry.json"), "source_manifest_sha256": _sha(source), "v4_protocol_commit": manifest["release"]["tag_peeled"], "v4_protocol_tag": manifest["release"]["tag"], "v4_protocol_tag_object": manifest["release"]["tag_object"]})
        for index, row in enumerate(reviewed["case_audits"]):
            card = f"research/v4_measurement/{registry['cases'][index]['case_card']}"
            row.update({"decision": "ACCEPT", "reason": f"Reviewed {row['case_id']} synthetic boundary.", "reviewed_at_utc": f"2026-09-04T00:00:{index + 1:02d}Z", "tag_blob_oid": files[list(V4_GOVERNED_SOURCE_PATHS).index(card)]["tag_blob_oid"], "tag_blob_sha256": _sha(self.root / card)})
        reviewed_path = _write(self.external / "reviewed-audit.json", reviewed)
        audit = self.external / "A.json"
        finalize_source_audit(self.root, reviewed_path, source, comparison, audit)
        version = _write(self.external / "version.json", {"version": "0.33.2"})
        tags = _write(self.external / "tags.json", self._tags())
        identity = self.external / "I.json"
        capture_runtime_identity(self.root, version, tags, source, comparison, identity)
        packet = self.external / "packet"
        materialize(self.root, source_manifest=source, comparison=comparison, source_audit=audit, runtime_identity=identity, output=packet, expected_v3=self.expected_v3)
        compatibility = packet / "compatibility_plan.json"
        full = packet / "full_plan.json"
        go = json.loads((self.root / "research/v4_measurement/conditional_go.template.json").read_text(encoding="utf-8"))
        go.update({"authorized_at_utc": "2026-09-04T00:01:00Z", "comparison_projection_sha256": _sha(packet / "comparison.json"), "compatibility_plan_sha256": _sha(compatibility), "decision": "GO", "full_plan_sha256": _sha(full), "model_digests": [model["digest"] for model in self._models()], "protocol_commit": manifest["release"]["tag_peeled"], "protocol_tag_object": manifest["release"]["tag_object"], "registry_sha256": _sha(self.root / "research/v4_measurement/case_registry.json"), "runtime_identity_sha256": _sha(identity), "source_audit_sha256": _sha(audit), "source_manifest_sha256": _sha(packet / "source_manifest.json"), "v3_included_count": 0})
        _write(packet / "G.json", go)
        return packet

    def inputs(self, name: str) -> dict[str, Path]:
        destination = self.external / "runs" / name
        destination.mkdir(parents=True)
        names = {"compatibility": "compatibility_plan.json", "full": "full_plan.json", "go": "G.json", "audit": "A.json", "identity": "I.json", "manifest": "source_manifest.json", "comparison": "comparison.json"}
        packet_paths = {"audit": self.external / "A.json", "identity": self.external / "I.json"}
        result = {}
        for key, filename in names.items():
            target = destination / filename
            shutil.copyfile(packet_paths.get(key, self.packet / filename), target)
            result[key] = target
        result["output"] = destination / "evidence"
        return result

    def transport(self, counter: list[int], *, failure_at: int | None = None, drift: bool = False, identity_failure: str | None = None):
        version = (self.external / "version.json").read_bytes()
        tags = (self.external / "tags.json").read_bytes()
        identity_calls = [0]
        def transport(_endpoint: str, path: str, payload: bytes | None, _timeout: int) -> bytes:
            if path == "/api/version":
                identity_calls[0] += 1
                if identity_failure == f"{identity_calls[0]}:version":
                    raise RuntimeError("injected identity version failure")
                return version
            if path == "/api/tags":
                identity_calls[0] += 1
                if identity_failure == f"{identity_calls[0]}:tags":
                    raise RuntimeError("injected identity tags failure")
                if drift:
                    changed = json.loads(tags.decode("utf-8"))
                    changed["models"][0]["digest"] = "d" * 64
                    return canonical_json_bytes(changed)
                return tags
            counter[0] += 1
            if failure_at == counter[0]:
                raise RuntimeError("injected transport failure")
            assert payload is not None
            request = json.loads(payload.decode("utf-8"))
            native = {"created_at": "2026-09-04T00:00:00.12345678Z", "done": True, "done_reason": "stop", "eval_count": 1, "eval_duration": 1, "load_duration": 1, "model": request["model"], "prompt_eval_count": 1, "prompt_eval_duration": 1, "total_duration": 1}
            if "tools" in request:
                return canonical_json_bytes({**native, "message": {"content": "", "role": "assistant", "tool_calls": [{"id": "call-1", "function": {"arguments": {"query": "synthetic bulletin"}, "index": 0, "name": "anachron_search"}}]}})
            return canonical_json_bytes({**native, "message": {"content": "answer excluded from projection", "role": "assistant"}})
        return transport


class V4OperationalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository_root = Path(__file__).resolve().parents[1]
        cls.fixture = _DisposableAuthorityFixture(cls.repository_root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close()

    def _run(self, inputs: dict[str, Path], transport, *, preflight_only: bool = False):
        return run_measurement(inputs["full"], inputs["go"], inputs["audit"], inputs["identity"], inputs["output"], repository_root=self.fixture.root, compatibility_plan=inputs["compatibility"], comparison=inputs["comparison"], source_manifest=inputs["manifest"], transport=transport, preflight_only=preflight_only, expected_v3=self.fixture.expected_v3)

    def test_registry_and_date_contracts_are_strict(self) -> None:
        registry, cards = load_v4_registry(self.fixture.root)
        self.assertEqual(registry["case_count"], 8)
        card = cards["fin-aster-2020-06-future"]
        self.assertEqual(validate_tool_arguments({"query": " x "}, card), "x")
        self.assertEqual(validate_tool_arguments({"query": "x", "date": card["as_of"]}, card), "x")
        self.assertNotEqual(eligible_records(card, "unrestricted", "contract"), eligible_records(card, "unrestricted", "revenue"))
        with self.assertRaises(V4MeasurementError):
            validate_tool_arguments({"query": "x", "date": "2020-06-16"}, card)
        with self.assertRaisesRegex(
            V4ContractError, "repository root cannot be inspected safely"
        ):
            load_v4_registry(self.fixture.root / "missing")

    def test_public_run_and_replay_have_exact_132_chat_campaign(self) -> None:
        inputs = self.fixture.inputs("success")
        counter = [0]
        projection = self._run(inputs, self.fixture.transport(counter))
        self.assertEqual(counter[0], 132)
        self.assertEqual(projection["topology"]["total_chats"], 132)
        self.assertEqual(len(projection["paired_tclr_reductions"]), 32)
        self.assertEqual(analyze_compatibility(inputs["output"], repository_root=self.fixture.root)["chat_count"], 4)
        self.assertEqual(analyze_measurement(inputs["output"], repository_root=self.fixture.root), projection)

    def test_each_of_132_chat_failures_is_fail_closed(self) -> None:
        for failure_at in range(1, 133):
            with self.subTest(failure_at=failure_at):
                inputs = self.fixture.inputs(f"failure-{failure_at:03d}")
                counter = [0]
                with self.assertRaises(V4MeasurementError):
                    self._run(inputs, self.fixture.transport(counter, failure_at=failure_at))
                self.assertEqual(counter[0], failure_at)
                phase = "compatibility" if failure_at <= 4 else "main"
                receipt = json.loads((inputs["output"] / "failure_receipt.json").read_text(encoding="utf-8"))
                self.assertEqual(receipt["failed_chat_index"], failure_at)
                self.assertEqual(receipt["last_completed_chat_index"], failure_at - 1 or None)
                self.assertEqual(receipt["completed_chat_count"], failure_at - 1)
                self.assertEqual(receipt["attempted_chat_count"], failure_at)
                self.assertEqual(receipt["phase"], phase)
                self.assertEqual(receipt["phase_directory"], "compatibility" if phase == "compatibility" else "full")
                self.assertEqual(
                    receipt["failure_stage"],
                    "first_chat_transport" if failure_at % 2 else "final_chat_transport",
                )
                self.assertEqual(receipt["fault_code"], "transport")
                self.assertEqual(receipt["campaign_status"], "operationally_invalid")
                self.assertFalse(receipt["scientific_result_available"])
                self.assertFalse(receipt["resume_allowed"])
                self.assertEqual(analyze_measurement(inputs["output"], repository_root=self.fixture.root, phase="failure"), {"operational_status": "invalid", "phase": phase, "scientific_result": False, "v3_included_count": 0})
                self.assertFalse((inputs["output"] / "full" / "projection.json").exists())

    def test_identity_drift_and_oversized_responses_fail_closed(self) -> None:
        drift = self.fixture.inputs("identity-drift")
        with self.assertRaises(V4MeasurementError):
            self._run(drift, self.fixture.transport([0], drift=True))
        self.assertTrue((drift["output"] / "failure_receipt.json").exists())
        for kind in ("identity", "chat", "final"):
            with self.subTest(kind=kind):
                inputs = self.fixture.inputs(f"oversized-{kind}")
                base = self.fixture.transport([0])
                def oversized(
                    endpoint: str,
                    path: str,
                    payload: bytes | None,
                    timeout: int,
                    *,
                    _base=base,
                    _kind=kind,
                ) -> bytes:
                    request = {} if payload is None else json.loads(payload.decode("utf-8"))
                    if ((_kind == "identity" and path == "/api/version") or (_kind == "chat" and path == "/api/chat" and "tools" in request) or (_kind == "final" and path == "/api/chat" and "tools" not in request)):
                        return b"x" * (_MAX_RESPONSE_BYTES + 1)
                    return _base(endpoint, path, payload, timeout)
                with self.assertRaises(V4MeasurementError):
                    self._run(inputs, oversized)
                prefixes = list((inputs["output"] / "compatibility" / "raw").glob("*.prefix.bin"))
                self.assertEqual(len(prefixes), 1)
                self.assertEqual(prefixes[0].stat().st_size, _RESPONSE_PREFIX_BYTES)

    def test_resource_receipts_bind_prefix_metadata_and_bytes(self) -> None:
        for mutation in ("short", "extended", "wrong-state", "wrong-class", "wrong-length"):
            with self.subTest(mutation=mutation):
                inputs = self.fixture.inputs(f"resource-{mutation}")
                base = self.fixture.transport([0])

                def oversized(
                    endpoint: str,
                    path: str,
                    payload: bytes | None,
                    timeout: int,
                    *,
                    _base=base,
                ) -> bytes:
                    if path == "/api/chat":
                        return b"x" * (_MAX_RESPONSE_BYTES + 1)
                    return _base(endpoint, path, payload, timeout)

                with self.assertRaises(V4MeasurementError):
                    self._run(inputs, oversized)
                receipt_path = inputs["output"] / "failure_receipt.json"
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                prefix = next((inputs["output"] / "compatibility" / "raw").glob("*.prefix.bin"))
                if mutation == "short":
                    prefix.write_bytes(prefix.read_bytes()[:-1])
                elif mutation == "extended":
                    prefix.write_bytes(prefix.read_bytes() + b"x")
                elif mutation == "wrong-state":
                    receipt["raw_response_state"] = "complete"
                    _write(receipt_path, receipt)
                elif mutation == "wrong-class":
                    receipt["resource_limit_class"] = "campaign"
                    _write(receipt_path, receipt)
                else:
                    receipt["retained_prefix_bytes"] = 1
                    _write(receipt_path, receipt)
                _reseal_failure(inputs["output"])
                with self.assertRaises(V4MeasurementError):
                    analyze_measurement(
                        inputs["output"], repository_root=self.fixture.root, phase="failure"
                    )

    def test_campaign_resource_limit_is_distinct_from_per_response_limit(self) -> None:
        budget = _ResponseBudget()
        for _ in range(8):
            self.assertEqual(len(budget.admit(b"x" * _MAX_RESPONSE_BYTES)), _MAX_RESPONSE_BYTES)
        with self.assertRaises(_ResponseLimitError) as raised:
            budget.admit(b"x")
        error = raised.exception
        self.assertEqual(error.limit_class, "campaign")
        self.assertEqual(error.prefix, b"x")
        self.assertEqual(error.campaign_response_bytes_before, _MAX_RESPONSE_BYTES * 8)
        self.assertEqual(error.observed_response_bytes, 1)
        self.assertIsNone(error.response_size_lower_bound)

    def test_protocol_state_rejects_resealed_illegal_mapping(self) -> None:
        inputs = self.fixture.inputs("protocol-illegal")
        with patch(
            "anachron.v4_measurement._projection", side_effect=RuntimeError("protocol")
        ), self.assertRaises(V4MeasurementError):
            self._run(inputs, self.fixture.transport([0]))
        receipt_path = inputs["output"] / "failure_receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["trajectory_id"] = "compatibility-01"
        _write(receipt_path, receipt)
        with self.assertRaises(V4MeasurementError):
            analyze_measurement(inputs["output"], repository_root=self.fixture.root, phase="failure")

    def test_public_protocol_failure_after_all_chats_seals_without_science(self) -> None:
        inputs = self.fixture.inputs("protocol-projection")
        with patch(
            "anachron.v4_measurement._projection", side_effect=RuntimeError("protocol")
        ), self.assertRaises(V4MeasurementError):
            self._run(inputs, self.fixture.transport([0]))
        receipt = json.loads(
            (inputs["output"] / "failure_receipt.json").read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["failure_stage"], "protocol")
        self.assertEqual(receipt["fault_code"], "protocol")
        self.assertEqual(receipt["failure_cause"], "protocol")
        self.assertEqual(receipt["raw_response_state"], "absent")
        self.assertIsNone(receipt["trajectory_id"])
        self.assertEqual(receipt["completed_chat_count"], 132)
        self.assertEqual(
            analyze_measurement(inputs["output"], repository_root=self.fixture.root, phase="failure"),
            {
                "operational_status": "invalid",
                "phase": "main",
                "scientific_result": False,
                "v3_included_count": 0,
            },
        )

    def test_reachable_compatibility_protocol_failure_replays_prefix(self) -> None:
        inputs = self.fixture.inputs("protocol-compatibility")
        original_json = _Writer.json

        def fail_runtime(writer, relative, value):
            if relative == "runtime.json" and writer.root.name == "compatibility":
                raise RuntimeError("protocol")
            return original_json(writer, relative, value)

        with patch.object(
            _Writer, "json", autospec=True, side_effect=fail_runtime
        ), self.assertRaises(V4MeasurementError):
            self._run(inputs, self.fixture.transport([0]))
        receipt = json.loads(
            (inputs["output"] / "failure_receipt.json").read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["phase"], "compatibility")
        self.assertEqual(receipt["completed_chat_count"], 4)
        self.assertEqual(receipt["failure_stage"], "protocol")
        self.assertEqual(
            analyze_measurement(inputs["output"], repository_root=self.fixture.root, phase="failure")["operational_status"],
            "invalid",
        )

    def test_campaign_resource_receipt_replays_and_rejects_resealed_prefix_changes(self) -> None:
        inputs = self.fixture.inputs("campaign-resource")
        base = self.fixture.transport([0])

        def cumulative(endpoint: str, path: str, payload: bytes | None, timeout: int) -> bytes:
            raw = base(endpoint, path, payload, timeout)
            if path == "/api/chat":
                return raw + b" " * (900_000 - len(raw))
            return raw

        with self.assertRaises(V4MeasurementError):
            self._run(inputs, cumulative)
        receipt_path = inputs["output"] / "failure_receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["fault_code"], "resource")
        self.assertEqual(receipt["failure_cause"], "resource")
        self.assertEqual(receipt["resource_limit_class"], "campaign")
        self.assertEqual(receipt["raw_response_state"], "prefix")
        self.assertEqual(receipt["retained_prefix_bytes"], _RESPONSE_PREFIX_BYTES)
        self.assertGreater(receipt["observed_response_bytes"], _RESPONSE_PREFIX_BYTES)
        self.assertGreater(
            receipt["campaign_response_bytes_before"] + receipt["observed_response_bytes"],
            8_388_608,
        )
        self.assertEqual(
            analyze_measurement(inputs["output"], repository_root=self.fixture.root, phase="failure")["operational_status"],
            "invalid",
        )
        prefix = next((inputs["output"] / "full" / "raw").glob("*.prefix.bin"))
        prefix.write_bytes(prefix.read_bytes()[:-1])
        _reseal_failure(inputs["output"])
        with self.assertRaises(V4MeasurementError):
            analyze_measurement(inputs["output"], repository_root=self.fixture.root, phase="failure")

    def test_post_main_identity_resource_raw_inventories(self) -> None:
        for injected, stage in (("5:version", "identity_after_main_version"), ("6:tags", "identity_after_main_tags")):
            with self.subTest(stage=stage):
                inputs = self.fixture.inputs(f"resource-{stage}")
                base = self.fixture.transport([0])
                calls = [0]

                def resource_identity(
                    endpoint: str,
                    path: str,
                    payload: bytes | None,
                    timeout: int,
                    *,
                    _base=base,
                    _calls=calls,
                    _injected=injected,
                ) -> bytes:
                    if path in {"/api/version", "/api/tags"}:
                        _calls[0] += 1
                        endpoint_name = "version" if path.endswith("version") else "tags"
                        if _injected == f"{_calls[0]}:{endpoint_name}":
                            return b"x" * (_MAX_RESPONSE_BYTES + 1)
                    return _base(endpoint, path, payload, timeout)

                with self.assertRaises(V4MeasurementError):
                    self._run(inputs, resource_identity)
                receipt = json.loads(
                    (inputs["output"] / "failure_receipt.json").read_text(encoding="utf-8")
                )
                self.assertEqual(receipt["failure_stage"], stage)
                self.assertEqual(receipt["failure_cause"], "resource")
                self.assertEqual(receipt["raw_response_state"], "prefix")
                self.assertEqual(receipt["retained_prefix_bytes"], _RESPONSE_PREFIX_BYTES)
                self.assertEqual(
                    analyze_measurement(
                        inputs["output"], repository_root=self.fixture.root, phase="failure"
                    )["operational_status"],
                    "invalid",
                )

    def test_six_identity_checkpoints_have_frozen_counts_and_stages(self) -> None:
        cases = (
            ("1:version", "compatibility", "identity_before_compatibility_version", 0),
            ("2:tags", "compatibility", "identity_before_compatibility_tags", 0),
            ("3:version", "main", "identity_between_phases_version", 4),
            ("4:tags", "main", "identity_between_phases_tags", 4),
            ("5:version", "main", "identity_after_main_version", 132),
            ("6:tags", "main", "identity_after_main_tags", 132),
        )
        for injected, phase, stage, count in cases:
            with self.subTest(stage=stage):
                inputs = self.fixture.inputs(f"identity-{stage}")
                with self.assertRaises(V4MeasurementError):
                    self._run(
                        inputs,
                        self.fixture.transport([0], identity_failure=injected),
                    )
                receipt = json.loads(
                    (inputs["output"] / "failure_receipt.json").read_text(encoding="utf-8")
                )
                self.assertEqual(receipt["phase"], phase)
                self.assertEqual(receipt["failure_stage"], stage)
                self.assertEqual(receipt["fault_code"], "identity")
                self.assertIsNone(receipt["trajectory_id"])
                self.assertIsNone(receipt["failed_chat_index"])
                self.assertEqual(receipt["attempted_chat_count"], count)
                self.assertEqual(receipt["completed_chat_count"], count)
                self.assertEqual(
                    analyze_measurement(
                        inputs["output"], repository_root=self.fixture.root, phase="failure"
                    )["operational_status"],
                    "invalid",
                )

    def test_failure_v2_replay_rejects_resealed_field_and_journal_mutations(self) -> None:
        for field, value in (
            ("attempted_chat_count", 2),
            ("campaign_status", "complete"),
            ("completed_chat_count", 1),
            ("failed_chat_index", None),
            ("failure_manifest_sha256", "0" * 64),
            ("failure_stage", "final_chat_transport"),
            ("fault_code", "native_envelope"),
            ("last_completed_chat_index", 1),
            ("phase", "main"),
            ("phase_directory", "main"),
            ("resume_allowed", True),
            ("schema_version", "anachron-v4-failure-state-v1"),
            ("scientific_result_available", True),
            ("trajectory_id", None),
            ("v3_included_count", 1),
        ):
            with self.subTest(field=field):
                inputs = self.fixture.inputs(f"failure-field-{field}")
                with self.assertRaises(V4MeasurementError):
                    self._run(inputs, self.fixture.transport([0], failure_at=1))
                receipt_path = inputs["output"] / "failure_receipt.json"
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                receipt[field] = value
                _write(receipt_path, receipt)
                with self.assertRaises(V4MeasurementError):
                    analyze_measurement(
                        inputs["output"], repository_root=self.fixture.root, phase="failure"
                    )
        inputs = self.fixture.inputs("failure-journal")
        with self.assertRaises(V4MeasurementError):
            self._run(inputs, self.fixture.transport([0], failure_at=1))
        journal = inputs["output"] / "compatibility" / "journal.jsonl"
        rows = journal.read_text(encoding="utf-8").splitlines()
        terminal = json.loads(rows[-1])
        terminal["fault_code"] = "resource"
        rows[-1] = json.dumps(terminal, separators=(",", ":"), sort_keys=True)
        journal.write_text("\n".join(rows) + "\n", encoding="utf-8")
        _reseal_failure(inputs["output"])
        with self.assertRaises(V4MeasurementError):
            analyze_measurement(inputs["output"], repository_root=self.fixture.root, phase="failure")

    def test_failure_v2_manifest_and_claimed_raw_inventory_are_exact(self) -> None:
        inputs = self.fixture.inputs("failure-manifest")
        with self.assertRaises(V4MeasurementError):
            self._run(inputs, self.fixture.transport([0], failure_at=1))
        manifest_path = inputs["output"] / "failure_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"].append(dict(manifest["files"][0]))
        _write(manifest_path, manifest)
        receipt = json.loads((inputs["output"] / "failure_receipt.json").read_text(encoding="utf-8"))
        receipt["failure_manifest_sha256"] = _sha(manifest_path)
        _write(inputs["output"] / "failure_receipt.json", receipt)
        (inputs["output"] / "failure_manifest.sha256").write_bytes(
            f"{receipt['failure_manifest_sha256']}  failure_manifest.json\n".encode()
        )
        with self.assertRaises(V4MeasurementError):
            analyze_measurement(inputs["output"], repository_root=self.fixture.root, phase="failure")
        inputs = self.fixture.inputs("failure-extra")
        with self.assertRaises(V4MeasurementError):
            self._run(inputs, self.fixture.transport([0], failure_at=1))
        extra = inputs["output"] / "compatibility" / "raw" / "extra.bin"
        extra.write_bytes(b"extra")
        _reseal_failure(inputs["output"])
        with self.assertRaises(V4MeasurementError):
            analyze_measurement(inputs["output"], repository_root=self.fixture.root, phase="failure")

    def test_candidate_claim_map_requires_fresh_tag_bound_x_language(self) -> None:
        document = (
            self.fixture.root / "paper/v4_measurement/CANDIDATE_CLAIM_EVIDENCE_MAP.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Freshly derived, tag-bound v3/v4 comparison projection", document)
        for directory in ("paper/v4_measurement", "research/v4_measurement"):
            for path in (self.fixture.root / directory).rglob("*.md"):
                content = path.read_text(encoding="utf-8").lower()
                self.assertNotIn("v3 exclusion record", content)
                self.assertNotIn("v3_exclusion_record", content)

    def test_semantic_tamper_and_path_refusals_use_public_entrypoints(self) -> None:
        inputs = self.fixture.inputs("tamper")
        self._run(inputs, self.fixture.transport([0]))
        target = next((inputs["output"] / "full" / "raw").glob("*.final.response.json"))
        value = json.loads(target.read_text(encoding="utf-8"))
        value["message"]["content"] = "semantic tamper"
        _write(target, value)
        manifest = json.loads((inputs["output"] / "full" / "manifest.json").read_text(encoding="utf-8"))
        for row in manifest["files"]:
            row["sha256"] = _sha(inputs["output"] / "full" / row["path"])
        _write(inputs["output"] / "full" / "manifest.json", manifest)
        (inputs["output"] / "full" / "manifest.sha256").write_bytes(f"{_sha(inputs['output'] / 'full' / 'manifest.json')}  manifest.json\n".encode())
        with self.assertRaises(V4MeasurementError):
            analyze_measurement(inputs["output"], repository_root=self.fixture.root)
        rejected = self.fixture.inputs("repository-input")
        with self.assertRaises(V4MeasurementError):
            self._run({**rejected, "full": self.fixture.root / "research/v4_measurement/case_registry.json"}, self.fixture.transport([0]))
        self.assertFalse(rejected["output"].exists())
        with self.assertRaises(V4MeasurementError):
            analyze_compatibility(self.fixture.root, repository_root=self.fixture.root)
        with self.assertRaises(V4MeasurementError):
            analyze_measurement(self.fixture.root, repository_root=self.fixture.root)
        with self.assertRaises(V4MeasurementError):
            analyze_measurement(self.fixture.root, repository_root=self.fixture.root, phase="failure")

    @unittest.skipUnless(os.name == "nt", "junctions are Windows-specific")
    def test_analyzers_refuse_actual_junction_reads(self) -> None:
        success = self.fixture.inputs("junction-success")
        self._run(success, self.fixture.transport([0]))
        failed = self.fixture.inputs("junction-failure")
        with self.assertRaises(V4MeasurementError):
            self._run(failed, self.fixture.transport([0], failure_at=1))
        junction = self.fixture.external / "junction-evidence"
        result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(junction), str(self.fixture.external / "runs")], capture_output=True, check=False, text=True)
        if result.returncode:
            self.skipTest("Windows junction creation unavailable")
        try:
            with self.assertRaises(V4MeasurementError):
                analyze_compatibility(junction / "junction-success" / "evidence", repository_root=self.fixture.root)
            with self.assertRaises(V4MeasurementError):
                analyze_measurement(junction / "junction-success" / "evidence", repository_root=self.fixture.root)
            with self.assertRaises(V4MeasurementError):
                analyze_measurement(junction / "junction-failure" / "evidence", repository_root=self.fixture.root, phase="failure")
        finally:
            junction.rmdir()

    def test_projection_and_cli_modules_remain_available(self) -> None:
        rows = []
        for case in range(8):
            for model in ("model-a", "model-b"):
                for repetition in (1, 2):
                    for mode in ("unrestricted", "enforced"):
                        rows.append({"case_id": f"case-{case}", "mode": mode, "model": model, "primary": True, "query_nonblank": True, "repetition": repetition, "restatement_returned": False, "survivorship_case": False, "tclr": mode == "unrestricted" and case == 0, "trajectory_id": f"{case}-{model}-{repetition}-{mode}", "valid": True})
        self.assertEqual(_projection(rows)["split_counts"]["primary_trajectories"], 64)
        for module in (build_v4_source_audit_ui, finalize_v4_source_audit, capture_v4_runtime_identity, run_v4_recovery, analyze_v4_measurement):
            with self.assertRaises(SystemExit) as raised:
                module.main(["--help"])
            self.assertEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
