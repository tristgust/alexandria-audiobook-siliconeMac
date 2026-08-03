from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend_render_plan import inspect_backend_render_plan
from generate_backend_render_plan import generate_local_backend_render_plan


class FakeRuntime:
    backend = "ollama-native"
    model_name = "fixture-qwen"
    thinking = False
    structured_output = True
    corrective_retry = True
    context_length = 40960

    def __init__(self, *, fail_on_call: int | None = None):
        self.calls = []
        self.fail_on_call = fail_on_call

    def preload(self):
        return True, "Fixture runtime ready."

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise RuntimeError("synthetic planner interruption")
        payload = json.loads(kwargs["messages"][1]["content"])
        entries = []
        for chunk in payload["chunks"]:
            cues = []
            if "wasn't" in chunk["text"]:
                cues = [
                    {
                        "anchor": "before_phrase",
                        "phrase": "I wasn't",
                        "occurrence": 1,
                        "tag": "voice breaking",
                        "kind": "delivery",
                    }
                ]
            entries.append(
                {
                    "index": chunk["index"],
                    "chunk_id": chunk["chunk_id"],
                    "speaker": chunk["speaker"],
                    "text_sha256": chunk["text_sha256"],
                    "qwen_instruction": "Direct, concise Qwen performance direction.",
                    "fish_direction": "short concrete Fish direction",
                    "fish_cues": cues,
                    "warnings": [],
                }
            )
        return SimpleNamespace(
            data={
                "schema_version": 1,
                "script_fingerprint": payload["script_fingerprint"],
                "chunks_fingerprint": payload["chunks_fingerprint"],
                "entries": entries,
                "warnings": [],
            },
            metrics={"fixture": True},
        )


class LocalBackendRenderPlanGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_path = self.root / "config.json"
        self.config_path.write_text(
            json.dumps({"llm": {"model_name": "fixture-qwen"}}),
            encoding="utf-8",
        )
        self.script = [
            {
                "speaker": "NARRATOR",
                "text": "I thought I was ready. I wasn't.",
                "instruct": "Controlled, then shaken.",
            },
            {
                "speaker": "THE DOCTOR",
                "text": "Hanky panky!",
                "instruct": "A delighted shout.",
            },
            {
                "speaker": "NARRATOR",
                "text": "shouted the Doctor.",
                "instruct": "Attached attribution.",
            },
            {
                "speaker": "JOAN REDFERN",
                "text": "Well, Mr Shuttleworth,",
                "instruct": "Polite correction.",
            },
            {
                "speaker": "NARRATOR",
                "text": "Joan began,",
                "instruct": "Attached attribution.",
            },
        ]
        self.chunks = [
            {
                "id": index,
                **entry,
                "status": "done",
                "audio_state": "current",
                "audio_path": f"voicelines/{index}.mp3",
            }
            for index, entry in enumerate(self.script)
        ]
        (self.root / "annotated_script.json").write_text(
            json.dumps(self.script),
            encoding="utf-8",
        )
        (self.root / "chunks.json").write_text(
            json.dumps(self.chunks),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_generates_same_contract_in_resumable_batches(self):
        runtime = FakeRuntime()
        with patch(
            "generate_backend_render_plan.build_runtime_client",
            return_value=runtime,
        ):
            result = generate_local_backend_render_plan(
                root_dir=self.root,
                config_path=self.config_path,
                batch_size=2,
                max_batch_chars=100000,
                max_tokens=4096,
            )
        self.assertEqual(len(runtime.calls), 3)
        self.assertEqual(result["chunk_count"], 5)
        self.assertEqual(result["fish_inline_chunk_count"], 1)
        status = inspect_backend_render_plan(self.root)
        self.assertTrue(status["current"])
        self.assertEqual(status["origin"]["type"], "local_llm")
        chunks = json.loads((self.root / "chunks.json").read_text(encoding="utf-8"))
        self.assertEqual(
            chunks[0]["qwen_render_instruction"],
            "Direct, concise Qwen performance direction.",
        )
        self.assertEqual(
            chunks[0]["fish_render_plan"]["cues"][0]["tag"],
            "voice breaking",
        )
        self.assertEqual(chunks[0]["audio_state"], "current")
        self.assertFalse((self.root / "backend_render_plan_state.json").exists())

    def test_stages_candidate_without_mutating_production_files(self):
        runtime = FakeRuntime()
        candidate_path = self.root / "staging" / "candidate.json"
        before_chunks = (self.root / "chunks.json").read_bytes()
        with patch(
            "generate_backend_render_plan.build_runtime_client",
            return_value=runtime,
        ):
            result = generate_local_backend_render_plan(
                root_dir=self.root,
                config_path=self.config_path,
                batch_size=2,
                max_batch_chars=100000,
                max_tokens=4096,
                candidate_path=candidate_path,
            )
        self.assertEqual(result["status"], "staged")
        self.assertTrue(candidate_path.is_file())
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        self.assertEqual(candidate["plan"]["script_fingerprint"], result["script_fingerprint"])
        self.assertEqual((self.root / "chunks.json").read_bytes(), before_chunks)
        self.assertFalse((self.root / "backend_render_plan.json").exists())
        self.assertFalse((self.root / "backend_render_plan_state.json").exists())

    def test_interrupted_run_resumes_without_repeating_completed_batch(self):
        first = FakeRuntime(fail_on_call=2)
        with patch(
            "generate_backend_render_plan.build_runtime_client",
            return_value=first,
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic planner interruption"):
                generate_local_backend_render_plan(
                    root_dir=self.root,
                    config_path=self.config_path,
                    batch_size=2,
                    max_batch_chars=100000,
                    max_tokens=4096,
                )
        state_path = self.root / "backend_render_plan_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIn("0", state["completed_batches"])
        self.assertNotIn("1", state["completed_batches"])

        second = FakeRuntime()
        with patch(
            "generate_backend_render_plan.build_runtime_client",
            return_value=second,
        ):
            result = generate_local_backend_render_plan(
                root_dir=self.root,
                config_path=self.config_path,
                batch_size=2,
                max_batch_chars=100000,
                max_tokens=4096,
            )
        self.assertEqual(len(second.calls), 2)
        self.assertEqual(result["chunk_count"], 5)
        self.assertFalse(state_path.exists())


if __name__ == "__main__":
    unittest.main()
