"""Focused controls for the loopback-only, append-only v0 runner."""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import nullcontext
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from anachron.core.leakage import CorpusItem, ToolInteraction, score_interactions
from anachron.v0_measurement import (
    _extract_query,
    _http_json,
    _safe_regular_files,
    _validate_final_response,
    admit_committed_source,
    admit_full_preconditions,
    analyze_evidence,
    expected_raw_inventory,
    expected_trajectories,
    load_plan,
    run_measurement,
    seal_falsifier_receipt,
    validate_exact_raw_inventory,
    validate_full_go_authorization,
    verify_source_admission,
)

_DIGEST = "a" * 64
_TEST_REPOSITORY_ROOT: Path | None = None


def _frozen_runtime_fixture(plan: dict):
    frozen = plan["python"]
    current = {
        "implementation": "CPython",
        "major": sys.version_info.major,
        "minor": sys.version_info.minor,
        "version": sys.version.split()[0],
    }
    if current == frozen:
        return nullcontext()
    return patch("anachron.v0_measurement._runtime_identity", return_value=frozen)


def setUpModule() -> None:
    global _TEST_REPOSITORY_ROOT
    repository_root = Path(__file__).resolve().parent.parent
    fixture = Path(tempfile.mkdtemp()) / "repository"
    fixture.mkdir()
    shutil.copy2(repository_root / ".gitattributes", fixture / ".gitattributes")
    for relative in (
        "anachron/core/leakage.py",
        "anachron/data/corpus.py",
        "anachron/data/v0_samples.py",
        "anachron/v0_measurement.py",
    ):
        destination = fixture / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repository_root / relative, destination)
    for command in (
        ("init", "-b", "master"),
        ("config", "user.email", "test@example.invalid"),
        ("config", "user.name", "Test"),
        ("add", "."),
        ("commit", "-m", "fixture"),
        ("tag", "-a", "v0-measurement-protocol-v1", "-m", "fixture"),
    ):
        subprocess.run(["git", *command], cwd=fixture, check=True, capture_output=True)
    _TEST_REPOSITORY_ROOT = fixture


def tearDownModule() -> None:
    if _TEST_REPOSITORY_ROOT is not None:
        shutil.rmtree(_TEST_REPOSITORY_ROOT.parent, ignore_errors=True)


def _plan() -> dict:
    root = Path(__file__).resolve().parent.parent
    return json.loads((root / "research" / "v0_measurement" / "falsifier_plan.json").read_text(encoding="utf-8"))


def _full_plan_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    return root / "research" / "v0_measurement" / "full_plan.json"


def _write_canonical_plan(path: Path, plan: dict) -> None:
    path.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _fake_transport(base_url: str):
    def transport(_planned_url, path, payload, timeout):
        return _http_json(base_url, path, payload, timeout)

    return transport


