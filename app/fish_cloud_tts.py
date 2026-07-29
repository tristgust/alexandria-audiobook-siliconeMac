from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import shutil
import tempfile
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import librosa
import numpy as np
import requests
import soundfile as sf
from pydub import AudioSegment

from fish_cloud_credentials import get_fish_api_key
from model_registry import model_cache_status, model_spec, resolve_model_path


FISH_API_BASE = "https://api.fish.audio"
DEFAULT_FISH_MODEL = "s2.1-pro-free"
FISH_SUPPORTED_MODELS = frozenset({"s2.1-pro-free", "s2-pro"})
FISH_PROVIDER_ID = "fish_s21_cloud"
FISH_CHUNK_FIELD_NAMES = (
    "cloud_provider",
    "cloud_model",
    "cloud_style_route",
    "cloud_prompt_variant",
    "cloud_candidate_count",
    "cloud_text_validation_passed",
    "cloud_word_error_rate",
    "cloud_identity_score",
    "cloud_identity_score_mode",
    "cloud_delivery_score",
    "cloud_quality_score",
    "cloud_selection_score",
    "cloud_reference_fingerprint",
    "cloud_reference_model_reused",
    "cloud_auto_selected",
    "cloud_manual_review_required",
)
_WORD_PATTERN = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
MLX_IDENTITY_FLOOR = 0.94
QUALITY_FLOOR = 0.65
STYLE_DELIVERY_FLOORS = {
    "neutral": 0.0,
    "grief": 0.34,
    "sarcasm": 0.45,
    "fear": 0.18,
    "expressive": 0.25,
}


def fish_cloud_chunk_reset_fields() -> dict[str, Any]:
    return {name: None for name in FISH_CHUNK_FIELD_NAMES}


class FishCloudError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FishPromptVariant:
    key: str
    text: str
    prior: float


@dataclass(frozen=True)
class FishPromptRoute:
    style: str
    variants: tuple[FishPromptVariant, ...]
    difficult: bool


@dataclass(frozen=True)
class AudioFeatures:
    duration_seconds: float
    words_per_second: float
    rms_mean: float
    rms_cv: float
    pitch_median_hz: float
    pitch_cv: float
    spectral_centroid_hz: float
    silence_ratio: float
    clipping_ratio: float


@dataclass(frozen=True)
class CandidateAssessment:
    prompt_key: str
    prompt_prior: float
    transcript: str
    word_error_rate: float
    text_passed: bool
    identity_score: float
    identity_mode: str
    delivery_score: float
    quality_score: float
    total_score: float
    features: AudioFeatures

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["features"] = asdict(self.features)
        return value


@dataclass(frozen=True)
class FishGenerationResult:
    output_path: str
    selected: CandidateAssessment
    candidates: tuple[CandidateAssessment, ...]
    style: str
    reference_fingerprint: str
    reference_model_reused: bool

    def metadata(self) -> dict[str, Any]:
        return {
            "cloud_provider": FISH_PROVIDER_ID,
            "cloud_model": DEFAULT_FISH_MODEL,
            "cloud_style_route": self.style,
            "cloud_prompt_variant": self.selected.prompt_key,
            "cloud_candidate_count": len(self.candidates),
            "cloud_text_validation_passed": self.selected.text_passed,
            "cloud_word_error_rate": round(self.selected.word_error_rate, 6),
            "cloud_identity_score": round(self.selected.identity_score, 6),
            "cloud_identity_score_mode": self.selected.identity_mode,
            "cloud_delivery_score": round(self.selected.delivery_score, 6),
            "cloud_quality_score": round(self.selected.quality_score, 6),
            "cloud_selection_score": round(self.selected.total_score, 6),
            "cloud_reference_fingerprint": self.reference_fingerprint,
            "cloud_reference_model_reused": self.reference_model_reused,
            "cloud_auto_selected": True,
            "cloud_manual_review_required": False,
            "production_promotion_allowed": True,
        }


def normalized_words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return [word.replace("’", "'") for word in _WORD_PATTERN.findall(normalized)]


def word_error_rate(reference: str, hypothesis: str) -> float:
    left = normalized_words(reference)
    right = normalized_words(hypothesis)
    if not left:
        return 0.0 if not right else 1.0
    previous = list(range(len(right) + 1))
    for row, left_word in enumerate(left, start=1):
        current = [row]
        for column, right_word in enumerate(right, start=1):
            current.append(
                min(
                    previous[column - 1] + (left_word != right_word),
                    previous[column] + 1,
                    current[column - 1] + 1,
                )
            )
        previous = current
    return previous[-1] / len(left)


