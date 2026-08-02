from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hf_access import HuggingFaceAccessError
from model_registry import (
    ModelCacheOperationError,
    download_or_repair_model,
    model_cache_status,
    model_spec,
    resolve_model_path,
)


ROOT = Path(__file__).resolve().parents[1]


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(path).encode("utf-8"))
        elif path.is_dir():
            digest.update(b"directory\0")
        elif path.is_file():
            digest.update(b"file\0")
            digest.update(path.read_bytes())
        else:
            digest.update(b"other\0")
    return digest.hexdigest()


def _write_snapshot(
    cache_root: Path,
    *,
    missing: set[str] | None = None,
) -> Path:
    spec = model_spec("mlx_clone")
    snapshot = cache_root / spec.cache_name / "snapshots" / spec.revision
    omitted = missing or set()
    for relative in spec.required_paths:
        if relative in omitted:
            continue
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"b19-t08:{relative}".encode("utf-8"))
    return snapshot


class B19T08BlockedNetworkCacheRecoveryTests(unittest.TestCase):
    def test_missing_cache_fails_before_hub_access_and_creates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary) / "cache"
            before = _tree_digest(cache_root)
            with (
                patch("hf_access.snapshot_download") as snapshot_download,
                patch("hf_access.hf_hub_download") as file_download,
            ):
                with self.assertRaises(ModelCacheOperationError) as caught:
                    resolve_model_path("mlx_clone", cache_dir=cache_root)

            self.assertEqual(caught.exception.code, "model_cache_download_required")
            snapshot_download.assert_not_called()
            file_download.assert_not_called()
            self.assertEqual(_tree_digest(cache_root), before)
            self.assertFalse(cache_root.exists())

    def test_blocked_repair_preserves_then_recovers_disposable_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary) / "cache"
            missing_path = "model.safetensors"
            snapshot = _write_snapshot(cache_root, missing={missing_path})
            spec = model_spec("mlx_clone")

            incomplete = model_cache_status(spec.key, cache_dir=cache_root)
            self.assertEqual(incomplete["state"], "incomplete")
            self.assertEqual(incomplete["missing_required_paths"], [missing_path])
            before_failed_repair = _tree_digest(cache_root)

            free_space = type(
                "Usage",
                (),
                {"free": spec.estimated_size_bytes + 2 * 1024**3},
            )()
            with (
                patch.object(
                    socket.socket,
                    "connect",
                    side_effect=AssertionError("network access is forbidden"),
                ),
                patch(
                    "hf_access.snapshot_download",
                    side_effect=OSError("network is unreachable"),
                ),
                patch("model_registry.shutil.disk_usage", return_value=free_space),
            ):
                with self.assertRaises(HuggingFaceAccessError) as blocked:
                    download_or_repair_model(
                        spec.key,
                        repair=True,
                        cache_dir=cache_root,
                    )

            self.assertEqual(blocked.exception.code, "huggingface_network_unavailable")
            self.assertEqual(_tree_digest(cache_root), before_failed_repair)
            still_incomplete = model_cache_status(spec.key, cache_dir=cache_root)
            self.assertEqual(still_incomplete["state"], "incomplete")
            self.assertEqual(still_incomplete["missing_required_paths"], [missing_path])

            def local_repair(repo_id: str, **options: object) -> Path:
                self.assertEqual(repo_id, spec.repo_id)
                self.assertEqual(options["revision"], spec.revision)
                self.assertEqual(options["cache_dir"], cache_root.resolve())
                self.assertTrue(options["force_download"])
                self.assertEqual(options["required_paths"], spec.required_paths)
                target = snapshot / missing_path
                target.write_bytes(b"b19-t08:recovered-model")
                return snapshot

            with (
                patch(
                    "hf_access.snapshot_download_with_public_fallback",
                    side_effect=local_repair,
                ),
                patch("model_registry.shutil.disk_usage", return_value=free_space),
            ):
                repaired = download_or_repair_model(
                    spec.key,
                    repair=True,
                    cache_dir=cache_root,
                )

            self.assertEqual(repaired["operation"], "repaired")
            self.assertTrue(repaired["downloaded"])
            self.assertTrue(repaired["repaired"])
            self.assertEqual(repaired["state"], "cached")
            self.assertEqual(repaired["missing_required_paths"], [])
            self.assertNotEqual(_tree_digest(cache_root), before_failed_repair)

            with (
                patch.object(
                    socket.socket,
                    "connect",
                    side_effect=AssertionError("network access is forbidden"),
                ),
                patch(
                    "hf_access.snapshot_download",
                    side_effect=AssertionError("Hub snapshot access is forbidden"),
                ) as snapshot_download,
                patch(
                    "hf_access.hf_hub_download",
                    side_effect=AssertionError("Hub file access is forbidden"),
                ) as file_download,
            ):
                resolved = resolve_model_path(spec.key, cache_dir=cache_root)

            self.assertEqual(resolved, snapshot.resolve())
            snapshot_download.assert_not_called()
            file_download.assert_not_called()

            code = """
import json
import os
import socket
from pathlib import Path
from unittest.mock import patch
from model_registry import model_cache_status, resolve_model_path

def blocked(*args, **kwargs):
    raise AssertionError('network access is forbidden')

cache_root = Path(os.environ['ALEXANDRIA_HF_CACHE'])
with patch.object(socket.socket, 'connect', blocked), \
     patch('hf_access.snapshot_download', side_effect=blocked), \
     patch('hf_access.hf_hub_download', side_effect=blocked):
    status = model_cache_status('mlx_clone', cache_dir=cache_root)
    resolved = resolve_model_path('mlx_clone', cache_dir=cache_root)
print(json.dumps({'state': status['state'], 'cached': status['cached'], 'resolved': str(resolved)}, sort_keys=True))
"""
            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": str(ROOT / "app"),
                    "ALEXANDRIA_HF_CACHE": str(cache_root),
                    "HF_HUB_OFFLINE": "1",
                }
            )
            process = subprocess.run(
                [sys.executable, "-c", code],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            payload = json.loads(process.stdout.strip().splitlines()[-1])
            self.assertEqual(payload["state"], "cached")
            self.assertTrue(payload["cached"])
            self.assertEqual(Path(payload["resolved"]), snapshot.resolve())


if __name__ == "__main__":
    unittest.main()
