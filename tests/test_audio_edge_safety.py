from __future__ import annotations

import math
import tempfile
import unittest
from array import array
from pathlib import Path
from unittest.mock import patch

from pydub import AudioSegment

from audio_edge_safety import ensure_click_safe_fade_in
from audio_artifacts import install_generated_audio
from project import ProjectManager
from tts import combine_audio_with_pauses


SAMPLE_RATE = 24_000
RAMP_MS = 3
SAFE_ENDPOINT = 328


def constant_segment(*, duration_ms: int, amplitude: int) -> AudioSegment:
    sample_count = duration_ms * SAMPLE_RATE // 1000
    samples = array("h", [amplitude]) * sample_count
    return AudioSegment(
        data=samples.tobytes(),
        sample_width=2,
        frame_rate=SAMPLE_RATE,
        channels=1,
    )


def cosine_segment(
    *,
    duration_ms: int,
    amplitude: int,
    frequency_hz: int,
) -> AudioSegment:
    sample_count = duration_ms * SAMPLE_RATE // 1000
    samples = array(
        "h",
        (
            round(amplitude * math.cos(2 * math.pi * frequency_hz * index / SAMPLE_RATE))
            for index in range(sample_count)
        ),
    )
    return AudioSegment(
        data=samples.tobytes(),
        sample_width=2,
        frame_rate=SAMPLE_RATE,
        channels=1,
    )


