(() => {
  const data = window.LAZY_VOICE_FOLLOWUP_DATA;
  if (!data || !Array.isArray(data.rows) || data.rows.length === 0) {
    throw new Error("Follow-up review data is missing.");
  }

  const storageKey = `alexandria:lazy-voice-followup:${data.schema_version}:${data.round_id}`;
  const progress = document.querySelector("#progress");
  const exportButton = document.querySelector("#export");
  const exportDialog = document.querySelector("#export-dialog");
  const routeNav = document.querySelector("#route-nav");
  const card = document.querySelector("#review-card");
  const ordinal = document.querySelector("#ordinal");
  const targetLabel = document.querySelector("#target-label");
  const modeLabel = document.querySelector("#mode-label");
  const purpose = document.querySelector("#purpose");
  const expectedText = document.querySelector("#expected-text");
  const status = document.querySelector("#status");
  const technicalStatus = document.querySelector("#technical-status");
  const previousButton = document.querySelector("#previous");
  const nextButton = document.querySelector("#next");
  const reloadButton = document.querySelector("#reload-audio");
  const audioState = document.querySelector("#audio-state");
  let currentIndex = 0;
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(storageKey) || "{}"); } catch { saved = {}; }

  const ratingFields = [
    "identity_1_to_5",
    "delivery_1_to_5",
    "naturalness_1_to_5",
    "audio_cleanliness_1_to_5",
  ];
  const booleanFields = [
    "spoken_text_matches_expected",
    "requested_delivery_is_clear",
    "approve_for_candidate",
  ];
  const audioParts = [
    { key: "target_audio", element: document.querySelector("#target-audio"), link: document.querySelector("#target-link"), error: document.querySelector("#target-error") },
    { key: "reference_audio", element: document.querySelector("#reference-audio"), link: document.querySelector("#reference-link"), error: document.querySelector("#reference-error") },
    { key: "generated_audio", element: document.querySelector("#generated-audio"), link: document.querySelector("#generated-link"), error: document.querySelector("#generated-error") },
  ];

  function recordFor(sampleId) {
    if (!saved[sampleId]) saved[sampleId] = { sample_id: sampleId, revision: 0 };
    return saved[sampleId];
  }

  function isComplete(record) {
    return ratingFields.every((field) => Number.isInteger(record[field]))
      && booleanFields.every((field) => typeof record[field] === "boolean");
  }

  function persist(sampleId, field, value) {
    const record = recordFor(sampleId);
    record[field] = value;
    record.revision = Number(record.revision || 0) + 1;
    record.updated_at = new Date().toISOString();
    localStorage.setItem(storageKey, JSON.stringify(saved));
    refreshProgress();
    refreshStatus();
  }

  function refreshProgress() {
    const complete = data.rows.filter((row) => isComplete(recordFor(row.sample_id))).length;
    progress.textContent = `${complete} / ${data.rows.length} complete`;
    [...routeNav.querySelectorAll("button")].forEach((button, index) => {
      const done = isComplete(recordFor(data.rows[index].sample_id));
      button.classList.toggle("complete", done);
      button.setAttribute("aria-label", `${button.textContent}${done ? ", complete" : ", pending"}`);
    });
  }

  function refreshStatus() {
    const row = data.rows[currentIndex];
    const complete = isComplete(recordFor(row.sample_id));
    status.textContent = complete ? "Complete" : "Pending";
    status.classList.toggle("complete", complete);
  }

  function clearAudio() {
    audioParts.forEach(({ element, error }) => {
      element.pause();
      element.removeAttribute("src");
      element.load();
      error.textContent = "";
    });
  }

  function loadAudio(row) {
    clearAudio();
    audioState.textContent = "Loading three audio files for this card…";
    let metadataCount = 0;
    audioParts.forEach(({ key, element, link, error }) => {
      const source = row[key];
      link.href = source;
      element.src = source;
      element.addEventListener("loadedmetadata", () => {
        metadataCount += 1;
        error.textContent = Number.isFinite(element.duration) ? `${element.duration.toFixed(1)} seconds` : "Audio metadata loaded";
        if (metadataCount === audioParts.length) audioState.textContent = "All three audio files are ready.";
      }, { once: true });
      element.addEventListener("error", () => {
        const code = element.error?.code || "unknown";
        error.textContent = `Player error ${code}. Use the direct audio link below.`;
        audioState.textContent = "A player failed. Direct audio links remain available.";
      }, { once: true });
      element.load();
    });
  }

  function renderScales(row) {
    ratingFields.forEach((field) => {
      const scale = card.querySelector(`fieldset[data-field="${field}"] .scale`);
      scale.replaceChildren();
      for (let value = 1; value <= 5; value += 1) {
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = "radio";
        input.name = `${row.sample_id}-${field}`;
        input.value = String(value);
        input.dataset.field = field;
        input.checked = recordFor(row.sample_id)[field] === value;
        input.addEventListener("change", () => persist(row.sample_id, field, value));
        label.append(input, document.createTextNode(String(value)));
        scale.append(label);
      }
    });
  }

  function renderChecks(row) {
    booleanFields.forEach((field) => {
      const input = card.querySelector(`input[data-field="${field}"]`);
      input.checked = recordFor(row.sample_id)[field] === true;
      input.onchange = () => persist(row.sample_id, field, input.checked);
    });
    const notes = card.querySelector('textarea[data-field="notes"]');
    notes.value = recordFor(row.sample_id).notes || "";
    notes.oninput = () => persist(row.sample_id, "notes", notes.value);
  }

  function renderNav() {
    routeNav.replaceChildren();
    data.rows.forEach((row, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `${index + 1}. ${row.target_label} · ${row.short_label || row.mode_label}`;
      button.classList.toggle("active", index === currentIndex);
      button.addEventListener("click", () => {
        currentIndex = index;
        render();
      });
      routeNav.append(button);
    });
  }

  function render() {
    const row = data.rows[currentIndex];
    ordinal.textContent = `Follow-up ${currentIndex + 1} of ${data.rows.length}`;
    targetLabel.textContent = row.target_label;
    modeLabel.textContent = row.mode_label;
    purpose.textContent = row.purpose_label;
    expectedText.textContent = row.expected_text;
    technicalStatus.textContent = row.technical_pass
      ? "Passed automatic stability checks"
      : "Near-threshold experimental repair — judge identity and delivery carefully";
    technicalStatus.className = row.technical_pass ? "pass" : "caution";
    renderScales(row);
    renderChecks(row);
    loadAudio(row);
    previousButton.disabled = currentIndex === 0;
    nextButton.disabled = currentIndex === data.rows.length - 1;
    renderNav();
    refreshProgress();
    refreshStatus();
    window.scrollTo({ top: 0, behavior: "instant" });
  }

  function exportReview() {
    const rows = data.rows.map((row) => ({
      sample_id: row.sample_id,
      target_key: row.target_key,
      target_label: row.target_label,
      mode: row.mode,
      mode_label: row.mode_label,
      purpose: row.purpose,
      ...recordFor(row.sample_id),
    }));
    const payload = {
      schema_version: 1,
      round_id: data.round_id,
      exported_at: new Date().toISOString(),
      summary: {
        ready_sample_count: rows.length,
        complete_sample_count: rows.filter(isComplete).length,
        incomplete_sample_count: rows.filter((row) => !isComplete(row)).length,
        approved_count: rows.filter((row) => row.approve_for_candidate === true).length,
      },
      rows,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "alexandria_targeted_voice_followup_review.json";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 500);
    exportDialog.showModal();
  }

  previousButton.addEventListener("click", () => {
    if (currentIndex > 0) {
      currentIndex -= 1;
      render();
    }
  });
  nextButton.addEventListener("click", () => {
    if (currentIndex < data.rows.length - 1) {
      currentIndex += 1;
      render();
    }
  });
  reloadButton.addEventListener("click", () => loadAudio(data.rows[currentIndex]));
  exportButton.addEventListener("click", exportReview);
  render();
})();
