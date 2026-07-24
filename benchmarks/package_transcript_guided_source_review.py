#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

from build_transcript_guided_source_bank import TranscriptBankError, sha256_file

ROUND_ID = "alexandria_transcript_guided_source_review_v1"
ASSET_ROOT = Path(__file__).with_name("transcript_guided_source_assets")
RANGE_SERVER = Path(__file__).with_name("range_http_server.py")

TARGET_REFERENCES = {
    "benny": Path(".omo/evidence/b17-t37-expanded-same-speaker-round/references/canonical-benny.wav"),
    "doctor": Path(".omo/evidence/b17-t25-doctor-character-identity-bank/banks/doctor_core_identity.wav"),
}


def encode_mp3(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-i", str(source),
            "-ac", "1", "-ar", "48000", "-c:a", "libmp3lame", "-b:a", "192k", str(output),
        ],
        check=True,
    )


def package(args: argparse.Namespace) -> dict[str, Any]:
    bank_path = Path(args.bank).expanduser().resolve()
    contexts_path = Path(args.contexts).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    if not bank_path.is_file() or not contexts_path.is_file():
        raise TranscriptBankError("Bank and context transcript files are required.")
    bank = json.loads(bank_path.read_text(encoding="utf-8"))
    context_payload = json.loads(contexts_path.read_text(encoding="utf-8"))
    contexts = {row["context_id"]: row for row in context_payload["contexts"]}
    rows = bank["accepted_candidates"]

    review = output_root / "review"
    if review.exists(): shutil.rmtree(review)
    (review / "audio" / "targets").mkdir(parents=True)
    (review / "audio" / "candidates").mkdir(parents=True)
    for name in ("index.html", "styles.css", "app.js"):
        shutil.copy2(ASSET_ROOT / name, review / name)
    shutil.copy2(RANGE_SERVER, review / "serve_review.py")

    target_urls: dict[str, str] = {}
    for target, relative in TARGET_REFERENCES.items():
        source = Path.cwd() / relative
        if not source.is_file(): raise TranscriptBankError(f"Target reference missing: {source}")
        destination = review / "audio" / "targets" / f"{target}.mp3"
        encode_mp3(source, destination)
        target_urls[target] = f"audio/targets/{target}.mp3"

    public_rows = []
    answer_rows = []
    for ordinal, row in enumerate(rows, start=1):
        source = Path(row["audio_path"])
        if not source.is_file(): raise TranscriptBankError(f"Candidate audio missing: {source}")
        destination = review / "audio" / "candidates" / f"{row['clip_id']}.mp3"
        encode_mp3(source, destination)
        context_texts = []
        for context_id in row["context_ids"]:
            context = contexts[context_id]
            if context["transcript"] not in context_texts: context_texts.append(context["transcript"])
        public_rows.append({
            "clip_id": row["clip_id"],
            "ordinal": ordinal,
            "target": row["target"],
            "target_label": row["target_label"],
            "source_title": contexts[row["context_ids"][0]]["source_title"],
            "transcript_start_seconds": row["transcript_start_seconds"],
            "transcript_end_seconds": row["transcript_end_seconds"],
            "selected_transcript": row["transcript"],
            "context_transcript": "\n\n".join(context_texts),
            "selection_reason": row["selection_reason"],
            "assistant_speaker_role": row["speaker_role"],
            "assistant_primary_emotion": row["primary_emotion"],
            "assistant_secondary_emotion": row["secondary_emotion"],
            "assistant_dramatic_function": row["dramatic_function"],
            "assistant_intensity_1_to_5": row["intensity_1_to_5"],
            "target_audio": target_urls[row["target"]],
            "candidate_audio": f"audio/candidates/{row['clip_id']}.mp3",
        })
        answer_rows.append({**row, "review_audio_sha256": sha256_file(destination)})

    public = {"schema_version": 1, "round_id": ROUND_ID, "candidate_count": len(public_rows), "rows": public_rows}
    (review / "data.js").write_text("window.TRANSCRIPT_GUIDED_SOURCE_DATA = " + json.dumps(public, ensure_ascii=False) + ";\n", encoding="utf-8")
    manifest = {
        "schema_version": 1, "round_id": ROUND_ID, "candidate_count": len(public_rows),
        "transcript_guided": True, "assistant_labels_prefilled": True,
        "maximum_simultaneous_audio_elements": 2, "range_server_included": True,
        "answer_key_outside_review_root": True, "production_promotion_allowed": False,
    }
    (review / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output_root / "answer-key.json").write_text(json.dumps(answer_rows, indent=2, ensure_ascii=False) + "\n")
    (output_root / "rejection-ledger.json").write_text(json.dumps(bank["rejected_contexts"], indent=2, ensure_ascii=False) + "\n")
    (output_root / "START_HERE.txt").write_text(f'cd "{review}"\npython3 serve_review.py --bind 127.0.0.1 --port 8786\n\nThen open http://127.0.0.1:8786/\n')
    return {"review": str(review / "index.html"), "candidate_count": len(public_rows)}


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve(); review = output_root / "review"
    prefix = "window.TRANSCRIPT_GUIDED_SOURCE_DATA = "
    text = (review / "data.js").read_text().strip(); data = json.loads(text[len(prefix):].rstrip(";"))
    missing=[]; bad=[]
    for row in data["rows"]:
        for key in ("target_audio","candidate_audio"):
            path=review/row[key]
            if not path.is_file(): missing.append(f"{row['clip_id']}:{key}")
            else:
                probe=subprocess.run(["ffprobe","-v","error","-show_entries","stream=codec_name,channels","-of","json",str(path)],capture_output=True,text=True,check=True)
                stream=json.loads(probe.stdout)["streams"][0]
                if stream.get("codec_name")!="mp3" or stream.get("channels")!=1: bad.append(f"{row['clip_id']}:{key}")
        for key in ("selected_transcript","selection_reason","assistant_primary_emotion","assistant_dramatic_function"):
            if not row.get(key): bad.append(f"{row['clip_id']}:{key}")
    body=(review/"index.html").read_text()
    if re.search(r"IndexTTS2|VoxCPM|Fish S2|Qwen",body,re.I): bad.append("model_name_leak")
    if missing or bad: raise TranscriptBankError(f"Review validation failed: missing={missing}, bad={bad}")
    return {"candidate_count":len(data["rows"]),"missing_count":len(missing),"bad_count":len(bad),"review":str(review/"index.html")}


def main(argv: Iterable[str] | None = None) -> int:
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("package");p.add_argument("--bank",required=True);p.add_argument("--contexts",required=True);p.add_argument("--output-root",required=True)
    v=sub.add_parser("validate");v.add_argument("--output-root",required=True)
    args=parser.parse_args(list(argv) if argv is not None else None)
    try: result=package(args) if args.command=="package" else validate(args)
    except (TranscriptBankError,subprocess.CalledProcessError,json.JSONDecodeError) as exc:
        print(json.dumps({"status":"failed","error_type":type(exc).__name__,"error":str(exc)}));return 2
    print(json.dumps(result,indent=2,ensure_ascii=False));return 0
if __name__=="__main__": raise SystemExit(main())
