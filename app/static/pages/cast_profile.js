'use strict';

import {
  castProfileValues, castStatus, castText,
} from './cast_model.js';
import { createCastProfileSections } from './cast_profile_sections.js';

const UI = globalThis.AlexandriaUI;

export function createCastProfile({
  profile, api, signal, shell, getSelected, getDirty, getSaveState,
  onDirty, onSave, onOpenWorkflow, onControlledCloneApplied, routeForTool,
}) {
  let popover = null;
  const sections = createCastProfileSections({
    api, signal, shell, getSelected, onDirty,
    onOpenWorkflow,
    onControlledCloneApplied,
  });

  function identityHeader() {
    const selected = getSelected();
    const header = document.createElement('header');
    header.className = 'cast-profile__identity';
    header.dataset.castIdentity = '';
    const portrait = UI.portrait({
      label: `Portrait evidence unavailable for ${selected.display_name}`,
    });
    portrait.classList.add('cast-profile__portrait');
    const identity = document.createElement('div');
    identity.className = 'cast-profile__identity-copy';
    const scriptLabel = selected.identity?.script_voice_label
      || selected.script_connection?.resolved_script_voice_label;
    const role = selected.character?.summary?.role || selected.identity?.role || 'Role not classified';
    identity.append(
      castText('div', 'metadata', `Script label: ${scriptLabel || 'Unresolved'}`),
      castText('h2', 'cast-profile__name', selected.display_name),
      castText('p', 'cast-profile__muted',
        `Role: ${role} · ${selected.speaking_role === 'speaking' ? 'Speaking' : 'Non-speaking'}`),
    );
    const opener = UI.button({
      label: 'More actions', variant: 'secondary', size: 'compact',
      attributes: { 'data-cast-more': '' },
    });
    popover = UI.popover({
      opener,
      label: `More actions for ${selected.display_name}`,
      items: [
        { label: 'Advanced identity operations', onSelect: () => onOpenWorkflow('advanced-character-operations', opener) },
        { label: 'Open Voice designer', onSelect: () => onOpenWorkflow('voice-designer', opener) },
        { label: 'Prepare reference audio', onSelect: () => onOpenWorkflow('audio-preparer', opener) },
        { label: 'Build a training dataset', onSelect: () => onOpenWorkflow('dataset-builder', opener) },
        { label: 'Open Voice training', onSelect: () => onOpenWorkflow('voice-training', opener) },
      ],
    });
    popover.dataset.returnContext = routeForTool('cast').context.return || '';
    header.append(portrait, identity, UI.status(castStatus(selected)), popover);
    return header;
  }

  function saveBar() {
    const saveState = getSaveState();
    const dirty = getDirty();
    const bar = document.createElement('div');
    bar.className = 'cast-profile__save';
    bar.dataset.castSaveBar = '';
    bar.hidden = !dirty && saveState !== 'error';
    const status = UI.inlineSave({
      state: saveState,
      label: saveState === 'error' ? 'Changes retained. Retry save.'
        : saveState === 'saving' ? 'Saving…' : 'Unsaved changes',
    });
    const button = UI.button({
      label: saveState === 'error' ? 'Retry save' : 'Save changes',
      variant: 'secondary', disabled: saveState === 'saving',
      attributes: { 'data-cast-save': '' }, onClick: onSave,
    });
    bar.append(status, button);
    return bar;
  }

  function render() {
    sections.cleanup();
    popover?.popoverCleanup?.();
    popover = null;
    const selected = getSelected();
    if (!selected) {
      profile.replaceChildren(UI.emptyState({
        title: 'Choose a character',
        body: 'Select a character to review their voice and Script-supported identity.',
      }));
      return;
    }
    const summaryGrid = document.createElement('div');
    summaryGrid.className = 'cast-profile__summary-grid';
    summaryGrid.append(sections.character(), sections.appearance());
    profile.replaceChildren(
      identityHeader(),
      sections.voice(),
      sections.reference(),
      sections.preview(),
      summaryGrid,
      sections.advanced(),
      saveBar(),
    );
  }

  return Object.freeze({
    render,
    values: () => castProfileValues(profile, getSelected()),
    cleanup() {
      sections.cleanup();
      popover?.popoverCleanup?.();
      popover = null;
    },
  });
}
