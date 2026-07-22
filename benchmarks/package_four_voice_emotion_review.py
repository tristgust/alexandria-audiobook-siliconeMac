#!/usr/bin/env python3
"""Package a durable emotion-centric five-lane listening review.

Each emotion page contains five hidden synthesis lanes:
- direct non-cloned Qwen Ryan control;
- IndexTTS2 generic Ryan upper-bound transfer;
- IndexTTS2 Narrator;
- IndexTTS2 Benny;
- IndexTTS2 Doctor.

Expected identity remains visible so identity can be judged. Candidate type is
kept only in the separate answer key. All audio is copied into the repository
review folder so no temporary or external cache path is required to listen.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = (
    ROOT / ".omo" / "evidence" / "b17-t05-four-voice-emotion-matrix"
)
STYLES = [
    "neutral",
    "sad",
    "fear",
    "angry",
    "happy",
    "excited",
    "friendly",
    "surprised",
    "whisper",
    "shout",
    "disgust",
    "contempt",
    "grief",
    "panic",
    "relief",
    "tender",
    "pleading",
    "sarcastic",
    "calm",
    "urgent",
    "exhausted",
    "authoritative",
]
LANES = ["qwen_direct", "generic_ryan", "narrator", "benny", "doctor"]
IDENTITY_REFERENCES = {
    "Narrator": ROOT / "clone_voices" / "narratorvoicelines_-_01_1784553553.mp3",
    "Benny": ROOT / "clone_voices" / "bennyvoice1_1784053953.mp3",
    "Doctor": ROOT / "clone_voices" / "dw7voice1_1784300409.mp3",
    "Ryan": DEFAULT_EVIDENCE / "qwen-control" / "audio" / "ryan_neutral.wav",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def blind_id(style: str, lane: str, source_sample_id: str) -> str:
    value = f"four-voice-emotion-review\0{style}\0{lane}\0{source_sample_id}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def load_lane(evidence_root: Path, lane: str) -> list[dict[str, Any]]:
    manifest = json.loads(
        (evidence_root / "review-manifests" / f"{lane}.json").read_text(
            encoding="utf-8"
        )
    )
    evaluation = json.loads(
        (evidence_root / "lane-evaluations" / lane / "evaluation.json").read_text(
            encoding="utf-8"
        )
    )
    trans = evaluation["transcription_evaluation"]["measurements"]
    speaker = evaluation["speaker_evaluation"]["measurements"]
    rows = []
    for item in manifest["samples"]:
        sid = item["sample_id"]
        automatic = trans.get(sid, {})
        identity = speaker.get(sid, {})
        rows.append(
            {
                **item,
                "lane": lane,
                "automatic_transcript": automatic.get("transcript"),
                "word_error_rate": automatic.get("word_error_rate"),
                "speaker_cosine": identity.get("speaker_cosine_to_primary_reference"),
            }
        )
    return rows


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def reference_markup() -> str:
    items = []
    for identity in ["Narrator", "Benny", "Doctor", "Ryan"]:
        source = IDENTITY_REFERENCES[identity]
        suffix = source.suffix.lower() or ".wav"
        file_name = f"{identity.lower()}{suffix}"
        items.append(
            f"""
<div class="reference">
  <strong>{html.escape(identity)}</strong>
  <audio controls preload="none" src="../../references/{html.escape(file_name)}"></audio>
</div>"""
        )
    return "".join(items)


def render_page(style: str, rows: list[dict[str, Any]]) -> str:
    storage_key = f"alexandria-five-lane-emotion-review-{style}"
    cards = []
    for index, row in enumerate(rows, start=1):
        transcript = row.get("automatic_transcript")
        transcript_text = (
            html.escape(str(transcript))
            if transcript is not None
            else "Automatic transcription unavailable"
        )
        wer = row.get("word_error_rate")
        wer_text = "—" if wer is None else f"{float(wer):.3f}"
        cards.append(
            f"""
<section class="sample" data-sample-id="{html.escape(row['blind_id'])}">
  <div class="sample-heading">
    <h2>{index}. Expected identity: {html.escape(row['expected_identity'])}</h2>
    <span>{html.escape(style.title())}</span>
  </div>
  <audio controls preload="none" src="audio/{html.escape(row['review_file'])}"></audio>
  <dl>
    <dt>Expected line</dt><dd>{html.escape(row['expected_text'])}</dd>
    <dt>Automatic transcript</dt><dd>{transcript_text}</dd>
    <dt>Word error rate</dt><dd>{wer_text}</dd>
  </dl>
  <div class="ratings">
    <label>Identity match <input type="number" min="1" max="5" step="0.1" data-field="narrator_identity_1_to_5"></label>
    <label>Delivery adherence <input type="number" min="1" max="5" step="0.1" data-field="delivery_adherence_1_to_5"></label>
    <label>Naturalness <input type="number" min="1" max="5" step="0.1" data-field="naturalness_1_to_5"></label>
    <label>Artifact severity <input type="number" min="1" max="5" step="0.1" data-field="artifact_severity_1_to_5"></label>
    <label>Spoken text matches <select data-field="spoken_text_matches_expected"><option value=""></option><option value="true">Yes</option><option value="false">No</option></select></label>
    <label>Approve this result <select data-field="approve_for_candidate_comparison"><option value=""></option><option value="true">Yes</option><option value="false">No</option></select></label>
  </div>
  <label>Notes<textarea data-field="notes"></textarea></label>
