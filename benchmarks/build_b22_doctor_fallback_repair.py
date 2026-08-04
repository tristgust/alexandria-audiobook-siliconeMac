#!/usr/bin/env python3
"""Build a minimal blind Doctor fallback-reference comparison.

The round reuses the two B18 dry/eccentric anchors and generates exactly three
additional local Qwen candidates from existing strict-approved Doctor routes.
It performs objective text screening, exposes one best-or-none choice, and
never mutates production routing or the live project.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import sys
from typing import Any, Callable, Mapping

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
BENCHMARKS = ROOT / "benchmarks"
for value in (APP, BENCHMARKS):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from mlx_backend import MLXBackend  # noqa: E402
from model_registry import model_cache_status  # noqa: E402
from transcription_evaluator import evaluate_transcriptions  # noqa: E402


ROUND_ID = "b22_doctor_fallback_repair_20260804"
REQUEST_LABEL_PREFIX = "doctor-fallback"
SAMPLE_ID_PREFIX = "DFR"
SEED = 130363
MAX_WORD_ERROR_RATE = 0.20
TARGET_TEXT = "I left him my scarf, but it clashes with his plumage."
TARGET_INSTRUCTION = (
    "Dryly amused, wry and eccentric, with clipped precision and "
    "understated authority."
)
DEFAULT_PROJECT = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Alexandria"
    / "Projects"
    / "original-sin--e6286665"
)
DEFAULT_OUTPUT = Path("/Users/tristan/Downloads/b22_doctor_fallback_repair_20260804")
B18_REVIEW = (
    ROOT
    / ".omo"
    / "evidence"
    / "b18-multivoice-archetype-screen-20260803"
    / "review"
)
REFERENCE_SOURCE = B18_REVIEW / "reference" / "doc_reference.mp3"
REFERENCE_SHA256 = "68915ea38222929e432ad1c8a3b45471d2efe0f9977093468ad8d934a823a964"


ANCHOR_SPECS: tuple[dict[str, str], ...] = (
    {
        "method": "b18_neutral_identity_anchor",
        "source": str(B18_REVIEW / "audio" / "DOC02.wav"),
        "sha256": "28d67c2a3d6f44ecdb1df2cb3519609e3dcf1e59bc4666e05a36fe1073662188",
    },
    {
        "method": "b18_current_route_anchor",
        "source": str(B18_REVIEW / "audio" / "DOC03.wav"),
        "sha256": "49028cfafd291a3795b38883a4a8eea653282360cf778b5e3455d40661247856",
    },
)

REFERENCE_ROUTE_KEYS = (
    "doctor_comic_disorientation",
    "doctor_acf_dismissive_contempt",
    "approved_adaptation_7cbcd727cdf85517",
)


class DoctorFallbackRepairError(RuntimeError):
    pass


Generator = Callable[..., dict[str, Any] | None]
Evaluator = Callable[[list[dict[str, Any]]], dict[str, Any]]


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DoctorFallbackRepairError(f"Could not read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise DoctorFallbackRepairError(f"{label} must contain an object.")
    return value


def _verify(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise DoctorFallbackRepairError(f"{label} is missing: {path}")
    actual = sha256_file(path)
    if actual != expected:
        raise DoctorFallbackRepairError(
            f"{label} changed: expected {expected}, received {actual}."
        )


def _resolve_project_asset(project: Path, relative: Any, label: str) -> Path:
    text = str(relative or "").strip()
    if not text:
        raise DoctorFallbackRepairError(f"{label} path is missing.")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = project / candidate
    path = candidate.resolve()
    if not path.is_file():
        raise DoctorFallbackRepairError(f"{label} is missing: {path}")
    return path


def _audio_record(path: Path) -> dict[str, Any]:
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    if mono.size == 0 or not np.all(np.isfinite(mono)):
        raise DoctorFallbackRepairError(f"Audio is empty or invalid: {path}")
    peak = float(np.max(np.abs(mono)))
    rms = float(np.sqrt(np.mean(np.square(mono), dtype=np.float64)))
    return {
        "sha256": sha256_file(path),
        "sample_rate": int(rate),
        "channels": int(audio.shape[1]),
        "frames": int(audio.shape[0]),
        "duration_seconds": float(audio.shape[0] / float(rate)),
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-12)),
        "rms_dbfs": 20.0 * math.log10(max(rms, 1e-12)),
    }


def _doctor_contract(project: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = _read_json(project / "voice_config.json", "Voice configuration")
    voice = config.get("THE DOCTOR")
    if not isinstance(voice, dict):
        raise DoctorFallbackRepairError("THE DOCTOR Voice configuration is missing.")
    policy = voice.get("responsive_backend_routing")
    if not isinstance(policy, Mapping):
        raise DoctorFallbackRepairError("THE DOCTOR responsive routing is missing.")
    routes = policy.get("routes")
    if not isinstance(routes, Mapping):
        raise DoctorFallbackRepairError("THE DOCTOR route map is missing.")
    references: list[dict[str, Any]] = []
    for route_key in REFERENCE_ROUTE_KEYS:
        raw = routes.get(route_key)
        if not isinstance(raw, Mapping):
            raise DoctorFallbackRepairError(f"Doctor route is missing: {route_key}")
        if raw.get("backend") != "qwen3_instruction_controlled":
            raise DoctorFallbackRepairError(
                f"Doctor route is not a Qwen route: {route_key}"
            )
        if raw.get("approval_tier") != "strict" or not raw.get(
            "production_promotion_allowed"
        ):
            raise DoctorFallbackRepairError(
                f"Doctor route is not strict-approved: {route_key}"
            )
        if raw.get("effect_chain") is not None:
            raise DoctorFallbackRepairError(
                f"Doctor reference route unexpectedly uses processing: {route_key}"
            )
        source = _resolve_project_asset(
            project,
            raw.get("identity_audio"),
            f"Doctor route {route_key}",
        )
        expected = str(raw.get("identity_audio_sha256") or "")
        _verify(source, expected, f"Doctor route {route_key}")
        transcript = str(raw.get("identity_text") or "").strip()
        if not transcript:
            raise DoctorFallbackRepairError(
                f"Doctor route transcript is missing: {route_key}"
            )
        references.append(
            {
                "method": f"qwen_reference__{route_key}",
                "route_key": route_key,
                "audio_path": source,
                "audio_sha256": expected,
                "reference_text": transcript,
            }
        )
    return voice, references


def _combined_instruction(voice: Mapping[str, Any]) -> str:
    style = str(
        voice.get("character_style") or voice.get("default_style") or ""
    ).strip()
    return " ".join(part for part in (TARGET_INSTRUCTION, style) if part)


def _default_evaluator(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluation = evaluate_transcriptions(
        {
            "model_status": model_cache_status("mlx_whisper_base"),
            "outputs": [
                {
                    "sample_id": row["method"],
                    "path": row["audio_path"],
                    "text": TARGET_TEXT,
                }
                for row in rows
            ],
        }
    )
    measurements = evaluation.get("measurements") or {}
    for row in rows:
        row["transcription"] = dict(measurements.get(row["method"]) or {})
    return evaluation


def _write_review_assets(review: Path) -> None:
    (review / "index.html").write_text(
        """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alexandria Doctor fallback repair</title><link rel="stylesheet" href="styles.css"></head>
