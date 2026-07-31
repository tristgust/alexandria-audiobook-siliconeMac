#!/usr/bin/env python3
"""Build the first bounded quasi-emotive acceptance round for non-core Voices.

The round is research-only. It reads approved Original Sin performance evidence,
generates unseen book lines into a separate external-workflow directory, runs the
pinned offline transcription evaluator, and writes a blind listening page. It
does not modify chunks.json, voice_config.json, or production routing.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import shutil
import sys
from typing import Any, Mapping

import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from model_registry import model_cache_status  # noqa: E402
from transcription_evaluator import (  # noqa: E402
    evaluate_transcriptions,
    normalized_words,
)
from tts import TTSEngine  # noqa: E402


ROUND_ID = "alexandria_original_sin_noncore_quasi_emotive_round_v1"
DEFAULT_PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
DEFAULT_CONFIG = Path(
    "/Users/tristan/pinokio/api/alexandria-audiobook.git/config.json"
)
DEFAULT_OUTPUT = (
    DEFAULT_PROJECT
    / "external_workflows"
    / "big_finish_overlap_reference_v1"
    / "noncore_quasi_emotive_round_v1"
)
PRIMARY_SEED = 130363
RETRY_SEED = 130464
MAX_ACCEPTABLE_WER = 0.25

# These aliases are deliberately limited to the Securitybot fixture, where the
# pinned recognizer repeatedly splits or respells proper nouns while preserving
# the audible words. They do not enable general fuzzy matching.
TRANSCRIPTION_ALIAS_POLICY = {
    "bot_synthetic_neutral": {
        "token_aliases": {
            "forrestor": "forrester",
            "rosling": "roslyn",
            "rosalind": "roslyn",
            "5": "five",
        },
        "phrase_aliases": {
            ("a", "judicator"): ("adjudicator",),
            ("space", "port"): ("spaceport",),
        },
    }
}


# Baseline versus specialist-reference comparison is reserved for characters
# with several clean performances. Thin-evidence characters get one bounded
# specialist test rather than a duplicate comparison pretending to be a bank.
MODE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "mode_id": "beltempest_interrogative_impatience",
        "speaker": "BELTEMPEST",
        "target_chunk_id": 1142,
        "reference_chunk_id": 2047,
        "title": "Beltempest — interrogative impatience",
        "review_instruction": (
            "Military authority, clipped questioning, and rising impatience "
            "without losing Beltempest's identity."
        ),
        "compare_identity_baseline": True,
    },
    {
        "mode_id": "beltempest_military_volatility",
        "speaker": "BELTEMPEST",
        "target_chunk_id": 1305,
        "reference_chunk_id": 1590,
        "title": "Beltempest — rigid military volatility",
        "review_instruction": (
            "Rigid command authority with controlled simmering volatility, "
            "not generic shouting."
        ),
        "compare_identity_baseline": True,
    },
    {
        "mode_id": "beltempest_weary_resignation",
        "speaker": "BELTEMPEST",
        "target_chunk_id": 1419,
        "reference_chunk_id": 2584,
        "title": "Beltempest — weary resignation",
        "review_instruction": (
            "Audibly weary and subdued while remaining recognizably Beltempest."
        ),
        "compare_identity_baseline": True,
    },
    {
        "mode_id": "beltempest_urgent_command",
        "speaker": "BELTEMPEST",
        "target_chunk_id": 2161,
        "reference_chunk_id": 2716,
        "title": "Beltempest — urgent command",
        "review_instruction": (
            "Urgent military projection and clipped momentum with complete, "
            "intelligible words."
        ),
        "compare_identity_baseline": True,
    },
    {
        "mode_id": "tobias_cultivated_menace",
        "speaker": "TOBIAS VAUGHN",
        "target_chunk_id": 1316,
        "reference_chunk_id": 3635,
        "title": "Tobias Vaughn — cultivated menace",
        "review_instruction": (
            "Smooth cultivated patience with chilling concealed menace, never "
            "melodramatic villainy."
        ),
        "compare_identity_baseline": True,
    },
    {
        "mode_id": "tobias_polished_probe",
        "speaker": "TOBIAS VAUGHN",
        "target_chunk_id": 989,
        "reference_chunk_id": 4764,
        "title": "Tobias Vaughn — polished probing calm",
        "review_instruction": (
            "Polished conversational control with a subtle threat underneath."
        ),
        "compare_identity_baseline": True,
    },
    {
        "mode_id": "zebulon_nervous_analysis",
        "speaker": "ZEBULON PRYCE",
        "target_chunk_id": 2945,
        "reference_chunk_id": 2919,
        "title": "Zebulon Pryce — nervous analysis",
        "review_instruction": (
            "Defensive analytical precision with mounting strain and unstable focus."
        ),
        "compare_identity_baseline": True,
    },
    {
        "mode_id": "zebulon_intense_questioning",
        "speaker": "ZEBULON PRYCE",
        "target_chunk_id": 2964,
        "reference_chunk_id": 3090,
        "title": "Zebulon Pryce — intense questioning",
        "review_instruction": (
            "Intense exact questioning with intellectual control beginning to fracture."
        ),
        "compare_identity_baseline": True,
    },
    {
        "mode_id": "hater_wounded_fury",
        "speaker": "HATER OF HUMANS",
        "target_chunk_id": 4477,
        "reference_chunk_id": 4366,
        "title": "Hater of Humans — wounded fury",
        "review_instruction": (
            "Thunderous alien formality, wounded pride, and commanding fury while "
            "remaining clear enough for long-form listening."
        ),
        "compare_identity_baseline": False,
    },
    {
        "mode_id": "karvellis_amplified_command",
        "speaker": "KARVELLIS",
        "target_chunk_id": 14,
        "reference_chunk_id": 11,
        "title": "Karvellis — amplified command",
        "review_instruction": (
            "Hard amplified command delivery, clipped urgency, and zero warmth."
        ),
        "compare_identity_baseline": False,
    },
    {
        "mode_id": "lubineki_rough_jovial",
        "speaker": "LUBINEKI",
        "target_chunk_id": 92,
        "reference_chunk_id": 90,
        "title": "Lubineki — rough jovial concern",
        "review_instruction": (
            "Rough jovial confidence with alert concern and natural conversational timing."
        ),
        "compare_identity_baseline": False,
    },
    {
        "mode_id": "powerless_panicked_urgency",
        "speaker": "POWERLESS FRIENDLESS",
        "target_chunk_id": 1317,
        "reference_chunk_id": 1322,
        "title": "Powerless Friendless — panicked urgency",
        "review_instruction": (
            "Exposed panic and urgent projection without losing intelligibility or "
            "the character's alien identity."
        ),
        "compare_identity_baseline": False,
    },
    {
        "mode_id": "rashid_tired_authority",
        "speaker": "RASHID",
        "target_chunk_id": 394,
        "reference_chunk_id": 405,
        "title": "Rashid — tired bureaucratic authority",
        "review_instruction": (
            "Tired bureaucratic authority, dry bluntness, and natural conversational rhythm."
        ),
        "compare_identity_baseline": False,
    },
    {
        "mode_id": "under_sergeant_military_menace",
        "speaker": "UNDER-SERGEANT",
        "target_chunk_id": 1884,
        "reference_chunk_id": 2002,
        "title": "Under-Sergeant — disciplined menace",
        "review_instruction": (
            "Terse military precision with controlled menace. Character-correct "
            "intercom coloration is acceptable; unrelated contamination is not."
        ),
        "compare_identity_baseline": False,
    },
    {
        "mode_id": "bot_synthetic_neutral",
        "speaker": "BOT",
        "target_chunk_id": 495,
        "reference_chunk_id": 618,
        "title": "Securitybot — constrained synthetic delivery",
        "review_instruction": (
            "Mechanically consistent, precise, and intentionally low-emotion. "
            "Do not reward human-like acting drift."
        ),
        "compare_identity_baseline": False,
    },
    {
        "mode_id": "computer_interrupted_system",
        "speaker": "COMPUTER",
        "target_chunk_id": 1280,
        "reference_chunk_id": 1261,
        "title": "Computer — interrupted system urgency",
        "review_instruction": (
            "Exact synthetic diction with a bounded interruption cue, not broad emotion."
        ),
        "compare_identity_baseline": False,
    },
)


class QuasiEmotiveRoundError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise QuasiEmotiveRoundError(f"{label} is missing: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuasiEmotiveRoundError(f"{label} is invalid: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audio_record(path: Path) -> dict[str, Any]:
    info = sf.info(str(path))
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "sample_rate": int(info.samplerate),
        "frames": int(info.frames),
        "duration_seconds": float(info.duration),
        "format": info.format,
    }


def locked_reference(chunk: Mapping[str, Any]) -> dict[str, Any]:
    lock = chunk.get("approved_audio_lock")
    origin = chunk.get("approved_audio_origin")
    if not isinstance(lock, Mapping) or lock.get("status") != "locked":
        raise QuasiEmotiveRoundError(
            f"Reference chunk {chunk.get('id')} is not approved and locked."
        )
    if lock.get("direct_placement_tier") != "strict_clean":
        raise QuasiEmotiveRoundError(
            f"Reference chunk {chunk.get('id')} is not strict-clean."
        )
    if not isinstance(origin, Mapping):
        raise QuasiEmotiveRoundError(
            f"Reference chunk {chunk.get('id')} has no approved origin."
        )
    source = Path(str(origin.get("source_audio_path") or "")).expanduser().resolve()
    expected_sha = str(origin.get("source_audio_sha256") or "")
    if not source.is_file() or sha256_file(source) != expected_sha:
        raise QuasiEmotiveRoundError(
            f"Approved reference source is missing or changed: {source}"
        )
    return {
        "audio_path": source,
        "audio_sha256": expected_sha,
        "reference_text": str(chunk.get("text") or "").strip(),
        "candidate_id": lock.get("candidate_id"),
        "chunk_id": chunk.get("id"),
    }


def current_identity_reference(
    project_root: Path,
    voice: Mapping[str, Any],
) -> dict[str, Any]:
    raw = Path(str(voice.get("ref_audio") or ""))
    source = raw.expanduser().resolve() if raw.is_absolute() else (project_root / raw).resolve()
    if not source.is_file():
        raise QuasiEmotiveRoundError(f"Voice identity audio is missing: {source}")
    text = str(voice.get("ref_text") or "").strip()
    if not text:
        raise QuasiEmotiveRoundError("Voice identity reference has no transcript.")
    return {
        "audio_path": source,
        "audio_sha256": sha256_file(source),
        "reference_text": text,
        "candidate_id": voice.get("approved_adaptation_identity_candidate_id"),
        "chunk_id": None,
    }


def candidate_identifier(
    *,
    mode_id: str,
    kind: str,
    reference_sha256: str,
    seed: int,
) -> str:
    payload = f"{ROUND_ID}:{mode_id}:{kind}:{reference_sha256}:{seed}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def candidate_voice(
    voice: Mapping[str, Any],
    reference: Mapping[str, Any],
    *,
    seed: int,
) -> dict[str, Any]:
    result = copy.deepcopy(dict(voice))
    result.update(
        {
            "type": "clone",
            "clone_backend": "qwen3_instruction_controlled",
            "ref_audio": str(reference["audio_path"]),
            "ref_text": reference["reference_text"],
            "seed": str(seed),
        }
    )
    # The round evaluates one selected reference directly; automatic project
    # routing and formal owned-recording reference banks remain untouched.
    result.pop("experimental_prompt_routing", None)
    result.pop("reference_bank_path", None)
    result.pop("reference_bank_fingerprint", None)
    result.pop("reference_bank_character_id", None)
    result.pop("responsive_backend_routing", None)
    return result


def generate_attempt(
    *,
    engine: TTSEngine,
    output: Path,
    mode: Mapping[str, Any],
    target: Mapping[str, Any],
    voice: Mapping[str, Any],
    reference: Mapping[str, Any],
    kind: str,
    seed: int,
) -> dict[str, Any]:
    identifier = candidate_identifier(
        mode_id=str(mode["mode_id"]),
        kind=kind,
        reference_sha256=str(reference["audio_sha256"]),
        seed=seed,
    )
    audio = output / "private" / "audio" / f"{identifier}.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    config = {str(mode["speaker"]): candidate_voice(voice, reference, seed=seed)}
    success = engine.generate_voice(
        str(target.get("text") or ""),
        str(target.get("instruct") or ""),
        str(mode["speaker"]),
        config,
        str(audio),
    )
    if not success or not audio.is_file():
        raise QuasiEmotiveRoundError(
            f"Generation failed for {mode['mode_id']} {kind} seed {seed}."
        )
    return {
        "candidate_id": identifier,
        "mode_id": mode["mode_id"],
        "candidate_kind": kind,
        "speaker": mode["speaker"],
        "target_chunk_id": target.get("id"),
        "text": str(target.get("text") or ""),
        "instruct": str(target.get("instruct") or ""),
        "reference_chunk_id": reference.get("chunk_id"),
        "reference_candidate_id": reference.get("candidate_id"),
        "reference_audio_sha256": reference["audio_sha256"],
        "reference_text": reference["reference_text"],
        "seed": seed,
        "audio_path": str(audio),
        "audio_relative": f"private/audio/{audio.name}",
        "audio": audio_record(audio),
    }


def transcription_result(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status = model_cache_status("mlx_whisper_base")
    return evaluate_transcriptions(
        {
            "model_status": status,
            "outputs": [
                {
                    "sample_id": row["candidate_id"],
                    "path": row["audio_path"],
                    "text": row["text"],
                }
                for row in rows
            ],
        }
    )


def attach_transcriptions(
    rows: list[dict[str, Any]],
    evaluation: Mapping[str, Any],
) -> None:
    measurements = evaluation.get("measurements")
    if not isinstance(measurements, Mapping):
        measurements = {}
    for row in rows:
        result = measurements.get(row["candidate_id"])
        normalized = copy.deepcopy(result) if isinstance(result, Mapping) else {}
        transcript = normalized.get("transcript")
        if isinstance(transcript, str):
            adjusted = alias_adjusted_word_error_rate(
                mode_id=str(row["mode_id"]),
                expected=str(row["text"]),
                transcript=transcript,
            )
            if adjusted is not None:
                normalized["raw_word_error_rate"] = normalized.get(
                    "word_error_rate"
                )
                normalized["word_error_rate"] = adjusted
                normalized["alias_policy_applied"] = row["mode_id"]
        row["transcription"] = normalized


def _replace_phrases(
    words: list[str],
    aliases: Mapping[tuple[str, ...], tuple[str, ...]],
) -> list[str]:
    if not aliases:
        return words
    result: list[str] = []
    index = 0
    ordered = sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True)
    while index < len(words):
        for source, target in ordered:
            if tuple(words[index : index + len(source)]) == source:
                result.extend(target)
                index += len(source)
                break
        else:
            result.append(words[index])
            index += 1
    return result


def _word_error_rate_from_words(reference: list[str], hypothesis: list[str]) -> float:
    if not reference:
        return 0.0 if not hypothesis else 1.0
    previous = list(range(len(hypothesis) + 1))
    for row_index, expected in enumerate(reference, start=1):
        current = [row_index]
        for column, heard in enumerate(hypothesis, start=1):
            substitution = previous[column - 1] + (expected != heard)
            deletion = previous[column] + 1
            insertion = current[column - 1] + 1
            current.append(min(substitution, deletion, insertion))
        previous = current
    return previous[-1] / len(reference)


def alias_adjusted_word_error_rate(
    *,
    mode_id: str,
    expected: str,
    transcript: str,
) -> float | None:
    policy = TRANSCRIPTION_ALIAS_POLICY.get(mode_id)
    if policy is None:
        return None
    token_aliases = dict(policy.get("token_aliases") or {})
    phrase_aliases = dict(policy.get("phrase_aliases") or {})
    expected_words = normalized_words(expected)
    heard_words = [token_aliases.get(word, word) for word in normalized_words(transcript)]
    heard_words = _replace_phrases(heard_words, phrase_aliases)
    return _word_error_rate_from_words(expected_words, heard_words)


def transcription_passed(row: Mapping[str, Any]) -> bool:
    result = row.get("transcription")
    if not isinstance(result, Mapping):
        return False
    wer = result.get("word_error_rate")
    return isinstance(wer, (int, float)) and not isinstance(wer, bool) and wer <= MAX_ACCEPTABLE_WER


def review_page(
    output: Path,
    modes: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    omissions: list[dict[str, Any]],
) -> None:
    review = output / "review"
    review.mkdir(parents=True, exist_ok=True)
    by_mode: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        by_mode.setdefault(row["mode_id"], []).append(row)
    randomizer = random.Random(20260731)
    public_modes = []
    for mode in modes:
        rows = list(by_mode.get(mode["mode_id"], []))
        randomizer.shuffle(rows)
        public_modes.append(
            {
                "mode_id": mode["mode_id"],
                "title": mode["title"],
                "instruction": mode["review_instruction"],
                "speaker": mode["speaker"],
                "text": mode["target_text"],
                "delivery_direction": mode["target_instruct"],
                "comparison": len(rows) > 1,
                "candidates": [
                    {
                        "candidate_id": row["candidate_id"],
                        "display_id": f"{index:02d}",
                        "audio": "../" + row["audio_relative"],
                    }
                    for index, row in enumerate(rows, start=1)
                ],
            }
        )
    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "modes": public_modes,
        "objective_omission_count": len(omissions),
    }
    (review / "data.js").write_text(
        "window.ALEXANDRIA_NONCORE_QUASI_EMOTIVE = "
        + json.dumps(public, indent=2, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )
    (review / "index.html").write_text(
        """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Non-core Voice refinement</title><link rel='icon' href='data:,'><style>:root{font-family:Inter,system-ui,sans-serif;color:#29251f;background:#f1eee7}*{box-sizing:border-box}body{margin:0}header,main{max-width:1020px;margin:auto;padding:28px 20px}header{border-bottom:1px solid #d4ccbf}h1,h2,h3{font-family:Georgia,serif}.mode{margin:34px 0}.candidate{background:#fffdf8;border:1px solid #d5cdbf;border-radius:10px;padding:18px;margin:16px 0}.meta{font-size:13px;color:#6d655b}.ratings{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:12px 0}label{display:grid;gap:5px;font-size:13px;font-weight:650}select,textarea{font:inherit;padding:8px;border:1px solid #b9afa2;border-radius:5px;background:white}textarea{width:100%;min-height:72px}.decision{display:flex;gap:14px;flex-wrap:wrap;margin:12px 0}.decision label{display:flex;align-items:center;gap:7px;border:1px solid #b9afa2;border-radius:6px;padding:8px;background:white}audio{width:100%}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}.line{border-left:3px solid #c9bda9;padding-left:14px}@media(max-width:760px){.ratings{grid-template-columns:1fr 1fr}}@media(max-width:480px){.ratings{grid-template-columns:1fr}}</style></head><body><header><p>Alexandria bounded Voice refinement</p><h1>Non-core quasi-emotive acceptance</h1><p>Backend and reference identities are hidden. Score each output for identity, delivery, naturalness, and intelligibility. For two-candidate modes, pass only the stronger candidate if one is clearly better.</p><button id='export'>Export review</button> <span id='progress'></span></header><main id='app'></main><script src='data.js'></script><script src='app.js'></script></body></html>""",
        encoding="utf-8",
    )
    (review / "app.js").write_text(
        """(()=>{'use strict';const d=window.ALEXANDRIA_NONCORE_QUASI_EMOTIVE,key='alexandria-noncore-quasi:'+d.round_id;let saved={};try{saved=JSON.parse(localStorage.getItem(key)||'{}')}catch(_){saved={}}const app=document.querySelector('#app'),scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join('');for(const m of d.modes){const section=document.createElement('section');section.className='mode';section.innerHTML=`<h2>${m.title}</h2><p>${m.instruction}</p><div class="line"><p><strong>Line:</strong> ${m.text}</p><p class="meta"><strong>Direction:</strong> ${m.delivery_direction}</p></div>`;app.appendChild(section);if(!m.candidates.length){section.insertAdjacentHTML('beforeend','<p>No candidate survived objective text screening.</p>');continue}for(const c of m.candidates){const x=saved[c.candidate_id]||{},el=document.createElement('article');el.className='candidate';el.innerHTML=`<p class="meta">Candidate ${c.display_id}</p><audio controls preload="none" src="${c.audio}"></audio><div class="ratings"><label>Identity<select data-id="${c.candidate_id}" data-name="identity">${scale}</select></label><label>Delivery fit<select data-id="${c.candidate_id}" data-name="delivery">${scale}</select></label><label>Naturalness<select data-id="${c.candidate_id}" data-name="naturalness">${scale}</select></label><label>Intelligibility<select data-id="${c.candidate_id}" data-name="intelligibility">${scale}</select></label></div><div class="decision"><label><input type="radio" name="decision-${c.candidate_id}" value="pass" data-id="${c.candidate_id}" data-name="decision">Pass</label><label><input type="radio" name="decision-${c.candidate_id}" value="fail" data-id="${c.candidate_id}" data-name="decision">Fail</label></div><label>Notes<textarea data-id="${c.candidate_id}" data-name="notes"></textarea></label>`;section.appendChild(el)}}for(const e of document.querySelectorAll('[data-id]')){const x=saved[e.dataset.id]||{},v=x[e.dataset.name];if(e.type==='radio')e.checked=v===e.value;else if(v!=null)e.value=v;e.addEventListener(e.tagName==='TEXTAREA'?'input':'change',event=>{const t=event.target,id=t.dataset.id,n=t.dataset.name;saved[id]=saved[id]||{};saved[id][n]=t.type==='radio'?t.value:t.value;localStorage.setItem(key,JSON.stringify(saved));progress()})}function progress(){const total=d.modes.reduce((n,m)=>n+m.candidates.length,0),done=Object.values(saved).filter(x=>x.identity&&x.delivery&&x.naturalness&&x.intelligibility&&x.decision).length;document.querySelector('#progress').textContent=`${done} of ${total} reviewed`}document.querySelector('#export').onclick=()=>{const payload={schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),results:saved};const a=document.createElement('a'),b=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();""",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    project = args.project_root.expanduser().resolve()
    config_path = args.config_path.expanduser().resolve()
    output = args.output_root.expanduser().resolve()
    if output.exists():
        if not args.replace:
            raise QuasiEmotiveRoundError(
                f"Output already exists; pass --replace to rebuild: {output}"
            )
        shutil.rmtree(output)
    output.mkdir(parents=True)

    chunks = read_json(project / "chunks.json", "Project chunks")
    voice_config = read_json(project / "voice_config.json", "Voice configuration")
    config = read_json(config_path, "Alexandria configuration") if config_path.is_file() else {}
    if not isinstance(chunks, list) or not isinstance(voice_config, Mapping):
        raise QuasiEmotiveRoundError("Project chunks or Voice configuration is invalid.")
    by_id = {
        item.get("id", index): item
        for index, item in enumerate(chunks)
        if isinstance(item, Mapping)
    }

    prepared_modes: list[dict[str, Any]] = []
    candidate_specs: list[dict[str, Any]] = []
    for raw_mode in MODE_SPECS:
        mode = dict(raw_mode)
        target = by_id.get(mode["target_chunk_id"])
        reference_chunk = by_id.get(mode["reference_chunk_id"])
        voice = voice_config.get(mode["speaker"])
        if not isinstance(target, Mapping) or not isinstance(reference_chunk, Mapping):
            raise QuasiEmotiveRoundError(f"Mode chunk is missing: {mode['mode_id']}")
        if target.get("speaker") != mode["speaker"]:
            raise QuasiEmotiveRoundError(f"Target speaker changed: {mode['mode_id']}")
        if target.get("approved_audio_lock"):
            raise QuasiEmotiveRoundError(f"Target is already approved audio: {mode['mode_id']}")
        if not isinstance(voice, Mapping):
            raise QuasiEmotiveRoundError(f"Voice is missing: {mode['speaker']}")
        specialist = locked_reference(reference_chunk)
        identity = current_identity_reference(project, voice)
        mode.update(
            {
                "target_text": str(target.get("text") or ""),
                "target_instruct": str(target.get("instruct") or ""),
                "reference_candidate_id": specialist["candidate_id"],
            }
        )
        prepared_modes.append(mode)
        if mode["compare_identity_baseline"] and identity["audio_sha256"] != specialist["audio_sha256"]:
            candidate_specs.append(
                {
                    "mode": mode,
                    "target": target,
                    "voice": voice,
                    "reference": identity,
                    "kind": "identity_baseline",
                }
            )
        candidate_specs.append(
            {
                "mode": mode,
                "target": target,
                "voice": voice,
                "reference": specialist,
                "kind": "specialist_reference",
            }
        )

    engine = TTSEngine(config)
    attempts: list[dict[str, Any]] = []
    try:
        for spec in candidate_specs:
            attempts.append(
                generate_attempt(
                    engine=engine,
                    output=output,
                    seed=PRIMARY_SEED,
                    **spec,
                )
            )
        first_eval = transcription_result(attempts)
        attach_transcriptions(attempts, first_eval)
        retry_specs = [
            spec
            for spec, row in zip(candidate_specs, attempts, strict=True)
            if not transcription_passed(row)
        ]
        retries = [
            generate_attempt(
                engine=engine,
                output=output,
                seed=RETRY_SEED,
                **spec,
            )
            for spec in retry_specs
        ]
        if retries:
            retry_eval = transcription_result(retries)
            attach_transcriptions(retries, retry_eval)
        else:
            retry_eval = None
    finally:
        close = getattr(engine, "close", None)
        if callable(close):
            close()

    retry_by_spec = {
        (row["mode_id"], row["candidate_kind"]): row for row in retries
    }
    accepted: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    for row in attempts:
        options = [row]
        retry = retry_by_spec.get((row["mode_id"], row["candidate_kind"]))
        if retry is not None:
            options.append(retry)
        valid = [item for item in options if transcription_passed(item)]
        if not valid:
            omissions.append(
                {
                    "mode_id": row["mode_id"],
                    "candidate_kind": row["candidate_kind"],
                    "reason": "transcription_gate_failed",
                    "attempts": [
                        {
                            "candidate_id": item["candidate_id"],
                            "seed": item["seed"],
                            "transcription": item["transcription"],
                        }
                        for item in options
                    ],
                }
            )
            continue
        chosen = min(
            valid,
            key=lambda item: float(item["transcription"]["word_error_rate"]),
        )
        accepted.append(chosen)

    answer = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "generated_at_utc": utc_now(),
        "project_root": str(project),
        "config_path": str(config_path),
        "mode_count": len(prepared_modes),
        "candidate_count": len(accepted),
        "objective_omission_count": len(omissions),
        "max_acceptable_word_error_rate": MAX_ACCEPTABLE_WER,
        "modes": prepared_modes,
        "candidates": {row["candidate_id"]: row for row in accepted},
        "omissions": omissions,
        "primary_transcription_evaluation": first_eval,
        "retry_transcription_evaluation": retry_eval,
        "production_routing_changed": False,
        "project_audio_changed": False,
        "voice_config_changed": False,
    }
    write_json(output / "private" / "answer-key.json", answer)
    write_json(
        output / "generation-summary.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "generated_at_utc": answer["generated_at_utc"],
            "mode_count": len(prepared_modes),
            "candidate_count": len(accepted),
            "objective_omission_count": len(omissions),
            "comparison_mode_count": sum(
                len([row for row in accepted if row["mode_id"] == mode["mode_id"]]) > 1
                for mode in prepared_modes
            ),
            "single_candidate_mode_count": sum(
                len([row for row in accepted if row["mode_id"] == mode["mode_id"]]) == 1
                for mode in prepared_modes
            ),
            "all_retained_candidates_passed_transcription_gate": all(
                transcription_passed(row) for row in accepted
            ),
            "production_routing_changed": False,
            "project_audio_changed": False,
            "voice_config_changed": False,
        },
    )
    review_page(output, prepared_modes, accepted, omissions)
    print(
        json.dumps(
            {
                "round_id": ROUND_ID,
                "output": str(output),
                "review": str(output / "review" / "index.html"),
                "mode_count": len(prepared_modes),
                "candidate_count": len(accepted),
                "objective_omission_count": len(omissions),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
