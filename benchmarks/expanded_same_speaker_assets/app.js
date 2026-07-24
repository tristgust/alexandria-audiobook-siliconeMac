(() => {
  const data = window.EXPANDED_SAME_SPEAKER_DATA;
  if (!data || !Array.isArray(data.rows)) throw new Error("Expanded same-speaker review data is missing.");

  const storageKey = `alexandria:expanded-same-speaker:${data.schema_version}:${data.round_id}`;
  const cardsRoot = document.querySelector("#cards");
  const template = document.querySelector("#card-template");
  const nav = document.querySelector("#target-nav");
  const progress = document.querySelector("#progress");
  const exportButton = document.querySelector("#export");
  const exportDialog = document.querySelector("#export-dialog");
  let currentTarget = "all";
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
    refreshStatus();
  }

  function refreshStatus() {
    let completed = 0;
    data.rows.forEach((row) => {
      const record = recordFor(row.sample_id);
      const complete = isComplete(record);
      if (complete) completed += 1;
      const card = cardsRoot.querySelector(`[data-sample-id="${row.sample_id}"]`);
      if (card) {
        const status = card.querySelector(".status");
        status.textContent = complete ? "Complete" : "Pending";
        status.classList.toggle("complete", complete);
      }
    });
    progress.textContent = `${completed} / ${data.rows.length} complete`;
  }

  function addScale(card, sampleId, field) {
    const scale = card.querySelector(`fieldset[data-field="${field}"] .scale`);
    for (let value = 1; value <= 5; value += 1) {
      const label = document.createElement("label");
      label.title = value === 1 ? "Poor" : value === 5 ? "Excellent" : String(value);
      const input = document.createElement("input");
      input.type = "radio";
      input.name = `${sampleId}-${field}`;
      input.value = String(value);
      input.dataset.field = field;
      input.checked = recordFor(sampleId)[field] === value;
      input.addEventListener("change", () => persist(sampleId, field, value));
      label.append(input, document.createTextNode(String(value)));
      scale.append(label);
    }
  }

  function renderCards() {
    cardsRoot.replaceChildren();
    const visible = data.rows.filter((row) => currentTarget === "all" || row.target_key === currentTarget);
    visible.forEach((row) => {
      const fragment = template.content.cloneNode(true);
      const card = fragment.querySelector(".review-card");
      card.dataset.sampleId = row.sample_id;
      card.dataset.targetKey = row.target_key;
      card.querySelector(".ordinal").textContent = `Sample ${row.ordinal} of ${data.rows.length}`;
      card.querySelector(".target-label").textContent = row.target_label;
      card.querySelector(".mode-label").textContent = row.mode_label;
      card.querySelector(".expected-text").textContent = row.expected_text;
      card.querySelector(".target-audio").src = row.target_audio;
      card.querySelector(".reference-audio").src = row.reference_audio;
      card.querySelector(".generated-audio").src = row.generated_audio;
      const technical = card.querySelector(".technical-status");
      technical.textContent = row.technical_pass
        ? "Passed automatic identity, text, pitch, and clipping checks"
        : "Automatic checks found a concern — listen carefully";
      technical.classList.add(row.technical_pass ? "pass" : "fail");

      ratingFields.forEach((field) => addScale(card, row.sample_id, field));
      booleanFields.forEach((field) => {
        const input = card.querySelector(`input[data-field="${field}"]`);
        const existing = recordFor(row.sample_id)[field];
        input.checked = existing === true;
        input.addEventListener("change", () => persist(row.sample_id, field, input.checked));
      });
      const notes = card.querySelector('textarea[data-field="notes"]');
      notes.value = recordFor(row.sample_id).notes || "";
      notes.addEventListener("input", () => persist(row.sample_id, "notes", notes.value));
      cardsRoot.append(fragment);
    });
    refreshStatus();
  }

  function renderNav() {
    const options = [{ key: "all", label: `All ${data.rows.length}` }, ...data.target_order.map((key) => ({
      key,
      label: data.rows.find((row) => row.target_key === key)?.target_label || key,
    }))];
    options.forEach((option) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = option.label;
      button.classList.toggle("active", option.key === currentTarget);
      button.addEventListener("click", () => {
        currentTarget = option.key;
        [...nav.querySelectorAll("button")].forEach((item) => item.classList.toggle("active", item === button));
        renderCards();
      });
      nav.append(button);
    });
  }

  function exportReview() {
    const rows = data.rows.map((row) => ({
      sample_id: row.sample_id,
      target_key: row.target_key,
      target_label: row.target_label,
      mode: row.mode,
      mode_label: row.mode_label,
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
    link.download = "alexandria_expanded_same_speaker_review.json";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 500);
    exportDialog.showModal();
  }

  exportButton.addEventListener("click", exportReview);
  renderNav();
  renderCards();
})();
