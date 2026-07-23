(() => {
  'use strict';

  const DATA = window.NARRATOR_CONTEXT_DATA;
  if (!DATA) throw new Error('Narrator context data is missing.');

  const STORAGE_KEY = `alexandria:${DATA.round_id}:review`;
  const categories = DATA.categories;
  const correctionById = new Map(DATA.corrections.map((row) => [row.sample_id, row]));
  const supplementById = new Map(DATA.supplement.map((row) => [row.sample_id, row]));

  const initialState = () => ({
    schema_version: 1,
    round_id: DATA.round_id,
    revision: 0,
    updated_at: new Date().toISOString(),
    corrections: Object.fromEntries(DATA.corrections.map((row) => [row.sample_id, {
      sample_id: row.sample_id,
      action: 'pending',
      transcript: row.original.transcript,
      instruction: row.original.instruction,
      category: row.original.category,
      notes: row.original.notes || '',
      updated_at: null,
    }])),
    supplement: Object.fromEntries(DATA.supplement.map((row) => [row.sample_id, {
      sample_id: row.sample_id,
      status: 'pending',
      transcript: row.transcript,
      transcript_confirmed: false,
      instruction: row.instruction,
      category: row.category,
      notes: '',
      updated_at: null,
    }])),
  });

  function mergeState(base, candidate) {
    if (!candidate || candidate.round_id !== DATA.round_id) return base;
    const merged = structuredClone(base);
    for (const [id, value] of Object.entries(candidate.corrections || {})) {
      if (!correctionById.has(id)) continue;
      merged.corrections[id] = { ...merged.corrections[id], ...value, sample_id: id };
    }
    for (const [id, value] of Object.entries(candidate.supplement || {})) {
      if (!supplementById.has(id)) continue;
      merged.supplement[id] = { ...merged.supplement[id], ...value, sample_id: id };
    }
    merged.revision = Math.max(Number(base.revision || 0), Number(candidate.revision || 0));
    merged.updated_at = candidate.updated_at || base.updated_at;
    return merged;
  }

  function loadState() {
    const base = initialState();
    try {
      return mergeState(base, JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null'));
    } catch (error) {
      console.warn('Could not load saved review', error);
      return base;
    }
  }

  let state = loadState();
  let mode = 'corrections';
  let selectedId = DATA.corrections[0]?.sample_id || DATA.supplement[0]?.sample_id || null;
  let filter = 'all';
  let search = '';
  let toastTimer;

  const el = (id) => document.getElementById(id);
  const queueList = el('queue-list');
  const correctionView = el('correction-view');
  const supplementView = el('supplement-view');
  const emptyState = el('empty-state');

  function save() {
    state.revision = Number(state.revision || 0) + 1;
    state.updated_at = new Date().toISOString();
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    updateProgress();
  }

  function showToast(message) {
    const toast = el('toast');
    toast.textContent = message;
    toast.classList.add('is-visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('is-visible'), 2200);
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function actionClass(value) {
    if (['apply_recommendation', 'edited', 'keep_original', 'accepted'].includes(value)) return 'completed';
    if (['exclude', 'rejected'].includes(value)) return 'excluded';
    if (value === 'replace_audio') return 'replacement';
    return '';
  }

  function actionLabel(value) {
    return {
      pending: 'pending',
      apply_recommendation: 'context applied',
      edited: 'edited',
      keep_original: 'original kept',
      exclude: 'excluded',
      replace_audio: 'replace old clip',
      accepted: 'accepted',
      rejected: 'rejected',
    }[value] || value;
  }

  function correctionState(id) { return state.corrections[id]; }
  function supplementState(id) { return state.supplement[id]; }

  function currentRows() { return mode === 'corrections' ? DATA.corrections : DATA.supplement; }

  function statusFor(row) {
    return mode === 'corrections' ? correctionState(row.sample_id).action : supplementState(row.sample_id).status;
  }

  function isCompleted(status) {
    return mode === 'corrections' ? status !== 'pending' : status !== 'pending';
  }

  function rowMatches(row) {
    const status = statusFor(row);
    if (filter === 'pending' && status !== 'pending') return false;
    if (filter === 'completed' && !isCompleted(status)) return false;
    if (filter === 'excluded' && !['exclude', 'replace_audio', 'rejected'].includes(status)) return false;
    const haystack = `${row.scene} ${row.context} ${row.kind === 'correction' ? row.original.transcript : row.transcript}`.toLowerCase();
    return !search || haystack.includes(search);
  }

  function visibleRows() { return currentRows().filter(rowMatches); }

  function renderQueue() {
    const rows = visibleRows();
    el('queue-summary').textContent = `${rows.length} shown · ${currentRows().length} total`;
    queueList.innerHTML = rows.map((row, index) => {
      const status = statusFor(row);
      const line = row.kind === 'correction' ? row.original.transcript : row.transcript;
      return `<button type="button" class="queue-item ${row.sample_id === selectedId ? 'is-selected' : ''}" data-id="${row.sample_id}" role="option" aria-selected="${row.sample_id === selectedId}">
        <span class="queue-index">${String(index + 1).padStart(2, '0')}</span>
        <span class="queue-copy"><strong>${escapeHtml(row.scene)}</strong><span>${escapeHtml(line)}</span></span>
        <span class="queue-state ${actionClass(status)}" title="${escapeHtml(actionLabel(status))}"></span>
      </button>`;
    }).join('');
    queueList.querySelectorAll('.queue-item').forEach((button) => button.addEventListener('click', () => select(button.dataset.id)));
    if (rows.length && !rows.some((row) => row.sample_id === selectedId)) select(rows[0].sample_id, false);
    if (!rows.length) {
      selectedId = null;
      renderSelected();
    }
  }

  function populateCategory(select, value) {
    select.innerHTML = Object.entries(categories).map(([key, label]) => `<option value="${key}">${escapeHtml(label)}</option>`).join('');
    select.value = value;
  }

  function select(id, rerenderQueue = true) {
    selectedId = id;
    if (rerenderQueue) renderQueue();
    renderSelected();
    queueList.querySelector(`[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest' });
  }

  function renderSelected() {
    emptyState.hidden = Boolean(selectedId);
    correctionView.hidden = mode !== 'corrections' || !selectedId;
    supplementView.hidden = mode !== 'supplement' || !selectedId;
    if (!selectedId) return;
    if (mode === 'corrections') renderCorrection(correctionById.get(selectedId));
    else renderSupplement(supplementById.get(selectedId));
  }

  function renderCorrection(row) {
    const current = correctionState(row.sample_id);
    el('correction-scene').textContent = row.scene;
    el('correction-source').textContent = `Canonical reference: ${row.source_reference}`;
    el('correction-context').textContent = row.context;
    el('correction-before').textContent = row.before.length ? row.before.join('  ') : 'No adjacent transcript recovered.';
    el('correction-current').textContent = row.original.transcript;
    el('correction-after').textContent = row.after.length ? row.after.join('  ') : 'No adjacent transcript recovered.';
    el('original-category').textContent = categories[row.original.category] || row.original.category;
    el('original-instruction').textContent = row.original.instruction;
    el('original-notes').textContent = row.original.notes || 'None';
    el('recommended-category').textContent = categories[row.recommendation.category] || row.recommendation.category;
    el('recommended-instruction').textContent = row.recommendation.instruction;
    el('recommended-reason').textContent = row.recommendation.reason;
    el('correction-audio').src = row.audio_url;
    el('correction-transcript').value = current.transcript;
    el('correction-instruction').value = current.instruction;
    populateCategory(el('correction-category'), current.category);
    el('correction-notes').value = current.notes || '';
    const status = el('correction-status');
    status.textContent = actionLabel(current.action);
    status.className = `status-badge ${actionClass(current.action)}`;
    const apply = el('apply-context');
    apply.textContent = row.recommendation.default_action === 'replace_audio'
      ? 'Replace with clean recut'
      : row.recommendation.default_action === 'exclude'
        ? 'Use exclusion recommendation'
        : 'Apply context recommendation';
  }

  function renderSupplement(row) {
    const current = supplementState(row.sample_id);
    el('supplement-scene').textContent = row.scene;
    el('supplement-time').textContent = `Original video · ${formatTime(row.source_start_seconds)}–${formatTime(row.source_end_seconds)}`;
    el('supplement-context').textContent = row.context;
    el('supplement-audio').src = row.audio_url;
    el('supplement-transcript').value = current.transcript;
    el('supplement-confirmed').checked = Boolean(current.transcript_confirmed);
    el('supplement-instruction').value = current.instruction;
    populateCategory(el('supplement-category'), current.category);
    el('supplement-notes').value = current.notes || '';
    const status = el('supplement-status');
    status.textContent = actionLabel(current.status);
    status.className = `status-badge ${actionClass(current.status)}`;
  }

  function formatTime(seconds) {
    const value = Math.max(0, Math.round(Number(seconds || 0)));
    const minutes = Math.floor(value / 60);
    return `${minutes}:${String(value % 60).padStart(2, '0')}`;
  }

  function updateCorrectionFields(action = 'edited') {
    if (!selectedId || mode !== 'corrections') return;
    const current = correctionState(selectedId);
    current.transcript = el('correction-transcript').value.trim();
    current.instruction = el('correction-instruction').value.trim();
    current.category = el('correction-category').value;
    current.notes = el('correction-notes').value;
    if (current.action !== 'exclude' && current.action !== 'replace_audio') current.action = action;
    current.updated_at = new Date().toISOString();
    save();
  }

  function updateSupplementFields() {
    if (!selectedId || mode !== 'supplement') return;
    const current = supplementState(selectedId);
    current.transcript = el('supplement-transcript').value.trim();
    current.transcript_confirmed = el('supplement-confirmed').checked;
    current.instruction = el('supplement-instruction').value.trim();
    current.category = el('supplement-category').value;
    current.notes = el('supplement-notes').value;
    current.updated_at = new Date().toISOString();
    save();
  }

  function applyRecommendation() {
    const row = correctionById.get(selectedId);
    const current = correctionState(selectedId);
    current.transcript = row.recommendation.transcript;
    current.instruction = row.recommendation.instruction;
    current.category = row.recommendation.category;
    current.action = row.recommendation.default_action === 'replace_audio' ? 'replace_audio'
      : row.recommendation.default_action === 'exclude' ? 'exclude'
        : 'apply_recommendation';
    current.updated_at = new Date().toISOString();
    save(); renderQueue(); renderSelected(); autoAdvance();
  }

  function keepOriginal() {
    const row = correctionById.get(selectedId);
    state.corrections[selectedId] = {
      ...state.corrections[selectedId],
      transcript: row.original.transcript,
      instruction: row.original.instruction,
      category: row.original.category,
      action: 'keep_original',
      updated_at: new Date().toISOString(),
    };
    save(); renderQueue(); renderSelected(); autoAdvance();
  }

  function excludeCorrection() {
    const current = correctionState(selectedId);
    current.action = 'exclude';
    current.updated_at = new Date().toISOString();
    save(); renderQueue(); renderSelected(); autoAdvance();
  }

  function setSupplementStatus(status) {
    updateSupplementFields();
    const current = supplementState(selectedId);
    if (status === 'accepted' && (!current.transcript_confirmed || !current.transcript || !current.instruction)) {
      showToast('Confirm the transcript and keep a nonempty instruction before accepting.');
      return;
    }
    current.status = status;
    current.updated_at = new Date().toISOString();
    save(); renderQueue(); renderSelected();
    if (status !== 'pending') autoAdvance();
  }

  function autoAdvance() {
    const rows = visibleRows();
    const index = rows.findIndex((row) => row.sample_id === selectedId);
    const next = rows.slice(index + 1).find((row) => statusFor(row) === 'pending')
      || rows.find((row) => statusFor(row) === 'pending');
    if (next) select(next.sample_id);
  }

  function move(delta) {
    const rows = visibleRows();
    if (!rows.length) return;
    const index = Math.max(0, rows.findIndex((row) => row.sample_id === selectedId));
    select(rows[(index + delta + rows.length) % rows.length].sample_id);
  }

  function updateProgress() {
    const corrections = Object.values(state.corrections);
    const supplement = Object.values(state.supplement);
    const correctionDone = corrections.filter((row) => row.action !== 'pending').length;
    const supplementDone = supplement.filter((row) => row.status !== 'pending').length;
    const done = correctionDone + supplementDone;
    const total = corrections.length + supplement.length;
    el('progress-title').textContent = `${done} of ${total} decisions complete`;
    el('progress-detail').textContent = `${correctionDone}/${corrections.length} corrections · ${supplementDone}/${supplement.length} supplement`;
    el('progress-bar').style.width = `${total ? done / total * 100 : 0}%`;
    el('correction-count').textContent = `${correctionDone}/${corrections.length}`;
    el('supplement-count').textContent = `${supplementDone}/${supplement.length}`;
  }

  function exportPayload() {
    return {
      schema_version: 1,
      round_id: DATA.round_id,
      exported_at: new Date().toISOString(),
      revision: state.revision,
      summary: {
        correction_count: DATA.corrections.length,
        correction_complete_count: Object.values(state.corrections).filter((row) => row.action !== 'pending').length,
        supplement_count: DATA.supplement.length,
        supplement_accepted_count: Object.values(state.supplement).filter((row) => row.status === 'accepted').length,
        supplement_rejected_count: Object.values(state.supplement).filter((row) => row.status === 'rejected').length,
      },
      corrections: DATA.corrections.map((row) => state.corrections[row.sample_id]),
      supplement: DATA.supplement.map((row) => state.supplement[row.sample_id]),
    };
  }

  function downloadReview() {
    const payload = JSON.stringify(exportPayload(), null, 2);
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([payload], { type: 'application/json' }));
    link.download = 'alexandria_narrator_context_emotion_review.json';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    showToast('Review exported.');
  }

  async function importReview(file) {
    if (!file) return;
    try {
      const payload = JSON.parse(await file.text());
      const candidate = initialState();
      candidate.revision = Number(payload.revision || 0);
      candidate.updated_at = payload.exported_at || new Date().toISOString();
      for (const row of payload.corrections || []) {
        if (correctionById.has(row.sample_id)) candidate.corrections[row.sample_id] = { ...candidate.corrections[row.sample_id], ...row };
      }
      for (const row of payload.supplement || []) {
        if (supplementById.has(row.sample_id)) candidate.supplement[row.sample_id] = { ...candidate.supplement[row.sample_id], ...row };
      }
      state = mergeState(state, candidate);
      save(); renderQueue(); renderSelected();
      showToast('Review imported.');
    } catch (error) {
      console.error(error);
      showToast('That review file could not be imported.');
    } finally {
      el('import-file').value = '';
    }
  }

  document.querySelectorAll('.mode-tab').forEach((button) => button.addEventListener('click', () => {
    mode = button.dataset.mode;
    document.querySelectorAll('.mode-tab').forEach((item) => item.classList.toggle('is-active', item === button));
    selectedId = currentRows()[0]?.sample_id || null;
    filter = 'all';
    el('status-filter').value = 'all';
    renderQueue(); renderSelected();
  }));

  el('search-input').addEventListener('input', (event) => { search = event.target.value.trim().toLowerCase(); renderQueue(); });
  el('status-filter').addEventListener('change', (event) => { filter = event.target.value; renderQueue(); });
  el('export-button').addEventListener('click', downloadReview);
  el('import-file').addEventListener('change', (event) => importReview(event.target.files?.[0]));
  el('keep-original').addEventListener('click', keepOriginal);
  el('apply-context').addEventListener('click', applyRecommendation);
  el('exclude-correction').addEventListener('click', excludeCorrection);
  el('accept-supplement').addEventListener('click', () => setSupplementStatus('accepted'));
  el('reject-supplement').addEventListener('click', () => setSupplementStatus('rejected'));
  el('pending-supplement').addEventListener('click', () => setSupplementStatus('pending'));

  ['correction-transcript', 'correction-instruction', 'correction-category', 'correction-notes'].forEach((id) => {
    el(id).addEventListener('change', () => { updateCorrectionFields(); renderQueue(); renderSelected(); });
  });
  ['supplement-transcript', 'supplement-confirmed', 'supplement-instruction', 'supplement-category', 'supplement-notes'].forEach((id) => {
    el(id).addEventListener('change', updateSupplementFields);
  });

  document.addEventListener('keydown', (event) => {
    const tag = event.target?.tagName?.toLowerCase();
    const editing = ['input', 'textarea', 'select'].includes(tag);
    if (event.code === 'Space' && !editing) {
      event.preventDefault();
      const audio = mode === 'corrections' ? el('correction-audio') : el('supplement-audio');
      if (audio.paused) audio.play(); else audio.pause();
    } else if (!editing && event.key.toLowerCase() === 'a') {
      if (mode === 'corrections') applyRecommendation(); else setSupplementStatus('accepted');
    } else if (!editing && event.key.toLowerCase() === 'o' && mode === 'corrections') {
      keepOriginal();
    } else if (!editing && event.key.toLowerCase() === 'r') {
      if (mode === 'corrections') excludeCorrection(); else setSupplementStatus('rejected');
    } else if (!editing && event.key.toLowerCase() === 'j') move(-1);
    else if (!editing && event.key.toLowerCase() === 'l') move(1);
  });

  updateProgress();
  renderQueue();
  renderSelected();
})();
