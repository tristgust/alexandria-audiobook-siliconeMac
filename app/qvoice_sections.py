from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from qvoice_binary import QVoiceBinaryError, QVoiceReader


MAX_WEIGHT_OVERRIDE_BYTES = 256 * 1024 * 1024
SUPPORTED_CODEC_VOCABULARY = 2048


@dataclass(frozen=True, slots=True)
class QVoiceWeightOverride:
    offset: int
    byte_length: int
    hidden_dimension: int
    text_hidden_dimension: int
    codec_vocabulary: int


@dataclass(frozen=True, slots=True)
class QVoiceOptionalSections:
    pad: tuple[float, ...] | None
    bos: tuple[float, ...] | None
    eos: tuple[float, ...] | None
    override: QVoiceWeightOverride | None
    names: tuple[str, ...]


def parse_optional_sections(
    reader: QVoiceReader,
    *,
    hidden_dimension: int,
) -> QVoiceOptionalSections:
    names = ["META"]
    pad = bos = eos = None
    override = None
    marker = reader.marker()
    if marker == b"TPAD":
        tpad_hidden = reader.u32("TPAD hidden dimension")
        if tpad_hidden != hidden_dimension:
            raise QVoiceBinaryError(
                "qvoice_model_mismatch",
                "TPAD does not match the source model.",
            )
        pad = reader.f32_values(hidden_dimension, "TPAD pad embedding")
        bos = reader.f32_values(hidden_dimension, "TPAD BOS embedding")
        eos = reader.f32_values(hidden_dimension, "TPAD EOS embedding")
        if not all(math.isfinite(value) for value in (*pad, *bos, *eos)):
            raise QVoiceBinaryError(
                "qvoice_invalid_embedding",
                "TPAD contains a non-finite value.",
            )
        names.append("TPAD")
        marker = reader.marker()
    if marker == b"WOVR":
        override = _parse_weight_override(reader, hidden_dimension)
        names.append("WOVR")
        marker = reader.marker()
    if marker in {b"WDLT", b"WFUL"}:
        raise QVoiceBinaryError(
            "qvoice_frozen_weights_unsupported",
            "This pack contains a full or delta talker replacement that would not "
            "preserve Alexandria's emotional instruction path.",
        )
    if marker is not None:
        raise QVoiceBinaryError(
            "qvoice_unknown_section",
            f"Unsupported .qvoice section: {marker!r}.",
        )
    return QVoiceOptionalSections(pad, bos, eos, override, tuple(names))


def _parse_weight_override(
    reader: QVoiceReader,
    hidden_dimension: int,
) -> QVoiceWeightOverride:
    weight_hidden = reader.u32("WOVR hidden dimension")
    text_hidden = reader.u32("WOVR text hidden dimension")
    codec_vocabulary = reader.u32("WOVR codec vocabulary")
    if (
        weight_hidden != hidden_dimension
        or text_hidden != 2048
        or codec_vocabulary != SUPPORTED_CODEC_VOCABULARY
    ):
        raise QVoiceBinaryError(
            "qvoice_model_mismatch",
            "WOVR does not match the source model.",
        )
    byte_length = (
        (text_hidden * text_hidden * 2)
        + (text_hidden * 4)
        + (weight_hidden * text_hidden * 2)
        + (weight_hidden * 4)
        + (codec_vocabulary * weight_hidden * 2)
    )
    if byte_length > MAX_WEIGHT_OVERRIDE_BYTES:
        raise QVoiceBinaryError(
            "qvoice_weight_override_unsafe",
            "WOVR exceeds Alexandria's safe import limit.",
        )
    offset = reader.tell()
    _finite_bfloat16(
        reader.exact(text_hidden * text_hidden * 2, "WOVR text weight"),
        "WOVR text weight",
    )
    _finite_float32(
        reader.exact(text_hidden * 4, "WOVR text bias"),
        "WOVR text bias",
    )
    _finite_bfloat16(
        reader.exact(weight_hidden * text_hidden * 2, "WOVR hidden weight"),
        "WOVR hidden weight",
    )
    _finite_float32(
        reader.exact(weight_hidden * 4, "WOVR hidden bias"),
        "WOVR hidden bias",
    )
    _finite_bfloat16(
        reader.exact(codec_vocabulary * weight_hidden * 2, "WOVR codec weight"),
        "WOVR codec weight",
    )
    return QVoiceWeightOverride(
        offset=offset,
        byte_length=byte_length,
        hidden_dimension=weight_hidden,
        text_hidden_dimension=text_hidden,
        codec_vocabulary=codec_vocabulary,
    )


def _finite_bfloat16(payload: bytes, label: str) -> None:
    values = np.frombuffer(payload, dtype="<u2")
    if np.any((values & 0x7F80) == 0x7F80):
        raise QVoiceBinaryError(
            "qvoice_invalid_weight_override",
            f"{label} contains a non-finite value.",
        )


def _finite_float32(payload: bytes, label: str) -> None:
    if not np.isfinite(np.frombuffer(payload, dtype="<f4")).all():
        raise QVoiceBinaryError(
            "qvoice_invalid_weight_override",
            f"{label} contains a non-finite value.",
        )
