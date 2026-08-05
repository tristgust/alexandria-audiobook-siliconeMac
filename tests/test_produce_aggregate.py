from __future__ import annotations

import copy
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

    def test_chunk_generation_provenance_prefers_recorded_model_over_inference(self) -> None:
        chunk = self._chunk(9)
        chunk["generation_provenance"] = {
            "schema_version": 1,
            "source": "generation",
            "recorded": True,
            "runtime": "mlx-audio",
            "model_id": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
            "model_revision": "revision-1",
            "base_model_id": None,
            "voice_type": "clone",
            "voice_method": "qwen3_instruction_controlled",
            "detail": None,
        }
        chunk["generated_at_utc"] = "2026-07-29T05:00:00Z"
        self._install_audio(chunk)
        row = self._aggregate([chunk])["chunks"][0]
        self.assertTrue(row["generation_provenance"]["recorded"])
        self.assertEqual(
            row["generation_provenance"]["model_id"],
            "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
        )
        self.assertEqual(row["generated_at_utc"], "2026-07-29T05:00:00Z")

    def test_legacy_chunk_exposes_explicit_current_config_inference(self) -> None:
        chunk = self._chunk(10)
        self._install_audio(chunk)
        provenance = self._aggregate([chunk])["chunks"][0][
            "generation_provenance"
        ]
        self.assertFalse(provenance["recorded"])
        self.assertEqual(provenance["source"], "current_voice_config")
        self.assertTrue(provenance["model_id"])

    def test_legacy_fish_metadata_is_reported_as_recorded_provenance(self) -> None:
        chunk = self._chunk(11)
        chunk.update(
            {
                "cloud_provider": "fish_s21_cloud",
                "cloud_model": "s2.1-pro-free",
                "cloud_prompt_variant": "rich_tag",
            }
        )
        self._install_audio(chunk)
        provenance = self._aggregate([chunk])["chunks"][0][
            "generation_provenance"
        ]
        self.assertTrue(provenance["recorded"])
        self.assertEqual(provenance["runtime"], "fish-audio-cloud")
        self.assertEqual(provenance["model_id"], "s2.1-pro-free")

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
        self.assertEqual(
            current_row["audio"]["url"],
            f"/voicelines/line-0.mp3?v={current['audio_sha256']}",
        )
        self.assertIsNone(stale_row["audio"]["url"])
        self.assertNotIn("stale_audio_path", stale_row["audio"])

    def test_final_listen_uses_export_chapter_clock_and_preserves_source_order(self) -> None:
        heading = self._chunk(0, text="Chapter One")
        line = self._chunk(1, text="The exact line follows the heading.")
        self._install_audio(heading)
        self._install_audio(line)
        heading["pause_after"] = 900
        aggregate = self._aggregate(
            [heading, line],
            selected_chunk_id="chunk:0",
        )
        final_listen = aggregate["final_listen"]
        selected = aggregate["selected_chunk"]
        self.assertEqual(final_listen["chapter_count"], 1)
        self.assertEqual(final_listen["chapters"][0]["name"], "Chapter One")
        self.assertEqual(final_listen["chapters"][0]["start_ms"], 0)
        self.assertEqual(final_listen["chapters"][0]["end_ms"], 3300)
        self.assertEqual(
            selected["final_listen"]["transition"]["next"]["chunk_id"],
            "chunk:1",
        )
        self.assertEqual(
            selected["final_listen"]["transition"]["transition_after_ms"],
            900,
        )
        self.assertEqual(
            selected["final_listen"]["source_order_fingerprint"],
            final_listen["source_order_fingerprint"],
        )
        pause_changed = copy.deepcopy([heading, line])
        pause_changed[0]["pause_after"] = 1400
        changed = self._aggregate(
            pause_changed,
            selected_chunk_id="chunk:0",
        )
        self.assertEqual(
            changed["final_listen"]["source_order_fingerprint"],
            final_listen["source_order_fingerprint"],
        )
        self.assertEqual(changed["final_listen"]["chapters"][0]["end_ms"], 3800)
        self.assertNotEqual(
            changed["fingerprints"]["final_listen"],
            aggregate["fingerprints"]["final_listen"],
        )

    def test_fish_generation_details_are_exposed_for_listening_review(self) -> None:
        current = self._chunk(0)
        current.update(
            {
                "cloud_provider": "fish_s21_cloud",
                "cloud_model": "s2.1-pro-free",
                "cloud_style_route": "expressive",
                "cloud_prompt_variant": "full_alexandria_tag",
                "cloud_candidate_count": 2,
                "cloud_text_validation_passed": True,
                "cloud_terminal_text_validation_passed": True,
                "cloud_word_error_rate": 0.0,
                "cloud_identity_score": 0.98,
                "cloud_delivery_score": 0.72,
                "cloud_instruction_delivery_score": 0.84,
                "cloud_quality_score": 1.0,
                "cloud_selection_score": 0.91,
                "fish_route_mode": "hybrid",
                "fish_route_reason": "style:expressive",
                "fish_hybrid_attempted": True,
                "fish_hybrid_fallback_used": False,
            }
        )
        self._install_audio(current)
        row = self._aggregate([current])["chunks"][0]
        fish = row["fish_generation"]
        self.assertEqual(fish["model"], "s2.1-pro-free")
        self.assertEqual(fish["prompt_variant"], "full_alexandria_tag")
        self.assertEqual(fish["instruction_delivery_score"], 0.84)
        self.assertFalse(fish["fallback_used"])

    def test_implausibly_long_audio_is_stale_even_when_hash_and_binding_match(self) -> None:
        runaway = self._chunk(0, text="Oh.")
        self._install_audio(runaway)
        runaway["audio_duration_ms"] = 327_680

        aggregate = self._aggregate([runaway])
        row = aggregate["chunks"][0]

        self.assertEqual(row["state"], "stale")
        self.assertEqual(row["reason"], "audio_duration_excessive")
        self.assertEqual(aggregate["counts"]["stale"], 1)
        self.assertEqual(
            row["blockers"][0]["code"],
            "produce_audio_duration_excessive",
        )

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

    def test_ready_only_plan_selects_exactly_ready_chunks(self) -> None:
        ready = self._chunk(0)
        stale = self._chunk(1)
        stale["stale_audio_path"] = "voicelines/old.mp3"
        failed = self._chunk(2, status="error", audio_state="failed")
        current = self._chunk(3)
        self._install_audio(current)
        generating = self._chunk(
            4,
            status="generating",
            audio_state="generating",
        )
        missing_voice = self._chunk(5, speaker="MISSING")
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
                stale,
                failed,
                current,
                generating,
                missing_voice,
                needs_review,
                needs_listening,
            ]
        )

        plan = build_produce_generation_plan(aggregate, mode="ready_only")

        self.assertEqual(plan["mode"], "ready_only")
        self.assertEqual(plan["indices"], [0])
        self.assertEqual(plan["chunk_ids"], ["chunk:0"])
        self.assertEqual(plan["state_counts"]["ready"], 1)
        self.assertFalse(plan["destructive"])
        self.assertTrue(plan["safe_to_execute"])

        empty_plan = build_produce_generation_plan(
            self._aggregate(
                [
                    stale,
                    failed,
                    current,
                    generating,
                    missing_voice,
                    needs_review,
                    needs_listening,
                ]
            ),
            mode="ready_only",
        )
        self.assertEqual(empty_plan["indices"], [])
        self.assertFalse(empty_plan["safe_to_execute"])
        self.assertEqual(
            empty_plan["empty_reason"],
            "No current chunks match this generation mode.",
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
        selected_failed = build_produce_generation_plan(
            aggregate,
            mode="selected",
            selected_chunk_ids=["chunk:1"],
        )
        self.assertEqual(selected_failed["indices"], [1])
        self.assertEqual(selected_failed["state_counts"]["failed"], 1)
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
                "cancelled_count": 1,
                "active_file_fractions": {
                    "chunk:12": 0.25,
                    "chunk:13": 0.75,
                    "invalid": "not-a-number",
                },
                "worker_limit": 2,
            },
        )
        self.assertTrue(aggregate["process"]["queued_chunk_ids_truncated"])
        self.assertEqual(len(aggregate["process"]["queued_chunk_ids"]), 200)
        self.assertEqual(aggregate["process"]["terminal_count"], 13)
        self.assertEqual(aggregate["process"]["active_file_count"], 2)
        self.assertEqual(aggregate["process"]["active_fraction_sum"], 1.0)
        self.assertEqual(aggregate["process"]["composite_fraction"], 0.056)
        self.assertEqual(aggregate["process"]["composite_percent"], 5.6)
        plan = build_produce_generation_plan(aggregate)
        self.assertFalse(plan["safe_to_execute"])
        self.assertEqual(
            plan["blockers"][0]["code"],
            "produce_generation_already_running",
        )

    def test_composite_progress_clamps_terminal_and_active_overflow(self) -> None:
        aggregate = self._aggregate(
            [self._chunk(index) for index in range(3)],
            process={
                "running": True,
                "total_count": 3,
                "completed_count": 2,
                "failed_count": 2,
                "cancelled_count": 1,
                "active_file_fractions": {
                    "chunk:0": -1,
                    "chunk:1": 4,
                    "chunk:2": "Infinity",
                },
            },
        )
        process = aggregate["process"]
        self.assertEqual(process["terminal_count"], 3)
        self.assertEqual(
            process["active_file_fractions"],
            {"chunk:0": 0.0, "chunk:1": 1.0},
        )
        self.assertEqual(process["active_fraction_sum"], 0.0)
        self.assertEqual(process["composite_fraction"], 1.0)
        self.assertEqual(process["composite_percent"], 100.0)

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

    def test_managed_project_uses_explicit_application_config_for_audio_binding(self) -> None:
        chunk = self._chunk(0)
        self._install_audio(chunk)
        (self.root / "chunks.json").write_text(
            json.dumps([chunk]), encoding="utf-8"
        )
        application_config = self.root / "application-config.json"
        application_config.write_text(json.dumps(self.config), encoding="utf-8")
        (self.root / "app" / "config.json").unlink()

        aggregate = inspect_produce_project(
            root_dir=self.root,
            config_path=application_config,
            cast=self.cast,
        )
        self.assertEqual(aggregate["state"], "complete")
        self.assertEqual(aggregate["counts"]["current"], 1)
        self.assertEqual(aggregate["chunks"][0]["state"], "current")

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
