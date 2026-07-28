from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from adaptive_speech import generate_adaptive_custom_speech
from audio_processing import (
    AudioProcessingError,
    GeneratedSpeechTooLongError,
)
from mlx_backend import MLXBackend


class _DurationQwen:
    def __init__(self, durations: dict[str, float]) -> None:
        self.durations = durations
        self.requests: list[str] = []

    def generate(self, text: str, **_kwargs):
        self.requests.append(text)
        return [self.durations.get(text, max(1.0, len(text) / 12.0))]


class _SeedAwareDurationQwen:
    def __init__(self, target: str, failure: str, seed_state: dict[str, int | None]) -> None:
        self.target = target
        self.failure = failure
        self.seed_state = seed_state
        self.requests: list[str] = []

    def generate(self, text: str, **kwargs):
        self.requests.append(text)
        seed_value = self.seed_state["value"]
        if self.target in text and (seed_value is None or seed_value % 2 == 0):
            duration = 0.32 if self.failure == "short" else int(kwargs["max_tokens"]) / 12.5
        else:
            duration = max(0.8, len(text) / 14.0)
        return [duration]


def _duration_waveform(_model, results) -> tuple[np.ndarray, int]:
    duration = float(results[0])
    sample_rate = 100
    timeline = np.arange(round(duration * sample_rate), dtype=np.float32) / sample_rate
    return 0.1 * np.sin(2.0 * np.pi * 4.0 * timeline), sample_rate


