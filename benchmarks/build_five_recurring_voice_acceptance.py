#!/usr/bin/env python3
"""Build and generate the five-recurring-Voice production acceptance pack."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Mapping

import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
import sys
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from chris_roz_recurring_voices import install_chris_roz_recurring_voices
from generation_state import atomic_json_write
from production_prompt_routes import (
    PRIMARY_VOICE_ALIASES,
    inspect_primary_responsive_voice_pack,
)
from project import ProjectManager

ROUND_ID = "alexandria_five_recurring_voice_acceptance_v1"
DEFAULT_SOURCE_ROOT = ROOT
DEFAULT_OUTPUT = ROOT / ".omo/evidence/five-recurring-voice-acceptance-v1"
PRIMARY_VOICES = ("NARRATOR", "BERNICE", "THE DOCTOR")

ACCEPTANCE_LINES = [
    {
        "speaker": "NARRATOR",
        "text": (
            "Alexander Shuttleworth leaned back in the easy chair and drummed "
            "his fingers rhythmically on his stomach."
        ),
        "instruct": (
            "Warm third-person narration; measured period cadence, lightly "
            "anticipate the comic boast."
        ),
        "expected_backend": "qwen3_instruction_controlled",
        "source_kind": "current_project_dialogue",
    },
    {
        "speaker": "BERNICE",
        "text": "Aren't there any alien monsters we can go and destroy?",
        "instruct": (
            "Curious with restrained irony; natural pace, lightly lift the final question."
        ),
        "expected_backend": "qwen3_instruction_controlled",
        "source_kind": "current_project_dialogue",
    },
    {
        "speaker": "THE DOCTOR",
        "text": (
            "They're all gone. Little Johnny Piper - no, sorry, different train "
            "of thought. No alien monsters, I'm afraid."
        ),
        "instruct": (
            "Restlessly thoughtful; varied pacing, emphasize the unexpected association."
        ),
        "expected_backend": "qwen3_instruction_controlled",
        "source_kind": "current_project_dialogue",
    },
    {
        "speaker": "CHRIS",
        "text": "Out of interest, what are the punishments for breaking Thrantasian laws?",
        "instruct": (
            "Dry humour with amused disbelief; underplay the irony and keep the timing exact."
        ),
        "expected_backend": "indextts2_matched_control",
        "expected_route": "dry_humour",
        "source_kind": "reviewed_character_dialogue",
    },
    {
        "speaker": "ROZ",
        "text": "Yeah, and I'm in a dress. We're all making sacrifices today.",
        "instruct": (
            "Dry professional sarcasm with restrained impatience and exact ironic emphasis."
        ),
        "expected_backend": "voxcpm2_controllable_clone",
        "expected_route": "dry_humour",
        "source_kind": "reviewed_character_dialogue",
    },
    {
        "speaker": "CHRIS",
        "text": "Just... she mentioned family and...",
        "instruct": (
            "Vulnerable and hesitant; sincere, emotionally exposed, and trying not to lose control."
        ),
        "expected_backend": "fish_s2_pro_cloud",
        "expected_route": "vulnerability",
        "source_kind": "reviewed_character_dialogue",
    },
    {
        "speaker": "ROZ",
        "text": (
            "So, if you lure the Dauntless forces into the crossroads at the center "
            "of the city, you'll be able to attack from above and from behind cover."
        ),
        "instruct": (
            "Tactical command with clipped precision, decisive authority, and sustained control."
        ),
        "expected_backend": "indextts2_matched_control",
        "expected_route": "urgent_authority",
        "source_kind": "reviewed_character_dialogue",
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_source_asset(source_root: Path, value: Any) -> tuple[Path, str]:
    relative = Path(str(value or ""))
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"Unsafe recurring Voice asset: {value!r}")
    source = (source_root / relative).resolve()
    source.relative_to(source_root)
    if not source.is_file():
        raise FileNotFoundError(source)
    return source, relative.as_posix()


def copy_asset(source_root: Path, output_root: Path, value: Any) -> str:
    source, relative = safe_source_asset(source_root, value)
    target = output_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if sha256_file(target) != sha256_file(source):
        raise ValueError(f"Copied asset changed: {relative}")
    return relative


def copy_voice_assets(
    source_root: Path,
    output_root: Path,
    raw_voice: Mapping[str, Any],
) -> dict[str, Any]:
    voice = copy.deepcopy(dict(raw_voice))
    if voice.get("ref_audio"):
        voice["ref_audio"] = copy_asset(source_root, output_root, voice["ref_audio"])
    routing = voice.get("experimental_prompt_routing")
    if isinstance(routing, dict):
        routes = routing.get("routes")
        if isinstance(routes, dict):
            for route in routes.values():
                if isinstance(route, dict) and route.get("ref_audio"):
                    route["ref_audio"] = copy_asset(
                        source_root,
                        output_root,
                        route["ref_audio"],
                    )
    return voice


def audio_record(path: Path) -> dict[str, Any]:
    info = sf.info(str(path))
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "sample_rate": int(info.samplerate),
        "frames": int(info.frames),
        "duration_seconds": float(info.duration),
        "format": info.format,
    }


def write_review(output: Path, summary: Mapping[str, Any]) -> None:
    review = output / "review"
    review.mkdir(parents=True, exist_ok=True)
    public_rows = []
    for row in summary["lines"]:
        public_rows.append(
            {
                "index": row["index"],
                "speaker": row["speaker"],
                "text": row["text"],
                "instruct": row["instruct"],
                "audio": "../" + row["audio_path"],
                "requested_backend": row.get("responsive_voice_backend"),
                "used_backend": row.get("responsive_voice_used_backend"),
                "route": row.get("responsive_voice_route"),
                "fallback_used": row.get("responsive_voice_fallback_used", False),
                "specialist_attempt_count": row.get(
                    "responsive_voice_specialist_attempt_count"
                ),
                "repair_strategy": row.get("responsive_voice_repair_strategy"),
                "text_verification": row.get("responsive_voice_text_verification"),
            }
        )
    data = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "combined_audio": "../cloned_audiobook.mp3",
        "rows": public_rows,
    }
    (review / "data.js").write_text(
        "window.ALEXANDRIA_RECURRING_ACCEPTANCE = "
        + json.dumps(data, indent=2, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )
    (review / "index.html").write_text(
        """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Five recurring Voices acceptance</title><link rel='icon' href='data:,'><style>:root{font-family:Inter,system-ui,sans-serif;color:#29241e;background:#f2eee6}*{box-sizing:border-box}body{margin:0}header,main{max-width:980px;margin:auto;padding:28px 20px}header{border-bottom:1px solid #d4ccbf}h1,h2{font-family:Georgia,serif}.row{background:#fffdf8;border:1px solid #d5cdbf;border-radius:10px;padding:18px;margin:16px 0}.meta{font-size:13px;color:#6d655b}.backend{font-family:ui-monospace,monospace;font-size:12px}audio{width:100%}textarea{width:100%;min-height:64px;font:inherit;padding:8px}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}.choices{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}.choices label{border:1px solid #aaa096;border-radius:6px;padding:8px;background:white}</style></head><body><header><p>Alexandria production-context audition</p><h1>Five recurring Voices</h1><p>Listen to the combined sequence, then each line. Confirm identity continuity, delivery, clean starts, loudness, and absence of echo or clipping.</p><audio id='combined' controls preload='metadata'></audio><p><button id='export'>Export review</button> <span id='progress'></span></p></header><main id='app'></main><script src='data.js'></script><script src='app.js'></script></body></html>""",
        encoding="utf-8",
    )
    (review / "app.js").write_text(
        """(()=>{'use strict';const d=window.ALEXANDRIA_RECURRING_ACCEPTANCE;const key='alexandria-recurring-acceptance:'+d.round_id;let saved={};try{saved=JSON.parse(localStorage.getItem(key)||'{}')}catch(_){saved={}}document.querySelector('#combined').src=d.combined_audio;const app=document.querySelector('#app');function persist(){localStorage.setItem(key,JSON.stringify(saved));progress()}function progress(){document.querySelector('#progress').textContent=`${d.rows.filter(r=>saved[r.index]?.decision).length} of ${d.rows.length} reviewed`}for(const r of d.rows){const x=saved[r.index]||{},el=document.createElement('article');el.className='row';el.innerHTML=`<p class="meta">Line ${r.index+1} · ${r.speaker}</p><h2>${r.speaker}</h2><p>${r.text}</p><p><em>${r.instruct}</em></p><p class="backend">Requested: ${r.requested_backend||'existing Qwen route'} · Used: ${r.used_backend||'existing Qwen route'} · Route: ${r.route||'existing recurring route'}${r.specialist_attempt_count?` · Attempts: ${r.specialist_attempt_count}`:''}${r.repair_strategy?` · Strategy: ${r.repair_strategy}`:''}${r.fallback_used?' · FALLBACK USED':''}</p><audio controls preload="none" src="${r.audio}"></audio><div class="choices"><label><input type="radio" name="d${r.index}" value="pass" ${x.decision==='pass'?'checked':''}> Pass</label><label><input type="radio" name="d${r.index}" value="fail" ${x.decision==='fail'?'checked':''}> Fail</label></div><textarea placeholder="Notes on identity, delivery, artifacts, clipping, pacing, or loudness">${x.notes||''}</textarea>`;app.appendChild(el);el.querySelectorAll('input').forEach(n=>n.onchange=()=>{saved[r.index]={...(saved[r.index]||{}),decision:n.value};persist()});el.querySelector('textarea').oninput=e=>{saved[r.index]={...(saved[r.index]||{}),notes:e.target.value};persist()}}document.querySelector('#export').onclick=()=>{const payload={schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),results:saved};const a=document.createElement('a'),b=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();""",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--reference-bank", type=Path, required=True)
    parser.add_argument(
        "--reviewed-chris-dry-reference",
        type=Path,
        required=True,
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    source_root = args.source_root.expanduser().resolve()
    bank = args.reference_bank.expanduser().resolve()
    reviewed_chris_dry = (
        args.reviewed_chris_dry_reference.expanduser().resolve()
    )
    output = args.output_root.expanduser().resolve()
    if output.exists():
        shutil.rmtree(output)
    (output / "app").mkdir(parents=True)
    source_config = read_json(source_root / "voice_config.json")
    voice_config: dict[str, Any] = {}
    for voice_name in PRIMARY_VOICES:
        value = source_config.get(voice_name)
        if not isinstance(value, dict):
            raise ValueError(f"Source recurring Voice is missing: {voice_name}")
        voice_config[voice_name] = copy_voice_assets(source_root, output, value)
    for alias, target in PRIMARY_VOICE_ALIASES.items():
        voice_config[alias] = {"alias_of": target}
    atomic_json_write(voice_config, output / "voice_config.json")
    shutil.copy2(source_root / "app/config.json", output / "app/config.json")

    install_receipt = install_chris_roz_recurring_voices(
        project_root=output,
        reference_bank_path=bank,
        reviewed_chris_dry_reference_path=reviewed_chris_dry,
        confirm_production_opt_in=True,
        approved_at_utc="2026-07-30T04:00:00Z",
    )
    pack = inspect_primary_responsive_voice_pack(output)
    if pack.get("ready") is not True:
        raise RuntimeError(f"Five-Voice pack failed inspection: {pack}")

    chunks = []
    for index, row in enumerate(ACCEPTANCE_LINES):
        chunks.append(
            {
                "id": index,
                "speaker": row["speaker"],
                "text": row["text"],
                "instruct": row["instruct"],
                "status": "pending",
                "audio_state": "pending",
                "audio_path": None,
            }
        )
    atomic_json_write(chunks, output / "chunks.json")
    atomic_json_write(ACCEPTANCE_LINES, output / "annotated_script.json")

    manager = ProjectManager(str(output))
    results = []
    for index in range(len(chunks)):
        success, message = manager.generate_chunk_audio(index, generation_seed=130363)
        results.append({"index": index, "success": bool(success), "message": str(message)})
        if not success:
            raise RuntimeError(f"Acceptance line {index} failed: {message}")
    merged, merged_path = manager.merge_audio()
    if not merged:
        raise RuntimeError(f"Acceptance sequence merge failed: {merged_path}")

    completed = json.loads((output / "chunks.json").read_text(encoding="utf-8"))
    summary_lines = []
    for index, (expected, chunk) in enumerate(zip(ACCEPTANCE_LINES, completed)):
        audio_relative = str(chunk["audio_path"])
        audio = output / audio_relative
        used = chunk.get("responsive_voice_used_backend")
        requested = chunk.get("responsive_voice_backend")
        if expected.get("expected_route") and chunk.get("responsive_voice_route") != expected["expected_route"]:
            raise RuntimeError(
                f"Acceptance line {index} selected {chunk.get('responsive_voice_route')!r}, "
                f"expected {expected['expected_route']!r}."
            )
        if requested and requested != expected["expected_backend"]:
            raise RuntimeError(
                f"Acceptance line {index} requested {requested!r}, expected {expected['expected_backend']!r}."
            )
        summary_lines.append(
            {
                "index": index,
                **expected,
                "status": chunk.get("status"),
                "audio_path": audio_relative,
                "audio": audio_record(audio),
                "responsive_voice_route": chunk.get("responsive_voice_route"),
                "responsive_voice_backend": requested,
                "responsive_voice_used_backend": used,
                "responsive_voice_fallback_backend": chunk.get("responsive_voice_fallback_backend"),
                "responsive_voice_fallback_used": bool(chunk.get("responsive_voice_fallback_used")),
                "responsive_voice_backend_error": chunk.get("responsive_voice_backend_error"),
                "generation_seed": chunk.get("generation_seed"),
            }
        )
    summary = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "generated_at": utc_now(),
        "project_root": str(output),
        "pack": pack,
        "install_receipt": install_receipt,
        "generation_results": results,
        "combined_audio": audio_record(output / "cloned_audiobook.mp3"),
        "lines": summary_lines,
        "all_requested_specialists_used": all(
            not row["responsive_voice_backend"]
            or row["responsive_voice_used_backend"] == row["responsive_voice_backend"]
            for row in summary_lines
        ),
        "fallback_count": sum(row["responsive_voice_fallback_used"] for row in summary_lines),
        "manual_listening_required": True,
        "production_assignment_changed": False,
    }
    atomic_json_write(summary, output / "summary.json")
    write_review(output, summary)
    print(
        json.dumps(
            {
                "output": str(output),
                "line_count": len(summary_lines),
                "fallback_count": summary["fallback_count"],
                "all_requested_specialists_used": summary["all_requested_specialists_used"],
                "review": str(output / "review/index.html"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
