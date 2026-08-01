'use strict';

import {
  EXPORT_FORMATS, exportDisplayFilename, exportPanel, exportText, exportWords,
} from './export_model.js';

const UI = globalThis.AlexandriaUI;

function validationMark(ok, label) {
  const mark = document.createElement('span');
  mark.className = 'export-validation__mark';
  mark.dataset.tone = ok ? 'success' : 'error';
  mark.append(
    UI.icon(ok ? 'check' : 'close'),
    exportText('span', 'visually-hidden', `${label}: ${ok ? 'ready' : 'needs attention'}`),
  );
  return mark;
}

function renderValidation(node, aggregate, metadata, selectedFormat) {
  node.replaceChildren();
  const destinations = new Set((aggregate.blockers || []).map((blocker) => blocker.native_destination));
  const format = EXPORT_FORMATS.find((entry) => entry.value === selectedFormat) || EXPORT_FORMATS[0];
  const chapters = aggregate.chapters || [];
  const checks = [
    {
      label: 'Title and author',
      copy: metadata.title && metadata.author
        ? 'Both publication fields are ready.' : 'Enter both title and author.',
      ok: Boolean(metadata.title && metadata.author),
    },
    {
      label: 'Production audio',
      copy: destinations.has('produce')
        ? 'Finish or repair Produce before building.' : 'All required production audio is current.',
      ok: !destinations.has('produce'),
    },
    {
      label: format.label,
      copy: format.description || 'The selected output format is available.',
      ok: !format.disabled,
    },
    {
      label: 'Chapter structure',
      copy: chapters.length
        ? `${chapters.length.toLocaleString()} chapter marker${chapters.length === 1 ? '' : 's'} will be used.`
        : 'Add at least one chapter marker before building.',
      ok: Boolean(chapters.length),
    },
  ];
  const blockingCount = checks.filter((check) => !check.ok).length;
  const summary = node.closest('.export-validation-panel')
    ?.querySelector('.export-panel__heading > .metadata');
  if (summary) summary.textContent = blockingCount
    ? `${blockingCount} blocking issue${blockingCount === 1 ? '' : 's'}` : 'Ready to build';
  checks.forEach(({ label, copy, ok }) => {
    const row = document.createElement('div');
    row.className = 'export-validation__row';
    const rowCopy = document.createElement('span');
    rowCopy.className = 'export-validation__copy';
    rowCopy.append(exportText('strong', '', label), exportText('small', '', copy));
    row.append(validationMark(ok, label), rowCopy);
    node.append(row);
  });
}

function technicalDetails(aggregate) {
  const body = document.createElement('div');
  body.className = 'export-output-list';
  Object.values(aggregate.outputs || {}).forEach((output) => {
    const row = document.createElement('div');
    row.className = 'export-output-row';
    const formatLabel = EXPORT_FORMATS.find((format) => format.value === output.format)?.label
      || exportWords(output.format);
    row.append(
      exportText('span', '', `${formatLabel} · ${exportDisplayFilename(output.filename)}`),
      exportText('span', 'metadata', exportWords(output.state || 'missing')),
    );
    body.append(row);
  });
  body.append(exportText(
    'p',
    'metadata',
    'Outputs are written to the current project output folder and verified before becoming current.',
  ));
  const disclosure = UI.disclosure({ label: 'Technical details', content: body });
  disclosure.classList.add('export-technical-details');
  disclosure.querySelector('.disclosure__trigger')?.append(UI.icon('chevron'));
  return disclosure;
}

function canonicalFilename(aggregate, selectedFormat) {
  return exportDisplayFilename(
    aggregate.plan?.output_filenames?.[selectedFormat]
      || aggregate.outputs?.[selectedFormat]?.filename
      || `audiobook${EXPORT_FORMATS.find((format) => format.value === selectedFormat)?.extension || ''}`,
  );
}

function outputSummary({ className, label, value, description, iconClass }) {
  const node = document.createElement('div');
  node.className = `export-output-summary ${className}`;
  const mark = document.createElement('span');
  mark.className = 'export-output-summary__mark';
  mark.setAttribute('aria-hidden', 'true');
  mark.append(UI.iconFromClass(iconClass, 'document'));
  const copy = document.createElement('div');
  copy.className = 'export-output-summary__copy';
  const valueNode = exportText('strong', 'export-output-summary__value', value);
  copy.append(
    exportText('span', 'metadata export-output-summary__label', label),
    valueNode,
    exportText('p', 'metadata export-output-summary__description', description),
  );
  node.append(mark, copy);
  return { node, valueNode };
}

export function createExportOutput({
  aggregate, selectedFormat, getMetadata, onFormatChange, onChange,
}) {
  const node = exportPanel('export-output', 'Output', '', 'Deliverable');
  const controls = {};
  const fields = document.createElement('div');
  fields.className = 'export-output-fields';
  const filenameValue = canonicalFilename(aggregate, selectedFormat);
  const filename = outputSummary({
    className: 'export-output-name',
    label: 'Output filename',
    value: filenameValue,
    description: 'Alexandria verifies this managed filename when the build completes.',
    iconClass: 'far fa-file-audio',
  });
  const filenameControl = document.createElement('input');
  filenameControl.type = 'hidden';
  filenameControl.id = 'export-filename';
  filenameControl.setAttribute('aria-label', 'Output filename');
  filenameControl.value = filenameValue;
  filename.node.append(filenameControl);
  controls.filename = filenameControl;
  fields.append(filename.node);

  const formats = document.createElement('div');
  formats.className = 'export-formats';
  const group = UI.radioGroup({
    label: 'Format',
    name: 'export-format',
    options: EXPORT_FORMATS.map((format) => ({
      value: format.value,
      label: format.label,
      disabled: format.disabled,
      checked: selectedFormat === format.value,
      description: format.description,
    })),
  });
  group.addEventListener('change', (event) => {
    if (!event.target.matches('input[type="radio"]')) return;
    const nextFormat = event.target.value;
    const nextFilename = canonicalFilename(aggregate, nextFormat);
    controls.filename.value = nextFilename;
    filename.valueNode.textContent = nextFilename;
    onFormatChange(nextFormat);
  });
  formats.append(group);

  const chapterMode = document.createElement('input');
  chapterMode.type = 'hidden';
  chapterMode.id = 'export-chapter-mode';
  chapterMode.setAttribute('aria-label', 'Chapter grouping');
  chapterMode.value = aggregate.chapter_mode || 'smart';
  controls.chapterMode = chapterMode;
  const folder = outputSummary({
    className: 'export-folder-row',
    label: 'Output location',
    value: 'Managed project output folder',
    description: 'Verified builds stay with this project and appear here when complete.',
    iconClass: 'far fa-folder-open',
  });
  fields.append(formats, chapterMode, folder.node);
  const validationNode = exportPanel('export-validation-panel', 'Final validation', 'Checking…', 'Preflight');
  const validation = document.createElement('section');
  validation.className = 'export-validation';
  node.append(fields, technicalDetails(aggregate));
  validationNode.append(validation);
  renderValidation(validation, aggregate, getMetadata(), selectedFormat);
  return {
    node,
    validationNode,
    controls,
    refreshValidation: () => renderValidation(validation, aggregate, getMetadata(), selectedFormat),
  };
}
