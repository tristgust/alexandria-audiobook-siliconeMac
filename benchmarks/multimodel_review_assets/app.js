(() => {
  "use strict";

  const data = window.ALEXANDRIA_ROUND1_DATA;
  if (!data) {
    document.body.innerHTML = "<p>Round 1 data could not be loaded.</p>";
    return;
  }

  const STORAGE_KEY = `alexandria-round1-review:${data.round_id}`;
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
    ["identity_1_to_5", "Identity", "How closely does it match the named voice?"],
    ["delivery_1_to_5", "Delivery", "How clearly does it perform the requested mode?"],
    ["naturalness_1_to_5", "Naturalness", "How believable and human does it sound?"],
    ["artifact_severity_1_to_5", "Artifacts", "1 = clean; 5 = severely broken"],
  ];
  const BINARY_FIELDS = [
    ["spoken_text_matches_expected", "Text matches", "Did it say the intended words?"],
    ["requested_mode_is_clear", "Mode is clear", "Is the requested emotion or delivery unmistakable?"],
    ["approve_for_comparison", "Keep", "Is it useful enough to retain for comparison?"],
  ];

  const byId = new Map(data.samples.map((sample) => [sample.sample_id, sample]));
  const stylesByKey = new Map(data.styles.map((style) => [style.key, style]));
  const groupKeys = Object.keys(data.groups);
  const readySamples = data.samples.filter((sample) => sample.status === "ready" && sample.audio);

  let saved = loadSaved();
  let activeGroup = restoreSelection("group", firstGeneratedGroup());
  let activeStyle = restoreSelection("style", firstStyleForGroup(activeGroup));
  let identityFilter = "all";
  let searchQuery = "";
  let incompleteOnly = false;
  let saveTimer = null;

  const els = {
    groupNavigation: document.getElementById("group-navigation"),
    styleNavigation: document.getElementById("style-navigation"),
    identityFilter: document.getElementById("identity-filter"),
    search: document.getElementById("search"),
    incompleteOnly: document.getElementById("incomplete-only"),
    groupLabel: document.getElementById("group-label"),
    styleTitle: document.getElementById("style-title"),
    styleInstruction: document.getElementById("style-instruction"),
    styleProgressText: document.getElementById("style-progress-text"),
    styleCoverageText: document.getElementById("style-coverage-text"),
    groupProgressCompact: document.getElementById("group-progress-compact"),
    referenceList: document.getElementById("reference-list"),
    sampleList: document.getElementById("sample-list"),
    notice: document.getElementById("notice"),
    overallProgress: document.getElementById("overall-progress"),
    overallGenerated: document.getElementById("overall-generated"),
    followupCount: document.getElementById("followup-count"),
    previousStyle: document.getElementById("previous-style"),
    nextStyle: document.getElementById("next-style"),
    nextIncomplete: document.getElementById("next-incomplete"),
    exportStyle: document.getElementById("export-style"),
    exportGroup: document.getElementById("export-group"),
    exportAll: document.getElementById("export-all"),
    importResults: document.getElementById("import-results"),
    importDialog: document.getElementById("import-dialog"),
    importSummary: document.getElementById("import-summary"),
  };

  function loadSaved() {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (_) {
      return {};
    }
  }

  function persistSelection(kind, value) {
    localStorage.setItem(`${STORAGE_KEY}:${kind}`, value);
  }

  function restoreSelection(kind, fallback) {
    const candidate = localStorage.getItem(`${STORAGE_KEY}:${kind}`);
    return candidate || fallback;
  }

  function firstGeneratedGroup() {
    return groupKeys.find((key) => samplesForGroup(key).some((sample) => sample.status === "ready")) || groupKeys[0];
  }

  function firstStyleForGroup(groupKey) {
    return data.groups[groupKey]?.styles?.find((styleKey) => samplesForStyle(styleKey).some((sample) => sample.status === "ready"))
      || data.groups[groupKey]?.styles?.[0]
      || data.styles[0]?.key;
  }

  function samplesForGroup(groupKey) {
    return data.samples.filter((sample) => sample.group === groupKey);
  }

  function samplesForStyle(styleKey) {
    return data.samples.filter((sample) => sample.style === styleKey);
  }

  function stateFor(sampleId) {
    if (!saved[sampleId]) {
      saved[sampleId] = { sample_id: sampleId, updated_at: null };
    }
    return saved[sampleId];
  }

  function isComplete(sampleId) {
    const row = saved[sampleId];
    return Boolean(row && REQUIRED_FIELDS.every((field) => row[field] !== null && row[field] !== undefined && row[field] !== ""));
  }

  function completion(samples) {
    const ready = samples.filter((sample) => sample.status === "ready" && sample.audio);
    return {
      complete: ready.filter((sample) => isComplete(sample.sample_id)).length,
      ready: ready.length,
    };
  }

  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
      document.querySelectorAll(".saved-indicator").forEach((node) => {
        node.textContent = "Saved";
      });
    }, 120);
  }

  function setValue(sampleId, field, value) {
    const row = stateFor(sampleId);
    row[field] = value;
    row.updated_at = new Date().toISOString();
    scheduleSave();
    updateCardStatus(sampleId);
    updateProgressOnly();
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatMetric(value, digits = 3) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    return Number(value).toFixed(digits);
  }

  function render() {
    if (!data.groups[activeGroup]) activeGroup = firstGeneratedGroup();
    if (!data.groups[activeGroup].styles.includes(activeStyle)) activeStyle = firstStyleForGroup(activeGroup);
    renderGroupNavigation();
    renderStyleNavigation();
    renderIdentityFilter();
    renderStyleHeader();
    renderReferences();
    renderSamples();
    updateProgressOnly();
    updatePreviousNextButtons();
  }

  function renderGroupNavigation() {
    els.groupNavigation.innerHTML = "";
    groupKeys.forEach((groupKey) => {
      const group = data.groups[groupKey];
      const progress = completion(samplesForGroup(groupKey));
      const button = document.createElement("button");
      button.type = "button";
      button.className = `nav-button${groupKey === activeGroup ? " active" : ""}`;
      button.innerHTML = `
        <span class="label">${escapeHtml(group.label)}</span>
        <span class="count">${progress.complete}/${progress.ready}</span>
        <small>${escapeHtml(group.description)}</small>`;
      button.addEventListener("click", () => {
        activeGroup = groupKey;
        activeStyle = firstStyleForGroup(groupKey);
        persistSelection("group", activeGroup);
        persistSelection("style", activeStyle);
        render();
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
      els.groupNavigation.appendChild(button);
    });
  }

  function renderStyleNavigation() {
    els.styleNavigation.innerHTML = "";
    data.groups[activeGroup].styles.forEach((styleKey) => {
      const style = stylesByKey.get(styleKey);
      const progress = completion(samplesForStyle(styleKey));
      const button = document.createElement("button");
      button.type = "button";
      button.className = `nav-button${styleKey === activeStyle ? " active" : ""}`;
      button.innerHTML = `<span class="label">${escapeHtml(style.label)}</span><span class="count">${progress.complete}/${progress.ready}</span>`;
      button.addEventListener("click", () => selectStyle(styleKey));
      els.styleNavigation.appendChild(button);
    });
  }

  function renderIdentityFilter() {
    const identities = uniqueIdentities(samplesForGroup(activeGroup).filter((sample) => sample.status === "ready"));
    const previous = identityFilter;
    els.identityFilter.innerHTML = '<option value="all">All identities</option>';
    identities.forEach(([key, label]) => {
      const option = document.createElement("option");
      option.value = key;
      option.textContent = label;
      els.identityFilter.appendChild(option);
    });
    identityFilter = identities.some(([key]) => key === previous) ? previous : "all";
    els.identityFilter.value = identityFilter;
  }

  function uniqueIdentities(samples) {
    const map = new Map();
    samples.forEach((sample) => map.set(sample.identity_key, sample.expected_identity));
    return [...map.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }

  function renderStyleHeader() {
    const style = stylesByKey.get(activeStyle);
    const progress = completion(samplesForStyle(activeStyle));
    const blocked = data.blocked_coverage.filter((item) => item.style === activeStyle).length;
    els.groupLabel.textContent = data.groups[activeGroup].label;
    els.styleTitle.textContent = style.label;
    els.styleInstruction.textContent = style.instruction;
    els.styleProgressText.textContent = `${progress.complete} / ${progress.ready} reviewed`;
    els.styleCoverageText.textContent = blocked ? `${blocked} documented unsupported cells` : "All declared cells available";
  }

  function renderReferences() {
    const samples = filteredStyleSamples({ ignoreSearch: true, ignoreIncomplete: true });
    const keys = [...new Set(samples.map((sample) => sample.identity_reference_key))];
    els.referenceList.innerHTML = "";
    keys.forEach((key) => {
      const reference = data.identities[key];
      if (!reference) return;
      const card = document.createElement("article");
      card.className = "reference-card";
      const original = reference.original_audio
        ? `<div class="reference-audio-row"><strong>Original source</strong><audio controls preload="none" src="${escapeHtml(reference.original_audio)}"></audio></div>`
        : "";
      const conditioning = reference.conditioning_audio
        ? `<div class="reference-audio-row"><strong>Conditioning clip</strong><audio controls preload="none" src="${escapeHtml(reference.conditioning_audio)}"></audio></div>`
        : "";
      card.innerHTML = `
        <h3>${escapeHtml(reference.review_name)}</h3>
        <p class="kind">${escapeHtml(reference.kind.replaceAll("_", " "))}</p>
        ${original}${conditioning}
        ${reference.conditioning_transcript ? `<details><summary>Conditioning transcript</summary><p>${escapeHtml(reference.conditioning_transcript)}</p></details>` : ""}`;
      els.referenceList.appendChild(card);
    });
    if (!keys.length) {
      els.referenceList.innerHTML = '<p class="empty-state">No generated samples are available for this style yet.</p>';
    }
  }

  function filteredStyleSamples(options = {}) {
    let samples = samplesForStyle(activeStyle).filter((sample) => sample.status === "ready" && sample.audio);
    if (identityFilter !== "all") samples = samples.filter((sample) => sample.identity_key === identityFilter);
    if (!options.ignoreIncomplete && incompleteOnly) samples = samples.filter((sample) => !isComplete(sample.sample_id));
    if (!options.ignoreSearch && searchQuery) {
      const needle = searchQuery.toLowerCase();
      samples = samples.filter((sample) => [sample.expected_identity, sample.target_text, sample.sample_id, sample.style_label]
        .some((value) => String(value).toLowerCase().includes(needle)));
    }
    return samples.sort((a, b) => {
      const sectionCompare = a.review_section_label.localeCompare(b.review_section_label);
      return sectionCompare || a.sample_id.localeCompare(b.sample_id);
    });
  }

  function renderSamples() {
    const samples = filteredStyleSamples();
    els.sampleList.innerHTML = "";
    if (!samples.length) {
      els.sampleList.innerHTML = '<div class="empty-state"><strong>No samples match the current filters.</strong><br>Clear the search, identity filter, or incomplete-only option.</div>';
      return;
    }

    const bySection = new Map();
    samples.forEach((sample) => {
      if (!bySection.has(sample.review_section_key)) bySection.set(sample.review_section_key, []);
      bySection.get(sample.review_section_key).push(sample);
    });

    bySection.forEach((sectionSamples) => {
      const section = document.createElement("section");
      section.className = "identity-section";
      const sectionProgress = completion(sectionSamples);
      sectionSamples.sort((a, b) => a.sample_id.localeCompare(b.sample_id));
      section.innerHTML = `
        <header class="identity-section-header">
          <h3>${escapeHtml(sectionSamples[0].review_section_label)}</h3>
          <span>${sectionProgress.complete} / ${sectionProgress.ready} reviewed</span>
        </header>`;
      sectionSamples.forEach((sample, index) => section.appendChild(sampleCard(sample, index + 1)));
      els.sampleList.appendChild(section);
    });
  }

  function sampleCard(sample, ordinal) {
    const row = stateFor(sample.sample_id);
    const card = document.createElement("article");
    card.className = "sample-card";
    card.dataset.sampleId = sample.sample_id;
    card.id = `sample-${sample.sample_id}`;
    card.innerHTML = `
      <header class="sample-card-header">
        <div>
          <h4>Candidate ${ordinal}</h4>
          <p>Expected voice: ${escapeHtml(sample.expected_identity)} · ${escapeHtml(sample.sample_id)}</p>
        </div>
        <span class="status-pill">Not reviewed</span>
      </header>
      <div class="sample-card-body">
        <p class="target-text">${escapeHtml(sample.target_text)}</p>
        <audio controls preload="none" src="${escapeHtml(sample.audio)}"></audio>
        <details class="technical-evidence">
          <summary>Text and technical evidence</summary>
          <dl class="evidence-grid">
            <dt>Automatic transcript</dt><dd>${escapeHtml(sample.automatic_transcript || "Not evaluated yet")}</dd>
            <dt>Word error rate</dt><dd>${formatMetric(sample.word_error_rate)}</dd>
            <dt>Identity cosine</dt><dd>${formatMetric(sample.speaker_cosine)}</dd>
            <dt>Duration</dt><dd>${sample.audio_diagnostics ? `${formatMetric(sample.audio_diagnostics.duration_seconds, 2)} seconds` : "—"}</dd>
          </dl>
        </details>
        <div class="rating-grid">${RATING_FIELDS.map(([field, label, help]) => ratingField(sample.sample_id, field, label, help, row[field])).join("")}</div>
        <div class="binary-grid">${BINARY_FIELDS.map(([field, label, help]) => binaryField(sample.sample_id, field, label, help, row[field])).join("")}</div>
        <label class="follow-up-toggle"><input type="checkbox" data-field="flag_for_follow_up" ${row.flag_for_follow_up ? "checked" : ""}> Flag for follow-up <span>Only when the scores do not tell the whole story.</span></label>
        <label class="notes-field">Notes<textarea data-field="notes" placeholder="Specific identity drift, delivery issue, text error, artifact, or reason to revisit.">${escapeHtml(row.notes || "")}</textarea></label>
        <footer class="sample-footer"><span>Autosaves in this browser</span><span class="saved-indicator">Saved</span></footer>
      </div>`;

    card.querySelectorAll("[data-field]").forEach((control) => {
      const field = control.dataset.field;
      const eventName = control.matches("textarea") ? "input" : "change";
      control.addEventListener(eventName, () => {
        let value;
        if (control.type === "checkbox") value = control.checked;
        else if (control.type === "radio") {
          if (!control.checked) return;
          value = control.value === "true" ? true : control.value === "false" ? false : Number(control.value);
        } else value = control.value || null;
        setValue(sample.sample_id, field, value);
      });
    });
    updateCardClass(card, sample.sample_id);
    return card;
  }

  function ratingField(sampleId, field, label, help, value) {
    const buttons = [1, 2, 3, 4, 5].map((number) => `
      <label class="score-choice">
        <input type="radio" name="${sampleId}-${field}" data-field="${field}" value="${number}" ${Number(value) === number ? "checked" : ""}>
        <span>${number}</span>
      </label>`).join("");
    return `<fieldset class="rating-field"><legend>${escapeHtml(label)}</legend><small>${escapeHtml(help)}</small><div class="score-row">${buttons}</div></fieldset>`;
  }

  function binaryField(sampleId, field, label, help, value) {
    return `<fieldset class="binary-field"><legend>${escapeHtml(label)}</legend><small>${escapeHtml(help)}</small><div class="binary-choice-row">
      <label><input type="radio" name="${sampleId}-${field}" data-field="${field}" value="true" ${value === true ? "checked" : ""}><span>Yes</span></label>
      <label><input type="radio" name="${sampleId}-${field}" data-field="${field}" value="false" ${value === false ? "checked" : ""}><span>No</span></label>
    </div></fieldset>`;
  }

  function updateCardStatus(sampleId) {
    const card = document.querySelector(`[data-sample-id="${CSS.escape(sampleId)}"]`);
    if (card) updateCardClass(card, sampleId);
  }

  function updateCardClass(card, sampleId) {
    const row = saved[sampleId] || {};
    const complete = isComplete(sampleId);
    card.classList.toggle("complete", complete);
    card.classList.toggle("flagged", Boolean(row.flag_for_follow_up));
    const pill = card.querySelector(".status-pill");
    if (pill) {
      pill.textContent = complete ? "Reviewed" : "Not reviewed";
      pill.classList.toggle("complete", complete);
    }
  }

  function updateProgressOnly() {
    const overall = completion(readySamples);
    const group = completion(samplesForGroup(activeGroup));
    const style = completion(samplesForStyle(activeStyle));
    const flagged = Object.values(saved).filter((row) => row.flag_for_follow_up).length;
    els.overallProgress.textContent = `${overall.complete} / ${overall.ready} reviewed`;
    els.overallGenerated.textContent = `${overall.ready} generated · ${data.blocked_coverage.length} documented unsupported`;
    els.groupProgressCompact.textContent = `${group.complete}/${group.ready}`;
    els.styleProgressText.textContent = `${style.complete} / ${style.ready} reviewed`;
    els.followupCount.textContent = `${flagged} flagged`;
    renderGroupNavigation();
    renderStyleNavigation();
  }

  function selectStyle(styleKey) {
    activeStyle = styleKey;
    activeGroup = stylesByKey.get(styleKey).group;
    persistSelection("group", activeGroup);
    persistSelection("style", activeStyle);
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function groupStyleIndex() {
    return data.groups[activeGroup].styles.indexOf(activeStyle);
  }

  function updatePreviousNextButtons() {
    const index = groupStyleIndex();
    els.previousStyle.disabled = index <= 0;
    els.nextStyle.disabled = index < 0 || index >= data.groups[activeGroup].styles.length - 1;
  }

  function moveStyle(delta) {
    const styles = data.groups[activeGroup].styles;
    const index = groupStyleIndex();
    const target = styles[index + delta];
    if (target) selectStyle(target);
  }

  function goToNextIncomplete() {
    const current = filteredStyleSamples({ ignoreSearch: true, ignoreIncomplete: true });
    const visibleIncomplete = current.find((sample) => !isComplete(sample.sample_id));
    if (visibleIncomplete) {
      const target = document.getElementById(`sample-${visibleIncomplete.sample_id}`);
      target?.scrollIntoView({ behavior: "smooth", block: "start" });
      target?.querySelector("audio")?.focus();
      return;
    }
    const orderedStyles = groupKeys.flatMap((groupKey) => data.groups[groupKey].styles);
    const currentIndex = orderedStyles.indexOf(activeStyle);
    for (let offset = 1; offset <= orderedStyles.length; offset += 1) {
      const candidateStyle = orderedStyles[(currentIndex + offset) % orderedStyles.length];
      if (samplesForStyle(candidateStyle).some((sample) => sample.status === "ready" && !isComplete(sample.sample_id))) {
        selectStyle(candidateStyle);
        requestAnimationFrame(goToNextIncomplete);
        return;
      }
    }
    showNotice("Every generated sample has been reviewed.");
  }

  function showNotice(message) {
    els.notice.textContent = message;
    els.notice.hidden = false;
    setTimeout(() => { els.notice.hidden = true; }, 5000);
  }

  function exportRows(scope, key) {
    let samples;
    if (scope === "style") samples = samplesForStyle(key);
    else if (scope === "group") samples = samplesForGroup(key);
    else samples = data.samples;
    const rows = samples
      .filter((sample) => saved[sample.sample_id])
      .map((sample) => ({ ...saved[sample.sample_id], sample_id: sample.sample_id }));
    const ready = samples.filter((sample) => sample.status === "ready");
    const complete = ready.filter((sample) => isComplete(sample.sample_id)).length;
    const payload = {
      schema_version: 1,
      round_id: data.round_id,
      export_scope: scope,
      export_key: key || "all",
      exported_at: new Date().toISOString(),
      summary: {
        ready_sample_count: ready.length,
        complete_sample_count: complete,
        incomplete_sample_count: ready.length - complete,
        follow_up_flag_count: rows.filter((row) => row.flag_for_follow_up).length,
      },
      rows,
    };
    downloadJson(`alexandria_round1_${scope}_${key || "all"}.json`, payload);
  }

  function downloadJson(filename, payload) {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
  }

  async function importFiles(files) {
    let imported = 0;
    let unknown = 0;
    let ignored = 0;
    for (const file of files) {
      try {
        const payload = JSON.parse(await file.text());
        if (payload.round_id && payload.round_id !== data.round_id) {
          ignored += 1;
          continue;
        }
        const rows = Array.isArray(payload) ? payload : Array.isArray(payload.rows) ? payload.rows : [];
        rows.forEach((row) => {
          if (!row?.sample_id || !byId.has(row.sample_id)) {
            unknown += 1;
            return;
          }
          const cleaned = { ...row };
          delete cleaned.round2_disposition;
          saved[row.sample_id] = { ...saved[row.sample_id], ...cleaned };
          imported += 1;
        });
      } catch (_) {
        ignored += 1;
      }
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    render();
    els.importSummary.textContent = `${imported} result rows merged. ${unknown} unknown sample IDs skipped. ${ignored} files ignored.`;
    if (typeof els.importDialog.showModal === "function") els.importDialog.showModal();
    else showNotice(els.importSummary.textContent);
  }

  els.previousStyle.addEventListener("click", () => moveStyle(-1));
  els.nextStyle.addEventListener("click", () => moveStyle(1));
  els.nextIncomplete.addEventListener("click", goToNextIncomplete);
  els.exportStyle.addEventListener("click", () => exportRows("style", activeStyle));
  els.exportGroup.addEventListener("click", () => exportRows("group", activeGroup));
  els.exportAll.addEventListener("click", () => exportRows("cumulative", "all"));
  els.importResults.addEventListener("change", async (event) => {
    await importFiles([...event.target.files]);
    event.target.value = "";
  });
  els.identityFilter.addEventListener("change", () => {
    identityFilter = els.identityFilter.value;
    renderReferences();
    renderSamples();
  });
  els.search.addEventListener("input", () => {
    searchQuery = els.search.value.trim();
    renderSamples();
  });
  els.incompleteOnly.addEventListener("change", () => {
    incompleteOnly = els.incompleteOnly.checked;
    renderSamples();
  });

  document.addEventListener("keydown", (event) => {
    if (event.target.matches("input, textarea, select, button")) return;
    if (event.key === "ArrowLeft") moveStyle(-1);
    if (event.key === "ArrowRight") moveStyle(1);
    if (event.key.toLowerCase() === "n") goToNextIncomplete();
  });

  render();
})();
