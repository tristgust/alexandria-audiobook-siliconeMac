#!/usr/bin/env python3
"""Build the focused repair round for failed five-recurring-Voice acceptance routes.

The round holds the accepted production-context lines and clean actor identity
anchors fixed. It varies only Chris dry-humour performance conditioning and
Roz urgent-authority control. Public review files contain no backend labels;
private receipts retain complete provenance. No production routing is changed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import shutil
import sys
from typing import Any, Mapping

import numpy as np
import soundfile as sf
from scipy.signal import correlate, correlation_lags, resample_poly

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from responsive_voice_backend import ResponsiveVoiceBackend


ROUND_ID = "alexandria_five_recurring_voice_repair_v1"
DEFAULT_OUTPUT = ROOT / ".omo/evidence/five-recurring-voice-repair-v1"
MOSSFORMER_REPO = "starkdmi/MossFormer2_SE_48K_MLX"
SEED = 130363

CHRIS_TEXT = "Out of interest, what are the punishments for breaking Thrantasian laws?"
ROZ_TEXT = (
    "So, if you lure the Dauntless forces into the crossroads at the center "
    "of the city, you'll be able to attack from above and from behind cover."
)

FISH_REFERENCES = {
    "chris": "631bff1fd20b48e1a4a08db8e936b038",
    "roz": "0a23ec9242bf4a42b88ab69f92aa9816",
}


class RepairRoundError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RepairRoundError(f"{label} could not be read: {exc}") from exc
    if not isinstance(payload, dict):
        raise RepairRoundError(f"{label} must contain an object.")
    return payload


def bank_records(bank: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    identities = bank.get("identity_references")
    performances = bank.get("performance_bank")
    if not isinstance(identities, dict) or not isinstance(performances, dict):
        raise RepairRoundError("Reference bank is incomplete.")
    records: dict[str, dict[str, Any]] = {}
    for character in ("chris", "roz"):
        identity = identities.get(character)
        if not isinstance(identity, dict) or not isinstance(identity.get("clean_actor"), dict):
            raise RepairRoundError(f"Missing clean actor identity for {character}.")
        records[f"{character}:identity"] = dict(identity["clean_actor"])
        rows = performances.get(character)
        if not isinstance(rows, list):
            raise RepairRoundError(f"Missing performance bank for {character}.")
        for row in rows:
            if not isinstance(row, dict):
                continue
            candidate_id = str(row.get("candidate_id") or "").strip()
            if candidate_id:
                records[candidate_id] = dict(row)
    required = {
        "chris:identity",
        "roz:identity",
        "chris_canonical_dry",
        "roz_canonical_tactical_01",
        "roz_vanguard_threat",
    }
    missing = sorted(required - set(records))
    if missing:
        raise RepairRoundError("Reference bank is missing: " + ", ".join(missing))
    return records


def record_path(record: Mapping[str, Any], label: str) -> Path:
    raw = record.get("audio_path") or record.get("final_path")
    path = Path(str(raw or "")).expanduser().resolve()
    expected = str(record.get("audio_sha256") or record.get("final_sha256") or "")
    if not path.is_file():
        raise RepairRoundError(f"{label} is missing: {path}")
    if not expected or sha256_file(path) != expected:
        raise RepairRoundError(f"{label} failed its source fingerprint check.")
    return path


def record_text(record: Mapping[str, Any], label: str) -> str:
    text = str(record.get("transcript") or "").strip()
    if not text:
        raise RepairRoundError(f"{label} has no exact transcript.")
    return text


def read_mono(path: Path) -> tuple[np.ndarray, int]:
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    return np.mean(audio, axis=1, dtype=np.float32), int(rate)


def resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return np.asarray(audio, dtype=np.float32)
    divisor = int(np.gcd(source_rate, target_rate))
    return resample_poly(
        np.asarray(audio, dtype=np.float32),
        target_rate // divisor,
        source_rate // divisor,
    ).astype(np.float32)


def safe_write(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    mono = np.asarray(audio, dtype=np.float32).reshape(-1)
    if mono.size == 0 or not np.all(np.isfinite(mono)):
        raise RepairRoundError(f"Invalid audio for {path.name}.")
    peak = float(np.max(np.abs(mono)))
    target_peak = 10.0 ** (-1.0 / 20.0)
    if peak > target_peak:
        mono = mono * (target_peak / peak)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), mono, int(sample_rate), subtype="PCM_16")


def enhance_reference(source: Path, target: Path) -> dict[str, Any]:
    import mlx.core as mx
    from huggingface_hub import snapshot_download
    from mlx.utils import tree_unflatten
    from mlx_audio.sts.models.mossformer2_se.config import MossFormer2SEConfig
    from mlx_audio.sts.models.mossformer2_se.model import MossFormer2SEModel
    from mlx_audio.sts.models.mossformer2_se.mossformer2_se_wrapper import (
        MossFormer2SE,
    )

    snapshot = Path(
        snapshot_download(
            repo_id=MOSSFORMER_REPO,
            allow_patterns=["model_fp16.safetensors"],
            local_files_only=True,
        )
    ).resolve()
    weights_path = snapshot / "model_fp16.safetensors"
    if not weights_path.is_file():
        raise RepairRoundError(f"MossFormer2 FP16 weights are missing: {weights_path}")
    config = MossFormer2SEConfig()
    network = MossFormer2SE(config)
    weights = mx.load(str(weights_path))
    network.update(tree_unflatten(list(weights.items())))
    model = MossFormer2SEModel(config=config, model=network)
    enhanced = np.asarray(model.enhance(str(source), chunked=False), dtype=np.float32)
    if enhanced.ndim > 1:
        enhanced = enhanced.reshape(-1)
    safe_write(target, enhanced, config.sample_rate)
    return {
        "model": MOSSFORMER_REPO,
        "snapshot": snapshot.name,
        "weights": weights_path.name,
        "weights_sha256": sha256_file(weights_path),
        "source_sha256": sha256_file(source),
        "output_sha256": sha256_file(target),
    }


def aligned_blend(
    original_path: Path,
    enhanced_path: Path,
    target: Path,
    enhanced_weight: float,
) -> dict[str, Any]:
    original, original_rate = read_mono(original_path)
    enhanced, enhanced_rate = read_mono(enhanced_path)
    target_rate = 48000
    original = resample(original, original_rate, target_rate)
    enhanced = resample(enhanced, enhanced_rate, target_rate)
    length = min(len(original), len(enhanced))
    original = original[:length]
    enhanced = enhanced[:length]
    maximum_shift = int(target_rate * 0.04)
    correlation = correlate(enhanced, original, mode="full", method="fft")
    lags = correlation_lags(len(enhanced), len(original), mode="full")
    allowed = np.abs(lags) <= maximum_shift
    lag = int(lags[allowed][np.argmax(correlation[allowed])])
    if lag > 0:
        enhanced = np.pad(enhanced, (0, lag))[lag : lag + length]
    elif lag < 0:
        enhanced = np.pad(enhanced, (-lag, 0))[:length]
    blend = enhanced_weight * enhanced + (1.0 - enhanced_weight) * original
    safe_write(target, blend, target_rate)
    return {
        "enhanced_weight": enhanced_weight,
        "alignment_lag_samples": lag,
        "output_sha256": sha256_file(target),
    }


def audio_metrics(path: Path) -> dict[str, Any]:
    audio, rate = read_mono(path)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64))) if audio.size else 0.0
    return {
        "sha256": sha256_file(path),
        "sample_rate": rate,
        "duration_seconds": len(audio) / rate if rate else 0.0,
        "peak_dbfs": 20.0 * np.log10(max(peak, 1e-12)),
        "rms_dbfs": 20.0 * np.log10(max(rms, 1e-12)),
    }


def index_route(
    *,
    identity_path: Path,
    identity_text: str,
    performance_path: Path,
    performance_text: str,
    strength: float,
) -> dict[str, Any]:
    return {
        "backend": "indextts2_matched_control",
        "identity_audio_path": str(identity_path),
        "identity_text": identity_text,
        "performance_audio_path": str(performance_path),
        "performance_text": performance_text,
        "control": {
            "emotion_strength": strength,
            "diffusion_steps": 8,
            "num_beams": 1,
            "greedy": True,
            "max_mel_tokens": 600,
        },
    }


def fish_route(*, character: str, tag: str) -> dict[str, Any]:
    return {
        "backend": "fish_s2_pro_cloud",
        "control": {
            "reference_id": FISH_REFERENCES[character],
            "api_model_header": "s2.1-pro-free",
            "prompt_mode": "full_alexandria_tag",
            "tag": tag,
            "temperature": 0.7,
            "top_p": 0.7,
            "repetition_penalty": 1.2,
        },
    }


def vox_route(*, identity_path: Path, identity_text: str, instruction: str) -> dict[str, Any]:
    return {
        "backend": "voxcpm2_controllable_clone",
        "identity_audio_path": str(identity_path),
        "identity_text": identity_text,
        "control": {
            "instruction": instruction,
            "cfg_value": 2.0,
            "inference_timesteps": 10,
            "warmup_patches": 0,
            "max_tokens": 1800,
        },
    }


def candidate_id(group: str, key: str, route: Mapping[str, Any]) -> str:
    payload = json.dumps(route, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{ROUND_ID}:{group}:{key}:{payload}".encode()).hexdigest()[:16]


def generate_candidate(
    *,
    backend: ResponsiveVoiceBackend,
    output: Path,
    group: str,
    key: str,
    text: str,
    route: dict[str, Any],
    description: str,
) -> dict[str, Any]:
    identifier = candidate_id(group, key, route)
    audio = output / "audio" / f"{identifier}.wav"
    receipt = backend.generate(route=route, text=text, output_path=audio, seed=SEED)
    if not audio.is_file():
        raise RepairRoundError(f"Candidate {key} produced no audio.")
    return {
        "candidate_id": identifier,
        "group": group,
        "key": key,
        "description": description,
        "text": text,
        "route": route,
        "receipt": receipt,
        "audio_path": str(audio),
        "audio_relative": f"audio/{audio.name}",
        "audio": audio_metrics(audio),
    }


def build_review(output: Path, rows: list[dict[str, Any]]) -> None:
    review = output / "review"
    review.mkdir(parents=True, exist_ok=True)
    groups = {
        "chris_dry_humour": {
            "title": "Chris — dry humour quality repair",
            "instruction": (
                "Choose a version that sounds like Chris, preserves dry humour, "
                "and has no echo, compression, or degraded audio quality."
            ),
            "text": CHRIS_TEXT,
        },
        "roz_urgent_authority": {
            "title": "Roz — urgent authority repair",
            "instruction": (
                "Choose a version that sounds like Roz and clearly communicates "
                "urgent tactical authority, not merely calm exposition."
            ),
            "text": ROZ_TEXT,
        },
    }
    randomizer = random.Random(20260730)
    public_groups = []
    for group_key, metadata in groups.items():
        candidates = [row for row in rows if row["group"] == group_key]
        randomizer.shuffle(candidates)
        public_groups.append(
            {
                "group": group_key,
                **metadata,
                "candidates": [
                    {
                        "candidate_id": row["candidate_id"],
                        "display_id": f"{group_key[:1].upper()}{index:02d}",
                        "audio": "../" + row["audio_relative"],
                    }
                    for index, row in enumerate(candidates, start=1)
                ],
            }
        )
    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "groups": public_groups,
    }
    (review / "data.js").write_text(
        "window.ALEXANDRIA_RECURRING_REPAIR = "
        + json.dumps(public, indent=2, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )
    (review / "index.html").write_text(
        """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Recurring Voice repair</title><link rel='icon' href='data:,'><style>:root{font-family:Inter,system-ui,sans-serif;color:#29251f;background:#f1eee7}*{box-sizing:border-box}body{margin:0}header,main{max-width:980px;margin:auto;padding:28px 20px}header{border-bottom:1px solid #d4ccbf}h1,h2,h3{font-family:Georgia,serif}.group{margin:32px 0}.candidate{background:#fffdf8;border:1px solid #d5cdbf;border-radius:10px;padding:18px;margin:16px 0}.meta{font-size:13px;color:#6d655b}.ratings{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:12px 0}label{display:grid;gap:5px;font-size:13px;font-weight:650}select,textarea{font:inherit;padding:8px;border:1px solid #b9afa2;border-radius:5px;background:white}textarea{width:100%;min-height:68px}.decision{display:flex;gap:14px;flex-wrap:wrap;margin:12px 0}.decision label{display:flex;align-items:center;gap:7px;border:1px solid #b9afa2;border-radius:6px;padding:8px;background:white}audio{width:100%}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}@media(max-width:650px){.ratings{grid-template-columns:1fr}}</style></head><body><header><p>Alexandria focused production repair</p><h1>Chris and Roz acceptance blockers</h1><p>Backend and cleanup identities are hidden. Score every candidate, then export the review.</p><button id='export'>Export review</button> <span id='progress'></span></header><main id='app'></main><script src='data.js'></script><script src='app.js'></script></body></html>""",
        encoding="utf-8",
    )
    (review / "app.js").write_text(
        """(()=>{'use strict';const d=window.ALEXANDRIA_RECURRING_REPAIR,key='alexandria-recurring-repair:'+d.round_id;let saved={};try{saved=JSON.parse(localStorage.getItem(key)||'{}')}catch(_){saved={}}const app=document.querySelector('#app'),scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join('');for(const g of d.groups){const section=document.createElement('section');section.className='group';section.innerHTML=`<h2>${g.title}</h2><p>${g.instruction}</p><p><strong>Line:</strong> ${g.text}</p>`;app.appendChild(section);for(const c of g.candidates){const x=saved[c.candidate_id]||{},el=document.createElement('article');el.className='candidate';el.innerHTML=`<p class="meta">Candidate ${c.display_id}</p><h3>${c.display_id}</h3><audio controls preload="none" src="${c.audio}"></audio><div class="ratings"><label>Identity<select data-id="${c.candidate_id}" data-name="identity">${scale}</select></label><label>Audio quality<select data-id="${c.candidate_id}" data-name="quality">${scale}</select></label><label>Delivery fit<select data-id="${c.candidate_id}" data-name="delivery">${scale}</select></label></div><div class="decision"><label><input type="radio" name="decision-${c.candidate_id}" value="pass" data-id="${c.candidate_id}" data-name="decision">Pass</label><label><input type="radio" name="decision-${c.candidate_id}" value="fail" data-id="${c.candidate_id}" data-name="decision">Fail</label></div><label>Notes<textarea data-id="${c.candidate_id}" data-name="notes"></textarea></label>`;section.appendChild(el)}}for(const e of document.querySelectorAll('[data-id]')){const x=saved[e.dataset.id]||{},v=x[e.dataset.name];if(e.type==='radio')e.checked=v===e.value;else if(v!=null)e.value=v;e.addEventListener(e.tagName==='TEXTAREA'?'input':'change',event=>{const t=event.target,id=t.dataset.id,n=t.dataset.name;saved[id]=saved[id]||{};saved[id][n]=t.type==='radio'?t.value:t.value;localStorage.setItem(key,JSON.stringify(saved));progress()})}function progress(){const total=d.groups.reduce((n,g)=>n+g.candidates.length,0),done=Object.values(saved).filter(x=>x.identity&&x.quality&&x.delivery&&x.decision).length;document.querySelector('#progress').textContent=`${done} of ${total} reviewed`}document.querySelector('#export').onclick=()=>{const payload={schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),results:saved};const a=document.createElement('a'),b=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();""",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-bank", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    bank_path = args.reference_bank.expanduser().resolve()
    output = args.output_root.expanduser().resolve()
    if output.exists():
        shutil.rmtree(output)
    (output / "audio").mkdir(parents=True)
    private = output / "private"
    private.mkdir(parents=True)

    bank = read_json(bank_path, "Chris/Roz reference bank")
    records = bank_records(bank)
    chris_identity = record_path(records["chris:identity"], "Chris clean identity")
    chris_identity_text = record_text(records["chris:identity"], "Chris clean identity")
    roz_identity = record_path(records["roz:identity"], "Roz clean identity")
    roz_identity_text = record_text(records["roz:identity"], "Roz clean identity")
    chris_dry = record_path(records["chris_canonical_dry"], "Chris dry prompt")
    chris_dry_text = record_text(records["chris_canonical_dry"], "Chris dry prompt")
    roz_tactical = record_path(records["roz_canonical_tactical_01"], "Roz tactical prompt")
    roz_tactical_text = record_text(records["roz_canonical_tactical_01"], "Roz tactical prompt")
    roz_threat = record_path(records["roz_vanguard_threat"], "Roz threat prompt")
    roz_threat_text = record_text(records["roz_vanguard_threat"], "Roz threat prompt")

    repaired = private / "references" / "chris_dry_mossformer2.wav"
    blend = private / "references" / "chris_dry_mossformer2_blend70.wav"
    enhancement = enhance_reference(chris_dry, repaired)
    blending = aligned_blend(chris_dry, repaired, blend, 0.70)

    backend = ResponsiveVoiceBackend()
    rows: list[dict[str, Any]] = []
    try:
        chris_candidates = [
            (
                "index_current",
                index_route(
                    identity_path=chris_identity,
                    identity_text=chris_identity_text,
                    performance_path=chris_dry,
                    performance_text=chris_dry_text,
                    strength=0.75,
                ),
                "Current failed IndexTTS2 route; blind baseline.",
            ),
            (
                "index_mossformer2",
                index_route(
                    identity_path=chris_identity,
                    identity_text=chris_identity_text,
                    performance_path=repaired,
                    performance_text=chris_dry_text,
                    strength=0.75,
                ),
                "IndexTTS2 with fully enhanced dry-performance prompt.",
            ),
            (
                "index_mossformer2_blend70",
                index_route(
                    identity_path=chris_identity,
                    identity_text=chris_identity_text,
                    performance_path=blend,
                    performance_text=chris_dry_text,
                    strength=0.75,
                ),
                "IndexTTS2 with 70% enhanced / 30% original aligned prompt.",
            ),
            (
                "fish_dry",
                fish_route(
                    character="chris",
                    tag=(
                        "Speak with dry, understated humour and amused disbelief; "
                        "keep the irony controlled and conversational."
                    ),
                ),
                "Fish S2.1 Pro Free dry-humour alternative using clean identity.",
            ),
        ]
        for key, route, description in chris_candidates:
            rows.append(
                generate_candidate(
                    backend=backend,
                    output=output,
                    group="chris_dry_humour",
                    key=key,
                    text=CHRIS_TEXT,
                    route=route,
                    description=description,
                )
            )

        roz_candidates = [
            (
                "index_current",
                index_route(
                    identity_path=roz_identity,
                    identity_text=roz_identity_text,
                    performance_path=roz_tactical,
                    performance_text=roz_tactical_text,
                    strength=0.85,
                ),
                "Current failed-mode IndexTTS2 route; blind baseline.",
            ),
            (
                "index_tactical_full",
                index_route(
                    identity_path=roz_identity,
                    identity_text=roz_identity_text,
                    performance_path=roz_tactical,
                    performance_text=roz_tactical_text,
                    strength=1.0,
                ),
                "IndexTTS2 tactical prompt at full conditioning strength.",
            ),
            (
                "index_threat_full",
                index_route(
                    identity_path=roz_identity,
                    identity_text=roz_identity_text,
                    performance_path=roz_threat,
                    performance_text=roz_threat_text,
                    strength=1.0,
                ),
                "IndexTTS2 stronger threat/authority prompt at full strength.",
            ),
            (
                "vox_urgent",
                vox_route(
                    identity_path=roz_identity,
                    identity_text=roz_identity_text,
                    instruction=(
                        "Deliver as an immediate tactical command with clipped precision, "
                        "high urgency, decisive authority, and no reflective softness."
                    ),
                ),
                "VoxCPM2 controllable-clone urgency alternative.",
            ),
            (
                "fish_urgent",
                fish_route(
                    character="roz",
                    tag=(
                        "Speak with immediate tactical urgency, clipped precision, "
                        "decisive authority, and sustained command."
                    ),
                ),
                "Fish S2.1 Pro Free urgency alternative using clean identity.",
            ),
        ]
        for key, route, description in roz_candidates:
            rows.append(
                generate_candidate(
                    backend=backend,
                    output=output,
                    group="roz_urgent_authority",
                    key=key,
                    text=ROZ_TEXT,
                    route=route,
                    description=description,
                )
            )
    finally:
        backend.close()

    answer = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "generated_at": utc_now(),
        "reference_bank": str(bank_path),
        "enhancement": enhancement,
        "blend": blending,
        "candidates": {row["candidate_id"]: row for row in rows},
        "production_routing_changed": False,
    }
    write_json(private / "answer-key.json", answer)
    write_json(
        output / "generation-summary.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "generated_at": answer["generated_at"],
            "candidate_count": len(rows),
            "groups": {
                "chris_dry_humour": sum(row["group"] == "chris_dry_humour" for row in rows),
                "roz_urgent_authority": sum(row["group"] == "roz_urgent_authority" for row in rows),
            },
            "all_text_verification_passed": all(
                bool((row.get("receipt") or {}).get("text_verification")) for row in rows
            ),
            "production_routing_changed": False,
        },
    )
    build_review(output, rows)
    print(
        json.dumps(
            {
                "output": str(output),
                "candidate_count": len(rows),
                "review": str(output / "review/index.html"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
