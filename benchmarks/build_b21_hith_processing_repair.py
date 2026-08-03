#!/usr/bin/env python3
"""Build a focused blind repair round for Powerless Friendless/Hith processing.

This round deliberately reuses the retained raw Qwen candidate that Tristan
scored 5/5/5 for identity, delivery, and naturalness. It changes only
deterministic post-processing, performs no synthesis, and never mutates the
live project or production routing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import sys
from typing import Any, Callable

import numpy as np
from scipy.signal import butter, sosfilt
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from voice_effects import apply_voice_effect_chain  # noqa: E402


ROUND_ID = "b21_hith_processing_repair_20260803"
SEED = 2026080302
ASSETS = ROOT / "benchmarks" / "b18_multivoice_review_assets"
SOURCE_ROOT = (
    ROOT
    / ".omo"
    / "evidence"
    / "b18-multivoice-archetype-screen-20260803"
    / "review"
)
DEFAULT_SOURCE = SOURCE_ROOT / "audio" / "HIT03.wav"
DEFAULT_EXISTING = SOURCE_ROOT / "audio" / "HIT04.wav"
DEFAULT_REFERENCE = SOURCE_ROOT / "reference" / "hit_reference.wav"
DEFAULT_SOURCE_DATA = SOURCE_ROOT / "data.json"
DEFAULT_OUTPUT = Path(
    "/Users/tristan/Downloads/b21_hith_processing_repair_20260803"
)

SOURCE_SHA256 = "330e67cc942c38e422ef3b291b25da39e336510b3babfc0ba030305aecfe4c97"
EXISTING_SHA256 = "a512b2811bce8237e06def12e0e8bc833efe5b0aa83113da4a363301e40a2874"
REFERENCE_SHA256 = "31a4c37f744ea4c413dff7ea9ccb4caa9fe08f1f3ac84f63b7f16328acb885a9"


class HithRepairError(RuntimeError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise HithRepairError(f"{label} is missing: {path}")
    actual = sha256_file(path)
    if actual != expected:
        raise HithRepairError(
            f"{label} changed: expected {expected}, received {actual}."
        )


def _read_mono(path: Path) -> tuple[np.ndarray, int]:
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    if mono.size == 0 or not np.all(np.isfinite(mono)):
        raise HithRepairError(f"Audio is empty or invalid: {path}")
    return mono, int(rate)


def _bandpass(audio: np.ndarray, rate: int, low: float, high: float) -> np.ndarray:
    nyquist = max(1.0, rate / 2.0)
    low_value = max(20.0, min(low, nyquist * 0.8)) / nyquist
    high_value = max(low + 20.0, min(high, nyquist * 0.95)) / nyquist
    sos = butter(3, [low_value, high_value], btype="bandpass", output="sos")
    return np.asarray(sosfilt(sos, audio), dtype=np.float32)


def _fixed_delay(audio: np.ndarray, rate: int, delay_ms: float) -> np.ndarray:
    delay = max(1, int(round(rate * delay_ms / 1000.0)))
    shifted = np.zeros_like(audio)
    shifted[delay:] = audio[:-delay]
    return shifted


def _variable_delay(
    audio: np.ndarray,
    rate: int,
    *,
    base_ms: float,
    depth_ms: float,
    modulation_hz: float,
    phase_radians: float,
) -> np.ndarray:
    positions = np.arange(audio.size, dtype=np.float64)
    seconds = positions / float(rate)
    delay_ms = base_ms + depth_ms * np.sin(
        2.0 * np.pi * modulation_hz * seconds + phase_radians
    )
    source_positions = positions - delay_ms * float(rate) / 1000.0
    delayed = np.interp(
        source_positions,
        positions,
        np.asarray(audio, dtype=np.float64),
        left=0.0,
        right=0.0,
    )
    return np.asarray(delayed, dtype=np.float32)


def _soft_saturate(audio: np.ndarray, drive: float) -> np.ndarray:
    return np.asarray(np.tanh(audio * drive) / np.tanh(drive), dtype=np.float32)


def _normalize(audio: np.ndarray) -> np.ndarray:
    output = np.asarray(audio, dtype=np.float32)
    peak = float(np.max(np.abs(output))) if output.size else 0.0
    target_peak = 10.0 ** (-1.0 / 20.0)
    if peak > target_peak:
        output = output * (target_peak / peak)
    return np.asarray(output, dtype=np.float32)


def _phase_chorus_v2(audio: np.ndarray, rate: int) -> tuple[np.ndarray, dict[str, Any]]:
    base = _bandpass(audio, rate, 150.0, 5600.0)
    first = _variable_delay(
        base,
        rate,
        base_ms=5.5,
        depth_ms=2.2,
        modulation_hz=4.7,
        phase_radians=0.0,
    )
    second = _variable_delay(
        base,
        rate,
        base_ms=11.0,
        depth_ms=3.4,
        modulation_hz=3.1,
        phase_radians=np.pi / 2.0,
    )
    time_axis = np.arange(base.size, dtype=np.float32) / float(rate)
    output = 0.66 * base + 0.20 * first + 0.14 * second
    output *= 1.0 + 0.08 * np.sin(2.0 * np.pi * 9.2 * time_axis)
    output = _soft_saturate(output, 1.14)
    return _normalize(output), {
        "bandpass_hz": [150.0, 5600.0],
        "variable_delays_ms": [[5.5, 2.2, 4.7], [11.0, 3.4, 3.1]],
        "mix": [0.66, 0.20, 0.14],
        "amplitude_modulation_hz": 9.2,
        "amplitude_modulation_depth": 0.08,
        "soft_saturation": 1.14,
    }


def _throat_split_v2(audio: np.ndarray, rate: int) -> tuple[np.ndarray, dict[str, Any]]:
    base = _bandpass(audio, rate, 130.0, 6100.0)
    slow = _variable_delay(
        base,
        rate,
        base_ms=8.5,
        depth_ms=3.6,
        modulation_hz=2.6,
        phase_radians=0.0,
    )
    fast = _variable_delay(
        base,
        rate,
        base_ms=3.4,
        depth_ms=1.1,
        modulation_hz=8.1,
        phase_radians=np.pi,
    )
    time_axis = np.arange(base.size, dtype=np.float32) / float(rate)
    output = 0.59 * base + 0.27 * slow + 0.14 * fast
    output *= 1.0 + 0.11 * np.sin(2.0 * np.pi * 7.3 * time_axis)
    output *= 1.0 + 0.04 * np.sin(2.0 * np.pi * 17.0 * time_axis)
    output = _soft_saturate(output, 1.18)
    return _normalize(output), {
        "bandpass_hz": [130.0, 6100.0],
        "variable_delays_ms": [[8.5, 3.6, 2.6], [3.4, 1.1, 8.1]],
        "mix": [0.59, 0.27, 0.14],
        "amplitude_modulation_hz": [7.3, 17.0],
        "amplitude_modulation_depth": [0.11, 0.04],
        "soft_saturation": 1.18,
    }


def _resonant_rasp_v2(audio: np.ndarray, rate: int) -> tuple[np.ndarray, dict[str, Any]]:
    base = _bandpass(audio, rate, 190.0, 5000.0)
    hollow = base - 0.18 * _fixed_delay(base, rate, 2.7)
    chorus = _variable_delay(
        hollow,
        rate,
        base_ms=7.5,
        depth_ms=1.8,
        modulation_hz=6.4,
        phase_radians=np.pi / 3.0,
    )
    edge = np.concatenate((np.zeros(1, dtype=np.float32), np.diff(base)))
    base_rms = float(np.sqrt(np.mean(np.square(base), dtype=np.float64)))
    edge_rms = float(np.sqrt(np.mean(np.square(edge), dtype=np.float64)))
    if edge_rms > 0.0:
        edge = edge * (base_rms / edge_rms)
    time_axis = np.arange(base.size, dtype=np.float32) / float(rate)
    output = 0.72 * hollow + 0.22 * chorus + 0.06 * edge
    output *= 1.0 + 0.12 * np.sin(2.0 * np.pi * 12.5 * time_axis)
    output = _soft_saturate(output, 1.22)
    return _normalize(output), {
        "bandpass_hz": [190.0, 5000.0],
        "comb_delay_ms": 2.7,
        "comb_inversion_mix": 0.18,
        "variable_delay_ms": [7.5, 1.8, 6.4],
        "mix": [0.72, 0.22, 0.06],
        "amplitude_modulation_hz": 12.5,
        "amplitude_modulation_depth": 0.12,
        "soft_saturation": 1.22,
    }


Renderer = Callable[[np.ndarray, int], tuple[np.ndarray, dict[str, Any]]]


EVALUATION_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "method": "raw_qwen_anchor",
        "description": "Unprocessed 5/5/5 Qwen anchor from the prior blind round.",
        "renderer": None,
    },
    {
        "method": "powerless_alien_modulation_v1_anchor",
        "description": "Current restricted production chain retained as a blind anchor.",
        "renderer": "production_v1",
    },
    {
        "method": "hith_phase_chorus_v2",
        "description": "Dual phase-varying chorus with restrained alien tremolo.",
        "renderer": _phase_chorus_v2,
    },
    {
        "method": "hith_throat_split_v2",
        "description": "Two independently moving throat layers with asymmetric modulation.",
        "renderer": _throat_split_v2,
    },
    {
        "method": "hith_resonant_rasp_v2",
        "description": "Hollow comb resonance, moving double, and low-level spectral rasp.",
        "renderer": _resonant_rasp_v2,
    },
)


def _audio_record(path: Path) -> dict[str, Any]:
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(mono), dtype=np.float64))) if mono.size else 0.0
    return {
        "sha256": sha256_file(path),
        "sample_rate": int(rate),
        "channels": int(audio.shape[1]),
        "frames": int(audio.shape[0]),
        "duration_seconds": float(audio.shape[0] / float(rate)),
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-12)),
        "rms_dbfs": 20.0 * math.log10(max(rms, 1e-12)),
    }


def _source_contract(path: Path) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    groups = [
        item
        for item in value.get("groups", [])
        if item.get("speaker_key") == "POWERLESS FRIENDLESS"
    ]
    if len(groups) != 1:
        raise HithRepairError("Prior Hith review group is missing or ambiguous.")
    group = groups[0]
    return {
        "display_name": str(group["display_name"]),
        "archetype": str(group["archetype"]),
        "expected_text": str(group["expected_text"]),
        "instruction": str(group["instruction"]),
    }


def build_round(
    *,
    source_path: str | Path = DEFAULT_SOURCE,
    existing_path: str | Path = DEFAULT_EXISTING,
    reference_path: str | Path = DEFAULT_REFERENCE,
    source_data_path: str | Path = DEFAULT_SOURCE_DATA,
    output_root: str | Path = DEFAULT_OUTPUT,
    verify_tracked_hashes: bool = True,
) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    existing = Path(existing_path).expanduser().resolve()
    reference = Path(reference_path).expanduser().resolve()
    source_data = Path(source_data_path).expanduser().resolve()
    output = Path(output_root).expanduser().resolve()
    if verify_tracked_hashes:
        _verify(source, SOURCE_SHA256, "Retained raw Qwen source")
        _verify(existing, EXISTING_SHA256, "Existing v1 processed anchor")
        _verify(reference, REFERENCE_SHA256, "Approved Hith reference")
    for path, label in (
        (source, "Source audio"),
        (existing, "Existing anchor"),
        (reference, "Reference audio"),
        (source_data, "Prior review data"),
    ):
        if not path.is_file():
            raise HithRepairError(f"{label} is missing: {path}")
    if output.exists():
        shutil.rmtree(output)
    review = output / "review"
    audio_root = review / "audio"
    reference_root = review / "reference"
    answer_root = output / "answer-keys"
    audio_root.mkdir(parents=True)
    reference_root.mkdir(parents=True)
    answer_root.mkdir(parents=True)
    for asset in ("index.html", "app.js", "styles.css"):
        shutil.copy2(ASSETS / asset, review / asset)
    index = (review / "index.html").read_text(encoding="utf-8")
    index = index.replace(
        "Alexandria multi-Voice blind screen",
        "Alexandria Hith processing repair",
    ).replace(
        "Alexandria · Boundary 18",
        "Alexandria · Boundary 21",
    ).replace(
        "Multi-Voice archetype screen",
        "Hith processing repair",
    )
    (review / "index.html").write_text(index, encoding="utf-8")
    reference_target = reference_root / "hith_reference.wav"
    shutil.copy2(reference, reference_target)

    mono, rate = _read_mono(source)
    generated: list[dict[str, Any]] = []
    for variant in EVALUATION_VARIANTS:
        method = str(variant["method"])
        destination = output / "private" / "audio" / f"{method}.wav"
        destination.parent.mkdir(parents=True, exist_ok=True)
        renderer = variant["renderer"]
        parameters: dict[str, Any] | None = None
        if renderer is None:
            shutil.copy2(source, destination)
        elif renderer == "production_v1":
            shutil.copy2(source, destination)
            receipt = apply_voice_effect_chain(
                destination,
                "powerless_alien_modulation_v1",
            )
            parameters = dict((receipt or {}).get("parameters") or {})
            if verify_tracked_hashes and sha256_file(destination) != sha256_file(existing):
                raise HithRepairError(
                    "Re-rendered v1 anchor does not match the retained reviewed WAV."
                )
        else:
            output_audio, parameters = renderer(mono.copy(), rate)
            sf.write(
                str(destination),
                output_audio,
                rate,
                subtype="PCM_16",
            )
        generated.append(
            {
                "candidate_id": hashlib.sha256(
                    f"{ROUND_ID}:{method}".encode("utf-8")
                ).hexdigest()[:16],
                "method": method,
                "description": str(variant["description"]),
                "source_sha256": sha256_file(source),
                "parameters": parameters,
                "audio_path": str(destination),
                "audio": _audio_record(destination),
            }
        )

    if len({item["audio"]["sha256"] for item in generated}) != len(generated):
        raise HithRepairError("Two Hith processing candidates are byte-identical.")
    contract = _source_contract(source_data)
    rows = list(generated)
    random.Random(SEED).shuffle(rows)
    public_samples: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    for index_number, row in enumerate(rows, start=1):
        blind_id = f"HPR{index_number:02d}"
        source_audio = Path(row["audio_path"])
        target = audio_root / f"{blind_id}.wav"
        shutil.copy2(source_audio, target)
        public_samples.append(
            {
                "sample_id": blind_id,
                "speaker_key": "POWERLESS FRIENDLESS",
                "expected_speaker": contract["display_name"],
                "expected_text": contract["expected_text"],
                "instruction": contract["instruction"],
                "archetype": contract["archetype"],
                "audio": f"audio/{target.name}",
            }
        )
        answers.append(
            {
                "sample_id": blind_id,
                **row,
            }
        )
    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "groups": [
            {
                "speaker_key": "POWERLESS FRIENDLESS",
                "display_name": contract["display_name"],
                "archetype": contract["archetype"],
                "expected_text": contract["expected_text"],
                "instruction": contract["instruction"],
                "reference_audio": "reference/hith_reference.wav",
                "candidate_count": len(public_samples),
            }
        ],
        "samples": public_samples,
        "score_contract": {
            "identity": "1 wrong character; 5 unmistakably Powerless Friendless",
            "delivery": "1 loses the panic; 5 preserves urgent Hith delivery",
            "naturalness": "1 damaged speech; 5 convincing processed performance",
            "text_match": "The entire authored line is present without additions",
            "artifact_free": "No clicks, warble damage, metallic breakup, or objectionable processing",
        },
        "review_rule": (
            "Judge the alien processing, not the backend. The underlying raw Qwen "
            "performance already passed 5/5/5. Prefer the candidate that sounds "
            "clearly non-human while preserving identity, panic, words, and natural timing."
        ),
    }
    (review / "data.json").write_text(
        json.dumps(public, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (review / "data.js").write_text(
        "window.ALEXANDRIA_REVIEW_DATA = "
        + json.dumps(public, ensure_ascii=False, sort_keys=True)
        + ";\n",
        encoding="utf-8",
    )
    answer = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "seed": SEED,
        "source_audio": _audio_record(source),
        "existing_v1_audio": _audio_record(existing),
        "reference_audio": _audio_record(reference),
        "answers": answers,
        "production_promotion_allowed": False,
        "synthesis_performed": False,
        "live_project_changed": False,
    }
    (answer_root / "answer-key.json").write_text(
        json.dumps(answer, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "candidate_count": len(public_samples),
        "review_path": str(review / "index.html"),
        "answer_key_path": str(answer_root / "answer-key.json"),
        "data_sha256": sha256_file(review / "data.json"),
        "data_js_sha256": sha256_file(review / "data.js"),
        "answer_key_sha256": sha256_file(answer_root / "answer-key.json"),
        "file_url_compatible": True,
        "production_promotion_allowed": False,
        "synthesis_performed": False,
        "live_project_changed": False,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-path", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--existing-path", type=Path, default=DEFAULT_EXISTING)
    parser.add_argument("--reference-path", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--source-data-path", type=Path, default=DEFAULT_SOURCE_DATA)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(
        json.dumps(
            build_round(
                source_path=args.source_path,
                existing_path=args.existing_path,
                reference_path=args.reference_path,
                source_data_path=args.source_data_path,
                output_root=args.output_root,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
