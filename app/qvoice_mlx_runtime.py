from __future__ import annotations

import types
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import mlx.core as mx
import mlx.nn as nn
import numpy as np

from qvoice_format import QVoicePack, QwenVoicePackError, parse_qvoice


@dataclass(frozen=True, slots=True)
class QVoiceGraftTensors:
    pack: QVoicePack
    speaker_embedding: np.ndarray
    tts_pad: np.ndarray | None
    tts_bos: np.ndarray | None
    tts_eos: np.ndarray | None
    fc1_weight: np.ndarray | None
    fc1_bias: np.ndarray | None
    fc2_weight: np.ndarray | None
    fc2_bias: np.ndarray | None
    codec_embedding: np.ndarray | None


def _bf16_to_float32(values: np.ndarray) -> np.ndarray:
    expanded = values.astype(np.uint32) << 16
    return expanded.view(np.float32)


def load_qvoice_graft(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
) -> QVoiceGraftTensors:
    pack = parse_qvoice(path, expected_encoder_dimension=2048)
    if expected_sha256 and pack.sha256 != expected_sha256:
        raise QwenVoicePackError(
            "qvoice_integrity_failed",
            "The installed .qvoice no longer matches its approved hash.",
        )
    if not pack.xvector_only:
        raise QwenVoicePackError(
            "qvoice_icl_runtime_unsupported",
            "This .qvoice uses stored ICL codes. Alexandria currently supports "
            "the instruction-preserving x-vector graft path only.",
        )
    arrays: tuple[np.ndarray | None, ...] = (None, None, None, None, None)
    override = pack.weight_override
    if override is not None:
        with pack.path.open("rb") as handle:
            handle.seek(override.offset)
            th = override.text_hidden_dimension
            hidden = override.hidden_dimension
            codec = override.codec_vocabulary
            fc1_weight = _bf16_to_float32(
                np.fromfile(handle, dtype="<u2", count=th * th)
            ).reshape(th, th)
            fc1_bias = np.fromfile(handle, dtype="<f4", count=th)
            fc2_weight = _bf16_to_float32(
                np.fromfile(handle, dtype="<u2", count=hidden * th)
            ).reshape(hidden, th)
            fc2_bias = np.fromfile(handle, dtype="<f4", count=hidden)
            codec_embedding = _bf16_to_float32(
                np.fromfile(handle, dtype="<u2", count=codec * hidden)
            ).reshape(codec, hidden)
            arrays = (
                fc1_weight,
                fc1_bias,
                fc2_weight,
                fc2_bias,
                codec_embedding,
            )
    return QVoiceGraftTensors(
        pack=pack,
        speaker_embedding=np.asarray(pack.speaker_embedding, dtype=np.float32),
        tts_pad=(
            np.asarray(pack.tts_pad_embedding, dtype=np.float32)
            if pack.tts_pad_embedding is not None
            else None
        ),
        tts_bos=(
            np.asarray(pack.tts_bos_embedding, dtype=np.float32)
            if pack.tts_bos_embedding is not None
            else None
        ),
        tts_eos=(
            np.asarray(pack.tts_eos_embedding, dtype=np.float32)
            if pack.tts_eos_embedding is not None
            else None
        ),
        fc1_weight=arrays[0],
        fc1_bias=arrays[1],
        fc2_weight=arrays[2],
        fc2_bias=arrays[3],
        codec_embedding=arrays[4],
    )


class _GraftedCodecEmbedding:
    def __init__(self, delegate, speaker_id: int, tensors: QVoiceGraftTensors):
        self._delegate = delegate
        self._speaker_id = speaker_id
        self._speaker = mx.array(tensors.speaker_embedding)
        self._codec = (
            mx.array(tensors.codec_embedding)
            if tensors.codec_embedding is not None
            else None
        )

    def __call__(self, indices):
        index = mx.array(indices)
        result = self._delegate(index)
        if self._codec is not None:
            upper = self._codec.shape[0]
            safe = mx.clip(index, 0, upper - 1)
            replacement = self._codec[safe]
            mask = ((index >= 0) & (index < upper))[..., None]
            result = mx.where(mask, replacement, result)
        speaker = mx.broadcast_to(self._speaker, result.shape)
        return mx.where((index == self._speaker_id)[..., None], speaker, result)


