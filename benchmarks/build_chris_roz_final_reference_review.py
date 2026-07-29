#!/usr/bin/env python3
"""Build the final blind source-reference review from scan + curated clips."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCAN_ROOT = Path("/private/tmp/alexandria-chris-roz-scan-v1")
DEFAULT_CURATED_ROOT = ROOT / ".omo/evidence/chris-roz-reference-selection-v1"
DEFAULT_OUTPUT = Path("/private/tmp/alexandria-chris-roz-final-reference-review-v2")
DEFAULT_SUPPLEMENT_CONFIG = ROOT / "benchmarks/chris_roz_consolidated_supplement.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("source_key") != right.get("source_key"):
        return False
    return min(float(left["end_seconds"]), float(right["end_seconds"])) - max(float(left["start_seconds"]), float(right["start_seconds"])) > 1.0


def select_scan(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    eligible = [
        row
        for row in rows
        if 4.5 <= float(row["duration_seconds"]) <= 16.0
        and float(row["acoustic_metrics"]["clipping_fraction"]) == 0.0
        and float(row["acoustic_metrics"]["silence_ratio"]) <= 0.3
        and float(row["min_half_similarity"]) >= 0.72
        and float(row["half_consistency"]) >= 0.65
        and float(row["identity_margin"]) >= 0.04
    ]
    eligible.sort(
        key=lambda row: (
            float(row["min_half_similarity"]),
            float(row["identity_margin"]),
            float(row["half_consistency"]),
            -abs(float(row["duration_seconds"]) - 9.0),
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    for row in eligible:
        if any(overlap(row, existing) for existing in selected):
            continue
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def copy_blind(source: Path, target_root: Path, identity: str, logical_id: str) -> tuple[str, str]:
    blind = hashlib.sha256(f"{identity}:{logical_id}:{sha256_file(source)}".encode("utf-8")).hexdigest()[:18]
    target = target_root / f"{blind}.wav"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or sha256_file(target) != sha256_file(source):
        shutil.copy2(source, target)
    return blind, str(target.relative_to(target_root.parent))


def load_supplement(config_path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], Path]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported consolidated-supplement schema.")
    evidence_root = Path(str(config["source_evidence_root"])).expanduser().resolve()
    result_path = evidence_root / "reference-finalists.json"
    if not result_path.is_file():
        raise FileNotFoundError(f"ECAPA finalist evidence is missing: {result_path}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    rows = {
        str(row["candidate"]["id"]): row
        for row in result.get("rows", [])
        if isinstance(row, dict) and isinstance(row.get("candidate"), dict)
    }
    requested = {
        str(candidate_id)
        for values in config.get("groups", {}).values()
        for candidate_id in values
    }
    missing = sorted(requested - set(rows))
    if missing:
        raise ValueError(f"ECAPA supplement candidates are missing: {missing}")
    excluded = set(config.get("selection_policy", {}).get("exclude_speaker_gate_statuses", []))
    invalid = sorted(
        candidate_id
        for candidate_id in requested
        if rows[candidate_id].get("speaker_consistency", {}).get("gate", {}).get("status") in excluded
    )
    if invalid:
        raise ValueError(f"ECAPA supplement contains excluded speaker-gate rows: {invalid}")
    return config, rows, evidence_root


def supplement_candidate(
    *,
    row: dict[str, Any],
    evidence_root: Path,
    audio_root: Path,
    identity: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    candidate = dict(row["candidate"])
    logical_id = str(candidate["id"])
    source = evidence_root / "reference-finalist-review" / str(row["review_audio"])
    if not source.is_file():
        raise FileNotFoundError(f"ECAPA review audio is missing: {source}")
    blind, relative = copy_blind(source, audio_root, identity, logical_id)
    public = {
        "id": blind,
        "audio": relative,
        "transcript": str(candidate["transcript_provisional"]),
        "duration_seconds": float(candidate["end_seconds"]) - float(candidate["start_seconds"]),
    }
    private = {
        "kind": "ecapa_large_v3_supplement",
        "logical_id": logical_id,
        "candidate": candidate,
        "speaker_consistency": row.get("speaker_consistency"),
        "transcript_gate": row.get("transcript_gate"),
        "audio_metrics": row.get("audio_metrics"),
        "audio_sha256": sha256_file(source),
    }
    return public, blind, private


def private_interval(row: dict[str, Any]) -> dict[str, Any] | None:
    candidate = row.get("candidate") if isinstance(row.get("candidate"), dict) else row
    source_key = candidate.get("source_key")
    trim = candidate.get("trim") if isinstance(candidate.get("trim"), dict) else None
    start = trim.get("start_seconds") if trim else candidate.get("start_seconds")
    end = trim.get("end_seconds") if trim else candidate.get("end_seconds")
    if source_key is None or start is None or end is None:
        return None
    return {
        "source_key": str(source_key),
        "start_seconds": float(start),
        "end_seconds": float(end),
    }


def ensure_not_duplicate(candidate: dict[str, Any], existing: dict[str, Any]) -> None:
    target = private_interval({"candidate": candidate})
    if target is None:
        return
    for blind_id, row in existing.items():
        current = private_interval(row)
        if current is not None and overlap(target, current):
            raise ValueError(
                "Consolidated supplement overlaps an existing review candidate: "
                f"{candidate.get('id')} with {blind_id}."
            )


def review_assets(output_root: Path, public: dict[str, Any]) -> None:
    review = output_root / "review"
    review.mkdir(parents=True, exist_ok=True)
    (review / "data.js").write_text(
        "window.CHRIS_ROZ_REFERENCE_REVIEW = " + json.dumps(public, indent=2, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    (review / "index.html").write_text(
        """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Chris and Roz reference review</title><link rel="stylesheet" href="styles.css"></head><body><header><p class="eyebrow">Alexandria blind listening</p><h1>Chris Cwej and Roz Forrester source selection</h1><p>First choose stable identity references, then judge the smaller performance bank. Sources, stories, timestamps, and objective scores remain hidden. T'Nia Miller appears only in the final style-layer section and is never treated as Roz identity.</p><div class="bar"><button id="export">Export scores</button><span id="progress"></span></div></header><main id="app"></main><script src="data.js"></script><script src="app.js"></script></body></html>""",
        encoding="utf-8",
    )
    (review / "styles.css").write_text(
        """:root{font-family:Inter,ui-sans-serif,system-ui,sans-serif;color:#29241e;background:#f2eee6}*{box-sizing:border-box}body{margin:0}header,main{max-width:1080px;margin:auto;padding:32px 24px}header{border-bottom:1px solid #d4ccbf}.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#70685e}h1,h2{font-family:Georgia,serif}h1{font-size:35px;margin:.2rem 0 .8rem}h2{font-size:27px;margin:46px 0 8px}.note{color:#6d655a;line-height:1.5;max-width:800px}.anchor{background:#e9e3d8;border-left:4px solid #806e5d;padding:16px 18px;margin:14px 0 18px}.card{background:#fffdf8;border:1px solid #d5cdbf;border-radius:10px;padding:18px;margin:14px 0}.card h3{margin:0 0 10px}.transcript{font:17px/1.55 Georgia,serif;color:#403930}audio{width:100%}.ratings{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;margin-top:12px}label{display:grid;gap:4px;font-size:12px;font-weight:650;color:#5f574d}select,textarea{font:inherit;border:1px solid #bcb2a4;border-radius:5px;padding:8px;background:white}textarea{min-height:68px}.retain{display:flex;align-items:center;gap:8px;margin:12px 0}.retain input{width:18px;height:18px}.bar{display:flex;gap:15px;align-items:center;margin-top:18px}button{border:1px solid #315c55;background:#315c55;color:white;border-radius:6px;padding:10px 14px;font-weight:700;cursor:pointer}@media(max-width:620px){header,main{padding:24px 16px}h1{font-size:29px}.ratings{grid-template-columns:1fr}}""",
        encoding="utf-8",
    )
    (review / "app.js").write_text(
        """(()=>{const d=window.CHRIS_ROZ_REFERENCE_REVIEW,k='chris-roz-reference:'+d.round_id;let s={};try{s=JSON.parse(localStorage.getItem(k)||'{}')}catch(_){s={}}const app=document.getElementById('app'),scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join('');function field(id,n,l){return `<label>${l}<select data-id="${id}" data-name="${n}">${scale}</select></label>`}for(const g of d.groups){const sec=document.createElement('section');sec.innerHTML=`<h2>${g.label}</h2><p class="note">${g.instructions}</p>`;if(g.anchor)sec.innerHTML+=`<div class="anchor"><strong>Fixed actor anchor</strong><audio controls preload="none" src="${g.anchor.audio}"></audio><p class="transcript">${g.anchor.transcript}</p></div>`;for(const c of g.candidates){const x=s[c.id]||{},card=document.createElement('article');card.className='card';const labels=g.kind==='identity'?['Identity likeness','Cleanliness','Single speaker','Naturalness','Clone suitability']:g.kind==='performance'?['Identity preserved','Delivery clarity','Cleanliness','Naturalness','Bank usefulness']:['Roz compatibility','Authority / weight','Cleanliness','Naturalness','Style usefulness'];card.innerHTML=`<h3>Candidate ${c.display_id}</h3><audio controls preload="none" src="${c.audio}"></audio><p class="transcript">${c.transcript}</p><div class="ratings">${labels.map((l,i)=>field(c.id,['identity','delivery','cleanliness','naturalness','usefulness'][i],l)).join('')}</div><label class="retain"><input type="checkbox" data-id="${c.id}" data-name="retain" ${x.retain?'checked':''}>Retain for model testing</label><label>Notes<textarea data-id="${c.id}" data-name="notes">${x.notes||''}</textarea></label>`;sec.appendChild(card)}app.appendChild(sec)}for(const e of document.querySelectorAll('[data-id]')){const id=e.dataset.id,n=e.dataset.name;if(e.tagName==='SELECT'&&s[id]?.[n]!=null)e.value=s[id][n];e.addEventListener('change',save);e.addEventListener('input',save)}function save(e){const id=e.target.dataset.id,n=e.target.dataset.name;s[id]=s[id]||{};s[id][n]=e.target.type==='checkbox'?e.target.checked:e.target.value;localStorage.setItem(k,JSON.stringify(s));progress()}function progress(){const total=d.groups.flatMap(g=>g.candidates).length,done=Object.values(s).filter(x=>x.identity&&x.delivery&&x.cleanliness&&x.naturalness&&x.usefulness).length;document.getElementById('progress').textContent=`${done} of ${total} fully scored`}document.getElementById('export').onclick=()=>{const b=new Blob([JSON.stringify({schema_version:1,round_id:d.round_id,reviewer:'tristan',exported_at:new Date().toISOString(),scores:s},null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};progress()})();""",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan-root", default=str(DEFAULT_SCAN_ROOT))
    parser.add_argument("--curated-root", default=str(DEFAULT_CURATED_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--supplement-config", default=str(DEFAULT_SUPPLEMENT_CONFIG))
    parser.add_argument("--scan-per-identity", type=int, default=10)
    args = parser.parse_args()

    scan_root = Path(args.scan_root).expanduser().resolve()
    curated_root = Path(args.curated_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    supplement_config_path = Path(args.supplement_config).expanduser().resolve()
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    audio_root = output_root / "review/audio"

    scan = json.loads((scan_root / "scan-results.json").read_text(encoding="utf-8"))
    curated = json.loads((curated_root / "private/answer-key.json").read_text(encoding="utf-8"))
    supplement, supplement_rows, supplement_root = load_supplement(supplement_config_path)
    answer: dict[str, Any] = {"schema_version": 1, "round_id": str(supplement["round_id"]), "candidates": {}, "anchors": curated["anchors"]}
    groups: list[dict[str, Any]] = []
    rng = random.Random(20260729)

    for identity, label in (("chris", "Chris Cwej — identity references"), ("roz", "Roz Forrester — identity references")):
        candidates = []
        for row in select_scan(list(scan["previews"][identity]), int(args.scan_per_identity)):
            source = scan_root / str(row["preview_audio"])
            blind, relative = copy_blind(source, audio_root, identity, str(row["preview_id"]))
            answer["candidates"][blind] = {"kind": "identity_scan", **row, "audio_sha256": sha256_file(source)}
            candidates.append({"id": blind, "audio": relative, "transcript": row["text"], "duration_seconds": row["duration_seconds"]})
        for logical_id in supplement["groups"][f"{identity}_identity"]:
            row = supplement_rows[str(logical_id)]
            ensure_not_duplicate(row["candidate"], answer["candidates"])
            public_candidate, blind, private_candidate = supplement_candidate(
                row=row,
                evidence_root=supplement_root,
                audio_root=audio_root,
                identity=identity,
            )
            answer["candidates"][blind] = private_candidate
            candidates.append(public_candidate)
        rng.shuffle(candidates)
        for index, candidate in enumerate(candidates, start=1):
            candidate["display_id"] = f"{identity[0].upper()}I{index:02d}"
        anchor_entry = curated["anchors"][identity]["entries"][0]
        anchor_source = curated_root / str(anchor_entry["audio"])
        anchor_target = output_root / "review/anchors" / f"{identity}.wav"
        anchor_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(anchor_source, anchor_target)
        groups.append({"kind": "identity", "key": f"{identity}_identity", "label": label, "instructions": "Choose clips that sound unmistakably like the actor, contain one speaker only, and are clean and stable enough to condition multiple models.", "anchor": {"audio": str(anchor_target.relative_to(output_root / "review")), "transcript": anchor_entry["transcript"]}, "candidates": candidates})

    performance_ids = {
        "chris": ["chris_trial_analytical", "chris_jabari_earnest", "chris_dread_protective", "chris_vanguard_resolute"],
        "roz": ["roz_vanguard_identity", "roz_vanguard_concern", "roz_vanguard_threat", "roz_damaged_goods_dry"],
    }
    for identity, label in (("chris", "Chris Cwej — performance bank"), ("roz", "Roz Forrester — performance bank")):
        candidates = []
        for logical_id in performance_ids[identity]:
            row = curated["candidates"][logical_id]
            source = curated_root / str(row["audio"])
            blind, relative = copy_blind(source, audio_root, identity, logical_id)
            answer["candidates"][blind] = {"kind": "curated_performance", **row}
            candidates.append({"id": blind, "audio": relative, "transcript": row["transcript"], "duration_seconds": row["duration_seconds"]})
        for logical_id in supplement["groups"][f"{identity}_performance"]:
            row = supplement_rows[str(logical_id)]
            ensure_not_duplicate(row["candidate"], answer["candidates"])
            public_candidate, blind, private_candidate = supplement_candidate(
                row=row,
                evidence_root=supplement_root,
                audio_root=audio_root,
                identity=identity,
            )
            answer["candidates"][blind] = private_candidate
            candidates.append(public_candidate)
        rng.shuffle(candidates)
        for index, candidate in enumerate(candidates, start=1):
            candidate["display_id"] = f"{identity[0].upper()}P{index:02d}"
        groups.append({"kind": "performance", "key": f"{identity}_performance", "label": label, "instructions": "These are not all intended as the primary clone anchor. Retain clips that preserve identity while providing a clearly useful delivery state for emotion-reference or targeted prompting tests.", "anchor": None, "candidates": candidates})

    style_candidates = []
    for logical_id, row in curated["candidates"].items():
        if row.get("identity") != "tnia_style":
            continue
        source = curated_root / str(row["audio"])
        blind, relative = copy_blind(source, audio_root, "tnia_style", logical_id)
        answer["candidates"][blind] = {"kind": "tnia_style", **row}
        style_candidates.append({"id": blind, "audio": relative, "transcript": row["transcript"], "duration_seconds": row["duration_seconds"]})
    for logical_id in supplement["groups"]["tnia_style"]:
        row = supplement_rows[str(logical_id)]
        ensure_not_duplicate(row["candidate"], answer["candidates"])
        public_candidate, blind, private_candidate = supplement_candidate(
            row=row,
            evidence_root=supplement_root,
            audio_root=audio_root,
            identity="tnia_style",
        )
        answer["candidates"][blind] = private_candidate
        style_candidates.append(public_candidate)
    rng.shuffle(style_candidates)
    for index, candidate in enumerate(style_candidates, start=1):
        candidate["display_id"] = f"T{index:02d}"
    groups.append({"kind": "style", "key": "tnia_style", "label": "T'Nia Miller — optional Roz performance layer", "instructions": "Judge only whether this delivery would add useful gravitas, authority, or weight when used separately from Yasmin Bannerman's identity reference. Reject anything likely to pull Roz into a second identity.", "anchor": None, "candidates": style_candidates})

    public = {"schema_version": 1, "round_id": answer["round_id"], "groups": groups, "answer_key_separate": True, "production_promotion_allowed": False}
    review_assets(output_root, public)
    write_json(output_root / "private/answer-key.json", answer)
    manifest = {"schema_version": 1, "round_id": answer["round_id"], "group_count": len(groups), "candidate_count": sum(len(group["candidates"]) for group in groups), "review": "review/index.html", "answer_key": "private/answer-key.json", "production_promotion_allowed": False, "voice_assignment_changed": False, "source_audio_changed": False}
    write_json(output_root / "manifest.json", manifest)
    print(json.dumps({"manifest": str(output_root / "manifest.json"), "review": str(output_root / "review/index.html"), "candidates": manifest["candidate_count"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
