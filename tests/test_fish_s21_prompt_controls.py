from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import wave

from benchmarks.fish_s21_blind_contract import (
    build_prompt,
    expected_counts,
    sha256_bytes,
    sha256_file,
)
from benchmarks.run_fish_s21_prompt_controls import (
    PromptControlContractError,
    _identity_config,
    _reused_tag_samples,
    _write_hub,
    load_config,
)


def write_wav(path: Path, value: int = 400) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = int(value).to_bytes(2, byteorder="little", signed=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(sample * 48000)


class PromptControlContractTests(unittest.TestCase):
    def test_four_identities_and_four_prompt_controls_are_explicit(self) -> None:
        config = load_config()
        self.assertEqual(
            {row["key"] for row in config["identities"]},
            {"ryan_synthetic", "narrator", "benny", "doctor"},
        )
        self.assertEqual(
            {row["key"] for row in config["prompt_modes"]},
            {
                "untagged",
                "simple_tag",
                "rich_tag",
                "full_alexandria_tag",
            },
        )
        for identity in config["identities"]:
            identity_config = _identity_config(config, identity)
            self.assertEqual(
                expected_counts(identity_config),
                {"baseline": 12, "fish": 32, "total": 44},
            )

    def test_prompt_forms_isolate_tag_complexity(self) -> None:
        config = load_config()
        grief = next(row for row in config["styles"] if row["key"] == "grief")
        untagged = build_prompt(grief, "untagged")
        simple = build_prompt(grief, "simple_tag")
        rich = build_prompt(grief, "rich_tag")
        full = build_prompt(grief, "full_alexandria_tag")
        self.assertEqual(untagged, grief["target_text"])
        self.assertTrue(simple.startswith("[sad] "))
        self.assertTrue(rich.startswith("[deep restrained grief"))
        self.assertTrue(full.startswith("[Speak with deep personal grief"))
        self.assertEqual(len({untagged, simple, rich, full}), 4)
        self.assertTrue(all(row.endswith(grief["target_text"]) for row in (simple, rich, full)))


class ReusedPromptEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = load_config()
        self.identity = self.config["identities"][0]
        self.identity_config = _identity_config(self.config, self.identity)
        self.tier = {
            "key": "standard_10s",
            "duration_seconds": 12.0,
            "entries": [
                {
                    "audio_path": str(self.root / "reference.wav"),
                    "audio_sha256": "a" * 64,
                    "text": "Reference text.",
                    "text_sha256": "b" * 64,
                }
            ],
        }
        self.model = {
            "model_id": "private-model-id",
            "reference_fingerprint": "c" * 64,
            "visibility": "private",
        }
        self._write_source_receipts()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_source_receipts(self) -> None:
        mode_map = {
            "rich_tag": "fish_optimized",
            "full_alexandria_tag": "alexandria_exact",
        }
        index = 0
        for style in self.identity_config["styles"]:
            for new_mode, old_mode in mode_map.items():
                prompt = build_prompt(style, new_mode)
                for repeat in (1, 2):
                    index += 1
                    directory = (
                        self.root
                        / "outputs/fish_s21_pro"
                        / "standard_10s"
                        / style["key"]
                        / old_mode
                    )
                    audio = directory / f"repeat-{repeat}.wav"
                    receipt = directory / f"repeat-{repeat}.json"
                    write_wav(audio, value=400 + index)
                    receipt.write_text(
                        json.dumps(
                            {
                                "audio_sha256": sha256_file(audio),
                                "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
                                "reference_fingerprint": self.model["reference_fingerprint"],
                                "settings": self.identity_config["generation"],
                            }
                        ),
                        encoding="utf-8",
                    )

    def test_verified_rich_and_full_samples_are_reused(self) -> None:
        rows = _reused_tag_samples(
            source_root=self.root,
            identity_config=self.identity_config,
            tier=self.tier,
            model=self.model,
        )
        self.assertEqual(len(rows), 16)
        self.assertEqual(
            {row.answer["prompt_mode"] for row in rows},
            {"rich_tag", "full_alexandria_tag"},
        )
        self.assertTrue(all(row.answer.get("reused_verified_source") for row in rows))

    def test_changed_source_prompt_is_rejected(self) -> None:
        receipt = next(self.root.glob("outputs/**/*.json"))
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        payload["prompt_sha256"] = "0" * 64
        receipt.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(PromptControlContractError, "prompt changed"):
            _reused_tag_samples(
                source_root=self.root,
                identity_config=self.identity_config,
                tier=self.tier,
                model=self.model,
            )


class PromptControlHubTests(unittest.TestCase):
    def test_hub_links_every_identity_without_answer_details(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summaries = [
                {
                    "identity": key,
                    "label": label,
                    "sample_count": 44,
                    "fish_sample_count": 32,
                    "baseline_sample_count": 12,
                    "reference_duration_seconds": 12.0,
                    "review": f"{key}/review/",
                    "answer_key": f"{key}/private/answer-key.json",
                }
                for key, label in (
                    ("ryan_synthetic", "Ryan"),
                    ("narrator", "Narrator"),
                    ("benny", "Benny"),
                    ("doctor", "Doctor"),
                )
            ]
            _write_hub(root, summaries)
            html = (root / "index.html").read_text(encoding="utf-8")
            for row in summaries:
                self.assertIn(f'{row["identity"]}/review/?reviewer=tristan', html)
                self.assertIn(row["label"], html)
            for forbidden in (
                "s2.1-pro-free",
                "indextts2",
                "voxcpm2",
                "rich_tag",
                "full_alexandria_tag",
            ):
                self.assertNotIn(forbidden, html)


if __name__ == "__main__":
    unittest.main()
