'use strict';

const UI = globalThis.AlexandriaUI;

export function castShouldRollbackDesignedVoice(response) {
  return response?.kind === 'http';
}

export function createCastVoiceSave({
  api, signal, page, profile, profileView, beginRequest,
  getSelected, setSelected, renderHeader,
  loadSelection, refreshAggregate, reloadVoiceLibrary,
}) {
  let dirty = false;
  let saveState = 'saved';
  let pendingSelection = null;

  const reconcileDesignedVoice = () => {
    page.dataset.refreshState = 'refreshing';
    const refreshes = [refreshAggregate()];
    if (reloadVoiceLibrary) refreshes.push(reloadVoiceLibrary());
    void Promise.allSettled(refreshes).then((results) => {
      if (signal.aborted) return;
      page.dataset.refreshState = results.every(
        (result) => result.status === 'fulfilled' && result.value === true,
      )
        ? 'ready' : 'error';
      if (saveState === 'refreshing') saveState = 'saved';
      renderHeader();
    });
  };

  const updateSaveBar = () => {
    const saveBar = profile.querySelector('[data-cast-save-bar]');
    if (saveBar) saveBar.hidden = false;
    const saveButton = profile.querySelector('[data-cast-save]');
    if (saveButton) {
      saveButton.disabled = saveState === 'saving' || !dirty;
      saveButton.textContent = saveState === 'error' ? 'Retry save'
        : saveState === 'saving' ? 'Saving…' : 'Save changes';
    }
    const editorState = profile.querySelector('.cast-profile__editor-state');
    if (editorState) {
      editorState.dataset.state = saveState === 'saved' && dirty ? 'dirty' : saveState;
      editorState.textContent = saveState === 'error' ? 'Changes retained — retry save'
        : saveState === 'saving' ? 'Saving…'
          : dirty ? 'Unsaved changes' : 'No changes';
    }
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
    const {
      voiceId, reuseMode, method, persistedMethod: currentPersistedMethod,
      methodChanged, assigned, description, transcript, scriptLabel,
      designedPreviewFile, designedPreviewText, designedPreviewFingerprint,
      designedPreviewUseAsClone,
    } = profileView.values();
    const existingTechnicalMethod = [
      'clone', 'supplied_recording_clone', 'controlled_clone',
      'instruction_controlled_clone', 'adapter', 'lora', 'trained_voice', 'alias',
    ].includes(currentPersistedMethod);
    if (method === 'existing' && !voiceId && !(existingTechnicalMethod && !methodChanged)) {
      saveState = 'error';
      dirty = true;
      renderHeader();
      updateSaveBar();
      return false;
    }
    if (method === 'sound_effect') {
      saveState = 'error';
      dirty = true;
      renderHeader();
      updateSaveBar();
      return false;
    }
    if (voiceId) {
      saveState = 'saving';
      renderHeader();
      updateSaveBar();
      const clearing = voiceId === '__clear__';
      const response = await api.post(
        clearing ? '/api/voice-library/clear' : '/api/voice-library/assign',
        clearing
          ? { character_id: selected.character_id }
          : {
            character_id: selected.character_id,
            voice_id: voiceId,
            reuse_mode: reuseMode,
          },
        { signal: beginRequest() },
      );
      if (signal.aborted) return false;
      if (!response.ok) {
        saveState = response.kind === 'canceled' ? 'dirty' : 'error';
        dirty = true;
        renderHeader();
        updateSaveBar();
        return false;
      }
      if (response.data?.character) setSelected(response.data.character);
      dirty = false;
      saveState = 'saved';
      page.dataset.dirty = 'false';
      await reloadVoiceLibrary?.();
      await loadSelection(selected.character_id, false);
      await refreshAggregate();
      renderHeader();
      return true;
    }
    const cloneMethod = method === 'existing'
      && ['clone', 'supplied_recording_clone', 'controlled_clone', 'instruction_controlled_clone']
        .includes(currentPersistedMethod);
    const builtInMethod = method === 'builtin';
    const designedMethod = method === 'design';
    let persistedMethod = designedMethod ? 'design'
      : builtInMethod ? 'custom' : currentPersistedMethod || method;
    const persistedAssignedVoice = builtInMethod ? assigned : null;
    let persistedReferenceAudio = null;
    let persistedTranscript = cloneMethod ? transcript : null;
    let savedDesignedVoiceId = null;
    let savedAuditionBundlePath = null;
    let savedAuditionFingerprint = null;
    if (designedMethod && !description.trim()) {
      saveState = 'error';
      dirty = true;
      renderHeader();
      updateSaveBar();
      return false;
    }
    if (designedMethod && designedPreviewUseAsClone && !designedPreviewFile) {
      saveState = 'error';
      dirty = true;
      renderHeader();
      updateSaveBar();
      return false;
    }
    saveState = 'saving';
    renderHeader();
    updateSaveBar();
    if (designedMethod && designedPreviewUseAsClone) {
      const auditionSave = await api.post('/api/voice_design/save', {
        name: `${selected.display_name} Audition Voice`,
        description,
        sample_text: designedPreviewText,
        preview_file: designedPreviewFile,
        preview_fingerprint: designedPreviewFingerprint,
        save_audition_bundle: true,
        scope: 'project',
      }, { signal: beginRequest() });
      if (signal.aborted) return false;
      if (!auditionSave.ok || !auditionSave.data?.voice_id) {
        saveState = 'error';
        dirty = true;
        renderHeader();
        updateSaveBar();
        return false;
      }
      savedDesignedVoiceId = String(auditionSave.data.voice_id);
      savedAuditionBundlePath = auditionSave.data.audition_bundle_path || null;
      savedAuditionFingerprint = auditionSave.data.preview_fingerprint || null;
      persistedMethod = 'clone';
      persistedReferenceAudio = `designed_voices/${savedDesignedVoiceId}.wav`;
      persistedTranscript = designedPreviewText;
    }
    const response = await api.post('/api/save_voice_config', {
      [scriptLabel]: {
        type: persistedMethod,
        voice: persistedAssignedVoice,
        description,
        character_style: description,
        ref_audio: persistedReferenceAudio,
        ref_text: persistedTranscript,
        ...(persistedMethod === 'clone' ? {
          clone_backend: 'qwen3_base',
          fish_hybrid_enabled: true,
          fish_hybrid_styles: ['fear', 'grief', 'sarcasm', 'expressive'],
          fish_hybrid_use_approved_routes: true,
          fish_hybrid_fallback_to_local: true,
          audition_bundle_path: savedAuditionBundlePath,
          audition_preview_fingerprint: savedAuditionFingerprint,
        } : {}),
      },
    }, { signal: beginRequest() });
    if (signal.aborted) return false;
    if (!response.ok) {
      if (savedDesignedVoiceId && castShouldRollbackDesignedVoice(response)) {
        await api.delete(
          `/api/voice_design/${encodeURIComponent(savedDesignedVoiceId)}`,
          { signal: beginRequest() },
        );
      }
      saveState = response.kind === 'canceled' ? 'dirty' : 'error';
      dirty = true;
      renderHeader();
      updateSaveBar();
      return false;
    }
    setSelected({
      ...selected,
      voice: {
        ...(selected.voice || {}),
        selected_production_method: persistedMethod,
        selected_voice: persistedAssignedVoice,
        persistent_voice_description: description,
        clone: {
          ...(selected.voice?.clone || {}),
          reference_source: persistedReferenceAudio,
          reference_audio_url: persistedReferenceAudio ? `/${persistedReferenceAudio}` : null,
          exact_reference_transcript: persistedTranscript,
          audition_bundle_path: savedAuditionBundlePath,
          audition_preview_fingerprint: savedAuditionFingerprint,
        },
      },
    });
    dirty = false;
    saveState = 'saved';
    page.dataset.dirty = 'false';
    await loadSelection(getSelected().character_id, false);
    await refreshAggregate();
    renderHeader();
    return true;
  };

  const openDirtyDialog = (characterId, opener) => {
    pendingSelection = characterId;
    const save = UI.button({
      label: 'Save Voice changes', variant: 'primary',
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
      confirmLabel: 'Discard changes',
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
    const keyboardOpener = opener === document.activeElement && opener.matches(':focus-visible')
      ? opener : null;
    loadSelection(characterId, true, keyboardOpener);
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
