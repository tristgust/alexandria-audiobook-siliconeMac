'use strict';

import { castAuditionPersonaContext, castAuditionText, castText } from './cast_model.js';

const UI = globalThis.AlexandriaUI;
const AUDITION_TIMEOUT_MS = 300000;

export function createCastVoiceAudition({
  api, signal, shell, selected, onOpenWorkflow, assignableVoices,
  voiceChoice, method, assigned, description, editorFact, onDirty,
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
    variant: 'quiet',
    size: 'compact',
    disabled: true,
    attributes: { 'data-cast-preview-choice': '' },
  });
  const previewSequence = document.createElement('div');
  previewSequence.className = 'cast-profile__voice-range';
  previewSequence.hidden = true;
  previewSequence.append(castText('span', 'metadata', 'Preview sequence'));
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
    const status = castText('span', 'metadata cast-profile__voice-range-status', direction);
    item.append(castText('strong', '', label), status);
    if (lane !== 'baseline') {
      const regenerate = UI.button({
        label: `Regenerate ${label.toLowerCase()}`,
        variant: 'quiet',
        size: 'compact',
        attributes: { 'data-cast-regenerate-audition-lane': lane },
      });
      regenerate.hidden = true;
      item.append(regenerate);
      laneControls.set(lane, { button: regenerate, status, direction, label });
    }
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
  const useAsClone = UI.button({
    label: 'Use audition as clone source',
    variant: 'secondary',
    size: 'compact',
    attributes: {
      'data-cast-use-audition-as-clone': '',
      'aria-pressed': 'false',
    },
  });
  useAsClone.hidden = true;
  const auditionText = castAuditionText(selected);
  const auditionPersonaContext = castAuditionPersonaContext(selected);
  designedPreview.dataset.sampleText = auditionText;
  let designedPreviewGeneration = 0;
  let designedPreviewFingerprint = '';
  let designedPreviewResult = null;

  const syncLaneControls = (result = null) => {
    const sequence = result?.sequence || [];
    const byLane = new Map(sequence.map((item) => [item.id, item]));
    for (const [lane, control] of laneControls) {
      const laneResult = byLane.get(lane);
      control.button.hidden = !designedPreviewFingerprint;
      control.button.disabled = !designedPreviewFingerprint;
      control.button.textContent = `Regenerate ${control.label.toLowerCase()}`;
      control.status.textContent = laneResult?.variance_status === 'subtle'
        ? `${control.direction} · Subtle`
        : laneResult ? `${control.direction} · Distinct` : control.direction;
      control.status.dataset.state = laneResult?.variance_status || '';
    }
  };

  const invalidateDesignedPreview = () => {
    const wasDesignedPreview = Boolean(designedPreview.value)
      || ['design', 'designed', 'designed_voice', 'voice_design'].includes(method.control.value);
    designedPreviewGeneration += 1;
    designedPreview.value = '';
    designedPreview.dataset.useAsClone = 'false';
    designedPreviewFingerprint = '';
    designedPreviewResult = null;
    syncLaneControls();
    useAsClone.hidden = true;
    useAsClone.setAttribute('aria-pressed', 'false');
    useAsClone.textContent = 'Use audition as clone source';
    if (!wasDesignedPreview) return;
    previewFeedback.hidden = false;
    previewFeedback.textContent = 'The Designed Voice definition changed, so the old audition was cleared. You can save the definition now or generate another audition first.';
  };

  const selectedResource = () => assignableVoices.find(
    (item) => item.voice_id === voiceChoice.control.value,
  );
  const builtInPreviewVoice = () => {
    const resource = selectedResource();
    if (resource?.method === 'built_in') return resource.key || resource.name;
    if (resource) return '';
    return ['custom', 'builtin', 'built_in', 'standard', 'saved_voice'].includes(method.control.value)
      ? assigned.control.value : '';
  };
  const update = () => {
    const resource = selectedResource();
    const rangeVoice = builtInPreviewVoice();
    const persistentDescription = description.control.value.trim();
    const designedMethod = ['design', 'designed', 'designed_voice', 'voice_design']
      .includes(method.control.value);
    previewSequence.hidden = !(rangeVoice || designedMethod);
    previewFeedback.hidden = !(rangeVoice || designedMethod);
    useAsClone.hidden = !(designedMethod && designedPreview.value);
    for (const control of laneControls.values()) {
      control.button.hidden = !(designedMethod && designedPreviewFingerprint);
    }
    useAsClone.setAttribute(
      'aria-pressed',
      designedPreview.dataset.useAsClone === 'true' ? 'true' : 'false',
    );
    useAsClone.textContent = designedPreview.dataset.useAsClone === 'true'
      ? 'Save as supplied-recording clone · undo'
      : 'Use audition as clone source';
    previewChoice.textContent = rangeVoice ? 'Preview Voice + delivery range'
      : designedMethod ? 'Generate Designed Voice audition'
        : resource?.preview?.available === true ? 'Preview selected Voice'
          : 'Open Voice designer';
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
      previewChoice.disabled = (rangeVoice || designedMethod) ? !persistentDescription : false;
      return;
    }
    choiceSummary.title.textContent = resource.name;
    choiceSummary.body.textContent = rangeVoice
      ? persistentDescription
        ? `${resource.method_label}. The persistent description remains active across all four line directions.`
        : `${resource.method_label}. Add a persistent voice description below to preview its delivery range.`
      : `${resource.method_label}. ${resource.description || resource.capability?.message || ''}`.trim();
    previewChoice.disabled = rangeVoice ? !persistentDescription : resource.preview?.available !== true;
  };

  useAsClone.addEventListener('click', () => {
    if (!designedPreview.value) return;
    const selectedForClone = designedPreview.dataset.useAsClone === 'true';
    designedPreview.dataset.useAsClone = selectedForClone ? 'false' : 'true';
    update();
    previewFeedback.hidden = false;
    previewFeedback.textContent = selectedForClone
      ? 'The audition remains temporary. Saving will keep the Designed Voice definition.'
      : 'Clone conversion selected. Saving will preserve the clean VoiceDesign identity seed—not the emotional montage—as a supplied-recording clone with its exact transcript.';
    onDirty?.();
  });

  for (const [lane, control] of laneControls) {
    control.button.addEventListener('click', async () => {
      if (!designedPreviewFingerprint) return;
      const generation = designedPreviewGeneration;
      for (const candidate of laneControls.values()) candidate.button.disabled = true;
      control.button.textContent = `Regenerating ${control.label.toLowerCase()}…`;
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
        return;
      }
      designedPreviewResult = result.data;
      designedPreviewFingerprint = String(result.data.preview_fingerprint || '');
      syncLaneControls(result.data);
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
      previewFeedback.textContent = subtleLabels.length
        ? `${control.label} regenerated. Replaying all four lanes. ${subtleLabels.join(' and ')} ${subtleLabels.length === 1 ? 'is' : 'are'} still acoustically subtle, so judge by listening or regenerate again.`
        : `${control.label} regenerated. Replaying the complete four-part audition with the other three lanes unchanged.`;
    });
  }

  previewChoice.addEventListener('click', async () => {
    const resource = selectedResource();
    const rangeVoice = builtInPreviewVoice();
    const designedMethod = ['design', 'designed', 'designed_voice', 'voice_design']
      .includes(method.control.value);
    if (rangeVoice) {
      previewChoice.disabled = true;
      previewChoice.textContent = 'Generating four-part preview…';
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
      previewFeedback.textContent = 'Playing baseline, happy, sad, and angry in succession with the persistent description applied throughout.';
      return;
    }
    if (designedMethod) {
      const previewGeneration = ++designedPreviewGeneration;
      designedPreview.value = '';
      designedPreview.dataset.useAsClone = 'false';
      designedPreviewFingerprint = '';
      designedPreviewResult = null;
      syncLaneControls();
      useAsClone.hidden = true;
      previewChoice.disabled = true;
      previewChoice.textContent = 'Generating audition…';
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
        ? `Designing the ${accentLabel} neutral identity plus temporary emotion references, then generating four Fish scenes…`
        : 'Designing one clean neutral identity plus temporary persona-matched emotion references, then generating four Fish scenes…';
      const result = await api.post('/api/voice_design/range-preview', {
        description: voiceDescription,
        persona_context: auditionPersonaContext,
        sample_text: auditionText,
        language: 'English',
      }, { signal, timeout: AUDITION_TIMEOUT_MS });
      if (signal.aborted || !isCurrentPreview()) return;
      if (!result.ok || !result.data?.audio_url) {
        previewFeedback.textContent = typeof result.data?.detail === 'object'
          ? result.data.detail.message || 'The Designed Voice audition could not be generated.'
          : result.error || 'The Designed Voice audition could not be generated.';
        update();
        return;
      }
      const appliedAccentLabel = result.data?.accent_pipeline?.applied
        ? String(result.data.accent_pipeline.label || '').trim() : '';
      designedPreview.value = String(result.data.clone_source_url || '').split('/').at(-1) || '';
      designedPreviewFingerprint = String(result.data.preview_fingerprint || '');
      designedPreviewResult = result.data;
      designedPreview.dataset.sampleText = String(
        result.data.clone_source_text || auditionText,
      );
      shell.player.set({
        state: 'playing', src: result.data.audio_url, position: 0,
        title: `${selected.display_name} · Designed Voice delivery range`,
        subtitle: result.data.all_lanes_distinct === false
          ? 'Baseline → happy → sad → angry · one or more lanes are subtle'
          : 'VoiceDesign references → Fish baseline → happy → sad → angry',
      });
      update();
      syncLaneControls(result.data);
      const subtleLabels = (result.data.warnings || [])
        .filter((warning) => warning?.code === 'audition_lane_subtle')
        .map((warning) => warning.label)
        .filter(Boolean);
      if (subtleLabels.length) {
        previewFeedback.textContent = `Audition ready. ${subtleLabels.join(' and ')} remained closer to neutral than requested. Listen to all four lanes, then regenerate only ${subtleLabels.length === 1 ? 'that lane' : 'the weak lanes'} as needed; the other portions stay unchanged.`;
      } else {
        previewFeedback.textContent = appliedAccentLabel
          ? `Audition ready. Alexandria’s ${appliedAccentLabel} accent pipeline created the clean neutral seed and temporary emotion references; Fish performs baseline, happy, sad, and angry scenes. You can regenerate any emotional lane without changing the other three.`
          : 'Audition ready. VoiceDesign created the clean neutral seed and temporary persona-matched emotion references; Fish performs baseline, happy, sad, and angry scenes. You can regenerate any emotional lane without changing the other three.';
      }
      return;
    }
    if (!resource?.preview?.url) {
      onOpenWorkflow('voice-designer', previewChoice);
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
    preview: { previewChoice, previewSequence, previewFeedback, useAsClone },
    invalidateDesignedPreview,
    update,
  });
}
