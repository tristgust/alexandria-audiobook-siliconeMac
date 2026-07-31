#!/usr/bin/env python3
"""Build surgical start/end repairs for expansion batches 001 and 002."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from model_registry import resolve_model_path
import benchmarks.build_original_sin_direct_overlap_expansion_batch_002 as review_base
from benchmarks.build_original_sin_direct_substitution_round import encode_proxy, probe_audio
from benchmarks.build_original_sin_overlap_reference_round import (
    WHISPER_MODEL_KEY,
    metrics,
    sha256_file,
    transcribe,
    utc_now,
    write_json,
)
from benchmarks.build_original_sin_overlap_reference_repair_round import project_hashes
from benchmarks.original_sin_overlap_word_alignment import accepted_transcript_check, transcript_check_eligible


ROUND_ID = "alexandria_original_sin_direct_overlap_boundary_repair_v3"
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_direct_overlap_boundary_repair_plan_v3.json")


class BoundaryRepairError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _stereo(audio: np.ndarray) -> np.ndarray:
    if audio.shape[1] == 1:
        return np.repeat(audio, 2, axis=1)
    return audio[:, :2]


def _fade_in(audio: np.ndarray, frames: int) -> None:
    frames = min(frames, len(audio))
    if frames > 0:
        audio[:frames] *= np.linspace(0.0, 1.0, frames, dtype=np.float32)[:, None]


def _fade_out(audio: np.ndarray, frames: int) -> None:
    frames = min(frames, len(audio))
    if frames > 0:
        audio[-frames:] *= np.linspace(1.0, 0.0, frames, dtype=np.float32)[:, None]


def _append_silence(audio: np.ndarray, rate: int, milliseconds: int) -> np.ndarray:
    frames = round(rate * milliseconds / 1000)
    if not frames:
        return audio
    return np.concatenate([audio, np.zeros((frames, audio.shape[1]), dtype=np.float32)], axis=0)


def write_start_trim(source: Path, destination: Path, trim_seconds: float, fade_ms: int, silence_ms: int) -> dict[str, Any]:
    audio, rate = sf.read(str(source), dtype="float32", always_2d=True)
    audio = _stereo(audio)
    trim_frames = max(0, min(len(audio) - 1, round(rate * trim_seconds)))
    output = audio[trim_frames:].copy()
    _fade_in(output, round(rate * fade_ms / 1000))
    output = _append_silence(output, rate, silence_ms)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(destination), output, rate, subtype="PCM_16")
    return {"trimmed_start_seconds": trim_frames / rate, "fade_milliseconds": fade_ms, "appended_silence_milliseconds": silence_ms}


def write_trailing_trim(source: Path, destination: Path, keep_seconds: float, fade_ms: int, silence_ms: int) -> dict[str, Any]:
    audio, rate = sf.read(str(source), dtype="float32", always_2d=True)
    audio = _stereo(audio)
    keep_frames = max(1, min(len(audio), round(rate * keep_seconds)))
    output = audio[:keep_frames].copy()
    _fade_out(output, round(rate * fade_ms / 1000))
    output = _append_silence(output, rate, silence_ms)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(destination), output, rate, subtype="PCM_16")
    return {"kept_seconds": keep_frames / rate, "fade_milliseconds": fade_ms, "appended_silence_milliseconds": silence_ms}


def write_tail_splice(
    processed: Path,
    original_source: Path,
    destination: Path,
    *,
    takeover_ms: int,
    start_trim_ms: int,
    fade_ms: int,
    silence_ms: int,
) -> dict[str, Any]:
    proc, proc_rate = sf.read(str(processed), dtype="float32", always_2d=True)
    raw, raw_rate = sf.read(str(original_source), dtype="float32", always_2d=True)
    if proc_rate != raw_rate:
        raise BoundaryRepairError("Tail-splice sample rates differ")
    proc, raw = _stereo(proc), _stereo(raw)
    frame_count = min(len(proc), len(raw))
    proc, raw = proc[:frame_count].copy(), raw[:frame_count].copy()
    takeover = min(frame_count, max(1, round(proc_rate * takeover_ms / 1000)))
    start = frame_count - takeover
    mix = np.linspace(0.0, 1.0, takeover, dtype=np.float32)[:, None]
    proc[start:] = proc[start:] * (1.0 - mix) + raw[start:] * mix
    trim_frames = min(len(proc) - 1, round(proc_rate * start_trim_ms / 1000))
    output = proc[trim_frames:].copy()
    _fade_in(output, round(proc_rate * fade_ms / 1000))
    output = _append_silence(output, proc_rate, silence_ms)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(destination), output, proc_rate, subtype="PCM_16")
    return {
        "tail_source_takeover_milliseconds": takeover_ms,
        "trimmed_start_milliseconds": start_trim_ms,
        "fade_milliseconds": fade_ms,
        "appended_silence_milliseconds": silence_ms,
    }


def source_wav_for(candidate: dict[str, Any]) -> Path:
    wav = Path(str(candidate["wav_path"]))
    matches = list(wav.parent.glob(f"chunk_{int(candidate['chunk_id'])}_*__source.wav"))
    if len(matches) != 1:
        raise BoundaryRepairError(f"Expected one source WAV for chunk {candidate['chunk_id']}: {matches}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    project = args.project_root.expanduser().resolve()
    plan = read_json(args.plan.expanduser().resolve())
    if plan.get("round_id") != ROUND_ID:
        raise BoundaryRepairError("Boundary-repair plan mismatch")
    workflow = project / "external_workflows/big_finish_overlap_reference_v1"
    answer_keys = {
        "direct_overlap_expansion_batch_001_timing_repair_v2": read_json(workflow / "direct_overlap_expansion_batch_001_timing_repair_v2/private/answer-key.json")["candidates"],
        "direct_overlap_expansion_batch_002": read_json(workflow / "direct_overlap_expansion_batch_002/private/answer-key.json")["candidates"],
    }
    output = args.output_root.expanduser().resolve() if args.output_root else workflow / "direct_overlap_boundary_repair_v3"
    if output.exists():
        if not args.replace:
            raise BoundaryRepairError(f"Output exists: {output}")
        shutil.rmtree(output)
    before = project_hashes(project)
    whisper = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    private = output / "private/audio"
    groups: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    for spec in plan["groups"]:
        rows = answer_keys[spec["source_round"]]
        bases = [rows[candidate_id] for candidate_id in spec["candidate_ids"]]
        candidates: list[dict[str, Any]] = []
        for base in bases:
            variants: list[tuple[str, dict[str, int]]] = []
            if spec["action"] in {"start_trim", "trailing_trim"}:
                variants = [(spec["action"], {})]
            elif spec["action"] == "tail_splice":
                variants = [("tail_splice", {"takeover_ms": value, "start_trim_ms": 0}) for value in plan["tail_splice_milliseconds"]]
            elif spec["action"] == "start_tail_splice":
                variants = [
                    ("start_tail_splice", {"takeover_ms": takeover, "start_trim_ms": trim})
                    for trim in plan["doctor_start_trim_milliseconds"]
                    for takeover in plan["tail_splice_milliseconds"]
                ]
            else:
                raise BoundaryRepairError(spec["action"])
            for action, values in variants:
                suffix = action
                if values:
                    suffix += "_" + "_".join(f"{key}{value}" for key, value in values.items())
                treatment = f"{base['treatment']}__{suffix}"
                wav = private / f"chunk_{spec['chunk_id']}__{treatment}.wav"
                try:
                    source = base["source"]
                    if action == "start_trim":
                        trim = max(0.0, float(source["transcript_segment_start_seconds"]) - float(source["source_start_seconds"]) - float(plan["start_preroll_seconds"]))
                        repair = write_start_trim(Path(base["wav_path"]), wav, trim, int(plan["fade_milliseconds"]), int(plan["appended_silence_milliseconds"]))
                    elif action == "trailing_trim":
                        keep = float(source["transcript_segment_end_seconds"]) - float(source["source_start_seconds"]) + float(plan["trailing_postroll_seconds"])
                        repair = write_trailing_trim(Path(base["wav_path"]), wav, keep, int(plan["fade_milliseconds"]), int(plan["appended_silence_milliseconds"]))
                    else:
                        repair = write_tail_splice(
                            Path(base["wav_path"]), source_wav_for(base), wav,
                            takeover_ms=int(values["takeover_ms"]),
                            start_trim_ms=int(values["start_trim_ms"]),
                            fade_ms=int(plan["fade_milliseconds"]),
                            silence_ms=int(plan["appended_silence_milliseconds"]),
                        )
                    wav_check = accepted_transcript_check([base["transcript"]], transcribe(wav, whisper))
                    proxy = private / f"chunk_{spec['chunk_id']}__{treatment}.mp3"
                    encode_proxy(wav, proxy, bitrate=plan["production_proxy"]["bitrate"])
                    proxy_check = accepted_transcript_check([base["transcript"]], transcribe(proxy, whisper))
                    probe = probe_audio(proxy)
                    if not (transcript_check_eligible(wav_check) and transcript_check_eligible(proxy_check) and probe["codec_name"] == "mp3" and probe["sample_rate"] == 44100 and probe["channels"] == 2):
                        raise BoundaryRepairError("Objective transcript/proxy gate failed")
                    candidates.append({
                        "treatment": treatment,
                        "base_candidate_id": next(key for key, value in rows.items() if value is base),
                        "base_treatment": base["treatment"],
                        "repair_action": spec["action"],
                        "repair_receipt": repair,
                        "wav_path": wav,
                        "wav_metrics": metrics(wav),
                        "wav_objective": wav_check,
                        "proxy_path": proxy,
                        "proxy_sha256": sha256_file(proxy),
                        "proxy_probe": probe,
                        "proxy_objective": proxy_check,
                        "objective_eligible": True,
                    })
                except Exception as exc:
                    omissions.append({"chunk_id": spec["chunk_id"], "treatment": treatment, "error_type": type(exc).__name__, "error": str(exc)[:1200]})
        if candidates:
            first = bases[0]
            groups.append({
                "character": first["character"],
                "book_speaker": first["book_speaker"],
                "chunk_id": int(first["chunk_id"]),
                "transcript": first["transcript"],
                "source": first["source"],
                "review_context": f"Surgical {spec['action'].replace('_', ' ')} repair; reject any clipped phoneme, pre-roll sound, source-tail contamination, or adjacent breath.",
                "candidates": candidates,
            })
    review_base.ROUND_ID = ROUND_ID
    review_base.build_review(output, groups)
    after = project_hashes(project)
    if before != after:
        raise BoundaryRepairError("Protected project hashes changed")
    write_json(output / "generation-summary.json", {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "generated_at": utc_now(),
        "planned_chunk_count": len(plan["groups"]),
        "review_chunk_count": len(groups),
        "candidate_count": sum(len(group["candidates"]) for group in groups),
        "omissions": omissions,
        "protected_project_hashes_before": before,
        "protected_project_hashes_after": after,
        "production_changes": False,
        "output_root": str(output),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
