'use strict';

const UI = globalThis.AlexandriaUI;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

function fileDescription(file) {
  if (!file) return 'No completed task selected';
  const size = Number(file.size || 0);
  const sizeLabel = size >= 1024 * 1024
    ? `${(size / (1024 * 1024)).toFixed(1)} MB`
    : `${Math.max(1, Math.round(size / 1024))} KB`;
  return `${file.name} · ${sizeLabel}`;
}

export function createTaskImportSurface({
  api, signal, title = 'Import completed task',
  description, onImported, report,
}) {
  let completedFile = null;
  let originalFile = null;
  const section = document.createElement('section');
  section.className = 'task-import-surface';
  section.dataset.taskImportSurface = '';

  const heading = document.createElement('header');
  heading.className = 'task-import-surface__header';
  heading.append(
    text('span', 'metadata task-import-surface__eyebrow', 'Return from ChatGPT'),
    text('h2', '', title),
    text('p', 'support-status-copy task-import-surface__description', description
      || 'Choose the completed ZIP ChatGPT returned. Alexandria validates the task identity and opens a native review; nothing is approved automatically.'),
  );

  const steps = document.createElement('ol');
  steps.className = 'task-import-steps';
  [
    ['1', 'Export', 'Alexandria task ZIP'],
    ['2', 'ChatGPT', 'Completed ZIP'],
    ['3', 'Import', 'Open native review'],
  ].forEach(([number, label, body], index) => {
    const item = document.createElement('li');
    item.className = 'task-import-steps__item';
    item.dataset.state = index === 2 ? 'current' : 'complete';
    item.append(
      text('span', 'task-import-steps__number', number),
      text('strong', 'task-import-steps__label', label),
      text('span', 'metadata task-import-steps__detail', body),
    );
    steps.append(item);
  });

  const completedInput = document.createElement('input');
  completedInput.type = 'file';
  completedInput.accept = '.zip,.json,application/zip,application/json';
  completedInput.hidden = true;
  completedInput.dataset.completedTaskFile = '';
  const drop = document.createElement('button');
  drop.type = 'button';
  drop.className = 'task-import-dropzone';
  drop.dataset.taskImportDropzone = '';
  const dropIcon = document.createElement('span');
  dropIcon.className = 'task-import-dropzone__icon';
  dropIcon.append(UI.icon('export'));
  const dropCopy = document.createElement('span');
  dropCopy.className = 'task-import-dropzone__copy';
  const dropTitle = text('strong', 'task-import-dropzone__title', 'Drop the completed ZIP here');
  const dropBody = text(
    'span',
    'metadata task-import-dropzone__description',
    'Choose the .alexandria-completed-task.zip returned by ChatGPT. Do not unzip it.',
  );
  dropCopy.append(dropTitle, dropBody);
  const dropAction = text('span', 'task-import-dropzone__action', 'Choose file');
  drop.append(dropIcon, dropCopy, dropAction);

  const selected = document.createElement('div');
  selected.className = 'task-import-selected';
  selected.hidden = true;
  const selectedIcon = document.createElement('span');
  selectedIcon.className = 'task-import-selected__icon';
  selectedIcon.append(UI.icon('document'));
  const selectedCopy = document.createElement('div');
  selectedCopy.className = 'task-import-selected__copy';
  selectedCopy.append(
    text('strong', '', 'Completed ZIP'),
    text('span', 'metadata', ''),
  );
  const clear = UI.button({ label: 'Remove file', variant: 'quiet', size: 'compact' });
  selected.append(selectedIcon, selectedCopy, clear);

  const fallback = UI.disclosure({
    label: 'Fallback JSON result',
    content: text(
      'p',
      'support-status-copy',
      'Only add the original task ZIP when ChatGPT returned a fallback JSON instead of a completed ZIP.',
    ),
  });
  fallback.classList.add('task-import-fallback');
  const originalInput = document.createElement('input');
  originalInput.type = 'file';
  originalInput.accept = '.zip,application/zip';
  originalInput.dataset.originalTaskFile = '';
  const originalField = document.createElement('label');
  originalField.className = 'task-import-fallback__field';
  originalField.append(
    text('span', 'field__label', 'Original task ZIP'),
    originalInput,
    text('span', 'field__message', 'Leave empty for a normal completed ZIP.'),
  );
  fallback.querySelector('.disclosure__content')?.append(originalField);

  const footer = document.createElement('div');
  footer.className = 'task-import-surface__footer';
  const status = text('div', 'transaction-status task-import-surface__status', 'No completed ZIP selected.');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  const importButton = UI.button({
    label: 'Validate ZIP',
    variant: 'primary',
    disabled: true,
    attributes: { 'data-import-completed-task': '' },
  });
  footer.append(status, importButton);
  const resultHost = document.createElement('div');
  resultHost.className = 'task-import-result';
  resultHost.dataset.completedTaskResult = '';

  function syncFile(file) {
    completedFile = file || null;
    importButton.disabled = !completedFile;
    drop.hidden = Boolean(completedFile);
    selected.hidden = !completedFile;
    selectedCopy.querySelector('.metadata').textContent = fileDescription(completedFile);
    status.textContent = completedFile
      ? 'Ready to validate. Nothing has changed.'
      : 'No completed ZIP selected.';
    resultHost.replaceChildren();
  }

  function sameFile(left, right) {
    return Boolean(
      left && right
      && left.name === right.name
      && left.size === right.size
      && left.lastModified === right.lastModified
    );
  }

  function acceptDroppedFile(file) {
    if (!file) return;
    if (completedFile && !sameFile(completedFile, file)) {
      const replace = typeof globalThis.confirm !== 'function' || globalThis.confirm(
        `Replace ${completedFile.name} with ${file.name}?`,
      );
      if (!replace) {
        status.textContent = `${completedFile.name} was kept.`;
        return;
      }
      completedInput.value = '';
      syncFile(file);
      status.textContent = `${file.name} replaced the previous file. Ready to validate.`;
      return;
    }
    syncFile(file);
  }

  function bindDropTarget(target) {
    ['dragenter', 'dragover'].forEach((name) => target.addEventListener(name, (event) => {
      event.preventDefault();
      target.dataset.dragging = 'true';
    }));
    ['dragleave', 'drop'].forEach((name) => target.addEventListener(name, (event) => {
      event.preventDefault();
      delete target.dataset.dragging;
    }));
    target.addEventListener('drop', (event) => {
      acceptDroppedFile(event.dataTransfer?.files?.[0]);
    });
  }

  drop.addEventListener('click', () => completedInput.click());
  completedInput.addEventListener('change', () => syncFile(completedInput.files?.[0]));
  clear.addEventListener('click', () => {
    completedInput.value = '';
    syncFile(null);
    drop.focus();
  });
  bindDropTarget(drop);
  bindDropTarget(selected);
  originalInput.addEventListener('change', () => {
    originalFile = originalInput.files?.[0] || null;
  });

  importButton.addEventListener('click', async () => {
    if (!completedFile) return;
    importButton.disabled = true;
    importButton.textContent = 'Validating…';
    status.textContent = 'Checking task identity, checksums, source, and artifact fingerprints…';
    const form = new FormData();
    form.append('file', completedFile);
    if (originalFile) form.append('original_task', originalFile);
    const response = await api.post('/api/tasks/import', form, { signal });
    importButton.textContent = 'Validate ZIP';
    importButton.disabled = false;
    if (!response.ok) {
      status.textContent = response.error;
      resultHost.replaceChildren(UI.notice({
        tone: 'error',
        title: 'Completed task was not imported',
        body: response.error,
        live: true,
      }));
      return;
    }
    status.textContent = 'Completed task validated. Nothing has been approved.';
    report?.('Completed task validated', 'Alexandria is preparing its native review.', 'success');
    await onImported?.(response.data || {}, resultHost, status);
  });

  section.append(
    heading,
    steps,
    completedInput,
    drop,
    selected,
    fallback,
    footer,
    resultHost,
  );
  return Object.freeze({ section, resultHost, status, syncFile });
}
