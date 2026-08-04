'use strict';

import { VOICE_METHODS, castText, castWords } from './cast_model.js';
import { createCastVoiceAudition } from './cast_voice_audition.js';

function dossierText(value) {
  if (typeof value === 'string') return value.trim();
  if (value && typeof value === 'object') return String(value.value || '').trim();
  return '';
}

function acousticClause(value, kind = '') {
  let text = dossierText(value).replace(/[.!?]+$/, '').trim();
  if (!text) return '';
  if (kind === 'accent') {
    text = text
      .replace(/^no specific accent is established;\s*use\s*/i, '')
      .replace(/^accent is unspecified;\s*(?:use|prioritize)\s*/i, '')
      .replace(/^no unsupported regional accent is imposed$/i, 'neutral diction');
  }
  text = text.split(/\s+(?:that|while|when|under|as though|so that)\b/i, 1)[0].trim();
  return text;
}

export function castDesignedVoiceIdentityDefinition(dossier = {}) {
  const authored = dossierText(dossier.designed_voice_description);
  const physical = [
    acousticClause(dossier.pitch),
    acousticClause(dossier.weight_and_resonance),
    acousticClause(dossier.texture_and_timbre),
    acousticClause(dossier.accent_and_language, 'accent'),
  ].filter(Boolean);
  if (physical.length < 3) return authored;
  const firstSentence = authored.match(/^.*?[.!?](?:\s|$)/)?.[0]?.trim() || '';
  const identityPrefix = /\b(?:woman|man|female|male|adult|young|older|mature|timeless|nonhuman|hith|machine)\b/i
    .test(firstSentence) ? firstSentence.replace(/[.!?]+$/, '') : '';
  return [...new Set([identityPrefix, ...physical].filter(Boolean))].join('; ');
}

