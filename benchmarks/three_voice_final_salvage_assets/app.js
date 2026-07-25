(() => {
  const data = window.THREE_VOICE_FINAL_SALVAGE_DATA;
  if (!data || !Array.isArray(data.rows) || !data.rows.length) throw new Error("Final salvage data is missing.");
  const storageKey = `alexandria:three-voice-final-salvage:${data.round_id}`;
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(storageKey) || "{}"); } catch {}
  let visibleRows = data.rows.slice();
  let currentVisibleIndex = 0;
  const $ = (selector) => document.querySelector(selector);
  const players = [0,1,2,3].map((index) => ({
    panel: $(`#panel-${index}`),
    label: $(`#label-${index}`),
    audio: $(`#audio-${index}`),
    link: $(`#link-${index}`),
  }));
  const decisionLabels = {
    candidate_A: "Candidate A",
    candidate_B: "Candidate B",
    candidate_C: "Candidate C",
    none: "None are usable",
    approve_final: "Use final boundary",
    still_wrong: "Boundary still wrong",
  };

  function record(row) {
    if (!saved[row.card_id]) saved[row.card_id] = { card_id: row.card_id, revision: 0 };
    return saved[row.card_id];
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
  function searchable(row) {
    return [row.card_type,row.target_label,row.source_title,row.selected_transcript,row.primary_emotion,row.dramatic_function,row.review_notes,row.boundary_reason].join(" ").toLowerCase();
  }
  function applyFilters({ preserveCardId = null } = {}) {
    const type = $("#type-filter").value;
    const target = $("#target-filter").value;
    const status = $("#status-filter").value;
    const query = $("#search").value.trim().toLowerCase();
    visibleRows = data.rows.filter((row) => {
      if (type !== "all" && row.card_type !== type) return false;
      if (target !== "all" && row.target !== target) return false;
      if (status === "incomplete" && complete(row)) return false;
      if (status === "complete" && !complete(row)) return false;
      if (query && !searchable(row).includes(query)) return false;
      return true;
    });
    let index = 0;
    if (preserveCardId) {
      const found = visibleRows.findIndex((row) => row.card_id === preserveCardId);
      if (found >= 0) index = found;
    }
    currentVisibleIndex = Math.min(index, Math.max(0, visibleRows.length - 1));
    $("#visible-count").textContent = `${visibleRows.length} visible`;
    render();
  }
  function clearPlayers() {
    for (const player of players) {
      player.audio.pause();
      player.audio.removeAttribute("src");
      player.audio.load();
      player.panel.hidden = true;
      player.panel.className = "";
    }
  }
  function setPlayer(index, label, src, className = "") {
    const player = players[index];
    player.panel.hidden = false;
    player.panel.className = className;
    player.label.textContent = label;
    player.audio.src = src;
    player.link.href = src;
    player.audio.load();
  }
  function loadAudio(row) {
    clearPlayers();
    if (!row) return;
    if (row.card_type === "source_separation") {
      setPlayer(0, "Original mixed source", row.original_audio, "previous");
      row.candidates.forEach((candidate, index) => setPlayer(index + 1, `Candidate ${candidate.candidate_label}`, candidate.audio, "candidate"));
    } else {
      setPlayer(0, "Previous repair", row.previous_audio, "previous");
      setPlayer(1, "Final source-timestamp cut", row.final_audio, "candidate");
    }
  }
  function drawNav() {
    const nav = $("#route-nav");
    nav.replaceChildren();
    visibleRows.forEach((row, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = String(index + 1);
      button.classList.toggle("active", index === currentVisibleIndex);
      button.classList.toggle("complete", complete(row));
      const decision = record(row).decision;
      button.classList.toggle("failed", decision === "none" || decision === "still_wrong");
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
    state.textContent = isDone ? decisionLabels[item.decision] : "No decision yet.";
    state.classList.toggle("approved", Boolean(item.decision && !["none","still_wrong"].includes(item.decision)));
    state.classList.toggle("problem", ["none","still_wrong"].includes(item.decision));
    drawNav();
  }
  function render() {
    const row = currentRow();
    $("#card").hidden = !row;
    $("#empty").hidden = Boolean(row);
    if (!row) { clearPlayers(); $("#route-nav").replaceChildren(); refresh(); return; }
    const item = record(row);
    $("#ordinal").textContent = `Card ${currentVisibleIndex + 1} of ${visibleRows.length} · ${row.card_type === "source_separation" ? "source separation" : "boundary"}`;
    $("#target-label").textContent = row.target_label;
    $("#emotion").textContent = row.primary_emotion;
    $("#source-line").textContent = row.source_title;
    $("#selected-transcript").textContent = row.selected_transcript;
    $("#context-note").textContent = row.card_type === "source_separation"
      ? (row.review_notes || "Choose a candidate only if music/effects are materially reduced without damaging the target voice.")
      : `${row.boundary_reason}${row.review_notes ? ` Prior note: ${row.review_notes}` : ""}`;
    $("#separation-actions").hidden = row.card_type !== "source_separation";
    $("#boundary-actions").hidden = row.card_type !== "boundary_final";
    $("#notes").value = item.notes || "";
    $("#notes").oninput = () => persist(row, "notes", $("#notes").value);
    $("#previous").disabled = currentVisibleIndex === 0;
    $("#next").disabled = currentVisibleIndex === visibleRows.length - 1;
    loadAudio(row);
    refresh();
    window.scrollTo({ top: 0, behavior: "instant" });
  }
  function advanceAfterDecision(cardId) {
    if ($("#status-filter").value === "incomplete") {
      applyFilters();
      return;
    }
    const index = visibleRows.findIndex((row) => row.card_id === cardId);
    currentVisibleIndex = Math.min(index + 1, visibleRows.length - 1);
    render();
  }
  function setDecision(value) {
    const row = currentRow();
    if (!row) return;
    const allowed = row.card_type === "source_separation"
      ? new Set(["candidate_A","candidate_B","candidate_C","none"])
      : new Set(["approve_final","still_wrong"]);
    if (!allowed.has(value)) return;
    persist(row, "decision", value);
    advanceAfterDecision(row.card_id);
  }
  function exportReview() {
    const rows = data.rows.map((row) => ({
      card_id: row.card_id,
      card_type: row.card_type,
      clip_id: row.clip_id,
      target: row.target,
      target_label: row.target_label,
      selected_transcript: row.selected_transcript,
      primary_emotion: row.primary_emotion,
      ...record(row),
    }));
    const payload = {
      schema_version: 1,
      round_id: data.round_id,
      exported_at: new Date().toISOString(),
      summary: {
        card_count: rows.length,
        complete_count: rows.filter((row) => row.decision).length,
        separation_selected_count: rows.filter((row) => /^candidate_/.test(row.decision || "")).length,
        separation_none_count: rows.filter((row) => row.decision === "none").length,
        boundary_approved_count: rows.filter((row) => row.decision === "approve_final").length,
        boundary_wrong_count: rows.filter((row) => row.decision === "still_wrong").length,
      },
      rows,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "alexandria_three_voice_final_salvage_review.json";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 500);
    $("#done").showModal();
  }
  async function importReview(file) {
    const payload = JSON.parse(await file.text());
    if (payload.round_id !== data.round_id || !Array.isArray(payload.rows)) throw new Error("This JSON belongs to a different review.");
    const known = new Set(data.rows.map((row) => row.card_id));
    for (const row of payload.rows) {
      if (!known.has(row.card_id)) continue;
      const existing = saved[row.card_id];
      if (!existing || Number(row.revision || 0) >= Number(existing.revision || 0)) {
        saved[row.card_id] = {
          card_id: row.card_id,
          revision: Number(row.revision || 0),
          decision: row.decision || undefined,
          notes: row.notes || "",
          updated_at: row.updated_at || payload.exported_at || new Date().toISOString(),
        };
      }
    }
    localStorage.setItem(storageKey, JSON.stringify(saved));
    applyFilters({ preserveCardId: currentRow()?.card_id });
  }

  document.querySelectorAll("[data-decision]").forEach((button) => { button.onclick = () => setDecision(button.dataset.decision); });
  for (const id of ["type-filter","target-filter","status-filter"]) $("#" + id).onchange = () => applyFilters({ preserveCardId: currentRow()?.card_id });
  $("#search").oninput = () => applyFilters({ preserveCardId: currentRow()?.card_id });
  $("#previous").onclick = () => { if (currentVisibleIndex > 0) { currentVisibleIndex -= 1; render(); } };
  $("#next").onclick = () => { if (currentVisibleIndex < visibleRows.length - 1) { currentVisibleIndex += 1; render(); } };
  $("#reload").onclick = () => loadAudio(currentRow());
  $("#export").onclick = exportReview;
  $("#import").onclick = () => $("#import-file").click();
  $("#import-file").onchange = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try { await importReview(file); } catch (error) { alert(error.message || String(error)); }
    event.target.value = "";
  };
  document.addEventListener("keydown", (event) => {
    const tag = document.activeElement?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    const row = currentRow();
    if (!row) return;
    if (event.code === "Space") {
      event.preventDefault();
      const player = row.card_type === "source_separation" ? players[1].audio : players[1].audio;
      player.paused ? player.play() : player.pause();
      return;
    }
    const key = event.key.toLowerCase();
    if (row.card_type === "source_separation") {
      if (key === "1") setDecision("candidate_A");
      else if (key === "2") setDecision("candidate_B");
      else if (key === "3") setDecision("candidate_C");
      else if (key === "n") setDecision("none");
    } else {
      if (key === "a") setDecision("approve_final");
      else if (key === "b") setDecision("still_wrong");
    }
    if (key === "j" && currentVisibleIndex > 0) { currentVisibleIndex -= 1; render(); }
    else if (key === "k" && currentVisibleIndex < visibleRows.length - 1) { currentVisibleIndex += 1; render(); }
  });
  render();
})();
