#!/usr/bin/env python3
"""Build repaired exact-line substitution candidates for five rejected pilot chunks."""
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
    RepairRoundError,
    precise_source_cut,
    project_hashes,
    read_json,
    re_slug,
    treatment_provenance,
    write_blend,
    write_center_channel,
)
from benchmarks.original_sin_overlap_word_alignment import accepted_transcript_check, normalized_words, transcript_check_eligible


ROUND_ID = "alexandria_original_sin_direct_substitution_repair_v2"
SEED = 20260731
DEFAULT_MEDIA = Path("/Users/tristan/Library/Mobile Documents/3L68KQB4HG~com~readdle~CommonDocuments/Documents/DW7*(RERUN:BF)/7c_divergentTimeline3*(RERUN:BF)/20c_bennyChris&Roz/1_originalSinAdaptation.mp3")
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_direct_substitution_repair_plan_v2.json")


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
        public_groups.append({
            "character": group["character"],
            "chunk_id": group["chunk_id"],
            "transcript": group["transcript"],
            "review_context": group.get("review_context"),
            "candidates": public,
        })
    write_json(output / "private" / "answer-key.json", {"schema_version": 1, "round_id": ROUND_ID, "candidates": answer, "production_changes": False})
    (review / "data.js").write_text(
        "window.ORIGINAL_SIN_DIRECT_REPAIR = " + json.dumps({"schema_version": 1, "round_id": ROUND_ID, "groups": public_groups}, indent=2, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    (review / "index.html").write_text(REVIEW_HTML, encoding="utf-8")


REVIEW_HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Original Sin direct repairs</title><link rel="icon" href="data:,"> <style>:root{font-family:Inter,system-ui,sans-serif;color:#28231f;background:#f2eee6}*{box-sizing:border-box}body{margin:0}header,main{max-width:1000px;margin:auto;padding:24px 20px}header{border-bottom:1px solid #d2c8b8}h1,h2{font-family:Georgia,serif}.group{background:#fffdf8;border:1px solid #d5ccbd;border-radius:10px;padding:18px;margin:18px 0}.transcript{font:17px/1.45 Georgia,serif}.context{padding:10px;border-left:3px solid #7d7468;background:#f4efe7}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.candidate{border:1px solid #d9d0c3;border-radius:8px;padding:12px;background:#fff}audio{width:100%}.ratings{display:grid;gap:8px}label{display:grid;gap:4px;font-size:13px;font-weight:650}select,textarea{font:inherit;padding:7px;border:1px solid #b9afa2;border-radius:5px}textarea{min-height:66px}.decision{display:flex;gap:12px;margin:8px 0}.decision label{display:flex;align-items:center;gap:5px}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}@media(max-width:760px){.grid{grid-template-columns:1fr}}</style></head><body><header><h1>Original Sin direct substitution repairs</h1><p>Each candidate is a production-format MP3 proxy. Reject clipped words, adjacent speech, music/effects, artifacts, or altered dialogue.</p><button id="export">Export review</button> <span id="progress"></span></header><main id="app"></main><script src="data.js"></script><script>(()=>{const d=window.ORIGINAL_SIN_DIRECT_REPAIR,k='os-direct:'+d.round_id;let s={};try{s=JSON.parse(localStorage.getItem(k)||'{}')}catch(_){s={}}const app=document.getElementById('app'),scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join('');for(const g of d.groups){const sec=document.createElement('section');sec.className='group';sec.innerHTML=`<h2>${g.character} · chunk ${g.chunk_id}</h2><p class="transcript">${g.transcript}</p>${g.review_context?`<p class="context">${g.review_context}</p>`:''}<div class="grid"></div>`;const grid=sec.querySelector('.grid');g.candidates.forEach((c,i)=>{const card=document.createElement('article');card.className='candidate';card.innerHTML=`<h3>Candidate ${i+1}</h3><audio controls preload="none" src="${c.audio}"></audio><div class="ratings"><label>Boundary completeness<select data-id="${c.id}" data-name="boundaries">${scale}</select></label><label>Isolation / contamination<select data-id="${c.id}" data-name="isolation">${scale}</select></label><label>Naturalness<select data-id="${c.id}" data-name="naturalness">${scale}</select></label><label>Replacement usefulness<select data-id="${c.id}" data-name="usefulness">${scale}</select></label></div><div class="decision"><label><input type="radio" name="d-${c.id}" value="pass" data-id="${c.id}" data-name="decision">Eligible</label><label><input type="radio" name="d-${c.id}" value="fail" data-id="${c.id}" data-name="decision">Reject</label></div><label>Notes<textarea data-id="${c.id}" data-name="notes"></textarea></label>`;grid.appendChild(card)});app.appendChild(sec)}for(const e of document.querySelectorAll('[data-id]')){const v=s[e.dataset.id]?.[e.dataset.name];if(e.type==='radio')e.checked=v===e.value;else if(v!=null)e.value=v;e.addEventListener(e.tagName==='TEXTAREA'?'input':'change',ev=>{const id=ev.target.dataset.id,n=ev.target.dataset.name;s[id]=s[id]||{};s[id][n]=ev.target.value;localStorage.setItem(k,JSON.stringify(s));progress()})}function progress(){const ids=d.groups.flatMap(g=>g.candidates.map(c=>c.id)),done=ids.filter(id=>s[id]?.decision).length;document.getElementById('progress').textContent=`${done} of ${ids.length} decided`}document.getElementById('export').onclick=()=>{const a=document.createElement('a'),b=new Blob([JSON.stringify({schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),results:s},null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();</script></body></html>'''


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
    if plan.get("round_id") != ROUND_ID or len(plan.get("groups") or []) != 5:
        raise RepairRoundError("direct repair plan mismatch")
    chunks = json.loads((project / "chunks.json").read_text(encoding="utf-8"))
    if not isinstance(chunks, list):
        raise RepairRoundError("chunks.json must contain a list")
    segments = read_json(project / "external_workflows" / "big_finish_overlap_reference_v1" / "private" / "transcript.json")["segments"]
    for spec in plan["groups"]:
        chunk = chunks[int(spec["chunk_id"])]
        if chunk.get("speaker") != spec["book_speaker"] or normalized_words(chunk.get("text")) != normalized_words(spec["expected_transcript"]):
            raise RepairRoundError(f"Direct repair chunk binding mismatch: {spec['chunk_id']}")
    output = args.output_root.expanduser().resolve() if args.output_root else project / "external_workflows" / "big_finish_overlap_reference_v1" / "direct_substitution_repair_round_v2"
    if output.exists():
        if not args.replace:
            raise RepairRoundError(f"Output exists; pass --replace: {output}")
        shutil.rmtree(output)
    before = project_hashes(project)
    whisper = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    vocal = MelRoFormer.from_pretrained(VOCAL_MODEL, config=MelRoFormerConfig.zfturbo_vocals_v1())
    vocal.eval()
    moss = load_mossformer()
    private_audio = output / "private" / "audio"
    groups = []
    omitted = []
    for spec in plan["groups"]:
        start_index = int(spec["segment_start"])
        end_index = int(spec["segment_end"])
        expected = str(spec["expected_transcript"])
        broad_padding = float(spec.get("broad_padding_seconds", plan["broad_padding_seconds"]))
        broad_start = max(0.0, float(segments[start_index]["start"]) - broad_padding)
        broad_end = float(segments[end_index]["end"]) + broad_padding
        maximum_end = float(segments[end_index + 1]["start"]) - float(spec["maximum_next_speaker_margin_seconds"])
        slug = f"chunk_{spec['chunk_id']}_{re_slug(spec['book_speaker'])}"
        source = private_audio / f"{slug}__source.wav"
        alignment = precise_source_cut(
            media=media,
            destination=source,
            broad_path=private_audio / f"{slug}__broad.wav",
            broad_start=broad_start,
            broad_end=broad_end,
            expected=expected,
            whisper_model=whisper,
            leading_margin=float(spec.get("leading_margin_seconds", plan["leading_margin_seconds"])),
            trailing_margin=float(spec.get("trailing_margin_seconds", plan["trailing_margin_seconds"])),
            maximum_source_end=maximum_end,
        )
        moss_path = private_audio / f"{slug}__moss.wav"
        mel_path = private_audio / f"{slug}__mel.wav"
        candidates = []
        for treatment in spec["treatments"]:
            if treatment == "mossformer2_source_mix":
                path = moss_path
                if not path.exists(): enhance(source, path, moss)
            elif treatment == "mel_roformer_vocal":
                path = mel_path
                if not path.exists(): separate(vocal, source, path)
            elif treatment == "center_channel_mid":
                path = private_audio / f"{slug}__center.wav"; write_center_channel(source, path)
            elif treatment == "mossformer2_blend70":
                if not moss_path.exists(): enhance(source, moss_path, moss)
                path = private_audio / f"{slug}__moss_blend70.wav"; write_blend(source, moss_path, path, 0.7)
            elif treatment == "mel_roformer_blend70":
                if not mel_path.exists(): separate(vocal, source, mel_path)
                path = private_audio / f"{slug}__mel_blend70.wav"; write_blend(source, mel_path, path, 0.7)
            else:
                raise RepairRoundError(f"Unsupported direct repair treatment: {treatment}")
            wav_check = accepted_transcript_check([expected], transcribe(path, whisper))
            proxy = private_audio / f"{slug}__{treatment}.mp3"
            encode_proxy(path, proxy, bitrate=plan["production_proxy"]["bitrate"])
            proxy_check = accepted_transcript_check([expected], transcribe(proxy, whisper))
            probe = probe_audio(proxy)
            objective = transcript_check_eligible(wav_check) and transcript_check_eligible(proxy_check) and probe["codec_name"] == "mp3" and probe["sample_rate"] == 44100 and probe["channels"] == 2
            candidates.append({
                "treatment": treatment,
                "wav_path": path,
                "wav_metrics": metrics(path),
                "wav_objective": wav_check,
                "proxy_path": proxy,
                "proxy_sha256": sha256_file(proxy),
                "proxy_probe": probe,
                "proxy_objective": proxy_check,
                "objective_eligible": objective,
                **treatment_provenance(treatment.replace("mel_roformer_blend70", "mel_roformer_vocal") if treatment == "mel_roformer_blend70" else treatment),
            })
        eligible = [candidate for candidate in candidates if candidate["objective_eligible"]]
        if not eligible:
            omitted.append({"chunk_id": spec["chunk_id"], "character": spec["character"], "reason": "no transcript-safe WAV/MP3 candidate"})
            print(f"omitted chunk {spec['chunk_id']} {spec['character']}", flush=True)
            continue
        groups.append({
            "character": spec["character"],
            "book_speaker": spec["book_speaker"],
            "chunk_id": int(spec["chunk_id"]),
            "transcript": expected,
            "review_context": spec.get("review_context"),
            "source": {"media_sha256": sha256_file(media), "segment_start": start_index, "segment_end": end_index, **alignment},
            "candidates": eligible,
        })
        print(f"built chunk {spec['chunk_id']} {spec['character']} ({len(eligible)} eligible)", flush=True)
    build_review(output, groups)
    after = project_hashes(project)
    if before != after:
        raise RepairRoundError("Protected project hashes changed")
    summary = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "generated_at": utc_now(),
        "planned_chunk_count": len(plan["groups"]),
        "review_chunk_count": len(groups),
        "omitted_chunks": omitted,
        "candidate_count": sum(len(group["candidates"]) for group in groups),
        "protected_project_hashes_before": before,
        "protected_project_hashes_after": after,
        "production_changes": False,
        "output_root": str(output),
    }
    write_json(output / "generation-summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
