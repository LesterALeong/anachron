"""V3 measurement boundaries, including the fake full authorization path."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from anachron import v3_measurement
from anachron.data.v3_samples import v3_samples_by_id
from anachron.v3_measurement import (
    _FULL_GO_KIND,
    _FULL_GO_STATEMENT,
    analyze_evidence,
    expected_raw_inventory,
    expected_trajectories,
    final_request,
    first_request,
    load_plan,
    run_measurement,
    seal_falsifier_receipt,
    validate_calibration_response,
    verify_falsifier_receipt,
    verify_full_go,
    verify_source_admission,
)

ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_ROOT: Path | None = None
_FIXTURE_REMOTE: Path | None = None


def setUpModule() -> None:
    global _FIXTURE_ROOT, _FIXTURE_REMOTE
    temporary = Path(tempfile.mkdtemp())
    source = temporary / "source"
    remote = temporary / "remote.git"
    clone = temporary / "clone"
    source.mkdir()
    shutil.copy2(ROOT / ".gitattributes", source / ".gitattributes")
    for relative in v3_measurement._GOVERNED_CLOSURE:
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    def git(directory: Path, *arguments: str) -> None:
        subprocess.run(["git", *arguments], cwd=directory, check=True, capture_output=True)
    git(source, "init", "-b", "master")
    git(source, "config", "user.email", "test@example.invalid")
    git(source, "config", "user.name", "Test")
    git(source, "add", ".")
    git(source, "commit", "-m", "v3 fixture")
    git(source, "tag", "-a", "v3-measurement-protocol-v1", "-m", "v3 fixture")
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(source, "remote", "add", "origin", str(remote))
    git(source, "push", "--follow-tags", "origin", "master")
    subprocess.run(["git", "clone", "--no-checkout", str(remote), str(clone)], check=True, capture_output=True)
    git(clone, "checkout", "--detach", "v3-measurement-protocol-v1")
    _FIXTURE_ROOT, _FIXTURE_REMOTE = clone, remote


def tearDownModule() -> None:
    if _FIXTURE_ROOT is not None:
        shutil.rmtree(_FIXTURE_ROOT.parent, ignore_errors=True)


def _plan(name: str) -> tuple[dict, bytes]:
    return load_plan(ROOT / "research" / "v3_measurement" / name)


def _admit(plan: dict, root: Path) -> dict:
    assert _FIXTURE_ROOT is not None and _FIXTURE_REMOTE is not None
    fixture_plan = json.loads(json.dumps(plan))
    fixture_plan["release"]["origin"] = str(_FIXTURE_REMOTE)
    identity = {"implementation": "CPython", "major": 3, "minor": 12, "version": "3.12.10"}
    with patch("anachron.v3_measurement._runtime_identity", return_value=identity):
        return v3_measurement.admit_committed_source(fixture_plan, _FIXTURE_ROOT)


def _raw(value: dict) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True).encode() + b"\n"


def _resign_manifest(evidence: Path) -> None:
    files = []
    for path in sorted(evidence.rglob("*")):
        relative = path.relative_to(evidence).as_posix()
        if path.is_file() and relative not in {"manifest.json", "manifest.sha256"}:
            files.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest_bytes = json.dumps(
        {"schema_version": 1, "files": files}, indent=2, sort_keys=True
    ).encode() + b"\n"
    (evidence / "manifest.json").write_bytes(manifest_bytes)
    (evidence / "manifest.sha256").write_bytes(
        f"{hashlib.sha256(manifest_bytes).hexdigest()}  manifest.json\n".encode()
    )


def _chat(model: str, content: str, *, tool_query: str | None = None) -> bytes:
    message = {"role": "assistant", "content": content}
    if tool_query is not None:
        message["tool_calls"] = [
            {"id": "call_native", "function": {"index": 0, "name": "anachron_search", "arguments": {"query": tool_query}}}
        ]
    return _raw(
        {
            "created_at": "2026-09-03T00:00:00Z",
            "done": True,
            "done_reason": "stop",
            "eval_count": 1,
            "eval_duration": 1,
            "load_duration": 1,
            "message": message,
            "model": model,
            "prompt_eval_count": 1,
            "prompt_eval_duration": 1,
            "total_duration": 1,
        }
    )


def _tag(name: str, digest: str) -> dict:
    return {"name": name, "model": name, "modified_at": "2026-08-05T08:41:44.0361924-05:00", "size": 1, "digest": digest, "details": {"parent_model": "", "format": "gguf", "family": "qwen", "families": ["qwen"], "parameter_size": "1B", "quantization_level": "Q4_K_M", "context_length": 8192, "embedding_length": 1}, "capabilities": ["completion", "tools"]}


def _transport(base_url: str, path: str, payload: bytes | None, timeout: int) -> bytes:
    if path == "/api/version":
        assert payload is None
        return _raw({"version": "0.33.2"})
    if path == "/api/tags":
        assert payload is None
        return _raw({"models": [_tag("qwen2.5:7b", "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"), _tag("qwen3:14b-q4_K_M", "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8")]})
    assert path == "/api/chat" and type(payload) is bytes
    request = json.loads(payload)
    if (
        len(request["messages"]) == 3
        and "tools" not in request
        and request["messages"][0]["content"]
        == "Return exactly CALIBRATION_OK after this recorded tool transcript."
    ):
        return _chat(request["model"], "CALIBRATION_OK")
    if "tools" in request:
        prompt = request["messages"][0]["content"]
        return _chat(request["model"], "", tool_query="Equinox" if "Equinox" in prompt else "Acme")
    return _chat(request["model"], "recorded final answer")


class TestV3Topology(unittest.TestCase):
    def test_real_http_transport_uses_bodyless_get_and_exact_post_bytes(self):
        received: list[tuple[str, str, bytes]] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *arguments: object) -> None:
                return

            def _reply(self, body: bytes) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                received.append(("GET", self.path, self.rfile.read(int(self.headers.get("Content-Length", "0")))))
                self._reply(b'{"version":"0.33.2"}\n')

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append(("POST", self.path, body))
                self._reply(b'{"version":"0.33.2"}\n')

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            self.assertEqual(json.loads(v3_measurement._http(base_url, "/api/version", None, 5)), {"version": "0.33.2"})
            body = b'{"exact":"bytes"}\n'
            self.assertEqual(json.loads(v3_measurement._http(base_url, "/api/chat", body, 5)), {"version": "0.33.2"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(received, [("GET", "/api/version", b""), ("POST", "/api/chat", body)])

    def test_real_http_falsifier_and_authorized_full_paths(self):
        """Exercise the complete protocol through ``_http``, not a callable fake."""
        assert _FIXTURE_ROOT is not None

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *arguments: object) -> None:
                return

            def _reply(self, body: bytes) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                raw = _transport("unused", self.path, body if body else None, 5)
                self._reply(raw)

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                raw = _transport("unused", self.path, body, 5)
                self._reply(raw)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = f"http://127.0.0.1:{server.server_port}"
            with tempfile.TemporaryDirectory() as temporary, patch(
                "anachron.v3_measurement._require_loopback", return_value=endpoint
            ):
                root = Path(temporary)
                falsifier = root / "falsifier"
                analysis = run_measurement(
                    ROOT / "research" / "v3_measurement" / "falsifier_plan.json",
                    falsifier,
                    source_admitter=_admit,
                    repository_root=_FIXTURE_ROOT,
                )
                self.assertTrue(analysis["go"])
                receipt = root / "receipt.json"
                seal_falsifier_receipt(falsifier, receipt, _FIXTURE_ROOT)
                _, full_raw = _plan("full_plan.json")
                go = root / "go.json"
                go.write_bytes(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "kind": _FULL_GO_KIND,
                            "decision": "GO",
                            "authorized_by": "Lester Leong",
                            "authorized_at_utc": "2026-09-03T00:00:00+00:00",
                            "statement": _FULL_GO_STATEMENT,
                            "full_plan_sha256": hashlib.sha256(full_raw).hexdigest(),
                            "falsifier_receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
                        },
                        indent=2,
                        sort_keys=True,
                    ).encode() + b"\n"
                )
                result = run_measurement(
                    ROOT / "research" / "v3_measurement" / "full_plan.json",
                    root / "full",
                    source_admitter=_admit,
                    repository_root=_FIXTURE_ROOT,
                    falsifier_evidence=falsifier,
                    falsifier_receipt=receipt,
                    full_go=go,
                )
                self.assertEqual((result["trajectory_count"], result["primary_trajectory_count"]), (336, 264))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def _authorized_full_inputs(self, root: Path) -> tuple[Path, Path, Path]:
        assert _FIXTURE_ROOT is not None
        evidence = root / "falsifier"
        run_measurement(
            ROOT / "research" / "v3_measurement" / "falsifier_plan.json",
            evidence,
            source_admitter=_admit,
            transport=_transport,
            repository_root=_FIXTURE_ROOT,
        )
        receipt = root / "receipt.json"
        seal_falsifier_receipt(evidence, receipt, _FIXTURE_ROOT)
        _, full_raw = _plan("full_plan.json")
        go = root / "go.json"
        go.write_bytes(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": _FULL_GO_KIND,
                    "decision": "GO",
                    "authorized_by": "Lester Leong",
                    "authorized_at_utc": "2026-09-03T00:00:00+00:00",
                    "statement": _FULL_GO_STATEMENT,
                    "full_plan_sha256": hashlib.sha256(full_raw).hexdigest(),
                    "falsifier_receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
                },
                indent=2,
                sort_keys=True,
            )
            .encode()
            + b"\n",
        )
        return evidence, receipt, go

    def test_falsifier_output_overlap_is_rejected_before_transport(self):
        assert _FIXTURE_ROOT is not None
        calls = []

        def transport(*arguments):
            calls.append(arguments)
            raise AssertionError("transport must not be called")

        plan = ROOT / "research" / "v3_measurement" / "falsifier_plan.json"
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            run_measurement(
                plan,
                plan / "evidence",
                source_admitter=_admit,
                transport=transport,
                repository_root=_FIXTURE_ROOT,
            )
        self.assertEqual(calls, [])

    def test_real_directory_link_ancestor_is_rejected_before_transport(self):
        assert _FIXTURE_ROOT is not None
        calls = []

        def transport(*arguments):
            calls.append(arguments)
            raise AssertionError("transport must not be called")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            try:
                os.symlink(target, link, target_is_directory=True)
            except OSError as error:
                if os.name != "nt":
                    self.skipTest(f"directory symlink capability unavailable: {error}")
                junction = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if junction.returncode:
                    self.skipTest(
                        "directory symlink and junction capabilities unavailable: "
                        f"{error}; {junction.stderr.strip()}"
                    )
            with self.assertRaisesRegex(ValueError, "ancestor"):
                run_measurement(
                    ROOT / "research" / "v3_measurement" / "falsifier_plan.json",
                    link / "evidence",
                    source_admitter=_admit,
                    transport=transport,
                    repository_root=_FIXTURE_ROOT,
                )
        self.assertEqual(calls, [])

    def test_full_prerequisite_overlap_and_path_budget_fail_before_transport(self):
        assert _FIXTURE_ROOT is not None
        calls = []

        def transport(*arguments):
            calls.append(arguments)
            raise AssertionError("transport must not be called")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence, receipt, go = self._authorized_full_inputs(root)
            full_plan = ROOT / "research" / "v3_measurement" / "full_plan.json"
            with self.assertRaisesRegex(ValueError, "overlap"):
                run_measurement(
                    full_plan,
                    evidence / "new-output",
                    source_admitter=_admit,
                    transport=transport,
                    repository_root=_FIXTURE_ROOT,
                    falsifier_evidence=evidence,
                    falsifier_receipt=receipt,
                    full_go=go,
                )
            with self.assertRaisesRegex(ValueError, "path budget"):
                run_measurement(
                    full_plan,
                    root / ("x" * 220),
                    source_admitter=_admit,
                    transport=transport,
                    repository_root=_FIXTURE_ROOT,
                    falsifier_evidence=evidence,
                    falsifier_receipt=receipt,
                    full_go=go,
                )

    def test_full_prerequisite_snapshot_survives_caller_mutation_before_verify(self):
        assert _FIXTURE_ROOT is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence, receipt, go = self._authorized_full_inputs(root)
            original = (evidence / "analysis.json").read_bytes()
            calls = []

            def reject_after_snapshot(snapshot: Path, snapshot_receipt: Path, repository: Path):
                (evidence / "analysis.json").write_bytes(b"caller mutation\n")
                self.assertEqual((snapshot / "analysis.json").read_bytes(), original)
                raise ValueError("test snapshot gate")

            def transport(*arguments):
                calls.append(arguments)
                raise AssertionError("transport must not run before prerequisite verification")

            with patch(
                "anachron.v3_measurement.verify_falsifier_receipt",
                side_effect=reject_after_snapshot,
            ), self.assertRaisesRegex(ValueError, "snapshot gate"):
                run_measurement(
                    ROOT / "research" / "v3_measurement" / "full_plan.json",
                    root / "full",
                    source_admitter=_admit,
                    transport=transport,
                    repository_root=_FIXTURE_ROOT,
                    falsifier_evidence=evidence,
                    falsifier_receipt=receipt,
                    full_go=go,
                )
            self.assertEqual(calls, [])
        self.assertEqual(calls, [])

    def test_offline_source_receipt_rejects_forged_tag_blob_remote_and_working_bytes(self):
        assert _FIXTURE_ROOT is not None
        plan, _ = _plan("falsifier_plan.json")
        receipt = _admit(plan, _FIXTURE_ROOT)
        verify_source_admission(plan, receipt, _FIXTURE_ROOT)
        for mutate in (
            lambda value: value["tag"].update({"local_object": "0" * 40}),
            lambda value: value["tag"].update({"remote_object": "0" * 40}),
            lambda value: value["governed_blobs"]["anachron/core/leakage.py"].update({"oid": "0" * 40}),
        ):
            forged = json.loads(json.dumps(receipt))
            mutate(forged)
            with self.assertRaises((RuntimeError, ValueError)):
                verify_source_admission(plan, forged, _FIXTURE_ROOT)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            clone = Path(temporary) / "clone"
            shutil.copytree(_FIXTURE_ROOT, clone)
            (clone / "anachron" / "v3_measurement.py").write_text("forged\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "governed bytes"):
                verify_source_admission(plan, receipt, clone)

    def test_admission_rejects_a_wrong_runtime_before_git_or_network_access(self):
        plan, _ = _plan("falsifier_plan.json")
        with (
            patch("anachron.v3_measurement._runtime_identity", return_value={**plan["python"], "version": "3.12.9"}),
            self.assertRaisesRegex(RuntimeError, "Python runtime identity mismatch"),
        ):
            v3_measurement.admit_committed_source(plan, ROOT)

    def test_plan_source_closure_matches_the_current_v3_files(self):
        for name in ("falsifier_plan.json", "full_plan.json"):
            plan, _ = _plan(name)
            for relative, digest in plan["source_hashes"].items():
                self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), digest)

    def test_frozen_plan_contract_rejects_identity_threshold_and_precondition_changes(self):
        mutations = (
            lambda value: value.update({"plan_id": "forged-plan-id"}),
            lambda value: value["acceptance"].update({"minimum_pooled_reduction": 0.19}),
            lambda value: value["preconditions"].update(
                {"requires_human_go": not value["preconditions"]["requires_human_go"]}
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            for plan_name in ("falsifier_plan.json", "full_plan.json"):
                plan, _ = _plan(plan_name)
                for index, mutate in enumerate(mutations):
                    forged = json.loads(json.dumps(plan))
                    mutate(forged)
                    path = Path(temporary) / f"{plan_name}-{index}.json"
                    path.write_bytes(json.dumps(forged, indent=2, sort_keys=True).encode() + b"\n")
                    with self.assertRaisesRegex(
                        ValueError, f"frozen {plan['kind']}"
                    ):
                        load_plan(path)

    def test_plans_reject_bool_and_integral_float_for_integer_fields(self):
        mutations = (
            lambda value: value.update({"repetitions": True}),
            lambda value: value.update({"trajectory_count": float(value["trajectory_count"])}),
        )
        with tempfile.TemporaryDirectory() as temporary:
            for plan_name in ("falsifier_plan.json", "full_plan.json"):
                plan, _ = _plan(plan_name)
                for index, mutate in enumerate(mutations):
                    forged = json.loads(json.dumps(plan))
                    mutate(forged)
                    path = Path(temporary) / f"{plan_name}-type-{index}.json"
                    path.write_bytes(json.dumps(forged, indent=2, sort_keys=True).encode() + b"\n")
                    with self.assertRaisesRegex(ValueError, "wrong JSON type"):
                        load_plan(path)

    def test_source_admission_rejects_bool_schema_version(self):
        assert _FIXTURE_ROOT is not None
        plan, _ = _plan("falsifier_plan.json")
        receipt = _admit(plan, _FIXTURE_ROOT)
        receipt["schema_version"] = True
        with self.assertRaisesRegex(ValueError, "wrong JSON type"):
            verify_source_admission(plan, receipt, _FIXTURE_ROOT)

    def test_frozen_topologies_and_group_split(self):
        falsifier, _ = _plan("falsifier_plan.json")
        full, _ = _plan("full_plan.json")
        self.assertEqual((len(expected_trajectories(falsifier)), len(expected_raw_inventory(falsifier))), (24, 126))
        self.assertEqual((len(expected_trajectories(full)), len(expected_raw_inventory(full))), (336, 1686))
        self.assertEqual(sum(row["primary"] for row in expected_trajectories(full)), 264)
        self.assertEqual(sum(not row["primary"] for row in expected_trajectories(full)), 72)

    def test_final_request_omits_tools_and_calibration_is_toolless(self):
        plan, _ = _plan("falsifier_plan.json")
        first = first_request("qwen2.5:7b", v3_samples_by_id()[plan["sample_ids"][0]], plan["generation"])
        final = final_request(first, {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "anachron_search", "arguments": {"query": "Acme"}}}]}, "tool result")
        self.assertIn("tools", first)
        self.assertNotIn("tools", final)

    def test_calibration_allows_ollama_metadata_but_rejects_tool_or_thinking_output(self):
        response = json.loads(_chat("qwen2.5:7b", "CALIBRATION_OK"))
        validate_calibration_response(response, "qwen2.5:7b")
        response["message"]["tool_calls"] = []
        with self.assertRaises(ValueError):
            validate_calibration_response(response, "qwen2.5:7b")
        response["message"]["thinking"] = "hidden"
        with self.assertRaises(ValueError):
            validate_calibration_response(response, "qwen2.5:7b")

    def test_chat_state_validators_accept_only_their_own_response_shape(self):
        model = "qwen2.5:7b"
        first = json.loads(_chat(model, "", tool_query="Acme"))
        final = json.loads(_chat(model, "recorded final answer"))
        calibration = json.loads(_chat(model, "CALIBRATION_OK"))

        self.assertEqual(v3_measurement._validate_first_tool_response(first, model), "Acme")
        self.assertEqual(
            v3_measurement._validate_terminal_final_response(final, model),
            "recorded final answer",
        )
        self.assertIsNone(validate_calibration_response(calibration, model))

        validators = (
            ("first", v3_measurement._validate_first_tool_response, first),
            ("final", v3_measurement._validate_terminal_final_response, final),
            ("calibration", validate_calibration_response, calibration),
        )
        expected_rejections = {
            "first": {"final", "calibration"},
            "final": {"first"},
            "calibration": {"first", "final"},
        }
        for name, validator, own_response in validators:
            for other_name, _, response in validators:
                if name == other_name:
                    continue
                with self.subTest(validator=name, response=other_name):
                    if other_name in expected_rejections[name]:
                        with self.assertRaises(ValueError):
                            validator(response, model)
                    else:
                        self.assertEqual(validator(response, model), "CALIBRATION_OK")

    def test_chat_envelope_rejects_missing_extra_and_wrong_scalar_fields(self):
        response = json.loads(_chat("qwen2.5:7b", "recorded final answer"))
        for field in response:
            with self.subTest(case="missing", field=field):
                mutated = deepcopy(response)
                del mutated[field]
                with self.assertRaises(ValueError):
                    v3_measurement._validate_chat_envelope(mutated, "response")
        mutated = deepcopy(response)
        mutated["unexpected"] = "value"
        with self.assertRaises(ValueError):
            v3_measurement._validate_chat_envelope(mutated, "response")

        wrong_scalars = (
            ("model", None),
            ("model", 1),
            ("done_reason", None),
            ("done_reason", True),
            ("done", False),
            ("done", 1),
            ("created_at", "2026-09-03T00:00:00+00:00"),
            ("created_at", "not-a-timestampZ"),
        )
        for field, value in wrong_scalars:
            with self.subTest(case="scalar", field=field, value=repr(value)):
                mutated = deepcopy(response)
                mutated[field] = value
                with self.assertRaises(ValueError):
                    v3_measurement._validate_chat_envelope(mutated, "response")
        for field in (
            "eval_count",
            "eval_duration",
            "load_duration",
            "prompt_eval_count",
            "prompt_eval_duration",
            "total_duration",
        ):
            for value in (True, -1, 1.0, "1"):
                with self.subTest(case="count", field=field, value=repr(value)):
                    mutated = deepcopy(response)
                    mutated[field] = value
                    with self.assertRaises(ValueError):
                        v3_measurement._validate_chat_envelope(mutated, "response")

    def test_chat_state_validators_reject_absent_null_nonstring_and_wrong_roles(self):
        model = "qwen2.5:7b"
        states = (
            ("first", v3_measurement._validate_first_tool_response, json.loads(_chat(model, "", tool_query="Acme"))),
            ("final", v3_measurement._validate_terminal_final_response, json.loads(_chat(model, "recorded final answer"))),
            ("calibration", validate_calibration_response, json.loads(_chat(model, "CALIBRATION_OK"))),
        )
        for name, validator, response in states:
            absent = deepcopy(response)
            del absent["message"]["role"]
            with self.subTest(state=name, role="absent"), self.assertRaises(ValueError):
                validator(absent, model)
            for role in (None, 1, "user", "system", "tool"):
                with self.subTest(state=name, role=repr(role)):
                    mutated = deepcopy(response)
                    mutated["message"]["role"] = role
                    with self.assertRaises(ValueError):
                        validator(mutated, model)

    def test_first_response_rejects_every_noncanonical_tool_call_shape(self):
        model = "qwen2.5:7b"
        response = json.loads(_chat(model, "", tool_query="Acme"))
        validator = v3_measurement._validate_first_tool_response
        mutations = (
            ("wrong-terminal-reason", lambda body: body.update({"done_reason": "length"})),
            ("nonempty-content", lambda body: body["message"].update({"content": "answer"})),
            ("empty-tool-calls", lambda body: body["message"].update({"tool_calls": []})),
            ("two-tool-calls", lambda body: body["message"]["tool_calls"].append(deepcopy(body["message"]["tool_calls"][0]))),
            ("missing-id", lambda body: body["message"]["tool_calls"][0].pop("id")),
            ("empty-id", lambda body: body["message"]["tool_calls"][0].update({"id": ""})),
            ("null-id", lambda body: body["message"]["tool_calls"][0].update({"id": None})),
            ("missing-function", lambda body: body["message"]["tool_calls"][0].pop("function")),
            ("wrong-function-name", lambda body: body["message"]["tool_calls"][0]["function"].update({"name": "other"})),
            ("missing-arguments", lambda body: body["message"]["tool_calls"][0]["function"].pop("arguments")),
            ("missing-query", lambda body: body["message"]["tool_calls"][0]["function"]["arguments"].pop("query")),
            ("empty-query", lambda body: body["message"]["tool_calls"][0]["function"]["arguments"].update({"query": "  "})),
            ("null-query", lambda body: body["message"]["tool_calls"][0]["function"]["arguments"].update({"query": None})),
        )
        for name, mutate in mutations:
            with self.subTest(case=name):
                mutated = deepcopy(response)
                mutate(mutated)
                with self.assertRaises(ValueError):
                    validator(mutated, model)
        for value, accepted in ((None, False), (False, False), (0.0, False), (-1, False), (1, False), (0, True)):
            with self.subTest(index=repr(value)):
                mutated = deepcopy(response)
                function = mutated["message"]["tool_calls"][0]["function"]
                self.assertIn("index", function)
                if value is None:
                    del function["index"]
                else:
                    function["index"] = value
                if accepted:
                    self.assertEqual(validator(mutated, model), "Acme")
                else:
                    with self.assertRaises(ValueError):
                        validator(mutated, model)

    def test_final_and_calibration_responses_reject_tool_calls_extras_and_wrong_content(self):
        model = "qwen2.5:7b"
        states = (
            ("final", v3_measurement._validate_terminal_final_response, json.loads(_chat(model, "recorded final answer")), ("",)),
            ("calibration", validate_calibration_response, json.loads(_chat(model, "CALIBRATION_OK")), ("", "recorded final answer")),
        )
        for name, validator, response, invalid_contents in states:
            mutations = (
                ("tool_calls", []),
                ("tool_calls", None),
                ("tool_calls", [{"function": {"name": "anachron_search"}}]),
                ("thinking", "hidden"),
                ("unexpected", "value"),
            ) + tuple(("content", content) for content in invalid_contents)
            for field, value in mutations:
                with self.subTest(state=name, field=field, value=repr(value)):
                    mutated = deepcopy(response)
                    mutated["message"][field] = value
                    with self.assertRaises(ValueError):
                        validator(mutated, model)

    def test_validators_run_envelope_checks_before_any_state_specific_success(self):
        model = "qwen2.5:7b"
        validators = (
            (v3_measurement._validate_first_tool_response, {"message": {}}),
            (v3_measurement._validate_terminal_final_response, {"message": {}}),
            (validate_calibration_response, {"message": {}}),
        )
        for validator, response in validators:
            with self.subTest(validator=validator.__name__), patch(
                "anachron.v3_measurement._validate_chat_envelope",
                side_effect=ValueError("envelope-first"),
            ) as envelope:
                with self.assertRaisesRegex(ValueError, "envelope-first"):
                    validator(response, model)
                envelope.assert_called_once_with(response, unittest.mock.ANY)

    def test_first_response_validation_blocks_caller_before_final_request_construction(self):
        assert _FIXTURE_ROOT is not None
        chat_requests: list[dict] = []

        def recording_transport(base_url: str, path: str, payload: bytes | None, timeout: int) -> bytes:
            if path == "/api/chat":
                assert payload is not None
                chat_requests.append(json.loads(payload))
            return _transport(base_url, path, payload, timeout)

        with tempfile.TemporaryDirectory() as temporary, patch(
            "anachron.v3_measurement._validate_first_tool_response",
            side_effect=ValueError("first response rejected"),
        ):
            evidence = Path(temporary) / "evidence"
            with self.assertRaisesRegex(RuntimeError, "stopped unsealed"):
                run_measurement(
                    ROOT / "research" / "v3_measurement" / "falsifier_plan.json",
                    evidence,
                    source_admitter=_admit,
                    transport=recording_transport,
                    repository_root=_FIXTURE_ROOT,
                )
        self.assertEqual(len(chat_requests), 26)
        self.assertEqual(sum("tools" not in request for request in chat_requests), 2)
        self.assertTrue(all("tools" in request for request in chat_requests[2:]))

    def test_resigned_raw_state_forgeries_reject_after_runtime_hash_rebinding(self):
        assert _FIXTURE_ROOT is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sealed = root / "sealed"
            run_measurement(
                ROOT / "research" / "v3_measurement" / "falsifier_plan.json",
                sealed,
                source_admitter=_admit,
                transport=_transport,
                repository_root=_FIXTURE_ROOT,
            )
            first_id = "m01-unrestricted-fin-acme-2022-01-future-r01"
            mutations = (
                (
                    "first-wrong-role",
                    f"raw/{first_id}.first.response.json",
                    lambda body: body["message"].update({"role": "user"}),
                    lambda runtime, digest: runtime["trajectories"][0].update(
                        {"first_response_sha256": digest}
                    ),
                ),
                (
                    "first-smuggled-tool-call",
                    f"raw/{first_id}.first.response.json",
                    lambda body: body["message"]["tool_calls"].append(
                        deepcopy(body["message"]["tool_calls"][0])
                    ),
                    lambda runtime, digest: runtime["trajectories"][0].update(
                        {"first_response_sha256": digest}
                    ),
                ),
                (
                    "final-wrong-role",
                    f"raw/{first_id}.final.response.json",
                    lambda body: body["message"].update({"role": "tool"}),
                    lambda runtime, digest: runtime["trajectories"][0].update(
                        {"final_response_sha256": digest}
                    ),
                ),
                (
                    "final-smuggled-tool-call",
                    f"raw/{first_id}.final.response.json",
                    lambda body: body["message"].update({"tool_calls": []}),
                    lambda runtime, digest: runtime["trajectories"][0].update(
                        {"final_response_sha256": digest}
                    ),
                ),
                (
                    "calibration-wrong-role",
                    "raw/calibration.m01.response.json",
                    lambda body: body["message"].update({"role": "system"}),
                    lambda runtime, digest: runtime["calibrations"][0].update(
                        {"response_sha256": digest}
                    ),
                ),
                (
                    "calibration-smuggled-tool-call",
                    "raw/calibration.m01.response.json",
                    lambda body: body["message"].update({"tool_calls": []}),
                    lambda runtime, digest: runtime["calibrations"][0].update(
                        {"response_sha256": digest}
                    ),
                ),
            )
            for index, (name, relative, mutate, bind_runtime) in enumerate(mutations):
                with self.subTest(case=name):
                    evidence = root / f"forgery-{index}"
                    shutil.copytree(sealed, evidence)
                    raw_path = evidence / relative
                    body = json.loads(raw_path.read_text())
                    mutate(body)
                    raw_path.write_bytes(_raw(body))
                    runtime_path = evidence / "runtime.json"
                    runtime = json.loads(runtime_path.read_text())
                    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
                    bind_runtime(runtime, digest)
                    runtime_path.write_bytes(_raw(runtime))
                    _resign_manifest(evidence)
                    self.assertEqual(
                        hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                        digest,
                    )
                    with self.assertRaises(ValueError):
                        analyze_evidence(evidence, _FIXTURE_ROOT)

    def test_falsifier_receipt_and_authorized_full_fake_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            falsifier_path = ROOT / "research" / "v3_measurement" / "falsifier_plan.json"
            evidence = root / "falsifier"
            analysis = run_measurement(falsifier_path, evidence, source_admitter=_admit, transport=_transport, repository_root=_FIXTURE_ROOT)
            self.assertTrue(analysis["go"])
            self.assertEqual(analysis["trajectory_count"], 24)
            self.assertEqual(analyze_evidence(evidence, _FIXTURE_ROOT), analysis)
            receipt = root / "receipt.json"
            seal_falsifier_receipt(evidence, receipt, _FIXTURE_ROOT)
            full_path = ROOT / "research" / "v3_measurement" / "full_plan.json"
            _, full_raw = load_plan(full_path)
            go = root / "go.json"
            go.write_bytes(json.dumps({"schema_version": 1, "kind": _FULL_GO_KIND, "decision": "GO", "authorized_by": "Lester Leong", "authorized_at_utc": "2026-09-03T00:00:00+00:00", "statement": _FULL_GO_STATEMENT, "full_plan_sha256": hashlib.sha256(full_raw).hexdigest(), "falsifier_receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()}, indent=2, sort_keys=True).encode() + b"\n")
            full = run_measurement(full_path, root / "full", source_admitter=_admit, transport=_transport, repository_root=_FIXTURE_ROOT, falsifier_evidence=evidence, falsifier_receipt=receipt, full_go=go)
            self.assertEqual(full["trajectory_count"], 336)
            self.assertEqual(full["primary_trajectory_count"], 264)
            self.assertEqual((root / "full" / "prerequisites" / "falsifier" / "analysis.json").read_bytes(), (evidence / "analysis.json").read_bytes())
            self.assertEqual((root / "full" / "prerequisites" / "falsifier_receipt.json").read_bytes(), receipt.read_bytes())
            self.assertEqual((root / "full" / "prerequisites" / "full_go.json").read_bytes(), go.read_bytes())

    def test_receipt_and_tool_topology_tampering_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path = ROOT / "research" / "v3_measurement" / "falsifier_plan.json"
            evidence = root / "evidence"
            run_measurement(plan_path, evidence, source_admitter=_admit, transport=_transport, repository_root=_FIXTURE_ROOT)
            raw = next((evidence / "raw").glob("*.final.request.json"))
            body = json.loads(raw.read_text())
            body["tools"] = []
            raw.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            manifest = json.loads((evidence / "manifest.json").read_text())
            for item in manifest["files"]:
                if item["path"] == raw.relative_to(evidence).as_posix():
                    item["sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
            manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
            (evidence / "manifest.json").write_bytes(manifest_bytes)
            (evidence / "manifest.sha256").write_bytes(
                f"{hashlib.sha256(manifest_bytes).hexdigest()}  manifest.json\n".encode()
            )
            with self.assertRaises(ValueError):
                analyze_evidence(evidence, _FIXTURE_ROOT)

    def test_resigned_forged_source_runtime_score_and_analysis_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "evidence"
            run_measurement(
                ROOT / "research" / "v3_measurement" / "falsifier_plan.json",
                evidence,
                source_admitter=_admit,
                transport=_transport,
                repository_root=_FIXTURE_ROOT,
            )
            source = json.loads((evidence / "source_admission.json").read_text())
            source["tag"]["remote_object"] = "0" * 40
            (evidence / "source_admission.json").write_text(
                json.dumps(source, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            runtime = json.loads((evidence / "runtime.json").read_text())
            runtime["trajectories"][0]["score"]["tclr"] = 0.0
            (evidence / "runtime.json").write_text(
                json.dumps(runtime, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            (evidence / "analysis.json").write_text("{}\n", encoding="utf-8")
            files = []
            for path in sorted(evidence.rglob("*")):
                if path.is_file() and path.relative_to(evidence).as_posix() not in {"manifest.json", "manifest.sha256"}:
                    files.append({"path": path.relative_to(evidence).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            manifest = {"schema_version": 1, "files": files}
            manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
            (evidence / "manifest.json").write_bytes(manifest_bytes)
            (evidence / "manifest.sha256").write_bytes(
                f"{hashlib.sha256(manifest_bytes).hexdigest()}  manifest.json\n".encode()
            )
            with self.assertRaises(ValueError):
                analyze_evidence(evidence, _FIXTURE_ROOT)

    def test_resigned_server_and_calibration_tampering_are_rejected(self):
        assert _FIXTURE_ROOT is not None
        mutations = (
            (
                "server.version.response.json",
                lambda value: value.update({"version": ""}),
                lambda runtime, evidence: runtime["server"].update(
                    {
                        "version_response_sha256": hashlib.sha256(
                            (evidence / "raw" / "server.version.response.json").read_bytes()
                        ).hexdigest()
                    }
                ),
                "version response",
            ),
            (
                "server.tags.response.json",
                lambda value: value["models"][0].update({"digest": "forged"}),
                lambda runtime, evidence: runtime["server"].update(
                    {
                        "tags_response_sha256": hashlib.sha256(
                            (evidence / "raw" / "server.tags.response.json").read_bytes()
                        ).hexdigest()
                    }
                ),
                "model digest",
            ),
            (
                "calibration.m01.request.json",
                lambda value: value["messages"][0].update({"content": "forged"}),
                lambda runtime, evidence: runtime["calibrations"][0].update(
                    {
                        "request_sha256": hashlib.sha256(
                            (evidence / "raw" / "calibration.m01.request.json").read_bytes()
                        ).hexdigest()
                    }
                ),
                "calibration request reconstruction",
            ),
            (
                "calibration.m01.response.json",
                lambda value: value["message"].update({"content": "forged"}),
                lambda runtime, evidence: runtime["calibrations"][0].update(
                    {
                        "response_sha256": hashlib.sha256(
                            (evidence / "raw" / "calibration.m01.response.json").read_bytes()
                        ).hexdigest()
                    }
                ),
                "calibration response",
            ),
        )
        for relative, mutate, bind_runtime, message in mutations:
            with tempfile.TemporaryDirectory() as temporary:
                evidence = Path(temporary) / "evidence"
                run_measurement(
                    ROOT / "research" / "v3_measurement" / "falsifier_plan.json",
                    evidence,
                    source_admitter=_admit,
                    transport=_transport,
                    repository_root=_FIXTURE_ROOT,
                )
                raw = evidence / "raw" / relative
                body = json.loads(raw.read_text())
                mutate(body)
                raw.write_bytes(json.dumps(body, indent=2, sort_keys=True).encode() + b"\n")
                runtime_path = evidence / "runtime.json"
                runtime = json.loads(runtime_path.read_text())
                bind_runtime(runtime, evidence)
                runtime_path.write_bytes(json.dumps(runtime, indent=2, sort_keys=True).encode() + b"\n")
                _resign_manifest(evidence)
                with self.assertRaises((ValueError, RuntimeError)):
                    analyze_evidence(evidence, _FIXTURE_ROOT)

    def test_resigned_runtime_rejects_bool_and_integer_type_confusion(self):
        assert _FIXTURE_ROOT is not None
        mutations = (
            (
                lambda runtime: runtime["calibrations"][0].update({"request_count": True}),
                "wrong JSON type",
            ),
            (
                lambda runtime: runtime["calibrations"][0].update({"passed": 1}),
                "wrong JSON type",
            ),
            (
                lambda runtime: runtime["trajectories"][0]["score"].update({"tclr": True}),
                "wrong JSON type",
            ),
        )
        for mutate, message in mutations:
            with tempfile.TemporaryDirectory() as temporary:
                evidence = Path(temporary) / "evidence"
                run_measurement(
                    ROOT / "research" / "v3_measurement" / "falsifier_plan.json",
                    evidence,
                    source_admitter=_admit,
                    transport=_transport,
                    repository_root=_FIXTURE_ROOT,
                )
                runtime_path = evidence / "runtime.json"
                runtime = json.loads(runtime_path.read_text())
                mutate(runtime)
                runtime_path.write_bytes(json.dumps(runtime, indent=2, sort_keys=True).encode() + b"\n")
                _resign_manifest(evidence)
                with self.assertRaisesRegex(ValueError, message):
                    analyze_evidence(evidence, _FIXTURE_ROOT)

    def test_resigned_request_type_aliases_fail_byte_replay(self):
        """Python equality must not accept false, zero, or integral floats."""
        assert _FIXTURE_ROOT is not None
        mutations = (
            ("*.first.request.json", "think", 0),
            ("*.final.request.json", "think", 0.0),
            ("calibration.m01.request.json", "think", None),
            ("*.first.request.json", "options.temperature", False),
            ("*.final.request.json", "options.seed", 0.0),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sealed = root / "sealed"
            run_measurement(
                ROOT / "research" / "v3_measurement" / "falsifier_plan.json",
                sealed,
                source_admitter=_admit,
                transport=_transport,
                repository_root=_FIXTURE_ROOT,
            )
            for index, (name, field, replacement) in enumerate(mutations):
                evidence = root / f"evidence-{index}"
                shutil.copytree(sealed, evidence)
                path = next((evidence / "raw").glob(name))
                body = json.loads(path.read_text())
                if field.startswith("options."):
                    body["options"][field.split(".", 1)[1]] = replacement
                else:
                    body[field] = replacement
                path.write_bytes(json.dumps(body, indent=2, sort_keys=True).encode() + b"\n")
                runtime_path = evidence / "runtime.json"
                runtime = json.loads(runtime_path.read_text())
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if path.name.startswith("calibration"):
                    runtime["calibrations"][0]["request_sha256"] = digest
                else:
                    record = next(
                        item for item in runtime["trajectories"] if path.name.startswith(item["trajectory_id"])
                    )
                    record[
                        "first_request_sha256" if ".first." in path.name else "final_request_sha256"
                    ] = digest
                runtime_path.write_bytes(json.dumps(runtime, indent=2, sort_keys=True).encode() + b"\n")
                _resign_manifest(evidence)
                with self.assertRaises(ValueError):
                    analyze_evidence(evidence, _FIXTURE_ROOT)

    def test_duplicate_nonfinite_and_nonutc_raw_responses_reject_before_equality(self):
        assert _FIXTURE_ROOT is not None
        cases = (
            b'{"version":"0.33.2","version":"0.33.2"}\n',
            b'{"version":NaN}\n',
            b'{"version":1e309}\n',
        )
        for raw in cases:
            with tempfile.TemporaryDirectory() as temporary:
                evidence = Path(temporary) / "evidence"
                run_measurement(
                    ROOT / "research" / "v3_measurement" / "falsifier_plan.json",
                    evidence,
                    source_admitter=_admit,
                    transport=_transport,
                    repository_root=_FIXTURE_ROOT,
                )
                path = evidence / "raw" / "server.version.response.json"
                path.write_bytes(raw)
                runtime_path = evidence / "runtime.json"
                runtime = json.loads(runtime_path.read_text())
                runtime["server"]["version_response_sha256"] = hashlib.sha256(raw).hexdigest()
                runtime_path.write_bytes(json.dumps(runtime, indent=2, sort_keys=True).encode() + b"\n")
                _resign_manifest(evidence)
                with self.assertRaises(ValueError):
                    analyze_evidence(evidence, _FIXTURE_ROOT)

    def test_nonfinite_runtime_and_analysis_bytes_reject_before_schema_checks(self):
        assert _FIXTURE_ROOT is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sealed = root / "sealed"
            run_measurement(ROOT / "research" / "v3_measurement" / "falsifier_plan.json", sealed, source_admitter=_admit, transport=_transport, repository_root=_FIXTURE_ROOT)
            for index, relative in enumerate(("runtime.json", "analysis.json")):
                evidence = root / f"evidence-{index}"
                shutil.copytree(sealed, evidence)
                (evidence / relative).write_bytes(b'{"overflow":1e309}\n')
                _resign_manifest(evidence)
                with self.assertRaises(ValueError):
                    analyze_evidence(evidence, _FIXTURE_ROOT)

    def test_resigned_journal_whitespace_and_final_newline_mutations_reject(self):
        assert _FIXTURE_ROOT is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sealed = root / "sealed"
            run_measurement(ROOT / "research" / "v3_measurement" / "falsifier_plan.json", sealed, source_admitter=_admit, transport=_transport, repository_root=_FIXTURE_ROOT)
            original = (sealed / "journal.jsonl").read_bytes()
            mutations = (original.replace(b"{", b"{ ", 1), original[:-1])
            for index, mutated in enumerate(mutations):
                self.assertNotEqual(mutated, original)
                evidence = root / f"evidence-{index}"
                shutil.copytree(sealed, evidence)
                (evidence / "journal.jsonl").write_bytes(mutated)
                _resign_manifest(evidence)
                with self.assertRaises(ValueError):
                    analyze_evidence(evidence, _FIXTURE_ROOT)

    def test_resigned_fixed_readme_mutation_rejects(self):
        assert _FIXTURE_ROOT is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "evidence"
            run_measurement(ROOT / "research" / "v3_measurement" / "falsifier_plan.json", evidence, source_admitter=_admit, transport=_transport, repository_root=_FIXTURE_ROOT)
            path = evidence / "README.md"
            original = path.read_bytes()
            mutated = original + b"changed\n"
            self.assertNotEqual(mutated, original)
            path.write_bytes(mutated)
            _resign_manifest(evidence)
            with self.assertRaises(ValueError):
                analyze_evidence(evidence, _FIXTURE_ROOT)

    def test_every_external_boundary_stops_unsealed_with_typed_terminal(self):
        assert _FIXTURE_ROOT is not None
        phases = ("version", "tags", "calibration", "first", "final")
        for phase in phases:
            with tempfile.TemporaryDirectory() as temporary:
                evidence = Path(temporary) / "evidence"

                def failing_transport(
                    base: str,
                    path: str,
                    body: bytes | None,
                    timeout: int,
                    phase_value: str = phase,
                ):
                    if (
                        (phase_value == "version" and path == "/api/version")
                        or (phase_value == "tags" and path == "/api/tags")
                        or (
                            phase_value == "calibration"
                            and path == "/api/chat"
                            and body is not None
                            and len(json.loads(body)["messages"]) == 3
                            and "tools" not in json.loads(body)
                        )
                        or (
                            phase_value == "first"
                            and path == "/api/chat"
                            and body is not None
                            and "tools" in json.loads(body)
                        )
                        or (
                            phase_value == "final"
                            and path == "/api/chat"
                            and body is not None
                            and len(json.loads(body)["messages"]) == 3
                            and "tools" not in json.loads(body)
                            and json.loads(body)["messages"][0]["content"] != "Return exactly CALIBRATION_OK after this recorded tool transcript."
                        )
                    ):
                        raise ConnectionError(phase_value)
                    return _transport(base, path, body, timeout)

                with self.assertRaisesRegex(RuntimeError, "stopped unsealed"):
                    run_measurement(
                        ROOT / "research" / "v3_measurement" / "falsifier_plan.json",
                        evidence,
                        source_admitter=_admit,
                        transport=failing_transport,
                        repository_root=_FIXTURE_ROOT,
                    )
                terminal = json.loads((evidence / "terminal_failure.json").read_text())
                self.assertEqual(terminal["sealed"], False)
                self.assertIs(type(terminal["failed_step"]), int)
                self.assertIs(type(terminal["last_completed_step"]), int)
                self.assertEqual(terminal["fault_code"], "ConnectionError" if phase in {"version", "tags", "calibration"} else "RuntimeError")
                self.assertFalse((evidence / "manifest.json").exists())

    def test_resigned_response_scalar_aliases_fail_before_equality(self):
        assert _FIXTURE_ROOT is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sealed = root / "sealed"
            run_measurement(
                ROOT / "research" / "v3_measurement" / "falsifier_plan.json",
                sealed,
                source_admitter=_admit,
                transport=_transport,
                repository_root=_FIXTURE_ROOT,
            )
            mutations = (
                ("raw/server.tags.response.json", lambda body: body["models"][0].update({"size": True})),
                ("raw/server.tags.response.json", lambda body: body["models"][0]["details"].update({"context_length": 8192.0})),
                ("raw/calibration.m01.response.json", lambda body: body.update({"done": 1})),
                ("raw/calibration.m01.response.json", lambda body: body.update({"total_duration": False})),
                ("raw/m01-unrestricted-fin-acme-2022-01-future-r01.first.response.json", lambda body: body["message"]["tool_calls"][0]["function"].update({"index": False})),
            )
            for index, (relative, mutate) in enumerate(mutations):
                evidence = root / f"evidence-{index}"
                shutil.copytree(sealed, evidence)
                path = evidence / relative
                body = json.loads(path.read_text())
                mutate(body)
                if path.name.endswith(".first.response.json"):
                    self.assertIs(
                        body["message"]["tool_calls"][0]["function"]["index"],
                        False,
                    )
                path.write_bytes(json.dumps(body, indent=2, sort_keys=True).encode() + b"\n")
                runtime_path = evidence / "runtime.json"
                runtime = json.loads(runtime_path.read_text())
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if path.name == "server.tags.response.json":
                    runtime["server"]["tags_response_sha256"] = digest
                elif path.name.startswith("calibration"):
                    runtime["calibrations"][0]["response_sha256"] = digest
                else:
                    runtime["trajectories"][0]["first_response_sha256"] = digest
                runtime_path.write_bytes(json.dumps(runtime, indent=2, sort_keys=True).encode() + b"\n")
                _resign_manifest(evidence)
                with self.assertRaises(ValueError):
                    analyze_evidence(evidence, _FIXTURE_ROOT)

    def test_receipt_and_human_go_require_canonical_bytes(self):
        assert _FIXTURE_ROOT is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence, receipt, go = self._authorized_full_inputs(root)
            receipt_body = json.loads(receipt.read_text())
            receipt.write_bytes(json.dumps(receipt_body, indent=4, sort_keys=True).encode() + b"\n")
            with self.assertRaisesRegex(ValueError, "receipt must use canonical JSON bytes"):
                verify_falsifier_receipt(evidence, receipt, _FIXTURE_ROOT)
            seal_falsifier_receipt(evidence, root / "canonical-receipt.json", _FIXTURE_ROOT)
            canonical_receipt = root / "canonical-receipt.json"
            full_plan, full_raw = _plan("full_plan.json")
            go_body = json.loads(go.read_text())
            go_body = {key: go_body[key] for key in reversed(go_body)}
            go.write_bytes(json.dumps(go_body, indent=2, sort_keys=False).encode() + b"\n")
            with self.assertRaisesRegex(ValueError, "human GO must use canonical JSON bytes"):
                verify_full_go(full_plan, full_raw, canonical_receipt, go)

    def test_checked_in_human_go_template_is_not_an_authorization(self):
        full_plan, full_raw = _plan("full_plan.json")
        template = ROOT / "research" / "v3_measurement" / "full_go.template.json"
        body = json.loads(template.read_text())
        self.assertEqual(body["decision"], "PENDING")
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / "receipt.json"
            receipt.write_bytes(b"{}\n")
            with self.assertRaisesRegex(ValueError, "human GO timestamp is not ISO-8601"):
                verify_full_go(full_plan, full_raw, receipt, template)
            inactive = {
                "schema_version": 1,
                "kind": _FULL_GO_KIND,
                "decision": body["decision"],
                "authorized_by": "Lester Leong",
                "authorized_at_utc": "2026-09-03T00:00:00+00:00",
                "statement": _FULL_GO_STATEMENT,
                "full_plan_sha256": hashlib.sha256(full_raw).hexdigest(),
                "falsifier_receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
            }
            inactive_path = Path(temporary) / "inactive.json"
            inactive_path.write_bytes(json.dumps(inactive, indent=2, sort_keys=True).encode() + b"\n")
            with self.assertRaisesRegex(ValueError, "human GO does not bind"):
                verify_full_go(full_plan, full_raw, receipt, inactive_path)

    def test_receipt_and_human_go_reject_bool_schema_versions(self):
        assert _FIXTURE_ROOT is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence, receipt, go = self._authorized_full_inputs(root)
            receipt_body = json.loads(receipt.read_text())
            receipt_body["schema_version"] = True
            receipt.write_bytes(json.dumps(receipt_body, indent=2, sort_keys=True).encode() + b"\n")
            with self.assertRaisesRegex(ValueError, "falsifier receipt.schema_version"):
                verify_falsifier_receipt(evidence, receipt, _FIXTURE_ROOT)
            full_plan, full_raw = _plan("full_plan.json")
            go_body = json.loads(go.read_text())
            go_body["schema_version"] = True
            go.write_bytes(json.dumps(go_body, indent=2, sort_keys=True).encode() + b"\n")
            with self.assertRaisesRegex(ValueError, "human GO.schema_version"):
                verify_full_go(full_plan, full_raw, receipt, go)


if __name__ == "__main__":
    unittest.main()
