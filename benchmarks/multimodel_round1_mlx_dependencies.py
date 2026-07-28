"""Lazy MLX/audio dependencies and typed errors for fake-safe tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
import wave

import numpy as np


MOSS_TOKENIZER_REVISION: Final[str] = (
    "f6e20e543b33d2c252a7ef71bdf8aa71e5ff9169"
)


class MlxRunnerError(RuntimeError):
    """Base error for expected MLX runner failures."""


@dataclass(frozen=True, slots=True)
class MlxDependencyError(MlxRunnerError):
    dependency: str

    def __str__(self) -> str:
        return f"MLX dependency is unavailable: {self.dependency}"


@dataclass(frozen=True, slots=True)
class NoAudioGeneratedError(MlxRunnerError):
    source: str

    def __str__(self) -> str:
        return f"MLX candidate returned no audio: {self.source}"


@dataclass(frozen=True, slots=True)
class ReferencePathError(MlxRunnerError):
    path: Path

    def __str__(self) -> str:
        return f"Reference path is outside the evidence root: {self.path}"


@dataclass(frozen=True, slots=True)
class ManifestPathError(MlxRunnerError):
    path: str
    detail: str

    def __str__(self) -> str:
        return f"Unsafe manifest path {self.path!r}: {self.detail}"


@dataclass(frozen=True, slots=True)
class ArtifactPathError(MlxRunnerError):
    path: str
    detail: str

    def __str__(self) -> str:
        return f"Unsafe artifact path {self.path!r}: {self.detail}"


@dataclass(frozen=True, slots=True)
class PreparedReferenceError(MlxRunnerError):
    path: Path
    sample_rate: int
    detail: str

    def __str__(self) -> str:
        return f"Prepared reference is invalid ({self.sample_rate} Hz): {self.path} ({self.detail})"


@dataclass(frozen=True, slots=True)
class ModelSnapshotError(MlxRunnerError):
    repo_id: str
    revision: str

    def __str__(self) -> str:
        return f"Exact cached snapshot missing: {self.repo_id}@{self.revision}"


@dataclass(frozen=True, slots=True)
class GenerationInputError(MlxRunnerError):
    model: str
    detail: str

    def __str__(self) -> str:
        return f"Invalid {self.model} generation input: {self.detail}"


@dataclass(frozen=True, slots=True)
class InvalidAudioError(MlxRunnerError):
    model: str

    def __str__(self) -> str:
        return f"{self.model} produced an invalid WAV"


try:
    import mlx.core as mx
except ModuleNotFoundError:
    class _FallbackRandom:
        def seed(self, _seed: int) -> None:
            return None

    class _FallbackMlx:
        int32 = np.int32
        random = _FallbackRandom()

        @staticmethod
        def array(value: Any, dtype: Any | None = None) -> np.ndarray:
            return np.asarray(value, dtype=dtype)

        @staticmethod
        def clear_cache() -> None:
            return None

        @staticmethod
        def eval(_value: Any) -> None:
            return None

        @staticmethod
        def load(_path: str) -> None:
            raise MlxDependencyError("mlx.core cache loader")

    mx = _FallbackMlx()


try:
    import soundfile as sf
except ModuleNotFoundError:
    @dataclass(frozen=True, slots=True)
    class _FallbackInfo:
        format: str
        channels: int
        samplerate: int
        frames: int

    class _FallbackSoundFile:
        @staticmethod
        def info(path: Any) -> _FallbackInfo:
            with wave.open(path, "rb") as handle:
                return _FallbackInfo(
                    "WAV",
                    handle.getnchannels(),
                    handle.getframerate(),
                    handle.getnframes(),
                )

        @staticmethod
        def read(
            path: Any,
            *,
            dtype: str = "float32",
            always_2d: bool = False,
        ) -> tuple[np.ndarray, int]:
            with wave.open(path, "rb") as handle:
                channels = handle.getnchannels()
                sample_rate = handle.getframerate()
                values = np.frombuffer(
                    handle.readframes(handle.getnframes()), dtype="<i2"
                ).astype(dtype) / 32768.0
            if channels > 1:
                values = values.reshape(-1, channels)
            elif always_2d:
                values = values.reshape(-1, 1)
            return values, sample_rate

        @staticmethod
        def write(
            path: Any,
            audio: np.ndarray,
            sample_rate: int,
            **_options: Any,
        ) -> None:
            values = np.asarray(audio, dtype=np.float32)
            values = np.clip(values, -1.0, 1.0)
            pcm = (values * 32767.0).astype("<i2")
            channels = 1 if pcm.ndim == 1 else pcm.shape[1]
            with wave.open(path, "wb") as handle:
                handle.setnchannels(channels)
                handle.setsampwidth(2)
                handle.setframerate(int(sample_rate))
                handle.writeframes(pcm.tobytes())

    sf = _FallbackSoundFile()


def require_mlx() -> Any:
    return mx


def require_soundfile() -> Any:
    return sf
