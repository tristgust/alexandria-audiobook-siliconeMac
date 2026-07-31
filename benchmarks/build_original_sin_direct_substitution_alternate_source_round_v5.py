#!/usr/bin/env python3
"""Build alternate exact-line sources after repeated onset contamination."""
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
from benchmarks.build_original_sin_overlap_reference_repair_round import (
    precise_source_cut,
    project_hashes,
    re_slug,
    treatment_provenance,
)
from benchmarks.original_sin_overlap_word_alignment import (
    accepted_transcript_check,
    normalized_words,
    transcript_check_eligible,
)


ROUND_ID = "alexandria_original_sin_direct_substitution_alternate_source_v5"
DEFAULT_MEDIA = Path("/Users/tristan/Library/Mobile Documents/3L68KQB4HG~com~readdle~CommonDocuments/Documents/DW7*(RERUN:BF)/7c_divergentTimeline3*(RERUN:BF)/20c_bennyChris&Roz/1_originalSinAdaptation.mp3")
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_direct_substitution_alternate_source_plan_v5.json")


class AlternateSourceError(RuntimeError):
    pass


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


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
    if plan.get("round_id") != ROUND_ID or sum(len(group["treatments"]) for group in plan["groups"]) != 9:
        raise AlternateSourceError("alternate-source plan mismatch")

    chunks = read_json(project / "chunks.json")
    segments = read_json(
        project / "external_workflows/big_finish_overlap_reference_v1/private/transcript.json"
    )["segments"]
    for spec in plan["groups"]:
        chunk = chunks[int(spec["chunk_id"])]
        if (
            chunk.get("speaker") != spec["book_speaker"]
            or normalized_words(chunk.get("text"))
            != normalized_words(spec["expected_transcript"])
        ):
            raise AlternateSourceError(f"chunk binding mismatch: {spec['chunk_id']}")

    output = (
        args.output_root.expanduser().resolve()
        if args.output_root
        else project
        / "external_workflows/big_finish_overlap_reference_v1/direct_substitution_alternate_source_round_v5"
    )
    if output.exists():
        if not args.replace:
            raise AlternateSourceError(f"Output exists: {output}")
        shutil.rmtree(output)

    before = project_hashes(project)
    whisper = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    vocal = MelRoFormer.from_pretrained(
        VOCAL_MODEL,
        config=MelRoFormerConfig.zfturbo_vocals_v1(),
    )
    vocal.eval()
    moss = load_mossformer()
    private = output / "private/audio"
    groups = []

    for spec in plan["groups"]:
        start = int(spec["segment_start"])
        end = int(spec["segment_end"])
        segment_start = float(segments[start]["start"])
        segment_end = float(segments[end]["end"])
        minimum_end = segment_end + float(spec["minimum_segment_end_margin_seconds"])
        maximum_end = float(segments[end + 1]["start"]) - float(
            spec["maximum_next_speaker_margin_seconds"]
        )
        slug = f"chunk_{spec['chunk_id']}_{re_slug(spec['book_speaker'])}"
        source = private / f"{slug}__source.wav"
        alignment = precise_source_cut(
            media=media,
            destination=source,
            broad_path=private / f"{slug}__broad.wav",
            broad_start=max(
                0.0,
                segment_start - float(plan["broad_padding_seconds"]),
            ),
            broad_end=segment_end + float(plan["broad_padding_seconds"]),
            expected=spec["expected_transcript"],
            whisper_model=whisper,
            leading_margin=float(plan["leading_margin_seconds"]),
            trailing_margin=float(plan["trailing_margin_seconds"]),
            minimum_source_end=minimum_end,
            maximum_source_end=maximum_end,
        )

        candidates = []
        for treatment in spec["treatments"]:
            if treatment == "source_mix":
                wav = source
            elif treatment == "mossformer2_source_mix":
                wav = private / f"{slug}__moss.wav"
                enhance(source, wav, moss)
            elif treatment == "mel_roformer_vocal":
                wav = private / f"{slug}__mel.wav"
                separate(vocal, source, wav)
            else:
                raise AlternateSourceError(treatment)

            accepted = [spec["expected_transcript"]]
            wav_check = accepted_transcript_check(
                accepted,
                transcribe(wav, whisper),
            )
            proxy = private / f"{slug}__{treatment}.mp3"
            encode_proxy(wav, proxy, bitrate=plan["production_proxy"]["bitrate"])
            proxy_check = accepted_transcript_check(
                accepted,
                transcribe(proxy, whisper),
            )
            probe = probe_audio(proxy)
            if not (
                transcript_check_eligible(wav_check)
                and transcript_check_eligible(proxy_check)
                and probe["codec_name"] == "mp3"
                and probe["sample_rate"] == 44100
                and probe["channels"] == 2
            ):
                continue
            candidates.append(
                {
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
                }
            )
        if not candidates:
            raise AlternateSourceError(
                f"No eligible candidate: {spec['character']}"
            )
        groups.append(
            {
                "character": spec["character"],
                "book_speaker": spec["book_speaker"],
                "chunk_id": int(spec["chunk_id"]),
                "transcript": spec["expected_transcript"],
                "review_context": spec.get("review_context"),
                "source": {
                    "media_sha256": sha256_file(media),
                    "segment_start": start,
                    "segment_end": end,
                    **alignment,
                },
                "candidates": candidates,
            }
        )
        print(
            f"built chunk {spec['chunk_id']} {spec['character']} "
            f"({len(candidates)} eligible)",
            flush=True,
        )

    review_base.ROUND_ID = ROUND_ID
    review_base.build_review(output, groups)
    after = project_hashes(project)
    if before != after:
        raise AlternateSourceError("protected project hashes changed")
    write_json(
        output / "generation-summary.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "generated_at": utc_now(),
            "chunk_count": len(groups),
            "candidate_count": sum(len(group["candidates"]) for group in groups),
            "protected_project_hashes_before": before,
            "protected_project_hashes_after": after,
            "production_changes": False,
            "output_root": str(output),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
