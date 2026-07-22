from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
DOCS = ROOT / "docs"
PHASE22 = (
    ROOT
    / "benchmarks"
    / "results"
    / "20260717T014952Z_phase22_apple_silicon.json"
)
LORA_RESULT = (
    ROOT
    / "benchmarks"
    / "results"
    / "20260719T213000Z_mps_lora_merged_mlx.json"
)

REQUIRED_DOCS = {
    "APPLE_SILICON.md",
    "NATIVE_OLLAMA.md",
    "ACCENT_PIPELINE.md",
    "FIDELITY_AUDIT.md",
    "BENCHMARKING.md",
    "RESUMABLE_GENERATION.md",
    "GENERATION_METADATA.md",
    "CHARACTER_ROSTER.md",
    "PERSONA_AND_VISUAL_REFS.md",
    "VOICE_TYPES.md",
    "DATASET_BUILDER.md",
    "VOICE_TRAINING.md",
    "INSTRUCTION_DATASET.md",
    "INSTRUCTION_PROPAGATION.md",
    "LORA_APPLE_SILICON.md",
    "SPEAKER_MANAGEMENT.md",
    "MAINTENANCE.md",
    "HELP_CENTER.md",
    "MIGRATION.md",
    "UPDATING_FORK.md",
    "INTERFACE_DESIGN.md",
    "INTERFACE_ACCEPTANCE.md",
    "BOUNDARY13_ACCEPTANCE.md",
}


class DocumentationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.readme = README.read_text(encoding="utf-8")
        self.phase22 = json.loads(PHASE22.read_text(encoding="utf-8"))
        self.lora_result = json.loads(LORA_RESULT.read_text(encoding="utf-8"))

    def test_required_phase24_documents_exist_and_are_nontrivial(self) -> None:
        actual = {path.name for path in DOCS.glob("*.md")}
        self.assertTrue(REQUIRED_DOCS.issubset(actual))
        for name in REQUIRED_DOCS:
            text = (DOCS / name).read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# "), name)
            self.assertGreater(len(text), 900, name)

    def test_readme_states_measured_apple_silicon_capability(self) -> None:
        self.assertIn("MLX-Audio", self.readme)
        self.assertIn(
            "LoRA inside shared MLX runtime | **Unsupported**; fail closed",
            self.readme,
        )
        self.assertIn(
            "Isolated MPS LoRA training | Experimental; technically validated",
            self.readme,
        )
        self.assertIn(
            "Merged 8-bit MLX LoRA inference | Experimental; faster than real time",
            self.readme,
        )
        self.assertIn(
            "LoRA production assignment | Blocked pending quality review",
            self.readme,
        )
        self.assertNotIn("CPU only; MPS not supported", self.readme)
        self.assertNotIn("LoRA Voice Training", self.readme)
        self.assertIn("requirements-apple-silicon.txt", self.readme)

    def test_lora_document_matches_phase22_outcome(self) -> None:
        text = (DOCS / "LORA_APPLE_SILICON.md").read_text(encoding="utf-8")
        self.assertEqual(self.phase22["stable_lora_outcome"], "unsupported")
        self.assertFalse(self.lora_result["shared_runtime_lora_supported"])
        self.assertTrue(self.lora_result["experimental_sidecar_training_supported"])
        self.assertTrue(self.lora_result["merged_mlx_inference_technically_validated"])
        self.assertFalse(self.lora_result["production_assignment_supported"])
        self.assertIn("technically viable Apple Silicon LoRA architecture", text)
        self.assertIn("The old shared-runtime LoRA path remains unsupported", text)
        self.assertIn("Production assignment remains blocked", text)
        self.assertIn("409 lora_sidecar_unavailable", text)
        self.assertIn("Train, validate, and install", text)
        self.assertIn("held-out validation fraction", text)
        self.assertIn("qwen-tts==0.1.1", text)
        self.assertIn("mlx-audio==0.4.5", text)
        self.assertIn("20260719T213000Z_mps_lora_merged_mlx.json", text)
        self.assertEqual(self.lora_result["training"]["epochs_completed"], 3)
        self.assertTrue(self.lora_result["training"]["resumed"])
        self.assertEqual(self.lora_result["training"]["held_out_validation_sample_count"], 6)
        self.assertTrue(self.lora_result["quality_review"]["technical_multi_sample_multi_epoch_completed"])
        self.assertFalse(self.lora_result["quality_review"]["dataset_reviewed"])
        self.assertIn("narrator_attention_r8_pilot", text)
        self.assertIn("Held-out loss improved each epoch", text)
        self.assertIn("warm RTF 0.354", text)

    def test_benchmark_document_uses_recorded_numbers(self) -> None:
        text = (DOCS / "BENCHMARKING.md").read_text(encoding="utf-8")
        llm = self.phase22["llm_measurement"]
        self.assertIsNotNone(llm)
        self.assertIn("67.27", text)
        self.assertIn("2.15", text)
        self.assertIn("20260717T014952Z_phase22_apple_silicon.json", text)
        self.assertIn("sequential loop", text)

    def test_readme_contains_curl_python_and_javascript_api_examples(self) -> None:
        self.assertIn("### cURL", self.readme)
        self.assertIn("### Python", self.readme)
        self.assertIn("### JavaScript", self.readme)
        self.assertIn("/api/voice_backend/capabilities", self.readme)
        self.assertIn("/api/migration/status", self.readme)
        self.assertIn("/openapi.json", self.readme)

    def test_readme_document_links_resolve(self) -> None:
        links = re.findall(r"\[[^\]]+\]\((docs/[^)#]+\.md)\)", self.readme)
        self.assertGreaterEqual(len(links), len(REQUIRED_DOCS) - 1)
        missing = [link for link in links if not (ROOT / link).exists()]
        self.assertEqual(missing, [])

    def test_apple_install_document_matches_launcher_contract(self) -> None:
        text = (DOCS / "APPLE_SILICON.md").read_text(encoding="utf-8")
        install = (ROOT / "install.js").read_text(encoding="utf-8")
        requirements = (ROOT / "app" / "requirements-apple-silicon.txt").read_text(encoding="utf-8")
        self.assertIn("requirements-apple-silicon.txt", install)
        self.assertIn("mlx-audio==0.4.5", requirements)
        self.assertIn("transformers==5.12.1", requirements)
        self.assertIn("intentionally omit `qwen-tts`", text)

    def test_settings_document_separates_preferences_from_maintenance(self) -> None:
        text = (DOCS / "SETTINGS.md").read_text(encoding="utf-8")
        self.assertGreater(len(text), 5000)
        for phrase in (
            "does not create a second settings store", "Structured output is required",
            "cleanup mode is `manual_only`", "GET /api/settings", "PUT /api/settings",
            "never returns an API-key value", "settings_config_conflict",
            "leaves `config.json` unchanged", "retains the user’s invalid edits",
            "prompt configuration", "Command-S", "ALEXANDRIA_CONFIG_PATH",
        ):
            self.assertIn(phrase, text)

    def test_maintenance_document_preserves_read_only_first_and_guarded_actions(self) -> None:
        text = (DOCS / "MAINTENANCE.md").read_text(encoding="utf-8")
        self.assertGreater(len(text), 5000)
        for phrase in (
            "read-only-first", "Promise.allSettled", "GET /api/migration/history",
            "never returns saved file snapshots", "exact artifact name",
            "recoverable Alexandria Trash", "APPLY MIGRATION", "ROLL BACK",
            "does not load a model", "restores focus", "zero console, network, or runtime errors",
        ):
            self.assertIn(phrase, text)

    def test_boundary13_acceptance_document_covers_accessibility_redirects_and_purity(self) -> None:
        text = (DOCS / "BOUNDARY13_ACCEPTANCE.md").read_text(encoding="utf-8")
        self.assertGreater(len(text), 6000)
        for phrase in (
            "ten surfaces", "Accessibility.getFullAXTree", "roving `tabindex=\"0\"`",
            "Swedish", "#project-recovery", "#/more?tool=maintenance",
            "filesystem snapshots", "api_unchanged", "raw 64-character fingerprints",
            "boundary13-final", "zero console errors",
        ):
            self.assertIn(phrase, text)

    def test_help_center_document_defines_manifest_context_sanitization_and_keyboard_contracts(self) -> None:
        text = (DOCS / "HELP_CENTER.md").read_text(encoding="utf-8")
        self.assertGreater(len(text), 6000)
        for phrase in (
            "manifest.json", "content_sha256", "globally unique stable `context_ids`",
            "Raw HTML is rejected", "textContent", "GET /api/help?search=...",
            "`help` — stable contextual entry ID", "does not overwrite the original `source`",
            "Arrow Up and Arrow Down", "Cast-only production Voice assignment",
            "zero executable topic elements",
        ):
            self.assertIn(phrase, text)

    def test_templates_document_preserves_user_intent_and_guarded_mutation(self) -> None:
        text = (DOCS / "TEMPLATES.md").read_text(encoding="utf-8")
        self.assertGreater(len(text), 3000)
        for phrase in (
            "Reading built-in templates is file-pure", "do not expose or store", "model names",
            "Historical usage is nonblocking", "never rewrites or deletes an existing project",
            "template_application_mismatch", "clears template provenance", "creation.template_id",
            "browser Back restoring the same result set",
        ):
            self.assertIn(phrase, text)

    def test_instruction_propagation_document_separates_mechanics_from_quality(self) -> None:
        text = (DOCS / "INSTRUCTION_PROPAGATION.md").read_text(encoding="utf-8")
        self.assertGreater(len(text), 7000)
        for phrase in (
            "identity_only", "per_record", "instruction_embedding_then_original_icl_prefill",
            "original ICL sequence remains byte-equivalent", "native `instruct_ids`",
            "propagation_fingerprint", "training/export mismatch", "registry/export mismatch",
            "does not prove that a trained model follows the instruction acoustically",
            "production_assignment_supported: false", "No training run",
        ):
            self.assertIn(phrase, text)

    def test_instruction_dataset_document_preserves_review_and_assignment_gates(self) -> None:
        text = (DOCS / "INSTRUCTION_DATASET.md").read_text(encoding="utf-8")
        training = (DOCS / "VOICE_TRAINING.md").read_text(encoding="utf-8")
        for phrase in (
            "exact reviewed delivery direction", "group by audio SHA-256",
            "cross-split audio leakage", '"manual_audio_review_status": "pending"',
            '"production_assignment_supported": false',
            "tampered manifest, checkpoint, or receipt fingerprints",
        ):
            self.assertIn(phrase, text)
        self.assertIn("Instruction-Aware Voice Dataset Contract", training)
        self.assertIn("exact delivery instruction", training)

    def test_migration_document_forbids_text_rewrite_and_deletion(self) -> None:
        text = (DOCS / "MIGRATION.md").read_text(encoding="utf-8")
        self.assertIn("file-pure", text)
        self.assertIn("profiles", text)
        self.assertIn("text rewriting", text)
        self.assertIn("automatic artifact deletion", text)
        self.assertIn("project-relative file paths", text)

    def test_interface_design_lists_current_tools(self) -> None:
        text = (DOCS / "INTERFACE_DESIGN.md").read_text(encoding="utf-8")
        for label in (
            "Character roster", "Speaker management", "Voice profiles & preparation",
            "Voice designer", "Audio preparer", "Dataset builder", "Voice training",
        ):
            self.assertIn(label, text)


if __name__ == "__main__":
    unittest.main()