def sample_at(segment: AudioSegment, milliseconds: int) -> int:
    samples = segment.get_array_of_samples()
    return int(samples[milliseconds * SAMPLE_RATE // 1000])


def write_wav(segment: AudioSegment, path: Path) -> None:
    with path.open("wb") as output:
        segment.export(output, format="wav")


def read_wav(path: Path) -> AudioSegment:
    with path.open("rb") as source:
        return AudioSegment.from_file(source, format="wav")


def read_mp3(path: Path) -> AudioSegment:
    return AudioSegment.from_file(path, format="mp3")


class AudioEdgeSafetyTests(unittest.TestCase):
    def test_preserves_duration_and_post_edge_samples_when_installing_generated_audio(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "abrupt-source.wav"
            source_segment = constant_segment(duration_ms=100, amplitude=16_000)
            write_wav(source_segment, source)

            result = install_generated_audio(
                root_dir=root,
                voicelines_dir=root / "voicelines",
                source_audio_path=source,
                filename_base="line",
                binding_fingerprint="f" * 64,
                prefer_mp3=False,
            )

            installed = read_wav(root / result["audio_path"])
            self.assertEqual(len(installed), len(source_segment))
            self.assertEqual(
                sample_at(installed, RAMP_MS + 1),
                sample_at(source_segment, RAMP_MS + 1),
            )
            self.assertEqual(
                installed.get_array_of_samples()[-1],
                source_segment.get_array_of_samples()[-1],
            )

    def test_preserves_authored_pause_and_trailing_release_when_combining_lines(
        self,
    ) -> None:
        first = constant_segment(duration_ms=100, amplitude=16_000)
        second = constant_segment(duration_ms=100, amplitude=-12_000)

        combined = combine_audio_with_pauses(
            [first, second],
            ["NARRATOR", "NARRATOR"],
            pause_overrides=[37, None],
        )

        self.assertEqual(len(combined), 237)
        self.assertEqual(sample_at(combined, 99), 16_000)
        self.assertEqual(sample_at(combined, 100), 0)
        self.assertEqual(sample_at(combined, 136), 0)
        self.assertEqual(sample_at(combined, 137 + RAMP_MS + 1), -12_000)

    def test_preserves_existing_safe_ramp_when_combining_installed_line(self) -> None:
        installed = constant_segment(
            duration_ms=100,
            amplitude=16_000,
        ).fade_in(RAMP_MS)

        combined = combine_audio_with_pauses([installed], ["NARRATOR"])

        self.assertEqual(combined.raw_data, installed.raw_data)

    def test_uses_one_percent_boundary_and_exact_three_ms_ramp(self) -> None:
        just_below_boundary = constant_segment(duration_ms=100, amplitude=327)
        self.assertEqual(
            ensure_click_safe_fade_in(just_below_boundary).raw_data,
            just_below_boundary.raw_data,
        )

        at_boundary = constant_segment(duration_ms=100, amplitude=328)
        ramped = ensure_click_safe_fade_in(at_boundary)
        ramp_end_frame = RAMP_MS * SAMPLE_RATE // 1000
        ramped_samples = ramped.get_array_of_samples()
        self.assertLess(ramped_samples[ramp_end_frame - 1], 328)
        self.assertEqual(ramped_samples[ramp_end_frame], 328)
        self.assertEqual(len(ramped), len(at_boundary))

    def test_preserves_installed_mp3_ramp_when_combining_for_final_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "abrupt-source.wav"
            write_wav(
                cosine_segment(
                    duration_ms=1_000,
                    amplitude=16_000,
                    frequency_hz=400,
                ),
                source,
            )

            result = install_generated_audio(
                root_dir=root,
                voicelines_dir=root / "voicelines",
                source_audio_path=source,
                filename_base="line",
                binding_fingerprint="f" * 64,
                prefer_mp3=True,
            )

            self.assertEqual(result["audio_format"], "mp3")
            installed = read_mp3(root / result["audio_path"])
            self.assertLessEqual(
                abs(installed.get_array_of_samples()[0]),
                SAFE_ENDPOINT,
            )
            combined = combine_audio_with_pauses([installed], ["NARRATOR"])
            self.assertEqual(combined.raw_data, installed.raw_data)

            manager = ProjectManager(root)
            manager._load_chunks_with_audio = lambda: [
                (
                    {"speaker": "NARRATOR", "text": "Chapter One"},
                    installed,
                )
            ]
            manager._load_pause_defaults = lambda: (500, 250)
            assembled: list[bytes] = []

            def recording_combiner(*args, **kwargs):
                exported = combine_audio_with_pauses(*args, **kwargs)
                assembled.append(exported.raw_data)
                return exported

            with patch(
                "project.combine_audio_with_pauses",
                side_effect=recording_combiner,
            ):
                mp3_path = root / "final.mp3"
                m4b_path = root / "final.m4b"
                mp3_success, mp3_message = manager.merge_audio(mp3_path)
                m4b_success, m4b_message = manager.merge_m4b(
                    metadata={"title": "Edge safety"},
                    output_path=m4b_path,
                )

            self.assertTrue(mp3_success, mp3_message)
            self.assertTrue(m4b_success, m4b_message)
            self.assertEqual(assembled, [installed.raw_data, installed.raw_data])
            self.assertGreater(len(AudioSegment.from_file(mp3_path)), 0)
            self.assertGreater(len(AudioSegment.from_file(m4b_path)), 0)

    def test_falls_back_to_wav_when_mp3_reopens_an_abrupt_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "codec-sensitive.wav"
            write_wav(
                cosine_segment(
                    duration_ms=1_000,
                    amplitude=16_000,
                    frequency_hz=800,
                ),
                source,
            )

            result = install_generated_audio(
                root_dir=root,
                voicelines_dir=root / "voicelines",
                source_audio_path=source,
                filename_base="line",
                binding_fingerprint="f" * 64,
                prefer_mp3=True,
            )

            self.assertEqual(result["audio_format"], "wav")
            installed = read_wav(root / result["audio_path"])
            self.assertLessEqual(
                abs(installed.get_array_of_samples()[0]),
                SAFE_ENDPOINT,
            )

    def test_softens_abrupt_start_without_shortening_when_installing_generated_audio(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "abrupt-source.wav"
            source_segment = constant_segment(duration_ms=100, amplitude=16_000)
            write_wav(source_segment, source)

            result = install_generated_audio(
                root_dir=root,
                voicelines_dir=root / "voicelines",
                source_audio_path=source,
                filename_base="line",
                binding_fingerprint="f" * 64,
                prefer_mp3=False,
            )

            installed = read_wav(root / result["audio_path"])
            self.assertEqual(installed.get_array_of_samples()[0], 0)
            self.assertEqual(len(installed), len(source_segment))
            self.assertEqual(
                sample_at(installed, RAMP_MS + 1),
                sample_at(source_segment, RAMP_MS + 1),
            )

    def test_softens_each_abrupt_start_without_changing_authored_pause_when_combining(
        self,
    ) -> None:
        first = constant_segment(duration_ms=100, amplitude=16_000)
        second = constant_segment(duration_ms=100, amplitude=-12_000)

        combined = combine_audio_with_pauses(
            [first, second],
            ["NARRATOR", "NARRATOR"],
            pause_overrides=[37, None],
        )

        samples = combined.get_array_of_samples()
        second_start = 137 * SAMPLE_RATE // 1000
        self.assertLessEqual(abs(samples[0]), SAFE_ENDPOINT)
        self.assertLessEqual(abs(samples[second_start]), SAFE_ENDPOINT)
        self.assertEqual(len(combined), 237)
        self.assertEqual(sample_at(combined, 99), 16_000)
        self.assertEqual(sample_at(combined, 100), 0)
        self.assertEqual(sample_at(combined, 136), 0)
        self.assertEqual(sample_at(combined, 137 + RAMP_MS + 1), -12_000)


if __name__ == "__main__":
    unittest.main()
