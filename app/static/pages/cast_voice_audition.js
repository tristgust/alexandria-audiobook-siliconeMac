'use strict';

import { castAuditionText, castText } from './cast_model.js';

const UI = globalThis.AlexandriaUI;

export function createCastVoiceAudition({
  api, signal, shell, selected, onOpenWorkflow, assignableVoices,
  voiceChoice, method, assigned, description, editorFact,
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
  const auditionText = castAuditionText(selected);
  designedPreview.dataset.sampleText = auditionText;
  let designedPreviewGeneration = 0;

  const invalidateDesignedPreview = () => {
    const wasDesignedPreview = Boolean(designedPreview.value)
      || ['design', 'designed', 'designed_voice', 'voice_design'].includes(method.control.value);
    designedPreviewGeneration += 1;
    designedPreview.value = '';
    if (!wasDesignedPreview) return;
    previewFeedback.hidden = false;
    previewFeedback.textContent = 'The Designed Voice definition changed. Generate a new audition before saving.';
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
    previewSequence.hidden = !rangeVoice;
    previewFeedback.hidden = !(rangeVoice || designedMethod);
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
      }, { signal });
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
        ? `Generating an audition for the ${accentLabel} accent definition…`
        : 'Generating this project’s Designed Voice audition…';
      const result = await api.post('/api/voice_design/preview', {
        description: voiceDescription,
        sample_text: auditionText,
        language: 'English',
      }, { signal });
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
      designedPreview.value = String(result.data.audio_url).split('/').at(-1) || '';
      shell.player.set({
        state: 'playing', src: result.data.audio_url, position: 0,
        title: `${selected.display_name} · Designed Voice audition`,
        subtitle: 'Current project · save changes to keep this Voice',
      });
      update();
      previewFeedback.textContent = appliedAccentLabel
        ? `Audition ready. Alexandria’s ${appliedAccentLabel} accent pipeline was applied; save changes to keep this project Designed Voice.`
        : 'Audition ready. Save changes to keep this as a project Designed Voice.';
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
    preview: { previewChoice, previewSequence, previewFeedback },
    invalidateDesignedPreview,
    update,
  });
}
