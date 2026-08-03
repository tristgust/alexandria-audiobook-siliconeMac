from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from benchmarks.build_b21_hith_processing_repair import (
    EVALUATION_VARIANTS,
    ROUND_ID,
    build_round,
    sha256_file,
)


class HithProcessingRepairTests(unittest.TestCase):
    def _write_audio(self, path: Path, *, rate: int = 24000) -> None:
        time_axis = np.arange(rate, dtype=np.float32) / float(rate)
        audio = (
            0.22 * np.sin(2.0 * np.pi * 180.0 * time_axis)
            + 0.11 * np.sin(2.0 * np.pi * 360.0 * time_axis)
            + 0.05 * np.sin(2.0 * np.pi * 720.0 * time_axis)
        )
        sf.write(str(path), audio, rate, subtype="PCM_16")

    def _write_contract(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "groups": [
                        {
                            "speaker_key": "POWERLESS FRIENDLESS",
                            "display_name": "Powerless Friendless",
                            "archetype": "Panicked Hith urgency",
                            "expected_text": "You have betrayed Hithis.",
                            "instruction": "Panicked urgency without losing words.",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def test_variant_set_has_anchors_and_three_distinct_repairs(self) -> None:
        methods = [str(item["method"]) for item in EVALUATION_VARIANTS]
        self.assertEqual(len(methods), 5)
        self.assertEqual(len(methods), len(set(methods)))
        self.assertIn("raw_qwen_anchor", methods)
        self.assertIn("powerless_alien_modulation_v1_anchor", methods)
        self.assertEqual(len([name for name in methods if name.endswith("_v2")]), 3)

    def test_round_is_blind_deterministic_and_non_installing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.wav"
            existing = root / "existing.wav"
            reference = root / "reference.wav"
            contract = root / "data.json"
            output_one = root / "round-one"
            output_two = root / "round-two"
            self._write_audio(source)
            self._write_audio(reference, rate=44100)
            existing.write_bytes(source.read_bytes())
            self._write_contract(contract)
            first = build_round(
                source_path=source,
                existing_path=existing,
                reference_path=reference,
                source_data_path=contract,
                output_root=output_one,
                verify_tracked_hashes=False,
            )
            second = build_round(
                source_path=source,
                existing_path=existing,
                reference_path=reference,
                source_data_path=contract,
                output_root=output_two,
                verify_tracked_hashes=False,
            )
            self.assertEqual(first["candidate_count"], 5)
            self.assertEqual(first["data_sha256"], second["data_sha256"])
            answer_one = json.loads(
                (output_one / "answer-keys" / "answer-key.json").read_text(
                    encoding="utf-8"
                )
            )
            answer_two = json.loads(
                (output_two / "answer-keys" / "answer-key.json").read_text(
                    encoding="utf-8"
                )
            )
            hashes_one = [row["audio"]["sha256"] for row in answer_one["answers"]]
            hashes_two = [row["audio"]["sha256"] for row in answer_two["answers"]]
            self.assertEqual(hashes_one, hashes_two)
            self.assertEqual(len(set(hashes_one)), 5)
            self.assertFalse(answer_one["production_promotion_allowed"])
            self.assertFalse(answer_one["synthesis_performed"])
            self.assertFalse(answer_one["live_project_changed"])
            public = (output_one / "review" / "data.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("hith_phase_chorus_v2", public)
            self.assertNotIn("powerless_alien_modulation_v1_anchor", public)
            self.assertIn(ROUND_ID, public)
            html = (output_one / "review" / "index.html").read_text(
                encoding="utf-8"
            )
            script = (output_one / "review" / "app.js").read_text(
                encoding="utf-8"
            )
            self.assertIn("Boundary 21", html)
            self.assertIn("Hith processing repair", html)
            self.assertIn("progress-backup.json", script)

    def test_tracked_source_contract_is_unchanged(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (
            root
            / ".omo/evidence/b18-multivoice-archetype-screen-20260803/review/audio/HIT03.wav"
        )
        existing = (
            root
            / ".omo/evidence/b18-multivoice-archetype-screen-20260803/review/audio/HIT04.wav"
        )
        reference = (
            root
            / ".omo/evidence/b18-multivoice-archetype-screen-20260803/review/reference/hit_reference.wav"
        )
        self.assertEqual(
            sha256_file(source),
            "330e67cc942c38e422ef3b291b25da39e336510b3babfc0ba030305aecfe4c97",
        )
        self.assertEqual(
            sha256_file(existing),
            "a512b2811bce8237e06def12e0e8bc833efe5b0aa83113da4a363301e40a2874",
        )
        self.assertEqual(
            sha256_file(reference),
            "31a4c37f744ea4c413dff7ea9ccb4caa9fe08f1f3ac84f63b7f16328acb885a9",
        )


if __name__ == "__main__":
    unittest.main()
