import base64
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from anachron import date_shift, date_shift_provenance
from anachron.date_shift import (
    DateShiftValidationError,
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    invalid_score,
    score_response,
)
from anachron.date_shift_bundle import (
    JournalV3,
    build_request,
    calibration_request,
    finalize_bundle_inputs,
    load_bundle,
    reduce_terminals,
    validate_execution_plan,
    validate_journal_v3,
    validate_runtime_preflight,
    verify_bundle_derivation,
    write_create_only,
)
from anachron.date_shift_provenance import (
    _require_detached_annotated_remote_tag,
    _tracked_bytes,
    _validate_static_python,
    build_audit_scaffold_release,
    verify_imported_sources,
)
from tools import build_date_shift_items, finalize_date_shift_audit, run_date_shift
from tools import capture_date_shift_runtime as capture_runtime
from tools import seal_date_shift_execution_bundle as seal_bundle
from tools.audit_date_shift_other_outputs import redact_temporal_clues
from tools.build_date_shift_items import (
    BuildError,
    build_artifacts,
)

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "research" / "date-shift"


def load(name):
    return json.loads((RESEARCH / name).read_text(encoding="utf-8"))


def completed_audit(accepted_count=2):
    audit = load("author_audit.template.json")
    audit["author_id"] = "fixture-author"
    audit["attested_at_utc"] = "2026-09-02T20:00:00Z"
    audit["attestation"] = (
        "I personally reviewed the bound pre and post excerpts for every proposed item and made every ACCEPT or REJECT decision above."
    )
    for index, decision in enumerate(audit["decisions"]):
        decision["decision"] = "ACCEPT" if index < accepted_count else "REJECT"
        decision["reviewed_at_utc"] = "2026-09-02T20:00:00Z"
        decision["reason"] = (
            "Fixture decision after inspecting the bound source excerpts."
        )
    return audit


def runtime(plan):
    return {
        "schema_version": "date-shift-runtime-preflight-v3",
        "capture_provenance": {
            "scaffold_tag": "date-shift-audit-scaffold-v1",
            "scaffold_commit": "a" * 40,
            "code_closure_sha256": "sha256:" + "a" * 64,
        },
        "captured_at_utc": "2026-09-02T20:00:00Z",
        "endpoint": plan["endpoint"],
        "ollama": {
            "cli_path": "C:/fixture/ollama.exe",
            "cli_sha256": "sha256:" + "b" * 64,
            "cli_version_raw": "ollama version fixture",
            "api_version": "fixture",
            "tags_response_sha256": "sha256:" + "c" * 64,
            "models": [
                {"name": model["id"], "digest": model["digest"]}
                for model in plan["models"]
            ],
        },
        "host": {
            "os": "fixture",
            "python": "fixture",
            "cpu": "fixture",
            "ram_bytes": 1,
            "video_adapters": [
                {
                    "name": "fixture",
                    "driver_version": "fixture",
                    "pnp_device_id": "fixture",
                }
            ],
            "video_adapter_capture_sha256": "sha256:" + "d" * 64,
        },
        "context_tokens": plan["decoding"]["num_ctx"],
    }


def bundle(accepted_count=2):
    plan = load("execution_plan.json")
    frame, items, contract, schedule = finalize_bundle_inputs(
        load("proposed_frame.json"),
        load("proposed_items.json"),
        completed_audit(accepted_count),
        plan,
        runtime(plan),
    )
    contract = {
        **contract,
        "models": plan["models"],
        "endpoint": plan["endpoint"],
        "decoding": plan["decoding"],
        "analysis": plan["analysis"],
        "calibration": plan["calibration"],
    }
    return {
        "manifest": {"fixture": True},
        "execution_plan": plan,
        "runtime_preflight": runtime(plan),
        "execution_contract": contract,
        "audited_frame": frame,
        "audited_items": items,
        "schedule": schedule,
        "author_audit": completed_audit(accepted_count),
    }


def write_sealed_bundle(directory, study):
    artifacts = {
        "author_audit.json": study["author_audit"],
        "runtime_preflight.json": study["runtime_preflight"],
        "audited_frame.json": study["audited_frame"],
        "audited_items.json": study["audited_items"],
        "execution_contract.json": study["execution_contract"],
        "schedule.json": study["schedule"],
        "execution_plan.json": study["execution_plan"],
    }
    for name, value in artifacts.items():
        write_create_only(directory / name, value)
    raw = {name: (directory / name).read_bytes() for name in artifacts}
    without_id = {
        "schema_version": "date-shift-execution-bundle-v2",
        "bundle_directory_name": directory.name,
        "scaffold_release_sha256": "sha256:" + "f" * 64,
        "author_audit_sha256": bytes_sha256(raw["author_audit.json"]),
        "runtime_preflight_sha256": bytes_sha256(raw["runtime_preflight.json"]),
        "contract_sha256": bytes_sha256(raw["execution_contract.json"]),
        "schedule_sha256": bytes_sha256(raw["schedule.json"]),
        "artifacts": {name: bytes_sha256(value) for name, value in raw.items()},
    }
    manifest = {
        **without_id,
        "bundle_id": bytes_sha256(canonical_bytes(without_id)),
    }
    write_create_only(directory / "bundle_manifest.json", manifest)
    write_create_only(
        directory / "publication.json",
        {
            "schema_version": "date-shift-bundle-publication-v1",
            "bundle_id": manifest["bundle_id"],
            "bundle_directory_name": directory.name,
            "manifest_sha256": bytes_sha256(
                (directory / "bundle_manifest.json").read_bytes()
            ),
        },
    )


