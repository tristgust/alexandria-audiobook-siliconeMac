#!/usr/bin/env python3
"""Build the final bounded direct-substitution repair round."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from pathlib import Path

from mlx_audio.sts.models.mel_roformer import MelRoFormer, MelRoFormerConfig
from model_registry import resolve_model_path

from benchmarks.build_original_sin_direct_substitution_round import encode_proxy, probe_audio
from benchmarks.build_original_sin_overlap_reference_round import (
    VOCAL_MODEL,
    WHISPER_MODEL_KEY,
    enhance,
    load_mossformer,
    metrics,
    separate,
    sha256_file,
    transcribe,
    utc_now,
    write_json,
)
from benchmarks.build_original_sin_overlap_reference_repair_round import (
    precise_source_cut,
    project_hashes,
    re_slug,
    treatment_provenance,
)
from benchmarks.original_sin_overlap_word_alignment import accepted_transcript_check, normalized_words, transcript_check_eligible


ROUND_ID = "alexandria_original_sin_direct_substitution_final_repair_v3"
SEED = 20260731
DEFAULT_MEDIA = Path("/Users/tristan/Library/Mobile Documents/3L68KQB4HG~com~readdle~CommonDocuments/Documents/DW7*(RERUN:BF)/7c_divergentTimeline3*(RERUN:BF)/20c_bennyChris&Roz/1_originalSinAdaptation.mp3")
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_direct_substitution_final_repair_plan.json")


class FinalDirectError(RuntimeError):
    pass


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_review(output: Path, groups: list[dict]) -> None:
    review = output / "review"
    audio = review / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    answer = {}
    public_groups = []
    for group in groups:
        candidates = list(group["candidates"])
        rng.shuffle(candidates)
        public = []
        for candidate in candidates:
            blind_id = hashlib.sha256(
                f"{ROUND_ID}:{group['chunk_id']}:{candidate['treatment']}:{candidate['proxy_sha256']}".encode()
            ).hexdigest()[:16]
            shutil.copy2(candidate["proxy_path"], audio / f"{blind_id}.mp3")
            public.append({"id": blind_id, "audio": f"audio/{blind_id}.mp3"})
            answer[blind_id] = {
                **candidate,
                "wav_path": str(candidate["wav_path"]),
                "proxy_path": str(candidate["proxy_path"]),
                "character": group["character"],
                "book_speaker": group["book_speaker"],
                "chunk_id": group["chunk_id"],
                "transcript": group["transcript"],
                "source": group["source"],
            }
        public_groups.append({"character": group["character"], "chunk_id": group["chunk_id"], "transcript": group["transcript"], "review_context": group.get("review_context"), "candidates": public})
    write_json(output / "private/answer-key.json", {"schema_version": 1, "round_id": ROUND_ID, "candidates": answer, "production_changes": False})
    (review / "data.js").write_text("window.ORIGINAL_SIN_FINAL_DIRECT_REPAIR = " + json.dumps({"round_id": ROUND_ID, "groups": public_groups}, indent=2, ensure_ascii=False) + ";\n", encoding="utf-8")
    (review / "index.html").write_text(REVIEW_HTML, encoding="utf-8")


REVIEW_HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Original Sin final direct repair</title><link rel="icon" href="data:,"><style>:root{font-family:Inter,system-ui,sans-serif;color:#28231f;background:#f2eee6}*{box-sizing:border-box}body{margin:0}header,main{max-width:960px;margin:auto;padding:24px 20px}header{border-bottom:1px solid #d2c8b8}h1,h2{font-family:Georgia,serif}.group{background:#fffdf8;border:1px solid #d5ccbd;border-radius:10px;padding:18px;margin:18px 0}.context{padding:10px;border-left:3px solid #7d7468;background:#f4efe7}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.candidate{border:1px solid #d9d0c3;border-radius:8px;padding:12px;background:#fff}audio{width:100%}label{display:grid;gap:4px;font-size:13px;font-weight:650;margin-top:7px}select,textarea{font:inherit;padding:7px;border:1px solid #b9afa2;border-radius:5px}textarea{min-height:62px}.decision{display:flex;gap:12px;margin:8px 0}.decision label{display:flex;align-items:center;gap:5px}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}@media(max-width:780px){.grid{grid-template-columns:1fr}}</style></head><body><header><h1>Original Sin final direct repair</h1><p>Each hidden candidate is a production-format MP3 proxy. Reject foreign onset, incomplete boundaries, music/effects, echo, or speaker bleed.</p><button id="export">Export review</button> <span id="progress"></span></header><main id="app"></main><script src="data.js"></script><script>(()=>{const d=window.ORIGINAL_SIN_FINAL_DIRECT_REPAIR,k='os-final-direct:'+d.round_id;let s={};try{s=JSON.parse(localStorage.getItem(k)||'{}')}catch(_){s={}}const scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join(''),app=document.querySelector('#app');for(const g of d.groups){const sec=document.createElement('section');sec.className='group';sec.innerHTML=`<h2>${g.character} · chunk ${g.chunk_id}</h2><p>${g.transcript}</p>${g.review_context?`<p class="context">${g.review_context}</p>`:''}<div class="grid"></div>`;for(const [i,c] of g.candidates.entries()){const card=document.createElement('article');card.className='candidate';card.innerHTML=`<h3>Candidate ${i+1}</h3><audio controls src="${c.audio}"></audio>${['boundaries','isolation','naturalness','usefulness'].map(n=>`<label>${n}<select data-id="${c.id}" data-name="${n}">${scale}</select></label>`).join('')}<div class="decision"><label><input type="radio" name="d-${c.id}" value="pass" data-id="${c.id}" data-name="decision">Eligible</label><label><input type="radio" name="d-${c.id}" value="fail" data-id="${c.id}" data-name="decision">Reject</label></div><label>Notes<textarea data-id="${c.id}" data-name="notes"></textarea></label>`;sec.querySelector('.grid').appendChild(card)}app.appendChild(sec)}for(const e of document.querySelectorAll('[data-id]')){const v=s[e.dataset.id]?.[e.dataset.name];if(e.type==='radio')e.checked=v===e.value;else if(v!=null)e.value=v;e.addEventListener(e.tagName==='TEXTAREA'?'input':'change',ev=>{const id=ev.target.dataset.id,n=ev.target.dataset.name;s[id]=s[id]||{};s[id][n]=ev.target.value;localStorage.setItem(k,JSON.stringify(s));progress()})}function progress(){const ids=d.groups.flatMap(g=>g.candidates.map(c=>c.id));document.querySelector('#progress').textContent=`${ids.filter(id=>s[id]?.decision).length} of ${ids.length} decided`}document.querySelector('#export').onclick=()=>{const a=document.createElement('a'),b=new Blob([JSON.stringify({schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),results:s},null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();</script></body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--media", type=Path, default=DEFAULT_MEDIA)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    project = args.project_root.expanduser().resolve()
    media = args.media.expanduser().resolve()
    plan = read_json(args.plan.expanduser().resolve())
    if plan.get("round_id") != ROUND_ID or sum(len(g["treatments"]) for g in plan["groups"]) != plan.get("candidate_count"):
        raise FinalDirectError("Final direct plan mismatch")
    chunks = read_json(project / "chunks.json")
    segments = read_json(project / "external_workflows/big_finish_overlap_reference_v1/private/transcript.json")["segments"]
    for spec in plan["groups"]:
        chunk = chunks[int(spec["chunk_id"])]
        if chunk.get("speaker") != spec["book_speaker"] or normalized_words(chunk.get("text")) != normalized_words(spec["expected_transcript"]):
            raise FinalDirectError(f"Chunk binding mismatch: {spec['chunk_id']}")
    output = args.output_root.expanduser().resolve() if args.output_root else project / "external_workflows/big_finish_overlap_reference_v1" / "direct_substitution_final_repair_round_v3"
    if output.exists():
        if not args.replace:
            raise FinalDirectError(f"Output exists; pass --replace: {output}")
        shutil.rmtree(output)
    before = project_hashes(project)
    whisper = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    vocal = MelRoFormer.from_pretrained(VOCAL_MODEL, config=MelRoFormerConfig.zfturbo_vocals_v1()); vocal.eval()
    moss = load_mossformer()
    private = output / "private/audio"
    groups = []
    for spec in plan["groups"]:
        start, end = int(spec["segment_start"]), int(spec["segment_end"])
        segment_start = float(segments[start]["start"])
        broad_start = max(0.0, segment_start - float(plan["broad_padding_seconds"]))
        broad_end = float(segments[end]["end"]) + float(plan["broad_padding_seconds"])
        minimum_start = segment_start + float(spec.get("minimum_source_start_offset_seconds", 0.0))
        maximum_end = float(segments[end + 1]["start"]) - float(spec.get("maximum_next_speaker_margin_seconds", 0.03)) if end + 1 < len(segments) else None
        slug = f"chunk_{spec['chunk_id']}_{re_slug(spec['book_speaker'])}"
        source = private / f"{slug}__source.wav"
        alignment = precise_source_cut(
            media=media,
            destination=source,
            broad_path=private / f"{slug}__broad.wav",
            broad_start=broad_start,
            broad_end=broad_end,
            expected=spec.get("alignment_transcript", spec["expected_transcript"]),
            whisper_model=whisper,
            leading_margin=float(plan["leading_margin_seconds"]),
            trailing_margin=float(plan["trailing_margin_seconds"]),
            minimum_source_start=minimum_start,
            maximum_source_end=maximum_end,
        )
        candidates = []
        for treatment in spec["treatments"]:
            if treatment == "source_mix":
                wav = source
            elif treatment == "mel_roformer_vocal":
                wav = private / f"{slug}__mel.wav"; separate(vocal, source, wav)
            elif treatment == "mossformer2_source_mix":
                wav = private / f"{slug}__moss.wav"; enhance(source, wav, moss)
            else:
                raise FinalDirectError(f"Unsupported treatment: {treatment}")
            accepted = [spec["expected_transcript"], spec.get("alignment_transcript", spec["expected_transcript"])]
            wav_check = accepted_transcript_check(accepted, transcribe(wav, whisper))
            proxy = private / f"{slug}__{treatment}.mp3"
            encode_proxy(wav, proxy, bitrate=plan["production_proxy"]["bitrate"])
            proxy_check = accepted_transcript_check(accepted, transcribe(proxy, whisper))
            probe = probe_audio(proxy)
            eligible = transcript_check_eligible(wav_check) and transcript_check_eligible(proxy_check) and probe["codec_name"] == "mp3" and probe["sample_rate"] == 44100 and probe["channels"] == 2
            if not eligible:
                continue
            candidates.append({"treatment": treatment, "wav_path": wav, "wav_metrics": metrics(wav), "wav_objective": wav_check, "proxy_path": proxy, "proxy_sha256": sha256_file(proxy), "proxy_probe": probe, "proxy_objective": proxy_check, "objective_eligible": True, **treatment_provenance(treatment)})
        if not candidates:
            raise FinalDirectError(f"No objective-eligible direct candidate for chunk {spec['chunk_id']}")
        groups.append({"character": spec["character"], "book_speaker": spec["book_speaker"], "chunk_id": int(spec["chunk_id"]), "transcript": spec["expected_transcript"], "review_context": spec.get("review_context"), "source": {"media_sha256": sha256_file(media), "segment_start": start, "segment_end": end, **alignment}, "candidates": candidates})
        print(f"built chunk {spec['chunk_id']} {spec['character']} ({len(candidates)} eligible)", flush=True)
    build_review(output, groups)
    after = project_hashes(project)
    if before != after:
        raise FinalDirectError("Protected project hashes changed")
    write_json(output / "generation-summary.json", {"schema_version": 1, "round_id": ROUND_ID, "generated_at": utc_now(), "chunk_count": len(groups), "candidate_count": sum(len(g["candidates"]) for g in groups), "protected_project_hashes_before": before, "protected_project_hashes_after": after, "production_changes": False, "output_root": str(output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
