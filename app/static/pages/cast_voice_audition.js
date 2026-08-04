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
  [
    ['Baseline', 'Neutral line direction'],
    ['Happy', 'Bright and delighted'],
    ['Sad', 'Quiet and vulnerable'],
    ['Angry', 'Controlled and intense'],
  ].forEach(([label, direction]) => {
    const item = document.createElement('li');
    item.append(castText('strong', '', label), castText('span', 'metadata', direction));
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

  const invalidateDesignedPreview = () => {
    const wasDesignedPreview = Boolean(designedPreview.value)
      || ['design', 'designed', 'designed_voice', 'voice_design'].includes(method.control.value);
    designedPreviewGeneration += 1;
    designedPreview.value = '';
    designedPreview.dataset.useAsClone = 'false';
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
      designedPreview.dataset.sampleText = String(
        result.data.clone_source_text || auditionText,
      );
      shell.player.set({
        state: 'playing', src: result.data.audio_url, position: 0,
        title: `${selected.display_name} · Designed Voice delivery range`,
        subtitle: 'VoiceDesign references → Fish baseline → happy → sad → angry',
      });
      update();
      previewFeedback.textContent = appliedAccentLabel
        ? `Audition ready. Alexandria’s ${appliedAccentLabel} accent pipeline created the clean neutral seed and temporary emotion references; Fish performs four distinct baseline, happy, sad, and angry scenes. Saving the definition keeps it as Designed Voice; clone conversion preserves only the clean neutral seed.`
        : 'Audition ready. VoiceDesign created the clean neutral seed and temporary persona-matched emotion references; Fish performs four distinct baseline, happy, sad, and angry scenes. Saving the definition keeps it as Designed Voice; clone conversion preserves only the clean neutral seed.';
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
