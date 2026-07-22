from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from audio_artifacts import audio_binding_fingerprint
from produce_aggregate import (
    ProduceAggregateError,
    build_produce_aggregate,
    build_produce_generation_plan,
    inspect_produce_project,
)


class ProduceAggregateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        self.config = {"tts": {"language": "English", "parallel_workers": 2}}
        (self.root / "app" / "config.json").write_text(
            json.dumps(self.config), encoding="utf-8"
        )
        self.voice_config = {
            "NARRATOR": {"type": "custom", "voice": "Ryan"},
            "DOCTOR": {"type": "custom", "voice": "Aiden"},
        }
        self.cast = {
            "characters": [
                self._character(
                    "character_narrator", "Narrator", "NARRATOR", valid=True
                ),
                self._character(
                    "character_doctor", "The Doctor", "DOCTOR", valid=True
                ),
                self._character(
                    "character_missing", "Missing Voice", "MISSING", valid=False
                ),
            ]
        }
        (self.root / "voice_config.json").write_text(
            json.dumps(self.voice_config), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _character(
        character_id: str,
        name: str,
        label: str,
        *,
        valid: bool,
    ) -> dict:
        return {
            "character_id": character_id,
            "display_name": name,
            "required_for_completion": True,
            "script_connection": {
                "resolved_script_voice_label": label,
            },
            "voice": {
                "configuration_key": label,
                "selected_production_method": "custom",
                "valid": valid,
                "blockers": [] if valid else [{"code": "cast_voice_selection_missing"}],
            },
        }

    def _chunk(
        self,
        index: int,
        *,
        speaker: str = "NARRATOR",
        status: str = "pending",
        audio_state: str | None = None,
        text: str | None = None,
    ) -> dict:
        value = {
            "id": index,
            "speaker": speaker,
            "text": text or f"Line {index}.",
            "instruct": "Calm and clear.",
            "status": status,
            "audio_path": None,
        }
        if audio_state is not None:
            value["audio_state"] = audio_state
        return value

    def _install_audio(
        self,
        chunk: dict,
        *,
        content: bytes | None = None,
    ) -> Path:
        content = content or (b"audio-" + str(chunk["id"]).encode("ascii"))
        path = self.root / "voicelines" / f"line-{chunk['id']}.mp3"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        resolved = chunk["speaker"]
        expected = audio_binding_fingerprint(
            chunk=chunk,
            resolved_speaker=resolved,
            voice_config=self.voice_config,
            synthesis_config={"language": "English", "parallel_workers": 2},
        )
        chunk.update(
            {
                "status": "done",
                "audio_state": "current",
                "audio_path": path.relative_to(self.root).as_posix(),
                "audio_fingerprint": expected,
                "audio_sha256": hashlib.sha256(content).hexdigest(),
                "audio_size_bytes": len(content),
                "audio_duration_ms": 1200,
                "audio_format": "mp3",
                "stale_audio_path": None,
            }
        )
        return path

    def _aggregate(
        self,
        chunks: list[dict],
        *,
        audio_validity: dict | None = None,
        process: dict | None = None,
        selected_chunk_id: str | None = None,
        filter_key: str = "all",
        search: str | None = None,
    ) -> dict:
        return build_produce_aggregate(
            root_dir=self.root,
            chunks=chunks,
            voice_config=self.voice_config,
            config=self.config,
            cast=self.cast,
            audio_validity=audio_validity or {},
            process=process or {},
            selected_chunk_id=selected_chunk_id,
            filter_key=filter_key,
            search=search,
        )

    def test_all_required_row_states_are_derived_from_authoritative_evidence(self) -> None:
        ready = self._chunk(0)
        generating = self._chunk(1, status="generating", audio_state="generating")
        stale = self._chunk(2)
        stale["stale_audio_path"] = "voicelines/old.mp3"
        failed = self._chunk(3, status="error", audio_state="failed")
        missing_voice = self._chunk(4, speaker="MISSING")
        current = self._chunk(5)
        self._install_audio(current)
        needs_review = self._chunk(6)
        self._install_audio(needs_review)
        needs_review["review_required"] = True
        needs_listening = self._chunk(7)
        self._install_audio(needs_listening)
        needs_listening["listening_required"] = True
        needs_listening["listening_state"] = "pending"

        aggregate = self._aggregate(
            [
                ready,
                generating,
                stale,
                failed,
                missing_voice,
                current,
                needs_review,
                needs_listening,
            ]
        )
        states = {
            item["chunk_id"]: item["state"] for item in aggregate["chunks"]
        }
        self.assertEqual(
            states,
            {
                "chunk:0": "ready",
                "chunk:1": "generating",
                "chunk:2": "stale",
                "chunk:3": "failed",
                "chunk:4": "missing_voice",
                "chunk:5": "current",
                "chunk:6": "needs_review",
                "chunk:7": "needs_listening",
            },
        )
        self.assertEqual(aggregate["summary"]["needs_generation_count"], 2)
        self.assertEqual(aggregate["summary"]["needs_review_count"], 2)
        self.assertEqual(aggregate["summary"]["missing_voice_count"], 1)
        self.assertEqual(
            aggregate["primary_action"]["id"],
            "generate_missing_stale_audio",
        )
        self.assertTrue(aggregate["secondary_actions"][0]["destructive"])

    def test_import_audio_validity_marks_rebuilt_pending_chunk_stale(self) -> None:
        aggregate = self._aggregate(
            [self._chunk(12)],
            audio_validity={
                "stale": True,
                "invalidated_chunks": [
                    {
                        "chunk_id": 12,
                        "audio_path": "voicelines/old.mp3",
                        "reason": "annotated_script_replaced",
                    }
                ],
            },
        )
        row = aggregate["chunks"][0]
        self.assertEqual(row["state"], "stale")
        self.assertTrue(row["audio"]["stale_audio_available"])

    def test_voice_change_and_text_change_make_current_audio_stale(self) -> None:
        chunk = self._chunk(0)
        self._install_audio(chunk)
        chunk["text"] = "Changed text."
        aggregate = self._aggregate([chunk])
        self.assertEqual(aggregate["chunks"][0]["state"], "stale")
        self.assertEqual(
            aggregate["chunks"][0]["reason"],
            "audio_fingerprint_mismatch",
        )

        chunk = self._chunk(1, speaker="NARRATOR")
        self._install_audio(chunk)
        changed_config = json.loads(json.dumps(self.voice_config))
        changed_config["NARRATOR"]["voice"] = "Aiden"
        aggregate = build_produce_aggregate(
            root_dir=self.root,
            chunks=[chunk],
            voice_config=changed_config,
            config=self.config,
            cast=self.cast,
        )
        self.assertEqual(aggregate["chunks"][0]["state"], "stale")

    def test_hash_invalid_and_incomplete_artifact_metadata_are_failed(self) -> None:
        changed = self._chunk(0)
        path = self._install_audio(changed)
        path.write_bytes(b"changed")
        incomplete = self._chunk(1)
        self._install_audio(incomplete)
        incomplete.pop("audio_duration_ms")

        aggregate = self._aggregate([changed, incomplete])
        by_id = {item["chunk_id"]: item for item in aggregate["chunks"]}
        self.assertEqual(by_id["chunk:0"]["reason"], "audio_hash_mismatch")
        self.assertEqual(by_id["chunk:1"]["reason"], "audio_metadata_incomplete")
        self.assertEqual(aggregate["counts"]["failed"], 2)

    def test_current_audio_is_playable_without_exposing_stale_paths(self) -> None:
        current = self._chunk(0)
        self._install_audio(current)
        stale = self._chunk(1)
        stale["stale_audio_path"] = "voicelines/private-old.mp3"
        aggregate = self._aggregate([current, stale])
        current_row, stale_row = aggregate["chunks"]
        self.assertEqual(current_row["audio"]["url"], "/voicelines/line-0.mp3")
        self.assertIsNone(stale_row["audio"]["url"])
        self.assertNotIn("stale_audio_path", stale_row["audio"])

    def test_selection_filter_and_search_preserve_selected_inspector(self) -> None:
        rows = [
            self._chunk(0, speaker="NARRATOR", text="Opening narration."),
            self._chunk(1, speaker="DOCTOR", text="Run now."),
        ]
        selected = "chunk:1"
        aggregate = self._aggregate(
            rows,
            selected_chunk_id=selected,
            filter_key="missing_voice",
            search="opening",
        )
        self.assertEqual(aggregate["visible_chunk_count"], 0)
        self.assertEqual(aggregate["selected_chunk_id"], selected)
        self.assertEqual(aggregate["selected_chunk"]["speaker"], "DOCTOR")
        self.assertFalse(aggregate["selection_visible"])

    def test_default_plan_generates_only_ready_and_stale(self) -> None:
        ready = self._chunk(0)
        stale = self._chunk(1)
        stale["stale_audio_path"] = "voicelines/old.mp3"
        failed = self._chunk(2, status="error", audio_state="failed")
        current = self._chunk(3)
        self._install_audio(current)
        missing_voice = self._chunk(4, speaker="MISSING")
        aggregate = self._aggregate(
            [ready, stale, failed, current, missing_voice]
        )
        plan = build_produce_generation_plan(aggregate)
        self.assertEqual(plan["indices"], [0, 1])
        self.assertEqual(plan["chunk_ids"], ["chunk:0", "chunk:1"])
        self.assertEqual(plan["preserved_current_count"], 1)
        self.assertFalse(plan["destructive"])
        self.assertTrue(plan["safe_to_execute"])
        self.assertTrue(
            any(item["code"] == "produce_voice_blockers_remain" for item in plan["blockers"])
        )

    def test_retry_selected_and_regenerate_all_plans_are_explicit(self) -> None:
        ready = self._chunk(0)
        failed = self._chunk(1, status="error", audio_state="failed")
        current = self._chunk(2)
        self._install_audio(current)
        aggregate = self._aggregate([ready, failed, current])

        retry = build_produce_generation_plan(aggregate, mode="retry_failed")
        self.assertEqual(retry["indices"], [1])
        selected = build_produce_generation_plan(
            aggregate,
            mode="selected",
            selected_chunk_ids=["chunk:2"],
        )
        self.assertEqual(selected["indices"], [2])
        all_plan = build_produce_generation_plan(
            aggregate,
            mode="regenerate_all",
        )
        self.assertEqual(all_plan["indices"], [0, 1, 2])
        self.assertTrue(all_plan["destructive"])

    def test_running_process_blocks_new_plan_and_exposes_bounded_queue(self) -> None:
        aggregate = self._aggregate(
            [self._chunk(index) for index in range(250)],
            process={
                "running": True,
                "cancel": False,
                "operation_id": "audio_1",
                "queued_chunk_ids": [f"chunk:{index}" for index in range(250)],
                "total_count": 250,
                "completed_count": 10,
                "failed_count": 2,
                "worker_limit": 2,
            },
        )
        self.assertTrue(aggregate["process"]["queued_chunk_ids_truncated"])
        self.assertEqual(len(aggregate["process"]["queued_chunk_ids"]), 200)
        plan = build_produce_generation_plan(aggregate)
        self.assertFalse(plan["safe_to_execute"])
        self.assertEqual(
            plan["blockers"][0]["code"],
            "produce_generation_already_running",
        )

    def test_inspect_project_is_file_pure_and_does_not_decode_audio(self) -> None:
        chunk = self._chunk(0)
        self._install_audio(chunk)
        (self.root / "chunks.json").write_text(
            json.dumps([chunk]), encoding="utf-8"
        )
        (self.root / "character_roster.json").write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "id": "character_narrator",
                            "canonical_name": "Narrator",
                            "display_name": "Narrator",
                            "speaking_status": "narrator",
                            "resolution_status": "resolved",
                            "aliases": ["NARRATOR"],
                            "titles": [],
                            "nicknames": [],
                            "sample_lines": ["Line 0."],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (self.root / "annotated_script.json").write_text(
            json.dumps([chunk]), encoding="utf-8"
        )
        protected = {
            path: path.read_bytes()
            for path in (
                self.root / "chunks.json",
                self.root / "voice_config.json",
                self.root / "character_roster.json",
                self.root / "annotated_script.json",
            )
        }
        aggregate = inspect_produce_project(
            root_dir=self.root,
            cast=self.cast,
        )
        self.assertEqual(aggregate["state"], "complete")
        self.assertEqual(
            {path: path.read_bytes() for path in protected},
            protected,
        )

    def test_invalid_filter_duplicate_id_and_stale_selection_fail_closed(self) -> None:
        with self.assertRaises(ProduceAggregateError) as filter_error:
            self._aggregate([self._chunk(0)], filter_key="bogus")
        self.assertEqual(filter_error.exception.code, "produce_filter_invalid")
        with self.assertRaises(ProduceAggregateError) as duplicate_error:
            self._aggregate([self._chunk(0), self._chunk(0)])
        self.assertEqual(duplicate_error.exception.code, "produce_chunk_id_duplicate")
        with self.assertRaises(ProduceAggregateError) as selection_error:
            self._aggregate([self._chunk(0)], selected_chunk_id="chunk:missing")
        self.assertEqual(selection_error.exception.code, "produce_chunk_not_found")


if __name__ == "__main__":
    unittest.main()
