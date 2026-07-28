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
      textNode('p', 'support-status-copy', voice.description || 'Designed Voice material'),
    );
    row.append(copy, UI.status({ label: 'Project Voice', tone: 'success' }));
    list.append(row);
  });
  return list.childElementCount ? list : UI.emptyState({
    title: 'No designed Voices yet',
    body: 'Describe and preview a Voice to save it in this project.',
  });
}

function designerForm(api, signal, route, shell) {
  const form = document.createElement('form');
  form.className = 'settings-form specialist-section voice-designer-form';
  form.append(textNode('h2', '', 'Design a Voice'));
  const name = UI.field({
    id: 'voice-designer-name',
    label: 'Voice name',
    attributes: { required: true },
  });
  const description = UI.field({
    id: 'voice-designer-description',
    label: 'Voice description',
    kind: 'textarea',
    description: 'Describe age, tone, pace, texture, and supported accent.',
    attributes: { required: true },
  });
  const sample = UI.field({
    id: 'voice-designer-sample',
    label: 'Preview text',
    kind: 'textarea',
    value: 'Welcome to Alexandria. This preview will not change the current Cast.',
    attributes: { required: true },
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
  const reusable = UI.checkbox({
    label: 'Make this Voice available to every project',
    checked: false,
  });
  reusable.classList.add('voice-designer-form__scope');
  reusable.querySelector('input').dataset.voiceDesignerReusable = '';
  reusable.append(textNode(
    'span', 'metadata',
    'Off by default. Project Voices stay with the current audiobook; reusable Voices also appear in the Voices menu for other projects.',
  ));
  save.hidden = true;
  const actions = document.createElement('div');
  actions.className = 'guarded-actions';
  actions.append(preview, save);
  const output = document.createElement('div');
  output.id = 'voice-designer-output';
  output.setAttribute('role', 'status');
  let previewFile = '';
  const invalidatePreview = () => {
    if (!previewFile) return;
    previewFile = '';
    save.disabled = true;
    save.hidden = true;
    output.replaceChildren(UI.notice({
      tone: 'information',
      title: 'Preview needs to be regenerated',
      body: 'The Voice description, preview text, or language changed after the last preview.',
      live: true,
    }));
  };
  [
    description.querySelector('textarea'),
    sample.querySelector('textarea'),
    language.querySelector('input'),
  ].forEach((input) => input.addEventListener('input', invalidatePreview));
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    previewFile = '';
    save.disabled = true;
    save.hidden = true;
    preview.disabled = true;
    output.replaceChildren(UI.status({ label: 'Generating preview', tone: 'neutral', live: true }));
    const result = await api.post('/api/voice_design/preview', {
      description: description.querySelector('textarea').value.trim(),
      sample_text: sample.querySelector('textarea').value.trim(),
      language: language.querySelector('input').value.trim() || 'English',
    }, { signal });
    if (signal.aborted) return;
    preview.disabled = false;
    if (!result.ok || !result.data?.audio_url) {
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
    shell.player.set({
      state: 'playing',
      src: audioUrl,
      position: 0,
      title: `${name.querySelector('input').value.trim() || 'Designed Voice'} audition`,
      subtitle: 'Current project · preview not saved yet',
    });
    output.dataset.previewReady = 'true';
    output.replaceChildren(UI.notice({
      tone: 'success',
      title: 'Preview ready',
      body: 'Playing in Alexandria’s persistent player. Listen before saving; the production Voice remains unchanged.',
      live: true,
    }));
    save.disabled = !previewFile;
    save.hidden = !previewFile;
  });
  save.addEventListener('click', async () => {
    if (!previewFile || save.disabled) return;
    save.disabled = true;
    const result = await api.post('/api/voice_design/save', {
      name: name.querySelector('input').value.trim(),
      description: description.querySelector('textarea').value.trim(),
      sample_text: sample.querySelector('textarea').value.trim(),
      preview_file: previewFile,
      scope: reusable.querySelector('input').checked ? 'reusable' : 'project',
    }, { signal });
    if (signal.aborted) return;
    if (!result.ok) save.disabled = false;
    output.replaceChildren(UI.notice({
      tone: result.ok ? 'success' : 'error',
      title: result.ok
        ? result.data?.scope === 'reusable' ? 'Reusable Designed Voice saved' : 'Project Designed Voice saved'
        : 'Voice was not saved',
      body: result.ok
        ? result.data?.scope === 'reusable'
          ? 'It is available from Voices in this and future projects.'
          : 'It stays with this project and is listed as a Project Voice in Voices.'
        : resultMessage(result, 'The preview remains available.'),
      live: true,
    }));
    if (result.ok) window.setTimeout(
      () => shell.navigate(route.hash, { historyMode: 'replace' }),
      180,
    );
  });
  const identityFields = document.createElement('div');
  identityFields.className = 'voice-designer-form__row';
  identityFields.append(name, language);
  const designFields = document.createElement('div');
  designFields.className = 'voice-designer-form__row';
  designFields.append(description, sample);
  form.append(identityFields, designFields, reusable, actions, output);
  return form;
}

export async function mount({ root, route, shell, api, signal }) {
  const dataRouteOwner = route.path;
  const { owner, stateRegion } = supportOwner(root, route, {
    shell,
    page: 'voice-designer',
    title: 'Voice designer',
    subtitle: 'Audition a Designed Voice, then keep it in this project or explicitly make it reusable.',
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
      body: 'Saving keeps the Voice in this project by default. Choose reusable scope only when it should be available to other projects; assignment still stays in Cast.',
    }),
    grid,
  );
  owner.dataset.viewState = 'ready';
  return () => {};
}
