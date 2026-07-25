(() => {
  const data = window.THREE_VOICE_SOURCE_ATLAS_DATA;
  if (!data || !Array.isArray(data.rows) || !data.rows.length) {
    throw new Error("Three-voice source atlas data is missing.");
  }

  const storageKey = `alexandria:three-voice-source-atlas:${data.round_id}`;
  const filterKey = `${storageKey}:filters`;
  const decisionFields = [
    "speaker_role_decision",
    "boundary_decision",
    "primary_emotion",
    "secondary_emotion",
    "dramatic_function",
    "intensity_1_to_5",
    "audio_cleanliness_decision",
    "reference_decision",
  ];
  const $ = (selector) => document.querySelector(selector);
  const audio = { target: $("#target-audio"), candidate: $("#candidate-audio") };

  let saved = {};
  let filters = { target: "all", status: "all", search: "" };
  try { saved = JSON.parse(localStorage.getItem(storageKey) || "{}"); } catch {}
  try { filters = { ...filters, ...JSON.parse(localStorage.getItem(filterKey) || "{}") }; } catch {}
  let currentClipId = data.rows[0].clip_id;

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
    return decisionFields.every((field) => String(item[field] ?? "").trim() !== "");
  }

  function disposition(row) {
    const item = record(row);
    if (!isComplete(row)) return "pending";
    if (item.reference_decision === "approve") return "approved";
    return "attention";
  }

  function persistFilters() {
    localStorage.setItem(filterKey, JSON.stringify(filters));
  }

  function saveRecord(row, updates) {
    const item = record(row);
    Object.assign(item, updates);
    item.revision = Number(item.revision || 0) + 1;
    item.updated_at = new Date().toISOString();
    localStorage.setItem(storageKey, JSON.stringify(saved));
  }

  function persist(row, field, value) {
    saveRecord(row, { [field]: value });
    refresh({ preserveCurrent: true });
  }

  function visibleRows() {
    const query = filters.search.trim().toLowerCase();
    return data.rows.filter((row) => {
      if (filters.target !== "all" && row.target !== filters.target) return false;
      if (filters.status !== "all" && disposition(row) !== filters.status) return false;
      if (!query) return true;
      const haystack = [
        row.target_label,
        row.source_title,
        row.selected_transcript,
        row.assistant_primary_emotion,
        row.assistant_secondary_emotion,
        row.assistant_dramatic_function,
        row.coverage_gap,
        record(row).notes,
      ].join(" ").toLowerCase();
      return haystack.includes(query);
    });
  }

  function currentRow(rows = visibleRows()) {
    return rows.find((row) => row.clip_id === currentClipId) || rows[0] || null;
  }

  function stopAudio() {
    for (const element of Object.values(audio)) {
      element.pause();
      element.removeAttribute("src");
      element.load();
    }
  }

  function loadAudio(row) {
    stopAudio();
    audio.target.src = row.target_audio;
    audio.candidate.src = row.candidate_audio;
    $("#target-link").href = row.target_audio;
    $("#candidate-link").href = row.candidate_audio;
    audio.target.load();
    audio.candidate.load();
  }

  function drawFilters() {
    const targetOptions = [
      ["all", "All"],
      ["narrator", "Narrator"],
      ["benny", "Benny"],
      ["doctor", "Doctor"],
    ];
    const statusOptions = [
      ["all", "All"],
      ["pending", "Pending"],
      ["approved", "Approved"],
      ["attention", "Repair / reject"],
    ];
    for (const [containerSelector, options, key] of [
      ["#target-filters", targetOptions, "target"],
      ["#status-filters", statusOptions, "status"],
    ]) {
      const container = $(containerSelector);
      container.replaceChildren();
      for (const [value, label] of options) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = label;
        button.classList.toggle("active", filters[key] === value);
        button.onclick = () => {
          filters[key] = value;
          persistFilters();
          refresh({ preserveCurrent: false });
        };
        container.append(button);
      }
    }
    $("#search").value = filters.search;
  }

  function drawCoverage() {
    const container = $("#coverage");
    container.replaceChildren();
    for (const target of ["narrator", "benny", "doctor"]) {
      const rows = data.rows.filter((row) => row.target === target);
      const complete = rows.filter(isComplete).length;
      const approved = rows.filter((row) => record(row).reference_decision === "approve").length;
      const card = document.createElement("div");
      card.className = "coverage-card";
      const label = rows[0]?.target_label || target;
      card.innerHTML = `<strong>${label}: ${complete}/${rows.length}</strong><span>${approved} approved · ${rows.length - complete} pending</span>`;
      container.append(card);
    }
  }

  function drawNav(rows) {
    const nav = $("#route-nav");
    nav.replaceChildren();
    rows.forEach((row, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = String(index + 1);
      button.classList.toggle("active", row.clip_id === currentClipId);
      button.classList.toggle("complete", isComplete(row));
      button.classList.toggle("rejected", isComplete(row) && record(row).reference_decision !== "approve");
      button.title = `${row.target_label}: ${row.assistant_primary_emotion}`;
      button.onclick = () => {
        currentClipId = row.clip_id;
        render();
      };
      nav.append(button);
    });
  }

  function statusLabel(row) {
    if (!isComplete(row)) return ["Pending", ""];
    const decision = record(row).reference_decision;
    if (decision === "approve") return ["Approved", "complete"];
    if (decision === "mine_nearby") return ["Mine nearby", "rejected"];
    return ["Rejected", "rejected"];
  }

  function fillCorrectionFields(row) {
    const item = record(row);
    for (const field of decisionFields) {
      const element = document.querySelector(`[data-field="${field}"]`);
      element.value = item[field] ?? "";
      element.onchange = () => persist(row, field, element.value);
      if (element.tagName === "INPUT") {
        element.oninput = () => persist(row, field, element.value);
      }
    }
    const notes = document.querySelector('[data-field="notes"]');
    notes.value = item.notes || "";
    notes.oninput = () => persist(row, "notes", notes.value);
  }

  function render() {
    drawFilters();
    drawCoverage();
    const rows = visibleRows();
    const row = currentRow(rows);
    $("#card").hidden = !row;
    $("#empty").hidden = Boolean(row);
    if (!row) {
      stopAudio();
      drawNav([]);
      updateProgress();
      return;
    }
    currentClipId = row.clip_id;
    const index = rows.findIndex((candidate) => candidate.clip_id === row.clip_id);
    $("#ordinal").textContent = `Candidate ${index + 1} of ${rows.length} shown · ${data.rows.length} total`;
    $("#target-label").textContent = row.target_label;
    $("#primary-heading").textContent = row.assistant_primary_emotion;
    $("#source-line").textContent = `${row.source_title} · ${row.transcript_start_seconds.toFixed(2)}–${row.transcript_end_seconds.toFixed(2)} seconds · ${row.assistant_speaker_role}`;
    $("#selected-transcript").textContent = row.selected_transcript;
    $("#selection-reason").textContent = row.selection_reason;
    $("#context-transcript").textContent = row.context_transcript;
    $("#source-scene").textContent = row.source_scene;
    $("#coverage-gap").textContent = row.coverage_gap.replaceAll("_", " ");
    $("#speaker-certainty").textContent = row.speaker_certainty;
    $("#transcript-score").textContent = `${Math.round(row.verification_similarity * 100)}% ASR similarity`;
    $("#judgment-primary").textContent = row.assistant_primary_emotion;
    $("#judgment-secondary").textContent = row.assistant_secondary_emotion;
    $("#judgment-function").textContent = row.assistant_dramatic_function;
    $("#judgment-intensity").textContent = `${row.assistant_intensity_1_to_5} / 5`;

    const warningParts = [];
    if (row.speaker_certainty !== "high") warningParts.push(`Speaker certainty is ${row.speaker_certainty}.`);
    if (row.source_role_warning) warningParts.push(row.source_role_warning);
    const warning = $("#warning");
    warning.hidden = warningParts.length === 0;
    warning.textContent = warningParts.join(" ");

    const [label, className] = statusLabel(row);
    $("#status").textContent = label;
    $("#status").className = className;
    fillCorrectionFields(row);
    $("#previous").disabled = index <= 0;
    $("#next").disabled = index >= rows.length - 1;
    loadAudio(row);
    drawNav(rows);
    updateProgress();
    window.scrollTo({ top: 0, behavior: "instant" });
  }

  function refresh({ preserveCurrent = true } = {}) {
    const rows = visibleRows();
    if (!preserveCurrent || !rows.some((row) => row.clip_id === currentClipId)) {
      currentClipId = rows[0]?.clip_id || "";
    }
    render();
  }

  function updateProgress() {
    const complete = data.rows.filter(isComplete).length;
    $("#progress").textContent = `${complete} / ${data.rows.length} complete`;
  }

  function quickDecision(kind) {
    const rowsBefore = visibleRows();
    const row = currentRow(rowsBefore);
    if (!row) return;
    const indexBefore = rowsBefore.findIndex((candidate) => candidate.clip_id === row.clip_id);
    const common = {
      speaker_role_decision: "correct",
      boundary_decision: "correct",
      primary_emotion: row.assistant_primary_emotion,
      secondary_emotion: row.assistant_secondary_emotion,
      dramatic_function: row.assistant_dramatic_function,
      intensity_1_to_5: String(row.assistant_intensity_1_to_5),
    };
    if (kind === "clean") {
      saveRecord(row, { ...common, audio_cleanliness_decision: "clean", reference_decision: "approve" });
    } else if (kind === "cleanup") {
      saveRecord(row, { ...common, audio_cleanliness_decision: "usable_with_cleanup", reference_decision: "approve" });
    } else if (kind === "mine") {
      saveRecord(row, { ...common, audio_cleanliness_decision: "usable_with_cleanup", reference_decision: "mine_nearby" });
    } else {
      saveRecord(row, { ...common, audio_cleanliness_decision: "not_clean", reference_decision: "reject" });
    }
    const rowsAfter = visibleRows();
    const retainedIndex = rowsAfter.findIndex((candidate) => candidate.clip_id === row.clip_id);
    if (retainedIndex >= 0) {
      currentClipId = rowsAfter[retainedIndex + 1]?.clip_id || row.clip_id;
    } else {
      currentClipId = rowsAfter[Math.min(indexBefore, Math.max(0, rowsAfter.length - 1))]?.clip_id || "";
    }
    render();
  }

  function move(offset) {
    const rows = visibleRows();
    const row = currentRow(rows);
    if (!row) return;
    const index = rows.findIndex((candidate) => candidate.clip_id === row.clip_id);
    const next = rows[index + offset];
    if (next) {
      currentClipId = next.clip_id;
      render();
    }
  }

  function exportReview() {
    const rows = data.rows.map((row) => ({
      clip_id: row.clip_id,
      target: row.target,
      target_label: row.target_label,
      source_title: row.source_title,
      youtube_id: row.youtube_id,
      transcript_start_seconds: row.transcript_start_seconds,
      transcript_end_seconds: row.transcript_end_seconds,
      selected_transcript: row.selected_transcript,
      assistant_speaker_role: row.assistant_speaker_role,
      assistant_primary_emotion: row.assistant_primary_emotion,
      assistant_secondary_emotion: row.assistant_secondary_emotion,
      assistant_dramatic_function: row.assistant_dramatic_function,
      assistant_intensity_1_to_5: row.assistant_intensity_1_to_5,
      speaker_certainty: row.speaker_certainty,
      source_role_warning: row.source_role_warning,
      coverage_gap: row.coverage_gap,
      selection_reason: row.selection_reason,
      ...record(row),
    }));
    const payload = {
      schema_version: 1,
      round_id: data.round_id,
      exported_at: new Date().toISOString(),
      summary: {
        candidate_count: rows.length,
        complete_count: rows.filter((row) => decisionFields.every((field) => String(row[field] ?? "").trim() !== "")).length,
        approved_count: rows.filter((row) => row.reference_decision === "approve").length,
        mine_nearby_count: rows.filter((row) => row.reference_decision === "mine_nearby").length,
        rejected_count: rows.filter((row) => row.reference_decision === "reject").length,
        target_counts: data.target_counts,
      },
      rows,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "alexandria_three_voice_source_atlas_review.json";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 500);
    $("#done").showModal();
  }

  $("#search").addEventListener("input", (event) => {
    filters.search = event.target.value;
    persistFilters();
    refresh({ preserveCurrent: false });
  });
  $("#previous").onclick = () => move(-1);
  $("#next").onclick = () => move(1);
  $("#reload").onclick = () => {
    const row = currentRow();
    if (row) loadAudio(row);
  };
  $("#approve-clean").onclick = () => quickDecision("clean");
  $("#approve-cleanup").onclick = () => quickDecision("cleanup");
  $("#mine-nearby").onclick = () => quickDecision("mine");
  $("#reject").onclick = () => quickDecision("reject");
  $("#export").onclick = exportReview;
  $("#clear-filters").onclick = () => {
    filters = { target: "all", status: "all", search: "" };
    persistFilters();
    refresh({ preserveCurrent: false });
  };

  document.addEventListener("keydown", (event) => {
    const tag = document.activeElement?.tagName;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(tag)) return;
    const key = event.key.toLowerCase();
    if (event.code === "Space") {
      event.preventDefault();
      if (audio.candidate.paused) audio.candidate.play(); else audio.candidate.pause();
    } else if (key === "a") {
      quickDecision("clean");
    } else if (key === "u") {
      quickDecision("cleanup");
    } else if (key === "m") {
      quickDecision("mine");
    } else if (key === "r") {
      quickDecision("reject");
    } else if (key === "j") {
      move(-1);
    } else if (key === "k") {
      move(1);
    }
  });

  render();
})();
