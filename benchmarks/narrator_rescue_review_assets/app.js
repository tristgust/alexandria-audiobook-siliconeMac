(() => {
  "use strict";

  const data = window.ALEXANDRIA_NARRATOR_RESCUE_DATA;
  if (!data || !Array.isArray(data.styles) || !Array.isArray(data.samples)) {
    document.body.innerHTML = "<p>Round 2 review data could not be loaded.</p>";
    return;
  }

  const REQUIRED_FIELDS = [
    "identity_1_to_5",
    "delivery_1_to_5",
    "naturalness_1_to_5",
    "artifact_severity_1_to_5",
    "spoken_text_matches_expected",
    "requested_mode_is_clear",
    "approve_for_comparison",
  ];
  const RATING_FIELDS = [
    ["identity_1_to_5", "Identity", "How closely does it match the expected narrator?"],
    ["delivery_1_to_5", "Delivery", "How clearly does it perform the requested mode?"],
    ["naturalness_1_to_5", "Naturalness", "How believable and human does it sound?"],
    ["artifact_severity_1_to_5", "Artifacts", "1 = clean; 5 = severely broken"],
  ];
  const BINARY_FIELDS = [
    ["spoken_text_matches_expected", "Text matches", "Did it say the intended words?"],
    ["requested_mode_is_clear", "Mode is clear", "Is the requested performance unmistakable?"],
    ["approve_for_comparison", "Keep", "Is it useful enough to retain for comparison?"],
  ];
  const IMPORT_FIELDS = [...REQUIRED_FIELDS, "flag_for_follow_up", "notes"];
  const RATING_KEYS = new Set(RATING_FIELDS.map(([field]) => field));
  const BINARY_KEYS = new Set(BINARY_FIELDS.map(([field]) => field));
  const stylesByKey = new Map(data.styles.map((style) => [style.key, style]));
  const styleOrder = data.style_order.filter((key) => stylesByKey.has(key));
  const samplesByStyle = new Map(styleOrder.map((key) => [key, []]));
  data.samples.forEach((sample) => {
    if (!samplesByStyle.has(sample.style)) samplesByStyle.set(sample.style, []);
    samplesByStyle.get(sample.style).push(sample);
  });
  const sampleById = new Map(data.samples.map((sample) => [sample.sample_id, sample]));
  const storageKey = `alexandria:narrator-rescue:round2:${data.schema_version || 1}:${encodeURIComponent(data.round_id)}`;
  const styleStorageKey = `${storageKey}:style`;

  const state = {
    saved: loadSaved(),
    activeStyle: restoreStyle(),
    searchQuery: "",
    incompleteOnly: false,
    saveTimer: null,
  };

  const els = Object.fromEntries([
    ["overallProgress", "overall-progress"],
    ["overallSummary", "overall-summary"],
    ["previousStyle", "previous-style"],
    ["nextStyle", "next-style"],
    ["nextIncomplete", "next-incomplete"],
    ["referenceToggle", "reference-toggle"],
    ["exportStyle", "export-style"],
    ["exportAll", "export-all"],
    ["importResults", "import-results"],
    ["styleNavigation", "style-navigation"],
    ["styleCount", "style-count"],
    ["search", "search"],
    ["incompleteOnly", "incomplete-only"],
    ["identityName", "identity-name"],
    ["styleTitle", "style-title"],
    ["styleInstruction", "style-instruction"],
    ["targetText", "target-text"],
    ["styleProgressText", "style-progress-text"],
    ["styleCandidateCount", "style-candidate-count"],
    ["referencePanel", "reference-panel"],
    ["referenceList", "reference-list"],
    ["notice", "notice"],
    ["sampleList", "sample-list"],
    ["importDialog", "import-dialog"],
    ["importSummary", "import-summary"],
  ].map(([key, id]) => [key, document.getElementById(id)]));

  function isRecord(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function loadSaved() {
    try {
      const parsed = JSON.parse(localStorage.getItem(storageKey) || "{}");
      return isRecord(parsed) ? parsed : {};
    } catch (_) {
      return {};
    }
  }

  function restoreStyle() {
    const stored = localStorage.getItem(styleStorageKey);
    return styleOrder.includes(stored) ? stored : styleOrder[0];
  }

  function validField(field, value) {
    if (RATING_KEYS.has(field)) {
      return Number.isInteger(value) && value >= 1 && value <= 5;
    }
    if (BINARY_KEYS.has(field) || field === "flag_for_follow_up") {
      return typeof value === "boolean";
    }
    if (field === "notes") {
      return value === null || (typeof value === "string" && value.length <= 10000);
    }
    return false;
  }

  function isComplete(sampleId) {
    const row = state.saved[sampleId];
    return isRecord(row) && REQUIRED_FIELDS.every((field) => validField(field, row[field]));
  }

  function completion(samples) {
    return {
      complete: samples.filter((sample) => isComplete(sample.sample_id)).length,
      total: samples.length,
    };
  }

  function showNotice(message) {
    els.notice.textContent = message;
    els.notice.hidden = false;
    window.setTimeout(() => { els.notice.hidden = true; }, 5000);
  }

  function scheduleSave() {
    clearTimeout(state.saveTimer);
    document.querySelectorAll(".saved-indicator").forEach((node) => {
      node.textContent = "Saving…";
    });
    state.saveTimer = window.setTimeout(() => {
      localStorage.setItem(storageKey, JSON.stringify(state.saved));
      document.querySelectorAll(".saved-indicator").forEach((node) => {
        node.textContent = "Saved";
      });
    }, 250);
  }

  function setValue(sampleId, field, value) {
    const existing = state.saved[sampleId];
    const row = isRecord(existing)
      ? existing
      : { sample_id: sampleId, updated_at: null, revision: 0 };
    row[field] = value;
    row.sample_id = sampleId;
    row.updated_at = new Date().toISOString();
    row.revision = Math.max(0, Number(row.revision) || 0) + 1;
    state.saved[sampleId] = row;
    scheduleSave();
    updateCardStatus(sampleId);
    updateProgress();
  }

  function styleSamples(styleKey = state.activeStyle) {
    return samplesByStyle.get(styleKey) || [];
  }

  function filteredSamples() {
    let samples = [...styleSamples()];
    if (state.incompleteOnly) {
      samples = samples.filter((sample) => !isComplete(sample.sample_id));
    }
    if (state.searchQuery) {
      const needle = state.searchQuery.toLowerCase();
      samples = samples.filter((sample) => [
        sample.sample_id,
        sample.target_text,
        sample.requested_instruction,
      ].some((value) => String(value).toLowerCase().includes(needle)));
    }
    return samples;
  }

  function renderReference() {
    const reference = data.identity || {};
    els.identityName.textContent = reference.review_name || "Narrator";
    const shared = reference.original_audio
      && reference.original_audio === reference.conditioning_audio;
    const rows = [];
    if (reference.original_audio) {
      rows.push([
        shared ? "Original and conditioning reference" : "Original source",
        reference.original_audio,
      ]);
    }
    if (reference.conditioning_audio && !shared) {
      rows.push(["Conditioning clip", reference.conditioning_audio]);
    }
    els.referenceList.innerHTML = rows.map(([label, source]) => `
      <article class="reference-card">
        <strong>${escapeHtml(label)}</strong>
        <audio controls preload="none" aria-label="${escapeHtml(`${reference.review_name || "Narrator"}: ${label}`)}" src="${escapeHtml(source)}"></audio>
      </article>`).join("");
    if (reference.conditioning_transcript) {
      const transcript = document.createElement("details");
      transcript.className = "reference-card";
      transcript.innerHTML = `<summary>Conditioning transcript</summary><p>${escapeHtml(reference.conditioning_transcript)}</p>`;
      els.referenceList.appendChild(transcript);
    }
  }

  function renderStyleNavigation() {
    els.styleNavigation.innerHTML = "";
    styleOrder.forEach((styleKey) => {
      const style = stylesByKey.get(styleKey);
      const progress = completion(styleSamples(styleKey));
      const button = document.createElement("button");
      button.type = "button";
      button.className = `nav-button${styleKey === state.activeStyle ? " active" : ""}`;
      button.innerHTML = `<span class="label">${escapeHtml(style.label)}</span><span class="count">${progress.complete}/${progress.total}</span>`;
      button.addEventListener("click", () => selectStyle(styleKey));
      els.styleNavigation.appendChild(button);
    });
    els.styleCount.textContent = `${styleOrder.length} styles`;
  }

  function renderStyleHeader() {
    const style = stylesByKey.get(state.activeStyle);
    const progress = completion(styleSamples());
    els.styleTitle.textContent = style.label;
    els.styleInstruction.textContent = style.instruction;
    els.targetText.textContent = `“${style.target_text}”`;
    els.styleProgressText.textContent = `${progress.complete} / ${progress.total} reviewed`;
    els.styleCandidateCount.textContent = `${progress.total} blinded candidates`;
  }

  function ratingField(sampleId, field, label, help, value) {
    const choices = [1, 2, 3, 4, 5].map((number) => `
      <label class="score-choice">
        <input type="radio" name="${sampleId}-${field}" data-field="${field}" value="${number}" ${Number(value) === number ? "checked" : ""}>
        <span>${number}</span>
      </label>`).join("");
    return `<fieldset class="rating-field"><legend>${escapeHtml(label)}</legend><small>${escapeHtml(help)}</small><div class="score-row">${choices}</div></fieldset>`;
  }

  function binaryField(sampleId, field, label, help, value) {
    return `<fieldset class="binary-field"><legend>${escapeHtml(label)}</legend><small>${escapeHtml(help)}</small><div class="binary-choice-row">
      <label><input type="radio" name="${sampleId}-${field}" data-field="${field}" value="true" ${value === true ? "checked" : ""}><span>Yes</span></label>
      <label><input type="radio" name="${sampleId}-${field}" data-field="${field}" value="false" ${value === false ? "checked" : ""}><span>No</span></label>
    </div></fieldset>`;
  }

  function sampleCard(sample, ordinal) {
    const row = state.saved[sample.sample_id] || {};
    const card = document.createElement("article");
    card.className = "sample-card";
    card.dataset.sampleId = sample.sample_id;
    card.id = `sample-${sample.sample_id}`;
    const ratings = RATING_FIELDS.map(([field, label, help]) => (
      ratingField(sample.sample_id, field, label, help, row[field])
    )).join("");
    const binaries = BINARY_FIELDS.map(([field, label, help]) => (
      binaryField(sample.sample_id, field, label, help, row[field])
    )).join("");
    card.innerHTML = `
      <header class="sample-card-header">
        <div><h3>Candidate ${ordinal}</h3><p>Expected voice: ${escapeHtml(sample.expected_identity)} · ${escapeHtml(sample.sample_id)}</p></div>
        <span class="status-pill">Not reviewed</span>
      </header>
      <div class="sample-card-body">
        <p class="sample-target">${escapeHtml(sample.target_text)}</p>
        <audio controls preload="none" aria-label="${escapeHtml(`Candidate ${ordinal}, ${sample.style_label}, sample ${sample.sample_id}`)}" src="${escapeHtml(sample.audio)}"></audio>
        <div class="rating-grid">${ratings}</div>
        <div class="binary-grid">${binaries}</div>
        <label class="follow-up-toggle"><input type="checkbox" data-field="flag_for_follow_up" ${row.flag_for_follow_up === true ? "checked" : ""}> Flag for follow-up <span>Use when the scores do not tell the whole story.</span></label>
        <label class="notes-field">Notes<textarea data-field="notes" maxlength="10000" placeholder="Identity drift, missing performance, pronunciation problem, artifact, or reason to revisit.">${escapeHtml(typeof row.notes === "string" ? row.notes : "")}</textarea></label>
        <footer class="sample-footer"><span>Autosaves in this browser</span><span class="saved-indicator" aria-live="polite">Saved</span></footer>
      </div>`;
    card.querySelectorAll("[data-field]").forEach((control) => {
      const eventName = control.matches("textarea") ? "input" : "change";
      control.addEventListener(eventName, () => {
        let value;
        if (control.type === "checkbox") value = control.checked;
        else if (control.type === "radio") {
          if (!control.checked) return;
          value = control.value === "true"
            ? true
            : control.value === "false"
              ? false
              : Number(control.value);
        } else value = control.value || null;
        setValue(sample.sample_id, control.dataset.field, value);
      });
    });
    updateCardClass(card, sample.sample_id);
    return card;
  }

  function renderSamples() {
    const samples = filteredSamples();
    els.sampleList.innerHTML = "";
    if (!samples.length) {
      els.sampleList.innerHTML = '<div class="empty-state"><strong>No candidates match the current filters.</strong><br>Clear the search or incomplete-only option.</div>';
      return;
    }
    samples.forEach((sample) => {
      const ordinal = styleSamples().findIndex((item) => item.sample_id === sample.sample_id) + 1;
      els.sampleList.appendChild(sampleCard(sample, ordinal));
    });
  }

  function updateCardStatus(sampleId) {
    const card = document.querySelector(`[data-sample-id="${CSS.escape(sampleId)}"]`);
    if (card) updateCardClass(card, sampleId);
  }

  function updateCardClass(card, sampleId) {
    const row = state.saved[sampleId] || {};
    const complete = isComplete(sampleId);
    card.classList.toggle("complete", complete);
    card.classList.toggle("flagged", row.flag_for_follow_up === true);
    const pill = card.querySelector(".status-pill");
    if (pill) {
      pill.textContent = complete ? "Reviewed" : "Not reviewed";
      pill.classList.toggle("complete", complete);
    }
  }

  function updateProgress() {
    const overall = completion(data.samples);
    const current = completion(styleSamples());
    els.overallProgress.textContent = `${overall.complete} / ${overall.total} reviewed`;
    els.overallSummary.textContent = `${data.samples.length} candidates · ${styleOrder.length} styles`;
    els.styleProgressText.textContent = `${current.complete} / ${current.total} reviewed`;
    renderStyleNavigation();
  }

  function updatePreviousNext() {
    const index = styleOrder.indexOf(state.activeStyle);
    els.previousStyle.disabled = index <= 0;
    els.nextStyle.disabled = index < 0 || index >= styleOrder.length - 1;
  }

  function render() {
    if (!styleOrder.includes(state.activeStyle)) state.activeStyle = styleOrder[0];
    renderStyleNavigation();
    renderStyleHeader();
    renderSamples();
    updateProgress();
    updatePreviousNext();
  }

  function selectStyle(styleKey) {
    if (!styleOrder.includes(styleKey)) return;
    state.activeStyle = styleKey;
    localStorage.setItem(styleStorageKey, styleKey);
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function moveStyle(delta) {
    const target = styleOrder[styleOrder.indexOf(state.activeStyle) + delta];
    if (target) selectStyle(target);
  }

  function nextIncomplete() {
    const current = styleSamples().find((sample) => !isComplete(sample.sample_id));
    if (current) {
      const target = document.getElementById(`sample-${current.sample_id}`);
      target?.scrollIntoView({ behavior: "smooth", block: "start" });
      target?.querySelector("audio")?.focus();
      return;
    }
    const currentIndex = styleOrder.indexOf(state.activeStyle);
    for (let offset = 1; offset < styleOrder.length; offset += 1) {
      const styleKey = styleOrder[(currentIndex + offset) % styleOrder.length];
      if (!styleSamples(styleKey).some((sample) => !isComplete(sample.sample_id))) continue;
      selectStyle(styleKey);
      requestAnimationFrame(nextIncomplete);
      return;
    }
    showNotice("Every candidate in this round has been reviewed.");
  }

  function exportRows(scope, styleKey) {
    const samples = scope === "style" ? styleSamples(styleKey) : data.samples;
    const rows = samples
      .filter((sample) => state.saved[sample.sample_id])
      .map((sample) => ({ ...state.saved[sample.sample_id], sample_id: sample.sample_id }));
    const complete = samples.filter((sample) => isComplete(sample.sample_id)).length;
    const payload = {
      schema_version: 1,
      round_id: data.round_id,
      export_scope: scope,
      export_key: styleKey || "all",
      exported_at: new Date().toISOString(),
      revision: rows.reduce((maximum, row) => Math.max(maximum, Number(row.revision) || 0), 0),
      summary: {
        ready_sample_count: samples.length,
        complete_sample_count: complete,
        incomplete_sample_count: samples.length - complete,
        follow_up_flag_count: rows.filter((row) => row.flag_for_follow_up === true).length,
      },
      rows,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `alexandria_narrator_rescue_round2_${scope}_${styleKey || "all"}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
  }

  function validTimestamp(value) {
    return typeof value === "string" && value.length > 0 && Number.isFinite(Date.parse(value));
  }

  function cleanImportRow(row, payload) {
    if (!isRecord(row) || typeof row.sample_id !== "string" || !sampleById.has(row.sample_id)) return null;
    const fields = IMPORT_FIELDS.filter((field) => Object.hasOwn(row, field));
    if (!fields.length || fields.some((field) => !validField(field, row[field]))) return null;
    const updatedAt = Object.hasOwn(row, "updated_at") ? row.updated_at : payload.exported_at;
    const revision = Object.hasOwn(row, "revision") ? row.revision : (payload.revision || 0);
    if (!validTimestamp(updatedAt) || !Number.isInteger(revision) || revision < 0) return null;
    const cleaned = { sample_id: row.sample_id, updated_at: updatedAt, revision };
    fields.forEach((field) => { cleaned[field] = row[field]; });
    return cleaned;
  }

  function priority(row) {
    return [
      Number.isFinite(Date.parse(row?.updated_at || "")) ? Date.parse(row.updated_at) : 0,
      Number(row?.revision) || 0,
      JSON.stringify(row || {}),
    ];
  }

  function incomingIsNewer(incoming, existing) {
    if (!existing) return true;
    const left = priority(incoming);
    const right = priority(existing);
    for (let index = 0; index < left.length; index += 1) {
      if (left[index] === right[index]) continue;
      return left[index] > right[index];
    }
    return false;
  }

  async function importFiles(files) {
    const counts = { imported: 0, conflicts: 0, unknown: 0, malformed: 0, ignored: 0 };
    for (const file of files) {
      let payload;
      try {
        payload = JSON.parse(await file.text());
      } catch (_) {
        counts.ignored += 1;
        continue;
      }
      if (!isRecord(payload) || payload.schema_version !== 1 || payload.round_id !== data.round_id || !Array.isArray(payload.rows)) {
        counts.ignored += 1;
        continue;
      }
      payload.rows.forEach((row) => {
        if (!isRecord(row) || typeof row.sample_id !== "string") {
          counts.malformed += 1;
          return;
        }
        if (!sampleById.has(row.sample_id)) {
          counts.unknown += 1;
          return;
        }
        const cleaned = cleanImportRow(row, payload);
        if (!cleaned) {
          counts.malformed += 1;
          return;
        }
        if (!incomingIsNewer(cleaned, state.saved[row.sample_id])) {
          counts.conflicts += 1;
          return;
        }
        state.saved[row.sample_id] = { ...state.saved[row.sample_id], ...cleaned };
        counts.imported += 1;
      });
    }
    localStorage.setItem(storageKey, JSON.stringify(state.saved));
    render();
    els.importSummary.textContent = [
      `${counts.imported} result rows merged.`,
      `${counts.conflicts} older or duplicate rows skipped.`,
      `${counts.unknown} unknown sample IDs skipped.`,
      `${counts.malformed} malformed rows skipped.`,
      `${counts.ignored} files ignored.`,
    ].join(" ");
    if (typeof els.importDialog.showModal === "function") els.importDialog.showModal();
    else showNotice(els.importSummary.textContent);
    return counts;
  }

  els.previousStyle.addEventListener("click", () => moveStyle(-1));
  els.nextStyle.addEventListener("click", () => moveStyle(1));
  els.nextIncomplete.addEventListener("click", nextIncomplete);
  els.referenceToggle.addEventListener("click", () => {
    els.referencePanel.open = !els.referencePanel.open;
    els.referenceToggle.setAttribute("aria-expanded", String(els.referencePanel.open));
  });
  els.exportStyle.addEventListener("click", () => exportRows("style", state.activeStyle));
  els.exportAll.addEventListener("click", () => exportRows("cumulative", "all"));
  els.importResults.addEventListener("change", async (event) => {
    await importFiles([...event.target.files]);
    event.target.value = "";
  });
  els.search.addEventListener("input", () => {
    state.searchQuery = els.search.value.trim();
    renderSamples();
  });
  els.incompleteOnly.addEventListener("change", () => {
    state.incompleteOnly = els.incompleteOnly.checked;
    renderSamples();
  });
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (event.target instanceof Element && event.target.closest("audio, input, textarea, select, button, a[href], summary, [contenteditable]")) return;
    if (event.key === "ArrowLeft") moveStyle(-1);
    if (event.key === "ArrowRight") moveStyle(1);
    if (event.key.toLowerCase() === "n") nextIncomplete();
  });

  renderReference();
  render();
})();
