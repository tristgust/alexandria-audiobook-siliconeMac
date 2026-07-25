(() => {
  const data = window.THREE_VOICE_BANK_BENCHMARK_DATA;
  if (!data || !Array.isArray(data.rows) || !data.rows.length) {
    throw new Error('Combined-bank benchmark data is missing.');
  }

  const storageKey = `alexandria:three-voice-bank-benchmark:${data.round_id}`;
  const decisionLabels = {
    candidate_A: 'Candidate A',
    candidate_B: 'Candidate B',
    neither: 'Neither candidate',
  };
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(storageKey) || '{}'); } catch {}
  let visibleRows = [];
  let currentVisibleIndex = 0;
  const $ = (selector) => document.querySelector(selector);
  const players = {
    identity: $('#identity-audio'),
    reference: $('#reference-audio'),
    A: $('#candidate-audio-a'),
    B: $('#candidate-audio-b'),
  };

  function record(row) {
    if (!saved[row.route_id]) {
      saved[row.route_id] = {
        route_id: row.route_id,
        revision: 0,
        candidate_A_issues: [],
        candidate_B_issues: [],
      };
    }
    return saved[row.route_id];
  }

  function complete(row) {
    return Boolean(record(row).decision);
  }

  function persist(row, field, value) {
    const item = record(row);
    item[field] = value;
    item.revision = Number(item.revision || 0) + 1;
    item.updated_at = new Date().toISOString();
    localStorage.setItem(storageKey, JSON.stringify(saved));
    refresh();
  }

  function currentRow() {
    return visibleRows[currentVisibleIndex] || null;
  }

  function searchText(row) {
    return [
      row.target_label,
      row.function_label,
      row.function,
      row.target_text,
      row.performance_reference_text,
      row.performance_reference_emotion,
      row.performance_reference_function,
    ].join(' ').toLowerCase();
  }

  function applyFilters({ preserveRouteId = null } = {}) {
    const target = $('#target-filter').value;
    const status = $('#status-filter').value;
    const query = $('#search').value.trim().toLowerCase();
    visibleRows = data.rows.filter((row) => {
      if (target !== 'all' && row.target !== target) return false;
      if (status === 'incomplete' && complete(row)) return false;
      if (status === 'complete' && !complete(row)) return false;
      if (query && !searchText(row).includes(query)) return false;
      return true;
    });
    let nextIndex = 0;
    if (preserveRouteId) {
      const found = visibleRows.findIndex((row) => row.route_id === preserveRouteId);
      if (found >= 0) nextIndex = found;
    }
    currentVisibleIndex = Math.min(nextIndex, Math.max(0, visibleRows.length - 1));
    $('#visible-count').textContent = `${visibleRows.length} visible`;
    render();
  }

  function pauseAll(except = null) {
    for (const [key, player] of Object.entries(players)) {
      if (key !== except) player.pause();
    }
  }

  function loadAudio(row) {
    for (const player of Object.values(players)) {
      player.pause();
      player.removeAttribute('src');
      player.load();
    }
    if (!row) return;
    players.identity.src = row.identity_audio;
    players.reference.src = row.performance_reference_audio;
    players.A.src = row.candidate_A.audio;
    players.B.src = row.candidate_B.audio;
    for (const player of Object.values(players)) player.load();
  }

  for (const [key, player] of Object.entries(players)) {
    player.addEventListener('play', () => pauseAll(key));
  }

  function drawNav() {
    const nav = $('#route-nav');
    nav.replaceChildren();
    visibleRows.forEach((row, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = String(index + 1);
      button.classList.toggle('active', index === currentVisibleIndex);
      button.classList.toggle('complete', complete(row));
      button.classList.toggle('neither', record(row).decision === 'neither');
      button.title = `${row.target_label}: ${row.function_label}`;
      button.onclick = () => {
        currentVisibleIndex = index;
        render();
      };
      nav.append(button);
    });
  }

  function issueKey(candidate) {
    return `candidate_${candidate}_issues`;
  }

  function renderIssues(row) {
    const item = record(row);
    document.querySelectorAll('.issue-tags').forEach((group) => {
      const candidate = group.dataset.candidate;
      const selected = new Set(item[issueKey(candidate)] || []);
      group.querySelectorAll('[data-issue]').forEach((button) => {
        button.classList.toggle('selected', selected.has(button.dataset.issue));
      });
    });
  }

  function refresh() {
    const completed = data.rows.filter(complete).length;
    $('#progress').textContent = `${completed} / ${data.rows.length} complete`;
    const row = currentRow();
    const item = row ? record(row) : {};
    const isComplete = Boolean(item.decision);
    $('#status').textContent = isComplete ? 'Complete' : 'Pending';
    $('#status').classList.toggle('complete', isComplete);
    const state = $('#current-decision');
    state.textContent = isComplete ? decisionLabels[item.decision] : 'No decision yet.';
    state.classList.toggle('approved', item.decision === 'candidate_A' || item.decision === 'candidate_B');
    state.classList.toggle('problem', item.decision === 'neither');
    if (row) renderIssues(row);
    drawNav();
  }

  function render() {
    const row = currentRow();
    $('#card').hidden = !row;
    $('#empty').hidden = Boolean(row);
    if (!row) {
      $('#route-nav').replaceChildren();
      loadAudio(null);
      refresh();
      return;
    }
    const item = record(row);
    $('#ordinal').textContent = `Benchmark ${currentVisibleIndex + 1} of ${visibleRows.length}`;
    $('#target-label').textContent = row.target_label;
    $('#function-label').textContent = row.function_label;
    $('#target-text').textContent = row.target_text;
    $('#reference-text').textContent = row.performance_reference_text;
    $('#reference-emotion').textContent = `${row.performance_reference_emotion} · ${row.performance_reference_function}`;
    $('#candidate-a-warning').hidden = Boolean(row.candidate_A.technical_pass);
    $('#candidate-b-warning').hidden = Boolean(row.candidate_B.technical_pass);
    $('#notes').value = item.notes || '';
    $('#notes').oninput = () => persist(row, 'notes', $('#notes').value);
    $('#previous').disabled = currentVisibleIndex === 0;
    $('#next').disabled = currentVisibleIndex === visibleRows.length - 1;
    loadAudio(row);
    refresh();
    window.scrollTo({ top: 0, behavior: 'instant' });
  }

  function advanceAfterDecision(routeId) {
    if ($('#status-filter').value === 'incomplete') {
      applyFilters();
      return;
    }
    const index = visibleRows.findIndex((row) => row.route_id === routeId);
    currentVisibleIndex = Math.min(index + 1, visibleRows.length - 1);
    render();
  }

  function setDecision(value) {
    const row = currentRow();
    if (!row) return;
    persist(row, 'decision', value);
    advanceAfterDecision(row.route_id);
  }

  function toggleIssue(candidate, issue) {
    const row = currentRow();
    if (!row) return;
    const key = issueKey(candidate);
    const selected = new Set(record(row)[key] || []);
    selected.has(issue) ? selected.delete(issue) : selected.add(issue);
    persist(row, key, [...selected].sort());
  }

  function exportReview() {
    const rows = data.rows.map((row) => ({
      route_id: row.route_id,
      target: row.target,
      target_label: row.target_label,
      function: row.function,
      function_label: row.function_label,
      target_text: row.target_text,
      ...record(row),
    }));
    const payload = {
      schema_version: 1,
      round_id: data.round_id,
      exported_at: new Date().toISOString(),
      summary: {
        candidate_count: rows.length,
        complete_count: rows.filter((row) => row.decision).length,
        candidate_A_count: rows.filter((row) => row.decision === 'candidate_A').length,
        candidate_B_count: rows.filter((row) => row.decision === 'candidate_B').length,
        neither_count: rows.filter((row) => row.decision === 'neither').length,
      },
      rows,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'alexandria_three_voice_combined_bank_benchmark_review.json';
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 500);
    $('#done').showModal();
  }

  async function importReview(file) {
    const payload = JSON.parse(await file.text());
    if (payload.round_id !== data.round_id || !Array.isArray(payload.rows)) {
      throw new Error('This JSON belongs to a different benchmark review.');
    }
    const known = new Set(data.rows.map((row) => row.route_id));
    for (const row of payload.rows) {
      if (!known.has(row.route_id)) continue;
      const existing = saved[row.route_id];
      if (!existing || Number(row.revision || 0) >= Number(existing.revision || 0)) {
        saved[row.route_id] = {
          route_id: row.route_id,
          revision: Number(row.revision || 0),
          decision: row.decision || undefined,
          notes: row.notes || '',
          candidate_A_issues: Array.isArray(row.candidate_A_issues) ? row.candidate_A_issues : [],
          candidate_B_issues: Array.isArray(row.candidate_B_issues) ? row.candidate_B_issues : [],
          updated_at: row.updated_at || payload.exported_at || new Date().toISOString(),
        };
      }
    }
    localStorage.setItem(storageKey, JSON.stringify(saved));
    applyFilters({ preserveRouteId: currentRow()?.route_id });
  }

  document.querySelectorAll('[data-decision]').forEach((button) => {
    button.onclick = () => setDecision(button.dataset.decision);
  });
  document.querySelectorAll('.issue-tags [data-issue]').forEach((button) => {
    button.onclick = () => toggleIssue(button.closest('.issue-tags').dataset.candidate, button.dataset.issue);
  });
  $('#target-filter').onchange = () => applyFilters({ preserveRouteId: currentRow()?.route_id });
  $('#status-filter').onchange = () => applyFilters({ preserveRouteId: currentRow()?.route_id });
  $('#search').oninput = () => applyFilters({ preserveRouteId: currentRow()?.route_id });
  $('#previous').onclick = () => {
    if (currentVisibleIndex > 0) {
      currentVisibleIndex -= 1;
      render();
    }
  };
  $('#next').onclick = () => {
    if (currentVisibleIndex < visibleRows.length - 1) {
      currentVisibleIndex += 1;
      render();
    }
  };
  $('#reload').onclick = () => loadAudio(currentRow());
  $('#export').onclick = exportReview;
  $('#import').onclick = () => $('#import-file').click();
  $('#import-file').onchange = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try { await importReview(file); } catch (error) { alert(error.message || String(error)); }
    event.target.value = '';
  };

  document.addEventListener('keydown', (event) => {
    const tag = document.activeElement?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    const key = event.key.toLowerCase();
    if (key === 'a') setDecision('candidate_A');
    else if (key === 'b') setDecision('candidate_B');
    else if (key === 'n') setDecision('neither');
    else if (key === '1') { pauseAll('A'); players.A.paused ? players.A.play() : players.A.pause(); }
    else if (key === '2') { pauseAll('B'); players.B.paused ? players.B.play() : players.B.pause(); }
    else if (key === '0') { pauseAll('reference'); players.reference.paused ? players.reference.play() : players.reference.pause(); }
    else if (key === 'j' && currentVisibleIndex > 0) { currentVisibleIndex -= 1; render(); }
    else if (key === 'k' && currentVisibleIndex < visibleRows.length - 1) { currentVisibleIndex += 1; render(); }
  });

  visibleRows = data.rows.slice();
  render();
})();
