(() => {
  const data = window.THREE_VOICE_SOURCE_REPAIR_DATA;
  if (!data || !Array.isArray(data.rows) || !data.rows.length) throw new Error("Three-voice source repair data is missing.");
  const storageKey = `alexandria:three-voice-source-repair:${data.round_id}`;
  const decisions = {
    approve_repaired: "Use repaired clip",
    cleanup_still_bad: "Cleanup still bad",
    boundary_still_wrong: "Boundary still wrong",
    mine_nearby: "Mine a better nearby line",
    reject: "Reject",
  };
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(storageKey) || "{}"); } catch {}
  let visibleRows = [];
  let currentVisibleIndex = 0;
  const $ = (selector) => document.querySelector(selector);
  const audio = { original: $("#original-audio"), repaired: $("#repaired-audio") };

  function record(row) {
    if (!saved[row.clip_id]) saved[row.clip_id] = { clip_id: row.clip_id, revision: 0 };
    return saved[row.clip_id];
  }
  function complete(row) { return Boolean(record(row).decision); }
  function persist(row, field, value) {
    const item = record(row);
    item[field] = value;
    item.revision = Number(item.revision || 0) + 1;
    item.updated_at = new Date().toISOString();
    localStorage.setItem(storageKey, JSON.stringify(saved));
    refresh();
  }
  function currentRow() { return visibleRows[currentVisibleIndex] || null; }
  function searchText(row) {
    return [row.target_label,row.repair_type,row.source_title,row.selected_transcript,row.primary_emotion,row.secondary_emotion,row.dramatic_function,row.coverage_gap].join(" ").toLowerCase();
  }
  function applyFilters({ preserveClipId = null } = {}) {
    const target = $("#target-filter").value;
    const type = $("#type-filter").value;
    const status = $("#status-filter").value;
    const query = $("#search").value.trim().toLowerCase();
    visibleRows = data.rows.filter((row) => {
      if (target !== "all" && row.target !== target) return false;
      if (type !== "all" && row.repair_type !== type) return false;
      if (status === "incomplete" && complete(row)) return false;
      if (status === "complete" && !complete(row)) return false;
      if (query && !searchText(row).includes(query)) return false;
      return true;
    });
    let nextIndex = 0;
    if (preserveClipId) {
      const found = visibleRows.findIndex((row) => row.clip_id === preserveClipId);
      if (found >= 0) nextIndex = found;
    }
    currentVisibleIndex = Math.min(nextIndex, Math.max(0, visibleRows.length - 1));
    $("#visible-count").textContent = `${visibleRows.length} visible`;
    render();
  }
  function loadAudio(row) {
    for (const element of Object.values(audio)) { element.pause(); element.removeAttribute("src"); element.load(); }
    if (!row) return;
    audio.original.src = row.original_audio;
    audio.repaired.src = row.repaired_audio;
    $("#original-link").href = row.original_audio;
    $("#repaired-link").href = row.repaired_audio;
    audio.original.load(); audio.repaired.load();
  }
  function drawNav() {
    const nav = $("#route-nav"); nav.replaceChildren();
    visibleRows.forEach((row, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = String(index + 1);
      button.classList.toggle("active", index === currentVisibleIndex);
      button.classList.toggle("complete", complete(row));
      button.classList.toggle("rejected", record(row).decision === "reject");
      button.title = `${row.target_label}: ${row.primary_emotion}`;
      button.onclick = () => { currentVisibleIndex = index; render(); };
      nav.append(button);
    });
  }
  function refresh() {
    const done = data.rows.filter(complete).length;
    $("#progress").textContent = `${done} / ${data.rows.length} complete`;
    const row = currentRow();
    const item = row ? record(row) : {};
    const isDone = Boolean(item.decision);
    $("#status").textContent = isDone ? "Complete" : "Pending";
    $("#status").classList.toggle("complete", isDone);
    const state = $("#current-decision");
    state.textContent = isDone ? decisions[item.decision] : "No decision yet.";
    state.classList.toggle("approved", item.decision === "approve_repaired");
    state.classList.toggle("problem", Boolean(item.decision && item.decision !== "approve_repaired"));
    drawNav();
  }
  function repairDescription(row) {
    if (row.repair_type === "cleanup") return "Speech-focused cleanup and normalization were applied after your conditional approval.";
    if (row.repair_type === "boundary") return `The ${String(row.prior_boundary_decision || "boundary").replaceAll("_", " ")} issue was repaired.`;
    return "The prior review had no final reference decision; this card resolves it.";
  }
  function render() {
    const row = currentRow();
    $("#card").hidden = !row;
    $("#empty").hidden = Boolean(row);
    if (!row) { $("#route-nav").replaceChildren(); loadAudio(null); refresh(); return; }
    const item = record(row);
    $("#ordinal").textContent = `Candidate ${currentVisibleIndex + 1} of ${visibleRows.length} · ${row.repair_type.replaceAll("_", " ")}`;
    $("#target-label").textContent = row.target_label;
    $("#emotion").textContent = row.primary_emotion;
    $("#source-line").textContent = row.source_title;
    $("#selected-transcript").textContent = row.selected_transcript;
    $("#repair-description").textContent = repairDescription(row);
    const technical = $("#technical-status");
    const similarity = Number(row.verification_similarity);
    const similarityText = Number.isFinite(similarity) ? ` · transcript similarity ${similarity.toFixed(2)}` : "";
    technical.textContent = `${row.technical_pass ? "Technical checks passed" : "Technical checks need caution"}${similarityText}`;
    technical.className = row.technical_pass ? "technical-pass" : "technical-fail";
    $("#notes").value = item.notes || "";
    $("#notes").oninput = () => persist(row, "notes", $("#notes").value);
    $("#previous").disabled = currentVisibleIndex === 0;
    $("#next").disabled = currentVisibleIndex === visibleRows.length - 1;
    loadAudio(row); refresh();
    window.scrollTo({ top: 0, behavior: "instant" });
  }
  function advanceAfterDecision(clipId) {
    const status = $("#status-filter").value;
    if (status === "incomplete") {
      applyFilters();
      return;
    }
    const index = visibleRows.findIndex((row) => row.clip_id === clipId);
    currentVisibleIndex = Math.min(index + 1, visibleRows.length - 1);
    render();
  }
  function setDecision(value) {
    const row = currentRow(); if (!row) return;
    persist(row, "decision", value);
    advanceAfterDecision(row.clip_id);
  }
  function exportReview() {
    const rows = data.rows.map((row) => ({
      clip_id: row.clip_id,
      target: row.target,
      target_label: row.target_label,
      repair_type: row.repair_type,
      repair_reason: row.repair_reason,
      selected_transcript: row.selected_transcript,
      primary_emotion: row.primary_emotion,
      dramatic_function: row.dramatic_function,
      technical_pass: row.technical_pass,
      ...record(row),
    }));
    const payload = {
      schema_version: 1,
      round_id: data.round_id,
      exported_at: new Date().toISOString(),
      summary: {
        candidate_count: rows.length,
        complete_count: rows.filter((row) => row.decision).length,
        approved_count: rows.filter((row) => row.decision === "approve_repaired").length,
        rejected_count: rows.filter((row) => row.decision === "reject").length,
        cleanup_still_bad_count: rows.filter((row) => row.decision === "cleanup_still_bad").length,
        boundary_still_wrong_count: rows.filter((row) => row.decision === "boundary_still_wrong").length,
        mine_nearby_count: rows.filter((row) => row.decision === "mine_nearby").length,
      },
      rows,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "alexandria_three_voice_source_repair_review.json";
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 500);
    $("#done").showModal();
  }
  async function importReview(file) {
    const payload = JSON.parse(await file.text());
    if (payload.round_id !== data.round_id || !Array.isArray(payload.rows)) throw new Error("This JSON belongs to a different repair review.");
    const known = new Set(data.rows.map((row) => row.clip_id));
    for (const row of payload.rows) {
      if (!known.has(row.clip_id)) continue;
      const existing = saved[row.clip_id];
      if (!existing || Number(row.revision || 0) >= Number(existing.revision || 0)) {
        saved[row.clip_id] = {
          clip_id: row.clip_id,
          revision: Number(row.revision || 0),
          decision: row.decision || undefined,
          notes: row.notes || "",
          updated_at: row.updated_at || payload.exported_at || new Date().toISOString(),
        };
      }
    }
    localStorage.setItem(storageKey, JSON.stringify(saved));
    applyFilters({ preserveClipId: currentRow()?.clip_id });
  }

  document.querySelectorAll("[data-decision]").forEach((button) => { button.onclick = () => setDecision(button.dataset.decision); });
  for (const id of ["target-filter","type-filter","status-filter"]) $("#" + id).onchange = () => applyFilters({ preserveClipId: currentRow()?.clip_id });
  $("#search").oninput = () => applyFilters({ preserveClipId: currentRow()?.clip_id });
  $("#previous").onclick = () => { if (currentVisibleIndex > 0) { currentVisibleIndex -= 1; render(); } };
  $("#next").onclick = () => { if (currentVisibleIndex < visibleRows.length - 1) { currentVisibleIndex += 1; render(); } };
  $("#reload").onclick = () => loadAudio(currentRow());
  $("#export").onclick = exportReview;
  $("#import").onclick = () => $("#import-file").click();
  $("#import-file").onchange = async (event) => {
    const file = event.target.files?.[0]; if (!file) return;
    try { await importReview(file); } catch (error) { alert(error.message || String(error)); }
    event.target.value = "";
  };
  document.addEventListener("keydown", (event) => {
    const tag = document.activeElement?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (event.code === "Space") { event.preventDefault(); const player = audio.repaired; player.paused ? player.play() : player.pause(); return; }
    const key = event.key.toLowerCase();
    if (key === "a") setDecision("approve_repaired");
    else if (key === "c") setDecision("cleanup_still_bad");
    else if (key === "b") setDecision("boundary_still_wrong");
    else if (key === "m") setDecision("mine_nearby");
    else if (key === "r") setDecision("reject");
    else if (key === "j" && currentVisibleIndex > 0) { currentVisibleIndex -= 1; render(); }
    else if (key === "k" && currentVisibleIndex < visibleRows.length - 1) { currentVisibleIndex += 1; render(); }
  });
  visibleRows = data.rows.slice();
  render();
})();
