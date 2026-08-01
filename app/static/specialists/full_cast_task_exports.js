'use strict';

import { resultMessage, textNode } from '/static/pages/more.js';
import { downloadTaskBundle } from '/static/pages/task_bundle_download.js';

const UI = globalThis.AlexandriaUI;

const INDIVIDUAL_TASKS = Object.freeze([
  {
    taskType: 'roster_discovery', step: '1',
    title: 'Roster and relationship evidence',
    body: 'Source-evidenced identities, aliases, titles, roles, relationships, groups, speaking status, and recurring non-speakers.',
    action: 'Download roster task bundle',
  },
  {
    taskType: 'roster_reconciliation', step: '2',
    title: 'Roster reconciliation',
    body: 'Reconcile previously imported observations into canonical identities, uncertainty, exclusions, groups, and duplicate candidates.',
    action: 'Download reconciliation task bundle',
  },
  {
    taskType: 'persona_catalog_generation', step: '3',
    title: 'Voice profiles only',
    body: 'Create persistent Voice-profile drafts for the currently approved Script speakers without redoing roster or visual work.',
    action: 'Download Voice task bundle',
  },
]);

async function run(button, pendingLabel, operation) {
  const prior = button.textContent;
  button.disabled = true;
  button.textContent = pendingLabel;
  try { return await operation(); }
  finally { button.disabled = false; button.textContent = prior; }
}

function approvedRoster(status) {
  const approved = status?.approved || {};
  return approved.status === 'approved' && approved.fingerprint ? approved : null;
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
    await downloadTaskBundle({
      api,
      signal,
      button: action,
      taskType: task.taskType,
      target: null,
      pendingLabel: 'Preparing download…',
      onError: (message) => result.replaceChildren(UI.notice({
        tone: 'error', title: 'Task bundle was not downloaded',
        body: message || 'No project data changed.', live: true,
      })),
      onDownloaded: () => {
        result.replaceChildren(textNode(
          'span', 'metadata',
          'Task bundle downloaded. Attach it directly to ChatGPT.',
        ));
        report?.('Task bundle downloaded', task.title, 'success');
      },
    });
  });
  card.append(marker, copy, action, result);
  return card;
}

