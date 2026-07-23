'use strict';

import {
  exportBytes, exportClock, exportPanel, exportText,
} from './export_model.js';

const UI = globalThis.AlexandriaUI;

function field(controls, name, label, value, options = {}, onChange) {
  const wrapper = UI.field({ id: `export-${name}`, label, value, ...options });
  const control = wrapper.querySelector('.field__control');
  controls[name] = control;
  control.addEventListener('input', onChange);
  control.addEventListener('change', onChange);
  return wrapper;
}

export function publicationMetadata(controls) {
  return {
    title: controls.title?.value?.trim() || '',
    author: controls.author?.value?.trim() || '',
    narrator: controls.narrator?.value?.trim() || '',
    year: controls.year?.value?.trim() || '',
    description: controls.description?.value?.trim() || '',
  };
}

function currentTake({ aggregate, metadata, shell }) {
  const current = document.createElement('section');
  current.className = 'export-current-take';
  current.append(exportText('h3', 'entity-title', 'Final audiobook preview'));
  const output = aggregate.selected_outputs?.find((item) => item.playback_url) || null;
  if (aggregate.player && output) {
    current.append(
      exportText('strong', '', 'Current Take'),
      exportText('span', 'metadata', `${output.filename} · ${exportClock(output.duration_ms)}`),
      UI.waveform({
        value: 0,
        maximum: Math.max(1, Math.round((Number(output.duration_ms) || 1000) / 1000)),
        label: `Final audiobook position for ${output.filename}`,
      }),
    );
    shell.player.set({
      state: 'active',
      title: metadata.title || output.filename,
      subtitle: `Current Take · ${output.filename}`,
    });
  } else {
    current.append(
      exportText('strong', '', 'No current Take'),
      exportText('span', 'metadata', 'Build a verified output to enable final audiobook playback.'),
      UI.waveform({ value: 0, maximum: 1, label: 'Final audiobook unavailable', disabled: true }),
    );
    shell.player.set({ state: 'inactive', title: 'No current Export audio' });
  }
  return current;
}

function coverControls({ aggregate, api, signal, onRefresh }) {
  const actions = document.createElement('div');
  actions.className = 'export-cover-actions';
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'image/jpeg,image/png,image/webp';
  input.className = 'visually-hidden';
  input.setAttribute('aria-label', 'Choose audiobook cover image');
  const upload = UI.button({
    label: aggregate.cover?.exists ? 'Replace cover' : 'Add cover',
    variant: 'secondary',
    size: 'compact',
    onClick: () => input.click(),
  });
  const feedback = document.createElement('div');
  feedback.className = 'transaction-status';
  feedback.setAttribute('role', 'status');
  feedback.setAttribute('aria-live', 'polite');
  input.addEventListener('change', async () => {
    const file = input.files?.[0];
    if (!file || signal.aborted) return;
    upload.disabled = true;
    feedback.textContent = 'Uploading cover…';
    const body = new FormData();
    body.set('file', file);
    const result = await api.post('/api/m4b_cover', body, { signal });
    upload.disabled = false;
    feedback.textContent = result.ok
      ? 'Cover updated.' : result.error || 'Cover could not be updated.';
    if (result.ok) await onRefresh?.();
  });
  actions.append(input, upload);
  if (aggregate.cover?.exists) {
    const remove = UI.button({ label: 'Remove cover', variant: 'quiet', size: 'compact' });
    UI.dialog({
      opener: remove,
      title: 'Remove audiobook cover?',
      body: 'The source book and generated audio remain unchanged. A new cover can be added before the next build.',
      confirmLabel: 'Remove cover',
      destructive: true,
      onConfirm: async () => {
        const result = await api.delete('/api/m4b_cover', { signal });
        feedback.textContent = result.ok
          ? 'Cover removed.' : result.error || 'Cover could not be removed.';
        if (result.ok) await onRefresh?.();
      },
    });
    actions.append(remove);
  }
  actions.append(feedback);
  return actions;
}

function publicationMetrics(aggregate, selectedOutput) {
  const metrics = document.createElement('div');
  metrics.className = 'produce-summary';
  [
    ['Total duration', exportClock(selectedOutput?.duration_ms)],
    ['Chapters', aggregate.summary?.chapter_count || aggregate.chapters?.length || 0],
    ['Estimated file size', exportBytes(selectedOutput?.size_bytes)],
  ].forEach(([label, value]) => {
    const item = document.createElement('div');
    item.className = 'produce-stat';
    item.append(
      exportText('strong', 'produce-stat__value', value),
      exportText('span', 'metadata', label),
    );
    metrics.append(item);
  });
  return metrics;
}

export function createExportPublication({
  aggregate, projectId, selectedOutput, shell, api, signal, onChange, onRefresh,
}) {
  const metadata = aggregate.metadata || {};
  const node = exportPanel('export-publication', 'Publication');
  const identity = document.createElement('div');
  identity.className = 'export-publication__identity';
  const coverUrl = aggregate.cover?.exists && projectId
    ? `/api/projects/${encodeURIComponent(projectId)}/cover`
    : null;
  const cover = UI.sourceCover({
    src: coverUrl,
    alt: coverUrl ? `Cover for ${metadata.title || 'audiobook'}` : '',
    label: 'Source cover not provided',
  });
  const copy = document.createElement('div');
  copy.append(
    exportText('h3', 'section-title', metadata.title || 'Untitled audiobook'),
    exportText('p', 'metadata', metadata.author ? `by ${metadata.author}` : 'Author required'),
    exportText('p', '', metadata.narrator
      ? `Narrated by ${metadata.narrator}` : 'No narrator or cast credits available'),
  );
  copy.append(coverControls({ aggregate, api, signal, onRefresh }));
  identity.append(cover, copy);

  const controls = {};
  const metadataForm = document.createElement('div');
  metadataForm.className = 'export-metadata';
  metadataForm.append(
    field(controls, 'title', 'Title', metadata.title || '', {
      required: true,
      ...(metadata.title ? {} : { message: 'Title is required before build.' }),
    }, onChange),
    field(controls, 'author', 'Author', metadata.author || '', {
      required: true,
      ...(metadata.author ? {} : { message: 'Author is required before build.' }),
    }, onChange),
    field(controls, 'narrator', 'Narrator', metadata.narrator || '', {}, onChange),
    field(controls, 'year', 'Year', metadata.year || '', {}, onChange),
    field(controls, 'description', 'Description', metadata.description || '', {
      kind: 'textarea',
    }, onChange),
  );
  node.append(
    identity,
    metadataForm,
    currentTake({ aggregate, metadata, shell }),
    publicationMetrics(aggregate, selectedOutput),
  );
  return { node, controls, metadata: () => publicationMetadata(controls) };
}
