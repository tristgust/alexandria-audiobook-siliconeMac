from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from audio_artifacts import audio_binding_fingerprint
from backend_render_plan import (
    BackendRenderPlanError,
    apply_backend_render_plan,
    application_record,
    chunks_fingerprint,
    inspect_backend_render_plan,
    normalize_backend_render_plan,
    task_guidance,
)
from fish_inline_cues import text_sha256
from generation_state import fingerprint_value


class BackendRenderPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.chunks = [
            {
                "id": 0,
                "speaker": "NARRATOR",
                "text": "I thought I was ready. I wasn't.",
                "instruct": "Controlled, then shaken.",
                "status": "done",
                "audio_state": "current",
                "audio_path": "voicelines/one.mp3",
            },
            {
                "id": 1,
                "speaker": "THE DOCTOR",
                "text": "Hanky panky!",
                "instruct": "A delighted shout.",
                "status": "done",
                "audio_state": "current",
                "audio_path": "voicelines/two.mp3",
            },
        ]
        (self.root / "chunks.json").write_text(
            json.dumps(self.chunks),
            encoding="utf-8",
        )
        self.script = [
            {
                "speaker": chunk["speaker"],
                "text": chunk["text"],
                "instruct": chunk["instruct"],
            }
            for chunk in self.chunks
        ]
        (self.root / "annotated_script.json").write_text(
            json.dumps(self.script),
            encoding="utf-8",
        )
        self.script_fingerprint = fingerprint_value(self.script)
        self.chunks_fingerprint = chunks_fingerprint(self.chunks)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def plan(self) -> dict:
        return {
            "schema_version": 1,
            "script_fingerprint": self.script_fingerprint,
            "chunks_fingerprint": self.chunks_fingerprint,
            "entries": [
                {
                    "index": 0,
                    "chunk_id": "chunk:0",
                    "speaker": "NARRATOR",
                    "text_sha256": text_sha256(self.chunks[0]["text"]),
                    "qwen_instruction": (
                        "Begin controlled, then let the second sentence fracture with disbelief."
                    ),
                    "fish_direction": "controlled, then voice breaking",
                    "fish_cues": [
                        {
                            "anchor": "before_phrase",
                            "phrase": "I wasn't",
                            "occurrence": 1,
                            "tag": "voice breaking",
                            "kind": "delivery",
                        }
                    ],
                    "warnings": [],
                },
                {
                    "index": 1,
                    "chunk_id": "chunk:1",
                    "speaker": "THE DOCTOR",
                    "text_sha256": text_sha256(self.chunks[1]["text"]),
                    "qwen_instruction": "A sudden delighted shout with a complete exclamatory finish.",
                    "fish_direction": "sudden delighted shout",
                    "fish_cues": [
                        {
                            "anchor": "start",
                            "tag": "shouting",
                            "kind": "delivery",
                        }
                    ],
                    "warnings": [],
                },
            ],
            "warnings": [],
        }

    def test_normalizes_complete_fingerprint_bound_plan(self) -> None:
        normalized = normalize_backend_render_plan(
            self.plan(),
            chunks=self.chunks,
            expected_script_fingerprint=self.script_fingerprint,
            expected_chunks_fingerprint=self.chunks_fingerprint,
        )
        self.assertEqual(len(normalized["entries"]), 2)
        self.assertEqual(
            normalized["entries"][0]["fish_cues"][0]["phrase"],
            "I wasn't",
        )

    def test_rejects_missing_or_stale_chunk_coverage(self) -> None:
        plan = self.plan()
        plan["entries"].pop()
        with self.assertRaisesRegex(
            BackendRenderPlanError,
            "cover every non-empty synthesis chunk",
        ):
            normalize_backend_render_plan(
                plan,
                chunks=self.chunks,
                expected_script_fingerprint=self.script_fingerprint,
                expected_chunks_fingerprint=self.chunks_fingerprint,
            )

    def test_rejects_fish_phrase_anchor_not_in_canonical_text(self) -> None:
        plan = self.plan()
        plan["entries"][0]["fish_cues"][0]["phrase"] = "not present"
        with self.assertRaises(BackendRenderPlanError) as caught:
            normalize_backend_render_plan(
                plan,
                chunks=self.chunks,
                expected_script_fingerprint=self.script_fingerprint,
                expected_chunks_fingerprint=self.chunks_fingerprint,
            )
        self.assertEqual(caught.exception.code, "fish_inline_phrase_not_found")

    def test_apply_is_migration_on_touch_and_does_not_stale_current_audio(self) -> None:
        voice_config = {
            "NARRATOR": {"type": "clone", "clone_backend": "qwen3_instruction_controlled"},
            "THE DOCTOR": {"type": "clone", "clone_backend": "qwen3_instruction_controlled"},
        }
        before = audio_binding_fingerprint(
            chunk=self.chunks[0],
            resolved_speaker="NARRATOR",
            voice_config=voice_config,
            synthesis_config={},
        )
        applied = apply_backend_render_plan(
            root_dir=self.root,
            value=self.plan(),
            expected_script_fingerprint=self.script_fingerprint,
            expected_chunks_fingerprint=self.chunks_fingerprint,
            at_utc="2026-07-29T18:00:00Z",
        )
        chunks = json.loads((self.root / "chunks.json").read_text(encoding="utf-8"))
        after_unapplied = audio_binding_fingerprint(
            chunk=chunks[0],
            resolved_speaker="NARRATOR",
            voice_config=voice_config,
            synthesis_config={},
        )
        self.assertEqual(before, after_unapplied)
        self.assertEqual(chunks[0]["audio_state"], "current")
        self.assertNotIn("backend_render_plan_applied", chunks[0])
        self.assertEqual(applied["fish_inline_chunk_count"], 2)
        self.assertTrue((self.root / "backend_render_plan.json").is_file())

        generation_chunk = dict(chunks[0])
        generation_chunk["backend_render_plan_binding_enabled"] = True
        after_bound = audio_binding_fingerprint(
            chunk=generation_chunk,
            resolved_speaker="NARRATOR",
            voice_config=voice_config,
            synthesis_config={},
        )
        self.assertNotEqual(before, after_bound)
        self.assertEqual(
            application_record(generation_chunk)["plan_fingerprint"],
            applied["plan_fingerprint"],
        )
        status = inspect_backend_render_plan(self.root)
        self.assertTrue(status["current"])
        self.assertEqual(status["chunk_count"], 2)
        self.assertEqual(status["applied_to_audio_count"], 0)

    def test_audio_metadata_changes_do_not_stale_delivery_plan(self) -> None:
        apply_backend_render_plan(
            root_dir=self.root,
            value=self.plan(),
            expected_script_fingerprint=self.script_fingerprint,
            expected_chunks_fingerprint=self.chunks_fingerprint,
            at_utc="2026-07-29T18:00:00Z",
        )
        chunks = json.loads((self.root / "chunks.json").read_text(encoding="utf-8"))
        chunks[0]["audio_sha256"] = "b" * 64
        chunks[0]["generated_at_utc"] = "2026-07-29T18:10:00Z"
        (self.root / "chunks.json").write_text(json.dumps(chunks), encoding="utf-8")
        status = inspect_backend_render_plan(self.root)
        self.assertTrue(status["current"])

    def test_guidance_separates_qwen_and_fish_best_practices(self) -> None:
        guidance = task_guidance()
        self.assertIn("Qwen3-TTS", guidance["qwen"]["target"])
        self.assertIn("Fish Audio", guidance["fish"]["target"])
        self.assertIn("community_caveats", guidance["fish"])
        self.assertIn("before_phrase", guidance["fish"]["cue_contract"]["anchors"])


if __name__ == "__main__":
    unittest.main()
