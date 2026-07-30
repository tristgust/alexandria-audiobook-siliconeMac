#!/usr/bin/env python3
"""Build old-vs-repaired blind pairs for Chris's canonical reference."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / ".omo/evidence/chris-roz-multimodel-round1-v1"
NEW = ROOT / ".omo/evidence/chris-reference-repair-validation-v2"
DEFAULT_OUTPUT = ROOT / ".omo/evidence/chris-reference-repair-pairwise-v2"
ROUND_ID = "alexandria_chris_reference_repair_pairwise_v2"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(*parts: Any, length: int = 16) -> str:
    return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()[:length]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    output = Path(args.output_root).expanduser().resolve()
    if output.exists():
        shutil.rmtree(output)
    review = output / "review"
    audio_root = review / "audio"
    reference_root = review / "reference"
    audio_root.mkdir(parents=True, exist_ok=True)
    reference_root.mkdir(parents=True, exist_ok=True)

    old_internal = read_json(OLD / "private/internal-manifest.json")
    new_internal = read_json(NEW / "private/internal-manifest.json")
    old_specs = {
        (str(spec["model_key"]), str(spec["style"])): spec
        for spec in old_internal["sample_specs"]
        if spec["identity_key"] == "chris"
        and spec["reference_tier"] == "canonical_cleaned"
        and int(spec["repeat"]) == 1
    }
    new_specs = {(str(spec["model_key"]), str(spec["style"])): spec for spec in new_internal["sample_specs"]}
    if set(old_specs) != set(new_specs) or len(new_specs) != 12:
        raise ValueError("Repair-validation coverage mismatch.")

    clean_reference = OLD / "private/references/identity/chris/clean_actor/reference.wav"
    clean_target = reference_root / "chris.wav"
    shutil.copy2(clean_reference, clean_target)

    rng = random.Random(20260729)
    public_pairs = []
    answer_pairs = {}
    for model, style in sorted(new_specs):
        old_spec = old_specs[(model, style)]
        new_spec = new_specs[(model, style)]
        old_audio = OLD / old_spec["output_file"]
        new_audio = NEW / new_spec["output_file"]
        if not old_audio.is_file() or not new_audio.is_file():
            raise FileNotFoundError(old_audio if not old_audio.is_file() else new_audio)
        pair_id = stable_id(ROUND_ID, model, style)
        repaired_on_a = bool(rng.getrandbits(1))
        a_source = new_audio if repaired_on_a else old_audio
        b_source = old_audio if repaired_on_a else new_audio
        a_target = audio_root / f"{pair_id}-a.wav"
        b_target = audio_root / f"{pair_id}-b.wav"
        shutil.copy2(a_source, a_target)
        shutil.copy2(b_source, b_target)
        public_pairs.append(
            {
                "pair_id": pair_id,
                "style": style,
                "style_label": new_spec["style_label"],
                "target_text": new_spec["target_text"],
                "expected_identity": "Chris Cwej",
                "reference_audio": "reference/chris.wav",
                "audio_a": f"audio/{pair_id}-a.wav",
                "audio_b": f"audio/{pair_id}-b.wav",
            }
        )
        answer_pairs[pair_id] = {
            "model_key": model,
            "style": style,
            "a_reference": "canonical_repaired_mossformer2" if repaired_on_a else "canonical_cleaned_old",
            "b_reference": "canonical_cleaned_old" if repaired_on_a else "canonical_repaired_mossformer2",
            "old_source": str(old_audio),
            "old_sha256": sha256_file(old_audio),
            "new_source": str(new_audio),
            "new_sha256": sha256_file(new_audio),
        }

    public = {"schema_version": 1, "round_id": ROUND_ID, "pairs": public_pairs}
    (review / "data.js").write_text("window.CHRIS_REPAIR_PAIRS = " + json.dumps(public, indent=2) + ";\n", encoding="utf-8")
    (review / "index.html").write_text(
        """<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Chris repair validation</title><link rel='icon' href='data:,'><link rel='stylesheet' href='styles.css'></head><body><header><p class='eyebrow'>Alexandria blind pairwise review</p><h1>Chris reference repair validation</h1><p>Each pair uses the same model, line, and delivery. Only the identity reference differs. Prefer the version with less echo/background coloration while preserving Chris and natural speech.</p><button id='export'>Export results</button> <span id='progress'></span></header><main id='app'></main><script src='data.js'></script><script src='app.js'></script></body></html>""",
        encoding="utf-8",
    )
    (review / "styles.css").write_text(
        """:root{font-family:Inter,system-ui,sans-serif;color:#29241e;background:#f2eee6}*{box-sizing:border-box}body{margin:0}header,main{max-width:980px;margin:auto;padding:28px 20px}header{border-bottom:1px solid #d4ccbf}.eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:12px;color:#70685e}h1,h2{font-family:Georgia,serif}.pair{background:#fffdf8;border:1px solid #d5cdbf;border-radius:10px;padding:18px;margin:18px 0}.reference{background:#eee8dd;padding:12px;border-radius:7px}.columns{display:grid;grid-template-columns:1fr 1fr;gap:18px}.candidate{border:1px solid #d8d0c4;border-radius:8px;padding:14px}audio{width:100%}.choices{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0}.choices label{border:1px solid #aaa096;border-radius:6px;padding:10px;background:white}textarea{width:100%;min-height:70px;font:inherit;padding:8px}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}@media(max-width:650px){.columns{grid-template-columns:1fr}}""",
        encoding="utf-8",
    )
    (review / "app.js").write_text(
        """(()=>{const d=window.CHRIS_REPAIR_PAIRS,k='chris-repair-pairs:'+d.round_id;let s={};try{s=JSON.parse(localStorage.getItem(k)||'{}')}catch(_){s={}}const app=document.getElementById('app');for(const p of d.pairs){const x=s[p.pair_id]||{},el=document.createElement('article');el.className='pair';el.innerHTML=`<h2>${p.style_label}</h2><p>${p.target_text}</p><div class="reference"><strong>Identity reference</strong><audio controls preload="none" src="${p.reference_audio}"></audio></div><div class="columns"><div class="candidate"><h3>A</h3><audio controls preload="none" src="${p.audio_a}"></audio></div><div class="candidate"><h3>B</h3><audio controls preload="none" src="${p.audio_b}"></audio></div></div><div class="choices"><label><input type="radio" name="${p.pair_id}" data-id="${p.pair_id}" value="a" ${x.choice==='a'?'checked':''}> A is better</label><label><input type="radio" name="${p.pair_id}" data-id="${p.pair_id}" value="tie" ${x.choice==='tie'?'checked':''}> No meaningful preference</label><label><input type="radio" name="${p.pair_id}" data-id="${p.pair_id}" value="b" ${x.choice==='b'?'checked':''}> B is better</label></div><textarea data-note="${p.pair_id}" placeholder="Optional note about echo, identity, or artifacts.">${x.notes||''}</textarea>`;app.appendChild(el)}for(const e of document.querySelectorAll('[data-id]'))e.addEventListener('change',()=>{const id=e.dataset.id;s[id]=s[id]||{};s[id].choice=e.value;save()});for(const e of document.querySelectorAll('[data-note]'))e.addEventListener('input',()=>{const id=e.dataset.note;s[id]=s[id]||{};s[id].notes=e.value;save()});function save(){localStorage.setItem(k,JSON.stringify(s));progress()}function progress(){document.getElementById('progress').textContent=`${Object.values(s).filter(x=>x.choice).length} of ${d.pairs.length} decided`}document.getElementById('export').onclick=()=>{const a=document.createElement('a'),b=new Blob([JSON.stringify({schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),results:s},null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();""",
        encoding="utf-8",
    )
    write_json(output / "private/answer-key.json", {"schema_version": 1, "round_id": ROUND_ID, "pairs": answer_pairs})
    write_json(output / "manifest.json", {"schema_version": 1, "round_id": ROUND_ID, "pair_count": len(public_pairs), "review": "review/index.html", "production_promotion_allowed": False})
    print(json.dumps({"review": str(review / 'index.html'), "pairs": len(public_pairs)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
