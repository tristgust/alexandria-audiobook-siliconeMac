'use strict';

import {
  resultMessage,
  supportOwner,
  supportReturn,
  textNode,
} from '/static/pages/more.js';

const UI = globalThis.AlexandriaUI;

function savedVoices(items) {
  const list = document.createElement('div');
  list.className = 'support-list';
  items.forEach((voice) => {
    const row = document.createElement('div');
    row.className = 'support-list-row';
    const copy = document.createElement('div');
    copy.append(
      textNode('strong', '', voice.name || 'Designed Voice'),
      textNode('p', 'support-status-copy', voice.description || 'Reusable Voice material'),
    );
    row.append(copy, UI.status({ label: 'Saved', tone: 'success' }));
    list.append(row);
  });
  return list.childElementCount ? list : UI.emptyState({
    title: 'No designed Voices yet',
    body: 'Describe and preview a Voice to create reusable material.',
  });
}

function designerForm(api, signal, route, shell) {
  const form = document.createElement('form');
  form.className = 'settings-form specialist-section';
  form.append(textNode('h2', '', 'Design a reusable Voice'));
  const name = UI.field({
    id: 'voice-designer-name',
    label: 'Voice name',
    description: 'A saved Voice is added to the Library but never assigned automatically.',
  });
  const description = UI.field({
    id: 'voice-designer-description',
    label: 'Voice description',
    kind: 'textarea',
    description: 'Describe age, tone, pace, texture, and accent only when supported by the text.',
  });
  const sample = UI.field({
    id: 'voice-designer-sample',
    label: 'Preview text',
    kind: 'textarea',
    value: 'Welcome to Alexandria. This preview will not change the current Cast.',
  });
  const language = UI.field({
    id: 'voice-designer-language',
    label: 'Language',
    value: 'English',
  });
  const preview = UI.button({
    label: 'Generate preview',
    variant: 'primary',
    type: 'submit',
  });
  const save = UI.button({
    label: 'Save Voice',
    variant: 'secondary',
    disabled: true,
  });
  const actions = document.createElement('div');
  actions.className = 'guarded-actions';
  actions.append(preview, save);
  const output = document.createElement('div');
  output.setAttribute('role', 'status');
  let previewFile = '';
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    preview.disabled = true;
    output.replaceChildren(UI.status({ label: 'Generating preview', tone: 'neutral', live: true }));
    const result = await api.post('/api/voice_design/preview', {
      description: description.querySelector('textarea').value.trim(),
      sample_text: sample.querySelector('textarea').value.trim(),
      language: language.querySelector('input').value.trim() || 'English',
    }, { signal });
    if (signal.aborted) return;
    preview.disabled = false;
    if (!result.ok) {
      output.replaceChildren(UI.notice({
        tone: 'error',
        title: 'Preview could not be generated',
        body: resultMessage(result, 'Try a shorter description and preview text.'),
        live: true,
      }));
      return;
    }
    const audioUrl = String(result.data.audio_url || '');
    previewFile = audioUrl.split('/').at(-1) || '';
    const audio = document.createElement('audio');
    audio.controls = true;
    audio.preload = 'metadata';
    audio.src = audioUrl;
    output.replaceChildren(audio, UI.notice({
      tone: 'success',
      title: 'Preview ready',
      body: 'Listen before saving. The production Voice remains unchanged.',
      live: true,
    }));
    save.disabled = !previewFile;
  });
  save.addEventListener('click', async () => {
    const result = await api.post('/api/voice_design/save', {
      name: name.querySelector('input').value.trim(),
      description: description.querySelector('textarea').value.trim(),
      sample_text: sample.querySelector('textarea').value.trim(),
      preview_file: previewFile,
    }, { signal });
    if (signal.aborted) return;
    output.replaceChildren(UI.notice({
      tone: result.ok ? 'success' : 'error',
      title: result.ok ? 'Voice saved to Library' : 'Voice was not saved',
      body: result.ok
        ? 'Choose it as a production Voice from Cast when you are ready.'
        : resultMessage(result, 'The preview remains available.'),
      live: true,
    }));
    if (result.ok) window.setTimeout(
      () => shell.navigate(route.hash, { historyMode: 'replace' }),
      180,
    );
  });
  form.append(name, description, sample, language, actions, output);
  return form;
}

export async function mount({ root, route, shell, api, signal }) {
  const dataRouteOwner = route.path;
  const { owner, stateRegion } = supportOwner(root, route, {
    page: 'voice-designer',
    title: 'Voice designer',
    subtitle: 'Create and preview reusable Voice material without changing Cast.',
    className: 'specialist-workspace',
  });
  owner.dataset.routeOwner = dataRouteOwner;
  stateRegion.setAttribute('data-state-region', '');
  const result = await api.get('/api/voice_design/list', { signal });
  if (signal.aborted) return () => {};
  const toolbar = document.createElement('div');
  toolbar.className = 'support-toolbar';
  toolbar.append(supportReturn(route, shell));
  if (!result.ok) {
    owner.dataset.viewState = 'error';
    stateRegion.replaceChildren(toolbar, UI.notice({
      tone: 'error',
      title: 'Voice designer could not be loaded',
      body: resultMessage(result, 'No Voice material was changed.'),
      live: true,
    }));
    return () => {};
  }
  const grid = document.createElement('div');
  grid.className = 'specialist-section-grid';
  grid.append(savedVoices(Array.isArray(result.data) ? result.data : []));
  grid.append(designerForm(api, signal, route, shell));
  stateRegion.replaceChildren(
    toolbar,
    UI.notice({
      tone: 'information',
      title: 'Assignment stays in Cast',
      body: 'Saving adds reusable Voice material. It does not assign that Voice to a character.',
    }),
    grid,
  );
  owner.dataset.viewState = 'ready';
  return () => {};
}
