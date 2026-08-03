#!/usr/bin/env python3
"""Prepare, verify, and package Cwej/Roz reference finalists.

This stage does not approve a voice. It extracts exact source intervals, checks
speaker consistency with ECAPA, cross-transcribes each clip with two Whisper
models, and builds an editable listening review.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import html
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(ROOT / "benchmarks") not in sys.path:
    sys.path.insert(0, str(ROOT / "benchmarks"))
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import mlx_whisper

from retrieve_cwej_roz_candidates import (
    EcapaSpeakerEncoder,
    acoustic_metrics,
    average_embeddings,
    embedding,
    extract_clip,
    sha256_file,
)

CONFIG_PATH = ROOT / "benchmarks/cwej_roz_sources.json"
CANDIDATES_PATH = ROOT / "benchmarks/cwej_roz_reference_candidates.json"
DEFAULT_EVIDENCE_ROOT = ROOT / ".omo/evidence/cwej-roz-voice-evaluation"
TRANSCRIPTION_MODELS = (
    "mlx-community/whisper-large-v3-turbo",
    "mlx-community/whisper-large-v3-mlx",
)


class FinalistError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_text(text: str) -> str:
    text = text.lower().replace("’", "'").replace("–", "-")
    text = re.sub(r"[^a-z0-9']+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(left: str, right: str) -> float:
    return round(
        SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio(),
        6,
    )


def source_maps(manifest: dict[str, Any]) -> dict[tuple[str, str], Path]:
    rows: dict[tuple[str, str], Path] = {}
    for row in manifest["owned_dramas"]:
        rows[("owned_drama", row["key"])] = Path(row["path"])
    for row in manifest["public_references"]:
        rows[("public_reference", row["key"])] = Path(row["prepared_path"])
    return rows


def load_prepared_public_paths(manifest: dict[str, Any]) -> dict[str, Path]:
    return {
        row["key"]: Path(row["prepared_path"])
        for row in manifest["public_references"]
    }


def build_seed_vectors(
    *,
    config: dict[str, Any],
    public_paths: dict[str, Path],
    evidence_root: Path,
    encoder: EcapaSpeakerEncoder,
) -> tuple[dict[str, np.ndarray], list[dict[str, Any]]]:
    clip_root = evidence_root / "private/reference-finalists/seed-clips"
    vectors: dict[str, np.ndarray] = {}
    receipts: list[dict[str, Any]] = []
    for speaker, intervals in config["speaker_seed_intervals"].items():
        speaker_vectors: list[np.ndarray] = []
        for index, interval in enumerate(intervals):
            source = public_paths[interval["source_key"]]
            target = clip_root / speaker / f"seed-{index:02d}.wav"
            extract_clip(
                source,
                target,
                float(interval["start_seconds"]),
                float(interval["end_seconds"]),
            )
            vector = embedding(encoder, target)
            speaker_vectors.append(vector)
            receipts.append(
                {
                    "speaker": speaker,
                    "source_key": interval["source_key"],
                    "start_seconds": interval["start_seconds"],
                    "end_seconds": interval["end_seconds"],
                    "path": str(target),
                    "sha256": sha256_file(target),
                }
            )
        vectors[speaker] = average_embeddings(speaker_vectors)
    return vectors, receipts


def write_window(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), audio, sample_rate, subtype="PCM_16")


def consistency_metrics(
    *,
    clip_path: Path,
    target_seed: np.ndarray,
    encoder: EcapaSpeakerEncoder,
    window_root: Path,
    window_seconds: float = 2.0,
    hop_seconds: float = 1.0,
) -> dict[str, Any]:
    audio, sample_rate = sf.read(
        str(clip_path), dtype="float32", always_2d=False
    )
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    whole = embedding(encoder, clip_path)
    whole_cosine = float(np.dot(whole, target_seed))
    window_samples = max(1, int(round(window_seconds * sample_rate)))
    hop_samples = max(1, int(round(hop_seconds * sample_rate)))
    starts = list(range(0, max(1, audio.size - window_samples + 1), hop_samples))
    final_start = max(0, audio.size - window_samples)
    if final_start not in starts:
        starts.append(final_start)
    windows: list[dict[str, Any]] = []
    for index, start in enumerate(sorted(set(starts))):
        window = audio[start : start + window_samples]
        if window.size < int(sample_rate * 0.9):
            continue
        rms = float(np.sqrt(np.mean(np.square(window), dtype=np.float64)))
        rms_dbfs = 20.0 * math.log10(max(rms, 1e-8))
        if rms_dbfs < -48.0:
            continue
        target = window_root / f"window-{index:03d}.wav"
        write_window(target, window, sample_rate)
        vector = embedding(encoder, target)
        windows.append(
            {
                "index": index,
                "start_seconds": round(start / sample_rate, 4),
                "end_seconds": round((start + window.size) / sample_rate, 4),
                "rms_dbfs": round(rms_dbfs, 4),
                "cosine": round(float(np.dot(vector, target_seed)), 6),
            }
        )
    cosines = np.array([row["cosine"] for row in windows], dtype=np.float64)
    if cosines.size:
        summary = {
            "window_count": int(cosines.size),
            "minimum": round(float(np.min(cosines)), 6),
            "p10": round(float(np.percentile(cosines, 10)), 6),
            "mean": round(float(np.mean(cosines)), 6),
            "maximum": round(float(np.max(cosines)), 6),
            "stddev": round(float(np.std(cosines)), 6),
        }
    else:
        summary = {
            "window_count": 0,
            "minimum": None,
            "p10": None,
            "mean": None,
            "maximum": None,
            "stddev": None,
        }
    return {
        "whole_clip_cosine": round(whole_cosine, 6),
        "window_seconds": window_seconds,
        "hop_seconds": hop_seconds,
        "summary": summary,
        "windows": windows,
    }


def consistency_gate(candidate: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    summary = metrics["summary"]
    whole = metrics["whole_clip_cosine"]
    mean = summary["mean"]
    p10 = summary["p10"]
    minimum = summary["minimum"]
    reasons: list[str] = []
    if whole < 0.20:
        reasons.append(f"whole-clip cosine {whole:.3f} < 0.200")
    if mean is None or mean < 0.18:
        reasons.append("window mean is missing or below 0.180")
    if p10 is None or p10 < -0.02:
        reasons.append("window p10 is missing or below -0.020")
    if minimum is not None and minimum < -0.18:
        reasons.append(f"minimum window cosine {minimum:.3f} < -0.180")
    if candidate["role"] in {"identity", "identity_fallback"}:
        if whole < 0.35:
            reasons.append(f"identity whole-clip cosine {whole:.3f} < 0.350")
        if mean is None or mean < 0.28:
            reasons.append("identity window mean is missing or below 0.280")
    return {
        "status": "pass" if not reasons else "review_required",
        "reasons": reasons,
    }


def transcribe_clip(path: Path, model: str) -> dict[str, Any]:
    result = mlx_whisper.transcribe(
        str(path),
        path_or_hf_repo=model,
        language="en",
        word_timestamps=True,
        condition_on_previous_text=False,
        verbose=None,
    )
    return {
        "model": model,
        "text": re.sub(r"\s+", " ", str(result.get("text") or "")).strip(),
        "segments": result.get("segments") or [],
    }


def transcript_gate(
    candidate: dict[str, Any], transcripts: list[dict[str, Any]]
) -> dict[str, Any]:
    provisional = candidate["transcript_provisional"]
    comparisons = [
        {
            "model": row["model"],
            "similarity_to_provisional": similarity(provisional, row["text"]),
        }
        for row in transcripts
    ]
    model_agreement = (
        similarity(transcripts[0]["text"], transcripts[1]["text"])
        if len(transcripts) > 1
        else None
    )
    reasons: list[str] = []
    if model_agreement is None or model_agreement < 0.86:
        reasons.append("two-model transcript agreement is below 0.860")
    if any(row["similarity_to_provisional"] < 0.80 for row in comparisons):
        reasons.append("at least one model differs materially from the provisional transcript")
    return {
        "status": "provisional_match" if not reasons else "listening_required",
        "model_agreement": model_agreement,
        "comparisons": comparisons,
        "reasons": reasons,
    }


def review_html(rows: list[dict[str, Any]]) -> str:
    groups = [
        ("Chris identity", lambda r: r["candidate"]["character"] == "chris_cwej" and r["candidate"]["role"].startswith("identity")),
        ("Chris canonical delivery", lambda r: r["candidate"]["character"] == "chris_cwej" and r["candidate"]["role"] == "canonical_delivery"),
        ("Roz identity", lambda r: r["candidate"]["character"] == "roz_forrester" and r["candidate"]["role"].startswith("identity")),
        ("Roz canonical delivery", lambda r: r["candidate"]["character"] == "roz_forrester" and r["candidate"]["role"] == "canonical_delivery"),
        ("T'Nia style-only references", lambda r: r["candidate"]["role"] == "delivery_style_only"),
    ]
    sections: list[str] = []
    for title, predicate in groups:
        cards: list[str] = []
        for row in [item for item in rows if predicate(item)]:
            candidate = row["candidate"]
            consistency = row["speaker_consistency"]
            transcript = row["transcript_gate"]
            transcripts = row["transcriptions"]
            cards.append(
                f"""