class AdaptiveCustomSpeechTests(unittest.TestCase):
    LIVE_TEXT = "Oops. Terribly glad to meet you, must be going."
    LIVE_TIMOTHY_TEXT = (
        "I'm... I'm being... It's the rules, I know, and I should just put up with it, but... "
        "the Captains, they beat me every day. I only wanted to ask, is it ever going to stop? "
        "Does it stop when I'm in the second year?"
    )
    LIVE_GEORGE_TEXT = (
        "Well, I was on the way here to give you a bit of a lecture, something mad and racy about "
        "Boadicea, I heard. But I stopped to have a glance at the cricket team selection and heard "
        "you giving that strange Dean boy a wonderful talking-to. That's just the spirit! Tell me, "
        "would you be interested in helping out with the OTC?"
    )
    TIMOTHY_SEGMENTS = (
        "I'm... I'm being... It's the rules, I know, and I should just put up with it, but...",
        "the Captains, they beat me every day. I only wanted to ask, is it ever going to stop?",
        "Does it stop when I'm in the second year?",
    )
    GEORGE_SEGMENTS = (
        "Well, I was on the way here to give you a bit of a lecture, something mad and racy about",
        "Boadicea, I heard.",
        "But I stopped to have a glance at the cricket team selection and heard you giving that strange",
        "Dean boy a wonderful talking-to. That's just the spirit!",
        "Tell me, would you be interested in helping out with the OTC?",
    )

    def _assert_random_seeds_preserve_live_context(
        self,
        text: str,
        target: str,
        failure: str,
        expected_segments: tuple[str, ...],
    ) -> None:
        target_index = next(index for index, segment in enumerate(expected_segments) if target in segment)
        expected_requests = (
            list(expected_segments[:target_index])
            + [expected_segments[target_index]] * 2
            + list(expected_segments[target_index + 1 :])
        )
        seed_state: dict[str, int | None] = {"value": None}
        model = _SeedAwareDurationQwen(target, failure, seed_state)
        backend = MLXBackend()

        with (
            patch("mlx_backend.secrets.randbits", side_effect=range(100, 200)) as random_seed,
            patch(
                "mlx_backend.mx.random.seed",
                side_effect=lambda value: seed_state.__setitem__("value", value),
            ) as mlx_seed,
            patch.object(backend, "_model", return_value=model),
            patch.object(backend, "_collect_audio", side_effect=_duration_waveform),
            patch.object(backend, "_save") as save,
        ):
            result = backend.generate_custom(text, "Natural delivery.", "Vivian", "/tmp/unused.wav")

        self.assertTrue(result)
        self.assertEqual(model.requests, expected_requests)
        self.assertTrue(all(len(segment.split()) >= 2 for segment in model.requests))
        self.assertEqual(random_seed.call_count, len(expected_segments))
        self.assertEqual(mlx_seed.call_count, len(expected_requests))
        save.assert_called_once()

    def _assert_repeated_failure_uses_contextual_children(
        self,
        text: str,
        failures: dict[str, float],
        forbidden_leaf: str,
        expected_requests: list[str],
        expected_seeds: list[int],
    ) -> None:
        model = _DurationQwen(failures)
        backend = MLXBackend()
        with (
            patch("mlx_backend.secrets.randbits", side_effect=range(700, 800)),
            patch("mlx_backend.mx.random.seed") as seed_mock,
            patch.object(backend, "_model", return_value=model),
            patch.object(backend, "_collect_audio", side_effect=_duration_waveform),
            patch.object(backend, "_save") as save,
        ):
            result = backend.generate_custom(
                text, "Natural delivery.", "Vivian", "/tmp/unused.wav"
            )

        self.assertTrue(result)
        self.assertEqual(model.requests, expected_requests)
        self.assertNotIn(forbidden_leaf, model.requests)
        self.assertTrue(all(len(segment.split()) >= 2 for segment in model.requests))
        self.assertEqual(
            [call.args[0] for call in seed_mock.call_args_list], expected_seeds
        )
        save.assert_called_once()

    def test_chunk_864_too_short_leaf_keeps_timothy_context(self) -> None:
        self.assertEqual(len(self.LIVE_TIMOTHY_TEXT), 212)
        self._assert_random_seeds_preserve_live_context(
            self.LIVE_TIMOTHY_TEXT,
            "I'm...",
            "short",
            self.TIMOTHY_SEGMENTS,
        )

    def test_chunk_888_exact_cap_leaf_keeps_george_context(self) -> None:
        self.assertEqual(len(self.LIVE_GEORGE_TEXT), 321)
        self._assert_random_seeds_preserve_live_context(
            self.LIVE_GEORGE_TEXT,
            "strange",
            "long",
            self.GEORGE_SEGMENTS,
        )

    def test_chunk_864_both_seed_attempts_keep_sentence_context(self) -> None:
        first, second, third = self.TIMOTHY_SEGMENTS
        self._assert_repeated_failure_uses_contextual_children(
            self.LIVE_TIMOTHY_TEXT,
            {first: 0.32, "I'm...": 0.32},
            "I'm...",
            [
                first,
                first,
                "I'm... I'm being...",
                "It's the rules, I know, and I should just put up with it, but...",
                second,
                third,
            ],
            [700, 701, 701, 702, 703, 704],
        )

    def test_chunk_888_both_seed_attempts_keep_balanced_context(self) -> None:
        first, second, target, fourth, fifth = self.GEORGE_SEGMENTS
        self._assert_repeated_failure_uses_contextual_children(
            self.LIVE_GEORGE_TEXT,
            {target: 19.76, "strange": 8.08},
            "strange",
            [
                first,
                second,
                target,
                target,
                "But I stopped to have a glance at the cricket",
                "team selection and heard you giving that strange",
                fourth,
                fifth,
            ],
            [700, 701, 702, 703, 703, 704, 705, 706],
        )

    def test_balanced_fallback_never_strands_a_one_word_child(self) -> None:
        requests: list[str] = []

        def fail(segment: str):
            requests.append(segment)
            raise GeneratedSpeechTooLongError("synthetic exact-cap exhaustion")

        with self.assertRaises(GeneratedSpeechTooLongError):
            generate_adaptive_custom_speech("alpha beta gamma", fail)

        self.assertEqual(requests, ["alpha beta gamma"])

    def test_repeated_too_short_keeps_one_word_sentence_with_context(self) -> None:
        self.assertEqual(len(self.LIVE_TEXT), 47)
        model = _DurationQwen({self.LIVE_TEXT: 0.81, "Oops.": 0.81})
        backend = MLXBackend()

        with (
            patch.object(backend, "_model", return_value=model),
            patch.object(backend, "_collect_audio", side_effect=_duration_waveform),
            patch.object(backend, "_save") as save,
        ):
            result = backend.generate_custom(
                self.LIVE_TEXT,
                "Friendly but hurried.",
                "Vivian",
                "/tmp/unused.wav",
            )

        self.assertTrue(result)
        self.assertEqual(
            model.requests,
            [
                self.LIVE_TEXT,
                self.LIVE_TEXT,
                "Oops. Terribly glad to",
                "meet you, must be going.",
            ],
        )
        save.assert_called_once()

    def test_successful_short_heading_is_generated_unchanged(self) -> None:
        model = _DurationQwen({"Chapter 1": 0.80})
        backend = MLXBackend()
        with (
            patch.object(backend, "_model", return_value=model),
            patch.object(backend, "_collect_audio", side_effect=_duration_waveform),
            patch.object(backend, "_save") as save,
        ):
            self.assertTrue(
                backend.generate_custom(
                    "Chapter 1", "Neutral delivery.", "Vivian", "/tmp/unused.wav"
                )
            )

        self.assertEqual(model.requests, ["Chapter 1"])
        save.assert_called_once()

    def test_unsplittable_too_short_remains_fail_closed(self) -> None:
        model = _DurationQwen({"Help": 0.10})
        backend = MLXBackend()

        with (
            patch.object(backend, "_model", return_value=model),
            patch.object(backend, "_collect_audio", side_effect=_duration_waveform),
            patch.object(backend, "_save") as save,
            self.assertRaisesRegex(AudioProcessingError, "too short"),
        ):
            backend.generate_custom("Help", "Urgent.", "Vivian", "/tmp/unused.wav")

        self.assertEqual(model.requests, ["Help", "Help"])
        save.assert_not_called()

    def test_silent_multiword_result_does_not_enter_adaptive_split(self) -> None:
        text = "Please help me."
        model = _DurationQwen({})
        backend = MLXBackend()

        with (
            patch.object(backend, "_model", return_value=model),
            patch.object(backend, "_collect_audio", return_value=(np.zeros(100), 100)),
            patch.object(backend, "_save") as save,
            self.assertRaisesRegex(AudioProcessingError, "silent"),
        ):
            backend.generate_custom(text, "Urgent.", "Vivian", "/tmp/unused.wav")

        self.assertEqual(model.requests, [text, text])
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