def legacy_v2_fixture():
    digest = lambda value: "sha256:" + value * 64
    document_text = "The post-cutoff answer is POST."
    document = {
        "text": document_text,
        "sha256": bytes_sha256(document_text.encode("utf-8")),
        "utf8_bytes": len(document_text.encode("utf-8")),
    }
    candidates = []
    for index in range(60):
        if index == 0:
            candidates.append(
                {
                    "frame_index": index,
                    "topic": "Legacy topic 0",
                    "cutoff_year": 2000,
                    "status": "accepted",
                    "item_id": "legacy-v2:0",
                }
            )
        elif index < 54:
            candidates.append(
                {
                    "frame_index": index,
                    "topic": f"Legacy topic {index}",
                    "cutoff_year": 2000,
                    "status": "rejected",
                    "item_id": f"legacy-v2:{index}",
                    "audit_reason": "Retained fixture rejection.",
                }
            )
        else:
            candidates.append(
                {
                    "frame_index": index,
                    "topic": f"Legacy topic {index}",
                    "cutoff_year": 2000,
                    "status": "excluded",
                    "reason": "Retained fixture exclusion.",
                }
            )
    frame = {
        "schema_version": "date-shift-audited-frame-v2",
        "upstream": {
            "source": "fixture source",
            "github_revision": "fixture-github",
            "github_artifact_url": "https://example.test/github",
            "github_source_sha256": digest("a"),
            "huggingface_revision": "fixture-huggingface",
            "huggingface_artifact_url": "https://example.test/huggingface",
            "huggingface_source_sha256": digest("b"),
            "legacy_sampling_frame_sha256": digest("c"),
        },
        "candidates": candidates,
    }
    source_side = lambda oldid, timestamp, excerpt_sha: {
        "immutable_url": f"https://example.test/w/index.php?oldid={oldid}",
        "timestamp": timestamp,
        "full_content_sha256": digest("d" if oldid == 1 else "e"),
        "anchor_sha256": digest("f" if oldid == 1 else "0"),
        "anchor_start_offset": 0,
        "anchor_end_offset": 1,
        "excerpt_sha256": excerpt_sha,
        "excerpt_start_offset": 0,
        "excerpt_end_offset": 1,
    }
    items = {
        "schema_version": "date-shift-audited-items-v2",
        "frame_sha256": canonical_sha256(frame),
        "author_audit_sha256": digest("1"),
        "items": [
            {
                "item_id": "legacy-v2:0",
                "frame_index": 0,
                "topic_cluster_id": "legacy-v2:0",
                "topic": "Legacy topic 0",
                "cutoff_date": "2000-12-31",
                "question": "What answer does the document state?",
                "citation_id": "LEGACY-CITATION",
                "presented_document_date_truthful": "2001-01-01",
                "presented_document_date_backdated": "2000-12-31",
                "document_content": document,
                "pre_answer_aliases": ["PRE"],
                "post_answer_aliases": ["POST"],
                "source_provenance": {
                    "legacy_raw_artifact_sha256": digest("2"),
                    "pre": source_side(1, "2000-01-01T00:00:00Z", digest("3")),
                    "post": source_side(
                        2, "2001-01-01T00:00:00Z", document["sha256"]
                    ),
                },
            }
        ],
    }
    contract = {
        "schema_version": "date-shift-execution-contract-v2",
        "study_id": "legacy-v2-fixture",
        "status": "pre_outcome_frozen",
        "frame_size": 60,
        "accepted_item_count": 1,
        "frame_sha256": canonical_sha256(frame),
        "items_sha256": canonical_sha256(items),
        "endpoint": "http://127.0.0.1:11434",
        "models": [
            {"id": "legacy-model-a", "digest": digest("4")},
            {"id": "legacy-model-b", "digest": digest("5")},
        ],
        "seed": 1,
        "temperature": 0,
        "num_predict": 32,
        "timeout_seconds": 1,
        "primary_arms": ["post_truthful", "post_backdated_eligible"],
        "prompt_template_version": "legacy-v2-fixture",
        "think": False,
        "decoding": {
            "temperature": 0,
            "seed": 1,
            "num_predict": 32,
            "top_k": 40,
            "top_p": 0.9,
            "min_p": 0.0,
            "repeat_penalty": 1.0,
            "num_ctx": 8192,
        },
        "bounds": {"max_document_utf8_bytes": 4096},
        "analysis": {
            "primary_outcome": "forward_transition_post_exact",
            "estimand": "mean_paired_truthful_nonpost_to_backdated_post_exact_rate",
            "bootstrap_seed": 1,
            "bootstrap_replicates": 1,
        },
        "calibration": {
            "question": "What answer does the document state?",
            "document_content": "The answer is CALIBRATION.",
            "citation_id": "LEGACY-CALIBRATION",
            "expected_answer": "CALIBRATION",
        },
        "runtime_evidence": {
            "schema_version": "date-shift-runtime-evidence-v2",
            "captured_at_utc": "2026-09-02T20:00:00Z",
            "ollama_cli_version": "fixture-cli",
            "ollama_api_version": "fixture-api",
            "ollama_ps": "fixture model evidence",
            "inventory_sha256": digest("6"),
            "os": "fixture-os",
            "python_version": "fixture-python",
            "cpu": "fixture-cpu",
            "ram_bytes": 1,
            "gpu": "fixture-gpu",
            "gpu_driver": "fixture-driver",
            "context_tokens": 8192,
        },
    }
    date_shift.validate_study(contract, frame, items)
    return {"contract": contract, "frame": frame, "items": items}


