from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import queue
import re
import struct
import subprocess
import tempfile
import threading
import time
from typing import Any, Mapping
import unicodedata

import numpy as np
import requests
import soundfile as sf

from audio_artifacts import install_generated_audio
from audio_processing import AudioProcessingError, prepare_generated_speech_audio
from model_registry import (
    INSTRUCTION_CONTROLLED_ENGINE_ID,
    LEGACY_CONTROLLED_CLONE_ENGINE_ID,
    engine_record_payload,
    resolve_model_path,
)
from recurring_voice_routing import (
    FISH_ROUTE_BACKEND_ID,
    INDEXTTS2_ROUTE_BACKEND_ID,
    VOXCPM2_ROUTE_BACKEND_ID,
)
from responsive_voice_models import (
    INDEXTTS2_MODEL_REVISION,
    WHISPER_VERIFIER_MODEL_KEY,
)


FISH_API_BASE = "https://api.fish.audio"
FISH_KEYCHAIN_SERVICE = "com.alexandria.fish-audio"
INDEX_CACHE_ENV = "ALEXANDRIA_INDEXTTS2_ROOT"
INDEX_CACHE_RELATIVE = Path("cache/alexandria-evaluation/indextts2")
VOXCPM2_MODEL_KEY = engine_record_payload(LEGACY_CONTROLLED_CLONE_ENGINE_ID)[
    "component_ids"
][0]


class ResponsiveVoiceBackendError(RuntimeError):
    pass


class ResponsiveBackendUnavailable(ResponsiveVoiceBackendError):
    pass


def _msgpack_encode(value: Any) -> bytes:
    """Encode the bounded Fish zero-shot request types without a new dependency."""
    if value is None:
        return b"\xc0"
    if value is False:
        return b"\xc2"
    if value is True:
        return b"\xc3"
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value <= 0x7F:
            return bytes((value,))
        if -32 <= value < 0:
            return struct.pack("b", value)
        if 0 <= value <= 0xFF:
            return b"\xcc" + struct.pack(">B", value)
        if 0 <= value <= 0xFFFF:
            return b"\xcd" + struct.pack(">H", value)
        if 0 <= value <= 0xFFFFFFFF:
            return b"\xce" + struct.pack(">I", value)
        if value >= 0:
            return b"\xcf" + struct.pack(">Q", value)
        if -0x80 <= value:
            return b"\xd0" + struct.pack(">b", value)
        if -0x8000 <= value:
            return b"\xd1" + struct.pack(">h", value)
        if -0x80000000 <= value:
            return b"\xd2" + struct.pack(">i", value)
        return b"\xd3" + struct.pack(">q", value)
    if isinstance(value, float):
        return b"\xcb" + struct.pack(">d", value)
    if isinstance(value, bytes):
        length = len(value)
        if length <= 0xFF:
            return b"\xc4" + struct.pack(">B", length) + value
        if length <= 0xFFFF:
            return b"\xc5" + struct.pack(">H", length) + value
        return b"\xc6" + struct.pack(">I", length) + value
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        length = len(encoded)
        if length <= 31:
            return bytes((0xA0 | length,)) + encoded
        if length <= 0xFF:
            return b"\xd9" + struct.pack(">B", length) + encoded
        if length <= 0xFFFF:
            return b"\xda" + struct.pack(">H", length) + encoded
        return b"\xdb" + struct.pack(">I", length) + encoded
    if isinstance(value, (list, tuple)):
        length = len(value)
        if length <= 15:
            prefix = bytes((0x90 | length,))
        elif length <= 0xFFFF:
            prefix = b"\xdc" + struct.pack(">H", length)
        else:
            prefix = b"\xdd" + struct.pack(">I", length)
        return prefix + b"".join(_msgpack_encode(item) for item in value)
    if isinstance(value, Mapping):
        items = list(value.items())
        length = len(items)
        if length <= 15:
            prefix = bytes((0x80 | length,))
        elif length <= 0xFFFF:
            prefix = b"\xde" + struct.pack(">H", length)
        else:
            prefix = b"\xdf" + struct.pack(">I", length)
        return prefix + b"".join(
            _msgpack_encode(str(key)) + _msgpack_encode(item)
            for key, item in items
        )
    raise TypeError(f"Unsupported MessagePack value: {type(value).__name__}")


