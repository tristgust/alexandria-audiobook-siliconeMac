from __future__ import annotations

import importlib.util
import json
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "package_three_voice_historical_provenance_review.py"
ASSET_ROOT = ROOT / "benchmarks" / "three_voice_historical_provenance_assets"
EVIDENCE_ROOT = ROOT / ".omo" / "evidence" / "b17-t66-three-voice-historical-provenance-review"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ThreeVoiceHistoricalProvenanceReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module(SCRIPT, "package_three_voice_historical_provenance_review")

    def test_contract_is_strict_and_bounded(self) -> None:
        self.assertEqual(self.module.EXPECTED_COUNTS, {"benny": 10, "doctor": 4})
        self.assertEqual(
            self.module.KNOWN_WRONG_SPEAKER_CLIP_ID,
            "doctor_acf_emergency_command",
        )
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("user_correction_required_before_bank_approval", source)
        self.assertIn('"rejected_wrong_speaker"', source)
        self.assertIn('"automatic_production_assignment": False', source)
        self.assertIn('"production_promotion_allowed": False', source)
        self.assertIn('"maximum_simultaneous_audio_elements": 3', source)

    def test_review_assets_use_one_decisive_outcome(self) -> None:
        html = (ASSET_ROOT / "index.html").read_text(encoding="utf-8")
        app = (ASSET_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertEqual(html.lower().count("<audio"), 3)
        for label in (
            "Correct speaker and usable",
            "Correct speaker, technically unusable",
            "Wrong or uncertain speaker",
            "Wrong boundary",
        ):
            self.assertIn(label, html)
        self.assertIn("Source context", html)
        self.assertIn("Known identity", html)
        self.assertIn("Extracted clip", html)
        self.assertIn("alexandria_three_voice_historical_provenance_review.json", app)
        self.assertIn("locked_rejected_wrong_speaker", app)
        self.assertNotIn("reference_decision", html)
        self.assertNotIn("audio_cleanliness_decision", html)

    def test_context_excerpt_is_bounded(self) -> None:
        context = {
            "transcript": "fallback",
            "segments": [
                {"start_seconds": 0.0, "end_seconds": 1.0, "text": "too early"},
                {"start_seconds": 8.0, "end_seconds": 9.0, "text": "before"},
                {"start_seconds": 10.0, "end_seconds": 11.0, "text": "selected"},
                {"start_seconds": 18.0, "end_seconds": 19.0, "text": "after"},
                {"start_seconds": 30.0, "end_seconds": 31.0, "text": "too late"},
            ],
        }
        excerpt = self.module.context_excerpt(context, 10.0, 11.0)
        self.assertNotIn("too early", excerpt)
        self.assertIn("before", excerpt)
        self.assertIn("selected", excerpt)
        self.assertIn("after", excerpt)
        self.assertNotIn("too late", excerpt)

    def test_generated_review_contract_when_present(self) -> None:
        review_root = EVIDENCE_ROOT / "review"
        data_path = review_root / "data.js"
        manifest_path = review_root / "manifest.json"
        answer_path = EVIDENCE_ROOT / "answer-key.json"
        if not all(path.is_file() for path in (data_path, manifest_path, answer_path)):
            self.skipTest("Generated provenance review is not present in this checkout.")
        prefix = "window.THREE_VOICE_HISTORICAL_PROVENANCE_DATA = "
        text = data_path.read_text(encoding="utf-8").strip()
        data = json.loads(text[len(prefix) :].rstrip(";"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        answers = json.loads(answer_path.read_text(encoding="utf-8"))
        self.assertEqual(data["candidate_count"], 14)
        self.assertEqual(data["actionable_count"], 13)
        self.assertEqual(data["warning_count"], 1)
        self.assertEqual(
            Counter(row["target"] for row in data["rows"]),
            Counter({"benny": 10, "doctor": 4}),
        )
        warning = [row for row in data["rows"] if row["warning_only"]]
        self.assertEqual(len(warning), 1)
        self.assertEqual(warning[0]["clip_id"], "doctor_acf_emergency_command")
        self.assertEqual(manifest["maximum_simultaneous_audio_elements"], 3)
        self.assertTrue(manifest["source_context_audio_included"])
        self.assertTrue(manifest["known_wrong_speaker_locked"])
        self.assertFalse(manifest["automatic_production_assignment"])
        self.assertFalse(manifest["production_promotion_allowed"])
        self.assertEqual(len(answers), 14)

    def test_all_generated_audio_is_mono_mp3_when_present(self) -> None:
        review_root = EVIDENCE_ROOT / "review"
        data_path = review_root / "data.js"
        if not data_path.is_file():
            self.skipTest("Generated provenance review is not present in this checkout.")
        prefix = "window.THREE_VOICE_HISTORICAL_PROVENANCE_DATA = "
        text = data_path.read_text(encoding="utf-8").strip()
        data = json.loads(text[len(prefix) :].rstrip(";"))
        for row in data["rows"]:
            for key in ("identity_audio", "context_audio", "candidate_audio"):
                with self.subTest(clip_id=row["clip_id"], key=key):
                    path = review_root / row[key]
                    self.assertTrue(path.is_file())
                    probe = self.module.audio_probe(path)
                    self.assertEqual(probe["codec_name"], "mp3")
                    self.assertEqual(probe["channels"], 1)
                    self.assertGreater(probe["duration_seconds"], 0.5)


if __name__ == "__main__":
    unittest.main()
