#!/usr/bin/env python3
"""Build the word-aligned v2 repair round for weak Original Sin references."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from pathlib import Path

import mlx_whisper
import numpy as np
import soundfile as sf

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
    from benchmarks.original_sin_overlap_word_alignment import (
        exact_alignment_record,
        normalized_words,
        transcript_comparison,
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
    from original_sin_overlap_word_alignment import (
        exact_alignment_record,
        normalized_words,
        transcript_comparison,
    )

from mlx_audio.sts.models.mel_roformer import MelRoFormer, MelRoFormerConfig
from model_registry import resolve_model_path


ROUND_ID = "alexandria_original_sin_overlap_reference_repair_v2"
SEED = 20260731
DEFAULT_MEDIA = Path("/Users/tristan/Library/Mobile Documents/3L68KQB4HG~com~readdle~CommonDocuments/Documents/DW7*(RERUN:BF)/7c_divergentTimeline3*(RERUN:BF)/20c_bennyChris&Roz/1_originalSinAdaptation.mp3")
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_overlap_reference_repair_plan.json")
MOSSFORMER_REVISION = "ccd0ded00e26f38e9f5b0ba21608aa6a0bcd6434"


class RepairRoundError(RuntimeError):
    pass


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairRoundError(f"Expected JSON object: {path}")
    return value


def transcript_path(project: Path) -> Path:
    return project / "external_workflows" / "big_finish_overlap_reference_v1" / "private" / "transcript.json"


def project_hashes(project: Path) -> dict[str, str]:
    return {
        "voice_config.json": sha256_file(project / "voice_config.json"),
        "chunks.json": sha256_file(project / "chunks.json"),
    }


def write_center_channel(source: Path, destination: Path) -> None:
    audio, rate = sf.read(str(source), dtype="float32", always_2d=True)
    if audio.shape[1] == 1:
        center = audio[:, 0]
    else:
        center = np.mean(audio[:, :2], axis=1, dtype=np.float32)
    stereo = np.column_stack([center, center]).astype(np.float32)
    peak = float(np.max(np.abs(stereo))) if stereo.size else 0.0
    if peak > 0.98:
        stereo *= 0.98 / peak
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(destination), stereo, rate, subtype="PCM_16")


def write_blend(source: Path, processed: Path, destination: Path, processed_weight: float) -> None:
    source_audio, source_rate = sf.read(str(source), dtype="float32", always_2d=True)
    processed_audio, processed_rate = sf.read(str(processed), dtype="float32", always_2d=True)
    if source_rate != processed_rate:
        raise RepairRoundError("Cannot blend audio with different sample rates")
    frame_count = min(len(source_audio), len(processed_audio))
    if frame_count <= 0:
        raise RepairRoundError("Cannot blend empty audio")
    source_audio = source_audio[:frame_count, :2]
    processed_audio = processed_audio[:frame_count, :2]
    if source_audio.shape[1] == 1:
        source_audio = np.repeat(source_audio, 2, axis=1)
    if processed_audio.shape[1] == 1:
        processed_audio = np.repeat(processed_audio, 2, axis=1)
    blended = (
        source_audio * (1.0 - processed_weight)
        + processed_audio * processed_weight
    ).astype(np.float32)
    peak = float(np.max(np.abs(blended))) if blended.size else 0.0
    if peak > 0.98:
        blended *= 0.98 / peak
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(destination), blended, source_rate, subtype="PCM_16")


def precise_source_cut(
    *,
    media: Path,
    destination: Path,
    broad_path: Path,
    broad_start: float,
    broad_end: float,
    expected: str,
    whisper_model: str,
    leading_margin: float,
    trailing_margin: float,
    accepted_transcripts: list[str] | None = None,
    word_aliases: dict[str, list[str]] | None = None,
    minimum_source_start: float | None = None,
    minimum_source_end: float | None = None,
    maximum_source_end: float | None = None,
) -> dict:
    cut(media, broad_start, broad_end, broad_path)
    result = mlx_whisper.transcribe(
        str(broad_path),
        path_or_hf_repo=whisper_model,
        language="en",
        condition_on_previous_text=False,
        word_timestamps=True,
        verbose=False,
    )
    alignment = exact_alignment_record(
        expected,
        result,
        accepted_transcripts=accepted_transcripts or (),
        word_aliases=word_aliases,
    )
    source_start = max(broad_start, broad_start + alignment["word_start_seconds"] - leading_margin)
    source_end = min(broad_end, broad_start + alignment["word_end_seconds"] + trailing_margin)
    if minimum_source_start is not None:
        source_start = max(source_start, minimum_source_start)
    if minimum_source_end is not None:
        source_end = max(source_end, minimum_source_end)
    if maximum_source_end is not None:
        source_end = min(source_end, maximum_source_end)
    if source_end <= source_start:
        raise RepairRoundError(f"Invalid precise source range for {expected!r}")
    cut(media, source_start, source_end, destination)
    return {
        **alignment,
        "broad_start_seconds": broad_start,
        "broad_end_seconds": broad_end,
        "source_start_seconds": source_start,
        "source_end_seconds": source_end,
        "leading_margin_seconds": leading_margin,
        "trailing_margin_seconds": trailing_margin,
        "minimum_source_start_seconds": minimum_source_start,
        "minimum_source_end_seconds": minimum_source_end,
        "maximum_source_end_seconds": maximum_source_end,
    }


def treatment_provenance(treatment: str) -> dict[str, str | None]:
    if treatment == "source_mix":
        return {"extraction_model": None, "extraction_revision": None}
    if treatment == "mel_roformer_vocal":
        return {"extraction_model": VOCAL_MODEL, "extraction_revision": None}
    if treatment == "mossformer2_source_mix":
        return {
            "extraction_model": "starkdmi/MossFormer2_SE_48K_MLX",
            "extraction_revision": MOSSFORMER_REVISION,
        }
    if treatment in {"mossformer2_blend50", "mossformer2_blend70"}:
        return {
            "extraction_model": "starkdmi/MossFormer2_SE_48K_MLX blended with source mix",
            "extraction_revision": MOSSFORMER_REVISION,
        }
    if treatment == "center_channel_mid":
        return {"extraction_model": "deterministic stereo mid-channel", "extraction_revision": "v1"}
    raise RepairRoundError(f"Unknown treatment: {treatment}")


def build_review(output: Path, groups: list[dict]) -> None:
    review = output / "review"
    audio_root = review / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    public_groups = []
    answer_key = {}
    for group in groups:
        candidates = list(group["candidates"])
        rng.shuffle(candidates)
        public_candidates = []
        for candidate in candidates:
            blind_id = hashlib.sha256(
                f"{ROUND_ID}:{group['book_speaker']}:{candidate['treatment']}:{candidate['metrics']['sha256']}".encode()
            ).hexdigest()[:16]
            shutil.copy2(candidate["path"], audio_root / f"{blind_id}.wav")
            public_candidates.append({"id": blind_id, "audio": f"audio/{blind_id}.wav"})
            answer_key[blind_id] = {
                **candidate,
                "path": str(candidate["path"]),
                "character": group["character"],
                "book_speaker": group["book_speaker"],
                "transcript": group["transcript"],
                "review_context": group.get("review_context"),
                "source": group["source"],
            }
        public_groups.append(
            {
                "character": group["character"],
                "book_speaker": group["book_speaker"],
                "transcript": group["transcript"],
                "review_context": group.get("review_context"),
                "candidates": public_candidates,
            }
        )
    write_json(
        output / "private" / "answer-key.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "generated_at": utc_now(),
            "candidates": answer_key,
            "production_changes": False,
        },
    )
    payload = {"schema_version": 1, "round_id": ROUND_ID, "groups": public_groups}
    (review / "data.js").write_text(
        "window.ORIGINAL_SIN_REPAIR_ROUND = " + json.dumps(payload, indent=2, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    (review / "index.html").write_text(REVIEW_HTML, encoding="utf-8")


REVIEW_HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Original Sin reference repair v2</title><link rel="icon" href="data:,"> <style>:root{font-family:Inter,system-ui,sans-serif;color:#28231f;background:#f2eee6}*{box-sizing:border-box}body{margin:0}header,main{max-width:1080px;margin:auto;padding:24px 20px}header{border-bottom:1px solid #d2c8b8}h1,h2{font-family:Georgia,serif}.group{background:#fffdf8;border:1px solid #d5ccbd;border-radius:10px;padding:18px;margin:18px 0}.transcript{font:17px/1.45 Georgia,serif}.context{padding:10px;border-left:3px solid #7d7468;background:#f4efe7}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.candidate{border:1px solid #d9d0c3;border-radius:8px;padding:12px;background:#fff}audio{width:100%}.ratings{display:grid;gap:8px}label{display:grid;gap:4px;font-size:13px;font-weight:650}select,textarea{font:inherit;padding:7px;border:1px solid #b9afa2;border-radius:5px}textarea{min-height:66px}.decision{display:flex;gap:12px;margin:8px 0}.decision label{display:flex;align-items:center;gap:5px}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}@media(max-width:780px){.grid{grid-template-columns:1fr}}</style></head><body><header><h1>Original Sin reference repair v2</h1><p>Processing is hidden. Reject missing words, clipped boundaries, unrelated voices, music/effects, or damaged identity. Character-correct synthetic coloration is not itself contamination.</p><button id="export">Export review</button> <span id="progress"></span></header><main id="app"></main><script src="data.js"></script><script>(()=>{const d=window.ORIGINAL_SIN_REPAIR_ROUND,k='os-repair:'+d.round_id;let s={};try{s=JSON.parse(localStorage.getItem(k)||'{}')}catch(_){s={}}const app=document.getElementById('app'),scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join('');for(const g of d.groups){const sec=document.createElement('section');sec.className='group';sec.innerHTML=`<h2>${g.character}</h2><p class="transcript">${g.transcript}</p>${g.review_context?`<p class="context">${g.review_context}</p>`:''}<div class="grid"></div>`;const grid=sec.querySelector('.grid');g.candidates.forEach((c,i)=>{const card=document.createElement('article');card.className='candidate';card.innerHTML=`<h3>Candidate ${i+1}</h3><audio controls preload="none" src="${c.audio}"></audio><div class="ratings"><label>Voice isolation<select data-id="${c.id}" data-name="isolation">${scale}</select></label><label>Naturalness<select data-id="${c.id}" data-name="naturalness">${scale}</select></label><label>Identity clarity<select data-id="${c.id}" data-name="identity">${scale}</select></label><label>Reference usefulness<select data-id="${c.id}" data-name="usefulness">${scale}</select></label></div><div class="decision"><label><input type="radio" name="d-${c.id}" value="pass" data-id="${c.id}" data-name="decision">Pass</label><label><input type="radio" name="d-${c.id}" value="fail" data-id="${c.id}" data-name="decision">Fail</label></div><label>Notes<textarea data-id="${c.id}" data-name="notes"></textarea></label>`;grid.appendChild(card)});app.appendChild(sec)}for(const e of document.querySelectorAll('[data-id]')){const v=s[e.dataset.id]?.[e.dataset.name];if(e.type==='radio')e.checked=v===e.value;else if(v!=null)e.value=v;e.addEventListener(e.tagName==='TEXTAREA'?'input':'change',ev=>{const id=ev.target.dataset.id,n=ev.target.dataset.name;s[id]=s[id]||{};s[id][n]=ev.target.value;localStorage.setItem(k,JSON.stringify(s));progress()})}function progress(){const ids=d.groups.flatMap(g=>g.candidates.map(c=>c.id)),done=ids.filter(id=>s[id]?.decision).length;document.getElementById('progress').textContent=`${done} of ${ids.length} decided`}document.getElementById('export').onclick=()=>{const a=document.createElement('a'),b=new Blob([JSON.stringify({schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),results:s},null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();</script></body></html>'''


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
        raise RepairRoundError("Repair plan round_id mismatch")
    groups_plan = plan.get("groups")
    if not isinstance(groups_plan, list) or len(groups_plan) != 11:
        raise RepairRoundError("Repair plan must contain 11 character groups")
    candidate_count = sum(len(group.get("treatments") or []) for group in groups_plan)
    if candidate_count != plan.get("candidate_count"):
        raise RepairRoundError("Repair plan candidate_count mismatch")

    output = (
        args.output_root.expanduser().resolve()
        if args.output_root
        else project / "external_workflows" / "big_finish_overlap_reference_v1" / "reference_repair_round_v2"
    )
    if output.exists():
        if not args.replace:
            raise RepairRoundError(f"Output exists; pass --replace to rebuild: {output}")
        shutil.rmtree(output)

    before_hashes = project_hashes(project)
    segments = read_json(transcript_path(project))["segments"]
    whisper_model = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    required_treatments = {t for group in groups_plan for t in group["treatments"]}
    vocal_model = None
    if "mel_roformer_vocal" in required_treatments:
        vocal_model = MelRoFormer.from_pretrained(
            VOCAL_MODEL,
            config=MelRoFormerConfig.zfturbo_vocals_v1(),
        )
        vocal_model.eval()
    mossformer = load_mossformer() if "mossformer2_source_mix" in required_treatments else None

    private_audio = output / "private" / "audio"
    groups = []
    for group_plan in groups_plan:
        character = str(group_plan["character"])
        book_speaker = str(group_plan["book_speaker"])
        expected = str(group_plan["expected_transcript"])
        accepted_transcripts = [
            str(value) for value in group_plan.get("accepted_transcript_variants", [])
        ]
        recognizer_transcripts = [
            str(value) for value in group_plan.get("recognizer_transcript_variants", [])
        ]
        comparison_transcripts = [expected, *accepted_transcripts, *recognizer_transcripts]
        alignment_word_aliases = {
            str(key): [str(value) for value in values]
            for key, values in group_plan.get("alignment_word_aliases", {}).items()
        }
        segment_start = int(group_plan["segment_start"])
        segment_end = int(group_plan["segment_end"])
        broad_padding = float(group_plan.get("broad_padding_seconds", plan["broad_padding_seconds"]))
        leading_margin = float(group_plan.get("leading_margin_seconds", plan["leading_margin_seconds"]))
        trailing_margin = float(group_plan.get("trailing_margin_seconds", plan["trailing_margin_seconds"]))
        broad_start = max(0.0, float(segments[segment_start]["start"]) - broad_padding)
        broad_end = float(segments[segment_end]["end"]) + broad_padding
        minimum_source_end = None
        maximum_source_end = None
        if group_plan.get("preserve_segment_end") is True:
            minimum_source_end = float(segments[segment_end]["end"]) + float(
                group_plan.get("minimum_segment_end_margin_seconds", 0.0)
            )
            if segment_end + 1 < len(segments):
                maximum_source_end = float(segments[segment_end + 1]["start"]) - float(
                    group_plan.get("next_speaker_safety_seconds", 0.01)
                )
        slug = re_slug(book_speaker)
        broad_path = private_audio / f"{slug}__broad.wav"
        source_path = private_audio / f"{slug}__source_precise.wav"
        alignment = precise_source_cut(
            media=media,
            destination=source_path,
            broad_path=broad_path,
            broad_start=broad_start,
            broad_end=broad_end,
            expected=expected,
            whisper_model=whisper_model,
            leading_margin=leading_margin,
            trailing_margin=trailing_margin,
            accepted_transcripts=[*accepted_transcripts, *recognizer_transcripts],
            word_aliases=alignment_word_aliases,
            minimum_source_end=minimum_source_end,
            maximum_source_end=maximum_source_end,
        )
        candidates = []
        mossformer_path = private_audio / f"{slug}__mossformer2.wav"
        for treatment in group_plan["treatments"]:
            treatment = str(treatment)
            if treatment == "source_mix":
                candidate_path = source_path
            elif treatment == "mel_roformer_vocal":
                if vocal_model is None:
                    raise RepairRoundError("Mel-RoFormer model was not loaded")
                candidate_path = private_audio / f"{slug}__mel_vocal.wav"
                separate(vocal_model, source_path, candidate_path)
            elif treatment == "mossformer2_source_mix":
                if mossformer is None:
                    raise RepairRoundError("MossFormer2 model was not loaded")
                candidate_path = mossformer_path
                if not candidate_path.exists():
                    enhance(source_path, candidate_path, mossformer)
            elif treatment in {"mossformer2_blend50", "mossformer2_blend70"}:
                if mossformer is None:
                    raise RepairRoundError("MossFormer2 model was not loaded")
                if not mossformer_path.exists():
                    enhance(source_path, mossformer_path, mossformer)
                weight = 0.5 if treatment.endswith("50") else 0.7
                candidate_path = private_audio / f"{slug}__{treatment}.wav"
                write_blend(source_path, mossformer_path, candidate_path, weight)
            elif treatment == "center_channel_mid":
                candidate_path = private_audio / f"{slug}__center_mid.wav"
                write_center_channel(source_path, candidate_path)
            else:
                raise RepairRoundError(f"Unsupported treatment: {treatment}")
            observed = transcribe(candidate_path, whisper_model)
            comparison = transcript_comparison(
                comparison_transcripts,
                observed,
                alignment_word_aliases,
            )
            matched_transcript = comparison["matched_expected_transcript"]
            if comparison["transcript_word_aliases_used"]:
                matched_transcript_basis = "bounded_recognizer_alias"
            elif matched_transcript == expected:
                matched_transcript_basis = "canonical_adaptation_transcript"
            elif matched_transcript in accepted_transcripts:
                matched_transcript_basis = "explicitly_approved_performance_variant"
            else:
                matched_transcript_basis = "bounded_recognizer_equivalent"
            candidates.append(
                {
                    "treatment": treatment,
                    "path": candidate_path,
                    "metrics": metrics(candidate_path),
                    "automatic_transcript": observed,
                    **comparison,
                    "matched_transcript_basis": matched_transcript_basis,
                    **treatment_provenance(treatment),
                }
            )
        groups.append(
            {
                "character": character,
                "book_speaker": book_speaker,
                "transcript": expected,
                "review_context": group_plan.get("review_context"),
                "source": {
                    "media_sha256": sha256_file(media),
                    "segment_start": segment_start,
                    "segment_end": segment_end,
                    "canonical_expected_transcript": expected,
                    "book_transcript": group_plan.get("book_transcript", expected),
                    "accepted_transcript_variants": accepted_transcripts,
                    "recognizer_transcript_variants": recognizer_transcripts,
                    "semantic_variant_approval": group_plan.get("semantic_variant_approval"),
                    "alignment_word_aliases": alignment_word_aliases,
                    **alignment,
                },
                "candidates": candidates,
            }
        )
        print(f"built {character}", flush=True)

    build_review(output, groups)
    after_hashes = project_hashes(project)
    if after_hashes != before_hashes:
        raise RepairRoundError("Protected project hashes changed during repair-round build")
    summary = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "generated_at": utc_now(),
        "character_count": len(groups),
        "candidate_count": sum(len(group["candidates"]) for group in groups),
        "source_media_sha256": sha256_file(media),
        "protected_project_hashes_before": before_hashes,
        "protected_project_hashes_after": after_hashes,
        "production_changes": False,
        "output_root": str(output),
    }
    write_json(output / "generation-summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0


def re_slug(value: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


if __name__ == "__main__":
    raise SystemExit(main())
