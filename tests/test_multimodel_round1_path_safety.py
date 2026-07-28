from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

import multimodel_round1_paths as safe_paths  # noqa: E402


class TypedPathBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "evidence"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_identifier_rejects_separators_traversal_and_non_ascii(self) -> None:
        # Given identifier-shaped hostile values, when parsed, then each is rejected.
        invalid = ("", ".", "..", "../sample", "sample/name", "sample\\name", "café")
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(OSError):
                    safe_paths.SafeIdentifier(value)

    def test_identifier_accepts_round1_allowlist(self) -> None:
        # Given a Round 1 identifier, when parsed, then its literal value is preserved.
        identifier = safe_paths.SafeIdentifier("ryan_acted:happy-01")

        self.assertEqual(str(identifier), "ryan_acted:happy-01")

    def test_relative_path_rejects_traversal_absolute_and_separators(self) -> None:
        # Given hostile relative path strings, when parsed, then each is rejected.
        invalid = (
            "", ".", "..", "../escape.wav", "outputs/../escape.wav",
            "/tmp/escape.wav", "outputs\\escape.wav", "C:\\escape.wav",
            "outputs//escape.wav", "outputs/$escape.wav",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(OSError):
                    safe_paths.SafeRelativePath(value)

    def test_output_and_result_paths_reject_traversal_independently(self) -> None:
        # Given traversal in either artifact field, when parsed, then both fail closed.
        cases = (
            ("../escape.wav", "outputs/sample.json"),
            ("outputs/sample.wav", "../escape.json"),
        )
        for output_file, result_file in cases:
            with self.subTest(output_file=output_file, result_file=result_file):
                with self.assertRaises(OSError):
                    safe_paths.parse_artifact_paths(
                        self.root, output_file, result_file
                    )

    def test_output_and_result_paths_accept_allowlisted_values(self) -> None:
        # Given normal artifact paths, when parsed, then both remain root-relative.
        artifacts = safe_paths.parse_artifact_paths(
            self.root, "outputs/model/sample.wav", "outputs/model/sample.json"
        )

        self.assertEqual(
            artifacts.output.literal, self.root / "outputs/model/sample.wav"
        )
        self.assertEqual(
            artifacts.result.literal, self.root / "outputs/model/sample.json"
        )


class ContainedIoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.root = base / "evidence"
        self.outside = base / "outside"
        self.root.mkdir()
        self.outside.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def target(self, relative: str) -> safe_paths.ContainedPath:
        return safe_paths.contained_path(self.root, relative)

    def test_atomic_write_and_read_use_regular_contained_file(self) -> None:
        # Given a typed nested target, when written and read, then bytes round-trip.
        target = self.target("nested/result.json")
        safe_paths.safe_atomic_write_text(target, "payload\n")

        self.assertEqual(safe_paths.safe_read_text(target), "payload\n")
        self.assertFalse(target.literal.is_symlink())

    def test_atomic_write_rejects_symlink_target(self) -> None:
        # Given the final target redirects outside, when written, then it fails closed.
        outside_file = self.outside / "result.json"
        outside_file.write_text("sentinel", encoding="utf-8")
        target = self.target("result.json")
        target.literal.symlink_to(outside_file)

        with self.assertRaises(OSError):
            safe_paths.safe_atomic_write_text(target, "replacement")

        self.assertEqual(outside_file.read_text(encoding="utf-8"), "sentinel")

    def test_atomic_write_rejects_symlink_ancestor(self) -> None:
        # Given a target ancestor redirects outside, when written, then no file escapes.
        (self.root / "linked").symlink_to(self.outside, target_is_directory=True)

        with self.assertRaises(OSError):
            safe_paths.safe_atomic_write_text(
                self.target("linked/result.json"), "payload"
            )

        self.assertEqual(list(self.outside.iterdir()), [])

    def test_read_rejects_symlink_target(self) -> None:
        # Given a readable-looking target symlink, when read, then it is rejected.
        outside_file = self.outside / "secret.txt"
        outside_file.write_text("secret", encoding="utf-8")
        target = self.target("secret.txt")
        target.literal.symlink_to(outside_file)

        with self.assertRaises(OSError):
            safe_paths.safe_read_text(target)

    def test_atomic_copy_rejects_symlink_source(self) -> None:
        # Given a source symlink, when copied, then linked bytes are not consumed.
        outside_file = self.outside / "source.bin"
        outside_file.write_bytes(b"secret")
        source = self.target("source.bin")
        source.literal.symlink_to(outside_file)

        with self.assertRaises(OSError):
            safe_paths.safe_atomic_copy(source, self.target("copy.bin"))

    def test_atomic_copy_writes_regular_contained_target(self) -> None:
        # Given a regular source, when copied, then the contained target is exact.
        source = self.target("source.bin")
        source.literal.write_bytes(b"round-one")
        target = self.target("nested/copy.bin")

        safe_paths.safe_atomic_copy(source, target)

        self.assertEqual(target.literal.read_bytes(), b"round-one")
        self.assertFalse(target.literal.is_symlink())

    def test_atomic_write_does_not_follow_last_instant_target_swap(self) -> None:
        # Given the target becomes a symlink at the final rename boundary.
        outside_file = self.outside / "result.json"
        outside_file.write_text("sentinel", encoding="utf-8")
        target = self.target("result.json")
        target.literal.write_text("old", encoding="utf-8")
        real_replace = os.replace

        def swap_then_replace(
            source: str,
            destination: str,
            *,
            src_dir_fd: int | None = None,
            dst_dir_fd: int | None = None,
        ) -> None:
            os.unlink(destination, dir_fd=dst_dir_fd)
            os.symlink(outside_file, destination, dir_fd=dst_dir_fd)
            real_replace(
                source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd
            )

        # When committed, then the link entry is replaced, not its outside target.
        with patch.object(safe_paths.os, "replace", side_effect=swap_then_replace):
            safe_paths.safe_atomic_write_text(target, "new")

        self.assertEqual(outside_file.read_text(encoding="utf-8"), "sentinel")
        self.assertEqual(target.literal.read_text(encoding="utf-8"), "new")
        self.assertFalse(target.literal.is_symlink())

    def test_contained_guard_rejects_symlink_leaf(self) -> None:
        # Given a root-aware guard and a leaf symlink.
        outside_file = self.outside / "secret.bin"
        outside_file.write_bytes(b"secret")
        linked = self.root / "linked.bin"
        linked.symlink_to(outside_file)

        # When guarded, then the leaf link is rejected.
        with self.assertRaises(OSError):
            safe_paths.contained_path_guard(self.root)(
                linked, allow_missing_leaf=False
            )

    def test_contained_guard_rejects_symlink_ancestor(self) -> None:
        # Given a root-aware guard and an ancestor symlink.
        (self.root / "linked").symlink_to(self.outside, target_is_directory=True)

        # When guarded, then the ancestor link is rejected.
        with self.assertRaises(OSError):
            safe_paths.contained_path_guard(self.root)(
                self.root / "linked" / "artifact.bin",
                allow_missing_leaf=True,
            )

    def test_contained_guard_allows_missing_regular_leaf(self) -> None:
        # Given a normal contained parent, when a missing leaf is allowed, then it passes.
        safe_paths.contained_path_guard(self.root)(
            self.root / "future.bin", allow_missing_leaf=True
        )

        self.assertFalse((self.root / "future.bin").exists())

    def test_streaming_sha256_reads_regular_contained_file(self) -> None:
        # Given a regular contained file, when hashed, then the digest is exact.
        target = self.target("artifact.bin")
        target.literal.write_bytes(b"round-one")

        digest = safe_paths.safe_sha256_file(target)

        self.assertEqual(digest, hashlib.sha256(b"round-one").hexdigest())

    def test_streaming_sha256_rejects_symlink_target(self) -> None:
        # Given a linked source, when hashed, then the target is never read.
        outside_file = self.outside / "secret.bin"
        outside_file.write_bytes(b"secret")
        target = self.target("artifact.bin")
        target.literal.symlink_to(outside_file)

        with self.assertRaises(OSError):
            safe_paths.safe_sha256_file(target)

    def test_regular_file_stat_is_descriptor_pinned(self) -> None:
        # Given a regular contained file, when stated, then its exact size is returned.
        target = self.target("artifact.bin")
        target.literal.write_bytes(b"round-one")

        details = safe_paths.safe_file_stat(target)

        self.assertEqual(details.st_size, len(b"round-one"))


if __name__ == "__main__":
    unittest.main()
