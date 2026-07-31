#!/usr/bin/env python3
"""Generate unseen Chris urgency using a real urgent adaptation performance."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from mlx_audio.sts.models.mel_roformer import MelRoFormer, MelRoFormerConfig
from model_registry import resolve_model_path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from responsive_voice_backend import ResponsiveVoiceBackend
from tts import TTSEngine

import benchmarks.build_original_sin_unseen_expression_round as expression_base
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
from benchmarks.build_original_sin_overlap_reference_repair_round import precise_source_cut, project_hashes
from benchmarks.original_sin_overlap_word_alignment import normalized_words, transcript_comparison


ROUND_ID = "alexandria_original_sin_chris_urgent_performance_v3"
DEFAULT_MEDIA = Path("/Users/tristan/Library/Mobile Documents/3L68KQB4HG~com~readdle~CommonDocuments/Documents/DW7*(RERUN:BF)/7c_divergentTimeline3*(RERUN:BF)/20c_bennyChris&Roz/1_originalSinAdaptation.mp3")
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_chris_urgent_performance_plan_v3.json")


class ChrisUrgentError(RuntimeError):
    pass


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify(path: Path, accepted: list[str], whisper: str) -> dict[str, Any]:
    observed = transcribe(path, whisper)
    comparison = transcript_comparison(accepted, observed, {})
    return {"automatic_transcript": observed, **comparison}


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
    if plan.get("round_id") != ROUND_ID or len(plan["adaptation_performance"]["treatments"]) * len(plan["routes_per_reference"]) != 6:
        raise ChrisUrgentError("Chris urgency plan mismatch")
    chunks = read_json(project / "chunks.json")
    chunk = chunks[int(plan["chunk_id"])]
    if chunk.get("speaker") != plan["book_speaker"] or normalized_words(chunk.get("text")) != normalized_words(plan["text"]):
        raise ChrisUrgentError("target chunk binding mismatch")
    segments = read_json(project / "external_workflows/big_finish_overlap_reference_v1/private/transcript.json")["segments"]
    source_spec = plan["adaptation_performance"]
    source_index = int(source_spec["segment_start"])
    if normalized_words(segments[source_index].get("text")) != normalized_words(source_spec["transcript"]):
        raise ChrisUrgentError("urgent adaptation performance transcript mismatch")
    output = args.output_root.expanduser().resolve() if args.output_root else project / "external_workflows/big_finish_overlap_reference_v1/chris_urgent_performance_round_v3"
    if output.exists():
        if not args.replace:
            raise ChrisUrgentError(f"Output exists: {output}")
        shutil.rmtree(output)
    before = project_hashes(project)
    whisper = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    vocal = MelRoFormer.from_pretrained(VOCAL_MODEL, config=MelRoFormerConfig.zfturbo_vocals_v1())
    vocal.eval()
    moss = load_mossformer()
    private = output / "private/audio"
    segment_start = float(segments[source_index]["start"])
    segment_end = float(segments[source_index]["end"])
    source = private / "chris_urgent_source.wav"
    alignment = precise_source_cut(
        media=media,
        destination=source,
        broad_path=private / "chris_urgent_broad.wav",
        broad_start=max(0.0, segment_start - 0.20),
        broad_end=segment_end + 0.30,
        expected=source_spec["transcript"],
        whisper_model=whisper,
        leading_margin=0.0,
        trailing_margin=0.12,
        minimum_source_start=segment_start,
        maximum_source_end=float(segments[source_index + 1]["start"]) - 0.20,
    )
    references = []
    for treatment in source_spec["treatments"]:
        if treatment == "mossformer2_source_mix":
            path = private / "chris_urgent_moss.wav"
            enhance(source, path, moss)
        elif treatment == "mel_roformer_vocal":
            path = private / "chris_urgent_mel.wav"
            separate(vocal, source, path)
        else:
            raise ChrisUrgentError(treatment)
        source_check = verify(
            path,
            [source_spec["transcript"], *source_spec.get("recognizer_transcripts", [])],
            whisper,
        )
        if source_check["word_error_rate"] != 0.0 or not source_check["first_word_present"] or not source_check["last_word_present"]:
            raise ChrisUrgentError(f"urgent reference transcript failed: {treatment}: {source_check}")
        references.append({
            "treatment": treatment,
            "path": path,
            "text": source_spec["transcript"],
            "sha256": sha256_file(path),
            "source_objective": source_check,
        })
    accepted = [plan["text"], *plan.get("accepted_recognizer_transcripts", [])]
    qwen = TTSEngine({"tts": {"mode": "local", "language": "English", "device": "auto"}})
    responsive = ResponsiveVoiceBackend()
    candidates = []
    omissions = []
    try:
        for reference in references:
            anchor = {
                "path": reference["path"], "text": reference["text"],
                "sha256": reference["sha256"], "candidate_id": reference["treatment"],
                "round_id": ROUND_ID,
            }
            for route_name in plan["routes_per_reference"]:
                route_key = f"{route_name}_{reference['treatment']}"
                cid = expression_base.candidate_id("chris_urgent_authority_performance", route_key, reference["sha256"])
                wav = private / f"{cid}.wav"
                try:
                    receipt = {}
                    if route_name == "qwen":
                        config = {plan["book_speaker"]: expression_base.qwen_voice(anchor, int(plan["seed"]))}
                        ok = qwen.generate_voice(plan["text"], plan["instruction"], plan["book_speaker"], config, str(wav))
                        if not ok:
                            raise ChrisUrgentError("Qwen returned false")
                        actual_backend = "qwen3_instruction_controlled"
                    elif route_name == "voxcpm2":
                        route = {
                            "backend": "voxcpm2_controllable_clone",
                            "identity_audio_path": str(reference["path"]),
                            "identity_text": reference["text"],
                            "control": {"instruction": plan["instruction"], "cfg_value": 2.0, "inference_timesteps": 10, "warmup_patches": 0, "max_tokens": 1800},
                        }
                        receipt = responsive.generate(route=route, text=plan["text"], output_path=wav, seed=int(plan["seed"]))
                        actual_backend = "voxcpm2_controllable_clone"
                    elif route_name == "fish_s2.1_pro_free":
                        receipt = expression_base.fish_inline_generate(anchor=anchor, text=plan["text"], instruction=plan["instruction"], output=wav)
                        actual_backend = "fish_s2.1_pro_free_inline_zero_shot"
                    else:
                        raise ChrisUrgentError(route_name)
                    source_check = verify(wav, accepted, whisper)
                    if source_check["word_error_rate"] != 0.0 or not source_check["first_word_present"] or not source_check["last_word_present"]:
                        raise ChrisUrgentError(f"source transcript failed: {source_check}")
                    proxy = private / f"{cid}.mp3"
                    encode_proxy(wav, proxy, bitrate="192k")
                    proxy_check = verify(proxy, accepted, whisper)
                    probe = probe_audio(proxy)
                    if proxy_check["word_error_rate"] != 0.0 or not proxy_check["first_word_present"] or not proxy_check["last_word_present"] or probe["codec_name"] != "mp3" or probe["sample_rate"] != 44100 or probe["channels"] != 2:
                        raise ChrisUrgentError("production proxy gate failed")
                    candidates.append({
                        "candidate_id": cid,
                        "route_key": route_key,
                        "requested_backend": route_name,
                        "actual_backend": actual_backend,
                        "fallback_used": False,
                        "reference_treatment": reference["treatment"],
                        "reference_audio_sha256": reference["sha256"],
                        "receipt": receipt,
                        "wav_path": wav,
                        "wav_metrics": metrics(wav),
                        "source_objective": source_check,
                        "proxy_path": proxy,
                        "proxy_sha256": sha256_file(proxy),
                        "proxy_probe": probe,
                        "proxy_objective": proxy_check,
                    })
                except Exception as exc:
                    omissions.append({"route": route_key, "error_type": type(exc).__name__, "error": str(exc)[:2000]})
                    wav.unlink(missing_ok=True)
    finally:
        responsive.close()
    if len(candidates) < 2:
        raise ChrisUrgentError(f"Fewer than two eligible candidates: {omissions}")
    expression_base.ROUND_ID = ROUND_ID
    expression_base.build_review(output, [{
        "group": "chris_urgent_authority_performance",
        "character": plan["character"], "book_speaker": plan["book_speaker"],
        "chunk_id": int(plan["chunk_id"]), "mode": plan["mode"],
        "text": plan["text"], "instruction": plan["instruction"],
        "anchor": {"source": "real urgent adaptation performance", "alignment": alignment, "references": [{**row, "path": str(row["path"])} for row in references]},
        "candidates": candidates,
    }])
    after = project_hashes(project)
    if before != after:
        raise ChrisUrgentError("protected project hashes changed")
    write_json(output / "generation-summary.json", {
        "schema_version": 1, "round_id": ROUND_ID, "generated_at": utc_now(),
        "planned_candidate_count": 6, "candidate_count": len(candidates), "omissions": omissions,
        "protected_project_hashes_before": before, "protected_project_hashes_after": after,
        "production_changes": False, "output_root": str(output),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
