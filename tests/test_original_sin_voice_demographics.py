from __future__ import annotations

import json
import unittest
from pathlib import Path

from voice_dossier_repair import load_voice_dossier_repair_manifest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "original_sin_voice_demographics_v2.json"


class OriginalSinVoiceDemographicsTests(unittest.TestCase):
    def test_all_dossiers_have_explicit_age_and_supported_gender_context(self) -> None:
        manifest = load_voice_dossier_repair_manifest(MANIFEST)
        voices = manifest["voices"]
        self.assertTrue(manifest["allow_saved_dossier_updates"])
        self.assertEqual(len(voices), 45)
        self.assertEqual(len({item["character_id"] for item in voices}), 45)
        self.assertEqual(len({item["designed_voice_description"] for item in voices}), 45)
        for item in voices:
            description = item["designed_voice_description"]
            self.assertGreaterEqual(len(description), 120, item["speaker"])
            self.assertRegex(
                description.casefold(),
                r"adult|young|older|mature|110-year-old|135-year-old|timeless",
                item["speaker"],
            )

    def test_high_value_source_demographics_are_not_lost(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        by_speaker = {item["speaker"]: item for item in manifest["voices"]}
        kan = by_speaker["KAN NBARO"]
        self.assertIn("110-year-old woman", kan["designed_voice_description"])
        self.assertIn("110 years old", kan["vocal_age_impression"])
        self.assertFalse(any(
            "gender" in value.casefold() or "age" in value.casefold()
            for value in kan["uncertainties"]
        ))
        self.assertIn("135-year-old man", by_speaker["ARCHER MCELWEE"]["designed_voice_description"])
        self.assertIn("female Hith", by_speaker["DWELLER IN SORROW"]["designed_voice_description"])
        self.assertIn("changed from female to male", by_speaker["VAP OPPAT POL"]["designed_voice_description"])
        self.assertIn("adult woman", by_speaker["KAKRELL"]["designed_voice_description"])


if __name__ == "__main__":
    unittest.main()
