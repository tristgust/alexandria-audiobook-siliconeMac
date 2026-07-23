'use strict';

import {
  resultMessage,
  supportOwner,
  supportReturn,
  textNode,
} from '/static/pages/more.js';

const UI = globalThis.AlexandriaUI;

function completedOutputs(files) {
  const list = document.createElement('div');
  list.className = 'support-list';
  files.forEach((file) => {
    const row = document.createElement('div');
    row.className = 'support-list-row';
    const copy = document.createElement('div');
    copy.append(
      textNode('strong', '', file.filename || 'Prepared dataset'),
      textNode('p', 'support-status-copy', `${Number(file.size_mb || 0)} MB · ready to download`),
    );
    const link = document.createElement('a');
    link.className = 'ui-button ui-button--quiet';
    link.href = `/api/preparer/download/${encodeURIComponent(file.filename)}`;
    link.textContent = 'Download';
    row.append(copy, link);
    list.append(row);
  });
  return list.childElementCount ? list : UI.emptyState({
    title: 'No prepared datasets',
    body: 'Upload an owned recording to create reviewable Voice material.',
  });
}

function preparerForm(api, signal) {
  const form = document.createElement('form');
  form.className = 'settings-form specialist-section';
  form.append(textNode('h2', '', 'Prepare an owned recording'));
  const source = UI.field({
    id: 'audio-preparer-source',
    label: 'Recording',
    type: 'file',
    attributes: { accept: 'audio/*' },
  });
  const output = UI.field({
    id: 'audio-preparer-output',
    label: 'Dataset name',
    value: 'alexandria_dataset.zip',
  });
  const language = UI.field({
    id: 'audio-preparer-language',
    label: 'Language code',
    value: 'en',
  });
  const submit = UI.button({
    label: 'Start preparation',
    variant: 'primary',
    type: 'submit',
  });
  const feedback = document.createElement('div');
  feedback.setAttribute('role', 'status');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const file = source.querySelector('input').files?.[0];
    if (!file) {
      feedback.replaceChildren(UI.notice({
        tone: 'warning',
        title: 'Choose a recording',
        body: 'Only use audio that you own or are authorized to process.',
        live: true,
      }));
      return;
    }
    const body = new FormData();
    body.set('audio_file', file);
    body.set('config_json', JSON.stringify({
      audio_filename: file.name,
      output_filename: output.querySelector('input').value.trim() || 'alexandria_dataset.zip',
      lang: language.querySelector('input').value.trim() || 'en',
      min_confidence: 0.85,
      min_snr: 25,
    }));
    submit.disabled = true;
    const result = await api.post('/api/preparer/start', body, { signal });
    if (signal.aborted) return;
    submit.disabled = false;
    feedback.replaceChildren(UI.notice({
      tone: result.ok ? 'success' : 'error',
      title: result.ok ? 'Preparation started' : 'Preparation could not start',
      body: result.ok
        ? 'The recording is being transcribed and segmented. Production Voice assignment is unchanged.'
        : resultMessage(result, 'The selected file was not queued.'),
      live: true,
    }));
  });
  form.append(source, output, language, submit, feedback);
  return form;
}

export async function mount({ root, route, shell, api, signal }) {
  const dataRouteOwner = route.path;
  const { owner, stateRegion } = supportOwner(root, route, {
    page: 'audio-preparer',
    title: 'Audio preparer',
    subtitle: 'Transcribe and segment authorized recordings into reviewable Voice material.',
    className: 'specialist-workspace',
  });
  owner.dataset.routeOwner = dataRouteOwner;
  stateRegion.setAttribute('data-state-region', '');
  const result = await api.get('/api/preparer/list', { signal });
  if (signal.aborted) return () => {};
  const toolbar = document.createElement('div');
  toolbar.className = 'support-toolbar';
  toolbar.append(supportReturn(route, shell));
  if (!result.ok) {
    owner.dataset.viewState = 'error';
    stateRegion.replaceChildren(toolbar, UI.notice({
      tone: 'error',
      title: 'Audio preparer could not be loaded',
      body: resultMessage(result, 'No recording was uploaded.'),
      live: true,
    }));
    return () => {};
  }
  const grid = document.createElement('div');
  grid.className = 'specialist-section-grid';
  grid.append(
    completedOutputs(result.data?.files || []),
    preparerForm(api, signal),
  );
  stateRegion.replaceChildren(
    toolbar,
    UI.notice({
      tone: 'information',
      title: 'Use authorized recordings only',
      body: 'Preparation creates source material for later review. It does not train or assign a production Voice.',
    }),
    grid,
  );
  owner.dataset.viewState = (result.data?.files || []).length ? 'ready' : 'empty';
  return () => {};
}
