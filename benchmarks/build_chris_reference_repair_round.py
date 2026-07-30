#!/usr/bin/env python3
"""Build a blind dereverberation repair round for Chris's canonical reference."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import shutil
from pathlib import Path
from typing import Any

import mlx_whisper
import numpy as np
import soundfile as sf
import torch
from clearvoice import ClearVoice
from nara_wpe.utils import istft, stft
from nara_wpe.wpe import wpe
from scipy.signal import correlate, correlation_lags, resample_poly
from speechbrain.inference.classifiers import EncoderClassifier
from srmrpy import srmr

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / ".omo/evidence/chris-canonical-reference-repair-v1"
RAW = Path(
    "/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-f3df5335/"
    ".omo/evidence/chris-roz-cleanup-v1/private/raw-normalized/"
    "chris-35-trial_time_machine-55.wav"
)
DEMUCS = Path(
    "/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-f3df5335/"
    ".omo/evidence/chris-roz-cleanup-v1/cleaned/"
    "chris-35-trial_time_machine-55.wav"
)
ECAPA_MODEL = Path(
    "/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-c2133ab9/"
    ".omo/evidence/cwej-roz-voice-evaluation/private/models/"
    "spkrec-ecapa-voxceleb-0f99f2d0"
)
SEED_ROOT = Path(
    "/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-c2133ab9/"
    ".omo/evidence/cwej-roz-voice-evaluation/private/retrieval/seeds/"
    "travis_oliver"
)
EXPECTED = "And I can see a few aliens and a couple of robots, but not many."
WHISPER = "mlx-community/whisper-large-v3-turbo"
TARGET_RATE = 24000


class RepairError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalized_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.casefold())


def word_error_rate(expected: str, observed: str) -> float:
    left = normalized_words(expected)
    right = normalized_words(observed)
    previous = list(range(len(right) + 1))
    for index, left_word in enumerate(left, start=1):
        current = [index]
        for right_index, right_word in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_word != right_word),
                )
            )
        previous = current
    return previous[-1] / max(1, len(left))


def read_mono(path: Path) -> tuple[np.ndarray, int]:
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    return np.mean(audio, axis=1).astype(np.float32), int(rate)


def resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return np.asarray(audio, dtype=np.float32)
    return resample_poly(audio, target_rate, source_rate).astype(np.float32)


def normalize_write(audio: np.ndarray, rate: int, target: Path) -> None:
    mono = resample(np.asarray(audio, dtype=np.float32).reshape(-1), rate, TARGET_RATE)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if peak > 0.98:
        mono = mono * (0.98 / peak)
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(target), mono, TARGET_RATE, subtype="PCM_16")


def wpe_variant(source: Path, target: Path, taps: int, delay: int) -> None:
    audio, rate = read_mono(source)
    work = resample(audio, rate, 16000)
    y = stft(work[None, :], size=512, shift=128)
    x = np.copy(y)
    _, _, frequencies = y.shape
    for frequency in range(frequencies):
        x[:, :, frequency] = wpe(
            y[:, :, frequency],
            taps=taps,
            delay=delay,
            iterations=3,
            statistics_mode="full",
        )
    time_signal = istft(x, size=512, shift=128)[0]
    normalize_write(time_signal[: len(work)], 16000, target)


def clearvoice_variant(model_name: str, source: Path, target: Path) -> None:
    rates = {
        "FRCRN_SE_16K": 16000,
        "MossFormer2_SE_48K": 48000,
        "MossFormerGAN_SE_16K": 16000,
    }
    model = ClearVoice(task="speech_enhancement", model_names=[model_name])
    result = model(input_path=str(source), online_write=False)
    value = np.asarray(result, dtype=np.float32)
    if value.ndim > 1:
        value = value[0]
    normalize_write(value, rates[model_name], target)


def aligned_blend(dry_path: Path, enhanced_path: Path, target: Path, enhanced_weight: float) -> None:
    dry, dry_rate = read_mono(dry_path)
    enhanced, enhanced_rate = read_mono(enhanced_path)
    dry = resample(dry, dry_rate, TARGET_RATE)
    enhanced = resample(enhanced, enhanced_rate, TARGET_RATE)
    limit = min(len(dry), len(enhanced))
    dry = dry[:limit]
    enhanced = enhanced[:limit]
    max_shift = int(TARGET_RATE * 0.04)
    corr = correlate(enhanced, dry, mode="full", method="fft")
    lags = correlation_lags(len(enhanced), len(dry), mode="full")
    allowed = np.abs(lags) <= max_shift
    lag = int(lags[allowed][np.argmax(corr[allowed])])
    if lag > 0:
        enhanced = np.pad(enhanced, (0, lag))[lag : lag + limit]
    elif lag < 0:
        enhanced = np.pad(enhanced, (-lag, 0))[:limit]
    blended = enhanced_weight * enhanced + (1.0 - enhanced_weight) * dry
    normalize_write(blended, TARGET_RATE, target)


def load_16k(path: Path) -> torch.Tensor:
    audio, rate = read_mono(path)
    return torch.from_numpy(resample(audio, rate, 16000))[None, :]


def embedding(model: EncoderClassifier, path: Path) -> np.ndarray:
    with torch.no_grad():
        vector = model.encode_batch(load_16k(path)).squeeze().cpu().numpy()
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    return vector / (np.linalg.norm(vector) + 1e-12)


def metrics(path: Path, speaker_model: EncoderClassifier, seed: np.ndarray) -> dict[str, Any]:
    audio, rate = read_mono(path)
    transcript = mlx_whisper.transcribe(
        str(path),
        path_or_hf_repo=WHISPER,
        language="en",
        condition_on_previous_text=False,
        word_timestamps=False,
        verbose=False,
    )
    observed = str(transcript.get("text") or "").strip()
    srmr_value = float(srmr(audio, rate, fast=True, norm=False)[0])
    tail_frames = max(1, int(rate * 0.16))
    before = audio[-2 * tail_frames : -tail_frames]
    tail = audio[-tail_frames:]
    before_rms = float(np.sqrt(np.mean(np.square(before), dtype=np.float64))) if before.size else 0.0
    tail_rms = float(np.sqrt(np.mean(np.square(tail), dtype=np.float64))) if tail.size else 0.0
    tail_drop = 20.0 * math.log10(max(before_rms, 1e-9) / max(tail_rms, 1e-9))
    speaker = float(np.dot(seed, embedding(speaker_model, path)))
    return {
        "audio_sha256": sha256_file(path),
        "duration_seconds": float(len(audio) / rate),
        "srmr": srmr_value,
        "tail_drop_db": tail_drop,
        "speaker_cosine": speaker,
        "transcript": observed,
        "word_error_rate": word_error_rate(EXPECTED, observed),
        "exact_transcript": normalized_words(EXPECTED) == normalized_words(observed),
    }


def zscores(rows: list[dict[str, Any]], field: str) -> dict[str, float]:
    values = np.asarray([float(row["metrics"][field]) for row in rows], dtype=np.float64)
    std = float(values.std())
    if std < 1e-9:
        return {str(row["key"]): 0.0 for row in rows}
    mean = float(values.mean())
    return {str(row["key"]): (float(row["metrics"][field]) - mean) / std for row in rows}


def build_review(output: Path, rows: list[dict[str, Any]]) -> None:
    review = output / "review"
    audio_root = review / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    public_rows = []
    answer = {"schema_version": 1, "round_id": "chris_canonical_reference_repair_v1", "candidates": {}}
    shuffled = list(rows)
    random.Random(20260729).shuffle(shuffled)
    for index, row in enumerate(shuffled, start=1):
        blind = hashlib.sha256(f"repair:{row['key']}:{row['metrics']['audio_sha256']}".encode()).hexdigest()[:16]
        target = audio_root / f"{blind}.wav"
        shutil.copy2(row["path"], target)
        public_rows.append({"id": blind, "display_id": f"C{index:02d}", "audio": f"audio/{blind}.wav", "transcript": EXPECTED})
        answer["candidates"][blind] = row
    public = {"schema_version": 1, "round_id": answer["round_id"], "candidates": public_rows}
    (review / "data.js").write_text("window.CHRIS_REFERENCE_REPAIR = " + json.dumps(public, indent=2) + ";\n", encoding="utf-8")
    (review / "index.html").write_text(
        """<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Chris reference repair</title><link rel='icon' href='data:,'><link rel='stylesheet' href='styles.css'></head><body><header><p class='eyebrow'>Alexandria blind cleanup review</p><h1>Chris canonical reference repair</h1><p>Judge which version removes echo and background coloration without changing Travis Oliver or making the speech synthetic.</p><div><button id='export'>Export results</button> <span id='progress'></span></div></header><main id='app'></main><script src='data.js'></script><script src='app.js'></script></body></html>""",
        encoding="utf-8",
    )
    (review / "styles.css").write_text(
        """:root{font-family:Inter,system-ui,sans-serif;color:#2b2721;background:#f2eee6}*{box-sizing:border-box}body{margin:0}header,main{max-width:920px;margin:auto;padding:28px 20px}header{border-bottom:1px solid #d3cabd}.eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:12px;color:#6c645a}h1{font-family:Georgia,serif}.card{background:#fffdf8;border:1px solid #d5cdbf;border-radius:10px;padding:18px;margin:16px 0}.transcript{font:17px/1.5 Georgia,serif}audio{width:100%}.ratings{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}label{display:grid;gap:5px;font-size:13px;font-weight:650}select,textarea{font:inherit;padding:8px;border:1px solid #b9afa2;border-radius:5px;background:white}textarea{min-height:70px}.retain{display:flex;align-items:center;gap:8px;margin:12px 0}.retain input{width:18px;height:18px}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}@media(max-width:650px){.ratings{grid-template-columns:1fr}}""",
        encoding="utf-8",
    )
    (review / "app.js").write_text(
        """(()=>{const d=window.CHRIS_REFERENCE_REPAIR,k='chris-reference-repair:'+d.round_id;let s={};try{s=JSON.parse(localStorage.getItem(k)||'{}')}catch(_){s={}}const app=document.getElementById('app'),scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join('');for(const c of d.candidates){const x=s[c.id]||{},card=document.createElement('article');card.className='card';card.innerHTML=`<h2>Candidate ${c.display_id}</h2><audio controls preload="none" src="${c.audio}"></audio><p class="transcript">${c.transcript}</p><div class="ratings"><label>Dryness / echo removal<select data-id="${c.id}" data-name="dryness">${scale}</select></label><label>Chris identity<select data-id="${c.id}" data-name="identity">${scale}</select></label><label>Naturalness<select data-id="${c.id}" data-name="naturalness">${scale}</select></label></div><label class="retain"><input type="checkbox" data-id="${c.id}" data-name="retain">Retain</label><label>Notes<textarea data-id="${c.id}" data-name="notes"></textarea></label>`;app.appendChild(card)}for(const e of document.querySelectorAll('[data-id]')){const id=e.dataset.id,n=e.dataset.name,v=s[id]?.[n];if(e.type==='checkbox')e.checked=Boolean(v);else if(v!=null)e.value=v;e.addEventListener(e.tagName==='TEXTAREA'?'input':'change',save)}function save(e){const id=e.target.dataset.id,n=e.target.dataset.name;s[id]=s[id]||{};s[id][n]=e.target.type==='checkbox'?e.target.checked:e.target.value;localStorage.setItem(k,JSON.stringify(s));progress()}function progress(){const done=Object.values(s).filter(x=>x.dryness&&x.identity&&x.naturalness).length;document.getElementById('progress').textContent=`${done} of ${d.candidates.length} scored`}document.getElementById('export').onclick=()=>{const a=document.createElement('a'),b=new Blob([JSON.stringify({schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),scores:s},null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();""",
        encoding="utf-8",
    )
    write_json(output / "private/answer-key.json", answer)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    output = Path(args.output_root).expanduser().resolve()
    if output.exists():
        shutil.rmtree(output)
    variants = output / "private/variants"
    variants.mkdir(parents=True, exist_ok=True)
    if not RAW.is_file() or not DEMUCS.is_file():
        raise FileNotFoundError(RAW if not RAW.is_file() else DEMUCS)

    sources = {"raw": RAW, "demucs": DEMUCS}
    specs = [
        ("raw_baseline", "copy", "raw", {}),
        ("current_demucs", "copy", "demucs", {}),
        ("wpe_demucs_t10_d3", "wpe", "demucs", {"taps": 10, "delay": 3}),
        ("wpe_demucs_t20_d3", "wpe", "demucs", {"taps": 20, "delay": 3}),
        ("wpe_raw_t10_d3", "wpe", "raw", {"taps": 10, "delay": 3}),
        ("mossformer2_demucs", "clearvoice", "demucs", {"model": "MossFormer2_SE_48K"}),
        ("mossformer2_raw", "clearvoice", "raw", {"model": "MossFormer2_SE_48K"}),
        ("frcrn_demucs", "clearvoice", "demucs", {"model": "FRCRN_SE_16K"}),
        ("frcrn_raw", "clearvoice", "raw", {"model": "FRCRN_SE_16K"}),
        ("mossformergan_demucs", "clearvoice", "demucs", {"model": "MossFormerGAN_SE_16K"}),
    ]
    paths: dict[str, Path] = {}
    for key, method, source_key, settings in specs:
        target = variants / f"{key}.wav"
        source = sources[source_key]
        if method == "copy":
            audio, rate = read_mono(source)
            normalize_write(audio, rate, target)
        elif method == "wpe":
            wpe_variant(source, target, int(settings["taps"]), int(settings["delay"]))
        else:
            clearvoice_variant(str(settings["model"]), source, target)
        paths[key] = target
        print(json.dumps({"generated": key, "path": str(target)}), flush=True)

    blend_specs = [
        ("mossformer2_blend_50", "mossformer2_demucs", 0.50),
        ("mossformer2_blend_70", "mossformer2_demucs", 0.70),
        ("mossformergan_blend_50", "mossformergan_demucs", 0.50),
        ("mossformergan_blend_70", "mossformergan_demucs", 0.70),
    ]
    for key, enhanced_key, weight in blend_specs:
        target = variants / f"{key}.wav"
        aligned_blend(paths["current_demucs"], paths[enhanced_key], target, weight)
        paths[key] = target
        specs.append((key, "aligned_blend", "demucs", {"enhanced": enhanced_key, "enhanced_weight": weight}))
        print(json.dumps({"generated": key, "path": str(target)}), flush=True)

    speaker_model = EncoderClassifier.from_hparams(source=str(ECAPA_MODEL))
    seed_vectors = [embedding(speaker_model, path) for path in sorted(SEED_ROOT.glob("*.wav"))]
    seed = np.mean(np.stack(seed_vectors), axis=0)
    seed = seed / (np.linalg.norm(seed) + 1e-12)
    rows = []
    for key, method, source_key, settings in specs:
        row = {
            "key": key,
            "method": method,
            "source": source_key,
            "settings": settings,
            "path": str(paths[key]),
            "metrics": metrics(paths[key], speaker_model, seed),
        }
        rows.append(row)
        print(json.dumps({"measured": key, **row["metrics"]}, ensure_ascii=False), flush=True)

    raw_cosine = next(row["metrics"]["speaker_cosine"] for row in rows if row["key"] == "raw_baseline")
    eligible = [
        row for row in rows
        if row["metrics"]["word_error_rate"] <= 0.10
        and row["metrics"]["speaker_cosine"] >= raw_cosine - 0.08
    ]
    srmr_z = zscores(eligible, "srmr")
    tail_z = zscores(eligible, "tail_drop_db")
    speaker_z = zscores(eligible, "speaker_cosine")
    for row in rows:
        row["eligible"] = row in eligible
        row["rank_score"] = (
            srmr_z.get(row["key"], -99.0)
            + 0.45 * tail_z.get(row["key"], -99.0)
            + 0.55 * speaker_z.get(row["key"], -99.0)
        )
    ranked = sorted(rows, key=lambda row: (row["eligible"], row["rank_score"]), reverse=True)
    write_json(output / "metrics.json", {"schema_version": 1, "expected_transcript": EXPECTED, "rows": rows, "ranking": [row["key"] for row in ranked]})
    review_rows = [row for row in rows if row["eligible"]]
    build_review(output, review_rows)
    write_json(output / "manifest.json", {
        "schema_version": 1,
        "round_id": "chris_canonical_reference_repair_v1",
        "generated_variant_count": len(rows),
        "candidate_count": len(review_rows),
        "excluded_variants": [row["key"] for row in rows if not row["eligible"]],
        "provisional_winner": ranked[0]["key"],
        "provisional_winner_path": ranked[0]["path"],
        "review": "review/index.html",
        "private_answer_key": "private/answer-key.json",
        "production_promotion_allowed": False,
    })
    print(json.dumps({"output": str(output), "generated_variants": len(rows), "review_candidates": len(review_rows), "provisional_winner": ranked[0]["key"], "ranking": [row["key"] for row in ranked]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
