(() => {
  const data = window.TRANSCRIPT_GUIDED_SOURCE_DATA;
  if (!data || !Array.isArray(data.rows) || !data.rows.length) throw new Error("Transcript-guided review data is missing.");
  const storageKey = `alexandria:transcript-guided-source:${data.round_id}`;
  const fields = ["speaker_role_decision","boundary_decision","primary_emotion","secondary_emotion","dramatic_function","intensity_1_to_5","audio_cleanliness_decision","reference_decision"];
  let saved = {}; try { saved = JSON.parse(localStorage.getItem(storageKey) || "{}"); } catch {}
  let currentIndex = 0;
  const $ = (selector) => document.querySelector(selector);
  const audio = { target: $("#target-audio"), candidate: $("#candidate-audio") };

  function record(row) {
    if (!saved[row.clip_id]) {
      saved[row.clip_id] = {
        clip_id: row.clip_id,
        revision: 0,
        primary_emotion: row.assistant_primary_emotion,
        secondary_emotion: row.assistant_secondary_emotion,
        dramatic_function: row.assistant_dramatic_function,
        intensity_1_to_5: String(row.assistant_intensity_1_to_5),
      };
    }
    return saved[row.clip_id];
  }
  function isComplete(row) {
    const item = record(row);
    return fields.every((field) => String(item[field] ?? "").trim() !== "");
  }
  function persist(row, field, value) {
    const item = record(row);
    item[field] = value;
    item.revision = Number(item.revision || 0) + 1;
    item.updated_at = new Date().toISOString();
    localStorage.setItem(storageKey, JSON.stringify(saved));
    refresh();
  }
  function loadAudio(row) {
    for (const element of Object.values(audio)) { element.pause(); element.removeAttribute("src"); element.load(); }
    audio.target.src = row.target_audio;
    audio.candidate.src = row.candidate_audio;
    $("#target-link").href = row.target_audio;
    $("#candidate-link").href = row.candidate_audio;
    audio.target.load(); audio.candidate.load();
  }
  function drawNav() {
    const nav = $("#route-nav"); nav.replaceChildren();
    data.rows.forEach((row, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = String(index + 1);
      button.classList.toggle("active", index === currentIndex);
      button.classList.toggle("complete", isComplete(row));
      button.title = `${row.target_label}: ${row.assistant_primary_emotion}`;
      button.onclick = () => { currentIndex = index; render(); };
      nav.append(button);
    });
  }
  function refresh() {
    const complete = data.rows.filter(isComplete).length;
    $("#progress").textContent = `${complete} / ${data.rows.length} complete`;
    const done = isComplete(data.rows[currentIndex]);
    $("#status").textContent = done ? "Complete" : "Pending";
    $("#status").classList.toggle("complete", done);
    drawNav();
  }
  function render() {
    const row = data.rows[currentIndex];
    const item = record(row);
    $("#ordinal").textContent = `Candidate ${currentIndex + 1} of ${data.rows.length}`;
    $("#target-label").textContent = row.target_label;
    $("#primary-heading").textContent = row.assistant_primary_emotion;
    $("#source-line").textContent = `${row.source_title} · ${row.transcript_start_seconds.toFixed(2)}–${row.transcript_end_seconds.toFixed(2)} seconds · ${row.assistant_speaker_role}`;
    $("#selected-transcript").textContent = row.selected_transcript;
    $("#selection-reason").textContent = row.selection_reason;
    $("#context-transcript").textContent = row.context_transcript;
    fields.forEach((field) => {
      const element = document.querySelector(`[data-field="${field}"]`);
      element.value = item[field] ?? "";
      element.onchange = () => persist(row, field, element.value);
      if (element.tagName === "INPUT") element.oninput = () => persist(row, field, element.value);
    });
    const notes = document.querySelector('[data-field="notes"]');
    notes.value = item.notes || "";
    notes.oninput = () => persist(row, "notes", notes.value);
    $("#previous").disabled = currentIndex === 0;
    $("#next").disabled = currentIndex === data.rows.length - 1;
    loadAudio(row);
    refresh();
    window.scrollTo({ top: 0, behavior: "instant" });
  }
  function exportReview() {
    const rows = data.rows.map((row) => ({
      clip_id: row.clip_id,
      target: row.target,
      target_label: row.target_label,
      source_title: row.source_title,
      transcript_start_seconds: row.transcript_start_seconds,
      transcript_end_seconds: row.transcript_end_seconds,
      selected_transcript: row.selected_transcript,
      assistant_speaker_role: row.assistant_speaker_role,
      assistant_primary_emotion: row.assistant_primary_emotion,
      assistant_secondary_emotion: row.assistant_secondary_emotion,
      assistant_dramatic_function: row.assistant_dramatic_function,
      assistant_intensity_1_to_5: row.assistant_intensity_1_to_5,
      selection_reason: row.selection_reason,
      ...record(row),
    }));
    const payload = {
      schema_version: 1,
      round_id: data.round_id,
      exported_at: new Date().toISOString(),
      summary: {
        candidate_count: rows.length,
        complete_count: rows.filter((row) => fields.every((field) => String(row[field] ?? "").trim() !== "")).length,
        approved_count: rows.filter((row) => row.reference_decision === "approve").length,
      },
      rows,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "alexandria_transcript_guided_source_review.json";
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 500);
    $("#done").showModal();
  }
  $("#previous").onclick = () => { if (currentIndex > 0) { currentIndex -= 1; render(); } };
  $("#next").onclick = () => { if (currentIndex < data.rows.length - 1) { currentIndex += 1; render(); } };
  $("#reload").onclick = () => loadAudio(data.rows[currentIndex]);
  $("#export").onclick = exportReview;
  render();
})();