def _source_admission(plan, root):
    assert _TEST_REPOSITORY_ROOT is not None
    commit = subprocess.run(
        ["git", "rev-parse", "refs/tags/v0-measurement-protocol-v1^{}"],
        cwd=_TEST_REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tag_object = subprocess.run(
        ["git", "rev-parse", "refs/tags/v0-measurement-protocol-v1"],
        cwd=_TEST_REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "-z", commit],
        cwd=_TEST_REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    blobs = {}
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", maxsplit=1)
        _mode, object_type, oid = metadata.decode("ascii").split()
        relative = path.decode("utf-8")
        if relative in plan["source_hashes"]:
            assert object_type == "blob"
            blobs[relative] = {"oid": oid, "sha256": plan["source_hashes"][relative]}
    return {
        "schema_version": 2,
        "release": plan["release"],
        "plan_sha256": hashlib.sha256(
            json.dumps(plan, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        ).hexdigest(),
        "tag_commit": commit,
        "tag": {
            "ref": plan["release"]["ref"],
            "local_object": tag_object,
            "remote_object": tag_object,
            "local_peeled_commit": commit,
            "remote_peeled_commit": commit,
        },
        "governed_blobs": blobs,
        "python": plan["python"],
    }


def _full_go(plan: dict, plan_bytes: bytes, receipt_bytes: bytes) -> dict:
    return {
        "schema_version": 1,
        "kind": "anachron-v0-full-measurement-authorization",
        "decision": "GO",
        "authorized_by": "Lester Leong",
        "authorized_at_utc": "2026-09-03T12:00:00+00:00",
        "authorization_statement": (
            "I authorize this exact frozen full v0 measurement plan after reviewing the "
            "bound passing falsifier receipt."
        ),
        "full_plan_id": plan["plan_id"],
        "full_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "falsifier_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
    }


def _resign_manifest(root: Path) -> None:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.relative_to(root).as_posix() not in {"manifest.json", "manifest.sha256"}:
            files.append({
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
    manifest["runtime_sha256"] = hashlib.sha256((root / "runtime.json").read_bytes()).hexdigest()
    manifest["files"] = files
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    (root / "manifest.json").write_bytes(manifest_bytes)
    (root / "manifest.sha256").write_text(
        f"{hashlib.sha256(manifest_bytes).hexdigest()}  manifest.json\n", encoding="ascii"
    )


class _OllamaHandler(BaseHTTPRequestHandler):
    chat_calls = 0
    invalid_tool = False
    digest = _DIGEST
    query = "Borealis Mining"

    def log_message(self, format, *args):
        return

    def _write(self, body: dict):
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/api/version":
            self._write({"version": "test"})
        elif self.path == "/api/tags":
            self._write({"models": [
                {"name": "qwen2.5:7b", "digest": self.digest},
                {"name": "qwen3:14b-q4_K_M", "digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8"},
            ]})
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/api/chat":
            self.send_error(404)
            return
        type(self).chat_calls += 1
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        if body["messages"][-1]["role"] == "tool":
            self._write({"model": body["model"], "done": True, "done_reason": "stop", "message": {"role": "assistant", "content": "Recorded."}})
            return
        calls = [{"function": {"name": "anachron_search", "arguments": {"query": type(self).query}}}]
        if type(self).invalid_tool:
            calls.append(calls[0])
        self._write({"model": body["model"], "done": True, "done_reason": "tool_calls", "message": {"role": "assistant", "content": "", "tool_calls": calls}})

    def assertEqual(self, first, second):
        if first != second:
            raise AssertionError(f"fake server observed {first!r}, expected {second!r}")


class _FakeOllama:
    def __init__(self, query: str = "Borealis Mining"):
        self.query = query

    def __enter__(self):
        _OllamaHandler.chat_calls = 0
        _OllamaHandler.invalid_tool = False
        _OllamaHandler.query = self.query
        _OllamaHandler.digest = "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaHandler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def __exit__(self, exc_type, exc, traceback):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()


class TestStaticControls(unittest.TestCase):
    def test_module_clis_run_from_a_clean_repo_root(self):
        root = Path(__file__).resolve().parent.parent
        for module in (
            "tools.run_v0_measurement",
            "tools.analyze_v0_measurement",
            "tools.seal_v0_falsifier_receipt",
        ):
            result = subprocess.run(
                [sys.executable, "-m", module, "--help"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        controls = subprocess.run(
            [sys.executable, "-m", "tools.run_v0_measurement", "--controls"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(controls.returncode, 0, controls.stderr)
        self.assertTrue(json.loads(controls.stdout)["result_positive"])

    def test_positive_and_negative_temporal_controls(self):
        future = CorpusItem("future", "future", date(2024, 1, 1))
        past = CorpusItem("past", "past", date(2020, 1, 1))
        as_of = date(2021, 1, 1)
        positive = score_interactions([ToolInteraction("tool", "q", returned_items=[future])], as_of)
        negative = score_interactions([ToolInteraction("tool", "q", returned_items=[past])], as_of)
        self.assertEqual(positive.tclr, 1.0)
        self.assertEqual(negative.tclr, 0.0)

    def test_tool_call_shape_requires_exactly_one_expected_call(self):
        response = {"model": "test-model", "done": True, "done_reason": "tool_calls", "message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "anachron_search", "arguments": {"query": "Acme"}}}
        ]}}
        self.assertEqual(_extract_query(response, "test-model"), ("Acme", None))
        invalid = {"model": "test-model", "done": True, "done_reason": "tool_calls", "message": {"role": "assistant", "content": "", "tool_calls": []}}
        self.assertIn("exactly one", _extract_query(invalid, "test-model")[1])

    def test_native_envelope_rejects_wrong_model_incomplete_and_thinking(self):
        valid = {"model": "qwen2.5:7b", "done": True, "done_reason": "tool_calls", "message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "anachron_search", "arguments": {"query": "Acme 2024-01-01"}}}]}}
        self.assertEqual(_extract_query(valid, "qwen2.5:7b")[0], "Acme 2024-01-01")
        for field, value in (("model", "qwen3:14b-q4_K_M"), ("done", False)):
            hostile = dict(valid)
            hostile[field] = value
            self.assertIsNotNone(_extract_query(hostile, "qwen2.5:7b")[1])
        thinking = dict(valid)
        thinking["message"] = dict(valid["message"], thinking="hidden")
        self.assertIn("thinking", _extract_query(thinking, "qwen2.5:7b")[1])
        self.assertIsNone(_validate_final_response({"model": "qwen3:14b-q4_K_M", "done": True, "done_reason": "stop", "message": {"role": "assistant", "content": "answer"}}, "qwen3:14b-q4_K_M"))

    def test_expected_falsifier_cardinality_is_24(self):
        root = Path(__file__).resolve().parent.parent
        plan, _ = load_plan(root / "research" / "v0_measurement" / "falsifier_plan.json")
        self.assertEqual(len(expected_trajectories(plan)), 24)
        self.assertEqual(len(expected_raw_inventory(plan)), 2 + 5 * 24)

    def test_expected_full_plan_cardinality_is_324(self):
        root = Path(__file__).resolve().parent.parent
        plan, _ = load_plan(root / "research" / "v0_measurement" / "full_plan.json")
        self.assertEqual(len(expected_trajectories(plan)), 324)

    def test_plan_requires_canonical_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "plan.json"
            path.write_text(json.dumps(_plan()), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "canonical JSON"):
                load_plan(path)

    def test_plan_rejects_extra_schema_lowered_threshold_and_generation_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, mutate, message in (
                ("extra", lambda plan: plan.update({"unexpected": True}), "unexpected fields"),
                ("threshold", lambda plan: plan["acceptance"].update({"minimum_pooled_reduction": 0.1}), "design differs"),
                ("generation", lambda plan: plan["generation"].update({"temperature": 1}), "generation"),
                ("timeout", lambda plan: plan.update({"timeout_seconds": 119}), "endpoint and timeout"),
            ):
                plan = _plan()
                mutate(plan)
                path = root / f"{name}.json"
                _write_canonical_plan(path, plan)
                with self.assertRaisesRegex(ValueError, message):
                    load_plan(path)


class TestFullAuthorization(unittest.TestCase):
    def test_pending_or_mismatched_human_go_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            full_plan_bytes = _full_plan_path().read_bytes()
            receipt_bytes = b"receipt"
            full_go = root / "full_go.json"
            full_go.write_text('{"status":"PENDING"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "pending"):
                validate_full_go_authorization(
                    json.loads(full_plan_bytes), full_plan_bytes, receipt_bytes, full_go
                )
            go = _full_go(json.loads(full_plan_bytes), full_plan_bytes, receipt_bytes)
            go["full_plan_sha256"] = "0" * 64
            _write_canonical_plan(full_go, go)
            with self.assertRaisesRegex(ValueError, "full plan"):
                validate_full_go_authorization(
                    json.loads(full_plan_bytes), full_plan_bytes, receipt_bytes, full_go
                )

    def test_receipt_requires_passing_evidence_and_creates_no_output_on_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "receipt.json"
            with self.assertRaises(FileNotFoundError):
                seal_falsifier_receipt(
                    root / "missing-evidence",
                    Path(__file__).resolve().parent.parent / "research" / "v0_measurement" / "falsifier_plan.json",
                    output,
                )
            self.assertFalse(output.exists())

    def test_full_missing_prerequisites_stops_before_output_or_http(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "full-evidence"
            _OllamaHandler.chat_calls = 0
            with self.assertRaisesRegex(ValueError, "requires falsifier evidence"):
                run_measurement(_full_plan_path(), output)
            self.assertFalse(output.exists())
            self.assertEqual(_OllamaHandler.chat_calls, 0)

    def test_falsifier_rejects_full_run_prerequisites_before_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "falsifier-evidence"
            with self.assertRaisesRegex(ValueError, "does not accept"):
                run_measurement(
                    Path(__file__).resolve().parent.parent / "research" / "v0_measurement" / "falsifier_plan.json",
                    output,
                    falsifier_evidence=root / "evidence",
                )
            self.assertFalse(output.exists())

    def test_full_preconditions_admit_a_real_passing_falsifier_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            falsifier_evidence = root / "falsifier-evidence"
            receipt = root / "falsifier-receipt.json"
            full_go = root / "full-go.json"
            full_output = root / "full-evidence"
            falsifier_plan = Path(__file__).resolve().parent.parent / "research" / "v0_measurement" / "falsifier_plan.json"
            with _FakeOllama(query="a") as base_url:
                falsifier_analysis = run_measurement(
                    falsifier_plan,
                    falsifier_evidence,
                    source_admitter=_source_admission,
                    transport=_fake_transport(base_url),
                    repository_root=_TEST_REPOSITORY_ROOT,
                )
                self.assertTrue(falsifier_analysis["go"])
                seal_falsifier_receipt(
                    falsifier_evidence, falsifier_plan, receipt, _TEST_REPOSITORY_ROOT
                )
                full_plan_bytes = _full_plan_path().read_bytes()
                _write_canonical_plan(
                    full_go,
                    _full_go(json.loads(full_plan_bytes), full_plan_bytes, receipt.read_bytes()),
                )
                full_plan = json.loads(full_plan_bytes)
                path_budget_output = root / ("x" * 241)
                with self.assertRaisesRegex(ValueError, "path budget"):
                    admit_full_preconditions(
                        full_plan,
                        full_plan_bytes,
                        falsifier_evidence,
                        receipt,
                        full_go,
                        path_budget_output,
                        _TEST_REPOSITORY_ROOT,
                    )
                self.assertFalse(path_budget_output.exists())
                full_analysis = run_measurement(
                    _full_plan_path(),
                    full_output,
                    source_admitter=_source_admission,
                    transport=_fake_transport(base_url),
                    falsifier_evidence=falsifier_evidence,
                    falsifier_receipt=receipt,
                    full_go=full_go,
                    repository_root=_TEST_REPOSITORY_ROOT,
                )
            self.assertEqual(full_analysis["valid_trajectory_count"], 324)
            self.assertEqual(
                (full_output / "prerequisites" / "falsifier_pass_receipt.json").read_bytes(), receipt.read_bytes()
            )
            self.assertEqual(analyze_evidence(full_output, _TEST_REPOSITORY_ROOT), full_analysis)
            original = next((falsifier_evidence / "raw").glob("*.tool_result.txt"))
            original.write_text("mutated after copy", encoding="utf-8")
            self.assertEqual(analyze_evidence(full_output, _TEST_REPOSITORY_ROOT), full_analysis)
            tampered = root / "tampered-full"
            shutil.copytree(full_output, tampered)
            embedded = next((tampered / "prerequisites" / "falsifier" / "raw").glob("*.tool_result.txt"))
            embedded.write_text("tampered embedded artifact", encoding="utf-8")
            _resign_manifest(tampered / "prerequisites" / "falsifier")
            _resign_manifest(tampered)
            with self.assertRaisesRegex(ValueError, "trajectory artifact is invalid"):
                analyze_evidence(tampered, _TEST_REPOSITORY_ROOT)
            extra_raw = root / "extra-embedded-raw"
            shutil.copytree(full_output, extra_raw)
            (extra_raw / "prerequisites" / "falsifier" / "raw" / "extra.txt").write_bytes(b"extra")
            _resign_manifest(extra_raw / "prerequisites" / "falsifier")
            _resign_manifest(extra_raw)
            with self.assertRaisesRegex(ValueError, "raw evidence inventory"):
                analyze_evidence(extra_raw, _TEST_REPOSITORY_ROOT)

    def test_resigned_falsifier_plan_mismatch_cannot_create_a_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "falsifier-evidence"
            plan_path = Path(__file__).resolve().parent.parent / "research" / "v0_measurement" / "falsifier_plan.json"
            with _FakeOllama(query="a") as base_url:
                run_measurement(
                    plan_path,
                    evidence,
                    source_admitter=_source_admission,
                    transport=_fake_transport(base_url),
                    repository_root=_TEST_REPOSITORY_ROOT,
                )
            (evidence / "plan.json").write_bytes(_full_plan_path().read_bytes())
            _resign_manifest(evidence)
            with self.assertRaisesRegex(ValueError, "evidence plan"):
                seal_falsifier_receipt(evidence, plan_path, root / "receipt.json", _TEST_REPOSITORY_ROOT)

    def test_resigned_runtime_provenance_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "evidence"
            plan_path = Path(__file__).resolve().parent.parent / "research" / "v0_measurement" / "falsifier_plan.json"
            with _FakeOllama() as base_url:
                run_measurement(
                    plan_path,
                    evidence,
                    source_admitter=_source_admission,
                    transport=_fake_transport(base_url),
                    repository_root=_TEST_REPOSITORY_ROOT,
                )
            for name, mutate, expected in (
                ("plan", lambda runtime: runtime.update({"plan_id": "wrong"}), "plan identity"),
                ("endpoint", lambda runtime: runtime.update({"base_url": "http://127.0.0.1:1"}), "endpoint"),
                ("count", lambda runtime: runtime.update({"generation_request_count_per_valid_trajectory": 1}), "request count"),
                ("server", lambda runtime: runtime["server"].update({"version": {"version": "wrong"}}), "server reconstruction"),
            ):
                candidate = root / name
                shutil.copytree(evidence, candidate)
                runtime_path = candidate / "runtime.json"
                runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
                mutate(runtime)
                runtime_path.write_bytes(json.dumps(runtime, indent=2, sort_keys=True).encode("utf-8") + b"\n")
                _resign_manifest(candidate)
                with self.assertRaisesRegex(ValueError, expected):
                    analyze_evidence(candidate, _TEST_REPOSITORY_ROOT)

            candidate = root / "source"
            shutil.copytree(evidence, candidate)
            source_path = candidate / "source_admission.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source["governed_blobs"]["anachron/core/leakage.py"]["sha256"] = "a" * 64
            source_path.write_bytes(json.dumps(source, indent=2, sort_keys=True).encode("utf-8") + b"\n")
            runtime_path = candidate / "runtime.json"
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            runtime["source_admission_sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
            runtime_path.write_bytes(json.dumps(runtime, indent=2, sort_keys=True).encode("utf-8") + b"\n")
            _resign_manifest(candidate)
            with self.assertRaisesRegex(ValueError, "source admission governed bytes mismatch"):
                analyze_evidence(candidate, _TEST_REPOSITORY_ROOT)


class TestRawInventoryTopology(unittest.TestCase):
    def test_real_symlink_is_rejected_when_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "target").mkdir()
            (root / "target" / "artifact.txt").write_text("artifact", encoding="utf-8")
            symlink = root / "symlink"
            try:
                symlink.symlink_to(root / "target", target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink capability unavailable: {error}")
            with self.assertRaisesRegex(ValueError, "symlink or reparse"):
                _safe_regular_files(root, "test root")

    def test_real_junction_is_rejected_when_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            (target / "artifact.txt").write_text("artifact", encoding="utf-8")
            junction = root / "junction"
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                self.skipTest(f"junction capability unavailable: {result.stderr.strip()}")
            with self.assertRaisesRegex(ValueError, "symlink or reparse"):
                _safe_regular_files(root, "test root")

    def test_extra_and_missing_raw_files_fail_exact_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw"
            raw.mkdir()
            plan = _plan()
            for relative in expected_raw_inventory(plan):
                path = raw / relative
                path.write_bytes(b"artifact")
            validate_exact_raw_inventory(root, plan)
            (raw / "extra.txt").write_bytes(b"extra")
            with self.assertRaisesRegex(ValueError, "raw evidence inventory"):
                validate_exact_raw_inventory(root, plan)
            (raw / "extra.txt").unlink()
            (raw / next(iter(expected_raw_inventory(plan)))).unlink()
            with self.assertRaisesRegex(ValueError, "raw evidence inventory"):
                validate_exact_raw_inventory(root, plan)


class TestOfflineSourceAdmission(unittest.TestCase):
    def test_live_source_admission_rejects_a_runtime_identity_mismatch(self):
        plan = _plan()
        with patch(
            "anachron.v0_measurement._runtime_identity",
            return_value=dict(plan["python"], version="3.12.9"),
        ), self.assertRaisesRegex(RuntimeError, "Python runtime identity mismatch"):
            admit_committed_source(plan, Path(__file__).resolve().parent.parent)

    def test_tampered_python_tag_and_blob_receipt_fields_are_rejected(self):
        assert _TEST_REPOSITORY_ROOT is not None
        plan = _plan()
        receipt = _source_admission(plan, _TEST_REPOSITORY_ROOT)
        verify_source_admission(plan, receipt, _TEST_REPOSITORY_ROOT)
        for mutate in (
            lambda value: value["python"].update({"version": "3.12.9"}),
            lambda value: value["tag"].update({"local_object": "0" * 40}),
            lambda value: value["governed_blobs"]["anachron/core/leakage.py"].update({"oid": "0" * 40}),
        ):
            candidate = json.loads(json.dumps(receipt))
            mutate(candidate)
            with self.assertRaises(ValueError):
                verify_source_admission(plan, candidate, _TEST_REPOSITORY_ROOT)

    def test_missing_tag_and_governed_checkout_change_are_rejected(self):
        assert _TEST_REPOSITORY_ROOT is not None
        plan = _plan()
        receipt = _source_admission(plan, _TEST_REPOSITORY_ROOT)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            clone = Path(temporary) / "clone"
            shutil.copytree(_TEST_REPOSITORY_ROOT, clone)
            subprocess.run(["git", "tag", "-d", plan["release"]["tag"]], cwd=clone, check=True, capture_output=True)
            with self.assertRaisesRegex(ValueError, "tag"):
                verify_source_admission(plan, receipt, clone)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            clone = Path(temporary) / "clone"
            shutil.copytree(_TEST_REPOSITORY_ROOT, clone)
            (clone / "result.txt").write_text("result successor\n", encoding="utf-8")
            subprocess.run(["git", "add", "result.txt"], cwd=clone, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "results only"], cwd=clone, check=True, capture_output=True)
            verify_source_admission(plan, receipt, clone)
            (clone / "anachron" / "core" / "leakage.py").write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "governed bytes"):
                verify_source_admission(plan, receipt, clone)


class TestRunnerEndToEnd(unittest.TestCase):
    def _write_plan(self, root: Path) -> Path:
        path = root / "plan.json"
        path.write_text(
            json.dumps(_plan(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return path

    def test_loopback_runner_preserves_raw_trace_and_verifies_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path = self._write_plan(root)
            output = root / "evidence"
            with _FakeOllama() as base_url:
                analysis = run_measurement(
                    plan_path, output, source_admitter=_source_admission, transport=_fake_transport(base_url)
                    , repository_root=_TEST_REPOSITORY_ROOT
                )
            self.assertEqual(_OllamaHandler.chat_calls, 48)
            self.assertEqual(analysis["valid_trajectory_count"], 24)
            self.assertFalse(analysis["go"])
            self.assertTrue((output / "raw" / "m01-unrestricted-fin-borealis-2020-06-survivorship-r01.first.request.json").is_file())
            self.assertTrue((output / "raw" / "m01-enforced-fin-borealis-2020-06-survivorship-r01.final.response.json").is_file())
            self.assertTrue((output / "manifest.sha256").is_file())
            self.assertEqual(analyze_evidence(output, _TEST_REPOSITORY_ROOT), analysis)
            journal = (output / "journal.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(journal), 48)

    def test_invalid_tool_call_is_terminal_without_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path = self._write_plan(root)
            output = root / "evidence"
            with _FakeOllama() as base_url:
                _OllamaHandler.invalid_tool = True
                with self.assertRaisesRegex(RuntimeError, "incomplete trajectories"):
                    run_measurement(
                        plan_path, output, source_admitter=_source_admission, transport=_fake_transport(base_url)
                        , repository_root=_TEST_REPOSITORY_ROOT
                    )
            self.assertEqual(_OllamaHandler.chat_calls, 24)
            self.assertFalse((output / "manifest.json").exists())
            self.assertEqual(len((output / "journal.jsonl").read_text(encoding="utf-8").splitlines()), 48)

    def test_digest_mismatch_stops_before_any_chat_trajectory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path = self._write_plan(root)
            with _FakeOllama() as base_url:
                _OllamaHandler.digest = "b" * 64
                with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
                    run_measurement(
                        plan_path, root / "evidence", source_admitter=_source_admission, transport=_fake_transport(base_url),
                        repository_root=_TEST_REPOSITORY_ROOT,
                    )
            self.assertEqual(_OllamaHandler.chat_calls, 0)

    def test_manifest_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path = self._write_plan(root)
            output = root / "evidence"
            with _FakeOllama() as base_url:
                run_measurement(
                    plan_path, output, source_admitter=_source_admission, transport=_fake_transport(base_url)
                    , repository_root=_TEST_REPOSITORY_ROOT
                )
            (output / "runtime.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest hash mismatch"):
                analyze_evidence(output, _TEST_REPOSITORY_ROOT)

    def test_resigned_raw_tool_tampering_still_fails_reconstruction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path = self._write_plan(root)
            output = root / "evidence"
            with _FakeOllama() as base_url:
                run_measurement(
                    plan_path, output, source_admitter=_source_admission, transport=_fake_transport(base_url)
                    , repository_root=_TEST_REPOSITORY_ROOT
                )
            tool = next((output / "raw").glob("*.tool_result.txt"))
            tool.write_text("No results.", encoding="utf-8")
            files = []
            for path in sorted(output.rglob("*")):
                if path.is_file() and path.name not in {"manifest.json", "manifest.sha256"}:
                    files.append({"path": path.relative_to(output).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            manifest["files"] = files
            raw = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
            (output / "manifest.json").write_bytes(raw)
            (output / "manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "trajectory artifact is invalid"):
                analyze_evidence(output, _TEST_REPOSITORY_ROOT)

    def test_resigned_valid_to_invalid_terminal_fails_reconstruction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path = self._write_plan(root)
            output = root / "evidence"
            with _FakeOllama() as base_url:
                run_measurement(
                    plan_path, output, source_admitter=_source_admission, transport=_fake_transport(base_url)
                    , repository_root=_TEST_REPOSITORY_ROOT
                )
            runtime = json.loads((output / "runtime.json").read_text(encoding="utf-8"))
            record = runtime["trajectories"][0]
            record["valid"] = False
            record["invalid_reason"] = "claimed invalid"
            runtime_bytes = json.dumps(runtime, indent=2, sort_keys=True).encode("utf-8") + b"\n"
            (output / "runtime.json").write_bytes(runtime_bytes)
            journal = [json.loads(line) for line in (output / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
            for entry in journal:
                if entry.get("kind") == "trajectory_terminal" and entry["trajectory_id"] == record["trajectory_id"]:
                    entry["valid"] = False
                    entry["reason"] = "claimed invalid"
            journal_bytes = b"".join(json.dumps(entry, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n" for entry in journal)
            (output / "journal.jsonl").write_bytes(journal_bytes)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            manifest["runtime_sha256"] = hashlib.sha256(runtime_bytes).hexdigest()
            files = []
            for path in sorted(output.rglob("*")):
                if path.is_file() and path.name not in {"manifest.json", "manifest.sha256"}:
                    files.append({"path": path.relative_to(output).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            manifest["files"] = files
            manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
            (output / "manifest.json").write_bytes(manifest_bytes)
            (output / "manifest.sha256").write_text(f"{hashlib.sha256(manifest_bytes).hexdigest()}  manifest.json\n", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "false-invalid trajectory"):
                analyze_evidence(output, _TEST_REPOSITORY_ROOT)

    def test_runner_derives_endpoint_and_timeout_from_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path = self._write_plan(root)
            observed = []

            def transport(base_url, path, payload, timeout):
                observed.append((base_url, timeout))
                raise RuntimeError("transport stopped")

            with self.assertRaisesRegex(RuntimeError, "transport stopped"):
                run_measurement(
                    plan_path,
                    root / "evidence",
                    source_admitter=_source_admission,
                    transport=transport,
                    repository_root=_TEST_REPOSITORY_ROOT,
                )
            self.assertEqual(observed, [("http://127.0.0.1:11434", 120)])

    def test_tracked_only_clean_checkout_source_admission_passes(self):
        repository_root = Path(__file__).resolve().parent.parent
        tracked = [
            "anachron/v0_measurement.py",
            "anachron/data/v0_samples.py",
            "anachron/data/corpus.py",
            "anachron/core/leakage.py",
        ]
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            source = temporary_root / "source"
            remote = temporary_root / "remote.git"
            clone = temporary_root / "clone"
            source.mkdir()
            shutil.copy2(repository_root / ".gitattributes", source / ".gitattributes")
            for relative in tracked:
                destination = source / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(repository_root / relative, destination)

            def git(directory, *args):
                subprocess.run(["git", *args], cwd=directory, check=True, capture_output=True)

            git(source, "init", "-b", "master")
            git(source, "config", "user.email", "test@example.invalid")
            git(source, "config", "user.name", "Test")
            git(source, "add", ".")
            git(source, "commit", "-m", "tracked fixture")
            git(source, "tag", "-a", "v0-measurement-protocol-v1", "-m", "fixture")
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            git(source, "remote", "add", "origin", str(remote))
            git(source, "push", "--follow-tags", "origin", "master")
            subprocess.run(["git", "clone", "--no-checkout", str(remote), str(clone)], check=True, capture_output=True)
            git(clone, "config", "core.autocrlf", "true")
            git(clone, "checkout", "--detach", "v0-measurement-protocol-v1")
            plan = _plan()
            plan["release"] = {"tag": "v0-measurement-protocol-v1", "ref": "refs/tags/v0-measurement-protocol-v1", "origin": str(remote), "branch": "master", "remote": "origin"}
            plan["source_hashes"] = {relative: hashlib.sha256((clone / relative).read_bytes()).hexdigest() for relative in tracked}
            plan["registry_sha256"] = plan["source_hashes"]["anachron/data/v0_samples.py"]
            plan["corpus_sha256"] = plan["source_hashes"]["anachron/data/corpus.py"]
            with _frozen_runtime_fixture(plan):
                receipt = admit_committed_source(plan, clone)
            self.assertIn("tag_commit", receipt)
            self.assertFalse((clone / "research" / "routes-v2" / "curation" / "development.review.md").exists())
            self.assertFalse((clone / "research" / "routes-v1").exists())

    def test_lightweight_tag_is_rejected_by_source_admission(self):
        repository_root = Path(__file__).resolve().parent.parent
        tracked = [
            "anachron/v0_measurement.py",
            "anachron/data/v0_samples.py",
            "anachron/data/corpus.py",
            "anachron/core/leakage.py",
        ]
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            shutil.copy2(repository_root / ".gitattributes", source / ".gitattributes")
            for relative in tracked:
                destination = source / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(repository_root / relative, destination)

            def git(*args):
                subprocess.run(["git", *args], cwd=source, check=True, capture_output=True)

            git("init", "-b", "master")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Test")
            git("add", ".")
            git("commit", "-m", "tracked fixture")
            git("tag", "v0-measurement-protocol-v1")
            git("checkout", "--detach", "v0-measurement-protocol-v1")
            plan = _plan()
            plan["source_hashes"] = {
                relative: hashlib.sha256((source / relative).read_bytes()).hexdigest()
                for relative in tracked
            }
            plan["registry_sha256"] = plan["source_hashes"]["anachron/data/v0_samples.py"]
            plan["corpus_sha256"] = plan["source_hashes"]["anachron/data/corpus.py"]
            with (
                self.assertRaisesRegex(RuntimeError, "annotated, not lightweight"),
                _frozen_runtime_fixture(plan),
            ):
                admit_committed_source(plan, source)


if __name__ == "__main__":
    unittest.main()
