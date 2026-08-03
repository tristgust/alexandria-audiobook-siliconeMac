from __future__ import annotations

import copy
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
from pydub import AudioSegment, effects

from audio_artifacts import sha256_file, validate_audio_file
from generation_state import fingerprint_value


MASTERING_SCHEMA_VERSION = 1
MASTERING_OPERATION = "publication_mastering"
MIN_AUDIO_DURATION_MS = 250
MAX_AUDIO_DURATION_DRIFT_MS = 12
MAX_ABSOLUTE_GAIN_DB = 12.0
MIN_FILTER_GAP_HZ = 500
_SAFE_PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")


class AudioMasteringError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.context = copy.deepcopy(dict(context or {}))


class AudioMasteringCancelled(AudioMasteringError):
    def __init__(self) -> None:
        super().__init__(
            "audio_mastering_cancelled",
            "Publication mastering was cancelled before publication.",
        )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(
    value: Any,
    *,
    field: str,
    minimum: float,
    maximum: float,
    default: float | None = None,
) -> float | None:
    if value is None and default is not None:
        return float(default)
    if value is None:
        return None
    if isinstance(value, bool):
        raise AudioMasteringError(
            "audio_mastering_settings_invalid",
            f"{field} must be between {minimum:g} and {maximum:g}.",
            context={"field": field, "value": value},
        )
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AudioMasteringError(
            "audio_mastering_settings_invalid",
            f"{field} must be between {minimum:g} and {maximum:g}.",
            context={"field": field, "value": value},
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise AudioMasteringError(
            "audio_mastering_settings_invalid",
            f"{field} must be between {minimum:g} and {maximum:g}.",
            context={"field": field, "value": result},
        )
    return round(result, 4)


def _integer(
    value: Any,
    *,
    field: str,
    minimum: int,
    maximum: int,
    default: int | None = None,
) -> int | None:
    result = _number(
        value,
        field=field,
        minimum=minimum,
        maximum=maximum,
        default=default,
    )
    if result is None:
        return None
    if result != int(result):
        raise AudioMasteringError(
            "audio_mastering_settings_invalid",
            f"{field} must be a whole number.",
            context={"field": field, "value": result},
        )
    return int(result)


def _boolean(value: Any, *, field: str, default: bool) -> bool:
    if value is None:
        return default
    if type(value) is not bool:
        raise AudioMasteringError(
            "audio_mastering_settings_invalid",
            f"{field} must be boolean.",
            context={"field": field, "value": value},
        )
    return value


def normalize_provenance_evidence(value: Any = None) -> dict[str, Any]:
    if value is not None and not isinstance(value, Mapping):
        raise AudioMasteringError(
            "audio_mastering_provenance_invalid",
            "Mastering provenance must be an object.",
        )
    source = _mapping(value)
    unknown = set(source) - {
        "schema_version",
        "c2pa",
        "watermark",
        "voice_authorization",
        "human_approval",
    }
    if unknown:
        raise AudioMasteringError(
            "audio_mastering_provenance_invalid",
            "Mastering provenance contains unsupported fields.",
            context={"fields": sorted(str(item) for item in unknown)},
        )
    if type(source.get("schema_version", MASTERING_SCHEMA_VERSION)) is not int or source.get(
        "schema_version", MASTERING_SCHEMA_VERSION
    ) != MASTERING_SCHEMA_VERSION:
        raise AudioMasteringError(
            "audio_mastering_provenance_invalid",
            "Mastering provenance schema is unsupported.",
        )
    voice_authorization = str(
        source.get("voice_authorization") or "not_evaluated"
    )
    if voice_authorization != "not_evaluated":
        raise AudioMasteringError(
            "audio_mastering_trust_claim_forbidden",
            "Mastering provenance cannot establish Voice authorization.",
        )
    human_approval = str(
        source.get("human_approval") or "pending_final_listen"
    )
    if human_approval != "pending_final_listen":
        raise AudioMasteringError(
            "audio_mastering_trust_claim_forbidden",
            "Mastering provenance cannot establish human approval.",
        )
    if source.get("c2pa") is not None and not isinstance(
        source.get("c2pa"), Mapping
    ):
        raise AudioMasteringError(
            "audio_mastering_provenance_invalid",
            "C2PA evidence must be an object.",
        )
    if source.get("watermark") is not None and not isinstance(
        source.get("watermark"), Mapping
    ):
        raise AudioMasteringError(
            "audio_mastering_provenance_invalid",
            "Watermark evidence must be an object.",
        )
    c2pa = _mapping(source.get("c2pa"))
    watermark = _mapping(source.get("watermark"))
    if set(c2pa) - {"present", "structural_status", "signer_trust"}:
        raise AudioMasteringError(
            "audio_mastering_provenance_invalid",
            "C2PA evidence contains unsupported fields.",
        )
    if set(watermark) - {"present", "structural_status", "ownership_trust"}:
        raise AudioMasteringError(
            "audio_mastering_provenance_invalid",
            "Watermark evidence contains unsupported fields.",
        )

    c2pa_present = _boolean(
        c2pa.get("present"),
        field="c2pa.present",
        default=False,
    )
    c2pa_status = str(
        c2pa.get("structural_status")
        or ("unverified" if c2pa_present else "not_present")
    )
    if c2pa_status not in {"not_present", "unverified", "valid", "invalid"}:
        raise AudioMasteringError(
            "audio_mastering_provenance_invalid",
            "C2PA structural status is invalid.",
        )
    if not c2pa_present and c2pa_status != "not_present":
        raise AudioMasteringError(
            "audio_mastering_provenance_invalid",
            "C2PA cannot have a structural status when no manifest is present.",
        )
    signer_trust = str(c2pa.get("signer_trust") or "not_evaluated")
    if signer_trust not in {"not_evaluated", "unverified"}:
        raise AudioMasteringError(
            "audio_mastering_trust_claim_forbidden",
            "Structural C2PA validity does not establish trusted signer identity.",
        )

    watermark_present = _boolean(
        watermark.get("present"),
        field="watermark.present",
        default=False,
    )
    watermark_status = str(
        watermark.get("structural_status")
        or ("unverified" if watermark_present else "not_present")
    )
    if watermark_status not in {
        "not_present",
        "unverified",
        "detected",
        "invalid",
    }:
        raise AudioMasteringError(
            "audio_mastering_provenance_invalid",
            "Watermark structural status is invalid.",
        )
    if not watermark_present and watermark_status != "not_present":
        raise AudioMasteringError(
            "audio_mastering_provenance_invalid",
            "Watermark evidence cannot have a status when no mark is present.",
        )
    ownership_trust = str(
        watermark.get("ownership_trust") or "not_evaluated"
    )
    if ownership_trust not in {"not_evaluated", "unverified"}:
        raise AudioMasteringError(
            "audio_mastering_trust_claim_forbidden",
            "Watermark detection does not establish ownership or Voice authorization.",
        )

    return {
        "schema_version": MASTERING_SCHEMA_VERSION,
        "c2pa": {
            "present": c2pa_present,
            "structural_status": c2pa_status,
            "signer_trust": signer_trust,
        },
        "watermark": {
            "present": watermark_present,
            "structural_status": watermark_status,
            "ownership_trust": ownership_trust,
        },
        "voice_authorization": voice_authorization,
        "human_approval": human_approval,
    }


def normalize_mastering_settings(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AudioMasteringError(
            "audio_mastering_settings_invalid",
            "Mastering settings must be an object.",
        )
    if type(value.get("schema_version", MASTERING_SCHEMA_VERSION)) is not int or value.get(
        "schema_version", MASTERING_SCHEMA_VERSION
    ) != MASTERING_SCHEMA_VERSION:
        raise AudioMasteringError(
            "audio_mastering_schema_unsupported",
            "Mastering settings schema is unsupported.",
        )
    allowed = {
        "schema_version",
        "settings_fingerprint",
        "gain_db",
        "high_pass_hz",
        "low_pass_hz",
        "compression",
        "normalization",
        "limiter_ceiling_dbfs",
        "room_correction",
    }
    unknown = set(value) - allowed
    if unknown:
        raise AudioMasteringError(
            "audio_mastering_effect_rejected",
            "Mastering settings contain unsupported or novelty effects.",
            context={"fields": sorted(str(item) for item in unknown)},
        )

    gain_db = _number(
        value.get("gain_db"),
        field="gain_db",
        minimum=-MAX_ABSOLUTE_GAIN_DB,
        maximum=MAX_ABSOLUTE_GAIN_DB,
        default=0,
    )
    high_pass_hz = _integer(
        value.get("high_pass_hz"),
        field="high_pass_hz",
        minimum=20,
        maximum=500,
    )
    low_pass_hz = _integer(
        value.get("low_pass_hz"),
        field="low_pass_hz",
        minimum=3000,
        maximum=22000,
    )

    compression_source = _mapping(value.get("compression"))
    if value.get("compression") is not None and not isinstance(
        value.get("compression"), Mapping
    ):
        raise AudioMasteringError(
            "audio_mastering_settings_invalid",
            "compression must be an object.",
        )
    if set(compression_source) - {
        "enabled",
        "threshold_dbfs",
        "ratio",
        "attack_ms",
        "release_ms",
    }:
        raise AudioMasteringError(
            "audio_mastering_effect_rejected",
            "Compression settings contain unsupported fields.",
        )
    compression = {
        "enabled": _boolean(
            compression_source.get("enabled"),
            field="compression.enabled",
            default=True,
        ),
        "threshold_dbfs": _number(
            compression_source.get("threshold_dbfs"),
            field="compression.threshold_dbfs",
            minimum=-60,
            maximum=-1,
            default=-22,
        ),
        "ratio": _number(
            compression_source.get("ratio"),
            field="compression.ratio",
            minimum=1,
            maximum=20,
            default=2,
        ),
        "attack_ms": _number(
            compression_source.get("attack_ms"),
            field="compression.attack_ms",
            minimum=1,
            maximum=200,
            default=8,
        ),
        "release_ms": _number(
            compression_source.get("release_ms"),
            field="compression.release_ms",
            minimum=10,
            maximum=2000,
            default=120,
        ),
    }

    normalization_source = _mapping(value.get("normalization"))
    if value.get("normalization") is not None and not isinstance(
        value.get("normalization"), Mapping
    ):
        raise AudioMasteringError(
            "audio_mastering_settings_invalid",
            "normalization must be an object.",
        )
    if set(normalization_source) - {
        "enabled",
        "target_loudness_dbfs",
        "maximum_gain_db",
    }:
        raise AudioMasteringError(
            "audio_mastering_effect_rejected",
            "Normalization settings contain unsupported fields.",
        )
    normalization = {
        "enabled": _boolean(
            normalization_source.get("enabled"),
            field="normalization.enabled",
            default=True,
        ),
        "target_loudness_dbfs": _number(
            normalization_source.get("target_loudness_dbfs"),
            field="normalization.target_loudness_dbfs",
            minimum=-30,
            maximum=-10,
            default=-20,
        ),
        "maximum_gain_db": _number(
            normalization_source.get("maximum_gain_db"),
            field="normalization.maximum_gain_db",
            minimum=0,
            maximum=18,
            default=8,
        ),
    }
    limiter_ceiling_dbfs = _number(
        value.get("limiter_ceiling_dbfs"),
        field="limiter_ceiling_dbfs",
        minimum=-6,
        maximum=-0.1,
        default=-1,
    )

    room_source = value.get("room_correction")
    room_correction = None
    if room_source is not None:
        if not isinstance(room_source, Mapping):
            raise AudioMasteringError(
                "audio_mastering_room_correction_invalid",
                "Room correction must be an approved bounded profile.",
            )
        if room_source.get("approved") is not True:
            raise AudioMasteringError(
                "audio_mastering_room_correction_unapproved",
                "Room correction requires explicit approval.",
            )
        if set(room_source) - {
            "approved",
            "profile_id",
            "gain_db",
            "high_pass_hz",
            "low_pass_hz",
        }:
            raise AudioMasteringError(
                "audio_mastering_effect_rejected",
                "Room correction contains unsupported effects.",
            )
        profile_id = str(room_source.get("profile_id") or "").strip()
        if not _SAFE_PROFILE_ID.fullmatch(profile_id):
            raise AudioMasteringError(
                "audio_mastering_room_correction_invalid",
                "Room correction profile ID is invalid.",
            )
        room_correction = {
            "approved": True,
            "profile_id": profile_id,
            "gain_db": _number(
                room_source.get("gain_db"),
                field="room_correction.gain_db",
                minimum=-6,
                maximum=6,
                default=0,
            ),
            "high_pass_hz": _integer(
                room_source.get("high_pass_hz"),
                field="room_correction.high_pass_hz",
                minimum=20,
                maximum=500,
            ),
            "low_pass_hz": _integer(
                room_source.get("low_pass_hz"),
                field="room_correction.low_pass_hz",
                minimum=3000,
                maximum=22000,
            ),
        }

    effective_high_pass = max(
        [item for item in (high_pass_hz, (room_correction or {}).get("high_pass_hz")) if item is not None],
        default=None,
    )
    effective_low_pass = min(
        [item for item in (low_pass_hz, (room_correction or {}).get("low_pass_hz")) if item is not None],
        default=None,
    )
    if (
        effective_high_pass is not None
        and effective_low_pass is not None
        and effective_low_pass - effective_high_pass < MIN_FILTER_GAP_HZ
    ):
        raise AudioMasteringError(
            "audio_mastering_filter_range_invalid",
            "High-pass and low-pass filters leave too little audiobook bandwidth.",
        )
    if compression["enabled"] and compression["ratio"] <= 1:
        raise AudioMasteringError(
            "audio_mastering_compression_invalid",
            "Enabled compression requires a ratio greater than 1.",
        )

    normalized = {
        "schema_version": MASTERING_SCHEMA_VERSION,
        "gain_db": gain_db,
        "high_pass_hz": high_pass_hz,
        "low_pass_hz": low_pass_hz,
        "compression": compression,
        "normalization": normalization,
        "limiter_ceiling_dbfs": limiter_ceiling_dbfs,
        "room_correction": room_correction,
    }
    normalized["settings_fingerprint"] = fingerprint_value(normalized)
    return normalized


def mastering_dependency_fingerprint(
    *,
    take_id: str,
    record_fingerprint: str,
    source_sha256: str,
    registry_fingerprint: str,
    source_order_fingerprint: str,
    settings_fingerprint: str,
) -> str:
    return fingerprint_value(
        {
            "contract": "alexandria_publication_mastering_dependency_v1",
            "take_id": str(take_id),
            "record_fingerprint": str(record_fingerprint),
            "source_sha256": str(source_sha256).casefold(),
            "registry_fingerprint": str(registry_fingerprint),
            "source_order_fingerprint": str(source_order_fingerprint),
            "settings_fingerprint": str(settings_fingerprint),
        }
    )


def build_mastering_plan(
    *,
    take: Mapping[str, Any],
    registry_fingerprint: str,
    source_order_fingerprint: str,
    settings: Mapping[str, Any],
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_mastering_settings(settings)
    provenance_value = normalize_provenance_evidence(provenance)
    artifact = _mapping(take.get("artifact"))
    take_id = str(take.get("take_id") or "")
    record_fingerprint = str(take.get("record_fingerprint") or "")
    source_sha256 = str(artifact.get("sha256") or "").casefold()
    source_sample_rate = artifact.get("sample_rate")
    if (
        not take_id
        or len(record_fingerprint) != 64
        or len(source_sha256) != 64
        or len(str(registry_fingerprint)) != 64
        or len(str(source_order_fingerprint)) != 64
    ):
        raise AudioMasteringError(
            "audio_mastering_source_identity_invalid",
            "Current Take identity is incomplete for mastering.",
        )
    if isinstance(source_sample_rate, int) and source_sample_rate > 0:
        nyquist_limit = max(3000, (source_sample_rate // 2) - 100)
        effective_low_pass = min(
            [
                item
                for item in (
                    normalized.get("low_pass_hz"),
                    _mapping(normalized.get("room_correction")).get(
                        "low_pass_hz"
                    ),
                )
                if item is not None
            ],
            default=None,
        )
        if effective_low_pass is not None and effective_low_pass > nyquist_limit:
            raise AudioMasteringError(
                "audio_mastering_filter_nyquist_invalid",
                "Low-pass frequency exceeds the source audio Nyquist limit.",
                context={
                    "sample_rate": source_sample_rate,
                    "maximum_low_pass_hz": nyquist_limit,
                },
            )
    dependency = mastering_dependency_fingerprint(
        take_id=take_id,
        record_fingerprint=record_fingerprint,
        source_sha256=source_sha256,
        registry_fingerprint=str(registry_fingerprint),
        source_order_fingerprint=str(source_order_fingerprint),
        settings_fingerprint=normalized["settings_fingerprint"],
    )
    seed = {
        "schema_version": MASTERING_SCHEMA_VERSION,
        "operation": MASTERING_OPERATION,
        "dependency_fingerprint": dependency,
        "settings_fingerprint": normalized["settings_fingerprint"],
        "provenance": provenance_value,
    }
    return {
        **seed,
        "take_id": take_id,
        "source_sha256": source_sha256,
        "record_fingerprint": record_fingerprint,
        "registry_fingerprint": str(registry_fingerprint),
        "source_order_fingerprint": str(source_order_fingerprint),
        "settings": normalized,
        "plan_fingerprint": fingerprint_value(seed),
        "safe_to_execute": True,
        "rejected_effects": [
            "pitch_shift",
            "chorus",
            "dramatic_reverb",
            "voice_transformation",
            "arbitrary_multitrack",
        ],
    }


def _audio_metrics(segment: AudioSegment) -> dict[str, Any]:
    samples = np.asarray(segment.get_array_of_samples())
    if segment.channels > 1:
        samples = samples.reshape((-1, segment.channels)).mean(axis=1)
    maximum = float(1 << (8 * segment.sample_width - 1))
    waveform = samples.astype(np.float64) / maximum
    if waveform.size == 0 or not np.all(np.isfinite(waveform)):
        raise AudioMasteringError(
            "audio_mastering_audio_invalid",
            "Mastering audio is empty or contains non-finite samples.",
        )
    peak = float(np.max(np.abs(waveform)))
    rms = float(np.sqrt(np.mean(np.square(waveform))))
    if peak <= 1e-8 or rms <= 1e-9:
        raise AudioMasteringError(
            "audio_mastering_audio_silent",
            "Mastering audio is effectively silent.",
        )
    true_peak = peak
    try:
        import soxr

        oversampled = soxr.resample(
            waveform.astype(np.float32),
            segment.frame_rate,
            segment.frame_rate * 4,
            quality="HQ",
        )
        true_peak = max(true_peak, float(np.max(np.abs(oversampled))))
    except Exception:
        pass

    def dbfs(value: float) -> float:
        return round(20.0 * math.log10(max(value, 1e-12)), 4)

    return {
        "duration_ms": len(segment),
        "sample_rate": segment.frame_rate,
        "channels": segment.channels,
        "sample_width_bytes": segment.sample_width,
        "peak_dbfs": dbfs(peak),
        "estimated_true_peak_dbfs": dbfs(true_peak),
        "estimated_loudness_dbfs": dbfs(rms),
        "dc_offset": round(float(np.mean(waveform)), 8),
        "clipped_sample_count": int(np.count_nonzero(np.abs(waveform) >= 1.0)),
    }


def create_mastered_candidate(
    *,
    source_audio_path: str | Path,
    output_path: str | Path,
    settings: Mapping[str, Any],
    provenance: Mapping[str, Any] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    normalized = normalize_mastering_settings(settings)
    provenance_value = normalize_provenance_evidence(provenance)
    source = Path(source_audio_path).expanduser().resolve()
    destination = Path(output_path).expanduser().resolve()
    if not source.is_file() or source.is_symlink():
        raise AudioMasteringError(
            "audio_mastering_source_missing",
            "The source Take audio is missing or unsafe.",
        )
    if destination == source:
        raise AudioMasteringError(
            "audio_mastering_output_unsafe",
            "Mastering output cannot overwrite the immutable source Take.",
        )
    source_sha256 = sha256_file(source)

    def checkpoint(completed: int, message: str) -> None:
        if cancel_check and cancel_check():
            destination.unlink(missing_ok=True)
            raise AudioMasteringCancelled()
        if progress_callback:
            progress_callback(completed, 7, message)

    checkpoint(0, "Decoding source Take")
    try:
        with source.open("rb") as handle:
            audio = AudioSegment.from_file(
                handle,
                format=source.suffix.casefold().lstrip(".") or None,
            )
    except Exception as exc:
        raise AudioMasteringError(
            "audio_mastering_source_invalid",
            "The source Take audio could not be decoded.",
        ) from exc
    audio = audio.set_channels(1)
    if len(audio) < MIN_AUDIO_DURATION_MS:
        raise AudioMasteringError(
            "audio_mastering_source_too_short",
            "The source Take is too short for publication mastering.",
        )
    before = _audio_metrics(audio)

    checkpoint(1, "Applying corrective filters")
    room = normalized.get("room_correction") or {}
    high_pass = max(
        [
            item
            for item in (
                normalized.get("high_pass_hz"),
                room.get("high_pass_hz"),
            )
            if item is not None
        ],
        default=None,
    )
    low_pass = min(
        [
            item
            for item in (
                normalized.get("low_pass_hz"),
                room.get("low_pass_hz"),
            )
            if item is not None
        ],
        default=None,
    )
    if high_pass is not None:
        audio = audio.high_pass_filter(high_pass)
    if low_pass is not None:
        audio = audio.low_pass_filter(low_pass)
    total_gain = float(normalized["gain_db"]) + float(room.get("gain_db") or 0)
    if total_gain:
        audio = audio.apply_gain(total_gain)

    checkpoint(2, "Applying bounded dynamics")
    compression = normalized["compression"]
    if compression["enabled"]:
        audio = effects.compress_dynamic_range(
            audio,
            threshold=compression["threshold_dbfs"],
            ratio=compression["ratio"],
            attack=compression["attack_ms"],
            release=compression["release_ms"],
        )

    checkpoint(3, "Normalizing publication loudness")
    normalization = normalized["normalization"]
    normalization_gain_db = 0.0
    current = _audio_metrics(audio)
    if normalization["enabled"]:
        requested = (
            normalization["target_loudness_dbfs"]
            - current["estimated_loudness_dbfs"]
        )
        normalization_gain_db = max(
            -normalization["maximum_gain_db"],
            min(normalization["maximum_gain_db"], requested),
        )
        if abs(normalization_gain_db) >= 0.0001:
            audio = audio.apply_gain(normalization_gain_db)

    checkpoint(4, "Enforcing publication peak ceiling")
    ceiling = normalized["limiter_ceiling_dbfs"]
    if audio.max_dBFS > ceiling:
        audio = audio.apply_gain(ceiling - audio.max_dBFS)
    after = _audio_metrics(audio)
    duration_drift = abs(after["duration_ms"] - before["duration_ms"])
    safeguards = {
        "duration_preserved": duration_drift <= MAX_AUDIO_DURATION_DRIFT_MS,
        "duration_drift_ms": duration_drift,
        "no_clipped_samples": after["clipped_sample_count"] == 0,
        "peak_ceiling_passed": (
            after["estimated_true_peak_dbfs"] <= ceiling + 0.15
        ),
        "non_silent": after["estimated_loudness_dbfs"] > -70,
        "normalization_target_passed": (
            not normalization["enabled"]
            or abs(
                after["estimated_loudness_dbfs"]
                - normalization["target_loudness_dbfs"]
            )
            <= 1.5
            or abs(normalization_gain_db)
            >= normalization["maximum_gain_db"] - 0.001
        ),
    }
    if not all(
        safeguards[key]
        for key in (
            "duration_preserved",
            "no_clipped_samples",
            "peak_ceiling_passed",
            "non_silent",
            "normalization_target_passed",
        )
    ):
        raise AudioMasteringError(
            "audio_mastering_safeguard_failed",
            "Mastered audio failed publication safeguards.",
            context={"safeguards": safeguards, "metrics": after},
        )

    checkpoint(5, "Writing deterministic mastered WAV")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".wav.tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary_output = Path(temporary_name)
    try:
        export = audio.export(
            temporary_output,
            format="wav",
            codec="pcm_s16le",
        )
        close = getattr(export, "close", None)
        if callable(close):
            close()
        validation = validate_audio_file(
            temporary_output,
            format_hint="wav",
        )
        os.replace(temporary_output, destination)
    except Exception as exc:
        temporary_output.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise AudioMasteringError(
            "audio_mastering_output_invalid",
            "The mastered child rendition could not be validated.",
        ) from exc
    checkpoint(6, "Verifying mastered artifact identity")
    output_sha256 = sha256_file(destination)
    if output_sha256 != validation["sha256"]:
        destination.unlink(missing_ok=True)
        raise AudioMasteringError(
            "audio_mastering_output_invalid",
            "The mastered artifact hash changed during validation.",
        )
    if sha256_file(source) != source_sha256:
        destination.unlink(missing_ok=True)
        raise AudioMasteringError(
            "audio_mastering_source_changed",
            "The source Take changed while mastering was running.",
        )

    processing = {
        "schema_version": MASTERING_SCHEMA_VERSION,
        "operation": MASTERING_OPERATION,
        "settings": normalized,
        "settings_fingerprint": normalized["settings_fingerprint"],
        "source_sha256": source_sha256,
        "source_duration_ms": before["duration_ms"],
        "output_sha256": output_sha256,
        "output_duration_ms": validation["duration_ms"],
        "metrics_before": before,
        "metrics_after": after,
        "normalization_gain_db": round(normalization_gain_db, 4),
        "safeguards": safeguards,
        "provenance": provenance_value,
        "publication_state": "candidate_verified",
    }
    processing["processing_fingerprint"] = fingerprint_value(processing)
    checkpoint(7, "Mastered candidate verified")
    return processing
