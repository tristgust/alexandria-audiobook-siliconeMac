from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.build_b18_multivoice_screen import METHODS, SPEAKERS
from benchmarks.package_b18_multivoice_review import package_round


class MultiVoiceScreenContractTests(unittest.TestCase):
    def test_screen_covers_requested_distinct_archetypes(self) -> None:
        keys = {item["speaker_key"] for item in SPEAKERS}
        self.assertEqual(
            keys,
            {
                "THE DOCTOR",
                "BERNICE",
                "CHRIS CWEJ",
                "ROZ FORRESTER",
                "COMPUTER",
                "TOBIAS VAUGHN",
                "POWERLESS FRIENDLESS",
            },
        )
        self.assertIn("current_route", METHODS)
        self.assertIn("fish_s2_pro_local", METHODS)
        self.assertIn("fish_s21_pro_free", METHODS)
        self.assertIn("moss_local_v15", METHODS)
        self.assertIn("moss_nano", METHODS)
        effects = {
            row["speaker_key"]: row.get("effect_isolation") for row in SPEAKERS
        }
        self.assertEqual(effects["COMPUTER"], "computer_terminal_v3")
        self.assertEqual(
            effects["POWERLESS FRIENDLESS"],
            "powerless_alien_modulation_v1",
        )

    def test_packager_hides_methods_and_groups_by_speaker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidates = root / "candidates"
            output = root / "output"
            rows = []
            for speaker in SPEAKERS:
                reference = root / f"{speaker['speaker_key']}.wav"
                reference.write_bytes(b"RIFF-reference")
                for method in ("current_route", "qwen_controlled_identity"):
                    audio = root / f"{speaker['speaker_key']}-{method}.wav"
                    audio.write_bytes(b"RIFF-audio-" + method.encode())
                    rows.append(
                        {
                            "candidate_id": f"{speaker['speaker_key']}__{method}",
                            "speaker_key": speaker["speaker_key"],
                            "display_name": speaker["display_name"],
                            "archetype": speaker["archetype"],
                            "source_chunk_id": speaker["source_chunk_id"],
                            "text": speaker["text"],
                            "instruction": speaker["instruction"],
                            "method": method,
                            "status": "generated",
                            "error": None,
                            "generation": {"output_path": str(audio)},
                            "eligible": True,
                            "exclusion_reason": None,
                            "objective": {
                                "output_path": str(audio),
                                "reference_path": str(reference),
                                "word_error_rate": 0.0,
                            },
                        }
                    )
            candidates.mkdir()
            (candidates / "objective_summary.json").write_text(
                json.dumps({"rows": rows}),
                encoding="utf-8",
            )
            manifest = package_round(
                candidate_root=candidates,
                output_root=output,
            )
            self.assertEqual(manifest["speaker_count"], 7)
            self.assertEqual(manifest["sample_count"], 14)
            public = (output / "review" / "data.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("current_route", public)
            self.assertNotIn("qwen_controlled_identity", public)
            answer = json.loads(
                (output / "answer-keys" / "answer-key.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(answer["answers"]), 14)


if __name__ == "__main__":
    unittest.main()
