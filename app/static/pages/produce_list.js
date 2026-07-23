'use strict';

import {
  groupProduceChunks, produceAudioTransport, produceDuration, produceInitials,
  produceState, produceText,
} from './produce_model.js';

const UI = globalThis.AlexandriaUI;
const INITIAL_LIMIT = 150;
const BATCH_SIZE = 150;

export function createProduceList({
  content, owner, shell, actions, getAggregate, getSelected, setSelected,
  onSelectionChange, onReviewScript, projectId,
}) {
  let visibleLimit = INITIAL_LIMIT;
  let rowPopovers = [];

  const cleanupPopovers = () => {
    rowPopovers.forEach((popover) => popover.popoverCleanup?.());
    rowPopovers = [];
  };

  const selectChunk = (chunk, row, focus = false) => {
    setSelected(chunk);
    getAggregate().selected_chunk_id = chunk.chunk_id;
    content.querySelectorAll('[data-audio-row]').forEach((item) => {
      const current = item === row;
      item.setAttribute('aria-selected', String(current));
      item.tabIndex = current ? 0 : -1;
    });
    if (focus) row?.focus();
    onSelectionChange?.();
  };

  const rowFor = (chunk, rowIndex) => {
    const aggregate = getAggregate();
    const selected = getSelected();
    const row = document.createElement('li');
    row.className = 'audio-row';
    row.dataset.audioRow = '';
    row.dataset.audioState = chunk.state || '';
    row.dataset.chunkId = chunk.chunk_id;
    row.setAttribute('role', 'option');
    row.setAttribute('aria-selected', String(chunk.chunk_id === selected?.chunk_id));
    row.setAttribute('aria-label', `${chunk.character_name || chunk.speaker || 'Narrator'}, ${produceState(chunk.state).label}`);
    row.tabIndex = chunk.chunk_id === selected?.chunk_id || (!selected && rowIndex === 0) ? 0 : -1;
    const identity = document.createElement('div');
    identity.className = 'audio-row__identity';
    const name = chunk.character_name || chunk.speaker || 'Narrator';
    const copy = document.createElement('div');
    copy.className = 'audio-row__identity-copy';
    copy.append(
      produceText('strong', 'audio-row__speaker', name),
      produceText('span', 'metadata', `Chunk ${Number(chunk.index) + 1}`),
    );
    identity.append(UI.monogram({ initials: produceInitials(name), label: `Monogram for ${name}` }), copy);
    const excerpt = document.createElement('div');
    excerpt.className = 'audio-row__excerpt';
    excerpt.append(
      produceText('span', '', chunk.text_excerpt || chunk.text, 'No Script text'),
      produceText('span', 'metadata audio-row__direction-inline', chunk.delivery_direction, 'No delivery direction'),
    );
    const direction = produceText('span', 'audio-row__direction', chunk.delivery_direction, 'No delivery direction');
    const time = produceText('span', 'timecode audio-row__duration', produceDuration(chunk.duration_ms));
    const state = UI.status({ ...produceState(chunk.state), domain: 'audio', value: chunk.state });
    const opener = UI.iconButton({
      name: 'more', size: 'compact',
      label: `Actions for chunk ${Number(chunk.index) + 1}`,
      disabled: actions.busy || aggregate.process?.running || !chunk.regenerate_action,
    });
    const popover = UI.popover({
      opener,
      label: `Chunk ${Number(chunk.index) + 1} actions`,
      items: chunk.regenerate_action ? [{
        label: chunk.regenerate_action.label || 'Regenerate',
        onSelect: () => actions.execute('selected', [chunk.chunk_id]),
      }] : [{ label: 'Regeneration unavailable' }],
    });
    rowPopovers.push(popover);
    row.append(
      identity, excerpt, direction, time,
      produceAudioTransport({ chunk, shell }), state, popover,
    );
    row.addEventListener('click', (event) => {
      if (event.target.closest('button, [role="menu"]')) return;
      selectChunk(chunk, row);
    });
    row.addEventListener('keydown', (event) => {
      if (!['ArrowUp', 'ArrowDown', 'Home', 'End', 'Enter'].includes(event.key)) return;
      event.preventDefault();
      if (event.key === 'Enter') {
        selectChunk(chunk, row);
        return;
      }
      const rows = [...content.querySelectorAll('[data-audio-row]')];
      const current = rows.indexOf(row);
      const target = event.key === 'Home' ? rows[0] : event.key === 'End' ? rows.at(-1)
        : rows[(current + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length];
      const targetChunk = getAggregate().chunks.find((item) => item.chunk_id === target?.dataset.chunkId);
      if (target && targetChunk) selectChunk(targetChunk, target, true);
    });
    return row;
  };

  const groupNode = (group, startIndex) => {
    const section = document.createElement('section');
    section.className = 'produce-chapter-group';
    section.setAttribute('aria-label', group.label);
    const heading = document.createElement('header');
    heading.className = 'produce-chapter-heading';
    heading.append(
      produceText('span', 'utility-heading', 'Chapter or scene'),
      produceText('h2', 'entity-title', group.label),
      produceText('span', 'metadata', `${group.chunks.length.toLocaleString()} chunks`),
    );
    const list = document.createElement('ul');
    list.className = 'audio-table';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', `${group.label} audio chunks`);
    group.chunks.forEach((chunk, index) => list.append(rowFor(chunk, startIndex + index)));
    section.append(heading, list);
    return section;
  };

  const render = ({ reset = false } = {}) => {
    if (reset) visibleLimit = INITIAL_LIMIT;
    cleanupPopovers();
    content.replaceChildren();
    const aggregate = getAggregate();
    const selected = getSelected();
    const chunks = (aggregate.chunks || []).filter(actions.matches);
    if (!chunks.length) {
      content.append(UI.emptyState({
        title: aggregate.chunks?.length ? 'No chunks match these filters' : 'No audio chunks are available',
        body: aggregate.chunks?.length
          ? 'Clear the filters to restore the production list.'
          : 'Review Script and Cast before generating audio.',
        action: aggregate.chunks?.length
          ? UI.button({ label: 'Clear filters', variant: 'quiet', onClick: actions.clearFilters })
          : UI.button({ label: 'Review Script', variant: 'secondary', onClick: onReviewScript }),
      }));
      owner.dataset.pageState = aggregate.chunks?.length ? 'ready' : 'empty';
      return;
    }
    const visible = chunks.slice(0, visibleLimit);
    if (selected && actions.matches(selected) && !visible.some((chunk) => chunk.chunk_id === selected.chunk_id)) {
      const pinned = chunks.find((chunk) => chunk.chunk_id === selected.chunk_id);
      if (pinned) visible.push(pinned);
    }
    const columns = document.createElement('div');
    columns.className = 'audio-table__header';
    columns.setAttribute('aria-hidden', 'true');
    ['Character', 'Text excerpt', 'Delivery direction', 'Duration', 'Audio', 'State', 'Action']
      .forEach((label) => columns.append(produceText('span', '', label)));
    content.append(columns);
    let rowIndex = 0;
    groupProduceChunks(visible).forEach((group) => {
      content.append(groupNode(group, rowIndex));
      rowIndex += group.chunks.length;
    });
    const footer = document.createElement('div');
    footer.className = 'collection-footer';
    footer.dataset.produceCollectionFooter = '';
    footer.append(produceText('span', 'metadata',
      `Showing ${visible.length.toLocaleString()} of ${chunks.length.toLocaleString()} chunks`));
    if (visibleLimit < chunks.length) footer.append(UI.button({
      label: `Load ${Math.min(BATCH_SIZE, chunks.length - visibleLimit).toLocaleString()} more`,
      variant: 'secondary', size: 'compact', attributes: { 'data-produce-load-more': '' },
      onClick: () => { visibleLimit += BATCH_SIZE; render(); },
    }));
    content.append(footer);
    owner.dataset.pageState = aggregate.process?.running ? 'running'
      : aggregate.state === 'blocked' ? 'blocked' : chunks.length > 20 ? 'dense' : 'ready';
  };

  return Object.freeze({ render, cleanup: cleanupPopovers });
}
