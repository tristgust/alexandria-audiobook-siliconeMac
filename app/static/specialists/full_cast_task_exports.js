'use strict';

import { resultMessage, textNode } from '/static/pages/more.js';

const UI = globalThis.AlexandriaUI;

const INDIVIDUAL_TASKS = Object.freeze([
  {
    taskType: 'roster_discovery', step: '1',
    title: 'Roster and relationship evidence',
    body: 'Source-evidenced identities, aliases, titles, roles, relationships, groups, speaking status, and recurring non-speakers.',
    action: 'Export roster-only task',
  },
  {
    taskType: 'roster_reconciliation', step: '2',
    title: 'Roster reconciliation',
    body: 'Reconcile previously imported observations into canonical identities, uncertainty, exclusions, groups, and duplicate candidates.',
    action: 'Export reconciliation task',
  },
  {
    taskType: 'persona_catalog_generation', step: '3',
    title: 'Voice profiles only',
    body: 'Create persistent Voice-profile drafts for the currently approved Script speakers without redoing roster or visual work.',
    action: 'Export Voice-only task',
  },
]);

async function run(button, pendingLabel, operation) {
  const prior = button.textContent;
  button.disabled = true;
  button.textContent = pendingLabel;
  try { return await operation(); }
  finally { button.disabled = false; button.textContent = prior; }
}

function downloadResult(response, label, note) {
  const host = document.createElement('div');
  host.className = 'full-cast-task-card__result';
  const link = document.createElement('a');
  link.className = 'ui-button';
  link.dataset.variant = 'secondary';
  link.href = response.data.download_url;
  link.textContent = label;
  host.append(link, textNode('span', 'metadata', note));
  return host;
}

function taskCard({ task, api, signal, registry, report }) {
  const card = document.createElement('article');
  card.className = 'full-cast-task-card';
  card.dataset.fullCastTask = task.taskType;
  const marker = textNode('span', 'full-cast-task-card__step', task.step);
  const copy = document.createElement('div');
  copy.className = 'full-cast-task-card__copy';
  copy.append(
    textNode('h3', '', task.title),
    textNode('p', 'support-status-copy', task.body),
  );
  const result = document.createElement('div');
  result.className = 'full-cast-task-card__result';
  const action = UI.button({ label: task.action, variant: 'secondary' });
  if (!registry.has(task.taskType)) action.disabled = true;
  action.addEventListener('click', async () => {
    result.replaceChildren();
    const response = await run(action, 'Preparing…', () => api.post(
      '/api/tasks/export',
      { task_type: task.taskType, target: null },
      { signal },
    ));
    if (!response.ok) {
      result.replaceChildren(UI.notice({
        tone: 'error', title: 'Task ZIP was not created',
        body: resultMessage(response, 'No project data changed.'), live: true,
      }));
      return;
    }
    result.replaceChildren(...downloadResult(
      response,
      'Download task ZIP',
      'Attach this individual task ZIP directly to ChatGPT.',
    ).childNodes);
    report?.('Task ZIP ready', task.title, 'success');
  });
  card.append(marker, copy, action, result);
  return card;
}

