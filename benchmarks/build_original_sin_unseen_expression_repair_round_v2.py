#!/usr/bin/env python3
"""Repair unseen-line modes using longer current identity references."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import benchmarks.build_original_sin_unseen_expression_round as base


ROUND_ID = "alexandria_original_sin_unseen_expression_repair_v2"
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_unseen_expression_repair_plan_v2.json")


class ExpressionRepairError(RuntimeError):
    pass


def current_identity_anchor(control: dict[str, Any]) -> dict[str, Any]:
    voice = control["voice"]
    raw = Path(str(voice.get("ref_audio") or ""))
    path = raw if raw.is_absolute() else (control["root"] / raw).resolve()
    text = str(voice.get("ref_text") or "").strip()
    if not path.is_file() or not text:
        raise ExpressionRepairError(
            f"Current identity is incomplete: {control['voice_key']}"
        )
    return {
        "path": path,
        "text": text,
        "sha256": base.sha256_file(path),
        "candidate_id": f"current_identity:{control['voice_key']}",
        "round_id": "current_alexandria_identity",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    project = args.project_root.expanduser().resolve()
    plan = base.read_json(args.plan.expanduser().resolve())
    expected = sum(len(group["routes"]) for group in plan["groups"])
    if plan.get("round_id") != ROUND_ID or expected != 17:
        raise ExpressionRepairError("expression-repair plan mismatch")

    output = (
        args.output_root.expanduser().resolve()
        if args.output_root
        else project
        / "external_workflows/big_finish_overlap_reference_v1/unseen_expression_repair_round_v2"
    )
    if output.exists():
        if not args.replace:
            raise ExpressionRepairError(f"Output exists: {output}")
        shutil.rmtree(output)

    before = base.project_hashes(project)
    chunks = base.read_json(project / "chunks.json")
    transcript = base.read_json(
        project / "external_workflows/big_finish_overlap_reference_v1/private/transcript.json"
    )["segments"]
    full_adaptation = " ".join(
        base.normalized_words(
            " ".join(str(row.get("text") or "") for row in transcript)
        )
    )
    for spec in plan["groups"]:
        chunk = chunks[int(spec["chunk_id"])]
        if (
            chunk.get("speaker") != spec["book_speaker"]
            or base.normalized_words(chunk.get("text"))
            != base.normalized_words(spec["text"])
        ):
            raise ExpressionRepairError(f"Chunk binding mismatch: {spec['group']}")
        if " ".join(base.normalized_words(spec["text"])) in full_adaptation:
            raise ExpressionRepairError(f"Line occurs in adaptation: {spec['group']}")

    whisper = str(
        base.resolve_model_path(base.WHISPER_MODEL_KEY, local_files_only=True)
    )
    qwen = base.TTSEngine(
        {"tts": {"mode": "local", "language": "English", "device": "auto"}}
    )
    responsive = base.ResponsiveVoiceBackend()
    private = output / "private/audio"
    private.mkdir(parents=True, exist_ok=True)
    controls: dict[str, dict[str, Any]] = {}
    groups: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    base.ROUND_ID = ROUND_ID

    try:
        for spec in plan["groups"]:
            control = base.prepare_current_control(
                project=project,
                output=output,
                book_speaker=spec["book_speaker"],
                cache=controls,
            )
            anchor = current_identity_anchor(control)
            candidates: list[dict[str, Any]] = []
            for route_spec in spec["routes"]:
                route_key = str(route_spec["key"])
                route_kind = str(route_spec["kind"])
                route_instruction = str(
                    route_spec.get("instruction") or spec["instruction"]
                )
                if route_spec.get("prompt_route"):
                    route_instruction = (
                        f"[prompt-route:{route_spec['prompt_route']}] "
                        + spec["instruction"]
                    )
                candidate_id = base.candidate_id(
                    spec["group"],
                    route_key,
                    anchor["sha256"],
                )
                wav = private / f"{candidate_id}.wav"
                route_meta: dict[str, Any] = {
                    "route_key": route_key,
                    "requested_backend": route_kind,
                    "actual_backend": None,
                    "fallback_used": False,
                }
                receipt: dict[str, Any] = {}
                try:
                    if route_kind == "qwen_identity":
                        config = {
                            spec["book_speaker"]: base.qwen_voice(
                                anchor,
                                int(plan["seed"]),
                            )
                        }
                        if not qwen.generate_voice(
                            spec["text"],
                            route_instruction,
                            spec["book_speaker"],
                            config,
                            str(wav),
                        ):
                            raise ExpressionRepairError(
                                "Qwen current-identity generation returned false"
                            )
                        route_meta["actual_backend"] = (
                            "qwen3_instruction_controlled"
                        )
                    elif route_kind == "current_route":
                        route_meta.update(
                            base.current_route_metadata(
                                control,
                                route_instruction,
                            )
                        )
                        current_wav = control["root"] / f"{candidate_id}.wav"
                        config = {control["voice_key"]: control["voice"]}
                        if not qwen.generate_voice(
                            spec["text"],
                            route_instruction,
                            control["voice_key"],
                            config,
                            str(current_wav),
                        ):
                            raise ExpressionRepairError(
                                "Current routed generation returned false"
                            )
                        shutil.copy2(current_wav, wav)
                    elif route_kind == "vox_identity":
                        route = {
                            "backend": "voxcpm2_controllable_clone",
                            "identity_audio_path": str(anchor["path"]),
                            "identity_text": anchor["text"],
                            "control": {
                                "instruction": route_instruction,
                                "cfg_value": 2.0,
                                "inference_timesteps": 10,
                                "warmup_patches": 0,
                                "max_tokens": 1800,
                            },
                        }
                        receipt = responsive.generate(
                            route=route,
                            text=spec["text"],
                            output_path=wav,
                            seed=int(plan["seed"]),
                        )
                        route_meta["actual_backend"] = (
                            "voxcpm2_controllable_clone"
                        )
                    elif route_kind == "fish_identity":
                        receipt = base.fish_inline_generate(
                            anchor=anchor,
                            text=spec["text"],
                            instruction=route_instruction,
                            output=wav,
                        )
                        route_meta["actual_backend"] = (
                            "fish_s2.1_pro_free_inline_zero_shot"
                        )
                    else:
                        raise ExpressionRepairError(
                            f"Unknown route kind: {route_kind}"
                        )

                    if not wav.is_file():
                        raise ExpressionRepairError("No WAV generated")
                    source_check = base.verify_audio(
                        wav,
                        spec["text"],
                        whisper,
                    )
                    if (
                        source_check["word_error_rate"] != 0.0
                        or not source_check["first_word_present"]
                        or not source_check["last_word_present"]
                    ):
                        raise ExpressionRepairError(
                            f"Source transcript gate failed: {source_check}"
                        )
                    proxy = private / f"{candidate_id}.mp3"
                    base.encode_proxy(wav, proxy, bitrate="192k")
                    proxy_check = base.verify_audio(
                        proxy,
                        spec["text"],
                        whisper,
                    )
                    probe = base.probe_audio(proxy)
                    if (
                        proxy_check["word_error_rate"] != 0.0
                        or not proxy_check["first_word_present"]
                        or not proxy_check["last_word_present"]
                        or probe["codec_name"] != "mp3"
                        or probe["sample_rate"] != 44100
                        or probe["channels"] != 2
                    ):
                        raise ExpressionRepairError(
                            "Production proxy gate failed"
                        )
                    candidates.append(
                        {
                            "candidate_id": candidate_id,
                            **route_meta,
                            "route_instruction": route_instruction,
                            "receipt": receipt,
                            "wav_path": wav,
                            "wav_metrics": base.metrics(wav),
                            "source_objective": source_check,
                            "proxy_path": proxy,
                            "proxy_sha256": base.sha256_file(proxy),
                            "proxy_probe": probe,
                            "proxy_objective": proxy_check,
                        }
                    )
                except Exception as exc:
                    omissions.append(
                        {
                            "group": spec["group"],
                            "route": route_key,
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:2000],
                        }
                    )
                    wav.unlink(missing_ok=True)
            if len(candidates) < 2:
                raise ExpressionRepairError(
                    f"Fewer than two eligible candidates for {spec['group']}: "
                    f"{omissions[-5:]}"
                )
            groups.append(
                {
                    "group": spec["group"],
                    "character": spec["character"],
                    "book_speaker": spec["book_speaker"],
                    "chunk_id": int(spec["chunk_id"]),
                    "mode": spec["mode"],
                    "text": spec["text"],
                    "instruction": spec["instruction"],
                    "anchor": {**anchor, "path": str(anchor["path"])},
                    "candidates": candidates,
                }
            )
            print(
                f"built {spec['group']} ({len(candidates)} eligible)",
                flush=True,
            )
    finally:
        responsive.close()

    base.build_review(output, groups)
    after = base.project_hashes(project)
    if before != after:
        raise ExpressionRepairError("Protected project hashes changed")
    base.write_json(
        output / "generation-summary.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "generated_at": base.utc_now(),
            "planned_candidate_count": plan["candidate_count"],
            "group_count": len(groups),
            "candidate_count": sum(
                len(group["candidates"]) for group in groups
            ),
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
