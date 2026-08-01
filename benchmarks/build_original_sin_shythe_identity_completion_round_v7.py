#!/usr/bin/env python3
"""Build a one-candidate completion review for Shythe Shahid identity evidence."""

from __future__ import annotations

import json
from pathlib import Path
import shutil


ROUND_ID = "alexandria_original_sin_shythe_identity_completion_round_v7"
PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
SALVAGE_ROOT = PROJECT / "external_workflows/big_finish_overlap_reference_v1/overlap_identity_salvage_round_v6"
OUTPUT = PROJECT / "external_workflows/big_finish_overlap_reference_v1/shythe_identity_completion_round_v7"
CANDIDATE_ID = "5ad130953556d32b"


def main() -> int:
    answer = json.loads((SALVAGE_ROOT / "private/answer-key.json").read_text(encoding="utf-8"))
    row = answer["candidates"][CANDIDATE_ID]
    if row["character"] != "Shythe Shahid":
        raise RuntimeError("Shythe candidate identity changed.")
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    review = OUTPUT / "review"
    audio = OUTPUT / "audio" / f"{CANDIDATE_ID}.wav"
    audio.parent.mkdir(parents=True)
    shutil.copyfile(row["audio_path"], audio)
    review.mkdir(parents=True)
    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "candidate_id": CANDIDATE_ID,
        "audio": "../audio/" + audio.name,
        "transcript": row["transcript"],
        "prior_review": {
            "identity": 5,
            "completeness": "complete",
            "decision": "pass",
        },
        "required_missing_scores": [
            "cleanliness",
            "naturalness",
            "intelligibility",
            "contamination",
        ],
        "production_changes": False,
    }
    (review / "data.js").write_text(
        "window.ALEXANDRIA_SHYTHE_COMPLETION = "
        + json.dumps(public, indent=2, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )
    (review / "index.html").write_text(
        """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Shythe identity completion</title><link rel='icon' href='data:,'><style>:root{font-family:Inter,system-ui,sans-serif;color:#29251f;background:#f1eee7}body{margin:0}header,main{max-width:760px;margin:auto;padding:28px 20px}header{border-bottom:1px solid #d4ccbf}h1,h2{font-family:Georgia,serif}.card{background:#fffdf8;border:1px solid #d5cdbf;border-radius:10px;padding:20px;margin:22px 0}.ratings{display:grid;grid-template-columns:1fr 1fr;gap:14px}label{display:grid;gap:6px;font-weight:650}select,textarea{font:inherit;padding:9px;border:1px solid #b9afa2;border-radius:5px;background:white}textarea{min-height:80px}audio{width:100%}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}@media(max-width:560px){.ratings{grid-template-columns:1fr}}</style></head><body><header><p>Alexandria fail-closed completion</p><h1>Shythe Shahid identity source</h1><p>You already passed identity at 5/5 and marked the entire line complete. Score the four omitted fields so the source can be accepted or rejected under the same contract.</p><button id='export'>Export completion review</button></header><main><section class='card'><audio controls src='../audio/5ad130953556d32b.wav'></audio><p><strong>Transcript:</strong> I'm Shahid Shahid. This is Empire Today.</p><div class='ratings' id='ratings'></div><label>Notes<textarea id='notes'></textarea></label></section></main><script src='data.js'></script><script src='app.js'></script></body></html>""",
        encoding="utf-8",
    )
    (review / "app.js").write_text(
        """(()=>{'use strict';const d=window.ALEXANDRIA_SHYTHE_COMPLETION,key='alexandria-shythe-completion-v7';let s={};try{s=JSON.parse(localStorage.getItem(key)||'{}')}catch(_){s={}}const scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join('');const r=document.querySelector('#ratings');for(const f of d.required_missing_scores){const l=document.createElement('label');l.textContent=f[0].toUpperCase()+f.slice(1);const q=document.createElement('select');q.innerHTML=scale;q.value=s[f]||'';q.onchange=()=>{s[f]=q.value;localStorage.setItem(key,JSON.stringify(s))};l.appendChild(q);r.appendChild(l)}const notes=document.querySelector('#notes');notes.value=s.notes||'';notes.oninput=()=>{s.notes=notes.value;localStorage.setItem(key,JSON.stringify(s))};document.querySelector('#export').onclick=()=>{const result={schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),results:{[d.candidate_id]:{identity:'5',cleanliness:s.cleanliness||null,naturalness:s.naturalness||null,intelligibility:s.intelligibility||null,contamination:s.contamination||null,completeness:'complete',decision:'pass',notes:s.notes||null}}};const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));a.download=d.round_id+'-tristan.json';a.click()}})();""",
        encoding="utf-8",
    )
    print(json.dumps({"round_id": ROUND_ID, "review": str(review / "index.html")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
