'use strict';

const UI = globalThis.AlexandriaUI;

export const PRODUCE_FILTERS = Object.freeze([
  ['ready', 'Ready to generate'],
  ['needs_listening', 'Needs listening'],
  ['failed', 'Failed'],
  ['stale', 'Stale'],
  ['current', 'Current'],
]);

export function produceText(tag, className, value, empty = 'Not available') {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null || value === '' ? empty : String(value);
  return node;
}

export function produceWords(value) {
  return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function produceInitials(value) {
  return String(value || 'Narrator').trim().split(/\s+/).filter(Boolean).slice(0, 2)
    .map((part) => part[0]?.toUpperCase() || '').join('') || 'N';
}

export function produceDuration(milliseconds) {
  if (!Number.isFinite(Number(milliseconds))) return 'Not generated';
  const seconds = Math.max(0, Math.round(Number(milliseconds) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

export function produceState(state) {
  return {
    ready: { label: 'Ready to generate', tone: 'information' },
    generating: { label: 'Generating', tone: 'information' },
    needs_listening: { label: 'Needs listening', tone: 'warning' },
    needs_review: { label: 'Needs listening', tone: 'warning' },
    current: { label: 'Current', tone: 'success' },
    stale: { label: 'Stale', tone: 'warning' },
    failed: { label: 'Failed', tone: 'error' },
    missing_voice: { label: 'Blocked', tone: 'error' },
  }[state] || { label: produceWords(state || 'Not started'), tone: 'neutral' };
}

export function attentionCount(counts = {}) {
  return ['ready', 'stale', 'failed', 'needs_listening', 'needs_review', 'missing_voice']
    .reduce((total, key) => total + (Number(counts[key]) || 0), 0);
}

export function produceReason(chunk) {
  if (chunk.reason) return produceWords(chunk.reason);
  if (chunk.state === 'stale') return 'A Script, direction, pause, or Voice dependency changed.';
  if (chunk.state === 'failed') return 'The most recent generation attempt failed.';
  if (chunk.state === 'missing_voice') return 'A valid production Voice is required in Cast.';
  if (chunk.state === 'needs_listening' || chunk.state === 'needs_review') {
    return 'Listen to the generated sample and record the review outcome.';
  }
  if (chunk.state === 'ready') return 'No current audio has been generated.';
  return 'The generated audio matches the current production dependencies.';
}

function headingChunk(chunk) {
  const text = String(chunk.text || chunk.text_excerpt || '').trim();
  const direction = String(chunk.delivery_direction || '').toLowerCase();
  return text.length > 0 && text.length <= 90
    && (direction.includes('announce') || direction.includes('heading')
      || /^(chapter|prologue|epilogue|part|book|cover)\b/i.test(text));
}

export function groupProduceChunks(chunks) {
  const groups = [];
  let current = null;
  chunks.forEach((chunk) => {
    const explicit = chunk.group_label || chunk.chapter?.name || chunk.chapter_title
      || chunk.scene?.name || chunk.scene_title;
    const label = explicit || (headingChunk(chunk) ? chunk.text || chunk.text_excerpt : null);
    if (!current || (label && current.label !== label)) {
      current = { label: label || (groups.length ? `Audio section ${groups.length + 1}` : 'Opening'), chunks: [] };
      groups.push(current);
    }
    current.chunks.push(chunk);
  });
  return groups;
}

export function produceStyle() {
  const existing = document.querySelector('link[data-page-style="produce-export"]');
  if (existing) return { node: existing, owned: false };
  const node = document.createElement('link');
  node.rel = 'stylesheet';
  node.href = '/static/styles/pages/produce_export.css';
  node.dataset.pageStyle = 'produce-export';
  document.head.append(node);
  return { node, owned: true };
}

export function waitForProduceStyle(node, signal) {
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

export function produceAudioTransport({ chunk, shell, detailed = false }) {
  const available = Boolean(chunk.audio?.available || chunk.audio?.stale_audio_available);
  const stale = chunk.state === 'stale';
  const root = document.createElement('div');
  root.className = detailed ? 'produce-inspector__transport' : 'audio-row__transport';
  const play = UI.compactPlay({
    state: available ? 'ready' : chunk.state === 'failed' ? 'failed' : 'disabled',
    label: available
      ? `Play ${stale ? 'stale ' : ''}audio for ${chunk.character_name || chunk.speaker || 'chunk'}`
      : `Audio unavailable for chunk ${chunk.index}`,
  });
  if (available) play.addEventListener('click', (event) => {
    event.stopPropagation();
    shell.player.set({
      state: 'playing',
      title: `${chunk.character_name || chunk.speaker || 'Narrator'} · Chunk ${chunk.index}`,
      subtitle: stale ? 'Stale sample · regenerate before export' : `${produceState(chunk.state).label} audio`,
    });
  });
  root.append(play, UI.waveform({
    value: 0,
    maximum: Math.max(1, Math.round((Number(chunk.duration_ms) || 1000) / 1000)),
    label: `Audio position for chunk ${chunk.index}`,
    disabled: !available,
  }));
  if (detailed) root.append(produceText('span', 'metadata', available
    ? stale ? 'Stale sample' : `Duration ${produceDuration(chunk.duration_ms)}`
    : 'No generated audio'));
  return root;
}