<body><header><p class="eyebrow">Alexandria · Boundary 22</p><h1>Doctor fallback repair</h1>
<p>Listen to the Doctor reference, then choose the single candidate that sounds most like the same character delivering the target line. Choose <strong>None</strong> if none is good enough. No scoring.</p></header>
<main><section class="reference"><h2>Doctor reference</h2><audio controls preload="metadata" src="reference/doctor_reference.mp3"></audio></section>
<section><h2>Target</h2><blockquote id="target"></blockquote><p id="direction"></p></section>
<form id="choices"></form><label class="notes">Optional note<textarea id="notes"></textarea></label>
<p id="status" aria-live="polite"></p><button id="export" type="button" disabled>Export choice</button></main>
<script src="data.js"></script><script src="app.js"></script></body></html>\n""",
        encoding="utf-8",
    )
    (review / "styles.css").write_text(
        """:root{font-family:Inter,system-ui,sans-serif;color:#29251f;background:#f1eee7}*{box-sizing:border-box}body{margin:0}header,main{max-width:900px;margin:auto;padding:28px 20px}header{border-bottom:1px solid #d4ccbf}h1,h2{font-family:Georgia,serif}.eyebrow{letter-spacing:.08em;text-transform:uppercase;font-size:12px}.reference,.candidate,.none{background:#fffdf8;border:1px solid #d5cdbf;border-radius:10px;padding:18px;margin:16px 0}.reference{background:#e9eee9}.candidate label,.none label{display:flex;gap:10px;align-items:center;font-weight:700}.candidate audio,.reference audio{width:100%;margin-top:12px}.notes{display:grid;gap:8px;font-weight:700}.notes textarea{min-height:80px;padding:10px;font:inherit}button{padding:11px 16px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}button:disabled{opacity:.45}blockquote{border-left:3px solid #b9afa2;margin-left:0;padding-left:16px;font-family:Georgia,serif;font-size:20px}#status{font-weight:700}input[type=radio]{width:20px;height:20px}strong{font-weight:750}\n""",
        encoding="utf-8",
    )
    (review / "app.js").write_text(
        """(()=>{'use strict';const d=window.ALEXANDRIA_DOCTOR_REPAIR_DATA,key='alexandria-choice:'+d.round_id;let state={selection:null,notes:''};try{state={...state,...JSON.parse(localStorage.getItem(key)||'{}')}}catch(_){}const form=document.querySelector('#choices');document.querySelector('#target').textContent=d.target_text;document.querySelector('#direction').textContent=d.direction;for(const s of d.samples){const card=document.createElement('article');card.className='candidate';card.innerHTML=`<label><input type="radio" name="winner" value="${s.sample_id}">Candidate ${s.display_id}</label><audio controls preload="metadata" src="${s.audio}"></audio>`;form.append(card)}const none=document.createElement('article');none.className='none';none.innerHTML='<label><input type="radio" name="winner" value="none">None are good enough</label>';form.append(none);const notes=document.querySelector('#notes'),status=document.querySelector('#status'),button=document.querySelector('#export');notes.value=state.notes||'';for(const input of document.querySelectorAll('input[name="winner"]')){input.checked=input.value===state.selection;input.addEventListener('change',()=>{state.selection=input.value;save()})}notes.addEventListener('input',()=>{state.notes=notes.value;save()});function save(){localStorage.setItem(key,JSON.stringify(state));button.disabled=!state.selection;status.textContent=state.selection?`Choice saved: ${state.selection==='none'?'None':state.selection}`:'Choose one candidate or None.'}button.addEventListener('click',()=>{if(!state.selection)return;const payload={schema_version:1,round_id:d.round_id,completed_at:new Date().toISOString(),selection:state.selection,notes:state.notes||''};const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=d.round_id+'-decision.json';a.hidden=true;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000)});save()})();\n""",
        encoding="utf-8",
    )


