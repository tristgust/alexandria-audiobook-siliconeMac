'use strict';

export { groupProduceChunks } from './produce_sections.js';

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
  const reasonCopy = {
    audio_not_generated: 'This chunk has not been generated yet.',
    voice_missing_or_invalid: 'A valid production Voice is required in Cast.',
    audio_invalidated: 'The current audio no longer matches the Script, direction, pause, or Voice.',
  };
  if (chunk.reason && reasonCopy[chunk.reason]) return reasonCopy[chunk.reason];
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
  const available = Boolean(chunk.audio?.available && chunk.audio?.url);
  const stale = chunk.state === 'stale';
  const root = document.createElement('div');
  root.className = detailed ? 'produce-inspector__transport' : 'audio-row__transport';
  if (!available) {
    if (!detailed) {
      root.setAttribute('aria-hidden', 'true');
      return root;
    }
    root.append(produceText(
      'span', 'metadata',
      stale ? 'Stale audio unavailable' : 'Not generated',
    ));
    return root;
  }
  const playLabel = `Play ${stale ? 'stale ' : ''}audio for ${chunk.character_name || chunk.speaker || 'chunk'}`;
  const play = UI.compactPlay({
    state: 'ready',
    labels: {
      ready: playLabel,
      paused: playLabel,
      playing: `Pause audio for ${chunk.character_name || chunk.speaker || 'chunk'}`,
      failed: `Retry audio for ${chunk.character_name || chunk.speaker || 'chunk'}`,
    },
    icons: {
      ready: 'fas fa-play',
      paused: 'fas fa-play',
      playing: 'fas fa-pause',
      failed: 'fas fa-rotate-right',
      loading: 'fas fa-spinner fa-spin',
    },
  });
  play.classList.add('produce-play');
  play.addEventListener('click', (event) => {
    event.stopPropagation();
    const player = shell.player.set({
      state: 'playing',
      src: chunk.audio?.url || null,
      position: 0,
      duration: Math.max(.01, (Number(chunk.duration_ms) || 1000) / 1000),
      title: `${chunk.character_name || chunk.speaker || 'Narrator'} · Chunk ${chunk.index}`,
      subtitle: stale ? 'Stale sample · regenerate before export' : `${produceState(chunk.state).label} audio`,
    });
    const syncPlayState = (nextState) => play.setPlaybackState(
      nextState === 'playing' ? 'playing' : nextState === 'failed' ? 'failed' : 'paused',
    );
    syncPlayState(player?.dataset.state || 'paused');
    player?.addEventListener('alexandriaplayerchange', (playerEvent) => {
      if (play.isConnected) syncPlayState(playerEvent.detail?.state || player?.dataset.state || 'paused');
    });
  });
  root.append(play, UI.waveform({
    value: 0,
    maximum: Math.max(1, Math.round((Number(chunk.duration_ms) || 1000) / 1000)),
    label: `Audio position for chunk ${chunk.index}`,
    disabled: false,
  }));
  if (detailed) root.append(produceText('span', 'metadata', stale
    ? 'Stale sample' : `Duration ${produceDuration(chunk.duration_ms)}`));
  return root;
}
