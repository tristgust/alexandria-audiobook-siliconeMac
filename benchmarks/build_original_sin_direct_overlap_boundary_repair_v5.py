#!/usr/bin/env python3
"""Build start trims and micro-tail extensions for expansion batch 004."""
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
from benchmarks.build_original_sin_direct_overlap_boundary_repair_v3 import write_start_trim
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
from benchmarks.original_sin_overlap_word_alignment import accepted_transcript_check, transcript_check_eligible


ROUND_ID = "alexandria_original_sin_direct_overlap_boundary_repair_v5"
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_MEDIA = Path("/Users/tristan/Library/Mobile Documents/3L68KQB4HG~com~readdle~CommonDocuments/Documents/DW7*(RERUN:BF)/7c_divergentTimeline3*(RERUN:BF)/20c_bennyChris&Roz/1_originalSinAdaptation.mp3")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_direct_overlap_boundary_repair_plan_v5.json")


class BoundaryRepairV5Error(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def process_treatment(treatment: str, source: Path, destination: Path, mel: Any, moss: Any) -> None:
    if treatment == "mel_roformer_vocal":
        separate(mel, source, destination)
    elif treatment == "mossformer2_source_mix":
        enhance(source, destination, moss)
    else:
        raise BoundaryRepairV5Error(f"Unsupported treatment: {treatment}")


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
        raise BoundaryRepairV5Error("Boundary-repair v5 plan mismatch")
    workflow = project / "external_workflows/big_finish_overlap_reference_v1"
    source_answer = read_json(workflow / "direct_overlap_expansion_batch_004/private/answer-key.json")["candidates"]
    output = args.output_root.expanduser().resolve() if args.output_root else workflow / "direct_overlap_boundary_repair_v5"
    if output.exists():
        if not args.replace:
            raise BoundaryRepairV5Error(f"Output exists: {output}")
        shutil.rmtree(output)

    before = project_hashes(project)
    whisper = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    needs_models = any(group["action"] == "micro_tail_extension" for group in plan["groups"])
    mel = moss = None
    if needs_models:
        mel = MelRoFormer.from_pretrained(VOCAL_MODEL, config=MelRoFormerConfig.zfturbo_vocals_v1())
        mel.eval()
        moss = load_mossformer()

    private = output / "private/audio"
    groups: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    for spec in plan["groups"]:
        candidates: list[dict[str, Any]] = []
        bases = [(candidate_id, source_answer[candidate_id]) for candidate_id in spec["candidate_ids"]]
        for base_id, base in bases:
            variants = [None] if spec["action"] == "start_trim" else list(plan["tail_extension_milliseconds"])
            for extension_ms in variants:
                treatment = str(base["treatment"])
                suffix = "start_trim" if extension_ms is None else f"micro_tail_extension_ms{extension_ms}"
                repaired_treatment = f"{treatment}__{suffix}"
                wav = private / f"chunk_{spec['chunk_id']}__{repaired_treatment}.wav"
                try:
                    if extension_ms is None:
                        source = base["source"]
                        trim_seconds = max(
                            0.0,
                            float(source["transcript_segment_start_seconds"])
                            - float(source["source_start_seconds"])
                            - float(plan["start_preroll_seconds"]),
                        )
                        repair = write_start_trim(
                            Path(base["wav_path"]),
                            wav,
                            trim_seconds,
                            int(plan["fade_milliseconds"]),
                            int(plan["appended_silence_milliseconds"]),
                        )
                    else:
                        source = base["source"]
                        source_start = float(source["source_start_seconds"])
                        source_end = float(source["transcript_segment_end_seconds"]) + int(extension_ms) / 1000.0
                        extended_source = private / f"chunk_{spec['chunk_id']}__source_ext_{extension_ms}ms.wav"
                        cut(media, source_start, source_end, extended_source)
                        process_treatment(treatment, extended_source, wav, mel, moss)
                        silence = append_deterministic_silence(wav, int(plan["appended_silence_milliseconds"]))
                        repair = {
                            "source_start_seconds": source_start,
                            "source_end_seconds": source_end,
                            "extended_past_transcript_milliseconds": int(extension_ms),
                            **silence,
                        }

                    wav_check = accepted_transcript_check([base["transcript"]], transcribe(wav, whisper))
                    proxy = private / f"chunk_{spec['chunk_id']}__{repaired_treatment}.mp3"
                    encode_proxy(wav, proxy, bitrate=plan["production_proxy"]["bitrate"])
                    proxy_check = accepted_transcript_check([base["transcript"]], transcribe(proxy, whisper))
                    probe = probe_audio(proxy)
                    eligible = (
                        transcript_check_eligible(wav_check)
                        and transcript_check_eligible(proxy_check)
                        and probe["codec_name"] == "mp3"
                        and probe["sample_rate"] == 44100
                        and probe["channels"] == 2
                    )
                    if not eligible:
                        raise BoundaryRepairV5Error("Objective transcript/proxy gate failed")
                    candidates.append(
                        {
                            "treatment": repaired_treatment,
                            "base_candidate_id": base_id,
                            "base_treatment": treatment,
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
                        "Start trim or micro-tail extension. Reject any clipped final phoneme, "
                        "captured next word, adjacent speaker, post-line sound, or separator artifact."
                    ),
                    "candidates": candidates,
                }
            )

    review_base.ROUND_ID = ROUND_ID
    review_base.build_review(output, groups)
    after = project_hashes(project)
    if before != after:
        raise BoundaryRepairV5Error("Protected project hashes changed")
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
