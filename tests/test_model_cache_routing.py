from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import alexandria_preparer
import mlx_backend
import training_sidecar_service
import tts
from model_registry import model_spec
from training_sidecar import qwen_training


class _FakeFromPretrained:
    calls = []

    @classmethod
    def from_pretrained(cls, path, **kwargs):
        cls.calls.append((path, kwargs))
        return {"path": path, "kwargs": kwargs}


class ModelCacheRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeFromPretrained.calls = []

    def test_pytorch_tts_resolves_registered_model_before_loader(self) -> None:
        resolved = Path("/cache/qwen-base")
        with patch("tts.resolve_model_path", return_value=resolved) as resolver:
            result = tts.TTSEngine._load_model(
                _FakeFromPretrained,
                tts.PYTORCH_CLONE_MODEL,
                {"dtype": "fixture"},
            )

        resolver.assert_called_once_with(
            tts.PYTORCH_CLONE_MODEL,
            local_files_only=True,
        )
        self.assertEqual(result["path"], str(resolved))
        self.assertEqual(result["kwargs"]["dtype"], "fixture")
        self.assertTrue(result["kwargs"]["local_files_only"])

    def test_pytorch_tts_unregistered_model_downloads_once_before_loader(self) -> None:
        resolved = Path("/cache/custom-model")
        with patch(
            "tts.snapshot_download_with_public_fallback",
            return_value=resolved,
        ) as download:
            result = tts.TTSEngine._load_model(
                _FakeFromPretrained,
                "owner/custom-model",
                {},
            )

        download.assert_called_once_with(
            "owner/custom-model",
            local_files_only=True,
        )
        self.assertEqual(result["path"], str(resolved))
        self.assertTrue(result["kwargs"]["local_files_only"])

    def test_mlx_backend_loads_registered_snapshot_path(self) -> None:
        resolved = Path("/cache/mlx-clone")
        loaded = object()
        with (
            patch("mlx_backend.resolve_model_path", return_value=resolved) as resolver,
            patch("mlx_backend.get_model_name_parts", return_value=("owner", "model")),
            patch("mlx_backend.load_model", return_value=loaded) as load,
        ):
            result = mlx_backend.MLXBackend._load_repository_model(
                mlx_backend.MLXBackend.CLONE_MODEL
            )

        self.assertIs(result, loaded)
        resolver.assert_called_once_with(mlx_backend.MLXBackend.CLONE_MODEL)
        load.assert_called_once_with(
            resolved,
            model_name_parts=("owner", "model"),
        )

    def test_preparer_passes_local_registered_path_to_mlx_whisper(self) -> None:
        resolved = Path("/cache/whisper")
        fake = types.SimpleNamespace(
            transcribe=lambda *args, **kwargs: {"args": args, "kwargs": kwargs}
        )
        with (
            patch.dict("sys.modules", {"mlx_whisper": fake}),
            patch(
                "alexandria_preparer.resolve_model_path",
                return_value=resolved,
            ) as resolver,
        ):
            result = alexandria_preparer._default_transcriber(
                Path("/tmp/audio.wav"),
                language="en",
                model=model_spec("mlx_whisper_large_v3_turbo").repo_id,
            )

        resolver.assert_called_once_with(
            model_spec("mlx_whisper_large_v3_turbo").repo_id,
            local_files_only=True,
        )
        self.assertEqual(result["kwargs"]["path_or_hf_repo"], str(resolved))

    def test_sidecar_registered_model_source_uses_registry(self) -> None:
        resolved = Path("/cache/pytorch-base")
        with patch(
            "training_sidecar.qwen_training.resolve_model_path",
            return_value=resolved,
        ) as resolver:
            result = qwen_training._resolved_model_source(
                qwen_training.DEFAULT_MODEL,
                local_files_only=True,
            )

        self.assertEqual(result, resolved)
        resolver.assert_called_once_with(
            qwen_training.DEFAULT_MODEL,
            local_files_only=True,
        )

    def test_sidecar_service_default_uses_registered_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = root / "app" / "training_sidecar" / "env" / "bin" / "python"
            runner = root / "app" / "training_sidecar" / "runner.py"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"python")
            runner.write_text("# fixture", encoding="utf-8")
            command = training_sidecar_service._runner_command(
                root_dir=root,
                action="model_probe",
                payload={"device": "mps", "local_files_only": True},
            )

        index = command.index("--model-name")
        self.assertEqual(
            command[index + 1],
            model_spec("pytorch_qwen_base").repo_id,
        )
        self.assertIn("--local-files-only", command)


if __name__ == "__main__":
    unittest.main()
