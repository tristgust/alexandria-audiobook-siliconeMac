#!/usr/bin/env python3
"""Generate blind unseen-book-line expression comparisons from approved anchors."""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import random
import shutil
import sys
from pathlib import Path
from typing import Any

import msgpack
import numpy as np
import requests
import soundfile as sf
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from experimental_prompt_routing import resolve_experimental_prompt_override
from recurring_voice_routing import resolve_recurring_voice_route
from responsive_voice_backend import (
    FISH_API_BASE,
    ResponsiveVoiceBackend,
    _finalize_specialist_audio,
    _fish_key,
    _verify_production_encoded_text,
    _verify_specialist_text,
)
from tts import TTSEngine

from benchmarks.build_original_sin_direct_substitution_round import encode_proxy, probe_audio
from benchmarks.build_original_sin_overlap_reference_round import (
    WHISPER_MODEL_KEY,
    metrics,
    sha256_file,
    transcribe,
    utc_now,
    write_json,
)
from benchmarks.build_original_sin_overlap_reference_repair_round import project_hashes
from benchmarks.original_sin_overlap_word_alignment import normalized_words, transcript_comparison
from model_registry import resolve_model_path


ROUND_ID = "alexandria_original_sin_unseen_expression_v1"
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_unseen_expression_plan_v1.json")
CANONICAL_SOURCE = Path("/Users/tristan/pinokio/api/alexandria-audiobook.git")


class ExpressionRoundError(RuntimeError):
    pass


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def anchor_answer_key(project: Path, round_id: str) -> Path:
    root = project / "external_workflows/big_finish_overlap_reference_v1"
    mapping = {
        "alexandria_original_sin_overlap_reference_cleanliness_v1": root / "reference_cleanliness_round_v1/private/answer-key.json",
        "alexandria_original_sin_overlap_reference_repair_v3": root / "reference_repair_round_v3/private/answer-key.json",
        "alexandria_original_sin_overlap_reference_final_repair_v4": root / "reference_final_repair_round_v4/private/answer-key.json",
    }
    try:
        return mapping[round_id]
    except KeyError as exc:
        raise ExpressionRoundError(f"Unknown anchor round: {round_id}") from exc


def resolve_anchor(project: Path, spec: dict[str, Any]) -> dict[str, Any]:
    answer = read_json(anchor_answer_key(project, spec["anchor_round"]))["candidates"]
    row = answer.get(spec["anchor_candidate_id"])
    if not isinstance(row, dict):
        raise ExpressionRoundError(f"Missing anchor {spec['anchor_candidate_id']}")
    path = Path(str(row.get("path") or "")).expanduser().resolve()
    text = str(row.get("transcript") or row.get("automatic_transcript") or "").strip()
    expected_hash = str((row.get("metrics") or {}).get("sha256") or "")
    if not path.is_file() or not text:
        raise ExpressionRoundError(f"Anchor is incomplete: {spec['anchor_candidate_id']}")
    actual_hash = sha256_file(path)
    if expected_hash and actual_hash != expected_hash:
        raise ExpressionRoundError(f"Anchor fingerprint failed: {spec['anchor_candidate_id']}")
    return {
        "path": path,
        "text": text,
        "sha256": actual_hash,
        "candidate_id": spec["anchor_candidate_id"],
        "round_id": spec["anchor_round"],
    }


def qwen_voice(anchor: dict[str, Any], seed: int) -> dict[str, Any]:
    return {
        "type": "clone",
        "voice": "Ryan",
        "seed": str(seed),
        "ref_audio": str(anchor["path"]),
        "ref_text": anchor["text"],
        "clone_backend": "qwen3_instruction_controlled",
        "instruction_clone_temperature": 0.75,
        "instruction_clone_top_k": 50,
        "instruction_clone_top_p": 0.95,
        "instruction_clone_repetition_penalty": 1.5,
        "instruction_clone_max_tokens": 2000,
    }


