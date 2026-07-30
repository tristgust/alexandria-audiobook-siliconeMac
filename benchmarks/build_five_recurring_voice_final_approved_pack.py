#!/usr/bin/env python3
"""Assemble the final approved five-recurring-Voice acceptance evidence.

This script performs no synthesis. It preserves the five exact production MP3s
accepted in the first human review and converts the two exact focused-repair
winner WAVs through Alexandria's canonical audio installer. Every input and
output is bound to a reviewed SHA-256 fingerprint.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any

from pydub import AudioSegment

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from audio_artifacts import install_generated_audio, sha256_file
from generation_state import atomic_json_write


ROUND_ID = "alexandria_five_recurring_voice_final_approved_v1"
DEFAULT_OUTPUT = ROOT / ".omo/evidence/five-recurring-voice-final-approved-v1"
EXPECTED_SPEAKERS = (
    "NARRATOR",
    "BERNICE",
    "THE DOCTOR",
    "CHRIS",
    "ROZ",
    "CHRIS",
    "ROZ",
)
ACCEPTED_PRODUCTION_MP3 = {
    0: "697b34570ba6c6570112b895e30f04d74e7c4e9033320c103e2d9d6b644affb9",
    1: "520980a337b3acce4e41ed492b2347fe88d98ad9ddffb2f27252944625c6e5a2",
    2: "d07a4c481b34f2516dfac40067324265b0d6859abd59b31f2b6e187611b81780",
    4: "f13f5b4eae2523c0b4d9f593648c8caa251a0f730b7922897f0e3c41c5e9322c",
    5: "b24604aa683a11c05ce1355839f8318ac4869ba9f6e145a54f16da0cc3683df0",
}
REPAIR_WINNERS = {
    3: {
        "candidate_id": "35dabcc48b843d89",
        "key": "index_mossformer2_blend70",
        "source_wav_sha256": "23b4c372bf1f37843bc904b523e27429d01f1ae1613e54ddb42589d73326bc28",
        "production_mp3_sha256": "baf835b64fc8306f89900f544d126ea140d17fc1f0144776be760dcf2cc246ea",
        "scores": {"quality": 4, "delivery": 5, "identity": 5},
    },
    6: {
        "candidate_id": "5fb7bc4ce8d86ea8",
        "key": "index_current",
        "source_wav_sha256": "c1395986cab48cfa2a62725a344418ce8626303c68c3f9047d8cc1b38b0ef027",
        "production_mp3_sha256": "137f3c0173faa59f038ad868bd9e86f8501b9f7672c17b7f26fedb9bfa34377d",
        "scores": {"quality": 5, "delivery": 5, "identity": 4},
    },
}


class FinalApprovedPackError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinalApprovedPackError(f"{label} could not be read: {exc}") from exc


def copy_exact(source: Path, destination: Path, expected_sha256: str) -> str:
    if not source.is_file():
        raise FinalApprovedPackError(f"Approved artifact is missing: {source}")
    actual = sha256_file(source)
    if actual != expected_sha256:
        raise FinalApprovedPackError(
            f"Approved artifact changed: {source.name}; expected {expected_sha256}, got {actual}."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    installed = sha256_file(destination)
    if installed != expected_sha256:
        destination.unlink(missing_ok=True)
        raise FinalApprovedPackError(
            f"Copied approved artifact failed verification: {destination.name}."
        )
    return installed


def binding_fingerprint(index: int, source_sha256: str) -> str:
    return hashlib.sha256(
        f"{ROUND_ID}:{index}:{source_sha256}".encode("utf-8")
    ).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--accepted-pack-root", type=Path, required=True)
    parser.add_argument("--repair-round-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    accepted_root = args.accepted_pack_root.expanduser().resolve()
    repair_root = args.repair_round_root.expanduser().resolve()
    output = args.output_root.expanduser().resolve()
    accepted_chunks_path = accepted_root / "chunks.json"
    accepted_chunks = read_json(accepted_chunks_path, "Accepted acceptance chunks")
    if not isinstance(accepted_chunks, list) or len(accepted_chunks) != 7:
        raise FinalApprovedPackError("Accepted acceptance pack must contain seven chunks.")
    if tuple(str(row.get("speaker") or "") for row in accepted_chunks) != EXPECTED_SPEAKERS:
        raise FinalApprovedPackError("Accepted acceptance pack speaker order changed.")

    if output.exists():
        shutil.rmtree(output)
    voicelines = output / "voicelines"
    voicelines.mkdir(parents=True)
    final_chunks = copy.deepcopy(accepted_chunks)
    provenance: list[dict[str, Any]] = []

    for index, expected_hash in ACCEPTED_PRODUCTION_MP3.items():
        row = accepted_chunks[index]
        relative = str(row.get("audio_path") or "")
        source = accepted_root / relative
        destination = output / relative
        installed_hash = copy_exact(source, destination, expected_hash)
        final = final_chunks[index]
        final.update(
            {
                "status": "done",
                "audio_state": "current",
                "audio_sha256": installed_hash,
                "human_review_status": "accepted_initial_round",
                "human_review_round_id": "alexandria_five_recurring_voice_acceptance_v1",
                "reused_human_approved_artifact": True,
            }
        )
        provenance.append(
            {
                "index": index,
                "speaker": final["speaker"],
                "source": "accepted_initial_production_mp3",
                "sha256": installed_hash,
            }
        )

    for index, winner in REPAIR_WINNERS.items():
        candidate_id = str(winner["candidate_id"])
        source = repair_root / "audio" / f"{candidate_id}.wav"
        source_hash = sha256_file(source) if source.is_file() else ""
        if source_hash != winner["source_wav_sha256"]:
            raise FinalApprovedPackError(
                f"Repair winner {candidate_id} changed; expected "
                f"{winner['source_wav_sha256']}, got {source_hash or 'missing'}."
            )
        accepted_relative = Path(str(accepted_chunks[index]["audio_path"]))
        artifact = install_generated_audio(
            root_dir=output,
            voicelines_dir=voicelines,
            source_audio_path=source,
            filename_base=accepted_relative.stem,
            binding_fingerprint=binding_fingerprint(index, source_hash),
            prefer_mp3=True,
            text=str(accepted_chunks[index]["text"]),
        )
        if artifact["audio_sha256"] != winner["production_mp3_sha256"]:
            raise FinalApprovedPackError(
                f"Repair winner {candidate_id} changed during production formatting; "
                f"expected {winner['production_mp3_sha256']}, got {artifact['audio_sha256']}."
            )
        final = final_chunks[index]
        final.update(artifact)
        final.update(
            {
                "status": "done",
                "human_review_status": "accepted_focused_repair_round",
                "human_review_round_id": "alexandria_five_recurring_voice_repair_v1",
                "human_review_candidate_id": candidate_id,
                "human_review_candidate_key": winner["key"],
                "human_review_scores": copy.deepcopy(winner["scores"]),
                "responsive_voice_fallback_used": False,
                "reused_human_approved_artifact": True,
            }
        )
        provenance.append(
            {
                "index": index,
                "speaker": final["speaker"],
                "source": "focused_repair_winner_wav",
                "candidate_id": candidate_id,
                "candidate_key": winner["key"],
                "source_wav_sha256": source_hash,
                "production_mp3_sha256": artifact["audio_sha256"],
                "scores": copy.deepcopy(winner["scores"]),
            }
        )

    ordered = []
    for index, row in enumerate(final_chunks):
        path = output / str(row["audio_path"])
        if not path.is_file():
            raise FinalApprovedPackError(f"Final line {index} is missing: {path}")
        actual = sha256_file(path)
        if actual != row.get("audio_sha256"):
            raise FinalApprovedPackError(f"Final line {index} fingerprint is stale.")
        ordered.append(AudioSegment.from_file(path))

    combined = AudioSegment.empty()
    for index, segment in enumerate(ordered):
        if index:
            combined += AudioSegment.silent(duration=500)
        combined += segment
    combined_path = output / "approved_sequence.mp3"
    combined.export(combined_path, format="mp3")

    atomic_json_write(final_chunks, output / "chunks.json")
    summary = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "generated_at": utc_now(),
        "human_review_status": "approved",
        "manual_listening_required": False,
        "synthesis_performed": False,
        "production_root_mutated": False,
        "line_count": len(final_chunks),
        "approved_initial_line_count": len(ACCEPTED_PRODUCTION_MP3),
        "approved_repair_line_count": len(REPAIR_WINNERS),
        "fallback_count": 0,
        "combined_audio": {
            "path": "approved_sequence.mp3",
            "sha256": sha256_file(combined_path),
            "duration_ms": len(combined),
        },
        "provenance": sorted(provenance, key=lambda row: row["index"]),
        "lines": [
            {
                "index": index,
                "speaker": row["speaker"],
                "text": row["text"],
                "audio_path": row["audio_path"],
                "audio_sha256": row["audio_sha256"],
                "human_review_status": row["human_review_status"],
                "human_review_round_id": row["human_review_round_id"],
                "human_review_candidate_id": row.get("human_review_candidate_id"),
            }
            for index, row in enumerate(final_chunks)
        ],
    }
    atomic_json_write(summary, output / "summary.json")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