function completeBundlePanel({
  api, signal, registry, rosterResult, enrichmentResult, report,
}) {
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
  const localButton = UI.button({
    label: 'Run selected work locally',
    variant: 'primary',
    attributes: { 'data-run-complete-cast-local': '' },
  });
  const exportButton = UI.button({
    label: 'Download Cast task bundle',
    variant: 'secondary',
    attributes: { 'data-export-complete-cast': '' },
  });
  const buttons = document.createElement('div');
  buttons.className = 'complete-cast-bundle__buttons';
  buttons.append(localButton, exportButton);
  const result = document.createElement('div');
  result.className = 'complete-cast-bundle__result';
  let rosterStatus = rosterResult.ok ? rosterResult.data || {} : null;
  let enrichmentStatus = enrichmentResult.ok
    ? enrichmentResult.data || {} : null;
  let pollTimer = null;
  signal.addEventListener('abort', () => {
    if (pollTimer !== null) window.clearTimeout(pollTimer);
  }, { once: true });

  const sync = () => {
    const count = [roster, voices, visuals].filter((input) => input.checked).length;
    exportButton.disabled = !registry.has('complete_cast_dossier') || count === 0;
    const bundleCopy = count
      ? `${count} section${count === 1 ? '' : 's'} selected for the task bundle.`
      : 'Select at least one section for the task bundle.';
    if (!rosterResult.ok || rosterStatus === null) {
      localButton.disabled = true;
      status.textContent = `Local Cast state could not be loaded. ${bundleCopy}`;
      return;
    }
    const approved = approvedRoster(rosterStatus);
    const discoveryRunning = rosterStatus.process?.running === true;
    const workingDraft = rosterStatus.draft?.status === 'draft';
    const enrichmentRunning = enrichmentStatus?.running === true;
    if (discoveryRunning) {
      localButton.disabled = true;
      status.textContent = `Roster discovery is running. ${bundleCopy}`;
    } else if (!approved && workingDraft) {
      localButton.disabled = true;
      status.textContent = `Review or approve the current roster draft first. ${bundleCopy}`;
    } else if (!approved && rosterStatus.source?.available === false) {
      localButton.disabled = true;
      status.textContent = `Select a readable source before running local Cast work. ${bundleCopy}`;
    } else if (!approved && !roster.checked) {
      localButton.disabled = true;
      status.textContent = `Select Roster & relationships to start local discovery. ${bundleCopy}`;
    } else if (!approved) {
      localButton.disabled = false;
      status.textContent = `No approved roster exists. Local work will start with roster discovery. ${bundleCopy}`;
    } else if (enrichmentRunning) {
      localButton.disabled = true;
      status.textContent = `Selected local Cast enrichment is running. ${bundleCopy}`;
    } else if (!voices.checked && !visuals.checked) {
      localButton.disabled = true;
      status.textContent = `The roster is approved. Select Voice or visual work to run locally. ${bundleCopy}`;
    } else {
      localButton.disabled = false;
      const completed = enrichmentStatus?.status === 'complete'
        ? ' The previous selected enrichment completed.' : '';
      status.textContent = `The approved roster will be preserved; selected Voice and visual work runs sequentially.${completed} ${bundleCopy}`;
    }
  };

  const pollEnrichment = async () => {
    if (signal.aborted) return;
    const response = await api.get('/api/character_roster/enrichment', { signal });
    if (!response.ok || signal.aborted) {
      result.replaceChildren(UI.notice({
        tone: 'warning', title: 'Local Cast status could not be refreshed',
        body: response.error || 'The local run may still be active.', live: true,
      }));
      return;
    }
    enrichmentStatus = response.data || {};
    sync();
    if (enrichmentStatus.running) {
      pollTimer = window.setTimeout(pollEnrichment, 1000);
      return;
    }
    const tone = enrichmentStatus.status === 'complete' ? 'success' : 'warning';
    result.replaceChildren(UI.notice({
      tone,
      title: enrichmentStatus.status === 'complete'
        ? 'Local Cast enrichment complete'
        : 'Local Cast enrichment needs attention',
      body: enrichmentStatus.error
        || 'Review the Voice and visual results in Cast.',
      live: true,
    }));
  };

  [roster, voices, visuals].forEach((input) => input.addEventListener('change', () => {
    result.replaceChildren();
    sync();
  }));
  localButton.addEventListener('click', async () => {
    result.replaceChildren();
    const approved = approvedRoster(rosterStatus);
    if (!approved) {
      const response = await run(localButton, 'Starting roster discovery…', () => api.post(
        '/api/character_roster/discover',
        { replace_draft: false },
        { signal },
      ));
      if (!response.ok) {
        result.replaceChildren(UI.notice({
          tone: 'error', title: 'Roster discovery did not start',
          body: resultMessage(response, 'No local Cast work started.'), live: true,
        }));
      } else {
        rosterStatus = {
          ...rosterStatus,
          process: { ...(rosterStatus.process || {}), running: true },
        };
        result.replaceChildren(textNode(
          'span', 'metadata',
          'Roster discovery started. Review and approve the roster before running Voice or visual enrichment.',
        ));
        report?.('Roster discovery started', 'Local Cast work is using the selected source.', 'success');
      }
      sync();
      return;
    }
    const response = await run(localButton, 'Starting local work…', () => api.post(
      '/api/character_roster/enrichment/run-selected',
      {
        expected_roster_fingerprint: approved.fingerprint,
        create_designed_voice_profiles: voices.checked,
        discover_visual_details: visuals.checked,
      },
      { signal },
    ));
    if (!response.ok) {
      result.replaceChildren(UI.notice({
        tone: 'error', title: 'Local Cast enrichment did not start',
        body: resultMessage(response, 'The approved roster was not changed.'), live: true,
      }));
      sync();
      return;
    }
    enrichmentStatus = { status: 'running', running: true, stage: 'queued' };
    result.replaceChildren(textNode(
      'span', 'metadata',
      'Selected Voice-profile and visual work started in the local sequential runner.',
    ));
    report?.('Local Cast enrichment started', 'Selected stages will run sequentially.', 'success');
    sync();
    pollTimer = window.setTimeout(pollEnrichment, 250);
  });
  exportButton.addEventListener('click', async () => {
    result.replaceChildren();
    await downloadTaskBundle({
      api,
      signal,
      button: exportButton,
      taskType: 'complete_cast_dossier',
      target: null,
      options: {
        roster_and_relationships: roster.checked,
        voice_personas_and_designs: voices.checked,
        visual_dossiers: visuals.checked,
      },
      pendingLabel: 'Preparing Cast task…',
      onError: (message) => result.replaceChildren(UI.notice({
        tone: 'error', title: 'Cast task bundle was not downloaded',
        body: message || 'No project data changed.', live: true,
      })),
      onDownloaded: () => {
        result.replaceChildren(textNode(
          'span', 'metadata',
          'Cast task bundle downloaded. Attach it to ChatGPT, then import the completed ZIP.',
        ));
        report?.('Cast task bundle downloaded', 'Selected work is contained in one task ZIP.', 'success');
      },
    });
  });

  actions.append(status, buttons);
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
      'Choose the work to run locally or download one task bundle for ChatGPT, then review each section in Alexandria. Individual task downloads remain below.'),
  );
  const [registryResult, rosterResult, enrichmentResult] = await Promise.all([
    api.get('/api/tasks/registry', { signal }),
    api.get('/api/character_roster/status', { signal }),
    api.get('/api/character_roster/enrichment', { signal }),
  ]);
  const registry = new Map(
    (registryResult.ok ? registryResult.data?.tasks || [] : [])
      .map((item) => [item.task_type, item]),
  );
  section.append(intro, completeBundlePanel({
    api, signal, registry, rosterResult, enrichmentResult, report,
  }));
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
