#!/usr/bin/env python3
"""Build the final bounded direct-overlap repair round."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from model_registry import resolve_model_path

import benchmarks.build_original_sin_direct_overlap_expansion_batch_002 as review_base
from benchmarks.build_original_sin_direct_overlap_boundary_repair_v3 import (
    source_wav_for,
    write_start_trim,
    write_tail_splice,
    write_trailing_trim,
)
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


ROUND_ID = "alexandria_original_sin_direct_overlap_boundary_repair_v9"
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_direct_overlap_boundary_repair_plan_v9.json")


class BoundaryRepairV9Error(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def apply_clarity(source: Path, destination: Path, profile: dict[str, Any]) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    audio_filter = (
        f"highpass=f={int(profile['highpass_hz'])},"
        f"equalizer=f={int(profile['presence_hz'])}:t=q:w=0.8:g={float(profile['presence_gain_db'])},"
        "alimiter=limit=0.95"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-af", audio_filter, "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(destination)],
        check=True,
    )
    return {"filter": audio_filter, "profile": profile}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    project = args.project_root.expanduser().resolve()
    plan = read_json(args.plan.expanduser().resolve())
    if plan.get("round_id") != ROUND_ID or not plan.get("final_bounded_round"):
        raise BoundaryRepairV9Error("Boundary-repair v9 plan mismatch")
    workflow = project / "external_workflows/big_finish_overlap_reference_v1"
    answers = {
        name: read_json(workflow / name / "private/answer-key.json")["candidates"]
        for name in {group["source_round"] for group in plan["groups"]}
    }
    output = args.output_root.expanduser().resolve() if args.output_root else workflow / "direct_overlap_boundary_repair_v9"
    if output.exists():
        if not args.replace:
            raise BoundaryRepairV9Error(f"Output exists: {output}")
        shutil.rmtree(output)

    before = project_hashes(project)
    whisper = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    private = output / "private/audio"
    private.mkdir(parents=True, exist_ok=True)
    groups: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []

    for spec in plan["groups"]:
        bases = [(cid, answers[spec["source_round"]][cid]) for cid in spec["candidate_ids"]]
        candidates: list[dict[str, Any]] = []
        for base_id, base in bases:
            action = spec["action"]
            if action == "terminal_trim":
                variants = [(postroll, None) for postroll in plan["terminal_postroll_seconds"]]
            elif action in {"start_trim", "start_trim_clarity"}:
                variants = [(preroll, profile if action.endswith("clarity") else None) for preroll in plan["start_preroll_seconds"] for profile in (["mild", "moderate"] if action.endswith("clarity") else [None])]
            elif action == "clarity":
                variants = [(None, profile) for profile in plan["clarity_profiles"]]
            elif action in {"tail_recovery", "clarity_tail_recovery"}:
                variants = [(ms, "mild" if action.startswith("clarity") else None) for ms in plan["tail_recovery_milliseconds"]]
            else:
                raise BoundaryRepairV9Error(f"Unsupported action: {action}")

            for first, second in variants:
                parts = [action]
                if isinstance(first, float):
                    parts.append(f"postroll{first}")
                elif isinstance(first, int):
                    parts.append(f"{first}ms")
                if second:
                    parts.append(str(second))
                treatment = f"{base['treatment']}__{'_'.join(parts)}"
                wav = private / f"chunk_{spec['chunk_id']}__{treatment}.wav"
                proxy = private / f"chunk_{spec['chunk_id']}__{treatment}.mp3"
                try:
                    source = base["source"]
                    if action == "terminal_trim":
                        keep = float(source["transcript_segment_end_seconds"]) - float(source["source_start_seconds"]) + float(first)
                        receipt = write_trailing_trim(Path(base["wav_path"]), wav, keep, int(plan["fade_milliseconds"]), int(plan["appended_silence_milliseconds"]))
                    elif action == "start_trim":
                        trim = max(0.0, float(source["transcript_segment_start_seconds"]) - float(source["source_start_seconds"]) - float(first))
                        receipt = write_start_trim(Path(base["wav_path"]), wav, trim, int(plan["fade_milliseconds"]), int(plan["appended_silence_milliseconds"]))
                    elif action == "start_trim_clarity":
                        trimmed = private / f"chunk_{spec['chunk_id']}__trim_{first}_{second}.wav"
                        trim = max(0.0, float(source["transcript_segment_start_seconds"]) - float(source["source_start_seconds"]) - float(first))
                        receipt = write_start_trim(Path(base["wav_path"]), trimmed, trim, int(plan["fade_milliseconds"]), int(plan["appended_silence_milliseconds"]))
                        receipt["clarity"] = apply_clarity(trimmed, wav, plan["clarity_profiles"][second])
                    elif action == "clarity":
                        receipt = apply_clarity(Path(base["wav_path"]), wav, plan["clarity_profiles"][second])
                    else:
                        processed = Path(base["wav_path"])
                        if action == "clarity_tail_recovery":
                            clarified = private / f"chunk_{spec['chunk_id']}__clarified_{first}.wav"
                            apply_clarity(processed, clarified, plan["clarity_profiles"][second])
                            processed = clarified
                        receipt = write_tail_splice(
                            processed,
                            source_wav_for(base),
                            wav,
                            takeover_ms=int(first),
                            start_trim_ms=0,
                            fade_ms=int(plan["fade_milliseconds"]),
                            silence_ms=int(plan["appended_silence_milliseconds"]),
                        )

                    wav_check = accepted_transcript_check([base["transcript"]], transcribe(wav, whisper))
                    encode_proxy(wav, proxy, bitrate=plan["production_proxy"]["bitrate"])
                    proxy_check = accepted_transcript_check([base["transcript"]], transcribe(proxy, whisper))
                    probe = probe_audio(proxy)
                    if not (
                        transcript_check_eligible(wav_check)
                        and transcript_check_eligible(proxy_check)
                        and probe["codec_name"] == "mp3"
                        and probe["sample_rate"] == 44100
                        and probe["channels"] == 2
                    ):
                        raise BoundaryRepairV9Error("Objective transcript/proxy gate failed")
                    candidates.append({
                        "treatment": treatment,
                        "base_candidate_id": base_id,
                        "base_treatment": base["treatment"],
                        "repair_action": action,
                        "repair_receipt": receipt,
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
            first_base = bases[0][1]
            context = "Final bounded repair. Any remaining boundary, clarity, identity, effect, or artifact defect is a final rejection."
            if int(first_base["chunk_id"]) == 4888:
                context += " Also judge suitability for the Doctor reference bank, including general delivery and rolled-R evidence."
            groups.append({
                "character": first_base["character"],
                "book_speaker": first_base["book_speaker"],
                "chunk_id": int(first_base["chunk_id"]),
                "transcript": first_base["transcript"],
                "source": first_base["source"],
                "review_context": context,
                "candidates": candidates,
            })

    review_base.ROUND_ID = ROUND_ID
    review_base.build_review(output, groups)
    after = project_hashes(project)
    if before != after:
        raise BoundaryRepairV9Error("Protected project hashes changed")
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
