'use strict';

import { createTaskImportSurface } from '/static/components/task_import_surface.js';
import { downloadTaskBundle } from './task_bundle_download.js';

const UI = globalThis.AlexandriaUI;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

async function runButton(button, label, operation) {
  const prior = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try { return await operation(); }
  finally { button.disabled = false; button.textContent = prior; }
}

function approvedEntry(candidate) {
  const entry = structuredClone(candidate);
  entry.review = {
    ...(entry.review || {}),
    state: 'approved',
    reviewer: 'Alexandria user',
    reviewed_at_utc: new Date().toISOString(),
  };
  return entry;
}

export function createScriptPronunciationGuidance({ api, signal, report }) {
  let registryFingerprint = null;
  const root = document.createElement('section');
  root.className = 'script-workflow-panel script-pronunciation-guidance';
  const heading = text('h2', 'section-title', 'Review names and pronunciation');
  heading.tabIndex = -1;
  const state = text('div', 'transaction-status', 'Pronunciation status not loaded.');
  state.setAttribute('role', 'status');
  state.setAttribute('aria-live', 'polite');
  const exportTask = UI.button({
    label: 'Download pronunciation task bundle',
    variant: 'secondary',
    attributes: { 'data-pronunciation-task-export': '' },
  });
  const actions = document.createElement('div');
  actions.className = 'script-workflow-actions';
  actions.append(exportTask);
  const candidateHost = document.createElement('div');
  candidateHost.className = 'pronunciation-task-candidates';

  const renderCandidates = (imported, host, statusNode) => {
    if (imported.task_type !== 'pronunciation_guidance') {
      statusNode.textContent = 'This completed task belongs to another workflow.';
      host.replaceChildren(UI.notice({
        tone: 'warning',
        title: 'Different task type',
        body: 'Use the importer attached to the task’s native Alexandria stage.',
        live: true,
      }));
      return;
    }
    const application = imported.application || {};
    if (application.explicit_acceptance_required !== true) {
      statusNode.textContent = 'The imported result is missing the pronunciation review gate.';
      host.replaceChildren(UI.notice({
        tone: 'error',
        title: 'Pronunciation review gate missing',
        body: 'Alexandria did not expose these entries for acceptance. Re-export the task from the current Script.',
        live: true,
      }));
      return;
    }
    const entries = Array.isArray(application.entries) ? application.entries : [];
    registryFingerprint = application.registry_fingerprint || registryFingerprint;
    statusNode.textContent = entries.length
      ? `${entries.length} pronunciation candidate${entries.length === 1 ? '' : 's'} ready for review. Nothing has been accepted.`
      : 'The completed task returned no pronunciation candidates.';
    const list = document.createElement('div');
    list.className = 'pronunciation-task-candidate-list';
    entries.forEach((candidate, index) => {
      const card = document.createElement('article');
      card.className = 'pronunciation-task-candidate';
      card.dataset.pronunciationCandidate = candidate.pronunciation_id || String(index);
      const title = text(
        'h3',
        'entity-title',
        `${candidate.original} → ${candidate.spoken_form || candidate.phonetic_hint}`,
      );
      const details = text(
        'p',
        'metadata',
        `Chunk ${Number(candidate.chunk_index) + 1} · characters ${candidate.start_char}–${candidate.end_char}`,
      );
      const rationale = text(
        'p',
        'support-status-copy',
        candidate.review?.notes || 'No rationale returned.',
      );
      const previewStatus = text(
        'div',
        'transaction-status',
        'Preview has not been checked.',
      );
      previewStatus.setAttribute('role', 'status');
      previewStatus.setAttribute('aria-live', 'polite');
      const preview = UI.button({
        label: 'Preview text',
        variant: 'secondary',
        size: 'compact',
      });
      const accept = UI.button({
        label: 'Accept guidance',
        variant: 'primary',
        size: 'compact',
      });
      preview.addEventListener('click', async () => {
        const response = await runButton(preview, 'Checking…', () => api.post(
          '/api/pronunciation-registry/preview',
          {
            chunk_index: candidate.chunk_index,
            candidate_entry: candidate,
            generate_audio: false,
          },
          { signal },
        ));
        if (!response.ok) {
          previewStatus.textContent = response.error;
          return;
        }
        previewStatus.textContent = `Synthesis preview: ${response.data.synthesis_text}`;
      });
      accept.addEventListener('click', async () => {
        const response = await runButton(accept, 'Accepting…', () => api.post(
          '/api/pronunciation-registry/entries',
          {
            entry: approvedEntry(candidate),
            expected_registry_fingerprint: registryFingerprint,
          },
          { signal },
        ));
        if (!response.ok) {
          report('Pronunciation guidance was not accepted', response.error);
          return;
        }
        registryFingerprint = response.data.registry?.registry_fingerprint
          || registryFingerprint;
        accept.disabled = true;
        preview.disabled = true;
        card.dataset.state = 'accepted';
        previewStatus.textContent = 'Accepted in the project pronunciation registry.';
        const invalidated = response.data.audio_invalidation?.affected_chunk_count || 0;
        report(
          'Pronunciation guidance accepted',
          invalidated
            ? `${invalidated} affected recording${invalidated === 1 ? '' : 's'} marked stale for regeneration.`
            : 'The reviewed exact occurrence is now available to synthesis.',
          'success',
        );
        await refresh();
      });
      const rowActions = document.createElement('div');
      rowActions.className = 'script-workflow-actions';
      rowActions.append(preview, accept);
      card.append(title, details, rationale, previewStatus, rowActions);
      list.append(card);
    });
    candidateHost.replaceChildren(list);
    host.replaceChildren(candidateHost);
  };

  const importer = createTaskImportSurface({
    api,
    signal,
    title: 'Import completed pronunciation task',
    description: 'Alexandria validates exact chunk hashes and character offsets, then shows draft candidates here. Import never changes the Script, registry, or audio.',
    report,
    onImported: async (imported, host, statusNode) => {
      renderCandidates(imported, host, statusNode);
    },
  });
  importer.section.classList.add('script-import-workflow');

  root.append(
    heading,
    text(
      'p',
      'metadata',
      'Download a ChatGPT task for difficult names and terms. Returned guidance stays draft until you preview and explicitly accept each exact occurrence.',
    ),
    state,
    actions,
    importer.section,
  );

  exportTask.addEventListener('click', () => {
    void downloadTaskBundle({
      api,
      signal,
      button: exportTask,
      taskType: 'pronunciation_guidance',
      onError: (error) => report('Pronunciation task could not be downloaded', error),
      onDownloaded: () => report(
        'Pronunciation task bundle downloaded',
        'Attach the ZIP to ChatGPT, then import the completed ZIP in this section.',
        'success',
      ),
    });
  });

  const refresh = async () => {
    const response = await api.get('/api/pronunciation-registry', { signal });
    if (!response.ok) {
      state.textContent = response.error;
      exportTask.disabled = true;
      return;
    }
    registryFingerprint = response.data.registry_fingerprint;
    const summary = response.data.summary || {};
    const approved = Number(summary.approved_count) || 0;
    const stale = Number(summary.stale_anchor_count) || 0;
    state.textContent = stale
      ? `${approved} approved exact occurrence${approved === 1 ? '' : 's'}; ${stale} stale anchor${stale === 1 ? '' : 's'} need review.`
      : `${approved} approved exact pronunciation occurrence${approved === 1 ? '' : 's'}.`;
    exportTask.disabled = false;
  };

  return Object.freeze({
    root,
    refresh,
    focus() {
      root.scrollIntoView({ block: 'nearest' });
      heading.focus({ preventScroll: true });
    },
  });
}
