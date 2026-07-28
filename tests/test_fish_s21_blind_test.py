from __future__ import annotations

import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

from fish_s21_blind_contract import (  # noqa: E402
    FishBlindContractError,
    build_prompt,
    expected_counts,
    load_config,
    reference_tier_payloads,
    sha256_file,
)
from run_fish_s21_blind_test import (  # noqa: E402
    FishBlindRunError,
    FishClient,
    GeneratedSample,
    build_review_package,
)


def write_wav(path: Path, *, frames: int = 48000, value: int = 400) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = int(value).to_bytes(2, byteorder="little", signed=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(sample * frames)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        payload: dict[str, Any] | None = None,
        content: bytes = b"",
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.text = text
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("No fake response remains")
        return self.responses.pop(0)


class FishS21ContractTests(unittest.TestCase):
    def test_binding_matrix_and_prompt_modes_are_explicit(self) -> None:
        config = load_config()
        self.assertEqual(expected_counts(config), {"baseline": 16, "fish": 48, "total": 64})
        grief = next(row for row in config["styles"] if row["key"] == "grief")
        exact = build_prompt(grief, "alexandria_exact")
        optimized = build_prompt(grief, "fish_optimized")
        self.assertTrue(exact.startswith("[Speak with deep personal grief"))
        self.assertTrue(optimized.startswith("[deep restrained grief"))
        self.assertTrue(exact.endswith(grief["target_text"]))
        self.assertNotEqual(exact, optimized)

    def test_reference_manifest_accepts_only_synthetic_ryan(self) -> None:
        config = load_config()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "references" / "ryan"
            entries = {}
            for index, key in enumerate(
                ("neutral", "acted_neutral", "acted_friendly", "acted_calm", "acted_determined", "acted_sad", "acted_grief")
            ):
                audio = reference / f"{key}.wav"
                write_wav(audio, frames=24000 + index * 100)
                entries[key] = {
                    "audio_file": audio.name,
                    "audio_sha256": sha256_file(audio),
                    "text": f"Synthetic reference {key}.",
                    "text_sha256": "a" * 64,
                    "kind": "built_in_qwen_custom_voice_anchor",
                    "audio": {"duration_seconds": 1.0},
                }
            manifest = {
                "voice": "Ryan",
                "neutral": entries["neutral"],
                "acted": [
                    {**entries[key], "style": key.removeprefix("acted_")}
                    for key in entries
                    if key.startswith("acted_")
                ],
            }
            reference.mkdir(parents=True, exist_ok=True)
            (reference / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            tiers = reference_tier_payloads(root, config)
            self.assertEqual([tier["key"] for tier in tiers], ["short_5s", "standard_10s", "long_30s"])
            manifest["neutral"]["kind"] = "supplied_recording_clone"
            (reference / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(FishBlindContractError, "not synthetic"):
                reference_tier_payloads(root, config)


class FishClientTests(unittest.TestCase):
    def test_authentication_error_redacts_key(self) -> None:
        key = "secret-token-for-test"
        session = FakeSession(
            [FakeResponse(401, payload={"message": f"invalid {key}"})]
        )
        client = FishClient(
            api_key=key,
            model_header="s2.1-pro-free",
            base_url="https://example.invalid",
            session=session,
            max_attempts=1,
        )
        with self.assertRaises(FishBlindRunError) as raised:
            client.list_owned_models("test")
        self.assertNotIn(key, str(raised.exception))
        self.assertIn("[redacted]", str(raised.exception))

    def test_voice_creation_is_private_and_uses_repeated_transcripts(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    201,
                    payload={
                        "_id": "voice-model-id",
                        "title": "Alexandria test",
                        "state": "created",
                        "visibility": "private",
                    },
                )
            ]
        )
        client = FishClient(
            api_key="test-key",
            model_header="s2.1-pro-free",
            base_url="https://example.invalid",
            session=session,
            max_attempts=1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "reference.wav"
            write_wav(audio)
            result = client.create_voice_model(
                title="Alexandria test",
                description="Synthetic test",
                entries=[
                    {"audio_path": str(audio), "text": "First transcript."},
                    {"audio_path": str(audio), "text": "Second transcript."},
                ],
            )
        self.assertEqual(result["_id"], "voice-model-id")
        call = session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertIn(("visibility", "private"), call["data"])
        self.assertEqual(
            [value for key, value in call["data"] if key == "texts"],
            ["First transcript.", "Second transcript."],
        )
        self.assertEqual(len(call["files"]), 2)
        self.assertEqual(call["headers"]["Authorization"], "Bearer test-key")


class FishReviewPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.round1 = self.root / "round1"
        self.output = self.root / "output"
        self.config = load_config()
        self.tiers = self.make_references()
        self.make_baselines()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_references(self) -> list[dict[str, Any]]:
        reference = self.round1 / "references" / "ryan"
        rows: dict[str, dict[str, Any]] = {}
        keys = ("neutral", "acted_neutral", "acted_friendly", "acted_calm", "acted_determined", "acted_sad", "acted_grief")
        for index, key in enumerate(keys):
            audio = reference / f"{key}.wav"
            write_wav(audio, frames=24000 + index * 1200, value=300 + index)
            rows[key] = {
                "audio_file": audio.name,
                "audio_sha256": sha256_file(audio),
                "text": f"Synthetic Ryan {key} reference.",
                "text_sha256": f"{index + 1:064x}",
                "kind": "built_in_qwen_custom_voice_anchor",
                "audio": {"duration_seconds": (24000 + index * 1200) / 24000},
            }
        reference.mkdir(parents=True, exist_ok=True)
        (reference / "manifest.json").write_text(
            json.dumps(
                {
                    "voice": "Ryan",
                    "neutral": rows["neutral"],
                    "acted": [
                        {**rows[key], "style": key.removeprefix("acted_")}
                        for key in keys
                        if key.startswith("acted_")
                    ],
                }
            ),
            encoding="utf-8",
        )
        return reference_tier_payloads(self.round1, self.config)

    def make_baselines(self) -> None:
        public = self.round1 / "review-round1-complete-final"
        answers = self.round1 / "review-round1-complete-final-answer-keys"
        public.mkdir(parents=True)
        answers.mkdir(parents=True)
        rows_by_group: dict[str, list[dict[str, Any]]] = {}
        index = 0
        for style in self.config["styles"]:
            for candidate in self.config["baseline_candidates"]:
                index += 1
                audio = public / "audio" / f"baseline-{index}.wav"
                write_wav(audio, frames=26000 + index, value=500 + index)
                row = {
                    "sample_id": f"old-blind-{index}",
                    "source_sample_id": f"source-{index}",
                    "model_key": candidate["model_key"],
                    "model_label": candidate["model_key"].replace("_", " ").title(),
                    "identity_key": candidate["identity_key"],
                    "style": style["key"],
                    "group": style["group"],
                    "control": {"mechanism": "fixture"},
                    "public_audio": f"audio/{audio.name}",
                    "public_audio_sha256": sha256_file(audio),
                    "status": "ready",
                    "review_eligible": True,
                }
                rows_by_group.setdefault(style["group"], []).append(row)
        for group, rows in rows_by_group.items():
            (answers / f"{group}.json").write_text(json.dumps(rows), encoding="utf-8")
        (answers / "manifest.json").write_text("{}", encoding="utf-8")

    def fake_fish_samples(self) -> list[GeneratedSample]:
        result: list[GeneratedSample] = []
        index = 0
        for style in self.config["styles"]:
            for tier in self.config["reference_tiers"]:
                for mode in self.config["prompt_modes"]:
                    for repeat in range(1, self.config["generation"]["repeats"] + 1):
                        index += 1
                        audio = self.output / "fake" / f"fish-{index}.wav"
                        write_wav(audio, frames=28000 + index, value=700 + index)
                        result.append(
                            GeneratedSample(
                                fingerprint=f"{index:064x}",
                                audio_path=audio,
                                audio_sha256=sha256_file(audio),
                                duration_seconds=(28000 + index) / 24000,
                                answer={
                                    "kind": "fish_cloud",
                                    "provider": "fish_audio",
                                    "marketed_model": "Fish Audio S2.1 Pro",
                                    "api_model_header": "s2.1-pro-free",
                                    "remote_reference_id": f"remote-{tier['key']}",
                                    "reference_tier": tier["key"],
                                    "prompt_mode": mode["key"],
                                    "style": style["key"],
                                    "repeat": repeat,
                                    "prompt_sha256": f"{index + 100:064x}",
                                    "receipt": f"fake/fish-{index}.json",
                                },
                            )
                        )
        return result

    def test_complete_package_is_blind_and_has_expected_cell_counts(self) -> None:
        manifest = build_review_package(
            output_root=self.output,
            round1_root=self.round1,
            config=self.config,
            tiers=self.tiers,
            fish_samples=self.fake_fish_samples(),
        )
        self.assertEqual(manifest["sample_count"], 64)
        self.assertEqual(manifest["baseline_sample_count"], 16)
        self.assertEqual(manifest["fish_sample_count"], 48)
        self.assertEqual(set(manifest["samples_per_style"].values()), {16})
        review = self.output / "review"
        public_text = (review / "data.js").read_text(encoding="utf-8")
        for secret in (
            "fish_audio",
            "Fish Audio S2.1 Pro",
            "s2.1-pro-free",
            "indextts2",
            "voxcpm2",
            "chatterbox_multilingual_v3",
            "remote-short_5s",
            "alexandria_exact",
            "fish_optimized",
        ):
            self.assertNotIn(secret, public_text)
        answer_text = (self.output / "private" / "answer-key.json").read_text(encoding="utf-8")
        self.assertIn("Fish Audio S2.1 Pro", answer_text)
        self.assertIn("indextts2", answer_text)
        public = json.loads(public_text.removeprefix("window.FISH_S21_BLIND_DATA = ").removesuffix(";\n"))
        for style in self.config["styles"]:
            candidates = [row for row in public["samples"] if row["style"] == style["key"]]
            self.assertEqual([row["candidate_number"] for row in candidates], list(range(1, 17)))
        self.assertTrue((review / "index.html").is_file())
        self.assertTrue((review / "reference" / "identity-reference.wav").is_file())
        review_html = (review / "index.html").read_text(encoding="utf-8")
        review_app = (review / "app.js").read_text(encoding="utf-8")
        self.assertIn('preload="none"', review_html)
        self.assertIn('audio.preload = "none"', review_app)
        self.assertIn('open.textContent = "Open audio"', review_app)
        self.assertIn('retry.textContent = "Retry audio"', review_app)
        self.assertIn("${safeRound}_${reviewer}.json", review_app)

    def test_partial_package_requires_explicit_permission(self) -> None:
        samples = self.fake_fish_samples()[:1]
        with self.assertRaisesRegex(FishBlindRunError, "fish_sample_count_incomplete"):
            build_review_package(
                output_root=self.output,
                round1_root=self.round1,
                config=self.config,
                tiers=self.tiers,
                fish_samples=samples,
            )
        manifest = build_review_package(
            output_root=self.output,
            round1_root=self.round1,
            config=self.config,
            tiers=self.tiers,
            fish_samples=samples,
            allow_partial=True,
        )
        self.assertTrue(manifest["partial"])
        self.assertEqual(manifest["sample_count"], 17)


if __name__ == "__main__":
    unittest.main()
