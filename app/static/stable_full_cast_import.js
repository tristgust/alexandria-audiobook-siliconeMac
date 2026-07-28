'use strict';

import { isCompletedCastPackage } from './cast_dossier_state.js';
import { renderStableCompletedCast } from './stable_cast_dossier_activation.js';
import { renderDirectDossierActivation } from './stable_cast_dossier_direct_activation.js';
import { button, fileLabel, text } from './stable_full_cast_dom.js';

export function buildImportSurface({ apiJson, body, footerStatus, footerActions }) {
  let completedFile = null;
  const section = document.createElement('section');
  section.className = 'stable-task-import';
  const intro = document.createElement('div');
  intro.className = 'stable-task-intro';
  intro.append(
    text('span', 'Return from ChatGPT', 'stable-task-eyebrow'),
    text('h3', 'Import a completed ZIP'),
    text('p', 'Drop in the ZIP ChatGPT returns. Alexandria validates it and opens the correct review.', 'stable-task-muted'),
  );
  const steps = document.createElement('ol');
  steps.className = 'stable-task-steps';
  [
    ['1', 'Export', 'Alexandria task ZIP', 'complete'],
    ['2', 'ChatGPT', 'Completed ZIP', 'complete'],
    ['3', 'Import', 'Open native review', 'current'],
  ].forEach(([number, label, copy, state]) => {
    const item = document.createElement('li');
    item.dataset.state = state;
    item.append(text('span', number, 'stable-task-step__number'), text('strong', label), text('span', copy, 'stable-task-muted'));
    steps.append(item);
  });
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.zip,.json,application/zip,application/json';
  input.hidden = true;
  input.dataset.stableCompletedTaskFile = '';
  const drop = button('', 'stable-task-dropzone');
  drop.dataset.stableTaskDropzone = '';
  const icon = text('span', '⇧', 'stable-task-dropzone__icon');
  const dropCopy = document.createElement('span');
  dropCopy.className = 'stable-task-dropzone__copy';
  dropCopy.append(text('strong', 'Drop the completed ZIP here'), text('span', 'Choose the .alexandria-completed-task.zip returned by ChatGPT. Do not unzip it.', 'stable-task-muted'));
  drop.append(icon, dropCopy, text('span', 'Choose file', 'stable-task-dropzone__action'));
  const selected = document.createElement('div');
  selected.className = 'stable-task-selected';
  selected.hidden = true;
  const selectedCopy = document.createElement('div');
  selectedCopy.className = 'stable-task-selected__copy';
  selectedCopy.append(text('strong', 'Completed ZIP'), text('span', '', 'stable-task-muted'));
  const remove = button('Remove', 'btn btn-link');
  selected.append(text('span', '▤', 'stable-task-dropzone__icon'), selectedCopy, remove);
  const fallback = document.createElement('details');
  fallback.className = 'stable-task-fallback';
  const original = document.createElement('input');
  original.type = 'file'; original.accept = '.zip,application/zip';
  const fallbackBody = document.createElement('div');
  fallbackBody.className = 'stable-task-fallback__body';
  const originalLabel = document.createElement('label');
  originalLabel.append(text('strong', 'Original task ZIP'), original, text('span', 'Only needed when ChatGPT returned a legacy fallback JSON.', 'stable-task-muted'));
  fallbackBody.append(text('p', 'The normal completed ZIP does not need its original task file.', 'stable-task-muted'), originalLabel);
  fallback.append(text('summary', 'Using a JSON result?'), fallbackBody);
  const resultHost = document.createElement('div');
  resultHost.className = 'stable-task-result';
  const validate = button('Validate ZIP', 'btn btn-primary');
  validate.disabled = true;
  footerActions.replaceChildren(validate);
  footerStatus.textContent = 'No completed ZIP selected.';

  const sync = (file) => {
    completedFile = file || null;
    drop.hidden = Boolean(file);
    selected.hidden = !file;
    validate.disabled = !file;
    selectedCopy.querySelector('.stable-task-muted').textContent = fileLabel(file);
    footerStatus.textContent = file
      ? 'Ready to validate. Nothing has changed.'
      : 'No completed ZIP selected.';
    resultHost.replaceChildren();
  };
  drop.addEventListener('click', () => input.click());
  input.addEventListener('change', () => sync(input.files?.[0]));
  remove.addEventListener('click', () => { input.value = ''; sync(null); drop.focus(); });
  ['dragenter', 'dragover'].forEach((name) => drop.addEventListener(name, (event) => {
    event.preventDefault(); drop.dataset.dragging = 'true';
  }));
  ['dragleave', 'drop'].forEach((name) => drop.addEventListener(name, (event) => {
    event.preventDefault(); delete drop.dataset.dragging;
  }));
  drop.addEventListener('drop', (event) => {
    const file = event.dataTransfer?.files?.[0]; if (file) sync(file);
  });
  validate.addEventListener('click', async () => {
    if (!completedFile) return;
    validate.disabled = true; validate.textContent = 'Validating…';
    footerStatus.textContent = 'Checking task identity, checksums, source, and artifact fingerprints…';
    const form = new FormData();
    form.append('file', completedFile);
    if (original.files?.[0]) form.append('original_task', original.files[0]);
    try {
      const response = await apiJson('/api/tasks/import', { method: 'POST', body: form });
      footerStatus.textContent = 'Completed task validated. Nothing has been approved.';
      if (response.task_type === 'roster_discovery' && response.candidate_id) {
        const module = await import('/static/stable_roster_import_review.js');
        await module.renderStableRosterImportReview({
          apiJson, candidate: response, body, footerStatus, footerActions,
        });
        return;
      }
      if (response.task_type === 'complete_cast_dossier' && response.cast_dossier_package) {
        if (isCompletedCastPackage(response.cast_dossier_package)) {
          resultHost.replaceChildren(renderStableCompletedCast({ reconciliation: response, text }));
          footerStatus.textContent = 'The completed Cast dossier is available as an audit record.';
          footerActions.replaceChildren();
          return;
        }
        renderDirectDossierActivation({
          apiJson, response, resultHost, footerStatus, footerActions,
        });
        return;
      }
      const routing = response.routing || {};
      resultHost.replaceChildren(
        text('strong', 'Task imported for native review'),
        text('p', routing.message || 'Open Cast to review the result.', 'stable-task-muted'),
      );
    } catch (error) {
      footerStatus.textContent = error.message || 'The completed task could not be imported.';
    } finally {
      validate.disabled = false; validate.textContent = 'Validate ZIP';
    }
  });
  section.append(intro, steps, input, drop, selected, fallback, resultHost);
  return { section, resultHost };
}
