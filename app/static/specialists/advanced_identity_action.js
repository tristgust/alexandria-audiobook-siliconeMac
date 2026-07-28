'use strict';

import { textNode } from '/static/pages/more.js';
import { entryOptions } from './advanced_identity_directory.js';
import { applyIdentityOperation } from './advanced_identity_mutation.js';

const UI = globalThis.AlexandriaUI;

export function createIdentityActionControl({ payload, api, signal, route, shell }) {
  const section = document.createElement('section');
  section.className = 'specialist-section identity-action-sheet';
  section.append(
    textNode('h2', '', 'Resolve and maintain identities'),
    textNode(
      'p',
      'support-status-copy',
      'Resolve provisional identities, rename them, maintain aliases, merge duplicates, or exclude non-speaking entries. Cast and Script update as one undoable operation.',
    ),
  );
  const character = UI.field({
    id: 'identity-action-character',
    label: 'Identity',
    kind: 'select',
    options: entryOptions(payload),
  });
  const characterSelect = character.querySelector('select');
  if ([...characterSelect.options].some(
    (option) => option.value === route.context.character,
  )) characterSelect.value = route.context.character;
  const action = UI.field({
    id: 'identity-action-operation',
    label: 'Action',
    kind: 'select',
    options: [
      { value: 'resolve', label: 'Confirm as resolved' },
      { value: 'rename', label: 'Rename identity' },
      { value: 'add_alias', label: 'Add alias' },
      { value: 'remove_alias', label: 'Remove alias' },
      { value: 'mark_unresolved', label: 'Mark unresolved' },
      { value: 'merge', label: 'Merge into another identity' },
      { value: 'exclude', label: 'Exclude non-speaking identity' },
    ],
  });
  const value = UI.field({
    id: 'identity-action-value',
    label: 'Name, alias, or reason',
  });
  const target = UI.field({
    id: 'identity-action-target',
    label: 'Keep this identity',
    kind: 'select',
    options: entryOptions(payload),
  });
  const voiceResolution = UI.field({
    id: 'identity-action-voice-resolution',
    label: 'Production Voice conflict',
    kind: 'select',
    options: [
      { value: 'old', label: 'Keep the selected target identity’s Voice' },
      { value: 'new', label: 'Keep the merged identity’s Voice' },
      { value: 'clear', label: 'Clear the conflicting Voice assignment' },
    ],
  });
  const resolveRename = UI.checkbox({
    label: 'Also mark the renamed identity resolved',
    checked: true,
  });
  const context = textNode('p', 'support-status-copy identity-action-sheet__context', '');
  const help = textNode('p', 'metadata identity-action-sheet__help', '');
  const feedback = document.createElement('div');
  feedback.setAttribute('role', 'status');
  const selectedEntry = () => (payload.entries || []).find(
    (entry) => entry.character_id === character.querySelector('select')?.value,
  );
  const syncVoiceResolutionLabels = () => {
    const mergedEntry = selectedEntry();
    const keptEntry = (payload.entries || []).find(
      (entry) => entry.character_id === target.querySelector('select')?.value,
    );
    const select = voiceResolution.querySelector('select');
    const keptName = keptEntry?.display_name || keptEntry?.canonical_name || 'selected target';
    const mergedName = mergedEntry?.display_name || mergedEntry?.canonical_name || 'merged identity';
    const labels = {
      old: `Keep ${keptName}’s production Voice`,
      new: `Keep ${mergedName}’s production Voice`,
      clear: 'Clear both conflicting Voice assignments',
    };
    [...(select?.options || [])].forEach((option) => {
      option.textContent = labels[option.value] || option.textContent;
    });
  };
  const selectedReviewEntry = selectedEntry();
  if (route.context.mode === 'identity-review'
    && (selectedReviewEntry?.script_voice_mapping === 'ambiguous'
      || !selectedReviewEntry?.script_voice_name)) {
    action.querySelector('select').value = 'merge';
  }
  const sync = () => {
    const operation = action.querySelector('select').value;
    const entry = selectedEntry();
    const candidates = entry?.script_voice_candidates || [];
    context.textContent = route.context.mode !== 'identity-review' ? ''
      : entry?.script_voice_mapping === 'ambiguous'
        ? `${entry.display_name} does not own a unique Script label. Its evidence overlaps ${candidates.join(' and ')}. Merge it into the correct existing identity, or exclude it if it is not a separate speaking role in this Script.`
        : !entry?.script_voice_name
          ? `${entry?.display_name || 'This identity'} owns no Script lines. Merge it into the matching Cast identity, or exclude it from the active Cast if it does not speak in this Script.`
          : `${entry.display_name} is linked to ${entry.script_voice_name}. Use the controls below only if that identity or label is wrong.`;
    context.hidden = !context.textContent;
    value.hidden = !['rename', 'add_alias', 'remove_alias', 'mark_unresolved', 'exclude'].includes(operation);
    target.hidden = operation !== 'merge';
    voiceResolution.hidden = operation !== 'merge';
    resolveRename.hidden = operation !== 'rename';
    value.querySelector('label').textContent = {
      rename: 'New canonical name',
      add_alias: 'Alias to add',
      remove_alias: 'Alias to remove',
      mark_unresolved: 'Question or reason',
      exclude: 'Exclusion reason',
    }[operation] || 'Value';
    const targetSelect = target.querySelector('select');
    if (targetSelect) {
      const current = targetSelect.value;
      targetSelect.replaceChildren();
      entryOptions(payload, entry?.character_id).forEach((option) => {
        targetSelect.append(new Option(option.label, option.value));
      });
      if ([...targetSelect.options].some((option) => option.value === current)) {
        targetSelect.value = current;
      }
    }
    syncVoiceResolutionLabels();
    help.textContent = {
      resolve: 'Clears the unresolved questions and confirms the current identity without changing its Script label or Voice.',
      rename: 'Updates the roster, Script speaker label, Voice aliases, and affected production records together.',
      add_alias: 'Adds a recognized identity label and maps that alias to the same production Voice.',
      remove_alias: 'Removes the roster alias and its matching Voice alias when present.',
      mark_unresolved: 'Keeps the identity active but visibly provisional until you resolve, merge, or exclude it.',
      merge: 'Keeps the selected target, moves the other identity’s evidence and Script lines into it, and retires the duplicate.',
      exclude: Number(entry?.line_count || 0)
        ? `This identity owns ${entry.line_count} Script line(s). Merge or reassign those lines before excluding it.`
        : 'Removes this non-speaking identity from active Cast while retaining its evidence in the exclusion audit.',
    }[operation] || '';
  };
  character.querySelector('select').addEventListener('change', sync);
  action.querySelector('select').addEventListener('change', sync);
  target.querySelector('select').addEventListener('change', syncVoiceResolutionLabels);
  sync();
  if (route.context.mode === 'identity-review'
    && action.querySelector('select').value === 'merge') {
    const candidates = selectedEntry()?.script_voice_candidates || [];
    const suggestedTarget = (payload.entries || []).find((entry) => candidates.some(
      (candidate) => String(candidate).localeCompare(
        String(entry.display_name || entry.canonical_name || ''),
        undefined,
        { sensitivity: 'base' },
      ) === 0,
    ));
    if (suggestedTarget && [...target.querySelector('select').options].some(
      (option) => option.value === suggestedTarget.character_id,
    )) {
      target.querySelector('select').value = suggestedTarget.character_id;
      syncVoiceResolutionLabels();
    }
  }

  const opener = UI.button({ label: 'Review selected action', variant: 'secondary' });
  UI.dialog({
    opener,
    title: 'Review identity action',
    body: 'Identity changes are transactional and undoable. Speaker-label changes can invalidate generated audio for the affected lines.',
    confirmLabel: 'Apply identity action',
    destructive: true,
    onConfirm: async () => {
      try {
        const entry = selectedEntry();
        const operation = action.querySelector('select').value;
        const rawValue = value.querySelector('input')?.value.trim() || '';
        if (!entry) {
          feedback.replaceChildren(UI.notice({
            tone: 'warning', title: 'Identity required',
            body: 'Choose a Cast identity.', live: true,
          }));
          return;
        }
        let operationPayload = { entry_id: entry.character_id };
        if (operation === 'rename') {
          if (!rawValue) throw new Error('Enter the new canonical name.');
          operationPayload = {
            ...operationPayload,
            new_name: rawValue,
            display_name: rawValue,
            preserve_old_as_alias: true,
            resolve: resolveRename.querySelector('input')?.checked === true,
          };
        } else if (operation === 'add_alias') {
          if (!rawValue) throw new Error('Enter the alias to add.');
          operationPayload.alias = rawValue;
        } else if (operation === 'remove_alias') {
          if (!rawValue) throw new Error('Enter the alias to remove.');
          operationPayload.alias = rawValue;
          operationPayload.remove_voice_alias = true;
        } else if (operation === 'mark_unresolved') {
          if (!rawValue) throw new Error('Enter the unresolved question or reason.');
          operationPayload.question = rawValue;
        } else if (operation === 'merge') {
          const targetId = target.querySelector('select')?.value;
          if (!targetId || targetId === entry.character_id) throw new Error('Choose the identity to keep.');
          operationPayload = {
            primary_entry_id: targetId,
            secondary_entry_id: entry.character_id,
            voice_resolution: voiceResolution.querySelector('select')?.value || 'old',
            voice_project_resolution: 'primary',
          };
        } else if (operation === 'exclude') {
          if (Number(entry.line_count || 0)) {
            throw new Error('Merge or reassign this identity’s Script lines before excluding it.');
          }
          operationPayload.reason = rawValue || 'Excluded from the canonical Cast during manual review.';
        }
        await applyIdentityOperation({
          api, signal, payload, operation, operationPayload,
          feedback, route, shell,
        });
      } catch (error) {
        feedback.replaceChildren(UI.notice({
          tone: 'error', title: 'Identity operation was not applied',
          body: error.message || String(error), live: true,
        }));
      }
    },
  });
  section.append(
    context,
    character,
    action,
    value,
    target,
    voiceResolution,
    resolveRename,
    help,
    opener,
    feedback,
  );
  return section;
}