def _fish_key() -> str:
    for name in ("FISH_API_KEY", "FISH_AUDIO_API_KEY"):
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    result = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-s",
            FISH_KEYCHAIN_SERVICE,
            "-a",
            getpass.getuser(),
            "-w",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        raise ResponsiveBackendUnavailable(
            "Fish Audio API key is unavailable in the environment or macOS Keychain."
        )
    return value


def _pinokio_root() -> Path:
    configured = str(os.environ.get(INDEX_CACHE_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    user_cache = (
        Path.home()
        / "pinokio"
        / "cache"
        / "alexandria-evaluation"
        / "indextts2"
    ).resolve()
    if user_cache.exists():
        return user_cache
    module = Path(__file__).resolve()
    try:
        pinokio_root = module.parents[3]
    except IndexError as exc:
        raise ResponsiveBackendUnavailable(
            "Alexandria could not resolve the Pinokio cache root."
        ) from exc
    return (pinokio_root / INDEX_CACHE_RELATIVE).resolve()


def _collect_mlx_audio(model: Any, results: Any) -> tuple[np.ndarray, int]:
    import mlx.core as mx

    arrays: list[np.ndarray] = []
    for result in results:
        mx.eval(result.audio)
        arrays.append(np.asarray(result.audio, dtype=np.float32).reshape(-1))
    if not arrays:
        raise ResponsiveVoiceBackendError("The MLX backend returned no audio.")
    audio = arrays[0] if len(arrays) == 1 else np.concatenate(arrays)
    return audio, int(getattr(model, "sample_rate", 48000))


def _safe_spoken_text(text: str) -> str:
    return str(text or "").strip()


def _normalized_words(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = (
        normalized.casefold()
        .replace("’", "'")
        .replace("‘", "'")
        .replace("‐", "-")
        .replace("‑", "-")
        .replace("–", "-")
        .replace("—", "-")
    )
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", normalized)


def _word_error_rate(expected: str, observed: str) -> float:
    left = _normalized_words(expected)
    right = _normalized_words(observed)
    previous = list(range(len(right) + 1))
    for index, left_word in enumerate(left, start=1):
        current = [index]
        for right_index, right_word in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_word != right_word),
                )
            )
        previous = current
    return previous[-1] / max(1, len(left))


def _verify_specialist_text(
    path: str | Path,
    text: str,
    *,
    maximum_word_error_rate: float = 0.15,
    require_first_word: bool = True,
) -> dict[str, Any]:
    try:
        import mlx_whisper
    except ImportError as exc:
        raise ResponsiveBackendUnavailable(
            "Specialist Voice text verification is unavailable."
        ) from exc
    try:
        verifier_path = resolve_model_path(
            WHISPER_VERIFIER_MODEL_KEY,
            local_files_only=True,
        )
    except Exception as exc:
        raise ResponsiveBackendUnavailable(
            "The pinned specialist Voice text verifier is not cached."
        ) from exc
    result = mlx_whisper.transcribe(
        str(Path(path).expanduser().resolve()),
        path_or_hf_repo=str(verifier_path),
        language="en",
        condition_on_previous_text=False,
        word_timestamps=False,
        verbose=False,
    )
    transcript = str(result.get("text") or "").strip()
    expected_words = _normalized_words(text)
    observed_words = _normalized_words(transcript)
    first_word_present = bool(
        expected_words
        and observed_words
        and expected_words[0] == observed_words[0]
    )
    wer = _word_error_rate(text, transcript)
    if (require_first_word and not first_word_present) or wer > maximum_word_error_rate:
        raise ResponsiveVoiceBackendError(
            "Specialist Voice failed text verification "
            f"(first_word_present={first_word_present}, WER={wer:.3f}, "
            f"maximum={maximum_word_error_rate:.3f})."
        )
    return {
        "automatic_transcript": transcript,
        "word_error_rate": wer,
        "first_word_present": first_word_present,
    }


