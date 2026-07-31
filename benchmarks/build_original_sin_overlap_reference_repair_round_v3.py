#!/usr/bin/env python3
"""Build the third blind repair round for unresolved Original Sin identities."""
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
from benchmarks.original_sin_overlap_word_alignment import transcript_comparison


ROUND_ID = "alexandria_original_sin_overlap_reference_repair_v3"
SEED = 20260731
DEFAULT_MEDIA = Path("/Users/tristan/Library/Mobile Documents/3L68KQB4HG~com~readdle~CommonDocuments/Documents/DW7*(RERUN:BF)/7c_divergentTimeline3*(RERUN:BF)/20c_bennyChris&Roz/1_originalSinAdaptation.mp3")
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_overlap_reference_repair_plan_v3.json")


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
                f"{ROUND_ID}:{group['book_speaker']}:{candidate['treatment']}:{candidate['metrics']['sha256']}".encode()
            ).hexdigest()[:16]
            shutil.copy2(candidate["path"], audio / f"{blind_id}.wav")
            public.append({"id": blind_id, "audio": f"audio/{blind_id}.wav"})
            answer[blind_id] = {
                **candidate,
                "path": str(candidate["path"]),
                "character": group["character"],
                "book_speaker": group["book_speaker"],
                "transcript": group["transcript"],
                "source": group["source"],
            }
        public_groups.append({
            "character": group["character"],
            "book_speaker": group["book_speaker"],
            "transcript": group["transcript"],
            "review_context": group.get("review_context"),
            "candidates": public,
        })
    write_json(output / "private" / "answer-key.json", {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "generated_at": utc_now(),
        "candidates": answer,
        "production_changes": False,
    })
    (review / "data.js").write_text(
        "window.ORIGINAL_SIN_REPAIR_V3 = " + json.dumps({"schema_version": 1, "round_id": ROUND_ID, "groups": public_groups}, indent=2, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    (review / "index.html").write_text(REVIEW_HTML, encoding="utf-8")


REVIEW_HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Original Sin repair v3</title><link rel="icon" href="data:,"> <style>:root{font-family:Inter,system-ui,sans-serif;color:#28231f;background:#f2eee6}*{box-sizing:border-box}body{margin:0}header,main{max-width:1080px;margin:auto;padding:24px 20px}header{border-bottom:1px solid #d2c8b8}h1,h2{font-family:Georgia,serif}.group{background:#fffdf8;border:1px solid #d5ccbd;border-radius:10px;padding:18px;margin:18px 0}.transcript{font:17px/1.45 Georgia,serif}.context{padding:10px;border-left:3px solid #7d7468;background:#f4efe7}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.candidate{border:1px solid #d9d0c3;border-radius:8px;padding:12px;background:#fff}audio{width:100%}.ratings{display:grid;gap:8px}label{display:grid;gap:4px;font-size:13px;font-weight:650}select,textarea{font:inherit;padding:7px;border:1px solid #b9afa2;border-radius:5px}textarea{min-height:66px}.decision{display:flex;gap:12px;margin:8px 0}.decision label{display:flex;align-items:center;gap:5px}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}@media(max-width:780px){.grid{grid-template-columns:1fr}}</style></head><body><header><h1>Original Sin reference repair v3</h1><p>Reject clipped words, unrelated speech, music/effects, echo, or damaged identity. Character-correct computer, intercom, or robot coloration is not itself contamination.</p><button id="export">Export review</button> <span id="progress"></span></header><main id="app"></main><script src="data.js"></script><script>(()=>{const d=window.ORIGINAL_SIN_REPAIR_V3,k='os-repair:'+d.round_id;let s={};try{s=JSON.parse(localStorage.getItem(k)||'{}')}catch(_){s={}}const app=document.getElementById('app'),scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join('');for(const g of d.groups){const sec=document.createElement('section');sec.className='group';sec.innerHTML=`<h2>${g.character}</h2><p class="transcript">${g.transcript}</p>${g.review_context?`<p class="context">${g.review_context}</p>`:''}<div class="grid"></div>`;const grid=sec.querySelector('.grid');g.candidates.forEach((c,i)=>{const card=document.createElement('article');card.className='candidate';card.innerHTML=`<h3>Candidate ${i+1}</h3><audio controls preload="none" src="${c.audio}"></audio><div class="ratings"><label>Voice isolation<select data-id="${c.id}" data-name="isolation">${scale}</select></label><label>Naturalness<select data-id="${c.id}" data-name="naturalness">${scale}</select></label><label>Identity clarity<select data-id="${c.id}" data-name="identity">${scale}</select></label><label>Reference usefulness<select data-id="${c.id}" data-name="usefulness">${scale}</select></label></div><div class="decision"><label><input type="radio" name="d-${c.id}" value="pass" data-id="${c.id}" data-name="decision">Pass</label><label><input type="radio" name="d-${c.id}" value="fail" data-id="${c.id}" data-name="decision">Fail</label></div><label>Notes<textarea data-id="${c.id}" data-name="notes"></textarea></label>`;grid.appendChild(card)});app.appendChild(sec)}for(const e of document.querySelectorAll('[data-id]')){const v=s[e.dataset.id]?.[e.dataset.name];if(e.type==='radio')e.checked=v===e.value;else if(v!=null)e.value=v;e.addEventListener(e.tagName==='TEXTAREA'?'input':'change',ev=>{const id=ev.target.dataset.id,n=ev.target.dataset.name;s[id]=s[id]||{};s[id][n]=ev.target.value;localStorage.setItem(k,JSON.stringify(s));progress()})}function progress(){const ids=d.groups.flatMap(g=>g.candidates.map(c=>c.id)),done=ids.filter(id=>s[id]?.decision).length;document.getElementById('progress').textContent=`${done} of ${ids.length} decided`}document.getElementById('export').onclick=()=>{const a=document.createElement('a'),b=new Blob([JSON.stringify({schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),results:s},null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();</script></body></html>'''


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
    if plan.get("round_id") != ROUND_ID or len(plan.get("groups") or []) != 11:
        raise RepairRoundError("v3 plan mismatch")
    if sum(len(group["treatments"]) for group in plan["groups"]) != plan["candidate_count"]:
        raise RepairRoundError("v3 candidate count mismatch")
    output = args.output_root.expanduser().resolve() if args.output_root else project / "external_workflows" / "big_finish_overlap_reference_v1" / "reference_repair_round_v3"
    if output.exists():
        if not args.replace:
            raise RepairRoundError(f"Output exists; pass --replace: {output}")
        shutil.rmtree(output)
    before = project_hashes(project)
    transcript = read_json(project / "external_workflows" / "big_finish_overlap_reference_v1" / "private" / "transcript.json")
    segments = transcript["segments"]
    whisper = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    treatments = {t for group in plan["groups"] for t in group["treatments"]}
    vocal = MelRoFormer.from_pretrained(VOCAL_MODEL, config=MelRoFormerConfig.zfturbo_vocals_v1()) if any(t.startswith("mel_roformer") for t in treatments) else None
    if vocal is not None:
        vocal.eval()
    moss = load_mossformer() if any(t.startswith("mossformer2") for t in treatments) else None
    private_audio = output / "private" / "audio"
    groups = []
    omitted_groups = []
    for spec in plan["groups"]:
        start_index = int(spec["segment_start"])
        end_index = int(spec["segment_end"])
        expected = str(spec["expected_transcript"])
        broad_padding = float(spec.get("broad_padding_seconds", plan["broad_padding_seconds"]))
        leading = float(spec.get("leading_margin_seconds", plan["leading_margin_seconds"]))
        trailing = float(spec.get("trailing_margin_seconds", plan["trailing_margin_seconds"]))
        broad_start = max(0.0, float(segments[start_index]["start"]) - broad_padding)
        broad_end = float(segments[end_index]["end"]) + broad_padding
        maximum_end = None
        if end_index + 1 < len(segments) and "maximum_next_speaker_margin_seconds" in spec:
            maximum_end = float(segments[end_index + 1]["start"]) - float(spec["maximum_next_speaker_margin_seconds"])
        slug = re_slug(str(spec["book_speaker"]))
        source = private_audio / f"{slug}__source.wav"
        broad = private_audio / f"{slug}__broad.wav"
        aliases = {str(k): [str(v) for v in values] for k, values in spec.get("alignment_word_aliases", {}).items()}
        recognizer_variants = [str(value) for value in spec.get("recognizer_transcript_variants", [])]
        alignment = precise_source_cut(
            media=media,
            destination=source,
            broad_path=broad,
            broad_start=broad_start,
            broad_end=broad_end,
            expected=expected,
            whisper_model=whisper,
            leading_margin=leading,
            trailing_margin=trailing,
            accepted_transcripts=recognizer_variants,
            word_aliases=aliases,
            maximum_source_end=maximum_end,
        )
        moss_path = private_audio / f"{slug}__moss.wav"
        mel_path = private_audio / f"{slug}__mel.wav"
        candidates = []
        for treatment in spec["treatments"]:
            if treatment == "source_mix":
                path = source
            elif treatment == "mel_roformer_vocal":
                path = mel_path
                if not path.exists():
                    separate(vocal, source, path)
            elif treatment == "mossformer2_source_mix":
                path = moss_path
                if not path.exists():
                    enhance(source, path, moss)
            elif treatment == "center_channel_mid":
                path = private_audio / f"{slug}__center.wav"
                write_center_channel(source, path)
            elif treatment == "mossformer2_blend70":
                if not moss_path.exists():
                    enhance(source, moss_path, moss)
                path = private_audio / f"{slug}__moss_blend70.wav"
                write_blend(source, moss_path, path, 0.7)
            elif treatment == "mel_roformer_blend70":
                if not mel_path.exists():
                    separate(vocal, source, mel_path)
                path = private_audio / f"{slug}__mel_blend70.wav"
                write_blend(source, mel_path, path, 0.7)
            else:
                raise RepairRoundError(f"Unsupported v3 treatment: {treatment}")
            observed = transcribe(path, whisper)
            comparison = transcript_comparison([expected, *recognizer_variants], observed, aliases)
            candidates.append({
                "treatment": treatment,
                "path": path,
                "metrics": metrics(path),
                "automatic_transcript": observed,
                **comparison,
                **treatment_provenance(treatment.replace("mel_roformer_blend70", "mel_roformer_vocal") if treatment == "mel_roformer_blend70" else treatment),
            })
        eligible = [c for c in candidates if c["word_error_rate"] == 0.0 and c["first_word_present"] and c["last_word_present"]]
        if not eligible:
            omitted_groups.append({
                "character": spec["character"],
                "book_speaker": spec["book_speaker"],
                "reason": "no objective-eligible candidate after bounded v3 salvage",
                "candidate_transcripts": [
                    {
                        "treatment": candidate["treatment"],
                        "automatic_transcript": candidate["automatic_transcript"],
                        "word_error_rate": candidate["word_error_rate"],
                        "first_word_present": candidate["first_word_present"],
                        "last_word_present": candidate["last_word_present"],
                    }
                    for candidate in candidates
                ],
            })
            print(f"omitted {spec['character']} (no objective-eligible candidate)", flush=True)
            continue
        groups.append({
            "character": spec["character"],
            "book_speaker": spec["book_speaker"],
            "transcript": expected,
            "review_context": spec.get("review_context"),
            "source": {"media_sha256": sha256_file(media), "segment_start": start_index, "segment_end": end_index, **alignment},
            "candidates": eligible,
        })
        print(f"built {spec['character']} ({len(eligible)} eligible)", flush=True)
    build_review(output, groups)
    after = project_hashes(project)
    if before != after:
        raise RepairRoundError("Protected project hashes changed")
    summary = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "generated_at": utc_now(),
        "planned_character_count": len(plan["groups"]),
        "review_character_count": len(groups),
        "omitted_character_count": len(omitted_groups),
        "omitted_groups": omitted_groups,
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
