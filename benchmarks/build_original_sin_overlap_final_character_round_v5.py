#!/usr/bin/env python3
"""Build the final three-character blind generation round for Original Sin."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
BENCHMARKS = ROOT / "benchmarks"
for value in (APP, BENCHMARKS):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

import build_original_sin_overlap_character_coverage_round_v3 as coverage_v3  # noqa: E402
from build_original_sin_noncore_quasi_emotive_round_v1 import (  # noqa: E402
    current_identity_reference,
    normalized_words,
    read_json,
    sha256_file,
    write_json,
)
from build_original_sin_overlap_character_repairs_round_v4 import route_reference  # noqa: E402
from responsive_voice_backend import ResponsiveVoiceBackend  # noqa: E402
from tts import TTSEngine  # noqa: E402


ROUND_ID = "alexandria_original_sin_overlap_final_character_round_v5"
PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
CONFIG = Path("/Users/tristan/pinokio/api/alexandria-audiobook.git/config.json")
OUTPUT = PROJECT / "external_workflows/big_finish_overlap_reference_v1/overlap_final_character_round_v5"
SALVAGE_ANSWER = PROJECT / "external_workflows/big_finish_overlap_reference_v1/overlap_identity_salvage_round_v6/private/answer-key.json"
INDEXTTS2_ROOT = Path("/Users/tristan/pinokio/cache/alexandria-evaluation/indextts2")
PRIMARY_SEED = 130363
RETRY_SEED = 130464
QWEN = coverage_v3.QWEN
VOX = coverage_v3.VOX
FISH = coverage_v3.FISH
INDEX = coverage_v3.INDEX


MODE_SPECS = (
    {
        "mode_id": "doctor_sudden_realization_final",
        "character": "The Doctor",
        "book_speaker": "DOCTOR",
        "voice_key": "THE DOCTOR",
        "target_chunk_ids": [362],
        "title": "The Doctor — sudden realization",
        "review_instruction": "A delighted, urgent intellectual breakthrough with the exact Doctor identity and no generic shouting.",
        "qwen_routes": [
            "ordinary_identity",
            "doctor_indomitable_determination",
            "approved_adaptation_17769426b8ffb17a",
        ],
        "specialists": [
            {"backend": VOX, "route": "ordinary_identity"},
            {"backend": FISH, "route": "ordinary_identity"},
            {"backend": INDEX, "route": "doctor_indomitable_determination"},
            {"backend": INDEX, "route": "approved_adaptation_17769426b8ffb17a"},
        ],
    },
    {
        "mode_id": "shythe_crisis_broadcast",
        "character": "Shythe Shahid",
        "book_speaker": "SHYTHE SHAHID",
        "voice_key": "SHYTHE SHAHID",
        "target_chunk_ids": [4629],
        "target_text": "Martial law is in effect across the Earth.",
        "target_instruct": "Polished crisis-broadcast authority with controlled urgency and exact adaptation identity.",
        "title": "Shythe Shahid — crisis broadcast",
        "review_instruction": "A credible Empire Today newsreader under escalating crisis, matching the approved Shythe identity.",
        "salvage_candidate_id": "5ad130953556d32b",
        "models": [QWEN, VOX, FISH, INDEX],
    },
    {
        "mode_id": "dantalion_weary_memory",
        "character": "Doc Dantalion",
        "book_speaker": "DOC DANTALION",
        "voice_key": "DOC DANTALION",
        "target_chunk_ids": [2658],
        "title": "Doc Dantalion — weary memory",
        "review_instruction": "Quiet, weary self-protection with dry intelligence and the approved Dantalion identity.",
        "salvage_candidate_id": "89773ee3454a2cbf",
        "models": [QWEN, VOX, FISH, INDEX],
    },
)

FISH_TAGS = {
    "doctor_sudden_realization_final": "delighted urgent intellectual breakthrough with eccentric Doctor identity",
    "shythe_crisis_broadcast": "polished authoritative news broadcast under controlled crisis urgency",
    "dantalion_weary_memory": "quiet weary self-protection with dry sardonic intelligence",
}

INDEX_STRENGTH = {
    "doctor_sudden_realization_final": 0.92,
    "shythe_crisis_broadcast": 0.78,
    "dantalion_weary_memory": 0.72,
}


class FinalRoundError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def salvage_reference(answer: Mapping[str, Any], candidate_id: str) -> dict[str, Any]:
    row = (answer.get("candidates") or {}).get(candidate_id)
    if not isinstance(row, Mapping):
        raise FinalRoundError(f"Salvage candidate is missing: {candidate_id}")
    source = Path(str(row["audio_path"])).resolve()
    expected = str(row["audio"]["sha256"])
    if not source.is_file() or sha256_file(source) != expected:
        raise FinalRoundError(f"Salvage candidate changed: {candidate_id}")
    return {
        "audio_path": source,
        "audio_sha256": expected,
        "reference_text": str(row["transcript"]),
        "candidate_id": candidate_id,
        "chunk_id": None,
    }


def provisional_voice(character: str) -> dict[str, Any]:
    styles = {
        "Shythe Shahid": (
            "Adult male broadcast journalist. Polished, authoritative, and exact, "
            "with controlled urgency rather than theatrical panic."
        ),
        "Doc Dantalion": (
            "Adult male alien physician. Dry, sardonic intelligence with weary "
            "amusement, measured pacing, and occasional sudden irritation."
        ),
    }
    return {
        "type": "clone",
        "clone_backend": "qwen3_instruction_controlled",
        "character_style": styles[character],
        "seed": str(PRIMARY_SEED),
    }


def main() -> int:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True)
    os.environ.setdefault("ALEXANDRIA_INDEXTTS2_ROOT", str(INDEXTTS2_ROOT))
    chunks = read_json(PROJECT / "chunks.json", "Project chunks")
    voices = read_json(PROJECT / "voice_config.json", "Voice configuration")
    config = read_json(CONFIG, "Alexandria configuration") if CONFIG.is_file() else {}
    salvage = read_json(SALVAGE_ANSWER, "Identity-salvage answer key")
    transcript = read_json(PROJECT / "external_workflows/big_finish_overlap_reference_v1/private/transcript.json", "Adaptation transcript")
    transcript_text = " ".join(normalized_words(str(transcript.get("text") or "")))
    coverage_v3.ROUND_ID = ROUND_ID
    coverage_v3.FISH_TAGS.update(FISH_TAGS)
    coverage_v3.INDEX_STRENGTH.update(INDEX_STRENGTH)

    modes: list[dict[str, Any]] = []
    specs: list[dict[str, Any]] = []
    for raw in MODE_SPECS:
        mode = copy.deepcopy(raw)
        target = coverage_v3.target_record(mode, chunks)
        if " ".join(normalized_words(target["text"])) in transcript_text:
            raise FinalRoundError(f"Target is present in adaptation: {mode['mode_id']}")
        if mode["character"] == "The Doctor":
            voice = voices["THE DOCTOR"]
            identity = current_identity_reference(PROJECT, voice)
            references = []
            for route_key in mode["qwen_routes"]:
                reference = route_reference(PROJECT, voice, route_key)
                references.append(reference)
                specs.append({"kind": "qwen", "mode": mode, "target": target, "voice": voice, "identity": identity, "performance": reference})
            for specialist in mode["specialists"]:
                performance = route_reference(PROJECT, voice, specialist["route"])
                references.append(performance)
                specs.append({"kind": "specialist", "backend": specialist["backend"], "mode": mode, "target": target, "voice": voice, "identity": identity, "performance": performance})
        else:
            identity = salvage_reference(salvage, mode["salvage_candidate_id"])
            voice = provisional_voice(mode["character"])
            references = [identity]
            for backend_name in mode["models"]:
                specs.append({"kind": "qwen" if backend_name == QWEN else "specialist", "backend": backend_name, "mode": mode, "target": target, "voice": voice, "identity": identity, "performance": identity})
        mode["target_text"] = target["text"]
        mode["target_instruct"] = target["instruct"]
        public = []
        seen = set()
        for index, reference in enumerate(references):
            if reference["audio_sha256"] in seen:
                continue
            seen.add(reference["audio_sha256"])
            relative = Path("references") / mode["mode_id"] / f"reference_{index}{Path(reference['audio_path']).suffix}"
            coverage_v3.safe_copy(Path(reference["audio_path"]), OUTPUT / relative, reference["audio_sha256"])
            public.append({"kind": "approved_character_reference", "label": "Approved character reference", "audio": "../" + relative.as_posix(), "transcript": reference["reference_text"], "audio_sha256": reference["audio_sha256"]})
        mode["public_references"] = public
        modes.append(mode)

    attempts: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    engine = TTSEngine(config)
    responsive = ResponsiveVoiceBackend()
    try:
        for spec in specs:
            try:
                if spec["kind"] == "qwen":
                    row = coverage_v3.qwen_candidate(engine=engine, output=OUTPUT, mode=spec["mode"], target=spec["target"], voice=spec["voice"], reference=spec["performance"], seed=PRIMARY_SEED)
                else:
                    row = coverage_v3.specialist_candidate(backend=responsive, output=OUTPUT, mode=spec["mode"], target=spec["target"], identity=spec["identity"], performance=spec["performance"], backend_name=spec["backend"], seed=PRIMARY_SEED)
                row["reference_variant_sha256"] = spec["performance"]["audio_sha256"]
                attempts.append(row)
            except Exception as first:
                try:
                    if spec["kind"] == "qwen":
                        row = coverage_v3.qwen_candidate(engine=engine, output=OUTPUT, mode=spec["mode"], target=spec["target"], voice=spec["voice"], reference=spec["performance"], seed=RETRY_SEED)
                    else:
                        row = coverage_v3.specialist_candidate(backend=responsive, output=OUTPUT, mode=spec["mode"], target=spec["target"], identity=spec["identity"], performance=spec["performance"], backend_name=spec["backend"], seed=RETRY_SEED)
                    row["generation_retry_of"] = str(first)
                    row["reference_variant_sha256"] = spec["performance"]["audio_sha256"]
                    attempts.append(row)
                except Exception as retry:
                    omissions.append({"mode_id": spec["mode"]["mode_id"], "backend": spec.get("backend", QWEN), "reason": "generation_failed_after_retry", "primary_error": str(first), "retry_error": str(retry)})
    finally:
        responsive.close()

    evaluation = coverage_v3.attach_transcriptions(attempts)
    accepted = [row for row in attempts if coverage_v3.transcription_passed(row)]
    for row in attempts:
        if row not in accepted:
            omissions.append({"mode_id": row["mode_id"], "backend": row["backend"], "candidate_id": row["candidate_id"], "reason": "final_transcription_gate_failed", "transcription": row.get("transcription")})
    answer = {"schema_version": 1, "round_id": ROUND_ID, "generated_at_utc": utc_now(), "mode_count": len(modes), "planned_candidate_count": len(specs), "candidate_count": len(accepted), "objective_omission_count": len(omissions), "modes": modes, "candidates": {row["candidate_id"]: row for row in accepted}, "omissions": omissions, "transcription_evaluation": evaluation, "review_contract": {"model_and_reference_identity_hidden": True, "approved_reference_audio_visible": True, "entire_line_required": True, "all_five_scores_required": True, "written_notes_override_pass": True}, "production_routing_changed": False, "project_audio_changed": False, "voice_config_changed": False}
    write_json(OUTPUT / "private/answer-key.json", answer)
    write_json(OUTPUT / "generation-summary.json", {"schema_version": 1, "round_id": ROUND_ID, "mode_count": len(modes), "planned_candidate_count": len(specs), "candidate_count": len(accepted), "objective_omission_count": len(omissions), "production_routing_changed": False, "project_audio_changed": False, "voice_config_changed": False})
    coverage_v3.build_review(output=OUTPUT, modes=modes, candidates=accepted, omissions=omissions)
    print(json.dumps({"round_id": ROUND_ID, "review": str(OUTPUT / 'review/index.html'), "mode_count": len(modes), "candidate_count": len(accepted), "objective_omission_count": len(omissions)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
