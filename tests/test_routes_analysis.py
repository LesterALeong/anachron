import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from anachron.routes import load_contract
from anachron.routes.analysis import (
    AnalysisValidationError,
    _gate_summary,
    analyze_phase,
    analyze_runner_phase,
    build_blinded_audit_packet,
    build_runner_blinded_audit_packet,
    cohen_kappa,
    paired_topic_cluster_bootstrap,
    write_analysis_artifacts,
)
from anachron.routes.manifest import (
    canonical_json_sha256,
    seal_manifest,
    stable_item_id,
)
from anachron.routes.retrieval import retrieve
from anachron.routes.runner import source_code_sha256
from anachron.routes.sources import discover_topic

ROOT = Path(__file__).parents[1]
CONTRACT_PATH = ROOT / "research" / "routes-v1" / "contract.json"
FIXTURES = Path(__file__).parent / "fixtures" / "routes"


class TestRoutesAnalysis(unittest.TestCase):
    def setUp(self):
        self.contract = load_contract(CONTRACT_PATH)
        self.strict_raw = (FIXTURES / "strict_revision.json").read_bytes()
        self.post_raw = (FIXTURES / "post_revisions.json").read_bytes()
        self.frame = {
            "schema_version": "routes-v1-exante-sampling-frame",
            "github_revision": self.contract["upstreams"]["exante_github"]["revision"],
            "github_artifact_url": self.contract["upstreams"]["exante_github"]["artifact_url"],
            "github_source_sha256": "sha256:" + "a" * 64,
            "huggingface_revision": self.contract["upstreams"]["exante_huggingface"]["revision"],
            "huggingface_artifact_url": self.contract["upstreams"]["exante_huggingface"]["artifact_url"],
            "huggingface_resolved_url": (
                "https://huggingface.co/api/resolve-cache/datasets/yachuanliu/ExAnte/"
                "4e30593e1aff7360fef5aee865117c5c8e05114e/exante_wiki.csv?etag=fixture"
            ),
            "huggingface_etag": "fixture",
            "huggingface_source_sha256": "sha256:" + "b" * 64,
            "observed_row_count": 60,
            "observed_unique_pair_count": 60,
            "topics": [
                topic
                for group in self.contract["sampling"]["topics"].values()
                for topic in group
            ],
        }
        self.manifest = self._manifest()

    def _fetcher(self, url, _timeout):
        if "2013-12-31T23%3A59%3A59Z" in url:
            return self.strict_raw
        if "2014-12-31T23%3A59%3A59Z" in url:
            return self.post_raw
        self.fail(f"unexpected fixture request: {url}")

    def _artifact(self):
        return discover_topic(
            self.contract,
            self.frame,
            phase="pilot",
            title="YouTube",
            fetcher=self._fetcher,
        ).to_dict()

    @staticmethod
    def _evidence(revision, snippet):
        return {
            "revision_id": revision["revision_id"],
            "timestamp": revision["timestamp"],
            "revision_url": revision["revision_url"],
            "mediawiki_sha1": revision["mediawiki_sha1"],
            "raw_response_sha256": revision["raw_response_sha256"],
            "content_sha256": revision["content_sha256"],
            "snippet": snippet,
            "snippet_sha256": "sha256:" + hashlib.sha256(snippet.encode()).hexdigest(),
            "displayed_document_date": "2013-12-31",
        }

    def _manifest(self):
        artifact = self._artifact()
        item_id = stable_item_id("pilot", "YouTube", 2013)
        pair = {
            "item_id": item_id,
            "topic_cluster_id": item_id,
            "study_phase": "pilot",
            "topic": "YouTube",
            "cutoff_year": 2013,
            "sampling_frame_sha256": canonical_json_sha256(self.frame),
            "curation_input_sha256": "sha256:" + "c" * 64,
            "discovery_artifact_sha256": canonical_json_sha256(artifact),
            "discovery_artifact_file": "youtube.json",
            "source_status": "source_valid",
            "post_snapshot_horizon_days": 365,
            "pre": self._evidence(artifact["strict_revision"], "Before revision"),
            "post": self._evidence(artifact["post_snapshot"], "After revision two"),
            "pre_anchor": "Before revision",
            "post_anchor": "After revision two",
            "question": "Which version-specific value is documented for this topic?",
            "pre_answer_aliases": ["Earlier answer"],
            "post_answer_aliases": ["Later answer"],
            "change_type": "event_status",
            "semantic_strength": "clean",
            "notes": "Synthetic fixture only.",
            "license_attribution": {
                "license": "CC BY-SA 4.0",
                "source_family": "English Wikipedia",
                "attribution_text": "English Wikipedia contributors, YouTube revision history.",
            },
            "curation": {
                "status": "human_validated",
                "human_validator_id": "fixture-reviewer",
                "human_validated_at": "2026-09-01T00:00:00Z",
            },
        }
        draft = {
            "schema_version": "routes-v1-curation-draft",
            "sampling_frame_sha256": canonical_json_sha256(self.frame),
            "curation_input_sha256": "sha256:" + "c" * 64,
            "pairs": [pair],
            "rejected_topics": [
                {
                    "study_phase": "pilot",
                    "title": topic["title"],
                    "reason": "Synthetic fixture rejection.",
                }
                for topic in self.contract["sampling"]["topics"]["pilot"]
                if topic["title"] != "YouTube"
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "youtube.json").write_text(json.dumps(artifact), encoding="utf-8")
            return seal_manifest(draft, self.contract, self.frame, directory)

    def _records(self, *, labels=True):
        pair = self.manifest["pairs"][0]
        records = []
        for condition in self.contract["conditions"]:
            for seed in self.contract["execution"]["seeds"]:
                run_id = f"pilot-youtube-{condition}-{seed}"
                identity = {
                    "schema_version": "routes-v1",
                    "run_id": run_id,
                    "topic": "YouTube",
                    "cutoff_year": 2013,
                    "model_id": "qwen2.5:7b",
                    "model_digest": self.contract["models"][0]["digest"],
                    "seed": seed,
                    "condition": condition,
                    "attempt": 1,
                    "study_phase": "pilot",
                }
                calls = []
                trace_valid = False
                if condition != "no_tool":
                    source = pair["pre"] if condition == "strict" else pair["post"]
                    calls = [
                        {
                            "tool": "wikipedia_revision",
                            "title": "YouTube",
                            "revision_timestamp": source["timestamp"],
                            "revision_url": source["revision_url"],
                        }
                    ]
                    trace_valid = True
                records.append(
                    {
                        **identity,
                        "record_type": "trace",
                        "started_at": "2026-09-01T00:00:00Z",
                        "completed_at": "2026-09-01T00:00:01Z",
                        "status": "ok",
                        "trace_valid": trace_valid,
                        "calls": calls,
                    }
                )
                answer = "Later answer" if condition == "misdated" else "Earlier answer"
                response_text = json.dumps({"answer": answer, "citation_ids": []})
                digest = "sha256:" + hashlib.sha256(response_text.encode()).hexdigest()
                records.append(
                    {
                        **identity,
                        "record_type": "response",
                        "completed_at": "2026-09-01T00:00:01Z",
                        "status": "ok",
                        "response_text": response_text,
                        "response_sha256": digest,
                    }
                )
                if labels:
                    label = "post_only" if condition == "misdated" else "pre_only"
                    for labeler_id in ("rater-a", "rater-b"):
                        records.append(
                            {
                                **identity,
                                "record_type": "label",
                                "labeler_id": labeler_id,
                                "labeled_at": "2026-09-01T00:02:00Z",
                                "answer_label": label,
                                "response_sha256": digest,
                            }
                        )
        return records

    def _runner_records_and_labels(self):
        ordinary = self._records()
        traces = {
            (record["run_id"], record["attempt"]): record
            for record in ordinary if record["record_type"] == "trace"
        }
        responses = {
            (record["run_id"], record["attempt"]): record
            for record in ordinary if record["record_type"] == "response"
        }
        labels = [record for record in ordinary if record["record_type"] == "label"]
        runner_records = []
        audit_labels = []
        for key, response in responses.items():
            trace = traces[key]
            retrieval = retrieve(
                self.manifest, self.contract, self.frame,
                item_id=self.manifest["pairs"][0]["item_id"],
                condition=response["condition"], retrieved_at=trace["started_at"],
            )
            raw = json.dumps({"message": {"content": response["response_text"]}}).encode()
            raw_sha = "sha256:" + hashlib.sha256(raw).hexdigest()
            record = {
                "schema_version": "routes-v1-runner-record", "record_type": "trajectory_attempt",
                "run_id": "routes-v1:pilot", "trajectory_id": f"trajectory-{response['condition']}-{response['seed']}",
                "attempt": 1, "study_phase": "pilot", "item_id": self.manifest["pairs"][0]["item_id"],
                "topic": response["topic"], "cutoff_year": response["cutoff_year"],
                "model_id": response["model_id"], "model_digest": response["model_digest"],
                "seed": response["seed"], "condition": response["condition"],
                "started_at": trace["started_at"], "completed_at": response["completed_at"], "status": "ok",
                "contract_sha256": canonical_json_sha256(self.contract),
                "manifest_sha256": canonical_json_sha256(self.manifest),
                "sampling_frame_sha256": canonical_json_sha256(self.frame), "code_sha256": source_code_sha256(),
                "request": {"sha256": canonical_json_sha256({}), "body": {}},
                "retrieval": {"sha256": canonical_json_sha256(retrieval), "result": retrieval},
                "response": {"sha256": raw_sha, "body_base64": __import__("base64").b64encode(raw).decode(), "received_bytes": len(raw)},
                "error": {"kind": None, "message_sha256": None},
            }
            runner_records.append(record)
            audit_id = "routes-v1-" + hashlib.sha256(
                f"{record['trajectory_id']}:{record['run_id']}:1:{raw_sha}".encode()
            ).hexdigest()
            for label in [item for item in labels if item["run_id"] == response["run_id"]]:
                audit_labels.append(
                    {
                        "schema_version": "routes-v1-audit-label", "record_type": "audit_label",
                        "audit_id": audit_id, "labeler_id": label["labeler_id"],
                        "labeled_at": label["labeled_at"], "answer_label": label["answer_label"],
                        "response_sha256": raw_sha,
                    }
                )
        return runner_records, audit_labels

    def test_analysis_is_deterministic_and_never_turns_a_failed_gate_into_a_pass(self):
        first = analyze_phase(
            self.contract, self.frame, self.manifest, self._records(), phase="pilot"
        )
        second = analyze_phase(
            self.contract, self.frame, self.manifest, self._records(), phase="pilot"
        )
        self.assertEqual(first, second)
        self.assertEqual(first["trajectory_count"], 6)
        self.assertEqual(first["primary_effect"]["point_estimate"], 1.0)
        self.assertFalse(first["gates"]["minimum_source_valid_pairs"])
        self.assertFalse(first["all_gates_pass"])
        self.assertTrue(
            all(row["descriptive_only"] for row in first["descriptive_semantic_strength_rows"])
        )

    def test_schedule_rejects_missing_duplicate_and_pilot_mixed_into_input(self):
        records = self._records()
        with self.assertRaises(AnalysisValidationError):
            analyze_phase(self.contract, self.frame, self.manifest, records[:-1], phase="pilot")
        duplicate = copy.deepcopy(records)
        duplicate[0]["run_id"] = "duplicated-trace"
        duplicate[1]["run_id"] = "duplicated-trace"
        with self.assertRaises(AnalysisValidationError):
            analyze_phase(self.contract, self.frame, self.manifest, duplicate, phase="pilot")
        mixed = copy.deepcopy(records)
        mixed.extend(copy.deepcopy(records[:2]))
        mixed[-2]["study_phase"] = "full"
        mixed[-1]["study_phase"] = "full"
        with self.assertRaises(AnalysisValidationError):
            analyze_phase(self.contract, self.frame, self.manifest, mixed, phase="pilot")

    def test_trace_drift_labels_and_blinded_packet_fail_closed(self):
        drifted = self._records()
        strict_trace = next(
            record
            for record in drifted
            if record["record_type"] == "trace" and record["condition"] == "strict"
        )
        strict_trace["calls"][0]["revision_url"] = self.manifest["pairs"][0]["post"]["revision_url"]
        with self.assertRaises(AnalysisValidationError):
            analyze_phase(self.contract, self.frame, self.manifest, drifted, phase="pilot")
        disagreement = self._records()
        next(
            record
            for record in disagreement
            if record["record_type"] == "label" and record["labeler_id"] == "rater-b"
        )["answer_label"] = "mixed"
        result = analyze_phase(
            self.contract, self.frame, self.manifest, disagreement, phase="pilot"
        )
        self.assertEqual(len(result["human_program_disagreements"]), 1)
        self.assertEqual(result["human_program_disagreements"][0]["program_label"], "pre_only")
        packet = build_blinded_audit_packet(
            self.contract, self.frame, self.manifest, self._records(labels=False), phase="pilot"
        )
        self.assertEqual(len(packet["items"]), 6)
        self.assertTrue(all("condition" not in item for item in packet["items"]))

    def test_cluster_bootstrap_is_topic_weighted_and_kappa_edge_cases_are_strict(self):
        effects = {"large-topic": 0.0, "small-topic": 1.0}
        interval = paired_topic_cluster_bootstrap(effects, resamples=10_000, seed=20_260_901)
        self.assertEqual(
            interval,
            paired_topic_cluster_bootstrap(effects, resamples=10_000, seed=20_260_901),
        )
        self.assertEqual(sum(effects.values()) / len(effects), 0.5)
        self.assertNotEqual(0.5, 1 / 11)
        self.assertEqual(cohen_kappa([("pre_only", "pre_only")] * 3), 1.0)
        self.assertEqual(cohen_kappa([("pre_only", "post_only")]), 0.0)
        with self.assertRaises(AnalysisValidationError):
            cohen_kappa([])
        with self.assertRaises(AnalysisValidationError):
            cohen_kappa([("not-a-label", "pre_only")])
        negative = paired_topic_cluster_bootstrap(
            {"left": -1.0, "right": 0.0}, resamples=10_000, seed=20_260_901
        )
        self.assertLess(negative["lower"], 0.0)

    def test_gate_boundaries_do_not_admit_secondary_rescue(self):
        rows = [
            {"condition": "strict", "trace_valid": index < 18, "answer_label": "pre_only"}
            for index in range(20)
        ] + [
            {"condition": "misdated", "trace_valid": True, "answer_label": "post_only"}
            for _ in range(20)
        ]
        effect = {"point_estimate": 0.1, "interval": {"lower": 0.01}}
        gates = _gate_summary(self.contract, "pilot", rows, 0.7, effect, 18, [effect])
        self.assertTrue(all(gates.values()))
        failed = _gate_summary(self.contract, "pilot", rows, 0.699, effect, 18, [effect])
        self.assertFalse(failed["minimum_blinded_two_rater_kappa"])

    def test_artifact_writer_emits_machine_and_blinded_outputs(self):
        result = analyze_phase(
            self.contract, self.frame, self.manifest, self._records(), phase="pilot"
        )
        packet = build_blinded_audit_packet(
            self.contract, self.frame, self.manifest, self._records(labels=False), phase="pilot"
        )
        with tempfile.TemporaryDirectory() as directory:
            write_analysis_artifacts(directory, result, packet)
            names = {path.name for path in Path(directory).iterdir()}
        self.assertEqual(
            names,
            {
                "summary.json",
                "blinded_audit_packet.json",
                "condition_rates.csv",
                "primary_effects.csv",
                "semantic_strength_descriptive.csv",
                "secondary_effects_descriptive.csv",
            },
        )

    def test_runner_ledger_adapter_rejects_binding_drift_and_builds_audit_packet(self):
        records, labels = self._runner_records_and_labels()
        result = analyze_runner_phase(
            self.contract, self.frame, self.manifest, records, labels, phase="pilot"
        )
        self.assertEqual(result["trajectory_count"], 6)
        packet = build_runner_blinded_audit_packet(
            self.contract, self.frame, self.manifest, records, phase="pilot"
        )
        self.assertEqual(len(packet["items"]), 6)
        self.assertEqual(len({item["audit_id"] for item in packet["items"]}), 6)
        self.assertTrue(
            all(
                {"question", "pre_answer_aliases", "post_answer_aliases"}.issubset(item)
                for item in packet["items"]
            )
        )
        drifted = copy.deepcopy(records)
        drifted[0]["manifest_sha256"] = "sha256:" + "0" * 64
        with self.assertRaises(AnalysisValidationError):
            analyze_runner_phase(self.contract, self.frame, self.manifest, drifted, labels, phase="pilot")

    def test_runner_rejects_invalid_audit_timestamp_duplicate_labels_and_status_receipts(self):
        records, labels = self._runner_records_and_labels()
        invalid_timestamp = copy.deepcopy(labels)
        invalid_timestamp[0]["labeled_at"] = "2026-09-01T00:02:00"
        with self.assertRaises(AnalysisValidationError):
            analyze_runner_phase(
                self.contract, self.frame, self.manifest, records, invalid_timestamp, phase="pilot"
            )
        duplicate = copy.deepcopy(labels)
        duplicate.append(copy.deepcopy(duplicate[0]))
        with self.assertRaises(AnalysisValidationError):
            analyze_runner_phase(self.contract, self.frame, self.manifest, records, duplicate, phase="pilot")
        status_tamper = copy.deepcopy(records)
        status_tamper[0]["response"] = {
            "sha256": None, "body_base64": None, "received_bytes": 0,
        }
        with self.assertRaises(AnalysisValidationError):
            analyze_runner_phase(self.contract, self.frame, self.manifest, status_tamper, labels, phase="pilot")


if __name__ == "__main__":
    unittest.main()
