from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from anachron.v5_contract import (
    V5_PROTOCOL_TAG,
    V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS,
    V5_SEED_NAMESPACE,
)
from anachron.v5_custody import ByteBudget, V5CustodyError
from anachron.v5_measurement import (
    _MAX_AUTHORITY_BYTES,
    _MAX_RESPONSE_BYTES,
    FAILURE_MAX_BYTES,
    REPETITION_SEED_NAMESPACE,
    REPETITION_SEEDS,
    V5MeasurementError,
    V5OperationalFailure,
    _Budget,
    _call,
    _default_transport,
    _Writer,
    analyze_failure,
    analyze_measurement,
    build_schedule,
    classify_final_response,
    classify_first_response,
    run_measurement,
    sha256_bytes,
)
from anachron.v5_registry import canonical_json_bytes, load_v5_registry


def measurement_plan_and_go(repository_root: Path, cards: dict[str, dict[str, object]], directory: Path) -> tuple[Path, Path]:
    models = [{"digest": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e", "name": "qwen2.5:7b"}, {"digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8", "name": "qwen3:14b-q4_K_M"}]
    plans = directory / "plans"
    plans.mkdir()
    compatibility_raw = canonical_json_bytes({"trace_count": 2})
    carry_raw = canonical_json_bytes({"case_count": 8})
    source_raw = canonical_json_bytes(
        {
            "governed_files": [
                {
                    "path": relative,
                    "sha256": sha256_bytes((repository_root / relative).read_bytes()),
                    "tag_blob_oid": "0" * 40,
                }
                for relative in V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS
            ],
            "governed_paths": list(V5_SCIENTIFIC_GOVERNED_SOURCE_PATHS),
            "release": "fixture",
            "schema_version": "anachron-v5-source-manifest-v2",
        }
    )
    (plans / "compatibility_plan.json").write_bytes(compatibility_raw)
    (plans / "carry_forward.json").write_bytes(carry_raw)
    (plans / "source_manifest.json").write_bytes(source_raw)
    runtime_raw = canonical_json_bytes({"models": models, "version": "0.33.2"})
    (plans / "runtime_identity.json").write_bytes(runtime_raw)
    plan = plans / "full.json"
    go = directory / "go.json"
    authority_raw = (repository_root / "research/v5_measurement/authority_binding_contract.json").read_bytes()
    acceptance_raw = (repository_root / "research/v5_measurement/ACCEPTANCE_MATRIX.md").read_bytes()
    component = {"analyzer": sha256_bytes((repository_root / "tools/analyze_v5_measurement.py").read_bytes()), "runner": sha256_bytes((repository_root / "tools/run_v5_recovery.py").read_bytes()), "wrapper": sha256_bytes((repository_root / "tools/run_v5_conditional_campaign.ps1").read_bytes())}
    output = directory / "evidence"
    plan_value = {"authority_contract_sha256": sha256_bytes(authority_raw), "carry_forward_sha256": sha256_bytes(carry_raw), "compatibility_plan_sha256": sha256_bytes(compatibility_raw), "component_sha256": component, "evidence_output_root": str(output.resolve()), "expected_runtime": {"models": models, "version": "0.33.2"}, "protocol_release": {"commit": "1" * 40, "tag": V5_PROTOCOL_TAG, "tag_object": "2" * 40}, "schedule": build_schedule(cards, [model["name"] for model in models]), "schema_version": "anachron-v5-full-plan-v2", "seeds": list(REPETITION_SEEDS), "v4_included_count": 0, "v5_source_manifest_sha256": sha256_bytes(source_raw)}
    plan_raw = canonical_json_bytes(plan_value)
    plan.write_bytes(plan_raw)
    schedule_raw = canonical_json_bytes({"rows": plan_value["schedule"], "v4_included_count": 0})
    receipt_raw = canonical_json_bytes({"authority_contract_sha256": sha256_bytes(authority_raw), "carry_forward_sha256": sha256_bytes(carry_raw), "compatibility_plan_sha256": sha256_bytes(compatibility_raw), "full_plan_sha256": sha256_bytes(plan_raw), "runtime_identity_sha256": sha256_bytes(runtime_raw), "schedule_sha256": sha256_bytes(schedule_raw), "schema_version": "anachron-v5-materialization-receipt-v2", "v4_included_count": 0, "v5_source_manifest_sha256": sha256_bytes(source_raw)})
    (plans / "materialization_receipt.json").write_bytes(receipt_raw)
    go.write_bytes(canonical_json_bytes({"acceptance_matrix_sha256": sha256_bytes(acceptance_raw), "analyzer_sha256": component["analyzer"], "authority_contract_sha256": sha256_bytes(authority_raw), "authorized_at_utc": "2026-09-06T00:00:00Z", "authorized_by": "Test", "carry_forward_sha256": sha256_bytes(carry_raw), "compatibility_plan_sha256": sha256_bytes(compatibility_raw), "decision": "GO", "expected_runtime": plan_value["expected_runtime"], "full_plan_sha256": sha256_bytes(plan_raw), "kind": "anachron-v5-conditional-measurement-authorization", "materialization_receipt_sha256": sha256_bytes(receipt_raw), "output_root": str(output.resolve()), "protocol_commit": "1" * 40, "protocol_tag": V5_PROTOCOL_TAG, "protocol_tag_object": "2" * 40, "runner_sha256": component["runner"], "source_manifest_sha256": sha256_bytes(source_raw), "statement": "fixture authorization", "v4_included_count": 0, "wrapper_sha256": component["wrapper"]}))
    return plan, go


class V5MeasurementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        _, self.cards = load_v5_registry(self.root)
        self.card = next(iter(self.cards.values()))

    def test_precommitted_seeds_match_documented_derivation(self) -> None:
        expected = tuple(int.from_bytes(hashlib.sha256(f"{V5_SEED_NAMESPACE}/repetition-{number}".encode()).digest()[:4], "big") & 0x7FFFFFFF for number in (1, 2))
        self.assertEqual(REPETITION_SEED_NAMESPACE, V5_SEED_NAMESPACE)
        self.assertEqual(REPETITION_SEEDS, expected)
        self.assertEqual(REPETITION_SEEDS, (1477205243, 106139663))

    def test_schedule_has_64_unique_ordinal_rows(self) -> None:
        rows = build_schedule(self.cards, ("qwen2.5:7b", "qwen3:14b-q4_K_M"))
        self.assertEqual(len(rows), 64)
        self.assertEqual([row["ordinal"] for row in rows], list(range(1, 65)))
        self.assertEqual({row["seed"] for row in rows}, set(REPETITION_SEEDS))

    def test_date_mismatch_is_data_and_does_not_admit_retrieval(self) -> None:
        response = {"message": {"role": "assistant", "tool_calls": [{"function": {"name": "anachron_search", "arguments": {"query": "Aster", "date": "2023-09-15"}}}]}}
        classified = classify_first_response(response, self.card)
        self.assertEqual(classified["category"], "date_mismatch")
        self.assertEqual(classified["tool"]["date"], "2023-09-15")

    def test_duplicate_arguments_take_precedence_over_keyset(self) -> None:
        response = {"message": {"role": "assistant", "tool_calls": [{"function": {"name": "anachron_search", "arguments": '{"query":"one","query":"two","extra":1}'}}]}}
        self.assertEqual(classify_first_response(response, self.card)["category"], "tool_arguments_duplicate_key")

    def test_first_taxonomy_has_frozen_precedence(self) -> None:
        cases = (
            ({"message": {"role": "assistant"}}, "no_tool_call"),
            ({"message": {"role": "assistant", "tool_calls": []}}, "tool_call_count_invalid"),
            ({"message": {"role": "assistant", "tool_calls": [{"function": {"name": "other", "arguments": {}}}]}}, "tool_name_invalid"),
            ({"message": {"role": "assistant", "tool_calls": [{"function": {"name": "anachron_search", "arguments": "[]"}}]}}, "tool_arguments_unparseable_or_nonobject"),
            ({"message": {"role": "assistant", "tool_calls": [{"function": {"name": "anachron_search", "arguments": {"query": "x", "extra": 1}}}]}}, "tool_keyset_invalid"),
            ({"message": {"role": "assistant", "tool_calls": [{"function": {"name": "anachron_search", "arguments": {"query": " "}}}]}}, "query_invalid"),
            ({"message": {"role": "assistant", "tool_calls": [{"function": {"name": "anachron_search", "arguments": {"query": "x", "date": 1}}}]}}, "date_invalid_type"),
            ({"message": {"role": "wrong", "tool_calls": [{"function": {"name": "anachron_search", "arguments": {"query": "x"}}}]}}, "first_native_response_invalid"),
        )
        for response, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(classify_first_response(response, self.card)["category"], expected)

    def test_final_invalidity_does_not_erase_valid_first_adherence(self) -> None:
        self.assertEqual(classify_final_response({"message": {"role": "assistant", "content": ""}}), "final_content_invalid")

    def test_campaign_continues_after_nonadherence_without_final_chat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            plan, go = self._plan_and_go(temporary_root)
            first_calls, final_calls = [], []

            def transport(_endpoint: str, path: str, payload: bytes | None, _timeout: int) -> bytes:
                if path == "/api/version":
                    return b'{"version":"0.33.2"}\n'
                if path == "/api/tags":
                    return canonical_json_bytes({"models": [{"digest": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e", "name": "qwen2.5:7b"}, {"digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8", "name": "qwen3:14b-q4_K_M"}]})
                request = json.loads(payload.decode("utf-8"))
                if "tools" in request:
                    first_calls.append(request)
                    arguments = {"query": "synthetic bulletin"}
                    if len(first_calls) == 3:
                        arguments = {"query": "Aster", "date": "1900-01-01"}
                    return canonical_json_bytes({"message": {"role": "assistant", "tool_calls": [{"function": {"name": "anachron_search", "arguments": arguments}}]}})
                final_calls.append(request)
                return canonical_json_bytes({"message": {"role": "assistant", "content": "excluded"}})

            result = run_measurement(plan, go, temporary_root / "evidence", repository_root=self.root, transport=transport)
        self.assertEqual(len(first_calls), 66)
        self.assertEqual(len(final_calls), 63)
        self.assertEqual(result["rows"][0]["first_category"], "date_mismatch")
        self.assertIsNone(result["rows"][0]["final_category"])
        self.assertEqual(result["summary"]["v4_included_count"], 0)

    def _plan_and_go(self, directory: Path) -> tuple[Path, Path]:
        return measurement_plan_and_go(self.root, self.cards, directory)

    def test_cap_plus_one_full_plan_rejects_before_transport_or_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            plan, go = self._plan_and_go(temporary_root)
            evidence = temporary_root / "evidence"
            plan.write_bytes(b'{"padding":"' + b"x" * _MAX_AUTHORITY_BYTES + b'"}')
            calls = []

            def transport(_endpoint: str, path: str, _payload: bytes | None, _timeout: int) -> bytes:
                calls.append(path)
                raise AssertionError("preflight must reject before transport")

            with self.assertRaisesRegex(V5MeasurementError, "full plan exceeds its byte cap"):
                run_measurement(plan, go, evidence, repository_root=self.root, transport=transport)
            self.assertEqual(calls, [])
            self.assertFalse(evidence.exists())
            self.assertEqual(
                [path.name for path in temporary_root.iterdir() if path.name.startswith(".evidence.staging-")],
                [],
            )

    def test_mock_production_lifecycle_is_opaque_then_replayable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            plan, go = self._plan_and_go(temporary_root)
            calls = []

            def transport(_endpoint: str, path: str, payload: bytes | None, _timeout: int) -> bytes:
                calls.append((path, payload))
                if path == "/api/version":
                    return b'{"version":"0.33.2"}\n'
                if path == "/api/tags":
                    return canonical_json_bytes({"models": [{"digest": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e", "name": "qwen2.5:7b"}, {"digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8", "name": "qwen3:14b-q4_K_M"}]})
                request = json.loads(payload.decode("utf-8"))
                if "tools" in request:
                    return canonical_json_bytes({"message": {"role": "assistant", "tool_calls": [{"function": {"name": "anachron_search", "arguments": {"query": "synthetic bulletin"}}}]}})
                return canonical_json_bytes({"message": {"role": "assistant", "content": "excluded"}})

            result = run_measurement(plan, go, temporary_root / "evidence", repository_root=self.root, transport=transport)
            self.assertEqual(len(result["rows"]), 64)
            self.assertEqual(analyze_measurement(temporary_root / "evidence", repository_root=self.root), result["summary"])
            self.assertEqual(len(calls), 136)
            compatibility = temporary_root / "evidence" / "compatibility" / "raw"
            self.assertEqual(sorted(path.suffix for path in compatibility.iterdir()), [".json", ".json", ".wire", ".wire"])
            (temporary_root / "evidence" / "raw" / "t0001-first-response.json").write_bytes(b"x" * (_MAX_RESPONSE_BYTES + 1))
            with self.assertRaisesRegex(V5MeasurementError, "replay byte cap"):
                analyze_measurement(temporary_root / "evidence", repository_root=self.root)
            authority_root = temporary_root / "oversized-authority"
            authority_root.mkdir()
            authority_plan, authority_go = self._plan_and_go(authority_root)
            run_measurement(authority_plan, authority_go, authority_root / "evidence", repository_root=self.root, transport=transport)
            (authority_root / "evidence" / "authority" / "full-plan.json").write_bytes(b"x" * (_MAX_AUTHORITY_BYTES + 1))
            with self.assertRaisesRegex(V5MeasurementError, "replay byte cap"):
                analyze_measurement(authority_root / "evidence", repository_root=self.root)

    def test_transport_failure_is_terminal_and_same_output_cannot_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            plan, go = self._plan_and_go(temporary_root)

            def transport(_endpoint: str, path: str, _payload: bytes | None, _timeout: int) -> bytes:
                if path == "/api/version":
                    return b'{"version":"0.33.2"}\n'
                if path == "/api/tags":
                    return canonical_json_bytes({"models": [{"digest": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e", "name": "qwen2.5:7b"}, {"digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8", "name": "qwen3:14b-q4_K_M"}]})
                raise OSError("offline mock fault")

            evidence = temporary_root / "evidence"
            with self.assertRaises(V5OperationalFailure):
                run_measurement(plan, go, evidence, repository_root=self.root, transport=transport)
            self.assertEqual(analyze_failure(evidence)["fault_code"], "transport")
            (evidence / "unexpected.json").write_bytes(b"{}\n")
            with self.assertRaisesRegex(V5MeasurementError, r"^failure replay differs$"):
                analyze_failure(evidence)
            with self.assertRaises(V5MeasurementError):
                run_measurement(plan, go, evidence, repository_root=self.root, transport=transport)

    def test_duplicate_runtime_identity_rows_terminalize_before_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            plan, go = self._plan_and_go(temporary_root)
            calls = []

            def transport(_endpoint: str, path: str, _payload: bytes | None, _timeout: int) -> bytes:
                calls.append(path)
                if path == "/api/version":
                    return b'{"version":"0.33.2"}\n'
                return canonical_json_bytes({"models": [{"digest": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e", "name": "qwen2.5:7b"}, {"digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8", "name": "qwen2.5:7b"}]})

            with self.assertRaises(V5OperationalFailure) as raised:
                run_measurement(plan, go, temporary_root / "evidence", repository_root=self.root, transport=transport)
            self.assertEqual(raised.exception.fault_code, "identity")
            self.assertEqual(calls, ["/api/version", "/api/tags"])

    def test_default_transport_streams_cap_plus_one_without_publication(self) -> None:
        remaining = _MAX_RESPONSE_BYTES + 1
        read_sizes: list[int] = []

        def read(size: int) -> bytes:
            nonlocal remaining
            read_sizes.append(size)
            chunk = min(size, remaining)
            remaining -= chunk
            return b"x" * chunk

        response = MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = read
        budget = _Budget()
        with patch("anachron.v5_measurement.urlopen", return_value=response), self.assertRaises(
            V5OperationalFailure,
        ) as raised:
            _call(
                _default_transport,
                "http://127.0.0.1:11434",
                "/api/version",
                None,
                budget,
                phase="identity",
                step=1,
                completed=0,
                ordinal=None,
            )
        self.assertEqual(raised.exception.fault_code, "resource")
        self.assertTrue(read_sizes)
        self.assertTrue(all(size <= 65_536 for size in read_sizes))
        self.assertEqual(budget.total, 0)

    def test_terminalization_discards_staging_at_each_terminal_boundary(self) -> None:
        def failure() -> V5OperationalFailure:
            return V5OperationalFailure("fixture", phase="identity", failed_step=1, last_completed_step=0)

        cases = ("failure_receipt.json", "failure_inventory.json", "failure_inventory.sha256")
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            for relative in cases:
                with self.subTest(relative=relative):
                    output = temporary_root / relative.replace(".json", "")
                    writer = _Writer(output)
                    original = writer._write

                    def fail_terminal_write(
                        path: str,
                        raw: bytes,
                        *,
                        target: str = relative,
                        writer_write=original,
                    ) -> None:
                        if path == target:
                            raise V5OperationalFailure("fixture", phase="primary", failed_step=1, last_completed_step=0)
                        writer_write(path, raw)

                    with patch.object(writer, "_write", side_effect=fail_terminal_write), self.assertRaises(
                        V5OperationalFailure,
                    ) as raised:
                        writer.terminal(failure())
                    self.assertEqual(raised.exception.fault_code, "terminalization")
                    self.assertFalse(output.exists())
                    self.assertEqual(list(temporary_root.glob(f".{output.name}.staging-*")), [])
            for name, target in (("fsync", "anachron.v5_measurement.fsync_directory"), ("publish", "anachron.v5_measurement.publish_staging_root")):
                with self.subTest(boundary=name):
                    output = temporary_root / name
                    writer = _Writer(output)
                    with patch(target, side_effect=OSError("injected terminalization fault")), self.assertRaises(
                        V5OperationalFailure,
                    ) as raised:
                        writer.terminal(failure())
                    if name == "fsync":
                        self.assertEqual(raised.exception.fault_code, "publication_durability_unknown")
                        self.assertTrue(output.exists())
                        self.assertEqual(analyze_failure(output)["fault_code"], "fixture")
                    else:
                        self.assertEqual(raised.exception.fault_code, "terminalization")
                        self.assertFalse(output.exists())
                        self.assertEqual(list(temporary_root.glob(f".{output.name}.staging-*")), [])

    def test_success_post_rename_fsync_preserves_replayable_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, go = self._plan_and_go(root)

            def transport(_endpoint: str, path: str, payload: bytes | None, _timeout: int) -> bytes:
                if path == "/api/version":
                    return b'{"version":"0.33.2"}\n'
                if path == "/api/tags":
                    return canonical_json_bytes(
                        {
                            "models": [
                                {"digest": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e", "name": "qwen2.5:7b"},
                                {"digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8", "name": "qwen3:14b-q4_K_M"},
                            ]
                        }
                    )
                request = json.loads(payload.decode("utf-8"))
                if "tools" in request:
                    return canonical_json_bytes({"message": {"role": "assistant", "tool_calls": [{"function": {"name": "anachron_search", "arguments": {"query": "synthetic bulletin"}}}]}})
                return canonical_json_bytes({"message": {"role": "assistant", "content": "fixture"}})

            output = root / "evidence"
            with patch("anachron.v5_measurement.fsync_directory", side_effect=OSError("injected")), self.assertRaises(
                V5OperationalFailure
            ) as raised:
                run_measurement(plan, go, output, repository_root=self.root, transport=transport)
            self.assertEqual(raised.exception.fault_code, "publication_durability_unknown")
            self.assertTrue(output.exists())
            self.assertEqual(analyze_measurement(output, repository_root=self.root)["v4_included_count"], 0)
            self.assertEqual(list(root.glob(".evidence.staging-*")), [])

    def test_terminalization_cleanup_failure_is_loud(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence"
            writer = _Writer(output)
            original = writer._write

            def fail_receipt(relative: str, raw: bytes) -> None:
                if relative == "failure_receipt.json":
                    raise V5OperationalFailure("fixture", phase="primary", failed_step=1, last_completed_step=0)
                original(relative, raw)

            with (
                patch.object(writer, "_write", side_effect=fail_receipt),
                patch(
                    "anachron.v5_measurement.discard_staging_root",
                    side_effect=OSError("injected cleanup fault"),
                ),
                self.assertRaises(V5OperationalFailure) as raised,
            ):
                writer.terminal(V5OperationalFailure("fixture", phase="primary", failed_step=1, last_completed_step=0))
            self.assertEqual(raised.exception.fault_code, "terminalization_cleanup")
            self.assertTrue(writer.root.exists())

    def test_malformed_first_wire_is_row_data_and_replays(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            plan, go = self._plan_and_go(temporary_root)
            calls = []

            def transport(_endpoint: str, path: str, payload: bytes | None, _timeout: int) -> bytes:
                if path == "/api/version":
                    return b'{"version":"0.33.2"}\n'
                if path == "/api/tags":
                    return canonical_json_bytes({"models": [{"digest": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e", "name": "qwen2.5:7b"}, {"digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8", "name": "qwen3:14b-q4_K_M"}]})
                request = json.loads(payload.decode("utf-8"))
                self.assertIn("tools", request)
                calls.append(request)
                if len(calls) <= 2:
                    return canonical_json_bytes({"message": {"role": "assistant", "tool_calls": [{"function": {"name": "anachron_search", "arguments": {"query": "synthetic bulletin"}}}]}})
                return b"not-json"

            result = run_measurement(plan, go, temporary_root / "evidence", repository_root=self.root, transport=transport)
            self.assertEqual({row["first_category"] for row in result["rows"]}, {"first_native_response_invalid"})
            self.assertEqual(result["summary"]["scheduled"], 64)

    def test_replay_rejects_raw_and_manifest_mutation(self) -> None:
        def transport(_endpoint: str, path: str, payload: bytes | None, _timeout: int) -> bytes:
            if path == "/api/version":
                return b'{"version":"0.33.2"}\n'
            if path == "/api/tags":
                return canonical_json_bytes({"models": [{"digest": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e", "name": "qwen2.5:7b"}, {"digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8", "name": "qwen3:14b-q4_K_M"}]})
            request = json.loads(payload.decode("utf-8"))
            if "tools" in request:
                return canonical_json_bytes({"message": {"role": "assistant", "tool_calls": [{"function": {"name": "anachron_search", "arguments": {"query": "synthetic bulletin"}}}]}})
            return canonical_json_bytes({"message": {"role": "assistant", "content": "excluded"}})

        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            plan, go = self._plan_and_go(temporary_root)
            evidence = temporary_root / "evidence"
            run_measurement(plan, go, evidence, repository_root=self.root, transport=transport)
            raw = evidence / "raw" / "t0001-first-response.json"
            raw.write_bytes(b'{"message":{"role":"assistant"}}\n')
            with self.assertRaisesRegex(Exception, "manifest"):
                analyze_measurement(evidence, repository_root=self.root)
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            plan, go = self._plan_and_go(temporary_root)
            evidence = temporary_root / "evidence"
            run_measurement(plan, go, evidence, repository_root=self.root, transport=transport)
            (evidence / "manifest.sha256").write_text("0" * 64 + "  manifest.json\n", encoding="ascii")
            with self.assertRaisesRegex(Exception, "manifest digest"):
                analyze_measurement(evidence, repository_root=self.root)

    def test_success_sidecar_uses_the_exact_terminal_budget_boundary(self) -> None:
        raw = canonical_json_bytes({"files": [], "schema_version": "fixture"})
        first = ByteBudget(FAILURE_MAX_BYTES, 2)
        first.reserve(FAILURE_MAX_BYTES - len(raw), "prior evidence")
        first.reserve(len(raw), "manifest sidecar")
        self.assertEqual(first.bytes_used, FAILURE_MAX_BYTES)
        second = ByteBudget(FAILURE_MAX_BYTES, 2)
        second.reserve(FAILURE_MAX_BYTES - len(raw) + 1, "prior evidence")
        with self.assertRaises(V5CustodyError):
            second.reserve(len(raw), "manifest sidecar")

    def _failure_fixture(self, root: Path, *, full_suffix: bool = False, partial: bool = False) -> Path:
        writer = _Writer(root)
        for path in (
            "authority/full-plan.json",
            "authority/conditional-go.json",
            "authority/compatibility-plan.json",
            "authority/carry-forward.json",
            "authority/source-manifest.json",
            "authority/materialization-receipt.json",
            "authority/runtime-identity.json",
            "authority/authority-contract.json",
            "authority/acceptance-matrix.md",
            "identity/version.wire",
            "identity/tags.wire",
            "compatibility/raw/t0001-first-request.json",
            "compatibility/raw/t0001-first-response.wire",
            "raw/t0001-first-request.json",
            "raw/t0001-first-response.json",
        ):
            writer._write(path, b'{"message":{"tool_calls":[{}]}}\n')
        if full_suffix:
            for path in (
                "raw/t0001-tool-result.json",
                "raw/t0001-final-request.json",
                "raw/t0001-final-response.json",
            ):
                writer._write(path, b"{}\n")
        if partial:
            writer._write("raw/t0002-first-request.json", b"{}\n")
        writer.terminal(
            V5OperationalFailure(
                "fixture",
                phase="primary",
                failed_step=1,
                last_completed_step=0,
                ordinal=2,
            )
        )
        return root

    def _resign_failure_inventory(self, evidence: Path) -> None:
        files = []
        for path in sorted(evidence.rglob("*")):
            if path.is_file() and path.relative_to(evidence).as_posix() not in {"failure_inventory.json", "failure_inventory.sha256"}:
                raw = path.read_bytes()
                files.append({"path": path.relative_to(evidence).as_posix(), "sha256": sha256_bytes(raw)})
        raw = canonical_json_bytes({"files": files, "schema_version": "anachron-v5-failure-inventory-v2", "v4_included_count": 0})
        (evidence / "failure_inventory.json").write_bytes(raw)
        (evidence / "failure_inventory.sha256").write_bytes(f"{sha256_bytes(raw)}  failure_inventory.json\n".encode("ascii"))

    def test_failure_replay_rejects_same_count_topology_forgery(self) -> None:
        mutations = (
            ("wrong-authority", lambda root: (root / "authority" / "full-plan.json").replace(root / "authority" / "forged.json")),
            ("split-compatibility", lambda root: (root / "compatibility" / "raw" / "t0001-first-response.wire").replace(root / "compatibility" / "raw" / "t0002-first-response.wire")),
            ("skipped-primary", lambda root: (root / "raw" / "t0001-first-request.json").replace(root / "raw" / "t0002-first-request.json")),
            ("mismatched-identity", lambda root: (root / "identity" / "tags.wire").replace(root / "identity" / "pre-primary-tags.wire")),
            ("invalid-partial-order", lambda root: (root / "raw" / "t0002-first-request.json").replace(root / "raw" / "t0002-final-request.json")),
        )
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for name, mutate in mutations:
                with self.subTest(name=name):
                    evidence = self._failure_fixture(parent / name, partial=name == "invalid-partial-order")
                    mutate(evidence)
                    self._resign_failure_inventory(evidence)
                    with self.assertRaisesRegex(V5MeasurementError, "topology|suffix|exact v5 evidence path|failure replay differs"):
                        analyze_failure(evidence)

    def test_failure_replay_rejects_incomplete_or_extra_valid_suffix_and_success_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            incomplete = self._failure_fixture(parent / "incomplete", full_suffix=True)
            (incomplete / "raw" / "t0001-final-response.json").unlink()
            self._resign_failure_inventory(incomplete)
            with self.assertRaisesRegex(V5MeasurementError, "suffix|topology"):
                analyze_failure(incomplete)
            extra = self._failure_fixture(parent / "extra")
            for name in ("tool-result", "final-request", "final-response"):
                (extra / "raw" / f"t0001-{name}.json").write_bytes(b"{}\n")
            self._resign_failure_inventory(extra)
            with self.assertRaisesRegex(V5MeasurementError, "valid-primary|suffix|topology"):
                analyze_failure(extra)
            mixed = self._failure_fixture(parent / "mixed")
            (mixed / "manifest.json").write_bytes(b"{}\n")
            self._resign_failure_inventory(mixed)
            with self.assertRaisesRegex(V5MeasurementError, "success sidecars"):
                analyze_failure(mixed)

    def test_failure_replay_rejects_empty_orphan_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = self._failure_fixture(Path(temporary) / "failure")
            (evidence / "orphan").mkdir()
            with self.assertRaisesRegex(V5MeasurementError, "topology"):
                analyze_failure(evidence)

    def test_failure_replay_aggregate_includes_receipt_at_exact_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = self._failure_fixture(Path(temporary) / "failure")
            total = sum(path.stat().st_size for path in evidence.rglob("*") if path.is_file())
            self.assertGreater((evidence / "failure_receipt.json").stat().st_size, 0)
            with patch("anachron.v5_measurement.FAILURE_MAX_BYTES", total):
                self.assertEqual(analyze_failure(evidence)["fault_code"], "fixture")
            with patch("anachron.v5_measurement.FAILURE_MAX_BYTES", total - 1), self.assertRaisesRegex(
                V5MeasurementError,
                r"^failure replay differs$",
            ):
                analyze_failure(evidence)

    def test_writer_rejects_orphan_directory_before_failure_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "failure"
            writer = _Writer(output)
            (writer.root / "orphan").mkdir()
            with self.assertRaisesRegex(V5OperationalFailure, "terminalization"):
                writer.terminal(
                    V5OperationalFailure(
                        "fixture",
                        phase="identity",
                        failed_step=1,
                        last_completed_step=0,
                    )
                )
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(temporary).glob(".failure.staging-*")), [])

    def test_writer_reconciles_before_each_write_and_discards_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for name, tamper in (
                ("orphan", lambda writer: (writer.root / "orphan").mkdir()),
                (
                    "unknown-file",
                    lambda writer: (writer.root / "raw" / "unexpected.bin").write_bytes(b"orphan\n"),
                ),
                (
                    "mutation",
                    lambda writer: (writer.root / "authority" / "full-plan.json").write_bytes(
                        b"changed\n"
                    ),
                ),
            ):
                with self.subTest(name=name):
                    output = parent / name
                    writer = _Writer(output)
                    writer._write("authority/full-plan.json", b"original\n")
                    tamper(writer)
                    with self.assertRaises(V5OperationalFailure) as raised:
                        writer._write("authority/conditional-go.json", b"next\n")
                    self.assertEqual(raised.exception.fault_code, "writer")
                    self.assertFalse((writer.root / "authority" / "conditional-go.json").exists())
                    writer.discard()
                    self.assertFalse(writer.root.exists())


if __name__ == "__main__":
    unittest.main()
