#!/usr/bin/env python3
"""Prepare and finalize the corrected, focused Alexandria Round 1 review.

The v1 evidence is preserved untouched. This tool creates a separate evidence
root containing only the identity lanes and delivery styles that materially
inform Alexandria's expressive-clone decision. Valid v1 audio is hard-linked
when possible. Fish S2 Pro and MOSS v1.5 are deliberately left pending so they
must be regenerated with the corrected sample-rate/channel handling.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path(
    "/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-78fc5814/"
    ".omo/evidence/b17-t05-multimodel-round1"
)
DEFAULT_DESTINATION = Path(
    "/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-78fc5814/"
    ".omo/evidence/b17-t05-multimodel-round1-v2-usable"
)
DEFAULT_RESULTS = Path("/Users/tristan/Downloads/alexandria_round1_cumulative_all(3).json")
EXPECTED_RESULTS_SHA256 = "9cbe494c9e3c727c4ac57bc72c5465ed511efafc0a057f3a4287955de93e7c26"
ROUND_ID = "alexandria_multimodel_expressive_clone_round1_v2_usable"
INVALID_V1_MODELS = {"fish_s2_pro", "moss_tts_local_v15"}
IDENTITIES = ("narrator", "benny", "doctor", "ryan_neutral", "ryan_acted")
STYLES = (
    "neutral",
    "happy",
    "tender",
    "grief",
    "panic",
    "angry",
    "menacing",
    "sarcastic",
    "whisper",
    "laughing",
)
REQUIRED_COMPLETE_FIELDS = (
    "identity_1_to_5",
    "delivery_1_to_5",
    "naturalness_1_to_5",
    "artifact_severity_1_to_5",
    "spoken_text_matches_expected",
    "requested_mode_is_clear",
    "approve_for_comparison",
)
FINAL_ASSET_FILES = (
    "index.html",
    "styles.css",
    "app.js",
    "review-core.js",
    "review-io.js",
    "review-content.js",
    "review-navigation.js",
)
REVIEW_AUDIO_TRIMS = {
    "r1_e2ad6dc4582eb5cac484": {
        "end_seconds": 7.0,
        "reason": "Keep the complete laughing line and a short laugh tail; remove the runaway continuation.",
    },
    "r1_272f5463403e809570f0": {
        "end_seconds": 5.25,
        "reason": "Keep the complete grief line and natural tail; remove the runaway continuation.",
    },
}
MAX_NORMAL_REVIEW_DURATION_SECONDS = 20.0


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(
            f"Expected exactly one review-asset patch target in {path.name}: {old[:80]!r}"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def wav_info(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as handle:
        sample_rate = int(handle.getframerate())
        channels = int(handle.getnchannels())
        frames = int(handle.getnframes())
    return {
        "sample_rate": sample_rate,
        "channels": channels,
        "frames": frames,
        "duration_seconds": frames / sample_rate if sample_rate else 0.0,
    }


def trim_review_audio(source: Path, target: Path, end_seconds: float) -> None:
    temporary = target.with_name(target.name + f".{os.getpid()}.partial.wav")
    temporary.unlink(missing_ok=True)
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-t",
            f"{float(end_seconds):.3f}",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(temporary),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not temporary.is_file():
        temporary.unlink(missing_ok=True)
        raise RuntimeError(completed.stderr[-2000:])
    info = wav_info(temporary)
    if info["sample_rate"] != 48000 or info["channels"] != 1:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Invalid trimmed MOSS review audio: {info}")
    os.replace(temporary, target)


def link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def copy_tree_linked(source: Path, target: Path) -> None:
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            link_or_copy(path, destination)


def filtered_groups(groups: dict[str, Any]) -> dict[str, Any]:
    selected = set(STYLES)
    result: dict[str, Any] = {}
    for key, group in groups.items():
        style_keys = [style for style in group.get("styles", []) if style in selected]
        if not style_keys:
            continue
        result[key] = {**group, "styles": style_keys}
    return result


def prepare(source: Path, destination: Path, force: bool) -> dict[str, Any]:
    if destination.exists():
        if not force:
            raise FileExistsError(f"Destination already exists: {destination}")
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    source_manifest = read_json(source / "round1_internal_manifest.json")
    selected_styles = set(STYLES)
    selected_identities = set(IDENTITIES)
    selected_samples = [
        dict(sample)
        for sample in source_manifest["sample_specs"]
        if sample["identity_key"] in selected_identities
        and sample["style"] in selected_styles
    ]
    selected_blocked = [
        dict(cell)
        for cell in source_manifest["blocked_cells"]
        if cell["identity_key"] in selected_identities and cell["style"] in selected_styles
    ]

    copy_tree_linked(source / "references", destination / "references")

    carried_audio = 0
    pending_regeneration = 0
    for sample in selected_samples:
        if sample["model_key"] in INVALID_V1_MODELS:
            sample["status"] = "pending_generation"
            pending_regeneration += 1
            continue
        for key in ("output_file", "result_file"):
            source_file = source / sample[key]
            if not source_file.is_file():
                raise FileNotFoundError(source_file)
            link_or_copy(source_file, destination / sample[key])
        sample["status"] = "ready"
        carried_audio += 1

    model_count = len(source_manifest["model_contract"]["models"])
    expected_cells = model_count * len(IDENTITIES) * len(STYLES)
    if len(selected_samples) + len(selected_blocked) != expected_cells:
        raise RuntimeError(
            "Focused coverage accounting mismatch: "
            f"{len(selected_samples)} + {len(selected_blocked)} != {expected_cells}"
        )

    styles_by_key = {item["key"]: item for item in source_manifest["styles"]}
    manifest = {
        **source_manifest,
        "round_id": ROUND_ID,
        "purpose": "corrected_focused_multimodel_expressive_clone_blind_round1",
        "supersedes_round_id": source_manifest["round_id"],
        "groups": filtered_groups(source_manifest["groups"]),
        "styles": [styles_by_key[key] for key in STYLES],
        "identity_lanes": {
            key: source_manifest["identity_lanes"][key] for key in IDENTITIES
        },
        "native_lanes": {},
        "selected_identity_lanes": list(IDENTITIES),
        "selected_styles": list(STYLES),
        "native_voice_matrix_removed": True,
        "expected_coverage_cell_count": expected_cells,
        "sample_spec_count": len(selected_samples),
        "blocked_cell_count": len(selected_blocked),
        "sample_specs": selected_samples,
        "blocked_cells": selected_blocked,
        "invalidated_v1_model_runs": {
            "fish_s2_pro": "Reference audio was incorrectly supplied at 24 kHz to a 44.1 kHz codec.",
            "moss_tts_local_v15": "Stereo output was flattened into an interleaved mono sequence.",
        },
        "review_contract": {
            **source_manifest.get("review_contract", {}),
            "focused_style_matrix": True,
            "native_voice_matrix": False,
            "technical_canary_required_before_bulk_generation": True,
        },
    }
    write_json(destination / "round1_internal_manifest.json", manifest)
    write_json(
        destination / "v2-preparation.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "source_evidence": str(source),
            "selected_identity_lanes": list(IDENTITIES),
            "selected_styles": list(STYLES),
            "sample_spec_count": len(selected_samples),
            "blocked_cell_count": len(selected_blocked),
            "carried_forward_audio_count": carried_audio,
            "pending_regeneration_count": pending_regeneration,
            "pending_models": sorted(INVALID_V1_MODELS),
        },
    )
    return {
        "destination": str(destination),
        "sample_spec_count": len(selected_samples),
        "blocked_cell_count": len(selected_blocked),
        "carried_forward_audio_count": carried_audio,
        "pending_regeneration_count": pending_regeneration,
    }


def parse_data_js(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").strip()
    prefix = "window.ALEXANDRIA_ROUND1_DATA = "
    if not text.startswith(prefix) or not text.endswith(";"):
        raise ValueError(f"Unexpected data.js format: {path}")
    return json.loads(text[len(prefix) : -1])


def write_data_js(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        "window.ALEXANDRIA_ROUND1_DATA = "
        + json.dumps(payload, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )


def finalize(
    source: Path,
    destination: Path,
    cumulative_results: Path,
) -> dict[str, Any]:
    review_root = destination / "review"
    if not (review_root / "data.js").is_file():
        raise FileNotFoundError(
            "Package the v2 evidence first with package_multimodel_round1_review.py"
        )
    if sha256_file(cumulative_results) != EXPECTED_RESULTS_SHA256:
        raise RuntimeError(f"Unexpected cumulative-results file: {cumulative_results}")

    source_assets = source / "review-round1-complete-final"
    for filename in FINAL_ASSET_FILES:
        source_file = source_assets / filename
        if not source_file.is_file():
            raise FileNotFoundError(source_file)
        shutil.copy2(source_file, review_root / filename)

    index_path = review_root / "index.html"
    replace_once(
        index_path,
        '<select id="identity-filter"><option value="all">All identities</option></select>',
        '<select id="identity-filter" aria-label="Selected identity"></select>',
    )
    replace_once(
        index_path,
        "<summary>Identity references used in this section</summary>",
        "<summary>Reference audio for the selected identity</summary>",
    )
    replace_once(
        index_path,
        '<p class="reference-help">The original source and the exact conditioning clip are provided where they differ. Listen before scoring identity.</p>',
        '<p class="reference-help">Only the selected identity is shown. The original source and exact conditioning clip are provided where they differ.</p>',
    )
    replace_once(
        index_path,
        '  <script src="data.js"></script>\n  <script src="review-core.js"></script>',
        '  <script src="data.js"></script>\n  <script src="seed-results.js"></script>\n  <script src="review-core.js"></script>',
    )

    app_path = review_root / "app.js"
    replace_once(
        app_path,
        '''  const state = {
    saved: core.loadSaved(storageKey, legacyStorageKey, allowLegacy),
    activeGroup: null,
    activeStyle: null,
    identityFilter: "all",
    searchQuery: "",
    incompleteOnly: false,
    saveTimer: null,
  };''',
        '''  const saved = core.loadSaved(storageKey, legacyStorageKey, allowLegacy);
  const seedPayload = window.ALEXANDRIA_ROUND1_SEED_RESULTS;
  let seedChanged = false;
  if (core.validImportPayload(seedPayload, data.round_id)) {
    seedPayload.rows.forEach((row) => {
      if (!byId.has(row?.sample_id)) return;
      const cleaned = core.cleanImportRow(row, seedPayload);
      if (!cleaned) return;
      const existing = saved[row.sample_id];
      if (!existing || typeof existing !== "object" || Array.isArray(existing)) {
        saved[row.sample_id] = cleaned;
        seedChanged = true;
        return;
      }
      Object.entries(cleaned).forEach(([field, value]) => {
        if (Object.hasOwn(existing, field)) return;
        existing[field] = value;
        seedChanged = true;
      });
    });
  }
  if (seedChanged) localStorage.setItem(storageKey, JSON.stringify(saved));
  const state = {
    saved,
    activeGroup: null,
    activeStyle: null,
    identityFilter: null,
    searchQuery: "",
    incompleteOnly: false,
    saveTimer: null,
  };''',
    )
    replace_once(
        app_path,
        '''  const firstStyle = context.navigation.firstStyleForGroup(state.activeGroup);
  state.activeStyle = core.restoreSelection(
    `${storageKey}:style`, `${legacyStorageKey}:style`, allowLegacy, firstStyle,
  );

  function render() {''',
        '''  const firstStyle = context.navigation.firstStyleForGroup(state.activeGroup);
  state.activeStyle = core.restoreSelection(
    `${storageKey}:style`, `${legacyStorageKey}:style`, allowLegacy, firstStyle,
  );
  const preferredIdentity = data.identity_order?.includes("narrator")
    ? "narrator"
    : data.identity_order?.[0];
  state.identityFilter = core.restoreSelection(
    `${storageKey}:identity`, `${legacyStorageKey}:identity`, allowLegacy, preferredIdentity,
  );

  function render() {''',
    )
    replace_once(
        app_path,
        '''  els.identityFilter.addEventListener("change", () => {
    state.identityFilter = els.identityFilter.value;
    context.content.renderReferences(context.navigation.filteredStyleSamples({
      ignoreSearch: true, ignoreIncomplete: true,
    }));
    context.content.renderSamples(context.navigation.filteredStyleSamples());
  });''',
        '''  els.identityFilter.addEventListener("change", () => {
    state.identityFilter = els.identityFilter.value;
    persistSelection("identity", state.identityFilter);
    render();
  });''',
    )

    content_path = review_root / "review-content.js"
    replace_once(
        content_path,
        '''        const progress = core.completion(state.saved, sectionSamples);
        sectionSamples.sort((left, right) => left.sample_id.localeCompare(right.sample_id));''',
        '''        const progress = core.completion(
          state.saved,
          context.samplesForStyle(state.activeStyle).filter((sample) => (
            sample.status === "ready"
              && sample.audio
              && sample.review_section_key === sectionSamples[0].review_section_key
          )),
        );
        sectionSamples.sort((left, right) => left.sample_id.localeCompare(right.sample_id));''',
    )

    navigation_path = review_root / "review-navigation.js"
    replace_once(
        navigation_path,
        '''    function renderIdentityFilter() {
      const identities = uniqueIdentities(context.samplesForGroup(state.activeGroup)
        .filter((sample) => sample.status === "ready"));
      const previous = state.identityFilter;
      els.identityFilter.innerHTML = '<option value="all">All identities</option>';
      identities.forEach(([key, label]) => {
        const option = document.createElement("option");
        option.value = key;
        option.textContent = label;
        els.identityFilter.appendChild(option);
      });
      state.identityFilter = identities.some(([key]) => key === previous) ? previous : "all";
      els.identityFilter.value = state.identityFilter;
    }

    function uniqueIdentities(samples) {
      const identities = new Map();
      samples.forEach((sample) => identities.set(sample.identity_key, sample.expected_identity));
      return [...identities.entries()].sort((left, right) => left[1].localeCompare(right[1]));
    }''',
        '''    function renderIdentityFilter() {
      const identities = uniqueIdentities(context.samplesForGroup(state.activeGroup)
        .filter((sample) => sample.status === "ready"));
      const previous = state.identityFilter;
      els.identityFilter.innerHTML = "";
      identities.forEach(([key, label]) => {
        const option = document.createElement("option");
        option.value = key;
        option.textContent = label;
        els.identityFilter.appendChild(option);
      });
      const fallback = identities.some(([key]) => key === "narrator")
        ? "narrator"
        : identities[0]?.[0];
      state.identityFilter = identities.some(([key]) => key === previous) ? previous : fallback;
      els.identityFilter.value = state.identityFilter;
      if (state.identityFilter !== previous) context.persistSelection("identity", state.identityFilter);
    }

    function uniqueIdentities(samples) {
      const identities = new Map();
      samples.forEach((sample) => identities.set(sample.identity_key, sample.expected_identity));
      const order = data.identity_order || [...identities.keys()];
      return order.filter((key) => identities.has(key)).map((key) => [key, identities.get(key)]);
    }''',
    )
    replace_once(
        navigation_path,
        '''    function renderStyleHeader() {
      const style = stylesByKey.get(state.activeStyle);
      const progress = core.completion(state.saved, context.samplesForStyle(state.activeStyle));
      const blocked = data.blocked_coverage.filter((item) => item.style === state.activeStyle).length;
      els.groupLabel.textContent = data.groups[state.activeGroup].label;
      els.styleTitle.textContent = style.label;
      els.styleInstruction.textContent = style.instruction;
      els.styleProgressText.textContent = `${progress.complete} / ${progress.ready} reviewed`;
      els.styleCoverageText.textContent = blocked ? `${blocked} documented unsupported cells` : "All declared cells available";
    }''',
        '''    function renderStyleHeader() {
      const style = stylesByKey.get(state.activeStyle);
      const styleSamples = context.samplesForStyle(state.activeStyle);
      const styleProgress = core.completion(state.saved, styleSamples);
      const identitySamples = styleSamples.filter((sample) => sample.identity_key === state.identityFilter);
      const identityProgress = core.completion(state.saved, identitySamples);
      const identityLabel = els.identityFilter.selectedOptions[0]?.textContent || state.identityFilter;
      const blocked = data.blocked_coverage.filter((item) => (
        item.style === state.activeStyle && item.identity_key === state.identityFilter
      )).length;
      els.groupLabel.textContent = data.groups[state.activeGroup].label;
      els.styleTitle.textContent = style.label;
      els.styleInstruction.textContent = style.instruction;
      els.styleProgressText.textContent = `${identityProgress.complete} / ${identityProgress.ready} reviewed for ${identityLabel}`;
      els.styleCoverageText.textContent = `${styleProgress.complete} / ${styleProgress.ready} reviewed across style · ${blocked} unsupported for selected identity`;
    }''',
    )
    replace_once(
        navigation_path,
        '''    function updateProgressOnly() {
      const overall = core.completion(state.saved, context.readySamples);
      const group = core.completion(state.saved, context.samplesForGroup(state.activeGroup));
      const style = core.completion(state.saved, context.samplesForStyle(state.activeStyle));
      const flagged = Object.values(state.saved).filter((row) => row?.flag_for_follow_up === true).length;
      const pending = data.samples.length - overall.ready;
      els.overallProgress.textContent = `${overall.complete} / ${overall.ready} reviewed`;
      els.overallGenerated.textContent = `${overall.ready} ready · ${pending} pending · ${data.samples.length} planned · ${data.blocked_coverage.length} unsupported`;
      els.groupProgressCompact.textContent = `${group.complete}/${group.ready}`;
      els.styleProgressText.textContent = `${style.complete} / ${style.ready} reviewed`;
      els.followupCount.textContent = `${flagged} flagged`;
      renderGroupNavigation();
      renderStyleNavigation();
    }''',
        '''    function updateProgressOnly() {
      const overall = core.completion(state.saved, context.readySamples);
      const group = core.completion(state.saved, context.samplesForGroup(state.activeGroup));
      const styleSamples = context.samplesForStyle(state.activeStyle);
      const identity = core.completion(
        state.saved,
        styleSamples.filter((sample) => sample.identity_key === state.identityFilter),
      );
      const identityLabel = els.identityFilter.selectedOptions[0]?.textContent || state.identityFilter;
      const flagged = Object.values(state.saved).filter((row) => row?.flag_for_follow_up === true).length;
      const pending = data.samples.length - overall.ready;
      els.overallProgress.textContent = `${overall.complete} / ${overall.ready} reviewed`;
      els.overallGenerated.textContent = `${overall.ready} ready · ${pending} pending · ${data.samples.length} planned · ${data.blocked_coverage.length} unsupported`;
      els.groupProgressCompact.textContent = `${group.complete}/${group.ready}`;
      els.styleProgressText.textContent = `${identity.complete} / ${identity.ready} reviewed for ${identityLabel}`;
      els.followupCount.textContent = `${flagged} flagged`;
      renderGroupNavigation();
      renderStyleNavigation();
    }''',
    )
    replace_once(
        navigation_path,
        '''        && (state.identityFilter === "all" || sample.identity_key === state.identityFilter)
        && !core.isComplete(state.saved, sample.sample_id)''',
        '''        && sample.identity_key === state.identityFilter
        && !core.isComplete(state.saved, sample.sample_id)''',
    )
    replace_once(
        navigation_path,
        '''      const selected = els.identityFilter.selectedOptions[0]?.textContent || "this identity";
      const scope = state.identityFilter === "all" ? "" : ` for ${selected}`;
      context.showNotice(`Every generated sample${scope} has been reviewed.`);''',
        '''      const selected = els.identityFilter.selectedOptions[0]?.textContent || "this identity";
      context.showNotice(`Every generated sample for ${selected} has been reviewed.`);''',
    )

    data = parse_data_js(review_root / "data.js")
    data["title"] = "Alexandria expressive-clone blind review — Corrected Round 1"
    data["test_revision"] = "focused-v2-usable"
    data["native_voice_matrix_removed"] = True
    data["identity_order"] = list(IDENTITIES)
    data["carried_forward_results_file"] = "alexandria_round1_v2_existing_results.json"

    answer_files: dict[Path, list[dict[str, Any]]] = {}
    answer_by_blind_id: dict[str, dict[str, Any]] = {}
    for answer_file in sorted((review_root / "answer-keys").glob("*.json")):
        payload = json.loads(answer_file.read_text(encoding="utf-8"))
        answer_files[answer_file] = payload
        for row in payload:
            answer_by_blind_id[row["sample_id"]] = row

    internal = read_json(destination / "round1_internal_manifest.json")
    source_sample_by_id = {sample["sample_id"]: sample for sample in internal["sample_specs"]}
    public_sample_by_id = {sample["sample_id"]: sample for sample in data["samples"]}
    review_adjustments: list[dict[str, Any]] = []
    for source_sample_id, policy in REVIEW_AUDIO_TRIMS.items():
        sample = source_sample_by_id.get(source_sample_id)
        if sample is None or sample["model_key"] != "moss_tts_local_v15":
            raise RuntimeError(f"Missing expected MOSS review-tail sample: {source_sample_id}")
        source_audio = destination / sample["output_file"]
        public_sample = public_sample_by_id[sample["blind_id"]]
        review_audio = review_root / public_sample["audio"]
        source_info = wav_info(source_audio)
        if source_info["duration_seconds"] <= MAX_NORMAL_REVIEW_DURATION_SECONDS:
            continue
        trim_review_audio(source_audio, review_audio, float(policy["end_seconds"]))
        review_info = wav_info(review_audio)
        review_hash = sha256_file(review_audio)
        source_hash = sha256_file(source_audio)
        public_sample["audio_sha256"] = review_hash
        public_sample["review_audio_adjusted"] = True
        public_sample["review_audio_duration_seconds"] = review_info["duration_seconds"]
        if isinstance(public_sample.get("audio_diagnostics"), dict):
            public_sample["audio_diagnostics"]["duration_seconds"] = review_info["duration_seconds"]
            public_sample["audio_diagnostics"]["sample_rate"] = review_info["sample_rate"]
        answer = answer_by_blind_id[sample["blind_id"]]
        answer["source_audio_sha256"] = source_hash
        answer["audio_sha256"] = review_hash
        answer["review_audio_adjustment"] = {
            "kind": "runaway_tail_trim",
            "end_seconds": float(policy["end_seconds"]),
            "reason": policy["reason"],
        }
        review_adjustments.append(
            {
                "sample_id": sample["blind_id"],
                "source_sample_id": source_sample_id,
                "identity_key": sample["identity_key"],
                "style": sample["style"],
                "source_audio_sha256": source_hash,
                "review_audio_sha256": review_hash,
                "source_duration_seconds": source_info["duration_seconds"],
                "review_duration_seconds": review_info["duration_seconds"],
                "end_seconds": float(policy["end_seconds"]),
                "reason": policy["reason"],
                "original_evidence_preserved": True,
            }
        )
    for answer_file, payload in answer_files.items():
        write_json(answer_file, payload)
    adjustments_path = review_root / "review-audio-adjustments.json"
    write_json(
        adjustments_path,
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "adjustment_count": len(review_adjustments),
            "adjustments": review_adjustments,
        },
    )

    source_results = read_json(cumulative_results)
    selected_ids = {sample["sample_id"] for sample in data["samples"]}
    rows: list[dict[str, Any]] = []
    for row in source_results.get("rows", []):
        answer = answer_by_blind_id.get(row.get("sample_id"))
        if row.get("sample_id") not in selected_ids or answer is None:
            continue
        if answer["model_key"] in INVALID_V1_MODELS:
            continue
        rows.append(row)

    complete = sum(
        1 for row in rows if all(field in row for field in REQUIRED_COMPLETE_FIELDS)
    )
    flags = sum(1 for row in rows if row.get("flag_for_follow_up") is True)
    exported_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    seed = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "export_scope": "cumulative",
        "export_key": "all",
        "exported_at": exported_at,
        "revision": int(source_results.get("revision") or 0) + 1,
        "summary": {
            "ready_sample_count": len(data["samples"]),
            "complete_sample_count": complete,
            "incomplete_sample_count": len(data["samples"]) - complete,
            "follow_up_flag_count": flags,
            "carried_forward_row_count": len(rows),
            "discarded_invalid_fish_moss_ratings": True,
        },
        "rows": rows,
    }
    seed_path = review_root / "alexandria_round1_v2_existing_results.json"
    write_json(seed_path, seed)
    (review_root / "seed-results.js").write_text(
        "window.ALEXANDRIA_ROUND1_SEED_RESULTS = "
        + json.dumps(seed, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )
    data["carried_forward_result_count"] = len(rows)
    data["carried_forward_complete_count"] = complete
    data["review_audio_adjustment_count"] = len(review_adjustments)
    write_data_js(review_root / "data.js", data)

    manifest_path = review_root / "manifest.json"
    manifest = read_json(manifest_path)
    manifest.update(
        {
            "title": data["title"],
            "focused_round1_v2": True,
            "native_voice_matrix_removed": True,
            "native_voices_pooled_across_models": False,
            "identity_count": len(IDENTITIES),
            "identity_order": list(IDENTITIES),
            "carried_forward_results_file": seed_path.name,
            "carried_forward_result_count": len(rows),
            "carried_forward_complete_count": complete,
            "seed_results_embedded": True,
            "existing_local_results_take_precedence": True,
            "cumulative_results_sha256": EXPECTED_RESULTS_SHA256,
            "invalid_v1_models_regenerated": sorted(INVALID_V1_MODELS),
            "review_audio_adjustment_file": adjustments_path.name,
            "review_audio_adjustment_count": len(review_adjustments),
            "all_audio_copied": not review_adjustments,
            "all_review_audio_present": True,
            "review_audio_derived_copy_count": len(review_adjustments),
            "original_generated_audio_preserved": True,
        }
    )
    write_json(manifest_path, manifest)

    readme = f"""ALEXANDRIA CORRECTED ROUND 1 — USABLE REVIEW\n\nThe 36 valid carried-forward result rows load automatically on first use.\nExisting browser results always take precedence over the included seed rows.\n\nRecommended macOS launch:\n1. Open Terminal.\n2. Run:\n   cd \"{review_root}\"\n   python3 -m http.server 8765 --bind 127.0.0.1\n3. In a second Terminal window, run:\n   open \"http://127.0.0.1:8765/\"\n\nThe matrix includes Narrator, Benny, Doctor, Ryan neutral, and Ryan acted.\nNative model voices are excluded. Fish uses the corrected 44.1 kHz reference path.\nMOSS uses 48 kHz references and proper stereo-to-mono downmixing. Two runaway MOSS\ntails are trimmed only in the review copies; original WAVs and receipts are preserved.\n"""
    (review_root / "START_HERE.txt").write_text(readme, encoding="utf-8")

    return {
        "review": str(review_root / "index.html"),
        "sample_count": len(data["samples"]),
        "identity_count": len(IDENTITIES),
        "style_count": len(STYLES),
        "carried_forward_row_count": len(rows),
        "carried_forward_complete_count": complete,
        "remaining_incomplete_count": len(data["samples"]) - complete,
        "review_audio_adjustment_count": len(review_adjustments),
        "seed_results": str(seed_path),
    }


