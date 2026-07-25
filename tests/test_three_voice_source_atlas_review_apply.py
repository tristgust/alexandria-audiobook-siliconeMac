from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
APPLY_SCRIPT = ROOT / "benchmarks" / "apply_three_voice_source_atlas_review.py"
REPAIR_SCRIPT = ROOT / "benchmarks" / "prepare_three_voice_source_repairs.py"
REPAIR_ASSETS = ROOT / "benchmarks" / "three_voice_source_repair_assets"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ThreeVoiceSourceAtlasReviewApplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.apply = load_module(APPLY_SCRIPT, "apply_three_voice_source_atlas_review")
        cls.repair = load_module(REPAIR_SCRIPT, "prepare_three_voice_source_repairs")

    def base_record(self, **changes):
        record = {
            "reference_decision": "approve",
            "speaker_role_decision": "correct",
            "boundary_decision": "correct",
            "audio_cleanliness_decision": "clean",
        }
        record.update(changes)
        return record

    def test_classification_preserves_review_safety_order(self) -> None:
        cases = [
            (self.base_record(), "approved_clean"),
            (self.base_record(audio_cleanliness_decision="usable_with_cleanup"), "cleanup_required"),
            (
                self.base_record(boundary_decision="ends_too_late", audio_cleanliness_decision="usable_with_cleanup"),
                "boundary_repair_required",
            ),
            (self.base_record(reference_decision="reject"), "rejected_by_reviewer"),
            (self.base_record(reference_decision="mine_nearby"), "mine_nearby_requested"),
            (
                self.base_record(reference_decision=None, audio_cleanliness_decision="usable_with_cleanup"),
                "incomplete_review",
            ),
            (
                self.base_record(speaker_role_decision=None),
                "blocked_approval",
            ),
        ]
        for record, expected in cases:
            with self.subTest(expected=expected):
                disposition, _ = self.apply.classify(record)
                self.assertEqual(disposition, expected)

    def test_boundary_repair_overrides_conditional_approval(self) -> None:
        disposition, reasons = self.apply.classify(
            self.base_record(
                boundary_decision="too_late",
                audio_cleanliness_decision="usable_with_cleanup",
            )
        )
        self.assertEqual(disposition, "boundary_repair_required")
        self.assertEqual(reasons, ["boundary_too_late"])

    def test_build_applied_validates_audio_and_never_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "clip.wav"
            sf.write(audio, np.zeros(24000, dtype=np.float32), 24000, subtype="PCM_16")
            audio_hash = self.apply.sha256_file(audio)
            atlas_path = root / "atlas.json"
            review_path = root / "review.json"
            atlas = {
                "round_id": self.apply.SOURCE_ROUND_ID,
                "rows": [{
                    "clip_id": "clip-1",
                    "target": "narrator",
                    "target_label": "Narrator",
                    "source": "source",
                    "source_title": "Source",
                    "source_kind": "test",
                    "youtube_id": "video",
                    "source_audio": str(audio),
                    "source_audio_sha256": audio_hash,
                    "audio_path": str(audio),
                    "audio_sha256": audio_hash,
                    "selected_start_seconds": 0.0,
                    "selected_end_seconds": 1.0,
                    "selected_duration_seconds": 1.0,
                    "expected_text": "Exact words.",
                    "primary_emotion": "Neutral",
                    "secondary_emotion": "Calm",
                    "dramatic_function": "Conversation",
                    "intensity_1_to_5": 1,
                    "coverage_gap": "neutral",
                    "speaker_certainty": "high",
                }],
            }
            review = {
                "round_id": self.apply.REVIEW_ROUND_ID,
                "exported_at": "2026-07-25T00:00:00Z",
                "summary": {},
                "rows": [{
                    "clip_id": "clip-1",
                    "target": "narrator",
                    "youtube_id": "video",
                    "speaker_role_decision": "correct",
                    "boundary_decision": "correct",
                    "audio_cleanliness_decision": "clean",
                    "reference_decision": "approve",
                }],
            }
            atlas_path.write_text(json.dumps(atlas), encoding="utf-8")
            review_path.write_text(json.dumps(review), encoding="utf-8")
            payload = self.apply.build_applied(
                atlas,
                review,
                atlas_path=atlas_path,
                review_path=review_path,
            )
            result = self.apply.validate_applied(payload)
            self.assertEqual(result["approved_clean_count"], 1)
            self.assertFalse(payload["automatic_production_assignment"])
            self.assertFalse(payload["production_promotion_allowed"])
            self.assertFalse(payload["approved_clean_references"][0]["production_promotion_allowed"])

    def test_word_span_resolves_expected_utterance_inside_context(self) -> None:
        words = [
            {"normalized": word, "start": index * 0.1, "end": index * 0.1 + 0.08}
            for index, word in enumerate("before words exact target sentence after words".split())
        ]
        start, end, score = self.repair.best_word_span(words, "exact target sentence")
        self.assertEqual((start, end), (2, 5))
        self.assertGreater(score, 0.95)

    def test_repair_source_encodes_directional_boundary_and_hard_gates(self) -> None:
        source = REPAIR_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("repair_boundary(row, repaired, boundary, whisper_model)", source)
        self.assertIn('if boundary == "too_early"', source)
        self.assertIn('elif boundary == "ends_too_late"', source)
        self.assertIn("verification_similarity", source)
        self.assertIn("clipping_sample_count", source)
        self.assertIn('"production_promotion_allowed": False', source)
        self.assertIn("dialoguenhance=original=0.25:enhance=2.1:voice=12", source)

    def test_repair_review_assets_keep_workload_bounded(self) -> None:
        html = (REPAIR_ASSETS / "index.html").read_text(encoding="utf-8")
        app = (REPAIR_ASSETS / "app.js").read_text(encoding="utf-8")
        self.assertEqual(html.lower().count("<audio"), 2)
        self.assertIn("Use repaired clip", html)
        self.assertIn("Cleanup still bad", html)
        self.assertIn("Boundary still wrong", html)
        self.assertIn("Mine nearby", html)
        self.assertIn("alexandria_three_voice_source_repair_review.json", app)
        self.assertIn("advanceAfterDecision", app)

    def test_generated_applied_and_repair_ledgers_match_review_when_present(self) -> None:
        applied_path = ROOT / ".omo" / "evidence" / "b17-t52-three-voice-source-atlas-applied" / "applied-review-ledger.json"
        repair_path = ROOT / ".omo" / "evidence" / "b17-t53-three-voice-source-repairs" / "repair-manifest.json"
        if not applied_path.is_file() or not repair_path.is_file():
            self.skipTest("Generated local review evidence is not present in this checkout.")
        applied = json.loads(applied_path.read_text(encoding="utf-8"))
        repair = json.loads(repair_path.read_text(encoding="utf-8"))
        self.assertEqual(
            applied["disposition_counts"],
            {
                "approved_clean": 10,
                "boundary_repair_required": 7,
                "cleanup_required": 13,
                "incomplete_review": 1,
                "rejected_by_reviewer": 14,
            },
        )
        self.assertEqual(repair["candidate_count"], 21)
        self.assertEqual(repair["technical_pass_count"], 21)
        self.assertEqual(repair["failure_count"], 0)
        self.assertFalse(repair["production_promotion_allowed"])


if __name__ == "__main__":
    unittest.main()
