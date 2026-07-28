'use strict';

import { createScriptImportWorkflows } from './script_import_workflows.js';

const UI = globalThis.AlexandriaUI;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

function facts(items) {
  const list = document.createElement('dl');
  list.className = 'script-workflow-facts';
  items.forEach(([label, value]) => {
    const row = document.createElement('div');
    row.append(text('dt', '', label), text('dd', '', value || '—'));
    list.append(row);
  });
  return list;
}

async function runButton(button, label, operation) {
  const prior = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try { return await operation(); }
  finally { button.disabled = false; button.textContent = prior; }
}

export function createScriptWorkflows({
  api, signal, shell, projectId, getModel, onReload, report,
}) {
  const root = document.createElement('section');
  root.className = 'script-workflows';
  const generationContent = document.createElement('div');
  generationContent.className = 'script-workflow-panel';
  const generationState = text('div', 'transaction-status', 'Generation status not loaded.');
  const generationActions = document.createElement('div');
  generationActions.className = 'script-workflow-actions';
  const generate = UI.button({ label: 'Generate locally', variant: 'secondary' });
  const review = UI.button({ label: 'Run contextual review', variant: 'secondary' });
  const exportTask = UI.button({ label: 'Export Script Task Bundle', variant: 'secondary' });
  generationActions.append(generate, review, exportTask);
  const taskScope = UI.notice({
    tone: 'information',
    title: 'This bundle creates the Script only',
    body: 'It does not discover relationships, aliases, roles, groups, non-speaking figures, visual dossiers, or production Voices. Use Full Cast tasks in Cast for whole-book identity enrichment.',
  });
  const taskResult = document.createElement('div');
  taskResult.className = 'script-workflow-result';
  const imports = createScriptImportWorkflows({
    api, signal, shell, projectId, onReload, report,
  });
  generationContent.append(
    generationState,
    generationActions,
    taskScope,
    taskResult,
    imports.completedTaskSection,
    imports.importSection,
  );

  const provenanceContent = document.createElement('div');
  provenanceContent.className = 'script-workflow-panel';
  const provenanceFacts = document.createElement('div');
  const versionsHost = document.createElement('div');
  versionsHost.className = 'script-version-list';
  provenanceContent.append(provenanceFacts, versionsHost);

  const generationDisclosure = UI.disclosure({
    label: 'Generation options', content: generationContent,
  });
  generationDisclosure.dataset.scriptWorkflow = 'generation';
  const provenanceDisclosure = UI.disclosure({
    label: 'Provenance and versions', content: provenanceContent,
  });
  provenanceDisclosure.dataset.scriptWorkflow = 'provenance';
  root.append(generationDisclosure, provenanceDisclosure);

  const refreshGeneration = async () => {
    const result = await api.get('/api/script_generation/status', { signal });
    if (!result.ok) {
      generationState.textContent = result.error;
      return;
    }
    const status = result.data || {};
    generationState.textContent = status.process?.running
      ? 'Script generation is running.'
      : status.progress?.status === 'resumable'
        ? 'Saved generation progress can resume.'
        : 'No Script generation is currently running.';
  };

  const refreshProvenance = async () => {
    const lifecycle = getModel().lifecycle || {};
    const provenance = lifecycle.provenance || {};
    provenanceFacts.replaceChildren(facts([
      ['Generation method', lifecycle.generation_method || provenance.method],
      ['Origin', provenance.origin_type || provenance.mode],
      ['Verification', provenance.provenance_status],
      ['Current version', lifecycle.accepted_version_id || 'Not approved'],
    ]));
    versionsHost.replaceChildren(UI.skeleton({ label: 'Loading Script versions' }));
    const result = await api.get('/api/script_lifecycle/versions', { signal });
    if (!result.ok) {
      versionsHost.replaceChildren(UI.notice({
        tone: 'error', title: 'Versions could not load', body: result.error,
      }));
      return;
    }
    const versions = result.data?.versions || [];
    if (!versions.length) {
      versionsHost.replaceChildren(text('p', 'metadata', 'No approved Script versions yet.'));
      return;
    }
    const list = document.createElement('ul');
    list.className = 'divider-list';
    versions.forEach((version) => {
      const row = document.createElement('li');
      row.className = 'script-version-row';
      row.append(
        text('strong', '', version.label || version.version_id || 'Script version'),
        text('span', 'metadata', version.created_at_utc || version.accepted_at_utc || 'Saved version'),
      );
      list.append(row);
    });
    versionsHost.replaceChildren(list);
  };

  generate.addEventListener('click', async () => {
    const result = await runButton(
      generate,
      'Starting…',
      () => api.post('/api/generate_script', {}, { signal }),
    );
    if (!result.ok) report('Local generation could not start', result.error);
    else {
      report(
        'Script generation started',
        'Alexandria is preparing the Script locally.',
        'information',
      );
      await refreshGeneration();
    }
  });
  review.addEventListener('click', async () => {
    const result = await runButton(
      review,
      'Starting review…',
      () => api.post('/api/review_script_contextual', { window_size: 4 }, { signal }),
    );
    if (!result.ok) report('Contextual review could not start', result.error);
    else report(
      'Contextual review started',
      'Review results will appear when the process completes.',
      'information',
    );
  });
  exportTask.addEventListener('click', async () => {
    const result = await runButton(
      exportTask,
      'Preparing…',
      () => api.post('/api/tasks/export', {
        task_type: 'script_generation', target: null,
      }, { signal }),
    );
    if (!result.ok) {
      report('Task Bundle could not be exported', result.error);
      return;
    }
    const link = document.createElement('a');
    link.className = 'ui-button';
    link.dataset.variant = 'secondary';
    link.href = result.data.download_url;
    link.textContent = 'Download Task Bundle';
    taskResult.replaceChildren(
      link,
      text(
        'span',
        'metadata',
        'Attach this Script-only ZIP to ChatGPT. Import the returned .alexandria-completed-task.zip below without unzipping it.',
      ),
    );
  });

  generationDisclosure.querySelector('.disclosure__trigger').addEventListener(
    'click', refreshGeneration,
  );
  provenanceDisclosure.querySelector('.disclosure__trigger').addEventListener(
    'click', refreshProvenance,
  );
  return Object.freeze({
    root,
    open(kind = 'generation') {
      const disclosure = kind === 'provenance' ? provenanceDisclosure : generationDisclosure;
      const trigger = disclosure.querySelector('.disclosure__trigger');
      if (trigger.getAttribute('aria-expanded') !== 'true') trigger.click();
      disclosure.scrollIntoView({ block: 'nearest' });
      trigger.focus();
    },
  });
}