</section>"""
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alexandria {html.escape(style.title())} five-lane review</title>
<style>
:root {{ color-scheme: light; }}
body {{ font: 16px/1.45 system-ui, sans-serif; max-width: 1040px; margin: 0 auto; padding: 32px; background: #f5f3ee; color: #25231f; }}
a {{ color: inherit; }}
header {{ margin-bottom: 22px; }}
.notice {{ padding: 14px 16px; border: 1px solid #b9b3a7; background: #fffdf8; border-radius: 8px; }}
.references {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(220px,1fr)); gap: 10px; margin: 18px 0 28px; }}
.reference {{ background: #fff; border: 1px solid #d9d3c7; border-radius: 8px; padding: 12px; }}
audio {{ width: 100%; margin-top: 8px; }}
.sample {{ margin: 22px 0; padding: 20px; background: white; border: 1px solid #d9d3c7; border-radius: 10px; }}
.sample-heading {{ display: flex; align-items: baseline; justify-content: space-between; gap: 16px; }}
.sample-heading h2 {{ margin: 0; font-size: 1.15rem; }}
.sample-heading span {{ font-weight: 700; color: #635f56; }}
dl {{ display: grid; grid-template-columns: 170px 1fr; gap: 6px 14px; }}
dt {{ font-weight: 700; }} dd {{ margin: 0; }}
.ratings {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(220px,1fr)); gap: 12px; margin: 18px 0; }}
label {{ display: grid; gap: 5px; font-weight: 600; }}
input, select, textarea, button {{ font: inherit; }}
textarea {{ min-height: 72px; }}
button {{ padding: 10px 14px; margin-right: 8px; }}
.actions {{ position: sticky; top: 0; padding: 10px 0; background: #f5f3ee; z-index: 2; }}
</style>
</head>
<body>
<header>
  <p><a href="../../index.html">← All emotions</a></p>
  <h1>{html.escape(style.title())} — five-lane review</h1>
  <p class="notice"><strong>Synthesis lane is hidden.</strong> Expected identity remains visible because identity cannot be scored blind. Compare against the four reference clips, then judge emotion, naturalness, artifacts, and exact text. Two samples expect Ryan: one is the direct non-cloned Qwen control and one is IndexTTS2 transferring the same performance into its generic Ryan identity.</p>
</header>
<section class="references">
{reference_markup()}
</section>
<div class="actions"><button id="save">Save in browser</button><button id="export">Export completed JSON</button></div>
{''.join(cards)}
<script>
const storageKey = {json.dumps(storage_key)};
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
function persist() {{ localStorage.setItem(storageKey, JSON.stringify(collect())); }}
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
}}
document.getElementById('save').onclick = () => {{ persist(); alert('Saved.'); }};
document.getElementById('export').onclick = () => {{
  const blob = new Blob([JSON.stringify(collect(), null, 2)], {{type: 'application/json'}});
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = {json.dumps(f'alexandria_five_lane_{style}_scores.json')};
  link.click();
  URL.revokeObjectURL(link.href);
}};
document.querySelectorAll('[data-field]').forEach(el => el.addEventListener('change', persist));
restore();
</script>
</body>
</html>
"""


