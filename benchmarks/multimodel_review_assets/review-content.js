(() => {
  "use strict";

  const namespace = window.AlexandriaRound1 = window.AlexandriaRound1 || {};

  function create(context) {
    const { core, data, els, state } = context;

    function renderReferences(samples) {
      const keys = [...new Set(samples.map((sample) => sample.identity_reference_key))];
      els.referenceList.innerHTML = "";
      keys.forEach((key) => {
        const reference = data.identities[key];
        if (!reference) return;
        const card = document.createElement("article");
        card.className = "reference-card";
        const sharedReference = reference.original_audio && reference.original_audio === reference.conditioning_audio;
        const original = reference.original_audio
          ? referenceAudioRow(reference, sharedReference ? "Original and conditioning reference" : "Original source", reference.original_audio)
          : "";
        const conditioning = reference.conditioning_audio && !sharedReference
          ? referenceAudioRow(reference, "Conditioning clip", reference.conditioning_audio)
          : "";
        card.innerHTML = `
          <h3>${core.escapeHtml(reference.review_name)}</h3>
          <p class="kind">${core.escapeHtml(reference.kind.replaceAll("_", " "))}</p>
          ${original}${conditioning}
          ${reference.conditioning_transcript ? `<details><summary>Conditioning transcript</summary><p>${core.escapeHtml(reference.conditioning_transcript)}</p></details>` : ""}`;
        els.referenceList.appendChild(card);
      });
      if (!keys.length) {
        els.referenceList.innerHTML = '<p class="empty-state">No generated samples are available for this style yet.</p>';
      }
    }

    function referenceAudioRow(reference, label, source) {
      const accessibleLabel = `${reference.review_name}: ${label.toLowerCase()} audio`;
      return `<div class="reference-audio-row"><strong>${core.escapeHtml(label)}</strong><audio controls preload="none" aria-label="${core.escapeHtml(accessibleLabel)}" src="${core.escapeHtml(source)}"></audio></div>`;
    }

    function renderSamples(samples) {
      els.sampleList.innerHTML = "";
      if (!samples.length) {
        els.sampleList.innerHTML = '<div class="empty-state"><strong>No samples match the current filters.</strong><br>Clear the search, identity filter, or incomplete-only option.</div>';
        return;
      }
      const bySection = new Map();
      samples.forEach((sample) => {
        if (!bySection.has(sample.review_section_key)) bySection.set(sample.review_section_key, []);
        bySection.get(sample.review_section_key).push(sample);
      });
      bySection.forEach((sectionSamples) => {
        const section = document.createElement("section");
        section.className = "identity-section";
        const progress = core.completion(state.saved, sectionSamples);
        sectionSamples.sort((left, right) => left.sample_id.localeCompare(right.sample_id));
        section.innerHTML = `
          <header class="identity-section-header">
            <h3>${core.escapeHtml(sectionSamples[0].review_section_label)}</h3>
            <span>${progress.complete} / ${progress.ready} reviewed</span>
          </header>`;
        sectionSamples.forEach((sample, index) => section.appendChild(sampleCard(sample, index + 1)));
        els.sampleList.appendChild(section);
      });
    }

    function sampleCard(sample, ordinal) {
      const row = state.saved[sample.sample_id] || { sample_id: sample.sample_id, updated_at: null, revision: 0 };
      const card = document.createElement("article");
      card.className = "sample-card";
      card.dataset.sampleId = sample.sample_id;
      card.id = `sample-${sample.sample_id}`;
      const ratingFields = core.RATING_FIELDS.map(([field, label, help]) => ratingField({
        field, help, label, sampleId: sample.sample_id, value: row[field],
      })).join("");
      const binaryFields = core.BINARY_FIELDS.map(([field, label, help]) => binaryField({
        field, help, label, sampleId: sample.sample_id, value: row[field],
      })).join("");
      card.innerHTML = `
        <header class="sample-card-header">
          <div><h4>Candidate ${ordinal}</h4><p>Expected voice: ${core.escapeHtml(sample.expected_identity)} · ${core.escapeHtml(sample.sample_id)}</p></div>
          <span class="status-pill">Not reviewed</span>
        </header>
        <div class="sample-card-body">
          <p class="target-text">${core.escapeHtml(sample.target_text)}</p>
          <audio controls preload="none" aria-label="${core.escapeHtml(`${sample.expected_identity}: candidate ${ordinal} ${sample.style_label} audio, sample ${sample.sample_id}`)}" src="${core.escapeHtml(sample.audio)}"></audio>
          <details class="technical-evidence">
            <summary>Text and technical evidence</summary>
            <dl class="evidence-grid">
              <dt>Automatic transcript</dt><dd>${core.escapeHtml(sample.automatic_transcript || "Not evaluated yet")}</dd>
              <dt>Word error rate</dt><dd>${core.formatMetric(sample.word_error_rate)}</dd>
              <dt>Identity cosine</dt><dd>${core.formatMetric(sample.speaker_cosine)}</dd>
              <dt>Duration</dt><dd>${sample.audio_diagnostics ? `${core.formatMetric(sample.audio_diagnostics.duration_seconds, 2)} seconds` : "—"}</dd>
            </dl>
          </details>
          <div class="rating-grid">${ratingFields}</div>
          <div class="binary-grid">${binaryFields}</div>
          <label class="follow-up-toggle"><input type="checkbox" data-field="flag_for_follow_up" ${row.flag_for_follow_up === true ? "checked" : ""}> Flag for follow-up <span>Only when the scores do not tell the whole story.</span></label>
          <label class="notes-field">Notes<textarea data-field="notes" maxlength="10000" placeholder="Specific identity drift, delivery issue, text error, artifact, or reason to revisit.">${core.escapeHtml(typeof row.notes === "string" ? row.notes : "")}</textarea></label>
          <footer class="sample-footer"><span>Autosaves in this browser</span><span class="saved-indicator" aria-live="polite">Saved</span></footer>
        </div>`;
      card.querySelectorAll("[data-field]").forEach((control) => {
        const eventName = control.matches("textarea") ? "input" : "change";
        control.addEventListener(eventName, () => updateValue(sample.sample_id, control));
      });
      updateCardClass(card, sample.sample_id);
      return card;
    }

    function updateValue(sampleId, control) {
      let value;
      if (control.type === "checkbox") value = control.checked;
      else if (control.type === "radio") {
        if (!control.checked) return;
        value = control.value === "true" ? true : control.value === "false" ? false : Number(control.value);
      } else value = control.value || null;
      context.setValue(sampleId, control.dataset.field, value);
    }

    function ratingField({ sampleId, field, label, help, value }) {
      const buttons = [1, 2, 3, 4, 5].map((number) => `
        <label class="score-choice">
          <input type="radio" name="${sampleId}-${field}" data-field="${field}" value="${number}" ${Number(value) === number ? "checked" : ""}>
          <span>${number}</span>
        </label>`).join("");
      return `<fieldset class="rating-field"><legend>${core.escapeHtml(label)}</legend><small>${core.escapeHtml(help)}</small><div class="score-row">${buttons}</div></fieldset>`;
    }

    function binaryField({ sampleId, field, label, help, value }) {
      return `<fieldset class="binary-field"><legend>${core.escapeHtml(label)}</legend><small>${core.escapeHtml(help)}</small><div class="binary-choice-row">
        <label><input type="radio" name="${sampleId}-${field}" data-field="${field}" value="true" ${value === true ? "checked" : ""}><span>Yes</span></label>
        <label><input type="radio" name="${sampleId}-${field}" data-field="${field}" value="false" ${value === false ? "checked" : ""}><span>No</span></label>
      </div></fieldset>`;
    }

    function updateCardStatus(sampleId) {
      const card = document.querySelector(`[data-sample-id="${CSS.escape(sampleId)}"]`);
      if (card) updateCardClass(card, sampleId);
    }

    function updateCardClass(card, sampleId) {
      const row = state.saved[sampleId] || {};
      const complete = core.isComplete(state.saved, sampleId);
      card.classList.toggle("complete", complete);
      card.classList.toggle("flagged", row.flag_for_follow_up === true);
      const pill = card.querySelector(".status-pill");
      if (!pill) return;
      pill.textContent = complete ? "Reviewed" : "Not reviewed";
      pill.classList.toggle("complete", complete);
    }

    return { renderReferences, renderSamples, updateCardStatus };
  }

  namespace.content = { create };
})();
