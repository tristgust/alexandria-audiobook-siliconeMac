'use strict';

import { resultMessage, textNode } from '/static/pages/more.js';

const UI = globalThis.AlexandriaUI;

const OPERATION_LABELS = Object.freeze({
  add: 'Speaker added to Cast',
  undo: 'Identity operation undone',
  add_alias: 'Alias added',
  remove_alias: 'Alias removed',
  mark_unresolved: 'Identity marked for review',
});

function operationLabel(operation) {
  if (OPERATION_LABELS[operation]) return OPERATION_LABELS[operation];
  const words = String(operation || 'Identity operation').replaceAll('_', ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function operationDetail(item, speakers, changedLines) {
  if (item.operation === 'undo') return 'An earlier identity operation was reversed. This audit record remains available.';
  if (item.operation === 'add') return `${speakers.join(', ') || 'Script speaker'} · Active Cast identity created`;
  const summary = `${speakers.join(', ') || 'Roster only'} · ${changedLines.length} Script changes`;
  return item.undo_blocked_reason ? `${summary} · ${item.undo_blocked_reason}` : summary;
}

export function createOperationHistory({ payload, api, signal, route, shell }) {
  const section = document.createElement('details');
  section.className = 'full-cast-operation-history';
  section.append(textNode('summary', '', `Recent identity operations (${(payload.history || []).length})`));
  const content = document.createElement('div');
  content.className = 'full-cast-operation-history__content';
  const feedback = document.createElement('div');
  feedback.className = 'full-cast-operation-history__feedback';
  const history = payload.history || [];
  if (!history.length) {
    content.append(textNode('p', 'metadata', 'No identity operations have been recorded.'));
  } else {
    history.slice(0, 20).forEach((item) => {
      const row = document.createElement('div');
      row.className = 'support-list-row';
      row.dataset.speakerOperationId = item.operation_id;
      const copy = document.createElement('div');
      const speakers = item.affected_speakers || [];
      const changedLines = item.changed_script_indices || [];
      copy.append(
        textNode('strong', '', operationLabel(item.operation)),
        textNode(
          'p', 'support-status-copy',
          operationDetail(item, speakers, changedLines),
        ),
      );
      let action;
      if (item.operation !== 'undo' && item.undoable !== false) {
        action = UI.button({
          label: 'Undo', variant: 'quiet',
          attributes: { 'data-speaker-operation-undo': '' },
        });
        action.addEventListener('click', async () => {
          action.disabled = true;
          const result = await api.post('/api/speaker_management/undo', {
            operation_id: item.operation_id,
          }, { signal });
          if (!result.ok) {
            action.disabled = false;
            feedback.replaceChildren(UI.notice({
              tone: 'error',
              title: 'Undo is no longer available',
              body: resultMessage(result, 'A newer identity change must be reviewed first.'),
              live: true,
            }));
            action.focus();
            return;
          }
          shell.navigate(route.hash, { historyMode: 'replace' });
        });
      } else {
        action = UI.status({
          label: item.undone ? 'Undone' : 'Audit record',
          tone: 'neutral',
        });
      }
      row.append(copy, action);
      content.append(row);
    });
  }
  content.append(feedback);
  section.append(content);
  return section;
}