class TestBundleConstruction(unittest.TestCase):
    def test_legacy_loose_execution_refuses_valid_v2_fixture_before_side_effects(self):
        fixture = legacy_v2_fixture()

        class SpyClient:
            def __init__(self):
                self.inventory_calls = 0
                self.chat_calls = 0

            def inventory(self, _timeout):
                self.inventory_calls += 1
                return {}

            def chat(self, _request, _timeout):
                self.chat_calls += 1

        client = SpyClient()
        with tempfile.TemporaryDirectory() as temporary:
            journal_path = Path(temporary) / "new" / "legacy-journal.jsonl"
            calls = (
                lambda: date_shift.finalize_author_audit(
                    fixture["frame"], fixture["items"], {}, fixture["contract"]
                ),
                lambda: date_shift.ExecutionJournal(journal_path, {"trajectories": []}),
                lambda: date_shift.DateShiftRunner(
                    fixture["contract"],
                    fixture["items"],
                    {"trajectories": []},
                    journal_path,
                    client,
                    [],
                ),
                lambda: date_shift.admit_client(fixture["contract"], client),
                lambda: date_shift.calibration_request(
                    fixture["contract"], "legacy-model-a"
                ),
                lambda: date_shift.run_calibrations(fixture["contract"], client),
            )
            for call in calls:
                with self.assertRaisesRegex(
                    DateShiftValidationError, "only a sealed V3 bundle may execute"
                ):
                    call()
            self.assertFalse(journal_path.exists())
            self.assertFalse(journal_path.parent.exists())
        self.assertEqual(client.inventory_calls, 0)
        self.assertEqual(client.chat_calls, 0)

    def test_static_plan_and_runtime_require_exact_identities(self):
        plan = validate_execution_plan(load("execution_plan.json"))
        self.assertEqual(plan["models"][0]["id"], "qwen2.5:7b")
        validate_runtime_preflight(runtime(plan), plan)
        altered = runtime(plan)
        altered["ollama"]["models"][0]["digest"] = "sha256:" + "0" * 64
        with self.assertRaises(DateShiftValidationError):
            validate_runtime_preflight(altered, plan)

    def test_author_audit_seals_dynamic_n_without_a_runtime_settings_file(self):
        result = bundle(2)
        self.assertEqual(result["execution_contract"]["accepted_item_count"], 2)
        self.assertEqual(len(result["schedule"]["trajectories"]), 8)

    def test_temp_bundle_names_and_imported_checkout_mismatch_fail(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            self.assertRaises(DateShiftValidationError),
        ):
            load_bundle(Path(temporary) / "bundle.incomplete-123")
        original = date_shift.__file__
        try:
            date_shift.__file__ = str(ROOT / "outside.py")
            with self.assertRaises(DateShiftValidationError):
                verify_imported_sources(ROOT)
        finally:
            date_shift.__file__ = original

    def test_raw_constructor_refuses_missing_and_tampered_discovery_artifacts(self):
        exclusions = {
            (f"Excluded topic {index}", 2000): "fixture exclusion"
            for index in range(6)
        }
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            topics = [
                {"title": f"Topic {index}", "cutoff_year": 2000}
                for index in range(54)
            ] + [
                {"title": title, "cutoff_year": cutoff_year}
                for title, cutoff_year in exclusions
            ]
            sampling = {
                "topics": topics,
                "github_revision": "fixture",
                "github_artifact_url": "https://example.test/github",
                "github_source_sha256": "sha256:" + "a" * 64,
                "huggingface_revision": "fixture",
                "huggingface_artifact_url": "https://example.test/huggingface",
                "huggingface_source_sha256": "sha256:" + "b" * 64,
            }
            sampling_path = repository / "research/routes-v1/sampling_frame.json"
            sampling_path.parent.mkdir(parents=True)
            sampling_path.write_bytes(canonical_bytes(sampling))
            pairs, raw_paths = {}, []
            for index in range(54):
                title, filename = f"Topic {index}", f"{index:02d}.json"
                pre_content, post_content = (
                    f"pre anchor {index}",
                    f"post anchor {index}",
                )
                pre = {
                    "revision_id": index * 2 + 1,
                    "revision_url": "https://en.wikipedia.org/w/index.php?oldid="
                    + str(index * 2 + 1),
                    "timestamp": "2000-01-01T00:00:00Z",
                    "content": pre_content,
                    "content_sha256": bytes_sha256(pre_content.encode("utf-8")),
                }
                post = {
                    "revision_id": index * 2 + 2,
                    "revision_url": "https://en.wikipedia.org/w/index.php?oldid="
                    + str(index * 2 + 2),
                    "timestamp": "2001-01-01T00:00:00Z",
                    "content": post_content,
                    "content_sha256": bytes_sha256(post_content.encode("utf-8")),
                }
                raw = {
                    "title": title,
                    "cutoff_year": 2000,
                    "strict_revision": pre,
                    "post_snapshot": post,
                }
                raw_path = (
                    repository
                    / "research/routes-v1/artifacts/discovery/pilot"
                    / filename
                )
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_bytes(canonical_bytes(raw))
                raw_paths.append(raw_path)
                pairs[(title, 2000)] = (
                    {
                        "discovery_artifact_file": filename,
                        "discovery_artifact_sha256": canonical_sha256(raw),
                        "pre": {
                            key: pre[key]
                            for key in (
                                "revision_id",
                                "revision_url",
                                "timestamp",
                                "content_sha256",
                            )
                        },
                        "post": {
                            key: post[key]
                            for key in (
                                "revision_id",
                                "revision_url",
                                "timestamp",
                                "content_sha256",
                            )
                        },
                        "pre_anchor": pre_content,
                        "post_anchor": post_content,
                        "pre_answer_aliases": [f"PRE-{index}"],
                        "post_answer_aliases": [f"POST-{index}"],
                        "question": f"What changed for topic {index}?",
                    },
                    "pilot",
                )
            with (
                mock.patch.object(
                    build_date_shift_items, "_draft_pairs", return_value=pairs
                ),
                mock.patch.object(
                    build_date_shift_items, "_EXCLUSIONS", exclusions
                ),
            ):
                frame, items = build_artifacts(repository)
                self.assertEqual(len(frame["candidates"]), 60)
                self.assertEqual(len(items["proposed_items"]), 54)
                raw_paths[0].unlink()
                with self.assertRaisesRegex(BuildError, "cannot load JSON object"):
                    build_artifacts(repository)
                raw_paths[0].write_bytes(
                    canonical_bytes(
                        {
                            "title": "Topic 0",
                            "cutoff_year": 2000,
                            "strict_revision": {},
                            "post_snapshot": {},
                        }
                    )
                )
                with self.assertRaisesRegex(BuildError, "discovery artifact hash drifted"):
                    build_artifacts(repository)

    def test_bundle_rejects_noncanonical_and_duplicate_json_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "sealed"
            write_sealed_bundle(directory, bundle(1))
            self.assertEqual(
                load_bundle(directory)["execution_contract"]["accepted_item_count"], 1
            )
            audit_path = directory / "author_audit.json"
            audit_path.write_bytes(audit_path.read_bytes() + b"\n")
            with self.assertRaises(DateShiftValidationError):
                load_bundle(directory)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.json"
            path.write_bytes(b'{"duplicate":1,"duplicate":2}')
            from anachron.date_shift_bundle import load_canonical_object

            with self.assertRaises(DateShiftValidationError):
                load_canonical_object(path)

    def test_bundle_raw_hash_rejects_whitespace_and_derived_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "sealed"
            write_sealed_bundle(directory, bundle(1))
            contract_path = directory / "execution_contract.json"
            value = json.loads(contract_path.read_text(encoding="utf-8"))
            contract_path.write_bytes(canonical_bytes(value) + b" ")
            with self.assertRaises(DateShiftValidationError):
                load_bundle(directory)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "sealed"
            write_sealed_bundle(directory, bundle(1))
            contract_path = directory / "execution_contract.json"
            value = json.loads(contract_path.read_text(encoding="utf-8"))
            value["accepted_item_count"] = 2
            contract_path.write_bytes(canonical_bytes(value))
            with self.assertRaises(DateShiftValidationError):
                load_bundle(directory)


