from __future__ import annotations

import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from fish_cloud_tts import (
    AudioFeatures,
    CandidateAssessment,
    FishCloudBackend,
    FishCloudClient,
    FishCloudError,
    build_prompt_route,
    classify_delivery,
    instruction_delivery_score,
    repeat_selection_score,
    terminal_text_matches,
    word_error_rate,
)


def wav_bytes(*, frequency: float = 180.0, duration: float = 1.2) -> bytes:
    sample_rate = 24000
    samples = np.arange(int(sample_rate * duration), dtype=np.float64)
    audio = np.sin(2 * np.pi * frequency * samples / sample_rate) * 0.16
    pcm = np.asarray(audio * 32767, dtype="<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, status_code=200, *, payload=None, content=b"", text=""):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.text = text
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("No response remains")
        return self.responses.pop(0)


class SequenceClient:
    model = "s2.1-pro-free"

    def __init__(self, audio_payloads):
        self.audio_payloads = list(audio_payloads)
        self.prompts = []
        self.created = 0

    def list_owned_models(self, title):
        return [{"_id": "private-model", "title": title, "visibility": "private", "state": "trained"}]

    def create_private_model(self, **kwargs):
        self.created += 1
        return {"_id": "created-model", "visibility": "private", "state": "created"}

    def synthesize(self, *, text, reference_id, settings):
        self.prompts.append(text)
        return self.audio_payloads.pop(0)

    def transcribe(self, audio_path, *, language="en"):
        return ""


class SequenceTranscriber:
    def __init__(self, values):
        self.values = list(values)

    def __call__(self, _path):
        return self.values.pop(0)


class SequenceSimilarity:
    def __init__(self, values):
        self.values = list(values)

    def score(self, _reference, _candidate):
        return self.values.pop(0), "mlx_qwen"


FEATURES = AudioFeatures(
    duration_seconds=2.0,
    words_per_second=2.5,
    rms_mean=0.08,
    rms_cv=0.4,
    pitch_median_hz=180.0,
    pitch_cv=0.3,
    spectral_centroid_hz=1700.0,
    silence_ratio=0.08,
    clipping_ratio=0.0,
)


class FishPromptRoutingTests(unittest.TestCase):
    def test_style_router_uses_results_backed_primary_prompt(self):
        neutral = build_prompt_route("Hello there.", "Natural and neutral.")
        joy = build_prompt_route("You came back!", "excited")
        grief = build_prompt_route("I am sorry.", "Deep grief, close to breaking.")
        anger = build_prompt_route("You knew better.", "angry")
        sarcasm = build_prompt_route("Wonderful.", "Dry sarcasm and disbelief.")
        fear = build_prompt_route("Run.", "Scared, with uneven breath and danger nearby.")

        self.assertEqual(neutral.style, "neutral")
        self.assertEqual(neutral.variants[0].key, "full_alexandria_tag")
        self.assertEqual(joy.style, "joy")
        self.assertEqual(joy.variants[0].text, "[excited] You came back!")
        self.assertEqual(grief.variants[0].key, "full_alexandria_tag")
        self.assertEqual(anger.style, "anger")
        self.assertEqual(anger.variants[0].text, "[angry] You knew better.")
        self.assertEqual(sarcasm.variants[0].key, "full_alexandria_tag")
        self.assertEqual(fear.variants[0].key, "full_alexandria_tag")
        self.assertTrue(fear.difficult)
        self.assertIn("[", fear.variants[1].text)

    def test_every_emotive_variant_preserves_the_authored_direction(self):
        instruction = "Hushed and hesitant; pause before the final admission."
        route = build_prompt_route("I did know.", instruction)
        self.assertGreaterEqual(len(route.variants), 2)
        for variant in route.variants:
            self.assertIn(instruction, variant.text)

    def test_instruction_score_rewards_requested_slow_quiet_delivery(self):
        reference = AudioFeatures(
            duration_seconds=3.0,
            words_per_second=3.0,
            rms_mean=0.10,
            rms_cv=0.40,
            pitch_median_hz=180.0,
            pitch_cv=0.30,
            spectral_centroid_hz=1700.0,
            silence_ratio=0.08,
            clipping_ratio=0.0,
        )
        matching = AudioFeatures(
            duration_seconds=4.0,
            words_per_second=2.35,
            rms_mean=0.07,
            rms_cv=0.38,
            pitch_median_hz=175.0,
            pitch_cv=0.28,
            spectral_centroid_hz=1650.0,
            silence_ratio=0.16,
            clipping_ratio=0.0,
        )
        contrary = AudioFeatures(
            duration_seconds=2.2,
            words_per_second=4.0,
            rms_mean=0.14,
            rms_cv=0.70,
            pitch_median_hz=210.0,
            pitch_cv=0.70,
            spectral_centroid_hz=2200.0,
            silence_ratio=0.02,
            clipping_ratio=0.0,
        )
        instruction = "Quiet and measured; pause before the final phrase."
        self.assertGreater(
            instruction_delivery_score(instruction, matching, reference),
            instruction_delivery_score(instruction, contrary, reference),
        )

    def test_classifier_covers_panic_and_sorrow(self):
        self.assertEqual(classify_delivery("Panicked and breathless"), "fear")
        self.assertEqual(classify_delivery("Excited and delighted"), "joy")
        self.assertEqual(classify_delivery("Mournful, carrying loss"), "grief")
        self.assertEqual(classify_delivery("Angry and accusatory"), "anger")
        self.assertEqual(classify_delivery("Understated ironic disbelief"), "sarcasm")
        self.assertEqual(
            classify_delivery("Natural and conversational, but increasingly anxious."),
            "fear",
        )
        self.assertEqual(
            classify_delivery("Natural, warm, and lightly enigmatic."),
            "expressive",
        )
        self.assertEqual(classify_delivery("Natural and neutral."), "neutral")
        self.assertEqual(classify_delivery(""), "neutral")

    def test_word_error_rate_is_word_order_sensitive(self):
        self.assertEqual(word_error_rate("One two three", "one two three"), 0.0)
        self.assertGreater(word_error_rate("One two three", "one three"), 0.0)

    def test_terminal_text_requires_the_authored_final_word(self):
        self.assertTrue(
            terminal_text_matches(
                "They seem almost uncomfortable.",
                "They seem almost uncomfortable.",
            )
        )
        self.assertFalse(
            terminal_text_matches(
                "They seem almost uncomfortable.",
                "They seem almost uncomf.",
            )
        )
        self.assertFalse(
            terminal_text_matches(
                "A long line whose final word is required.",
                "A long line whose final word is",
            )
        )


class FishClientTests(unittest.TestCase):
    def test_client_redacts_key_from_error(self):
        key = "super-secret-fish-key"
        session = FakeSession(
            [FakeResponse(401, payload={"message": f"invalid {key}"})]
        )
        client = FishCloudClient(
            api_key=key,
            model="s2-pro",
            base_url="https://example.invalid",
            session=session,
            max_attempts=1,
        )
        with self.assertRaises(FishCloudError) as raised:
            client.list_owned_models("Example")
        self.assertNotIn(key, str(raised.exception))
        self.assertIn("[redacted]", str(raised.exception))

    def test_private_model_request_never_marks_reference_public(self):
        session = FakeSession(
            [
                FakeResponse(
                    201,
                    payload={
                        "_id": "model-id",
                        "visibility": "private",
                        "state": "created",
                    },
                )
            ]
        )
        client = FishCloudClient(
            api_key="test-key",
            model="s2-pro",
            base_url="https://example.invalid",
            session=session,
            max_attempts=1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            reference = Path(temporary) / "reference.wav"
            reference.write_bytes(wav_bytes())
            created = client.create_private_model(
                title="Private reference",
                reference_audio=reference,
                reference_text="A clean reference sentence.",
            )
        self.assertEqual(created["_id"], "model-id")
        data = session.calls[0]["data"]
        self.assertIn(("visibility", "private"), data)
        self.assertNotIn(("visibility", "public"), data)


class FishCandidateSelectionTests(unittest.TestCase):
    @staticmethod
    def assessment(*, features: AudioFeatures, identity: float = 0.98) -> CandidateAssessment:
        return CandidateAssessment(
            prompt_key="full_alexandria_tag",
            prompt_prior=0.1,
            transcript="Exact authored text.",
            word_error_rate=0.0,
            text_passed=True,
            terminal_text_passed=True,
            identity_score=identity,
            identity_mode="mlx_qwen",
            delivery_score=0.5,
            instruction_delivery_score=0.5,
            quality_score=1.0,
            total_score=0.0,
            features=features,
        )

    def test_grief_repeat_ranking_prefers_blind_test_backed_delivery_cues(self):
        weaker = self.assessment(
            features=AudioFeatures(
                duration_seconds=4.9,
                words_per_second=2.45,
                rms_mean=0.055,
                rms_cv=0.76,
                pitch_median_hz=85.0,
                pitch_cv=4.2,
                spectral_centroid_hz=2208.0,
                silence_ratio=0.20,
                clipping_ratio=0.0,
            ),
            identity=0.991,
        )
        stronger = self.assessment(
            features=AudioFeatures(
                duration_seconds=5.46,
                words_per_second=2.20,
                rms_mean=0.066,
                rms_cv=0.72,
                pitch_median_hz=85.0,
                pitch_cv=4.59,
                spectral_centroid_hz=2240.0,
                silence_ratio=0.18,
                clipping_ratio=0.0,
            ),
            identity=0.989,
        )
        pool = [weaker, stronger]
        self.assertGreater(
            repeat_selection_score("grief", stronger, pool),
            repeat_selection_score("grief", weaker, pool),
        )

    def test_fear_automatically_advances_to_stronger_prompt_stage(self):
        target = "A floorboard creaked behind him, and he knew he was not alone."
        client = SequenceClient([wav_bytes() for _ in range(4)])
        backend = FishCloudBackend(
            client=client,
            transcriber=SequenceTranscriber([target] * 4),
            similarity=SequenceSimilarity([0.99] * 4),
            candidate_count=2,
            difficult_candidate_count=4,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            reference.write_bytes(wav_bytes())
            output = root / "output.wav"
            with (
                patch("fish_cloud_tts.audio_features", return_value=FEATURES),
                patch(
                    "fish_cloud_tts.delivery_score",
                    side_effect=[0.08, 0.12, 0.41, 0.44],
                ),
                patch("fish_cloud_tts.quality_score", return_value=1.0),
            ):
                result = backend.generate(
                    text=target,
                    instruction="Terrified, breath catching, danger nearby.",
                    speaker="NARRATOR",
                    reference_audio=reference,
                    reference_text="Reference words.",
                    output_path=output,
                )
            self.assertTrue(output.is_file())
        self.assertEqual(len(result.candidates), 4)
        self.assertEqual(result.selected.prompt_key, "paralinguistic_fear_tag")
        self.assertEqual(client.prompts[0], client.prompts[1])
        self.assertEqual(client.prompts[2], client.prompts[3])
        self.assertNotEqual(client.prompts[0], client.prompts[2])

    def test_valid_but_unconvincing_delivery_fails_instead_of_requiring_review(self):
        target = "A floorboard creaked behind him, and he knew he was not alone."
        client = SequenceClient([wav_bytes() for _ in range(4)])
        backend = FishCloudBackend(
            client=client,
            transcriber=SequenceTranscriber([target] * 4),
            similarity=SequenceSimilarity([0.99] * 4),
            candidate_count=2,
            difficult_candidate_count=4,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            reference.write_bytes(wav_bytes())
            output = root / "output.wav"
            with (
                patch("fish_cloud_tts.audio_features", return_value=FEATURES),
                patch("fish_cloud_tts.delivery_score", return_value=0.1),
                patch("fish_cloud_tts.quality_score", return_value=1.0),
                self.assertRaisesRegex(
                    FishCloudError,
                    "did not install a weak take",
                ),
            ):
                backend.generate(
                    text=target,
                    instruction="Terrified, breath catching, danger nearby.",
                    speaker="NARRATOR",
                    reference_audio=reference,
                    reference_text="Reference words.",
                    output_path=output,
                )
            self.assertFalse(output.exists())

    def test_bad_transcript_is_rejected_and_best_passing_candidate_is_selected(self):
        target_text = "There was no goodbye, only the empty chair."
        client = SequenceClient([wav_bytes(frequency=170), wav_bytes(frequency=190)])
        backend = FishCloudBackend(
            client=client,
            transcriber=SequenceTranscriber(
                ["There was goodbye only chair", target_text]
            ),
            similarity=SequenceSimilarity([0.99, 0.97]),
            candidate_count=2,
            difficult_candidate_count=2,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            reference.write_bytes(wav_bytes(frequency=180))
            output = root / "output.wav"
            with (
                patch("fish_cloud_tts.audio_features", return_value=FEATURES),
                patch("fish_cloud_tts.delivery_score", side_effect=[0.95, 0.72]),
                patch("fish_cloud_tts.quality_score", return_value=1.0),
            ):
                result = backend.generate(
                    text=target_text,
                    instruction="Deep grief, close to breaking.",
                    speaker="NARRATOR",
                    reference_audio=reference,
                    reference_text="This is a reference sentence.",
                    output_path=output,
                )
            self.assertTrue(output.is_file())
        self.assertTrue(result.selected.text_passed)
        self.assertEqual(result.selected.word_error_rate, 0.0)
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(result.selected.identity_score, 0.97)
        self.assertEqual(client.created, 0)

    def test_all_text_mismatches_fail_closed(self):
        client = SequenceClient([wav_bytes(), wav_bytes()])
        backend = FishCloudBackend(
            client=client,
            transcriber=SequenceTranscriber(["wrong words", "still wrong"]),
            similarity=SequenceSimilarity([0.99, 0.99]),
            candidate_count=2,
            difficult_candidate_count=2,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            reference.write_bytes(wav_bytes())
            with (
                patch("fish_cloud_tts.audio_features", return_value=FEATURES),
                patch("fish_cloud_tts.delivery_score", return_value=0.9),
                patch("fish_cloud_tts.quality_score", return_value=1.0),
                self.assertRaisesRegex(FishCloudError, "authored-text"),
            ):
                backend.generate(
                    text="Exact authored text.",
                    instruction="Fearful.",
                    speaker="DOCTOR",
                    reference_audio=reference,
                    reference_text="Reference words.",
                    output_path=root / "output.wav",
                )

    def test_audition_mode_keeps_identity_safe_text_mismatch_for_listening(self):
        client = SequenceClient([wav_bytes()])
        backend = FishCloudBackend(
            client=client,
            transcriber=SequenceTranscriber(["wrong words"]),
            similarity=SequenceSimilarity([0.99]),
            candidate_count=2,
            difficult_candidate_count=2,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            reference.write_bytes(wav_bytes())
            output = root / "output.wav"
            with (
                patch("fish_cloud_tts.audio_features", return_value=FEATURES),
                patch("fish_cloud_tts.delivery_score", return_value=0.9),
                patch("fish_cloud_tts.quality_score", return_value=1.0),
            ):
                result = backend.generate(
                    text="Exact authored text.",
                    instruction="Fearful.",
                    speaker="Designed Voice audition",
                    reference_audio=reference,
                    reference_text="Reference words.",
                    output_path=output,
                    require_delivery_evidence=False,
                    allow_text_mismatch=True,
                    max_candidates=1,
                )
            self.assertTrue(output.is_file())
            self.assertFalse(result.selected.text_passed)
            self.assertEqual(len(result.candidates), 1)


if __name__ == "__main__":
    unittest.main()
