from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from benchmarks import build_b22_doctor_fallback_repair as repair


def write_wav(path: Path, value: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.full(2400, value, dtype=np.float32), 24000)


class DoctorFallbackRepairTests(unittest.TestCase):
    def project_fixture(self, root: Path) -> Path:
        project = root / "project"
        routes = {}
        for index, route_key in enumerate(repair.REFERENCE_ROUTE_KEYS, start=1):
            audio = project / "references" / f"{route_key}.wav"
            write_wav(audio, 0.01 * index)
            routes[route_key] = {
                "backend": "qwen3_instruction_controlled",
                "instruction_keywords": ["fixture"],
                "identity_audio": audio.relative_to(project).as_posix(),
                "identity_audio_sha256": repair.sha256_file(audio),
                "identity_text": f"Reference transcript {index}.",
                "performance_audio": None,
                "performance_audio_sha256": None,
                "performance_text": None,
                "control": {},
                "effect_chain": None,
                "approval_tier": "strict",
                "production_promotion_allowed": True,
            }
        config = {
            "THE DOCTOR": {
                "seed": repair.SEED,
                "character_style": "Dry Doctor fixture.",
                "responsive_backend_routing": {
                    "schema_version": 1,
                    "enabled": True,
                    "default_route": "neutral",
                    "fallback_backend": "qwen3_instruction_controlled",
                    "evidence_round_id": "fixture",
                    "production_promotion_allowed": True,
                    "routes": routes,
                },
            }
        }
        project.mkdir(parents=True, exist_ok=True)
        (project / "voice_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        return project

    def test_contract_has_two_anchors_and_three_reference_tests(self) -> None:
        self.assertEqual(len(repair.ANCHOR_SPECS), 2)
        self.assertEqual(len(repair.REFERENCE_ROUTE_KEYS), 3)
        self.assertEqual(len({item["method"] for item in repair.ANCHOR_SPECS}), 2)
        self.assertEqual(len(set(repair.REFERENCE_ROUTE_KEYS)), 3)

    def test_build_is_blind_single_choice_and_non_installing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.project_fixture(root)
            output = root / "output"
            anchor_root = root / "anchors"
            reference = root / "reference.mp3"
            write_wav(reference, 0.02)
            original_reference = repair.REFERENCE_SOURCE
            original_anchors = repair.ANCHOR_SPECS
            try:
                repair.REFERENCE_SOURCE = reference
                repair.ANCHOR_SPECS = tuple(
                    {
                        "method": f"anchor_{index}",
                        "source": str(anchor_root / f"anchor_{index}.wav"),
                        "sha256": "unused",
                    }
                    for index in range(2)
                )
                for index, spec in enumerate(repair.ANCHOR_SPECS, start=1):
                    write_wav(Path(spec["source"]), 0.03 * index)

                def generator(*, destination, reference, voice_data):
                    write_wav(destination, 0.05)
                    return {
                        "backend": "qwen3_instruction_controlled",
                        "reference_route": reference["route_key"],
                    }

                def evaluator(rows):
                    for row in rows:
                        row["transcription"] = {
                            "word_error_rate": 0.0,
                            "transcript": repair.TARGET_TEXT,
                        }
                    return {"complete": True, "measurements": {}}

                manifest = repair.build_round(
                    project_root=project,
                    output_root=output,
                    generator=generator,
                    evaluator=evaluator,
                    verify_tracked_hashes=False,
                )
            finally:
                repair.REFERENCE_SOURCE = original_reference
                repair.ANCHOR_SPECS = original_anchors
            self.assertEqual(manifest["candidate_count"], 5)
            self.assertEqual(manifest["prior_anchor_count"], 2)
            self.assertEqual(manifest["new_qwen_candidate_count"], 3)
            self.assertEqual(manifest["review_contract"], "single_best_or_none")
            self.assertFalse(manifest["production_promotion_allowed"])
            self.assertFalse(manifest["live_project_changed"])
            public = (output / "review" / "data.json").read_text(encoding="utf-8")
            self.assertNotIn("doctor_comic_disorientation", public)
            self.assertNotIn("anchor_0", public)
            script = (output / "review" / "app.js").read_text(encoding="utf-8")
            html = (output / "review" / "index.html").read_text(encoding="utf-8")
            self.assertIn("None are good enough", script)
            self.assertIn("No scoring", html)
            self.assertNotIn("identity_1_to_5", script)
            answer = json.loads(
                (output / "answer-keys" / "answer-key.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(answer["answers"]), 5)

    def test_objective_text_failure_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.project_fixture(root)
            output = root / "output"
            anchor_root = root / "anchors"
            reference = root / "reference.mp3"
            write_wav(reference, 0.02)
            original_reference = repair.REFERENCE_SOURCE
            original_anchors = repair.ANCHOR_SPECS
            try:
                repair.REFERENCE_SOURCE = reference
                repair.ANCHOR_SPECS = tuple(
                    {
                        "method": f"anchor_{index}",
                        "source": str(anchor_root / f"anchor_{index}.wav"),
                        "sha256": "unused",
                    }
                    for index in range(2)
                )
                for index, spec in enumerate(repair.ANCHOR_SPECS, start=1):
                    write_wav(Path(spec["source"]), 0.03 * index)

                def generator(*, destination, reference, voice_data):
                    write_wav(destination, 0.05)
                    return {}

                def evaluator(rows):
                    for row in rows:
                        row["transcription"] = {
                            "word_error_rate": (
                                1.0 if row["route_key"] == repair.REFERENCE_ROUTE_KEYS[0] else 0.0
                            ),
                            "transcript": repair.TARGET_TEXT,
                        }
                    return {"complete": True, "measurements": {}}

                manifest = repair.build_round(
                    project_root=project,
                    output_root=output,
                    generator=generator,
                    evaluator=evaluator,
                    verify_tracked_hashes=False,
                )
            finally:
                repair.REFERENCE_SOURCE = original_reference
                repair.ANCHOR_SPECS = original_anchors
            self.assertEqual(manifest["candidate_count"], 4)
            self.assertEqual(manifest["objective_rejection_count"], 1)


if __name__ == "__main__":
    unittest.main()