class TestJournalAndReduction(unittest.TestCase):
    def _complete_journal(self, study, directory):
        path = directory / "run" / "journal.jsonl"
        journal = JournalV3(path, study)
        journal.create()
        journal.append("admission_terminal", status="ok")
        for model in study["execution_contract"]["models"]:
            request = calibration_request(study["execution_plan"], model["id"])
            request_bytes = json.dumps(
                request, sort_keys=True, separators=(",", ":")
            ).encode()
            journal.append(
                "calibration_claim",
                model_id=model["id"],
                request_sha256="sha256:" + hashlib.sha256(request_bytes).hexdigest(),
                request_base64=base64.b64encode(request_bytes).decode(),
            )
            calibration_content = json.dumps(
                {
                    "answer": study["execution_plan"]["calibration"]["expected_answer"],
                    "citation_ids": [
                        study["execution_plan"]["calibration"]["citation_id"]
                    ],
                }
            )
            calibration_raw = json.dumps(
                {
                    "model": model["id"],
                    "done": True,
                    "message": {"role": "assistant", "content": calibration_content},
                },
                separators=(",", ":"),
            ).encode()
            journal.append(
                "calibration_terminal",
                model_id=model["id"],
                status="ok",
                response_sha256="sha256:" + hashlib.sha256(calibration_raw).hexdigest(),
                response_base64=base64.b64encode(calibration_raw).decode(),
            )
            raw = (
                "NAME ID SIZE PROCESSOR UNTIL\n"
                + model["id"]
                + " 123 1 GB 100% GPU Forever\n"
            ).encode()
            journal.append(
                "loaded_backend_evidence",
                model_id=model["id"],
                ollama_ps_base64=base64.b64encode(raw).decode(),
                ollama_ps_sha256="sha256:" + hashlib.sha256(raw).hexdigest(),
            )
        journal.append("phase_transition", to="science")
        terminals = []
        for trajectory in study["schedule"]["trajectories"]:
            item = study["audited_items"]["items"][trajectory["item_index"]]
            answer = (
                item["post_answer_aliases"][0]
                if trajectory["arm"] == "post_backdated_eligible"
                else "ABSTAIN"
            )
            score = score_response(
                json.dumps(
                    {
                        "answer": answer,
                        "citation_ids": []
                        if answer == "ABSTAIN"
                        else [item["citation_id"]],
                    }
                ),
                item,
            )
            status = "ok"
            if trajectory["schedule_index"] == 0:
                score = invalid_score()
                status = "client_exception"
            request = build_request(study["execution_plan"], item, trajectory)
            request_bytes = json.dumps(
                request, sort_keys=True, separators=(",", ":")
            ).encode()
            journal.append(
                "dispatch_claim",
                schedule_index=trajectory["schedule_index"],
                request_sha256="sha256:" + hashlib.sha256(request_bytes).hexdigest(),
                request_base64=base64.b64encode(request_bytes).decode(),
            )
            raw = (
                b""
                if status != "ok"
                else json.dumps(
                    {
                        "model": trajectory["model_id"],
                        "done": True,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "answer": answer,
                                    "citation_ids": []
                                    if answer == "ABSTAIN"
                                    else [item["citation_id"]],
                                }
                            ),
                        },
                    },
                    separators=(",", ":"),
                ).encode()
            )
            row = {
                "schedule_index": trajectory["schedule_index"],
                "status": status,
                "score": score,
                "response_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "response_base64": base64.b64encode(raw).decode(),
            }
            journal.append("terminal_outcome", **row)
            terminals.append(row)
        journal.append("run_terminal", status="science_complete")
        return path, terminals

    def test_journal_requires_calibration_backend_before_science(self):
        study = bundle(2)
        with tempfile.TemporaryDirectory() as temporary:
            path, _ = self._complete_journal(study, Path(temporary))
            self.assertEqual(
                len(validate_journal_v3(path, study)), 1 + 1 + 6 + 1 + 16 + 1
            )
            lines = path.read_text(encoding="utf-8").splitlines()
            row = json.loads(lines[4])
            row["model_id"] = "wrong-model"
            lines[4] = json.dumps(row, sort_keys=True, separators=(",", ":"))
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaises(DateShiftValidationError):
                validate_journal_v3(path, study)

    def test_complete_valid_and_itt_have_distinct_denominators(self):
        study = bundle(2)
        with tempfile.TemporaryDirectory() as temporary:
            _, terminals = self._complete_journal(study, Path(temporary))
        result = reduce_terminals(study, terminals)
        self.assertLess(
            result["complete_valid_primary"]["included_pairs"],
            result["all_planned_cell_itt"]["included_pairs"],
        )
        self.assertEqual(len(result["by_model_by_arm_invalids"]), 4)
        self.assertIn("reverse", result["all_planned_cell_itt"])
        self.assertIn("truthful_leakage", result["all_planned_cell_itt"])