<article class="card" data-id="{html.escape(candidate['id'])}">
  <header><div><strong>{html.escape(candidate['id'])}</strong><small>{html.escape(candidate['source_key'])} · {candidate['start_seconds']:.2f}–{candidate['end_seconds']:.2f}</small></div><span class="badge {html.escape(consistency['gate']['status'])}">{html.escape(consistency['gate']['status'])}</span></header>
  <p class="delivery">{html.escape(candidate['delivery'].replace('_', ' '))}</p>
  <audio controls preload="none" src="{html.escape(row['review_audio'])}"></audio>
  <dl>
    <div><dt>Whole identity</dt><dd>{consistency['whole_clip_cosine']:.3f}</dd></div>
    <div><dt>Window mean</dt><dd>{consistency['summary']['mean'] if consistency['summary']['mean'] is not None else '—'}</dd></div>
    <div><dt>Window minimum</dt><dd>{consistency['summary']['minimum'] if consistency['summary']['minimum'] is not None else '—'}</dd></div>
    <div><dt>ASR agreement</dt><dd>{transcript['model_agreement'] if transcript['model_agreement'] is not None else '—'}</dd></div>
  </dl>
  <details><summary>Machine transcripts</summary>
    <p><b>Turbo:</b> {html.escape(transcripts[0]['text'])}</p>
    <p><b>Large v3:</b> {html.escape(transcripts[1]['text'])}</p>
    <p><b>Provisional:</b> {html.escape(candidate['transcript_provisional'])}</p>
  </details>
  <label>Disposition<select data-field="disposition"><option value="unreviewed">Unreviewed</option><option value="approve_identity">Approve identity</option><option value="approve_delivery">Approve delivery only</option><option value="reject_wrong_speaker">Reject: wrong/mixed speaker</option><option value="reject_overlap">Reject: overlap</option><option value="reject_music_sfx">Reject: music/SFX</option><option value="reject_transcript">Reject: transcript uncertain</option><option value="reject_quality">Reject: audio quality</option></select></label>
  <label>Exact transcript<textarea rows="4" data-field="exact_transcript">{html.escape(candidate['transcript_provisional'])}</textarea></label>
  <label>Emotion / delivery note<textarea rows="2" data-field="delivery_note">{html.escape(candidate.get('note') or '')}</textarea></label>
  <label>Reviewer note<textarea rows="2" data-field="reviewer_note"></textarea></label>
