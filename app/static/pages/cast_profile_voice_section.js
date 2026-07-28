'use strict';

import { castSection, castText } from './cast_model.js';
import { createCastVoiceAssignmentForm } from './cast_voice_assignment_form.js';

const UI = globalThis.AlexandriaUI;

export function createCastProfileVoiceSection({
  api, signal, shell, getSelected, getVoiceLibrary, getVoiceLibraryState,
  onOpenWorkflow, onRetryVoiceLibrary, fieldControl, sectionHeading, editorFact, voiceFacts,
}) {
  let editingPreview = null;
  let voiceLibraryControls = null;

  function voiceEditor({ onCancel, onSave, saveState, dirty }) {
    const cancel = UI.button({
      label: 'Cancel', variant: 'quiet', size: 'compact', onClick: onCancel,
      attributes: { 'data-cast-cancel-edit': '' },
    });
    const save = UI.button({
      label: saveState === 'error' ? 'Retry save' : saveState === 'saving' ? 'Saving…' : 'Save changes',
      variant: 'primary', size: 'compact', disabled: saveState === 'saving' || !dirty,
      onClick: onSave, attributes: { 'data-cast-save': '' },
    });
    const actions = document.createElement('div');
    actions.className = 'cast-profile__editor-actions';
    actions.append(cancel, save);
    const editorState = castText(
      'span', 'cast-profile__editor-state',
      saveState === 'error' ? 'Changes retained — retry save'
        : saveState === 'saving' ? 'Saving…' : dirty ? 'Unsaved changes' : 'No changes',
    );
    editorState.dataset.state = saveState === 'saved' && dirty ? 'dirty' : saveState;
    editorState.setAttribute('role', 'status');
    editorState.setAttribute('aria-live', 'polite');
    const context = document.createElement('div');
    context.className = 'cast-profile__editor-context';
    context.append(castText('strong', '', 'Editing production Voice'), editorState);
    const toolbar = document.createElement('div');
    toolbar.className = 'cast-profile__editor-toolbar';
    toolbar.dataset.castSaveBar = '';
    toolbar.append(context, actions);
    const activateSave = () => {
      save.disabled = false;
      editorState.dataset.state = 'dirty';
      editorState.textContent = 'Unsaved changes';
    };
    const form = createCastVoiceAssignmentForm({
      api, signal, shell, selected: getSelected(), library: getVoiceLibrary?.() || { voices: [] },
      onOpenWorkflow, fieldControl, editorFact, onDirty: activateSave,
    });
    editingPreview = form.preview;
    const content = document.createElement('div');
    content.className = 'cast-profile__voice-editor';
    content.append(
      sectionHeading({ eyebrow: 'Production', title: 'Voice' }),
      toolbar, form.setup, form.designedPreview,
    );
    return content;
  }

  function voice({ editing, onEdit, onCancel, onSave, saveState, dirty }) {
    const selected = getSelected();
    if (editing) return castSection('voice', '', voiceEditor({
      onCancel, onSave, saveState, dirty,
    }));

    const edit = UI.button({
      label: 'Edit Voice', variant: 'secondary', size: 'compact',
      onClick: onEdit, attributes: { 'data-cast-edit-voice': '' },
    });
    const libraryStatus = document.createElement('span');
    libraryStatus.dataset.castVoiceLibraryState = '';
    voiceLibraryControls = { edit, libraryStatus };
    syncVoiceLibraryState();
    const actions = document.createElement('div');
    actions.className = 'cast-profile__voice-actions';
    actions.append(UI.inlineSave({ state: 'saved', label: 'Saved' }), edit, libraryStatus);
    const content = document.createElement('div');
    content.append(
      sectionHeading({ eyebrow: 'Production', title: 'Voice', action: actions }),
      voiceFacts(selected),
    );
    const blockers = selected.voice?.blockers || selected.blockers || [];
    if (blockers.length) content.append(UI.notice({
      tone: 'warning',
      title: blockers[0].title || 'Voice requires attention',
      body: blockers[0].explanation || 'Resolve the current Voice blocker before production.',
    }));
    if (selected.voice?.clone?.controlled_capability) {
      content.append(UI.notice({
        tone: 'warning',
        title: 'Instruction control is experimental',
        body: 'Re-preview after changing the reference, transcript, direction, or generation settings.',
      }));
    }
    return castSection('voice', '', content);
  }

  function syncVoiceLibraryState() {
    if (!voiceLibraryControls) return;
    const { edit, libraryStatus } = voiceLibraryControls;
    const state = getVoiceLibraryState?.() || { status: 'ready', error: '' };
    edit.disabled = state.status !== 'ready';
    libraryStatus.hidden = state.status === 'ready';
    if (state.status === 'ready') {
      libraryStatus.replaceChildren();
      return;
    }
    const message = castText(
      'span', 'metadata',
      state.status === 'loading' ? 'Loading saved Voices…'
        : state.error || 'Saved Voices could not be loaded.',
    );
    message.dataset.castVoiceLibraryStatus = '';
    message.setAttribute('role', 'status');
    message.setAttribute('aria-live', 'polite');
    libraryStatus.replaceChildren(message);
    if (state.status === 'error') {
      libraryStatus.append(UI.button({
        label: 'Retry saved Voices', variant: 'quiet', size: 'compact',
        attributes: { 'data-cast-voice-library-retry': '' },
        onClick: onRetryVoiceLibrary,
      }));
    }
  }

  return Object.freeze({
    voice,
    syncVoiceLibraryState,
    getEditingPreview: () => editingPreview,
    cleanup() {
      editingPreview = null;
      voiceLibraryControls = null;
    },
  });
}
