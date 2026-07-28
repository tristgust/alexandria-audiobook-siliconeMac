from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests import test_multimodel_round1_packaging as packaging_test


JsonValue = packaging_test.JsonValue
canonical_hash = packaging_test.canonical_hash
sha256_file = packaging_test.sha256_file


def index_hash(value: dict[str, JsonValue]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


ROOT = Path(__file__).resolve().parents[1]
PACKAGER = ROOT / "benchmarks" / "package_multimodel_round1_review.py"
VERIFIER = ROOT / "benchmarks" / "verify_multimodel_round1.py"
MODEL_DIR = "/Users/tristan/pinokio/cache/alexandria-evaluation/indextts2/huggingface/models--IndexTeam--IndexTTS-2/snapshots/740dcaff396282ffb241903d150ac011cd4b1ede"
AUX_ROOT = "/Users/tristan/pinokio/cache/alexandria-evaluation/indextts2/aux-flat"
INDEX_RUNTIME = {
    "device": "mps",
    "use_fp16": False,
    "mps_fast_math": True,
    "mps_prefer_metal": True,
    "num_beams": 1,
    "greedy_generation": True,
    "diffusion_steps": 8,
}
CHATTER_RUNTIME = {
    "device": "mps",
    "cpu_staged_checkpoint_load": True,
    "watermark_applied": False,
    "watermark_reason": "perth_backend_unavailable_on_macos",
    "temperature": 0.8,
    "repetition_penalty": 1.2,
    "min_p": 0.05,
    "top_p": 1.0,
}


class ModelSpecificReceiptContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.evidence = Path(self.temporary.name) / "evidence"
        fixture = packaging_test.MultimodelRound1PackagingTests(
            methodName="test_verifier_accepts_complete_package"
        )
        self.samples, _ = fixture.make_fixture(self.evidence)
        self.rewrite_as_model_specific_fixture()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_cli(self, script: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), "--evidence-root", str(self.evidence)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def rewrite_as_model_specific_fixture(self) -> None:
        internal_path = self.evidence / "round1_internal_manifest.json"
        internal = json.loads(internal_path.read_text())
        chatter, index = internal["sample_specs"]
        chatter["model_key"] = "chatterbox_multilingual_v3"
        chatter["model_label"] = "Chatterbox Multilingual V3"
        index["model_key"] = "indextts2"
        index["model_label"] = "IndexTTS2"
        internal["model_contract"]["models"] = [
            {"key": chatter["model_key"], "label": chatter["model_label"], "model_repo": "ResembleAI/chatterbox", "revision": "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18", "runtime": "official chatterbox-tts PyTorch MPS path with t3_model=v3"},
            {"key": index["model_key"], "label": index["model_label"], "model_repo": "IndexTeam/IndexTTS-2", "revision": "740dcaff396282ffb241903d150ac011cd4b1ede", "runtime": "pinned IndexTTS2 PyTorch MPS evaluation runtime"},
        ]
        for sample in (chatter, index):
            control = sample["control"]
            control["requested_instruction_sha256"] = hashlib.sha256(
                control["requested_instruction"].encode()
            ).hexdigest()
        chatter["control"].update(
            {
                "language_id": "en",
                "exaggeration": 0.1,
                "cfg_weight": 0.5,
                "semantic_instruction_directly_consumed": False,
            }
        )
        index["target_text"] = "They found it—the missing map was inside the frame."
        index["target_text_sha256"] = hashlib.sha256(
            index["target_text"].encode()
        ).hexdigest()
        index["control"]["target_text_sha256"] = index["target_text_sha256"]
        index["control"]["emo_alpha"] = 0.5
        reference = index["reference"]
        reference["acted_emotion_reference_file"] = reference["conditioning_file"]
        reference["acted_emotion_reference_sha256"] = reference["conditioning_sha256"]
        internal_path.write_text(json.dumps(internal), encoding="utf-8")
        self.samples = [chatter, index]
        self.write_chatterbox_receipt(chatter)
        self.write_index_receipt(index)

    def write_chatterbox_receipt(self, sample: dict[str, JsonValue]) -> None:
        fingerprint = canonical_hash(
            {
                "round_id": "alexandria_multimodel_expressive_clone_round1_v1",
                "sample_id": sample["sample_id"],
                "model_repo": "ResembleAI/chatterbox",
                "model_revision": "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
                "source_commit": "5de7a54aa4e5e2baadb0182dde554908b48b85c2",
                "t3_model": "v3",
                "identity_key": sample["identity_key"],
                "style": sample["style"],
                "target_text_sha256": sample["target_text_sha256"],
                "reference_audio_sha256": sample["reference"]["conditioning_sha256"],
                "control": sample["control"],
                "seed": sample["seed"],
                "runtime": CHATTER_RUNTIME,
            }
        )
        receipt = self.base_receipt(sample, fingerprint)
        receipt.update(
            {
                "model_label": sample["model_label"],
                "model_repo": "ResembleAI/chatterbox",
                "model_revision": "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
                "source_commit": "5de7a54aa4e5e2baadb0182dde554908b48b85c2",
                "t3_model": "v3",
                "reference_audio_sha256": sample["reference"]["conditioning_sha256"],
                "reference_text_sha256": sample["reference"]["conditioning_transcript_sha256"],
                "runtime_controls": {
                    **CHATTER_RUNTIME,
                    "language_id": "en",
                    "exaggeration": 0.1,
                    "cfg_weight": 0.5,
                    "semantic_instruction_directly_consumed": False,
                    "numeric_control_proxy": True,
                },
            }
        )
        self.write_receipt(sample, receipt)

    def write_index_receipt(self, sample: dict[str, JsonValue]) -> None:
        reference = sample["reference"]
        active_sample: dict[str, JsonValue] = {
            "sample_id": sample["sample_id"], "blind_id": sample["blind_id"],
            "model_key": "indextts2", "speaker": sample["identity_key"],
            "identity_key": sample["identity_key"], "identity_label": sample["identity_review_name"],
            "style": sample["style"], "group": sample["group"], "text": sample["target_text"],
            "reference_audio": str((self.evidence / "references" / reference["conditioning_file"]).resolve()),
            "reference_audio_sha256": reference["conditioning_sha256"],
            "emotion_audio_prompt": str((self.evidence / "references" / reference["acted_emotion_reference_file"]).resolve()),
            "emotion_audio_sha256": reference["acted_emotion_reference_sha256"],
            "emotion_strength": sample["control"]["emo_alpha"],
            "emotion_strength_origin": "round1_taxonomy_contract",
            "selection_kind": "style_matched_acted_reference",
            "source_selection_sample_id": f"ryan_acted:{sample['style']}",
            "source_instruction_sha256": sample["control"]["requested_instruction_sha256"],
            "source_seed": 6100, "seed": sample["seed"], "control": sample["control"],
            "output_file": str((self.evidence / sample["output_file"]).resolve()),
            "result_file": str((self.evidence / sample["result_file"]).resolve()),
            "generation": {"max_mel_tokens": 600},
        }
        manifest = {
            "schema_version": 1,
            "round_id": "alexandria_multimodel_expressive_clone_round1_v1",
            "runtime_profile": {"candidate": "IndexTTS2", **INDEX_RUNTIME, "persistent_worker_count": 2},
            "samples": [active_sample],
        }
        manifest_path = self.evidence / "indextts2_round1_manifest_baseline.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        config = {
            "round_id": manifest["round_id"], "model_dir": MODEL_DIR,
            "aux_root": AUX_ROOT, "device": "mps", "diffusion_steps": 8, "greedy": True,
        }
        fingerprint = index_hash(
            {"sample": active_sample, "runtime": config, "manifest_sha256": sha256_file(manifest_path)}
        )
        receipt = self.base_receipt(sample, fingerprint)
        receipt.pop("control")
        receipt.update(
            {
                "runtime_controls": INDEX_RUNTIME,
                "source_instruction_sha256": sample["control"]["requested_instruction_sha256"],
                "reference_audio_sha256": sample["reference"]["conditioning_sha256"],
                "emotion_audio_sha256": sample["reference"]["acted_emotion_reference_sha256"],
                "emotion_strength": sample["control"]["emo_alpha"],
            }
        )
        self.write_receipt(sample, receipt)

    def base_receipt(
        self, sample: dict[str, JsonValue], fingerprint: str
    ) -> dict[str, JsonValue]:
        output = self.evidence / sample["output_file"]
        return {
            "round_id": "alexandria_multimodel_expressive_clone_round1_v1",
            "sample_id": sample["sample_id"], "blind_id": sample["blind_id"],
            "sample_fingerprint": fingerprint, "model_key": sample["model_key"],
            "target_text_sha256": sample["target_text_sha256"], "control": sample["control"],
            "output_file": str(output.resolve()), "audio_sha256": sha256_file(output),
            "group": sample["group"], "identity_key": sample["identity_key"],
            "style": sample["style"], "seed": sample["seed"],
        }

    def write_receipt(
        self, sample: dict[str, JsonValue], receipt: dict[str, JsonValue]
    ) -> None:
        (self.evidence / sample["result_file"]).write_text(json.dumps(receipt))

    def test_packager_accepts_exact_model_specific_active_contracts(self) -> None:
        # Given exact active Index and Chatterbox receipt fingerprints, when packaged.
        completed = self.run_cli(PACKAGER)

        # Then both model-specific contracts are accepted without a waiver.
        self.assertEqual(completed.returncode, 0, completed.stderr)
        counts = json.loads(completed.stdout)["generated_counts_by_model"]
        self.assertEqual(counts, {"chatterbox_multilingual_v3": 1, "indextts2": 1})

    def test_packager_rejects_tampered_index_runtime_controls(self) -> None:
        # Given a hash-valid Index receipt whose runtime profile was changed.
        result = self.evidence / self.samples[1]["result_file"]
        receipt = json.loads(result.read_text())
        receipt["runtime_controls"]["device"] = "cpu"
        result.write_text(json.dumps(receipt))

        # When packaged, then the strict receipt contract rejects the control drift.
        completed = self.run_cli(PACKAGER)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("receipt_runtime_controls", completed.stderr + completed.stdout)

    def test_packager_rejects_tampered_chatter_source_commit(self) -> None:
        result = self.evidence / self.samples[0]["result_file"]
        receipt = json.loads(result.read_text())
        receipt["source_commit"] = "0" * 40
        result.write_text(json.dumps(receipt))

        completed = self.run_cli(PACKAGER)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("receipt_source_commit", completed.stderr + completed.stdout)

    def test_verifier_accepts_exact_model_specific_active_contracts(self) -> None:
        # Given a package built from exact contracts, when fully verified, then it is clean.
        packaged = self.run_cli(PACKAGER)
        self.assertEqual(packaged.returncode, 0, packaged.stderr)
        completed = self.run_cli(VERIFIER)
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)


if __name__ == "__main__":
    unittest.main()
