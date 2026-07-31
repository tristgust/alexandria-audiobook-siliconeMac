#!/usr/bin/env python3
"""Build isolation recheck and trailing trims after expansion batch 006."""
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

from model_registry import resolve_model_path

import benchmarks.build_original_sin_direct_overlap_expansion_batch_002 as review_base
from benchmarks.build_original_sin_direct_overlap_boundary_repair_v3 import write_trailing_trim
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
from benchmarks.original_sin_overlap_word_alignment import (
    accepted_transcript_check,
    transcript_check_eligible,
)


ROUND_ID = "alexandria_original_sin_direct_overlap_boundary_repair_v7"
DEFAULT_PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
DEFAULT_PLAN = Path(__file__).with_name(
    "original_sin_direct_overlap_boundary_repair_plan_v7.json"
)


class BoundaryRepairV7Error(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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
        raise BoundaryRepairV7Error("Boundary-repair v7 plan mismatch")
    workflow = project / "external_workflows/big_finish_overlap_reference_v1"
    source_answer = read_json(
        workflow / "direct_overlap_expansion_batch_006/private/answer-key.json"
    )["candidates"]
    output = (
        args.output_root.expanduser().resolve()
        if args.output_root
        else workflow / "direct_overlap_boundary_repair_v7"
    )
    if output.exists():
        if not args.replace:
            raise BoundaryRepairV7Error(f"Output exists: {output}")
        shutil.rmtree(output)

    before = project_hashes(project)
    whisper = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    private = output / "private/audio"
    private.mkdir(parents=True, exist_ok=True)
    groups: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []

    for spec in plan["groups"]:
        candidates: list[dict[str, Any]] = []
        bases = [(candidate_id, source_answer[candidate_id]) for candidate_id in spec["candidate_ids"]]
        for base_id, base in bases:
            variants = [None] if spec["action"] == "isolation_recheck" else plan["trailing_postroll_seconds"]
            for postroll in variants:
                suffix = (
                    "isolation_recheck"
                    if postroll is None
                    else f"trailing_trim_postroll{postroll}"
                )
                treatment = f"{base['treatment']}__{suffix}"
                wav = private / f"chunk_{spec['chunk_id']}__{treatment}.wav"
                proxy = private / f"chunk_{spec['chunk_id']}__{treatment}.mp3"
                try:
                    if postroll is None:
                        shutil.copy2(base["wav_path"], wav)
                        encode_proxy(wav, proxy, bitrate=plan["production_proxy"]["bitrate"])
                        repair = {"action": "unchanged_audio_isolation_recheck"}
                    else:
                        source = base["source"]
                        keep_seconds = (
                            float(source["transcript_segment_end_seconds"])
                            - float(source["source_start_seconds"])
                            + float(postroll)
                        )
                        repair = write_trailing_trim(
                            Path(base["wav_path"]),
                            wav,
                            keep_seconds,
                            int(plan["fade_milliseconds"]),
                            int(plan["appended_silence_milliseconds"]),
                        )
                        repair["requested_postroll_seconds"] = float(postroll)
                        encode_proxy(wav, proxy, bitrate=plan["production_proxy"]["bitrate"])

                    wav_check = accepted_transcript_check(
                        [base["transcript"]], transcribe(wav, whisper)
                    )
                    proxy_check = accepted_transcript_check(
                        [base["transcript"]], transcribe(proxy, whisper)
                    )
                    probe = probe_audio(proxy)
                    if not (
                        transcript_check_eligible(wav_check)
                        and transcript_check_eligible(proxy_check)
                        and probe["codec_name"] == "mp3"
                        and probe["sample_rate"] == 44100
                        and probe["channels"] == 2
                    ):
                        raise BoundaryRepairV7Error(
                            "Objective transcript/proxy gate failed"
                        )
                    candidates.append(
                        {
                            "treatment": treatment,
                            "base_candidate_id": base_id,
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
                        }
                    )
                except Exception as exc:
                    omissions.append(
                        {
                            "chunk_id": spec["chunk_id"],
                            "treatment": treatment,
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:1200],
                        }
                    )
        if candidates:
            first = bases[0][1]
            context = (
                "Unchanged audio: explicitly score intended-voice isolation."
                if spec["action"] == "isolation_recheck"
                else "Tighter trailing trim. Reject clipped final phonemes or any remaining post-line sound."
            )
            if int(first["chunk_id"]) == 973:
                context += " Also judge whether the clean result is suitable for Roz's wider reference bank."
            groups.append(
                {
                    "character": first["character"],
                    "book_speaker": first["book_speaker"],
                    "chunk_id": int(first["chunk_id"]),
                    "transcript": first["transcript"],
                    "source": first["source"],
                    "review_context": context,
                    "candidates": candidates,
                }
            )

    review_base.ROUND_ID = ROUND_ID
    review_base.build_review(output, groups)
    after = project_hashes(project)
    if before != after:
        raise BoundaryRepairV7Error("Protected project hashes changed")
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
