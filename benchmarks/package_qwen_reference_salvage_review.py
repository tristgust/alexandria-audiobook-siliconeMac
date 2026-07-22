#!/usr/bin/env python3
"""Evaluate and package the direct-Qwen acting-reference salvage review."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = (
    ROOT / ".omo" / "evidence" / "b17-t05-reference-transfer-salvage"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidate-manifest",
        default=str(DEFAULT_ROOT / "qwen-reference-candidates" / "manifest.json"),
    )
    parser.add_argument("--output-root", default=str(DEFAULT_ROOT / "acting-review"))
    args = parser.parse_args()

    candidate_path = Path(args.candidate_manifest).expanduser().resolve()
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate_root = candidate_path.parent
    output_root = Path(args.output_root).expanduser().resolve()
    manifests_dir = output_root / "manifests"
    pages_dir = output_root / "pages"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)

    neutral_reference = (
        ROOT / ".omo" / "evidence" / "b17-t05-four-voice-emotion-matrix"
        / "qwen-control" / "audio" / "ryan_neutral.wav"
    ).resolve()
    if not neutral_reference.is_file():
        raise FileNotFoundError(neutral_reference)

    styles = sorted({item["style"] for item in candidate["samples"]})
    page_info = {}
    for style in styles:
        style_samples = [item for item in candidate["samples"] if item["style"] == style]
        if len(style_samples) != 3:
            raise ValueError(f"Expected three candidates for {style}, found {len(style_samples)}")
        expected_texts = {item["text"] for item in style_samples}
        if len(expected_texts) != 1:
            raise ValueError(f"Expected one matched line for {style}")
        manifest = {
            "schema_version": 1,
            "expected_text": next(iter(expected_texts)),
            "reference_audio": str(neutral_reference),
            "identity_label": "Ryan identity",
            "samples": [
                {
                    "sample_id": item["sample_id"],
                    "candidate": f"prompt_variant_{item['variant']}",
                    "direction": style,
                    "seed": item["seed"],
                    "path": str((candidate_root / item["audio_file"]).resolve()),
                }
                for item in style_samples
            ],
        }
        manifest_path = manifests_dir / f"{style}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        page_dir = pages_dir / style
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "benchmarks" / "evaluate_emotional_clone_outputs.py"),
                "--manifest",
                str(manifest_path),
                "--output-dir",
                str(page_dir),
            ],
            cwd=str(ROOT),
            check=True,
        )
        audio_dir = page_dir / "audio"
        for audio_path in audio_dir.iterdir():
            if audio_path.is_symlink():
                source = audio_path.resolve()
                audio_path.unlink()
                shutil.copy2(source, audio_path)
        page_info[style] = {
            "review": str((page_dir / "review.html").relative_to(output_root)),
            "answer_key": str((page_dir / "answer_key.json").relative_to(output_root)),
            "sample_count": 3,
        }

    cards = "\n".join(
        f'<a class="card" href="{html.escape(info["review"])}">'
        f'<strong>{html.escape(style.title())}</strong><span>3 acting candidates</span></a>'
        for style, info in page_info.items()
    )
    hub = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Acting-reference salvage review</title>
<style>
body{{font:16px/1.45 system-ui,sans-serif;max-width:900px;margin:0 auto;padding:32px;background:#f5f3ee;color:#25231f}}
.notice{{padding:14px 16px;border:1px solid #b9b3a7;background:#fffdf8;border-radius:8px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin-top:24px}}
.card{{display:grid;gap:4px;padding:18px;background:white;border:1px solid #d9d3c7;border-radius:10px;color:inherit;text-decoration:none}}
.card:hover{{border-color:#777}}
.card span{{color:#666}}
</style></head><body>
<h1>Acting-reference salvage review</h1>
<p class="notice">Review the direct Qwen performances only. Choose whether each sample genuinely communicates the named emotion before any IndexTTS2 transfer is attempted. Do not open answer keys until every page is exported.</p>
<div class="grid">{cards}</div>
</body></html>"""
    (output_root / "index.html").write_text(hub, encoding="utf-8")
    summary = {
        "schema_version": 1,
        "purpose": "direct_qwen_acting_reference_salvage_review",
        "style_count": len(styles),
        "sample_count": len(styles) * 3,
        "page_info": page_info,
        "temporary_paths_required": False,
        "manual_blinded_review_required": True,
        "production_promotion_allowed": False,
    }
    (output_root / "manifest.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "hub": str(output_root / "index.html"),
        "style_count": len(styles),
        "sample_count": len(styles) * 3,
        "temporary_paths_required": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
