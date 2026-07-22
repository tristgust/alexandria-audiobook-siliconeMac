#!/usr/bin/env python3
"""Evaluate emotional-clone outputs and build a usable blinded review page.

The manifest supplies already-generated local audio. This tool performs no model
or package downloads. It runs Alexandria's pinned speaker and transcription
evaluators in isolated workers, exposes the exact expected line and automatic
transcript for every sample, and writes a separate answer key.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
BENCHMARKS = ROOT / "benchmarks"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

from hf_access import cached_snapshot_status  # noqa: E402
from model_registry import model_spec  # noqa: E402
from run_expressive_clone_matrix import _invoke_worker  # noqa: E402
from transcription_evaluator import EVALUATOR_MODEL_KEY  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--worker-timeout", type=int, default=1800)
    return parser.parse_args()


def _status(model_key: str) -> dict[str, Any]:
    spec = model_spec(model_key)
    return cached_snapshot_status(
        spec.repo_id,
        revision=spec.revision,
        required_paths=spec.required_paths,
    )


def _review_id(sample_id: str) -> str:
    return hashlib.sha256(f"emotional-clone-review\0{sample_id}".encode()).hexdigest()[:16]


def _link_audio(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        target.unlink()
    try:
        os.symlink(source, target)
    except OSError:
        shutil.copy2(source, target)


def _render_html(rows: list[dict[str, Any]]) -> str:
    review_key = hashlib.sha256(
        "\0".join(str(row["sample_id"]) for row in rows).encode("utf-8")
    ).hexdigest()[:16]
    cards = []
    for index, row in enumerate(rows, start=1):
        transcript = row.get("automatic_transcript")
        expected_text = str(row.get("expected_text") or "")
        identity_label = html.escape(
            str(row.get("identity_label") or "Narrator identity")
        )
        transcript_html = (
            html.escape(str(transcript))
            if transcript is not None
            else "Automatic transcription unavailable"
        )
        wer = row.get("word_error_rate")
        wer_html = "—" if wer is None else f"{float(wer):.3f}"
        direction = html.escape(str(row["requested_direction"]))
        sample_id = html.escape(str(row["sample_id"]))
        file_name = html.escape(str(row["file"]))
        cards.append(
            f"""
<section class="sample" data-sample-id="{sample_id}">
  <h2>{index}. {direction}</h2>
  <audio controls preload="none" src="{file_name}"></audio>
  <dl>
    <dt>Expected line</dt><dd>{html.escape(expected_text)}</dd>
    <dt>Automatic transcript</dt><dd>{transcript_html}</dd>
    <dt>Word error rate</dt><dd>{wer_html}</dd>
  </dl>
  <div class="ratings">
    <label>{identity_label} <input type="number" min="1" max="5" data-field="narrator_identity_1_to_5"></label>
    <label>Delivery adherence <input type="number" min="1" max="5" data-field="delivery_adherence_1_to_5"></label>
    <label>Naturalness <input type="number" min="1" max="5" data-field="naturalness_1_to_5"></label>
    <label>Artifact severity <input type="number" min="1" max="5" data-field="artifact_severity_1_to_5"></label>
    <label>Spoken text matches <select data-field="spoken_text_matches_expected"><option value=""></option><option value="true">Yes</option><option value="false">No</option></select></label>
    <label>Approve comparison <select data-field="approve_for_candidate_comparison"><option value=""></option><option value="true">Yes</option><option value="false">No</option></select></label>
  </div>
  <label>Notes<textarea data-field="notes"></textarea></label>
