#!/usr/bin/env python3
"""Generate a durable non-cloned Qwen emotion-control bank.

The built-in Ryan CustomVoice lane is a calibration control, not a clone. Its
neutral output becomes the generic IndexTTS2 identity reference, and its broad
emotion/performance outputs become shared references for all IndexTTS2 speaker
lanes. Outputs are evaluation-only and never mutate production Voice state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import mlx.core as mx
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from mlx_backend import MLXBackend
from model_registry import model_cache_status, model_spec

DEFAULT_OUTPUT = (
    ROOT / ".omo" / "evidence" / "b17-t05-four-voice-emotion-matrix" / "qwen-control"
)

SAMPLES = [
    {
        "style": "neutral",
        "text": "There was something wrong with the room, though neither of them could say exactly what.",
        "instruction": "Speak in a natural, neutral, conversational tone with clear diction and restrained expression.",
    },
    {
        "style": "sad",
        "text": "She lowered her eyes, knowing that nothing would ever be the same again.",
        "instruction": "Speak with unmistakable grief and emotional weight. Sound genuinely sorrowful, subdued, and close to breaking, not merely slower or quieter.",
    },
    {
        "style": "fear",
        "text": "A floorboard creaked behind him, and suddenly he knew he was no longer alone.",
        "instruction": "Speak with unmistakable fear. Keep the breath tight and uneven and the voice tense and cautious, as though danger is immediately nearby. Do not sound merely surprised.",
    },
    {
        "style": "angry",
        "text": "After everything I did for you, this is how you chose to repay me.",
        "instruction": "Speak with strong controlled anger. Carry heat, resentment, and force rather than mild annoyance, while keeping every word clear instead of shouting.",
    },
    {
        "style": "happy",
        "text": "At last, the doors swung open, and everyone inside began to cheer.",
        "instruction": "Speak with genuine warm happiness and relief. Let a smile be clearly audible, with lifted energy and brightness, but do not sound manic.",
    },
    {
        "style": "excited",
        "text": "At last, the doors swung open, and everyone inside began to cheer.",
        "instruction": "Speak with unmistakable excitement and eager momentum. Increase energy and pace, with delighted anticipation and animated emphasis.",
    },
    {
        "style": "friendly",
        "text": "Come in, make yourself comfortable, and let me tell you what happened next.",
        "instruction": "Speak warmly and invitingly, as though welcoming a trusted friend. Sound relaxed, open, and genuinely pleased to see them.",
    },
    {
        "style": "surprised",
        "text": "The empty chair turned slowly toward me and said my name.",
        "instruction": "Speak with unmistakable sudden surprise and disbelief. Include an involuntary lift and a startled pause, not simple curiosity.",
    },
    {
        "style": "whisper",
        "text": "Keep your voice down. There is someone standing just outside the door.",
        "instruction": "Whisper the entire line audibly. Use breathy low-volume phonation and intimate close-mic urgency; do not merely speak softly.",
    },
    {
        "style": "shout",
        "text": "Run! Get out of the building now!",
        "instruction": "Shout the line with urgent projection and alarm. Be forceful and loud while preserving clear words and avoiding distortion.",
    },
    {
        "style": "disgust",
        "text": "The smell rolled out of the box, and I recoiled before I could stop myself.",
        "instruction": "Speak with unmistakable physical disgust and revulsion. Let the reaction tighten the voice as though the sight or smell is genuinely sickening; do not sound merely annoyed.",
    },
    {
        "style": "contempt",
        "text": "You really thought that little trick would fool me.",
        "instruction": "Speak with cold contempt and superiority. Sound dismissive and cutting, with controlled disdain rather than open anger.",
    },
    {
        "style": "grief",
        "text": "There was no goodbye, only the empty chair and the silence afterward.",
        "instruction": "Speak with deep personal grief and loss. The voice should carry pain, restraint, and the effort not to break down; make this heavier than ordinary sadness.",
    },
    {
        "style": "panic",
        "text": "The door would not open, the smoke was getting thicker, and there was nowhere left to run.",
        "instruction": "Speak in genuine escalating panic. Use shortened breath, urgent pace, and loss of composure while keeping the words intelligible. This must feel more intense than ordinary fear.",
    },
    {
        "style": "relief",
        "text": "The signal came back at last, and everyone in the room began to breathe again.",
        "instruction": "Speak with clear relief after prolonged tension. Let the breath release and the voice soften and brighten as the danger finally passes.",
    },
    {
        "style": "tender",
        "text": "Come here. You do not have to carry this alone anymore.",
        "instruction": "Speak with sincere tenderness and protective affection. Sound intimate, gentle, and emotionally present without becoming sentimental or breathy.",
    },
    {
        "style": "pleading",
        "text": "Please, just listen to me. We still have time to make this right.",
        "instruction": "Speak with urgent, vulnerable pleading. Sound emotionally exposed and desperate to persuade the listener, not merely polite or concerned.",
    },
    {
        "style": "sarcastic",
        "text": "Brilliant. Another flawless plan, and only three things are on fire.",
        "instruction": "Deliver the line with dry, unmistakable sarcasm and amused disbelief. Use ironic emphasis and timing without becoming broadly comic.",
    },
    {
        "style": "calm",
        "text": "Breathe slowly. You are safe here, and I am not going anywhere.",
        "instruction": "Speak with steady, grounded reassurance. Keep the voice calm, controlled, and comforting, with deliberate pacing and no emotional flatness.",
    },
    {
        "style": "urgent",
        "text": "We have less than a minute. Take the stairs and do not look back.",
        "instruction": "Speak with focused urgency and immediate stakes. Move quickly and decisively without shouting or sounding panicked.",
    },
    {
        "style": "exhausted",
        "text": "I have been awake for two days, and I cannot keep pretending I am fine.",
        "instruction": "Speak with unmistakable physical and emotional exhaustion. Let the breath, pace, and vocal energy sag naturally without becoming inaudible or robotic.",
    },
    {
        "style": "authoritative",
        "text": "Stand down, secure the room, and wait for my command.",
        "instruction": "Speak with firm authority and command presence. Be controlled, decisive, and impossible to ignore without shouting or sounding angry.",
    },
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def audio_metrics(path: Path) -> dict[str, Any]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    duration = len(mono) / sample_rate
    rms = float(np.sqrt(np.mean(mono * mono))) if len(mono) else 0.0
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    return {
        "sample_rate": int(sample_rate),
        "duration_seconds": duration,
        "rms": rms,
        "peak": peak,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--voice", default="Ryan")
    parser.add_argument("--seed", type=int, default=4801)
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
    for index, sample in enumerate(SAMPLES):
        sample_seed = args.seed + index
        output_path = audio_dir / f"ryan_{sample['style']}.wav"
        started = time.perf_counter()
        if not (args.reuse_existing and output_path.is_file()):
            mx.random.seed(sample_seed)
            backend.generate_custom(
                text=sample["text"],
                instruct=sample["instruction"],
                voice=args.voice,
                output_path=str(output_path),
            )
        elapsed = time.perf_counter() - started
        if not output_path.is_file():
            raise RuntimeError(f"Control generation did not create {output_path}")
        metrics = audio_metrics(output_path)
        records.append(
            {
                "style": sample["style"],
                "voice": args.voice,
                "seed": sample_seed,
                "text_sha256": sha256_text(sample["text"]),
                "instruction_sha256": sha256_text(sample["instruction"]),
                "text": sample["text"],
                "instruction": sample["instruction"],
                "audio_file": str(output_path.relative_to(output_dir)),
                "audio_sha256": sha256_file(output_path),
                "generation_seconds": elapsed,
                "real_time_factor": elapsed / metrics["duration_seconds"],
                "audio": metrics,
            }
        )
        print(
            json.dumps(
                {
                    "style": sample["style"],
                    "duration_seconds": metrics["duration_seconds"],
                    "generation_seconds": elapsed,
                    "output": str(output_path),
                }
            ),
            flush=True,
        )

    manifest = {
        "schema_version": 1,
        "purpose": "non_cloned_qwen_control_and_shared_emotion_reference_bank",
        "control_interpretation": "same_model_upper_bound_after_index_transfer",
        "model": {
            "key": "mlx_custom_voice",
            "repo_id": spec.repo_id,
            "revision": spec.revision,
            "snapshot_path": status.get("snapshot_path"),
        },
        "voice": args.voice,
        "sample_count": len(records),
        "samples": records,
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
