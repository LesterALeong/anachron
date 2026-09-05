from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from anachron.v4_comparison import derive
from anachron.v4_contract import V4_GOVERNED_SOURCE_PATHS
from tools import build_v4_source_manifest


class V4SourceManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository_root = Path(__file__).resolve().parents[1]

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
        shutil.copytree(
            self.repository_root,
            root,
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
        )
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
        self._git(root, "tag", "-a", "v4-measurement-protocol-v2", "-m", "v4")
        self._git(root, "push", "origin", "protocol/v4-recovery-v1", "refs/tags/v4-measurement-protocol-v2")
        self._git(root, "checkout", "--detach", "v4-measurement-protocol-v2")
        return temporary, root, origin, {"commit": v3_commit, "tag": "v3-test", "tag_object": v3_tag_object}

    def test_build_validate_and_reject_release_and_blob_drift(self) -> None:
        temporary, root, origin, expected_v3 = self._repository()
        with temporary:
            manifest = root.parent / "source-manifest.json"
            build_v4_source_manifest.build(
                root,
                manifest,
                expected_origin=str(origin),
                expected_v3=expected_v3,
            )
            self.assertEqual(
                build_v4_source_manifest.validate(
                    root,
                    manifest,
                    expected_origin=str(origin),
                    expected_v3=expected_v3,
                )["governed_paths"],
                list(V4_GOVERNED_SOURCE_PATHS),
            )
            comparison = derive(root, v3_tag="v3-test", v4_tag="v4-measurement-protocol-v2")
            self.assertTrue(all(not values for values in comparison["intersections"].values()))

            target = root / V4_GOVERNED_SOURCE_PATHS[0]
            target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaises(build_v4_source_manifest.V4SourceManifestError):
                build_v4_source_manifest.validate(root, manifest, expected_origin=str(origin), expected_v3=expected_v3)
            self._git(root, "reset", "--hard", "HEAD")

            self._git(root, "remote", "set-url", "origin", str(root.parent / "wrong.git"))
            with self.assertRaises(build_v4_source_manifest.V4SourceManifestError):
                build_v4_source_manifest.validate(root, manifest, expected_origin=str(origin), expected_v3=expected_v3)
            self._git(root, "remote", "set-url", "origin", str(origin))

            self._git(root, "branch", "-f", "master", "HEAD")
            with self.assertRaises(build_v4_source_manifest.V4SourceManifestError):
                build_v4_source_manifest.validate(root, manifest, expected_origin=str(origin), expected_v3=expected_v3)
            self._git(root, "branch", "-f", "master", expected_v3["commit"])

            self._git(root, "branch", "-f", "protocol/v4-recovery-v1", expected_v3["commit"])
            with self.assertRaises(build_v4_source_manifest.V4SourceManifestError):
                build_v4_source_manifest.validate(root, manifest, expected_origin=str(origin), expected_v3=expected_v3)
            self._git(root, "branch", "-f", "protocol/v4-recovery-v1", "HEAD")

            v4_tag_object = self._git(root, "rev-parse", "refs/tags/v4-measurement-protocol-v2^{tag}")
            self._git(root, "tag", "-f", "-a", "v4-measurement-protocol-v2", "-m", "drift", "HEAD")
            with self.assertRaises(build_v4_source_manifest.V4SourceManifestError):
                build_v4_source_manifest.validate(root, manifest, expected_origin=str(origin), expected_v3=expected_v3)
            self._git(root, "update-ref", "refs/tags/v4-measurement-protocol-v2", v4_tag_object)

            self._git(
                origin,
                "update-ref",
                "refs/tags/v3-test",
                v4_tag_object,
            )
            with self.assertRaises(build_v4_source_manifest.V4SourceManifestError):
                build_v4_source_manifest.validate(
                    root,
                    manifest,
                    expected_origin=str(origin),
                    expected_v3=expected_v3,
                )
            self._git(
                origin,
                "update-ref",
                "refs/tags/v3-test",
                expected_v3["tag_object"],
            )

            self._git(root, "tag", "-f", "-a", "v3-test", "-m", "drift", "HEAD")
            with self.assertRaises(build_v4_source_manifest.V4SourceManifestError):
                build_v4_source_manifest.validate(root, manifest, expected_origin=str(origin), expected_v3=expected_v3)

    def test_rejects_manifest_path_and_blob_substitution(self) -> None:
        temporary, root, origin, expected_v3 = self._repository()
        with temporary:
            manifest = root.parent / "source-manifest.json"
            build_v4_source_manifest.build(root, manifest, expected_origin=str(origin), expected_v3=expected_v3)
            original = json.loads(manifest.read_text(encoding="utf-8"))
            for mutation in ("missing", "extra", "blob"):
                with self.subTest(mutation=mutation):
                    value = json.loads(json.dumps(original))
                    if mutation == "missing":
                        value["governed_paths"].pop()
                    elif mutation == "extra":
                        value["governed_paths"].append("tools/forged.py")
                    else:
                        value["governed_files"][0]["tag_blob_oid"] = "0" * 40
                    manifest.write_bytes(build_v4_source_manifest._canonical_json_bytes(value))
                    with self.assertRaises(build_v4_source_manifest.V4SourceManifestError):
                        build_v4_source_manifest.validate(root, manifest, expected_origin=str(origin), expected_v3=expected_v3)
            manifest.write_bytes(build_v4_source_manifest._canonical_json_bytes(original))

    def test_comparison_detects_hostile_entity_identifier_overlap(self) -> None:
        temporary, root, _, expected_v3 = self._repository()
        with temporary:
            self._git(root, "checkout", "-b", "entity-overlap")
            card_path = root / "research/v4_measurement/cases/fin-aster-2020-06-future.json"
            card = json.loads(card_path.read_text(encoding="utf-8"))
            card["entity_identifier"] = "ACME"
            card_path.write_bytes(build_v4_source_manifest._canonical_json_bytes(card))
            self._git(root, "add", str(card_path))
            self._git(root, "commit", "-m", "hostile entity identifier")
            self._git(
                root,
                "tag",
                "-f",
                "-a",
                "v4-measurement-protocol-v2",
                "-m",
                "hostile v4",
            )
            comparison = derive(
                root,
                v3_tag=expected_v3["tag"],
                v4_tag="v4-measurement-protocol-v2",
            )
            self.assertEqual(comparison["intersections"]["entity_identifiers"], ["ACME"])
            self.assertFalse(
                comparison["no_overlap_assertions"]["entity_identifiers_empty"]
            )

    def test_requires_external_absent_destination_and_detached_tag(self) -> None:
        temporary, root, origin, expected_v3 = self._repository()
        with temporary:
            with self.assertRaises(build_v4_source_manifest.V4SourceManifestError):
                build_v4_source_manifest.build(root, root / "source-manifest.json", expected_origin=str(origin), expected_v3=expected_v3)
            self._git(root, "checkout", "protocol/v4-recovery-v1")
            with self.assertRaises(build_v4_source_manifest.V4SourceManifestError):
                build_v4_source_manifest.build(root, root.parent / "source-manifest.json", expected_origin=str(origin), expected_v3=expected_v3)


if __name__ == "__main__":
    unittest.main()
