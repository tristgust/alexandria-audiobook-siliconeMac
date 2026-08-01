'use strict';

const UI = globalThis.AlexandriaUI;

export const REDUNDANT_PROVENANCE = new Set([
  'name', 'id', 'artifact_id', 'path', 'relative_path', 'hash', 'source_filename',
]);

export const PROVENANCE_LABELS = Object.freeze({
  updated_at_utc: 'Last updated',
  created_at_utc: 'Created',
  total_chunks: 'Total chunks',
  audio_file_count: 'Audio files',
  current_chunk_count: 'Current chunks',
  pending_chunk_count: 'Pending chunks',
  stale_chunk_count: 'Stale chunks',
  failed_chunk_count: 'Failed chunks',
  sample_count: 'Samples',
  production_assignment_supported: 'Production assignment',
  manual_audio_review_status: 'Listening review',
});

const ARTIFACT_GROUPS = Object.freeze({
  source_book: 'Project material',
  production_audio: 'Project material',
  export_output: 'Project material',
  built_in: 'Voice sources',
  designed: 'Voice sources',
  supplied_recording: 'Voice sources',
  instruction_controlled: 'Voice sources',
  clone_reference: 'Voice sources',
  voice_preparation_project: 'Voice development',
  preparer_output: 'Voice development',
  dataset_builder_project: 'Voice development',
  lora_dataset: 'Voice development',
  training_dataset: 'Voice development',
  lora_adapter: 'Voice development',
  trained_adapter: 'Voice development',
});

export const ARTIFACT_GROUP_ORDER = Object.freeze([
  'Project material', 'Voice sources', 'Voice development', 'Other artifacts',
]);

export const words = (value, fallback = '') => String(value || fallback)
  .replaceAll('_', ' ')
  .replace(/\b\w/g, (letter) => letter.toUpperCase())
  .replace(/\bLora\b/g, 'LoRA')
  .replace(/\bUtc\b/g, 'UTC');

export function applyLibraryPayload(payload, select) {
  const artifacts = Array.isArray(payload?.artifacts) ? payload.artifacts : [];
  const presentGroups = new Set(artifacts.map(artifactGroup));
  const options = [
    { value: 'all', label: 'Everything' },
    ...ARTIFACT_GROUP_ORDER
      .filter((group) => presentGroups.has(group))
      .map((group) => ({ value: group, label: group })),
  ];
  select.replaceChildren(...options.map((entry) => {
    const option = document.createElement('option');
    option.value = entry.value;
    option.textContent = entry.label;
    return option;
  }));
  return artifacts;
}

export function artifactName(artifact) {
  const raw = String(artifact?.name || 'Unnamed artifact')
    .replace(/_\d{10,}(?=\.[^.]+$|$)/, '')
    .replace(/\.(?:txt|md|epub|json|zip|wav|mp3|m4b)$/i, '');
  const parts = raw.split(/[_-]+/).filter(Boolean);
  while (parts.length > 1 && /\d/.test(parts[0])) parts.shift();
  return parts.join(' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/(voice|attempt|attention|lines|pilot)/gi, ' $1 ')
    .replace(/([A-Za-z])(\d+)/g, '$1 $2')
    .replace(/\s+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
    .replace(/\bDw\s+(\d+)/g, 'DW$1')
    .replace(/\bR\s+(\d+)/g, 'R$1')
    .replace(/\bLora\b/g, 'LoRA')
    .trim() || 'Unnamed artifact';
}

export function uniqueArtifactLabels(items) {
  const totals = new Map();
  const kindTotals = new Map();
  items.forEach((item) => {
    const label = artifactName(item);
    const kind = words(item.kind, 'Artifact');
    totals.set(label, (totals.get(label) || 0) + 1);
    kindTotals.set(`${label}\u0000${kind}`, (kindTotals.get(`${label}\u0000${kind}`) || 0) + 1);
  });
  const kindSeen = new Map();
  return new Map(items.map((item) => {
    const label = artifactName(item);
    if (totals.get(label) === 1) return [item, label];
    const kind = words(item.kind, 'Artifact');
    const key = `${label}\u0000${kind}`;
    const next = (kindSeen.get(key) || 0) + 1;
    kindSeen.set(key, next);
    const ordinal = kindTotals.get(key) > 1 ? ` ${next}` : '';
    return [item, `${label} — ${kind}${ordinal}`];
  }));
}

export function artifactMeta(artifact) {
  const name = artifactName(artifact).toLocaleLowerCase();
  const kind = words(artifact?.kind, 'Artifact');
  const parts = [];
  if (name !== kind.toLocaleLowerCase()) parts.push(kind);
  parts.push(words(artifact?.state, 'Unknown'));
  return parts.join(' · ');
}

export const artifactGroup = (artifact) => ARTIFACT_GROUPS[artifact?.kind] || 'Other artifacts';

export const artifactPresentation = (artifact) => ({
  source_book: ['Source book', 'fas fa-book-open'],
  production_audio: ['Production audio', 'fas fa-wave-square'],
  export_output: ['Finished output', 'fas fa-arrow-up-from-bracket'],
  built_in: ['Built-in Voice', 'fas fa-microphone-lines'],
  designed: ['Designed Voice', 'fas fa-wand-magic-sparkles'],
  supplied_recording: ['Supplied recording', 'fas fa-wave-square'],
  instruction_controlled: ['Instruction-controlled Voice', 'fas fa-sliders'],
  clone_reference: ['Clone reference', 'fas fa-wave-square'],
  dataset_builder_project: ['Dataset Builder project', 'fas fa-database'],
  lora_dataset: ['LoRA dataset', 'fas fa-database'],
  training_dataset: ['Training dataset', 'fas fa-database'],
  lora_adapter: ['LoRA adapter', 'fas fa-layer-group'],
  trained_adapter: ['Trained adapter', 'fas fa-layer-group'],
  preparer_output: ['Prepared audio', 'far fa-file-audio'],
  voice_preparation_project: ['Voice preparation', 'fas fa-sliders'],
}[artifact?.kind] || [words(artifact?.kind, 'Artifact'), 'far fa-file']);

export function artifactMark(artifact, className = 'library-artifact__mark') {
  const [, iconClass] = artifactPresentation(artifact);
  const mark = document.createElement('span');
  mark.className = className;
  mark.setAttribute('aria-hidden', 'true');
  mark.append(UI.iconFromClass(iconClass, 'document'));
  return mark;
}

export function text(tag, className, value) {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

export function ownerForLibrary(route) {
  const owner = document.createElement('article');
  owner.className = 'supporting-page library-page';
  owner.dataset.routeOwner = route.path;
  owner.dataset.page = route.path;
  const heading = text('span', 'visually-hidden', 'Library');
  heading.id = 'library-page-heading';
  heading.dataset.pageHeading = '';
  owner.append(heading);
  return owner;
}

export function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes.toLocaleString()} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024).toLocaleString()} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function provenanceValue(label, value) {
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number') return value.toLocaleString();
  if (/(?:_at|_utc)$/.test(label)) {
    const date = new Date(value);
    if (!Number.isNaN(date.getTime())) {
      return new Intl.DateTimeFormat(undefined, {
        dateStyle: 'medium', timeStyle: 'short',
      }).format(date);
    }
  }
  if (label === 'status' || label.endsWith('_status')) return words(value);
  return String(value);
}