def build_round(
    *,
    project_root: str | Path = DEFAULT_PROJECT,
    output_root: str | Path = DEFAULT_OUTPUT,
    replace: bool = False,
    generator: Generator | None = None,
    evaluator: Evaluator | None = None,
    verify_tracked_hashes: bool = True,
) -> dict[str, Any]:
    project = Path(project_root).expanduser().resolve()
    output = Path(output_root).expanduser().resolve()
    if output.exists():
        if not replace:
            raise DoctorFallbackRepairError(
                f"Output exists; pass --replace: {output}"
            )
        shutil.rmtree(output)
    output.mkdir(parents=True)
    review = output / "review"
    audio_root = review / "audio"
    reference_root = review / "reference"
    answer_root = output / "answer-keys"
    private_audio = output / "private" / "audio"
    for path in (review, audio_root, reference_root, answer_root, private_audio):
        path.mkdir(parents=True, exist_ok=True)

    reference_source = REFERENCE_SOURCE.resolve()
    if verify_tracked_hashes:
        _verify(reference_source, REFERENCE_SHA256, "B18 Doctor reference")
    elif not reference_source.is_file():
        raise DoctorFallbackRepairError(
            f"B18 Doctor reference is missing: {reference_source}"
        )
    shutil.copy2(reference_source, reference_root / "doctor_reference.mp3")

    voice, references = _doctor_contract(project)
    rows: list[dict[str, Any]] = []
    for spec in ANCHOR_SPECS:
        source = Path(spec["source"]).resolve()
        if verify_tracked_hashes:
            _verify(source, spec["sha256"], spec["method"])
        elif not source.is_file():
            raise DoctorFallbackRepairError(f"Anchor is missing: {source}")
        destination = private_audio / f"{spec['method']}.wav"
        shutil.copy2(source, destination)
        rows.append(
            {
                "method": spec["method"],
                "kind": "prior_blind_anchor",
                "route_key": None,
                "audio_path": str(destination),
                "audio": _audio_record(destination),
                "generation_receipt": None,
            }
        )

    backend: MLXBackend | None = None
    if generator is None:
        backend = MLXBackend(language="English")

        def default_generator(
            *,
            destination: Path,
            reference: Mapping[str, Any],
            voice_data: Mapping[str, Any],
        ) -> dict[str, Any]:
            assert backend is not None
            backend.generate_instruction_controlled_clone(
                text=TARGET_TEXT,
                ref_audio=str(reference["audio_path"]),
                ref_text=str(reference["reference_text"]),
                instruct=_combined_instruction(voice_data),
                output_path=str(destination),
                temperature=float(voice_data.get("instruction_clone_temperature", 0.75)),
                top_k=int(voice_data.get("instruction_clone_top_k", 50)),
                top_p=float(voice_data.get("instruction_clone_top_p", 0.95)),
                repetition_penalty=float(
                    voice_data.get("instruction_clone_repetition_penalty", 1.5)
                ),
                max_tokens=int(voice_data.get("instruction_clone_max_tokens", 2000)),
                seed=int(voice_data.get("seed", SEED)),
                request_label=f"{REQUEST_LABEL_PREFIX}:{reference['route_key']}",
            )
            return {
                "backend": "qwen3_instruction_controlled",
                "seed": int(voice_data.get("seed", SEED)),
                "reference_route": reference["route_key"],
            }

        active_generator: Generator = default_generator
    else:
        active_generator = generator

    try:
        for reference in references:
            destination = private_audio / f"{reference['method']}.wav"
            receipt = active_generator(
                destination=destination,
                reference=reference,
                voice_data=voice,
            )
            if not destination.is_file():
                raise DoctorFallbackRepairError(
                    f"Generator created no candidate: {reference['route_key']}"
                )
            rows.append(
                {
                    "method": reference["method"],
                    "kind": "new_local_qwen_reference_test",
                    "route_key": reference["route_key"],
                    "reference_audio_sha256": reference["audio_sha256"],
                    "audio_path": str(destination),
                    "audio": _audio_record(destination),
                    "generation_receipt": receipt,
                }
            )
    finally:
        if backend is not None:
            backend.release_models_if_idle()

    active_evaluator = evaluator or _default_evaluator
    evaluation = active_evaluator(rows)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        result = row.get("transcription")
        if not isinstance(result, Mapping):
            rejected.append({**row, "rejection_reason": "transcription_missing"})
            continue
        value = result.get("word_error_rate")
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and float(value) <= MAX_WORD_ERROR_RATE
        ):
            accepted.append(row)
        else:
            rejected.append({**row, "rejection_reason": "authored_text_gate"})
    if len(accepted) < 3:
        raise DoctorFallbackRepairError(
            f"Only {len(accepted)} candidates passed authored-text screening."
        )

    rng = random.Random(SEED)
    blinded = list(accepted)
    rng.shuffle(blinded)
    public_samples: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    for index, row in enumerate(blinded, start=1):
        sample_id = f"{SAMPLE_ID_PREFIX}{index:02d}"
        source = Path(row["audio_path"])
        target = audio_root / f"{sample_id}.wav"
        shutil.copy2(source, target)
        public_samples.append(
            {
                "sample_id": sample_id,
                "display_id": chr(ord("A") + index - 1),
                "audio": f"audio/{target.name}",
            }
        )
        answers.append(
            {
                "sample_id": sample_id,
                "method": row["method"],
                "kind": row["kind"],
                "route_key": row.get("route_key"),
                "source_path": str(source),
                "source_sha256": row["audio"]["sha256"],
                "reference_audio_sha256": row.get("reference_audio_sha256"),
                "transcription": row["transcription"],
                "generation_receipt": row.get("generation_receipt"),
            }
        )

    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "target_text": TARGET_TEXT,
        "direction": TARGET_INSTRUCTION,
        "samples": public_samples,
        "review_rule": "Choose one best candidate or None. No scoring required.",
    }
    (review / "data.json").write_text(
        json.dumps(public, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (review / "data.js").write_text(
        "window.ALEXANDRIA_DOCTOR_REPAIR_DATA = "
        + json.dumps(public, ensure_ascii=False, sort_keys=True)
        + ";\n",
        encoding="utf-8",
    )
    _write_review_assets(review)
    answer = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "seed": SEED,
        "answers": answers,
        "objective_rejections": [
            {
                "method": row["method"],
                "kind": row["kind"],
                "route_key": row.get("route_key"),
                "reason": row["rejection_reason"],
                "transcription": row.get("transcription"),
            }
            for row in rejected
        ],
        "transcription_evaluation": evaluation,
        "production_promotion_allowed": False,
        "live_project_changed": False,
    }
    answer_path = answer_root / "answer-key.json"
    answer_path.write_text(
        json.dumps(answer, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "candidate_count": len(public_samples),
        "objective_rejection_count": len(rejected),
        "prior_anchor_count": sum(
            item["kind"] == "prior_blind_anchor" for item in accepted
        ),
        "new_qwen_candidate_count": sum(
            item["kind"] == "new_local_qwen_reference_test" for item in accepted
        ),
        "review_contract": "single_best_or_none",
        "review_path": str(review / "index.html"),
        "answer_key_path": str(answer_path),
        "data_sha256": sha256_file(review / "data.json"),
        "data_js_sha256": sha256_file(review / "data.js"),
        "answer_key_sha256": sha256_file(answer_path),
        "production_promotion_allowed": False,
        "live_project_changed": False,
        "file_url_compatible": True,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build_round(
                project_root=args.project_root,
                output_root=args.output_root,
                replace=args.replace,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
