(() => {
  "use strict";

  const data = window.FISH_S21_BLIND_DATA;
  if (!data || !Array.isArray(data.styles) || !Array.isArray(data.samples)) {
    document.body.textContent = "Blind-review data could not be loaded.";
    return;
  }

  const required = [
    "identity_1_to_5",
    "delivery_1_to_5",
    "naturalness_1_to_5",
    "artifact_severity_1_to_5",
    "spoken_text_matches_expected",
    "requested_mode_is_clear",
    "approve_for_comparison",
  ];
  const ratingFields = [
    ["identity_1_to_5", "Identity", "How closely does this match the Ryan reference?"],
    ["delivery_1_to_5", "Delivery", "How clearly does it perform the requested delivery?"],
    ["naturalness_1_to_5", "Naturalness", "How believable and human does it sound?"],
    ["artifact_severity_1_to_5", "Artifacts", "1 = clean; 5 = severely broken"],
  ];
  const binaryFields = [
    ["spoken_text_matches_expected", "Text matches", "Did it say the target words?"],
    ["requested_mode_is_clear", "Mode is clear", "Is the requested delivery unmistakable?"],
    ["approve_for_comparison", "Keep", "Should this remain in the finalist comparison?"],
  ];

  const styleByKey = new Map(data.styles.map((style) => [style.key, style]));
  const query = new URLSearchParams(location.search);
  const reviewer = (query.get("reviewer") || localStorage.getItem("alexandria-fish-reviewer") || "default")
    .trim().slice(0, 80) || "default";
  const storageKey = `alexandria:fish-s21-blind:${data.round_id}:${encodeURIComponent(reviewer)}`;
  const state = {
    activeStyle: localStorage.getItem(`${storageKey}:style`) || data.styles[0].key,
    rows: loadRows(),
    saveTimer: null,
  };

  const els = Object.fromEntries([
    "overall-progress", "style-progress", "style-navigation", "reviewer-profile",
    "style-group", "style-title", "requested-delivery", "target-text",
    "identity-label", "reference-text", "reference-audio", "sample-list", "notice",
    "previous-style", "next-style", "next-incomplete", "export-results",
  ].map((id) => [id.replaceAll("-", "_"), document.getElementById(id)]));

  function loadRows() {
    try {
      const parsed = JSON.parse(localStorage.getItem(storageKey) || "{}");
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch (_) {
      return {};
    }
  }

  function rowComplete(row) {
    return row && required.every((field) => Object.hasOwn(row, field));
  }

  function samplesForStyle(key) {
    return data.samples.filter((sample) => sample.style === key && sample.status === "ready");
  }

  function saveSoon() {
    clearTimeout(state.saveTimer);
    document.querySelectorAll(".saved-indicator").forEach((node) => { node.textContent = "Saving…"; });
    state.saveTimer = setTimeout(() => {
      localStorage.setItem(storageKey, JSON.stringify(state.rows));
      document.querySelectorAll(".saved-indicator").forEach((node) => { node.textContent = "Saved"; });
    }, 250);
  }

  function setField(sampleId, field, value) {
    const row = state.rows[sampleId] || { sample_id: sampleId, revision: 0 };
    row[field] = value;
    row.updated_at = new Date().toISOString();
    row.revision = Number(row.revision || 0) + 1;
    state.rows[sampleId] = row;
    saveSoon();
    updateProgress();
    updateCard(sampleId);
  }

  function updateCard(sampleId) {
    const card = document.querySelector(`[data-sample-id="${CSS.escape(sampleId)}"]`);
    if (!card) return;
    const complete = rowComplete(state.rows[sampleId]);
    card.classList.toggle("complete", complete);
    card.classList.toggle("flagged", state.rows[sampleId]?.flag_for_follow_up === true);
    card.querySelector(".status").textContent = complete ? "Reviewed" : "Incomplete";
  }

  function updateProgress() {
    const ready = data.samples.filter((sample) => sample.status === "ready");
    const complete = ready.filter((sample) => rowComplete(state.rows[sample.sample_id])).length;
    const current = samplesForStyle(state.activeStyle);
    const currentComplete = current.filter((sample) => rowComplete(state.rows[sample.sample_id])).length;
    els.overall_progress.textContent = `${complete} / ${ready.length} reviewed`;
    els.style_progress.textContent = `${currentComplete} / ${current.length} in this delivery`;
    renderNavigation();
  }

  function renderNavigation() {
    const fragment = document.createDocumentFragment();
    data.styles.forEach((style) => {
      const samples = samplesForStyle(style.key);
      const complete = samples.filter((sample) => rowComplete(state.rows[sample.sample_id])).length;
      const button = document.createElement("button");
      button.type = "button";
      button.className = `style-nav${style.key === state.activeStyle ? " active" : ""}`;
      button.setAttribute("aria-current", style.key === state.activeStyle ? "page" : "false");
      const label = document.createElement("span");
      label.textContent = style.label;
      const count = document.createElement("span");
      count.textContent = `${complete}/${samples.length}`;
      button.append(label, count);
      button.addEventListener("click", () => selectStyle(style.key));
      fragment.append(button);
    });
    els.style_navigation.replaceChildren(fragment);
  }

  function scoreField(sample, row, [field, label, help]) {
    const fieldset = document.createElement("fieldset");
    fieldset.className = "rating-field";
    const legend = document.createElement("legend");
    legend.textContent = label;
    const small = document.createElement("small");
    small.textContent = help;
    const choices = document.createElement("div");
    choices.className = "score-row";
    for (let value = 1; value <= 5; value += 1) {
      const choice = document.createElement("label");
      choice.className = "score-choice";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = `${sample.sample_id}-${field}`;
      input.value = String(value);
      input.checked = Number(row?.[field]) === value;
      input.addEventListener("change", () => setField(sample.sample_id, field, value));
      const span = document.createElement("span");
      span.textContent = String(value);
      choice.append(input, span);
      choices.append(choice);
    }
    fieldset.append(legend, small, choices);
    return fieldset;
  }

  function binaryField(sample, row, [field, label, help]) {
    const fieldset = document.createElement("fieldset");
    fieldset.className = "binary-field";
    const legend = document.createElement("legend");
    legend.textContent = label;
    const small = document.createElement("small");
    small.textContent = help;
    const choices = document.createElement("div");
    choices.className = "binary-row";
    [[true, "Yes"], [false, "No"]].forEach(([value, text]) => {
      const choice = document.createElement("label");
      choice.className = "binary-choice";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = `${sample.sample_id}-${field}`;
      input.value = String(value);
      input.checked = row?.[field] === value;
      input.addEventListener("change", () => setField(sample.sample_id, field, value));
      const span = document.createElement("span");
      span.textContent = text;
      choice.append(input, span);
      choices.append(choice);
    });
    fieldset.append(legend, small, choices);
    return fieldset;
  }

  function sampleCard(sample) {
    const row = state.rows[sample.sample_id] || {};
    const card = document.createElement("article");
    card.className = "sample-card";
    card.dataset.sampleId = sample.sample_id;
    const header = document.createElement("header");
    header.className = "sample-header";
    const title = document.createElement("h3");
    title.textContent = `Candidate ${String(sample.candidate_number).padStart(2, "0")}`;
    const status = document.createElement("span");
    status.className = "status";
    header.append(title, status);
    const body = document.createElement("div");
    body.className = "sample-body";
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "metadata";
    audio.src = sample.audio;
    audio.setAttribute("aria-label", `${title.textContent} audio`);
    const ratings = document.createElement("div");
    ratings.className = "rating-grid";
    ratingFields.forEach((definition) => ratings.append(scoreField(sample, row, definition)));
    const binaries = document.createElement("div");
    binaries.className = "binary-grid";
    binaryFields.forEach((definition) => binaries.append(binaryField(sample, row, definition)));
    const follow = document.createElement("label");
    follow.className = "follow-up";
    const followInput = document.createElement("input");
    followInput.type = "checkbox";
    followInput.checked = row.flag_for_follow_up === true;
    followInput.addEventListener("change", () => setField(sample.sample_id, "flag_for_follow_up", followInput.checked));
    const followText = document.createElement("span");
    followText.textContent = "Flag for focused follow-up";
    follow.append(followInput, followText);
    const notes = document.createElement("label");
    notes.className = "notes";
    notes.textContent = "Notes";
    const textarea = document.createElement("textarea");
    textarea.maxLength = 10000;
    textarea.value = row.notes || "";
    textarea.addEventListener("input", () => setField(sample.sample_id, "notes", textarea.value));
    notes.append(textarea);
    const footer = document.createElement("footer");
    footer.className = "sample-footer";
    const saved = document.createElement("span");
    saved.className = "saved-indicator";
    saved.textContent = "Saved";
    footer.append(saved);
    body.append(audio, ratings, binaries, follow, notes, footer);
    card.append(header, body);
    requestAnimationFrame(() => updateCard(sample.sample_id));
    return card;
  }

  function renderSamples() {
    const rows = samplesForStyle(state.activeStyle);
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "No ready candidates are available for this delivery.";
      els.sample_list.replaceChildren(empty);
      return;
    }
    els.sample_list.replaceChildren(...rows.map(sampleCard));
  }

  function renderStyle() {
    const style = styleByKey.get(state.activeStyle) || data.styles[0];
    state.activeStyle = style.key;
    localStorage.setItem(`${storageKey}:style`, style.key);
    els.style_group.textContent = style.group;
    els.style_title.textContent = style.label;
    els.requested_delivery.textContent = style.requested_delivery;
    els.target_text.textContent = style.target_text;
    renderSamples();
    updateProgress();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function selectStyle(key) {
    if (!styleByKey.has(key)) return;
    state.activeStyle = key;
    renderStyle();
  }

  function moveStyle(offset) {
    const index = data.styles.findIndex((style) => style.key === state.activeStyle);
    const next = (index + offset + data.styles.length) % data.styles.length;
    selectStyle(data.styles[next].key);
  }

  function nextIncomplete() {
    const styles = data.styles.map((style) => style.key);
    const start = styles.indexOf(state.activeStyle);
    for (let styleOffset = 0; styleOffset < styles.length; styleOffset += 1) {
      const key = styles[(start + styleOffset) % styles.length];
      const sample = samplesForStyle(key).find((candidate) => !rowComplete(state.rows[candidate.sample_id]));
      if (!sample) continue;
      if (key !== state.activeStyle) selectStyle(key);
      requestAnimationFrame(() => {
        const card = document.querySelector(`[data-sample-id="${CSS.escape(sample.sample_id)}"]`);
        card?.scrollIntoView({ behavior: "smooth", block: "start" });
        card?.querySelector("audio")?.focus();
      });
      return;
    }
    showNotice("Every ready candidate has a complete score.");
  }

  function showNotice(message) {
    els.notice.textContent = message;
    els.notice.hidden = false;
    setTimeout(() => { els.notice.hidden = true; }, 4500);
  }

  function exportResults() {
    const rows = data.samples
      .filter((sample) => state.rows[sample.sample_id])
      .map((sample) => ({ ...state.rows[sample.sample_id], sample_id: sample.sample_id }));
    const payload = {
      schema_version: 1,
      round_id: data.round_id,
      reviewer,
      exported_at: new Date().toISOString(),
      summary: {
        ready_sample_count: data.samples.length,
        complete_sample_count: data.samples.filter((sample) => rowComplete(state.rows[sample.sample_id])).length,
        follow_up_count: rows.filter((row) => row.flag_for_follow_up === true).length,
      },
      rows,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `alexandria_fish_s21_blind_${reviewer}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
  }

  els.reviewer_profile.value = reviewer;
  els.reviewer_profile.addEventListener("change", () => {
    const value = els.reviewer_profile.value.trim().slice(0, 80) || "default";
    localStorage.setItem("alexandria-fish-reviewer", value);
    const url = new URL(location.href);
    url.searchParams.set("reviewer", value);
    location.href = url.toString();
  });
  els.identity_label.textContent = data.identity.label;
  els.reference_text.textContent = data.identity.reference_text;
  els.reference_audio.src = data.identity.reference_audio;
  els.reference_audio.setAttribute("aria-label", `${data.identity.label} identity reference`);
  els.previous_style.addEventListener("click", () => moveStyle(-1));
  els.next_style.addEventListener("click", () => moveStyle(1));
  els.next_incomplete.addEventListener("click", nextIncomplete);
  els.export_results.addEventListener("click", exportResults);
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (event.target instanceof Element && event.target.closest("audio, input, textarea, button, a[href], summary")) return;
    if (event.key === "ArrowLeft") moveStyle(-1);
    if (event.key === "ArrowRight") moveStyle(1);
    if (event.key.toLowerCase() === "n") nextIncomplete();
  });

  renderStyle();
})();
