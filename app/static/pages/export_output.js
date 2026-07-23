'use strict';

import {
  EXPORT_FORMATS, exportPanel, exportText, exportWords,
} from './export_model.js';

const UI = globalThis.AlexandriaUI;

function validationMark(ok, label) {
  const mark = document.createElement('span');
  mark.className = 'export-validation__mark';
  mark.dataset.tone = ok ? 'success' : 'error';
  mark.append(
    UI.icon(ok ? 'check' : 'blocked'),
    exportText('span', 'visually-hidden', `${label}: ${ok ? 'ready' : 'needs attention'}`),
  );
  return mark;
}

function renderValidation(node, aggregate, metadata) {
  node.replaceChildren(exportText('h3', 'entity-title', 'Final validation'));
  const destinations = new Set((aggregate.blockers || []).map((blocker) => blocker.native_destination));
  [
    ['Script integrity', !destinations.has('script')],
    ['Voice assignments', !destinations.has('cast')],
    ['Audio generation', !destinations.has('produce')],
    ['Chapter structure', Boolean(aggregate.chapters?.length)],
    ['Metadata & credits', Boolean(metadata.title && metadata.author)],
    ['Duration consistency', !aggregate.selected_outputs?.some((output) => output.state === 'invalid')],
  ].forEach(([label, ok]) => {
    const row = document.createElement('div');
    row.className = 'export-validation__row';
    row.append(exportText('span', '', label), validationMark(ok, label));
    node.append(row);
  });
}

function technicalDetails(aggregate) {
  const body = document.createElement('div');
  body.className = 'export-output-list';
  Object.values(aggregate.outputs || {}).forEach((output) => {
    const row = document.createElement('div');
    row.className = 'export-output-row';
    row.append(
      exportText('span', '', `${exportWords(output.format)} · ${output.filename}`),
      exportText('span', 'metadata', exportWords(output.state || 'missing')),
    );
    body.append(row);
  });
  body.append(exportText(
    'p',
    'metadata',
    'Outputs are written to the current project output folder and verified before becoming current.',
  ));
  return UI.disclosure({ label: 'Technical Details', content: body });
}

function canonicalFilename(aggregate, selectedFormat) {
  return aggregate.plan?.output_filenames?.[selectedFormat]
    || aggregate.outputs?.[selectedFormat]?.filename
    || `audiobook${EXPORT_FORMATS.find((format) => format.value === selectedFormat)?.extension || ''}`;
}

export function createExportOutput({
  aggregate, selectedFormat, getMetadata, onFormatChange, onChange,
}) {
  const node = exportPanel('export-output', 'Output');
  const controls = {};
  const fields = document.createElement('div');
  fields.className = 'export-output-fields';
  const filename = UI.field({
    id: 'export-filename',
    label: 'Output filename',
    value: canonicalFilename(aggregate, selectedFormat),
    readOnly: true,
    message: 'The current exporter uses this canonical verified filename.',
  });
  controls.filename = filename.querySelector('.field__control');
  fields.append(filename);

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
    })),
  });
  group.addEventListener('change', (event) => {
    if (!event.target.matches('input[type="radio"]')) return;
    const nextFormat = event.target.value;
    controls.filename.value = canonicalFilename(aggregate, nextFormat);
    onFormatChange(nextFormat);
  });
  formats.append(group);

  const chapterMode = UI.field({
    id: 'export-chapter-mode',
    label: 'Chapter grouping',
    kind: 'select',
    value: aggregate.chapter_mode || 'smart',
    options: [
      { value: 'smart', label: 'Smart chapters' },
      { value: 'per_chunk', label: 'One chapter per chunk' },
      { value: 'none', label: 'No embedded chapters' },
    ],
  });
  controls.chapterMode = chapterMode.querySelector('select');
  controls.chapterMode.addEventListener('change', onChange);
  const folder = UI.field({
    label: 'Output folder',
    value: 'Current project output folder',
    readOnly: true,
    message: 'The active backend writes verified outputs inside the current project.',
  });
  fields.append(formats, chapterMode, folder);
  const validation = document.createElement('section');
  validation.className = 'export-validation';
  renderValidation(validation, aggregate, getMetadata());
  node.append(fields, validation, technicalDetails(aggregate));
  return {
    node,
    controls,
    refreshValidation: () => renderValidation(validation, aggregate, getMetadata()),
  };
}
