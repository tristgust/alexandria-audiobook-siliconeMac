'use strict';

import {
  describeProduceBatch, produceBatchActionLabel,
} from './produce_sections.js';
import { produceText } from './produce_model.js';

const UI = globalThis.AlexandriaUI;

function selectionBreakdown(batch) {
  const parts = [
    batch.stateCounts.ready ? `${batch.stateCounts.ready.toLocaleString()} ready` : '',
    batch.stateCounts.stale ? `${batch.stateCounts.stale.toLocaleString()} stale` : '',
    batch.stateCounts.failed ? `${batch.stateCounts.failed.toLocaleString()} failed` : '',
  ].filter(Boolean);
  return parts.join(' · ');
}

export function createProduceBatchSelection({
  chunks, selectedIds, disabled, onExecute, onClear,
}) {
  const batch = describeProduceBatch(chunks, selectedIds);
  if (!batch.count) return null;

  const region = document.createElement('section');
  region.className = 'produce-batch-selection';
  region.dataset.produceBatchSelection = '';
  region.setAttribute('aria-label', 'Selected audio actions');
  region.setAttribute('aria-live', 'polite');

  const copy = document.createElement('div');
  copy.className = 'produce-batch-selection__copy';
  copy.append(
    produceText(
      'strong', '',
      `${batch.count.toLocaleString()} audio chunk${batch.count === 1 ? '' : 's'} selected`,
    ),
    produceText('span', 'metadata', selectionBreakdown(batch)),
  );

  const actions = document.createElement('div');
  actions.className = 'produce-batch-selection__actions';
  const label = produceBatchActionLabel(batch, 'selected');
  actions.append(
    UI.button({
      label,
      variant: 'primary',
      size: 'compact',
      disabled,
      attributes: {
        'data-produce-batch-action': '',
        'aria-label': `${label} across all selected sections`,
      },
      onClick: () => onExecute(batch.ids),
    }),
    UI.button({
      label: 'Clear selection',
      variant: 'quiet',
      size: 'compact',
      disabled,
      attributes: { 'data-produce-batch-clear': '' },
      onClick: onClear,
    }),
  );
  region.append(copy, actions);
  return region;
}
