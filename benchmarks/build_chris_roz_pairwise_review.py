#!/usr/bin/env python3
"""Build a four-pair blind tie-breaker for the Chris/Roz model round."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / ".omo/evidence/chris-roz-multimodel-round1-v1"
DEFAULT_OUTPUT = ROOT / ".omo/evidence/chris-roz-pairwise-v1"

PAIR_CELLS = (
    ("chris", "dry_humour", 1),
    ("chris", "dry_humour", 2),
    ("roz", "neutral", 1),
    ("roz", "neutral", 2),
)
MODELS = ("fish_s2_pro_cloud", "indextts2_matched_control")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def copy_audio(source: Path, target: Path) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {"audio": str(target), "sha256": sha256_file(target)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    evidence = Path(args.evidence_root).expanduser().resolve()
    output = Path(args.output_root).expanduser().resolve()
    if output.exists():
        shutil.rmtree(output)
    review = output / "review"
    audio_root = review / "audio"
    reference_root = review / "reference"
    answer = read_json(evidence / "private/answer-key.json")
    internal = read_json(evidence / "private/internal-manifest.json")
    target_by_source_id = {
        str(row["sample_id"]): str(row["target_text"])
        for row in internal["sample_specs"]
    }
    samples = list(answer["samples"].values())
    public_pairs: list[dict[str, Any]] = []
    private_pairs: dict[str, Any] = {}
    references: dict[str, Any] = {}

    for identity in ("chris", "roz"):
        rows = [
            row for row in samples
            if row["identity_key"] == identity and row["reference_tier"] == "clean_actor"
        ]
        reference = rows[0]["reference"]
        source = evidence / str(reference["audio_file"])
        target = reference_root / f"{identity}.wav"
        copy_audio(source, target)
        references[identity] = {
            "label": rows[0]["expected_identity"],
            "audio": f"reference/{identity}.wav",
            "transcript": reference["transcript"],
            "sha256": sha256_file(target),
        }

    for identity, style, repeat in PAIR_CELLS:
        candidates = [
            row for row in samples
            if row["identity_key"] == identity
            and row["reference_tier"] == "clean_actor"
            and row["style"] == style
            and int(row["repeat"]) == repeat
            and row["model_key"] in MODELS
        ]
        if {row["model_key"] for row in candidates} != set(MODELS):
            raise ValueError(f"Pair cell is incomplete: {identity}/{style}/{repeat}")
        candidates.sort(key=lambda row: row["model_key"])
        pair_id = hashlib.sha256(f"{identity}:{style}:{repeat}".encode()).hexdigest()[:16]
        swap = int(hashlib.sha256(f"side:{pair_id}".encode()).hexdigest(), 16) % 2 == 1
        ordered = list(reversed(candidates)) if swap else candidates
        public_audio = {}
        for side, row in zip(("a", "b"), ordered):
            source = Path(row["source_audio"])
            target = audio_root / f"{pair_id}-{side}.wav"
            copy_audio(source, target)
            public_audio[side] = f"audio/{pair_id}-{side}.wav"
        public_pairs.append({
            "pair_id": pair_id,
            "identity_key": identity,
            "expected_identity": ordered[0]["expected_identity"],
            "style": style,
            "style_label": style.replace("_", " ").title(),
            "target_text": target_by_source_id[ordered[0]["source_sample_id"]],
            "requested_instruction": ordered[0]["instruction"],
            "reference_audio": references[identity]["audio"],
            "reference_transcript": references[identity]["transcript"],
            "candidate_a": public_audio["a"],
            "candidate_b": public_audio["b"],
        })
        private_pairs[pair_id] = {
            "pair_id": pair_id,
            "identity_key": identity,
            "style": style,
            "repeat": repeat,
            "a": ordered[0],
            "b": ordered[1],
        }

    data = {
        "schema_version": 1,
        "round_id": "alexandria_chris_roz_pairwise_v1",
        "title": "Chris and Roz final tie-breakers",
        "pairs": public_pairs,
        "pair_count": len(public_pairs),
        "model_identity_hidden": True,
        "reference_tier": "clean_actor",
    }
    review.mkdir(parents=True, exist_ok=True)
    (review / "data.js").write_text("window.CHRIS_ROZ_PAIRWISE = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8")
    (review / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (review / "styles.css").write_text(STYLES, encoding="utf-8")
    (review / "app.js").write_text(APP_JS, encoding="utf-8")
    write_json(output / "private/answer-key.json", {
        "schema_version": 1,
        "round_id": data["round_id"],
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "pairs": private_pairs,
        "production_promotion_allowed": False,
    })
    write_json(output / "manifest.json", {
        "schema_version": 1,
        "round_id": data["round_id"],
        "pair_count": len(public_pairs),
        "review": "review/index.html",
        "answer_key": "private/answer-key.json",
        "production_promotion_allowed": False,
    })
    print(json.dumps({"review": str(review / "index.html"), "pairs": len(public_pairs)}, indent=2))
    return 0


INDEX_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Chris and Roz tie-breakers</title><link rel="icon" href="data:,"><link rel="stylesheet" href="styles.css"></head><body><main><p class="eyebrow">Alexandria blind pairwise review</p><h1>Chris and Roz final tie-breakers</h1><p>Choose the better performance. Model names and left/right assignments are hidden.</p><div class="toolbar"><button id="previous">← Previous</button><button id="next">Next →</button><button id="next-incomplete">Next incomplete</button><button id="export">Export results</button><span id="progress"></span></div><section id="pair"></section></main><script src="data.js"></script><script src="app.js"></script></body></html>"""