def candidate_id(group: str, route: str, anchor_hash: str) -> str:
    return hashlib.sha256(f"{ROUND_ID}:{group}:{route}:{anchor_hash}".encode()).hexdigest()[:16]


def verify_audio(path: Path, text: str, whisper: str) -> dict[str, Any]:
    observed = transcribe(path, whisper)
    comparison = transcript_comparison([text], observed, {})
    return {"automatic_transcript": observed, **comparison}


def _fish_reference_bytes(path: Path) -> bytes:
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    target_rate = 44100
    if int(rate) != target_rate:
        divisor = int(np.gcd(int(rate), target_rate))
        mono = resample_poly(mono, target_rate // divisor, int(rate) // divisor).astype(np.float32)
    buffer = io.BytesIO()
    sf.write(buffer, mono, target_rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def _concise_instruction(value: str) -> str:
    first = str(value).split(";", 1)[0].strip(" .")
    return first or "natural expressive delivery"


def fish_inline_generate(*, anchor: dict[str, Any], text: str, instruction: str, output: Path) -> dict[str, Any]:
    reference_audio = _fish_reference_bytes(anchor["path"])
    attempts = (
        ("primary", 0.7, 0.7, instruction, True),
        ("lower_variance_retry", 0.35, 0.55, instruction, False),
        ("concise_tag_retry", 0.35, 0.55, _concise_instruction(instruction), False),
    )
    failures: list[str] = []
    key = _fish_key()
    for index, (strategy, temperature, top_p, tag, previous) in enumerate(attempts, start=1):
        candidate = output.with_name(f".{output.stem}.fish-{index}.wav")
        candidate.unlink(missing_ok=True)
        payload = {
            "text": f"[{tag}] {text}",
            "references": [{"audio": reference_audio, "text": anchor["text"]}],
            "temperature": temperature,
            "top_p": top_p,
            "prosody": {"speed": 1.0, "volume": 0, "normalize_loudness": True},
            "chunk_length": 200,
            "normalize": True,
            "format": "wav",
            "sample_rate": 44100,
            "latency": "normal",
            "max_new_tokens": 1024,
            "repetition_penalty": 1.2,
            "min_chunk_length": 50,
            "condition_on_previous_chunks": previous,
            "early_stop_threshold": 1,
        }
        try:
            response = requests.post(
                FISH_API_BASE + "/v1/tts",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/msgpack",
                    "model": "s2.1-pro-free",
                },
                data=msgpack.packb(payload, use_bin_type=True),
                timeout=300,
            )
            if response.status_code >= 400:
                detail = response.text[:500].replace(key, "[redacted]")
                raise ExpressionRoundError(f"Fish HTTP {response.status_code}: {detail}")
            if len(response.content) < 512:
                raise ExpressionRoundError(f"Fish returned only {len(response.content)} bytes")
            candidate.write_bytes(response.content)
            sf.info(str(candidate))
            _finalize_specialist_audio(candidate, text)
            source_verification = _verify_specialist_text(candidate, text)
            production_verification = _verify_production_encoded_text(candidate, text)
            output.parent.mkdir(parents=True, exist_ok=True)
            candidate.replace(output)
            return {
                "attempt_count": index,
                "repair_strategy": strategy,
                "api_model_header": "s2.1-pro-free",
                "reference_mode": "inline_zero_shot_msgpack",
                "reference_audio_sha256": hashlib.sha256(reference_audio).hexdigest(),
                "source_text_verification": source_verification,
                "text_verification": production_verification,
            }
        except Exception as exc:
            failures.append(f"{strategy}: {type(exc).__name__}: {exc}")
            candidate.unlink(missing_ok=True)
    raise ExpressionRoundError("Fish inline generation failed: " + " | ".join(failures))


def _copy_asset(source_root: Path, target_root: Path, value: Any) -> None:
    relative = Path(str(value or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        return
    source = (source_root / relative).resolve()
    try:
        source.relative_to(source_root)
    except ValueError:
        return
    if not source.is_file():
        raise ExpressionRoundError(f"Current-route asset is missing: {source}")
    target = target_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if sha256_file(target) != sha256_file(source):
        raise ExpressionRoundError(f"Current-route asset changed: {relative}")


def prepare_current_control(
    *, project: Path, output: Path, book_speaker: str, cache: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    mapping = {
        "BERNICE": (project, "BERNICE"),
        "CHRIS CWEJ": (CANONICAL_SOURCE, "CHRIS"),
        "ROZ FORRESTER": (CANONICAL_SOURCE, "ROZ"),
    }
    if book_speaker not in mapping:
        raise ExpressionRoundError(f"No current-route control for {book_speaker}")
    source_root, voice_key = mapping[book_speaker]
    if voice_key in cache:
        return cache[voice_key]
    source_config = read_json(source_root / "voice_config.json")
    voice = source_config.get(voice_key)
    if not isinstance(voice, dict):
        raise ExpressionRoundError(f"Current voice is missing: {voice_key}")
    control_root = output / "private/current_controls" / voice_key.casefold()
    control_root.mkdir(parents=True, exist_ok=True)
    _copy_asset(source_root, control_root, voice.get("ref_audio"))
    experimental = voice.get("experimental_prompt_routing")
    if isinstance(experimental, dict):
        for route in (experimental.get("routes") or {}).values():
            if isinstance(route, dict):
                _copy_asset(source_root, control_root, route.get("ref_audio"))
    responsive = voice.get("responsive_backend_routing")
    if isinstance(responsive, dict):
        for route in (responsive.get("routes") or {}).values():
            if isinstance(route, dict):
                _copy_asset(source_root, control_root, route.get("identity_audio"))
                _copy_asset(source_root, control_root, route.get("performance_audio"))
    (control_root / "voice_config.json").write_text(
        json.dumps({voice_key: voice}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    cache[voice_key] = {"root": control_root, "voice_key": voice_key, "voice": copy.deepcopy(voice)}
    return cache[voice_key]


def current_route_metadata(control: dict[str, Any], instruction: str) -> dict[str, Any]:
    voice = control["voice"]
    if voice.get("clone_backend") == "alexandria_responsive_router":
        route = resolve_recurring_voice_route(
            voice_data=voice,
            instruction=instruction,
            project_root=control["root"],
            verify_audio=True,
        )
        if route is None:
            return {"requested_backend": None, "actual_backend": None, "fallback_used": False}
        requested = str(route["backend"])
        available = ResponsiveVoiceBackend().backend_available(requested)
        return {
            "route_key": route["route_key"],
            "requested_backend": requested,
            "actual_backend": requested if available else str(route["fallback_backend"]),
            "fallback_used": not available,
            "fallback_backend": route["fallback_backend"],
            "mapping_reason": route["mapping_reason"],
        }
    selected = resolve_experimental_prompt_override(
        voice_data=voice,
        instruction=instruction,
        project_root=control["root"],
    )
    return {
        "route_key": selected["route_key"] if selected else "current_default",
        "requested_backend": str(voice.get("clone_backend") or "qwen3_instruction_controlled"),
        "actual_backend": str(voice.get("clone_backend") or "qwen3_instruction_controlled"),
        "fallback_used": False,
    }


def build_review(output: Path, groups: list[dict[str, Any]]) -> None:
    review, audio = output / "review", output / "review/audio"
    audio.mkdir(parents=True, exist_ok=True)
    rng, answer, public_groups = random.Random(20260731), {}, []
    for group in groups:
        candidates = list(group["candidates"])
        rng.shuffle(candidates)
        public = []
        for candidate in candidates:
            shutil.copy2(candidate["proxy_path"], audio / f"{candidate['candidate_id']}.mp3")
            public.append({"id": candidate["candidate_id"], "audio": f"audio/{candidate['candidate_id']}.mp3"})
            answer[candidate["candidate_id"]] = {
                **candidate,
                "wav_path": str(candidate["wav_path"]),
                "proxy_path": str(candidate["proxy_path"]),
                "character": group["character"],
                "book_speaker": group["book_speaker"],
                "chunk_id": group["chunk_id"],
                "mode": group["mode"],
                "text": group["text"],
                "instruction": group["instruction"],
                "anchor": group["anchor"],
            }
        public_groups.append(
            {
                "group": group["group"],
                "character": group["character"],
                "mode": group["mode"],
                "text": group["text"],
                "instruction": group["instruction"],
                "candidates": public,
            }
        )
    write_json(
        output / "private/answer-key.json",
        {"schema_version": 1, "round_id": ROUND_ID, "candidates": answer, "production_changes": False},
    )
    (review / "data.js").write_text(
        "window.ORIGINAL_SIN_UNSEEN_EXPRESSION = "
        + json.dumps({"round_id": ROUND_ID, "groups": public_groups}, indent=2, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )
    (review / "index.html").write_text(REVIEW_HTML, encoding="utf-8")


REVIEW_HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Original Sin unseen expression</title><link rel="icon" href="data:,"><style>:root{font-family:Inter,system-ui,sans-serif;color:#28231f;background:#f2eee6}*{box-sizing:border-box}body{margin:0}header,main{max-width:1100px;margin:auto;padding:24px 20px}header{border-bottom:1px solid #d2c8b8}h1,h2{font-family:Georgia,serif}.group{background:#fffdf8;border:1px solid #d5ccbd;border-radius:10px;padding:18px;margin:18px 0}.instruction{padding:10px;border-left:3px solid #7d7468;background:#f4efe7}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.candidate{border:1px solid #d9d0c3;border-radius:8px;padding:12px;background:#fff}audio{width:100%}label{display:grid;gap:4px;font-size:13px;font-weight:650;margin-top:7px}select,textarea{font:inherit;padding:7px;border:1px solid #b9afa2;border-radius:5px}textarea{min-height:62px}.decision{display:flex;gap:12px;margin:8px 0}.decision label{display:flex;align-items:center;gap:5px}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}@media(max-width:900px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:620px){.grid{grid-template-columns:1fr}}</style></head><body><header><h1>Original Sin unseen-line expressive generation</h1><p>These book lines are not spoken in the adaptation. Backend and route are hidden. Judge identity generalization, requested delivery, naturalness, and absence of artifacts.</p><button id="export">Export review</button> <span id="progress"></span></header><main id="app"></main><script src="data.js"></script><script>(()=>{const d=window.ORIGINAL_SIN_UNSEEN_EXPRESSION,k='os-unseen-expression:'+d.round_id;let s={};try{s=JSON.parse(localStorage.getItem(k)||'{}')}catch(_){s={}}const scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join(''),app=document.querySelector('#app');for(const g of d.groups){const sec=document.createElement('section');sec.className='group';sec.innerHTML=`<h2>${g.character} — ${g.mode}</h2><p><strong>Line:</strong> ${g.text}</p><p class="instruction">${g.instruction}</p><div class="grid"></div>`;for(const [i,c] of g.candidates.entries()){const card=document.createElement('article');card.className='candidate';card.innerHTML=`<h3>Candidate ${i+1}</h3><audio controls src="${c.audio}"></audio>${['identity','delivery','naturalness','artifacts'].map(n=>`<label>${n}<select data-id="${c.id}" data-name="${n}">${scale}</select></label>`).join('')}<div class="decision"><label><input type="radio" name="d-${c.id}" value="pass" data-id="${c.id}" data-name="decision">Pass</label><label><input type="radio" name="d-${c.id}" value="fail" data-id="${c.id}" data-name="decision">Fail</label></div><label>Notes<textarea data-id="${c.id}" data-name="notes"></textarea></label>`;sec.querySelector('.grid').appendChild(card)}app.appendChild(sec)}for(const e of document.querySelectorAll('[data-id]')){const v=s[e.dataset.id]?.[e.dataset.name];if(e.type==='radio')e.checked=v===e.value;else if(v!=null)e.value=v;e.addEventListener(e.tagName==='TEXTAREA'?'input':'change',ev=>{const id=ev.target.dataset.id,n=ev.target.dataset.name;s[id]=s[id]||{};s[id][n]=ev.target.value;localStorage.setItem(k,JSON.stringify(s));progress()})}function progress(){const ids=d.groups.flatMap(g=>g.candidates.map(c=>c.id));document.querySelector('#progress').textContent=`${ids.filter(id=>s[id]?.decision).length} of ${ids.length} decided`}document.querySelector('#export').onclick=()=>{const a=document.createElement('a'),b=new Blob([JSON.stringify({schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),results:s},null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();</script></body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    project = args.project_root.expanduser().resolve()
    plan = read_json(args.plan.expanduser().resolve())
    expected_candidates = sum(len(group["routes"]) for group in plan["groups"])
    if plan.get("round_id") != ROUND_ID or expected_candidates != plan.get("candidate_count"):
        raise ExpressionRoundError("Expression plan mismatch")
    output = (
        args.output_root.expanduser().resolve()
        if args.output_root
        else project / "external_workflows/big_finish_overlap_reference_v1/unseen_expression_round_v1"
    )
    if output.exists():
        if not args.replace:
            raise ExpressionRoundError(f"Output exists: {output}")
        shutil.rmtree(output)
    before = project_hashes(project)
    chunks = read_json(project / "chunks.json")
    transcript = read_json(project / "external_workflows/big_finish_overlap_reference_v1/private/transcript.json")["segments"]
    full_adaptation = " ".join(normalized_words(" ".join(str(row.get("text") or "") for row in transcript)))
    for spec in plan["groups"]:
        chunk = chunks[int(spec["chunk_id"])]
        if chunk.get("speaker") != spec["book_speaker"] or normalized_words(chunk.get("text")) != normalized_words(spec["text"]):
            raise ExpressionRoundError(f"Chunk binding mismatch: {spec['group']}")
        if " ".join(normalized_words(spec["text"])) in full_adaptation:
            raise ExpressionRoundError(f"Line occurs in adaptation: {spec['group']}")

    whisper = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    qwen = TTSEngine({"tts": {"mode": "local", "language": "English", "device": "auto"}})
    responsive = ResponsiveVoiceBackend()
    private = output / "private/audio"
    private.mkdir(parents=True, exist_ok=True)
    controls: dict[str, dict[str, Any]] = {}
    groups: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    try:
        for spec in plan["groups"]:
            anchor = resolve_anchor(project, spec)
            candidates: list[dict[str, Any]] = []
            for route_name in spec["routes"]:
                cid = candidate_id(spec["group"], route_name, anchor["sha256"])
                wav = private / f"{cid}.wav"
                route_meta: dict[str, Any] = {
                    "route_key": route_name,
                    "requested_backend": route_name,
                    "actual_backend": None,
                    "fallback_used": False,
                }
                receipt: dict[str, Any] = {}
                try:
                    if route_name == "qwen_adaptation_anchor":
                        config = {spec["book_speaker"]: qwen_voice(anchor, int(plan["seed"]))}
                        if not qwen.generate_voice(spec["text"], spec["instruction"], spec["book_speaker"], config, str(wav)):
                            raise ExpressionRoundError("Qwen generation returned false")
                        route_meta["actual_backend"] = "qwen3_instruction_controlled"
                    elif route_name == "vox_adaptation_anchor":
                        route = {
                            "backend": "voxcpm2_controllable_clone",
                            "identity_audio_path": str(anchor["path"]),
                            "identity_text": anchor["text"],
                            "control": {
                                "instruction": spec["instruction"],
                                "cfg_value": 2.0,
                                "inference_timesteps": 10,
                                "warmup_patches": 0,
                                "max_tokens": 1800,
                            },
                        }
                        receipt = responsive.generate(route=route, text=spec["text"], output_path=wav, seed=int(plan["seed"]))
                        route_meta["actual_backend"] = "voxcpm2_controllable_clone"
                    elif route_name == "fish_inline_adaptation_anchor":
                        receipt = fish_inline_generate(anchor=anchor, text=spec["text"], instruction=spec["instruction"], output=wav)
                        route_meta["actual_backend"] = "fish_s2.1_pro_free_inline_zero_shot"
                    elif route_name == "current_alexandria_route":
                        control = prepare_current_control(
                            project=project,
                            output=output,
                            book_speaker=spec["book_speaker"],
                            cache=controls,
                        )
                        route_meta.update(current_route_metadata(control, spec["instruction"]))
                        current_wav = control["root"] / f"{cid}.wav"
                        config = {control["voice_key"]: control["voice"]}
                        if not qwen.generate_voice(
                            spec["text"], spec["instruction"], control["voice_key"], config, str(current_wav)
                        ):
                            raise ExpressionRoundError("Current Alexandria route returned false")
                        shutil.copy2(current_wav, wav)
                    else:
                        raise ExpressionRoundError(f"Unknown route: {route_name}")

                    if not wav.is_file():
                        raise ExpressionRoundError("No WAV generated")
                    source_check = verify_audio(wav, spec["text"], whisper)
                    if source_check["word_error_rate"] != 0.0 or not source_check["first_word_present"] or not source_check["last_word_present"]:
                        raise ExpressionRoundError(f"Source transcript gate failed: {source_check}")
                    proxy = private / f"{cid}.mp3"
                    encode_proxy(wav, proxy, bitrate="192k")
                    proxy_check = verify_audio(proxy, spec["text"], whisper)
                    probe = probe_audio(proxy)
                    if (
                        proxy_check["word_error_rate"] != 0.0
                        or not proxy_check["first_word_present"]
                        or not proxy_check["last_word_present"]
                        or probe["codec_name"] != "mp3"
                        or probe["sample_rate"] != 44100
                        or probe["channels"] != 2
                    ):
                        raise ExpressionRoundError("Production proxy gate failed")
                    candidates.append(
                        {
                            "candidate_id": cid,
                            **route_meta,
                            "receipt": receipt,
                            "wav_path": wav,
                            "wav_metrics": metrics(wav),
                            "source_objective": source_check,
                            "proxy_path": proxy,
                            "proxy_sha256": sha256_file(proxy),
                            "proxy_probe": probe,
                            "proxy_objective": proxy_check,
                        }
                    )
                except Exception as exc:
                    omissions.append(
                        {
                            "group": spec["group"],
                            "route": route_name,
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:2000],
                        }
                    )
                    wav.unlink(missing_ok=True)
            if len(candidates) < 2:
                raise ExpressionRoundError(
                    f"Fewer than two eligible candidates for {spec['group']}: {omissions[-4:]}"
                )
            groups.append(
                {
                    "group": spec["group"],
                    "character": spec["character"],
                    "book_speaker": spec["book_speaker"],
                    "chunk_id": int(spec["chunk_id"]),
                    "mode": spec["mode"],
                    "text": spec["text"],
                    "instruction": spec["instruction"],
                    "anchor": {**anchor, "path": str(anchor["path"])},
                    "candidates": candidates,
                }
            )
            print(f"built {spec['group']} ({len(candidates)} eligible)", flush=True)
    finally:
        responsive.close()

    build_review(output, groups)
    after = project_hashes(project)
    if before != after:
        raise ExpressionRoundError("Protected project hashes changed")
    write_json(
        output / "generation-summary.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "generated_at": utc_now(),
            "planned_candidate_count": plan["candidate_count"],
            "group_count": len(groups),
            "candidate_count": sum(len(group["candidates"]) for group in groups),
            "omissions": omissions,
            "protected_project_hashes_before": before,
            "protected_project_hashes_after": after,
            "production_changes": False,
            "output_root": str(output),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