</article>"""
            )
        sections.append(f"<section><h2>{html.escape(title)}</h2><div class='grid'>{''.join(cards)}</div></section>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Cwej / Roz reference finalists</title>
<style>
:root{{font-family:system-ui,sans-serif;color:#211f1b;background:#eee9df}}body{{margin:0}}main{{max-width:1500px;margin:auto;padding:24px}}h1{{margin-bottom:4px}}.intro{{max-width:88ch;color:#59534a}}.toolbar{{position:sticky;top:0;z-index:4;background:#eee9df;padding:10px 0;display:flex;gap:10px;align-items:center;border-bottom:1px solid #c6beaf}}button{{font:inherit;padding:8px 12px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr));gap:14px}}section{{margin-top:30px}}.card{{background:#fffdf7;border:1px solid #c9c0b1;padding:14px}}header{{display:flex;justify-content:space-between;gap:10px}}header div{{display:grid;gap:3px}}small{{color:#736b60}}.badge{{border:1px solid currentColor;padding:3px 7px;font-size:.75rem}}.pass{{color:#17663b}}.review_required{{color:#984119}}.delivery{{text-transform:capitalize;color:#4d4941}}audio{{width:100%}}dl{{display:grid;grid-template-columns:1fr 1fr;gap:5px;font-size:.86rem}}dl div{{display:flex;justify-content:space-between;gap:8px}}dt{{color:#6d665b}}dd{{margin:0}}details{{font-size:.85rem}}label{{display:block;margin-top:10px;font-size:.84rem}}select,textarea{{width:100%;box-sizing:border-box;margin-top:4px;font:inherit}}textarea{{resize:vertical}}#status{{font-size:.85rem;color:#5b554c}}
</style></head><body><main><h1>Chris Cwej / Roz Forrester reference finalists</h1><p class="intro">Machine gates only remove obvious contamination. Listen to every candidate, confirm the exact transcript, and approve identity separately from delivery. T'Nia Miller entries are style-only and must never be approved as Roz identity.</p><div class="toolbar"><button id="export">Export review JSON</button><button id="clear">Clear local review</button><span id="status"></span></div>{''.join(sections)}</main>
<script>
const KEY='alexandria-cwej-roz-reference-review-v1';
const state=JSON.parse(localStorage.getItem(KEY)||'{{}}');
const cards=[...document.querySelectorAll('.card')];
function save(){{localStorage.setItem(KEY,JSON.stringify(state));document.querySelector('#status').textContent=`Saved ${{Object.keys(state).length}} reviewed rows locally`;}}
for(const card of cards){{const id=card.dataset.id;state[id]=state[id]||{{}};for(const field of card.querySelectorAll('[data-field]')){{const key=field.dataset.field;if(state[id][key]!==undefined)field.value=state[id][key];field.addEventListener('input',()=>{{state[id][key]=field.value;save();}});}}}}
document.querySelector('#export').addEventListener('click',()=>{{const payload={{schema_version:1,round_id:'alexandria_cwej_roz_reference_finalists_v1',exported_at:new Date().toISOString(),rows:state}};const blob=new Blob([JSON.stringify(payload,null,2)+'\\n'],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='cwej-roz-reference-review.json';a.click();URL.revokeObjectURL(a.href);}});
document.querySelector('#clear').addEventListener('click',()=>{{if(confirm('Clear all locally saved review decisions?')){{localStorage.removeItem(KEY);location.reload();}}}});save();
</script></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--candidates", type=Path, default=CANDIDATES_PATH)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    parser.add_argument("--skip-transcription", action="store_true")
    args = parser.parse_args()

    evidence_root = args.evidence_root.resolve()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    candidate_manifest = json.loads(args.candidates.read_text(encoding="utf-8"))
    source_manifest_path = evidence_root / "source-manifest.json"
    if not source_manifest_path.is_file():
        raise FinalistError(f"Source manifest is missing: {source_manifest_path}")
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    sources = source_maps(source_manifest)
    public_paths = load_prepared_public_paths(source_manifest)

    encoder = EcapaSpeakerEncoder(evidence_root)
    seeds, seed_receipts = build_seed_vectors(
        config=config,
        public_paths=public_paths,
        evidence_root=evidence_root,
        encoder=encoder,
    )
    speaker_seed_key = {
        "travis_oliver": "travis_oliver",
        "yasmin_bannerman": "yasmin_bannerman",
        "tnia_miller": "tnia_miller_delivery",
    }
    private_root = evidence_root / "private/reference-finalists"
    review_root = evidence_root / "reference-finalist-review"
    review_audio_root = review_root / "audio"
    rows: list[dict[str, Any]] = []

    for candidate in candidate_manifest["candidates"]:
        source_key = (candidate["source_group"], candidate["source_key"])
        if source_key not in sources:
            raise FinalistError(f"Unknown finalist source: {source_key}")
        raw_path = private_root / "raw" / f"{candidate['id']}.wav"
        extract_clip(
            sources[source_key],
            raw_path,
            float(candidate["start_seconds"]),
            float(candidate["end_seconds"]),
        )
        seed = seeds[speaker_seed_key[candidate["speaker"]]]
        consistency = consistency_metrics(
            clip_path=raw_path,
            target_seed=seed,
            encoder=encoder,
            window_root=private_root / "windows" / candidate["id"],
        )
        consistency["gate"] = consistency_gate(candidate, consistency)

        transcription_path = private_root / "transcriptions" / f"{candidate['id']}.json"
        if transcription_path.is_file():
            transcriptions = json.loads(transcription_path.read_text(encoding="utf-8"))["transcriptions"]
        elif args.skip_transcription:
            transcriptions = [
                {"model": model, "text": candidate["transcript_provisional"], "segments": []}
                for model in TRANSCRIPTION_MODELS
            ]
        else:
            transcriptions = [transcribe_clip(raw_path, model) for model in TRANSCRIPTION_MODELS]
            transcription_path.parent.mkdir(parents=True, exist_ok=True)
            transcription_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "candidate_id": candidate["id"],
                        "clip_sha256": sha256_file(raw_path),
                        "transcriptions": transcriptions,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
        gate = transcript_gate(candidate, transcriptions)
        review_audio = review_audio_root / f"{candidate['id']}.wav"
        review_audio.parent.mkdir(parents=True, exist_ok=True)
        if not review_audio.is_file() or sha256_file(review_audio) != sha256_file(raw_path):
            review_audio.write_bytes(raw_path.read_bytes())
        rows.append(
            {
                "candidate": candidate,
                "raw_path": str(raw_path),
                "raw_sha256": sha256_file(raw_path),
                "audio_metrics": acoustic_metrics(raw_path),
                "speaker_consistency": consistency,
                "transcriptions": transcriptions,
                "transcript_gate": gate,
                "review_audio": str(review_audio.relative_to(review_root)),
            }
        )
        print(
            f"{candidate['id']}: speaker={consistency['gate']['status']} "
            f"transcript={gate['status']}",
            flush=True,
        )

    output = {
        "schema_version": 1,
        "round_id": candidate_manifest["round_id"],
        "generated_at": utc_now(),
        "candidate_manifest": str(args.candidates.resolve()),
        "candidate_manifest_sha256": sha256_file(args.candidates.resolve()),
        "speaker_encoder": {
            "name": encoder.name,
            "revision": encoder.revision,
        },
        "transcription_models": list(TRANSCRIPTION_MODELS),
        "seed_receipts": seed_receipts,
        "rows": rows,
        "approval_status": "human_listening_required",
    }
    output_path = evidence_root / "reference-finalists.json"
    output_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    review_root.mkdir(parents=True, exist_ok=True)
    (review_root / "index.html").write_text(review_html(rows), encoding="utf-8")
    summary = {
        "candidate_count": len(rows),
        "speaker_pass": sum(
            row["speaker_consistency"]["gate"]["status"] == "pass"
            for row in rows
        ),
        "speaker_review_required": sum(
            row["speaker_consistency"]["gate"]["status"] != "pass"
            for row in rows
        ),
        "transcript_listening_required": sum(
            row["transcript_gate"]["status"] != "provisional_match"
            for row in rows
        ),
        "result": str(output_path),
        "review": str(review_root / "index.html"),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
