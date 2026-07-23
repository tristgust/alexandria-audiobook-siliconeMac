(() => {
  "use strict";

  const data = window.NARRATOR_TRIAGE_DATA;
  if (!data || !Array.isArray(data.rows)) {
    document.body.innerHTML = "<p>Review data could not be loaded.</p>";
    throw new Error("NARRATOR_TRIAGE_DATA is missing");
  }

  const storageKey = `alexandria:${data.round_id}:review`;
  const elements = {
    reviewed: document.getElementById("reviewed-count"),
    accepted: document.getElementById("accepted-count"),
    rejected: document.getElementById("rejected-count"),
    acceptedDuration: document.getElementById("accepted-duration"),
    progress: document.getElementById("progress-fill"),
    statusFilter: document.getElementById("status-filter"),
    categoryFilter: document.getElementById("category-filter"),
    sourceFilter: document.getElementById("source-filter"),
    search: document.getElementById("search-input"),
    clearFilters: document.getElementById("clear-filters"),
    emptyClear: document.getElementById("empty-clear"),
    empty: document.getElementById("empty-state"),
    shell: document.getElementById("review-shell"),
    position: document.getElementById("clip-position"),
    title: document.getElementById("clip-title"),
    statusPill: document.getElementById("status-pill"),
    audio: document.getElementById("audio-player"),
    replay: document.getElementById("replay-button"),
    transcript: document.getElementById("transcript-input"),
    confirmed: document.getElementById("transcript-confirmed"),
    instruction: document.getElementById("instruction-input"),
    category: document.getElementById("category-input"),
    notes: document.getElementById("notes-input"),
    detailSource: document.getElementById("detail-source"),
    detailTime: document.getElementById("detail-time"),
    detailDuration: document.getElementById("detail-duration"),
    detailConfidence: document.getElementById("detail-confidence"),
    detailSnr: document.getElementById("detail-snr"),
    detailRate: document.getElementById("detail-rate"),
    reject: document.getElementById("reject-button"),
    previous: document.getElementById("previous-button"),
    skip: document.getElementById("skip-button"),
    next: document.getElementById("next-button"),
    accept: document.getElementById("accept-button"),
    exportButton: document.getElementById("export-button"),
    importFile: document.getElementById("import-file"),
    toast: document.getElementById("toast"),
  };

  const rowsById = new Map(data.rows.map((row) => [row.sample_id, row]));
  let toastTimer = null;
  let saveTimer = null;
  let currentId = null;

  function initialReview(row) {
    return {
      sample_id: row.sample_id,
      status: "pending",
      transcript: row.text,
      transcript_confirmed: false,
      instruction: row.suggested_instruction,
      category: row.category,
      notes: "",
      updated_at: null,
      revision: 0,
    };
  }

  function initialState() {
    const rows = {};
    for (const row of data.rows) rows[row.sample_id] = initialReview(row);
    return {
      schema_version: 1,
      round_id: data.round_id,
      revision: 0,
      updated_at: null,
      current_id: data.rows[0]?.sample_id || null,
      filters: { status: "pending", category: "all", source: "all", search: "" },
      rows,
    };
  }

  function loadState() {
    const base = initialState();
    try {
      const parsed = JSON.parse(localStorage.getItem(storageKey) || "null");
      if (!parsed || parsed.round_id !== data.round_id || typeof parsed.rows !== "object") return base;
      for (const row of data.rows) {
        const saved = parsed.rows[row.sample_id];
        if (saved && typeof saved === "object") {
          base.rows[row.sample_id] = { ...base.rows[row.sample_id], ...saved, sample_id: row.sample_id };
        }
      }
      base.revision = Number(parsed.revision || 0);
      base.updated_at = parsed.updated_at || null;
      base.current_id = rowsById.has(parsed.current_id) ? parsed.current_id : base.current_id;
      base.filters = { ...base.filters, ...(parsed.filters || {}) };
    } catch (error) {
      console.warn("Could not restore review state", error);
    }
    return base;
  }

  let state = loadState();

  function saveState({ bump = true } = {}) {
    if (bump) {
      state.revision += 1;
      state.updated_at = new Date().toISOString();
    }
    localStorage.setItem(storageKey, JSON.stringify(state));
  }

  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => saveState(), 150);
  }

  function showToast(message) {
    elements.toast.textContent = message;
    elements.toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => elements.toast.classList.remove("show"), 1800);
  }

  function formatDuration(seconds) {
    const rounded = Math.max(0, Math.round(Number(seconds || 0)));
    const minutes = Math.floor(rounded / 60);
    const remainder = rounded % 60;
    return `${minutes}:${String(remainder).padStart(2, "0")}`;
  }

  function formatSourceTime(seconds) {
    if (seconds === null || seconds === undefined) return "Not recorded";
    const value = Math.max(0, Math.floor(Number(seconds)));
    const hours = Math.floor(value / 3600);
    const minutes = Math.floor((value % 3600) / 60);
    const remainder = value % 60;
    return hours
      ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
      : `${minutes}:${String(remainder).padStart(2, "0")}`;
  }

  function populateOptions() {
    for (const [key, label] of Object.entries(data.category_labels || {})) {
      const filterOption = document.createElement("option");
      filterOption.value = key;
      filterOption.textContent = label;
      elements.categoryFilter.appendChild(filterOption);
      const inputOption = document.createElement("option");
      inputOption.value = key;
      inputOption.textContent = label;
      elements.category.appendChild(inputOption);
    }
    const sources = [...new Set(data.rows.map((row) => row.source_label))].sort();
    for (const source of sources) {
      const option = document.createElement("option");
      option.value = source;
      option.textContent = source;
      elements.sourceFilter.appendChild(option);
    }
  }

  function reviewFor(id) {
    return state.rows[id];
  }

  function filteredRows() {
    const query = state.filters.search.trim().toLowerCase();
    return data.rows.filter((row) => {
      const review = reviewFor(row.sample_id);
      if (state.filters.status !== "all" && review.status !== state.filters.status) return false;
      if (state.filters.category !== "all" && review.category !== state.filters.category) return false;
      if (state.filters.source !== "all" && row.source_label !== state.filters.source) return false;
      if (query) {
        const haystack = `${review.transcript} ${review.instruction} ${review.notes}`.toLowerCase();
        if (!haystack.includes(query)) return false;
      }
      return true;
    });
  }

  function syncInputsToState() {
    if (!currentId) return;
    const review = reviewFor(currentId);
    review.transcript = elements.transcript.value;
    review.transcript_confirmed = elements.confirmed.checked;
    review.instruction = elements.instruction.value;
    review.category = elements.category.value;
    review.notes = elements.notes.value;
    review.updated_at = new Date().toISOString();
    review.revision = Number(review.revision || 0) + 1;
    updateAcceptState();
    scheduleSave();
  }

  function updateAcceptState() {
    elements.accept.disabled = !elements.confirmed.checked
      || !elements.transcript.value.trim()
      || !elements.instruction.value.trim();
  }

  function updateSummary() {
    const reviews = Object.values(state.rows);
    const accepted = reviews.filter((row) => row.status === "accepted");
    const rejected = reviews.filter((row) => row.status === "rejected");
    const reviewed = accepted.length + rejected.length;
    const duration = accepted.reduce((sum, review) => sum + Number(rowsById.get(review.sample_id)?.duration_seconds || 0), 0);
    elements.reviewed.textContent = `${reviewed} / ${data.rows.length}`;
    elements.accepted.textContent = String(accepted.length);
    elements.rejected.textContent = String(rejected.length);
    elements.acceptedDuration.textContent = formatDuration(duration);
    elements.progress.style.width = `${data.rows.length ? (reviewed / data.rows.length) * 100 : 0}%`;
  }

  function ensureCurrentVisible(rows) {
    if (!rows.length) {
      currentId = null;
      return;
    }
    if (!currentId || !rows.some((row) => row.sample_id === currentId)) {
      const preferred = state.current_id && rows.find((row) => row.sample_id === state.current_id);
      currentId = (preferred || rows[0]).sample_id;
    }
  }

  function render() {
    const visible = filteredRows();
    ensureCurrentVisible(visible);
    updateSummary();
    elements.empty.hidden = visible.length > 0;
    elements.shell.hidden = visible.length === 0;
    if (!visible.length || !currentId) return;

    const row = rowsById.get(currentId);
    const review = reviewFor(currentId);
    const index = visible.findIndex((item) => item.sample_id === currentId);
    state.current_id = currentId;
    elements.position.textContent = `Clip ${index + 1} of ${visible.length} · shortlist ${row.ordinal} of ${data.rows.length}`;
    elements.title.textContent = review.category
      ? (data.category_labels[review.category] || row.category_label)
      : row.category_label;
    elements.statusPill.textContent = review.status[0].toUpperCase() + review.status.slice(1);
    elements.statusPill.className = `status-pill ${review.status}`;
    if (elements.audio.getAttribute("src") !== row.audio_url) {
      elements.audio.src = row.audio_url;
      elements.audio.load();
    }
    elements.transcript.value = review.transcript;
    elements.confirmed.checked = Boolean(review.transcript_confirmed);
    elements.instruction.value = review.instruction;
    elements.category.value = review.category;
    elements.notes.value = review.notes || "";
    elements.detailSource.textContent = row.source_label;
    elements.detailTime.textContent = formatSourceTime(row.source_start_seconds);
    elements.detailDuration.textContent = `${Number(row.duration_seconds).toFixed(2)} seconds`;
    elements.detailConfidence.textContent = `${(Number(row.transcript_confidence) * 100).toFixed(1)}%`;
    elements.detailSnr.textContent = `${Number(row.snr_db).toFixed(1)} dB`;
    elements.detailRate.textContent = `${Number(row.features?.words_per_second || 0).toFixed(2)} words/sec`;
    elements.previous.disabled = index <= 0;
    elements.next.disabled = index >= visible.length - 1;
    updateAcceptState();
    saveState({ bump: false });
  }

  function move(delta) {
    syncInputsToState();
    const visible = filteredRows();
    if (!visible.length) return;
    const index = Math.max(0, visible.findIndex((row) => row.sample_id === currentId));
    const target = Math.min(visible.length - 1, Math.max(0, index + delta));
    currentId = visible[target].sample_id;
    render();
  }

  function nextPending() {
    const pending = data.rows.find((row) => reviewFor(row.sample_id).status === "pending");
    if (pending) {
      if (state.filters.status !== "pending") {
        state.filters.status = "pending";
        elements.statusFilter.value = "pending";
      }
      currentId = pending.sample_id;
      render();
      return;
    }
    state.filters.status = "all";
    elements.statusFilter.value = "all";
    render();
    showToast("All shortlisted clips have a decision.");
  }

  function decide(status) {
    if (!currentId) return;
    syncInputsToState();
    const review = reviewFor(currentId);
    if (status === "accepted" && elements.accept.disabled) {
      showToast("Confirm the transcript and keep a delivery instruction before accepting.");
      return;
    }
    review.status = status;
    review.updated_at = new Date().toISOString();
    review.revision = Number(review.revision || 0) + 1;
    saveState();
    showToast(status === "accepted" ? "Clip accepted" : "Clip rejected");
    nextPending();
  }

  function clearFilters() {
    state.filters = { status: "pending", category: "all", source: "all", search: "" };
    elements.statusFilter.value = "pending";
    elements.categoryFilter.value = "all";
    elements.sourceFilter.value = "all";
    elements.search.value = "";
    currentId = null;
    saveState();
    render();
  }

  function exportPayload() {
    const rows = data.rows.map((source) => ({ ...reviewFor(source.sample_id) }));
    const accepted = rows.filter((row) => row.status === "accepted");
    const rejected = rows.filter((row) => row.status === "rejected");
    return {
      schema_version: 1,
      round_id: data.round_id,
      export_scope: "cumulative",
      exported_at: new Date().toISOString(),
      revision: state.revision,
      summary: {
        ready_sample_count: rows.length,
        accepted_sample_count: accepted.length,
        rejected_sample_count: rejected.length,
        pending_sample_count: rows.length - accepted.length - rejected.length,
        accepted_duration_seconds: accepted.reduce(
          (sum, review) => sum + Number(rowsById.get(review.sample_id)?.duration_seconds || 0),
          0,
        ),
      },
      rows,
    };
  }

  function downloadJson(payload, filename) {
    const blob = new Blob([JSON.stringify(payload, null, 2) + "\n"], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  function importPayload(payload) {
    if (!payload || payload.round_id !== data.round_id || !Array.isArray(payload.rows)) {
      throw new Error("This file is not a Narrator triage export for this package.");
    }
    let merged = 0;
    for (const incoming of payload.rows) {
      const current = state.rows[incoming.sample_id];
      if (!current) continue;
      const incomingTime = Date.parse(incoming.updated_at || "") || 0;
      const currentTime = Date.parse(current.updated_at || "") || 0;
      const incomingRevision = Number(incoming.revision || 0);
      const currentRevision = Number(current.revision || 0);
      if (incomingTime < currentTime || (incomingTime === currentTime && incomingRevision < currentRevision)) continue;
      state.rows[incoming.sample_id] = { ...current, ...incoming, sample_id: incoming.sample_id };
      merged += 1;
    }
    state.revision = Math.max(state.revision, Number(payload.revision || 0)) + 1;
    state.updated_at = new Date().toISOString();
    saveState({ bump: false });
    currentId = null;
    render();
    showToast(`Imported ${merged} review row${merged === 1 ? "" : "s"}.`);
  }

  function bind() {
    for (const element of [elements.transcript, elements.instruction, elements.notes]) {
      element.addEventListener("input", syncInputsToState);
    }
    elements.confirmed.addEventListener("change", syncInputsToState);
    elements.category.addEventListener("change", syncInputsToState);
    elements.replay.addEventListener("click", () => {
      elements.audio.currentTime = 0;
      elements.audio.play().catch(() => {});
    });
    elements.reject.addEventListener("click", () => decide("rejected"));
    elements.accept.addEventListener("click", () => decide("accepted"));
    elements.previous.addEventListener("click", () => move(-1));
    elements.next.addEventListener("click", () => move(1));
    elements.skip.addEventListener("click", () => move(1));
    elements.statusFilter.addEventListener("change", () => {
      state.filters.status = elements.statusFilter.value;
      currentId = null;
      saveState();
      render();
    });
    elements.categoryFilter.addEventListener("change", () => {
      state.filters.category = elements.categoryFilter.value;
      currentId = null;
      saveState();
      render();
    });
    elements.sourceFilter.addEventListener("change", () => {
      state.filters.source = elements.sourceFilter.value;
      currentId = null;
      saveState();
      render();
    });
    elements.search.addEventListener("input", () => {
      state.filters.search = elements.search.value;
      currentId = null;
      scheduleSave();
      render();
    });
    elements.clearFilters.addEventListener("click", clearFilters);
    elements.emptyClear.addEventListener("click", clearFilters);
    elements.exportButton.addEventListener("click", () => {
      syncInputsToState();
      saveState();
      downloadJson(exportPayload(), "alexandria_narrator_dataset_triage_review.json");
      showToast("Review exported.");
    });
    elements.importFile.addEventListener("change", async () => {
      const file = elements.importFile.files?.[0];
      elements.importFile.value = "";
      if (!file) return;
      try {
        importPayload(JSON.parse(await file.text()));
      } catch (error) {
        console.error(error);
        showToast(error.message || "Import failed.");
      }
    });
    document.addEventListener("keydown", (event) => {
      const tag = document.activeElement?.tagName?.toLowerCase();
      const editing = ["input", "textarea", "select"].includes(tag);
      if (editing) return;
      if (event.code === "Space") {
        event.preventDefault();
        if (elements.audio.paused) elements.audio.play().catch(() => {});
        else elements.audio.pause();
      } else if (event.key.toLowerCase() === "a") {
        event.preventDefault();
        decide("accepted");
      } else if (event.key.toLowerCase() === "r") {
        event.preventDefault();
        decide("rejected");
      } else if (event.key.toLowerCase() === "j" || event.key === "ArrowLeft") {
        event.preventDefault();
        move(-1);
      } else if (event.key.toLowerCase() === "k" || event.key === "ArrowRight") {
        event.preventDefault();
        move(1);
      }
    });
  }

  populateOptions();
  elements.statusFilter.value = state.filters.status;
  elements.categoryFilter.value = state.filters.category;
  elements.sourceFilter.value = state.filters.source;
  elements.search.value = state.filters.search;
  currentId = state.current_id;
  bind();
  render();
})();