def render_hub(page_info: dict[str, dict[str, Any]]) -> str:
    rows = []
    for style in STYLES:
        info = page_info[style]
        rows.append(
            f"<li><a href=\"pages/{style}/review.html\">{html.escape(style.title())}</a> — five samples, storage key <code>{html.escape(info['storage_key'])}</code></li>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alexandria durable five-lane emotion review</title>
<style>
body {{ font: 16px/1.5 system-ui, sans-serif; max-width: 900px; margin: 0 auto; padding: 36px; background: #f5f3ee; color: #25231f; }}
main {{ background: #fff; border: 1px solid #d9d3c7; border-radius: 12px; padding: 26px; }}
li {{ margin: 10px 0; }}
.notice {{ padding: 14px 16px; background: #fff8dc; border: 1px solid #c8b66b; border-radius: 8px; }}
code {{ font-size: .85em; }}
</style>
</head>
<body>
<main>
<h1>Alexandria durable five-lane emotion review</h1>
<p class="notice"><strong>Do not open answer keys yet.</strong> Complete and export every emotion page first. All audio and references are contained inside this repository folder; nothing depends on <code>/tmp</code>, model caches, or symlinks.</p>
<p>Each page compares direct non-cloned Qwen Ryan, IndexTTS2 generic Ryan, Narrator, Benny, and Doctor for the same line and emotion. Expected identity is visible; synthesis lane remains hidden.</p>
<ol>{''.join(rows)}</ol>
<p>Export {len(STYLES)} JSON files total.</p>
</main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    args = parser.parse_args()
    evidence_root = Path(args.evidence_root).expanduser().resolve()
    output_root = evidence_root / "review"
    pages_root = output_root / "pages"
    answers_root = output_root / "answer-keys"
    references_root = output_root / "references"
    if output_root.exists():
        shutil.rmtree(output_root)
    pages_root.mkdir(parents=True, exist_ok=True)
    answers_root.mkdir(parents=True, exist_ok=True)
    references_root.mkdir(parents=True, exist_ok=True)

    for identity, source in IDENTITY_REFERENCES.items():
        source = Path(source).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        suffix = source.suffix.lower() or ".wav"
        copy_file(source, references_root / f"{identity.lower()}{suffix}")

    all_rows = []
    for lane in LANES:
        all_rows.extend(load_lane(evidence_root, lane))
    expected_row_count = len(STYLES) * len(LANES)
    if len(all_rows) != expected_row_count:
        raise ValueError(
            f"Expected {expected_row_count} rows, got {len(all_rows)}"
        )

    page_info: dict[str, dict[str, Any]] = {}
    review_manifest_rows = []
    for style in STYLES:
        style_rows = [row for row in all_rows if row["direction"] == style]
        if len(style_rows) != 5:
            raise ValueError(f"Expected five rows for {style}, got {len(style_rows)}")
        prepared = []
        answer_key = []
        page_audio = pages_root / style / "audio"
        for row in style_rows:
            source = Path(row["path"]).expanduser().resolve()
            if not source.is_file():
                raise FileNotFoundError(source)
            bid = blind_id(style, row["lane"], row["sample_id"])
            suffix = source.suffix.lower() or ".wav"
            target_name = f"sample_{bid}{suffix}"
            target = page_audio / target_name
            copy_file(source, target)
            prepared_row = {
                **row,
                "blind_id": bid,
                "review_file": target_name,
            }
            prepared.append(prepared_row)
            answer_key.append(
                {
                    "sample_id": bid,
                    "source_sample_id": row["sample_id"],
                    "lane": row["lane"],
                    "candidate": row["candidate"],
                    "expected_identity": row["expected_identity"],
                    "direction": style,
                    "source_audio_sha256": sha256_file(source),
                }
            )
            review_manifest_rows.append(
                {
                    "style": style,
                    "blind_id": bid,
                    "lane": row["lane"],
                    "candidate": row["candidate"],
                    "expected_identity": row["expected_identity"],
                    "expected_text_sha256": hashlib.sha256(
                        row["expected_text"].encode("utf-8")
                    ).hexdigest(),
                    "word_error_rate": row["word_error_rate"],
                    "speaker_cosine": row["speaker_cosine"],
                    "audio_sha256": sha256_file(target),
                }
            )
        prepared.sort(key=lambda row: row["blind_id"])
        answer_key.sort(key=lambda row: row["sample_id"])
        page_path = pages_root / style / "review.html"
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(render_page(style, prepared), encoding="utf-8")
        answer_path = answers_root / f"{style}.json"
        answer_path.write_text(json.dumps(answer_key, indent=2) + "\n", encoding="utf-8")
        page_info[style] = {
            "sample_count": 5,
            "storage_key": f"alexandria-five-lane-emotion-review-{style}",
            "review_html_sha256": sha256_file(page_path),
            "answer_key_sha256": sha256_file(answer_path),
        }

    hub = output_root / "index.html"
    hub.write_text(render_hub(page_info), encoding="utf-8")
    alias = evidence_root / "review.html"
    alias.write_text(
        "<!doctype html><meta charset=\"utf-8\">"
        "<meta http-equiv=\"refresh\" content=\"0; url=review/index.html\">"
        "<title>Open Alexandria emotion review</title>"
        "<p><a href=\"review/index.html\">Open the durable five-lane emotion review</a></p>",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "purpose": "durable_five_lane_emotion_review",
        "page_count": len(STYLES),
        "sample_count": len(review_manifest_rows),
        "lanes": LANES,
        "styles": STYLES,
        "page_info": page_info,
        "samples": review_manifest_rows,
        "hub_sha256": sha256_file(hub),
        "root_alias_sha256": sha256_file(alias),
        "manual_blinded_review_required": True,
        "candidate_lane_hidden": True,
        "expected_identity_visible": True,
        "all_audio_copied_into_review_folder": True,
        "temporary_paths_required": False,
        "production_promotion_allowed": False,
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "review_hub": str(hub),
                "page_count": manifest["page_count"],
                "sample_count": manifest["sample_count"],
                "temporary_paths_required": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
