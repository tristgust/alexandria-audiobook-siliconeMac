from __future__ import annotations

import hashlib
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

from fish_s21_blind_contract import expected_counts, sha256_file  # noqa: E402
from run_fish_s21_blind_test import GeneratedSample, build_review_package  # noqa: E402
from run_fish_s21_permitted_clones import (  # noqa: E402
    identity_tiers,
    load_config,
    per_identity_config,
)


def write_wav(path: Path, *, seconds: float, value: int = 500) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(24000 * seconds)
    sample = int(value).to_bytes(2, byteorder="little", signed=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(sample * frames)


def text_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PermittedCloneContractTests(unittest.TestCase):
    def test_permission_identity_matrix_and_counts_are_explicit(self) -> None:
        config = load_config()
        self.assertTrue(config["permission"]["confirmed_by_user"])
        self.assertEqual(
            {row["key"] for row in config["identities"]},
            {"narrator", "benny", "doctor"},
        )
        narrator = per_identity_config(config, config["identities"][0])
        self.assertEqual(expected_counts(narrator), {"baseline": 16, "fish": 32, "total": 48})
        self.assertTrue(narrator["identity"]["permission_confirmed_by_user"])
        self.assertEqual(narrator["identity"]["source_kind"], "permitted_human_recording")

    def test_reference_tiers_verify_exact_audio_and_transcript_hashes(self) -> None:
        config = load_config()
        identity = next(row for row in config["identities"] if row["key"] == "narrator")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "references" / "narrator"
            conditioning = reference / "conditioning.wav"
            source = reference / "source.wav"
            write_wav(conditioning, seconds=2.0)
            write_wav(source, seconds=4.0, value=600)
            conditioning_text = "A short exact conditioning transcript."
            source_text = "A longer exact source transcript for the permitted evaluation."
            manifest = {
                "identity_key": "narrator",
                "label": "Narrator",
                "conditioning_file": "narrator/conditioning.wav",
                "conditioning_sha256": sha256_file(conditioning),
                "conditioning_transcript": conditioning_text,
                "conditioning_transcript_sha256": text_sha(conditioning_text),
                "source_file": "narrator/source.wav",
                "source_sha256": sha256_file(source),
                "source_transcript": source_text,
                "source_transcript_sha256": text_sha(source_text),
            }
            reference.mkdir(parents=True, exist_ok=True)
            (reference / "reference.json").write_text(json.dumps(manifest), encoding="utf-8")
            tiers, loaded = identity_tiers(
                root,
                config,
                identity,
                prepared_root=root / "prepared",
            )
            self.assertEqual(loaded["identity_key"], "narrator")
            self.assertEqual([row["key"] for row in tiers], ["conditioning", "full_source"])
            self.assertEqual([round(row["duration_seconds"], 1) for row in tiers], [2.0, 4.0])
            manifest["source_transcript"] += " changed"
            (reference / "reference.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "transcript hash changed"):
                identity_tiers(
                    root,
                    config,
                    identity,
                    prepared_root=root / "prepared",
                )


class PermittedClonePackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.round1 = self.root / "round1"
        self.output = self.root / "output"
        base = load_config()
        identity = next(row for row in base["identities"] if row["key"] == "narrator")
        self.config = per_identity_config(base, identity)
        self.tiers = self._make_reference_tiers()
        self._make_baselines()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _make_reference_tiers(self) -> list[dict[str, Any]]:
        rows = []
        for index, key in enumerate(("conditioning", "full_source"), start=1):
            audio = self.root / "references" / f"{key}.wav"
            write_wav(audio, seconds=1.0 + index, value=300 + index)
            text = f"Exact {key} transcript."
            rows.append(
                {
                    "key": key,
                    "label": key.replace("_", " ").title(),
                    "entries": [
                        {
                            "audio_path": str(audio),
                            "audio_sha256": sha256_file(audio),
                            "text": text,
                            "text_sha256": text_sha(text),
                        }
                    ],
                    "duration_seconds": 1.0 + index,
                    "source_manifest": "fixture",
                }
            )
        return rows

    def _make_baselines(self) -> None:
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
                write_wav(audio, seconds=1.2, value=500 + index)
                rows_by_group.setdefault(style["group"], []).append(
                    {
                        "sample_id": f"old-{index}",
                        "source_sample_id": f"source-{index}",
                        "model_key": candidate["model_key"],
                        "model_label": candidate["model_key"],
                        "identity_key": "narrator",
                        "style": style["key"],
                        "group": style["group"],
                        "control": {"mechanism": "fixture"},
                        "public_audio": f"audio/{audio.name}",
                        "public_audio_sha256": sha256_file(audio),
                        "status": "ready",
                        "review_eligible": True,
                    }
                )
        for group, rows in rows_by_group.items():
            (answers / f"{group}.json").write_text(json.dumps(rows), encoding="utf-8")
        (answers / "manifest.json").write_text("{}", encoding="utf-8")

    def _fake_fish(self) -> list[GeneratedSample]:
        rows = []
        index = 0
        for style in self.config["styles"]:
            for tier in self.config["reference_tiers"]:
                for mode in self.config["prompt_modes"]:
                    for repeat in range(1, 3):
                        index += 1
                        audio = self.output / "fake" / f"fish-{index}.wav"
                        write_wav(audio, seconds=1.3, value=700 + index)
                        rows.append(
                            GeneratedSample(
                                fingerprint=f"{index:064x}",
                                audio_path=audio,
                                audio_sha256=sha256_file(audio),
                                duration_seconds=1.3,
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
                                    "receipt": f"fake/{index}.json",
                                },
                            )
                        )
        return rows

    def test_human_package_is_blind_balanced_and_records_permission(self) -> None:
        manifest = build_review_package(
            output_root=self.output,
            round1_root=self.round1,
            config=self.config,
            tiers=self.tiers,
            fish_samples=self._fake_fish(),
        )
        self.assertEqual(manifest["sample_count"], 48)
        self.assertEqual(manifest["fish_sample_count"], 32)
        self.assertEqual(manifest["baseline_sample_count"], 16)
        self.assertTrue(manifest["human_or_licensed_voice_uploaded"])
        self.assertTrue(manifest["permission_confirmed_by_user"])
        self.assertFalse(manifest["synthetic_reference_only"])
        public = (self.output / "review" / "data.js").read_text(encoding="utf-8")
        for secret in ("fish_audio", "s2.1-pro-free", "indextts2", "remote-conditioning"):
            self.assertNotIn(secret, public)


if __name__ == "__main__":
    unittest.main()
