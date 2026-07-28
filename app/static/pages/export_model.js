'use strict';

export const EXPORT_FORMATS = Object.freeze([
  { value: 'm4b', label: 'M4B audiobook', extension: '.m4b', description: 'Metadata, cover, and chapters.' },
  { value: 'mp3', label: 'MP3 audio file', extension: '.mp3', description: 'One compatible audio master.' },
  { value: 'audacity', label: 'Audacity project package', extension: '.zip', description: 'Editable project and audio assets.' },
  {
    value: 'chapter_separated',
    label: 'Separate chapter files',
    extension: '',
    disabled: true,
    description: 'Not yet available from the verified Export builder.',
  },
]);

export function exportText(tag, className, value, empty = 'Not available') {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null || value === '' ? empty : String(value);
  return node;
}

export function exportWords(value) {
  return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function exportDisplayFilename(value) {
  const filename = String(value || '').trim();
  if (!filename) return '';
  return {
    'cloned_audiobook.mp3': 'audiobook.mp3',
    'audacity_export.zip': 'audiobook-audacity.zip',
  }[filename] || filename;
}

export function exportClock(milliseconds) {
  const total = Math.max(0, Math.round((Number(milliseconds) || 0) / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
    : `${minutes}:${String(seconds).padStart(2, '0')}`;
}

export function exportBytes(value) {
  const size = Number(value);
  if (!Number.isFinite(size) || size <= 0) return 'Calculated during build';
  if (size >= 1_000_000_000) return `${(size / 1_000_000_000).toFixed(1)} GB`;
  if (size >= 1_000_000) return `${Math.round(size / 1_000_000)} MB`;
  return `${Math.round(size / 1000)} KB`;
}

export function exportPanel(className, title, metadata = '', eyebrow = '') {
  const node = document.createElement('section');
  node.className = `export-panel ${className}`;
  const heading = document.createElement('header');
  heading.className = 'export-panel__heading';
  if (eyebrow) {
    const copy = document.createElement('div');
    copy.append(
      exportText('span', 'utility-heading', eyebrow),
      exportText('h2', 'section-title', title),
    );
    heading.append(copy);
  } else {
    heading.append(exportText('h2', 'section-title', title));
  }
  if (metadata) heading.append(exportText('span', 'metadata', metadata));
  node.append(heading);
  return node;
}

export function exportStyle() {
  const existing = document.querySelector('link[data-page-style="produce-export"]');
  if (existing) return { node: existing, owned: false };
  const node = document.createElement('link');
  node.rel = 'stylesheet';
  node.href = '/static/styles/pages/produce_export.css';
  node.dataset.pageStyle = 'produce-export';
  document.head.append(node);
  return { node, owned: true };
}

export function waitForExportStyle(node, signal) {
  if (node.sheet) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      node.removeEventListener('load', loaded);
      node.removeEventListener('error', failed);
      signal.removeEventListener('abort', aborted);
    };
    const loaded = () => { cleanup(); resolve(); };
    const failed = () => { cleanup(); reject(new Error('Produce and Export styles could not load.')); };
    const aborted = () => { cleanup(); reject(signal.reason || new DOMException('Navigation canceled', 'AbortError')); };
    node.addEventListener('load', loaded, { once: true });
    node.addEventListener('error', failed, { once: true });
    signal.addEventListener('abort', aborted, { once: true });
  });
}
