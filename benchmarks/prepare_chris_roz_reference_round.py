#!/usr/bin/env python3
"""Prepare a blind review pack for Chris Cwej and Roz Forrester references.

The script is research-only. It never changes Alexandria Voice assignments or
production audio. It trims user-owned or explicitly permitted source material,
records hashes and exact trim bounds, checks provisional transcripts with the
pinned local Whisper runtime, measures speaker similarity against clean actor
anchors with WavLM, and builds a self-contained browser review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import soundfile as sf
import torch
from transformers import AutoFeatureExtractor, WavLMForXVector

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "benchmarks/chris_roz_reference_sources.json"
DEFAULT_OUTPUT = ROOT / ".omo/evidence/chris-roz-reference-selection-v1"
WHISPER_REVISION = "1e3e249fb8d01c655324bd6841b1deadffd6d04c"
WHISPER_MODEL = "mlx-community/whisper-base-mlx"
WAVLM_MODEL = "microsoft/wavlm-base-plus-sv"
_WORD_PATTERN = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)


class ReferenceRoundError(RuntimeError):
    """Raised when source, evidence, or runtime requirements are invalid."""


@dataclass(frozen=True)
class PreparedAudio:
    path: Path
    duration_seconds: float
    sample_rate: int
    channels: int
    sha256: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_value(value: Any) -> str:
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def normalized_words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return [word.replace("’", "'") for word in _WORD_PATTERN.findall(normalized)]


def word_error_rate(reference: str, hypothesis: str) -> float:
    left = normalized_words(reference)
    right = normalized_words(hypothesis)
    if not left:
        return 0.0 if not right else 1.0
    previous = list(range(len(right) + 1))
    for row_index, left_word in enumerate(left, start=1):
        current = [row_index]
        for column_index, right_word in enumerate(right, start=1):
            current.append(
                min(
                    previous[column_index - 1] + (left_word != right_word),
                    previous[column_index] + 1,
                    current[column_index - 1] + 1,
                )
            )
        previous = current
    return previous[-1] / len(left)


def resolve_whisper_snapshot() -> Path:
    candidates = [
        Path.home() / ".cache/huggingface/hub/models--mlx-community--whisper-base-mlx/snapshots" / WHISPER_REVISION,
        Path("/Users/tristan/pinokio/cache/HF_HOME/hub/models--mlx-community--whisper-base-mlx/snapshots") / WHISPER_REVISION,
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    raise ReferenceRoundError("Pinned Whisper Base MLX snapshot is not cached.")


def load_whisper() -> Any:
    try:
        import mlx_whisper  # type: ignore
    except Exception as exc:  # pragma: no cover - runtime diagnosis
        raise ReferenceRoundError(f"mlx_whisper unavailable: {type(exc).__name__}") from exc
    return mlx_whisper


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def trim_to_wav(source: Path, target: Path, *, start: float, end: float) -> PreparedAudio:
    if end <= start:
        raise ReferenceRoundError(f"Invalid trim {start:.3f}-{end:.3f}: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not target.is_file():
        raise ReferenceRoundError(completed.stderr[-2000:] or f"ffmpeg failed: {source}")
    info = sf.info(target)
    if info.frames <= 0 or info.samplerate <= 0 or info.channels != 1:
        raise ReferenceRoundError(f"Invalid prepared audio: {target}")
    return PreparedAudio(
        path=target,
        duration_seconds=info.frames / info.samplerate,
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        sha256=sha256_file(target),
    )


def transcribe_clip(whisper: Any, snapshot: Path, path: Path) -> str:
    result = whisper.transcribe(
        str(path),
        path_or_hf_repo=str(snapshot),
        language="en",
        word_timestamps=False,
        condition_on_previous_text=False,
        verbose=False,
    )
    return str(result.get("text") or "").strip()


def read_mono_16k(path: Path) -> np.ndarray:
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout:
        raise ReferenceRoundError(f"Could not decode {path}")
    return np.frombuffer(completed.stdout, dtype="<f4").copy()


def l2_normalize(vector: np.ndarray) -> np.ndarray:
    denominator = float(np.linalg.norm(vector))
    if denominator <= 1e-12:
        return vector
    return vector / denominator


class SpeakerEmbedder:
    def __init__(self, *, cache_dir: Path) -> None:
        self.device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
        self.extractor = AutoFeatureExtractor.from_pretrained(
            WAVLM_MODEL,
            cache_dir=str(cache_dir),
            local_files_only=False,
        )
        self.model = WavLMForXVector.from_pretrained(
            WAVLM_MODEL,
            cache_dir=str(cache_dir),
            local_files_only=False,
        ).to(self.device)
        self.model.eval()

    def _batch_embeddings(self, clips: list[np.ndarray]) -> list[np.ndarray]:
        if not clips:
            return []
        inputs = self.extractor(
            clips,
            sampling_rate=16000,
            padding=True,
            return_tensors="pt",
        )
        input_values = inputs["input_values"].to(self.device)
        attention_mask = inputs.get("attention_mask")
        if attention_mask is not None:
            attention_mask = attention_mask.to(self.device)
        with torch.inference_mode():
            output = self.model(input_values=input_values, attention_mask=attention_mask)
        rows = output.embeddings.detach().float().cpu().numpy()
        return [l2_normalize(row.astype(np.float64)) for row in rows]

    def embed(self, path: Path) -> tuple[np.ndarray, float]:
        waveform = read_mono_16k(path)
        if waveform.size < 16000:
            waveform = np.pad(waveform, (0, 16000 - waveform.size))
        window = 8 * 16000
        hop = 6 * 16000
        chunks: list[np.ndarray] = []
        if waveform.size <= window:
            chunks = [waveform]
        else:
            for start in range(0, max(1, waveform.size - window + 1), hop):
                chunks.append(waveform[start : start + window])
            if chunks and chunks[-1].size and waveform.size - (len(chunks) - 1) * hop > window // 2:
                tail = waveform[-window:]
                if not np.array_equal(tail, chunks[-1]):
                    chunks.append(tail)
        embeddings: list[np.ndarray] = []
        for index in range(0, len(chunks), 8):
            embeddings.extend(self._batch_embeddings(chunks[index : index + 8]))
        centroid = l2_normalize(np.mean(np.stack(embeddings), axis=0))
        if len(embeddings) == 1:
            consistency = 1.0
        else:
            consistency = float(np.mean([np.dot(row, centroid) for row in embeddings]))
        return centroid, consistency


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(l2_normalize(left), l2_normalize(right)))


def acoustic_metrics(path: Path) -> dict[str, float]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if audio.ndim != 1:
        audio = np.mean(audio, axis=1)
    absolute = np.abs(audio)
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64)))) if audio.size else 0.0
    peak = float(np.max(absolute)) if audio.size else 0.0
    rms_dbfs = 20.0 * math.log10(max(rms, 1e-9))
    peak_dbfs = 20.0 * math.log10(max(peak, 1e-9))
    frame = max(1, int(sample_rate * 0.025))
    usable = audio[: (audio.size // frame) * frame]
    if usable.size:
        frame_rms = np.sqrt(np.mean(np.square(usable.reshape(-1, frame), dtype=np.float64), axis=1))
        silence_ratio = float(np.mean(frame_rms < 10 ** (-45 / 20)))
        noise_floor_dbfs = float(20.0 * math.log10(max(float(np.percentile(frame_rms, 10)), 1e-9)))
    else:
        silence_ratio = 1.0
        noise_floor_dbfs = -180.0
    clipping_fraction = float(np.mean(absolute >= 0.999)) if audio.size else 0.0
    crest_factor_db = peak_dbfs - rms_dbfs
    return {
        "rms_dbfs": round(rms_dbfs, 4),
        "peak_dbfs": round(peak_dbfs, 4),
        "noise_floor_dbfs_p10": round(noise_floor_dbfs, 4),
        "silence_ratio": round(silence_ratio, 6),
        "clipping_fraction": round(clipping_fraction, 8),
        "crest_factor_db": round(crest_factor_db, 4),
    }


def source_maps(config: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    owned = {str(row["key"]): dict(row) for row in config["sources"]}
    online = {str(row["key"]): dict(row) for row in config["tnia_style_sources"]}
    return owned, online


def resolve_source(
    config: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    owned: Mapping[str, Mapping[str, Any]],
    online: Mapping[str, Mapping[str, Any]],
) -> Path:
    source_root = Path(str(config["source_root"])).expanduser().resolve()
    online_root = Path(str(config["online_root"])).expanduser().resolve()
    source_kind = str(row.get("source_kind") or "")
    if source_kind == "owned_audio":
        source = owned[str(row["source_key"])]
        return (source_root / str(source["audio"])).resolve()
    if source_kind == "online_audio":
        return (online_root / str(row["audio"])).resolve()
    source_key = str(row.get("source_key") or "")
    if source_key in owned:
        return (source_root / str(owned[source_key]["audio"])).resolve()
    if source_key in online:
        return (online_root / str(online[source_key]["audio"])).resolve()
    raise ReferenceRoundError(f"Unknown source for {row.get('key') or row.get('label')}")


def review_assets(output_root: Path, public_data: Mapping[str, Any]) -> None:
    review = output_root / "review"
    review.mkdir(parents=True, exist_ok=True)
    (review / "data.js").write_text(
        "window.REFERENCE_REVIEW_DATA = "
        + json.dumps(public_data, indent=2, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )
    (review / "index.html").write_text(
        """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Chris and Roz reference review</title><link rel="stylesheet" href="styles.css"></head>