function completeBundlePanel({ api, signal, registry, report }) {
  const panel = document.createElement('section');
  panel.className = 'complete-cast-bundle';
  panel.dataset.completeCastBundle = '';

  const choices = document.createElement('fieldset');
  choices.className = 'complete-cast-bundle__choices';
  const legend = textNode('legend', 'metadata complete-cast-bundle__legend', 'Include in bundle');
  choices.append(legend);

  const option = (key, label, body, destination) => {
    const row = document.createElement('label');
    row.className = 'complete-cast-bundle__choice';
    row.dataset.castDossierOption = key;
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = true;
    const copy = document.createElement('span');
    copy.className = 'complete-cast-bundle__choice-copy';
    copy.append(
      textNode('strong', '', label),
      textNode('span', 'metadata', body),
    );
    row.append(
      input,
      copy,
      textNode('span', 'metadata complete-cast-bundle__destination', destination),
    );
    choices.append(row);
    return input;
  };

  const roster = option(
    'roster_and_relationships',
    'Roster & relationships',
    'Identities, aliases, roles, groups, speaking status, and source-evidenced relationships.',
    'Roster review',
  );
  const voices = option(
    'voice_personas_and_designs',
    'Voice personas & designs',
    'A performance persona and synthesis-ready Voice definition for every Script speaker.',
    'Voice review',
  );
  const visuals = option(
    'visual_dossiers',
    'Visual dossiers',
    'Source-backed stable traits, scene variants, conflicts, and unknowns for every Cast identity.',
    'Visual review',
  );

  const actions = document.createElement('footer');
  actions.className = 'complete-cast-bundle__actions';
  const status = document.createElement('div');
  status.className = 'transaction-status complete-cast-bundle__status';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  const exportButton = UI.button({
    label: 'Export Cast bundle',
    variant: 'primary',
    attributes: { 'data-export-complete-cast': '' },
  });
  const result = document.createElement('div');
  result.className = 'complete-cast-bundle__result';

  const sync = () => {
    const count = [roster, voices, visuals].filter((input) => input.checked).length;
    exportButton.disabled = !registry.has('complete_cast_dossier') || count === 0;
    status.textContent = count
      ? `${count} section${count === 1 ? '' : 's'} · one ZIP · separate reviews`
      : 'Select at least one section.';
    result.replaceChildren();
  };

  [roster, voices, visuals].forEach((input) => input.addEventListener('change', sync));
  exportButton.addEventListener('click', async () => {
    result.replaceChildren();
    const response = await run(exportButton, 'Preparing bundle…', () => api.post(
      '/api/tasks/export',
      {
        task_type: 'complete_cast_dossier',
        target: null,
        options: {
          roster_and_relationships: roster.checked,
          voice_personas_and_designs: voices.checked,
          visual_dossiers: visuals.checked,
        },
      },
      { signal },
    ));
    if (!response.ok) {
      result.replaceChildren(UI.notice({
        tone: 'error', title: 'Cast bundle was not created',
        body: resultMessage(response, 'No project data changed.'), live: true,
      }));
      return;
    }
    result.replaceChildren(...downloadResult(
      response,
      'Download Cast bundle ZIP',
      'Attach this ZIP directly to ChatGPT, then import the completed ZIP it returns.',
    ).childNodes);
    status.textContent = 'Cast bundle ready.';
    report?.('Cast bundle ready', 'Selected work is contained in one task ZIP.', 'success');
  });

  actions.append(status, exportButton);
  panel.append(choices, actions, result);
  sync();
  return panel;
}

export async function createFullCastTaskExports({ api, signal, report }) {
  const section = document.createElement('section');
  section.className = 'specialist-section full-cast-task-workspace';
  section.dataset.fullCastTasks = '';
  const intro = document.createElement('header');
  intro.className = 'full-cast-task-workspace__header';
  intro.append(
    textNode('span', 'metadata task-import-surface__eyebrow', 'Whole-book workflow'),
    textNode('h2', '', 'Complete the Cast'),
    textNode('p', 'support-status-copy',
      'Choose the work to include, send one ZIP to ChatGPT, and review each section in Alexandria. Individual task exports remain below.'),
  );
  const registryResult = await api.get('/api/tasks/registry', { signal });
  const registry = new Map(
    (registryResult.ok ? registryResult.data?.tasks || [] : [])
      .map((item) => [item.task_type, item]),
  );
  section.append(intro, completeBundlePanel({ api, signal, registry, report }));
  const advanced = document.createElement('details');
  advanced.className = 'full-cast-task-advanced';
  advanced.append(textNode('summary', '', 'Individual task exports'));
  const taskGrid = document.createElement('div');
  taskGrid.className = 'full-cast-task-grid';
  INDIVIDUAL_TASKS.forEach((task) => taskGrid.append(taskCard({
    task, api, signal, registry, report,
  })));
  advanced.append(taskGrid);
  section.append(advanced);
  if (!registryResult.ok) section.append(UI.notice({
    tone: 'error', title: 'Task registry could not load',
    body: resultMessage(registryResult, 'Exports are unavailable.'), live: true,
  }));
  return section;
}