def _normalized_instruction(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def classify_delivery(instruction: str) -> str:
    text = _normalized_instruction(instruction).casefold()
    if not text or any(word in text for word in ("neutral", "natural", "ordinary")):
        return "neutral"
    if any(
        word in text
        for word in (
            "fear",
            "fearful",
            "scared",
            "panic",
            "panicked",
            "terror",
            "terrified",
            "anxious",
            "danger",
        )
    ):
        return "fear"
    if any(
        word in text
        for word in (
            "grief",
            "grieving",
            "sad",
            "sorrow",
            "mourn",
            "loss",
            "heartbroken",
            "crying",
            "tearful",
        )
    ):
        return "grief"
    if any(
        word in text
        for word in (
            "sarcas",
            "sardonic",
            "ironic",
            "dryly",
            "dry wit",
            "mocking",
            "amused disbelief",
        )
    ):
        return "sarcasm"
    return "expressive"


def _bracket(instruction: str, text: str) -> str:
    return f"[{_normalized_instruction(instruction)}] {text.strip()}"


def build_prompt_route(text: str, instruction: str) -> FishPromptRoute:
    target = str(text or "").strip()
    if not target:
        raise FishCloudError("fish_text_required", "Fish generation requires text.")
    authored = _normalized_instruction(instruction)
    style = classify_delivery(authored)
    if style == "neutral":
        candidates = (
            FishPromptVariant(
                "simple_tag",
                _bracket("neutral, natural clear delivery", target),
                0.08,
            ),
            FishPromptVariant("untagged", target, 0.04),
            FishPromptVariant(
                "full_alexandria_tag",
                _bracket(authored or "Natural, clear delivery.", target),
                0.02,
            ),
        )
    elif style == "grief":
        candidates = (
            FishPromptVariant(
                "full_alexandria_tag",
                _bracket(authored or "Deep restrained grief and loss.", target),
                0.10,
            ),
            FishPromptVariant(
                "rich_tag",
                _bracket(
                    "deep restrained grief, pain held back, close to breaking",
                    target,
                ),
                0.06,
            ),
            FishPromptVariant("simple_tag", _bracket("sad", target), 0.03),
            FishPromptVariant("untagged", target, 0.0),
        )
    elif style == "sarcasm":
        candidates = (
            FishPromptVariant(
                "rich_tag",
                _bracket(
                    "dry sarcasm, amused disbelief, ironic emphasis, understated comedy",
                    target,
                ),
                0.10,
            ),
            FishPromptVariant(
                "full_alexandria_tag",
                _bracket(authored or "Dry, unmistakable sarcasm.", target),
                0.06,
            ),
            FishPromptVariant("simple_tag", _bracket("sarcastic", target), 0.03),
            FishPromptVariant("untagged", target, 0.0),
        )
    elif style == "fear":
        candidates = (
            FishPromptVariant(
                "full_alexandria_tag",
                _bracket(
                    authored
                    or (
                        "Unmistakable fear with tight uneven breath, tense caution, "
                        "and immediate nearby danger."
                    ),
                    target,
                ),
                0.12,
            ),
            FishPromptVariant(
                "paralinguistic_fear_tag",
                _bracket(
                    "scared, tight uneven breath, audible inhale, tense caution, "
                    "immediate nearby danger",
                    target,
                ),
                0.10,
            ),
            FishPromptVariant(
                "embedded_fear_cue",
                _bracket(
                    "terrified but trying to stay quiet; breath catches before the realization",
                    target.replace(
                        ",",
                        ", [sharp inhale, voice tightens]",
                        1,
                    ),
                ),
                0.08,
            ),
            FishPromptVariant(
                "rich_tag",
                _bracket(
                    "fear held barely under control, breath catching, alert to danger",
                    target,
                ),
                0.06,
            ),
            FishPromptVariant("simple_tag", _bracket("scared", target), 0.03),
        )
    else:
        candidates = (
            FishPromptVariant(
                "full_alexandria_tag",
                _bracket(authored or "Expressive, natural delivery.", target),
                0.08,
            ),
            FishPromptVariant(
                "rich_tag",
                _bracket(authored or "Expressive, natural delivery", target),
                0.05,
            ),
            FishPromptVariant("untagged", target, 0.0),
        )
    deduped: list[FishPromptVariant] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.text in seen:
            continue
        seen.add(candidate.text)
        deduped.append(candidate)
    return FishPromptRoute(
        style=style,
        variants=tuple(deduped),
        difficult=style == "fear",
    )


class FishCloudClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_FISH_MODEL,
        base_url: str = FISH_API_BASE,
        session: requests.Session | None = None,
        timeout_seconds: int = 240,
        max_attempts: int = 3,
    ) -> None:
        self._api_key = str(api_key or get_fish_api_key()).strip()
        if not self._api_key:
            raise FishCloudError(
                "fish_api_key_missing",
                "Fish Audio is not configured. Add a Fish key in Speech settings "
                "or set FISH_API_KEY before starting Alexandria.",
            )
        if model not in FISH_SUPPORTED_MODELS:
            raise FishCloudError(
                "fish_model_unsupported",
                f"Unsupported Fish model header: {model!r}.",
            )
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout_seconds = max(10, int(timeout_seconds))
        self.max_attempts = max(1, int(max_attempts))

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _safe_detail(self, response: requests.Response) -> str:
        try:
            payload = response.json()
            detail = payload.get("message") or payload.get("detail") or payload.get("status")
            rendered = detail if isinstance(detail, str) else json.dumps(detail)
        except Exception:
            rendered = response.text[:500]
        return str(rendered or "Fish request failed.").replace(
            self._api_key,
            "[redacted]",
        )

    def request(self, method: str, path: str, *, retryable: bool = True, **kwargs: Any) -> requests.Response:
        last_exception: Exception | None = None
        for attempt in range(self.max_attempts):
            request_kwargs = dict(kwargs)
            try:
                headers = {
                    **self._headers,
                    **dict(request_kwargs.pop("headers", {})),
                }
                response = self.session.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    timeout=request_kwargs.pop(
                        "timeout",
                        self.timeout_seconds,
                    ),
                    **request_kwargs,
                )
            except requests.RequestException as exc:
                last_exception = exc
                if not retryable or attempt + 1 >= self.max_attempts:
                    raise FishCloudError(
                        "fish_network_error",
                        "Fish Audio could not be reached.",
                    ) from exc
                time.sleep(min(4.0, 0.5 * (2**attempt)))
                continue
            if response.status_code < 400:
                return response
            if response.status_code == 402:
                raise FishCloudError(
                    "fish_quota_exhausted",
                    "Fish Audio rejected the request because the account has no "
                    "available credit or free-tier capacity.",
                )
            transient = response.status_code == 429 or response.status_code >= 500
            if retryable and transient and attempt + 1 < self.max_attempts:
                time.sleep(min(8.0, 0.75 * (2**attempt)))
                continue
            raise FishCloudError(
                f"fish_http_{response.status_code}",
                self._safe_detail(response),
            )
        raise FishCloudError(
            "fish_request_failed",
            type(last_exception).__name__ if last_exception else "Fish request failed.",
        )

    def list_owned_models(self, title: str) -> list[dict[str, Any]]:
        response = self.request(
            "GET",
            "/model",
            params={
                "self": "true",
                "title": title,
                "page_size": 100,
                "page_number": 1,
                "sort_by": "created_at",
            },
        )
        payload = response.json()
        return [
            dict(item)
            for item in payload.get("items", [])
            if isinstance(item, Mapping) and item.get("title") == title
        ]

    def create_private_model(
        self,
        *,
        title: str,
        reference_audio: Path,
        reference_text: str,
    ) -> dict[str, Any]:
        with reference_audio.open("rb") as handle:
            response = self.request(
                "POST",
                "/model",
                data=[
                    ("type", "tts"),
                    ("title", title),
                    ("train_mode", "fast"),
                    ("visibility", "private"),
                    (
                        "description",
                        "Private Alexandria voice reference for automatic Fish S2.1 generation.",
                    ),
                    ("enhance_audio_quality", "true"),
                    ("generate_sample", "false"),
                    ("tags", "alexandria-production"),
                    ("texts", reference_text),
                ],
                files={
                    "voices": (
                        "reference.wav",
                        handle,
                        "audio/wav",
                    )
                },
                retryable=False,
                timeout=180,
            )
        payload = response.json()
        if not str(payload.get("_id") or ""):
            raise FishCloudError(
                "fish_reference_model_invalid",
                "Fish Audio created no usable reference model.",
            )
        if payload.get("visibility") not in {None, "private"}:
            raise FishCloudError(
                "fish_reference_model_not_private",
                "Fish Audio did not create the reference model as private.",
            )
        return dict(payload)

    def synthesize(self, *, text: str, reference_id: str, settings: Mapping[str, Any]) -> bytes:
        response = self.request(
            "POST",
            "/v1/tts",
            headers={"Content-Type": "application/json", "model": self.model},
            json={
                "text": text,
                "reference_id": reference_id,
                "temperature": float(settings.get("temperature", 0.7)),
                "top_p": float(settings.get("top_p", 0.7)),
                "prosody": {
                    "speed": float(settings.get("speed", 1.0)),
                    "volume": int(settings.get("volume", 0)),
                    "normalize_loudness": True,
                },
                "chunk_length": int(settings.get("chunk_length", 200)),
                "normalize": True,
                "format": "wav",
                "sample_rate": 44100,
                "latency": str(settings.get("latency", "normal")),
                "max_new_tokens": 1024,
                "repetition_penalty": float(settings.get("repetition_penalty", 1.2)),
                "min_chunk_length": 50,
                "condition_on_previous_chunks": True,
                "early_stop_threshold": 1,
            },
            timeout=240,
        )
        if len(response.content) < 1024:
            raise FishCloudError(
                "fish_audio_too_small",
                "Fish Audio returned an incomplete audio response.",
            )
        return response.content

    def transcribe(self, audio_path: Path, *, language: str | None = "en") -> str:
        with audio_path.open("rb") as handle:
            response = self.request(
                "POST",
                "/v1/asr",
                data={
                    "language": language or "",
                    "ignore_timestamps": "true",
                },
                files={"audio": (audio_path.name, handle, "audio/wav")},
                timeout=180,
            )
        payload = response.json()
        return str(payload.get("text") or "").strip()


