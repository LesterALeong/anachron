from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.build_v5_source_manifest import derive


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
            _git(root, "tag", "-a", "v5-measurement-protocol-v1", "-m", "freeze")
            _git(root, "push", "origin", "protocol/v5-successor", "--tags")
            commit = _git(root, "rev-parse", "HEAD")
            manifest = derive(
                root,
                expected_origin=str(remote),
                expected_release={
                    "branch_ref": commit,
                    "commit": commit,
                    "tag_object": _git(root, "rev-parse", "refs/tags/v5-measurement-protocol-v1^{tag}"),
                    "tag_peeled": commit,
                },
                governed_paths=("governed.txt",),
            )
            self.assertEqual(manifest["release"]["tag_peeled"], commit)
            self.assertEqual(manifest["governed_files"][0]["tag_blob_oid"], _git(root, "rev-parse", "HEAD:governed.txt"))


if __name__ == "__main__":
    unittest.main()
