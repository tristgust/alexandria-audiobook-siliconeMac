'use strict';

import { textNode } from '/static/pages/more.js';
import { entryOptions } from './advanced_identity_directory.js';
import { applyIdentityOperation } from './advanced_identity_mutation.js';

const UI = globalThis.AlexandriaUI;

function parseIndices(value) {
  const result = new Set();
  String(value || '').split(',').map((item) => item.trim()).filter(Boolean)
    .forEach((item) => {
      const range = item.match(/^(\d+)\s*-\s*(\d+)$/);
      if (range) {
        const start = Number(range[1]);
        const end = Number(range[2]);
        if (end < start) throw new Error(`Invalid range: ${item}`);
        for (let index = start; index <= end; index += 1) result.add(index);
        return;
      }
      if (!/^\d+$/.test(item)) throw new Error(`Invalid index: ${item}`);
      result.add(Number(item));
    });
  return [...result].sort((a, b) => a - b);
}

export function createLineCorrectionControl({ payload, api, signal, route, shell }) {
  const section = document.createElement('details');
  section.className = 'full-cast-line-corrections';
  section.append(textNode('summary', '', 'Advanced line corrections'));
  const content = document.createElement('div');
  content.className = 'full-cast-line-corrections__content';
  content.append(textNode(
    'p',
    'support-status-copy',
    'Use these only when specific Script lines belong to the wrong identity or one Cast identity must be split into two.',
  ));
  const operation = UI.field({
    id: 'line-correction-operation',
    label: 'Correction',
    kind: 'select',
    options: [
      { value: 'reassign', label: 'Reassign Script lines' },
      { value: 'split', label: 'Split one identity into two' },
    ],
  });
  const identity = UI.field({
    id: 'line-correction-identity',
    label: 'Target identity',
    kind: 'select',
    options: entryOptions(payload),
  });
  const lines = UI.field({
    id: 'line-correction-lines',
    label: 'Script entry indices',
    description: 'Comma-separated indices or ranges, for example 14, 22-25.',
  });
  const expectedSpeaker = UI.field({
    id: 'line-correction-expected-speaker',
    label: 'Current speaker label guard',
    description: 'Optional. The operation stops if a selected line has another speaker.',
  });
  const newName = UI.field({
    id: 'line-correction-new-name',
    label: 'New identity name',
  });
  const evidence = UI.field({
    id: 'line-correction-evidence',
    label: 'Evidence indices to move',
    description: 'Required for a split. Leave at least one evidence item with the original identity.',
  });
  const feedback = document.createElement('div');
  feedback.setAttribute('role', 'status');
  const sync = () => {
    const split = operation.querySelector('select').value === 'split';
    newName.hidden = !split;
    evidence.hidden = !split;
    expectedSpeaker.hidden = split;
    identity.querySelector('label').textContent = split ? 'Identity to split' : 'Move lines to';
  };
  operation.querySelector('select').addEventListener('change', sync);
  sync();
  const opener = UI.button({ label: 'Review line correction', variant: 'secondary' });
  UI.dialog({
    opener,
    title: 'Review line correction',
    body: 'This changes Script speaker ownership and invalidates generated audio for affected chunks. The operation can be undone from history.',
    confirmLabel: 'Apply line correction',
    destructive: true,
    onConfirm: async () => {
      try {
        const operationName = operation.querySelector('select').value;
        const entryIndices = parseIndices(lines.querySelector('input')?.value);
        if (!entryIndices.length) throw new Error('Enter at least one Script entry index.');
        let operationPayload;
        if (operationName === 'split') {
          const identityId = identity.querySelector('select')?.value;
          const name = newName.querySelector('input')?.value.trim();
          const evidenceIndexes = parseIndices(evidence.querySelector('input')?.value);
          if (!identityId || !name || !evidenceIndexes.length) {
            throw new Error('Choose the identity, enter a new name, and select evidence indices.');
          }
          operationPayload = {
            entry_id: identityId,
            new_name: name,
            entry_indices: entryIndices,
            evidence_indexes: evidenceIndexes,
          };
        } else {
          operationPayload = {
            target_entry_id: identity.querySelector('select')?.value,
            entry_indices: entryIndices,
          };
          const guard = expectedSpeaker.querySelector('input')?.value.trim();
          if (guard) operationPayload.expected_speaker = guard;
        }
        await applyIdentityOperation({
          api, signal, payload, operation: operationName,
          operationPayload, feedback, route, shell,
        });
      } catch (error) {
        feedback.replaceChildren(UI.notice({
          tone: 'error', title: 'Line correction was not applied',
          body: error.message || String(error), live: true,
        }));
      }
    },
  });
  content.append(
    operation,
    identity,
    lines,
    expectedSpeaker,
    newName,
    evidence,
    opener,
    feedback,
  );
  section.append(content);
  return section;
}