def _verify_production_encoded_text(
    path: str | Path,
    text: str,
    *,
    maximum_word_error_rate: float = 0.15,
    require_first_word: bool = True,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="alexandria-specialist-production-check-"
    ) as directory:
        root = Path(directory)
        artifact = install_generated_audio(
            root_dir=root,
            voicelines_dir=root / "voicelines",
            source_audio_path=path,
            filename_base="candidate",
            binding_fingerprint="0" * 64,
            prefer_mp3=True,
            text=text,
        )
        canonical = root / str(artifact["audio_path"])
        verification = _verify_specialist_text(
            canonical,
            text,
            maximum_word_error_rate=maximum_word_error_rate,
            require_first_word=require_first_word,
        )
        return {
            **verification,
            "audio_format": artifact["audio_format"],
            "audio_sha256": artifact["audio_sha256"],
        }


def _finalize_specialist_audio(path: str | Path, text: str) -> None:
    destination = Path(path).expanduser().resolve()
    try:
        audio, sample_rate = sf.read(
            str(destination),
            dtype="float32",
            always_2d=True,
        )
        mono = np.mean(audio, axis=1, dtype=np.float32)
        prepared = prepare_generated_speech_audio(mono, int(sample_rate), text)
        peak = float(np.max(np.abs(prepared)))
        target_peak = 10.0 ** (-1.0 / 20.0)
        if peak > target_peak:
            prepared = prepared * (target_peak / peak)
        sf.write(
            str(destination),
            prepared,
            int(sample_rate),
            subtype="PCM_16",
        )
    except (OSError, RuntimeError, ValueError, AudioProcessingError) as exc:
        raise ResponsiveVoiceBackendError(
            f"Specialist Voice returned invalid audio: {exc}"
        ) from exc


def _normalized_reference(source: str | Path, sample_rate: int, destination: Path) -> Path:
    from scipy.signal import resample_poly

    audio, source_rate = sf.read(str(source), dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    if int(source_rate) != int(sample_rate):
        gcd = int(np.gcd(int(source_rate), int(sample_rate)))
        mono = resample_poly(
            mono,
            int(sample_rate) // gcd,
            int(source_rate) // gcd,
        ).astype(np.float32)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(destination), mono, int(sample_rate), subtype="PCM_16")
    return destination