STYLES = """:root{font-family:Inter,system-ui,sans-serif;background:#f2eee6;color:#29241e}*{box-sizing:border-box}body{margin:0}main{max-width:980px;margin:auto;padding:34px 22px}.eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:12px;color:#6d655a}h1,h2{font-family:Georgia,serif}.toolbar{position:sticky;top:0;z-index:3;display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:12px 0;background:#f2eee6;border-bottom:1px solid #d4ccbf}button{font:inherit;padding:9px 12px;border:1px solid #315c55;background:#fff;color:#315c55;border-radius:6px}button:hover,button:focus-visible{background:#e5ece9}.card{margin-top:24px;background:#fffdf8;border:1px solid #d5cdbf;border-radius:12px;padding:22px}.meta{color:#6b6358}.target{font:19px/1.5 Georgia,serif}.reference{border-left:4px solid #806e5d;padding:12px 16px;background:#eee7db}.candidates{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:20px}.candidate{border:1px solid #ccc1b2;border-radius:8px;padding:16px}.candidate h3{margin-top:0}audio{width:100%}.decision{display:grid;gap:10px;margin-top:20px}.choice{display:flex;align-items:center;gap:10px;padding:12px;border:1px solid #c8beb0;border-radius:7px}.choice input{width:20px;height:20px}textarea{width:100%;min-height:82px;font:inherit;padding:9px}#progress{margin-left:auto}@media(max-width:680px){.candidates{grid-template-columns:1fr}#progress{width:100%;margin-left:0}}"""

APP_JS = """(()=>{'use strict';const d=window.CHRIS_ROZ_PAIRWISE;const key='alexandria-pairwise:'+d.round_id;let saved={};try{saved=JSON.parse(localStorage.getItem(key)||'{}')}catch(_){saved={}}let index=0;const pair=document.querySelector('#pair'),progress=document.querySelector('#progress');function persist(){localStorage.setItem(key,JSON.stringify(saved));renderProgress()}function renderProgress(){const complete=d.pairs.filter(p=>saved[p.pair_id]?.choice).length;progress.textContent=`${complete} of ${d.pairs.length} decided`}function render(){const p=d.pairs[index],row=saved[p.pair_id]||{};pair.innerHTML=`<article class="card"><p class="meta">Pair ${index+1} of ${d.pairs.length} · ${p.expected_identity} · ${p.style_label}</p><h2>${p.expected_identity}</h2><p class="target">${p.target_text}</p><p>${p.requested_instruction}</p><div class="reference"><strong>Identity reference</strong><audio controls preload="none" src="${p.reference_audio}"></audio></div><div class="candidates"><div class="candidate"><h3>Candidate A</h3><audio controls preload="none" src="${p.candidate_a}"></audio></div><div class="candidate"><h3>Candidate B</h3><audio controls preload="none" src="${p.candidate_b}"></audio></div></div><fieldset class="decision"><legend>Which is better?</legend>${[['a','A is better'],['tie','No meaningful preference'],['b','B is better']].map(([v,l])=>`<label class="choice"><input type="radio" name="choice" value="${v}" ${row.choice===v?'checked':''}>${l}</label>`).join('')}</fieldset><label>Notes<textarea id="notes">${row.notes||''}</textarea></label></article>`;pair.querySelectorAll('input[name="choice"]').forEach(n=>n.onchange=()=>{saved[p.pair_id]={...(saved[p.pair_id]||{}),choice:n.value,updated_at:new Date().toISOString()};persist()});pair.querySelector('#notes').oninput=e=>{saved[p.pair_id]={...(saved[p.pair_id]||{}),notes:e.target.value,updated_at:new Date().toISOString()};persist()};document.querySelector('#previous').disabled=index===0;document.querySelector('#next').disabled=index===d.pairs.length-1;renderProgress()}document.querySelector('#previous').onclick=()=>{index=Math.max(0,index-1);render()};document.querySelector('#next').onclick=()=>{index=Math.min(d.pairs.length-1,index+1);render()};document.querySelector('#next-incomplete').onclick=()=>{const next=d.pairs.findIndex((p,i)=>i>index&&!saved[p.pair_id]?.choice);const wrap=next>=0?next:d.pairs.findIndex(p=>!saved[p.pair_id]?.choice);if(wrap>=0){index=wrap;render()}};document.querySelector('#export').onclick=()=>{const payload={schema_version:1,round_id:d.round_id,reviewer:'tristan',exported_at:new Date().toISOString(),rows:d.pairs.map(p=>({pair_id:p.pair_id,...(saved[p.pair_id]||{})})).filter(r=>r.choice||r.notes)};const blob=new Blob([JSON.stringify(payload,null,2)+'\\n'],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=d.round_id+'-tristan.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};render()})();"""

if __name__ == "__main__":
    raise SystemExit(main())
