from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from model_registry import (
    MODEL_REGISTRY_SCHEMA_VERSION,
    ModelCacheOperationError,
    ModelRegistryError,
    download_or_repair_model,
    is_registered_model,
    model_cache_status,
    model_registry_payload,
    model_registry_status,
    model_spec,
    registered_models,
    resolve_model_path,
)


class ModelRegistryTests(unittest.TestCase):
    def test_every_model_has_unique_key_repo_and_immutable_revision(self) -> None:
        specs = registered_models()
        self.assertGreaterEqual(len(specs), 7)
        self.assertEqual(len({item.key for item in specs}), len(specs))
        self.assertEqual(len({item.repo_id for item in specs}), len(specs))
        for item in specs:
            with self.subTest(item=item.key):
                self.assertRegex(item.revision, re.compile(r"^[0-9a-f]{40}$"))
                self.assertTrue(item.required_paths)
                self.assertGreater(item.estimated_size_bytes, 100_000_000)
                self.assertNotIn("..", item.cache_name)

    def test_registry_classifies_installation_and_declares_consumers(self) -> None:
        specs = registered_models()
        core = {item.key for item in specs if item.installation_class == "core"}
        self.assertEqual(
            core,
            {"mlx_clone", "mlx_custom_voice", "mlx_voice_design"},
        )
        self.assertEqual(
            {item.key for item in specs if item.required_by_default},
            core,
        )
        for item in specs:
            with self.subTest(item=item.key):
                self.assertTrue(item.consumers)
                self.assertTrue(all(value.strip() for value in item.consumers))
                payload = item.as_dict()
                self.assertEqual(payload["installation_class"], item.installation_class)
                self.assertEqual(payload["consumers"], list(item.consumers))

    def test_qwen_manifests_require_runtime_tokenizer_processor_and_backend_files(self) -> None:
        expected = {
            "config.json",
            "generation_config.json",
            "model.safetensors",
            "preprocessor_config.json",
            "tokenizer_config.json",
            "merges.txt",
            "vocab.json",
            "speech_tokenizer/config.json",
            "speech_tokenizer/configuration.json",
            "speech_tokenizer/model.safetensors",
            "speech_tokenizer/preprocessor_config.json",
        }
        for key in (
            "mlx_clone",
            "mlx_custom_voice",
            "mlx_voice_design",
            "pytorch_qwen_custom_voice",
            "pytorch_qwen_voice_design",
            "pytorch_qwen_base",
        ):
            with self.subTest(key=key):
                self.assertEqual(set(model_spec(key).required_paths), expected)

    def test_registry_resolves_keys_and_repository_ids(self) -> None:
        clone = model_spec("mlx_clone")
        self.assertEqual(model_spec(clone.repo_id), clone)
        self.assertTrue(is_registered_model(clone.repo_id))
        self.assertFalse(is_registered_model("owner/unregistered"))
        with self.assertRaises(ModelRegistryError):
            model_spec("owner/unregistered")

    def test_resolve_model_path_forwards_pin_and_required_files(self) -> None:
        expected = Path("/cache/pinned-model")
        cached = {
            "state": "cached",
            "cached": True,
        }
        with (
            patch("model_registry.model_cache_status", return_value=cached),
            patch(
                "hf_access.snapshot_download_with_public_fallback",
                return_value=expected,
            ) as download,
        ):
            result = resolve_model_path("mlx_clone")

        spec = model_spec("mlx_clone")
        self.assertEqual(result, expected)
        self.assertEqual(download.call_args.args, (spec.repo_id,))
        self.assertEqual(download.call_args.kwargs["revision"], spec.revision)
        self.assertEqual(
            download.call_args.kwargs["required_paths"],
            spec.required_paths,
        )
        self.assertTrue(download.call_args.kwargs["local_files_only"])

    def test_runtime_missing_or_incomplete_model_fails_before_hub_access(self) -> None:
        for state, expected_code, expected_action in (
            ("missing", "model_cache_download_required", "Download"),
            ("incomplete", "model_cache_repair_required", "Repair"),
        ):
            with self.subTest(state=state):
                with (
                    patch(
                        "model_registry.model_cache_status",
                        return_value={"state": state, "cached": False},
                    ),
                    patch(
                        "hf_access.snapshot_download_with_public_fallback"
                    ) as download,
                ):
                    with self.assertRaises(ModelCacheOperationError) as caught:
                        resolve_model_path("mlx_clone")

                self.assertEqual(caught.exception.code, expected_code)
                self.assertIn("Maintenance → Local model cache", str(caught.exception))
                self.assertIn(expected_action, str(caught.exception))
                download.assert_not_called()

    def test_fresh_process_resolves_complete_core_models_with_network_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)
            required = [
                spec for spec in registered_models()
                if spec.required_by_default
            ]
            for spec in required:
                snapshot = cache_root / spec.cache_name / "snapshots" / spec.revision
                for relative in spec.required_paths:
                    target = snapshot / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(f"{spec.key}:{relative}".encode("utf-8"))

            def cache_digest() -> str:
                digest = hashlib.sha256()
                for path in sorted(cache_root.rglob("*")):
                    if path.is_file():
                        digest.update(path.relative_to(cache_root).as_posix().encode("utf-8"))
                        digest.update(path.read_bytes())
                return digest.hexdigest()

            before = cache_digest()
            code = """
import json
import socket
from unittest.mock import patch
from model_registry import model_registry_status, registered_models, resolve_model_path

def blocked(*args, **kwargs):
    raise AssertionError('network access is forbidden')

with patch.object(socket.socket, 'connect', blocked), \
     patch('hf_access.snapshot_download', side_effect=blocked), \
     patch('hf_access.hf_hub_download', side_effect=blocked):
    status = model_registry_status()
    resolved = {
        spec.key: str(resolve_model_path(spec.key))
        for spec in registered_models()
        if spec.required_by_default
    }
print(json.dumps({'status': status, 'resolved': resolved}, sort_keys=True))
"""
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "app")
            environment["ALEXANDRIA_HF_CACHE"] = str(cache_root)
            environment["HF_HUB_OFFLINE"] = "1"
            result = subprocess.run(
                [sys.executable, "-c", code],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(payload["status"]["required_missing_count"], 0)
            self.assertEqual(payload["status"]["required_incomplete_count"], 0)
            self.assertEqual(set(payload["resolved"]), {spec.key for spec in required})
            for spec in required:
                self.assertEqual(
                    Path(payload["resolved"][spec.key]),
                    (cache_root / spec.cache_name / "snapshots" / spec.revision).resolve(),
                )
            self.assertEqual(cache_digest(), before)

    def test_repeated_required_model_resolution_makes_no_hub_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)
            required = [
                spec for spec in registered_models()
                if spec.required_by_default
            ]
            for spec in required:
                snapshot = (
                    cache_root
                    / spec.cache_name
                    / "snapshots"
                    / spec.revision
                )
                for relative in spec.required_paths:
                    path = snapshot / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"fixture")

            with (
                patch(
                    "hf_access.snapshot_download",
                    side_effect=AssertionError("Hub snapshot call is forbidden"),
                ) as snapshot_download,
                patch(
                    "hf_access.hf_hub_download",
                    side_effect=AssertionError("Hub file call is forbidden"),
                ) as file_download,
            ):
                for _ in range(2):
                    for spec in required:
                        resolved = resolve_model_path(
                            spec.key,
                            cache_dir=cache_root,
                        )
                        self.assertEqual(
                            resolved,
                            (
                                cache_root
                                / spec.cache_name
                                / "snapshots"
                                / spec.revision
                            ).resolve(),
                        )

            snapshot_download.assert_not_called()
            file_download.assert_not_called()

    def test_canonical_status_does_not_accept_duplicate_fallback_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical = root / "canonical"
            fallback = root / "fallback"
            spec = model_spec("mlx_clone")
            snapshot = fallback / spec.cache_name / "snapshots" / spec.revision
            for relative in spec.required_paths:
                target = snapshot / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"fixture")
            with (
                patch("hf_access.shared_huggingface_cache_dir", return_value=canonical),
                patch("hf_access.constants.HF_HUB_CACHE", str(fallback)),
            ):
                status = model_cache_status(spec.key)
            self.assertEqual(status["state"], "missing")
            self.assertEqual(status["cache_root"], str(canonical.resolve()))
            self.assertIsNone(status["snapshot_path"])

    def test_cache_status_uses_registered_revision_and_required_files(self) -> None:
        with patch(
            "hf_access.cached_snapshot_status",
            return_value={
                "state": "cached",
                "cached": True,
                "snapshot_path": "/cache/model",
                "cache_root": "/cache",
                "revision": "a" * 40,
                "required_paths": ["config.json"],
                "missing_required_paths": [],
                "broken_symlinks": [],
                "file_count": 3,
                "size_bytes": 123,
            },
        ) as status:
            result = model_cache_status("mlx_voice_design")

        spec = model_spec("mlx_voice_design")
        self.assertTrue(result["cached"])
        self.assertFalse(result["repair_required"])
        self.assertIsNone(result["action"])
        self.assertEqual(result["schema_version"], MODEL_REGISTRY_SCHEMA_VERSION)
        self.assertEqual(result["model"]["key"], spec.key)
        self.assertEqual(status.call_args.args, (spec.repo_id,))
        self.assertEqual(status.call_args.kwargs["revision"], spec.revision)
        self.assertEqual(
            status.call_args.kwargs["required_paths"],
            spec.required_paths,
        )

    def test_incomplete_status_requires_explicit_repair(self) -> None:
        spec = model_spec("mlx_voice_design")
        with patch(
            "hf_access.cached_snapshot_status",
            return_value={
                "state": "incomplete",
                "cached": False,
                "snapshot_path": "/cache/incomplete",
                "cache_root": "/cache",
                "revision": spec.revision,
                "required_paths": list(spec.required_paths),
                "missing_required_paths": ["model.safetensors"],
                "broken_symlinks": [],
                "file_count": 2,
                "size_bytes": 123,
            },
        ):
            result = model_cache_status(spec.key)

        self.assertTrue(result["repair_required"])
        self.assertEqual(result["action"], "repair")

    def test_registry_status_aggregates_cache_states_and_sizes(self) -> None:
        statuses = []
        for index, spec in enumerate(registered_models()):
            state = "cached" if index == 0 else "missing"
            statuses.append(
                {
                    "schema_version": MODEL_REGISTRY_SCHEMA_VERSION,
                    "model": spec.as_dict(),
                    "dependencies": {
                        "modules": {name: True for name in spec.dependency_modules},
                        "ready": True,
                        "missing": [],
                    },
                    "state": state,
                    "cached": state == "cached",
                    "snapshot_path": "/cache/model" if state == "cached" else None,
                    "cache_root": "/cache",
                    "revision": spec.revision,
                    "required_paths": list(spec.required_paths),
                    "missing_required_paths": [] if state == "cached" else list(spec.required_paths),
                    "broken_symlinks": [],
                    "file_count": 3 if state == "cached" else 0,
                    "size_bytes": 123 if state == "cached" else 0,
                }
            )
        with patch(
            "model_registry.model_cache_status",
            side_effect=statuses,
        ):
            result = model_registry_status()

        self.assertEqual(result["cached_count"], 1)
        self.assertEqual(result["missing_count"], len(statuses) - 1)
        self.assertEqual(result["incomplete_count"], 0)
        self.assertEqual(result["cached_size_bytes"], 123)
        self.assertGreater(result["estimated_total_bytes"], 0)

    def test_registry_status_reports_memory_eligibility_and_loaded_identity(self) -> None:
        specs = registered_models()
        statuses = []
        for spec in specs:
            statuses.append(
                {
                    "schema_version": MODEL_REGISTRY_SCHEMA_VERSION,
                    "model": spec.as_dict(),
                    "dependencies": {
                        "modules": {name: True for name in spec.dependency_modules},
                        "ready": True,
                        "missing": [],
                    },
                    "state": "cached",
                    "cached": True,
                    "snapshot_path": f"/cache/{spec.key}/{spec.revision}",
                    "cache_root": "/cache",
                    "revision": spec.revision,
                    "required_paths": list(spec.required_paths),
                    "missing_required_paths": [],
                    "broken_symlinks": [],
                    "file_count": len(spec.required_paths),
                    "size_bytes": spec.estimated_size_bytes,
                }
            )
        with (
            patch("model_registry.model_cache_status", side_effect=statuses),
            patch(
                "model_registry._memory_snapshot",
                return_value={
                    "available": True,
                    "total_bytes": 128 * 1024**3,
                    "available_bytes": 96 * 1024**3,
                    "used_bytes": 32 * 1024**3,
                    "platform": "darwin",
                    "architecture": "arm64",
                },
            ),
        ):
            result = model_registry_status(loaded_model_keys=["mlx_clone"])
        self.assertEqual(result["loaded_models"][0]["model_key"], "mlx_clone")
        self.assertEqual(
            result["loaded_models"][0]["revision"],
            model_spec("mlx_clone").revision,
        )
        self.assertTrue(all(item["current_machine_eligible"] for item in result["models"]))
        self.assertTrue(all(item["memory"]["estimated_loaded_bytes"] > 0 for item in result["models"]))

        statuses[0]["dependencies"] = {
            "modules": {"mlx": False, "mlx_audio": True},
            "ready": False,
            "missing": ["mlx"],
        }
        with (
            patch("model_registry.model_cache_status", side_effect=statuses),
            patch(
                "model_registry._memory_snapshot",
                return_value={
                    "available": True,
                    "total_bytes": 8 * 1024**3,
                    "available_bytes": 1,
                    "used_bytes": 8 * 1024**3 - 1,
                    "platform": "darwin",
                    "architecture": "arm64",
                },
            ),
        ):
            blocked = model_registry_status()
        first = blocked["models"][0]
        self.assertFalse(first["current_machine_eligible"])
        self.assertTrue(any("missing dependencies" in reason for reason in first["ineligibility_reasons"]))
        self.assertTrue(any("available bytes" in reason for reason in first["ineligibility_reasons"]))

    def test_download_returns_existing_cache_without_network(self) -> None:
        cached = {
            "schema_version": MODEL_REGISTRY_SCHEMA_VERSION,
            "model": model_spec("mlx_clone").as_dict(),
            "state": "cached",
            "cached": True,
            "snapshot_path": "/cache/model",
            "cache_root": "/cache",
            "revision": model_spec("mlx_clone").revision,
            "required_paths": ["config.json"],
            "missing_required_paths": [],
            "broken_symlinks": [],
            "file_count": 3,
            "size_bytes": 123,
        }
        with (
            patch("model_registry.model_cache_status", return_value=cached),
            patch("hf_access.snapshot_download_with_public_fallback") as download,
        ):
            result = download_or_repair_model("mlx_clone")

        self.assertEqual(result["operation"], "already_cached")
        self.assertFalse(result["downloaded"])
        download.assert_not_called()

    def test_download_fails_before_network_when_disk_space_is_low(self) -> None:
        missing = {
            "schema_version": MODEL_REGISTRY_SCHEMA_VERSION,
            "model": model_spec("mlx_clone").as_dict(),
            "state": "missing",
            "cached": False,
            "snapshot_path": None,
            "cache_root": "/cache",
            "revision": model_spec("mlx_clone").revision,
            "required_paths": ["config.json"],
            "missing_required_paths": ["config.json"],
            "broken_symlinks": [],
            "file_count": 0,
            "size_bytes": 0,
        }
        usage = type("Usage", (), {"free": 1})()
        with (
            patch("model_registry.model_cache_status", return_value=missing),
            patch("model_registry._disk_usage_path", return_value=Path("/")),
            patch("model_registry.shutil.disk_usage", return_value=usage),
            patch("hf_access.snapshot_download_with_public_fallback") as download,
        ):
            with self.assertRaises(ModelCacheOperationError) as caught:
                download_or_repair_model("mlx_clone")

        self.assertEqual(caught.exception.code, "insufficient_model_cache_space")
        download.assert_not_called()

    def test_explicit_repair_forwards_pin_and_force_download(self) -> None:
        spec = model_spec("mlx_clone")
        incomplete = {
            "schema_version": MODEL_REGISTRY_SCHEMA_VERSION,
            "model": spec.as_dict(),
            "state": "incomplete",
            "cached": False,
            "snapshot_path": "/cache/incomplete",
            "cache_root": "/cache",
            "revision": spec.revision,
            "required_paths": list(spec.required_paths),
            "missing_required_paths": ["model.safetensors"],
            "broken_symlinks": [],
            "file_count": 2,
            "size_bytes": 10,
        }
        complete = {
            **incomplete,
            "state": "cached",
            "cached": True,
            "snapshot_path": "/cache/complete",
            "missing_required_paths": [],
            "file_count": 3,
            "size_bytes": spec.estimated_size_bytes,
        }
        usage = type("Usage", (), {"free": 20_000_000_000})()
        with (
            patch(
                "model_registry.model_cache_status",
                side_effect=[incomplete, complete],
            ),
            patch("model_registry._disk_usage_path", return_value=Path("/")),
            patch("model_registry.shutil.disk_usage", return_value=usage),
            patch(
                "hf_access.snapshot_download_with_public_fallback",
                return_value=Path("/cache/complete"),
            ) as download,
        ):
            result = download_or_repair_model("mlx_clone", repair=True)

        self.assertEqual(result["operation"], "repaired")
        self.assertTrue(result["downloaded"])
        self.assertTrue(result["repaired"])
        self.assertEqual(download.call_args.args, (spec.repo_id,))
        self.assertEqual(download.call_args.kwargs["revision"], spec.revision)
        self.assertTrue(download.call_args.kwargs["force_download"])
        self.assertEqual(
            download.call_args.kwargs["required_paths"],
            spec.required_paths,
        )

    def test_payload_is_stable_json_data(self) -> None:
        payload = model_registry_payload()
        self.assertEqual(payload["schema_version"], MODEL_REGISTRY_SCHEMA_VERSION)
        self.assertEqual(
            [item["key"] for item in payload["models"]],
            [item.key for item in registered_models()],
        )
        self.assertTrue(all(item["cache_name"].startswith("models--") for item in payload["models"]))


if __name__ == "__main__":
    unittest.main()
