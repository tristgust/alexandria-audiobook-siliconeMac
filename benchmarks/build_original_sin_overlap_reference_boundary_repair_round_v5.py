#!/usr/bin/env python3
"""Build the Beltempest and remaining boundary/source reference repair round."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from pathlib import Path

from mlx_audio.sts.models.mel_roformer import MelRoFormer, MelRoFormerConfig
from model_registry import resolve_model_path

from benchmarks.build_original_sin_overlap_reference_round import (
    VOCAL_MODEL, WHISPER_MODEL_KEY, enhance, load_mossformer, metrics,
    separate, sha256_file, transcribe, utc_now, write_json,
)
from benchmarks.build_original_sin_overlap_reference_repair_round import (
    precise_source_cut, project_hashes, re_slug, treatment_provenance,
    write_center_channel,
)
from benchmarks.original_sin_overlap_word_alignment import transcript_comparison

ROUND_ID = "alexandria_original_sin_overlap_reference_boundary_repair_v5"
SEED = 20260731
DEFAULT_MEDIA = Path("/Users/tristan/Library/Mobile Documents/3L68KQB4HG~com~readdle~CommonDocuments/Documents/DW7*(RERUN:BF)/7c_divergentTimeline3*(RERUN:BF)/20c_bennyChris&Roz/1_originalSinAdaptation.mp3")
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_overlap_reference_boundary_repair_plan_v5.json")


class BoundaryRepairError(RuntimeError):
    pass


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_review(output: Path, groups: list[dict]) -> None:
    review, audio = output / "review", output / "review/audio"
    audio.mkdir(parents=True, exist_ok=True)
    rng, answer, public_groups = random.Random(SEED), {}, []
    for group in groups:
        candidates = list(group["candidates"]); rng.shuffle(candidates)
        public = []
        for candidate in candidates:
            cid = hashlib.sha256(
                f"{ROUND_ID}:{group['book_speaker']}:{candidate['treatment']}:{candidate['metrics']['sha256']}".encode()
            ).hexdigest()[:16]
            shutil.copy2(candidate["path"], audio / f"{cid}.wav")
            public.append({"id": cid, "audio": f"audio/{cid}.wav"})
            answer[cid] = {
                **candidate, "path": str(candidate["path"]),
                "character": group["character"], "book_speaker": group["book_speaker"],
                "transcript": group["transcript"], "source": group["source"],
            }
        public_groups.append({
            "character": group["character"], "transcript": group["transcript"],
            "review_context": group.get("review_context"), "candidates": public,
        })
    write_json(output / "private/answer-key.json", {
        "schema_version": 1, "round_id": ROUND_ID,
        "candidates": answer, "production_changes": False,
    })
    (review / "data.js").write_text(
        "window.ORIGINAL_SIN_BOUNDARY_REFERENCE_REPAIR = "
        + json.dumps({"round_id": ROUND_ID, "groups": public_groups}, indent=2, ensure_ascii=False)
        + ";\n", encoding="utf-8",
    )
    (review / "index.html").write_text(REVIEW_HTML, encoding="utf-8")


REVIEW_HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Original Sin boundary/reference repair</title><link rel="icon" href="data:,"><style>:root{font-family:Inter,system-ui,sans-serif;color:#28231f;background:#f2eee6}*{box-sizing:border-box}body{margin:0}header,main{max-width:1050px;margin:auto;padding:24px 20px}header{border-bottom:1px solid #d2c8b8}h1,h2{font-family:Georgia,serif}.group{background:#fffdf8;border:1px solid #d5ccbd;border-radius:10px;padding:18px;margin:18px 0}.context{padding:10px;border-left:3px solid #7d7468;background:#f4efe7}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.candidate{border:1px solid #d9d0c3;border-radius:8px;padding:12px;background:#fff}audio{width:100%}label{display:grid;gap:4px;font-size:13px;font-weight:650;margin-top:7px}select,textarea{font:inherit;padding:7px;border:1px solid #b9afa2;border-radius:5px}textarea{min-height:62px}.decision{display:flex;gap:12px;margin:8px 0}.decision label{display:flex;align-items:center;gap:5px}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}@media(max-width:780px){.grid{grid-template-columns:1fr}}</style></head><body><header><h1>Original Sin boundary and replacement-anchor repair</h1><p>Reject clipped first/final words, music, effects, echo, or damaged identity. Beltempest must be clear enough for a stable anchor.</p><button id="export">Export review</button> <span id="progress"></span></header><main id="app"></main><script src="data.js"></script><script>(()=>{const d=window.ORIGINAL_SIN_BOUNDARY_REFERENCE_REPAIR,k='os-boundary-ref:'+d.round_id;let s={};try{s=JSON.parse(localStorage.getItem(k)||'{}')}catch(_){s={}}const scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join(''),app=document.querySelector('#app');for(const g of d.groups){const sec=document.createElement('section');sec.className='group';sec.innerHTML=`<h2>${g.character}</h2><p>${g.transcript}</p>${g.review_context?`<p class="context">${g.review_context}</p>`:''}<div class="grid"></div>`;for(const [i,c] of g.candidates.entries()){const card=document.createElement('article');card.className='candidate';card.innerHTML=`<h3>Candidate ${i+1}</h3><audio controls src="${c.audio}"></audio>${['isolation','naturalness','identity','usefulness'].map(n=>`<label>${n}<select data-id="${c.id}" data-name="${n}">${scale}</select></label>`).join('')}<div class="decision"><label><input type="radio" name="d-${c.id}" value="pass" data-id="${c.id}" data-name="decision">Pass</label><label><input type="radio" name="d-${c.id}" value="fail" data-id="${c.id}" data-name="decision">Fail</label></div><label>Notes<textarea data-id="${c.id}" data-name="notes"></textarea></label>`;sec.querySelector('.grid').appendChild(card)}app.appendChild(sec)}for(const e of document.querySelectorAll('[data-id]')){const v=s[e.dataset.id]?.[e.dataset.name];if(e.type==='radio')e.checked=v===e.value;else if(v!=null)e.value=v;e.addEventListener(e.tagName==='TEXTAREA'?'input':'change',ev=>{const id=ev.target.dataset.id,n=ev.target.dataset.name;s[id]=s[id]||{};s[id][n]=ev.target.value;localStorage.setItem(k,JSON.stringify(s));progress()})}function progress(){const ids=d.groups.flatMap(g=>g.candidates.map(c=>c.id));document.querySelector('#progress').textContent=`${ids.filter(id=>s[id]?.decision).length} of ${ids.length} decided`}document.querySelector('#export').onclick=()=>{const a=document.createElement('a'),b=new Blob([JSON.stringify({schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),results:s},null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();</script></body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--media", type=Path, default=DEFAULT_MEDIA)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    project, media = args.project_root.expanduser().resolve(), args.media.expanduser().resolve()
    plan = read_json(args.plan.expanduser().resolve())
    if plan.get("round_id") != ROUND_ID or sum(len(g["treatments"]) for g in plan["groups"]) != 12:
        raise BoundaryRepairError("boundary repair plan mismatch")
    output = args.output_root.expanduser().resolve() if args.output_root else project / "external_workflows/big_finish_overlap_reference_v1/reference_boundary_repair_round_v5"
    if output.exists():
        if not args.replace: raise BoundaryRepairError(f"Output exists: {output}")
        shutil.rmtree(output)
    before = project_hashes(project)
    segments = read_json(project / "external_workflows/big_finish_overlap_reference_v1/private/transcript.json")["segments"]
    whisper = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    vocal = MelRoFormer.from_pretrained(VOCAL_MODEL, config=MelRoFormerConfig.zfturbo_vocals_v1()); vocal.eval()
    moss, private, groups = load_mossformer(), output / "private/audio", []
    for spec in plan["groups"]:
        start, end = int(spec["segment_start"]), int(spec["segment_end"])
        padding = float(spec.get("broad_padding_seconds", plan["broad_padding_seconds"]))
        maximum_end = None
        minimum_end = None
        if "minimum_segment_end_margin_seconds" in spec:
            minimum_end = float(segments[end]["end"]) + float(spec["minimum_segment_end_margin_seconds"])
        if end + 1 < len(segments) and "maximum_next_speaker_margin_seconds" in spec:
            maximum_end = float(segments[end + 1]["start"]) - float(spec["maximum_next_speaker_margin_seconds"])
        slug, source = re_slug(spec["book_speaker"]), private / f"{re_slug(spec['book_speaker'])}__source.wav"
        alignment = precise_source_cut(
            media=media, destination=source, broad_path=private / f"{slug}__broad.wav",
            broad_start=max(0.0, float(segments[start]["start"]) - padding),
            broad_end=float(segments[end]["end"]) + padding,
            expected=spec["expected_transcript"], whisper_model=whisper,
            leading_margin=float(spec.get("leading_margin_seconds", plan["leading_margin_seconds"])),
            trailing_margin=float(spec.get("trailing_margin_seconds", plan["trailing_margin_seconds"])),
            minimum_source_end=minimum_end, maximum_source_end=maximum_end,
        )
        candidates = []
        for treatment in spec["treatments"]:
            if treatment == "source_mix": path = source
            elif treatment == "mel_roformer_vocal": path = private / f"{slug}__mel.wav"; separate(vocal, source, path)
            elif treatment == "mossformer2_source_mix": path = private / f"{slug}__moss.wav"; enhance(source, path, moss)
            elif treatment == "center_channel_mid": path = private / f"{slug}__center.wav"; write_center_channel(source, path)
            else: raise BoundaryRepairError(treatment)
            observed = transcribe(path, whisper)
            comparison = transcript_comparison([spec["expected_transcript"]], observed, {})
            if comparison["word_error_rate"] == 0.0 and comparison["first_word_present"] and comparison["last_word_present"]:
                candidates.append({"treatment": treatment, "path": path, "metrics": metrics(path), "automatic_transcript": observed, **comparison, **treatment_provenance(treatment)})
        if not candidates: raise BoundaryRepairError(f"No eligible candidate: {spec['character']}")
        groups.append({"character": spec["character"], "book_speaker": spec["book_speaker"], "transcript": spec["expected_transcript"], "review_context": spec.get("review_context"), "source": {"media_sha256": sha256_file(media), "segment_start": start, "segment_end": end, **alignment}, "candidates": candidates})
        print(f"built {spec['character']} ({len(candidates)} eligible)", flush=True)
    build_review(output, groups)
    after = project_hashes(project)
    if before != after: raise BoundaryRepairError("protected project hashes changed")
    write_json(output / "generation-summary.json", {"schema_version":1,"round_id":ROUND_ID,"generated_at":utc_now(),"character_count":len(groups),"candidate_count":sum(len(g["candidates"]) for g in groups),"protected_project_hashes_before":before,"protected_project_hashes_after":after,"production_changes":False,"output_root":str(output)})
    return 0


if __name__ == "__main__": raise SystemExit(main())
