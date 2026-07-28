from __future__ import annotations

import json
import tempfile
import wave
from pathlib import Path
from typing import TypedDict

from audio_processing import GeneratedSpeechTooShortError
from project import ProjectManager


class BatchProviderFailure(RuntimeError):
    """A provider/bootstrap failure used by lifecycle tests."""


class PreflightFailure(RuntimeError):
    """A preflight failure used to verify public redaction."""


class BatchResult(TypedDict, total=False):
    """The provider's nominal result shape; malformed cases omit keys."""

    completed: list[int]
    failed: list[tuple[int, object]]


class AudioFailureProjectMixin:
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        (self.root / "app" / "config.json").write_text(
            json.dumps({"tts": {"language": "English"}}),
            encoding="utf-8",
        )
        (self.root / "voice_config.json").write_text(
            json.dumps({"NARRATOR": {"type": "custom", "voice": "Ryan"}}),
            encoding="utf-8",
        )
        self.manager = ProjectManager(str(self.root))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_chunks(self, chunks: list[dict[str, object]]) -> None:
        (self.root / "chunks.json").write_text(
            json.dumps(chunks),
            encoding="utf-8",
        )

    def read_chunks(self) -> list[dict[str, object]]:
        return json.loads((self.root / "chunks.json").read_text(encoding="utf-8"))

    @staticmethod
    def chunk(index: int) -> dict[str, object]:
        return {
            "id": index,
            "speaker": "NARRATOR",
            "text": "The bounded failure must remain auditable.",
            "instruct": "Calm and clear.",
            "status": "pending",
            "audio_path": None,
        }

    @staticmethod
    def cast() -> dict[str, object]:
        return {
            "characters": [
                {
                    "character_id": "character_narrator",
                    "display_name": "Narrator",
                    "script_connection": {
                        "resolved_script_voice_label": "NARRATOR",
                    },
                    "voice": {"valid": True},
                }
            ]
        }


class BoundedFailureEngine:
    def __init__(self, message: str) -> None:
        self.message = message

    def generate_voice(self, text, instruct, speaker, voice_config, output_path):
        raise GeneratedSpeechTooShortError(self.message)


class FailedBatchEngine:
    def __init__(self, message: str) -> None:
        self.message = message

    def generate_batch(self, chunks, voice_config, output_dir, seed):
        failed = [(item["index"], self.message) for item in chunks]
        return {"completed": [], "failed": failed}


class RaisingBatchEngine:
    def generate_batch(self, chunks, voice_config, output_dir, seed):
        raise BatchProviderFailure("backend failed at /private/provider/request-123")


class MalformedBatchEngine:
    def __init__(self, result: object) -> None:
        self.result = result

    def generate_batch(self, chunks, voice_config, output_dir, seed):
        return self.result


class SuccessfulEngine:
    def generate_voice(self, text, instruct, speaker, voice_config, output_path):
        with wave.open(str(output_path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(24000)
            handle.writeframes(b"\x00\x40" * 60000)
        return True
