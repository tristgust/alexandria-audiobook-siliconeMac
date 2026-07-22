from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import hf_utils


class HuggingFaceUtilityTests(unittest.TestCase):
    def setUp(self) -> None:
        hf_utils._manifest_cache = None
        hf_utils._manifest_cache_time = 0

    def tearDown(self) -> None:
        hf_utils._manifest_cache = None
        hf_utils._manifest_cache_time = 0

    def test_local_manifest_avoids_hub_request_regardless_of_age(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            expected = [{"id": "builtin_voice"}]
            manifest.write_text(json.dumps(expected), encoding="utf-8")

            with patch(
                "hf_utils.hf_hub_download_with_public_fallback"
            ) as download:
                result = hf_utils.fetch_builtin_manifest(root)

        self.assertEqual(result, expected)
        download.assert_not_called()

    def test_explicit_refresh_uses_pinned_revision_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps([{"id": "old"}]),
                encoding="utf-8",
            )
            remote = root / "remote.json"
            expected = [{"id": "new"}]
            remote.write_text(json.dumps(expected), encoding="utf-8")

            with patch(
                "hf_utils.hf_hub_download_with_public_fallback",
                return_value=str(remote),
            ) as download:
                result = hf_utils.fetch_builtin_manifest(root, refresh=True)

            persisted = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(result, expected)
        self.assertEqual(persisted, expected)
        download.assert_called_once_with(
            repo_id=hf_utils.BUILTIN_LORA_HF_REPO,
            filename="manifest.json",
            revision=hf_utils.BUILTIN_LORA_HF_REVISION,
            force_download=True,
        )

    def test_failed_explicit_refresh_falls_back_to_local_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            expected = [{"id": "cached"}]
            manifest.write_text(json.dumps(expected), encoding="utf-8")

            with patch(
                "hf_utils.hf_hub_download_with_public_fallback",
                side_effect=RuntimeError("offline"),
            ):
                result = hf_utils.fetch_builtin_manifest(root, refresh=True)

        self.assertEqual(result, expected)

    def test_adapter_download_uses_pinned_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cached = root / "cached"
            cached.write_bytes(b"fixture")
            with patch(
                "hf_utils.hf_hub_download_with_public_fallback",
                return_value=str(cached),
            ) as download:
                hf_utils.download_builtin_adapter("builtin_voice", root)

        self.assertTrue(download.call_args_list)
        self.assertTrue(
            all(
                call.kwargs["revision"] == hf_utils.BUILTIN_LORA_HF_REVISION
                for call in download.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()
