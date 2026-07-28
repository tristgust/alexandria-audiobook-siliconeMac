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

function formSection(title, content) {
  const section = document.createElement('section');
  section.className = 'new-project__section';
  section.append(text('h3', 'entity-title', title));
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
  const mark = document.createElement('span');
  mark.className = 'new-project__file-mark';
  mark.append(UI.icon('document'));
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
    setSummary(nextTitle, nextMeta, nextAction = 'Change') {
      title.textContent = nextTitle;
      meta.textContent = nextMeta;
      action.textContent = nextAction;
    },
  };
}

function sourcePlaceholder() {
  const node = document.createElement('div');
  node.className = 'new-project__empty-source';
  const mark = document.createElement('span');
  mark.className = 'new-project__empty-source-mark book-mark';
  mark.setAttribute('aria-hidden', 'true');
  mark.innerHTML = `
    <svg viewBox="0 0 72 58" focusable="false">
      <path d="M36 50c-7.5-5.7-16.5-8.5-27-8.5V7.8C19.7 7.8 28.7 10.7 36 16.4Z"></path>
      <path d="M36 50c7.5-5.7 16.5-8.5 27-8.5V7.8C52.3 7.8 43.3 10.7 36 16.4Z"></path>
      <path d="M36 16.4V50"></path>
      <path d="M9 13.5H4.5V47c12 0 22.5 2.2 31.5 6.7 9-4.5 19.5-6.7 31.5-6.7V13.5H63"></path>
    </svg>`;
  node.append(
    mark,
    text('strong', 'entity-title', 'Choose a source book'),
    text('p', 'metadata', 'The cover and extracted metadata will appear here.'),
  );
  return node;
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
  surface.className = 'dialog-surface new-project new-project-dialog';
  surface.setAttribute('role', 'dialog');
  surface.setAttribute('aria-modal', 'true');
  surface.setAttribute('aria-labelledby', 'new-project-title');
  surface.setAttribute('aria-describedby', 'new-project-description');

  const header = document.createElement('header');
  header.className = 'dialog__header new-project__header new-project-header';
  const headerCopy = document.createElement('div');
  headerCopy.className = 'new-project__header-copy';
  const title = text('h2', 'page-title', 'New Project');
  title.id = 'new-project-title';
  const description = text('p', 'page-subtitle',
    'Create an audiobook project without confronting advanced runtime settings.');
  description.id = 'new-project-description';
  headerCopy.append(title, description);
  const closeButton = UI.iconButton({
    label: 'Close New Project', name: 'close', tooltip: 'Close', onClick: onClose,
  });
  closeButton.dataset[dataNewProjectClose] = '';
  header.append(headerCopy, closeButton);

  const form = document.createElement('form');
  form.className = 'new-project__form';
  const body = document.createElement('div');
  body.className = 'new-project__body new-project-layout';

  const editorial = document.createElement('aside');
  editorial.className = 'new-project__editorial new-project-editorial';
  const sourcePreview = document.createElement('div');
  sourcePreview.className = 'new-project__source-preview';
  let activeVisual = sourcePlaceholder();
  sourcePreview.append(activeVisual);
  editorial.append(sourcePreview);

  const sourceFacts = document.createElement('dl');
  sourceFacts.className = 'new-project__source-facts';
  sourceFacts.hidden = true;
  const factNodes = {};
  [['format', 'Format'], ['chapters', 'Chapters'], ['language', 'Language'], ['file', 'File']]
    .forEach(([key, label]) => {
      const row = document.createElement('div');
      const value = text('dd', '', '—');
      factNodes[key] = value;
      row.append(text('dt', '', label), value);
      sourceFacts.append(row);
    });
  editorial.append(sourceFacts);

  const formColumn = document.createElement('div');
  formColumn.className = 'new-project__fields new-project-form-column';

  const source = filePicker({
    label: 'Choose source file',
    description: 'EPUB, UTF-8 text, or Alexandria Script JSON',
    name: 'source_file',
    accept: '.epub,.txt,.md,.json',
  });
  const sourceStatus = text('div', 'transaction-status new-project__source-status',
    'Choose an EPUB or UTF-8 text file. It is inspected before attachment.');
  sourceStatus.setAttribute('role', 'status');
  sourceStatus.setAttribute('aria-live', 'polite');

  const identity = document.createElement('div');
  identity.className = 'form-grid new-project__identity-grid';
  const projectName = field({ label: 'Project name', name: 'project_name', required: true });
  projectName.wrapper.classList.add('new-project__field-wide');
  const bookTitle = field({ label: 'Book title', name: 'book_title', required: true });
  const author = field({ label: 'Author', name: 'author', required: true });
  identity.append(projectName.wrapper, bookTitle.wrapper, author.wrapper);

  const language = document.createElement('div');
  language.className = 'form-grid';
  const sourceLanguage = field({
    label: 'Source language', name: 'source_language', value: 'English', required: true,
  });
  const outputLanguage = field({
    label: 'Output language', name: 'output_language', value: 'English', required: true,
  });
  language.append(sourceLanguage.wrapper, outputLanguage.wrapper);

  const method = UI.radioGroup({
    label: 'Generation method', name: 'generation_method', options: methodOptions,
  });
  method.classList.add('new-project__method-options');
  const importNote = text('p', 'metadata new-project__import-note',
    'Choose an Alexandria Script JSON as the source file above.');
  importNote.hidden = true;

  const preset = UI.radioGroup({ label: 'Preset', name: 'preset', options: presetOptions });
  preset.classList.add('new-project__preset-options');

  formColumn.append(
    formSection('Source file', [source.wrapper, sourceStatus]),
    formSection('Project and book identity', identity),
    formSection('Language', language),
    formSection('Generation method', [method, importNote]),
    formSection('Preset', preset),
  );

  const advanced = document.createElement('div');
  advanced.className = 'new-project__advanced';
  advanced.append(text('p', 'metadata',
    'Advanced values follow the selected preset and can be changed after project creation.'));
  const disclosure = UI.disclosure({
    label: 'Advanced options', content: advanced, expanded: Boolean(templateId),
  });
  disclosure.classList.add('new-project__advanced-disclosure');
  formColumn.append(disclosure);

  const submitStatus = text('div', 'transaction-status new-project__submit-status',
    'Choose a valid source to continue.');
  submitStatus.setAttribute('role', 'status');
  submitStatus.setAttribute('aria-live', 'polite');
  body.append(editorial, formColumn);

  const footer = document.createElement('footer');
  footer.className = 'dialog__footer new-project__footer new-project-footer';
  const footerActions = document.createElement('div');
  footerActions.className = 'new-project__footer-actions new-project-footer-actions';
  const cancel = UI.button({ label: 'Cancel', variant: 'secondary', onClick: onClose });
  const create = UI.button({
    label: 'Create Project', variant: 'primary', type: 'submit', disabled: true,
  });
  footerActions.append(cancel, create);
  footer.append(submitStatus, footerActions);
  form.append(body, footer);
  surface.append(header, form);
  layer.append(surface);

  const renderSourcePreview = (candidate, inspection) => {
    const coverUrl = inspection.cover_url || inspection.cover?.url || '';
    const nextVisual = document.createElement('div');
    nextVisual.className = 'new-project__selected-source';
    const cover = UI.sourceCover({
      src: coverUrl || null,
      alt: coverUrl ? `Cover for ${inspection.title || candidate.name}` : '',
      label: `No source cover is available for ${inspection.title || candidate.name}`,
      emptyLabel: inspection.source_type ? String(inspection.source_type).toUpperCase() : 'Source selected',
    });
    cover.classList.add('new-project__cover');
    const sourceType = inspection.source_type
      ? String(inspection.source_type).toUpperCase() : candidate.name.split('.').pop()?.toUpperCase() || 'SOURCE';
    nextVisual.append(
      cover,
      text('strong', 'entity-title', inspection.title || candidate.name.replace(/\.[^.]+$/, '')),
      text('p', 'metadata', inspection.author || 'Author not found'),
    );
    activeVisual.replaceWith(nextVisual);
    activeVisual = nextVisual;
    sourceFacts.hidden = false;
    factNodes.format.textContent = sourceType;
    factNodes.chapters.textContent = Number.isFinite(inspection.chapter_count)
      ? String(inspection.chapter_count) : '—';
    factNodes.language.textContent = inspection.language || '—';
    factNodes.file.textContent = candidate.name;
    source.setSummary(candidate.name, `${sourceType} · ${candidate.size.toLocaleString()} bytes`, 'Change');
  };

  return {
    layer, form, source, sourceStatus, projectName, bookTitle, author,
    sourceLanguage, outputLanguage, importNote, submitStatus, create, renderSourcePreview,
  };
}