def validate(destination: Path) -> dict[str, Any]:
    review_root = destination / "review"
    manifest = read_json(destination / "round1_internal_manifest.json")
    review_manifest = read_json(review_root / "manifest.json")
    public = parse_data_js(review_root / "data.js")
    seed = read_json(review_root / "alexandria_round1_v2_existing_results.json")
    adjustments = read_json(review_root / "review-audio-adjustments.json")
    samples = manifest["sample_specs"]
    blocked = manifest["blocked_cells"]

    if len(samples) != 264:
        raise RuntimeError(f"Expected 264 sample specifications, found {len(samples)}")
    if len(public["samples"]) != 264:
        raise RuntimeError(f"Expected 264 public samples, found {len(public['samples'])}")
    if set(public.get("identity_order") or []) != set(IDENTITIES):
        raise RuntimeError("The public identity selector does not contain all five required lanes")
    if {item["key"] for item in public["styles"]} != set(STYLES):
        raise RuntimeError("The public package does not contain the required ten styles")

    native = [
        sample["sample_id"]
        for sample in samples
        if sample["identity_key"].startswith("native_")
    ]
    native.extend(
        sample["sample_id"]
        for sample in public["samples"]
        if sample["identity_key"].startswith("native_")
        or sample.get("review_section_key") == "model_native_voices"
    )
    if native:
        raise RuntimeError(f"Native identities remain in v2: {native[:3]}")

    model_keys = [item["key"] for item in manifest["model_contract"]["models"]]
    expected_cells = {
        (model_key, identity_key, style)
        for model_key in model_keys
        for identity_key in IDENTITIES
        for style in STYLES
    }
    sample_cells = [
        (sample["model_key"], sample["identity_key"], sample["style"])
        for sample in samples
    ]
    blocked_cells = [
        (cell["model_key"], cell["identity_key"], cell["style"])
        for cell in blocked
    ]
    actual_cells = sample_cells + blocked_cells
    duplicates = sorted({cell for cell in actual_cells if actual_cells.count(cell) != 1})
    missing_cells = sorted(expected_cells - set(actual_cells))
    unexpected_cells = sorted(set(actual_cells) - expected_cells)
    if duplicates or missing_cells or unexpected_cells:
        raise RuntimeError(
            "Focused coverage mismatch: "
            f"duplicates={len(duplicates)} missing={len(missing_cells)} "
            f"unexpected={len(unexpected_cells)}"
        )

    output_wavs = sorted((destination / "outputs").rglob("*.wav"))
    output_receipts = sorted((destination / "outputs").rglob("*.json"))
    if len(output_wavs) != 264 or len(output_receipts) != 264:
        raise RuntimeError(
            f"Expected 264 WAVs and receipts, found {len(output_wavs)} WAVs and "
            f"{len(output_receipts)} receipts"
        )

    public_by_id = {sample["sample_id"]: sample for sample in public["samples"]}
    missing: list[str] = []
    invalid_receipts: list[str] = []
    invalid_hashes: list[str] = []
    fish_rates: set[int] = set()
    moss_source_rates: set[int] = set()
    moss_source_channels: set[int] = set()
    moss_source_runaways: list[str] = []
    moss_review_rates: set[int] = set()
    moss_review_channels: set[int] = set()
    moss_review_durations: list[float] = []
    adjustment_source_ids = {
        item["source_sample_id"] for item in adjustments.get("adjustments", [])
    }

    for sample in samples:
        audio = destination / sample["output_file"]
        receipt = destination / sample["result_file"]
        if not audio.is_file() or not receipt.is_file():
            missing.append(sample["sample_id"])
            continue
        payload = read_json(receipt)
        expected_receipt = {
            "blind_id": sample["blind_id"],
            "model_key": sample["model_key"],
            "identity_key": sample["identity_key"],
            "style": sample["style"],
        }
        if any(payload.get(key) != value for key, value in expected_receipt.items()):
            invalid_receipts.append(sample["sample_id"])
        if payload.get("audio_sha256") != sha256_file(audio):
            invalid_hashes.append(sample["sample_id"])
        info = wav_info(audio)
        if sample["model_key"] == "fish_s2_pro":
            fish_rates.add(info["sample_rate"])
        if sample["model_key"] == "moss_tts_local_v15":
            moss_source_rates.add(info["sample_rate"])
            moss_source_channels.add(info["channels"])
            if info["duration_seconds"] > MAX_NORMAL_REVIEW_DURATION_SECONDS:
                moss_source_runaways.append(sample["sample_id"])
            review_sample = public_by_id[sample["blind_id"]]
            review_audio = review_root / review_sample["audio"]
            review_info = wav_info(review_audio)
            moss_review_rates.add(review_info["sample_rate"])
            moss_review_channels.add(review_info["channels"])
            moss_review_durations.append(review_info["duration_seconds"])

    if missing or invalid_receipts or invalid_hashes:
        raise RuntimeError(
            "v2 receipt validation failed: "
            f"missing={len(missing)} invalid_receipts={len(invalid_receipts)} "
            f"invalid_hashes={len(invalid_hashes)}"
        )
    if fish_rates != {44100}:
        raise RuntimeError(f"Fish outputs are not uniformly 44.1 kHz: {fish_rates}")
    if moss_source_rates != {48000} or moss_source_channels != {1}:
        raise RuntimeError(
            f"MOSS source outputs must be 48 kHz mono: rates={moss_source_rates} "
            f"channels={moss_source_channels}"
        )
    if set(moss_source_runaways) != adjustment_source_ids:
        raise RuntimeError(
            "MOSS source runaways and review adjustments disagree: "
            f"runaways={moss_source_runaways} adjustments={sorted(adjustment_source_ids)}"
        )
    if moss_review_rates != {48000} or moss_review_channels != {1}:
        raise RuntimeError(
            f"MOSS review files must be 48 kHz mono: rates={moss_review_rates} "
            f"channels={moss_review_channels}"
        )
    if not moss_review_durations or max(moss_review_durations) > MAX_NORMAL_REVIEW_DURATION_SECONDS:
        raise RuntimeError(
            f"MOSS review duration sanity failed: max={max(moss_review_durations, default=0):.3f}"
        )

    review_wavs = sorted((review_root / "audio").glob("*.wav"))
    if len(review_wavs) != 264:
        raise RuntimeError(f"Expected 264 review WAV files, found {len(review_wavs)}")
    for sample in public["samples"]:
        review_audio = review_root / sample["audio"]
        if not review_audio.is_file() or sha256_file(review_audio) != sample["audio_sha256"]:
            invalid_hashes.append(sample["sample_id"])
    if invalid_hashes:
        raise RuntimeError(f"Review audio hash mismatch: {invalid_hashes[:3]}")

    stale_files = [
        str(path.relative_to(destination))
        for path in destination.rglob("*")
        if path.is_file()
        and (
            path.name.endswith(".lock")
            or ".partial" in path.name
            or path.name.endswith(".tmp")
        )
    ]
    if stale_files:
        raise RuntimeError(f"Stale lock/partial files remain: {stale_files[:3]}")

    complete = sum(
        1
        for row in seed["rows"]
        if all(field in row for field in REQUIRED_COMPLETE_FIELDS)
    )
    if len(seed["rows"]) != seed["summary"]["carried_forward_row_count"]:
        raise RuntimeError("Seed carried-forward row count is inconsistent")
    if complete != seed["summary"]["complete_sample_count"]:
        raise RuntimeError("Seed complete row count is inconsistent")
    if review_manifest.get("generated_sample_count") != len(samples):
        raise RuntimeError("Review package generation count does not match v2 manifest")
    if review_manifest.get("identity_count") != len(IDENTITIES):
        raise RuntimeError("Review manifest identity count is incorrect")
    if review_manifest.get("style_count") != len(STYLES):
        raise RuntimeError("Review manifest style count is incorrect")
    if not review_manifest.get("seed_results_embedded"):
        raise RuntimeError("Review package does not embed the carried-forward seed")
    if not (review_root / "seed-results.js").is_file():
        raise RuntimeError("Embedded seed-results.js is missing")

    return {
        "round_id": manifest["round_id"],
        "sample_spec_count": len(samples),
        "wav_count": len(output_wavs),
        "receipt_count": len(output_receipts),
        "review_wav_count": len(review_wavs),
        "blocked_count": len(blocked),
        "identity_lanes": list(IDENTITIES),
        "styles": list(STYLES),
        "coverage_cell_count": len(actual_cells),
        "missing_count": 0,
        "invalid_receipt_count": 0,
        "invalid_audio_hash_count": 0,
        "stale_lock_count": 0,
        "native_sample_count": 0,
        "fish_sample_rates": sorted(fish_rates),
        "moss_source_sample_rates": sorted(moss_source_rates),
        "moss_source_channels": sorted(moss_source_channels),
        "moss_source_runaway_count": len(moss_source_runaways),
        "moss_review_sample_rates": sorted(moss_review_rates),
        "moss_review_channels": sorted(moss_review_channels),
        "moss_review_max_duration_seconds": max(moss_review_durations),
        "review_audio_adjustment_count": adjustments.get("adjustment_count"),
        "carried_forward_row_count": len(seed["rows"]),
        "carried_forward_complete_count": complete,
        "remaining_incomplete_count": len(samples) - complete,
        "review": str(review_root / "index.html"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "finalize", "validate"))
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--destination", default=str(DEFAULT_DESTINATION))
    parser.add_argument("--cumulative-results", default=str(DEFAULT_RESULTS))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    destination = Path(args.destination).expanduser().resolve()
    if args.mode == "prepare":
        result = prepare(source, destination, args.force)
    elif args.mode == "finalize":
        result = finalize(
            source,
            destination,
            Path(args.cumulative_results).expanduser().resolve(),
        )
    else:
        result = validate(destination)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
