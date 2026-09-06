from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anachron import v5_paths
from anachron.v5_custody import (
    FAILURE_MAX_BYTES,
    FAILURE_MAX_ENTRIES,
    FAILURE_MAX_FILES,
    verify_copy_archive_round_trips,
)
from anachron.v5_paths import (
    V5PathError,
    assert_no_alternate_data_streams,
    fsync_directory,
    ordinal_evidence_path,
    portable_component,
    portable_relative_path,
)


class V5PathTests(unittest.TestCase):
    def test_windows_directory_flush_accepts_only_unsupported_directory_handle(self) -> None:
        fixture = Path("C:/fixture")
        with (
            patch.object(v5_paths.os, "name", "nt"),
            patch.object(v5_paths.os, "open", side_effect=PermissionError(v5_paths.errno.EACCES, "access denied")),
        ):
            fsync_directory(fixture, "fixture")

    def test_windows_directory_flush_refuses_unrelated_open_failure(self) -> None:
        fixture = Path("C:/fixture")
        with (
            patch.object(v5_paths.os, "name", "nt"),
            patch.object(v5_paths.os, "open", side_effect=OSError(v5_paths.errno.ENOENT, "not found")),
            self.assertRaisesRegex(V5PathError, "cannot be flushed"),
        ):
            fsync_directory(fixture, "fixture")

    def test_portable_components_reject_windows_ambiguous_names(self) -> None:
        for value in ("model:tag", "CON", "file.", "file ", "a/b", "a\\b", ".."):
            with self.subTest(value=value), self.assertRaises(V5PathError):
                portable_component(value, "test")

    def test_relative_path_rejects_traversal_and_backslashes(self) -> None:
        for value in ("../raw/file.json", "raw\\file.json", "/raw/file.json", "raw/CON.json"):
            with self.subTest(value=value), self.assertRaises(V5PathError):
                portable_relative_path(value, "test")

    def test_model_name_never_enters_ordinal_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "raw").mkdir()
            path = ordinal_evidence_path(root, 1, "first-response")
            self.assertEqual(path.name, "t0001-first-response.json")
            self.assertNotIn(":", path.name)

    def test_binary_copy_and_archive_round_trip_preserve_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "evidence"
            root.mkdir()
            (root / "raw").mkdir()
            (root / "raw" / "t0001-first-response.json").write_bytes(b'{"x":"\\u00ff"}\n')
            verify_copy_archive_round_trips(
                root,
                maximum_entries=FAILURE_MAX_ENTRIES,
                maximum_files=FAILURE_MAX_FILES,
                maximum_bytes=FAILURE_MAX_BYTES,
                maximum_depth=2,
            )

    @unittest.skipUnless(os.name == "nt", "NTFS ADS semantics are Windows-specific")
    def test_windows_colon_filename_is_rejected_before_admission(self) -> None:
        with self.assertRaises(V5PathError):
            portable_component("qwen2.5:7b", "model")

    @unittest.skipUnless(os.name == "nt", "NTFS ADS semantics are Windows-specific")
    def test_real_alternate_data_stream_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "ordinary.json"
            target.write_text("{}\n", encoding="utf-8")
            Path(f"{target}:unexpected").write_text("stream", encoding="utf-8")
            with self.assertRaisesRegex(V5PathError, "alternate data stream"):
                assert_no_alternate_data_streams(root)


if __name__ == "__main__":
    unittest.main()
