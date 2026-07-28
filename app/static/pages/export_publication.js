'use strict';

import {
  exportBytes, exportClock, exportDisplayFilename, exportPanel, exportText,
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

function requiredMetadataField(controls, name, label, value, options = {}, onChange) {
  let wrapper;
  const sync = () => {
    const control = controls[name];
    const missing = !control.value.trim();
    wrapper.dataset.state = missing ? 'invalid' : 'filled';
    if (missing) control.setAttribute('aria-invalid', 'true');
    else control.removeAttribute('aria-invalid');
    const message = wrapper.querySelector('.field__message');
    if (message) {
      message.hidden = !missing;
      message.classList.toggle('field__message--error', missing);
      if (missing) message.setAttribute('role', 'alert');
      else message.removeAttribute('role');
    }
  };
  const handleChange = () => {
    sync();
    onChange();
  };
  wrapper = field(controls, name, label, value, {
    ...options,
    required: true,
    invalid: !String(value || '').trim(),
    message: 'Required to build.',
  }, handleChange);
  sync();
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

function currentTake({ aggregate, metadata, selectedOutput, shell }) {
  const preview = document.createElement('section');
  preview.className = 'export-preview';
  const output = aggregate.selected_outputs?.find((item) => item.playback_url) || null;
  if (aggregate.player && output) {
    const displayFilename = exportDisplayFilename(output.filename);
    preview.append(UI.waveform({
      value: 0,
      maximum: Math.max(1, Math.round((Number(output.duration_ms) || 1000) / 1000)),
      label: `Final audiobook position for ${displayFilename}`,
    }));
    shell.player.set({
      state: 'active',
      src: output.playback_url,
      position: 0,
      duration: Math.max(.01, (Number(output.duration_ms) || 1000) / 1000),
      title: metadata.title || displayFilename,
      subtitle: `Current Take · ${displayFilename}`,
    });
  } else {
    preview.append(UI.waveform({
      value: 0,
      maximum: 1,
      label: 'Final audiobook unavailable',
      disabled: true,
    }));
    shell.player.set({ state: 'inactive', title: 'No current Export audio' });
  }
  preview.append(publicationMetrics(aggregate, selectedOutput));
  return preview;
}

function publicationMetrics(aggregate, selectedOutput) {
  const metrics = document.createElement('dl');
  metrics.className = 'export-summary-metrics';
  [
    ['Total duration', exportClock(selectedOutput?.duration_ms)],
    ['Chapters', aggregate.summary?.chapter_count || aggregate.chapters?.length || 0],
    ['Estimated file size', exportBytes(selectedOutput?.size_bytes)],
  ].forEach(([label, value]) => {
    const item = document.createElement('div');
    item.append(exportText('dt', '', label), exportText('dd', '', value));
    metrics.append(item);
  });
  return metrics;
}

export function createExportPublication({
  aggregate, projectId, projectTitle, selectedOutput, shell, api, signal, onChange, onRefresh,
}) {
  const metadata = aggregate.metadata || {};
  const displayTitle = metadata.title || projectTitle || 'Untitled audiobook';
  const node = exportPanel('export-publication', 'Publication');
  const identity = document.createElement('div');
  identity.className = 'export-publication__identity';
  const coverUrl = aggregate.cover?.exists && projectId
    ? `/api/projects/${encodeURIComponent(projectId)}/cover`
    : null;
  const initials = String(displayTitle || 'Audiobook')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 3)
    .map((word) => word[0])
    .join('')
    .toUpperCase() || 'A';
  const cover = UI.sourceCover({
    src: coverUrl,
    alt: coverUrl ? `Cover for ${displayTitle}` : '',
    label: 'Source cover not provided',
    emptyLabel: initials,
  });
  const coverBlock = document.createElement('div');
  coverBlock.className = 'export-cover-block';
  const coverActions = document.createElement('div');
  coverActions.className = 'export-cover-actions';
  const coverStatus = document.createElement('span');
  coverStatus.className = 'transaction-status';
  coverStatus.setAttribute('role', 'status');
  coverStatus.setAttribute('aria-live', 'polite');
  const fileInput = document.createElement('input');
  fileInput.type = 'file';
  fileInput.accept = 'image/*';
  fileInput.className = 'visually-hidden';
  fileInput.setAttribute('aria-label', coverUrl ? 'Choose replacement cover image' : 'Choose cover image');
  const chooseCover = UI.button({
    label: coverUrl ? 'Replace' : 'Add cover',
    variant: 'secondary',
    size: 'compact',
    onClick: () => {
      fileInput.value = '';
      fileInput.click();
    },
  });
  fileInput.addEventListener('change', async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      coverStatus.textContent = 'Choose an image file.';
      return;
    }
    chooseCover.disabled = true;
    coverStatus.textContent = 'Uploading cover…';
    const body = new FormData();
    body.set('file', file);
    const result = await api.post('/api/m4b_cover', body, { signal });
    if (signal.aborted) return;
    chooseCover.disabled = false;
    if (!result.ok) {
      coverStatus.textContent = result.error || 'Cover could not be uploaded.';
      return;
    }
    coverStatus.textContent = 'Cover updated.';
    await onRefresh();
  });
  coverActions.append(chooseCover, fileInput);
  if (coverUrl && aggregate.cover?.user_provided) {
    const removeCover = UI.button({ label: 'Remove', variant: 'quiet', size: 'compact' });
    const removeDialog = UI.dialog({
      opener: removeCover,
      title: 'Remove cover',
      body: 'Remove the cover from this project. Existing audio and metadata are unchanged.',
      confirmLabel: 'Remove cover',
      destructive: true,
      onConfirm: async () => {
        coverStatus.textContent = 'Removing cover…';
        const result = await api.delete('/api/m4b_cover', { signal });
        if (signal.aborted) return;
        if (!result.ok) {
          coverStatus.textContent = result.error || 'Cover could not be removed.';
          return;
        }
        await onRefresh();
      },
    });
    coverActions.append(removeCover);
  }
  coverActions.append(coverStatus);
  coverBlock.append(cover, coverActions);
  const copy = document.createElement('div');
  copy.className = 'export-publication__copy';
  const credits = document.createElement('dl');
  credits.className = 'export-credit-list';
  [
    ['Narrator', metadata.narrator || 'Not entered'],
    ['Cast', aggregate.cast_credit || aggregate.cast?.credit || 'Project Cast'],
  ].forEach(([term, value]) => {
    const row = document.createElement('div');
    row.append(exportText('dt', '', term), exportText('dd', '', value));
    credits.append(row);
  });
  copy.append(
    exportText('span', 'utility-heading', 'Final audiobook'),
    exportText('h3', 'section-title', displayTitle),
    exportText('p', 'metadata', metadata.author ? `by ${metadata.author}` : 'Author required'),
    credits,
  );
  identity.append(coverBlock, copy);

  const controls = {};
  const metadataForm = document.createElement('div');
  metadataForm.className = 'export-metadata';
  metadataForm.append(
    requiredMetadataField(controls, 'title', 'Title', metadata.title || '', {
      placeholder: projectTitle || 'Enter audiobook title',
    }, onChange),
    requiredMetadataField(controls, 'author', 'Author', metadata.author || '', {}, onChange),
    field(controls, 'narrator', 'Narrator', metadata.narrator || '', {}, onChange),
    field(controls, 'year', 'Year', metadata.year || '', {}, onChange),
    field(controls, 'description', 'Description', metadata.description || '', {
      kind: 'textarea',
    }, onChange),
  );
  node.append(
    identity,
    metadataForm,
    currentTake({ aggregate, metadata, selectedOutput, shell }),
  );
  return { node, controls, metadata: () => publicationMetadata(controls) };
}
