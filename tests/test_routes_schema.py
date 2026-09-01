import copy
import unittest
from pathlib import Path

from anachron.routes import (
    ContractValidationError,
    load_contract,
    validate_contract_document,
    validate_experiment_records,
    validate_label_record,
    validate_response_record,
    validate_trace_record,
)

CONTRACT_PATH = Path(__file__).parents[1] / "research" / "routes-v1" / "contract.json"


class TestRoutesContract(unittest.TestCase):
    def setUp(self):
        self.contract = load_contract(CONTRACT_PATH)

    def test_frozen_contract_is_admissible_and_complete(self):
        self.assertEqual(self.contract["sampling"]["pilot_topic_count"], 20)
        self.assertEqual(self.contract["sampling"]["extension_topic_count"], 40)
        self.assertEqual(len(self.contract["sampling"]["topics"]["pilot"]), 20)
        self.assertEqual(len(self.contract["sampling"]["topics"]["extension"]), 40)
        self.assertEqual(self.contract["conditions"], ["no_tool", "strict", "misdated"])
        self.assertFalse(self.contract["execution"]["think"])
        self.assertEqual(self.contract["source_selection"]["post_snapshot_horizon_days"], 365)
        self.assertEqual(self.contract["source_selection"]["snippet_max_chars"], 4000)
        self.assertEqual(
            self.contract["source_selection"]["snippet_context_chars_each_side"], 800
        )
        self.assertEqual(self.contract["full_gates"]["minimum_source_valid_pairs"], 36)
        self.assertEqual(self.contract["analysis"]["analysis_seed"], 20_260_901)
        self.assertTrue(self.contract["full_gates"]["pilot_data_excluded_from_confirmatory"])

    def test_contract_rejects_extra_missing_mutable_and_duplicate_values(self):
        extra = copy.deepcopy(self.contract)
        extra["unexpected"] = True
        with self.assertRaises(ContractValidationError):
            validate_contract_document(extra)
        missing = copy.deepcopy(self.contract)
        del missing["pilot_gates"]
        with self.assertRaises(ContractValidationError):
            validate_contract_document(missing)
        mutable = copy.deepcopy(self.contract)
        mutable["upstreams"]["exante_github"]["url"] = "https://github.com/yachuan/ExAnte"
        with self.assertRaises(ContractValidationError):
            validate_contract_document(mutable)
        duplicate = copy.deepcopy(self.contract)
        duplicate["sampling"]["topics"]["extension"][0] = copy.deepcopy(duplicate["sampling"]["topics"]["pilot"][0])
        with self.assertRaises(ContractValidationError):
            validate_contract_document(duplicate)

    def test_contract_rejects_invalid_hashes_and_changed_predeclared_values(self):
        invalid_hash = copy.deepcopy(self.contract)
        invalid_hash["models"][0]["digest"] = "sha256:bad"
        with self.assertRaises(ContractValidationError):
            validate_contract_document(invalid_hash)
        changed_gate = copy.deepcopy(self.contract)
        changed_gate["pilot_gates"]["minimum_source_valid_pairs"] = 17
        with self.assertRaises(ContractValidationError):
            validate_contract_document(changed_gate)
        changed_full_gate = copy.deepcopy(self.contract)
        changed_full_gate["full_gates"]["pilot_data_excluded_from_confirmatory"] = False
        with self.assertRaises(ContractValidationError):
            validate_contract_document(changed_full_gate)
        changed_horizon = copy.deepcopy(self.contract)
        changed_horizon["source_selection"]["post_snapshot_horizon_days"] = 30
        with self.assertRaises(ContractValidationError):
            validate_contract_document(changed_horizon)
        changed_think = copy.deepcopy(self.contract)
        changed_think["execution"]["think"] = True
        with self.assertRaises(ContractValidationError):
            validate_contract_document(changed_think)
        changed_snippet_limit = copy.deepcopy(self.contract)
        changed_snippet_limit["source_selection"]["snippet_max_chars"] = 2_000
        with self.assertRaises(ContractValidationError):
            validate_contract_document(changed_snippet_limit)
        changed_snippet_context = copy.deepcopy(self.contract)
        changed_snippet_context["source_selection"]["snippet_context_chars_each_side"] = 400
        with self.assertRaises(ContractValidationError):
            validate_contract_document(changed_snippet_context)
        changed_analysis_seed = copy.deepcopy(self.contract)
        changed_analysis_seed["analysis"]["analysis_seed"] = 7
        with self.assertRaises(ContractValidationError):
            validate_contract_document(changed_analysis_seed)
        mutable_artifact = copy.deepcopy(self.contract)
        mutable_artifact["upstreams"]["exante_huggingface"]["artifact_url"] = "https://huggingface.co/datasets/yachuanliu/ExAnte/resolve/main/exante_wiki.csv"
        with self.assertRaises(ContractValidationError):
            validate_contract_document(mutable_artifact)
        naive_time = copy.deepcopy(self.contract)
        naive_time["created_at"] = "2026-09-01T00:00:00"
        with self.assertRaises(ContractValidationError):
            validate_contract_document(naive_time)


