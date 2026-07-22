from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from expressive_clone_candidates import (
    comparison_candidate_keys,
    expressive_clone_candidate,
    expressive_clone_candidate_catalog,
    expressive_clone_candidate_status,
    expressive_clone_candidates,
    primary_candidate_keys,
    required_next_blind_round_candidate_keys,
)


class ExpressiveCloneCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.cache = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def install_requirement(self, requirement) -> Path:
        repository = self.cache / (
            "models--" + requirement.repo_id.replace("/", "--")
        )
        snapshot = repository / "snapshots" / requirement.revision
        for relative in requirement.required_paths:
            path = snapshot / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        return snapshot

    def test_primary_order_matches_the_measured_comparison_plan(self) -> None:
        self.assertEqual(
            primary_candidate_keys(),
            (
                "fish_s2_pro",
                "chatterbox_original",
                "chatterbox_turbo",
                "tada_1b",
                "moss_tts_nano",
                "moss_tts_local_v15",
            ),
        )
        self.assertEqual(
            comparison_candidate_keys(),
            (
                "qwen_icl_patch_baseline",
                "voxcpm2_baseline",
            ),
        )
        self.assertEqual(
            required_next_blind_round_candidate_keys(),
            ("voxcpm2_baseline",),
        )
        self.assertEqual(
            [item.benchmark_order for item in expressive_clone_candidates()],
            list(range(1, 9)),
        )

    def test_candidate_revisions_and_control_modes_are_pinned(self) -> None:
        fish = expressive_clone_candidate("fish_s2_pro")
        self.assertEqual(
            fish.model_repo_id,
            "mlx-community/fish-audio-s2-pro",
        )
        self.assertEqual(
            fish.repository_requirements[0].revision,
            "eccd57bf5c1ebc13cb2f993df867f4e49931a36a",
        )
        self.assertEqual(fish.control_mode, "inline_freeform_tags")

        turbo = expressive_clone_candidate("chatterbox_turbo")
        self.assertEqual(
            turbo.model_repo_id,
            "mlx-community/chatterbox-turbo-4bit",
        )
        self.assertEqual(turbo.control_mode, "native_event_tags")

        tada = expressive_clone_candidate("tada_1b")
        self.assertEqual(tada.control_mode, "reference_style_bank")
        self.assertEqual(tada.license_id, "llama3.2")

    def test_every_candidate_is_evaluation_only(self) -> None:
        for candidate in expressive_clone_candidates():
            payload = candidate.as_dict()
            self.assertTrue(payload["evaluation_only"])
            self.assertFalse(payload["production_assignment_supported"])
            self.assertFalse(payload["delivery_control_validated"])

    def test_missing_exact_snapshots_fail_closed(self) -> None:
        with (
            patch(
                "expressive_clone_candidates._module_available",
                return_value=True,
            ),
            patch(
                "hf_access.huggingface_cache_roots",
                return_value=(self.cache,),
            ),
        ):
            status = expressive_clone_candidate_status(
                "fish_s2_pro",
                cache_dir=self.cache,
            )
        self.assertFalse(status["ready_for_benchmark"])
        self.assertEqual(
            status["blockers"][0]["code"],
            "candidate_download_required",
        )
        self.assertIn(
            "mlx-community/fish-audio-s2-pro",
            status["blockers"][0]["message"],
        )

    def test_complete_exact_snapshots_enable_benchmark_only(self) -> None:
        candidate = expressive_clone_candidate("chatterbox_original")
        for requirement in candidate.repository_requirements:
            self.install_requirement(requirement)
        with patch(
            "expressive_clone_candidates._module_available",
            return_value=True,
        ):
            status = expressive_clone_candidate_status(
                candidate.key,
                cache_dir=self.cache,
            )
        self.assertTrue(status["ready_for_benchmark"])
        self.assertEqual(status["blockers"], [])
        self.assertTrue(status["evaluation_only"])
        self.assertFalse(status["production_assignment_supported"])
        self.assertEqual(len(status["repositories"]), 2)

    def test_upstream_pytorch_cache_does_not_count_as_mlx_candidate(self) -> None:
        upstream = (
            self.cache
            / "models--ResembleAI--chatterbox-turbo"
            / "snapshots"
            / ("a" * 40)
        )
        upstream.mkdir(parents=True)
        (upstream / "t3_turbo_v1.safetensors").write_bytes(b"fixture")
        with (
            patch(
                "expressive_clone_candidates._module_available",
                return_value=True,
            ),
            patch(
                "hf_access.huggingface_cache_roots",
                return_value=(self.cache,),
            ),
        ):
            status = expressive_clone_candidate_status(
                "chatterbox_turbo",
                cache_dir=self.cache,
            )
        self.assertFalse(status["ready_for_benchmark"])
        self.assertEqual(status["repositories"][0]["cache"]["state"], "missing")

    def test_catalog_never_enables_implicit_download_or_promotion(self) -> None:
        with (
            patch(
                "expressive_clone_candidates._module_available",
                return_value=True,
            ),
            patch(
                "expressive_clone_candidates._package_version",
                return_value="0.4.5",
            ),
        ):
            catalog = expressive_clone_candidate_catalog(cache_dir=self.cache)
        self.assertEqual(catalog["mlx_audio_version"], "0.4.5")
        self.assertEqual(catalog["candidate_count"], 8)
        self.assertEqual(
            catalog["required_next_blind_round_candidate_keys"],
            ["voxcpm2_baseline"],
        )
        voxcpm = next(
            item for item in catalog["candidates"]
            if item["candidate"]["key"] == "voxcpm2_baseline"
        )
        self.assertTrue(voxcpm["candidate"]["required_next_blind_round"])
        self.assertFalse(catalog["implicit_downloads_allowed"])
        self.assertTrue(catalog["manual_listening_required"])
        self.assertFalse(catalog["production_promotion_allowed"])


if __name__ == "__main__":
    unittest.main()
