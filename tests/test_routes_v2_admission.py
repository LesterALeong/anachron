import copy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from anachron.routes.v2.admission import (
    AdmissionError,
    admit_clean_checkout,
    build_code_closure,
    validate_loaded_code_closure,
)

ROOT = Path(__file__).parents[1]


def git(directory, *args):
    return subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True, text=True).stdout.strip()


class TestRoutesV2Admission(unittest.TestCase):
    @staticmethod
    def _copy_governed_fixture(root):
        root.mkdir(exist_ok=True)
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

    def _fixture(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name) / "fixture"
        remote = Path(temporary.name) / "remote.git"
        git(Path(temporary.name), "init", "--bare", str(remote))
        git(Path(temporary.name), "init", "-b", "main", str(root))
        git(root, "config", "user.email", "fixture@example.test")
        git(root, "config", "user.name", "fixture")
        self._copy_governed_fixture(root)
        git(root, "add", ".")
        git(root, "commit", "-m", "fixture")
        git(root, "remote", "add", "origin", str(remote))
        git(root, "push", "-u", "origin", "main")
        closure = build_code_closure(root)
        freeze = {"schema_version": "routes-v2-freeze-receipt", "study_phase": "development", "commit": git(root, "rev-parse", "HEAD"), "tree": git(root, "rev-parse", "HEAD^{tree}"), "branch": "main", "remote": str(remote), "closure_sha256": closure["closure_sha256"]}
        return temporary, root, remote, closure, freeze

    def test_clean_exact_pushed_fixture_passes(self):
        temporary, root, _remote, closure, freeze = self._fixture()
        self.addCleanup(temporary.cleanup)
        admit_clean_checkout(root, freeze, closure)
        with self.assertRaises(AdmissionError):
            validate_loaded_code_closure(root, closure)

    def test_fresh_subprocess_imports_actual_committed_closure_not_pass_surrogate(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root, remote = Path(temporary.name) / "candidate", Path(temporary.name) / "actual.git"
        git(Path(temporary.name), "init", "--bare", str(remote))
        git(Path(temporary.name), "init", "-b", "main", str(root))
        git(root, "config", "user.email", "fixture@example.test")
        git(root, "config", "user.name", "fixture")
        self._copy_governed_fixture(root)
        git(root, "add", ".")
        git(root, "commit", "-m", "actual")
        git(root, "remote", "add", "origin", str(remote))
        git(root, "push", "-u", "origin", "main")
        checkout = Path(temporary.name) / "checkout"
        git(Path(temporary.name), "init", "-b", "main", str(checkout))
        git(checkout, "remote", "add", "origin", str(remote))
        git(checkout, "config", "core.autocrlf", "true")
        git(checkout, "fetch", "origin", "main")
        git(checkout, "checkout", "-B", "main", "origin/main")
        self.assertEqual(git(checkout, "config", "--get", "core.autocrlf"), "true")
        closure = build_code_closure(root)
        for relative in sorted(set(closure["files"]) | set(closure["bound_text_files"])):
            with self.subTest(relative=relative):
                expected = subprocess.run(
                    ["git", "-C", str(root), "show", f"HEAD:{relative}"],
                    check=True,
                    capture_output=True,
                ).stdout
                self.assertEqual((checkout / relative).read_bytes(), expected)
        probe = (
            "import subprocess\n"
            "from pathlib import Path\n"
            "from anachron.routes.v2.admission import admit_clean_checkout, build_code_closure, validate_loaded_code_closure\n"
            "root = Path.cwd().resolve()\n"
            "closure = build_code_closure(root)\n"
            "git_value = lambda *args: subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()\n"
            "freeze = {'schema_version': 'routes-v2-freeze-receipt', 'study_phase': 'development', 'commit': git_value('rev-parse', 'HEAD'), 'tree': git_value('rev-parse', 'HEAD^{tree}'), 'branch': 'main', 'remote': git_value('config', '--get', 'remote.origin.url'), 'closure_sha256': closure['closure_sha256']}\n"
            "admit_clean_checkout(root, freeze, closure)\n"
            "validate_loaded_code_closure(root, closure)\n"
        )
        subprocess.run([sys.executable, "-B", "-c", probe], cwd=checkout, check=True)

    def test_missing_tampered_attributes_and_crlf_byte_drift_fail(self):
        temporary, root, _remote, closure, freeze = self._fixture()
        self.addCleanup(temporary.cleanup)
        attributes = root / ".gitattributes"
        attributes.unlink()
        with self.assertRaises(AdmissionError):
            build_code_closure(root)
        git(root, "checkout", "--", ".gitattributes")
        attributes.write_text("*.py text\n", encoding="utf-8", newline="\n")
        with self.assertRaises(AdmissionError):
            build_code_closure(root)
        git(root, "checkout", "--", ".gitattributes")
        contract = root / "research/routes-v2/contract.json"
        git(root, "update-index", "--assume-unchanged", "research/routes-v2/contract.json")
        contract.write_bytes(contract.read_bytes().replace(b"\n", b"\r\n"))
        with self.assertRaises(AdmissionError):
            admit_clean_checkout(root, freeze, closure)
        git(root, "update-index", "--no-assume-unchanged", "research/routes-v2/contract.json")
        git(root, "checkout", "--", "research/routes-v2/contract.json")
        target = root / "anachron/routes/v2/runtime.py"
        git(root, "update-index", "--assume-unchanged", "anachron/routes/v2/runtime.py")
        target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
        with self.assertRaises(AdmissionError):
            admit_clean_checkout(root, freeze, closure)

    def test_dirty_untracked_and_actual_workspace_fail_before_provenance(self):
        temporary, root, _remote, closure, freeze = self._fixture()
        self.addCleanup(temporary.cleanup)
        (root / "untracked.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(AdmissionError):
            admit_clean_checkout(root, freeze, closure)
        fabricated = {"schema_version": "routes-v2-freeze-receipt", "study_phase": "development", "commit": "x", "tree": "x", "branch": "main", "remote": "x", "closure_sha256": "x"}
        with self.assertRaises(AdmissionError):
            admit_clean_checkout(ROOT, fabricated, {"schema_version": "routes-v2-code-closure", "files": {}, "closure_sha256": "x"})

    def test_unpushed_remote_ahead_and_origin_mismatch_fail(self):
        temporary, root, remote, closure, freeze = self._fixture()
        self.addCleanup(temporary.cleanup)
        (root / "extra.txt").write_text("local", encoding="utf-8")
        git(root, "add", "extra.txt")
        git(root, "commit", "-m", "unpushed")
        unpushed = dict(freeze, commit=git(root, "rev-parse", "HEAD"), tree=git(root, "rev-parse", "HEAD^{tree}"))
        with self.assertRaises(AdmissionError):
            admit_clean_checkout(root, unpushed, build_code_closure(root))
        git(root, "reset", "--hard", freeze["commit"])
        second = Path(temporary.name) / "second"
        git(Path(temporary.name), "clone", "-b", "main", str(remote), str(second))
        git(second, "config", "user.email", "fixture@example.test")
        git(second, "config", "user.name", "fixture")
        (second / "remote.txt").write_text("ahead", encoding="utf-8")
        git(second, "add", "remote.txt")
        git(second, "commit", "-m", "remote ahead")
        git(second, "push", "origin", "main")
        with self.assertRaises(AdmissionError):
            admit_clean_checkout(root, freeze, closure)
        git(root, "remote", "set-url", "origin", str(Path(temporary.name) / "wrong.git"))
        with self.assertRaises(AdmissionError):
            admit_clean_checkout(root, freeze, closure)

    def test_changed_source_omitted_import_dynamic_import_and_local_read_fail(self):
        temporary, root, _remote, closure, freeze = self._fixture()
        self.addCleanup(temporary.cleanup)
        target = root / "anachron/routes/v2/runtime.py"
        git(root, "update-index", "--assume-unchanged", "anachron/routes/v2/runtime.py")
        target.write_text("changed\n", encoding="utf-8", newline="\n")
        with self.assertRaises(AdmissionError):
            admit_clean_checkout(root, freeze, closure)
        git(root, "update-index", "--no-assume-unchanged", "anachron/routes/v2/runtime.py")
        git(root, "checkout", "--", "anachron/routes/v2/runtime.py")
        omitted = copy.deepcopy(closure)
        omitted["files"].pop("anachron/routes/v2/runtime.py")
        with self.assertRaises(AdmissionError):
            admit_clean_checkout(root, freeze, omitted)
        dynamic = root / "tools" / "dynamic.py"
        dynamic.write_text("__import__('x')\n", encoding="utf-8", newline="\n")
        with self.assertRaises(AdmissionError):
            build_code_closure(root, roots=("tools/dynamic.py",))
        dynamic.write_text("open('hidden.py').read()\n", encoding="utf-8", newline="\n")
        with self.assertRaises(AdmissionError):
            build_code_closure(root, roots=("tools/dynamic.py",))


if __name__ == "__main__":
    unittest.main()
