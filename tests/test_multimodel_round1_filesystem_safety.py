from __future__ import annotations

import fcntl
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

import multimodel_round1_safety as safety  # noqa: E402


class ExistingFilesystemAttackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "evidence"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_disk_receipt_rejects_symlink_target(self) -> None:
        # Given a disk receipt entry redirected outside its evidence root.
        outside = self.root / "outside.jsonl"
        outside.write_text("sentinel\n", encoding="utf-8")
        receipt = self.root / "disk-headroom.jsonl"
        receipt.symlink_to(outside)

        # When the disk guard attempts to append its receipt, then it fails closed.
        with self.assertRaises(OSError):
            safety.require_disk_headroom(
                self.root,
                projected_bytes=0,
                safety_margin_bytes=0,
                free_bytes=64 * 1024**3,
                receipt_path=receipt,
                stage="adversarial-receipt",
            )

        self.assertEqual(outside.read_text(encoding="utf-8"), "sentinel\n")

    def test_disk_receipt_rejects_symlink_ancestor(self) -> None:
        # Given a receipt directory redirected outside the evidence root.
        outside = self.base / "receipt-outside"
        outside.mkdir()
        (self.root / "recovery").symlink_to(outside, target_is_directory=True)

        # When the receipt is appended, then the linked ancestor is rejected.
        with self.assertRaises(OSError):
            safety.require_disk_headroom(
                self.root,
                projected_bytes=0,
                safety_margin_bytes=0,
                free_bytes=64 * 1024**3,
                receipt_path=self.root / "recovery" / "disk-headroom.jsonl",
                stage="ancestor-test",
            )

        self.assertEqual(list(outside.iterdir()), [])

    def test_disk_receipt_rejects_lexical_traversal(self) -> None:
        # Given a receipt path containing a literal parent traversal component.
        receipt = self.root / "recovery" / ".." / "outside.jsonl"

        # When the disk guard records a check, then traversal is rejected.
        with self.assertRaises(OSError):
            safety.require_disk_headroom(
                self.root,
                projected_bytes=0,
                safety_margin_bytes=0,
                free_bytes=64 * 1024**3,
                receipt_path=receipt,
                stage="traversal-test",
            )

        self.assertFalse((self.root / "outside.jsonl").exists())

    def test_disk_receipt_writes_regular_contained_entry(self) -> None:
        # Given a normal contained receipt path.
        receipt = self.root / "recovery" / "disk-headroom.jsonl"

        # When the disk guard records a passing check.
        safety.require_disk_headroom(
            self.root,
            projected_bytes=0,
            safety_margin_bytes=0,
            free_bytes=64 * 1024**3,
            receipt_path=receipt,
            stage="normal-test",
        )

        # Then a regular receipt contains the requested stage.
        self.assertFalse(receipt.is_symlink())
        self.assertEqual(json.loads(receipt.read_text())["stage"], "normal-test")

    def test_metal_lock_rejects_symlink_target(self) -> None:
        # Given the literal lock entry is a symlink to an unrelated file.
        outside = self.root / "outside.lock"
        outside.write_text("sentinel", encoding="utf-8")
        lock_path = self.root / "metal.lock"
        lock_path.symlink_to(outside)

        # When the lease is requested, then the symlink is never followed.
        with self.assertRaises(OSError):
            lease = safety.acquire_metal_lock(lock_path, purpose="symlink-test")
            lease.close()

        self.assertEqual(outside.read_text(encoding="utf-8"), "sentinel")

    def test_metal_lock_rejects_symlink_parent(self) -> None:
        # Given the lock directory itself redirects outside the root.
        outside = self.base / "lock-outside"
        outside.mkdir()
        linked_parent = self.root / "locks"
        linked_parent.symlink_to(outside, target_is_directory=True)

        # When a lease is requested, then the linked parent is rejected.
        with self.assertRaises(OSError):
            lease = safety.acquire_metal_lock(
                linked_parent / "metal.lock", purpose="ancestor-test"
            )
            lease.close()

        self.assertEqual(list(outside.iterdir()), [])

    def test_metal_lock_rejects_lexical_traversal(self) -> None:
        # Given a lock path containing a literal parent traversal component.
        lock_path = self.root / "locks" / ".." / "metal.lock"

        # When a lease is requested, then traversal is rejected before creation.
        with self.assertRaises(OSError):
            lease = safety.acquire_metal_lock(lock_path, purpose="traversal-test")
            lease.close()

        self.assertFalse((self.root / "metal.lock").exists())

    def test_metal_lock_rejects_entry_swapped_after_flock(self) -> None:
        # Given an attacker swaps the literal lock entry immediately after flock.
        lock_path = self.root / "metal.lock"
        outside = self.root / "outside.lock"
        outside.write_text("sentinel", encoding="utf-8")
        real_flock = fcntl.flock
        swapped = False

        def swap_after_lock(descriptor: int, operation: int) -> None:
            nonlocal swapped
            real_flock(descriptor, operation)
            if operation & fcntl.LOCK_EX and not swapped:
                swapped = True
                lock_path.unlink()
                lock_path.symlink_to(outside)

        # When acquisition reaches the post-lock boundary, then the swap is detected.
        with patch.object(safety.fcntl, "flock", side_effect=swap_after_lock):
            with self.assertRaises(OSError):
                lease = safety.acquire_metal_lock(lock_path, purpose="swap-test")
                lease.close()

        self.assertEqual(outside.read_text(encoding="utf-8"), "sentinel")

    def test_metal_lock_contends_and_releases_on_regular_entry(self) -> None:
        # Given a lease held on a regular literal lock entry.
        lock_path = self.root / "metal.lock"
        first = safety.acquire_metal_lock(lock_path, purpose="first")
        self.addCleanup(first.close)

        # When a second lease is requested, then contention is explicit.
        with self.assertRaises(safety.MetalLockBusyError):
            safety.acquire_metal_lock(lock_path, purpose="second")

        # When the first lease closes, then the same entry can be reacquired.
        first.close()
        with safety.acquire_metal_lock(lock_path, purpose="third"):
            self.assertFalse(lock_path.is_symlink())


if __name__ == "__main__":
    unittest.main()