class TestRoutesRecords(unittest.TestCase):
    def setUp(self):
        self.contract = load_contract(CONTRACT_PATH)

    def _identity(self, record_type, condition="strict", attempt=1, study_phase="pilot"):
        return {
            "schema_version": "routes-v1",
            "record_type": record_type,
            "run_id": "pilot-youtube-2013-qwen2.5-7b-17-strict",
            "topic": "YouTube",
            "cutoff_year": 2013,
            "model_id": "qwen2.5:7b",
            "model_digest": "sha256:2bada8a7450677000f678be90653b85d364de7db25eb5ea54136ada5f3933730",
            "seed": 17,
            "condition": condition,
            "attempt": attempt,
            "study_phase": study_phase,
        }

    def _trace(self, condition="strict", status="ok", attempt=1):
        record = self._identity("trace", condition, attempt)
        record.update(
            {
                "started_at": "2026-09-01T00:00:00Z",
                "completed_at": "2026-09-01T00:00:01Z",
                "status": status,
                "trace_valid": status == "ok" and condition != "no_tool",
                "calls": [],
            }
        )
        if status == "ok" and condition != "no_tool":
            timestamp = "2013-12-31T23:59:59Z" if condition == "strict" else "2014-01-01T00:00:00Z"
            record["calls"] = [{"tool": "wikipedia_revision", "title": "YouTube", "revision_timestamp": timestamp, "revision_url": "https://en.wikipedia.org/w/index.php?title=YouTube&oldid=123456"}]
        return record

    def _response(self, condition="strict", status="ok", attempt=1):
        record = self._identity("response", condition, attempt)
        record.update(
            {
                "completed_at": "2026-09-01T00:00:01Z",
                "status": status,
                "response_text": "A bounded answer" if status == "ok" else "",
                "response_sha256": "sha256:" + "a" * 64 if status == "ok" else None,
            }
        )
        return record

    def _label(self, condition="strict", answer_label="pre_only", attempt=1):
        record = self._identity("label", condition, attempt)
        record.update(
            {
                "labeler_id": "rater-a",
                "labeled_at": "2026-09-01T00:01:00Z",
                "answer_label": answer_label,
                "response_sha256": (
                    None
                    if answer_label == "invalid_output"
                    else "sha256:" + "a" * 64
                ),
            }
        )
        return record

    def test_valid_strict_misdated_and_no_tool_traces(self):
        validate_trace_record(self._trace("strict"), self.contract)
        validate_trace_record(self._trace("misdated"), self.contract)
        validate_trace_record(self._trace("no_tool"), self.contract)

    def test_trace_rejects_naive_time_mutable_url_and_boundary_violation(self):
        naive = self._trace()
        naive["started_at"] = "2026-09-01T00:00:00"
        with self.assertRaises(ContractValidationError):
            validate_trace_record(naive, self.contract)
        mutable = self._trace()
        mutable["calls"][0]["revision_url"] = "https://en.wikipedia.org/wiki/YouTube"
        with self.assertRaises(ContractValidationError):
            validate_trace_record(mutable, self.contract)
        wrong_boundary = self._trace("misdated")
        wrong_boundary["calls"][0]["revision_timestamp"] = "2013-12-31T23:59:59Z"
        with self.assertRaises(ContractValidationError):
            validate_trace_record(wrong_boundary, self.contract)

    def test_records_reject_malformed_identity_and_labels(self):
        response = self._response()
        response["model_digest"] = "sha256:" + "b" * 64
        with self.assertRaises(ContractValidationError):
            validate_response_record(response, self.contract)
        label = self._label()
        label["answer_label"] = "future_only"
        with self.assertRaises(ContractValidationError):
            validate_label_record(label, self.contract)
        with self.assertRaises(ContractValidationError):
            validate_response_record({"record_type": "response"}, self.contract)
        wrong_phase = self._response()
        wrong_phase["study_phase"] = "pilot"
        wrong_phase["model_id"] = "qwen3:14b-q4_K_M"
        wrong_phase["model_digest"] = "sha256:a8cc1361f3145dc01f6d77c6c82c9116b9ffe3c97b34716fe20418455876c40e"
        with self.assertRaises(ContractValidationError):
            validate_response_record(wrong_phase, self.contract)
        extension_in_pilot = self._response()
        extension_in_pilot["topic"] = "Elon Musk"
        extension_in_pilot["cutoff_year"] = 2012
        with self.assertRaises(ContractValidationError):
            validate_response_record(extension_in_pilot, self.contract)

    def test_cross_record_checks_duplicates_retries_and_failed_labels(self):
        trace = self._trace()
        response = self._response()
        label = self._label()
        validate_experiment_records([trace, response, label], self.contract)
        with self.assertRaises(ContractValidationError):
            validate_experiment_records([trace, trace, response], self.contract)
        same_identity_new_run_id = self._trace()
        same_identity_new_run_id["run_id"] = "duplicate-with-new-id"
        with self.assertRaises(ContractValidationError):
            validate_experiment_records(
                [trace, response, same_identity_new_run_id], self.contract
            )
        failed_trace = self._trace(status="timeout_after_dispatch")
        failed_response = self._response(status="timeout_after_dispatch")
        invalid_label = self._label(answer_label="invalid_output")
        validate_experiment_records([failed_trace, failed_response, invalid_label], self.contract)
        wrong_label = self._label(answer_label="pre_only")
        with self.assertRaises(ContractValidationError):
            validate_experiment_records([failed_trace, failed_response, wrong_label], self.contract)
        retry_trace = self._trace(attempt=2)
        retry_response = self._response(attempt=2)
        with self.assertRaises(ContractValidationError):
            validate_experiment_records([retry_trace, retry_response], self.contract)

    def test_retry_follows_only_transport_failure(self):
        first_trace = self._trace(status="transport_failure_before_response")
        first_response = self._response(status="transport_failure_before_response")
        retry_trace = self._trace(attempt=2)
        retry_response = self._response(attempt=2)
        validate_experiment_records([first_trace, first_response, retry_trace, retry_response], self.contract)
        exhausted_retry_trace = self._trace(
            status="transport_failure_before_response", attempt=2
        )
        exhausted_retry_response = self._response(
            status="transport_failure_before_response", attempt=2
        )
        validate_experiment_records(
            [
                first_trace,
                first_response,
                exhausted_retry_trace,
                exhausted_retry_response,
            ],
            self.contract,
        )


if __name__ == "__main__":
    unittest.main()
