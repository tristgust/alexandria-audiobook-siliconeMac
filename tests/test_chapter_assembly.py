from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from audio_artifacts import validate_audio_file
from chapter_assembly import (
    ChapterAssemblyError,
    build_chapters,
    chapter_rows,
    create_processed_rendition,
    transition_context,
)


def write_tone(path: Path, *, duration_ms: int = 2000, rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = round(duration_ms * rate / 1000)
    samples = bytearray()
    for index in range(frames):
        value = int(2400 * ((index % 64) / 32.0 - 1.0))
        samples.extend(value.to_bytes(2, byteorder="little", signed=True))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(samples))


class ChapterAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chunks = [
            {
                "chunk_id": "chunk:intro",
                "speaker": "NARRATOR",
                "text": "A short introduction.",
                "duration_ms": 1000,
                "pause_after_ms": 300,
                "audio": {"available": True, "url": "/intro.mp3"},
            },
            {
                "chunk_id": "chunk:chapter-one",
                "speaker": "NARRATOR",
                "text": "Chapter One",
                "duration_ms": 800,
                "pause_after_ms": 500,
                "audio": {"available": True, "url": "/one.mp3"},
            },
            {
                "chunk_id": "chunk:line-one",
                "speaker": "CLARA",
                "text": "The first line remains exact.",
                "duration_ms": 1200,
                "pause_after_ms": 700,
                "audio": {"available": True, "url": "/line.mp3"},
            },
            {
                "chunk_id": "chunk:chapter-two",
                "speaker": "NARRATOR",
                "text": "Chapter Two",
                "duration_ms": 900,
                "audio": {"available": True, "url": "/two.mp3"},
            },
        ]

    def test_smart_chapters_and_transition_context_share_exact_timing(self) -> None:
        chapters = build_chapters(self.chunks, mode="smart")
        self.assertEqual(
            [(item["name"], item["start_ms"], item["end_ms"]) for item in chapters],
            [
                ("Introduction", 0, 1000),
                ("Chapter One", 1300, 3800),
                ("Chapter Two", 4500, 5400),
            ],
        )
        context = transition_context(
            self.chunks,
            selected_chunk_id="chunk:line-one",
        )
        self.assertEqual(context["chapter"]["name"], "Chapter One")
        self.assertEqual(context["previous"]["chunk_id"], "chunk:chapter-one")
        self.assertEqual(context["next"]["chunk_id"], "chunk:chapter-two")
        self.assertEqual(context["transition_before_ms"], 500)
        self.assertEqual(context["transition_after_ms"], 700)
        self.assertFalse(context["is_chapter_start"])
        self.assertTrue(context["is_chapter_end"])

    def test_source_order_fingerprint_changes_only_when_canonical_order_changes(self) -> None:
        before = transition_context(
            self.chunks,
            selected_chunk_id="chunk:line-one",
        )["source_order_fingerprint"]
        pause_changed = [dict(item) for item in self.chunks]
        pause_changed[2]["pause_after_ms"] = 1250
        after_pause = transition_context(
            pause_changed,
            selected_chunk_id="chunk:line-one",
        )["source_order_fingerprint"]
        reordered = [self.chunks[0], self.chunks[2], self.chunks[1], self.chunks[3]]
        after_order = transition_context(
            reordered,
            selected_chunk_id="chunk:line-one",
        )["source_order_fingerprint"]
        self.assertEqual(before, after_pause)
        self.assertNotEqual(before, after_order)

    def test_rows_use_configured_default_pauses_when_no_override_exists(self) -> None:
        values = [dict(item) for item in self.chunks[:2]]
        values[0].pop("pause_after_ms")
        values[1].pop("pause_after_ms")
        rows = chapter_rows(
            values,
            config={
                "tts": {
                    "pause_between_speakers_ms": 610,
                    "pause_same_speaker_ms": 230,
                }
            },
        )
        self.assertEqual(rows[0]["pause_after_ms"], 230)
        self.assertEqual(rows[1]["pause_after_ms"], 0)

    def test_trim_creates_valid_child_candidate_without_touching_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.wav"
            output = root / "trimmed.wav"
            write_tone(source, duration_ms=2400)
            source_bytes = source.read_bytes()
            processing = create_processed_rendition(
                source_audio_path=source,
                output_path=output,
                operation="trim_edges",
                settings={"trim_start_ms": 200, "trim_end_ms": 300},
            )
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertEqual(processing["operation"], "final_listen_trim_edges")
            self.assertEqual(processing["source_duration_ms"], 2400)
            self.assertGreaterEqual(processing["output_duration_ms"], 1890)
            self.assertLessEqual(processing["output_duration_ms"], 1910)
            self.assertEqual(validate_audio_file(output)["sha256"], processing["output_sha256"])

    def test_internal_split_inserts_one_bounded_pause_without_splitting_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.wav"
            output = root / "split.wav"
            write_tone(source, duration_ms=2000)
            processing = create_processed_rendition(
                source_audio_path=source,
                output_path=output,
                operation="split_with_pause",
                settings={"split_at_ms": 900, "pause_ms": 450},
            )
            self.assertEqual(processing["operation"], "final_listen_split_with_pause")
            self.assertEqual(processing["settings"]["split_at_ms"], 900)
            self.assertEqual(processing["settings"]["pause_ms"], 450)
            self.assertGreaterEqual(processing["output_duration_ms"], 2440)
            self.assertLessEqual(processing["output_duration_ms"], 2460)

    def test_invalid_processing_fails_before_output_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.wav"
            output = root / "invalid.wav"
            write_tone(source, duration_ms=1000)
            with self.assertRaisesRegex(ChapterAssemblyError, "complete spoken"):
                create_processed_rendition(
                    source_audio_path=source,
                    output_path=output,
                    operation="trim_edges",
                    settings={"trim_start_ms": 800, "trim_end_ms": 100},
                )
            self.assertFalse(output.exists())
            with self.assertRaisesRegex(ChapterAssemblyError, "between 50 and 950"):
                create_processed_rendition(
                    source_audio_path=source,
                    output_path=output,
                    operation="split_with_pause",
                    settings={"split_at_ms": 980, "pause_ms": 300},
                )
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
