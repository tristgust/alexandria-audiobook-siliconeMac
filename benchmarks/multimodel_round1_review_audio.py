"""Sanitize and publish content-addressed Round 1 review audio."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from multimodel_round1_paths import (
    ContainedPath,
    contained_path,
    contained_path_guard,
    safe_atomic_copy,
    safe_atomic_write_bytes,
    safe_sha256_file,
)
from multimodel_round1_public_audio import (
    decoded_audio_sha256,
    sanitize_public_audio,
    verify_public_audio,
)


class ReviewAudioError(RuntimeError):
    def __init__(self, code: str, subject: str) -> None:
        self.code = code
        self.subject = subject
        super().__init__(f"{code}: {subject}")


class PublicAudioDirectory(str, Enum):
    CANDIDATE = "audio"
    REFERENCE = "reference-audio"


@dataclass(frozen=True, slots=True)
class PublishedAudio:
    relative_path: str
    source_sha256: str
    public_sha256: str
    source_decoded_sha256: str
    public_decoded_sha256: str


@dataclass(frozen=True, slots=True)
class AudioPublisher:
    evidence_root: Path
    public_root_name: str

    def publish(
        self,
        source: ContainedPath,
        directory: PublicAudioDirectory,
        expected_source_sha256: str,
    ) -> PublishedAudio:
        source_sha = safe_sha256_file(source)
        if source_sha != expected_source_sha256:
            raise ReviewAudioError("source_audio_hash", str(source.literal))
        suffix = source.literal.suffix.casefold()
        stage = contained_path(
            self.evidence_root,
            f"recovery/package-audio-stage/current{suffix}",
        )
        safe_atomic_write_bytes(stage, b"")
        guard = contained_path_guard(self.evidence_root)
        sanitized = sanitize_public_audio(
            source.literal,
            stage.literal,
            path_guard=guard,
        )
        source_decoded_sha256 = decoded_audio_sha256(
            source.literal,
            path_guard=guard,
        )
        if source_decoded_sha256 != sanitized.decoded_sha256:
            raise ReviewAudioError("source_public_decoded_mismatch", str(source.literal))
        relative = f"{directory.value}/{sanitized.sha256}{suffix}"
        public = contained_path(
            self.evidence_root,
            f"{self.public_root_name}/{relative}",
        )
        safe_atomic_copy(stage, public)
        verified = verify_public_audio(public.literal, path_guard=guard)
        if (
            verified.sha256 != sanitized.sha256
            or verified.decoded_sha256 != sanitized.decoded_sha256
        ):
            raise ReviewAudioError("public_audio_copy_changed", relative)
        return PublishedAudio(
            relative,
            source_sha,
            verified.sha256,
            source_decoded_sha256,
            verified.decoded_sha256,
        )
