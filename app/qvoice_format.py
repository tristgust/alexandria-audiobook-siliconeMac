from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from qvoice_binary import QVoiceBinaryError, QVoiceReader, fixed_text, sha256_file
from qvoice_sections import QVoiceWeightOverride, parse_optional_sections


QVCE_MAGIC: Final = b"QVCE"
QVCE_VERSION: Final = 3
CODEBOOKS: Final = 16
CODEBOOK_SIZE: Final = 2048
MAX_REFERENCE_TEXT_BYTES: Final = 65_536
MAX_REFERENCE_FRAMES: Final = 100_000


class QwenVoicePackError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class QVoicePack:
    path: Path
    sha256: str
    version: int
    encoder_dimension: int
    speaker_embedding: tuple[float, ...]
    reference_text: str | None
    reference_codes: tuple[tuple[int, ...], ...]
    reference_frames: int
    language_id: int
    language: str | None
    model_hidden_dimension: int
    reference_duration: float
    voice_name: str | None
    flags: int
    tts_pad_embedding: tuple[float, ...] | None
    tts_bos_embedding: tuple[float, ...] | None
    tts_eos_embedding: tuple[float, ...] | None
    weight_override: QVoiceWeightOverride | None
    sections: tuple[str, ...]

    @property
    def has_icl(self) -> bool:
        return bool(self.flags & 0b010) and self.reference_frames > 0

    @property
    def xvector_only(self) -> bool:
        return bool(self.flags & 0b001)

    @property
    def source_is_base_model(self) -> bool:
        return bool(self.flags & 0b100)


@dataclass(frozen=True, slots=True)
class _QVoiceMetadata:
    language_id: int
    language: str | None
    hidden_dimension: int
    duration: float
    voice_name: str | None
    flags: int


def parse_qvoice(
    path: str | Path,
    *,
    expected_encoder_dimension: int | None = None,
) -> QVoicePack:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise QwenVoicePackError(
            "qvoice_missing",
            f"The .qvoice file does not exist: {source}",
        )
    with source.open("rb") as handle:
        reader = QVoiceReader(handle)
        try:
            return _parse_qvoice_stream(
                source,
                reader,
                expected_encoder_dimension=expected_encoder_dimension,
            )
        except QVoiceBinaryError as exc:
            raise QwenVoicePackError(exc.code, str(exc)) from exc


def _parse_qvoice_stream(
    source: Path,
    reader: QVoiceReader,
    *,
    expected_encoder_dimension: int | None,
) -> QVoicePack:
    version, encoder_dimension, speaker_embedding = _read_prompt_header(
        reader,
        expected_encoder_dimension=expected_encoder_dimension,
    )
    reference_text, reference_codes, reference_frames = _read_reference(reader)
    metadata = _read_metadata(reader, encoder_dimension=encoder_dimension)
    _validate_prompt_mode(metadata.flags, reference_text, reference_frames)
    optional = parse_optional_sections(
        reader,
        hidden_dimension=metadata.hidden_dimension,
    )
    return QVoicePack(
        path=source,
        sha256=sha256_file(source),
        version=version,
        encoder_dimension=encoder_dimension,
        speaker_embedding=speaker_embedding,
        reference_text=reference_text,
        reference_codes=reference_codes,
        reference_frames=reference_frames,
        language_id=metadata.language_id,
        language=metadata.language,
        model_hidden_dimension=metadata.hidden_dimension,
        reference_duration=metadata.duration,
        voice_name=metadata.voice_name,
        flags=metadata.flags,
        tts_pad_embedding=optional.pad,
        tts_bos_embedding=optional.bos,
        tts_eos_embedding=optional.eos,
        weight_override=optional.override,
        sections=optional.names,
    )


