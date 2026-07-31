#!/usr/bin/env python3
"""Build final bounded boundary repairs for expansion batch 005."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from mlx_audio.sts.models.mel_roformer import MelRoFormer, MelRoFormerConfig
from model_registry import resolve_model_path

import benchmarks.build_original_sin_direct_overlap_expansion_batch_002 as review_base
from benchmarks.build_original_sin_direct_overlap_boundary_repair_v3 import (
    write_start_trim,
    write_tail_splice,
)
from benchmarks.build_original_sin_direct_substitution_round import encode_proxy, probe_audio
from benchmarks.build_original_sin_overlap_reference_round import (
    VOCAL_MODEL,
    WHISPER_MODEL_KEY,
    cut,
    enhance,
    load_mossformer,
    metrics,
    separate,
    sha256_file,
    transcribe,
    utc_now,
    write_json,
)
from benchmarks.build_original_sin_overlap_reference_repair_round import project_hashes
from benchmarks.original_sin_direct_overlap_timing import append_deterministic_silence
from benchmarks.original_sin_overlap_word_alignment import transcript_comparison


ROUND_ID = "alexandria_original_sin_direct_overlap_boundary_repair_v6"
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_MEDIA = Path("/Users/tristan/Library/Mobile Documents/3L68KQB4HG~com~readdle~CommonDocuments/Documents/DW7*(RERUN:BF)/7c_divergentTimeline3*(RERUN:BF)/20c_bennyChris&Roz/1_originalSinAdaptation.mp3")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_direct_overlap_boundary_repair_plan_v6.json")


class BoundaryRepairV6Error(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def base_treatment(candidate: dict[str, Any]) -> str:
    return str(candidate.get("base_treatment") or candidate["treatment"]).split("__", 1)[0]


def process_treatment(treatment: str, source: Path, destination: Path, mel: Any, moss: Any) -> None:
    if treatment == "mel_roformer_vocal":
        separate(mel, source, destination)
    elif treatment == "mossformer2_source_mix":
        enhance(source, destination, moss)
    else:
        raise BoundaryRepairV6Error(f"Unsupported treatment: {treatment}")


def objective_check(candidate: dict[str, Any], observed: str) -> dict[str, Any]:
    policy = candidate.get("source", {}).get("transcript_policy") or {}
    aliases = policy.get("word_aliases") or {}
    comparison = transcript_comparison([str(candidate["transcript"])], observed, aliases)
    comparison["automatic_transcript"] = observed
    return comparison


def objective_eligible(check: dict[str, Any]) -> bool:
    return (
        check["word_error_rate"] == 0.0
        and check["first_word_present"] is True
        and check["last_word_present"] is True
    )


def source_wav_for(candidate: dict[str, Any]) -> Path:
    wav = Path(str(candidate["wav_path"]))
    matches = list(wav.parent.glob(f"chunk_{int(candidate['chunk_id'])}_*__source.wav"))
    if len(matches) != 1:
        raise BoundaryRepairV6Error(f"Expected one source WAV for chunk {candidate['chunk_id']}: {matches}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--media", type=Path, default=DEFAULT_MEDIA)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    project = args.project_root.expanduser().resolve()
    media = args.media.expanduser().resolve()
    plan = read_json(args.plan.expanduser().resolve())
    if plan.get("round_id") != ROUND_ID:
        raise BoundaryRepairV6Error("Boundary-repair v6 plan mismatch")
    workflow = project / "external_workflows/big_finish_overlap_reference_v1"
    answers = {
        "direct_overlap_boundary_repair_v5": read_json(workflow / "direct_overlap_boundary_repair_v5/private/answer-key.json")["candidates"],
        "direct_overlap_expansion_batch_005": read_json(workflow / "direct_overlap_expansion_batch_005/private/answer-key.json")["candidates"],
    }
    output = args.output_root.expanduser().resolve() if args.output_root else workflow / "direct_overlap_boundary_repair_v6"
    if output.exists():
        if not args.replace:
            raise BoundaryRepairV6Error(f"Output exists: {output}")
        shutil.rmtree(output)

    before = project_hashes(project)
    whisper = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    mel = MelRoFormer.from_pretrained(VOCAL_MODEL, config=MelRoFormerConfig.zfturbo_vocals_v1())
    mel.eval()
    moss = load_mossformer()
    private = output / "private/audio"
    groups: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []

    for spec in plan["groups"]:
        source_answer = answers[spec["source_round"]]
        bases = [(candidate_id, source_answer[candidate_id]) for candidate_id in spec["candidate_ids"]]
        candidates: list[dict[str, Any]] = []
        for base_id, base in bases:
            action = str(spec["action"])
            if action == "start_trim":
                variants = [None]
            elif action in {"micro_tail_extension", "start_micro_tail_extension"}:
                variants = list(spec["extension_milliseconds"])
            elif action == "start_end_cleanup":
                variants = list(spec["postroll_seconds"])
            elif action == "start_tail_recovery":
                variants = list(spec["takeover_milliseconds"])
            else:
                raise BoundaryRepairV6Error(f"Unsupported action: {action}")

            for variant in variants:
                treatment = base_treatment(base)
                suffix = action if variant is None else f"{action}_{variant}"
                repaired_treatment = f"{treatment}__{suffix}"
                wav = private / f"chunk_{spec['chunk_id']}__{repaired_treatment}.wav"
                try:
                    source = base["source"]
                    if action == "start_trim":
                        trim = max(
                            0.0,
                            float(source["transcript_segment_start_seconds"])
                            - float(source["source_start_seconds"])
                            - float(plan["start_preroll_seconds"]),
                        )
                        repair = write_start_trim(
                            Path(base["wav_path"]), wav, trim,
                            int(plan["fade_milliseconds"]),
                            int(plan["appended_silence_milliseconds"]),
                        )
                    elif action == "start_tail_recovery":
                        trim_ms = round(
                            1000.0 * max(
                                0.0,
                                float(source["transcript_segment_start_seconds"])
                                - float(source["source_start_seconds"])
                                - float(plan["start_preroll_seconds"]),
                            )
                        )
                        repair = write_tail_splice(
                            Path(base["wav_path"]), source_wav_for(base), wav,
                            takeover_ms=int(variant),
                            start_trim_ms=trim_ms,
                            fade_ms=int(plan["fade_milliseconds"]),
                            silence_ms=int(plan["appended_silence_milliseconds"]),
                        )
                    else:
                        start = float(source["source_start_seconds"])
                        if action in {"start_micro_tail_extension", "start_end_cleanup"}:
                            start = float(source["transcript_segment_start_seconds"]) - float(plan["start_preroll_seconds"])
                        if action in {"micro_tail_extension", "start_micro_tail_extension"}:
                            end = float(source["transcript_segment_end_seconds"]) + int(variant) / 1000.0
                            variant_receipt = {"extended_past_transcript_milliseconds": int(variant)}
                        else:
                            end = float(source["transcript_segment_end_seconds"]) + float(variant)
                            variant_receipt = {"postroll_seconds": float(variant)}
                        source_cut = private / f"chunk_{spec['chunk_id']}__source_{suffix}.wav"
                        cut(media, start, end, source_cut)
                        process_treatment(treatment, source_cut, wav, mel, moss)
                        silence = append_deterministic_silence(wav, int(plan["appended_silence_milliseconds"]))
                        repair = {"source_start_seconds": start, "source_end_seconds": end, **variant_receipt, **silence}

                    wav_check = objective_check(base, transcribe(wav, whisper))
                    proxy = private / f"chunk_{spec['chunk_id']}__{repaired_treatment}.mp3"
                    encode_proxy(wav, proxy, bitrate=plan["production_proxy"]["bitrate"])
                    proxy_check = objective_check(base, transcribe(proxy, whisper))
                    probe = probe_audio(proxy)
                    if not (
                        objective_eligible(wav_check)
                        and objective_eligible(proxy_check)
                        and probe["codec_name"] == "mp3"
                        and probe["sample_rate"] == 44100
                        and probe["channels"] == 2
                    ):
                        raise BoundaryRepairV6Error("Objective transcript/proxy gate failed")
                    candidates.append(
                        {
                            "treatment": repaired_treatment,
                            "base_candidate_id": base_id,
                            "base_treatment": treatment,
                            "repair_action": action,
                            "repair_receipt": repair,
                            "wav_path": wav,
                            "wav_metrics": metrics(wav),
                            "wav_objective": wav_check,
                            "proxy_path": proxy,
                            "proxy_sha256": sha256_file(proxy),
                            "proxy_probe": probe,
                            "proxy_objective": proxy_check,
                            "objective_eligible": True,
                        }
                    )
                except Exception as exc:
                    omissions.append(
                        {
                            "chunk_id": spec["chunk_id"],
                            "treatment": repaired_treatment,
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:1200],
                        }
                    )
        if candidates:
            first = bases[0][1]
            groups.append(
                {
                    "character": first["character"],
                    "book_speaker": first["book_speaker"],
                    "chunk_id": int(first["chunk_id"]),
                    "transcript": first["transcript"],
                    "source": first["source"],
                    "review_context": (
                        "Final bounded start/end repair. Reject any clipped phoneme, captured next word, "
                        "adjacent speaker, post-line sound, or separator artifact."
                    ),
                    "candidates": candidates,
                }
            )

    review_base.ROUND_ID = ROUND_ID
    review_base.build_review(output, groups)
    after = project_hashes(project)
    if before != after:
        raise BoundaryRepairV6Error("Protected project hashes changed")
    write_json(
        output / "generation-summary.json",
        {
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
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
