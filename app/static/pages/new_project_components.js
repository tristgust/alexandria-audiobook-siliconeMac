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
  const closeButton = UI.iconButton({
    label: 'Close New Project', name: 'close', tooltip: 'Close', onClick: onClose,
  });
  closeButton.dataset[dataNewProjectClose] = '';
  header.append(headerCopy, closeButton);

  const form = document.createElement('form');
  form.className = 'new-project__form';
  const body = document.createElement('div');
  body.className = 'new-project__body';
  const editorial = document.createElement('aside');
  editorial.className = 'new-project__editorial';
  const sourcePreview = document.createElement('div');
  sourcePreview.className = 'new-project__source-preview';
  let activeCover = UI.sourceCover({
    label: 'No source cover selected', emptyLabel: 'Choose a source book',
  });
  activeCover.classList.add('new-project__cover');
  const sourceIdentity = document.createElement('div');
  sourceIdentity.className = 'new-project__source-identity';
  const sourceIdentityTitle = text('strong', 'entity-title', 'Choose a source book');
  const sourceIdentityMeta = text('span', 'metadata', 'EPUB, text, Markdown, or Alexandria Script');
  sourceIdentity.append(sourceIdentityTitle, sourceIdentityMeta);
  sourcePreview.append(activeCover, sourceIdentity);
  const source = field({
    label: 'Source file',
    description: 'Choose an EPUB, plain text, Markdown, or existing Alexandria Script.',
    type: 'file', name: 'source_file', required: true,
    attributes: { accept: '.epub,.txt,.md,.json' },
  });
  const sourceStatus = text('div', 'transaction-status', 'Choose a source to inspect.');
  sourceStatus.setAttribute('role', 'status');
  sourceStatus.setAttribute('aria-live', 'polite');
  editorial.append(formSection(1, 'Choose source file', [sourcePreview, source.wrapper, sourceStatus]));

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
      emptyLabel: inspection.source_type ? String(inspection.source_type).toUpperCase() : 'Source selected',
    });
    nextCover.classList.add('new-project__cover');
    activeCover.replaceWith(nextCover);
    activeCover = nextCover;
    sourceIdentityTitle.textContent = inspection.title || candidate.name.replace(/\.[^.]+$/, '');
    sourceIdentityMeta.textContent = [
      inspection.author,
      inspection.source_type ? String(inspection.source_type).toUpperCase() : null,
      Number.isFinite(inspection.chapter_count) ? `${inspection.chapter_count} chapters` : null,
      inspection.language,
    ].filter(Boolean).join(' · ') || candidate.name;
  };

  return {
    layer, form, source, sourceStatus, bookTitle, author, sourceLanguage, outputLanguage,
    importNote, submitStatus, create, renderSourcePreview,
  };
}
