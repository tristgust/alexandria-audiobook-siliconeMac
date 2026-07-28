'use strict';

import {
  castInitials, castProfileValues, castStatus, castText,
} from './cast_model.js';
import { createCastProfileSections } from './cast_profile_sections.js';

const UI = globalThis.AlexandriaUI;

function statusState(tone) {
  if (tone === 'success') return 'ready';
  if (tone === 'warning') return 'warning';
  if (tone === 'error') return 'error';
  return 'neutral';
}

export function createCastProfile({
  profile, api, signal, shell, getSelected, getDirty, getSaveState,
  onDirty, onResetDirty, onSave, onOpenWorkflow, onControlledCloneApplied, routeForTool,
}) {
  let popover = null;
  const sections = createCastProfileSections({
    api, signal, shell, getSelected, onDirty, onResetDirty,
    onCancelEdit: () => render(),
    onOpenWorkflow,
    onControlledCloneApplied,
  });

  function identityHeader() {
    const selected = getSelected();
    const header = document.createElement('header');
    header.className = 'cast-profile__identity cast-detail-header';
    header.dataset.castIdentity = '';

    const portrait = UI.monogram({
      initials: castInitials(selected.display_name),
      label: `Monogram for ${selected.display_name}`,
    });
    portrait.classList.add('cast-profile__portrait', 'cast-detail-portrait');

    const identity = document.createElement('div');
    identity.className = 'cast-profile__identity-copy cast-detail-identity';
    const scriptLabel = selected.identity?.script_voice_label
      || selected.script_connection?.resolved_script_voice_label;
    const role = selected.character?.summary?.role || selected.identity?.role || 'Role not classified';
    identity.append(
      castText('span', 'canonical-kicker utility-heading', 'Selected character'),
      castText('h2', 'cast-profile__name', selected.display_name),
      castText('p', 'cast-profile__muted',
        `${scriptLabel || 'Unresolved'} · ${role} · ${selected.speaking_role === 'speaking' ? 'Speaking' : 'Non-speaking'}`),
    );

    const actions = document.createElement('div');
    actions.className = 'cast-detail-actions';
    const state = castStatus(selected);
    const stateNode = castText('span', 'cast-detail-state', state.label);
    stateNode.dataset.state = statusState(state.tone);
    const opener = UI.iconButton({
      name: 'more', label: `More actions for ${selected.display_name}`,
      tooltip: 'More character actions',
    });
    opener.dataset.castMore = '';
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
    actions.append(stateNode, popover);
    header.append(portrait, identity, actions);
    return header;
  }

  function saveBar() {
    const saveState = getSaveState();
    const dirty = getDirty();
    const bar = document.createElement('div');
    bar.className = 'cast-profile__save cast-voice-editor-save';
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
    const voice = sections.voice();
    voice.querySelector('[data-cast-voice-editor]')?.append(saveBar());
    profile.replaceChildren(
      identityHeader(),
      voice,
      sections.reference(),
      sections.preview(),
      sections.character(),
      sections.appearance(),
      sections.advanced(),
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
