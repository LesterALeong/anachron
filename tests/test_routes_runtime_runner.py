import base64
import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from anachron.routes import load_contract
from anachron.routes.manifest import canonical_json_sha256
from anachron.routes.runner import (
    RunnerValidationError,
    ScheduledTrajectory,
    _load_jsonl,
    build_schedule,
    execute_phase,
    main,
    run_calibration,
)
from anachron.routes.runtime import (
    ChatResult,
    OllamaRuntimeError,
    TimeoutAfterDispatch,
    TransportFailureBeforeResponse,
    build_chat_request,
    classify_chat_response,
    verify_declared_model_inventory,
)

ROOT = Path(__file__).parents[1]
CONTRACT_PATH = ROOT / "research" / "routes-v1" / "contract.json"
IDENTITY = {
    "contract_sha256": "sha256:" + "a" * 64,
    "manifest_sha256": "sha256:" + "b" * 64,
    "sampling_frame_sha256": "sha256:" + "c" * 64,
    "code_sha256": "sha256:" + "d" * 64,
}


class FakeClient:
    def __init__(self, results, inventory):
        self._results = list(results)
        self._inventory = inventory
        self.chat_calls = 0
        self.inventory_calls = 0

    def inventory(self, _timeout_seconds):
        self.inventory_calls += 1
        return self._inventory

    def chat(self, _request, _timeout_seconds):
        self.chat_calls += 1
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class TestRoutesRuntime(unittest.TestCase):
    def setUp(self):
        self.contract = load_contract(CONTRACT_PATH)
        self.pair = {
            "item_id": "fixture-item",
            "question": "What was documented?",
            "cutoff_year": 2013,
            "pre_answer_aliases": ["Earlier"],
            "post_answer_aliases": ["Later"],
        }
        self.retrieval = {
            "item_id": "fixture-item",
            "condition": "no_tool",
            "evidence": [],
            "trace_event": {
                "event_type": "routes_retrieval",
                "created_at": "2026-09-01T00:00:00Z",
                "item_id": "fixture-item",
                "condition": "no_tool",
                "evidence_ids": [],
            },
        }

    def test_chat_request_freezes_json_schema_options_and_no_tool_evidence(self):
        request = build_chat_request(
            self.pair,
            self.retrieval,
            self.contract,
            model_id="qwen2.5:7b",
            seed=17,
        )
        self.assertFalse(request["stream"])
        self.assertFalse(request["think"])
        self.assertEqual(request["options"], {"temperature": 0.2, "num_predict": 160, "seed": 17})
        self.assertEqual(request["format"]["required"], ["answer", "citation_ids"])
        user_payload = json.loads(request["messages"][1]["content"])
        self.assertEqual(user_payload["evidence"], [])
        self.assertNotIn("Earlier", request["messages"][1]["content"])
        self.assertNotIn("Later", request["messages"][1]["content"])

    def test_inventory_digest_drift_fails_before_dispatch(self):
        inventory = {entry["id"]: entry["digest"] for entry in self.contract["models"]}
        verify_declared_model_inventory(self.contract, inventory)
        drifted = copy.deepcopy(inventory)
        drifted["qwen2.5:7b"] = "sha256:" + "0" * 64
        with self.assertRaises(OllamaRuntimeError):
            verify_declared_model_inventory(self.contract, drifted)

    def test_chat_envelope_requires_model_done_role_and_empty_thinking(self):
        request = build_chat_request(
            self.pair,
            self.retrieval,
            self.contract,
            model_id="qwen2.5:7b",
            seed=17,
        )
        envelope = {
            "model": request["model"],
            "done": True,
            "message": {
                "role": "assistant",
                "content": json.dumps({"answer": "Earlier", "citation_ids": []}),
            },
        }
        for field, value in (
            ("model", "wrong-model"),
            ("done", False),
            ("role", "user"),
            ("thinking", "hidden reasoning"),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(envelope)
                if field in {"model", "done"}:
                    changed[field] = value
                else:
                    changed["message"][field] = value
                result = classify_chat_response(
                    json.dumps(changed).encode("utf-8"),
                    self.pair,
                    self.retrieval,
                    requested_model_id=request["model"],
                )
                self.assertEqual(result.status, "malformed_response")


class TestRoutesRunner(unittest.TestCase):
    def setUp(self):
        self.contract = load_contract(CONTRACT_PATH)
        model = self.contract["models"][0]
        self.trajectory = ScheduledTrajectory(
            study_phase="pilot",
            item_id="fixture-item",
            topic="YouTube",
            cutoff_year=2013,
            model_id=model["id"],
            model_digest=model["digest"],
            seed=17,
            condition="no_tool",
            trajectory_id="routes-v1-trajectory:fixture",
        )
        self.pair = {
            "item_id": "fixture-item",
            "question": "What was documented?",
            "cutoff_year": 2013,
            "pre_answer_aliases": ["Earlier"],
            "post_answer_aliases": ["Later"],
        }
        self.manifest = {"pairs": [self.pair]}
        self.frame = {"fixture": True}
        self.inventory = {entry["id"]: entry["digest"] for entry in self.contract["models"]}

    def _retriever(self, _manifest, _contract, _frame, *, item_id, condition, retrieved_at):
        return {
            "item_id": item_id,
            "condition": condition,
            "evidence": [],
            "trace_event": {
                "event_type": "routes_retrieval",
                "created_at": retrieved_at,
                "item_id": item_id,
                "condition": condition,
                "evidence_ids": [],
            },
        }

    def _ok_response(self, answer="Earlier", model_id=None):
        model_id = self.trajectory.model_id if model_id is None else model_id
        return ChatResult(
            "ok",
            json.dumps(
                {
                    "model": model_id,
                    "done": True,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({"answer": answer, "citation_ids": []}),
                    },
                }
            ).encode("utf-8"),
            None,
            None,
        )

    def _execute(self, directory, client, retriever=None):
        moments = iter(["2026-09-01T00:00:00Z", "2026-09-01T00:00:01Z"] * 4)
        active_retriever = self._retriever if retriever is None else retriever
        with patch(
            "anachron.routes.runner.preflight_plan", return_value=(IDENTITY, [self.trajectory])
        ), patch("anachron.routes.runner.retrieve", active_retriever):
            return execute_phase(
                self.manifest,
                self.contract,
                self.frame,
                "pilot",
                Path(directory) / "ledger.jsonl",
                client=client,
                clock=lambda: next(moments),
                retriever=active_retriever,
            )

    def test_schedule_counts_follow_accepted_pairs_not_audited_candidates(self):
        pilot = {
            "pairs": [
                {"study_phase": "pilot", "item_id": f"pilot-{index}", "topic": "YouTube", "cutoff_year": 2013}
                for index in range(18)
            ]
        }
        full = {
            "pairs": [
                {"study_phase": "full", "item_id": f"full-{index}", "topic": "Elon Musk", "cutoff_year": 2012}
                for index in range(36)
            ]
        }
        self.assertEqual(len(build_schedule(pilot, self.contract, "pilot")), 108)
        self.assertEqual(len(build_schedule(full, self.contract, "full")), 432)

    def test_transport_only_retries_once_and_preserves_both_attempts(self):
        client = FakeClient(
            [TransportFailureBeforeResponse("offline"), self._ok_response()], self.inventory
        )
        with tempfile.TemporaryDirectory() as directory:
            records = self._execute(directory, client)
            persisted = _load_jsonl(Path(directory) / "ledger.jsonl")
        self.assertEqual([record["status"] for record in records], ["transport_failure_before_response", "ok"])
        self.assertEqual([record["attempt"] for record in persisted], [1, 2])
        self.assertEqual(client.chat_calls, 2)
        self.assertIsNone(persisted[0]["response"]["sha256"])

    def test_second_transport_failure_is_retained_but_never_gets_a_third_attempt(self):
        client = FakeClient(
            [TransportFailureBeforeResponse("offline"), TransportFailureBeforeResponse("offline")],
            self.inventory,
        )
        with tempfile.TemporaryDirectory() as directory:
            records = self._execute(directory, client)
            self._execute(directory, FakeClient([], self.inventory))
        self.assertEqual(
            [record["status"] for record in records],
            ["transport_failure_before_response", "transport_failure_before_response"],
        )
        self.assertEqual(client.chat_calls, 2)

    def test_timeout_malformed_and_returned_error_are_nonreplaceable(self):
        cases = {
            "timeout": TimeoutAfterDispatch("deadline"),
            "malformed": ChatResult("ok", b"not-json", None, None),
            "returned_error": ChatResult("returned_error", b"service error", None, "http_500"),
        }
        for name, result in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                client = FakeClient([result], self.inventory)
                records = self._execute(directory, client)
                self.assertEqual(len(records), 1)
                self.assertIn(records[0]["status"], {"timeout_after_dispatch", "malformed_response", "returned_error"})
                self.assertEqual(client.chat_calls, 1)

    def test_wrong_trace_fails_loudly_without_dispatch(self):
        def wrong_retriever(*_args, **kwargs):
            result = self._retriever(*_args, **kwargs)
            result["trace_event"]["item_id"] = "wrong-item"
            return result

        client = FakeClient([self._ok_response()], self.inventory)
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(RunnerValidationError):
            self._execute(directory, client, wrong_retriever)
        self.assertEqual(client.inventory_calls, 1)
        self.assertEqual(client.chat_calls, 0)

    def test_resume_rejects_duplicate_or_identity_drift_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient([self._ok_response()], self.inventory)
            self._execute(directory, client)
            ledger = Path(directory) / "ledger.jsonl"
            original = ledger.read_text(encoding="utf-8")
            self._execute(directory, FakeClient([], self.inventory))
            self.assertEqual(ledger.read_text(encoding="utf-8"), original)
            with ledger.open("a", encoding="utf-8") as handle:
                handle.write(original)
            with self.assertRaises(RunnerValidationError):
                self._execute(directory, FakeClient([], self.inventory))

    def test_resume_recomputes_request_retrieval_response_and_timestamp_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient([self._ok_response()], self.inventory)
            self._execute(directory, client)
            ledger = Path(directory) / "ledger.jsonl"
            baseline = json.loads(ledger.read_text(encoding="utf-8"))
            cases = {}
            changed_request = copy.deepcopy(baseline)
            changed_request["request"]["body"]["model"] = "wrong-model"
            changed_request["request"]["sha256"] = canonical_json_sha256(
                changed_request["request"]["body"]
            )
            cases["request"] = changed_request
            changed_retrieval = copy.deepcopy(baseline)
            changed_retrieval["retrieval"]["result"]["trace_event"]["condition"] = "strict"
            changed_retrieval["retrieval"]["sha256"] = canonical_json_sha256(
                changed_retrieval["retrieval"]["result"]
            )
            cases["retrieval"] = changed_retrieval
            changed_response = copy.deepcopy(baseline)
            envelope = json.loads(base64.b64decode(changed_response["response"]["body_base64"]))
            envelope["model"] = "wrong-model"
            raw = json.dumps(envelope).encode("utf-8")
            changed_response["response"] = {
                "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "body_base64": base64.b64encode(raw).decode("ascii"),
                "received_bytes": len(raw),
            }
            cases["response"] = changed_response
            changed_timestamp = copy.deepcopy(baseline)
            changed_timestamp["started_at"] = "2026-09-01T00:00:00"
            cases["timestamp"] = changed_timestamp
            changed_error = copy.deepcopy(baseline)
            changed_error["error"] = {"kind": "invented", "message_sha256": None}
            cases["error"] = changed_error
            for name, record in cases.items():
                with self.subTest(name=name):
                    ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")
                    with self.assertRaises(RunnerValidationError):
                        self._execute(directory, FakeClient([], self.inventory))
            ledger.write_text(json.dumps(baseline) + "\n", encoding="utf-8")

    def test_synthetic_calibration_exercises_each_phase_declared_model(self):
        for phase, models in (("pilot", ["qwen2.5:7b"]), ("full", ["qwen2.5:7b", "qwen3:14b-q4_K_M"])):
            with self.subTest(phase=phase):
                results = [self._ok_response("CALIBRATION", model_id) for model_id in models]
                receipt = run_calibration(
                    self.contract,
                    phase,
                    client=FakeClient(results, self.inventory),
                    clock=lambda: "2026-09-01T00:00:00Z",
                )
                self.assertEqual(receipt["schema_version"], "routes-v1-calibration-receipt")
                self.assertEqual([item["model_id"] for item in receipt["models"]], models)
                self.assertTrue(all(item["answer_label"] == "pre_only" for item in receipt["models"]))

    def test_dry_run_does_not_construct_or_call_ollama(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_path = root / "contract.json"
            frame_path = root / "frame.json"
            manifest_path = root / "manifest.json"
            contract_path.write_text(json.dumps(self.contract), encoding="utf-8")
            frame_path.write_text("{}", encoding="utf-8")
            manifest_path.write_text("{}", encoding="utf-8")
            output = io.StringIO()
            with patch(
                "anachron.routes.runner.preflight_plan", return_value=(IDENTITY, [self.trajectory])
            ), patch("anachron.routes.runner.OllamaHttpClient", side_effect=AssertionError("network")), redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "--contract", str(contract_path), "--sampling-frame", str(frame_path),
                            "--manifest", str(manifest_path), "--phase", "pilot",
                            "--ledger", str(root / "ledger.jsonl"), "--dry-run",
                        ]
                    ),
                    0,
                )
        self.assertIn('"scheduled_trajectories": 1', output.getvalue())


if __name__ == "__main__":
    unittest.main()