export function createCastVoiceAssignmentForm({
  api, signal, shell, selected, library, onOpenWorkflow,
  fieldControl, editorFact, onDirty, onSaveAudition,
}) {
  const value = selected.voice || {};
  const assignableVoices = (library.voices || []).filter(
    (item) => item.assignment?.supported === true && item.method !== 'built_in',
  );
  const aliasTarget = String(value.alias?.target || '').trim();
  const aliasedProjectVoice = aliasTarget
    ? assignableVoices.find((item) => item.key === aliasTarget
      && item.technical_details?.scope === 'project_configuration')
    : null;
  const currentLibraryVoiceId = value.library_voice_id || aliasedProjectVoice?.voice_id || '';
  const currentLibraryVoice = (library.voices || []).find(
    (item) => item.voice_id === currentLibraryVoiceId,
  );
  const voiceChoice = fieldControl({
    id: 'cast-voice-choice', label: 'Existing Voice', kind: 'select', value: currentLibraryVoiceId,
    options: [
      {
        value: '',
        label: value.configuration_key || value.selected_production_method
          ? 'Keep current Voice' : 'Choose a saved Voice…',
      },
      ...(value.configuration_key || value.selected_production_method
        ? [{ value: '__clear__', label: 'Remove current Voice assignment' }] : []),
      ...assignableVoices.map((item) => ({
        value: item.voice_id, label: `${item.name} — ${item.method_label}`,
      })),
    ],
    description: 'Pick a ready saved Voice by name. Its approved method and settings come with it.',
  });
  voiceChoice.wrapper.classList.add('cast-profile__voice-choice');
  voiceChoice.control.dataset.castVoiceChoice = '';
  const rawMethodValue = value.selected_production_method || 'custom';
  const normalizedMethod = ['controlled_clone', 'instruction_controlled_clone'].includes(rawMethodValue)
    ? 'clone' : rawMethodValue;
  const methodValue = currentLibraryVoice?.method === 'built_in' ? 'custom'
    : ['adapter', 'alias'].includes(currentLibraryVoice?.method) ? currentLibraryVoice.method
      : currentLibraryVoiceId ? 'existing' : normalizedMethod;
  const baseMethods = [['existing', 'Existing saved Voice'], ...VOICE_METHODS];
  const methods = baseMethods.some(([method]) => method === methodValue)
    ? baseMethods : [[methodValue, castWords(methodValue)], ...baseMethods];
  const method = fieldControl({
    id: 'cast-voice-method', label: 'Production mode', kind: 'select', value: methodValue,
    options: methods.map(([option, label]) => ({ value: option, label })),
    description: 'The controls below update immediately for the selected mode.',
  });
  method.wrapper.classList.add('cast-profile__editor-method');
  method.control.dataset.castVoiceMethod = '';
  const builtInOptions = (library.voices || [])
    .filter((item) => item.method === 'built_in' && item.assignment?.supported === true)
    .map((item) => ({ value: item.key, label: item.name }));
  if (value.selected_voice && !builtInOptions.some((item) => item.value === value.selected_voice)) {
    builtInOptions.unshift({ value: value.selected_voice, label: value.selected_voice });
  }
  const assigned = fieldControl({
    id: 'cast-assigned-voice', label: 'Built-in Voice', kind: 'select',
    value: value.selected_voice || builtInOptions[0]?.value || '',
    options: builtInOptions.length ? builtInOptions : [{ value: '', label: 'No built-in Voices available' }],
    message: 'Choose the built-in speaker that carries the persistent description and line directions.',
  });
  assigned.wrapper.classList.add('cast-profile__editor-assigned');
  assigned.control.dataset.castAssignedVoice = '';
  const transcript = String(value.clone?.exact_reference_transcript || '').trim();
  const referenceIdentity = editorFact({
    className: 'cast-profile__editor-reference', iconName: 'waveform',
    label: 'Reference identity', title: 'Saved supplied recording',
    body: transcript ? `${transcript.split(/\s+/).length.toLocaleString()}-word transcript attached.`
      : 'Exact transcript required before production.',
  });
  const delivery = editorFact({
    className: 'cast-profile__editor-delivery', iconName: 'sliders',
    label: 'Delivery control', title: 'Standard per-line directions',
    body: 'Uses the delivery direction stored with each Script line.',
  });
  const description = fieldControl({
    id: 'cast-voice-description', label: 'Persistent voice description', kind: 'textarea',
    value: value.persistent_voice_description || '',
    placeholder: 'Describe age, source-supported gender presentation, accent, vocal texture, rhythm, and emotional range',
    description: 'Optional for a clone, but useful for keeping long-form delivery consistent.',
  });
  description.wrapper.classList.add('cast-profile__editor-description');
  description.control.dataset.castVoiceDescription = '';
  description.control.rows = 3;
  const importedDefinition = castDesignedVoiceIdentityDefinition(value.imported_dossier || {});
  let descriptionTouched = false;
  let previousMethod = method.control.value;
  const audition = createCastVoiceAudition({
    api, signal, shell, selected, onOpenWorkflow, assignableVoices,
    voiceChoice, method, assigned, description, editorFact,
    onDirty, onSaveAudition,
  });
  description.control.addEventListener('input', () => {
    descriptionTouched = true;
    audition.invalidateDesignedPreview();
    audition.update();
  });
  const catalog = document.createElement('div');
  catalog.className = 'cast-profile__voice-catalog';
  catalog.append(voiceChoice.wrapper, audition.choiceSummary.node);
  const grid = document.createElement('div');
  grid.className = 'cast-profile__field-grid';
  grid.append(assigned.wrapper, referenceIdentity.node, delivery.node, description.wrapper);

  const syncMethodFields = () => {
    const selectedMethod = method.control.value;
    const cloneMethod = ['clone', 'supplied_recording_clone'].includes(selectedMethod);
    const designedMethod = ['design', 'designed', 'designed_voice', 'voice_design'].includes(selectedMethod);
    const adapterMethod = ['adapter', 'lora', 'trained_voice'].includes(selectedMethod);
    const aliasMethod = selectedMethod === 'alias';
    const existingMethod = selectedMethod === 'existing';
    const builtInMethod = ['custom', 'builtin', 'built_in', 'standard', 'saved_voice'].includes(selectedMethod);
    const controlledMethod = cloneMethod && Boolean(value.clone?.controlled_capability);
    const previousWasDesigned = ['design', 'designed', 'designed_voice', 'voice_design'].includes(previousMethod);
    if (designedMethod && !previousWasDesigned && !descriptionTouched && importedDefinition) {
      description.control.value = importedDefinition;
      description.control.dataset.seededFromImportedDossier = 'true';
    }
    previousMethod = selectedMethod;
    // Approved saved Voices are always immediately available; choosing one below
    // switches the production method to `existing` without a separate Cast step.
    catalog.hidden = false;
    grid.hidden = existingMethod;
    assigned.wrapper.hidden = !builtInMethod;
    referenceIdentity.node.hidden = !cloneMethod;
    description.wrapper.hidden = adapterMethod || aliasMethod;
    [...voiceChoice.control.options].forEach((option) => {
      if (!option.value || option.value === '__clear__') { option.hidden = false; return; }
      const resource = assignableVoices.find((item) => item.voice_id === option.value);
      option.hidden = adapterMethod ? resource?.method !== 'adapter'
        : aliasMethod ? resource?.method !== 'alias' : false;
    });
    const selectedResource = assignableVoices.find((item) => item.voice_id === voiceChoice.control.value);
    if ((adapterMethod && selectedResource?.method !== 'adapter')
      || (aliasMethod && selectedResource?.method !== 'alias')) voiceChoice.control.value = '';
    const descriptionLabel = description.wrapper.querySelector('.field__label');
    if (descriptionLabel) descriptionLabel.textContent = designedMethod
      ? 'Designed Voice definition' : 'Persistent voice description';
    const descriptionHelp = description.wrapper.querySelector('#cast-voice-description-description');
    if (descriptionHelp) descriptionHelp.textContent = designedMethod
      ? 'Defines the Voice identity; each Script line direction controls its immediate performance.'
      : builtInMethod
        ? 'Applied to every line so the Voice identity stays consistent while delivery directions change.'
        : 'Optional for a clone, but useful for keeping long-form delivery consistent.';
    description.control.required = designedMethod;
    delivery.title.textContent = controlledMethod ? 'Instruction-controlled'
      : cloneMethod ? 'Standard clone delivery' : designedMethod ? 'Definition plus line directions'
        : adapterMethod ? 'Adapter-supported delivery' : aliasMethod ? 'Shared identity, independent line directions'
          : 'Standard per-line directions';
    delivery.body.textContent = controlledMethod
      ? 'Reads tone, pacing, emphasis, and emotion from each line’s direction.'
      : cloneMethod ? 'Uses per-line directions. Instruction control is enabled after a listening check.'
        : designedMethod ? 'The definition creates the Voice identity; each Script direction controls the immediate performance.'
          : adapterMethod ? 'Choose a production-approved adapter in this mode’s controls.'
            : aliasMethod ? 'Choose an approved shared Voice in this mode’s controls.'
              : 'Uses the direction stored with each Script line.';
    grid.dataset.methodFamily = existingMethod ? 'existing' : controlledMethod ? 'controlled-clone'
      : cloneMethod ? 'clone' : designedMethod ? 'designed' : adapterMethod ? 'adapter'
        : aliasMethod ? 'alias' : 'built-in';
    requestAnimationFrame(() => {
      const referenceSection = method.control.closest('[data-cast-profile]')
        ?.querySelector('[data-cast-section="reference"]');
      if (referenceSection) referenceSection.hidden = !cloneMethod;
    });
  };
  const setup = document.createElement('section');
  setup.className = 'cast-profile__voice-setup';
  const setupHeader = document.createElement('header');
  setupHeader.append(castText('h4', '', 'Choose a production mode'),
    castText('p', 'metadata', 'Choose an approved saved Voice directly, or select another production mode.'));
  setup.append(setupHeader, method.wrapper, catalog, grid);
  voiceChoice.control.addEventListener('change', () => {
    if (voiceChoice.control.value && voiceChoice.control.value !== '__clear__') {
      method.control.value = 'existing';
      syncMethodFields();
    }
    audition.update();
    onDirty();
  });
  method.control.addEventListener('change', () => {
    if (!['existing', 'adapter', 'alias'].includes(method.control.value)) voiceChoice.control.value = '';
    syncMethodFields(); audition.update(); onDirty();
  });
  const clearChoice = () => { voiceChoice.control.value = ''; audition.update(); onDirty(); };
  grid.addEventListener('input', clearChoice);
  grid.addEventListener('change', clearChoice);
  syncMethodFields();
  audition.update();
  return Object.freeze({ setup, designedPreview: audition.designedPreview, preview: audition.preview });
}