class TestProvenanceAdmission(unittest.TestCase):
    def _git(self, repository, *arguments):
        subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
        )

    def _command(self, repository, *arguments):
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()

    def _tagged_remote(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        repository, remote = root / "repository", root / "remote.git"
        self._git(root, "init", str(repository))
        self._git(repository, "config", "user.email", "fixture@example.test")
        self._git(repository, "config", "user.name", "Fixture")
        (repository / "payload.txt").write_text("one\n", encoding="utf-8")
        self._git(repository, "add", "payload.txt")
        self._git(repository, "commit", "-m", "fixture")
        self._git(root, "init", "--bare", str(remote))
        self._git(repository, "remote", "add", "origin", str(remote))
        self._git(repository, "push", "origin", "HEAD")
        self._git(repository, "tag", "-a", "fixture-v1", "-m", "fixture tag")
        self._git(repository, "push", "origin", "refs/tags/fixture-v1")
        self._git(repository, "checkout", "--detach", "HEAD")
        head = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return temporary, repository, remote, head

    def _child(self, repository, source, *, check=True):
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                "import sys; sys.path.insert(0, '.'); " + source,
            ],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if check and completed.returncode:
            self.fail(completed.stderr)
        return completed

    def _released_scaffold(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        repository, remote = root / "repository", root / "remote.git"
        self._git(root, "init", str(repository))
        self._git(repository, "config", "user.email", "fixture@example.test")
        self._git(repository, "config", "user.name", "Fixture")
        self._git(repository, "branch", "-M", "master")
        for relative in (
            *date_shift_provenance._BOUND_TEXT,
            *date_shift_provenance._CLOSURE,
            ".gitignore",
            "research/date-shift/author_audit.template.json",
        ):
            source, destination = ROOT / relative, repository / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        self.assertFalse((repository / "research/routes-v1/artifacts").exists())
        self._git(root, "init", "--bare", str(remote))
        self._git(repository, "remote", "add", "origin", str(remote))
        self._child(
            repository,
            "from tools.build_date_shift_audit_scaffold_release import main; "
            "raise SystemExit(main(['--repository', '.', '--tag', 'fixture-v1', "
            "'--output', 'research/date-shift/audit_scaffold_release.json']))",
        )
        self._git(repository, "add", "--all")
        self._git(repository, "commit", "-m", "fixture audit scaffold")
        self.assertEqual(
            self._command(repository, "rev-list", "--count", "HEAD"), "1"
        )
        self._git(repository, "push", "origin", "HEAD:refs/heads/master")
        self._git(repository, "tag", "-a", "fixture-v1", "-m", "fixture tag")
        self._git(repository, "push", "origin", "refs/tags/fixture-v1")
        self._git(repository, "checkout", "--detach", "HEAD")
        return temporary, repository

    def _assert_child_admission(self, repository, *, check=True):
        return self._child(
            repository,
            "import json; from pathlib import Path; "
            "from anachron.date_shift_provenance import admit_scaffold_repository; "
            "print(json.dumps(admit_scaffold_repository(Path('.')), sort_keys=True))",
            check=check,
        )

    def test_static_admission_allows_only_audited_pathfinder_read(self):
        _validate_static_python(ROOT, ("anachron/date_shift_provenance.py",))
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            source = (ROOT / "anachron/date_shift_provenance.py").read_text(
                encoding="utf-8"
            )
            (repository / "bad.py").write_text(
                source + "\nsys.path.append('bad')\n", encoding="utf-8"
            )
            with self.assertRaises(DateShiftValidationError):
                _validate_static_python(repository, ("bad.py",))

    def test_detached_annotated_local_and_remote_tag_admission(self):
        temporary, repository, _, head = self._tagged_remote()
        try:
            _require_detached_annotated_remote_tag(repository, "fixture-v1", head)
            self._git(repository, "tag", "-d", "fixture-v1")
            self._git(repository, "tag", "fixture-v1")
            with self.assertRaises(DateShiftValidationError):
                _require_detached_annotated_remote_tag(repository, "fixture-v1", head)
        finally:
            temporary.cleanup()

    def test_remote_tag_mismatch_refuses(self):
        temporary, repository, _remote, head = self._tagged_remote()
        try:
            self._git(repository, "checkout", "-b", "other")
            (repository / "payload.txt").write_text("two\n", encoding="utf-8")
            self._git(repository, "commit", "-am", "other")
            self._git(repository, "tag", "-d", "fixture-v1")
            self._git(repository, "tag", "-a", "fixture-v1", "-m", "new tag")
            self._git(repository, "checkout", "--detach", "HEAD")
            new_head = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertNotEqual(new_head, head)
            with self.assertRaises(DateShiftValidationError):
                _require_detached_annotated_remote_tag(repository, "fixture-v1", new_head)
        finally:
            temporary.cleanup()

    def test_pathfinder_detects_cached_module_origin_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            fake_root = Path(temporary)
            package = fake_root / "anachron"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "date_shift.py").write_text("x = 1\n", encoding="utf-8")
            sys.path.insert(0, str(fake_root))
            try:
                import importlib

                importlib.invalidate_caches()
                with self.assertRaises(DateShiftValidationError):
                    verify_imported_sources(ROOT)
            finally:
                sys.path.remove(str(fake_root))

    def test_committed_blob_bytes_detect_lf_worktree_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self._git(repository, "init")
            self._git(repository, "config", "user.email", "fixture@example.test")
            self._git(repository, "config", "user.name", "Fixture")
            path = repository / "fixture.py"
            path.write_bytes(b"value = 1\n")
            self._git(repository, "add", "fixture.py")
            self._git(repository, "commit", "-m", "fixture")
            head = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            path.write_bytes(b"value = 1\r\n")
            self.assertEqual(_tracked_bytes(repository, head, "fixture.py"), b"value = 1\n")
            self.assertNotEqual(path.read_bytes(), _tracked_bytes(repository, head, "fixture.py"))

    def test_released_scaffold_admits_in_a_fresh_child_and_executes_all_stages(self):
        temporary, repository = self._released_scaffold()
        try:
            admitted = self._assert_child_admission(repository)
            self.assertIn('"scaffold_tag": "fixture-v1"', admitted.stdout)
            external = Path(temporary.name) / "external"
            pipeline = "\n".join(
                (
                    "import base64",
                    "import json",
                    "from pathlib import Path",
                    "from unittest import mock",
                    "from anachron.date_shift import TransportOutcome, bytes_sha256, canonical_bytes",
                    "from tools import capture_date_shift_runtime as capture",
                    "from tools import finalize_date_shift_audit as finalize",
                    "from tools import run_date_shift as run",
                    "from tools import seal_date_shift_execution_bundle as seal",
                    f"external = Path({str(external)!r})",
                    "external.mkdir()",
                    "plan = json.loads(Path('research/date-shift/execution_plan.json').read_text(encoding='utf-8'))",
                    "audit = json.loads(Path('research/date-shift/author_audit.template.json').read_text(encoding='utf-8'))",
                    "audit['author_id'] = 'fixture-author'",
                    "audit['attested_at_utc'] = '2026-09-02T20:00:00Z'",
                    "audit['attestation'] = 'I personally reviewed the bound pre and post excerpts for every proposed item and made every ACCEPT or REJECT decision above.'",
                    "for index, decision in enumerate(audit['decisions']):",
                    "    decision['decision'] = 'ACCEPT' if index == 0 else 'REJECT'",
                    "    decision['reviewed_at_utc'] = '2026-09-02T20:00:00Z'",
                    "    decision['reason'] = 'Fixture author decision after source review.'",
                    "editable = external / 'author_audit.editable.json'",
                    "editable.write_text(json.dumps(audit), encoding='utf-8')",
                    "inventory = {'models': [{'name': row['id'], 'digest': row['digest']} for row in plan['models']]}",
                    "with mock.patch.object(capture, '_api', side_effect=[inventory, {'version': 'fixture'}]), mock.patch.object(capture, '_command', return_value=b'ollama version fixture\\n'), mock.patch.object(capture, '_video_adapters', return_value=([{'name': 'fixture', 'driver_version': 'fixture', 'pnp_device_id': 'fixture'}], 'sha256:' + 'd' * 64)), mock.patch.object(capture, '_ram_bytes', return_value=1), mock.patch.object(capture.shutil, 'which', return_value=__import__('sys').executable):",
                    "    capture.main(['--repository', '.', '--endpoint', plan['endpoint'], '--context-tokens', str(plan['decoding']['num_ctx']), '--output', str(external / 'runtime.json')])",
                    "finalize.main(['--repository', '.', '--input', str(editable), '--output', str(external / 'author_audit.json')])",
                    "seal.main(['--repository', '.', '--author-audit', str(external / 'author_audit.json'), '--runtime-preflight', str(external / 'runtime.json'), '--bundle-dir', str(external / 'bundle')])",
                    "class FakeClient:",
                    "    def __init__(self, endpoint): self.endpoint = endpoint",
                    "    def inventory(self, timeout): return {row['id']: row['digest'] for row in plan['models']}",
                    "    def chat(self, request, timeout):",
                    "        payload = json.loads(request['messages'][1]['content'])",
                    "        answer = 'CALIBRATION' if payload['citation_id'] == 'CAL-DATE-SHIFT' else 'ABSTAIN'",
                    "        citations = ['CAL-DATE-SHIFT'] if answer == 'CALIBRATION' else []",
                    "        return TransportOutcome('ok', canonical_bytes({'model': request['model'], 'done': True, 'message': {'role': 'assistant', 'content': json.dumps({'answer': answer, 'citation_ids': citations}, separators=(',', ':'))}}))",
                    "def backend(model_id):",
                    "    raw = ('NAME ID PROCESSOR\\n' + model_id + ' fixture GPU\\n').encode()",
                    "    return {'model_id': model_id, 'ollama_ps_base64': base64.b64encode(raw).decode('ascii'), 'ollama_ps_sha256': bytes_sha256(raw)}",
                    "with mock.patch.object(run, 'OllamaClient', FakeClient), mock.patch.object(run, '_backend_evidence', side_effect=backend):",
                    "    run.main(['--repository', '.', '--bundle-dir', str(external / 'bundle'), '--run-dir', str(external / 'run')])",
                    "assert (external / 'run' / 'run_receipt.json').is_file()",
                )
            )
            self._child(repository, pipeline)
        finally:
            temporary.cleanup()

    def test_released_scaffold_refuses_receipt_closure_and_hidden_blob_drift(self):
        for drift in ("receipt", "closure", "hidden_blob"):
            temporary, repository = self._released_scaffold()
            try:
                receipt_path = repository / "research/date-shift/audit_scaffold_release.json"
                source_path = repository / "anachron/date_shift.py"
                if drift == "receipt":
                    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                    receipt["bound_text_sha256"][".gitattributes"] = "sha256:" + "0" * 64
                    receipt_path.write_bytes(canonical_bytes(receipt))
                    self._git(
                        repository,
                        "update-index",
                        "--assume-unchanged",
                        "research/date-shift/audit_scaffold_release.json",
                    )
                else:
                    source_path.write_bytes(source_path.read_bytes() + b"\n# fixture drift\n")
                    self._git(
                        repository,
                        "update-index",
                        "--assume-unchanged",
                        "anachron/date_shift.py",
                    )
                    if drift == "hidden_blob":
                        receipt_path.write_bytes(
                            canonical_bytes(
                                build_audit_scaffold_release(repository, "fixture-v1")
                            )
                        )
                        self._git(
                            repository,
                            "update-index",
                            "--assume-unchanged",
                            "research/date-shift/audit_scaffold_release.json",
                        )
                self.assertEqual(
                    self._command(repository, "status", "--porcelain", "--untracked-files=all"),
                    "",
                )
                completed = self._assert_child_admission(repository, check=False)
                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, "")
                expected = (
                    "anachron.date_shift.DateShiftValidationError: "
                    + (
                        "committed blob drifted: anachron/date_shift.py"
                        if drift == "hidden_blob"
                        else "audit scaffold receipt drifted"
                    )
                )
                self.assertEqual(completed.stderr.strip().splitlines()[-1], expected)
            finally:
                temporary.cleanup()

    def test_bundle_manifest_binds_the_admitted_scaffold_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_path, audit_path = root / "runtime.json", root / "audit.json"
            provenance = {
                "scaffold_tag": "fixture-v1",
                "scaffold_commit": "a" * 40,
                "code_closure_sha256": "sha256:" + "a" * 64,
            }
            runtime_preflight = runtime(load("execution_plan.json"))
            runtime_preflight["capture_provenance"] = provenance
            write_create_only(runtime_path, runtime_preflight)
            write_create_only(audit_path, completed_audit(1))
            bundle_dir = root / "sealed"
            with mock.patch.object(
                seal_bundle, "admit_scaffold_repository", return_value=provenance
            ):
                self.assertEqual(
                    seal_bundle.main(
                        [
                            "--repository",
                            str(ROOT),
                            "--author-audit",
                            str(audit_path),
                            "--runtime-preflight",
                            str(runtime_path),
                            "--bundle-dir",
                            str(bundle_dir),
                        ]
                    ),
                    0,
                )
            verify_bundle_derivation(load_bundle(bundle_dir), ROOT, provenance)
            manifest_path = bundle_dir / "bundle_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["scaffold_release_sha256"] = "sha256:" + "0" * 64
            without_id = {
                key: value for key, value in manifest.items() if key != "bundle_id"
            }
            manifest["bundle_id"] = canonical_sha256(without_id)
            manifest_path.write_bytes(canonical_bytes(manifest))
            (bundle_dir / "publication.json").unlink()
            write_create_only(
                bundle_dir / "publication.json",
                {
                    "schema_version": "date-shift-bundle-publication-v1",
                    "bundle_id": manifest["bundle_id"],
                    "bundle_directory_name": bundle_dir.name,
                    "manifest_sha256": bytes_sha256(manifest_path.read_bytes()),
                },
            )
            tampered = load_bundle(bundle_dir)
            with self.assertRaisesRegex(
                DateShiftValidationError, "bundle manifest scaffold provenance drifted"
            ):
                verify_bundle_derivation(tampered, ROOT, provenance)