class LocalOrFishTranscriber:
    def __init__(self, client: FishCloudClient):
        self.client = client
        self._whisper: Any | None = None
        self._whisper_path: str | None = None
        self._whisper_checked = False
        self._lock = threading.RLock()

    def _load_local(self) -> tuple[Any, str] | None:
        with self._lock:
            if self._whisper_checked:
                return (
                    (self._whisper, self._whisper_path)
                    if self._whisper is not None and self._whisper_path
                    else None
                )
            self._whisper_checked = True
            if platform.system() != "Darwin" or platform.machine() != "arm64":
                return None
            if not model_cache_status("mlx_whisper_base").get("cached"):
                return None
            try:
                import mlx_whisper

                self._whisper = mlx_whisper
                self._whisper_path = resolve_model_path(
                    model_spec("mlx_whisper_base").repo_id
                )
            except Exception:
                self._whisper = None
                self._whisper_path = None
                return None
            return self._whisper, self._whisper_path

    def __call__(self, audio_path: Path) -> str:
        local = self._load_local()
        if local is not None:
            whisper, model_path = local
            try:
                result = whisper.transcribe(
                    str(audio_path),
                    path_or_hf_repo=str(model_path),
                    language="en",
                    word_timestamps=False,
                    condition_on_previous_text=False,
                    verbose=False,
                )
                transcript = str(result.get("text") or "").strip()
                if transcript:
                    return transcript
            except Exception:
                pass
        return self.client.transcribe(audio_path)


