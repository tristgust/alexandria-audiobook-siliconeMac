#!/usr/bin/env python3
"""Build strict direct-overlap expansion batch 002 with corrected timing."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from mlx_audio.sts.models.mel_roformer import MelRoFormer, MelRoFormerConfig
from model_registry import resolve_model_path

from benchmarks.build_original_sin_direct_overlap_expansion_batch_001 import edge_metrics
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
from benchmarks.original_sin_direct_overlap_timing import (
    append_deterministic_silence,
    safe_segment_bounds,
)
from benchmarks.original_sin_overlap_word_alignment import (
    transcript_comparison,
    transcript_check_eligible,
)


ROUND_ID = "alexandria_original_sin_direct_overlap_expansion_batch_002"
SEED = 20260731
DEFAULT_MEDIA = Path(
    "/Users/tristan/Library/Mobile Documents/3L68KQB4HG~com~readdle~"
    "CommonDocuments/Documents/DW7*(RERUN:BF)/7c_divergentTimeline3*"
    "(RERUN:BF)/20c_bennyChris&Roz/1_originalSinAdaptation.mp3"
)
DEFAULT_PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
DEFAULT_PLAN = Path(__file__).with_name(
    "original_sin_direct_overlap_expansion_batch_002_plan.json"
)


class ExpansionBatch002Error(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_review(output: Path, groups: list[dict[str, Any]]) -> None:
    review = output / "review"
    audio_root = review / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    answer: dict[str, Any] = {}
    public_groups: list[dict[str, Any]] = []
    for group in groups:
        candidates = list(group["candidates"])
        rng.shuffle(candidates)
        public_candidates = []
        for candidate in candidates:
            blind_id = hashlib.sha256(
                (
                    f"{ROUND_ID}:{group['chunk_id']}:{candidate['treatment']}:"
                    f"{candidate['proxy_sha256']}"
                ).encode()
            ).hexdigest()[:16]
            shutil.copy2(candidate["proxy_path"], audio_root / f"{blind_id}.mp3")
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
                "character_correct_effect_allowance": group.get(
                    "character_correct_effect_allowance"
                ),
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
        output / "private/answer-key.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "generated_at": utc_now(),
            "candidates": answer,
            "production_changes": False,
        },
    )
    (review / "data.js").write_text(
        "window.ORIGINAL_SIN_DIRECT_EXPANSION_002 = "
        + json.dumps(
            {"schema_version": 1, "round_id": ROUND_ID, "groups": public_groups},
            indent=2,
            ensure_ascii=False,
        )
        + ";\n",
        encoding="utf-8",
    )
    (review / "index.html").write_text(REVIEW_HTML, encoding="utf-8")


REVIEW_HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Original Sin direct overlap batch 2</title><link rel="icon" href="data:,"><style>:root{font-family:Inter,system-ui,sans-serif;color:#28231f;background:#f2eee6}*{box-sizing:border-box}body{margin:0}header,main{max-width:1120px;margin:auto;padding:24px 20px}header{border-bottom:1px solid #d2c8b8}h1,h2{font-family:Georgia,serif}.group{background:#fffdf8;border:1px solid #d5ccbd;border-radius:10px;padding:18px;margin:18px 0}.line{font:17px/1.45 Georgia,serif}.context{padding:10px;border-left:3px solid #7d7468;background:#f4efe7}.warning{padding:10px;border-left:3px solid #8a4438;background:#f7ece7}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.candidate{border:1px solid #d9d0c3;border-radius:8px;padding:12px;background:#fff}audio{width:100%}label{display:grid;gap:4px;font-size:13px;font-weight:650;margin-top:7px}select,textarea{font:inherit;padding:7px;border:1px solid #b9afa2;border-radius:5px}textarea{min-height:68px}.decision{display:flex;gap:12px;margin:8px 0}.decision label{display:flex;align-items:center;gap:5px}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}@media(max-width:760px){.grid{grid-template-columns:1fr}}</style></head><body><header><h1>Original Sin direct-overlap expansion · batch 2</h1><p class="warning"><strong>Strict contract:</strong> reject incomplete words, adjacent speech, music, unrelated effects, room spill, echo, or extraction artifacts. The Under-Sergeant card alone permits character-correct intercom coloration.</p><button id="export">Export review</button> <span id="progress"></span></header><main id="app"></main><script src="data.js"></script><script>(()=>{'use strict';const d=window.ORIGINAL_SIN_DIRECT_EXPANSION_002,k='os-direct-expansion:'+d.round_id;let s={};try{s=JSON.parse(localStorage.getItem(k)||'{}')}catch(_){s={}}const app=document.querySelector('#app'),scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join('');for(const g of d.groups){const sec=document.createElement('section');sec.className='group';sec.innerHTML=`<h2>${g.character} · chunk ${g.chunk_id}</h2><p class="line">${g.transcript}</p>${g.review_context?`<p class="context">${g.review_context}</p>`:''}<div class="grid"></div>`;for(const [i,c] of g.candidates.entries()){const card=document.createElement('article');card.className='candidate';card.innerHTML=`<h3>Candidate ${i+1}</h3><audio controls preload="none" src="${c.audio}"></audio>${[['boundaries','Complete boundaries'],['isolation','Only intended voice'],['music_effects','No prohibited music/effects'],['artifacts','No extraction artifacts'],['naturalness','Natural performance'],['usefulness','Safe chunk replacement']].map(([n,l])=>`<label>${l}<select data-id="${c.id}" data-name="${n}">${scale}</select></label>`).join('')}<div class="decision"><label><input type="radio" name="d-${c.id}" value="pass" data-id="${c.id}" data-name="decision">Eligible</label><label><input type="radio" name="d-${c.id}" value="fail" data-id="${c.id}" data-name="decision">Reject</label></div><label>Notes<textarea data-id="${c.id}" data-name="notes"></textarea></label>`;sec.querySelector('.grid').appendChild(card)}app.appendChild(sec)}for(const e of document.querySelectorAll('[data-id]')){const v=s[e.dataset.id]?.[e.dataset.name];if(e.type==='radio')e.checked=v===e.value;else if(v!=null)e.value=v;e.addEventListener(e.tagName==='TEXTAREA'?'input':'change',ev=>{const id=ev.target.dataset.id,n=ev.target.dataset.name;s[id]=s[id]||{};s[id][n]=ev.target.value;localStorage.setItem(k,JSON.stringify(s));progress()})}function progress(){const ids=d.groups.flatMap(g=>g.candidates.map(c=>c.id));document.querySelector('#progress').textContent=`${ids.filter(id=>s[id]?.decision).length} of ${ids.length} decided`}document.querySelector('#export').onclick=()=>{const a=document.createElement('a'),b=new Blob([JSON.stringify({schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),results:s},null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();</script></body></html>'''


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
    if plan.get("round_id") != ROUND_ID:
        raise ExpansionBatch002Error("Batch-002 plan round mismatch.")
    ledger = read_json(ROOT / str(plan["ledger"]))
    rows = {int(row["chunk_id"]): row for row in ledger["rows"]}
    selected = [int(value) for value in plan["selected_chunk_ids"]]
    expected_unique_chunk_count = int(plan.get("expected_unique_chunk_count", 18))
    if (
        len(selected) != expected_unique_chunk_count
        or len(selected) != len(set(selected))
    ):
        raise ExpansionBatch002Error(
            "Batch plan must contain "
            f"{expected_unique_chunk_count} unique chunks."
        )
    for chunk_id in selected:
        row = rows.get(chunk_id)
        if not row or not row.get("selected_window"):
            raise ExpansionBatch002Error(f"Unbound chunk: {chunk_id}")
        if row.get("previously_direct_reviewed") or row.get("already_blind_approved"):
            raise ExpansionBatch002Error(f"Reviewed/approved chunk recycled: {chunk_id}")
    allowances = {
        int(key): value
        for key, value in plan.get("character_correct_effect_allowances", {}).items()
    }
    if allowances.get(2002, {}).get("kind") != "character_correct_intercom":
        raise ExpansionBatch002Error("Under-Sergeant intercom allowance is missing.")
    alignment_overrides = {
        int(key): value
        for key, value in plan.get("line_alignment_overrides", {}).items()
    }

    output = (
        args.output_root.expanduser().resolve()
        if args.output_root
        else project
        / "external_workflows/big_finish_overlap_reference_v1/"
        "direct_overlap_expansion_batch_002"
    )
    if output.exists():
        if not args.replace:
            raise ExpansionBatch002Error(f"Output exists: {output}")
        shutil.rmtree(output)

    before = project_hashes(project)
    segments = read_json(
        project
        / "external_workflows/big_finish_overlap_reference_v1/private/transcript.json"
    )["segments"]
    whisper = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    vocal = MelRoFormer.from_pretrained(
        VOCAL_MODEL, config=MelRoFormerConfig.zfturbo_vocals_v1()
    )
    vocal.eval()
    moss = load_mossformer()
    private = output / "private/audio"
    groups: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []

    for chunk_id in selected:
        spec = rows[chunk_id]
        window = spec["selected_window"]
        start_index = int(window["segment_start"])
        end_index = int(window["segment_end"])
        timing = safe_segment_bounds(
            segments=segments,
            segment_start=start_index,
            segment_end=end_index,
            adjacent_guard_seconds=float(plan["adjacent_speaker_guard_seconds"]),
            requested_segment_tail_seconds=float(plan["requested_segment_tail_seconds"]),
        )
        slug = f"chunk_{chunk_id}_{re_slug(spec['speaker'])}"
        source = private / f"{slug}__source.wav"
        override = alignment_overrides.get(chunk_id, {})
        accepted_transcripts = list(override.get("accepted_transcripts", []))
        word_aliases = dict(override.get("word_aliases", {}))
        transcript_options = [spec["text"], *accepted_transcripts]
        try:
            alignment = precise_source_cut(
                media=media,
                destination=source,
                broad_path=private / f"{slug}__broad.wav",
                broad_start=max(
                    0.0,
                    timing["transcript_segment_start_seconds"]
                    - float(plan["broad_padding_seconds"]),
                ),
                broad_end=(
                    timing["transcript_segment_end_seconds"]
                    + float(plan["broad_padding_seconds"])
                ),
                expected=spec["text"],
                whisper_model=whisper,
                leading_margin=float(plan["leading_margin_seconds"]),
                trailing_margin=float(plan["trailing_margin_seconds"]),
                accepted_transcripts=accepted_transcripts,
                word_aliases=word_aliases,
                minimum_source_start=timing["minimum_source_start_seconds"],
                minimum_source_end=timing["minimum_source_end_seconds"],
                maximum_source_end=timing["maximum_source_end_seconds"],
            )
        except Exception as exc:
            omissions.append(
                {"chunk_id": chunk_id, "stage": "source_cut", "error": str(exc)[:2000]}
            )
            continue

        candidates: list[dict[str, Any]] = []
        for treatment in plan["treatments"]:
            try:
                if treatment == "mel_roformer_vocal":
                    wav = private / f"{slug}__mel.wav"
                    separate(vocal, source, wav)
                elif treatment == "mossformer2_source_mix":
                    wav = private / f"{slug}__moss.wav"
                    enhance(source, wav, moss)
                else:
                    raise ExpansionBatch002Error(treatment)
                silence = append_deterministic_silence(
                    wav, int(plan["appended_silence_milliseconds"])
                )
                wav_check = transcript_comparison(
                    transcript_options,
                    transcribe(wav, whisper),
                    word_aliases,
                )
                proxy = private / f"{slug}__{treatment}.mp3"
                encode_proxy(wav, proxy, bitrate=plan["production_proxy"]["bitrate"])
                proxy_check = transcript_comparison(
                    transcript_options,
                    transcribe(proxy, whisper),
                    word_aliases,
                )
                probe = probe_audio(proxy)
                if not (
                    transcript_check_eligible(wav_check)
                    and transcript_check_eligible(proxy_check)
                    and probe["codec_name"] == "mp3"
                    and probe["sample_rate"] == 44100
                    and probe["channels"] == 2
                ):
                    raise ExpansionBatch002Error("Objective transcript/proxy gate failed.")
                candidates.append(
                    {
                        "treatment": treatment,
                        "wav_path": wav,
                        "wav_metrics": {**metrics(wav), **edge_metrics(wav)},
                        "wav_objective": wav_check,
                        "proxy_path": proxy,
                        "proxy_sha256": sha256_file(proxy),
                        "proxy_probe": probe,
                        "proxy_objective": proxy_check,
                        "objective_eligible": True,
                        "timing_contract": {**timing, **silence},
                        "transcript_policy": {
                            "canonical_transcript": spec["text"],
                            "accepted_transcripts": accepted_transcripts,
                            "word_aliases": word_aliases,
                            "policy": override.get("policy", "canonical_exact"),
                        },
                        "music_effects_status": "human_blind_review_required",
                        **treatment_provenance(treatment),
                    }
                )
            except Exception as exc:
                omissions.append(
                    {
                        "chunk_id": chunk_id,
                        "treatment": treatment,
                        "stage": "treatment_screen",
                        "error": str(exc)[:2000],
                    }
                )
        if candidates:
            allowance = allowances.get(chunk_id)
            groups.append(
                {
                    "character": spec["speaker"].title(),
                    "book_speaker": spec["speaker"],
                    "chunk_id": chunk_id,
                    "transcript": spec["text"],
                    "review_context": allowance.get("review_instruction") if allowance else None,
                    "character_correct_effect_allowance": allowance,
                    "source": {
                        "segment_start": start_index,
                        "segment_end": end_index,
                        "ledger_binding_basis": spec["binding_basis"],
                        "transcript_policy": {
                            "accepted_transcripts": accepted_transcripts,
                            "word_aliases": word_aliases,
                            "policy": override.get("policy", "canonical_exact"),
                        },
                        **timing,
                        **alignment,
                    },
                    "candidates": candidates,
                }
            )
            print(f"built batch 002 chunk {chunk_id} ({len(candidates)} eligible)", flush=True)

    build_review(output, groups)
    after = project_hashes(project)
    if before != after:
        raise ExpansionBatch002Error("Protected project hashes changed.")
    write_json(
        output / "generation-summary.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "generated_at": utc_now(),
            "planned_chunk_count": len(selected),
            "review_chunk_count": len(groups),
            "candidate_count": sum(len(group["candidates"]) for group in groups),
            "character_correct_effect_allowance_count": sum(
                group.get("character_correct_effect_allowance") is not None
                for group in groups
            ),
            "omissions": omissions,
            "protected_project_hashes_before": before,
            "protected_project_hashes_after": after,
            "production_changes": False,
            "output_root": str(output),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
