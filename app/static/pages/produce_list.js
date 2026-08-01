'use strict';

import { produceText } from './produce_model.js';
import {
  buildProduceSections, groupProduceChunks, resolveProduceSectionBatch,
} from './produce_sections.js';
import {
  createProduceSectionAction,
} from './produce_section_controls.js';
import { createProduceAudioRow } from './produce_audio_row.js';
import { createProduceBatchSelection } from './produce_batch_selection.js';
import { createProduceColumnHeader } from './produce_column_resizer.js';
import { createProduceListSelection } from './produce_list_selection.js';

const UI = globalThis.AlexandriaUI;
const chunkPageSize = () => {
  const layout = document.querySelector('.app-shell')?.dataset.layout;
  return layout === 'narrow' ? 30 : layout === 'compact' ? 75 : 120;
};

export function createProduceList({
  content, visibleSummary, owner, shell, actions, getAggregate, getSelected, setSelected,
  onSelectionChange, onReviewScript, projectId,
}) {
  let visibleLimit = chunkPageSize();
  let revealedSelectionId = null;
  let sectionByChunkId = new Map();
  let sectionsComplete = false;

  const selection = createProduceListSelection({
    content,
    actions,
    getAggregate,
    setSelected,
    onSelectionChange,
    onRender: () => render(),
  });

  const rowFor = (chunk, rowIndex) => {
    const aggregate = getAggregate();
    return createProduceAudioRow({
      chunk,
      rowIndex,
      aggregate,
      selected: getSelected(),
      actions,
      shell,
      batchSelected: selection.has(chunk.chunk_id),
      onClick: selection.onRowClick,
      onKeydown: selection.onRowKeydown,
    });
  };

  const groupNode = (group, fullSection, startIndex) => {
    const section = document.createElement('section');
    section.className = 'produce-chapter-group';
    section.setAttribute('aria-label', group.label);
    const heading = document.createElement('header');
    heading.className = 'produce-chapter-heading';
    const headingActions = document.createElement('div');
    headingActions.className = 'produce-chapter-heading__actions';
    headingActions.append(produceText(
      'span', 'metadata', `${fullSection.chunks.length.toLocaleString()} chunks`,
    ));
    if (fullSection.eligibleIds.some(selection.has)) {
      headingActions.append(UI.button({
        label: 'Clear selection', variant: 'quiet', size: 'compact',
        attributes: { 'data-produce-clear-selection': '' },
        onClick: () => selection.clearSection(fullSection.eligibleIds),
      }));
    }
    headingActions.append(createProduceSectionAction({
      section: fullSection,
      selectedIds: selection.ids(),
      collectionComplete: sectionsComplete,
      disabled: actions.busy || getAggregate().process?.running
        || getAggregate().process?.cancel_requested,
      onGenerate: (section) => actions.execute(
        'selected', resolveProduceSectionBatch(section, selection.ids()),
      ),
    }));
    heading.append(
      produceText('span', 'utility-heading', 'Chapter or scene'),
      produceText('h2', 'entity-title', group.label),
      headingActions,
    );
    const list = document.createElement('ul');
    list.className = 'audio-table';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-multiselectable', 'true');
    list.setAttribute('aria-label', `${group.label} audio chunks`);
    group.chunks.forEach((chunk, index) => list.append(rowFor(chunk, startIndex + index)));
    section.append(heading, list);
    return section;
  };

  const render = ({ reset = false } = {}) => {
    if (reset) visibleLimit = chunkPageSize();
    content.replaceChildren();
    const aggregate = getAggregate();
    const selected = getSelected();
    const sectionModel = buildProduceSections(
      aggregate.chunks || [], aggregate.all_chunk_count ?? aggregate.chunks?.length ?? 0,
    );
    sectionsComplete = sectionModel.complete;
    selection.prune(sectionModel.sections);
    sectionByChunkId = new Map(sectionModel.sections.flatMap((section) => (
      section.chunks.map((chunk) => [chunk.chunk_id, section])
    )));
    const chunks = (aggregate.chunks || []).filter(actions.matches);
    const selectedCount = selection.ids().size;
    visibleSummary.textContent = [
      `${chunks.length.toLocaleString()} chunk${chunks.length === 1 ? '' : 's'}`,
      selectedCount ? `${selectedCount.toLocaleString()} selected` : '',
    ].filter(Boolean).join(' · ');
    const batchSelection = createProduceBatchSelection({
      chunks: aggregate.chunks || [],
      selectedIds: selection.ids(),
      disabled: actions.busy || aggregate.process?.running
        || aggregate.process?.cancel_requested,
      onExecute: (chunkIds) => actions.execute('selected', chunkIds),
      onClear: selection.clearAll,
    });
    if (batchSelection) content.append(batchSelection);
    if (!chunks.length) {
      content.append(UI.emptyState({
        iconClass: aggregate.chunks?.length ? 'fas fa-filter-circle-xmark' : 'fas fa-wave-square',
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
    const columns = createProduceColumnHeader(content);
    content.append(columns);
    let rowIndex = 0;
    groupProduceChunks(visible).forEach((group) => {
      const fullSection = sectionByChunkId.get(group.chunks[0]?.chunk_id) || group;
      content.append(groupNode(group, fullSection, rowIndex));
      rowIndex += group.chunks.length;
    });
    const footer = document.createElement('div');
    footer.className = 'collection-footer produce-list-footer';
    footer.dataset.produceCollectionFooter = '';
    footer.append(produceText('span', 'metadata',
      `Showing ${visible.length.toLocaleString()} of ${chunks.length.toLocaleString()} chunks`));
    if (visibleLimit < chunks.length) footer.append(UI.button({
      label: `Load ${Math.min(chunkPageSize(), chunks.length - visibleLimit).toLocaleString()} more`,
      variant: 'secondary', size: 'compact', attributes: { 'data-produce-load-more': '' },
      onClick: () => { visibleLimit += chunkPageSize(); render(); },
    }));
    content.append(footer);
    if (selected?.chunk_id && selected.chunk_id !== revealedSelectionId) {
      const selectedRow = [...content.querySelectorAll('[data-audio-row]')]
        .find((row) => row.dataset.chunkId === selected.chunk_id);
      if (selectedRow) {
        revealedSelectionId = selected.chunk_id;
        requestAnimationFrame(() => selectedRow.scrollIntoView({ block: 'center' }));
      }
    }
    owner.dataset.pageState = aggregate.process?.running ? 'running'
      : aggregate.state === 'blocked' ? 'blocked' : chunks.length > 20 ? 'dense' : 'ready';
  };

  return Object.freeze({ render, cleanup() {} });
}