def _prepare_reference_wav(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_format = source.suffix.casefold().lstrip(".") or None
    with source.open("rb") as input_handle:
        audio = AudioSegment.from_file(
            input_handle,
            format=source_format,
        )
    audio = audio.set_channels(1).set_frame_rate(24000).set_sample_width(2)
    with destination.open("wb") as output_handle:
        audio.export(output_handle, format="wav")
    info = sf.info(destination)
    if info.frames <= 0 or info.channels != 1 or info.samplerate != 24000:
        raise FishCloudError(
            "fish_reference_audio_invalid",
            "The supplied reference could not be normalized for Fish Audio.",
        )
    return destination


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _closeness(value: float, target: float, tolerance: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(0.0, 1.0 - abs(value - target) / max(tolerance, 1e-6))


def audio_features(path: Path, text: str) -> AudioFeatures:
    audio, sample_rate = librosa.load(path, sr=24000, mono=True)
    if audio.size == 0:
        raise FishCloudError("fish_candidate_empty", "Fish candidate audio is empty.")
    trimmed, _ = librosa.effects.trim(audio, top_db=38)
    signal = trimmed if trimmed.size else audio
    duration = signal.size / sample_rate
    if duration <= 0.15:
        raise FishCloudError(
            "fish_candidate_too_short",
            "Fish candidate audio is too short.",
        )
    rms = librosa.feature.rms(y=signal, frame_length=1024, hop_length=256)[0]
    rms_mean = float(np.mean(rms)) if rms.size else 0.0
    rms_cv = float(np.std(rms) / max(rms_mean, 1e-6)) if rms.size else 0.0
    try:
        f0 = librosa.yin(
            signal,
            fmin=librosa.note_to_hz("C2"),
            fmax=librosa.note_to_hz("C7"),
            sr=sample_rate,
            frame_length=2048,
            hop_length=256,
        )
        f0 = f0[np.isfinite(f0)]
    except Exception:
        f0 = np.asarray([], dtype=np.float32)
    pitch_median = float(np.median(f0)) if f0.size else 0.0
    pitch_cv = float(np.std(f0) / max(pitch_median, 1e-6)) if f0.size else 0.0
    centroid = librosa.feature.spectral_centroid(y=signal, sr=sample_rate)[0]
    spectral_centroid = float(np.mean(centroid)) if centroid.size else 0.0
    frame_rms = librosa.feature.rms(y=audio, frame_length=1024, hop_length=256)[0]
    silence_floor = max(0.0015, float(np.max(frame_rms)) * 0.045) if frame_rms.size else 0.0015
    silence_ratio = float(np.mean(frame_rms < silence_floor)) if frame_rms.size else 1.0
    clipping_ratio = float(np.mean(np.abs(audio) >= 0.985))
    return AudioFeatures(
        duration_seconds=duration,
        words_per_second=len(normalized_words(text)) / duration,
        rms_mean=rms_mean,
        rms_cv=rms_cv,
        pitch_median_hz=pitch_median,
        pitch_cv=pitch_cv,
        spectral_centroid_hz=spectral_centroid,
        silence_ratio=silence_ratio,
        clipping_ratio=clipping_ratio,
    )


def delivery_score(
    style: str,
    candidate: AudioFeatures,
    reference: AudioFeatures,
) -> float:
    def ratio(value: float, baseline: float) -> float:
        return value / max(abs(baseline), 1e-6)

    speed_ratio = ratio(candidate.words_per_second, reference.words_per_second)
    energy_ratio = ratio(candidate.rms_mean, reference.rms_mean)
    pitch_variation_ratio = ratio(candidate.pitch_cv, reference.pitch_cv)
    energy_variation_ratio = ratio(candidate.rms_cv, reference.rms_cv)
    centroid_ratio = ratio(
        candidate.spectral_centroid_hz,
        reference.spectral_centroid_hz,
    )
    if style == "neutral":
        parts = (
            _closeness(speed_ratio, 1.0, 0.45),
            _closeness(energy_ratio, 1.0, 0.55),
            _closeness(pitch_variation_ratio, 1.0, 0.9),
            _closeness(candidate.silence_ratio, reference.silence_ratio, 0.18),
        )
    elif style == "grief":
        parts = (
            _closeness(speed_ratio, 0.78, 0.42),
            _closeness(energy_ratio, 0.82, 0.5),
            _closeness(pitch_variation_ratio, 1.2, 1.0),
            _closeness(
                candidate.silence_ratio,
                min(0.45, reference.silence_ratio + 0.10),
                0.24,
            ),
        )
    elif style == "sarcasm":
        parts = (
            _closeness(speed_ratio, 0.95, 0.5),
            _closeness(pitch_variation_ratio, 1.45, 1.1),
            _closeness(energy_variation_ratio, 1.3, 1.0),
            _closeness(candidate.silence_ratio, 0.12, 0.18),
        )
    elif style == "fear":
        parts = (
            _closeness(speed_ratio, 1.18, 0.58),
            _closeness(pitch_variation_ratio, 1.65, 1.25),
            _closeness(energy_variation_ratio, 1.6, 1.2),
            _closeness(centroid_ratio, 1.15, 0.55),
        )
    else:
        parts = (
            _closeness(speed_ratio, 1.0, 0.65),
            _closeness(energy_variation_ratio, 1.25, 1.25),
            _closeness(pitch_variation_ratio, 1.25, 1.25),
            1.0 - min(1.0, candidate.silence_ratio),
        )
    return float(max(0.0, min(1.0, sum(parts) / len(parts))))


def quality_score(features: AudioFeatures) -> float:
    duration_score = 1.0 if 0.6 <= features.duration_seconds <= 45 else 0.4
    clipping_score = max(0.0, 1.0 - features.clipping_ratio * 80)
    silence_score = max(0.0, 1.0 - max(0.0, features.silence_ratio - 0.55) * 2)
    return float(max(0.0, min(1.0, (duration_score + clipping_score + silence_score) / 3)))


def candidate_is_eligible(assessment: CandidateAssessment) -> bool:
    if not assessment.text_passed or assessment.quality_score < QUALITY_FLOOR:
        return False
    return not (
        assessment.identity_mode == "mlx_qwen"
        and assessment.identity_score < MLX_IDENTITY_FLOOR
    )


def _rank_score(
    values: list[float],
    selected: float,
    *,
    higher_is_better: bool,
) -> float:
    if not values:
        return 0.0
    ordered = sorted(values, reverse=higher_is_better)
    if len(ordered) == 1:
        return 0.5
    try:
        index = ordered.index(selected)
    except ValueError:
        return 0.0
    return 1.0 - index / (len(ordered) - 1)


def repeat_selection_score(
    style: str,
    assessment: CandidateAssessment,
    pool: list[CandidateAssessment],
) -> float:
    """Rank repeats from one prompt route using blind-test-backed cues.

    Prompt selection happens before this function. The score never compares a
    weaker prompt route against the preferred route; it only chooses among
    stochastic repeats that have already passed text, identity, and integrity
    validation.
    """

    feature = assessment.features
    identities = [item.identity_score for item in pool]
    qualities = [item.quality_score for item in pool]
    identity = _rank_score(
        identities,
        assessment.identity_score,
        higher_is_better=True,
    )
    quality = _rank_score(
        qualities,
        assessment.quality_score,
        higher_is_better=True,
    )
    if style == "neutral":
        return (
            identity * 0.30
            + _rank_score(
                [item.features.rms_cv for item in pool],
                feature.rms_cv,
                higher_is_better=False,
            )
            * 0.40
            + _rank_score(
                [item.features.silence_ratio for item in pool],
                feature.silence_ratio,
                higher_is_better=False,
            )
            * 0.10
            + quality * 0.10
            + _closeness(feature.words_per_second, 2.8, 1.0) * 0.10
        )
    if style == "grief":
        return (
            identity * 0.10
            + quality * 0.10
            + _rank_score(
                [item.features.rms_mean for item in pool],
                feature.rms_mean,
                higher_is_better=True,
            )
            * 0.35
            + _rank_score(
                [item.features.silence_ratio for item in pool],
                feature.silence_ratio,
                higher_is_better=False,
            )
            * 0.15
            + _rank_score(
                [item.features.words_per_second for item in pool],
                feature.words_per_second,
                higher_is_better=False,
            )
            * 0.15
            + _rank_score(
                [item.features.pitch_cv for item in pool],
                feature.pitch_cv,
                higher_is_better=False,
            )
            * 0.05
            + _rank_score(
                [item.features.rms_cv for item in pool],
                feature.rms_cv,
                higher_is_better=False,
            )
            * 0.10
        )
    if style == "sarcasm":
        return (
            identity * 0.20
            + quality * 0.10
            + _rank_score(
                [item.features.rms_mean for item in pool],
                feature.rms_mean,
                higher_is_better=True,
            )
            * 0.25
            + _rank_score(
                [item.features.rms_cv for item in pool],
                feature.rms_cv,
                higher_is_better=False,
            )
            * 0.15
            + _rank_score(
                [item.features.spectral_centroid_hz for item in pool],
                feature.spectral_centroid_hz,
                higher_is_better=False,
            )
            * 0.10
            + _rank_score(
                [item.features.silence_ratio for item in pool],
                feature.silence_ratio,
                higher_is_better=False,
            )
            * 0.10
            + _rank_score(
                [item.features.words_per_second for item in pool],
                feature.words_per_second,
                higher_is_better=False,
            )
            * 0.10
        )
    if style == "fear":
        return (
            identity * 0.35
            + quality * 0.10
            + _rank_score(
                [item.features.words_per_second for item in pool],
                feature.words_per_second,
                higher_is_better=False,
            )
            * 0.15
            + _rank_score(
                [item.features.rms_mean for item in pool],
                feature.rms_mean,
                higher_is_better=True,
            )
            * 0.10
            + _rank_score(
                [item.features.spectral_centroid_hz for item in pool],
                feature.spectral_centroid_hz,
                higher_is_better=False,
            )
            * 0.15
            + _rank_score(
                [item.features.rms_cv for item in pool],
                feature.rms_cv,
                higher_is_better=False,
            )
            * 0.10
            + _rank_score(
                [item.features.silence_ratio for item in pool],
                feature.silence_ratio,
                higher_is_better=True,
            )
            * 0.05
        )
    return identity * 0.55 + quality * 0.20 + assessment.delivery_score * 0.25


def prompt_stage_has_delivery(
    style: str,
    candidates: list[CandidateAssessment],
) -> bool:
    if not candidates:
        return False
    floor = STYLE_DELIVERY_FLOORS.get(style, STYLE_DELIVERY_FLOORS["expressive"])
    return max(item.delivery_score for item in candidates) >= floor


class SpeakerSimilarityScorer:
    def __init__(self) -> None:
        self._model: Any | None = None
        self._model_checked = False
        self._mlx_embeddings: dict[str, np.ndarray] = {}
        self._mfcc_embeddings: dict[str, np.ndarray] = {}
        self._lock = threading.RLock()

    def _mlx_model(self) -> Any | None:
        with self._lock:
            if self._model_checked:
                return self._model
            self._model_checked = True
            if platform.system() != "Darwin" or platform.machine() != "arm64":
                return None
            if not model_cache_status("mlx_clone").get("cached"):
                return None
            try:
                import mlx.core as mx  # noqa: F401
                from mlx_audio.tts.utils import load_model
                from mlx_audio.utils import get_model_name_parts

                repo_id = model_spec("mlx_clone").repo_id
                self._model = load_model(
                    resolve_model_path(repo_id),
                    model_name_parts=get_model_name_parts(repo_id),
                )
            except Exception:
                self._model = None
            return self._model

    @staticmethod
    def _audio(path: Path) -> np.ndarray:
        audio, _ = librosa.load(path, sr=24000, mono=True)
        return np.asarray(audio, dtype=np.float32)

    @staticmethod
    def _cache_key(path: Path) -> str:
        resolved = path.expanduser().resolve()
        stat = resolved.stat()
        return f"{resolved}:{stat.st_size}:{stat.st_mtime_ns}"

    def _mlx_embedding(self, path: Path) -> np.ndarray | None:
        model = self._mlx_model()
        if model is None:
            return None
        key = self._cache_key(path)
        with self._lock:
            cached = self._mlx_embeddings.get(key)
        if cached is not None:
            return cached
        try:
            import mlx.core as mx

            embedding = np.asarray(
                model.extract_speaker_embedding(mx.array(self._audio(path)), sr=24000)
            ).reshape(-1)
            normalized = embedding / (np.linalg.norm(embedding) + 1e-12)
        except Exception:
            return None
        with self._lock:
            self._mlx_embeddings[key] = normalized
        return normalized

    def _mfcc_embedding(self, path: Path) -> np.ndarray:
        key = self._cache_key(path)
        with self._lock:
            cached = self._mfcc_embeddings.get(key)
        if cached is not None:
            return cached
        audio, sample_rate = librosa.load(path, sr=24000, mono=True)
        mfcc = librosa.feature.mfcc(y=audio, sr=sample_rate, n_mfcc=24)
        delta = librosa.feature.delta(mfcc)
        vector = np.concatenate(
            [
                np.mean(mfcc, axis=1),
                np.std(mfcc, axis=1),
                np.mean(delta, axis=1),
            ]
        )
        normalized = vector / (np.linalg.norm(vector) + 1e-12)
        with self._lock:
            self._mfcc_embeddings[key] = normalized
        return normalized

    def score(self, reference: Path, candidate: Path) -> tuple[float, str]:
        left = self._mlx_embedding(reference)
        right = self._mlx_embedding(candidate)
        if left is not None and right is not None:
            return float(np.clip(np.dot(left, right), -1.0, 1.0)), "mlx_qwen"
        left = self._mfcc_embedding(reference)
        right = self._mfcc_embedding(candidate)
        # MFCC similarity is useful for ranking candidates from one voice but is
        # not calibrated as an absolute speaker-verification score.
        return float(np.clip((np.dot(left, right) + 1.0) / 2.0, 0.0, 1.0)), "mfcc_rank"


class FishCloudBackend:
    def __init__(
        self,
        *,
        model: str = DEFAULT_FISH_MODEL,
        candidate_count: int = 2,
        difficult_candidate_count: int = 4,
        text_wer_limit: float = 0.08,
        timeout_seconds: int = 240,
        client: FishCloudClient | None = None,
        transcriber: Callable[[Path], str] | None = None,
        similarity: SpeakerSimilarityScorer | None = None,
    ) -> None:
        self.model = model
        self.candidate_count = max(2, min(6, int(candidate_count)))
        self.difficult_candidate_count = max(
            self.candidate_count,
            min(8, int(difficult_candidate_count)),
        )
        self.text_wer_limit = max(0.0, min(0.5, float(text_wer_limit)))
        self.timeout_seconds = max(30, int(timeout_seconds))
        self.client = client or FishCloudClient(
            model=model,
            timeout_seconds=timeout_seconds,
        )
        self.transcriber = transcriber or LocalOrFishTranscriber(self.client)
        self.similarity = similarity or SpeakerSimilarityScorer()
        self._reference_ids: dict[str, str] = {}
        self._reference_features: dict[str, AudioFeatures] = {}
        self._lock = threading.RLock()

    def _ensure_reference_model(
        self,
        *,
        reference_wav: Path,
        reference_text: str,
        speaker: str,
    ) -> tuple[str, str, bool]:
        fingerprint = hashlib.sha256(
            (
                _sha256_file(reference_wav)
                + "\0"
                + hashlib.sha256(reference_text.encode("utf-8")).hexdigest()
            ).encode("ascii")
        ).hexdigest()
        with self._lock:
            cached = self._reference_ids.get(fingerprint)
            if cached:
                return cached, fingerprint, True
        title = f"Alexandria {fingerprint[:20]}"
        remote = next(
            (
                item
                for item in self.client.list_owned_models(title)
                if item.get("visibility") == "private"
                and item.get("state") not in {"failed", "deleted"}
            ),
            None,
        )
        reused = remote is not None
        if remote is None:
            remote = self.client.create_private_model(
                title=title,
                reference_audio=reference_wav,
                reference_text=reference_text,
            )
        model_id = str(remote.get("_id") or "")
        if not model_id:
            raise FishCloudError(
                "fish_reference_model_missing",
                f"Fish reference model for {speaker!r} is unavailable.",
            )
        with self._lock:
            self._reference_ids[fingerprint] = model_id
        return model_id, fingerprint, reused

    def _assessment(
        self,
        *,
        candidate_path: Path,
        reference_wav: Path,
        reference_text: str,
        expected_text: str,
        style: str,
        variant: FishPromptVariant,
    ) -> CandidateAssessment:
        transcript = self.transcriber(candidate_path)
        wer = word_error_rate(expected_text, transcript)
        candidate_features = audio_features(candidate_path, expected_text)
        reference_key = _sha256_file(reference_wav)
        reference_features = self._reference_features.get(reference_key)
        if reference_features is None:
            reference_features = audio_features(
                reference_wav,
                reference_text,
            )
            self._reference_features[reference_key] = reference_features
        identity, identity_mode = self.similarity.score(reference_wav, candidate_path)
        delivery = delivery_score(style, candidate_features, reference_features)
        quality = quality_score(candidate_features)
        text_passed = wer <= self.text_wer_limit
        total = (
            identity * 0.55
            + quality * 0.25
            + delivery * 0.20
            + variant.prior
        )
        if identity_mode == "mlx_qwen" and identity < MLX_IDENTITY_FLOOR:
            total -= 1.0
        if not text_passed:
            total -= 2.0
        return CandidateAssessment(
            prompt_key=variant.key,
            prompt_prior=variant.prior,
            transcript=transcript,
            word_error_rate=wer,
            text_passed=text_passed,
            identity_score=identity,
            identity_mode=identity_mode,
            delivery_score=delivery,
            quality_score=quality,
            total_score=total,
            features=candidate_features,
        )

    def generate(
        self,
        *,
        text: str,
        instruction: str,
        speaker: str,
        reference_audio: str | Path,
        reference_text: str,
        output_path: str | Path,
        settings: Mapping[str, Any] | None = None,
    ) -> FishGenerationResult:
        source = Path(reference_audio).expanduser().resolve()
        if not source.is_file():
            raise FishCloudError(
                "fish_reference_audio_missing",
                f"Reference audio for {speaker!r} is missing.",
            )
        if not str(reference_text or "").strip():
            raise FishCloudError(
                "fish_reference_text_missing",
                f"Reference transcript for {speaker!r} is missing.",
            )
        target = Path(output_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        route = build_prompt_route(text, instruction)
        generated: list[tuple[Path, CandidateAssessment]] = []
        with tempfile.TemporaryDirectory(
            prefix=".fish-candidates-",
            dir=target.parent,
        ) as temporary:
            temporary_root = Path(temporary)
            reference_wav = _prepare_reference_wav(
                source,
                temporary_root / "reference.wav",
            )
            reference_id, fingerprint, reused = self._ensure_reference_model(
                reference_wav=reference_wav,
                reference_text=str(reference_text).strip(),
                speaker=speaker,
            )
            request_settings = dict(settings or {})
            if route.difficult:
                total_budget = self.difficult_candidate_count
                repeats_per_stage = min(self.candidate_count, total_budget)
            else:
                fallback_allowed = route.style in {
                    "grief",
                    "sarcasm",
                    "expressive",
                }
                total_budget = self.candidate_count * (2 if fallback_allowed else 1)
                repeats_per_stage = self.candidate_count

            selected_stage: list[tuple[Path, CandidateAssessment]] | None = None
            attempted = 0
            for variant in route.variants:
                if attempted >= total_budget:
                    break
                stage_count = min(repeats_per_stage, total_budget - attempted)
                stage: list[tuple[Path, CandidateAssessment]] = []
                for _ in range(stage_count):
                    attempted += 1
                    candidate_path = temporary_root / f"candidate-{attempted:02d}.wav"
                    try:
                        audio = self.client.synthesize(
                            text=variant.text,
                            reference_id=reference_id,
                            settings=request_settings,
                        )
                        candidate_path.write_bytes(audio)
                        assessment = self._assessment(
                            candidate_path=candidate_path,
                            reference_wav=reference_wav,
                            reference_text=str(reference_text).strip(),
                            expected_text=text,
                            style=route.style,
                            variant=variant,
                        )
                    except Exception:
                        continue
                    generated.append((candidate_path, assessment))
                    if candidate_is_eligible(assessment):
                        stage.append((candidate_path, assessment))

                stage_assessments = [item[1] for item in stage]
                if stage and prompt_stage_has_delivery(
                    route.style,
                    stage_assessments,
                ):
                    selected_stage = stage
                    break

            if selected_stage is None:
                eligible = [
                    item for item in generated if candidate_is_eligible(item[1])
                ]
                mismatch_count = sum(
                    not assessment.text_passed for _, assessment in generated
                )
                identity_failure_count = sum(
                    assessment.identity_mode == "mlx_qwen"
                    and assessment.identity_score < MLX_IDENTITY_FLOOR
                    for _, assessment in generated
                )
                if not eligible:
                    raise FishCloudError(
                        "fish_no_valid_candidate",
                        "Fish Audio could not produce a candidate that passed "
                        "authored-text, identity, and audio-integrity validation "
                        f"({mismatch_count} text failures; "
                        f"{identity_failure_count} identity failures).",
                    )
                raise FishCloudError(
                    "fish_delivery_not_achieved",
                    "Fish Audio produced valid speech, but none of the automatic "
                    f"prompt stages established the requested {route.style} delivery. "
                    "Alexandria did not install a weak take; generate the line again.",
                )

            stage_assessments = [item[1] for item in selected_stage]
            selected_path, raw_selected = max(
                selected_stage,
                key=lambda item: repeat_selection_score(
                    route.style,
                    item[1],
                    stage_assessments,
                ),
            )
            selected = replace(
                raw_selected,
                total_score=repeat_selection_score(
                    route.style,
                    raw_selected,
                    stage_assessments,
                ),
            )
            shutil.copyfile(selected_path, target)
            return FishGenerationResult(
                output_path=str(target),
                selected=selected,
                candidates=tuple(assessment for _, assessment in generated),
                style=route.style,
                reference_fingerprint=fingerprint,
                reference_model_reused=reused,
            )
