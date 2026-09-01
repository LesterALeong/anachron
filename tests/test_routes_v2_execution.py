import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from anachron.routes.v2 import load_contract
from anachron.routes.v2.admission import (
    AdmissionError,
    build_code_closure,
    canonical_json_sha256,
    revalidate_raw_source,
    write_create_only,
)
from anachron.routes.v2.manifest import seal_manifest
from anachron.routes.v2.runner import (
    ExecutionJournal,
    ExecutionSession,
    RunnerValidationError,
    UnknownAfterClaimError,
    create_schedule,
    validate_schedule,
)
from anachron.routes.v2.runtime import (
    RuntimeValidationError,
    TransportResult,
    validate_session_calibration,
)

ROOT = Path(__file__).parents[1]
CONTRACT_PATH = ROOT / "research" / "routes-v2" / "contract.json"
FRAME_PATH = ROOT / "research" / "routes-v2" / "sampling_frame.json"


def git(directory, *args):
    return subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True, text=True).stdout.strip()


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
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.contract = load_contract(CONTRACT_PATH)
        self.frame = json.loads(FRAME_PATH.read_text(encoding="utf-8"))
        self.manifest = self._manifest()
        self.source_gate = self.manifest["source_gate_receipt"]
        self.repository, self.freeze, self.closure = self._clean_repository()
        self.inventory = {model["id"]: model["digest"] for model in self.contract["models"]}

    def tearDown(self):
        self.temporary.cleanup()

    def _revision(self, oldid, content, timestamp):
        return {
            "revision_id": oldid,
            "revision_url": f"https://en.wikipedia.org/w/index.php?title=Fixture&oldid={oldid}",
            "timestamp": timestamp,
            "content": content,
            "content_sha256": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }

    def _manifest(self):
        paths, mapping = [], []
        for index, topic in enumerate(self.contract["development"]["topics"]):
            pre, post = f"Earlier evidence for item {index} is OLD-{index}.", f"Evidence for item {index} is VALUE-{index}."
            raw_path = self.directory / f"raw-{index}.json"
            receipt_path = self.directory / f"receipt-{index}.json"
            write_create_only(raw_path, {
                "schema_version": "routes-v1-source-discovery", "title": topic["title"], "cutoff_year": topic["cutoff_year"],
                "strict_revision": self._revision(100 + index, pre, f"{topic['cutoff_year']}-12-30T00:00:00Z"),
                "post_snapshot": self._revision(200 + index, post, f"{topic['cutoff_year'] + 1}-01-02T00:00:00Z"),
            })
            revalidate_raw_source(contract_path=CONTRACT_PATH, sampling_frame_path=FRAME_PATH, raw_artifact_path=raw_path, phase="development", item_id=f"routes-v2:development:{index}", output_path=receipt_path)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            paths.append(receipt_path)
            mapping.append({"item_id": f"routes-v2:development:{index}", "question": f"What value is documented for item {index}?", "pre_content": pre, "post_content": post, "pre_opaque_citation_id": f"PRE{index:02d}", "opaque_citation_id": f"DOC{index:02d}", "raw_discovery_artifact_sha256": receipt["raw_discovery_artifact_sha256"]})
        mapping_path, draft_path = self.directory / "mapping.json", self.directory / "draft.json"
        write_create_only(mapping_path, {"schema_version": "routes-v2-source-mapping-input", "study_phase": "development", "contract_sha256": canonical_json_sha256(self.contract), "sampling_frame_sha256": canonical_json_sha256(self.frame), "items": mapping})
        from anachron.routes.v2.manifest import prepare_pending_draft

        draft = prepare_pending_draft(phase="development", contract_path=CONTRACT_PATH, sampling_frame_path=FRAME_PATH, revalidation_receipt_paths=paths, source_mapping_input_path=mapping_path, output_path=draft_path)
        decisions = {"schema_version": "routes-v2-source-decisions", "study_phase": "development", "pending_draft_sha256": canonical_json_sha256(draft), "validator_id": "fixture-human", "decisions": [{"item_id": pair["item_id"], "decision": "PASS", "reason": "fixture"} for pair in draft["pairs"]]}
        return seal_manifest(phase="development", contract=self.contract, sampling_frame=self.frame, draft=draft, source_decisions=decisions, revalidation_receipt_paths=paths)

    def _clean_repository(self):
        root, remote = self.directory / "repository", self.directory / "remote.git"
        git(self.directory, "init", "--bare", str(remote))
        git(self.directory, "init", "-b", "main", str(root))
        git(root, "config", "user.email", "fixture@example.test")
        git(root, "config", "user.name", "fixture")
        shutil.copy2(ROOT / ".gitattributes", root / ".gitattributes")
        for relative in ("anachron", "tools", "research/routes-v2", "paper/routes_v2"):
            shutil.copytree(
                ROOT / relative,
                root / relative,
                ignore=shutil.ignore_patterns("__pycache__", "build", "dist", "generated"),
            )
        parent_frame = root / "research" / "routes-v1"
        parent_frame.mkdir()
        shutil.copy2(ROOT / "research" / "routes-v1" / "sampling_frame.json", parent_frame / "sampling_frame.json")
        git(root, "add", ".")
        git(root, "commit", "-m", "fixture")
        git(root, "remote", "add", "origin", str(remote))
        git(root, "push", "-u", "origin", "main")
        closure = build_code_closure(root)
        freeze = {"schema_version": "routes-v2-freeze-receipt", "study_phase": "development", "commit": git(root, "rev-parse", "HEAD"), "tree": git(root, "rev-parse", "HEAD^{tree}"), "branch": "main", "remote": str(remote), "closure_sha256": closure["closure_sha256"]}
        return root, freeze, closure

    def _schedule(self):
        return create_schedule(self.directory / "schedule.json", self.manifest, self.contract, source_gate=self.source_gate, freeze_receipt=self.freeze, closure_lock=self.closure)

    def _response(self, answer, model=None):
        model = self.contract["models"][0]["id"] if model is None else model
        body = json.dumps({"model": model, "done": True, "message": {"role": "assistant", "content": json.dumps({"answer": answer, "citation_id": "DOC"})}}).encode("utf-8")
        return TransportResult("ok", body, True)

    def _session(self, client, nonce="session-a"):
        schedule = self._schedule()
        return ExecutionSession(client=client, contract=self.contract, manifest=self.manifest, schedule=schedule, journal=ExecutionJournal(self.directory / "journal.jsonl", schedule), inventory=self.inventory, client_binding={"endpoint": client.endpoint, "configuration": client.configuration}, calibration_path=self.directory / f"calibration-{nonce}.json", session_nonce=nonce)

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
            with self.assertRaises(RunnerValidationError):
                validate_schedule(changed, self.manifest, self.contract, source_gate=self.source_gate, freeze_receipt=self.freeze, closure_lock=self.closure)
        (self.directory / "schedule.json").write_bytes(b"{}\n")
        with self.assertRaises(AdmissionError):
            self._schedule()

    def test_terminal_without_claim_and_wrong_chain_reject(self):
        schedule = self._schedule()
        with ExecutionJournal(self.directory / "journal.jsonl", schedule) as journal, self.assertRaises(RunnerValidationError):
            journal.append_terminal({"schema_version": "routes-v2-journal-record", "record_type": "terminal_outcome"})
        self.assertEqual((self.directory / "journal.jsonl").read_bytes(), b"")

    def test_crash_claim_halts_before_fake_client_chat(self):
        schedule = self._schedule()
        with ExecutionJournal(self.directory / "journal.jsonl", schedule) as journal:
            trajectory, attempt = journal.next_trajectory()
            request = {"model": trajectory["model_id"], "options": {"seed": trajectory["seed"]}}
            journal.append_claim({"schema_version": "routes-v2-journal-record", "record_type": "dispatch_claim", "run_id": "crashed", "session_nonce": "crashed", "schedule_index": 0, "attempt": attempt, "trajectory": trajectory, "admission": {"contract_sha256": schedule["contract_sha256"], "manifest_sha256": schedule["manifest_sha256"], "source_gate_sha256": schedule["source_gate_sha256"], "freeze_receipt_sha256": schedule["freeze_receipt_sha256"], "closure_sha256": schedule["closure_sha256"], "schedule_sha256": canonical_json_sha256(schedule), "calibration_sha256": "sha256:" + "1" * 64}, "request": {"base64": __import__("base64").b64encode(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).decode(), "length": len(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()), "sha256": "sha256:" + hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}, "delivery": {"packet_sha256": "sha256:" + "2" * 64, "model_visible_packet_sha256": "sha256:" + "2" * 64, "delivered_evidence_sha256": "sha256:" + "3" * 64}})
        client = FakeClient(self.inventory, [self._response("CALIBRATION"), self._response("VALUE-0")])
        with self._session(client, "after-crash") as session, self.assertRaises(UnknownAfterClaimError):
            session.dispatch_next(expected_answers={f"routes-v2:development:{index}": f"VALUE-{index}" for index in range(6)})
        self.assertEqual(client.chat_calls, 0)

    def test_read_reset_is_terminal_and_retry_after_response_or_later_item_rejects(self):
        client = FakeClient(self.inventory, [self._response("CALIBRATION"), TransportResult("read_error", b"partial", True, "connection_reset")])
        with self._session(client) as session:
            outcome = session.dispatch_next(expected_answers={f"routes-v2:development:{index}": f"VALUE-{index}" for index in range(6)})
        self.assertEqual(outcome["status"], "read_error")
        with ExecutionJournal(self.directory / "journal.jsonl", self._schedule()) as journal:
            trajectory, attempt = journal.next_trajectory()
            self.assertEqual(trajectory["schedule_index"], 1)
            self.assertEqual(attempt, 1)

    def test_only_no_response_transport_failure_gets_one_immediate_retry(self):
        client = FakeClient(
            self.inventory,
            [
                self._response("CALIBRATION"),
                TransportResult("transport_failure_no_response_object", b"", False, "offline"),
                self._response("CALIBRATION"),
                self._response("VALUE-0"),
            ],
        )
        with self._session(client) as session:
            first = session.dispatch_next(expected_answers={f"routes-v2:development:{index}": f"VALUE-{index}" for index in range(6)})
        self.assertEqual(first["status"], "transport_failure_no_response_object")
        records = [json.loads(line) for line in (self.directory / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
        claims = [row for row in records if row["record_type"] == "dispatch_claim"]
        self.assertEqual([(row["schedule_index"], row["attempt"]) for row in claims], [(0, 1)])
        bad_claim = copy.deepcopy(claims[0])
        for field in ("previous_record_sha256", "previous_prefix_sha256", "record_sha256"):
            bad_claim.pop(field)
        bad_claim["schedule_index"] = 1
        bad_claim["attempt"] = 1
        bad_claim["trajectory"] = self._schedule()["trajectories"][1]
        with ExecutionJournal(self.directory / "journal.jsonl", self._schedule()) as journal, self.assertRaises(RunnerValidationError):
            journal.append_claim(bad_claim)
        with self._session(client, "session-b") as session:
            second = session.dispatch_next(expected_answers={f"routes-v2:development:{index}": f"VALUE-{index}" for index in range(6)})
        self.assertEqual(second["status"], "ok")
        records = [json.loads(line) for line in (self.directory / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
        claims = [row for row in records if row["record_type"] == "dispatch_claim"]
        self.assertEqual([(row["schedule_index"], row["attempt"]) for row in claims], [(0, 1), (0, 2)])

    def test_session_receipt_binds_client_endpoint_closure_and_validates_before_return(self):
        client = FakeClient(self.inventory, [self._response("CALIBRATION"), self._response("VALUE-0")])
        with self._session(client) as session:
            outcome = session.dispatch_next(expected_answers={f"routes-v2:development:{index}": f"VALUE-{index}" for index in range(6)})
        self.assertEqual(outcome["status"], "ok")
        records = [json.loads(line) for line in (self.directory / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["record_type"] for row in records], ["dispatch_claim", "terminal_outcome"])
        self.assertEqual(records[-1]["response"]["length"], len(self._response("VALUE-0").response_bytes))
        receipt = json.loads((self.directory / "calibration-session-a.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["closure_sha256"], self.closure["closure_sha256"])
        self.assertNotEqual(receipt["client_binding_sha256"], canonical_json_sha256({"endpoint": "http://other.invalid", "configuration": client.configuration}))
        for client_binding, closure_sha256, nonce in (
            ({"endpoint": "http://other.invalid", "configuration": client.configuration}, self.closure["closure_sha256"], "session-a"),
            ({"endpoint": client.endpoint, "configuration": {"api": "other"}}, self.closure["closure_sha256"], "session-a"),
            ({"endpoint": client.endpoint, "configuration": client.configuration}, "sha256:" + "0" * 64, "session-a"),
            ({"endpoint": client.endpoint, "configuration": client.configuration}, self.closure["closure_sha256"], "other-session"),
        ):
            with self.subTest(client_binding=client_binding, closure_sha256=closure_sha256, nonce=nonce), self.assertRaises(RuntimeValidationError):
                validate_session_calibration(receipt, self.contract, inventory=self.inventory, client_binding=client_binding, closure_sha256=closure_sha256, session_nonce=nonce, model_id=self.contract["models"][0]["id"])


if __name__ == "__main__":
    unittest.main()
