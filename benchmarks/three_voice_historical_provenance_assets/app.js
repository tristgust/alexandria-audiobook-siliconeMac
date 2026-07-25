(() => {
  const data = window.THREE_VOICE_HISTORICAL_PROVENANCE_DATA;
  if (!data || !Array.isArray(data.rows) || data.rows.length !== 14) {
    throw new Error("Historical provenance review data is missing or incomplete.");
  }

  const storageKey = `alexandria:three-voice-historical-provenance:${data.schema_version}:${data.round_id}`;
  const allowedDecisions = new Set([
    "approve_usable",
    "correct_speaker_unusable",
    "wrong_or_uncertain_speaker",
    "wrong_boundary",
  ]);
  const decisionLabels = {
    approve_usable: "Correct speaker and usable",
    correct_speaker_unusable: "Correct speaker, technically unusable",
    wrong_or_uncertain_speaker: "Wrong or uncertain speaker",
    wrong_boundary: "Wrong boundary",
    locked_rejected_wrong_speaker: "Locked wrong-speaker rejection",
  };

  const $ = (selector) => document.querySelector(selector);
  const audio = {
    identity: $("#identity-audio"),
    context: $("#context-audio"),
    candidate: $("#candidate-audio"),
  };
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(storageKey) || "{}");
  } catch {
    saved = {};
  }
  let currentIndex = 0;

  function rowRecord(row) {
    if (!saved[row.clip_id] || typeof saved[row.clip_id] !== "object") {
      saved[row.clip_id] = {
        clip_id: row.clip_id,
        revision: 0,
      };
    }
    const record = saved[row.clip_id];
    if (row.warning_only) {
      record.decision = "locked_rejected_wrong_speaker";
      record.known_disposition = "rejected_wrong_speaker";
      record.locked = true;
    }
    return record;
  }

  function persistStorage() {
    localStorage.setItem(storageKey, JSON.stringify(saved));
  }

  function statusFor(row) {
    if (row.warning_only) return "warning";
    return allowedDecisions.has(rowRecord(row).decision) ? "complete" : "pending";
  }

  function isComplete(row) {
    return statusFor(row) !== "pending";
  }

  function filterMatches(row) {
    const targetFilter = $("#target-filter").value;
    const statusFilter = $("#status-filter").value;
    const query = $("#search").value.trim().toLowerCase();
    if (targetFilter !== "all" && row.target !== targetFilter) return false;
    const status = statusFor(row);
    if (statusFilter !== "all" && status !== statusFilter) return false;
    if (!query) return true;
    const searchable = [
      row.clip_id,
      row.target_label,
      row.source_title,
      row.selected_transcript,
      row.context_transcript,
      row.assistant_speaker_role,
      row.assistant_primary_emotion,
      row.assistant_secondary_emotion,
      row.assistant_dramatic_function,
    ].join(" ").toLowerCase();
    return searchable.includes(query);
  }

  function visibleIndices() {
    return data.rows.map((row, index) => filterMatches(row) ? index : -1).filter((index) => index >= 0);
  }

  function ensureCurrentVisible() {
    const visible = visibleIndices();
    if (!visible.length) return;
    if (!visible.includes(currentIndex)) currentIndex = visible[0];
  }

  function move(direction) {
    const visible = visibleIndices();
    if (!visible.length) return;
    const position = visible.indexOf(currentIndex);
    const nextPosition = position + direction;
    if (nextPosition >= 0 && nextPosition < visible.length) {
      currentIndex = visible[nextPosition];
      render();
    }
  }

  function stopAudio() {
    Object.values(audio).forEach((element) => {
      element.pause();
      element.currentTime = 0;
    });
  }

  function loadAudio(row) {
    stopAudio();
    const mapping = [
      [audio.identity, $("#identity-link"), row.identity_audio],
      [audio.context, $("#context-link"), row.context_audio],
      [audio.candidate, $("#candidate-link"), row.candidate_audio],
    ];
    mapping.forEach(([element, link, source]) => {
      element.removeAttribute("src");
      element.load();
      element.src = source;
      link.href = source;
      element.load();
    });
  }

  function playOnly(element) {
    Object.values(audio).forEach((other) => {
      if (other !== element) other.pause();
    });
    if (element.paused) element.play().catch(() => {});
    else element.pause();
  }

  function saveField(row, field, value) {
    const record = rowRecord(row);
    record[field] = value;
    record.revision = Number(record.revision || 0) + 1;
    record.updated_at = new Date().toISOString();
    persistStorage();
    refreshChrome();
  }

  function saveDecision(row, decision) {
    if (row.warning_only || !allowedDecisions.has(decision)) return;
    saveField(row, "decision", decision);
    renderDecisionState(row);
    window.setTimeout(() => move(1), 100);
  }

  function renderDecisionState(row) {
    const selected = rowRecord(row).decision;
    document.querySelectorAll("[data-decision]").forEach((button) => {
      button.classList.toggle("selected", button.dataset.decision === selected);
      button.setAttribute("aria-pressed", String(button.dataset.decision === selected));
    });
  }

  function drawNavigation() {
    const nav = $("#candidate-nav");
    nav.replaceChildren();
    data.rows.forEach((row, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = String(index + 1);
      button.hidden = !filterMatches(row);
      button.classList.toggle("active", index === currentIndex);
      button.classList.toggle("complete", statusFor(row) === "complete");
      button.classList.toggle("warning", row.warning_only);
      const status = row.warning_only ? "locked warning" : (decisionLabels[rowRecord(row).decision] || "pending");
      button.title = `${row.target_label} · ${row.assistant_primary_emotion} · ${status}`;
      button.addEventListener("click", () => {
        currentIndex = index;
        render();
      });
      nav.append(button);
    });
  }

  function refreshChrome() {
    const actionable = data.rows.filter((row) => !row.warning_only);
    const completed = actionable.filter((row) => allowedDecisions.has(rowRecord(row).decision)).length;
    $("#progress").textContent = `${data.warning_count} warning locked · ${completed} / ${actionable.length} decisions`;
    const row = data.rows[currentIndex];
    const status = statusFor(row);
    const badge = $("#status-badge");
    badge.className = `status-badge ${status}`;
    badge.textContent = status === "warning"
      ? "Locked warning"
      : status === "complete"
        ? decisionLabels[rowRecord(row).decision]
        : "Pending";
    drawNavigation();
  }

  function render() {
    ensureCurrentVisible();
    const row = data.rows[currentIndex];
    const record = rowRecord(row);
    $("#ordinal").textContent = `Candidate ${currentIndex + 1} of ${data.rows.length}`;
    $("#target-label").textContent = row.target_label;
    $("#emotion-label").textContent = row.assistant_primary_emotion;
    $("#source-line").textContent = `${row.source_title} · selected ${row.selected_start_seconds.toFixed(2)}–${row.selected_end_seconds.toFixed(2)} s · context ${row.context_start_seconds.toFixed(2)}–${row.context_end_seconds.toFixed(2)} s`;
    $("#selected-transcript").textContent = row.selected_transcript;
    $("#context-transcript").textContent = row.context_transcript;
    $("#selection-reason").textContent = row.selection_reason;
    $("#speaker-role").textContent = row.assistant_speaker_role;
    $("#dramatic-function").textContent = `${row.assistant_primary_emotion}${row.assistant_secondary_emotion ? ` / ${row.assistant_secondary_emotion}` : ""} · ${row.assistant_dramatic_function}`;
    $("#intensity").textContent = String(row.assistant_intensity_1_to_5);

    const warningPanel = $("#warning-panel");
    warningPanel.hidden = !row.warning_only;
    $("#warning-reason").textContent = row.warning_reason || "";
    $("#decision-panel").hidden = row.warning_only;

    $("#notes").value = record.notes || "";
    $("#notes").oninput = (event) => saveField(row, "notes", event.target.value);
    renderDecisionState(row);
    loadAudio(row);

    const visible = visibleIndices();
    const position = visible.indexOf(currentIndex);
    $("#previous").disabled = position <= 0;
    $("#next").disabled = position < 0 || position >= visible.length - 1;
    refreshChrome();
    window.scrollTo({ top: 0, behavior: "instant" });
  }

  function exportedRows() {
    return data.rows.map((row) => {
      const record = rowRecord(row);
      return {
        clip_id: row.clip_id,
        target: row.target,
        target_label: row.target_label,
        source_title: row.source_title,
        selected_transcript: row.selected_transcript,
        assistant_speaker_role: row.assistant_speaker_role,
        assistant_primary_emotion: row.assistant_primary_emotion,
        assistant_secondary_emotion: row.assistant_secondary_emotion,
        assistant_dramatic_function: row.assistant_dramatic_function,
        assistant_intensity_1_to_5: row.assistant_intensity_1_to_5,
        selected_start_seconds: row.selected_start_seconds,
        selected_end_seconds: row.selected_end_seconds,
        warning_only: row.warning_only,
        warning_reason: row.warning_reason,
        decision: record.decision || null,
        known_disposition: record.known_disposition || null,
        locked: Boolean(record.locked),
        notes: record.notes || null,
        revision: Number(record.revision || 0),
        updated_at: record.updated_at || null,
      };
    });
  }

  function exportReview() {
    const rows = exportedRows();
    const actionable = rows.filter((row) => !row.warning_only);
    const payload = {
      schema_version: 1,
      round_id: data.round_id,
      exported_at: new Date().toISOString(),
      summary: {
        candidate_count: rows.length,
        actionable_count: actionable.length,
        warning_count: rows.filter((row) => row.warning_only).length,
        complete_count: actionable.filter((row) => allowedDecisions.has(row.decision)).length,
        approved_usable_count: actionable.filter((row) => row.decision === "approve_usable").length,
        correct_speaker_unusable_count: actionable.filter((row) => row.decision === "correct_speaker_unusable").length,
        wrong_or_uncertain_speaker_count: actionable.filter((row) => row.decision === "wrong_or_uncertain_speaker").length,
        wrong_boundary_count: actionable.filter((row) => row.decision === "wrong_boundary").length,
        locked_wrong_speaker_count: rows.filter((row) => row.decision === "locked_rejected_wrong_speaker").length,
      },
      rows,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const href = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = href;
    link.download = "alexandria_three_voice_historical_provenance_review.json";
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(href), 500);
    $("#export-dialog").showModal();
  }

  function importReview(file) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const payload = JSON.parse(String(reader.result || ""));
        if (payload.round_id !== data.round_id || !Array.isArray(payload.rows)) {
          throw new Error("This JSON belongs to a different review round.");
        }
        const known = new Set(data.rows.map((row) => row.clip_id));
        payload.rows.forEach((row) => {
          if (!known.has(row.clip_id)) return;
          const source = data.rows.find((item) => item.clip_id === row.clip_id);
          if (source.warning_only) {
            rowRecord(source);
            return;
          }
          const decision = row.decision;
          saved[row.clip_id] = {
            clip_id: row.clip_id,
            decision: allowedDecisions.has(decision) ? decision : null,
            notes: row.notes || "",
            revision: Number(row.revision || 0),
            updated_at: row.updated_at || null,
          };
        });
        persistStorage();
        render();
      } catch (error) {
        window.alert(`Could not import review: ${error.message}`);
      } finally {
        $("#import-file").value = "";
      }
    };
    reader.readAsText(file);
  }

  document.querySelectorAll("[data-decision]").forEach((button) => {
    button.addEventListener("click", () => saveDecision(data.rows[currentIndex], button.dataset.decision));
  });
  $("#previous").addEventListener("click", () => move(-1));
  $("#next").addEventListener("click", () => move(1));
  $("#reload").addEventListener("click", () => loadAudio(data.rows[currentIndex]));
  $("#export-button").addEventListener("click", exportReview);
  $("#import-button").addEventListener("click", () => $("#import-file").click());
  $("#import-file").addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    if (file) importReview(file);
  });
  [$("#target-filter"), $("#status-filter")].forEach((element) => {
    element.addEventListener("change", () => {
      ensureCurrentVisible();
      render();
    });
  });
  $("#search").addEventListener("input", () => {
    ensureCurrentVisible();
    render();
  });
  $("#identity-audio").addEventListener("play", () => {
    audio.context.pause();
    audio.candidate.pause();
  });
  $("#context-audio").addEventListener("play", () => {
    audio.identity.pause();
    audio.candidate.pause();
  });
  $("#candidate-audio").addEventListener("play", () => {
    audio.identity.pause();
    audio.context.pause();
  });

  document.addEventListener("keydown", (event) => {
    const tag = document.activeElement && document.activeElement.tagName;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(tag)) return;
    const row = data.rows[currentIndex];
    const key = event.key.toLowerCase();
    if (key === "j") move(-1);
    else if (key === "k") move(1);
    else if (key === "1") playOnly(audio.identity);
    else if (key === "2") playOnly(audio.context);
    else if (key === "3" || key === " ") {
      event.preventDefault();
      playOnly(audio.candidate);
    } else if (!row.warning_only && key === "a") saveDecision(row, "approve_usable");
    else if (!row.warning_only && key === "u") saveDecision(row, "correct_speaker_unusable");
    else if (!row.warning_only && key === "s") saveDecision(row, "wrong_or_uncertain_speaker");
    else if (!row.warning_only && key === "b") saveDecision(row, "wrong_boundary");
  });

  data.rows.forEach(rowRecord);
  persistStorage();
  render();
})();
