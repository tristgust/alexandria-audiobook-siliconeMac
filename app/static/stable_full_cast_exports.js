'use strict';

import { button, text } from './stable_full_cast_dom.js';

export const TASKS = Object.freeze([
  {
    value: 'roster_discovery', step: '1', label: 'Discover source-evidenced roster',
    body: 'Identities, aliases, titles, roles, relationships, groups, narrator roles, speaking status, Voice clues, and recurring non-speakers.',
    action: 'Export discovery task',
  },
  {
    value: 'roster_reconciliation', step: '2', label: 'Reconcile and enrich the roster',
    body: 'Canonical identities with preserved relationships, uncertainty, exclusions, groups, and duplicate candidates.',
    action: 'Export reconciliation task',
  },
  {
    value: 'persona_catalog_generation', step: '3', label: 'Create all Voice-profile drafts',
    body: 'Persistent Designed Voice definitions for approved speaking identities. Production Voice assignment remains separate.',
    action: 'Export Voice-profile task',
  },
]);

export function taskCard({ task, apiJson, status, resultHost }) {
  const card = document.createElement('article');
  card.className = 'stable-task-card';
  card.dataset.stableFullCastTask = task.value;
  const copy = document.createElement('div');
  copy.className = 'stable-task-card__copy';
  copy.append(text('h4', task.label), text('p', task.body, 'stable-task-muted'));
  const action = button(task.action, 'btn btn-outline-secondary');
  const result = document.createElement('div');
  result.className = 'stable-task-card__result';
  action.addEventListener('click', async () => {
    action.disabled = true;
    action.textContent = 'Preparing…';
    result.replaceChildren();
    status.textContent = `Preparing ${task.label}…`;
    try {
      const response = await apiJson('/api/tasks/export', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_type: task.value, target: null }),
      });
      const link = text('a', 'Download task ZIP', 'btn btn-outline-secondary');
      link.href = response.download_url;
      result.append(link, text('span', 'Attach this ZIP directly to ChatGPT. Do not unzip it.', 'stable-task-muted'));
      status.textContent = 'Task ZIP ready.';
      resultHost.replaceChildren();
    } catch (error) {
      status.textContent = error.message || 'The Task ZIP could not be created.';
    } finally {
      action.disabled = false;
      action.textContent = task.action;
    }
  });
  card.append(text('span', task.step, 'stable-task-card__step'), copy, action, result);
  return card;
}

export function completeCastPanel(apiJson, footerStatus, resultHost) {
  const panel = document.createElement('section');
  panel.className = 'stable-complete-cast';
  panel.dataset.stableCompleteCastBundle = '';

  const choices = document.createElement('fieldset');
  choices.className = 'stable-complete-cast__choices';
  const legend = text('legend', 'Include in bundle', 'stable-complete-cast__legend');
  choices.append(legend);

  const choice = (key, label, body, destination) => {
    const row = document.createElement('label');
    row.className = 'stable-complete-cast__choice';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = true;
    input.dataset.stableCastDossierOption = key;
    const copy = document.createElement('span');
    copy.className = 'stable-complete-cast__choice-copy';
    copy.append(
      text('strong', label),
      text('span', body, 'stable-task-muted'),
    );
    row.append(
      input,
      copy,
      text('span', destination, 'stable-complete-cast__destination'),
    );
    choices.append(row);
    return input;
  };

  const roster = choice(
    'roster_and_relationships',
    'Roster & relationships',
    'Identities, aliases, roles, groups, speaking status, and source-evidenced relationships.',
    'Roster review',
  );
  const voices = choice(
    'voice_personas_and_designs',
    'Voice personas & designs',
    'A performance persona and synthesis-ready Voice definition for every Script speaker.',
    'Voice review',
  );
  const visuals = choice(
    'visual_dossiers',
    'Visual dossiers',
    'Source-backed stable traits, scene variants, conflicts, and unknowns for every Cast identity.',
    'Visual review',
  );

  const actions = document.createElement('footer');
  actions.className = 'stable-complete-cast__actions';
  const status = text('span', '', 'stable-managed-import-status');
  const exportAction = button('Export Cast bundle', 'btn btn-primary');
  exportAction.dataset.stableExportCompleteCast = '';
  const result = document.createElement('div');
  result.className = 'stable-complete-cast__result';

  const sync = () => {
    const count = [roster, voices, visuals].filter((input) => input.checked).length;
    exportAction.disabled = count === 0;
    status.textContent = count
      ? `${count} section${count === 1 ? '' : 's'} · one ZIP · separate reviews`
      : 'Select at least one section.';
    result.replaceChildren();
  };

  [roster, voices, visuals].forEach((input) => input.addEventListener('change', sync));
  exportAction.addEventListener('click', async () => {
    exportAction.disabled = true;
    exportAction.textContent = 'Preparing bundle…';
    footerStatus.textContent = 'Preparing Cast bundle…';
    result.replaceChildren();
    try {
      const response = await apiJson('/api/tasks/export', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_type: 'complete_cast_dossier',
          target: null,
          options: {
            roster_and_relationships: roster.checked,
            voice_personas_and_designs: voices.checked,
            visual_dossiers: visuals.checked,
          },
        }),
      });
      const link = text('a', 'Download Cast bundle ZIP', 'btn btn-outline-secondary');
      link.href = response.download_url;
      result.append(
        link,
        text(
          'span',
          'Attach this ZIP directly to ChatGPT, then import the completed ZIP it returns.',
          'stable-task-muted',
        ),
      );
      footerStatus.textContent = 'Cast bundle ready.';
      resultHost.replaceChildren();
    } catch (error) {
      footerStatus.textContent = error.message || 'The Cast bundle could not be created.';
    } finally {
      exportAction.disabled = false;
      exportAction.textContent = 'Export Cast bundle';
    }
  });

  actions.append(status, exportAction);
  panel.append(choices, actions, result);
  sync();
  return panel;
}
