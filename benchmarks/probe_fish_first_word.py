#!/usr/bin/env python3
"""Probe Fish first-word omissions without changing production state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from responsive_voice_backend import (  # noqa: E402
    FishAudioBackend,
    _normalized_words,
    _word_error_rate,
)


LINE = "Just... she mentioned family and..."
LONG_TAG = (
    "Speak with hesitant, restrained vulnerability: sincere, emotionally "
    "exposed, and trying not to lose control."
)

VARIANTS = (
    {
        "key": "long_tag_original",
        "prompt_mode": "full_alexandria_tag",
        "tag": LONG_TAG,
        "text": LINE,
    },
    {
        "key": "untagged_original",
        "prompt_mode": "untagged",
        "tag": "",
        "text": LINE,
    },
    {
        "key": "short_tag_original",
        "prompt_mode": "simple_tag",
        "tag": "vulnerable",
        "text": LINE,
    },
    {
        "key": "long_tag_sentence_boundary",
        "prompt_mode": "full_alexandria_tag",
        "tag": LONG_TAG,
        "text": "Just. She mentioned family and...",
    },
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    import mlx_whisper

    args = parse_args()
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    backend = FishAudioBackend()
    rows = []
    for variant in VARIANTS:
        audio = output / f"{variant['key']}.wav"
        control = {
            "reference_id": "631bff1fd20b48e1a4a08db8e936b038",
            "api_model_header": "s2.1-pro-free",
            "prompt_mode": variant["prompt_mode"],
            "tag": variant["tag"],
            "temperature": 0.7,
            "top_p": 0.7,
            "repetition_penalty": 1.2,
        }
        backend.generate(
            text=variant["text"],
            control=control,
            output_path=audio,
        )
        transcription = mlx_whisper.transcribe(
            str(audio),
            path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
            language="en",
            condition_on_previous_text=False,
            word_timestamps=False,
            verbose=False,
        )
        observed = str(transcription.get("text") or "").strip()
        expected_words = _normalized_words(variant["text"])
        observed_words = _normalized_words(observed)
        row = {
            **variant,
            "audio": audio.name,
            "automatic_transcript": observed,
            "word_error_rate": _word_error_rate(variant["text"], observed),
            "first_word_present": bool(
                expected_words
                and observed_words
                and expected_words[0] == observed_words[0]
            ),
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    summary = {
        "schema_version": 1,
        "line": LINE,
        "variants": rows,
        "production_state_changed": False,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
