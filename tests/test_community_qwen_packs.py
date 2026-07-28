from __future__ import annotations

import importlib
import tempfile
import unittest
import wave
from pathlib import Path

from tests.test_qwen_voice_packs import qvoice_bytes


def load_service():
    try:
        return importlib.import_module("community_qwen_packs")
    except ModuleNotFoundError as exc:
        raise AssertionError(
            "Alexandria has no transactional community-pack store yet."
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
