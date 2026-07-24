#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from prepare_expanded_same_speaker_round import fingerprint, now_iso, sha256_file

ROUND_ID = "alexandria_doctor_actor_identity_followup_v1"

ROUTES: tuple[dict[str, Any], ...] = (
    {
        "target_key": "doctor",
        "target_label": "Doctor",
        "mode": "cold_existential_dismissal",
        "mode_label": "Cold existential dismissal · actor identity repair",
        "target_text": "You are an echo pretending to be a man. Nothing more.",
        "reference_text": "You're not real. You never were. You never will be. You exist in this instant.",
        "alpha": 0.55,
    },
    {
        "target_key": "doctor",
        "target_label": "Doctor",
        "mode": "dry_sarcasm",
        "mode_label": "Dry sarcasm · cleanliness repair",
        "target_text": "Oh, brilliant. Another impossible machine with no instructions and a very large red button.",
        "reference_text": "She always puts you down, tells you how stupid you are. I can see what she means. I might as well be talking to a door.",
        "alpha": 0.30,
    },
    {
        "target_key": "doctor",
        "target_label": "Doctor",
        "mode": "protective_authority_repair",
        "mode_label": "Protective authority · actor identity repair",
        "target_text": "Stay behind me. Whatever happens, do not let go of my hand.",
        "reference_text": "I'm the Doctor, and I take care of my friends.",
        "alpha": 0.15,
    },
)


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    source_root = Path(args.source_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    actor_identity = source_root / "references" / "doctor-actor-identity.wav"
    canonical_identity = source_root / "references" / "canonical-doctor.wav"
    if not actor_identity.is_file():
        raise FileNotFoundError(f"Doctor actor identity is missing: {actor_identity}")
    if not canonical_identity.is_file():
        raise FileNotFoundError(f"Doctor canonical identity is missing: {canonical_identity}")

    source_matrix = json.loads((source_root / "matrix.json").read_text(encoding="utf-8"))
    source_routes = {
        (route["target_key"], route["mode"]): route
        for route in source_matrix["routes"]
    }
    routes = []
    samples = []
    for spec in ROUTES:
        source = source_routes[(spec["target_key"], spec["mode"])]
        reference_audio = Path(source["reference_audio"]).resolve()
        if not reference_audio.is_file():
            raise FileNotFoundError(f"Doctor performance reference is missing: {reference_audio}")
        route = {
            **spec,
            "purpose": "repair",
            "speaker_strategies": ["actor_identity"],
            "alphas": [spec["alpha"]],
            "reference_audio": str(reference_audio),
            "reference_audio_sha256": sha256_file(reference_audio),
            "canonical_identity_audio": str(canonical_identity),
            "canonical_identity_sha256": sha256_file(canonical_identity),
            "doctor_actor_identity_audio": str(actor_identity),
            "doctor_actor_identity_sha256": sha256_file(actor_identity),
        }
        routes.append(route)
        sample_id = fingerprint(
            {
                "round": ROUND_ID,
                "target": spec["target_key"],
                "mode": spec["mode"],
                "strategy": "actor_identity",
                "alpha": spec["alpha"],
                "speaker": sha256_file(actor_identity),
                "reference": sha256_file(reference_audio),
                "text": spec["target_text"],
            }
        )
        samples.append(
            {
                "sample_id": sample_id,
                "target_key": spec["target_key"],
                "target_label": spec["target_label"],
                "mode": spec["mode"],
                "mode_label": spec["mode_label"],
                "purpose": "repair",
                "target_text": spec["target_text"],
                "reference_text": spec["reference_text"],
                "speaker_strategy": "actor_identity",
                "alpha": float(spec["alpha"]),
                "speaker_audio": str(actor_identity),
                "speaker_audio_sha256": sha256_file(actor_identity),
                "reference_audio": str(reference_audio),
                "reference_audio_sha256": sha256_file(reference_audio),
                "canonical_identity_audio": str(canonical_identity),
                "canonical_identity_sha256": sha256_file(canonical_identity),
                "doctor_actor_identity_audio": str(actor_identity),
                "doctor_actor_identity_sha256": sha256_file(actor_identity),
            }
        )

    output_root.mkdir(parents=True, exist_ok=True)
    matrix = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "route_count": len(routes),
        "sample_count": len(samples),
        "target_order": ["doctor"],
        "routes": routes,
        "samples": samples,
        "production_promotion_allowed": False,
    }
    path = output_root / "matrix.json"
    path.write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"route_count": len(routes), "sample_count": len(samples), "matrix": str(path)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare Doctor actor-identity repair samples.")
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = prepare(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
