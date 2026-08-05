'use strict';

import {
  castAuditionPersonaContext, castAuditionText, castText, castVoiceTechnicalFamily,
} from './cast_model.js';

const UI = globalThis.AlexandriaUI;
const AUDITION_TIMEOUT_MS = 300000;

export function createCastVoiceAudition({
  api, signal, shell, selected, onOpenWorkflow, assignableVoices,
  voiceChoice, method, assigned, description, editorFact, voiceOverlay,
  onDirty, onSaveAudition,
}) {
  const choiceSummary = editorFact({
    className: 'cast-profile__editor-choice-summary',
    iconName: 'microphone',
    label: 'Selected Voice',
    title: 'No saved Voice selected',
    body: 'Choose a saved Voice for this character.',
  });
  choiceSummary.node.dataset.castVoicePickerSummary = '';
  const previewChoice = UI.button({
    label: 'Preview selected Voice',
    variant: 'secondary',
    size: 'compact',
    disabled: true,
    attributes: { 'data-cast-preview-choice': '' },
  });
  previewChoice.classList.add('cast-profile__audition-generate');
  const studio = document.createElement('div');
  studio.className = 'cast-profile__audition-studio';
  studio.dataset.state = 'idle';
  const studioHeader = document.createElement('header');
  studioHeader.className = 'cast-profile__audition-header';
  const studioHeading = document.createElement('div');
  studioHeading.className = 'cast-profile__audition-heading';
  studioHeading.append(
    castText('span', 'metadata cast-profile__eyebrow', 'Voice range audition'),
    castText('h4', '', 'One identity · four deliveries'),
    castText(
      'p',
      'metadata',
      'Generate one neutral identity, then hear it as baseline, happy, sad, and angry.',
    ),
  );
  studioHeader.append(studioHeading, previewChoice);
  const generation = document.createElement('div');
  generation.className = 'cast-profile__audition-generation';
  generation.hidden = true;
  generation.setAttribute('role', 'status');
  generation.setAttribute('aria-live', 'polite');
  const generationWave = document.createElement('span');
  generationWave.className = 'cast-profile__audition-wave';
  generationWave.setAttribute('aria-hidden', 'true');
  for (let index = 0; index < 5; index += 1) generationWave.append(document.createElement('i'));
  const generationText = castText('span', 'metadata', 'Generating audition…');
  generation.append(generationWave, generationText);
  const previewSequence = document.createElement('div');
  previewSequence.className = 'cast-profile__voice-range';
  previewSequence.hidden = true;
  const sequenceHeader = document.createElement('div');
  sequenceHeader.className = 'cast-profile__voice-range-header';
  sequenceHeader.append(
    castText('strong', '', 'Preview sequence'),
    castText('span', 'metadata', 'The player always replays all four in order.'),
  );
  previewSequence.append(sequenceHeader);
  const previewSequenceList = document.createElement('ol');
  const laneControls = new Map();
  [
    ['baseline', 'Baseline', 'Neutral line direction'],
    ['happy', 'Happy', 'Bright and delighted'],
    ['sad', 'Sad', 'Quiet and vulnerable'],
    ['angry', 'Angry', 'Controlled and intense'],
  ].forEach(([lane, label, direction]) => {
    const item = document.createElement('li');
    item.dataset.castAuditionLane = lane;
    const laneHeading = document.createElement('div');
    laneHeading.className = 'cast-profile__voice-range-lane-heading';
    laneHeading.append(
      castText('span', 'cast-profile__voice-range-index', String(
        ['baseline', 'happy', 'sad', 'angry'].indexOf(lane) + 1,
      )),
      castText('strong', '', label),
    );
    const status = castText('span', 'metadata cast-profile__voice-range-status', direction);
    const laneTop = document.createElement('div');
    laneTop.className = 'cast-profile__voice-range-lane-top';
    laneTop.append(laneHeading);
    if (lane !== 'baseline') {
      const regenerate = UI.iconButton({
        name: 'refresh',
        label: `Regenerate ${label.toLowerCase()} only`,
        size: 'compact',
        attributes: { 'data-cast-regenerate-audition-lane': lane },
      });
      regenerate.hidden = true;
      laneTop.append(regenerate);
      laneControls.set(lane, { button: regenerate, status, direction, label });
    }
    item.append(laneTop, status);
    previewSequenceList.append(item);
  });
  previewSequence.append(previewSequenceList);
  const previewFeedback = castText('p', 'metadata cast-profile__voice-range-feedback', '');
  previewFeedback.setAttribute('role', 'status');
  previewFeedback.setAttribute('aria-live', 'polite');
  const designedPreview = document.createElement('input');
  designedPreview.type = 'hidden';
  designedPreview.dataset.castDesignedPreview = '';
  designedPreview.dataset.useAsClone = 'false';
  designedPreview.dataset.previewFingerprint = '';
  const saveAudition = UI.button({
    label: 'Save audition as Production Voice',
    variant: 'primary',
    attributes: {
      'data-cast-save-audition': '',
    },
  });
  saveAudition.hidden = true;
  const saveHint = castText(
    'p',
    'metadata cast-profile__audition-save-hint',
    'Saves the neutral identity, all four reviewed lanes, the combined audition, and its metadata as one Voice package.',
  );
  saveHint.hidden = true;
  const studioFooter = document.createElement('footer');
  studioFooter.className = 'cast-profile__audition-footer';
  studioFooter.append(saveAudition, saveHint);
  studio.append(studioHeader, generation, previewSequence, previewFeedback, studioFooter);
  const auditionText = castAuditionText(selected);
  const auditionPersonaContext = castAuditionPersonaContext(selected);
  designedPreview.dataset.sampleText = auditionText;
  let designedPreviewGeneration = 0;
  let designedPreviewFingerprint = '';
  let designedPreviewResult = null;
  let suppliedPreviewFingerprint = '';

  const setButtonContent = (button, label, loading = false) => {
    button.replaceChildren();
    if (loading) {
      const spinner = document.createElement('span');
      spinner.className = 'ui-button__spinner';
      spinner.setAttribute('aria-hidden', 'true');
      button.append(spinner);
    }
    button.append(castText('span', '', label));
    button.dataset.state = loading ? 'loading' : 'default';
    if (loading) button.setAttribute('aria-busy', 'true');
    else button.removeAttribute('aria-busy');
  };

  const setGenerating = (active, message = '') => {
    studio.dataset.state = active ? 'generating' : designedPreviewFingerprint ? 'ready' : 'idle';
    generation.hidden = !active;
    if (message) generationText.textContent = message;
    previewSequence.setAttribute('aria-busy', String(active));
  };

  const setLaneLoading = (control, active) => {
    control.button.dataset.state = active ? 'loading' : 'default';
    control.button.disabled = active || !designedPreviewFingerprint;
    control.button.setAttribute(
      'aria-label',
      active
        ? `Regenerating ${control.label.toLowerCase()}`
        : `Regenerate ${control.label.toLowerCase()} only`,
    );
    control.button.replaceChildren(UI.icon(active ? 'loader' : 'refresh'));
    if (active) control.button.setAttribute('aria-busy', 'true');
    else control.button.removeAttribute('aria-busy');
  };

  const syncLaneControls = (result = null) => {
    const sequence = result?.sequence || [];
    const byLane = new Map(sequence.map((item) => [item.id, item]));
    for (const [lane, control] of laneControls) {
      const laneResult = byLane.get(lane);
      control.button.hidden = !designedPreviewFingerprint;
      control.button.disabled = !designedPreviewFingerprint;
      setLaneLoading(control, false);
      control.status.textContent = laneResult?.text_validation_passed === false
        ? `${control.direction} · Listen-check`
        : laneResult?.variance_status === 'subtle'
        ? `${control.direction} · Subtle`
        : laneResult ? `${control.direction} · Distinct` : control.direction;
      control.status.dataset.state = laneResult?.text_validation_passed === false
        ? 'review'
        : laneResult?.variance_status || '';
    }
  };

  const invalidateDesignedPreview = () => {
    const wasDesignedPreview = Boolean(designedPreview.value)
      || method.control.value === 'design';
    designedPreviewGeneration += 1;
    designedPreview.value = '';
    designedPreview.dataset.useAsClone = 'false';
    designedPreview.dataset.previewFingerprint = '';
    designedPreviewFingerprint = '';
    designedPreviewResult = null;
    syncLaneControls();
    saveAudition.hidden = true;
    saveHint.hidden = true;
    setGenerating(false);
    if (!wasDesignedPreview) return;
    previewFeedback.hidden = false;
    previewFeedback.textContent = 'The Designed Voice definition changed, so the old audition was cleared. You can save the definition now or generate another audition first.';
  };

  const invalidateExistingPreview = () => {
    suppliedPreviewFingerprint = '';
    if (method.control.value !== 'existing') return;
    previewFeedback.hidden = false;
    previewFeedback.textContent = (
      'The Existing Voice source or character adjustments changed, so generate '
      + 'a new audition before judging this setup.'
    );
  };

  const selectedResource = () => assignableVoices.find(
    (item) => item.voice_id === voiceChoice.control.value,
  );
  const persistedFamily = () => castVoiceTechnicalFamily(
    method.control.dataset.persistedMethod,
  );
  const currentVoiceOverlay = () => ({
    direction: voiceOverlay?.direction?.value || '',
    pitch_semitones: Number(voiceOverlay?.pitch?.value || 0),
    pace_percent: Number(voiceOverlay?.pace?.value || 100),
    level_db: Number(voiceOverlay?.level?.value || 0),
  });
  const builtInPreviewVoice = () => {
    const resource = selectedResource();
    if (resource?.method === 'built_in') return resource.key || resource.name;
    if (resource) return '';
    return method.control.value === 'builtin' ? assigned.control.value : '';
  };
  const update = () => {
    const resource = selectedResource();
    const rangeVoice = builtInPreviewVoice();
    const persistentDescription = description.control.value.trim();
    const designedMethod = method.control.value === 'design';
    const existingMode = method.control.value === 'existing';
    const currentExistingTarget = existingMode && !resource
      && Boolean(selected.voice?.selected_production_method);
    const existingTarget = existingMode && (Boolean(resource) || currentExistingTarget);
    const currentCloneIncomplete = currentExistingTarget
      && persistedFamily() === 'clone'
      && !(
        selected.voice?.clone?.reference_audio_state === 'ready'
        && Boolean(selected.voice?.clone?.exact_reference_transcript?.trim())
      );
    previewSequence.hidden = !(rangeVoice || designedMethod || existingTarget);
    previewFeedback.hidden = !(rangeVoice || designedMethod || existingTarget);
    saveAudition.hidden = !(designedMethod && designedPreview.value);
    saveHint.hidden = saveAudition.hidden;
    for (const control of laneControls.values()) {
      control.button.hidden = !(designedMethod && designedPreviewFingerprint);
    }
    setButtonContent(previewChoice, rangeVoice ? 'Preview Voice + delivery range'
      : currentCloneIncomplete ? 'Prepare reference audio'
        : existingTarget && suppliedPreviewFingerprint ? 'Regenerate Existing Voice audition'
          : existingTarget ? 'Generate Existing Voice audition'
      : designedMethod && designedPreviewFingerprint ? 'Regenerate full audition'
        : designedMethod ? 'Generate Designed Voice audition'
        : resource?.preview?.available === true ? 'Preview selected Voice'
          : 'Preview unavailable');
    if (voiceChoice.control.value === '__clear__') {
      choiceSummary.title.textContent = 'Remove current Voice assignment';
      choiceSummary.body.textContent = 'Saving will return this speaking identity to Missing voice. Source, Script, and roster identity are unchanged.';
      previewChoice.disabled = true;
      previewSequence.hidden = true;
      previewFeedback.hidden = true;
      return;
    }
    if (!resource) {
      choiceSummary.title.textContent = rangeVoice
        ? assigned.control.selectedOptions[0]?.textContent || rangeVoice
        : selected.voice?.selected_production_method
          ? 'Keep current Voice settings' : 'No saved Voice selected';
      choiceSummary.body.textContent = rangeVoice
        ? persistentDescription
          ? 'The same persistent description is held across baseline, happy, sad, and angry line directions.'
          : 'Add a persistent voice description below to audition the Voice across four deliveries.'
        : selected.voice?.selected_production_method
          ? 'The current method remains active unless you choose another Voice.'
          : 'Choose a production mode and complete its required controls.';
      previewChoice.disabled = rangeVoice || designedMethod
        ? !persistentDescription
        : !existingTarget;
      return;
    }
    choiceSummary.title.textContent = resource.name;
    choiceSummary.body.textContent = rangeVoice
      ? persistentDescription
        ? `${resource.method_label}. The persistent description remains active across all four line directions.`
        : `${resource.method_label}. Add a persistent voice description below to preview its delivery range.`
      : `${resource.method_label}. ${resource.description || resource.capability?.message || ''}`.trim();
    previewChoice.disabled = rangeVoice ? !persistentDescription
      : existingTarget ? false : resource.preview?.available !== true;
  };

  saveAudition.addEventListener('click', async () => {
    if (!designedPreview.value) return;
    designedPreview.dataset.useAsClone = 'true';
    onDirty?.();
    saveAudition.disabled = true;
    setButtonContent(saveAudition, 'Saving audition…', true);
    previewFeedback.hidden = false;
    previewFeedback.textContent = 'Saving the complete audition package and assigning it as this character’s Production Voice…';
    const saved = await onSaveAudition?.();
    if (saved || signal.aborted || !saveAudition.isConnected) return;
    designedPreview.dataset.useAsClone = 'false';
    saveAudition.disabled = false;
    setButtonContent(saveAudition, 'Retry saving audition');
    previewFeedback.textContent = 'The audition remains intact. Retry saving when ready.';
  });

  for (const [lane, control] of laneControls) {
    control.button.addEventListener('click', async () => {
      if (!designedPreviewFingerprint) return;
      const generation = designedPreviewGeneration;
      for (const candidate of laneControls.values()) candidate.button.disabled = true;
      setLaneLoading(control, true);
      setGenerating(true, `Regenerating ${control.label} with the same identity…`);
      previewFeedback.hidden = false;
      previewFeedback.textContent = `Regenerating ${control.label} only, then replaying the complete baseline, happy, sad, and angry audition…`;
      const result = await api.post('/api/voice_design/range-preview/regenerate', {
        preview_fingerprint: designedPreviewFingerprint,
        lane,
      }, { signal, timeout: AUDITION_TIMEOUT_MS });
      if (signal.aborted || generation !== designedPreviewGeneration) return;
      if (!result.ok || !result.data?.audio_url) {
        previewFeedback.textContent = typeof result.data?.detail === 'object'
          ? result.data.detail.message || `The ${control.label} lane could not be regenerated.`
          : result.error || `The ${control.label} lane could not be regenerated.`;
        syncLaneControls(designedPreviewResult);
        setGenerating(false);
        return;
      }
      designedPreviewResult = result.data;
      designedPreviewFingerprint = String(result.data.preview_fingerprint || '');
      syncLaneControls(result.data);
      setGenerating(false);
      shell.player.set({
        state: 'playing',
        src: result.data.audio_url,
        position: 0,
        title: `${selected.display_name} · Updated Designed Voice delivery range`,
        subtitle: `${control.label} regenerated · full baseline → happy → sad → angry montage`,
      });
      const subtleLabels = (result.data.warnings || [])
        .filter((warning) => warning?.code === 'audition_lane_subtle')
        .map((warning) => warning.label)
        .filter(Boolean);
      const textReviewLabels = (result.data.warnings || [])
        .filter((warning) => warning?.code === 'audition_text_unverified')
        .map((warning) => warning.label)
        .filter(Boolean);
      previewFeedback.textContent = textReviewLabels.length
        ? `${control.label} regenerated. Automatic transcription was uncertain for ${textReviewLabels.join(' and ')}; the audio is available, so judge those lanes by listening.`
        : subtleLabels.length
        ? `${control.label} regenerated. Replaying all four lanes. ${subtleLabels.join(' and ')} ${subtleLabels.length === 1 ? 'is' : 'are'} still acoustically subtle, so judge by listening or regenerate again.`
        : `${control.label} regenerated. Replaying the complete four-part audition with the other three lanes unchanged.`;
    });
  }

  previewChoice.addEventListener('click', async () => {
    const resource = selectedResource();
    const rangeVoice = builtInPreviewVoice();
    const designedMethod = method.control.value === 'design';
    const existingMode = method.control.value === 'existing';
    const currentExistingTarget = existingMode && !resource
      && Boolean(selected.voice?.selected_production_method);
    const existingTarget = existingMode && (Boolean(resource) || currentExistingTarget);
    const currentCloneIncomplete = currentExistingTarget
      && persistedFamily() === 'clone'
      && !(
        selected.voice?.clone?.reference_audio_state === 'ready'
        && Boolean(selected.voice?.clone?.exact_reference_transcript?.trim())
      );
    if (existingTarget) {
      if (currentCloneIncomplete) {
        onOpenWorkflow('audio-preparer', previewChoice);
        return;
      }
      const regenerateFull = Boolean(suppliedPreviewFingerprint);
      previewChoice.disabled = true;
      setButtonContent(
        previewChoice,
        regenerateFull ? 'Regenerating Existing Voice audition…' : 'Generating Existing Voice audition…',
        true,
      );
      setGenerating(
        true,
        regenerateFull
          ? 'Rebuilding all four deliveries from the saved identity…'
          : 'Using the saved Voice identity for all four deliveries…',
      );
      previewFeedback.hidden = false;
      previewFeedback.textContent = (
        'Generating baseline, happy, sad, and angry from the exact saved Voice '
        + 'with this character’s direction, pitch, pace, and level adjustments.'
      );
      const result = await api.post('/api/voice-library/supplied-range-preview', {
        ...(resource
          ? { voice_id: resource.voice_id }
          : { character_id: selected.character_id }),
        voice_overlay: currentVoiceOverlay(),
        force_regenerate: regenerateFull,
      }, { signal, timeout: AUDITION_TIMEOUT_MS });
      if (signal.aborted) return;
      if (!result.ok || !result.data?.audio_url) {
        previewFeedback.textContent = typeof result.data?.detail === 'object'
          ? result.data.detail.message || 'The Existing Voice audition could not be generated.'
          : result.error || 'The Existing Voice audition could not be generated.';
        setGenerating(false);
        update();
        return;
      }
      suppliedPreviewFingerprint = String(result.data.preview_fingerprint || '');
      shell.player.set({
        state: 'playing',
        src: result.data.audio_url,
        position: 0,
        title: `${selected.display_name} · Existing Voice delivery range`,
        subtitle: 'Saved identity → baseline → happy → sad → angry',
      });
      setGenerating(false);
      update();
      previewFeedback.textContent = (
        `${regenerateFull ? 'Existing Voice audition regenerated' : 'Existing Voice audition ready'}. `
        + 'All four deliveries use the exact saved identity plus this character’s adjustments.'
      );
      return;
    }
    if (rangeVoice) {
      previewChoice.disabled = true;
      setButtonContent(previewChoice, 'Generating four-part preview…', true);
      setGenerating(true, 'Generating baseline, happy, sad, and angry…');
      previewFeedback.hidden = false;
      previewFeedback.textContent = 'Generating baseline, happy, sad, and angry deliveries with one persistent description…';
      const result = await api.post('/api/voice-library/built-in-range-preview', {
        voice: rangeVoice,
        persistent_description: description.control.value.trim(),
      }, { signal, timeout: AUDITION_TIMEOUT_MS });
      if (signal.aborted) return;
      if (!result.ok || !result.data?.audio_url) {
        previewFeedback.textContent = typeof result.data?.detail === 'object'
          ? result.data.detail.message || 'The Voice range preview could not be generated.'
          : result.error || 'The Voice range preview could not be generated.';
        update();
        setGenerating(false);
        return;
      }
      shell.player.set({
        state: 'playing',
        src: result.data.audio_url,
        position: 0,
        title: `${rangeVoice.replaceAll('_', ' ')} delivery range`,
        subtitle: 'Baseline → Happy → Sad → Angry · persistent description applied throughout',
      });
      update();
      setGenerating(false);
      previewFeedback.textContent = 'Playing baseline, happy, sad, and angry in succession with the persistent description applied throughout.';
      return;
    }
    if (designedMethod) {
      const regenerateFull = Boolean(designedPreviewFingerprint);
      const previewGeneration = ++designedPreviewGeneration;
      designedPreview.value = '';
      designedPreview.dataset.useAsClone = 'false';
      designedPreview.dataset.previewFingerprint = '';
      designedPreviewFingerprint = '';
      designedPreviewResult = null;
      syncLaneControls();
      saveAudition.hidden = true;
      saveHint.hidden = true;
      previewChoice.disabled = true;
      setButtonContent(
        previewChoice,
        regenerateFull ? 'Regenerating full audition…' : 'Generating audition…',
        true,
      );
      setGenerating(
        true,
        regenerateFull
          ? 'Rebuilding the identity and all four deliveries…'
          : 'Designing one identity and four deliveries…',
      );
      previewFeedback.hidden = false;
      const voiceDescription = description.control.value.trim();
      const isCurrentPreview = () => previewGeneration === designedPreviewGeneration
        && description.control.value.trim() === voiceDescription;
      previewFeedback.textContent = 'Checking the Designed Voice definition…';
      const accentStatus = await api.post('/api/voice_design/accent_status', {
        description: voiceDescription,
        output_language: 'English',
      }, { signal });
      if (signal.aborted || !isCurrentPreview()) return;
      const accentLabel = accentStatus.ok && accentStatus.data?.accent_detected
        ? String(accentStatus.data.accent_label || '').trim() : '';
      previewFeedback.textContent = accentLabel
        ? `Designing one ${accentLabel} neutral identity, then using that exact voice for all four Fish deliveries…`
        : 'Designing one clean neutral identity, then using that exact voice for baseline, happy, sad, and angry…';
      const result = await api.post('/api/voice_design/range-preview', {
        description: voiceDescription,
        persona_context: auditionPersonaContext,
        sample_text: auditionText,
        language: 'English',
        force_regenerate: regenerateFull,
      }, { signal, timeout: AUDITION_TIMEOUT_MS });
      if (signal.aborted || !isCurrentPreview()) return;
      if (!result.ok || !result.data?.audio_url) {
        previewFeedback.textContent = typeof result.data?.detail === 'object'
          ? result.data.detail.message || 'The Designed Voice audition could not be generated.'
          : result.error || 'The Designed Voice audition could not be generated.';
        update();
        setGenerating(false);
        return;
      }
      const appliedAccentLabel = result.data?.accent_pipeline?.applied
        ? String(result.data.accent_pipeline.label || '').trim() : '';
      designedPreview.value = String(result.data.clone_source_url || '').split('/').at(-1) || '';
      designedPreviewFingerprint = String(result.data.preview_fingerprint || '');
      designedPreview.dataset.previewFingerprint = designedPreviewFingerprint;
      designedPreviewResult = result.data;
      designedPreview.dataset.sampleText = String(
        result.data.clone_source_text || auditionText,
      );
      shell.player.set({
        state: 'playing', src: result.data.audio_url, position: 0,
        title: `${selected.display_name} · Designed Voice delivery range`,
        subtitle: result.data.all_lanes_distinct === false
          ? 'Baseline → happy → sad → angry · one or more lanes are subtle'
          : 'VoiceDesign baseline → Fish happy → sad → angry',
      });
      update();
      syncLaneControls(result.data);
      setGenerating(false);
      const subtleLabels = (result.data.warnings || [])
        .filter((warning) => warning?.code === 'audition_lane_subtle')
        .map((warning) => warning.label)
        .filter(Boolean);
      const textReviewLabels = (result.data.warnings || [])
        .filter((warning) => warning?.code === 'audition_text_unverified')
        .map((warning) => warning.label)
        .filter(Boolean);
      if (textReviewLabels.length) {
        previewFeedback.textContent = `${regenerateFull ? 'Full audition regenerated' : 'Audition ready'}. Automatic transcription was uncertain for ${textReviewLabels.join(' and ')}, but Alexandria kept the identity-safe audition audio for listening review instead of discarding it.`;
      } else if (subtleLabels.length) {
        previewFeedback.textContent = `${regenerateFull ? 'Full audition regenerated' : 'Audition ready'}. All four lanes use the same neutral identity recording. ${subtleLabels.join(' and ')} remained closer to neutral than requested; regenerate only ${subtleLabels.length === 1 ? 'that lane' : 'the weak lanes'} as needed.`;
      } else {
        previewFeedback.textContent = appliedAccentLabel
          ? `${regenerateFull ? 'Full audition regenerated' : 'Audition ready'}. Alexandria’s ${appliedAccentLabel} accent pipeline created the neutral baseline; Fish uses that exact identity for happy, sad, and angry. You can regenerate any emotional lane without changing the identity or other three.`
          : `${regenerateFull ? 'Full audition regenerated' : 'Audition ready'}. VoiceDesign created the neutral baseline; Fish uses that exact identity for happy, sad, and angry. You can regenerate any emotional lane without changing the identity or other three.`;
      }
      return;
    }
    if (!resource?.preview?.url) {
      previewFeedback.hidden = false;
      previewFeedback.textContent = 'This Voice does not expose an audition from the current Cast workflow.';
      return;
    }
    shell.player.set({
      state: 'playing',
      src: resource.preview.url,
      position: 0,
      title: resource.name,
      subtitle: resource.method_label,
    });
  });

  return Object.freeze({
    choiceSummary,
    designedPreview,
    preview: {
      studio,
      previewChoice,
      previewSequence,
      previewFeedback,
      saveAudition,
    },
    invalidateDesignedPreview,
    invalidateExistingPreview,
    update,
  });
}
