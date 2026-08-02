from __future__ import annotations

import hashlib
import json
import os
import select
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Callable

import audio_crash_reconciliation as crash_reconciliation
import audio_takes


FORK_SUPPORTED = hasattr(os, "fork") and hasattr(os, "register_at_fork")


def _terminate_and_reap(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 0.25
    while time.monotonic() < deadline:
        waited, _status = os.waitpid(pid, os.WNOHANG)
        if waited == pid:
            return
        time.sleep(0.01)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)


def _fork_while_other_thread_holds(
    lock: threading.RLock,
    child_action: Callable[[], str],
    *,
    timeout_seconds: float = 1.0,
) -> str:
    owned = threading.Event()
    release = threading.Event()

    def own_lock() -> None:
        with lock:
            owned.set()
            release.wait(timeout_seconds * 4)

    owner = threading.Thread(target=own_lock, name="fork-lock-owner")
    owner.start()
    if not owned.wait(timeout_seconds):
        release.set()
        owner.join(timeout_seconds)
        raise AssertionError("background thread did not acquire the lock")

    read_fd, write_fd = os.pipe()
    pid = -1
    try:
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            try:
                payload = {"ok": True, "result": child_action()}
            except BaseException as exc:
                payload = {"ok": False, "error": repr(exc)}
            encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
            os.write(write_fd, encoded)
            os.close(write_fd)
            os._exit(0 if payload["ok"] else 1)

        os.close(write_fd)
        write_fd = -1
        release.set()
        owner.join(timeout_seconds)
        if owner.is_alive():
            raise AssertionError("background lock owner did not terminate")

        deadline = time.monotonic() + timeout_seconds
        body = bytearray()
        status = None
        while time.monotonic() < deadline:
            readable, _writable, _exceptional = select.select(
                [read_fd], [], [], 0.01
            )
            if readable:
                chunk = os.read(read_fd, 65536)
                if chunk:
                    body.extend(chunk)
            waited, observed_status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                status = observed_status
                body.extend(os.read(read_fd, 65536))
                break
        if status is None:
            _terminate_and_reap(pid)
            pid = -1
            raise AssertionError(
                "fork child timed out while acquiring an inherited audio lock"
            )
        pid = -1
        with unittest.TestCase().assertRaises(ChildProcessError):
            os.waitpid(waited, os.WNOHANG)
        if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
            raise AssertionError(
                f"fork child failed with status {status}: {body.decode('utf-8')}"
            )
        decoded = json.loads(body.decode("utf-8"))
        if not decoded["ok"]:
            raise AssertionError(f"fork child action failed: {decoded['error']}")
        return str(decoded["result"])
    finally:
        release.set()
        owner.join(timeout_seconds)
        if pid > 0:
            _terminate_and_reap(pid)
        if write_fd >= 0:
            os.close(write_fd)
        os.close(read_fd)


@unittest.skipUnless(FORK_SUPPORTED, "POSIX fork hooks are unavailable")
class AudioForkSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.chunks = [{"id": 0, "speaker": "NARRATOR", "text": "Fork safety."}]
        (self.root / "chunks.json").write_text(
            json.dumps(self.chunks), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_child_resets_project_lock_inherited_from_vanished_thread(self) -> None:
        inherited_lock = crash_reconciliation._PROJECT_LOCKS.setdefault(
            str(self.root.resolve()), threading.RLock()
        )

        def child_transition() -> str:
            result = crash_reconciliation.apply_audio_transition(
                self.root,
                transition="chunks_metadata",
                operation_id="fork-child-project-lock",
                json_writes={"fork-child.json": {"writer": "child"}},
            )
            return str(result["status"])

        child_status = _fork_while_other_thread_holds(
            inherited_lock, child_transition
        )

        self.assertEqual(child_status, "committed")
        self.assertEqual(
            json.loads((self.root / "fork-child.json").read_text()),
            {"writer": "child"},
        )
        parent = crash_reconciliation.apply_audio_transition(
            self.root,
            transition="chunks_metadata",
            operation_id="fork-parent-project-lock",
            json_writes={"fork-parent.json": {"writer": "parent"}},
        )
        self.assertEqual(parent["status"], "committed")

    def test_child_resets_registry_lock_inherited_from_vanished_thread(self) -> None:
        child_record = self._record("take_child", b"child-artifact")
        inherited_lock = audio_takes._registry_thread_lock(self.root)

        def child_registration() -> str:
            take, _registry = audio_takes.register_take(
                self.root,
                chunks=self.chunks,
                record=child_record,
            )
            return str(take["take_id"])

        child_take_id = _fork_while_other_thread_holds(
            inherited_lock, child_registration
        )

        self.assertEqual(child_take_id, "take_child")
        parent_record = self._record("take_parent", b"parent-artifact")
        parent_take, registry = audio_takes.register_take(
            self.root,
            chunks=self.chunks,
            record=parent_record,
        )
        self.assertEqual(parent_take["take_id"], "take_parent")
        self.assertEqual(set(registry["takes"]), {"take_child", "take_parent"})
        for take in registry["takes"].values():
            artifact = self.root / take["artifact"]["relative_path"]
            self.assertEqual(
                hashlib.sha256(artifact.read_bytes()).hexdigest(),
                take["artifact"]["sha256"],
            )

    def _record(self, take_id: str, content: bytes) -> dict:
        relative_path = f"voicelines/takes/chunk_0/{take_id}.wav"
        artifact = self.root / relative_path
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(content)
        return audio_takes.build_take_record(
            take_id=take_id,
            chunk_key_value="chunk:0",
            chunk_index=0,
            kind="raw",
            source_take_id=None,
            root_take_id=take_id,
            artifact={
                "relative_path": relative_path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "duration_ms": 1,
                "format": "wav",
                "sample_rate": 24000,
                "sample_count": 24,
                "channels": 1,
            },
            authored={"text": "Fork safety.", "speaker": "NARRATOR"},
            voice={},
            generation={},
            synthesis={},
        )


if __name__ == "__main__":
    unittest.main()
