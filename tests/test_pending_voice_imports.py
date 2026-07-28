from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from pending_voice_imports import (
    PENDING_VOICE_IMPORT_SCHEMA_VERSION,
    consume_pending_voice_import_queue,
    sha256_file,
)
from voice_library import resolve_voice_library_assignment


def write_wav(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(value * 4800)


def wav_bytes(value: bytes, frames: int = 2400) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(value * frames)
    return buffer.getvalue()


class PendingVoiceImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.queue = self.base / "queue.json"
        self.romana = self.base / "sources" / "romana.wav"
        self.ten = self.base / "sources" / "ten.wav"
        write_wav(self.romana, b"\x01\x00")
        write_wav(self.ten, b"\x02\x00")
        (self.project / "voice_config.json").write_text(
            json.dumps(
                {
                    "ROMANA": {"type": "custom", "voice": "Aiden"},
                    "THE TENTH DOCTOR": {"type": "custom", "voice": "Aiden"},
                }
            ),
            encoding="utf-8",
        )
        (self.project / "chunks.json").write_text(
            json.dumps(
                [
                    {
                        "id": 1,
                        "speaker": "THE TENTH DOCTOR",
                        "status": "done",
                        "audio_state": "current",
                        "audio_path": "voicelines/ten.wav",
                    }
                ]
            ),
            encoding="utf-8",
        )
        write_wav(self.project / "voicelines" / "ten.wav", b"\x03\x00")
        self.payload = {
            "schema_version": PENDING_VOICE_IMPORT_SCHEMA_VERSION,
            "queue_id": "romana-ten-test",
            "target_project_id": "project_human_nature",
            "target_project_root": str(self.project.resolve()),
            "imports": [
                {
                    "speaker": "ROMANA",
                    "display_name": "Romana supplied clone",
                    "source_audio": str(self.romana.resolve()),
                    "source_sha256": sha256_file(self.romana),
                    "destination_filename": "romana_simple_clone.wav",
                    "transcript": "It was K-9 who traced you.",
                    "source_url": "https://www.youtube.com/watch?v=romana",
                },
                {
                    "speaker": "THE TENTH DOCTOR",
                    "display_name": "Tenth Doctor supplied clone",
                    "source_audio": str(self.ten.resolve()),
                    "source_sha256": sha256_file(self.ten),
                    "destination_filename": "tenth_doctor_simple_clone.wav",
                    "transcript": "Rose, it's me. Honestly, it's me.",
                    "source_url": "https://www.youtube.com/watch?v=ten",
                },
            ],
        }
        self.queue.write_text(json.dumps(self.payload), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_queue_applies_simple_clones_and_invalidates_old_audio(self) -> None:
        result = consume_pending_voice_import_queue(
            queue_path=self.queue,
            project_root=self.project,
            project_id="project_human_nature",
            at_utc="2026-07-27T23:00:00Z",
        )
        self.assertEqual(result["status"], "applied")
        self.assertFalse(self.queue.exists())
        config = json.loads((self.project / "voice_config.json").read_text(encoding="utf-8"))
        for speaker, filename in (
            ("ROMANA", "romana_simple_clone.wav"),
            ("THE TENTH DOCTOR", "tenth_doctor_simple_clone.wav"),
        ):
            self.assertEqual(config[speaker]["type"], "clone")
            self.assertEqual(config[speaker]["clone_backend"], "qwen3_base")
            self.assertEqual(config[speaker]["ref_audio"], f"clone_voices/{filename}")
            self.assertEqual(config[speaker]["seed"], "-1")
            self.assertTrue((self.project / "clone_voices" / filename).is_file())
        manifest = json.loads(
            (self.project / "clone_voices" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual({row["id"] for row in manifest}, {
            "romana_simple_clone", "tenth_doctor_simple_clone"
        })
        chunks = json.loads((self.project / "chunks.json").read_text(encoding="utf-8"))
        self.assertEqual(chunks[0]["status"], "pending")
        self.assertEqual(chunks[0]["audio_state"], "stale")
        self.assertIsNone(chunks[0]["audio_path"])
        receipt = Path(result["receipt_path"])
        self.assertTrue(receipt.is_file())
        receipt_value = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(receipt_value["mode"], "supplied_recording_clone")
        self.assertEqual(len(receipt_value["imports"]), 2)

    def test_missing_assignments_are_created_from_project_roster(self) -> None:
        (self.project / "voice_config.json").write_text("{}", encoding="utf-8")
        (self.project / "character_roster.json").write_text(
            json.dumps(
                {
                    "entries": [
                        {"canonical_name": "Romana", "display_name": "Romana"},
                        {
                            "canonical_name": "THE TENTH DOCTOR",
                            "display_name": "THE TENTH DOCTOR",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = consume_pending_voice_import_queue(
            queue_path=self.queue,
            project_root=self.project,
            project_id="project_human_nature",
            at_utc="2026-07-27T23:10:00Z",
        )
        self.assertEqual(result["status"], "applied")
        config = json.loads(
            (self.project / "voice_config.json").read_text(encoding="utf-8")
        )
        self.assertEqual(config["ROMANA"]["type"], "clone")
        self.assertEqual(config["THE TENTH DOCTOR"]["type"], "clone")

    def test_reusable_library_publication_hides_legacy_doctor_and_assigns_greeneye(self) -> None:
        reusable = self.base / "reusable"
        reusable.mkdir()
        write_wav(reusable / "clone_voices" / "seventh.wav", b"\x07\x00")
        (reusable / "voice_config.json").write_text(
            json.dumps(
                {
                    "THE DOCTOR": {
                        "type": "clone",
                        "clone_backend": "qwen3_base",
                        "ref_audio": "clone_voices/seventh.wav",
                        "ref_text": "The Doctor reference.",
                    },
                    "DOCTOR": {
                        "type": "clone",
                        "clone_backend": "qwen3_base",
                        "ref_audio": "clone_voices/seventh.wav",
                        "ref_text": "Old legacy Doctor reference.",
                    },
                }
            ),
            encoding="utf-8",
        )
        (self.project / "voice_config.json").write_text("{}", encoding="utf-8")
        (self.project / "character_roster.json").write_text(
            json.dumps(
                {
                    "entries": [
                        {"canonical_name": "ROMANA", "display_name": "Romana"},
                        {
                            "canonical_name": "THE TENTH DOCTOR",
                            "display_name": "THE TENTH DOCTOR",
                        },
                        {"canonical_name": "GREENEYE", "display_name": "GREENEYE"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.payload["publish_reusable"] = True
        self.payload["hide_reusable_configuration_keys"] = {
            "DOCTOR": "THE DOCTOR"
        }
        self.payload["imports"][0].update(
            {
                "reusable_configuration_key": "ROMANA",
                "assign_speakers": ["ROMANA"],
                "display_name": "Romana",
            }
        )
        self.payload["imports"][1].update(
            {
                "reusable_configuration_key": "THE TENTH DOCTOR",
                "assign_speakers": ["THE TENTH DOCTOR", "GREENEYE"],
                "display_name": "Tenth Doctor",
            }
        )
        self.queue.write_text(json.dumps(self.payload), encoding="utf-8")

        result = consume_pending_voice_import_queue(
            queue_path=self.queue,
            project_root=self.project,
            project_id="project_human_nature",
            reusable_library_root=reusable,
            at_utc="2026-07-27T23:20:00Z",
        )
        self.assertEqual(result["status"], "applied")
        self.assertEqual(
            result["published_reusable_voices"],
            ["ROMANA", "THE TENTH DOCTOR"],
        )
        reusable_config = json.loads(
            (reusable / "voice_config.json").read_text(encoding="utf-8")
        )
        self.assertEqual(reusable_config["DOCTOR"], {"alias_of": "THE DOCTOR"})
        self.assertEqual(reusable_config["ROMANA"]["type"], "clone")
        self.assertEqual(
            reusable_config["THE TENTH DOCTOR"]["clone_backend"],
            "qwen3_base",
        )
        project_config = json.loads(
            (self.project / "voice_config.json").read_text(encoding="utf-8")
        )
        tenth_voice_id = project_config["THE TENTH DOCTOR"]["library_voice_id"]
        self.assertEqual(project_config["GREENEYE"]["library_voice_id"], tenth_voice_id)
        self.assertEqual(
            project_config["GREENEYE"]["ref_audio"],
            "clone_voices/tenth_doctor_simple_clone.wav",
        )
        assignment = resolve_voice_library_assignment(
            voice_id=tenth_voice_id,
            reusable_root_dir=reusable,
        )
        self.assertEqual(assignment["name"], "Tenth Doctor")
        self.assertEqual(assignment["kind"], "reusable_clone")

    def test_remote_segments_build_dry_composite_and_replace_assignments(self) -> None:
        reusable = self.base / "reusable"
        reusable.mkdir()
        (reusable / "voice_config.json").write_text("{}", encoding="utf-8")
        (self.project / "voice_config.json").write_text("{}", encoding="utf-8")
        (self.project / "character_roster.json").write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "canonical_name": "THE TENTH DOCTOR",
                            "display_name": "THE TENTH DOCTOR",
                        },
                        {"canonical_name": "GREENEYE", "display_name": "GREENEYE"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        first = wav_bytes(b"\x11\x00")
        second = wav_bytes(b"\x22\x00")
        payload = {
            "schema_version": PENDING_VOICE_IMPORT_SCHEMA_VERSION,
            "queue_id": "dry-ten-test",
            "target_project_id": "project_human_nature",
            "target_project_root": str(self.project.resolve()),
            "publish_reusable": True,
            "imports": [
                {
                    "speaker": "THE TENTH DOCTOR",
                    "display_name": "Tenth Doctor",
                    "reusable_configuration_key": "THE TENTH DOCTOR",
                    "assign_speakers": ["THE TENTH DOCTOR", "GREENEYE"],
                    "source_segments": [
                        {
                            "url": "https://example.test/one.wav",
                            "sha256": hashlib.sha256(first).hexdigest(),
                        },
                        {
                            "url": "https://example.test/two.wav",
                            "sha256": hashlib.sha256(second).hexdigest(),
                        },
                    ],
                    "destination_filename": "tenth_doctor_dry.wav",
                    "transcript": "First line. Second line.",
                    "source_url": "https://example.test/source",
                }
            ],
        }
        self.queue.write_text(json.dumps(payload), encoding="utf-8")
        downloads = {
            "https://example.test/one.wav": first,
            "https://example.test/two.wav": second,
        }
        with patch(
            "pending_voice_imports._download_segment_bytes",
            side_effect=lambda url: downloads[url],
        ):
            result = consume_pending_voice_import_queue(
                queue_path=self.queue,
                project_root=self.project,
                project_id="project_human_nature",
                reusable_library_root=reusable,
                at_utc="2026-07-28T00:10:00Z",
            )
        self.assertEqual(result["status"], "applied")
        output = self.project / "clone_voices" / "tenth_doctor_dry.wav"
        self.assertTrue(output.is_file())
        with wave.open(str(output), "rb") as handle:
            self.assertEqual(handle.getframerate(), 24000)
            self.assertEqual(handle.getnchannels(), 1)
            self.assertEqual(handle.getsampwidth(), 2)
            self.assertGreater(handle.getnframes(), 4800)
        config = json.loads(
            (self.project / "voice_config.json").read_text(encoding="utf-8")
        )
        self.assertEqual(config["GREENEYE"]["type"], "clone")
        self.assertEqual(
            config["GREENEYE"]["ref_audio"],
            "clone_voices/tenth_doctor_dry.wav",
        )
        reusable_config = json.loads(
            (reusable / "voice_config.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            reusable_config["THE TENTH DOCTOR"]["ref_audio"],
            "clone_voices/tenth_doctor_dry.wav",
        )

    def test_queue_for_another_project_is_left_untouched(self) -> None:
        before = (self.project / "voice_config.json").read_bytes()
        result = consume_pending_voice_import_queue(
            queue_path=self.queue,
            project_root=self.project,
            project_id="project_other",
        )
        self.assertEqual(result["status"], "not_target")
        self.assertTrue(self.queue.is_file())
        self.assertEqual((self.project / "voice_config.json").read_bytes(), before)
        self.assertFalse((self.project / "clone_voices").exists())


if __name__ == "__main__":
    unittest.main()
