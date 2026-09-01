import base64
import copy
import hashlib
import inspect
import json
import unittest

from anachron.routes.v2.admission import (
    AdmissionError,
    build_code_closure,
    canonical_json_sha256,
)
from anachron.routes.v2.runner import (
    ExecutionJournal,
    ExecutionSession,
    RunnerValidationError,
    UnknownAfterClaimError,
    admit_execution_session,
    create_schedule,
    validate_schedule,
)
from anachron.routes.v2.runtime import (
    RuntimeValidationError,
    TransportResult,
    classify_response,
    validate_session_calibration,
)
from tests.test_routes_v2_production_scale_source_boundary import (
    ROOT,
    TestRoutesV2ProductionScaleSourceBoundary,
)


class FakeClient:
    endpoint = "http://127.0.0.1:11434"

    def __init__(self, inventory, results):
        self._inventory = inventory
        self._results = list(results)
        self.configuration = {"api": "chat", "stream": False}
        self.chat_calls = 0

    def inventory(self, _timeout):
        return self._inventory

    def chat(self, _request, _timeout):
        self.chat_calls += 1
        return self._results.pop(0)


class TestRoutesV2Execution(unittest.TestCase):
    def setUp(self):
        self.fixture = TestRoutesV2ProductionScaleSourceBoundary()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        _draft, self.manifest = self.fixture._prepare_draft(production_scale=False)
        self.contract = self.fixture.contract
        self.source_gate = self.manifest["source_gate_receipt"]
        self.closure = build_code_closure(ROOT)
        self.freeze = {
            "schema_version": "routes-v2-freeze-receipt",
            "study_phase": "development",
            "commit": "fixture-commit",
            "tree": "fixture-tree",
            "branch": "fixture-branch",
            "remote": "fixture-remote",
            "closure_sha256": self.closure["closure_sha256"],
        }
        self.inventory = {model["id"]: model["digest"] for model in self.contract["models"]}

    def _schedule(self):
        return create_schedule(
            self.fixture.directory / "schedule.json",
            self.manifest,
            self.contract,
            source_gate=self.source_gate,
            freeze_receipt=self.freeze,
            closure_lock=self.closure,
        )

    def _response(self, answer, citation_id, model=None):
        model = self.contract["models"][0]["id"] if model is None else model
        content = json.dumps({"answer": answer, "citation_id": citation_id})
        return TransportResult(
            "ok",
            json.dumps({"model": model, "done": True, "message": {"role": "assistant", "content": content}}).encode("utf-8"),
            True,
        )

    def _session(self, client, nonce="session-a"):
        schedule = self._schedule()
        return ExecutionSession(
            client=client,
            contract=self.contract,
            manifest=self.manifest,
            schedule=schedule,
            journal=ExecutionJournal(self.fixture.directory / "journal.jsonl", schedule),
            inventory=self.inventory,
            client_binding={"endpoint": client.endpoint, "configuration": client.configuration},
            calibration_path=self.fixture.directory / f"calibration-{nonce}.json",
            session_nonce=nonce,
        )

    def test_execution_scoring_is_manifest_owned_and_never_accepts_caller_answers(self):
        self.assertEqual(tuple(inspect.signature(ExecutionSession.dispatch_next).parameters), ("self",))
        pair = self.manifest["pairs"][0]
        content = json.dumps({"answer": pair["post_aliases"][0], "citation_id": pair["post_opaque_citation_id"]})
        response = TransportResult(
            "ok",
            json.dumps({"model": "qwen2.5:7b", "done": True, "message": {"role": "assistant", "content": content}}).encode("utf-8"),
            True,
        )
        scored = classify_response(
            response,
            requested_model="qwen2.5:7b",
            answer_rules={
                "pre_aliases": pair["pre_aliases"],
                "post_aliases": pair["post_aliases"],
                "abstention_aliases": self.contract["answer_rules"]["abstention_aliases"],
            },
            expected_citation_id=pair["post_opaque_citation_id"],
        )
        self.assertEqual(scored["score"], {"answer_label": "post_only", "post_only": 1})

    def test_schedule_is_create_only_and_rederives_every_binding(self):
        schedule = self._schedule()
        self.assertEqual(schedule["algorithm"], "routes-v2-counterbalance-v3")
        self.assertEqual(schedule["seed"], 20260901)
        self.assertEqual(len(schedule["trajectories"]), 24)
        changed = copy.deepcopy(schedule)
        changed["trajectories"][0], changed["trajectories"][1] = changed["trajectories"][1], changed["trajectories"][0]
        with self.assertRaises(RunnerValidationError):
            validate_schedule(changed, self.manifest, self.contract, source_gate=self.source_gate, freeze_receipt=self.freeze, closure_lock=self.closure)
        for field in ("seed", "manifest_sha256", "freeze_receipt_sha256"):
            changed = copy.deepcopy(schedule)
            changed[field] = 0 if field == "seed" else "sha256:" + "0" * 64
            with self.subTest(field=field), self.assertRaises(RunnerValidationError):
                validate_schedule(changed, self.manifest, self.contract, source_gate=self.source_gate, freeze_receipt=self.freeze, closure_lock=self.closure)
        (self.fixture.directory / "schedule.json").write_bytes(b"{}\n")
        with self.assertRaises(AdmissionError):
            self._schedule()

    def test_terminal_without_claim_and_wrong_chain_reject(self):
        schedule = self._schedule()
        with ExecutionJournal(self.fixture.directory / "journal.jsonl", schedule) as journal, self.assertRaises(RunnerValidationError):
            journal.append_terminal({"schema_version": "routes-v2-journal-record", "record_type": "terminal_outcome"})
        self.assertEqual((self.fixture.directory / "journal.jsonl").read_bytes(), b"")

    def test_crash_claim_halts_before_fake_client_chat(self):
        schedule = self._schedule()
        with ExecutionJournal(self.fixture.directory / "journal.jsonl", schedule) as journal:
            trajectory, attempt = journal.next_trajectory()
            request = {"model": trajectory["model_id"], "options": {"seed": trajectory["seed"]}}
            request_bytes = json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
            journal.append_claim({
                "schema_version": "routes-v2-journal-record", "record_type": "dispatch_claim",
                "run_id": "crashed", "session_nonce": "crashed", "schedule_index": 0,
                "attempt": attempt, "trajectory": trajectory,
                "admission": {
                    "contract_sha256": schedule["contract_sha256"], "manifest_sha256": schedule["manifest_sha256"],
                    "source_gate_sha256": schedule["source_gate_sha256"], "freeze_receipt_sha256": schedule["freeze_receipt_sha256"],
                    "closure_sha256": schedule["closure_sha256"], "schedule_sha256": canonical_json_sha256(schedule),
                    "calibration_sha256": "sha256:" + "1" * 64,
                },
                "request": {"base64": base64.b64encode(request_bytes).decode("ascii"), "length": len(request_bytes), "sha256": "sha256:" + hashlib.sha256(request_bytes).hexdigest()},
                "delivery": {"packet_sha256": "sha256:" + "2" * 64, "model_visible_packet_sha256": "sha256:" + "2" * 64, "delivered_evidence_sha256": "sha256:" + "3" * 64},
            })
        pair = self.manifest["pairs"][0]
        client = FakeClient(self.inventory, [self._response("CALIBRATION", "CAL"), self._response(pair["post_aliases"][0], pair["post_opaque_citation_id"])])
        with self._session(client, "after-crash") as session, self.assertRaises(UnknownAfterClaimError):
            session.dispatch_next()
        self.assertEqual(client.chat_calls, 0)

    def test_read_reset_is_terminal_and_retry_after_response_or_later_item_rejects(self):
        client = FakeClient(self.inventory, [self._response("CALIBRATION", "CAL"), TransportResult("read_error", b"partial", True, "connection_reset")])
        with self._session(client) as session:
            outcome = session.dispatch_next()
        self.assertEqual(outcome["status"], "read_error")
        with ExecutionJournal(self.fixture.directory / "journal.jsonl", self._schedule()) as journal:
            trajectory, attempt = journal.next_trajectory()
            self.assertEqual((trajectory["schedule_index"], attempt), (1, 1))

    def test_only_no_response_transport_failure_gets_one_immediate_retry(self):
        pair = self.manifest["pairs"][0]
        client = FakeClient(
            self.inventory,
            [
                self._response("CALIBRATION", "CAL"),
                TransportResult("transport_failure_no_response_object", b"", False, "offline"),
                self._response("CALIBRATION", "CAL"),
                self._response(pair["post_aliases"][0], pair["post_opaque_citation_id"]),
            ],
        )
        with self._session(client) as session:
            first = session.dispatch_next()
        self.assertEqual(first["status"], "transport_failure_no_response_object")
        with self._session(client, "session-b") as session:
            second = session.dispatch_next()
        self.assertEqual(second["status"], "ok")
        records = [json.loads(line) for line in (self.fixture.directory / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
        claims = [row for row in records if row["record_type"] == "dispatch_claim"]
        self.assertEqual([(row["schedule_index"], row["attempt"]) for row in claims], [(0, 1), (0, 2)])

    def test_session_receipt_binds_client_endpoint_closure_and_validates_before_return(self):
        pair = self.manifest["pairs"][0]
        client = FakeClient(self.inventory, [self._response("CALIBRATION", "CAL"), self._response(pair["post_aliases"][0], pair["post_opaque_citation_id"])])
        with self._session(client) as session:
            outcome = session.dispatch_next()
        self.assertEqual(outcome["status"], "ok")
        records = [json.loads(line) for line in (self.fixture.directory / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["record_type"] for row in records], ["dispatch_claim", "terminal_outcome"])
        receipt = json.loads((self.fixture.directory / "calibration-session-a.json").read_text(encoding="utf-8"))
        for client_binding, closure_sha256, nonce in (
            ({"endpoint": "http://other.invalid", "configuration": client.configuration}, self.closure["closure_sha256"], "session-a"),
            ({"endpoint": client.endpoint, "configuration": {"api": "other"}}, self.closure["closure_sha256"], "session-a"),
            ({"endpoint": client.endpoint, "configuration": client.configuration}, "sha256:" + "0" * 64, "session-a"),
            ({"endpoint": client.endpoint, "configuration": client.configuration}, self.closure["closure_sha256"], "other-session"),
        ):
            with self.subTest(client_binding=client_binding, closure_sha256=closure_sha256, nonce=nonce), self.assertRaises(RuntimeValidationError):
                validate_session_calibration(receipt, self.contract, inventory=self.inventory, client_binding=client_binding, closure_sha256=closure_sha256, session_nonce=nonce, model_id=self.contract["models"][0]["id"])

    def test_forged_self_hashed_source_receipt_rejects_before_any_client_call(self):
        forged = copy.deepcopy(self.manifest)
        receipt = next(item for item in forged["excerpt_receipts"] if item["item_id"] == "routes-v2:development:0" and item["arm"] == "post")
        receipt["excerpt"]["text"] = "FORGED " + receipt["excerpt"]["text"]
        receipt["excerpt"]["sha256"] = "sha256:" + hashlib.sha256(receipt["excerpt"]["text"].encode("utf-8")).hexdigest()
        receipt["excerpt"]["utf8_bytes"] = len(receipt["excerpt"]["text"].encode("utf-8"))
        receipt["excerpt"]["utf8_end"] = receipt["excerpt"]["utf8_start"] + receipt["excerpt"]["utf8_bytes"]
        receipt["receipt_sha256"] = canonical_json_sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})
        pair = next(item for item in forged["pairs"] if item["item_id"] == receipt["item_id"])
        pair["post_excerpt"] = receipt["excerpt"]
        pair["source_provenance"]["post_excerpt_receipt_sha256"] = receipt["receipt_sha256"]
        client = FakeClient(self.inventory, [])
        with self.assertRaises(ValueError):
            admit_execution_session(
                phase="development",
                repository=self.fixture.repository,
                contract=self.contract,
                manifest=forged,
                source_gate=self.source_gate,
                freeze_receipt=self.freeze,
                closure_lock=self.closure,
                schedule_path=self.fixture.directory / "forged-schedule.json",
                journal_path=self.fixture.directory / "forged-journal.jsonl",
                calibration_path=self.fixture.directory / "forged-calibration.json",
                client=client,
            )
        self.assertEqual(client.chat_calls, 0)


if __name__ == "__main__":
    unittest.main()