class FishAudioBackend:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._api_key: str | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        try:
            self._key()
            return True
        except ResponsiveBackendUnavailable:
            return False

    def _key(self) -> str:
        if self._api_key is None:
            self._api_key = _fish_key()
        return self._api_key

    @staticmethod
    def _concise_tag(value: str) -> str:
        tag = re.sub(r"^speak\s+with\s+", "", value.strip(), flags=re.IGNORECASE)
        tag = tag.split(":", 1)[0].strip(" .")
        return tag or "natural emotional delivery"

    def _request(
        self,
        *,
        text: str,
        control: Mapping[str, Any],
        output_path: Path,
        temperature: float,
        top_p: float,
        tag: str,
        condition_on_previous_chunks: bool,
    ) -> None:
        prompt_mode = str(control["prompt_mode"])
        spoken_text = _safe_spoken_text(text)
        prompt = (
            spoken_text
            if prompt_mode == "untagged"
            else f"[{tag}] {spoken_text}"
        )
        key = self._key()
        response = self._session.post(
            FISH_API_BASE + "/v1/tts",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "model": str(control["api_model_header"]),
            },
            json={
                "text": prompt,
                "reference_id": str(control["reference_id"]),
                "temperature": temperature,
                "top_p": top_p,
                "prosody": {"speed": 1.0, "volume": 0, "normalize_loudness": True},
                "normalize": True,
                "format": "wav",
                "sample_rate": 44100,
                "latency": "normal",
                "repetition_penalty": float(control["repetition_penalty"]),
                "condition_on_previous_chunks": condition_on_previous_chunks,
                "chunk_length": 200,
                "max_new_tokens": 1024,
                "min_chunk_length": 50,
                "early_stop_threshold": 1,
            },
            timeout=300,
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get("message") or response.json().get("detail")
            except Exception:
                detail = response.text[:300]
            raise ResponsiveVoiceBackendError(
                f"Fish Audio HTTP {response.status_code}: {str(detail).replace(key, '[redacted]')}"
            )
        if len(response.content) < 512:
            raise ResponsiveVoiceBackendError(
                f"Fish Audio returned only {len(response.content)} bytes."
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
        try:
            info = sf.info(str(output_path))
        except Exception as exc:
            output_path.unlink(missing_ok=True)
            raise ResponsiveVoiceBackendError(
                "Fish Audio returned an unreadable WAV."
            ) from exc
        if info.frames <= 0:
            output_path.unlink(missing_ok=True)
            raise ResponsiveVoiceBackendError("Fish Audio returned an empty WAV.")

    def _request_zero_shot(
        self,
        *,
        text: str,
        reference_audio: Path,
        reference_text: str,
        output_path: Path,
        api_model_header: str,
        prompt_mode: str,
        temperature: float,
        top_p: float,
        repetition_penalty: float,
        tag: str,
        condition_on_previous_chunks: bool,
    ) -> None:
        spoken_text = _safe_spoken_text(text)
        prompt = spoken_text if prompt_mode == "untagged" else f"[{tag}] {spoken_text}"
        if not reference_audio.is_file():
            raise ResponsiveVoiceBackendError(
                f"Fish zero-shot reference is missing: {reference_audio}"
            )
        exact_reference_text = _safe_spoken_text(reference_text)
        if not exact_reference_text:
            raise ResponsiveVoiceBackendError(
                "Fish zero-shot reference requires an exact transcript."
            )
        key = self._key()
        payload = {
            "text": prompt,
            "references": [
                {
                    "audio": reference_audio.read_bytes(),
                    "text": exact_reference_text,
                }
            ],
            "reference_id": None,
            "temperature": temperature,
            "top_p": top_p,
            "prosody": {
                "speed": 1.0,
                "volume": 0,
                "normalize_loudness": True,
            },
            "normalize": True,
            "format": "wav",
            "sample_rate": 44100,
            "latency": "normal",
            "repetition_penalty": repetition_penalty,
            "condition_on_previous_chunks": condition_on_previous_chunks,
            "chunk_length": 200,
            "max_new_tokens": 1024,
            "min_chunk_length": 50,
            "early_stop_threshold": 1,
        }
        response = self._session.post(
            FISH_API_BASE + "/v1/tts",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/msgpack",
                "model": api_model_header,
            },
            data=_msgpack_encode(payload),
            timeout=300,
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get("message") or response.json().get("detail")
            except Exception:
                detail = response.text[:300]
            raise ResponsiveVoiceBackendError(
                f"Fish Audio HTTP {response.status_code}: {str(detail).replace(key, '[redacted]')}"
            )
        if len(response.content) < 512:
            raise ResponsiveVoiceBackendError(
                f"Fish Audio returned only {len(response.content)} bytes."
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
        try:
            info = sf.info(str(output_path))
        except Exception as exc:
            output_path.unlink(missing_ok=True)
            raise ResponsiveVoiceBackendError(
                "Fish Audio returned an unreadable WAV."
            ) from exc
        if info.frames <= 0:
            output_path.unlink(missing_ok=True)
            raise ResponsiveVoiceBackendError("Fish Audio returned an empty WAV.")

    def generate(
        self,
        *,
        text: str,
        control: Mapping[str, Any],
        output_path: str | Path,
    ) -> dict[str, Any]:
        with self._lock:
            return self._generate_locked(
                text=text,
                control=control,
                output_path=output_path,
            )

    def generate_zero_shot(
        self,
        *,
        text: str,
        reference_audio: str | Path,
        reference_text: str,
        control: Mapping[str, Any],
        output_path: str | Path,
    ) -> dict[str, Any]:
        """Generate with a private inline Fish reference; no model is created."""
        with self._lock:
            destination = Path(output_path).expanduser().resolve()
            source = Path(reference_audio).expanduser().resolve()
            original_tag = str(control.get("tag") or "").strip()
            prompt_mode = str(control.get("prompt_mode") or "full_alexandria_tag")
            api_model_header = str(
                control.get("api_model_header") or "s2.1-pro-free"
            )
            base_temperature = float(control.get("temperature", 0.7))
            base_top_p = float(control.get("top_p", 0.7))
            repetition_penalty = float(control.get("repetition_penalty", 1.2))
            maximum_word_error_rate = float(
                control.get("verification_maximum_word_error_rate", 0.15)
            )
            require_first_word = bool(
                control.get("verification_require_first_word", True)
            )
            attempts = (
                {
                    "strategy": "primary",
                    "temperature": base_temperature,
                    "top_p": base_top_p,
                    "tag": original_tag,
                    "condition_on_previous_chunks": True,
                },
                {
                    "strategy": "lower_variance_retry",
                    "temperature": min(base_temperature, 0.35),
                    "top_p": min(base_top_p, 0.55),
                    "tag": original_tag,
                    "condition_on_previous_chunks": False,
                },
                {
                    "strategy": "concise_tag_retry",
                    "temperature": min(base_temperature, 0.35),
                    "top_p": min(base_top_p, 0.55),
                    "tag": self._concise_tag(original_tag),
                    "condition_on_previous_chunks": False,
                },
            )
            failures: list[str] = []
            for attempt_index, attempt in enumerate(attempts, start=1):
                candidate = destination.with_name(
                    f".{destination.stem}.fish-zero-shot-{attempt_index}{destination.suffix}"
                )
                candidate.unlink(missing_ok=True)
                try:
                    self._request_zero_shot(
                        text=text,
                        reference_audio=source,
                        reference_text=reference_text,
                        output_path=candidate,
                        api_model_header=api_model_header,
                        prompt_mode=prompt_mode,
                        temperature=float(attempt["temperature"]),
                        top_p=float(attempt["top_p"]),
                        repetition_penalty=repetition_penalty,
                        tag=str(attempt["tag"]),
                        condition_on_previous_chunks=bool(
                            attempt["condition_on_previous_chunks"]
                        ),
                    )
                    _finalize_specialist_audio(candidate, text)
                    source_verification = _verify_specialist_text(
                        candidate,
                        text,
                        maximum_word_error_rate=maximum_word_error_rate,
                        require_first_word=require_first_word,
                    )
                    production_verification = _verify_production_encoded_text(
                        candidate,
                        text,
                        maximum_word_error_rate=maximum_word_error_rate,
                        require_first_word=require_first_word,
                    )
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(candidate, destination)
                    return {
                        "attempt_count": attempt_index,
                        "repair_strategy": attempt["strategy"],
                        "source_text_verification": source_verification,
                        "text_verification": production_verification,
                        "reference_mode": "inline_zero_shot",
                    }
                except (ResponsiveBackendUnavailable, ResponsiveVoiceBackendError) as exc:
                    failures.append(f"{attempt['strategy']}: {exc}")
                    candidate.unlink(missing_ok=True)
            raise ResponsiveVoiceBackendError(
                "Fish Audio zero-shot failed verified same-model recovery: "
                + " | ".join(failures)
            )

    def _generate_locked(
        self,
        *,
        text: str,
        control: Mapping[str, Any],
        output_path: str | Path,
    ) -> dict[str, Any]:
        destination = Path(output_path).expanduser().resolve()
        original_tag = str(control.get("tag") or "").strip()
        base_temperature = float(control["temperature"])
        base_top_p = float(control["top_p"])
        attempts = (
            {
                "strategy": "primary",
                "temperature": base_temperature,
                "top_p": base_top_p,
                "tag": original_tag,
                "condition_on_previous_chunks": True,
            },
            {
                "strategy": "lower_variance_retry",
                "temperature": min(base_temperature, 0.35),
                "top_p": min(base_top_p, 0.55),
                "tag": original_tag,
                "condition_on_previous_chunks": False,
            },
            {
                "strategy": "concise_tag_retry",
                "temperature": min(base_temperature, 0.35),
                "top_p": min(base_top_p, 0.55),
                "tag": self._concise_tag(original_tag),
                "condition_on_previous_chunks": False,
            },
        )
        failures: list[str] = []
        for attempt_index, attempt in enumerate(attempts, start=1):
            candidate = destination.with_name(
                f".{destination.stem}.fish-attempt-{attempt_index}{destination.suffix}"
            )
            candidate.unlink(missing_ok=True)
            try:
                self._request(
                    text=text,
                    control=control,
                    output_path=candidate,
                    temperature=float(attempt["temperature"]),
                    top_p=float(attempt["top_p"]),
                    tag=str(attempt["tag"]),
                    condition_on_previous_chunks=bool(
                        attempt["condition_on_previous_chunks"]
                    ),
                )
                _finalize_specialist_audio(candidate, text)
                source_verification = _verify_specialist_text(candidate, text)
                production_verification = _verify_production_encoded_text(
                    candidate,
                    text,
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(candidate, destination)
                return {
                    "attempt_count": attempt_index,
                    "repair_strategy": attempt["strategy"],
                    "source_text_verification": source_verification,
                    "text_verification": production_verification,
                }
            except (ResponsiveBackendUnavailable, ResponsiveVoiceBackendError) as exc:
                failures.append(f"{attempt['strategy']}: {exc}")
                candidate.unlink(missing_ok=True)
        raise ResponsiveVoiceBackendError(
            "Fish Audio failed verified same-model recovery: " + " | ".join(failures)
        )


class IndexTTS2SidecarClient:
    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._cache_root: Path | None = None

    def _resolved_cache(self) -> Path:
        root = _pinokio_root()
        required = (
            root / "env/bin/python",
            root / "source/indextts/infer_v2.py",
            root / "aux-flat/semantic_codec/model.safetensors",
            root / "aux-flat/campplus_cn_common.bin",
            root / "aux-flat/bigvgan",
        )
        if not all(path.exists() for path in required):
            raise ResponsiveBackendUnavailable(
                f"The pinned IndexTTS2 runtime is incomplete: {root}"
            )
        snapshot = (
            root
            / "huggingface/models--IndexTeam--IndexTTS-2/snapshots"
            / INDEXTTS2_MODEL_REVISION
        )
        if not (snapshot / "config.yaml").is_file():
            raise ResponsiveBackendUnavailable(
                f"The pinned IndexTTS2 model snapshot is unavailable: {snapshot}"
            )
        return root

    def available(self) -> bool:
        try:
            self._resolved_cache()
            return True
        except ResponsiveBackendUnavailable:
            return False

    def _reader_loop(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                self._responses.put(payload)

    def _response(self, timeout: float) -> dict[str, Any]:
        try:
            return self._responses.get(timeout=timeout)
        except queue.Empty as exc:
            self.close()
            raise ResponsiveVoiceBackendError(
                "Timed out waiting for the IndexTTS2 sidecar."
            ) from exc

    def _ensure_started(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        root = self._resolved_cache()
        script = Path(__file__).with_name("indextts2_sidecar.py")
        if not script.is_file():
            raise ResponsiveBackendUnavailable(
                "Alexandria's IndexTTS2 sidecar script is missing."
            )
        env = os.environ.copy()
        env.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "TOKENIZERS_PARALLELISM": "false",
                "PYTORCH_MPS_FAST_MATH": "1",
                "PYTORCH_MPS_PREFER_METAL": "1",
            }
        )
        self._responses = queue.Queue()
        self._process = subprocess.Popen(
            [
                str(root / "env/bin/python"),
                "-u",
                str(script),
                "--cache-root",
                str(root),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
            env=env,
        )
        self._cache_root = root
        self._reader = threading.Thread(
            target=self._reader_loop,
            args=(self._process,),
            daemon=True,
            name="alexandria-indextts2-reader",
        )
        self._reader.start()
        ready = self._response(360.0)
        if (
            ready.get("status") != "ready"
            or ready.get("model_revision") != INDEXTTS2_MODEL_REVISION
        ):
            self.close()
            raise ResponsiveVoiceBackendError(
                f"IndexTTS2 sidecar failed to start with the pinned revision: "
                f"{ready.get('error') or ready}"
            )

    def generate(
        self,
        *,
        text: str,
        identity_audio: str,
        performance_audio: str,
        control: Mapping[str, Any],
        output_path: str | Path,
        seed: int,
    ) -> None:
        with self._lock:
            self._ensure_started()
            assert self._process is not None and self._process.stdin is not None
            request_id = f"index-{time.time_ns()}"
            payload = {
                "request_id": request_id,
                "text": text,
                "identity_audio": identity_audio,
                "performance_audio": performance_audio,
                "emotion_strength": float(control["emotion_strength"]),
                "diffusion_steps": int(control["diffusion_steps"]),
                "num_beams": int(control["num_beams"]),
                "greedy": bool(control["greedy"]),
                "max_mel_tokens": int(control["max_mel_tokens"]),
                "seed": int(seed),
                "output_path": str(Path(output_path).expanduser().resolve()),
            }
            self._process.stdin.write(json.dumps(payload) + "\n")
            self._process.stdin.flush()
            response = self._response(1800.0)
            if response.get("request_id") != request_id:
                self.close()
                raise ResponsiveVoiceBackendError(
                    "IndexTTS2 sidecar returned an unrelated response."
                )
            if response.get("status") != "ok":
                raise ResponsiveVoiceBackendError(
                    f"IndexTTS2 generation failed: {response.get('error') or response}"
                )
            destination = Path(output_path)
            if not destination.is_file() or destination.stat().st_size < 512:
                raise ResponsiveVoiceBackendError(
                    "IndexTTS2 did not create a valid output WAV."
                )

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin is not None and process.poll() is None:
                process.stdin.write(json.dumps({"command": "shutdown"}) + "\n")
                process.stdin.flush()
        except Exception:
            pass
        try:
            process.wait(timeout=10)
        except Exception:
            process.terminate()
            try:
                process.wait(timeout=5)
            except Exception:
                process.kill()


class VoxCPM2Backend:
    def __init__(self) -> None:
        self._model: Any | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        try:
            resolve_model_path(VOXCPM2_MODEL_KEY, local_files_only=True)
            return True
        except Exception:
            return False

    def _loaded_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            model_path = resolve_model_path(
                VOXCPM2_MODEL_KEY,
                local_files_only=True,
            )
        except Exception as exc:
            raise ResponsiveBackendUnavailable(
                "The pinned VoxCPM2 MLX model is not cached."
            ) from exc
        from mlx_audio.tts.utils import load_model

        self._model = load_model(str(model_path))
        return self._model

    def generate(
        self,
        *,
        text: str,
        identity_audio: str,
        identity_text: str,
        control: Mapping[str, Any],
        output_path: str | Path,
        seed: int,
    ) -> None:
        with self._lock:
            model = self._loaded_model()
            import mlx.core as mx
            mx.random.seed(int(seed))
            destination = Path(output_path).expanduser().resolve()
            normalized = destination.with_name(f".{destination.stem}.voxcpm-reference.wav")
            try:
                encode_rate = int(getattr(model, "_encode_sample_rate", 16000))
                _normalized_reference(identity_audio, encode_rate, normalized)
                results = model.generate(
                    text=text,
                    ref_audio=str(normalized),
                    ref_text=identity_text,
                    instruct=str(control["instruction"]),
                    cfg_value=float(control["cfg_value"]),
                    inference_timesteps=int(control["inference_timesteps"]),
                    warmup_patches=int(control["warmup_patches"]),
                    max_tokens=int(control["max_tokens"]),
                )
                audio, sample_rate = _collect_mlx_audio(model, results)
                destination.parent.mkdir(parents=True, exist_ok=True)
                sf.write(str(destination), audio, sample_rate, subtype="PCM_16")
            finally:
                normalized.unlink(missing_ok=True)


class ResponsiveVoiceBackend:
    def __init__(self) -> None:
        self.fish = FishAudioBackend()
        self.index = IndexTTS2SidecarClient()
        self.vox = VoxCPM2Backend()

    def backend_available(self, backend: str) -> bool:
        if backend == FISH_ROUTE_BACKEND_ID:
            return self.fish.available()
        if backend == INDEXTTS2_ROUTE_BACKEND_ID:
            return self.index.available()
        if backend == VOXCPM2_ROUTE_BACKEND_ID:
            return self.vox.available()
        if backend == INSTRUCTION_CONTROLLED_ENGINE_ID:
            return True
        return False

    def generate(
        self,
        *,
        route: Mapping[str, Any],
        text: str,
        output_path: str | Path,
        seed: int,
    ) -> dict[str, Any]:
        backend = str(route["backend"])
        verification = route.get("verification")
        if not isinstance(verification, Mapping):
            verification = {}
        maximum_word_error_rate = float(
            verification.get("maximum_word_error_rate", 0.15)
        )
        require_first_word = bool(
            verification.get("require_first_word", True)
        )
        if backend == FISH_ROUTE_BACKEND_ID:
            if route["control"].get("reference_mode") == "inline_zero_shot":
                return self.fish.generate_zero_shot(
                    text=text,
                    reference_audio=str(route["identity_audio_path"]),
                    reference_text=str(route["identity_text"]),
                    control=route["control"],
                    output_path=output_path,
                )
            return self.fish.generate(
                text=text,
                control=route["control"],
                output_path=output_path,
            )
        if backend == INDEXTTS2_ROUTE_BACKEND_ID:
            performance = str(route.get("performance_audio_path") or "")
            if not performance:
                raise ResponsiveVoiceBackendError(
                    "IndexTTS2 route has no performance reference."
                )
            self.index.generate(
                text=text,
                identity_audio=str(route["identity_audio_path"]),
                performance_audio=performance,
                control=route["control"],
                output_path=output_path,
                seed=seed,
            )
            _finalize_specialist_audio(output_path, text)
            source_verification = _verify_specialist_text(
                output_path,
                text,
                maximum_word_error_rate=maximum_word_error_rate,
                require_first_word=require_first_word,
            )
            return {
                "attempt_count": 1,
                "repair_strategy": "direct",
                "source_text_verification": source_verification,
                "text_verification": _verify_production_encoded_text(
                    output_path,
                    text,
                    maximum_word_error_rate=maximum_word_error_rate,
                    require_first_word=require_first_word,
                ),
            }
        if backend == VOXCPM2_ROUTE_BACKEND_ID:
            self.vox.generate(
                text=text,
                identity_audio=str(route["identity_audio_path"]),
                identity_text=str(route["identity_text"]),
                control=route["control"],
                output_path=output_path,
                seed=seed,
            )
            _finalize_specialist_audio(output_path, text)
            source_verification = _verify_specialist_text(
                output_path,
                text,
                maximum_word_error_rate=maximum_word_error_rate,
                require_first_word=require_first_word,
            )
            return {
                "attempt_count": 1,
                "repair_strategy": "zero_warmup_direct",
                "source_text_verification": source_verification,
                "text_verification": _verify_production_encoded_text(
                    output_path,
                    text,
                    maximum_word_error_rate=maximum_word_error_rate,
                    require_first_word=require_first_word,
                ),
            }
        raise ResponsiveVoiceBackendError(
            f"Responsive backend {backend!r} is not a specialist runtime."
        )

    def close(self) -> None:
        self.index.close()
