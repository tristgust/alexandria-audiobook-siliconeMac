'use strict';

import {
  produceAudioTransport, produceDuration, produceInitials,
  produceState, produceText,
} from './produce_model.js';
import { isProduceSectionEligible } from './produce_sections.js';

const UI = globalThis.AlexandriaUI;

function createStatusControl({ chunk, aggregate, actions }) {
  if (!chunk.regenerate_action) {
    return UI.status({ ...produceState(chunk.state), domain: 'audio', value: chunk.state });
  }
  const label = chunk.state === 'ready' ? 'Generate'
    : chunk.state === 'failed' ? 'Retry' : chunk.regenerate_action.label || 'Regenerate';
  const control = UI.button({
    label,
    variant: 'quiet',
    size: 'compact',
    disabled: actions.busy || aggregate.process?.running,
    attributes: {
      'data-produce-row-action': '',
      'data-status-value': chunk.state || '',
      'aria-label': `${label} audio for chunk ${Number(chunk.index) + 1}`,
    },
    onClick: () => actions.execute('selected', [chunk.chunk_id]),
  });
  control.classList.add('audio-row__status-pill');
  return control;
}

export function createProduceAudioRow({
  chunk, rowIndex, aggregate, selected, actions, shell, batchSelected,
  onClick, onKeydown,
}) {
  const row = document.createElement('li');
  row.className = 'audio-row';
  row.dataset.audioRow = '';
  row.dataset.audioState = chunk.state || '';
  row.dataset.chunkId = chunk.chunk_id;
  row.dataset.active = String(chunk.chunk_id === selected?.chunk_id);
  row.dataset.batchSelected = String(batchSelected);
  row.setAttribute('role', 'option');
  row.setAttribute('aria-selected', String(batchSelected));
  row.setAttribute('aria-label', `${chunk.character_name || chunk.speaker || 'Narrator'}, ${produceState(chunk.state).label}${batchSelected ? ', selected for batch audio action' : ''}`);
  row.tabIndex = chunk.chunk_id === selected?.chunk_id || (!selected && rowIndex === 0) ? 0 : -1;

  const name = chunk.character_name || chunk.speaker || 'Narrator';
  const identity = document.createElement('div');
  identity.className = 'audio-row__identity';
  const copy = document.createElement('div');
  copy.className = 'audio-row__identity-copy';
  copy.append(
    produceText('strong', 'audio-row__speaker', name),
    produceText('span', 'metadata', `Chunk ${Number(chunk.index) + 1}`),
  );
  identity.append(
    UI.monogram({ initials: produceInitials(name), label: `Monogram for ${name}` }),
    copy,
  );

  const excerpt = document.createElement('div');
  excerpt.className = 'audio-row__excerpt';
  excerpt.append(
    produceText('span', '', chunk.text_excerpt || chunk.text, 'No Script text'),
    produceText(
      'span', 'metadata audio-row__direction-inline',
      chunk.delivery_direction, 'No delivery direction',
    ),
  );
  row.append(
    identity,
    excerpt,
    produceText('span', 'audio-row__direction', chunk.delivery_direction, 'No delivery direction'),
    produceText('span', 'timecode audio-row__duration', produceDuration(chunk.duration_ms)),
    produceAudioTransport({ chunk, shell }),
    createStatusControl({ chunk, aggregate, actions }),
  );

  const canSelect = isProduceSectionEligible(chunk);
  row.addEventListener('click', (event) => {
    if (event.target.closest('button, input, label, [role="menu"]')) return;
    onClick({ chunk, row, event, canSelect });
  });
  row.addEventListener('keydown', (event) => {
    onKeydown({ chunk, row, event, canSelect });
  });
  return row;
}
