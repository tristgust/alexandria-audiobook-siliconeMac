#!/usr/bin/env python3
"""Build a bounded exact-line substitution pilot from the Original Sin adaptation."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import subprocess
from pathlib import Path

from mlx_audio.sts.models.mel_roformer import MelRoFormer, MelRoFormerConfig
from model_registry import resolve_model_path

try:
    from benchmarks.build_original_sin_overlap_reference_round import (
        VOCAL_MODEL,
        WHISPER_MODEL_KEY,
        cut,
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
        MOSSFORMER_REVISION,
        precise_source_cut,
        project_hashes,
        re_slug,
        treatment_provenance,
    )
    from benchmarks.original_sin_overlap_word_alignment import (
        accepted_transcript_check,
        normalized_words,
        transcript_check_eligible,
    )
except ModuleNotFoundError:
    from build_original_sin_overlap_reference_round import (
        VOCAL_MODEL,
        WHISPER_MODEL_KEY,
        cut,
        enhance,
        load_mossformer,
        metrics,
        separate,
        sha256_file,
        transcribe,
        utc_now,
        write_json,
    )
    from build_original_sin_overlap_reference_repair_round import (
        MOSSFORMER_REVISION,
        precise_source_cut,
        project_hashes,
        re_slug,
        treatment_provenance,
    )
    from original_sin_overlap_word_alignment import (
        accepted_transcript_check,
        normalized_words,
        transcript_check_eligible,
    )


ROUND_ID = "alexandria_original_sin_direct_substitution_pilot_v1"
SEED = 20260731
DEFAULT_MEDIA = Path("/Users/tristan/Library/Mobile Documents/3L68KQB4HG~com~readdle~CommonDocuments/Documents/DW7*(RERUN:BF)/7c_divergentTimeline3*(RERUN:BF)/20c_bennyChris&Roz/1_originalSinAdaptation.mp3")
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_direct_substitution_plan.json")


class DirectSubstitutionError(RuntimeError):
    pass


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def encode_proxy(source: Path, destination: Path, *, bitrate: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-ar", "44100", "-ac", "2", "-c:a", "libmp3lame",
            "-b:a", bitrate, str(destination),
        ],
        check=True,
    )


def probe_audio(path: Path) -> dict:
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,sample_rate,channels,duration,bit_rate",
            "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(completed.stdout).get("streams") or []
    if len(streams) != 1:
        raise DirectSubstitutionError(f"Expected one audio stream: {path}")
    stream = streams[0]
    return {
        "codec_name": str(stream.get("codec_name") or ""),
        "sample_rate": int(stream.get("sample_rate") or 0),
        "channels": int(stream.get("channels") or 0),
        "duration_seconds": float(stream.get("duration") or 0.0),
        "bit_rate": int(stream.get("bit_rate") or 0),
    }


def validate_plan(plan: dict, chunks: list[dict], segments: list[dict]) -> None:
    if plan.get("round_id") != ROUND_ID:
        raise DirectSubstitutionError("Plan round_id mismatch")
    groups = plan.get("groups")
    if not isinstance(groups, list) or len(groups) != 6:
        raise DirectSubstitutionError("Direct substitution pilot must contain six groups")
    if sum(len(group.get("treatments") or []) for group in groups) != plan.get("candidate_count"):
        raise DirectSubstitutionError("Plan candidate_count mismatch")
    for group in groups:
        chunk_id = int(group["chunk_id"])
        if chunk_id < 0 or chunk_id >= len(chunks):
            raise DirectSubstitutionError(f"Invalid chunk id: {chunk_id}")
        chunk = chunks[chunk_id]
        if int(chunk.get("id", -1)) != chunk_id:
            raise DirectSubstitutionError(f"Chunk index/id mismatch: {chunk_id}")
        if chunk.get("speaker") != group["book_speaker"]:
            raise DirectSubstitutionError(f"Chunk speaker mismatch: {chunk_id}")
        book_transcript = str(group.get("book_transcript", group["expected_transcript"]))
        if normalized_words(chunk.get("text")) != normalized_words(book_transcript):
            raise DirectSubstitutionError(f"Chunk transcript mismatch: {chunk_id}")
        start = int(group["segment_start"])
        end = int(group["segment_end"])
        transcript = " ".join(
            str(segments[index].get("text") or "").strip()
            for index in range(start, end + 1)
        ).strip()
        accepted = group.get("accepted_adaptation_transcripts") or [group["expected_transcript"]]
        if not isinstance(accepted, list) or not all(isinstance(value, str) and value.strip() for value in accepted):
            raise DirectSubstitutionError(f"Invalid accepted adaptation transcripts: {chunk_id}")
        if tuple(normalized_words(transcript)) not in {
            tuple(normalized_words(value)) for value in accepted
        }:
            raise DirectSubstitutionError(f"Adaptation transcript mismatch: {chunk_id}")


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
        public_candidates = []
        for candidate in candidates:
            blind_id = hashlib.sha256(
                f"{ROUND_ID}:{group['chunk_id']}:{candidate['treatment']}:{candidate['proxy_sha256']}".encode()
            ).hexdigest()[:16]
            shutil.copy2(candidate["proxy_path"], audio / f"{blind_id}.mp3")
            public_candidates.append({"id": blind_id, "audio": f"audio/{blind_id}.mp3"})
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
        public_groups.append(
            {
                "character": group["character"],
                "chunk_id": group["chunk_id"],
                "transcript": group["transcript"],
                "review_context": group.get("review_context"),
                "candidates": public_candidates,
            }
        )
    write_json(
        output / "private" / "answer-key.json",
        {"schema_version": 1, "round_id": ROUND_ID, "candidates": answer, "production_changes": False},
    )
    (review / "data.js").write_text(
        "window.ORIGINAL_SIN_DIRECT_SUBSTITUTION = "
        + json.dumps({"schema_version": 1, "round_id": ROUND_ID, "groups": public_groups}, indent=2, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )
    (review / "index.html").write_text(REVIEW_HTML, encoding="utf-8")


REVIEW_HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Original Sin direct substitution pilot</title><link rel="icon" href="data:,"> <style>:root{font-family:Inter,system-ui,sans-serif;color:#28231f;background:#f2eee6}*{box-sizing:border-box}body{margin:0}header,main{max-width:1000px;margin:auto;padding:24px 20px}header{border-bottom:1px solid #d2c8b8}h1,h2{font-family:Georgia,serif}.group{background:#fffdf8;border:1px solid #d5ccbd;border-radius:10px;padding:18px;margin:18px 0}.transcript{font:17px/1.45 Georgia,serif}.context{padding:10px;border-left:3px solid #7d7468;background:#f4efe7}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.candidate{border:1px solid #d9d0c3;border-radius:8px;padding:12px;background:#fff}audio{width:100%}.ratings{display:grid;gap:8px}label{display:grid;gap:4px;font-size:13px;font-weight:650}select,textarea{font:inherit;padding:7px;border:1px solid #b9afa2;border-radius:5px}textarea{min-height:66px}.decision{display:flex;gap:12px;margin:8px 0}.decision label{display:flex;align-items:center;gap:5px}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}@media(max-width:720px){.grid{grid-template-columns:1fr}}</style></head><body><header><h1>Original Sin direct substitution pilot</h1><p>Each hidden candidate is the complete production-format MP3 proxy for one exact book chunk. Reject clipped boundaries, adjacent speech, music/effects, artifacts, or any version that should not replace the synthesized chunk.</p><button id="export">Export review</button> <span id="progress"></span></header><main id="app"></main><script src="data.js"></script><script>(()=>{const d=window.ORIGINAL_SIN_DIRECT_SUBSTITUTION,k='os-direct:'+d.round_id;let s={};try{s=JSON.parse(localStorage.getItem(k)||'{}')}catch(_){s={}}const app=document.getElementById('app'),scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join('');for(const g of d.groups){const sec=document.createElement('section');sec.className='group';sec.innerHTML=`<h2>${g.character} · chunk ${g.chunk_id}</h2><p class="transcript">${g.transcript}</p>${g.review_context?`<p class="context">${g.review_context}</p>`:''}<div class="grid"></div>`;const grid=sec.querySelector('.grid');g.candidates.forEach((c,i)=>{const card=document.createElement('article');card.className='candidate';card.innerHTML=`<h3>Candidate ${i+1}</h3><audio controls preload="none" src="${c.audio}"></audio><div class="ratings"><label>Boundary completeness<select data-id="${c.id}" data-name="boundaries">${scale}</select></label><label>Isolation / contamination<select data-id="${c.id}" data-name="isolation">${scale}</select></label><label>Naturalness<select data-id="${c.id}" data-name="naturalness">${scale}</select></label><label>Replacement usefulness<select data-id="${c.id}" data-name="usefulness">${scale}</select></label></div><div class="decision"><label><input type="radio" name="d-${c.id}" value="pass" data-id="${c.id}" data-name="decision">Eligible</label><label><input type="radio" name="d-${c.id}" value="fail" data-id="${c.id}" data-name="decision">Reject</label></div><label>Notes<textarea data-id="${c.id}" data-name="notes"></textarea></label>`;grid.appendChild(card)});app.appendChild(sec)}for(const e of document.querySelectorAll('[data-id]')){const v=s[e.dataset.id]?.[e.dataset.name];if(e.type==='radio')e.checked=v===e.value;else if(v!=null)e.value=v;e.addEventListener(e.tagName==='TEXTAREA'?'input':'change',ev=>{const id=ev.target.dataset.id,n=ev.target.dataset.name;s[id]=s[id]||{};s[id][n]=ev.target.value;localStorage.setItem(k,JSON.stringify(s));progress()})}function progress(){const ids=d.groups.flatMap(g=>g.candidates.map(c=>c.id)),done=ids.filter(id=>s[id]?.decision).length;document.getElementById('progress').textContent=`${done} of ${ids.length} decided`}document.getElementById('export').onclick=()=>{const a=document.createElement('a'),b=new Blob([JSON.stringify({schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),results:s},null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();</script></body></html>'''


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
    chunks = read_json(project / "chunks.json")
    segments = read_json(
        project / "external_workflows" / "big_finish_overlap_reference_v1" / "private" / "transcript.json"
    )["segments"]
    validate_plan(plan, chunks, segments)
    output = (
        args.output_root.expanduser().resolve()
        if args.output_root
        else project / "external_workflows" / "big_finish_overlap_reference_v1" / "direct_substitution_round_v1"
    )
    if output.exists():
        if not args.replace:
            raise DirectSubstitutionError(f"Output exists; pass --replace: {output}")
        shutil.rmtree(output)
    before_hashes = project_hashes(project)
    whisper_model = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    treatments = {t for group in plan["groups"] for t in group["treatments"]}
    vocal_model = None
    if "mel_roformer_vocal" in treatments:
        vocal_model = MelRoFormer.from_pretrained(VOCAL_MODEL, config=MelRoFormerConfig.zfturbo_vocals_v1())
        vocal_model.eval()
    mossformer = load_mossformer() if "mossformer2_source_mix" in treatments else None
    private_audio = output / "private" / "audio"
    groups = []
    for group_plan in plan["groups"]:
        start_index = int(group_plan["segment_start"])
        end_index = int(group_plan["segment_end"])
        segment_start = float(segments[start_index]["start"])
        segment_end = float(segments[end_index]["end"])
        broad_padding = float(group_plan.get("broad_padding_seconds", plan["broad_padding_seconds"]))
        boundary = float(
            group_plan.get(
                "maximum_boundary_margin_seconds",
                plan["maximum_boundary_margin_seconds"],
            )
        )
        leading_margin = float(
            group_plan.get("leading_margin_seconds", plan["leading_margin_seconds"])
        )
        trailing_margin = float(
            group_plan.get("trailing_margin_seconds", plan["trailing_margin_seconds"])
        )
        broad_start = max(0.0, segment_start - broad_padding)
        broad_end = segment_end + broad_padding
        slug = f"chunk_{group_plan['chunk_id']}_{re_slug(group_plan['book_speaker'])}"
        broad = private_audio / f"{slug}__broad.wav"
        source = private_audio / f"{slug}__source.wav"
        accepted_transcripts = list(
            group_plan.get("accepted_adaptation_transcripts")
            or [group_plan["expected_transcript"]]
        )
        alignment_transcript = str(
            group_plan.get("alignment_transcript", group_plan["expected_transcript"])
        )
        alignment = precise_source_cut(
            media=media,
            destination=source,
            broad_path=broad,
            broad_start=broad_start,
            broad_end=broad_end,
            expected=alignment_transcript,
            whisper_model=whisper_model,
            leading_margin=leading_margin,
            trailing_margin=trailing_margin,
            minimum_source_start=segment_start - boundary,
            maximum_source_end=segment_end + boundary,
        )
        candidates = []
        for treatment in group_plan["treatments"]:
            if treatment == "source_mix":
                wav = source
            elif treatment == "mossformer2_source_mix":
                if mossformer is None:
                    raise DirectSubstitutionError("MossFormer2 was not loaded")
                wav = private_audio / f"{slug}__mossformer2.wav"
                enhance(source, wav, mossformer)
            elif treatment == "mel_roformer_vocal":
                if vocal_model is None:
                    raise DirectSubstitutionError("Mel-RoFormer was not loaded")
                wav = private_audio / f"{slug}__mel_vocal.wav"
                separate(vocal_model, source, wav)
            else:
                raise DirectSubstitutionError(f"Unsupported treatment: {treatment}")
            wav_check = accepted_transcript_check(
                accepted_transcripts,
                transcribe(wav, whisper_model),
            )
            proxy = private_audio / f"{slug}__{treatment}.mp3"
            encode_proxy(wav, proxy, bitrate=plan["production_proxy"]["bitrate"])
            proxy_check = accepted_transcript_check(
                accepted_transcripts,
                transcribe(proxy, whisper_model),
            )
            proxy_probe = probe_audio(proxy)
            objective = transcript_check_eligible(wav_check) and transcript_check_eligible(proxy_check)
            if proxy_probe["codec_name"] != "mp3" or proxy_probe["sample_rate"] != 44100 or proxy_probe["channels"] != 2:
                objective = False
            candidates.append(
                {
                    "treatment": treatment,
                    "wav_path": wav,
                    "wav_metrics": metrics(wav),
                    "wav_objective": wav_check,
                    "proxy_path": proxy,
                    "proxy_sha256": sha256_file(proxy),
                    "proxy_probe": proxy_probe,
                    "proxy_objective": proxy_check,
                    "objective_eligible": objective,
                    **treatment_provenance(treatment),
                }
            )
        if not all(candidate["objective_eligible"] for candidate in candidates):
            failed = [candidate["treatment"] for candidate in candidates if not candidate["objective_eligible"]]
            raise DirectSubstitutionError(
                f"Objective failure in direct substitution group {group_plan['chunk_id']}: {failed}"
            )
        groups.append(
            {
                "character": group_plan["character"],
                "book_speaker": group_plan["book_speaker"],
                "chunk_id": int(group_plan["chunk_id"]),
                "transcript": group_plan["expected_transcript"],
                "book_transcript": group_plan.get(
                    "book_transcript",
                    group_plan["expected_transcript"],
                ),
                "accepted_adaptation_transcripts": accepted_transcripts,
                "review_context": group_plan.get("review_context"),
                "source": {
                    "media_sha256": sha256_file(media),
                    "segment_start": start_index,
                    "segment_end": end_index,
                    "alignment_transcript": alignment_transcript,
                    **alignment,
                },
                "candidates": candidates,
            }
        )
        print(f"built chunk {group_plan['chunk_id']} {group_plan['character']}", flush=True)
    build_review(output, groups)
    after_hashes = project_hashes(project)
    if before_hashes != after_hashes:
        raise DirectSubstitutionError("Protected project hashes changed")
    summary = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "generated_at": utc_now(),
        "group_count": len(groups),
        "candidate_count": sum(len(group["candidates"]) for group in groups),
        "source_media_sha256": sha256_file(media),
        "protected_project_hashes_before": before_hashes,
        "protected_project_hashes_after": after_hashes,
        "all_candidates_objective_eligible": True,
        "production_changes": False,
        "output_root": str(output),
    }
    write_json(output / "generation-summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
