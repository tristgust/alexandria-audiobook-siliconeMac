#!/usr/bin/env python3
"""Generate stronger direct Qwen acting candidates for weak emotion references.

This is evaluation-only. It creates three prompt/seed candidates for each style
that failed or remained ambiguous in the five-lane human review. The outputs are
reviewed directly before any further IndexTTS2 cross-speaker generation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import mlx.core as mx
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from mlx_backend import MLXBackend
from model_registry import model_cache_status, model_spec

DEFAULT_OUTPUT = (
    ROOT / ".omo" / "evidence" / "b17-t05-reference-transfer-salvage"
    / "qwen-reference-candidates"
)

STYLE_CASES = {
    "fear": {
        "text": "A floorboard creaked behind him, and suddenly he knew he was no longer alone.",
        "instructions": [
            "Act the line with immediate, unmistakable terror. Let the breath catch, make the voice tremble, and sound as though a threat is within arm's reach. Do not merely raise the pitch.",
            "Speak as someone trying desperately not to be heard while terrified. Use a tight throat, broken breath, involuntary hesitation, and genuine dread.",
            "Deliver this with fear escalating during the sentence: cautious at first, then a sharp realization and a voice nearly breaking at the end.",
        ],
    },
    "panic": {
        "text": "The doors would not open, the smoke was getting thicker, and there was nowhere left to run.",
        "instructions": [
            "Act full panic. Words should tumble out breathlessly with loss of control, racing thoughts, and desperate urgency. Do not sound merely concerned.",
            "Speak in escalating panic with ragged breathing, clipped phrases, and a voice close to cracking as escape becomes impossible.",
            "Deliver the line as an acute emergency: frantic, breathless, overwhelmed, and visibly struggling to think clearly.",
        ],
    },
    "disgust": {
        "text": "The smell rolled out of the box, and I recoiled before I could stop myself.",
        "instructions": [
            "Act unmistakable physical revulsion. Recoil from the words, tighten the throat, and let a restrained gagging edge enter the voice without becoming comic.",
            "Speak with visceral disgust and involuntary repulsion, as though the smell is making you nauseated. Do not sound indifferent or mildly annoyed.",
            "Deliver the line with a clear grimace, sharp recoil, and contemptuous physical revulsion. The reaction must be audible immediately.",
        ],
    },
    "contempt": {
        "text": "You actually believed that would impress me.",
        "instructions": [
            "Speak with unmistakable cold contempt: a controlled sneer, dismissive superiority, and total lack of respect. Do not sound merely bored.",
            "Deliver the line as cutting disdain. Make the listener feel beneath you, with a slight sneer and deliberate insulting emphasis.",
            "Act quiet, venomous contempt. Keep it controlled and precise, with an audible eye-roll and dismissive finality.",
        ],
    },
    "relief": {
        "text": "The signal came back at last, and everyone in the room began to breathe again.",
        "instructions": [
            "Act profound relief after prolonged fear. Begin tense, then release into an audible exhale and softened voice as the danger passes.",
            "Speak with unmistakable emotional release: breath returning, shoulders dropping, and near-laughter or tears after expecting the worst.",
            "Deliver the sentence as tension finally breaking. Let the voice loosen, warm, and exhale visibly on the final words.",
        ],
    },
    "urgent": {
        "text": "We have less than a minute. Move now, and do not stop for anything.",
        "instructions": [
            "Speak with immediate operational urgency: clipped, fast, forceful, and focused. Do not shout, but make delay feel dangerous.",
            "Deliver a time-critical command with accelerating pace, hard consonants, and no room for hesitation. Every second matters.",
            "Act urgent command authority under pressure. Keep the words clear and controlled while driving the listener to move immediately.",
        ],
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--voice", default="Ryan")
    parser.add_argument("--seed", type=int, default=5301)
    parser.add_argument("--reuse-existing", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    audio_dir = output_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    status = model_cache_status("mlx_custom_voice")
    if not status.get("cached"):
        raise RuntimeError("Pinned MLX CustomVoice model is not cached.")
    spec = model_spec("mlx_custom_voice")
    backend = MLXBackend(language="English")

    records = []
    index = 0
    for style, case in STYLE_CASES.items():
        for variant, instruction in enumerate(case["instructions"], start=1):
            seed = args.seed + index
            index += 1
            sample_id = f"{style}_acting_v{variant}_{seed}"
            output_path = audio_dir / f"{sample_id}.wav"
            started = time.perf_counter()
            if not (args.reuse_existing and output_path.is_file()):
                mx.random.seed(seed)
                backend.generate_custom(
                    text=case["text"],
                    instruct=instruction,
                    voice=args.voice,
                    output_path=str(output_path),
                )
            elapsed = time.perf_counter() - started
            audio, sample_rate = sf.read(output_path, always_2d=True)
            duration = len(audio) / sample_rate
            records.append({
                "sample_id": sample_id,
                "style": style,
                "variant": variant,
                "seed": seed,
                "text": case["text"],
                "instruction": instruction,
                "audio_file": str(output_path.relative_to(output_dir)),
                "audio_sha256": sha256_file(output_path),
                "duration_seconds": duration,
                "generation_seconds": elapsed,
                "real_time_factor": elapsed / duration,
            })
            print(json.dumps({
                "sample_id": sample_id,
                "style": style,
                "duration_seconds": duration,
                "generation_seconds": elapsed,
            }), flush=True)

    manifest = {
        "schema_version": 1,
        "purpose": "direct_qwen_acting_reference_salvage_candidates",
        "model": {
            "key": "mlx_custom_voice",
            "repo_id": spec.repo_id,
            "revision": spec.revision,
            "snapshot_path": status.get("snapshot_path"),
        },
        "voice": args.voice,
        "style_count": len(STYLE_CASES),
        "sample_count": len(records),
        "samples": records,
        "manual_blinded_review_required": True,
        "production_promotion_allowed": False,
        "production_registry_changed": False,
        "voice_assignment_changed": False,
        "live_project_audio_changed": False,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in manifest.items() if k != "samples"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
