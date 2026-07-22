from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = (
    ROOT
    / "benchmarks"
    / "results"
    / "20260721T182355Z_emotional_clone_followup_narrator.json"
)
CORRECTED = (
    ROOT
    / "benchmarks"
    / "results"
    / "20260721T201257Z_cosyvoice3_corrected_identity_followup.json"
)
HUMAN_REVIEW = (
    ROOT
    / "benchmarks"
    / "results"
    / "20260721T204043Z_cosyvoice3_corrected_identity_human_review.json"
)
INDEX_FINALIST = (
    ROOT
    / "benchmarks"
    / "results"
    / "20260721T220419Z_indextts2_finalist_expansion.json"
)
INDEX_HUMAN = (
    ROOT
    / "benchmarks"
    / "results"
    / "20260721T231229Z_indextts2_finalist_human_review.json"
)
INDEX_SALVAGE = (
    ROOT
    / "benchmarks"
    / "results"
    / "20260721T231229Z_indextts2_salvage_happy_objective.json"
)
INDEX_EMOTION_SPEED = (
    ROOT
    / "benchmarks"
    / "results"
    / "20260722T004820Z_indextts2_emotion_bank_speed_expansion.json"
)
NARRATOR_BANK_HUMAN = (
    ROOT
    / "benchmarks"
    / "results"
    / "20260722T_narrator_emotion_bank_speed_human_review.json"
)
CROSS_SPEAKER = (
    ROOT
    / "benchmarks"
    / "results"
    / "20260722T_cross_speaker_emotion_bank_objective.json"
)
FIVE_LANE = (
    ROOT
    / ".omo"
    / "evidence"
    / "b17-t05-four-voice-emotion-matrix"
    / "objective_summary.json"
)
FIVE_LANE_REVIEW = (
    ROOT
    / ".omo"
    / "evidence"
    / "b17-t05-four-voice-emotion-matrix"
    / "review"
    / "manifest.json"
)
FIVE_LANE_HUMAN = (
    ROOT
    / ".omo"
    / "evidence"
    / "b17-t05-four-voice-emotion-matrix"
    / "human_review_summary.json"
)
DOCTOR_RELIEF_FOLLOWUP = (
    ROOT
    / ".omo"
    / "evidence"
    / "b17-t05-four-voice-emotion-matrix"
    / "doctor-relief-followup.html"
)
SALVAGE_OBJECTIVE = (
    ROOT
    / ".omo"
    / "evidence"
    / "b17-t05-reference-transfer-salvage"
    / "objective_summary.json"
)
SALVAGE_MANIFEST = (
    ROOT
    / ".omo"
    / "evidence"
    / "b17-t05-reference-transfer-salvage"
    / "manifest.json"
)
SALVAGE_HUMAN = (
    ROOT
    / ".omo"
    / "evidence"
    / "b17-t05-reference-transfer-salvage"
    / "human_review_summary.json"
)
WINNER_MANIFEST = (
    ROOT
    / ".omo"
    / "evidence"
    / "b17-t05-reference-transfer-salvage"
    / "winner-validation"
    / "manifest.json"
)
WINNER_OBJECTIVE = (
    ROOT
    / ".omo"
    / "evidence"
    / "b17-t05-reference-transfer-salvage"
    / "winner-validation"
    / "objective_summary.json"
)
WINNER_REVIEW = (
    ROOT
    / ".omo"
    / "evidence"
    / "b17-t05-reference-transfer-salvage"
    / "winner-validation"
    / "review"
    / "manifest.json"
)
NEXT_MODEL_SCREEN = (
    ROOT
    / ".omo"
    / "evidence"
    / "b17-t05-next-multimodel-screen"
    / "model_screen.json"
)
MODEL_HANDLING = (
    ROOT
    / ".omo"
    / "evidence"
    / "b17-t05-next-multimodel-screen"
    / "model_handling_contracts.json"
)
SALVAGE_VERIFICATION = (
    ROOT
    / ".omo"
    / "evidence"
    / "b17-t05-reference-transfer-salvage"
    / "verification.json"
)
DOC = ROOT / "docs" / "EMOTIONAL_CLONE_FOLLOWUP_EVALUATION.md"


class EmotionalCloneFollowupEvidenceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        cls.corrected = json.loads(CORRECTED.read_text(encoding="utf-8"))
        cls.human_review = json.loads(HUMAN_REVIEW.read_text(encoding="utf-8"))
        cls.index_finalist = json.loads(INDEX_FINALIST.read_text(encoding="utf-8"))
        cls.index_human = json.loads(INDEX_HUMAN.read_text(encoding="utf-8"))
        cls.index_salvage = json.loads(INDEX_SALVAGE.read_text(encoding="utf-8"))
        cls.index_emotion_speed = json.loads(
            INDEX_EMOTION_SPEED.read_text(encoding="utf-8")
        )
        cls.narrator_bank_human = json.loads(
            NARRATOR_BANK_HUMAN.read_text(encoding="utf-8")
        )
        cls.cross_speaker = json.loads(CROSS_SPEAKER.read_text(encoding="utf-8"))
        cls.five_lane = json.loads(FIVE_LANE.read_text(encoding="utf-8"))
        cls.five_lane_review = json.loads(
            FIVE_LANE_REVIEW.read_text(encoding="utf-8")
        )
        cls.five_lane_human = json.loads(
            FIVE_LANE_HUMAN.read_text(encoding="utf-8")
        )
        cls.salvage_objective = json.loads(
            SALVAGE_OBJECTIVE.read_text(encoding="utf-8")
        )
        cls.salvage_manifest = json.loads(
            SALVAGE_MANIFEST.read_text(encoding="utf-8")
        )
        cls.salvage_human = json.loads(
            SALVAGE_HUMAN.read_text(encoding="utf-8")
        )
        cls.winner_manifest = json.loads(
            WINNER_MANIFEST.read_text(encoding="utf-8")
        )
        cls.winner_objective = json.loads(
            WINNER_OBJECTIVE.read_text(encoding="utf-8")
        )
        cls.winner_review = json.loads(
            WINNER_REVIEW.read_text(encoding="utf-8")
        )
        cls.next_model_screen = json.loads(
            NEXT_MODEL_SCREEN.read_text(encoding="utf-8")
        )
        cls.model_handling = json.loads(
            MODEL_HANDLING.read_text(encoding="utf-8")
        )
        cls.salvage_verification = json.loads(
            SALVAGE_VERIFICATION.read_text(encoding="utf-8")
        )

    def test_real_narrator_fixture_is_fingerprinted_not_embedded(self) -> None:
        reference = self.evidence["reference"]
        self.assertEqual(
            reference["kind"],
            "real_NARRATOR_supplied_recording_clone",
        )
        self.assertRegex(reference["audio_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(reference["target_text_sha256"], r"^[0-9a-f]{64}$")

        rendered = EVIDENCE.read_text(encoding="utf-8")
        self.assertNotIn("This is the story of a man named Stanley", rendered)
        self.assertNotIn(
            "He just couldn't shake the terrible feeling",
            rendered,
        )
        self.assertNotIn("narrator_attention_r8_pilot", rendered)

    def test_exact_candidate_revisions_and_sample_counts_are_recorded(self) -> None:
        candidates = self.evidence["candidates"]

        openvoice = candidates["openvoice_v2"]
        self.assertEqual(openvoice["sample_count"], 18)
        self.assertEqual(
            [item["revision"] for item in openvoice["models"]],
            [
                "c70fc8b939bd1d8213994ff7c88e32be39708271",
                "f36e7edfe1684461a8343844af60babc2efbb727",
            ],
        )

        indextts = candidates["indextts2"]
        self.assertEqual(indextts["sample_count"], 8)
        self.assertEqual(
            indextts["model"]["revision"],
            "740dcaff396282ffb241903d150ac011cd4b1ede",
        )

        cosyvoice = candidates["cosyvoice3"]
        self.assertEqual(cosyvoice["sample_count"], 6)
        self.assertEqual(
            cosyvoice["model"]["revision"],
            "29e01c4e8d000f4bcd70751be16fa94bf3d85a18",
        )
        self.assertEqual(
            sum(item["sample_count"] for item in candidates.values()),
            32,
        )

    def test_objective_results_preserve_the_actual_speed_disposition(self) -> None:
        candidates = self.evidence["candidates"]
        self.assertLess(
            candidates["openvoice_v2"]["mean_real_time_factor"],
            1.0,
        )
        self.assertGreater(
            candidates["indextts2"]["mean_real_time_factor"],
            5.0,
        )
        self.assertGreater(
            candidates["cosyvoice3"]["mean_real_time_factor"],
            3.0,
        )
        self.assertIn(
            "final_istft_on_cpu",
            candidates["cosyvoice3"]["device"],
        )
        self.assertTrue(candidates["indextts2"]["strict_mps_no_cpu_fallback"])

    def test_all_samples_have_complete_pinned_evaluator_results(self) -> None:
        evaluators = self.evidence["evaluators"]
        transcription = evaluators["transcription"]
        self.assertTrue(transcription["available"])
        self.assertTrue(transcription["complete"])
        self.assertEqual(transcription["success_count"], 32)
        self.assertEqual(transcription["failure_count"], 0)
        self.assertEqual(
            transcription["revision"],
            "1e3e249fb8d01c655324bd6841b1deadffd6d04c",
        )
        self.assertEqual(
            evaluators["speaker_similarity"]["revision"],
            "e7dd0585652209fa0d7783659aad4e8a324de11c",
        )

    def test_listening_gate_blocks_every_production_claim(self) -> None:
        review = self.evidence["review"]
        acceptance = self.evidence["acceptance"]
        self.assertEqual(review["sample_count"], 32)
        self.assertTrue(review["manual_blinded_review_required"])
        self.assertTrue(review["automatic_transcription_complete"])
        self.assertTrue(review["speaker_similarity_complete"])
        self.assertFalse(acceptance["production_promotion_allowed"])
        for candidate in self.evidence["candidates"].values():
            self.assertFalse(candidate["production_promotion_allowed"])

    def test_corrected_cosyvoice_rerun_rejects_long_prompt_leakage(self) -> None:
        result = self.corrected["corrected_cosyvoice3_objective_results"]
        self.assertEqual(
            result["full_persistent_description_mps"]["perfect_transcripts"],
            0,
        )
        self.assertEqual(
            result["full_persistent_description_cpu"]["perfect_transcripts"],
            1,
        )
        self.assertEqual(
            result["concise_persistent_description_mps"]["perfect_transcripts"],
            4,
        )
        self.assertEqual(
            result["concise_persistent_description_mps"]["mean_word_error_rate"],
            0.0,
        )
        conclusion = self.corrected["technical_conclusion"]
        self.assertFalse(conclusion["full_description_supported"])
        self.assertTrue(conclusion["concise_description_text_stable"])
        self.assertEqual(
            conclusion["identity_acceptance"],
            "pending_focused_blinded_listening",
        )
        self.assertFalse(conclusion["production_promotion_allowed"])

    def test_human_scores_override_misleading_speaker_cosine(self) -> None:
        prior = self.corrected["prior_blinded_human_result"]
        self.assertEqual(prior["cosyvoice3_original_instruct"]["mean_narrator_identity"], 1.0)
        self.assertEqual(prior["cosyvoice3_original_instruct"]["approved_samples"], 0)
        self.assertEqual(prior["openvoice_v2"]["approved_samples"], 0)
        self.assertEqual(prior["indextts2"]["approved_samples"], 6)
        self.assertGreater(prior["indextts2"]["mean_narrator_identity"], 2.5)

    def test_corrected_cosyvoice_human_review_selects_indextts2_only(self) -> None:
        acceptance = self.human_review["acceptance"]
        self.assertFalse(acceptance["cosyvoice3_accepted_for_this_narrator"])
        self.assertEqual(acceptance["cosyvoice3_finalist_status"], "rejected")
        self.assertEqual(acceptance["quality_finalist"], "IndexTTS2")
        self.assertEqual(acceptance["samples_meeting_identity_floor"], 0)
        self.assertFalse(acceptance["production_promotion_allowed"])
        summary = self.human_review["conditioning_summary"]
        self.assertEqual(
            summary["exact_transcript_zero_shot_mps"]["mean_narrator_identity"],
            2.75,
        )
        self.assertEqual(
            summary["concise_persistent_profile_mps"]["mean_narrator_identity"],
            1.0625,
        )

    def test_indextts2_finalist_matrix_is_complete_and_text_private(self) -> None:
        matrix = self.index_finalist["matrix"]
        self.assertEqual(matrix["total_sample_count"], 30)
        self.assertEqual(matrix["perfect_transcript_count"], 30)
        self.assertEqual(
            matrix["category_counts"],
            {
                "reference_selection": 6,
                "emotion_strength": 8,
                "beam_count": 4,
                "seed_stability": 6,
                "unseen_short": 3,
                "long_form": 3,
            },
        )
        self.assertRegex(matrix["output_tree_sha256"], r"^[0-9a-f]{64}$")
        self.assertFalse(self.index_finalist["fixture"]["text_embedded_in_evidence"])
        rendered = INDEX_FINALIST.read_text(encoding="utf-8")
        self.assertNotIn("He just couldn't shake", rendered)
        self.assertNotIn("Stanley paused at the doorway", rendered)
        self.assertNotIn("The lights behind him went dark", rendered)

    def test_indextts2_one_beam_is_bounded_not_production_acceleration(self) -> None:
        beam = self.index_finalist["beam_count"]
        self.assertLess(
            beam["identity"]["one_beam"]["real_time_factor"],
            beam["identity"]["three_beams"]["real_time_factor"],
        )
        self.assertLess(
            beam["melancholic_0_55"]["one_beam"]["real_time_factor"],
            beam["melancholic_0_55"]["three_beams"]["real_time_factor"],
        )
        self.assertGreater(
            beam["melancholic_0_55"]["one_beam_rtf_reduction_fraction"],
            0.3,
        )
        performance = self.index_finalist["performance_disposition"]
        self.assertTrue(performance["gpt_is_primary_measured_bottleneck"])
        self.assertGreater(performance["gpt_share_observed_range"][0], 0.6)
        self.assertFalse(performance["real_time_achieved"])
        self.assertTrue(performance["one_beam_is_bounded_improvement_only"])

    def test_indextts2_human_review_accepts_identity_and_melancholic_only(self) -> None:
        review = self.index_human
        self.assertEqual(review["review_status"]["received_review_pages"], 5)
        self.assertEqual(review["review_status"]["missing_review_page"], "reference_selection")
        self.assertGreater(review["selected_reference"]["mean_narrator_identity"], 4.7)
        directions = review["direction_summary"]
        self.assertEqual(directions["melancholic"]["approved_count"], 8)
        self.assertEqual(directions["afraid"]["approved_count"], 0)
        self.assertEqual(directions["sad"]["approved_count"], 1)
        disposition = review["human_disposition"]
        self.assertIn("strength 0.55", disposition["melancholic"])
        self.assertIn("rejected", disposition["sad"])
        self.assertIn("rejected", disposition["afraid"])
        self.assertFalse(disposition["production_promotion_allowed"])

    def test_indextts2_salvage_is_unstable_and_happy_remains_listening_gated(self) -> None:
        salvage = self.index_salvage
        probes = salvage["salvage_control_probes"]
        self.assertEqual(
            probes["text_derived_grief_vector"]["status"],
            "rejected_generation_unstable",
        )
        self.assertEqual(
            probes["sad_melancholic_blend"]["status"],
            "rejected_generation_unstable",
        )
        self.assertEqual(
            probes["afraid_surprised_blend"]["status"],
            "rejected_generation_unstable",
        )
        happy = salvage["happy_generalization"]
        self.assertEqual(happy["sample_count"], 3)
        self.assertEqual(happy["perfect_transcripts"], 3)
        self.assertGreater(happy["samples"][1]["real_time_factor"], 12.0)
        self.assertGreater(
            happy["samples"][1]["real_time_factor"],
            happy["samples"][0]["real_time_factor"] * 2.0,
        )
        self.assertTrue(happy["review"]["manual_listening_required"])
        self.assertFalse(salvage["production_promotion_allowed"])

    def test_indextts2_emotion_bank_covers_broad_modes_with_exact_text(self) -> None:
        bank = self.index_emotion_speed["emotion_bank"]
        self.assertEqual(bank["sample_count"], 12)
        self.assertEqual(bank["perfect_transcript_count"], 12)
        self.assertEqual(
            set(bank["directions"]),
            {
                "sad",
                "fear",
                "angry",
                "happy",
                "excited",
                "friendly",
                "surprised",
                "whisper",
                "shout",
            },
        )
        self.assertEqual(len(bank["samples"]), 12)
        self.assertTrue(
            all(item["word_error_rate"] == 0.0 for item in bank["samples"])
        )
        self.assertTrue(
            all(len(item["audio_sha256"]) == 64 for item in bank["samples"])
        )
        architecture = self.index_emotion_speed["architecture_decision"]
        self.assertTrue(architecture["reviewed_emotion_bank_added_as_candidate_path"])
        self.assertFalse(architecture["speech_to_speech_used"])

    def test_indextts2_speed_stack_selects_two_workers_and_greedy(self) -> None:
        speed = self.index_emotion_speed["speed_stack"]
        fixed = speed["fixed_identity_probe"]
        self.assertLess(
            fixed["fp32_metal_greedy_8_steps_rtf"],
            fixed["fp32_mps_fast_math_metal_rtf"],
        )
        self.assertGreater(
            fixed["fp16_rtf"],
            fixed["fp32_mps_fast_math_metal_rtf"],
        )
        pool = speed["warm_pool"]
        self.assertEqual(pool["selected_worker_count"], 2)
        self.assertLess(
            pool["two_workers_greedy_8_steps_aggregate_rtf"],
            pool["sequential_one_worker_12_steps_aggregate_rtf"],
        )
        self.assertLess(
            pool["two_workers_greedy_8_steps_emotion_reference_aggregate_rtf"],
            pool["two_workers_greedy_8_steps_aggregate_rtf"],
        )
        self.assertGreater(
            pool["three_workers_12_steps_aggregate_rtf"],
            pool["two_workers_12_steps_aggregate_rtf"],
        )

    def test_indextts2_mlx_block_mapping_has_numerical_parity(self) -> None:
        mlx = self.index_emotion_speed["mlx_decoder_feasibility"]
        self.assertTrue(mlx["parity_passed"])
        self.assertLess(mlx["max_absolute_error"], 0.0001)
        self.assertLess(mlx["mean_absolute_error"], 0.000001)
        self.assertGreater(mlx["cosine_similarity"], 0.999999999)
        self.assertIn("24-layer transformer", mlx["next_increment"])

    def test_emotion_speed_review_remains_human_license_and_production_gated(self) -> None:
        review = self.index_emotion_speed["review"]
        acceptance = self.index_emotion_speed["acceptance"]
        self.assertEqual(review["page_count"], 9)
        self.assertEqual(review["sample_count"], 16)
        self.assertTrue(review["manual_blinded_review_required"])
        self.assertTrue(review["answer_keys_separate"])
        self.assertFalse(acceptance["human_emotion_bank_scores_complete"])
        self.assertFalse(acceptance["human_speed_quality_scores_complete"])
        self.assertFalse(acceptance["license_review_complete"])
        self.assertFalse(acceptance["production_promotion_allowed"])
        self.assertFalse(acceptance["production_registry_changed"])
        self.assertFalse(acceptance["voice_assignment_changed"])
        self.assertFalse(acceptance["live_project_audio_changed"])

    def test_emotion_speed_evidence_stores_hashes_not_fixture_text(self) -> None:
        rendered = INDEX_EMOTION_SPEED.read_text(encoding="utf-8")
        self.assertNotIn("He lowered his eyes", rendered)
        self.assertNotIn("A floorboard creaked", rendered)
        self.assertNotIn("Get out of the building", rendered)
        self.assertNotIn("He just couldn't shake", rendered)

    def test_narrator_bank_human_review_selects_greedy_and_restricts_modes(self) -> None:
        review = self.narrator_bank_human
        self.assertEqual(review["received_export_count"], 10)
        self.assertEqual(review["deduplicated_sample_count"], 16)
        self.assertEqual(review["duplicate_exports"][0]["logical_page"], "sad")
        disposition = review["disposition"]
        self.assertEqual(disposition["speed_default"], "fp32_greedy_8_steps")
        self.assertTrue(disposition["speed_default_accepted"])
        self.assertEqual(disposition["friendly"], "accepted")
        self.assertEqual(disposition["shout"], "accepted")
        self.assertIn("restriction", disposition["whisper"])
        self.assertIn("rejected", disposition["fear"])
        self.assertIn("rejected", disposition["surprised"])
        self.assertIn("rejected", disposition["happy"])
        self.assertIn("rejected", disposition["excited"])
        self.assertFalse(disposition["broad_emotion_support_proven"])
        self.assertFalse(disposition["production_promotion_allowed"])

    def test_cross_speaker_matrix_is_complete_exact_and_human_gated(self) -> None:
        evidence = self.cross_speaker
        self.assertEqual(
            evidence["shared_emotion_modes"],
            [
                "neutral",
                "sad",
                "fear",
                "angry",
                "happy",
                "excited",
                "friendly",
                "surprised",
                "whisper",
                "shout",
            ],
        )
        aggregate = evidence["aggregate"]
        self.assertEqual(aggregate["sample_count"], 20)
        self.assertEqual(aggregate["perfect_transcript_count"], 20)
        self.assertTrue(aggregate["mechanical_generation_complete"])
        self.assertTrue(aggregate["objective_identity_preserved_across_all_modes"])
        self.assertTrue(aggregate["human_delivery_generalization_pending"])
        benny = evidence["speakers"]["benny"]
        doctor = evidence["speakers"]["doctor"]
        self.assertEqual(benny["sample_count"], 10)
        self.assertEqual(doctor["sample_count"], 10)
        self.assertEqual(benny["perfect_transcript_count"], 10)
        self.assertEqual(doctor["perfect_transcript_count"], 10)
        self.assertLess(benny["mean_real_time_factor"], doctor["mean_real_time_factor"])
        self.assertRegex(benny["reference_audio_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(doctor["reference_audio_sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(evidence["review"]["unique_browser_storage_per_page"])
        self.assertTrue(evidence["review"]["manual_blinded_review_required"])
        self.assertFalse(
            evidence["acceptance"]["cross_speaker_emotion_support_accepted"]
        )
        self.assertFalse(evidence["acceptance"]["production_promotion_allowed"])

    def test_cross_speaker_evidence_stores_hashes_not_test_lines(self) -> None:
        rendered = CROSS_SPEAKER.read_text(encoding="utf-8")
        self.assertNotIn("There was something wrong with the room", rendered)
        self.assertNotIn("After everything I did for you", rendered)
        self.assertNotIn("At last, the doors swung open", rendered)
        self.assertNotIn("Run! Get out of the building now", rendered)

    def test_durable_five_lane_matrix_covers_core_emotions_and_capabilities(self) -> None:
        evidence = self.five_lane
        design = evidence["experimental_design"]
        self.assertEqual(design["direct_non_cloned_control"], "qwen_direct")
        self.assertEqual(design["same_model_same_voice_upper_bound"], "generic_ryan")
        self.assertEqual(
            design["cross_identity_transfer_lanes"],
            ["narrator", "benny", "doctor"],
        )
        self.assertEqual(design["index_model_lane_count"], 4)
        self.assertEqual(design["total_lane_count"], 5)
        self.assertEqual(design["style_count"], 22)
        self.assertEqual(evidence["sample_count"], 110)
        self.assertEqual(evidence["perfect_transcript_count"], 105)
        self.assertEqual(evidence["nonzero_wer_count"], 5)
        review = self.five_lane_review
        styles = set(review["styles"])
        self.assertTrue(
            {
                "neutral",
                "happy",
                "sad",
                "angry",
                "fear",
                "surprised",
                "disgust",
            }.issubset(styles)
        )
        self.assertTrue(
            {
                "grief",
                "panic",
                "relief",
                "contempt",
                "tender",
                "pleading",
                "sarcastic",
                "calm",
                "urgent",
                "exhausted",
                "authoritative",
                "whisper",
                "shout",
            }.issubset(styles)
        )

    def test_durable_five_lane_review_is_self_contained_and_human_gated(self) -> None:
        review = self.five_lane_review
        self.assertEqual(review["page_count"], 22)
        self.assertEqual(review["sample_count"], 110)
        self.assertEqual(review["lanes"], [
            "qwen_direct",
            "generic_ryan",
            "narrator",
            "benny",
            "doctor",
        ])
        self.assertTrue(review["candidate_lane_hidden"])
        self.assertTrue(review["expected_identity_visible"])
        self.assertTrue(review["all_audio_copied_into_review_folder"])
        self.assertFalse(review["temporary_paths_required"])
        self.assertFalse(review["production_promotion_allowed"])
        self.assertEqual(len(review["page_info"]), 22)
        storage_keys = {
            item["storage_key"] for item in review["page_info"].values()
        }
        self.assertEqual(len(storage_keys), 22)
        self.assertTrue(
            all(item["sample_count"] == 5 for item in review["page_info"].values())
        )
        acceptance = self.five_lane["acceptance"]
        self.assertTrue(acceptance["manual_scores_complete"])
        self.assertEqual(acceptance["doctor_relief_disposition"], "restricted_pass")
        self.assertFalse(acceptance["license_review_complete"])
        self.assertFalse(acceptance["production_promotion_allowed"])
        self.assertFalse(acceptance["production_registry_changed"])
        self.assertFalse(acceptance["voice_assignment_changed"])
        self.assertFalse(acceptance["live_project_audio_changed"])

    def test_durable_five_lane_human_review_classifies_source_transfer_and_speaker_failures(self) -> None:
        review = self.five_lane_human
        source = review["source"]
        self.assertEqual(source["export_count"], 22)
        self.assertEqual(source["raw_score_row_count"], 110)
        self.assertEqual(source["complete_score_row_count"], 110)
        self.assertEqual(source["incomplete_rows"], [])
        self.assertEqual(
            source["doctor_relief_followup"]["sample_id"]
            if "sample_id" in source["doctor_relief_followup"]
            else source["doctor_relief_followup"]["review_sample_id"],
            "c712ca1bb47a58a6",
        )
        aggregate = review["aggregate"]
        self.assertEqual(aggregate["completed_approvals"], 83)
        self.assertEqual(aggregate["completed_rejections"], 27)
        self.assertEqual(aggregate["human_confirmed_text_match_count"], 110)
        lanes = review["lane_summary"]
        self.assertEqual(lanes["qwen_direct"]["approvals"], 19)
        self.assertEqual(lanes["generic_ryan"]["approvals"], 12)
        self.assertEqual(lanes["narrator"]["approvals"], 16)
        self.assertEqual(lanes["benny"]["approvals"], 17)
        self.assertEqual(lanes["doctor"]["approvals"], 19)
        disposition = review["human_disposition"]
        self.assertIn("happy", disposition["strong_cross_identity"])
        self.assertIn("panic", disposition["failed_or_unusable"])
        self.assertIn("fear", disposition["not_proven_despite_comparison_approvals"])
        self.assertTrue(
            disposition["next_work"]["speaker_specific_bank_metadata_required"]
        )
        self.assertTrue(disposition["manual_scores_complete"])
        self.assertFalse(disposition["production_promotion_allowed"])

    def test_doctor_relief_followup_is_single_sample_and_durable(self) -> None:
        source = DOCTOR_RELIEF_FOLLOWUP.read_text(encoding="utf-8")
        self.assertEqual(source.count('class="sample"'), 1)
        self.assertIn('data-sample-id="c712ca1bb47a58a6"', source)
        self.assertIn("Expected identity: Doctor", source)
        self.assertIn("Requested delivery</dt><dd>Relief", source)
        self.assertIn("review/pages/relief/audio/sample_c712ca1bb47a58a6.wav", source)
        self.assertNotIn("/tmp/", source)
        self.assertNotIn("/private/tmp/", source)

    def test_targeted_salvage_separates_acting_reference_and_transfer_strength(self) -> None:
        salvage = self.salvage_objective
        acting = salvage["acting_reference_candidates"]
        self.assertEqual(acting["sample_count"], 18)
        self.assertEqual(acting["perfect_transcript_count"], 13)
        self.assertEqual(
            set(acting["styles"]),
            {"fear", "panic", "disgust", "contempt", "relief", "urgent"},
        )
        panic = next(
            item for item in acting["nonzero_wer_samples"]
            if item["sample_id"] == "panic_acting_v3_5306"
        )
        self.assertIn("inserted_laughter", panic["disposition"])
        transfer = salvage["generic_ryan_transfer_strength"]
        self.assertEqual(transfer["sample_count"], 15)
        self.assertEqual(transfer["perfect_transcript_count"], 15)
        self.assertEqual(transfer["strengths"], [0.7, 0.85, 1.0])
        self.assertEqual(
            set(transfer["styles"]),
            {"calm", "pleading", "whisper", "sarcastic", "shout"},
        )
        self.assertTrue(transfer["runtime_controls"]["greedy_generation"])
        self.assertEqual(transfer["runtime_controls"]["diffusion_steps_override"], 8)

    def test_targeted_salvage_review_is_compact_durable_and_production_gated(self) -> None:
        manifest = self.salvage_manifest
        self.assertEqual(manifest["total_followup_pages"], 12)
        self.assertEqual(manifest["total_followup_samples"], 34)
        self.assertEqual(manifest["acting_reference_review"]["sample_count"], 18)
        self.assertEqual(manifest["transfer_strength_review"]["sample_count"], 15)
        self.assertEqual(manifest["transfer_strength_review"]["strengths"], [0.7, 0.85, 1.0])
        self.assertFalse(manifest["temporary_paths_required"])
        self.assertTrue(manifest["manual_blinded_review_required"])
        self.assertFalse(manifest["production_promotion_allowed"])
        acceptance = self.salvage_objective["acceptance"]
        self.assertTrue(acceptance["doctor_relief_score_complete"])
        self.assertTrue(acceptance["acting_reference_scores_complete"])
        self.assertTrue(acceptance["transfer_strength_scores_complete"])
        self.assertFalse(acceptance["production_promotion_allowed"])

    def test_targeted_salvage_human_review_preserves_complete_unblinding_and_selections(self) -> None:
        review = self.salvage_human
        source = review["source"]
        self.assertEqual(source["input_file_count"], 12)
        self.assertEqual(source["raw_score_row_count"], 34)
        self.assertEqual(source["complete_score_row_count"], 34)
        self.assertIn("sample_id", source["unblinding_method"])
        self.assertEqual(
            review["selected_acting_references"]["fear"]["winner"]["source_sample_id"],
            "fear_acting_v2_5302",
        )
        self.assertEqual(
            review["selected_acting_references"]["panic"]["winner"]["source_sample_id"],
            "panic_acting_v2_5305",
        )
        self.assertIsNone(
            review["selected_acting_references"]["disgust"]["winner"]
        )
        self.assertEqual(
            review["selected_acting_references"]["contempt"]["winner"]["source_sample_id"],
            "contempt_acting_v3_5312",
        )
        self.assertEqual(
            review["selected_acting_references"]["relief"]["winner"]["source_sample_id"],
            "relief_acting_v2_5314",
        )
        self.assertEqual(
            review["selected_acting_references"]["urgent"]["winner"]["source_sample_id"],
            "urgent_acting_v3_5318",
        )
        self.assertEqual(
            review["selected_transfer_strengths"]["calm"]["winner_strength"],
            0.7,
        )
        self.assertEqual(
            review["selected_transfer_strengths"]["pleading"]["winner_strength"],
            1.0,
        )
        self.assertIsNone(
            review["selected_transfer_strengths"]["whisper"]["winner_strength"]
        )
        self.assertIsNone(
            review["selected_transfer_strengths"]["sarcastic"]["winner_strength"]
        )
        self.assertEqual(
            review["selected_transfer_strengths"]["shout"]["winner_strength"],
            1.0,
        )
        self.assertEqual(
            review["doctor_relief_disposition"]["disposition"],
            "restricted_pass",
        )
        acting_rows = review["sample_level_scores"]["acting_reference"]
        panic_v3 = next(
            row for row in acting_rows
            if row["source_sample_id"] == "panic_acting_v3_5306"
        )
        self.assertAlmostEqual(panic_v3["word_error_rate"], 0.47058823529411764)
        self.assertIn("laughter", panic_v3["rejection_reason"])
        for row in acting_rows:
            self.assertRegex(row["audio_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(row["instruction_sha256"], r"^[0-9a-f]{64}$")
        acceptance = review["acceptance"]
        self.assertTrue(acceptance["all_uploaded_samples_unblinded"])
        self.assertFalse(acceptance["license_review_complete"])
        self.assertFalse(acceptance["production_promotion_allowed"])
        self.assertFalse(acceptance["production_registry_changed"])
        self.assertFalse(acceptance["voice_assignment_changed"])
        self.assertFalse(acceptance["live_project_audio_changed"])

    def test_winner_validation_is_exactly_bounded_and_uses_the_accepted_runtime(self) -> None:
        manifest = self.winner_manifest
        self.assertEqual(manifest["sample_count"], 24)
        self.assertEqual(
            manifest["styles"],
            [
                "fear",
                "panic",
                "contempt",
                "relief",
                "urgent",
                "calm",
                "pleading",
                "shout",
            ],
        )
        self.assertEqual(manifest["speakers"], ["narrator", "benny", "doctor"])
        self.assertFalse(manifest["generic_ryan_regenerated"])
        self.assertFalse(manifest["broad_combinatorial_matrix_generated"])
        runtime = manifest["runtime_profile"]
        self.assertEqual(runtime["candidate"], "IndexTTS2")
        self.assertEqual(runtime["device"], "mps")
        self.assertFalse(runtime["use_fp16"])
        self.assertTrue(runtime["mps_fast_math"])
        self.assertTrue(runtime["mps_prefer_metal"])
        self.assertEqual(runtime["num_beams"], 1)
        self.assertTrue(runtime["greedy_generation"])
        self.assertEqual(runtime["diffusion_steps"], 8)
        self.assertEqual(runtime["persistent_worker_count"], 2)
        self.assertEqual(
            {speaker: sum(row["speaker"] == speaker for row in manifest["samples"])
             for speaker in manifest["speakers"]},
            {"narrator": 8, "benny": 8, "doctor": 8},
        )
        self.assertTrue(
            all(row["speaker"] != "generic_ryan" for row in manifest["samples"])
        )

    def test_winner_validation_review_is_complete_self_contained_and_human_gated(self) -> None:
        objective = self.winner_objective
        self.assertEqual(objective["sample_count"], 24)
        self.assertEqual(objective["perfect_transcript_count"], 24)
        self.assertEqual(objective["max_word_error_rate"], 0.0)
        self.assertEqual(len(objective["samples"]), 24)
        review = objective["review"]
        self.assertEqual(review["page_count"], 8)
        self.assertEqual(review["sample_count"], 24)
        self.assertTrue(review["answer_keys_separate"])
        self.assertTrue(review["unique_autosave_keys"])
        self.assertTrue(review["autosave_on_input"])
        self.assertTrue(review["completion_counter"])
        self.assertTrue(review["next_incomplete_control"])
        self.assertTrue(review["incomplete_export_blocked"])
        self.assertTrue(review["all_audio_copied_into_review_tree"])
        self.assertFalse(review["temporary_paths_required"])
        self.assertEqual(
            len({item["storage_key"] for item in review["page_info"].values()}),
            8,
        )
        self.assertTrue(
            all(item["sample_count"] == 3 for item in review["page_info"].values())
        )
        for page in review["page_info"].values():
            page_path = WINNER_REVIEW.parent / page["review"]
            source = page_path.read_text(encoding="utf-8")
            self.assertIn("Next incomplete", source)
            self.assertIn("complete all required fields", source.lower())
            self.assertNotIn("/tmp/", source)
            self.assertNotIn("/private/tmp/", source)
            audio_dir = page_path.parent / "audio"
            self.assertEqual(len(list(audio_dir.glob("*.wav"))), 3)
            self.assertFalse(any(path.is_symlink() for path in audio_dir.iterdir()))
            evaluation = json.loads(
                (page_path.parent / "evaluation.json").read_text(encoding="utf-8")
            )
            self.assertEqual(evaluation["speaker_evaluation"]["reference_group_count"], 3)
            self.assertTrue(evaluation["speaker_evaluation"]["complete"])
            answer_key = json.loads(
                (WINNER_REVIEW.parent / page["answer_key"]).read_text(encoding="utf-8")
            )
            expected_hashes = evaluation["reference_audio_sha256_by_sample"]
            self.assertEqual(
                {row["source_sample_id"]: row["speaker_reference_sha256"] for row in answer_key},
                expected_hashes,
            )
        self.assertTrue(
            all(
                status == "pending_human_review"
                for speaker in objective["compatibility_status"].values()
                for status in speaker.values()
            )
        )
        self.assertFalse(objective["production_promotion_allowed"])
        self.assertFalse(objective["production_registry_changed"])
        self.assertFalse(objective["voice_assignment_changed"])
        self.assertFalse(objective["live_project_audio_changed"])
        self.assertEqual(self.winner_review["page_count"], 8)
        self.assertEqual(self.winner_review["sample_count"], 24)

    def test_next_multimodel_screen_requires_all_named_models_and_official_docs(self) -> None:
        screen = self.next_model_screen
        self.assertEqual(
            screen["required_screening_lineup"],
            [
                "indextts2",
                "voxcpm2_controllable_clone",
                "qwen3_tts_base_clone_control",
                "fish_s2_pro_reworked",
                "higgs_audio_v25",
                "moss_tts_local_v15",
                "chatterbox_multilingual_v3",
            ],
        )
        self.assertTrue(screen["documentation_first_generation_gate"])
        self.assertIn("valid local cloned-voice audio", screen["screening_to_blind_test_rule"])
        required = {item["key"] for item in screen["required_candidates"]}
        self.assertIn("indextts2", required)
        self.assertIn("voxcpm2_controllable_clone", required)
        self.assertTrue(screen["user_requirement"]["voxcpm2_must_be_included"])
        handling = self.model_handling
        gate = handling["global_generation_gate"]
        self.assertTrue(gate["official_documentation_review_required_before_generation"])
        self.assertTrue(gate["runner_must_follow_model_specific_api"])
        self.assertTrue(gate["shared_one_adapter_fits_all_translation_forbidden"])
        self.assertFalse(gate["production_promotion_allowed"])
        models = handling["models"]
        self.assertEqual(set(models), set(screen["required_screening_lineup"]))
        self.assertTrue(models["voxcpm2_controllable_clone"]["required_next_blind_round"])
        self.assertIn(
            "disables Control Instruction",
            models["voxcpm2_controllable_clone"]["documented_clone_modes"]["ultimate_cloning"],
        )
        self.assertFalse(
            models["qwen3_tts_base_clone_control"]["blind_test_eligible_after_setup_probe"]
        )
        self.assertIn(
            "does not mark instruction control",
            models["qwen3_tts_base_clone_control"]["documented_control_boundary"]["base_model"],
        )
        self.assertIn(
            "inline",
            models["fish_s2_pro_reworked"]["documented_control_surface"].lower(),
        )
        self.assertIn(
            "t3_model='v3'",
            models["chatterbox_multilingual_v3"]["documented_clone_mode"],
        )
        self.assertTrue(
            handling["acceptance"]["documentation_contract_complete_for_required_screening_lineup"]
        )
        self.assertFalse(handling["acceptance"]["exact_setup_probe_complete_for_all_models"])
        self.assertFalse(handling["acceptance"]["production_promotion_allowed"])

    def test_salvage_verification_receipt_closes_safe_local_work_only(self) -> None:
        receipt = self.salvage_verification
        self.assertEqual(receipt["protected_state"]["branch"], "feature/native-ollama-structured-json")
        self.assertEqual(
            receipt["protected_state"]["head"],
            "0cd5b3e6eb758fb62e7f3a2798adb684eaac4922",
        )
        self.assertEqual(receipt["protected_state"]["plan_revision"], "5.1")
        validation = receipt["winner_validation"]
        self.assertEqual(validation["sample_count"], 24)
        self.assertEqual(validation["valid_audio_count"], 24)
        self.assertEqual(validation["result_receipt_count"], 24)
        self.assertEqual(validation["exact_transcript_count"], 24)
        self.assertEqual(validation["review_page_count"], 8)
        self.assertEqual(validation["review_audio_count"], 24)
        self.assertEqual(validation["symlink_count"], 0)
        self.assertTrue(validation["resume_receipt"]["all_samples_fp32_mps_greedy_8_steps"])
        self.assertEqual(validation["resume_receipt"]["all_sample_worker_indices"], [1, 2])
        self.assertTrue(
            receipt["human_review"]["actual_upload_hashes_reverified_from_mnt_data"]
        )
        self.assertEqual(receipt["verification"]["tests_passed"], 74)
        acceptance = receipt["acceptance"]
        self.assertTrue(acceptance["targeted_salvage_human_review_complete"])
        self.assertTrue(acceptance["winner_validation_generation_complete"])
        self.assertTrue(acceptance["winner_validation_objective_evaluation_complete"])
        self.assertFalse(acceptance["winner_validation_human_scores_complete"])
        self.assertFalse(acceptance["next_multimodel_generation_complete"])
        self.assertFalse(acceptance["license_review_complete"])
        self.assertFalse(acceptance["production_promotion_allowed"])
        self.assertFalse(acceptance["production_registry_changed"])
        self.assertFalse(acceptance["voice_assignment_changed"])
        self.assertFalse(acceptance["live_project_audio_changed"])

    def test_durable_five_lane_runtime_is_pinned_and_evidence_is_text_private(self) -> None:
        runtime = self.five_lane["runtime"]
        self.assertEqual(
            runtime["source"]["actual_commit"],
            "13495845e3028f0bb6ca1462ad22aa0e76349e40",
        )
        self.assertEqual(
            runtime["model"]["revision"],
            "740dcaff396282ffb241903d150ac011cd4b1ede",
        )
        self.assertEqual(
            {item["revision"] for item in runtime["auxiliary"].values()},
            {
                "da985ba0987f70aaeb84a80f2851cfac8c697a7b",
                "265c6cef07625665d0c28d2faafb1415562379dc",
                "e4b6ede7ce16997aff4ae69fbca1f0175e2afede",
                "633ff708ed5b74903e86ff1298cf4a98e921c513",
            },
        )
        rendered = FIVE_LANE.read_text(encoding="utf-8")
        self.assertNotIn("There was something wrong with the room", rendered)
        self.assertNotIn("The smell rolled out of the box", rendered)
        self.assertNotIn("There was no goodbye", rendered)
        self.assertNotIn("Brilliant. Another flawless plan", rendered)

    def test_indextts2_finalist_review_remains_human_and_license_gated(self) -> None:
        review = self.index_finalist["review"]
        acceptance = self.index_finalist["acceptance"]
        self.assertTrue(review["unique_browser_storage_per_page"])
        self.assertTrue(review["tuning_variables_hidden_until_answer_key"])
        self.assertTrue(review["manual_blinded_review_required"])
        self.assertEqual(sum(page["sample_count"] for page in review["pages"].values()), 30)
        self.assertEqual(acceptance["sole_quality_finalist"], "indextts2")
        self.assertFalse(acceptance["human_finalist_expansion_scores_complete"])
        self.assertTrue(acceptance["license_review_required"])
        self.assertFalse(acceptance["production_promotion_allowed"])
        self.assertFalse(acceptance["production_registry_changed"])
        self.assertFalse(acceptance["voice_assignment_changed"])
        self.assertFalse(acceptance["live_project_audio_changed"])

    def test_documentation_names_the_final_non_production_disposition(self) -> None:
        source = DOC.read_text(encoding="utf-8")
        self.assertIn("actual `NARRATOR` supplied-recording clone", source)
        self.assertIn("32 randomized samples", source)
        self.assertIn("The finalist expansion generated 30 additional samples", source)
        self.assertIn("No candidate is production-supported", source)
        self.assertIn("Corrected identity and persistent-description rerun", source)
        self.assertIn("full user-authored narrator description was too long", source)
        self.assertIn("concise persistent description was stable", source)
        self.assertIn("CosyVoice is therefore rejected as a finalist", source)
        self.assertIn("IndexTTS2 — sole quality finalist", source)
        self.assertIn("Finalist human result", source)
        self.assertIn("Sad and afraid salvage", source)
        self.assertIn("Happy generalization", source)
        self.assertIn("Reviewed emotion-reference bank", source)
        self.assertIn("Narrator bank human result", source)
        self.assertIn("Durable five-lane capability expansion", source)
        self.assertIn("Five-lane human result", source)
        self.assertIn("22 styles", source)
        self.assertIn("110 samples", source)
        self.assertIn("all 110 samples", source)
        self.assertIn("same-voice generic Ryan lane did not behave as an upper bound", source)
        self.assertIn("Targeted reference and transfer salvage", source)
        self.assertIn("18 candidates", source)
        self.assertIn("15 samples", source)
        self.assertIn("12 pages and 34 samples total", source)
        self.assertIn("Benny", source)
        self.assertIn("Doctor", source)
        self.assertIn("aggregate throughput RTF 1.680", source)
        self.assertIn("MLX block parity", source)
        self.assertIn("unique browser-storage key", source)
        self.assertIn("It did not use:", source)
        self.assertIn("speech-to-speech voice conversion", source.lower())


if __name__ == "__main__":
    unittest.main()