class TestExternalCaptureSealAndRun(unittest.TestCase):
    def _provenance(self):
        return {
            "scaffold_tag": "fixture-v1",
            "scaffold_commit": "a" * 40,
            "code_closure_sha256": "sha256:" + "a" * 64,
        }

    def _capture_runtime(self, output):
        plan = load("execution_plan.json")
        inventory = {
            "models": [
                {"name": model["id"], "digest": model["digest"]}
                for model in plan["models"]
            ]
        }
        with (
            mock.patch.object(
                capture_runtime, "admit_scaffold_repository", return_value=self._provenance()
            ),
            mock.patch.object(capture_runtime.shutil, "which", return_value=sys.executable),
            mock.patch.object(
                capture_runtime,
                "_api",
                side_effect=[inventory, {"version": "fixture"}],
            ),
            mock.patch.object(capture_runtime, "_command", return_value=b"ollama version fixture\n"),
            mock.patch.object(
                capture_runtime,
                "_video_adapters",
                return_value=(
                    [
                        {
                            "name": "fixture",
                            "driver_version": "fixture",
                            "pnp_device_id": "fixture",
                        }
                    ],
                    "sha256:" + "d" * 64,
                ),
            ),
            mock.patch.object(capture_runtime, "_ram_bytes", return_value=1),
        ):
            self.assertEqual(
                capture_runtime.main(
                    [
                        "--repository",
                        str(ROOT),
                        "--endpoint",
                        plan["endpoint"],
                        "--context-tokens",
                        str(plan["decoding"]["num_ctx"]),
                        "--output",
                        str(output),
                    ]
                ),
                0,
            )

    def test_capture_fails_loudly_for_api_and_cim_evidence_failures(self):
        with (
            mock.patch.object(
                capture_runtime,
                "urlopen",
                side_effect=capture_runtime.URLError("x"),
            ),
            self.assertRaises(DateShiftValidationError),
        ):
            capture_runtime._api("http://127.0.0.1:11434", "/api/tags")
        with mock.patch.object(
            capture_runtime,
            "_command",
            side_effect=DateShiftValidationError("x"),
        ), self.assertRaises(DateShiftValidationError):
            capture_runtime._video_adapters()

    def test_author_audit_finalizer_accepts_editable_json_and_writes_canonical_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            editable, sealed = root / "editable.json", root / "sealed.json"
            editable.write_text(
                json.dumps(completed_audit(1), indent=2) + "\n", encoding="utf-8"
            )
            self.assertEqual(
                finalize_date_shift_audit.main(
                    [
                        "--repository",
                        str(ROOT),
                        "--input",
                        str(editable),
                        "--output",
                        str(sealed),
                    ]
                ),
                0,
            )
            self.assertEqual(
                sealed.read_bytes(), canonical_bytes(completed_audit(1))
            )

    def test_fake_capture_seal_and_run_has_no_real_network_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_path, audit_path = root / "runtime.json", root / "audit.json"
            self._capture_runtime(runtime_path)
            write_create_only(audit_path, completed_audit(1))
            bundle_dir = root / "sealed"
            with mock.patch.object(
                seal_bundle, "admit_scaffold_repository", return_value=self._provenance()
            ):
                self.assertEqual(
                    seal_bundle.main(
                        [
                            "--repository",
                            str(ROOT),
                            "--author-audit",
                            str(audit_path),
                            "--runtime-preflight",
                            str(runtime_path),
                            "--bundle-dir",
                            str(bundle_dir),
                        ]
                    ),
                    0,
                )
            calls = []

            class FakeClient:
                def __init__(self, _endpoint):
                    pass

                def inventory(self, _timeout):
                    return {
                        model["id"]: model["digest"]
                        for model in load("execution_plan.json")["models"]
                    }

                def chat(self, request, _timeout):
                    calls.append(canonical_bytes(request))
                    payload = json.loads(request["messages"][1]["content"])
                    answer = (
                        "CALIBRATION"
                        if payload["citation_id"] == "CAL-DATE-SHIFT"
                        else "ABSTAIN"
                    )
                    raw = canonical_bytes(
                        {
                            "model": request["model"],
                            "done": True,
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "answer": answer,
                                        "citation_ids": []
                                        if answer == "ABSTAIN"
                                        else ["CAL-DATE-SHIFT"],
                                    },
                                    separators=(",", ":"),
                                ),
                            },
                        }
                    )
                    return date_shift.TransportOutcome("ok", raw)

            def backend(model_id):
                raw = f"NAME ID PROCESSOR\n{model_id} fixture GPU\n".encode()
                return {
                    "model_id": model_id,
                    "ollama_ps_base64": base64.b64encode(raw).decode("ascii"),
                    "ollama_ps_sha256": bytes_sha256(raw),
                }

            with (
                mock.patch.object(
                    run_date_shift,
                    "admit_scaffold_repository",
                    return_value=self._provenance(),
                ),
                mock.patch.object(run_date_shift, "OllamaClient", FakeClient),
                mock.patch.object(run_date_shift, "_backend_evidence", side_effect=backend),
            ):
                self.assertEqual(
                    run_date_shift.main(
                        [
                            "--repository",
                            str(ROOT),
                            "--bundle-dir",
                            str(bundle_dir),
                            "--run-dir",
                            str(root / "run"),
                        ]
                    ),
                    0,
                )
            self.assertEqual(len(calls), 6)
            validate_journal_v3(root / "run/journal.jsonl", load_bundle(bundle_dir))

    def test_scientific_request_hash_and_base64_share_one_unicode_byte_string(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "sealed"
            proposed = load("proposed_items.json")
            proposed["proposed_items"][0]["question"] += " non\u2011ASCII"
            plan = load("execution_plan.json")
            audit = completed_audit(1)
            audit["proposed_items_sha256"] = bytes_sha256(canonical_bytes(proposed))
            runtime_preflight = runtime(plan)
            runtime_preflight["capture_provenance"] = self._provenance()
            frame, items, contract, schedule = finalize_bundle_inputs(
                load("proposed_frame.json"),
                proposed,
                audit,
                plan,
                runtime_preflight,
            )
            study = {
                "manifest": {"fixture": True},
                "execution_plan": plan,
                "runtime_preflight": runtime_preflight,
                "execution_contract": contract,
                "audited_frame": frame,
                "audited_items": items,
                "schedule": schedule,
                "author_audit": audit,
            }
            write_sealed_bundle(directory, study)
            seen = []

            class FakeClient:
                def __init__(self, _endpoint):
                    pass

                def inventory(self, _timeout):
                    return {model["id"]: model["digest"] for model in plan["models"]}

                def chat(self, request, _timeout):
                    seen.append(canonical_bytes(request))
                    payload = json.loads(request["messages"][1]["content"])
                    answer = "CALIBRATION" if payload["citation_id"] == "CAL-DATE-SHIFT" else "ABSTAIN"
                    return date_shift.TransportOutcome(
                        "ok",
                        canonical_bytes(
                            {
                                "model": request["model"],
                                "done": True,
                                "message": {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        {
                                            "answer": answer,
                                            "citation_ids": []
                                            if answer == "ABSTAIN"
                                            else ["CAL-DATE-SHIFT"],
                                        },
                                        separators=(",", ":"),
                                    ),
                                },
                            }
                        ),
                    )

            def backend(model_id):
                raw = f"NAME ID PROCESSOR\n{model_id} fixture GPU\n".encode()
                return {"model_id": model_id, "ollama_ps_base64": base64.b64encode(raw).decode(), "ollama_ps_sha256": bytes_sha256(raw)}

            with (
                mock.patch.object(run_date_shift, "admit_scaffold_repository", return_value=self._provenance()),
                mock.patch.object(run_date_shift, "verify_bundle_derivation"),
                mock.patch.object(run_date_shift, "OllamaClient", FakeClient),
                mock.patch.object(run_date_shift, "_backend_evidence", side_effect=backend),
            ):
                run_date_shift.main(
                    ["--repository", str(ROOT), "--bundle-dir", str(directory), "--run-dir", str(Path(temporary) / "run")]
                )
            self.assertTrue(any("non\u2011ASCII".encode("utf-8") in request for request in seen))
            journal = (Path(temporary) / "run/journal.jsonl").read_text(encoding="utf-8")
            for row in map(json.loads, journal.splitlines()):
                if row["record_type"] == "dispatch_claim":
                    raw = base64.b64decode(row["request_base64"], validate=True)
                    self.assertEqual(row["request_sha256"], bytes_sha256(raw))


class TestQualitativeRedaction(unittest.TestCase):
    def test_redacts_dates_years_and_temporal_sentences(self):
        text = "Published December 31, 2013, after the cutoff. It was revised on 2014-01-01. Other text."
        redacted = redact_temporal_clues(text, ["fixture-model"])
        self.assertNotIn("2013", redacted)
        self.assertNotIn("2014", redacted)
        self.assertNotIn("Published", redacted)
        self.assertIn("Other text", redacted)


if __name__ == "__main__":
    unittest.main()
