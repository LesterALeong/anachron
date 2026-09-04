"""Focused synthetic tests for the outcome-neutral v3 candidate projection."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_ROOT = Path(os.environ.get("ANACHRON_V3_PROTOCOL_ROOT", r"C:\Users\leste\Downloads\Repos\anachron-v3-protocol-v1"))
PROTOCOL_PYTHON = Path(os.environ.get("ANACHRON_V3_PROTOCOL_PYTHON", sys.executable))
COMMON_PATH = ROOT / "tools" / "v3_candidate_common.py"
PAPER_BUILDER_PATH = ROOT / "tools" / "build_v3_measurement_candidate_paper.py"
SPEC = importlib.util.spec_from_file_location("v3_candidate_common", COMMON_PATH)
assert SPEC and SPEC.loader
common = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(common)
PAPER_SPEC = importlib.util.spec_from_file_location("v3_candidate_paper", PAPER_BUILDER_PATH)
assert PAPER_SPEC and PAPER_SPEC.loader
paper_builder = importlib.util.module_from_spec(PAPER_SPEC)
PAPER_SPEC.loader.exec_module(paper_builder)
TECTONIC = Path(os.environ.get("ANACHRON_V3_TECTONIC", r"C:\Users\leste\.codex\tools\tectonic-0.17.0\bin\tectonic.exe"))
REQUIRE_PAPER_QA = os.environ.get("ANACHRON_V3_REQUIRE_PAPER_QA") == "1"


def _rows() -> list[dict]:
    rows: list[dict] = []
    for primary, samples in ((True, range(22)), (False, range(6))):
        for model in ("qwen2.5:7b", "qwen3:14b-q4_K_M"):
            for mode in ("unrestricted", "enforced"):
                for sample in samples:
                    for repetition in range(1, 4):
                        sample_id = (
                            "fin-equinox-2021-06-delisted-before-cutoff"
                            if primary and sample == 0
                            else f"{'primary' if primary else 'development'}-{sample:02d}"
                        )
                        rows.append(
                            {
                                "mode": mode,
                                "model": model,
                                "split": "primary" if primary else "development",
                                "repetition": repetition,
                                "sample_id": sample_id,
                                "score": {
                                    "finance_interactions": 1,
                                    "query_leaks": 0,
                                    "restatement_leaks": 0,
                                    "result_leaks": 1 if mode == "unrestricted" else 0,
                                    "survivorship_leaks": 1 if sample == 0 and mode == "enforced" else 0,
                                    "total_interactions": 1,
                                },
                            }
                        )
    return rows


def _analysis() -> dict:
    return {
        "development_trajectory_count": 72,
        "equinox_enforced_survivorship": {"qwen2.5:7b": True, "qwen3:14b-q4_K_M": True},
        "gates": {
            "all_trajectories_valid": True,
            "enforced_equinox_survivorship_each_model": True,
            "minimum_primary_reduction": True,
            "no_model_negative": True,
        },
        "go": True,
        "model_primary_reductions": {"qwen2.5:7b": 1.0, "qwen3:14b-q4_K_M": 1.0},
        "plan_id": "anachron-v3-full-primary-2026-09-03",
        "primary_trajectory_count": 264,
        "trajectory_count": 336,
    }


class TestV3CandidateProjection(unittest.TestCase):
    def test_candidate_contract_binds_the_frozen_matrix_and_protocol(self):
        contract = json.loads((ROOT / "paper/v3_measurement/candidate_contract.json").read_text())
        self.assertEqual(common.validate_candidate_contract(ROOT), contract)
        self.assertEqual(contract["candidate_acceptance_matrix_sha256"], common.sha256_path(ROOT / "paper/v3_measurement/CANDIDATE_ACCEPTANCE_MATRIX.md"))
        self.assertEqual(contract["frozen_protocol_commit"], common.PROTOCOL_COMMIT)
        self.assertEqual(contract["frozen_protocol_tag_object"], common.PROTOCOL_TAG_OBJECT)

    def test_exact_projection_keeps_splits_separate_and_reports_a_false_gate(self):
        rows = _rows()
        for row in rows:
            row["score"]["result_leaks"] = 0
        analysis = _analysis()
        analysis["model_primary_reductions"] = {"qwen2.5:7b": 0.0, "qwen3:14b-q4_K_M": 0.0}
        analysis["gates"]["minimum_primary_reduction"] = False
        analysis["go"] = False
        projection = common.build_projection(rows, analysis)
        self.assertFalse(projection["analysis_go"])
        self.assertEqual(projection["split_counts"], {"development": 72, "primary": 264, "total": 336})
        primary = next(
            cell
            for cell in projection["cells"]
            if cell["split"] == "primary"
            and cell["model"] == "qwen2.5:7b"
            and cell["mode"] == "unrestricted"
            and cell["metric"] == "tclr"
        )
        self.assertEqual(primary["rate"], {"numerator": 0, "denominator": 1})
        self.assertEqual((primary["count"], primary["denominator_count"]), (0, 66))
        self.assertEqual((primary["repetition_n"], primary["scope_text"]), (3, "finite synthetic panel; descriptive only"))
        development = next(cell for cell in projection["cells"] if cell["split"] == "development")
        self.assertEqual(development["trajectory_count"], 18)
        reduction = next(
            row
            for row in projection["paired_tclr_reductions"]
            if row["split"] == "primary" and row["model"] == "pooled"
        )
        self.assertEqual(reduction["rate"], {"numerator": 0, "denominator": 1})
        self.assertEqual(reduction["sign_class"], "zero")

    def test_projection_rejects_native_reconciliation_drift(self):
        analysis = _analysis()
        analysis["model_primary_reductions"]["qwen2.5:7b"] = 0.5
        with self.assertRaisesRegex(common.CandidateProjectionError, "does not reconcile"):
            common.build_projection(_rows(), analysis)

    def test_answer_free_worker_needs_no_terminal_artifacts(self):
        if not PROTOCOL_ROOT.is_dir() and not REQUIRE_PAPER_QA:
            self.skipTest("frozen protocol worktree is unavailable")
        self.assertTrue(PROTOCOL_ROOT.is_dir(), "required frozen protocol worktree is unavailable")
        common.verify_detached_protocol_root(PROTOCOL_ROOT)
        protocol_spec = importlib.util.spec_from_file_location(
            "frozen_v3_measurement", PROTOCOL_ROOT / "anachron/v3_measurement.py"
        )
        assert protocol_spec and protocol_spec.loader
        frozen = importlib.util.module_from_spec(protocol_spec)
        protocol_spec.loader.exec_module(frozen)
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            plan = json.loads((PROTOCOL_ROOT / "research/v3_measurement/full_plan.json").read_text())
            (evidence / "plan.json").write_bytes(frozen._canonical_json(plan))
            raw = evidence / "raw"
            raw.mkdir()
            for trajectory in frozen.expected_trajectories(plan):
                prompt = trajectory["sample"].prompt()
                query = "Equinox" if "Equinox" in prompt else "Acme"
                response = {
                    "created_at": "2026-09-03T00:00:00Z",
                    "done": True,
                    "done_reason": "stop",
                    "eval_count": 1,
                    "eval_duration": 1,
                    "load_duration": 1,
                    "message": {
                        "content": "",
                        "role": "assistant",
                        "tool_calls": [{"id": "call_native", "function": {"index": 0, "name": "anachron_search", "arguments": {"query": query}}}],
                    },
                    "model": trajectory["model"],
                    "prompt_eval_count": 1,
                    "prompt_eval_duration": 1,
                    "total_duration": 1,
                }
                identifier = trajectory["id"]
                (raw / f"{identifier}.first.response.json").write_bytes(frozen._canonical_json(response))
                items = frozen.search_v3(query, trajectory["sample"].as_of if trajectory["mode"] == "enforced" else None)
                (raw / f"{identifier}.tool_result.txt").write_text(frozen.format_search_results(items), encoding="utf-8")
            rows = common.answer_free_rows(PROTOCOL_ROOT, evidence)
            plan = common._load_snapshot_plan(evidence)
        self.assertEqual(len(rows), 336)
        self.assertTrue(all("score" in row for row in rows))

        bool_alias = copy.deepcopy(rows)
        bool_alias[0]["score"]["total_interactions"] = True
        with self.assertRaisesRegex(common.CandidateProjectionError, "invalid integer"):
            common._validate_answer_free_rows(bool_alias, plan)

        duplicate = copy.deepcopy(rows)
        duplicate[1]["split"] = duplicate[0]["split"]
        duplicate[1]["model"] = duplicate[0]["model"]
        duplicate[1]["sample_id"] = duplicate[0]["sample_id"]
        duplicate[1]["repetition"] = duplicate[0]["repetition"]
        duplicate[1]["mode"] = duplicate[0]["mode"]
        with self.assertRaisesRegex(common.CandidateProjectionError, "duplicate trajectory"):
            common._validate_answer_free_rows(duplicate, plan)

        unknown_field = copy.deepcopy(rows)
        unknown_field[0]["unexpected"] = "value"
        with self.assertRaisesRegex(common.CandidateProjectionError, "row shape"):
            common._validate_answer_free_rows(unknown_field, plan)

    def test_create_only_output_rejects_existing_and_overlapping_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "evidence"
            evidence.mkdir()
            with self.assertRaisesRegex(common.CandidateProjectionError, "must not overlap"):
                common.require_create_only_output(evidence / "projection.json", (evidence,))
            output = root / "projection.json"
            output.write_bytes(b"existing")
            with self.assertRaisesRegex(FileExistsError, "must not already exist"):
                common.require_create_only_output(output, (evidence,))

    def test_admitted_file_reader_rejects_a_deterministic_link_swap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            admitted = root / "admitted.json"
            replacement = root / "replacement.json"
            admitted.write_bytes(b"original")
            replacement.write_bytes(b"replacement")
            real_open = common.os.open

            def swap_to_link(path, flags):
                Path(path).unlink()
                os.symlink(replacement, path)
                return real_open(path, flags)

            with patch.object(common.os, "open", side_effect=swap_to_link), self.assertRaisesRegex(
                common.CandidateProjectionError, "(opened|safely)"
            ):
                common._read_regular_file(admitted, "injected admitted file")

    def test_native_analyzer_cli_is_called_from_the_frozen_root(self):
        if not PROTOCOL_ROOT.is_dir() and not REQUIRE_PAPER_QA:
            self.skipTest("frozen protocol worktree is unavailable")
        self.assertTrue(PROTOCOL_ROOT.is_dir(), "required frozen protocol worktree is unavailable")
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(
            common.CandidateProjectionError, "frozen analyzer failed"
        ):
            common.invoke_frozen_analyzer(PROTOCOL_ROOT, Path(temporary))

    def test_candidate_workers_do_not_use_the_protocol_generation_interpreter(self):
        if not PROTOCOL_ROOT.is_dir() and not REQUIRE_PAPER_QA:
            self.skipTest("frozen protocol worktree is unavailable")
        self.assertTrue(PROTOCOL_ROOT.is_dir(), "required frozen protocol worktree is unavailable")
        completed = subprocess.CompletedProcess([], 0, stdout=b"{}", stderr=b"")
        with patch.dict(os.environ, {"ANACHRON_V3_PROTOCOL_PYTHON": str(ROOT / "wrong-python")}), patch.object(
            common.subprocess, "run", return_value=completed
        ) as run:
            common.invoke_frozen_analyzer(PROTOCOL_ROOT, ROOT)
            self.assertEqual(run.call_args.args[0][0], sys.executable)

        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            (evidence / "plan.json").write_bytes(
                (PROTOCOL_ROOT / "research/v3_measurement/full_plan.json").read_bytes()
            )
            completed = subprocess.CompletedProcess([], 0, stdout=b'{"rows":[]}', stderr=b"")
            with patch.dict(os.environ, {"ANACHRON_V3_PROTOCOL_PYTHON": str(ROOT / "wrong-python")}), patch.object(
                common.subprocess, "run", return_value=completed
            ) as run, self.assertRaisesRegex(common.CandidateProjectionError, "wrong trajectory count"):
                common.answer_free_rows(PROTOCOL_ROOT, evidence)
            self.assertEqual(run.call_args.args[0][0], sys.executable)

    def test_complete_synthetic_study_projects_through_the_frozen_snapshot(self):
        if not PROTOCOL_ROOT.is_dir() and not REQUIRE_PAPER_QA:
            self.skipTest("frozen protocol worktree is unavailable")
        self.assertTrue(PROTOCOL_ROOT.is_dir(), "required frozen protocol worktree is unavailable")
        self.assertTrue(PROTOCOL_PYTHON.is_absolute(), "protocol generation interpreter must be absolute")
        self.assertTrue(PROTOCOL_PYTHON.is_file(), "protocol generation interpreter must exist")
        identity = subprocess.run(
            [
                str(PROTOCOL_PYTHON),
                "-I",
                "-c",
                "import json, platform; print(json.dumps({'implementation': platform.python_implementation(), 'version': platform.python_version()}, sort_keys=True))",
            ],
            capture_output=True,
            check=False,
        )
        self.assertEqual(identity.returncode, 0, identity.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(
            json.loads(identity.stdout),
            {"implementation": "CPython", "version": "3.12.10"},
            "synthetic protocol evidence must be generated by the frozen runtime",
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "full"
            driver = r'''
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
output = Path(sys.argv[2])
sys.path.insert(0, str(root))

import anachron.v3_measurement as measurement

try:
    Path(measurement.__file__).resolve().relative_to(root.resolve())
except ValueError as error:
    raise RuntimeError("synthetic driver imported outside the explicit protocol root") from error

from anachron.v3_measurement import _FULL_GO_KIND, _FULL_GO_STATEMENT, _canonical_json, run_measurement, seal_falsifier_receipt

def tag(name, digest):
    return {"name": name, "model": name, "modified_at": "2026-08-05T08:41:44.0361924-05:00", "size": 1, "digest": digest, "details": {"parent_model": "", "format": "gguf", "family": "qwen", "families": ["qwen"], "parameter_size": "1B", "quantization_level": "Q4_K_M", "context_length": 8192, "embedding_length": 1}, "capabilities": ["completion", "tools"]}

def chat(model, content, query=None):
    message = {"role": "assistant", "content": content}
    if query is not None:
        message["tool_calls"] = [{"id": "call_native", "function": {"index": 0, "name": "anachron_search", "arguments": {"query": query}}}]
    return _canonical_json({"created_at": "2026-09-03T00:00:00Z", "done": True, "done_reason": "stop", "eval_count": 1, "eval_duration": 1, "load_duration": 1, "message": message, "model": model, "prompt_eval_count": 1, "prompt_eval_duration": 1, "total_duration": 1})

def transport(base_url, path, payload, timeout):
    if path == "/api/version":
        return _canonical_json({"version": "0.33.2"})
    if path == "/api/tags":
        return _canonical_json({"models": [tag("qwen2.5:7b", "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"), tag("qwen3:14b-q4_K_M", "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8")]})
    request = json.loads(payload)
    if "tools" in request:
        return chat(request["model"], "", "Equinox" if "Equinox" in request["messages"][0]["content"] else "Acme")
    if request["messages"][0]["content"] == "Return exactly CALIBRATION_OK after this recorded tool transcript.":
        return chat(request["model"], "CALIBRATION_OK")
    return chat(request["model"], "SENTINEL_FINAL_ANSWER_MUST_NOT_ESCAPE")

falsifier = output.parent / "falsifier"
run_measurement(root / "research/v3_measurement/falsifier_plan.json", falsifier, transport=transport, repository_root=root)
receipt = output.parent / "receipt.json"
seal_falsifier_receipt(falsifier, receipt, root)
full_plan = root / "research/v3_measurement/full_plan.json"
full_raw = full_plan.read_bytes()
go = output.parent / "go.json"
go.write_bytes(_canonical_json({"schema_version": 1, "kind": _FULL_GO_KIND, "decision": "GO", "authorized_by": "Lester Leong", "authorized_at_utc": "2026-09-03T00:00:00+00:00", "statement": _FULL_GO_STATEMENT, "full_plan_sha256": hashlib.sha256(full_raw).hexdigest(), "falsifier_receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()}))
run_measurement(full_plan, output, transport=transport, repository_root=root, falsifier_evidence=falsifier, falsifier_receipt=receipt, full_go=go)
'''
            result = subprocess.run(
                [str(PROTOCOL_PYTHON), "-I", "-c", driver, str(PROTOCOL_ROOT), str(output)],
                cwd=PROTOCOL_ROOT,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
            projection = common.project_candidate(PROTOCOL_ROOT, output)
            if TECTONIC.is_file():
                candidates = []
                builder_driver = r'''
import sys
from pathlib import Path

repository = Path(sys.argv[1])
sys.path.insert(0, str(repository))
from tools.build_v3_measurement_candidate_paper import build_candidate

build_candidate(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), Path(sys.argv[5]))
'''
                for seed in ("0", "1"):
                    candidate = Path(temporary) / f"candidate-{seed}"
                    environment = os.environ.copy()
                    environment["PYTHONHASHSEED"] = seed
                    result = subprocess.run(
                        [sys.executable, "-I", "-c", builder_driver, str(ROOT), str(PROTOCOL_ROOT), str(output), str(candidate), str(TECTONIC)],
                        cwd=ROOT,
                        env=environment,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
                    candidates.append(candidate)
                candidate = candidates[0]
                deterministic_paths = (
                    "projection.json", "candidate.pdf", "source.zip", "arxiv_metadata.json",
                    "paper_source_manifest.json", "candidate_receipt.json",
                    *(f"source/{name}" for name in paper_builder.ARCHIVE_FILES),
                )
                for relative_path in deterministic_paths:
                    self.assertEqual(
                        common.sha256_path(candidates[0] / relative_path),
                        common.sha256_path(candidates[1] / relative_path),
                        f"PYTHONHASHSEED changed {relative_path}",
                    )
                from tools.release_v3_measurement_candidate import (
                    ATTESTATION,
                    release_candidate,
                )
                from tools.verify_v3_measurement_candidate_reviews import (
                    CandidateReviewError,
                    create_review_set_manifest,
                    revalidate_candidate,
                    verify_reviews,
                )

                receipt, hashes = revalidate_candidate(candidate)
                original_pdf = (candidate / "candidate.pdf").read_bytes()
                (candidate / "candidate.pdf").write_bytes(original_pdf + b"tampered")
                with self.assertRaisesRegex(CandidateReviewError, "binds current bytes"):
                    revalidate_candidate(candidate)
                (candidate / "candidate.pdf").write_bytes(original_pdf)
                receipt, hashes = revalidate_candidate(candidate)
                reviews = Path(temporary) / "reviews"
                reviews.mkdir()
                for lens in common.REVIEW_LENS_IDS:
                    review = {
                        "archive_sha256": receipt["archive_sha256"],
                        "candidate_receipt_sha256": hashes["candidate_receipt.json"],
                        "evidence_manifest_sha256": receipt["evidence_manifest_sha256"],
                        "findings": [],
                        "lens_id": lens,
                        "paper_source_manifest_sha256": receipt["paper_source_manifest_sha256"],
                        "paper_pdf_sha256": receipt["candidate_pdf_sha256"],
                        "projection_sha256": receipt["projection_sha256"],
                        "resolutions": [],
                        "reviewed_at_utc": "2026-09-04T00:00:00Z",
                        "reviewer": f"synthetic-{lens}",
                        "schema_version": "anachron-v3-candidate-review-v1",
                        "status": "APPROVED",
                    }
                    (reviews / f"{lens}.json").write_bytes(common.canonical_json(review))
                review_manifest_path = Path(temporary) / "review-set-manifest.json"
                review_manifest = create_review_set_manifest(candidate, reviews, review_manifest_path)
                self.assertEqual(review_manifest["lens_ids"], list(common.REVIEW_LENS_IDS))
                self.assertEqual(len(verify_reviews(candidate, reviews)), 10)
                approval = {
                    "abstract_sha256": receipt["abstract_sha256"],
                    "ai_assistance_disclosure_sha256": receipt["ai_assistance_disclosure_sha256"],
                    "approval": "APPROVED",
                    "approved_at_utc": "2026-09-04T00:00:01Z",
                    "approved_by": "Lester Leong",
                    "archive_sha256": receipt["archive_sha256"],
                    "arxiv_metadata_sha256": hashes["arxiv_metadata.json"],
                    "attestation": ATTESTATION,
                    "candidate_receipt_sha256": hashes["candidate_receipt.json"],
                    "paper_pdf_sha256": receipt["candidate_pdf_sha256"],
                    "review_set_manifest_sha256": common.sha256_path(review_manifest_path),
                    "schema_version": "anachron-v3-candidate-author-approval-v1",
                    "status": "APPROVED",
                }
                approval_path = Path(temporary) / "approval.json"
                approval_path.write_bytes(common.canonical_json(approval))
                release = Path(temporary) / "release"
                release_candidate(candidate, reviews, review_manifest_path, approval_path, release)
                self.assertEqual((release / "candidate.pdf").read_bytes(), (candidate / "candidate.pdf").read_bytes())
                self.assertEqual((release / "source.zip").read_bytes(), (candidate / "source.zip").read_bytes())
                forbidden = ("SENTINEL_FINAL_ANSWER_MUST_NOT_ESCAPE", "runtime.json", "full_go.json", "falsifier_receipt.json")
                for path in candidate.rglob("*"):
                    if path.is_file() and path.suffix.lower() not in {".pdf", ".zip", ".png"}:
                        text = path.read_text(encoding="utf-8", errors="replace")
                        self.assertTrue(all(token not in text for token in forbidden), path)
                self.assertNotIn("SENTINEL_FINAL_ANSWER_MUST_NOT_ESCAPE", (candidate / "candidate.pdf").read_bytes().decode("latin1"))
            elif REQUIRE_PAPER_QA:
                self.fail("required pinned Tectonic executable is unavailable")
        self.assertEqual(projection["split_counts"]["total"], 336)
        self.assertIn("analysis_go", projection)


if __name__ == "__main__":
    unittest.main()