def _replace_tpad(
    model,
    tensors: QVoiceGraftTensors,
    original_prepare,
    *args,
    **kwargs,
):
    input_embeds, trailing, current_pad = original_prepare(*args, **kwargs)
    if tensors.tts_pad is None:
        return input_embeds, trailing, current_pad
    config = model.config
    talker_config = config.talker_config
    tokens = mx.array(
        [[config.tts_bos_token_id, config.tts_eos_token_id, config.tts_pad_token_id]]
    )
    projected = model.talker.text_projection(
        model.talker.get_text_embeddings()(tokens)
    )
    current_bos = projected[:, 0:1, :]
    current_eos = projected[:, 1:2, :]
    override_pad = mx.array(tensors.tts_pad)[None, None, :]
    override_bos = mx.array(tensors.tts_bos)[None, None, :]
    override_eos = mx.array(tensors.tts_eos)[None, None, :]
    language = str(kwargs.get("language") or "auto").lower()
    has_language = bool(
        language != "auto"
        and getattr(talker_config, "codec_language_id", None)
        and language in talker_config.codec_language_id
    )
    codec_prefill = 4 if has_language else 3
    pad_count = codec_prefill + (1 if kwargs.get("speaker") else 0)
    instruct = kwargs.get("instruct")
    instruct_length = 0
    if instruct:
        formatted = f"<|im_start|>user\n{instruct}<|im_end|>\n"
        instruct_length = len(model.tokenizer.encode(formatted))
    start = instruct_length + 3
    pad_segment = (
        input_embeds[:, start : start + pad_count, :]
        - current_pad
        + override_pad
    )
    bos_index = start + pad_count
    bos_segment = (
        input_embeds[:, bos_index : bos_index + 1, :]
        - current_bos
        + override_bos
    )
    input_embeds = mx.concatenate(
        [
            input_embeds[:, :start, :],
            pad_segment,
            bos_segment,
            input_embeds[:, bos_index + 1 :, :],
        ],
        axis=1,
    )
    trailing = mx.concatenate(
        [trailing[:, :-1, :], trailing[:, -1:, :] - current_eos + override_eos],
        axis=1,
    )
    return input_embeds, trailing, override_pad


@contextmanager
def apply_qvoice_graft(
    model,
    tensors: QVoiceGraftTensors,
    *,
    speaker: str,
) -> Iterator[None]:
    if getattr(model.config, "tts_model_type", None) != "custom_voice":
        raise QwenVoicePackError(
            "qvoice_custom_model_required",
            "Community .qvoice grafts require the 1.7B CustomVoice model.",
        )
    speaker_ids = getattr(model.config.talker_config, "spk_id", {}) or {}
    speaker_id = speaker_ids.get(speaker.casefold())
    if speaker_id is None:
        raise QwenVoicePackError(
            "qvoice_speaker_slot_missing",
            "The CustomVoice model has no compatible speaker slot.",
        )
    talker = model.talker
    original_get = talker.get_input_embeddings
    original_prepare = model._prepare_generation_inputs
    projection = getattr(talker, "text_projection", None)
    originals = None
    if tensors.fc1_weight is not None:
        originals = (projection.linear_fc1, projection.linear_fc2)
        text_hidden = tensors.fc1_weight.shape[1]
        hidden = tensors.fc2_weight.shape[0]
        fc1 = nn.Linear(text_hidden, text_hidden, bias=True)
        fc2 = nn.Linear(text_hidden, hidden, bias=True)
        fc1.weight = mx.array(tensors.fc1_weight)
        fc1.bias = mx.array(tensors.fc1_bias)
        fc2.weight = mx.array(tensors.fc2_weight)
        fc2.bias = mx.array(tensors.fc2_bias)
        projection.linear_fc1 = fc1
        projection.linear_fc2 = fc2
    grafted = _GraftedCodecEmbedding(original_get(), int(speaker_id), tensors)
    talker.get_input_embeddings = types.MethodType(lambda _self: grafted, talker)
    if tensors.tts_pad is not None:
        model._prepare_generation_inputs = types.MethodType(
            lambda self, *args, **kwargs: _replace_tpad(
                self,
                tensors,
                original_prepare,
                *args,
                **kwargs,
            ),
            model,
        )
    try:
        yield
    finally:
        talker.get_input_embeddings = original_get
        model._prepare_generation_inputs = original_prepare
        if originals is not None:
            projection.linear_fc1, projection.linear_fc2 = originals