<body><header><p class="eyebrow">Alexandria blind reference review</p><h1>Chris Cwej, Roz Forrester, and Roz style layer</h1>
<p>Judge the audio, not the source title. Identity candidates are compared with a fixed actor anchor; the T'Nia section asks only whether a performance reference would add useful weight or authority to Roz.</p>
<div class="actions"><button id="export">Export scores</button><span id="progress"></span></div></header>
<main id="app"></main><script src="data.js"></script><script src="app.js"></script></body></html>""",
        encoding="utf-8",
    )
    (review / "styles.css").write_text(
        """:root{font-family:Inter,ui-sans-serif,system-ui,sans-serif;color:#27231e;background:#f3efe7}*{box-sizing:border-box}body{margin:0}header,main{max-width:1040px;margin:auto;padding:32px 24px}header{border-bottom:1px solid #d6cec0}.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.12em;color:#6d665c}h1{font:600 34px/1.1 Georgia,serif;margin:.25rem 0 1rem}header>p{max-width:760px;line-height:1.55}.actions{display:flex;gap:16px;align-items:center;margin-top:20px}button{border:1px solid #315c55;background:#315c55;color:white;border-radius:6px;padding:10px 14px;font-weight:650;cursor:pointer}.group{margin:24px 0 44px}.group h2{font:600 25px/1.2 Georgia,serif}.anchor{padding:16px 18px;border-left:4px solid #8a7562;background:#ebe5db;margin:12px 0 18px}.card{background:#fffdf8;border:1px solid #d6cec0;border-radius:10px;padding:18px;margin:14px 0}.card h3{margin:0 0 12px;font-size:16px}.transcript{font-family:Georgia,serif;line-height:1.55;margin:12px 0;color:#403a33}.ratings{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:14px}label{display:grid;gap:5px;font-size:12px;font-weight:650;color:#5e574d}select,input,textarea{font:inherit;border:1px solid #bdb3a5;border-radius:5px;padding:8px;background:white}textarea{min-height:72px;resize:vertical}.retain{display:flex;align-items:center;gap:8px;margin-top:14px;font-size:14px}.retain input{width:18px;height:18px}audio{width:100%}.meta{font-size:12px;color:#746c61;margin-top:8px}.section-note{color:#6d665c;max-width:760px;line-height:1.5}@media(max-width:600px){h1{font-size:28px}header,main{padding:24px 16px}}""",
        encoding="utf-8",
    )
    (review / "app.js").write_text(
        """(() => {
const data=window.REFERENCE_REVIEW_DATA;const key='alexandria-reference-review:'+data.round_id;let state={};try{state=JSON.parse(localStorage.getItem(key)||'{}')}catch(_){state={}};
const app=document.getElementById('app');const scale=()=>'<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option value="${v}">${v}</option>`).join('');
function field(id,name,label){const value=state[id]?.[name]??'';return `<label>${label}<select data-id="${id}" data-name="${name}">${scale()}</select></label>`}
function group(row){const section=document.createElement('section');section.className='group';section.innerHTML=`<h2>${row.label}</h2><p class="section-note">${row.instructions}</p>`;
if(row.anchor){section.innerHTML+=`<div class="anchor"><strong>Fixed comparison anchor</strong><audio controls preload="none" src="${row.anchor.audio}"></audio><p class="transcript">${row.anchor.transcript}</p></div>`}
for(const item of row.candidates){const saved=state[item.id]||{};const card=document.createElement('article');card.className='card';card.innerHTML=`<h3>Candidate ${item.display_id}</h3><audio controls preload="none" src="${item.audio}"></audio><p class="transcript">${item.transcript}</p><div class="ratings">${field(item.id,'identity','Identity / fit')}${field(item.id,'cleanliness','Cleanliness')}${field(item.id,'naturalness','Naturalness')}${field(item.id,'usefulness','Performance usefulness')}</div><label class="retain"><input type="checkbox" data-id="${item.id}" data-name="retain" ${saved.retain?'checked':''}>Retain for model testing</label><label>Notes<textarea data-id="${item.id}" data-name="notes">${saved.notes||''}</textarea></label><div class="meta">${item.duration_seconds.toFixed(2)} seconds · source hidden</div>`;section.appendChild(card)}return section}
for(const row of data.groups)app.appendChild(group(row));
for(const el of document.querySelectorAll('[data-id]')){const id=el.dataset.id,name=el.dataset.name;if(el.tagName==='SELECT'&&state[id]?.[name]!=null)el.value=state[id][name];el.addEventListener('change',save);el.addEventListener('input',save)}
function save(e){const id=e.target.dataset.id,name=e.target.dataset.name;state[id]=state[id]||{};state[id][name]=e.target.type==='checkbox'?e.target.checked:e.target.value;localStorage.setItem(key,JSON.stringify(state));progress()}
function progress(){const total=data.groups.reduce((n,g)=>n+g.candidates.length,0);const done=Object.values(state).filter(r=>r.identity&&r.cleanliness&&r.naturalness&&r.usefulness).length;document.getElementById('progress').textContent=`${done} of ${total} fully scored`}
function download(){const blob=new Blob([JSON.stringify({schema_version:1,round_id:data.round_id,reviewer:'tristan',exported_at:new Date().toISOString(),scores:state},null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=data.round_id+'-tristan.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}
document.getElementById('export').addEventListener('click',download);progress();})();""",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--wavlm-cache",
        default="/private/tmp/alexandria-chris-roz-20260729/hf-cache",
    )
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1:
        raise ReferenceRoundError("Unsupported reference source schema.")
    permission = config.get("permission")
    if not isinstance(permission, Mapping) or permission.get("confirmed_by_user") is not True:
        raise ReferenceRoundError("Explicit permission is not recorded.")

    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    owned, online = source_maps(config)
    whisper = load_whisper()
    whisper_snapshot = resolve_whisper_snapshot()
    embedder = SpeakerEmbedder(cache_dir=Path(args.wavlm_cache).expanduser().resolve())

    anchor_records: dict[str, dict[str, Any]] = {}
    anchor_vectors: dict[str, np.ndarray] = {}
    for identity, rows in config["anchors"].items():
        vectors: list[np.ndarray] = []
        prepared_rows: list[dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            source = resolve_source(config, row, owned=owned, online=online)
            if not source.is_file():
                raise FileNotFoundError(source)
            target = output_root / "anchors" / f"{identity}-{index}.wav"
            prepared = trim_to_wav(
                source,
                target,
                start=float(row["start_seconds"]),
                end=float(row["end_seconds"]),
            )
            vector, consistency = embedder.embed(target)
            vectors.append(vector)
            asr = transcribe_clip(whisper, whisper_snapshot, target)
            prepared_rows.append(
                {
                    "label": row["label"],
                    "audio": str(target.relative_to(output_root)),
                    "audio_sha256": prepared.sha256,
                    "source_sha256": sha256_file(source),
                    "source_path": str(source),
                    "trim": {
                        "start_seconds": row["start_seconds"],
                        "end_seconds": row["end_seconds"],
                    },
                    "transcript": row["transcript"],
                    "pinned_asr_transcript": asr,
                    "word_error_rate": word_error_rate(row["transcript"], asr),
                    "embedding_consistency": consistency,
                    "acoustic_metrics": acoustic_metrics(target),
                }
            )
        anchor_vectors[identity] = l2_normalize(np.mean(np.stack(vectors), axis=0))
        anchor_records[identity] = {
            "identity": identity,
            "entries": prepared_rows,
            "centroid_sha256": hashlib.sha256(anchor_vectors[identity].tobytes()).hexdigest(),
        }

    answer_key: dict[str, Any] = {
        "schema_version": 1,
        "round_id": config["round_id"],
        "anchors": anchor_records,
        "candidates": {},
    }
    public_groups: list[dict[str, Any]] = []
    public_by_identity: dict[str, list[dict[str, Any]]] = {"chris": [], "roz": []}
    candidate_records: list[dict[str, Any]] = []

    for row in config["curated_candidates"]:
        identity = str(row["identity"])
        source = resolve_source(config, row, owned=owned, online=online)
        target = output_root / "clips" / f"{row['key']}.wav"
        prepared = trim_to_wav(
            source,
            target,
            start=float(row["start_seconds"]),
            end=float(row["end_seconds"]),
        )
        vector, consistency = embedder.embed(target)
        asr = transcribe_clip(whisper, whisper_snapshot, target)
        similarities = {name: cosine(vector, anchor) for name, anchor in anchor_vectors.items()}
        target_similarity = similarities[identity]
        other_identity = "roz" if identity == "chris" else "chris"
        margin = target_similarity - similarities[other_identity]
        record = {
            "id": str(row["key"]),
            "identity": identity,
            "source_key": row["source_key"],
            "source_path": str(source),
            "source_sha256": sha256_file(source),
            "trim": {
                "start_seconds": row["start_seconds"],
                "end_seconds": row["end_seconds"],
            },
            "delivery": row["delivery"],
            "audio": str(target.relative_to(output_root)),
            "audio_sha256": prepared.sha256,
            "duration_seconds": prepared.duration_seconds,
            "transcript": row["transcript"],
            "transcript_sha256": hashlib.sha256(str(row["transcript"]).encode("utf-8")).hexdigest(),
            "pinned_asr": {
                "model": WHISPER_MODEL,
                "revision": WHISPER_REVISION,
                "transcript": asr,
                "word_error_rate": word_error_rate(row["transcript"], asr),
                "exact_normalized_text": normalized_words(row["transcript"]) == normalized_words(asr),
            },
            "speaker_measurement": {
                "model": WAVLM_MODEL,
                "target_similarity": target_similarity,
                "other_identity_similarity": similarities[other_identity],
                "identity_margin": margin,
                "within_clip_consistency": consistency,
            },
            "acoustic_metrics": acoustic_metrics(target),
        }
        candidate_records.append(record)
        answer_key["candidates"][record["id"]] = record
        public_by_identity[identity].append(
            {
                "id": record["id"],
                "audio": "../" + record["audio"],
                "duration_seconds": record["duration_seconds"],
                "transcript": record["transcript"],
            }
        )

    style_records: list[dict[str, Any]] = []
    for row in config.get("curated_style_candidates", []):
        source = resolve_source(config, row, owned=owned, online=online)
        target = output_root / "style-clips" / f"{row['key']}.wav"
        prepared = trim_to_wav(
            source,
            target,
            start=float(row["start_seconds"]),
            end=float(row["end_seconds"]),
        )
        vector, consistency = embedder.embed(target)
        asr = transcribe_clip(whisper, whisper_snapshot, target)
        record = {
            "id": str(row["key"]),
            "identity": "tnia_style",
            "source_key": row["source_key"],
            "source_path": str(source),
            "source_sha256": sha256_file(source),
            "trim": {
                "start_seconds": row["start_seconds"],
                "end_seconds": row["end_seconds"],
            },
            "delivery": row["delivery"],
            "audio": str(target.relative_to(output_root)),
            "audio_sha256": prepared.sha256,
            "duration_seconds": prepared.duration_seconds,
            "transcript": row["transcript"],
            "pinned_asr": {
                "model": WHISPER_MODEL,
                "revision": WHISPER_REVISION,
                "transcript": asr,
                "word_error_rate": word_error_rate(row["transcript"], asr),
                "exact_normalized_text": normalized_words(row["transcript"]) == normalized_words(asr),
            },
            "speaker_measurement": {
                "model": WAVLM_MODEL,
                "within_clip_consistency": consistency,
                "roz_anchor_similarity": cosine(vector, anchor_vectors["roz"]),
            },
            "acoustic_metrics": acoustic_metrics(target),
        }
        style_records.append(record)
        answer_key["candidates"][record["id"]] = record

    seed = int(sha256_value(config["round_id"])[:12], 16)
    rng = random.Random(seed)
    for identity, label in (("chris", "Chris Cwej — Travis Oliver"), ("roz", "Roz Forrester — Yasmin Bannerman")):
        candidates = list(public_by_identity[identity])
        rng.shuffle(candidates)
        for index, candidate in enumerate(candidates, start=1):
            candidate["display_id"] = f"{identity[0].upper()}{index:02d}"
        anchor_entry = anchor_records[identity]["entries"][0]
        public_groups.append(
            {
                "key": identity,
                "label": label,
                "instructions": "Score resemblance to the actor/character, technical cleanliness, naturalness, and usefulness as a clone or delivery reference.",
                "anchor": {
                    "audio": "../" + anchor_entry["audio"],
                    "transcript": anchor_entry["transcript"],
                },
                "candidates": candidates,
            }
        )

    style_public = [
        {
            "id": row["id"],
            "audio": "../" + row["audio"],
            "duration_seconds": row["duration_seconds"],
            "transcript": row["transcript"],
        }
        for row in style_records
    ]
    rng.shuffle(style_public)
    for index, candidate in enumerate(style_public, start=1):
        candidate["display_id"] = f"T{index:02d}"
    public_groups.append(
        {
            "key": "tnia_style",
            "label": "Potential T'Nia Miller performance layer for Roz",
            "instructions": "Do not score this as Roz identity. Score whether the delivery offers useful depth, authority, restraint, or gravitas for a separate emotion/style reference without overwhelming Yasmin Bannerman's identity.",
            "anchor": None,
            "candidates": style_public,
        }
    )

    public_data = {
        "schema_version": 1,
        "round_id": config["round_id"],
        "groups": public_groups,
        "answer_key_separate": True,
        "production_promotion_allowed": False,
    }
    review_assets(output_root, public_data)
    write_json(output_root / "private/answer-key.json", answer_key)

    ranked = sorted(
        candidate_records,
        key=lambda row: (
            row["speaker_measurement"]["target_similarity"],
            row["speaker_measurement"]["identity_margin"],
            row["speaker_measurement"]["within_clip_consistency"],
            -row["pinned_asr"]["word_error_rate"],
        ),
        reverse=True,
    )
    manifest = {
        "schema_version": 1,
        "round_id": config["round_id"],
        "config_path": str(config_path),
        "config_sha256": sha256_file(config_path),
        "permission": config["permission"],
        "anchor_count": sum(len(value["entries"]) for value in anchor_records.values()),
        "identity_candidate_count": len(candidate_records),
        "style_candidate_count": len(style_records),
        "recommended_provisional_order": [row["id"] for row in ranked],
        "review": "review/index.html",
        "answer_key": "private/answer-key.json",
        "production_promotion_allowed": False,
        "voice_assignment_changed": False,
        "live_project_audio_changed": False,
        "notes": [
            "ASR text is a verification aid, not a substitute for checking the supplied transcript by ear.",
            "WavLM speaker similarity is supporting evidence only; human listening is authoritative.",
            "T'Nia Miller clips are style-layer candidates and are never mixed into Yasmin Bannerman's identity training pool.",
        ],
    }
    write_json(output_root / "manifest.json", manifest)
    print(json.dumps({"manifest": str(output_root / "manifest.json"), "review": str(output_root / "review/index.html"), "ranked": manifest["recommended_provisional_order"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
