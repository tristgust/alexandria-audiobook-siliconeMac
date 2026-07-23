(() => {
  const data = window.NARRATOR_INDEXTTS2_REFERENCE_DATA;
  if (!data || !Array.isArray(data.rows)) throw new Error("Reference validation data is missing.");

  const storageKey = `alexandria:narrator-indextts2-reference:${data.schema_version}:${data.round_id}`;
  const cards = document.querySelector("#cards");
  const template = document.querySelector("#card-template");
  const progress = document.querySelector("#progress");
  const exportButton = document.querySelector("#export");
  const exportDialog = document.querySelector("#export-dialog");
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(storageKey) || "{}"); } catch { saved = {}; }

  const ratingFields = [
    "identity_1_to_5",
    "delivery_1_to_5",
    "naturalness_1_to_5",
    "artifact_severity_1_to_5",
  ];
  const booleanFields = [
    "spoken_text_matches_expected",
    "requested_mode_is_clear",
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
      const card = cards.querySelector(`[data-sample-id="${row.sample_id}"]`);
      const complete = isComplete(recordFor(row.sample_id));
      if (complete) completed += 1;
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

  function render() {
    data.rows.forEach((row) => {
      const fragment = template.content.cloneNode(true);
      const card = fragment.querySelector(".review-card");
      card.dataset.sampleId = row.sample_id;
      card.querySelector(".ordinal").textContent = `Sample ${row.ordinal} of ${data.rows.length}`;
      card.querySelector(".style-label").textContent = row.style_label;
      card.querySelector(".scene").textContent = row.emotion_scene;
      card.querySelector(".reference-text").textContent = row.emotion_reference_text;
      card.querySelector(".target-text").textContent = row.target_text;
      card.querySelector(".identity-audio").src = row.identity_audio;
      card.querySelector(".emotion-audio").src = row.emotion_audio;
      card.querySelector(".generated-audio").src = row.audio;

      ratingFields.forEach((field) => addScale(card, row.sample_id, field));
      booleanFields.forEach((field) => {
        const input = card.querySelector(`input[data-field="${field}"]`);
        const existing = recordFor(row.sample_id)[field];
        input.checked = existing === true;
        if (typeof existing === "boolean") input.indeterminate = false;
        input.addEventListener("change", () => persist(row.sample_id, field, input.checked));
      });
      const notes = card.querySelector('textarea[data-field="notes"]');
      notes.value = recordFor(row.sample_id).notes || "";
      notes.addEventListener("input", () => persist(row.sample_id, "notes", notes.value));
      cards.append(fragment);
    });
    refreshStatus();
  }

  function exportReview() {
    const rows = data.rows.map((row) => ({
      sample_id: row.sample_id,
      style: row.style,
      ...recordFor(row.sample_id),
    }));
    const payload = {
      schema_version: 1,
      round_id: data.round_id,
      exported_at: new Date().toISOString(),
      summary: {
        ready_sample_count: data.rows.length,
        complete_sample_count: rows.filter(isComplete).length,
        incomplete_sample_count: rows.filter((row) => !isComplete(row)).length,
        approved_count: rows.filter((row) => row.approve_for_candidate === true).length,
      },
      rows,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "alexandria_narrator_indextts2_reference_validation.json";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 500);
    exportDialog.showModal();
  }

  exportButton.addEventListener("click", exportReview);
  render();
})();
