#!/usr/bin/env python3
"""Build the last boundary-only exact-line repair round."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf
from mlx_audio.sts.models.mel_roformer import MelRoFormer, MelRoFormerConfig
from model_registry import resolve_model_path

from benchmarks.build_original_sin_direct_substitution_round import encode_proxy, probe_audio
from benchmarks.build_original_sin_direct_substitution_final_repair_round import REVIEW_HTML
from benchmarks.build_original_sin_overlap_reference_round import (
    VOCAL_MODEL, WHISPER_MODEL_KEY, enhance, load_mossformer, metrics,
    separate, sha256_file, transcribe, utc_now, write_json,
)
from benchmarks.build_original_sin_overlap_reference_repair_round import (
    precise_source_cut, project_hashes, re_slug, treatment_provenance,
)
from benchmarks.original_sin_overlap_word_alignment import (
    accepted_transcript_check, normalized_words, transcript_check_eligible,
)

ROUND_ID = "alexandria_original_sin_direct_substitution_boundary_repair_v4"
SEED = 20260731
DEFAULT_MEDIA = Path("/Users/tristan/Library/Mobile Documents/3L68KQB4HG~com~readdle~CommonDocuments/Documents/DW7*(RERUN:BF)/7c_divergentTimeline3*(RERUN:BF)/20c_bennyChris&Roz/1_originalSinAdaptation.mp3")
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_direct_substitution_boundary_repair_plan_v4.json")


class DirectBoundaryError(RuntimeError):
    pass


def read_json(path: Path): return json.loads(path.read_text(encoding="utf-8"))


def fade_in(path: Path, milliseconds: int) -> None:
    if milliseconds <= 0: return
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    frames = min(len(audio), max(1, round(rate * milliseconds / 1000)))
    audio[:frames] *= np.linspace(0.0, 1.0, frames, dtype=np.float32)[:, None]
    sf.write(str(path), audio, rate, subtype="PCM_16")


def build_review(output: Path, groups: list[dict]) -> None:
    review, audio = output / "review", output / "review/audio"
    audio.mkdir(parents=True, exist_ok=True)
    rng, answer, public_groups = random.Random(SEED), {}, []
    for group in groups:
        candidates = list(group["candidates"]); rng.shuffle(candidates)
        public = []
        for candidate in candidates:
            cid = hashlib.sha256(
                f"{ROUND_ID}:{group['chunk_id']}:{candidate['treatment']}:{candidate['proxy_sha256']}".encode()
            ).hexdigest()[:16]
            shutil.copy2(candidate["proxy_path"], audio / f"{cid}.mp3")
            public.append({"id": cid, "audio": f"audio/{cid}.mp3"})
            answer[cid] = {
                **candidate, "wav_path": str(candidate["wav_path"]),
                "proxy_path": str(candidate["proxy_path"]), "character": group["character"],
                "book_speaker": group["book_speaker"], "chunk_id": group["chunk_id"],
                "transcript": group["transcript"], "source": group["source"],
            }
        public_groups.append({"character":group["character"],"chunk_id":group["chunk_id"],"transcript":group["transcript"],"review_context":group.get("review_context"),"candidates":public})
    write_json(output / "private/answer-key.json", {"schema_version":1,"round_id":ROUND_ID,"candidates":answer,"production_changes":False})
    (review / "data.js").write_text(
        "window.ORIGINAL_SIN_FINAL_DIRECT_REPAIR = " + json.dumps({"round_id":ROUND_ID,"groups":public_groups}, indent=2, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    (review / "index.html").write_text(REVIEW_HTML, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--media", type=Path, default=DEFAULT_MEDIA)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    project, media = args.project_root.expanduser().resolve(), args.media.expanduser().resolve()
    plan = read_json(args.plan.expanduser().resolve())
    if plan.get("round_id") != ROUND_ID or sum(len(g["treatments"]) for g in plan["groups"]) != 7:
        raise DirectBoundaryError("direct boundary plan mismatch")
    chunks = read_json(project / "chunks.json")
    segments = read_json(project / "external_workflows/big_finish_overlap_reference_v1/private/transcript.json")["segments"]
    for spec in plan["groups"]:
        chunk = chunks[int(spec["chunk_id"])]
        if chunk.get("speaker") != spec["book_speaker"] or normalized_words(chunk.get("text")) != normalized_words(spec["expected_transcript"]):
            raise DirectBoundaryError(f"chunk binding mismatch: {spec['chunk_id']}")
    output = args.output_root.expanduser().resolve() if args.output_root else project / "external_workflows/big_finish_overlap_reference_v1/direct_substitution_boundary_repair_round_v4"
    if output.exists():
        if not args.replace: raise DirectBoundaryError(f"Output exists: {output}")
        shutil.rmtree(output)
    before = project_hashes(project)
    whisper = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    vocal = MelRoFormer.from_pretrained(VOCAL_MODEL, config=MelRoFormerConfig.zfturbo_vocals_v1()); vocal.eval()
    moss, private, groups = load_mossformer(), output / "private/audio", []
    for spec in plan["groups"]:
        start, end = int(spec["segment_start"]), int(spec["segment_end"])
        segment_start, segment_end = float(segments[start]["start"]), float(segments[end]["end"])
        maximum_end = float(segments[end+1]["start"]) - float(spec["maximum_next_speaker_margin_seconds"])
        minimum_end = segment_end + float(spec["minimum_segment_end_margin_seconds"])
        slug, source = f"chunk_{spec['chunk_id']}_{re_slug(spec['book_speaker'])}", private / f"chunk_{spec['chunk_id']}__source.wav"
        alignment = precise_source_cut(
            media=media, destination=source, broad_path=private / f"{slug}__broad.wav",
            broad_start=max(0.0, segment_start - float(plan["broad_padding_seconds"])),
            broad_end=segment_end + float(plan["broad_padding_seconds"]),
            expected=spec["expected_transcript"], whisper_model=whisper,
            leading_margin=float(plan["leading_margin_seconds"]), trailing_margin=float(plan["trailing_margin_seconds"]),
            minimum_source_end=minimum_end, maximum_source_end=maximum_end,
        )
        candidates = []
        for treatment in spec["treatments"]:
            if treatment == "source_mix": wav = source
            elif treatment == "mel_roformer_vocal": wav = private / f"{slug}__mel.wav"; separate(vocal, source, wav)
            elif treatment == "mossformer2_source_mix": wav = private / f"{slug}__moss.wav"; enhance(source, wav, moss)
            else: raise DirectBoundaryError(treatment)
            fade_in(wav, int(spec.get("fade_in_milliseconds", 0)))
            accepted = [spec["expected_transcript"]]
            wav_check = accepted_transcript_check(accepted, transcribe(wav, whisper))
            proxy = private / f"{slug}__{treatment}.mp3"
            encode_proxy(wav, proxy, bitrate=plan["production_proxy"]["bitrate"])
            proxy_check = accepted_transcript_check(accepted, transcribe(proxy, whisper))
            probe = probe_audio(proxy)
            if not (transcript_check_eligible(wav_check) and transcript_check_eligible(proxy_check) and probe["codec_name"] == "mp3" and probe["sample_rate"] == 44100 and probe["channels"] == 2):
                continue
            candidates.append({"treatment":treatment,"wav_path":wav,"wav_metrics":metrics(wav),"wav_objective":wav_check,"proxy_path":proxy,"proxy_sha256":sha256_file(proxy),"proxy_probe":probe,"proxy_objective":proxy_check,"objective_eligible":True,**treatment_provenance(treatment)})
        if not candidates: raise DirectBoundaryError(f"No eligible candidate: {spec['character']}")
        groups.append({"character":spec["character"],"book_speaker":spec["book_speaker"],"chunk_id":int(spec["chunk_id"]),"transcript":spec["expected_transcript"],"source":{"media_sha256":sha256_file(media),"segment_start":start,"segment_end":end,**alignment},"candidates":candidates})
        print(f"built chunk {spec['chunk_id']} {spec['character']} ({len(candidates)} eligible)", flush=True)
    build_review(output, groups)
    after = project_hashes(project)
    if before != after: raise DirectBoundaryError("protected project hashes changed")
    write_json(output / "generation-summary.json", {"schema_version":1,"round_id":ROUND_ID,"generated_at":utc_now(),"chunk_count":len(groups),"candidate_count":sum(len(g["candidates"]) for g in groups),"protected_project_hashes_before":before,"protected_project_hashes_after":after,"production_changes":False,"output_root":str(output)})
    return 0


if __name__ == "__main__": raise SystemExit(main())
