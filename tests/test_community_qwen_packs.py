from __future__ import annotations

import importlib
import json
import tempfile
import unittest
import wave
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

from tests.test_qwen_voice_packs import qvoice_bytes


def load_service():
    try:
        return importlib.import_module("community_qwen_packs")
    except ModuleNotFoundError as exc:
        raise AssertionError(
            "Alexandria has no transactional community-pack store yet."
        ) from exc


def load_candidate_service():
    try:
        return importlib.import_module("community_qwen_candidates")
    except ModuleNotFoundError as exc:
        raise AssertionError(
            "Alexandria has no curated community-candidate workflow yet."
        ) from exc


class CommunityQwenPackStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "incoming.qvoice"
        self.source.write_bytes(
            qvoice_bytes(reference_text=b"", reference_frames=0, flags=0b101)
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _preview(self, path: Path) -> None:
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(24_000)
            handle.writeframes(b"\x01\x00" * 2_400)

    def test_install_is_exact_idempotent_and_not_approved(self) -> None:
        module = load_service()

        first = module.install_qvoice_pack(
            source_path=self.source,
            reusable_root=self.root,
        )
        second = module.install_qvoice_pack(
            source_path=self.source,
            reusable_root=self.root,
        )

        self.assertEqual(first["pack_id"], second["pack_id"])
        self.assertEqual(first["state"], "review_required")
        self.assertFalse(first["production_supported"])
        self.assertIsNone(first["license_name"])
        stored = self.root / first["relative_path"]
        self.assertEqual(stored.read_bytes(), self.source.read_bytes())
        self.assertEqual(len(module.list_qwen_packs(reusable_root=self.root)), 1)

    def test_invalid_import_leaves_no_pack_or_manifest(self) -> None:
        module = load_service()
        self.source.write_bytes(b"not a qvoice")

        with self.assertRaises(module.CommunityQwenPackError) as caught:
            module.install_qvoice_pack(
                source_path=self.source,
                reusable_root=self.root,
            )

        self.assertEqual(caught.exception.code, "qvoice_invalid_magic")
        self.assertEqual(module.list_qwen_packs(reusable_root=self.root), [])
        self.assertFalse((self.root / "community_qwen_packs").exists())

    def test_icl_pack_is_classified_before_install(self) -> None:
        module = load_service()
        self.source.write_bytes(qvoice_bytes())

        with self.assertRaises(module.CommunityQwenPackError) as caught:
            module.install_qvoice_pack(
                source_path=self.source,
                reusable_root=self.root,
            )

        self.assertEqual(caught.exception.code, "qwen_pack_runtime_unsupported")
        self.assertEqual(module.list_qwen_packs(reusable_root=self.root), [])

    def test_approval_requires_the_exact_recorded_preview(self) -> None:
        module = load_service()
        installed = module.install_qvoice_pack(
            source_path=self.source,
            reusable_root=self.root,
        )

        with self.assertRaises(module.CommunityQwenPackError) as missing:
            module.approve_qvoice_pack(
                pack_id=installed["pack_id"],
                expected_preview_fingerprint="missing",
                reusable_root=self.root,
            )
        self.assertEqual(missing.exception.code, "qwen_pack_preview_required")

        preview = self.root / "preview.wav"
        self._preview(preview)
        reviewed = module.record_qvoice_preview(
            pack_id=installed["pack_id"],
            preview_path=preview,
            persistent_description="An older English storyteller.",
            direction="Warm, amused, and conversational.",
            reusable_root=self.root,
        )

        with self.assertRaises(module.CommunityQwenPackError) as stale:
            module.approve_qvoice_pack(
                pack_id=installed["pack_id"],
                expected_preview_fingerprint="wrong",
                reusable_root=self.root,
            )
        self.assertEqual(stale.exception.code, "qwen_pack_preview_changed")

        approved = module.approve_qvoice_pack(
            pack_id=installed["pack_id"],
            expected_preview_fingerprint=reviewed["preview_fingerprint"],
            reusable_root=self.root,
        )

        self.assertEqual(approved["state"], "approved")
        self.assertTrue(approved["production_supported"])
        self.assertEqual(
            approved["approval_fingerprint"],
            reviewed["preview_fingerprint"],
        )

        stored_preview = self.root / reviewed["preview"]
        stored_preview.write_bytes(b"changed-after-review")
        with self.assertRaises(module.CommunityQwenPackError) as changed:
            module.resolve_qvoice_preview(
                item=reviewed,
                reusable_root=self.root,
            )
        self.assertEqual(changed.exception.code, "qwen_pack_preview_changed")

    def test_peft_directory_import_links_source_without_copying_weights(self) -> None:
        module = load_service()
        bundle = self.root / "peft-source"
        bundle.mkdir()
        (bundle / "adapter_config.json").write_text(
            json.dumps({"peft_type": "LORA", "r": 2, "lora_alpha": 4}),
            encoding="utf-8",
        )
        (bundle / "adapter_model.safetensors").write_bytes(b"adapter-weights")
        (bundle / "speaker_embedding.safetensors").write_bytes(b"speaker")
        (bundle / "tts_config.json").write_text(
            json.dumps({
                "tts_model_type": "custom_voice",
                "tts_model_size": "1b7",
                "talker_config": {"spk_id": {"reader": 7}},
            }),
            encoding="utf-8",
        )

        installed = module.install_community_qwen_pack(
            source_path=bundle,
            reusable_root=self.root,
        )

        self.assertEqual(installed["family"], "peft_speaker_bundle")
        self.assertEqual(installed["storage_mode"], "linked_source")
        descriptor = self.root / installed["relative_path"]
        payload = json.loads(descriptor.read_text(encoding="utf-8"))
        self.assertEqual(payload["source_path"], str(bundle.resolve()))
        self.assertFalse((descriptor.parent / "adapter_model.safetensors").exists())
        self.assertLess(descriptor.stat().st_size, 20_000)

        resolved, path = module.resolve_community_qwen_pack(
            pack_id=installed["pack_id"],
            reusable_root=self.root,
        )
        self.assertEqual(resolved["pack_id"], installed["pack_id"])
        self.assertEqual(path, descriptor.resolve())

        (bundle / "adapter_model.safetensors").write_bytes(b"changed")
        with self.assertRaises(module.CommunityQwenPackError) as changed:
            module.resolve_community_qwen_pack(
                pack_id=installed["pack_id"],
                reusable_root=self.root,
            )
        self.assertEqual(changed.exception.code, "qwen_pack_runtime_invalid")

    def test_full_checkpoint_conversion_respects_disk_guard_and_skips_source_copy(self) -> None:
        module = load_service()
        checkpoint = self.root / "checkpoint-source"
        checkpoint.mkdir()
        (checkpoint / "config.json").write_text(
            json.dumps({
                "tts_model_type": "custom_voice",
                "tts_model_size": "1b7",
                "talker_config": {"spk_id": {"reader": 7}},
            }),
            encoding="utf-8",
        )
        (checkpoint / "model.safetensors").write_bytes(b"pytorch-source")

        allowed_plan = {
            "q_bits": 8,
            "source_weight_bytes": 14,
            "estimated_output_bytes": 7,
            "reserved_free_bytes": 16 * 1024**3,
            "required_free_bytes": 16 * 1024**3 + 7,
            "available_free_bytes": 64 * 1024**3,
            "allowed": True,
        }

        def fake_convert(*, source_dir, output_dir, q_bits):
            output = Path(output_dir)
            output.mkdir(parents=True)
            (output / "model.safetensors").write_bytes(b"mlx")
            (output / "config.json").write_text("{}", encoding="utf-8")
            return {
                "status": "converted",
                "output_dir": str(output),
                "size_bytes": 5,
                "plan": allowed_plan,
                "hardlinked_files": 0,
                "copied_files": 0,
            }

        with mock.patch.object(module, "conversion_plan", return_value=allowed_plan), mock.patch.object(
            module,
            "convert_full_checkpoint_low_disk",
            side_effect=fake_convert,
        ):
            installed = module.install_community_qwen_pack(
                source_path=checkpoint,
                reusable_root=self.root,
            )

        self.assertEqual(installed["storage_mode"], "converted_mlx")
        descriptor = self.root / installed["relative_path"]
        self.assertTrue((descriptor.parent / "mlx_model" / "model.safetensors").is_file())
        self.assertFalse((descriptor.parent / "model.safetensors").exists())
        self.assertEqual(
            json.loads(descriptor.read_text(encoding="utf-8"))["model_path"],
            "mlx_model",
        )

        blocked_plan = {**allowed_plan, "available_free_bytes": 1, "allowed": False}
        second = self.root / "checkpoint-blocked"
        shutil_source = second
        shutil_source.mkdir()
        (second / "config.json").write_text(
            (checkpoint / "config.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (second / "model.safetensors").write_bytes(b"different-source")
        with mock.patch.object(module, "conversion_plan", return_value=blocked_plan):
            with self.assertRaises(module.CommunityQwenPackError) as blocked:
                module.install_community_qwen_pack(
                    source_path=second,
                    reusable_root=self.root,
                )
        self.assertEqual(blocked.exception.code, "qwen_pack_disk_space_guard")

    def test_curated_catalog_exposes_pinned_unverified_candidate_and_peak_space(self) -> None:
        module = load_candidate_service()
        with mock.patch.object(
            module,
            "model_cache_status",
            return_value={"cached": False, "state": "missing"},
        ), mock.patch.object(
            module.shutil,
            "disk_usage",
            return_value=SimpleNamespace(free=64 * 1024**3),
        ):
            catalog = module.curated_qwen_candidate_catalog(
                reusable_root=self.root,
            )

        self.assertEqual(len(catalog), 1)
        candidate = catalog[0]
        self.assertEqual(candidate["key"], "scrappylabs_narrator")
        self.assertEqual(candidate["repo_id"], "scrappylabs/narrator-tts")
        self.assertEqual(
            candidate["evidence_status"],
            "publisher_claimed_unverified",
        )
        self.assertFalse(candidate["installed"])
        self.assertTrue(candidate["conversion_estimates"]["8"]["allowed"])
        self.assertGreater(
            candidate["conversion_estimates"]["8"]["required_peak_free_bytes"],
            candidate["source_size_bytes"],
        )

    def test_curated_install_pins_review_state_and_removes_new_source_cache(self) -> None:
        module = load_candidate_service()
        from community_qwen_pack_store import write_manifest

        source = self.root / "downloaded-snapshot"
        source.mkdir()
        cache_root = self.root / "hf-cache"
        spec = module.model_spec("pytorch_scrappylabs_narrator")
        repository_cache = cache_root / spec.cache_name
        repository_cache.mkdir(parents=True)
        (repository_cache / "downloaded.bin").write_bytes(b"temporary-source")

        def fake_install(**kwargs):
            entry = {
                "pack_id": "qcustom_narrator",
                "name": "downloaded-snapshot",
                "state": "review_required",
                "production_supported": False,
                "relative_path": "community_qwen_packs/qcustom_narrator/pack.json",
                "family": "full_custom_voice_checkpoint",
                "runtime": "mlx_checkpoint",
                "preview": None,
                "preview_fingerprint": None,
                "approval_fingerprint": None,
            }
            write_manifest(self.root, {entry["pack_id"]: entry})
            return dict(entry)

        inspection = {
            "family": "full_custom_voice_checkpoint",
            "speakers": ["narrator"],
        }
        with mock.patch.object(
            module,
            "model_cache_status",
            return_value={"cached": False, "state": "missing"},
        ), mock.patch.object(
            module.shutil,
            "disk_usage",
            return_value=SimpleNamespace(free=64 * 1024**3),
        ), mock.patch.object(
            module,
            "download_or_repair_model",
        ) as download, mock.patch.object(
            module,
            "resolve_model_path",
            return_value=source,
        ), mock.patch.object(
            module,
            "inspect_qwen_pack_path",
            return_value=inspection,
        ), mock.patch.object(
            module,
            "install_community_qwen_pack",
            side_effect=fake_install,
        ), mock.patch.object(
            module,
            "shared_huggingface_cache_dir",
            return_value=cache_root,
        ):
            installed = module.install_curated_qwen_candidate(
                candidate_key="scrappylabs_narrator",
                reusable_root=self.root,
                q_bits=8,
            )

        download.assert_called_once()
        self.assertEqual(installed["name"], "ScrappyLabs Narrator")
        self.assertEqual(installed["catalog_key"], "scrappylabs_narrator")
        self.assertEqual(installed["license_name"], "Apache-2.0")
        self.assertIn("lost you", installed["preview_text_default"])
        self.assertIn("grief-stricken", installed["preview_direction"])
        self.assertEqual(installed["state"], "review_required")
        self.assertFalse(installed["production_supported"])
        self.assertFalse(repository_cache.exists())
        cleanup = installed["candidate_install"]["source_cache_cleanup"]
        self.assertTrue(cleanup["removed"])
        self.assertGreater(cleanup["reclaimed_bytes"], 0)

    def test_removal_deletes_only_the_selected_pack(self) -> None:
        module = load_service()
        installed = module.install_qvoice_pack(
            source_path=self.source,
            reusable_root=self.root,
        )

        removed = module.remove_qvoice_pack(
            pack_id=installed["pack_id"],
            reusable_root=self.root,
        )

        self.assertEqual(removed["status"], "removed")
        self.assertEqual(module.list_qwen_packs(reusable_root=self.root), [])
        self.assertFalse((self.root / installed["relative_path"]).exists())


if __name__ == "__main__":
    unittest.main()
