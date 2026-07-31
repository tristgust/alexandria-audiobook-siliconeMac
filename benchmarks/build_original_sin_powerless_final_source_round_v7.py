#!/usr/bin/env python3
"""Build the final bounded Powerless exact-line source round."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from mlx_audio.sts.models.mel_roformer import MelRoFormer, MelRoFormerConfig
from model_registry import resolve_model_path

import benchmarks.build_original_sin_direct_substitution_boundary_repair_round_v4 as review_base
from benchmarks.build_original_sin_direct_substitution_round import encode_proxy, probe_audio
from benchmarks.build_original_sin_overlap_reference_round import (
    VOCAL_MODEL,
    WHISPER_MODEL_KEY,
    enhance,
    load_mossformer,
    metrics,
    separate,
    sha256_file,
    transcribe,
    utc_now,
    write_json,
)
from benchmarks.build_original_sin_overlap_reference_repair_round import precise_source_cut, project_hashes, treatment_provenance
from benchmarks.original_sin_overlap_word_alignment import accepted_transcript_check, normalized_words, transcript_check_eligible


ROUND_ID = "alexandria_original_sin_powerless_final_source_v7"
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_MEDIA = Path("/Users/tristan/Library/Mobile Documents/3L68KQB4HG~com~readdle~CommonDocuments/Documents/DW7*(RERUN:BF)/7c_divergentTimeline3*(RERUN:BF)/20c_bennyChris&Roz/1_originalSinAdaptation.mp3")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_powerless_final_source_plan_v7.json")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--media", type=Path, default=DEFAULT_MEDIA)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    project = args.project_root.expanduser().resolve()
    media = args.media.expanduser().resolve()
    plan = read_json(args.plan.expanduser().resolve())
    if plan.get("round_id") != ROUND_ID or len(plan.get("treatments") or []) != 3:
        raise RuntimeError("Powerless final plan mismatch")
    chunks = read_json(project / "chunks.json")
    chunk = chunks[int(plan["chunk_id"])]
    if chunk.get("speaker") != plan["book_speaker"] or normalized_words(chunk.get("text")) != normalized_words(plan["expected_transcript"]):
        raise RuntimeError("Powerless chunk binding mismatch")
    output = project / "external_workflows/big_finish_overlap_reference_v1/powerless_final_source_round_v7"
    if output.exists():
        if not args.replace:
            raise RuntimeError(f"Output exists: {output}")
        shutil.rmtree(output)
    before = project_hashes(project)
    segments = read_json(project / "external_workflows/big_finish_overlap_reference_v1/private/transcript.json")["segments"]
    index = int(plan["segment_start"])
    segment = segments[index]
    maximum_end = float(segments[index + 1]["start"]) - float(plan["maximum_next_speaker_margin_seconds"])
    whisper = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    vocal = MelRoFormer.from_pretrained(VOCAL_MODEL, config=MelRoFormerConfig.zfturbo_vocals_v1()); vocal.eval()
    moss = load_mossformer()
    private = output / "private/audio"
    source = private / "powerless__source.wav"
    alignment = precise_source_cut(
        media=media,
        destination=source,
        broad_path=private / "powerless__broad.wav",
        broad_start=max(0.0, float(segment["start"]) - 0.30),
        broad_end=float(segment["end"]) + 0.20,
        expected=plan["expected_transcript"],
        whisper_model=whisper,
        leading_margin=float(plan["leading_margin_seconds"]),
        trailing_margin=float(plan["trailing_margin_seconds"]),
        word_aliases=plan.get("alignment_word_aliases", {}),
        maximum_source_end=maximum_end,
    )
    candidates = []
    for treatment in plan["treatments"]:
        if treatment == "source_mix":
            wav = source
        elif treatment == "mossformer2_source_mix":
            wav = private / "powerless__moss.wav"; enhance(source, wav, moss)
        elif treatment == "mel_roformer_vocal":
            wav = private / "powerless__mel.wav"; separate(vocal, source, wav)
        else:
            raise RuntimeError(treatment)
        wav_check = accepted_transcript_check([plan["expected_transcript"]], transcribe(wav, whisper))
        proxy = private / f"powerless__{treatment}.mp3"
        encode_proxy(wav, proxy, bitrate=plan["production_proxy"]["bitrate"])
        proxy_check = accepted_transcript_check([plan["expected_transcript"]], transcribe(proxy, whisper))
        probe = probe_audio(proxy)
        if not (
            transcript_check_eligible(wav_check)
            and transcript_check_eligible(proxy_check)
            and probe["codec_name"] == "mp3"
            and probe["sample_rate"] == 44100
            and probe["channels"] == 2
        ):
            continue
        candidates.append({
            "treatment": treatment,
            "wav_path": wav,
            "wav_metrics": metrics(wav),
            "wav_objective": wav_check,
            "proxy_path": proxy,
            "proxy_sha256": sha256_file(proxy),
            "proxy_probe": probe,
            "proxy_objective": proxy_check,
            "objective_eligible": True,
            **treatment_provenance(treatment),
        })
    if not candidates:
        raise RuntimeError("No Powerless candidates survived")
    groups = [{
        "character": plan["character"],
        "book_speaker": plan["book_speaker"],
        "chunk_id": int(plan["chunk_id"]),
        "transcript": plan["expected_transcript"],
        "review_context": "Final source attempt. Reject any clipped exclamation, adjacent speech, scene effects, or extraction damage.",
        "source": {"media_sha256": sha256_file(media), "segment_start": index, "segment_end": index, **alignment},
        "candidates": candidates,
    }]
    review_base.ROUND_ID = ROUND_ID
    review_base.build_review(output, groups)
    after = project_hashes(project)
    if before != after:
        raise RuntimeError("Protected project hashes changed")
    write_json(output / "generation-summary.json", {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "generated_at": utc_now(),
        "candidate_count": len(candidates),
        "protected_project_hashes_before": before,
        "protected_project_hashes_after": after,
        "production_changes": False,
        "output_root": str(output),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
