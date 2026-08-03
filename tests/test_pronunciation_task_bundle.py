from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from backend_render_plan import chunks_fingerprint
from external_stage_transfers import (
    ExternalStageTransferConflictError,
    ExternalStageTransferValidationError,
    transfer_structured_result_candidate,
)
from external_workflows import (
    get_structured_result_candidate,
    store_structured_result_candidate,
)
from generation_state import fingerprint_value
from llm_schemas import ContractValidationError, validate_contract
from pronunciation_registry import (
    empty_pronunciation_registry,
    load_pronunciation_registry,
)
from task_bundles import TASK_REGISTRY, task_definition_contract


class PronunciationTaskBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.chunks = [
            {
                "id": 0,
                "speaker": "NARRATOR",
                "text": "Skaro was silent.",
                "instruct": "Quiet narration.",
                "status": "done",
                "audio_state": "current",
                "audio_path": "voicelines/chunk-0.mp3",
            },
            {
                "id": 1,
                "speaker": "THE DOCTOR",
                "text": "We return to Skaro.",
                "instruct": "Grave recognition.",
                "status": "pending",
                "audio_state": "pending",
                "audio_path": None,
            },
        ]
        self.script = [
            {
                "speaker": item["speaker"],
                "text": item["text"],
                "instruct": item["instruct"],
            }
            for item in self.chunks
        ]
        self.write_json("annotated_script.json", self.script)
        self.write_json("chunks.json", self.chunks)
        self.state_path = self.root / "character_roster_state.json"
        self.draft_path = self.root / "character_roster.draft.json"
        self.approved_path = self.root / "character_roster.json"
        self.visual_state_path = self.root / "persona_visual_state.json"
        self.projects_root = self.root / "voice_training_projects"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_json(self, name: str, value: object) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def result(self, **overrides: object) -> dict:
        entry = {
            "chunk_index": 0,
            "start_char": 0,
            "end_char": 5,
            "original": "Skaro",
            "chunk_text_sha256": hashlib.sha256(
                self.chunks[0]["text"].encode("utf-8")
            ).hexdigest(),
            "spoken_form": "SKA-roh",
            "phonetic_hint": None,
            "languages": ["en"],
            "character_labels": ["NARRATOR"],
            "voice_ids": [],
            "engine_ids": [],
            "engine_source": {
                "kind": "task_bundle",
                "engine": None,
                "revision": None,
                "phoneme_alphabet": None,
            },
            "fallback": {
                "strategy": "bypass",
                "spoken_form": None,
                "reason": "Use only the reviewed spoken form.",
            },
            "rationale": "The proper name is not pronounced as ordinary English spelling.",
        }
        entry.update(overrides)
        return {
            "schema_version": 1,
            "entries": [entry],
            "warnings": [],
        }

    def store_candidate(
        self,
        result: dict,
        *,
        registry: dict | None = None,
    ) -> dict:
        registry = registry or empty_pronunciation_registry()
        return store_structured_result_candidate(
            root_dir=self.root,
            validated={
                "handoff_id": "handoff_pronunciation0000000000",
                "task_type": "pronunciation_guidance",
                "result_fingerprint": fingerprint_value(result),
                "review": {
                    "root_type": "object",
                    "item_count": len(result["entries"]),
                    "source_fingerprint_verified": True,
                    "artifact_fingerprints_verified": [
                        "annotated_script",
                        "chunks",
                        "pronunciation_registry",
                    ],
                },
                "result": result,
            },
            handoff={
                "manifest": {
                    "source_fingerprint": None,
                    "artifact_fingerprints": {
                        "annotated_script": fingerprint_value(self.script),
                        "chunks": chunks_fingerprint(self.chunks),
                        "pronunciation_registry": fingerprint_value(registry),
                    },
                },
                "input": {},
            },
            created_at_utc="2026-08-02T22:30:00Z",
        )

    def transfer(self, candidate: dict) -> dict:
        return transfer_structured_result_candidate(
            root_dir=self.root,
            candidate_id=candidate["candidate_id"],
            expected_result_fingerprint=candidate["result_fingerprint"],
            source_snapshot=None,
            source_text=None,
            roster_state_path=self.state_path,
            roster_draft_path=self.draft_path,
            approved_roster_path=self.approved_path,
            voice_training_projects_root=self.projects_root,
            visual_state_path=self.visual_state_path,
            at_utc="2026-08-02T22:31:00Z",
        )

    def test_registry_contract_is_candidate_only_and_native(self) -> None:
        definition = TASK_REGISTRY["pronunciation_guidance"]
        contract = task_definition_contract(definition)
        self.assertEqual(contract["schema"]["contract"], "pronunciation_guidance")
        self.assertEqual(contract["native_destination"], "pronunciation_registry")
        self.assertEqual(contract["transfer_handler"], "pronunciation_guidance")
        self.assertTrue(contract["native_transfer"]["supported"])
        self.assertFalse(contract["text_mutation_permitted"])
        self.assertEqual(
            set(contract["dependencies"]["artifacts"]),
            {"annotated_script", "chunks", "pronunciation_registry"},
        )

    def test_contract_normalizes_exact_candidate_and_rejects_approval_fields(self) -> None:
        normalized = validate_contract("pronunciation_guidance", self.result())
        self.assertEqual(normalized["entries"][0]["original"], "Skaro")
        self.assertEqual(normalized["entries"][0]["spoken_form"], "SKA-roh")
        self.assertNotIn("review", normalized["entries"][0])
        self.assertNotIn("pronunciation_id", normalized["entries"][0])

        invalid = self.result()
        invalid["entries"][0]["review"] = {"state": "approved"}
        with self.assertRaises(ContractValidationError):
            validate_contract("pronunciation_guidance", invalid)

    def test_transfer_creates_review_ready_drafts_without_registry_or_audio_mutation(self) -> None:
        before_chunks = (self.root / "chunks.json").read_bytes()
        before_script = (self.root / "annotated_script.json").read_bytes()
        registry_path = self.root / "pronunciation_registry.json"
        candidate = self.store_candidate(self.result())

        transferred = self.transfer(candidate)

        self.assertEqual(transferred["status"], "transferred")
        application = transferred["application"]
        self.assertEqual(application["status"], "review_ready")
        self.assertEqual(application["destination"], "pronunciation_registry")
        self.assertTrue(application["explicit_acceptance_required"])
        self.assertFalse(application["production_state_changed"])
        self.assertEqual(application["candidate_count"], 1)
        entry = application["entries"][0]
        self.assertEqual(entry["review"]["state"], "draft")
        self.assertEqual(entry["source"]["kind"], "accepted_script_chunk")
        self.assertEqual(entry["provenance"]["source"], "task_bundle")
        self.assertFalse(registry_path.exists())
        self.assertEqual((self.root / "chunks.json").read_bytes(), before_chunks)
        self.assertEqual((self.root / "annotated_script.json").read_bytes(), before_script)
        self.assertEqual(load_pronunciation_registry(self.root)["entries"], [])

    def test_stale_registry_blocks_transfer_before_candidate_application(self) -> None:
        candidate = self.store_candidate(self.result())
        changed_registry = {
            "schema_version": 1,
            "entries": [
                self._approved_entry(
                    pronunciation_id="later-skaro",
                    chunk_index=1,
                    start_char=13,
                    end_char=18,
                    original="Skaro",
                )
            ],
        }
        from pronunciation_registry import normalize_pronunciation_registry

        changed_registry = normalize_pronunciation_registry(
            changed_registry,
            chunks=self.chunks,
            require_current_anchors=True,
        )
        self.write_json("pronunciation_registry.json", changed_registry)

        with self.assertRaises(ExternalStageTransferConflictError) as error:
            self.transfer(candidate)
        self.assertEqual(error.exception.code, "stale_artifact")
        stored = get_structured_result_candidate(
            root_dir=self.root,
            candidate_id=candidate["candidate_id"],
        )
        self.assertEqual(stored["status"], "inspected")

    def test_changed_anchor_and_overlapping_approved_entry_fail_closed(self) -> None:
        changed = self.result(original="Wrong")
        candidate = self.store_candidate(changed)
        with self.assertRaises(ExternalStageTransferConflictError) as anchor_error:
            self.transfer(candidate)
        self.assertEqual(anchor_error.exception.code, "pronunciation_source_span_mismatch")

        approved = {
            "schema_version": 1,
            "entries": [
                self._approved_entry()
            ],
        }
        from pronunciation_registry import normalize_pronunciation_registry

        approved = normalize_pronunciation_registry(
            approved,
            chunks=self.chunks,
            require_current_anchors=True,
        )
        self.write_json("pronunciation_registry.json", approved)
        overlapping = self.store_candidate(self.result(), registry=approved)
        before_registry = (self.root / "pronunciation_registry.json").read_bytes()
        with self.assertRaises(ExternalStageTransferConflictError) as overlap_error:
            self.transfer(overlapping)
        self.assertEqual(overlap_error.exception.code, "pronunciation_candidate_conflict")
        self.assertEqual(
            (self.root / "pronunciation_registry.json").read_bytes(),
            before_registry,
        )

    def _approved_entry(
        self,
        *,
        pronunciation_id: str = "existing-skaro",
        chunk_index: int = 0,
        start_char: int = 0,
        end_char: int = 5,
        original: str = "Skaro",
    ) -> dict:
        chunk_text = self.chunks[chunk_index]["text"]
        chunk_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
        return {
            "pronunciation_id": pronunciation_id,
            "scope": "exact_occurrence",
            "chunk_index": chunk_index,
            "start_char": start_char,
            "end_char": end_char,
            "original": original,
            "chunk_text_sha256": chunk_hash,
            "source": {
                "kind": "accepted_script_chunk",
                "chunk_index": chunk_index,
                "start_char": start_char,
                "end_char": end_char,
                "quote": original,
                "chunk_text_sha256": chunk_hash,
            },
            "spoken_form": "SKA-roh",
            "phonetic_hint": None,
            "languages": [],
            "character_labels": [],
            "voice_ids": [],
            "engine_ids": [],
            "engine_source": {
                "kind": "manual",
                "engine": None,
                "revision": None,
                "phoneme_alphabet": None,
            },
            "fallback": {
                "strategy": "bypass",
                "spoken_form": None,
                "reason": None,
            },
            "review": {
                "state": "approved",
                "reviewer": "Tristan",
                "reviewed_at_utc": "2026-08-02T22:00:00Z",
                "notes": None,
            },
            "provenance": {
                "source": "manual",
                "created_at_utc": "2026-08-02T22:00:00Z",
                "evidence": None,
            },
        }


if __name__ == "__main__":
    unittest.main()
