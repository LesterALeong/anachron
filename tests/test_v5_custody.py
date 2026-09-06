"""Direct adversarial tests for the shared v5 custody boundary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anachron import v5_custody
from anachron.v5_custody import (
    ByteBudget,
    V5CustodyError,
    capture_regular,
    physical_inventory,
    publish_staging_root,
    scandir_exact,
    write_create_only,
)


class V5CustodyTests(unittest.TestCase):
    def test_capture_rejects_cap_plus_one_before_buffering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "authority.json"
            path.write_bytes(b"x" * 17)
            with self.assertRaisesRegex(V5CustodyError, "byte cap"):
                capture_regular(path, "authority", 16)

    def test_physical_inventory_enforces_aggregate_during_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one.json").write_bytes(b"one")
            (root / "two.json").write_bytes(b"two")
            inventory = physical_inventory(
                root,
                "fixture",
                maximum_entries=2,
                maximum_depth=1,
                byte_limit=6,
                member_cap=lambda _path: 3,
            )
            self.assertEqual(sum(member.size_bytes for member in inventory.files), 6)
            with self.assertRaisesRegex(V5CustodyError, "custody budget"):
                physical_inventory(
                    root,
                    "fixture",
                    maximum_entries=2,
                    maximum_depth=1,
                    byte_limit=5,
                    member_cap=lambda _path: 3,
                )

    def test_scandir_rejects_max_plus_one_before_topology_collection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one").write_bytes(b"")
            (root / "two").write_bytes(b"")
            with self.assertRaisesRegex(V5CustodyError, "entry cap"):
                scandir_exact(root, (), 1, "fixture")

    def test_create_only_write_keeps_competing_bytes_and_refunds_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "receipt.json"
            budget = ByteBudget(16, 1)
            move = v5_custody._atomic_no_replace_move

            def publish_competitor(source: Path, destination: Path) -> None:
                Path(destination).write_bytes(b"competing\n")
                move(source, destination)

            with patch("anachron.v5_custody._atomic_no_replace_move", side_effect=publish_competitor), self.assertRaisesRegex(
                V5CustodyError,
                "must be absent",
            ):
                write_create_only(target, b"ours\n", "receipt", budget)
            self.assertEqual(target.read_bytes(), b"competing\n")
            self.assertEqual((budget.bytes_used, budget.members_used), (0, 0))
            self.assertEqual(list(Path(temporary).glob(".receipt.json.*")), [])

    def test_create_only_write_refuses_duplicate_without_spending_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "receipt.json"
            budget = ByteBudget(16, 2)
            write_create_only(target, b"{}\n", "receipt", budget)
            with self.assertRaisesRegex(V5CustodyError, "must be absent"):
                write_create_only(target, b'{"changed":true}\n', "receipt", budget)
            self.assertEqual(target.read_bytes(), b"{}\n")
            self.assertEqual((budget.bytes_used, budget.members_used), (3, 1))

    def test_create_only_write_retries_a_prepublication_windows_sharing_violation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "receipt.json"
            rename = v5_custody.os.rename
            calls = 0

            def rename_after_one_failure(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 1:
                    error = PermissionError(13, "sharing violation")
                    error.winerror = 32
                    raise error
                rename(source, destination)

            with patch("anachron.v5_custody._WINDOWS", True), patch(
                "anachron.v5_custody.os.rename",
                side_effect=rename_after_one_failure,
            ):
                write_create_only(target, b"{}\n", "receipt", ByteBudget(16, 1))
            self.assertEqual(calls, 2)
            self.assertEqual(target.read_bytes(), b"{}\n")
            self.assertEqual(list(Path(temporary).glob(".receipt.json.*")), [])

    def test_create_only_write_has_no_post_publication_cleanup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "receipt.json"
            unlink = Path.unlink

            def reject_temporary(path: Path, *args: object, **kwargs: object) -> None:
                if path.name.startswith(".receipt.json."):
                    raise PermissionError("injected sharing violation")
                unlink(path, *args, **kwargs)

            with patch("anachron.v5_custody.Path.unlink", autospec=True, side_effect=reject_temporary):
                write_create_only(target, b"{}\n", "receipt", ByteBudget(16, 1))
            self.assertEqual(target.read_bytes(), b"{}\n")
            self.assertEqual(list(Path(temporary).glob(".receipt.json.*")), [])

    def test_publish_refuses_a_race_created_final_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / ".candidate.staging"
            staging.mkdir()
            (staging / "staged.json").write_bytes(b"staged\n")
            final = root / "candidate"
            final.mkdir()
            (final / "user.json").write_bytes(b"user\n")
            with self.assertRaisesRegex(V5CustodyError, "final target must be absent"):
                publish_staging_root(staging, final, "candidate")
            self.assertEqual((final / "user.json").read_bytes(), b"user\n")
            self.assertEqual((staging / "staged.json").read_bytes(), b"staged\n")

    def test_publish_preserves_a_raced_empty_final_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / ".candidate.staging"
            staging.mkdir()
            (staging / "staged.json").write_bytes(b"staged\n")
            final = root / "candidate"
            move = v5_custody._atomic_no_replace_move

            def publish_competitor(source: Path, destination: Path) -> None:
                Path(destination).mkdir()
                move(source, destination)

            with patch(
                "anachron.v5_custody._atomic_no_replace_move",
                side_effect=publish_competitor,
            ), self.assertRaisesRegex(V5CustodyError, "final target must be absent"):
                publish_staging_root(staging, final, "candidate")
            self.assertTrue(final.is_dir())
            self.assertEqual(list(final.iterdir()), [])
            self.assertEqual((staging / "staged.json").read_bytes(), b"staged\n")


if __name__ == "__main__":
    unittest.main()
