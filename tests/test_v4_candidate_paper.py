"""Focused deterministic rendering tests for the v4 candidate-paper builder."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from anachron.v4_candidate_common import pooled_tclr_direction
from anachron.v4_contract import V4ContractError
from tests.test_v4_candidate_projection import V4CandidateProjectionTests
from tools import build_v4_measurement_candidate_paper as builder
from tools import build_v4_source_manifest

ROOT = Path(__file__).resolve().parents[1]
TECTONIC = (
    Path(os.environ["ANACHRON_V4_TECTONIC"])
    if "ANACHRON_V4_TECTONIC" in os.environ
    else None
)


def _paper_dependencies_available() -> bool:
    try:
        return all(importlib.util.find_spec(name) is not None for name in ("fitz", "PIL"))
    except (ImportError, AttributeError, ValueError):
        return False


PAPER_DEPENDENCIES_AVAILABLE = _paper_dependencies_available()
PAPER_DEPENDENCIES_REASON = "v4 paper tests require the [paper] extras"


class V4CandidatePaperTests(unittest.TestCase):
    @staticmethod
    def _git(root: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _repository(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path, dict[str, str]]:
        temporary = tempfile.TemporaryDirectory()
        base = Path(temporary.name)
        origin = base / "origin.git"
        root = base / "repository"
        self._git(base, "init", "--bare", str(origin))
        shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
        self._git(root, "init")
        self._git(root, "config", "user.email", "test@example.com")
        self._git(root, "config", "user.name", "Test")
        self._git(root, "add", ".")
        self._git(root, "commit", "-m", "v3 source")
        v3_commit = self._git(root, "rev-parse", "HEAD")
        self._git(root, "tag", "-a", "v3-test", "-m", "v3")
        v3_tag_object = self._git(root, "rev-parse", "refs/tags/v3-test^{tag}")
        self._git(root, "remote", "add", "origin", str(origin))
        self._git(root, "push", "origin", "master", "refs/tags/v3-test")
        self._git(root, "checkout", "-b", "protocol/v4-recovery-v1")
        self._git(root, "commit", "--allow-empty", "-m", "v4 source")
        self._git(root, "tag", "-a", "v4-measurement-protocol-v1", "-m", "v4")
        self._git(root, "push", "origin", "protocol/v4-recovery-v1", "refs/tags/v4-measurement-protocol-v1")
        self._git(root, "checkout", "--detach", "v4-measurement-protocol-v1")
        return temporary, root, origin, {"commit": v3_commit, "tag": "v3-test", "tag_object": v3_tag_object}

    @staticmethod
    def _closure(value: dict, authority: dict[str, str]) -> None:
        rows = [
            {"path": path, "sha256": authority[field]}
            for field, path in sorted(builder._AUTHORITY_CLOSURE_FIELDS.items(), key=lambda row: row[1])
        ]
        value["evidence_closure"] = {
            "files": rows,
            "schema_version": "anachron-v4-whole-evidence-closure-v1",
            "sha256": hashlib.sha256(builder.canonical_json_bytes(rows)).hexdigest(),
        }

    def _envelope(self, root: Path, manifest: Path, release: dict[str, str]) -> dict:
        value = {"projection": self._projection("positive")["projection"]}
        authority = {
            "actual_go_sha256": "1" * 64,
            "authority_contract_sha256": hashlib.sha256((root / "research/v4_measurement/authority_binding_contract.json").read_bytes()).hexdigest(),
            "comparison_projection_sha256": "2" * 64,
            "runtime_identity_sha256": "3" * 64,
            "source_audit_sha256": "4" * 64,
            "source_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        }
        value.update({
            "authority": authority,
            "complete": True,
            "protocol": release,
            "schema_version": "anachron-v4-candidate-answer-free-projection-v1",
            "v3_included_count": 0,
        })
        self._closure(value, authority)
        return value
    def _projection(self, sign: str) -> dict:
        value = V4CandidateProjectionTests()._projection(ROOT)
        for pair in value["paired_tclr_reductions"]:
            if sign == "positive":
                pair["unrestricted_numerator"] = 1
                pair["enforced_numerator"] = 0
                pair["sign_class"] = "positive"
            elif sign == "negative":
                pair["unrestricted_numerator"] = 0
                pair["enforced_numerator"] = 1
                pair["sign_class"] = "negative"
            else:
                pair["unrestricted_numerator"] = 0
                pair["enforced_numerator"] = 0
                pair["sign_class"] = "zero"
        for cell in value["cells"]:
            numerator = sum(
                pair[f"{cell['mode']}_numerator"]
                for pair in value["paired_tclr_reductions"]
                if cell["model"] == "pooled" or pair["model"] == cell["model"]
            )
            cell["numerator"] = numerator
            cell["rate_fixed_decimal"] = f"{numerator / cell['denominator']:.6f}"
        return {"projection": value}

    def test_positive_zero_and_negative_forms_render_only_from_projection(self) -> None:
        _, template = builder._template(ROOT)
        for sign, phrase in (
            ("positive", "paired TCLR difference was positive"),
            ("zero", "paired TCLR difference was zero"),
            ("negative", "paired TCLR difference was negative"),
        ):
            with self.subTest(sign=sign):
                tex, figure = builder.build_tex(template, self._projection(sign))
                self.assertIn(phrase, tex)
                self.assertIn("0 v3 inclusions", tex)
                self.assertIn("restatement-returned", tex)
                self.assertIn(r"\begin{picture}", figure)
                for forbidden in ("significance", "population", "answer quality", "live web", "ranking"):
                    self.assertNotIn(forbidden, tex.lower())

    def test_archive_rejects_extra_and_unsafe_members(self) -> None:
        contract, _ = builder._template(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "source.zip"
            with zipfile.ZipFile(archive, "w") as target:
                for name in builder.ARCHIVE_FILES:
                    target.writestr(name, name)
                target.writestr("extra.txt", "extra")
            with self.assertRaisesRegex(builder.CandidatePaperError, "allowlist"):
                builder._extract(archive, root / "out", contract["resource_policy"])
            unsafe = root / "unsafe.zip"
            with zipfile.ZipFile(unsafe, "w") as target:
                for name in builder.ARCHIVE_FILES[:-1]:
                    target.writestr(name, name)
                target.writestr("../references.bib", "unsafe")
            with self.assertRaisesRegex(builder.CandidatePaperError, "allowlist|unsafe"):
                builder._extract(unsafe, root / "unsafe-out", contract["resource_policy"])
            nonregular = root / "nonregular.zip"
            with zipfile.ZipFile(nonregular, "w") as target:
                for name in builder.ARCHIVE_FILES:
                    info = zipfile.ZipInfo(name)
                    info.external_attr = (0o120777) << 16
                    target.writestr(info, "unsafe")
            with self.assertRaisesRegex(builder.CandidatePaperError, "unsafe"):
                builder._extract(nonregular, root / "nonregular-out", contract["resource_policy"])

    def test_source_archive_cap_refuses_before_zipfile_open(self) -> None:
        contract, _ = builder._template(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "oversized-sparse.zip"
            with archive.open("xb") as stream:
                stream.seek(contract["resource_policy"]["source_archive_max_bytes"])
                stream.write(b"x")
            with (
                patch.object(builder.zipfile, "ZipFile") as opener,
                self.assertRaisesRegex(builder.CandidatePaperError, "byte cap"),
            ):
                builder._extract(archive, Path(temporary) / "out", contract["resource_policy"])
            opener.assert_not_called()

    def test_pooled_direction_uses_sealed_projection_cells(self) -> None:
        for sign, expected in (
            ("positive", "positive"),
            ("zero", "zero"),
            ("negative", "negative"),
        ):
            with self.subTest(sign=sign):
                candidate = self._projection(sign)
                self.assertEqual(pooled_tclr_direction(candidate), expected)

    def test_wrong_tectonic_refuses_before_compilation(self) -> None:
        contract, _ = builder._template(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            wrong = Path(temporary) / "wrong.exe"
            wrong.write_bytes(b"wrong")
            with self.assertRaisesRegex(builder.CandidatePaperError, "SHA-256"):
                builder.verify_tectonic(wrong, contract)

    @unittest.skipUnless(PAPER_DEPENDENCIES_AVAILABLE, PAPER_DEPENDENCIES_REASON)
    def test_archive_and_pdf_compile_are_deterministic_when_pinned_tectonic_exists(self) -> None:
        if TECTONIC is None or not TECTONIC.is_file():
            self.skipTest("pinned Tectonic is unavailable")
        contract, template = builder._template(ROOT)
        candidate = self._projection("positive")
        tex, figure = builder.build_tex(template, candidate)
        builder.verify_tectonic(TECTONIC, contract)
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            hashes = []
            for temporary in (Path(first), Path(second)):
                source = temporary / "source"
                builder._write(source / "main.tex", tex.encode("utf-8"))
                builder._write(source / "figures/primary_tclr.tex", figure.encode("utf-8"))
                builder._write(source / "references.bib", (ROOT / "paper/v4_measurement/candidate_references.bib").read_bytes())
                builder._write(source / "README.md", b"candidate\n")
                archive = temporary / "source.zip"
                builder._archive(source, archive, contract["resource_policy"])
                extracted = temporary / "extract"
                builder._extract(archive, extracted, contract["resource_policy"])
                pdf = builder._compile(TECTONIC, source, temporary / "compile", contract["resource_policy"])
                extracted_pdf = builder._compile(TECTONIC, extracted, temporary / "extract-compile", contract["resource_policy"])
                self.assertEqual(pdf.read_bytes(), extracted_pdf.read_bytes())
                qa = builder._pdf_qa(pdf, temporary / "renders", template["title"], contract["resource_policy"])
                self.assertGreaterEqual(qa["page_count"], 1)
                hashes.append((builder.sha256_path(archive), builder.sha256_path(pdf)))
            self.assertEqual(hashes[0], hashes[1])

    @unittest.skipUnless(PAPER_DEPENDENCIES_AVAILABLE, PAPER_DEPENDENCIES_REASON)
    def test_public_build_candidate_revalidates_complete_disposable_release(self) -> None:
        if TECTONIC is None or not TECTONIC.is_file():
            self.skipTest("pinned Tectonic is unavailable")
        temporary, root, origin, expected_v3 = self._repository()
        with temporary:
            manifest = root.parent / "M.json"
            source = build_v4_source_manifest.build(root, manifest, expected_origin=str(origin), expected_v3=expected_v3)
            projection = root.parent / "projection.json"
            envelope = self._envelope(root, manifest, source["release"])
            projection.write_bytes(builder.canonical_json_bytes(envelope))
            output = root.parent / "candidate"
            result = builder.build_candidate(root, manifest, projection, output, TECTONIC, expected_origin=str(origin), expected_v3=expected_v3)
            self.assertEqual(result["protocol_tag"], "v4-measurement-protocol-v1")
            self.assertEqual({item.name for item in output.iterdir()}, set(builder.CANDIDATE_COMPLETION))
            self.assertEqual((output / "projection.json").read_bytes(), projection.read_bytes())
            receipt = json.loads((output / "candidate_receipt.json").read_text(encoding="utf-8"))
            render_manifest = json.loads(
                (output / "qa_render_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                receipt["arxiv_metadata_sha256"],
                builder.sha256_path(output / "arxiv_metadata.json"),
            )
            self.assertEqual(
                receipt["qa_render_manifest_sha256"],
                builder.sha256_path(output / "qa_render_manifest.json"),
            )
            self.assertEqual(render_manifest["page_count"], len(render_manifest["renders"]))
            self.assertEqual(
                [row["path"] for row in render_manifest["renders"]],
                [f"page-{index}.png" for index in range(1, render_manifest["page_count"] + 1)],
            )
            with zipfile.ZipFile(output / "source.zip") as archive:
                self.assertEqual(tuple(archive.namelist()), builder.ARCHIVE_FILES)
            self.assertFalse(list(output.parent.glob(f".{output.name}.stage-*")))

    @unittest.skipUnless(PAPER_DEPENDENCIES_AVAILABLE, PAPER_DEPENDENCIES_REASON)
    def test_public_build_rejects_manifest_authority_tectonic_and_late_collision(self) -> None:
        if TECTONIC is None or not TECTONIC.is_file():
            self.skipTest("pinned Tectonic is unavailable")
        temporary, root, origin, expected_v3 = self._repository()
        with temporary:
            manifest = root.parent / "M.json"
            source = build_v4_source_manifest.build(root, manifest, expected_origin=str(origin), expected_v3=expected_v3)
            envelope = self._envelope(root, manifest, source["release"])
            projection = root.parent / "projection.json"
            projection.write_bytes(builder.canonical_json_bytes(envelope))
            for label, pattern in (
                ("wrong-M", "source manifest"),
                ("authority", "authority"),
            ):
                with self.subTest(label=label):
                    if label == "wrong-M":
                        manifest.write_bytes(b"{}\n")
                    else:
                        envelope["authority"]["authority_contract_sha256"] = "0" * 64
                    if label == "authority":
                        projection.write_bytes(builder.canonical_json_bytes(envelope))
                    with self.assertRaisesRegex(builder.CandidatePaperError, pattern):
                        builder.build_candidate(root, manifest, projection, root.parent / label, TECTONIC, expected_origin=str(origin), expected_v3=expected_v3)
                    self.assertFalse((root.parent / label).exists())
                    if label == "wrong-M":
                        manifest.unlink()
                        build_v4_source_manifest.build(root, manifest, expected_origin=str(origin), expected_v3=expected_v3)
                        envelope = self._envelope(root, manifest, source["release"])
                        projection.write_bytes(builder.canonical_json_bytes(envelope))
            envelope = self._envelope(root, manifest, source["release"])
            projection.write_bytes(builder.canonical_json_bytes(envelope))
            wrong = root.parent / "wrong.exe"
            wrong.write_bytes(b"wrong")
            with self.assertRaisesRegex(builder.CandidatePaperError, "SHA-256"):
                builder.build_candidate(root, manifest, projection, root.parent / "wrong-tectonic", wrong, expected_origin=str(origin), expected_v3=expected_v3)
            self.assertFalse((root.parent / "wrong-tectonic").exists())
            output = root.parent / "late-collision"
            original = builder._projection_input
            def collide(*arguments, **keywords):
                output.mkdir()
                return original(*arguments, **keywords)
            with patch.object(builder, "_projection_input", side_effect=collide), self.assertRaises(FileExistsError):
                builder.build_candidate(root, manifest, projection, output, TECTONIC, expected_origin=str(origin), expected_v3=expected_v3)
            self.assertFalse(list(output.parent.glob(f".{output.name}.stage-*")))

    def test_projection_admission_caps_authority_and_contract_pins(self) -> None:
        contract, _ = builder._template(ROOT)
        self.assertEqual(contract["tectonic"], builder._template(ROOT)[0]["tectonic"])
        self.assertNotIn("99ffcf", (ROOT / "tools/build_v4_measurement_candidate_paper.py").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "projection.json"
            path.write_bytes(b"x" * (contract["resource_policy"]["candidate_projection_max_bytes"] + 1))
            with self.assertRaisesRegex(builder.CandidatePaperError, "byte cap"):
                builder._read_limited(path, contract["resource_policy"]["candidate_projection_max_bytes"], "candidate projection")
        native = V4CandidateProjectionTests()._projection(ROOT)
        native["diagnostics"][0]["model"] = "m" * (contract["resource_policy"]["string_max_bytes"] + 1)
        from anachron import v4_candidate_common
        with self.assertRaises(v4_candidate_common.CandidateProjectionError):
            v4_candidate_common._projection(native, ROOT)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repository"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
            candidate_path = root / "paper/v4_measurement/candidate_contract.json"
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            candidate["tectonic"]["version"] = "forged"
            candidate_path.write_bytes(builder.canonical_json_bytes(candidate))
            with self.assertRaisesRegex(V4ContractError, "Tectonic identity"):
                builder.build_candidate(root, Path(temporary) / "M.json", Path(temporary) / "P.json", Path(temporary) / "out", TECTONIC)

    def test_public_manifest_and_executable_caps_precede_full_reads(self) -> None:
        contract, _ = builder._template(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "M.json"
            projection = root / "projection.json"
            projection.write_bytes(b"{}\n")
            executable = root / "tectonic.exe"
            for path, maximum in (
                (manifest, contract["resource_policy"]["source_manifest_max_bytes"]),
                (executable, contract["resource_policy"]["tectonic_executable_max_bytes"]),
            ):
                with path.open("xb") as stream:
                    stream.seek(maximum)
                    stream.write(b"x")
                with self.subTest(path=path), self.assertRaisesRegex(builder.CandidatePaperError, "byte cap"):
                    if path == manifest:
                        builder._projection_input(ROOT, manifest, projection, contract)
                    else:
                        builder.verify_tectonic(executable, contract)

        reads: list[int] = []
        stream = Mock()
        stream.read.side_effect = lambda size: reads.append(size) or (b"x" if len(reads) == 1 else b"")
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        with patch.object(Path, "open", return_value=stream):
            builder.sha256_path(Path("streamed"))
        self.assertEqual(reads, [65536, 65536])

    def test_posix_publish_uses_rename_noreplace_and_refuses_collision(self) -> None:
        library = Mock()
        renameat2 = Mock(return_value=-1)
        library.renameat2 = renameat2
        with patch.object(builder.os, "name", "posix"), patch.object(builder.ctypes, "CDLL", return_value=library), patch.object(builder.ctypes, "get_errno", return_value=builder.errno.EEXIST), self.assertRaises(FileExistsError):
            builder._publish_no_replace(Path("staging"), Path("output"))
        self.assertEqual(renameat2.call_args.args[-1], 1)

    def test_bounded_compiler_timeout_nonzero_and_log_output_caps(self) -> None:
        contract, _ = builder._template(ROOT)
        policy = dict(contract["resource_policy"])
        policy["tectonic_timeout_seconds"] = 1
        with tempfile.TemporaryDirectory() as temporary:
            cwd = Path(temporary)
            for code, pattern in (
                ("import time; time.sleep(2)", "timed out"),
                ("raise SystemExit(3)", "failed"),
                (f"print('x' * {policy['tectonic_log_max_bytes'] + 1})", "log exceeds"),
            ):
                with self.subTest(pattern=pattern), self.assertRaisesRegex(builder.CandidatePaperError, pattern):
                    builder._bounded_command([sys.executable, "-c", code], cwd, policy, "Tectonic compilation")
            source = cwd / "source"
            source.mkdir()
            output = cwd / "output"
            def oversized_output(*_arguments, **_keywords):
                (output / "main.pdf").write_bytes(b"x" * (policy["pdf_max_bytes"] + 1))
                return ""
            with patch.object(builder, "_bounded_command", side_effect=oversized_output), self.assertRaisesRegex(builder.CandidatePaperError, "candidate PDF"):
                builder._compile(Path("fake-tectonic"), source, output, policy)

    def test_static_builder_boundary_excludes_raw_and_v3_imports(self) -> None:
        source = (ROOT / "tools/build_v4_measurement_candidate_paper.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("v3_candidate", source)
        self.assertNotIn("ollama", source)
        for forbidden in ("terminal_answer", "go_prose", "review_set", "author_approval"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
