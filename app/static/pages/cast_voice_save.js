'use strict';

const UI = globalThis.AlexandriaUI;

export function createCastVoiceSave({
  api, signal, page, profile, profileView, beginRequest,
  getSelected, setSelected, renderHeader, renderProfile,
  loadSelection, refreshAggregate,
}) {
  let dirty = false;
  let saveState = 'saved';
  let pendingSelection = null;

  const updateSaveBar = () => {
    const saveBar = profile.querySelector('[data-cast-save-bar]');
    if (saveBar) saveBar.hidden = !dirty && saveState !== 'error';
  };

  const markDirty = (value = true) => {
    dirty = Boolean(value);
    saveState = dirty ? 'dirty' : 'saved';
    page.dataset.dirty = String(dirty);
    renderHeader();
    updateSaveBar();
  };

  const saveProfile = async () => {
    const selected = getSelected();
    if (!selected || saveState === 'saving') return false;
    const { method, assigned, description, transcript, scriptLabel } = profileView.values();
    setSelected({
      ...selected,
      voice: {
        ...(selected.voice || {}),
        selected_production_method: method,
        selected_voice: assigned,
        persistent_voice_description: description,
        clone: {
          ...(selected.voice?.clone || {}),
          exact_reference_transcript: transcript,
        },
      },
    });
    saveState = 'saving';
    renderHeader();
    renderProfile();
    const response = await api.post('/api/save_voice_config', {
      [scriptLabel]: {
        type: method,
        voice: assigned,
        description,
        ref_text: transcript,
      },
    }, { signal: beginRequest() });
    if (signal.aborted) return false;
    if (!response.ok) {
      saveState = response.kind === 'canceled' ? 'dirty' : 'error';
      dirty = true;
      renderHeader();
      renderProfile();
      return false;
    }
    dirty = false;
    saveState = 'saved';
    await loadSelection(getSelected().character_id, false);
    await refreshAggregate();
    renderHeader();
    return true;
  };

  const openDirtyDialog = (characterId, opener) => {
    pendingSelection = characterId;
    const save = UI.button({
      label: 'Save', variant: 'primary',
      onClick: async () => {
        const target = pendingSelection;
        if (await saveProfile()) {
          dialog.forceClose('save');
          loadSelection(target);
        }
      },
    });
    const dialog = UI.dialog({
      title: 'Unsaved Cast changes',
      body: 'Save this character’s voice changes before opening another profile.',
      content: save,
      confirmLabel: 'Discard',
      destructive: true,
      onConfirm: () => {
        dirty = false;
        saveState = 'saved';
        loadSelection(characterId);
      },
      onClose: () => {
        pendingSelection = null;
        opener?.focus();
      },
    });
    dialog.open(opener);
  };

  const requestSelection = (characterId, opener) => {
    if (!characterId || characterId === getSelected()?.character_id) return;
    if (dirty) {
      openDirtyDialog(characterId, opener);
      return;
    }
    loadSelection(characterId);
  };

  const reset = () => {
    dirty = false;
    saveState = 'saved';
    page.dataset.dirty = 'false';
  };

  return Object.freeze({
    get dirty() { return dirty; },
    get saveState() { return saveState; },
    markDirty,
    requestSelection,
    reset,
    saveProfile,
  });
}
