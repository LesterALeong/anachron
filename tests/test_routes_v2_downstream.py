import json
import unittest

from anachron.routes.v2.admission import _VALIDATED_EXECUTION_TOKEN, ValidatedExecution
from anachron.routes.v2.analysis import (
    AnalysisValidationError,
    _public_payload,
    build_private_audit_join,
    public_audit_template,
    reduce_finite_set,
    scan_public_audit_packet,
    validate_public_audit,
)
from anachron.routes.v2.runtime import bytes_receipt
from anachron.routes.v2.schema import load_contract


class TestRoutesV2Downstream(unittest.TestCase):
    @staticmethod
    def _audit_execution(phase: str) -> ValidatedExecution:
        contract = load_contract("research/routes-v2/contract.json")
        specification = contract["evaluation"][phase]
        rows = []
        index = 0
        for topic in specification["topics"]:
            for model_id in specification["models"]:
                for condition in contract["evaluation"]["primary_arms"]:
                    malformed = index == 0
                    raw = (
                        b'{"model":"qwen2.5:7b","oldid":"private-oldid",'
                        b'"url":"https://private.example"}'
                        if malformed
                        else json.dumps({"message": {"content": "ordinary seed 17 text from 1999"}}).encode("utf-8")
                    )
                    response = bytes_receipt(raw)
                    rows.append({
                        "trajectory_id": f"private-trajectory-{index:03d}",
                        "study_phase": phase,
                        "topic_id": topic["title"],
                        "condition": condition,
                        "model_id": model_id,
                        "seed": 17,
                        "status": "malformed_response" if malformed else "ok",
                        "response": response,
                        "request_sha256": f"sha256:{index:064x}",
                        "delivery_sha256": f"sha256:{index + 1000:064x}",
                        "response_sha256": response["sha256"],
                        "terminal_record_sha256": f"sha256:{index + 2000:064x}",
                        "machine_label": "post_only" if condition == "post_misdated_eligible" else "not_post_only",
                        "post_only": int(condition == "post_misdated_eligible"),
                    })
                    index += 1
        return ValidatedExecution(
            _VALIDATED_EXECUTION_TOKEN,
            {"source_gate_sha256": "sha256:" + "a" * 64},
            tuple(rows),
            contract,
            frozenset(
                contract["conditions"]
                + [model["id"] for model in contract["models"]]
                + [model["digest"] for model in contract["models"]]
            ),
        )

    def test_audit_payload_redacts_exact_private_values_but_allows_ordinary_text(self):
        sensitive_hashes = [f"sha256:{value * 64}" for value in "12345"]
        private_values = {
            "qwen2.5:7b", "private-trajectory-000", "post_truthful", "private-oldid",
            "https://private.example", "private-citation", "2020-01-01", "private-run",
            "private-session", *sensitive_hashes,
        }
        response = bytes_receipt(json.dumps({"message": {"content": json.dumps({
            "answer": "model qwen2.5:7b private-trajectory-000 post_truthful private-oldid "
            "https://private.example private-citation 2020-01-01 private-run private-session "
            + " ".join(sensitive_hashes) + " seed 17 in 1999 and 2031-02-03",
            "citation_id": "private-citation",
        })}}).encode("utf-8"))
        payload = _public_payload(response, private_values, b"audit-fixture-private-key")
        self.assertEqual(payload["kind"], "response_content")
        for value in private_values:
            self.assertNotIn(value, payload["content"])
        self.assertIn("[private value redacted]", payload["content"])
        self.assertIn("model", payload["content"])
        self.assertIn("seed 17", payload["content"])
        self.assertIn("1999", payload["content"])
        self.assertIn("2031-02-03", payload["content"])
        raw_envelope = bytes_receipt(json.dumps({"message": {"content": json.dumps({
            "model": "private-model",
            "oldid": "private-oldid",
            "url": "https://private.example",
        })}}).encode("utf-8"))
        self.assertEqual(
            _public_payload(raw_envelope, {"private-model"}, b"audit-fixture-private-key")["kind"],
            "malformed_response",
        )

    def test_malformed_audit_population_stays_exact_and_publicly_redacted(self):
        for phase, expected_population in (("pilot", 36), ("confirmatory", 144)):
            with self.subTest(phase=phase):
                execution = self._audit_execution(phase)
                topics = execution._contract["evaluation"][phase]["topics"]
                private = build_private_audit_join(
                    execution,
                    phase=phase,
                    private_blind_key=b"audit-fixture-private-key",
                    questions={topic["title"]: "What is the answer?" for topic in topics},
                    alias_rubrics={topic["title"]: ["VALUE"] for topic in topics},
                    instructions="Choose the matching alias.",
                )
                packet = private._packet
                self.assertEqual(len(packet["items"]), expected_population)
                self.assertEqual(scan_public_audit_packet(packet), packet)
                public_text = json.dumps(packet, ensure_ascii=False, sort_keys=True)
                malformed = next(
                    item["inspectable_payload"]
                    for item in packet["items"]
                    if item["inspectable_payload"]["kind"] == "malformed_response"
                )
                self.assertEqual(malformed["preview"], "[response bytes withheld: malformed response envelope]")
                self.assertEqual(malformed["parse_error_class"], "response_envelope_unavailable")
                self.assertIn("blinded_digest", malformed)
                self.assertNotIn("sha256", malformed)
                self.assertNotIn("sha256", public_text)
                self.assertNotIn("private-trajectory-000", public_text)
                self.assertNotIn("private-oldid", public_text)
                self.assertNotIn("https://private.example", public_text)

                first = public_audit_template(private, "rater-a")
                second = public_audit_template(private, "rater-b")
                for submission in (first, second):
                    for label in submission["labels"]:
                        label["label"] = "post_only"
                audit = validate_public_audit(private, first, second)
                self.assertEqual(audit.report["coverage"], {"rater-a": expected_population, "rater-b": expected_population})
                self.assertEqual(audit.report["semantic_kappa_denominator"], expected_population - 1)
                result = reduce_finite_set(execution, audit).value
                self.assertTrue(all(result["gates"].values()))

    def test_public_packet_is_allowlisted_not_word_or_year_blacklisted(self):
        packet = {
            "schema_version": "routes-v2-public-audit-packet",
            "phase": "pilot",
            "items": [{
                "audit_id": "audit:fixture",
                "question": "What did the model say in 1999?",
                "alias_rubric": ["VALUE"],
                "inspectable_payload": {"kind": "response_content", "content": "model VALUE 1999"},
                "instructions": "Choose the matching alias.",
            }],
        }
        self.assertEqual(scan_public_audit_packet(packet), packet)
        packet["items"][0]["machine_label"] = "post_only"
        with self.assertRaises(AnalysisValidationError):
            scan_public_audit_packet(packet)

    def test_public_packet_rejects_structural_private_and_envelope_fields(self):
        packet = {
            "schema_version": "routes-v2-public-audit-packet",
            "phase": "pilot",
            "items": [{
                "audit_id": "audit:fixture",
                "question": "Question",
                "alias_rubric": ["VALUE"],
                "inspectable_payload": {"kind": "response_content", "content": "VALUE"},
                "instructions": "Choose the matching alias.",
            }],
        }
        for field in (
            "condition", "model", "model_id", "oldid", "url", "revision_url", "citation_id",
            "presented_document_date", "document_date", "trajectory_id", "request_sha256",
            "delivery_sha256", "response_sha256", "terminal_record_sha256", "machine_label",
            "semantic_eligible", "message",
        ):
            with self.subTest(field=field):
                packet["items"][0]["inspectable_payload"][field] = "private"
                with self.assertRaises(AnalysisValidationError):
                    scan_public_audit_packet(packet)
                del packet["items"][0]["inspectable_payload"][field]

    def test_guarded_execution_rejects_free_form_construction(self):
        with self.assertRaises(TypeError):
            ValidatedExecution(object(), {}, (), {})


if __name__ == "__main__":
    unittest.main()
