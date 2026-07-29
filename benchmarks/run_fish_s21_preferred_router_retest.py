#!/usr/bin/env python3
"""Generate the focused Fish S2.1 preferred-router retest.

Uses existing private Fish voice models for Narrator, Benny, and Doctor. The
round is research-only and never changes Alexandria Voice assignments or
production audio.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

from fish_s21_blind_contract import build_prompt, sha256_bytes, sha256_file, sha256_value  # noqa: E402
from prepare_chris_roz_reference_round import (  # noqa: E402
    load_whisper,
    normalized_words,
    resolve_whisper_snapshot,
    word_error_rate,
)
from run_fish_s21_blind_test import API_BASE, FishBlindRunError, FishClient, audio_metadata, write_json  # noqa: E402

CONFIG_PATH = ROOT / "benchmarks/fish_s21_preferred_router_retest.json"
DEFAULT_OUTPUT = ROOT / ".omo/evidence/fish-s21-preferred-router-retest-v1"
FISH_KEYCHAIN_SERVICE = "com.alexandria.fish-audio"


class PreferredRouterError(RuntimeError):
    """Raised when the focused Fish retest contract cannot be satisfied."""


def read_fish_key() -> str:
    for name in ("FISH_API_KEY", "FISH_AUDIO_API_KEY"):
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    completed = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-s",
            FISH_KEYCHAIN_SERVICE,
            "-a",
            getpass.getuser(),
            "-w",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value:
        raise PreferredRouterError("Fish API key is not configured in the environment or macOS Keychain.")
    return value


def load_config(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise PreferredRouterError("Unsupported preferred-router schema.")
    if payload.get("api_model_header") != "s2.1-pro-free":
        raise PreferredRouterError("Fish model header changed.")
    permission = payload.get("permission")
    if not isinstance(permission, Mapping) or permission.get("confirmed_by_user") is not True:
        raise PreferredRouterError("Explicit permission is not recorded.")
    identities = payload.get("identities")
    tests = payload.get("tests")
    generation = payload.get("generation")
    if not isinstance(identities, list) or not isinstance(tests, list) or not isinstance(generation, Mapping):
        raise PreferredRouterError("Identities, tests, and generation settings are required.")
    expected = {"narrator", "benny", "doctor"}
    actual = {str(row.get("key") or "") for row in identities}
    if actual != expected:
        raise PreferredRouterError(f"Identity set changed: {sorted(actual)}")
    seen: set[str] = set()
    for test in tests:
        key = str(test.get("key") or "")
        if not key or key in seen:
            raise PreferredRouterError(f"Duplicate or empty test key: {key!r}")
        seen.add(key)
        if test.get("identity") not in expected:
            raise PreferredRouterError(f"Unknown identity for {key!r}")
        build_prompt(test, str(test.get("prompt_mode") or ""))
    if int(generation.get("repeats") or 0) != 2:
        raise PreferredRouterError("Exactly two repeats are required.")
    return payload


def load_voice_models(config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    source_root = Path(str(config["source_root"])).expanduser().resolve()
    result: dict[str, dict[str, Any]] = {}
    for identity in config["identities"]:
        key = str(identity["key"])
        path = source_root / key / "private/fish-voice-models.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected_round = f"alexandria_fish_s21_permitted_clones_v1_{key}"
        if payload.get("round_id") != expected_round:
            raise PreferredRouterError(f"Voice-model state changed for {key}: {path}")
        tier_key = str(identity["reference_tier"])
        model = payload.get("models", {}).get(tier_key)
        if not isinstance(model, Mapping):
            raise PreferredRouterError(f"Reference tier {tier_key!r} missing for {key}")
        if model.get("visibility") != "private" or model.get("state") != "trained":
            raise PreferredRouterError(f"Fish model is not trained and private for {key}")
        if not str(model.get("model_id") or ""):
            raise PreferredRouterError(f"Fish model ID missing for {key}")
        result[key] = dict(model)
    return result


def sample_contract(config: Mapping[str, Any], test: Mapping[str, Any], repeat: int, model: Mapping[str, Any]) -> dict[str, Any]:
    prompt_mode = str(test["prompt_mode"])
    prompt = build_prompt(test, prompt_mode)
    return {
        "round_id": config["round_id"],
        "provider": config["provider"],
        "api_model_header": config["api_model_header"],
        "identity": test["identity"],
        "test_key": test["key"],
        "style": test["style"],
        "prompt_mode": prompt_mode,
        "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
        "target_text_sha256": sha256_bytes(str(test["target_text"]).encode("utf-8")),
        "reference_fingerprint": model["reference_fingerprint"],
        "repeat": repeat,
        "generation": dict(config["generation"]),
    }


def generate_samples(client: FishClient, *, output_root: Path, config: Mapping[str, Any], models: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    repeats = int(config["generation"]["repeats"])
    for test in config["tests"]:
        identity = str(test["identity"])
        model = models[identity]
        prompt = build_prompt(test, str(test["prompt_mode"]))
        for repeat in range(1, repeats + 1):
            contract = sample_contract(config, test, repeat, model)
            fingerprint = sha256_value(contract)
            directory = output_root / "outputs" / identity / str(test["key"])
            audio_path = directory / f"repeat-{repeat}.wav"
            receipt_path = directory / f"repeat-{repeat}.json"
            receipt: dict[str, Any] | None = None
            if audio_path.is_file() and receipt_path.is_file():
                candidate = json.loads(receipt_path.read_text(encoding="utf-8"))
                if (
                    candidate.get("sample_fingerprint") == fingerprint
                    and candidate.get("audio_sha256") == sha256_file(audio_path)
                    and candidate.get("prompt_sha256") == contract["prompt_sha256"]
                ):
                    receipt = candidate
            if receipt is None:
                audio = client.synthesize(
                    text=prompt,
                    reference_id=str(model["model_id"]),
                    settings=config["generation"],
                )
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = audio_path.with_name(f".{audio_path.name}.tmp")
                temporary.write_bytes(audio)
                os.replace(temporary, audio_path)
                metadata = audio_metadata(audio_path)
                receipt = {
                    "schema_version": 1,
                    "sample_fingerprint": fingerprint,
                    "identity": identity,
                    "test_key": test["key"],
                    "style": test["style"],
                    "repeat": repeat,
                    "prompt_mode": test["prompt_mode"],
                    "prompt_sha256": contract["prompt_sha256"],
                    "target_text_sha256": contract["target_text_sha256"],
                    "reference_fingerprint": model["reference_fingerprint"],
                    "reference_duration_seconds": model["reference_duration_seconds"],
                    "audio_sha256": sha256_file(audio_path),
                    "audio": metadata,
                    "generation": dict(config["generation"]),
                    "production_promotion_allowed": False,
                }
                write_json(receipt_path, receipt)
                time.sleep(float(config["generation"].get("request_pause_seconds") or 0.0))
            samples.append(
                {
                    "fingerprint": fingerprint,
                    "identity": identity,
                    "identity_label": next(row["label"] for row in config["identities"] if row["key"] == identity),
                    "test_key": test["key"],
                    "style": test["style"],
                    "repeat": repeat,
                    "prompt_mode": test["prompt_mode"],
                    "target_text": test["target_text"],
                    "prompt": prompt,
                    "audio_path": audio_path,
                    "audio_sha256": receipt["audio_sha256"],
                    "duration_seconds": float(receipt["audio"]["duration_seconds"]),
                    "reference_model_id": model["model_id"],
                    "reference_fingerprint": model["reference_fingerprint"],
                    "receipt_path": receipt_path,
                }
            )
            write_json(
                output_root / "progress.json",
                {
                    "schema_version": 1,
                    "round_id": config["round_id"],
                    "generated_or_verified": len(samples),
                    "expected": len(config["tests"]) * repeats,
                    "last_sample": fingerprint,
                },
            )
    return samples


def attach_asr(samples: list[dict[str, Any]]) -> None:
    whisper = load_whisper()
    snapshot = resolve_whisper_snapshot()
    for index, sample in enumerate(samples, start=1):
        result = whisper.transcribe(
            str(sample["audio_path"]),
            path_or_hf_repo=str(snapshot),
            language="en",
            word_timestamps=False,
            condition_on_previous_text=False,
            verbose=False,
        )
        transcript = str(result.get("text") or "").strip()
        expected = str(sample["target_text"])
        sample["pinned_asr"] = {
            "model": "mlx-community/whisper-base-mlx",
            "revision": snapshot.name,
            "transcript": transcript,
            "word_error_rate": word_error_rate(expected, transcript),
            "exact_normalized_text": normalized_words(expected) == normalized_words(transcript),
        }


def build_review(output_root: Path, config: Mapping[str, Any], samples: list[dict[str, Any]], models: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    review = output_root / "review"
    audio_root = review / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    answer_key: dict[str, Any] = {
        "schema_version": 1,
        "round_id": config["round_id"],
        "samples": {},
        "voice_models": {
            identity: {
                "model_id": model["model_id"],
                "reference_fingerprint": model["reference_fingerprint"],
                "reference_duration_seconds": model["reference_duration_seconds"],
                "visibility": model["visibility"],
            }
            for identity, model in models.items()
        },
    }
    by_test: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        blind_id = hashlib.sha256((str(config["round_id"]) + sample["fingerprint"]).encode("utf-8")).hexdigest()[:16]
        target = audio_root / f"{blind_id}.wav"
        if not target.is_file() or sha256_file(target) != sample["audio_sha256"]:
            shutil.copy2(sample["audio_path"], target)
        answer_key["samples"][blind_id] = {
            "identity": sample["identity"],
            "test_key": sample["test_key"],
            "style": sample["style"],
            "repeat": sample["repeat"],
            "prompt_mode": sample["prompt_mode"],
            "prompt": sample["prompt"],
            "prompt_sha256": sha256_bytes(sample["prompt"].encode("utf-8")),
            "target_text": sample["target_text"],
            "audio_sha256": sample["audio_sha256"],
            "reference_fingerprint": sample["reference_fingerprint"],
            "reference_model_id": sample["reference_model_id"],
            "source_receipt": str(sample["receipt_path"]),
            "pinned_asr": sample["pinned_asr"],
        }
        by_test.setdefault(str(sample["test_key"]), []).append(
            {
                "id": blind_id,
                "audio": f"audio/{blind_id}.wav",
                "duration_seconds": sample["duration_seconds"],
                "asr_exact": sample["pinned_asr"]["exact_normalized_text"],
            }
        )

    seed = int(sha256_value(config["round_id"])[:12], 16)
    rng = random.Random(seed)
    identity_groups: list[dict[str, Any]] = []
    for identity in config["identities"]:
        tests = []
        for test in [row for row in config["tests"] if row["identity"] == identity["key"]]:
            candidates = list(by_test[str(test["key"])])
            rng.shuffle(candidates)
            for index, candidate in enumerate(candidates, start=1):
                candidate["label"] = chr(64 + index)
            tests.append(
                {
                    "key": test["key"],
                    "style": test["style"],
                    "target_text": test["target_text"],
                    "candidates": candidates,
                }
            )
        identity_groups.append(
            {
                "key": identity["key"],
                "label": identity["label"],
                "routing_policy": identity["routing_policy"],
                "tests": tests,
            }
        )

    public_data = {
        "schema_version": 1,
        "round_id": config["round_id"],
        "identities": identity_groups,
        "answer_key_separate": True,
        "production_promotion_allowed": False,
    }
    write_json(output_root / "private/answer-key.json", answer_key)
    (review / "data.js").write_text(
        "window.FISH_ROUTER_RETEST = " + json.dumps(public_data, indent=2, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    (review / "index.html").write_text(
        """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Fish preferred-router retest</title><link rel="stylesheet" href="styles.css"></head><body><header><p class="eyebrow">Alexandria blind listening</p><h1>Fish S2.1 preferred-router retest</h1><p>Compare the two hidden generations for each line. Score voice identity, intended delivery, naturalness, text fidelity, and artifacts. Prompt modes and repeats remain hidden until export is decoded.</p><div><button id="export">Export scores</button><span id="progress"></span></div></header><main id="app"></main><script src="data.js"></script><script src="app.js"></script></body></html>""",
        encoding="utf-8",
    )
    (review / "styles.css").write_text(
        """:root{font-family:Inter,ui-sans-serif,system-ui,sans-serif;color:#28231e;background:#f2eee6}*{box-sizing:border-box}body{margin:0}header,main{max-width:1080px;margin:auto;padding:32px 24px}header{border-bottom:1px solid #d6cec0}.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.12em;color:#70685e}h1,h2{font-family:Georgia,serif}h1{font-size:34px;margin:.2rem 0 .8rem}h2{font-size:27px;margin-top:44px}.policy{color:#6c645a;max-width:780px}.test{background:#fffdf8;border:1px solid #d6cec0;border-radius:10px;padding:18px;margin:16px 0 22px}.test h3{margin:0}.text{font:17px/1.55 Georgia,serif}.candidates{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.candidate{border-top:1px solid #ddd4c7;padding-top:14px}audio{width:100%}.ratings{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:12px}label{display:grid;gap:4px;font-size:12px;font-weight:650;color:#5f574d}select,textarea{font:inherit;border:1px solid #bdb3a5;border-radius:5px;padding:8px;background:white}textarea{min-height:62px}.retain{display:flex;align-items:center;gap:7px;margin-top:10px}.retain input{width:18px;height:18px}button{border:1px solid #315c55;background:#315c55;color:white;border-radius:6px;padding:10px 14px;font-weight:700;cursor:pointer}header>div{display:flex;gap:15px;align-items:center;margin-top:18px}@media(max-width:720px){.candidates{grid-template-columns:1fr}.ratings{grid-template-columns:1fr}header,main{padding:24px 16px}}""",
        encoding="utf-8",
    )
    (review / "app.js").write_text(
        """(()=>{const d=window.FISH_ROUTER_RETEST,k='fish-router-retest:'+d.round_id;let s={};try{s=JSON.parse(localStorage.getItem(k)||'{}')}catch(_){s={}}const app=document.getElementById('app'),scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join('');function rating(id,n,l){return `<label>${l}<select data-id="${id}" data-name="${n}">${scale}</select></label>`}for(const identity of d.identities){const sec=document.createElement('section');sec.innerHTML=`<h2>${identity.label}</h2><p class="policy">${identity.routing_policy}</p>`;for(const test of identity.tests){const card=document.createElement('article');card.className='test';card.innerHTML=`<h3>${test.style.replaceAll('_',' ')}</h3><p class="text">${test.target_text}</p><div class="candidates">`+test.candidates.map(c=>{const x=s[c.id]||{};return `<section class="candidate"><strong>Candidate ${c.label}</strong><audio controls preload="none" src="${c.audio}"></audio><div class="ratings">${rating(c.id,'identity','Identity')}${rating(c.id,'delivery','Delivery')}${rating(c.id,'naturalness','Naturalness')}${rating(c.id,'text','Text fidelity')}${rating(c.id,'artifacts','Artifact severity')}</div><label class="retain"><input type="checkbox" data-id="${c.id}" data-name="retain" ${x.retain?'checked':''}>Retain</label><label>Notes<textarea data-id="${c.id}" data-name="notes">${x.notes||''}</textarea></label></section>`}).join('')+'</div>';sec.appendChild(card)}app.appendChild(sec)}for(const e of document.querySelectorAll('[data-id]')){const id=e.dataset.id,n=e.dataset.name;if(e.tagName==='SELECT'&&s[id]?.[n]!=null)e.value=s[id][n];e.addEventListener('change',save);e.addEventListener('input',save)}function save(e){const id=e.target.dataset.id,n=e.target.dataset.name;s[id]=s[id]||{};s[id][n]=e.target.type==='checkbox'?e.target.checked:e.target.value;localStorage.setItem(k,JSON.stringify(s));progress()}function progress(){const total=d.identities.flatMap(i=>i.tests).flatMap(t=>t.candidates).length,done=Object.values(s).filter(x=>x.identity&&x.delivery&&x.naturalness&&x.text&&x.artifacts).length;document.getElementById('progress').textContent=`${done} of ${total} fully scored`}document.getElementById('export').onclick=()=>{const b=new Blob([JSON.stringify({schema_version:1,round_id:d.round_id,reviewer:'tristan',exported_at:new Date().toISOString(),scores:s},null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};progress()})();""",
        encoding="utf-8",
    )
    return public_data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--api-base", default=API_BASE)
    parser.add_argument("--generation-only", action="store_true")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    config = load_config(config_path)
    models = load_voice_models(config)
    client = FishClient(
        api_key=read_fish_key(),
        model_header=str(config["api_model_header"]),
        base_url=args.api_base,
        max_attempts=int(config["generation"]["max_attempts"]),
    )
    samples = generate_samples(client, output_root=output_root, config=config, models=models)
    if args.generation_only:
        write_json(
            output_root / "generation-manifest.json",
            {
                "schema_version": 1,
                "round_id": config["round_id"],
                "sample_count": len(samples),
                "expected_sample_count": len(config["tests"]) * int(config["generation"]["repeats"]),
                "generation_complete": True,
                "asr_complete": False,
                "review_complete": False,
                "production_promotion_allowed": False,
            },
        )
        print(json.dumps({"generation_complete": True, "samples": len(samples)}, indent=2))
        return 0
    attach_asr(samples)
    public_data = build_review(output_root, config, samples, models)
    exact = sum(1 for sample in samples if sample["pinned_asr"]["exact_normalized_text"])
    manifest = {
        "schema_version": 1,
        "round_id": config["round_id"],
        "config_path": str(config_path),
        "config_sha256": sha256_file(config_path),
        "permission": config["permission"],
        "sample_count": len(samples),
        "test_count": len(config["tests"]),
        "identity_count": len(config["identities"]),
        "exact_asr_count": exact,
        "non_exact_asr_count": len(samples) - exact,
        "review": "review/index.html",
        "answer_key": "private/answer-key.json",
        "production_promotion_allowed": False,
        "voice_assignment_changed": False,
        "live_project_audio_changed": False,
        "public_identity_groups": len(public_data["identities"]),
    }
    write_json(output_root / "manifest.json", manifest)
    print(json.dumps({"manifest": str(output_root / "manifest.json"), "review": str(output_root / "review/index.html"), "samples": len(samples), "exact_asr": exact}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
