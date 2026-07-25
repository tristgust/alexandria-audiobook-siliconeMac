(() => {
  const data = window.THREE_VOICE_SELECTED_REFINEMENT_DATA;
  if (!data || !Array.isArray(data.rows) || data.rows.length !== 2) throw new Error("Selected refinement data is missing.");
  const storageKey = `alexandria:selected-source-refinement:${data.round_id}`;
  const labels = {
    use_refined: "Use refined clip",
    keep_selected: "Keep originally selected candidate",
    reject: "Reject source",
  };
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(storageKey) || "{}"); } catch {}
  let index = 0;
  const $ = (selector) => document.querySelector(selector);
  const players = { selected: $("#selected-audio"), refined: $("#refined-audio") };

  function row() { return data.rows[index]; }
  function record(item) {
    if (!saved[item.clip_id]) saved[item.clip_id] = { clip_id: item.clip_id, revision: 0 };
    return saved[item.clip_id];
  }
  function complete(item) { return Boolean(record(item).decision); }
  function persist(item, field, value) {
    const state = record(item);
    state[field] = value;
    state.revision = Number(state.revision || 0) + 1;
    state.updated_at = new Date().toISOString();
    localStorage.setItem(storageKey, JSON.stringify(saved));
    refresh();
  }
  function loadAudio(item) {
    for (const player of Object.values(players)) { player.pause(); player.removeAttribute("src"); player.load(); }
    players.selected.src = item.selected_audio;
    players.refined.src = item.refined_audio;
    $("#selected-link").href = item.selected_audio;
    $("#refined-link").href = item.refined_audio;
    players.selected.load(); players.refined.load();
  }
  function drawNav() {
    const nav = $("#route-nav"); nav.replaceChildren();
    data.rows.forEach((item, itemIndex) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = String(itemIndex + 1);
      button.classList.toggle("active", itemIndex === index);
      button.classList.toggle("complete", complete(item));
      button.classList.toggle("rejected", record(item).decision === "reject");
      button.title = `${item.target_label}: ${item.primary_emotion}`;
      button.onclick = () => { index = itemIndex; render(); };
      nav.append(button);
    });
  }
  function refresh() {
    const done = data.rows.filter(complete).length;
    $("#progress").textContent = `${done} / ${data.rows.length} complete`;
    const state = record(row());
    const isDone = Boolean(state.decision);
    $("#status").textContent = isDone ? "Complete" : "Pending";
    $("#status").classList.toggle("complete", isDone);
    const decision = $("#current-decision");
    decision.textContent = isDone ? labels[state.decision] : "No decision yet.";
    decision.classList.toggle("approved", state.decision === "use_refined" || state.decision === "keep_selected");
    decision.classList.toggle("problem", state.decision === "reject");
    drawNav();
  }
  function render() {
    const item = row();
    const state = record(item);
    $("#ordinal").textContent = `Candidate ${index + 1} of ${data.rows.length} · ${item.refinement_type.replaceAll("_", " ")}`;
    $("#target-label").textContent = item.target_label;
    $("#emotion").textContent = item.primary_emotion;
    $("#source-line").textContent = item.source_title;
    $("#selected-transcript").textContent = item.selected_transcript;
    $("#review-note").textContent = item.review_notes || "No additional note.";
    const technical = $("#technical-status");
    technical.textContent = `${item.technical_pass ? "Technical checks passed" : "Technical checks need caution"} · transcript similarity ${Number(item.verification_similarity).toFixed(2)}`;
    technical.className = item.technical_pass ? "technical-pass" : "technical-fail";
    $("#notes").value = state.notes || "";
    $("#notes").oninput = () => persist(item, "notes", $("#notes").value);
    $("#previous").disabled = index === 0;
    $("#next").disabled = index === data.rows.length - 1;
    loadAudio(item);
    refresh();
    window.scrollTo({ top: 0, behavior: "instant" });
  }
  function decide(value) {
    const current = row();
    persist(current, "decision", value);
    if (index < data.rows.length - 1) { index += 1; render(); }
  }
  function exportReview() {
    const rows = data.rows.map((item) => ({
      clip_id: item.clip_id,
      target: item.target,
      target_label: item.target_label,
      selected_transcript: item.selected_transcript,
      primary_emotion: item.primary_emotion,
      refinement_type: item.refinement_type,
      ...record(item),
    }));
    const payload = {
      schema_version: 1,
      round_id: data.round_id,
      exported_at: new Date().toISOString(),
      summary: {
        candidate_count: rows.length,
        complete_count: rows.filter((item) => item.decision).length,
        refined_count: rows.filter((item) => item.decision === "use_refined").length,
        selected_count: rows.filter((item) => item.decision === "keep_selected").length,
        rejected_count: rows.filter((item) => item.decision === "reject").length,
      },
      rows,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "alexandria_three_voice_selected_refinement_review.json";
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 500);
    $("#done").showModal();
  }

  document.querySelectorAll("[data-decision]").forEach((button) => { button.onclick = () => decide(button.dataset.decision); });
  $("#previous").onclick = () => { if (index > 0) { index -= 1; render(); } };
  $("#next").onclick = () => { if (index < data.rows.length - 1) { index += 1; render(); } };
  $("#export").onclick = exportReview;
  document.addEventListener("keydown", (event) => {
    const tag = document.activeElement?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (event.code === "Space") { event.preventDefault(); const player = players.refined; player.paused ? player.play() : player.pause(); return; }
    const key = event.key.toLowerCase();
    if (key === "a") decide("use_refined");
    else if (key === "o") decide("keep_selected");
    else if (key === "r") decide("reject");
    else if (key === "j" && index > 0) { index -= 1; render(); }
    else if (key === "k" && index < data.rows.length - 1) { index += 1; render(); }
  });
  render();
})();
