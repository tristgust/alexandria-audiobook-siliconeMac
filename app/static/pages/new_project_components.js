'use strict';

const UI = globalThis.AlexandriaUI;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

function field(options) {
  const wrapper = UI.field(options);
  const control = wrapper.querySelector('input, select, textarea');
  control.name = options.name;
  return { wrapper, control };
}

function formSection(number, title, content) {
  const section = document.createElement('section');
  section.className = 'new-project__section';
  section.append(text('h3', 'entity-title', `${number}. ${title}`));
  (Array.isArray(content) ? content : [content]).filter(Boolean)
    .forEach((node) => section.append(node));
  return section;
}

function filePicker({ label, description, name, accept }) {
  const wrapper = document.createElement('div');
  wrapper.className = 'new-project__file-field';
  const input = document.createElement('input');
  input.type = 'file';
  input.name = name;
  input.accept = accept;
  input.required = true;
  input.className = 'visually-hidden';
  const row = document.createElement('label');
  row.className = 'new-project__file-row';
  row.tabIndex = 0;
  row.setAttribute('role', 'button');
  row.setAttribute('aria-label', label);
  row.addEventListener('keydown', (event) => {
    if (!['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    input.click();
  });
  const mark = document.createElement('span');
  mark.className = 'new-project__file-mark';
  const fileIcon = document.createElement('i');
  fileIcon.className = 'far fa-file-lines';
  fileIcon.setAttribute('aria-hidden', 'true');
  mark.append(fileIcon);
  const copy = document.createElement('span');
  copy.className = 'new-project__file-copy';
  const title = text('strong', '', label);
  const meta = text('span', 'metadata', description);
  copy.append(title, meta);
  const action = text('span', 'new-project__file-action', 'Choose');
  row.append(mark, copy, action, input);
  wrapper.append(row);
  return {
    wrapper,
    control: input,
    focusTarget: row,
    setSummary(nextTitle, nextMeta, nextAction = 'Change') {
      title.textContent = nextTitle;
      meta.textContent = nextMeta;
      action.textContent = nextAction;
    },
  };
}

export function showNewProjectDiscardConfirmation(layer, onDiscard) {
  if (!layer || layer.querySelector('[data-new-project-discard]')) return;
  const form = layer.querySelector('.new-project__form');
  const confirmation = document.createElement('section');
  confirmation.className = 'new-project__discard-confirmation';
  confirmation.dataset.newProjectDiscard = '';
  confirmation.setAttribute('role', 'alertdialog');
  confirmation.setAttribute('aria-labelledby', 'new-project-discard-title');
  const title = text('h3', 'section-title', 'Discard this new project?');
  title.id = 'new-project-discard-title';
  const body = text('p', 'metadata', 'The selected source and unsaved project choices will be cleared.');
  const actions = document.createElement('div');
  actions.className = 'new-project__discard-actions';
  const keep = UI.button({ label: 'Keep editing', variant: 'secondary' });
  const discard = UI.button({ label: 'Discard', variant: 'destructive' });
  keep.addEventListener('click', () => {
    confirmation.remove();
    form.inert = false;
    layer.querySelector('[data-new-project-close]')?.focus();
  });
  discard.addEventListener('click', onDiscard);
  actions.append(keep, discard);
  confirmation.append(title, body, actions);
  form.inert = true;
  layer.querySelector('.new-project').append(confirmation);
  keep.focus();
}

export function buildNewProjectDialog({
  templateId, onClose, dataNewProject, dataNewProjectClose, methodOptions, presetOptions,
}) {
  const layer = document.createElement('div');
  layer.className = 'dialog-layer new-project-layer';
  layer.dataset[dataNewProject] = '';
  const surface = document.createElement('section');
  surface.className = 'dialog-surface new-project';
  surface.setAttribute('role', 'dialog');
  surface.setAttribute('aria-modal', 'true');
  surface.setAttribute('aria-labelledby', 'new-project-title');
  surface.setAttribute('aria-describedby', 'new-project-description');
  const header = document.createElement('header');
  header.className = 'dialog__header new-project__header';
  const headerCopy = document.createElement('div');
  headerCopy.className = 'new-project__header-copy';
  const title = text('h2', 'page-title', 'New Project');
  title.id = 'new-project-title';
  const description = text('p', 'page-subtitle', 'Create a new audiobook project in just a few steps.');
  description.id = 'new-project-description';
  headerCopy.append(title, description);
  const steps = document.createElement('ol');
  steps.className = 'new-project__steps';
  ['Source', 'Details', 'Language', 'Method', 'Preset'].forEach((label, index, labels) => {
    const item = document.createElement('li');
    item.className = 'new-project__step';
    item.dataset.state = index === 0 ? 'current' : 'future';
    const marker = text('span', 'new-project__step-marker', String(index + 1));
    const caption = text('span', 'new-project__step-label', label);
    item.append(marker, caption);
    if (index < labels.length - 1) item.append(text('span', 'new-project__step-line', ''));
    steps.append(item);
  });
  steps.setAttribute('aria-label', 'New project steps');
  const closeButton = UI.iconButton({
    label: 'Close New Project', name: 'close', tooltip: 'Close', onClick: onClose,
  });
  closeButton.dataset[dataNewProjectClose] = '';
  header.append(headerCopy, steps, closeButton);

  const form = document.createElement('form');
  form.className = 'new-project__form';
  const body = document.createElement('div');
  body.className = 'new-project__body';
  const editorial = document.createElement('aside');
  editorial.className = 'new-project__editorial';
  const editorialEmpty = document.createElement('div');
  editorialEmpty.className = 'new-project__editorial-empty';
  editorialEmpty.innerHTML = `
    <span class="new-project__empty-book" aria-hidden="true">
      <svg viewBox="0 0 72 58" focusable="false">
        <path d="M36 50c-7.5-5.7-16.5-8.5-27-8.5V7.8C19.7 7.8 28.7 10.7 36 16.4Z"></path>
        <path d="M36 50c7.5-5.7 16.5-8.5 27-8.5V7.8C52.3 7.8 43.3 10.7 36 16.4Z"></path>
        <path d="M36 16.4V50"></path>
      </svg>
    </span>
    <strong>Choose a source book</strong>
    <span>The cover and extracted metadata will appear here.</span>`;
  const sourcePreview = document.createElement('div');
  sourcePreview.className = 'new-project__source-preview';
  sourcePreview.hidden = true;
  let activeCover = UI.sourceCover({
    label: 'No source cover selected', iconClass: 'fas fa-book-open',
  });
  activeCover.classList.add('new-project__cover');
  const sourceIdentity = document.createElement('div');
  sourceIdentity.className = 'new-project__source-identity';
  const sourceState = text('span', 'new-project__source-state', 'Source not selected');
  const sourceIdentityTitle = text('strong', 'entity-title', 'Choose a source book');
  const sourceIdentityMeta = text('span', 'metadata', 'EPUB, text, Markdown, or Alexandria Script');
  const sourceFacts = document.createElement('dl');
  sourceFacts.className = 'new-project__source-facts';
  const factNodes = {};
  [['format', 'Format'], ['chapters', 'Chapters'], ['language', 'Language'], ['file', 'File']]
    .forEach(([key, label]) => {
      const row = document.createElement('div');
      const value = text('dd', '', '—');
      factNodes[key] = value;
      row.append(text('dt', '', label), value);
      sourceFacts.append(row);
    });
  sourceIdentity.append(sourceState, sourceIdentityTitle, sourceIdentityMeta, sourceFacts);
  sourcePreview.append(activeCover, sourceIdentity);
  const source = filePicker({
    label: 'Choose source file',
    description: 'EPUB, UTF-8 text, Markdown, or Alexandria Script JSON',
    name: 'source_file',
    accept: '.epub,.txt,.md,.json',
  });
  const sourceStatus = text('div', 'transaction-status', 'Choose a source to inspect.');
  sourceStatus.setAttribute('role', 'status');
  sourceStatus.setAttribute('aria-live', 'polite');
  editorial.append(formSection(1, 'Choose source file', [editorialEmpty, sourcePreview, source.wrapper, sourceStatus]));

  const formColumn = document.createElement('div');
  formColumn.className = 'new-project__fields';
  const identity = document.createElement('div');
  identity.className = 'form-grid';
  const bookTitle = field({ label: 'Title', name: 'book_title', required: true });
  const author = field({ label: 'Author', name: 'author', required: true });
  identity.append(bookTitle.wrapper, author.wrapper);
  const language = document.createElement('div');
  language.className = 'form-grid';
  const sourceLanguage = field({
    label: 'Source language', description: 'Detected from file',
    name: 'source_language', value: 'English', required: true,
  });
  const outputLanguage = field({
    label: 'Output language', description: 'For narration and generated content',
    name: 'output_language', value: 'English', required: true,
  });
  language.append(sourceLanguage.wrapper, outputLanguage.wrapper);
  const method = UI.radioGroup({
    label: 'Script creation method', name: 'generation_method', options: methodOptions,
  });
  method.classList.add('new-project__method-options');
  const importNote = text('p', 'metadata new-project__import-note',
    'Choose an Alexandria Script JSON as the source file above.');
  importNote.hidden = true;
  const preset = UI.radioGroup({ label: 'Preset', name: 'preset', options: presetOptions });
  preset.classList.add('new-project__preset-options');
  formColumn.append(
    formSection(2, 'Confirm title and author', identity),
    formSection(3, 'Select source and output language', language),
    formSection(4, 'Choose Script creation method', [method, importNote]),
    formSection(5, 'Select a preset', preset),
  );
  const submitStatus = text('div', 'transaction-status new-project__submit-status', '');
  submitStatus.setAttribute('role', 'status');
  submitStatus.setAttribute('aria-live', 'polite');
  formColumn.append(submitStatus);
  body.append(editorial, formColumn);

  const advanced = document.createElement('div');
  advanced.className = 'new-project__advanced';
  advanced.append(text('p', 'metadata',
    'Advanced values follow the selected preset and remain hidden from the normal creation flow.'));
  const disclosure = UI.disclosure({
    label: 'Advanced options', content: advanced, expanded: Boolean(templateId),
  });
  const footer = document.createElement('footer');
  footer.className = 'dialog__footer new-project__footer';
  const footerActions = document.createElement('div');
  footerActions.className = 'new-project__footer-actions';
  const cancel = UI.button({ label: 'Cancel', variant: 'secondary', onClick: onClose });
  const create = UI.button({
    label: 'Create Project', variant: 'primary', type: 'submit', disabled: true,
  });
  footerActions.append(cancel, create);
  footer.append(disclosure, footerActions);
  form.append(body, footer);
  surface.append(header, form);
  layer.append(surface);

  const renderSourcePreview = (candidate, inspection) => {
    const coverUrl = inspection.cover_url || inspection.cover?.url || '';
    const nextCover = UI.sourceCover({
      src: coverUrl || null,
      alt: coverUrl ? `Cover for ${inspection.title || candidate.name}` : '',
      label: `No source cover is available for ${inspection.title || candidate.name}`,
      iconClass: 'fas fa-book-open',
    });
    nextCover.classList.add('new-project__cover');
    activeCover.replaceWith(nextCover);
    activeCover = nextCover;
    editorialEmpty.hidden = true;
    sourcePreview.hidden = false;
    const sourceType = inspection.source_type
      ? String(inspection.source_type).toUpperCase() : candidate.name.split('.').pop()?.toUpperCase() || 'SOURCE';
    sourceState.textContent = `${sourceType} file selected`;
    sourceState.dataset.state = 'success';
    sourceIdentityTitle.textContent = inspection.title || candidate.name.replace(/\.[^.]+$/, '');
    sourceIdentityMeta.textContent = inspection.author || 'Author not found';
    factNodes.format.textContent = sourceType;
    factNodes.chapters.textContent = Number.isFinite(inspection.chapter_count)
      ? String(inspection.chapter_count) : '—';
    factNodes.language.textContent = inspection.language || '—';
    factNodes.file.textContent = candidate.name;
    source.setSummary(candidate.name, `${sourceType} · ${candidate.size.toLocaleString()} bytes`, 'Change');
  };

  return {
    layer, form, source, sourceStatus, bookTitle, author, sourceLanguage, outputLanguage,
    importNote, submitStatus, create, renderSourcePreview,
  };
}
