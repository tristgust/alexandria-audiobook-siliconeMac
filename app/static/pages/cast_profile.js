'use strict';

import {
  castInitials, castProfileValues, castStatus, castText,
} from './cast_model.js';
import { createCastProfileSections } from './cast_profile_sections.js';
import { castScriptLineCount } from './cast_line_count.js';

const UI = globalThis.AlexandriaUI;

export function createCastProfile({
  profile, api, signal, shell, getSelected, getVoiceLibrary, getVoiceLibraryState,
  getDirty, getSaveState,
  onDirty, onSave, onCancelEdit, onOpenWorkflow, onControlledCloneApplied, routeForTool,
  onRetryVoiceLibrary,
}) {
  let popover = null;
  let editing = false;
  let renderedCharacterId = null;
  const sections = createCastProfileSections({
    api, signal, shell, getSelected, getVoiceLibrary, getVoiceLibraryState, onDirty,
    onOpenWorkflow,
    onControlledCloneApplied,
    onRetryVoiceLibrary,
  });

  function identityHeader() {
    const selected = getSelected();
    const header = document.createElement('header');
    header.className = 'cast-profile__identity';
    header.dataset.castIdentity = '';
    const portrait = UI.monogram({
      initials: castInitials(selected.display_name),
      label: `Monogram for ${selected.display_name}`,
    });
    portrait.classList.add('cast-profile__portrait');
    const identity = document.createElement('div');
    identity.className = 'cast-profile__identity-copy';
    const scriptLabel = selected.identity?.script_voice_label
      || selected.script_connection?.resolved_script_voice_label;
    const role = selected.character?.summary?.role || selected.identity?.role
      || (selected.speaking_role === 'speaking'
        || selected.identity?.speaking_state === 'speaking' ? 'Speaking role' : 'Non-speaking');
    const lineCount = castScriptLineCount(selected);
    const normalizedDisplayName = String(selected.display_name || '').trim().toLocaleLowerCase();
    const normalizedScriptLabel = String(scriptLabel || '').trim().toLocaleLowerCase();
    const identityMeta = normalizedScriptLabel && normalizedScriptLabel !== normalizedDisplayName
      ? `${scriptLabel} · ${role}`
      : role;
    identity.append(
      castText('span', 'metadata cast-profile__eyebrow', 'Selected character'),
      castText('h2', 'cast-profile__name', selected.display_name),
      castText('p', 'cast-profile__muted', identityMeta || 'Script label unresolved'),
    );
    if (lineCount > 0) {
      identity.append(castText(
        'p',
        'metadata cast-profile__script-line-count',
        `${lineCount.toLocaleString()} Script lines`,
      ));
    }
    const opener = UI.iconButton({
      iconClass: 'fas fa-ellipsis-vertical',
      label: `More actions for ${selected.display_name}`, size: 'compact',
      attributes: { 'data-cast-more': '' },
    });
    popover = UI.popover({
      opener,
      label: `More actions for ${selected.display_name}`,
      items: [
        { label: 'Identity and roster tools', onSelect: () => onOpenWorkflow('advanced-character-operations', opener) },
        { label: 'Open Voice designer', onSelect: () => onOpenWorkflow('voice-designer', opener) },
        { label: 'Prepare reference audio', onSelect: () => onOpenWorkflow('audio-preparer', opener) },
        { label: 'Build a training dataset', onSelect: () => onOpenWorkflow('dataset-builder', opener) },
        { label: 'Open Voice training', onSelect: () => onOpenWorkflow('voice-training', opener) },
      ],
    });
    popover.dataset.returnContext = routeForTool('cast').context.return || '';
    const actions = document.createElement('div');
    actions.className = 'cast-profile__identity-actions';
    actions.append(UI.status({ ...castStatus(selected), domain: 'cast' }));
    if (selected.next_useful_action?.id === 'review_character_identity') {
      const reviewIdentity = UI.button({
        label: selected.next_useful_action.label || 'Review identity',
        variant: 'secondary',
        attributes: { 'data-cast-review-identity': '' },
        onClick: () => onOpenWorkflow(
          'advanced-character-operations', reviewIdentity, { mode: 'identity-review' },
        ),
      });
      actions.append(reviewIdentity);
    }
    actions.append(popover);
    header.append(portrait, identity, actions);
    return header;
  }

  function beginEdit() {
    editing = true;
    render();
    requestAnimationFrame(() => profile.querySelector('[data-cast-voice-method]')?.focus());
  }

  function cancelEdit() {
    editing = false;
    onCancelEdit?.();
    render();
    requestAnimationFrame(() => profile.querySelector('[data-cast-edit-voice]')?.focus());
  }

  async function saveEdit() {
    const saved = await onSave?.();
    if (!saved) return;
    editing = false;
    render();
    requestAnimationFrame(() => profile.querySelector('[data-cast-edit-voice]')?.focus());
  }

  function render() {
    sections.cleanup();
    popover?.popoverCleanup?.();
    popover = null;
    const selected = getSelected();
    if (!selected) {
      editing = false;
      renderedCharacterId = null;
      profile.replaceChildren(UI.emptyState({
        title: 'Choose a character',
        body: 'Select a character to review their voice and Script-supported identity.',
      }));
      return;
    }
    if (renderedCharacterId && renderedCharacterId !== selected.character_id) editing = false;
    renderedCharacterId = selected.character_id;
    profile.dataset.editing = String(editing);
    const profileSections = [
      identityHeader(),
      sections.voice({
        editing,
        onEdit: beginEdit,
        onCancel: cancelEdit,
        onSave: saveEdit,
        saveState: getSaveState(),
        dirty: getDirty(),
      }),
      sections.reference({ editing }),
      sections.preview(),
      sections.character(),
      sections.appearance(),
      sections.advanced(),
    ];
    profile.replaceChildren(...profileSections);
  }

  return Object.freeze({
    render,
    syncVoiceLibraryState: sections.syncVoiceLibraryState,
    values: () => castProfileValues(profile, getSelected()),
    cleanup() {
      sections.cleanup();
      popover?.popoverCleanup?.();
      popover = null;
    },
  });
}
