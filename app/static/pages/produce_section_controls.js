'use strict';

import {
  describeProduceBatch, produceBatchActionLabel,
} from './produce_sections.js';

const UI = globalThis.AlexandriaUI;

export function createProduceSectionAction({
  section, selectedIds, collectionComplete, disabled, onGenerate,
}) {
  const hasSelection = selectedIds.size > 0;
  const batch = describeProduceBatch(
    section.chunks,
    hasSelection ? selectedIds : null,
  );
  const reason = !collectionComplete
    ? 'Section generation is unavailable because the complete audio list is not loaded.'
    : hasSelection && !batch.count
      ? 'The current selection contains no actionable audio in this section.'
      : !batch.count
        ? 'No ready, stale, or failed chunks with a valid Voice are available.'
        : disabled ? 'Section audio actions are unavailable while audio work is running.' : '';
  const label = produceBatchActionLabel(batch, hasSelection ? 'selected' : 'eligible');
  const button = UI.button({
    label,
    variant: 'secondary',
    size: 'compact',
    disabled: Boolean(reason),
    attributes: {
      'data-produce-section-generate': '',
      'aria-label': batch.count
        ? `${label} in ${section.label}`
        : `No actionable selected audio in ${section.label}`,
      title: reason || `${label} in ${section.label}`,
    },
    onClick: () => onGenerate(section),
  });
  return button;
}
