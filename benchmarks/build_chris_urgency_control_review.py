#!/usr/bin/env python3
"""Build the blind Chris urgency-control review."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".omo/evidence/chris-urgency-control-retest-v1"
DEFAULT_OUTPUT = ROOT / ".omo/evidence/chris-urgency-control-review-v1"
ROUND_ID = "alexandria_chris_urgency_control_review_v1"


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
    internal = read_json(EVIDENCE / "private/internal-manifest.json")
    reference = EVIDENCE / "private/references/chris/clean_actor/reference.wav"
    shutil.copy2(reference, reference_root / "chris.wav")

    rows = []
    answer = {}
    for spec in internal["sample_specs"]:
        audio = EVIDENCE / spec["output_file"]
        result = EVIDENCE / spec["result_file"]
        if not audio.is_file() or not result.is_file():
            raise FileNotFoundError(audio if not audio.is_file() else result)
        blind = str(spec["blind_id"])
        target = audio_root / f"{blind}.wav"
        shutil.copy2(audio, target)
        rows.append({
            "sample_id": blind,
            "audio": f"audio/{blind}.wav",
            "target_text": spec["target_text"],
            "expected_identity": "Chris Cwej",
            "reference_audio": "reference/chris.wav",
        })
        answer[blind] = {
            "model_key": spec["model_key"],
            "variant": spec.get("fish_prompt_mode") or spec["output_file"].split("/")[-1].replace(".wav", ""),
            "control": spec["control"],
            "emotion_reference": spec["emotion_reference"],
            "source_audio": str(audio),
            "source_audio_sha256": sha256_file(audio),
        }
    random.Random(20260729).shuffle(rows)
    public = {"schema_version": 1, "round_id": ROUND_ID, "samples": rows}
    (review / "data.js").write_text("window.CHRIS_URGENCY_REVIEW = " + json.dumps(public, indent=2) + ";\n", encoding="utf-8")
    (review / "index.html").write_text(
        """<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Chris urgency retest</title><link rel='icon' href='data:,'><link rel='stylesheet' href='styles.css'></head><body><header><p class='eyebrow'>Alexandria blind control review</p><h1>Chris urgency retest</h1><p>All candidates use the clean Travis identity reference. Judge whether protective urgency is unmistakable and sustained through the whole line.</p><button id='export'>Export results</button> <span id='progress'></span></header><main id='app'></main><script src='data.js'></script><script src='app.js'></script></body></html>""",
        encoding="utf-8",
    )
    (review / "styles.css").write_text(
        """:root{font-family:Inter,system-ui,sans-serif;color:#29241e;background:#f2eee6}*{box-sizing:border-box}body{margin:0}header,main{max-width:980px;margin:auto;padding:28px 20px}header{border-bottom:1px solid #d4ccbf}.eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:12px;color:#70685e}h1,h2{font-family:Georgia,serif}.card{background:#fffdf8;border:1px solid #d5cdbf;border-radius:10px;padding:18px;margin:18px 0}.reference{background:#eee8dd;padding:12px;border-radius:7px}audio{width:100%}.ratings{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}label{display:grid;gap:5px;font-size:13px;font-weight:650}select,textarea{font:inherit;padding:8px;border:1px solid #b9afa2;border-radius:5px;background:white}textarea{width:100%;min-height:70px}.checks{display:flex;gap:18px;margin:14px 0}.checks label{display:flex;align-items:center;gap:7px}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}@media(max-width:700px){.ratings{grid-template-columns:1fr 1fr}}""",
        encoding="utf-8",
    )
    (review / "app.js").write_text(
        """(()=>{const d=window.CHRIS_URGENCY_REVIEW,k='chris-urgency:'+d.round_id;let s={};try{s=JSON.parse(localStorage.getItem(k)||'{}')}catch(_){s={}}const app=document.getElementById('app'),scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join('');for(const [i,c] of d.samples.entries()){const x=s[c.sample_id]||{},el=document.createElement('article');el.className='card';el.innerHTML=`<h2>Candidate ${i+1}</h2><p>${c.target_text}</p><div class="reference"><strong>Identity reference</strong><audio controls preload="none" src="${c.reference_audio}"></audio></div><audio controls preload="none" src="${c.audio}"></audio><div class="ratings"><label>Identity<select data-id="${c.sample_id}" data-name="identity">${scale}</select></label><label>Urgency delivery<select data-id="${c.sample_id}" data-name="delivery">${scale}</select></label><label>Naturalness<select data-id="${c.sample_id}" data-name="naturalness">${scale}</select></label><label>Artifacts (1 clean, 5 bad)<select data-id="${c.sample_id}" data-name="artifacts">${scale}</select></label></div><div class="checks"><label><input type="checkbox" data-id="${c.sample_id}" data-name="mode_clear"> Urgency is clear throughout</label><label><input type="checkbox" data-id="${c.sample_id}" data-name="retain"> Retain</label></div><textarea data-id="${c.sample_id}" data-name="notes" placeholder="Optional note"></textarea>`;app.appendChild(el)}for(const e of document.querySelectorAll('[data-id]')){const id=e.dataset.id,n=e.dataset.name,v=s[id]?.[n];if(e.type==='checkbox')e.checked=Boolean(v);else if(v!=null)e.value=v;e.addEventListener(e.tagName==='TEXTAREA'?'input':'change',()=>{s[id]=s[id]||{};s[id][n]=e.type==='checkbox'?e.checked:e.value;localStorage.setItem(k,JSON.stringify(s));progress()})}function progress(){document.getElementById('progress').textContent=`${Object.values(s).filter(x=>x.identity&&x.delivery&&x.naturalness&&x.artifacts).length} of ${d.samples.length} scored`}document.getElementById('export').onclick=()=>{const a=document.createElement('a'),b=new Blob([JSON.stringify({schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),scores:s},null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();""",
        encoding="utf-8",
    )
    write_json(output / "private/answer-key.json", {"schema_version": 1, "round_id": ROUND_ID, "samples": answer})
    write_json(output / "manifest.json", {"schema_version": 1, "round_id": ROUND_ID, "sample_count": len(rows), "review": "review/index.html", "production_promotion_allowed": False})
    print(json.dumps({"review": str(review / 'index.html'), "samples": len(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
