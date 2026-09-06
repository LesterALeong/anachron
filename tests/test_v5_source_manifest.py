from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from shutil import copyfile

from anachron.v5_contract import V5_GOVERNED_SOURCE_PATHS, V5_PROTOCOL_TAG
from tools.build_v5_source_manifest import (
    V5SourceManifestError,
    build,
    derive,
    validate,
)


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class V5SourceManifestTests(unittest.TestCase):
    def test_derives_real_annotated_tag_objects_and_blobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            remote = workspace / "remote.git"
            root = workspace / "source"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", "-b", "protocol/v5-successor", str(root)], check=True, capture_output=True)
            _git(root, "config", "user.email", "test@example.invalid")
            _git(root, "config", "user.name", "V5 Test")
            _git(root, "config", "core.autocrlf", "false")
            _git(root, "remote", "add", "origin", str(remote))
            (root / "governed.txt").write_bytes(b"frozen\n")
            _git(root, "add", "governed.txt")
            _git(root, "commit", "-m", "freeze")
            _git(root, "tag", "-a", V5_PROTOCOL_TAG, "-m", "freeze")
            _git(root, "push", "origin", "protocol/v5-successor", "--tags")
            commit = _git(root, "rev-parse", "HEAD")
            with self.assertRaises(V5SourceManifestError):
                derive(root, expected_origin=str(remote), governed_paths=("governed.txt",))
            detached = workspace / "detached"
            _git(root, "worktree", "add", "--detach", str(detached), V5_PROTOCOL_TAG)
            manifest = derive(
                detached,
                expected_origin=str(remote),
                expected_release={
                    "branch_ref": commit,
                    "commit": commit,
                    "tag_object": _git(detached, "rev-parse", f"refs/tags/{V5_PROTOCOL_TAG}^{{tag}}"),
                    "tag_peeled": commit,
                },
                governed_paths=("governed.txt",),
            )
            self.assertEqual(manifest["release"]["tag_peeled"], commit)
            self.assertEqual(manifest["governed_files"][0]["tag_blob_oid"], _git(detached, "rev-parse", "HEAD:governed.txt"))

    def test_builds_and_validates_default_closure_from_clean_detached_local_bare_git(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            remote = workspace / "remote.git"
            source = workspace / "source"
            detached = workspace / "detached"
            output = workspace / "source-manifest.json"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(
                ["git", "init", "-b", "protocol/v5-successor", str(source)],
                check=True,
                capture_output=True,
            )
            _git(source, "config", "user.email", "test@example.invalid")
            _git(source, "config", "user.name", "V5 Test")
            _git(source, "config", "core.autocrlf", "true")
            _git(source, "remote", "add", "origin", str(remote))
            for relative in V5_GOVERNED_SOURCE_PATHS:
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                copyfile(repository / relative, target)
            _git(source, "add", ".")
            _git(source, "commit", "-m", "freeze default closure")
            _git(source, "tag", "-a", V5_PROTOCOL_TAG, "-m", "freeze default closure")
            _git(source, "push", "origin", "protocol/v5-successor", "--tags")
            _git(source, "worktree", "add", "--detach", str(detached), V5_PROTOCOL_TAG)

            self.assertEqual(_git(detached, "status", "--porcelain", "--untracked-files=all"), "")
            self.assertEqual(_git(detached, "branch", "--show-current"), "")
            self.assertEqual(
                _git(detached, "check-attr", "eol", "--", "tools/.gitattributes"),
                "tools/.gitattributes: eol: lf",
            )
            self.assertEqual(
                _git(detached, "check-attr", "eol", "--", "tools/run_v5_conditional_campaign.ps1"),
                "tools/run_v5_conditional_campaign.ps1: eol: lf",
            )
            manifest = build(detached, output, expected_origin=str(remote))

            self.assertEqual(validate(detached, output, expected_origin=str(remote)), manifest)
            self.assertEqual(manifest["governed_paths"], list(V5_GOVERNED_SOURCE_PATHS))
            self.assertEqual(len(manifest["governed_files"]), len(V5_GOVERNED_SOURCE_PATHS))
            self.assertEqual(manifest["release"]["tag"], V5_PROTOCOL_TAG)
            self.assertEqual(manifest["release"]["commit"], _git(detached, "rev-parse", "HEAD"))


if __name__ == "__main__":
    unittest.main()