</section>"""
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alexandria emotional clone review</title>
<style>
body {{ font: 16px/1.45 system-ui, sans-serif; max-width: 980px; margin: 0 auto; padding: 32px; background: #f5f3ee; color: #25231f; }}
h1 {{ margin-bottom: 8px; }}
.notice {{ padding: 14px 16px; border: 1px solid #b9b3a7; background: #fffdf8; border-radius: 8px; }}
.sample {{ margin: 24px 0; padding: 20px; background: white; border: 1px solid #d9d3c7; border-radius: 10px; }}
audio {{ width: 100%; }}
dl {{ display: grid; grid-template-columns: 170px 1fr; gap: 6px 14px; }}
dt {{ font-weight: 700; }} dd {{ margin: 0; }}
.ratings {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(220px,1fr)); gap: 12px; margin: 18px 0; }}
label {{ display: grid; gap: 5px; font-weight: 600; }}
input, select, textarea, button {{ font: inherit; }}
textarea {{ min-height: 72px; }}
button {{ padding: 10px 14px; }}
.review-toolbar {{ position: sticky; top: 0; z-index: 10; display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 12px 0; background: #f5f3ee; }}
.sample[data-complete="false"] {{ border-left: 4px solid #a55f2a; }}
.sample[data-complete="true"] {{ border-left: 4px solid #52765a; }}
#save-status {{ color: #666; }}
</style>
</head>
<body>
<h1>Emotional clone listening review</h1>
<p class="notice"><strong>Blinded candidate review.</strong> The requested direction and exact expected line are visible. Candidate identities are only in the separate answer key. Automatic transcripts are evidence, not substitutes for listening.</p>
<div class="review-toolbar">
  <button id="save" type="button">Save now</button>
  <button id="next-incomplete" type="button">Next incomplete</button>
  <button id="export" type="button">Export completed JSON</button>
  <strong id="completion" aria-live="polite">0/{len(rows)} complete</strong>
  <span id="save-status" role="status" aria-live="polite"></span>
</div>
{''.join(cards)}
<script>
const storageKey = 'alexandria-emotional-clone-review-{review_key}';
const fields = [...document.querySelectorAll('[data-field]')];
const requiredFields = [
  'narrator_identity_1_to_5',
  'delivery_adherence_1_to_5',
  'naturalness_1_to_5',
  'artifact_severity_1_to_5',
  'spoken_text_matches_expected',
  'approve_for_candidate_comparison'
];
function collect() {{
  return [...document.querySelectorAll('.sample')].map(section => {{
    const row = {{sample_id: section.dataset.sampleId}};
    section.querySelectorAll('[data-field]').forEach(el => {{
      let value = el.value;
      if (value === 'true') value = true;
      else if (value === 'false') value = false;
      else if (el.type === 'number' && value !== '') value = Number(value);
      row[el.dataset.field] = value === '' ? null : value;
    }});
    return row;
  }});
}}
function rowComplete(row) {{
  return requiredFields.every(field => row[field] !== null && row[field] !== undefined);
}}
function updateCompletion(rows = collect()) {{
  const byId = Object.fromEntries(rows.map(row => [row.sample_id, row]));
  let complete = 0;
  document.querySelectorAll('.sample').forEach(section => {{
    const done = rowComplete(byId[section.dataset.sampleId] || {{}});
    section.dataset.complete = String(done);
    if (done) complete += 1;
  }});
  document.getElementById('completion').textContent = `${{complete}}/${{rows.length}} complete`;
}}
function persist() {{
  const rows = collect();
  localStorage.setItem(storageKey, JSON.stringify(rows));
  updateCompletion(rows);
  const status = document.getElementById('save-status');
  status.textContent = 'Saved';
  window.clearTimeout(persist.statusTimer);
  persist.statusTimer = window.setTimeout(() => status.textContent = '', 1200);
}}
function restore() {{
  const saved = JSON.parse(localStorage.getItem(storageKey) || '[]');
  const byId = Object.fromEntries(saved.map(row => [row.sample_id, row]));
  document.querySelectorAll('.sample').forEach(section => {{
    const row = byId[section.dataset.sampleId] || {{}};
    section.querySelectorAll('[data-field]').forEach(el => {{
      const value = row[el.dataset.field];
      if (value !== undefined && value !== null) el.value = String(value);
    }});
  }});
  updateCompletion();
}}
function firstIncompleteControl() {{
  const rows = collect();
  const byId = Object.fromEntries(rows.map(row => [row.sample_id, row]));
  for (const section of document.querySelectorAll('.sample')) {{
    const row = byId[section.dataset.sampleId] || {{}};
    if (!rowComplete(row)) {{
      const field = requiredFields.find(name => row[name] === null || row[name] === undefined);
      return section.querySelector(`[data-field="${{field}}"]`);
    }}
  }}
  return null;
}}
document.getElementById('save').onclick = persist;
document.getElementById('next-incomplete').onclick = () => {{
  const control = firstIncompleteControl();
  if (control) {{ control.scrollIntoView({{behavior: 'smooth', block: 'center'}}); control.focus(); }}
}};
document.getElementById('export').onclick = () => {{
  const rows = collect();
  const incomplete = rows.filter(row => !rowComplete(row));
  if (incomplete.length) {{
    updateCompletion(rows);
    const control = firstIncompleteControl();
    if (control) {{ control.scrollIntoView({{behavior: 'smooth', block: 'center'}}); control.focus(); }}
    alert(`Complete all required fields before export. ${{incomplete.length}} sample(s) remain.`);
    return;
  }}
  persist();
  const blob = new Blob([JSON.stringify(rows, null, 2)], {{type: 'application/json'}});
  const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'alexandria_emotional_clone_scores.json'; link.click(); URL.revokeObjectURL(link.href);
}};
fields.forEach(el => {{
  el.addEventListener('input', persist);
  el.addEventListener('change', persist);
}});
restore();
</script>
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    default_expected_text = str(manifest.get("expected_text") or "").strip()
    identity_label = str(manifest.get("identity_label") or "Narrator identity").strip()
    reference_audio_value = str(manifest.get("reference_audio") or "").strip()
    reference_audio = (
        Path(reference_audio_value).expanduser().resolve()
        if reference_audio_value
        else None
    )
    samples = list(manifest.get("samples") or [])
    if reference_audio is not None and not reference_audio.is_file():
        raise FileNotFoundError(reference_audio)
    if not samples:
        raise ValueError("Manifest samples are required.")

    output_records = []
    normalized = []
    seen = set()
    for item in samples:
        sample_id = str(item.get("sample_id") or "").strip()
        audio_path = Path(str(item.get("path") or "")).expanduser().resolve()
        sample_reference_value = str(item.get("reference_audio") or "").strip()
        sample_reference = (
            Path(sample_reference_value).expanduser().resolve()
            if sample_reference_value
            else reference_audio
        )
        expected_text = str(item.get("expected_text") or default_expected_text).strip()
        if not sample_id or sample_id in seen:
            raise ValueError(f"Duplicate or empty sample_id: {sample_id!r}")
        if not expected_text:
            raise ValueError(f"Expected text is required for sample {sample_id!r}.")
        if sample_reference is None or not sample_reference.is_file():
            raise FileNotFoundError(sample_reference or "missing sample reference audio")
        if not audio_path.is_file():
            raise FileNotFoundError(audio_path)
        seen.add(sample_id)
        normalized.append(
            {
                **item,
                "sample_id": sample_id,
                "path": str(audio_path),
                "reference_audio": str(sample_reference),
                "expected_text": expected_text,
            }
        )
        output_records.append(
            {"sample_id": sample_id, "path": str(audio_path), "text": expected_text}
        )

    speaker_groups: dict[str, list[dict[str, Any]]] = {}
    for item, output in zip(normalized, output_records, strict=True):
        speaker_groups.setdefault(item["reference_audio"], []).append(output)
    speaker_results = []
    for group_reference, group_outputs in speaker_groups.items():
        speaker_results.append(
            _invoke_worker(
                "speaker",
                {
                    "model_status": _status("mlx_clone"),
                    "reference_audio": group_reference,
                    "outputs": group_outputs,
                },
                timeout=args.worker_timeout,
            )
        )
    speaker_measurements: dict[str, Any] = {}
    for result in speaker_results:
        overlap = set(speaker_measurements) & set(result.get("measurements", {}))
        if overlap:
            raise ValueError(f"Duplicate speaker measurements: {sorted(overlap)}")
        speaker_measurements.update(result.get("measurements", {}))
    first_speaker = speaker_results[0]
    speaker = {
        **{key: value for key, value in first_speaker.items() if key != "measurements"},
        "available": all(result.get("available") for result in speaker_results),
        "complete": all(
            len(result.get("measurements", {})) == len(group_outputs)
            for result, group_outputs in zip(
                speaker_results, speaker_groups.values(), strict=True
            )
        ),
        "reference_group_count": len(speaker_groups),
        "measurements": speaker_measurements,
        "worker_exit_codes": [result.get("worker_exit_code") for result in speaker_results],
    }
    transcription = _invoke_worker(
        "transcription",
        {
            "model_status": _status(EVALUATOR_MODEL_KEY),
            "text": default_expected_text,
            "outputs": output_records,
        },
        timeout=args.worker_timeout,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = output_dir / "audio"
    review_rows = []
    answer_key = []
    for item in sorted(normalized, key=lambda row: _review_id(row["sample_id"])):
        blind_id = _review_id(item["sample_id"])
        source = Path(item["path"])
        target = audio_dir / f"sample_{blind_id}{source.suffix.lower() or '.wav'}"
        _link_audio(source, target)
        automatic = transcription.get("measurements", {}).get(item["sample_id"], {})
        identity = speaker.get("measurements", {}).get(item["sample_id"], {})
        review_rows.append(
            {
                "sample_id": blind_id,
                "file": str(Path("audio") / target.name),
                "requested_direction": item.get("direction"),
                "expected_text": item["expected_text"],
                "identity_label": str(item.get("identity_label") or identity_label),
                "automatic_transcription_status": (
                    "available" if "transcript" in automatic else "unavailable"
                ),
                "automatic_transcript": automatic.get("transcript"),
                "word_error_rate": automatic.get("word_error_rate"),
                "speaker_cosine_to_narrator_reference": identity.get(
                    "speaker_cosine_to_primary_reference"
                ),
                "spoken_text_matches_expected": None,
                "narrator_identity_1_to_5": None,
                "delivery_adherence_1_to_5": None,
                "naturalness_1_to_5": None,
                "artifact_severity_1_to_5": None,
                "approve_for_candidate_comparison": None,
                "notes": "",
            }
        )
        answer_key.append(
            {
                "sample_id": blind_id,
                "source_sample_id": item["sample_id"],
                "candidate": item.get("candidate"),
                "direction": item.get("direction"),
                "seed": item.get("seed"),
                "source_path": item["path"],
            }
        )

    evaluation = {
        "schema_version": 1,
        "reference_audio_sha256": (
            hashlib.sha256(reference_audio.read_bytes()).hexdigest()
            if reference_audio is not None and len(speaker_groups) == 1
            else None
        ),
        "reference_audio_sha256_by_sample": {
            item["sample_id"]: hashlib.sha256(
                Path(item["reference_audio"]).read_bytes()
            ).hexdigest()
            for item in normalized
        },
        "expected_text_sha256": (
            hashlib.sha256(default_expected_text.encode()).hexdigest()
            if default_expected_text
            else None
        ),
        "expected_text_sha256_by_sample": {
            item["sample_id"]: hashlib.sha256(item["expected_text"].encode()).hexdigest()
            for item in normalized
        },
        "sample_count": len(review_rows),
        "speaker_evaluation": speaker,
        "transcription_evaluation": transcription,
        "production_promotion_allowed": False,
        "manual_blinded_review_required": True,
    }
    (output_dir / "evaluation.json").write_text(json.dumps(evaluation, indent=2) + "\n")
    (output_dir / "listening_review.json").write_text(json.dumps(review_rows, indent=2) + "\n")
    (output_dir / "answer_key.json").write_text(json.dumps(answer_key, indent=2) + "\n")
    (output_dir / "review.html").write_text(_render_html(review_rows))
    print(
        json.dumps(
            {
                "sample_count": len(review_rows),
                "transcription_complete": transcription.get("complete"),
                "transcription_failures": transcription.get("failure_count"),
                "speaker_available": speaker.get("available"),
                "review_html": str(output_dir / "review.html"),
                "answer_key": str(output_dir / "answer_key.json"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
