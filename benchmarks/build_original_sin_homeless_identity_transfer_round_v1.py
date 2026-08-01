#!/usr/bin/env python3
"""Blind-test Homeless Forsaken identity transfer without approving noisy source audio."""

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
    read_json,
    sha256_file,
    write_json,
)
from responsive_voice_backend import ResponsiveVoiceBackend  # noqa: E402
from tts import TTSEngine  # noqa: E402


ROUND_ID = "alexandria_original_sin_homeless_identity_transfer_round_v1"
PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
CONFIG = Path("/Users/tristan/pinokio/api/alexandria-audiobook.git/config.json")
OUTPUT = PROJECT / "external_workflows/big_finish_overlap_reference_v1/homeless_identity_transfer_round_v1"
SOURCE_ANSWER = PROJECT / "external_workflows/big_finish_overlap_reference_v1/reference_repair_round_v2/private/answer-key.json"
SOURCE_CANDIDATE_ID = "3932d1942197febd"
INDEXTTS2_ROOT = Path("/Users/tristan/pinokio/cache/alexandria-evaluation/indextts2")
QWEN = coverage_v3.QWEN
VOX = coverage_v3.VOX
FISH = coverage_v3.FISH
INDEX = coverage_v3.INDEX
PRIMARY_SEED = 130363
RETRY_SEED = 130464


class IdentityTransferError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def adaptation_context() -> dict[str, Any]:
    answer = read_json(SOURCE_ANSWER, "Homeless source answer key")
    row = answer["candidates"][SOURCE_CANDIDATE_ID]
    source = Path(row["path"]).resolve()
    expected = row["metrics"]["sha256"]
    if not source.is_file() or sha256_file(source) != expected:
        raise IdentityTransferError("Homeless adaptation source changed.")
    return {
        "audio_path": source,
        "audio_sha256": expected,
        "reference_text": row["transcript"],
        "candidate_id": SOURCE_CANDIDATE_ID,
        "chunk_id": None,
    }


def main() -> int:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True)
    os.environ.setdefault("ALEXANDRIA_INDEXTTS2_ROOT", str(INDEXTTS2_ROOT))
    voices = read_json(PROJECT / "voice_config.json", "Voice configuration")
    config = read_json(CONFIG, "Alexandria configuration") if CONFIG.is_file() else {}
    voice = voices["HOMELESS FORSAKEN"]
    designed = current_identity_reference(PROJECT, voice)
    adaptation = adaptation_context()
    mode = {
        "mode_id": "homeless_identity_transfer",
        "title": "Homeless Forsaken — dying identity transfer",
        "character": "Homeless Forsaken",
        "speaker": "HOMELESS FORSAKEN",
        "voice_key": "HOMELESS FORSAKEN",
        "target_text": "Bernice, listen to me. There isn't much time.",
        "target_instruct": "Weak, breathy urgency with fading strength and the exact adaptation identity.",
        "review_instruction": (
            "Judge whether the clean generated line sounds like the dying adaptation "
            "character. The noisy adaptation clip is identity context only and cannot pass."
        ),
        "public_references": coverage_v3.public_references(
            output=OUTPUT,
            mode={"mode_id": "homeless_identity_transfer"},
            identity=adaptation,
            performance=designed,
        ),
    }
    target = {
        "text": mode["target_text"],
        "instruct": mode["target_instruct"],
        "chunk_ids": [],
    }
    coverage_v3.ROUND_ID = ROUND_ID
    coverage_v3.FISH_TAGS[mode["mode_id"]] = (
        "weak breathy urgency, fading strength, and restrained fear"
    )
    coverage_v3.INDEX_STRENGTH[mode["mode_id"]] = 0.82
    attempts: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    engine = TTSEngine(config)
    responsive = ResponsiveVoiceBackend()
    specs = []
    for reference_name, reference in (("adaptation", adaptation), ("designed", designed)):
        specs.append((QWEN, reference_name, reference))
        for backend_name in (VOX, FISH, INDEX):
            specs.append((backend_name, reference_name, reference))
    try:
        for backend_name, reference_name, reference in specs:
            try:
                if backend_name == QWEN:
                    row = coverage_v3.qwen_candidate(
                        engine=engine,
                        output=OUTPUT,
                        mode=mode,
                        target=target,
                        voice=voice,
                        reference=reference,
                        seed=PRIMARY_SEED,
                    )
                else:
                    row = coverage_v3.specialist_candidate(
                        backend=responsive,
                        output=OUTPUT,
                        mode=mode,
                        target=target,
                        identity=reference,
                        performance=reference,
                        backend_name=backend_name,
                        seed=PRIMARY_SEED,
                    )
                row["identity_source_kind"] = reference_name
                attempts.append(row)
            except Exception as first:
                try:
                    if backend_name == QWEN:
                        row = coverage_v3.qwen_candidate(
                            engine=engine,
                            output=OUTPUT,
                            mode=mode,
                            target=target,
                            voice=voice,
                            reference=reference,
                            seed=RETRY_SEED,
                        )
                    else:
                        row = coverage_v3.specialist_candidate(
                            backend=responsive,
                            output=OUTPUT,
                            mode=mode,
                            target=target,
                            identity=reference,
                            performance=reference,
                            backend_name=backend_name,
                            seed=RETRY_SEED,
                        )
                    row["identity_source_kind"] = reference_name
                    row["generation_retry_of"] = str(first)
                    attempts.append(row)
                except Exception as retry:
                    omissions.append(
                        {
                            "backend": backend_name,
                            "identity_source_kind": reference_name,
                            "reason": "generation_failed_after_retry",
                            "primary_error": str(first),
                            "retry_error": str(retry),
                        }
                    )
    finally:
        responsive.close()
    evaluation = coverage_v3.attach_transcriptions(attempts)
    accepted = [row for row in attempts if coverage_v3.transcription_passed(row)]
    for row in attempts:
        if row not in accepted:
            omissions.append(
                {
                    "backend": row["backend"],
                    "identity_source_kind": row["identity_source_kind"],
                    "candidate_id": row["candidate_id"],
                    "reason": "final_transcription_gate_failed",
                    "transcription": row.get("transcription"),
                }
            )
    answer = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "generated_at_utc": utc_now(),
        "candidate_count": len(accepted),
        "planned_candidate_count": len(specs),
        "objective_omission_count": len(omissions),
        "modes": [mode],
        "candidates": {row["candidate_id"]: row for row in accepted},
        "omissions": omissions,
        "transcription_evaluation": evaluation,
        "review_contract": {
            "model_and_identity_source_hidden": True,
            "adaptation_context_visible_but_not_eligible": True,
            "entire_line_required": True,
            "all_five_scores_required": True,
            "written_notes_override_pass": True,
        },
        "production_routing_changed": False,
        "project_audio_changed": False,
        "voice_config_changed": False,
    }
    write_json(OUTPUT / "private/answer-key.json", answer)
    write_json(
        OUTPUT / "generation-summary.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "planned_candidate_count": len(specs),
            "candidate_count": len(accepted),
            "objective_omission_count": len(omissions),
            "production_routing_changed": False,
            "project_audio_changed": False,
            "voice_config_changed": False,
        },
    )
    coverage_v3.build_review(
        output=OUTPUT,
        modes=[mode],
        candidates=accepted,
        omissions=omissions,
    )
    print(
        json.dumps(
            {
                "round_id": ROUND_ID,
                "review": str(OUTPUT / "review/index.html"),
                "candidate_count": len(accepted),
                "objective_omission_count": len(omissions),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