def _read_prompt_header(
    reader: QVoiceReader,
    *,
    expected_encoder_dimension: int | None,
) -> tuple[int, int, tuple[float, ...]]:
    if reader.exact(4, "magic") != QVCE_MAGIC:
        raise QwenVoicePackError(
            "qvoice_invalid_magic",
            "The file is not a QVCE voice pack.",
        )
    version = reader.u32("version")
    if version != QVCE_VERSION:
        raise QwenVoicePackError(
            "qvoice_unsupported_version",
            f"Alexandria supports QVCE version {QVCE_VERSION}, not {version}.",
        )
    dimension = reader.u32("encoder dimension")
    if expected_encoder_dimension is not None and dimension != expected_encoder_dimension:
        raise QwenVoicePackError(
            "qvoice_model_mismatch",
            f"The pack uses encoder dimension {dimension}; "
            f"the selected model requires {expected_encoder_dimension}.",
        )
    if dimension not in {1024, 2048}:
        raise QwenVoicePackError(
            "qvoice_model_mismatch",
            f"Unsupported Qwen encoder dimension: {dimension}.",
        )
    embedding = reader.f32_values(dimension, "speaker embedding")
    if not all(math.isfinite(value) for value in embedding):
        raise QwenVoicePackError(
            "qvoice_invalid_embedding",
            "The speaker embedding contains a non-finite value.",
        )
    return version, dimension, embedding


def _read_reference(
    reader: QVoiceReader,
) -> tuple[str | None, tuple[tuple[int, ...], ...], int]:
    text_size = reader.u32("reference text length")
    if text_size > MAX_REFERENCE_TEXT_BYTES:
        raise QwenVoicePackError(
            "qvoice_invalid_text",
            "The reference transcript is too large.",
        )
    reference_text = None
    if text_size:
        try:
            reference_text = reader.exact(text_size, "reference text").decode("utf-8")
        except UnicodeDecodeError as exc:
            raise QwenVoicePackError(
                "qvoice_invalid_text",
                "The ICL reference transcript is not valid UTF-8.",
            ) from exc
    frames = reader.u32("reference frame count")
    if frames > MAX_REFERENCE_FRAMES:
        raise QwenVoicePackError(
            "qvoice_invalid_codes",
            "The ICL reference frame count is unsafe.",
        )
    flat_codes = reader.i32_values(frames * CODEBOOKS, "reference codes")
    if not all(0 <= value < CODEBOOK_SIZE for value in flat_codes):
        raise QwenVoicePackError(
            "qvoice_invalid_codes",
            "The ICL reference codes exceed the supported codebook.",
        )
    codes = tuple(
        flat_codes[index : index + CODEBOOKS]
        for index in range(0, len(flat_codes), CODEBOOKS)
    )
    return reference_text, codes, frames


def _read_metadata(
    reader: QVoiceReader,
    *,
    encoder_dimension: int,
) -> _QVoiceMetadata:
    if reader.marker() != b"META":
        raise QwenVoicePackError(
            "qvoice_metadata_required",
            "QVCE version 3 requires the META section.",
        )
    language_id = reader.u32("language identifier")
    language = fixed_text(reader.exact(16, "language name"), "language")
    hidden = reader.u32("model hidden dimension")
    meta_encoder = reader.u32("metadata encoder dimension")
    duration = reader.f32_values(1, "reference duration")[0]
    voice_name = fixed_text(reader.exact(64, "voice name"), "voice name")
    flags = reader.u32("metadata flags")
    if meta_encoder != encoder_dimension or hidden not in {1024, 2048}:
        raise QwenVoicePackError(
            "qvoice_model_mismatch",
            "QVCE metadata does not match its prompt tensors.",
        )
    if not math.isfinite(duration) or duration < 0 or flags & ~0b111:
        raise QwenVoicePackError(
            "qvoice_invalid_metadata",
            "QVCE metadata contains invalid duration or flags.",
        )
    return _QVoiceMetadata(
        language_id=language_id,
        language=language,
        hidden_dimension=hidden,
        duration=duration,
        voice_name=voice_name,
        flags=flags,
    )


def _validate_prompt_mode(
    flags: int,
    reference_text: str | None,
    reference_frames: int,
) -> None:
    xvector_only = bool(flags & 0b001)
    has_icl = bool(flags & 0b010)
    if xvector_only == has_icl:
        raise QwenVoicePackError(
            "qvoice_invalid_metadata",
            "A .qvoice must select exactly one prompt mode: x-vector or ICL.",
        )
    if has_icl and (reference_text is None or reference_frames == 0):
        raise QwenVoicePackError(
            "qvoice_invalid_codes",
            "An ICL .qvoice requires a transcript and reference codes.",
        )
