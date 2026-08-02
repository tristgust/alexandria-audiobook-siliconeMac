'use strict';

import {
  isProduceSectionEligible, pruneProduceSectionSelection,
} from './produce_sections.js';

export function createProduceListSelection({
  content, actions, getAggregate, setSelected, onSelectionChange, onRender,
}) {
  let selectedIds = new Set();
  let anchorId = null;

  const rowById = (chunkId) => [...content.querySelectorAll('[data-audio-row]')]
    .find((row) => row.dataset.chunkId === chunkId);

  const selectChunk = (chunk, row, focus = false) => {
    setSelected(chunk, row);
    getAggregate().selected_chunk_id = chunk.chunk_id;
    content.querySelectorAll('[data-audio-row]').forEach((item) => {
      const current = item === row;
      item.dataset.active = String(current);
      item.toggleAttribute('aria-current', current);
      item.tabIndex = current ? 0 : -1;
    });
    if (focus) row?.focus({ preventScroll: true });
    onSelectionChange?.();
  };

  const renderAndFocus = (chunkId) => {
    onRender();
    rowById(chunkId)?.focus({ preventScroll: true });
  };

  const busy = () => actions.busy || getAggregate().process?.running
    || getAggregate().process?.cancel_requested;
  const canSelectRow = (eligible = true) => eligible && !busy();
  const update = (chunk, event) => {
    const additive = Boolean(event.metaKey || event.ctrlKey);
    const range = Boolean(event.shiftKey && anchorId);
    if (range) {
      const visibleIds = [...content.querySelectorAll('[data-audio-row]')]
        .filter((item) => {
          const candidate = getAggregate().chunks.find(
            (value) => value.chunk_id === item.dataset.chunkId,
          );
          return candidate && isProduceSectionEligible(candidate);
        })
        .map((item) => item.dataset.chunkId);
      const anchorIndex = visibleIds.indexOf(anchorId);
      const targetIndex = visibleIds.indexOf(chunk.chunk_id);
      if (anchorIndex < 0 || targetIndex < 0) {
        selectedIds.clear();
        selectedIds.add(chunk.chunk_id);
        anchorId = chunk.chunk_id;
        return;
      }
      if (!additive) selectedIds.clear();
      visibleIds.slice(
        Math.min(anchorIndex, targetIndex), Math.max(anchorIndex, targetIndex) + 1,
      ).forEach((id) => selectedIds.add(id));
      return;
    }
    if (additive) {
      if (selectedIds.has(chunk.chunk_id)) selectedIds.delete(chunk.chunk_id);
      else selectedIds.add(chunk.chunk_id);
    } else {
      selectedIds.clear();
      selectedIds.add(chunk.chunk_id);
    }
    anchorId = chunk.chunk_id;
  };

  const onRowClick = ({ chunk, row, event, canSelect }) => {
    if (!canSelectRow(canSelect)) {
      selectChunk(chunk, row);
      return;
    }
    update(chunk, event);
    if (event.metaKey || event.ctrlKey || event.shiftKey) {
      renderAndFocus(chunk.chunk_id);
      return;
    }
    onRender();
    selectChunk(chunk, rowById(chunk.chunk_id), true);
  };

  const onRowKeydown = ({ chunk, row, event, canSelect }) => {
    if (event.target !== row) return;
    const selectAll = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'a';
    if (!selectAll
      && !['ArrowUp', 'ArrowDown', 'Home', 'End', 'Enter', ' ', 'Escape'].includes(event.key)) return;
    event.preventDefault();
    if (selectAll) {
      if (!canSelectRow()) {
        selectedIds.clear();
        anchorId = null;
        renderAndFocus(chunk.chunk_id);
        return;
      }
      selectedIds.clear();
      content.querySelectorAll('[data-audio-row]').forEach((item) => {
        const candidate = getAggregate().chunks.find(
          (value) => value.chunk_id === item.dataset.chunkId,
        );
        if (candidate && isProduceSectionEligible(candidate)) selectedIds.add(candidate.chunk_id);
      });
      anchorId = chunk.chunk_id;
      renderAndFocus(chunk.chunk_id);
      return;
    }
    if (event.key === 'Escape') {
      selectedIds.clear();
      anchorId = null;
      renderAndFocus(chunk.chunk_id);
      return;
    }
    if (event.key === 'Enter') {
      selectChunk(chunk, row);
      return;
    }
    if (event.key === ' ') {
      if (canSelectRow(canSelect)) {
        update(chunk, event);
        renderAndFocus(chunk.chunk_id);
      } else selectChunk(chunk, row, true);
      return;
    }
    const rows = [...content.querySelectorAll('[data-audio-row]')];
    const current = rows.indexOf(row);
    const target = event.key === 'Home' ? rows[0] : event.key === 'End' ? rows.at(-1)
      : rows[(current + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length];
    const targetChunk = getAggregate().chunks.find(
      (item) => item.chunk_id === target?.dataset.chunkId,
    );
    if (target && targetChunk) selectChunk(targetChunk, target, true);
  };

  return Object.freeze({
    has: (chunkId) => selectedIds.has(chunkId),
    ids: () => selectedIds,
    prune(sections) {
      selectedIds = pruneProduceSectionSelection(selectedIds, sections);
    },
    clearAll() {
      selectedIds.clear();
      anchorId = null;
      onRender();
    },
    clearSection(eligibleIds) {
      eligibleIds.forEach((id) => selectedIds.delete(id));
      anchorId = null;
      onRender();
    },
    onRowClick,
    onRowKeydown,
  });
}
