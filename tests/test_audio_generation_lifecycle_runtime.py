from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from audio_generation_lifecycle import (
    claim_request,
    finalize_request,
    load_request,
    normalize_request_manifest,
    prepare_request,
    reconcile_interrupted_requests,
    request_cancel,
    request_context,
)
from project import ProjectManager
from tts import TTSEngine


def write_speech(path: Path, text: str, *, sample_rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.8, len(text) * 0.05)
    count = max(1, round(duration * sample_rate))
    timeline = np.arange(count, dtype=np.float32) / sample_rate
    audio = 0.1 * np.sin(2.0 * np.pi * 7.0 * timeline)
    sf.write(path, audio, sample_rate, subtype="FLOAT")


class AudioGenerationLifecycleRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        (self.root / "app" / "config.json").write_text(
            json.dumps({"tts": {"mode": "local", "language": "English"}}),
            encoding="utf-8",
        )
        (self.root / "voice_config.json").write_text(
            json.dumps({"NARRATOR": {"type": "custom", "voice": "Ryan"}}),
            encoding="utf-8",
        )
        self.text = (
            "The first sentence is long enough to become one internal request. "
            "The second sentence also requires its own bounded synthesis window."
        )
        (self.root / "chunks.json").write_text(
            json.dumps(
                [
                    {
                        "id": 0,
                        "speaker": "NARRATOR",
                        "text": self.text,
                        "instruct": "Calm.",
                        "status": "pending",
                        "audio_path": None,
                    }
                ]
            ),
            encoding="utf-8",
        )
        self.manager = ProjectManager(str(self.root))
        self.engine = TTSEngine({"tts": {"mode": "local"}})
        self.manager.engine = self.engine

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def prepare(self):
        request_args = {
            "indices": [0],
            "mode": "parallel",
            "operation_mode": "missing_stale",
            "generation_seed": None,
            "plan_fingerprint": "plan-fixture",
            "chunks_fingerprint": "chunks-fixture",
        }
        manifest = self.manager.build_audio_generation_manifest(**request_args)
        prepared = prepare_request(
            self.root,
            manifest,
            operation_id="operation-fixture",
        )
        claimed = claim_request(
            self.root,
            prepared["record"]["request_id"],
            expected_request_fingerprint=prepared["record"]["request_fingerprint"],
        )
        normalized = normalize_request_manifest(manifest)
        chunk = normalized["chunks"][0]
        context = {
            **request_context(
                self.root,
                claimed["request_id"],
                claimed["owner_token"],
                chunk["chunk_key"],
            ),
            "manifest_request": request_args,
        }
        return prepared, claimed, context

    def test_interrupted_request_reuses_completed_segment_and_publishes_once(self) -> None:
        _prepared, running, context = self.prepare()
        calls = []

        def first_attempt(segment_text, _instruct, _speaker, _config, output_path, **_kwargs):
            calls.append(("first", segment_text))
            if len(calls) == 2:
                return False
            write_speech(Path(output_path), segment_text)
            return True

        with patch.object(
            self.engine,
            "_generate_voice_unsegmented",
            side_effect=first_attempt,
        ):
            success, _message = self.manager.generate_chunk_audio(
                0,
                generation_seed=None,
                generation_context=context,
            )
        self.assertFalse(success)
        interrupted = load_request(self.root, running["request_id"])
        segments = interrupted["progress"]["chunk:0"]["segments"]
        self.assertEqual(segments["segment_0000"]["state"], "completed")
        self.assertEqual(segments["segment_0001"]["state"], "failed")
        self.assertFalse(any((self.root / "voicelines").glob("voiceline_*")))

        reconcile_interrupted_requests(self.root)
        resumed = claim_request(
            self.root,
            running["request_id"],
            expected_request_fingerprint=running["request_fingerprint"],
        )
        request_args = context["manifest_request"]
        resumed_context = {
            **request_context(
                self.root,
                resumed["request_id"],
                resumed["owner_token"],
                "chunk:0",
            ),
            "manifest_request": request_args,
        }

        def second_attempt(segment_text, _instruct, _speaker, _config, output_path, **_kwargs):
            calls.append(("second", segment_text))
            write_speech(Path(output_path), segment_text)
            return True

        with patch.object(
            self.engine,
            "_generate_voice_unsegmented",
            side_effect=second_attempt,
        ):
            success, path = self.manager.generate_chunk_audio(
                0,
                generation_seed=None,
                generation_context=resumed_context,
            )
        self.assertTrue(success)
        self.assertTrue((self.root / path).is_file())
        self.assertEqual(
            [label for label, _text in calls],
            ["first", "first", "second"],
        )
        completed = load_request(self.root, resumed["request_id"])
        self.assertEqual(completed["progress"]["chunk:0"]["state"], "completed")
        terminal = finalize_request(
            self.root,
            resumed["request_id"],
            resumed["owner_token"],
        )
        self.assertEqual(terminal["state"], "succeeded")
        self.assertEqual(terminal["terminal_summary"]["completed"], 1)
        installed = list((self.root / "voicelines" / "takes").rglob("*.*"))
        self.assertEqual(len([path for path in installed if path.is_file()]), 1)
        self.assertTrue((self.root / path).is_file())

    def test_cancel_during_provider_call_prevents_segment_and_canonical_publication(self) -> None:
        _prepared, running, context = self.prepare()

        def cancel_before_return(segment_text, _instruct, _speaker, _config, output_path, **_kwargs):
            write_speech(Path(output_path), segment_text)
            request_cancel(self.root, running["request_id"])
            return True

        with patch.object(
            self.engine,
            "_generate_voice_unsegmented",
            side_effect=cancel_before_return,
        ):
            success, message = self.manager.generate_chunk_audio(
                0,
                generation_seed=None,
                generation_context=context,
            )
        self.assertFalse(success)
        self.assertIn("cancel", message.casefold())
        self.assertFalse(any((self.root / "voicelines").glob("voiceline_*")))
        chunk = json.loads((self.root / "chunks.json").read_text(encoding="utf-8"))[0]
        self.assertEqual(chunk["status"], "pending")
        self.assertIsNone(chunk["audio_path"])
        terminal = finalize_request(
            self.root,
            running["request_id"],
            running["owner_token"],
        )
        self.assertEqual(terminal["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()
