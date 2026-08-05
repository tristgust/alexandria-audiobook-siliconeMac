'use strict';

import {
  castList, castStatus, castText, castVoiceLabel, castVoiceMethod,
} from './cast_model.js';

const UI = globalThis.AlexandriaUI;

const previewStateLabel = (preview = {}) => {
  if (preview.approved) return 'Approved for production';
  if (preview.status === 'failed') return 'Preview failed';
  if (preview.status === 'ready') return 'Ready for listening review';
  return 'Not generated';
};

const methodIcon = (selected) => {
  const method = castVoiceMethod(selected);
  if (['sound_effect', 'sound_effects', 'sfx', 'non_speech'].includes(method)) return 'waveform';
  if (['clone', 'supplied_recording_clone'].includes(method)) return 'waveform';
  if (['controlled_clone', 'instruction_controlled_clone'].includes(method)) return 'sliders';
  if (['design', 'designed', 'designed_voice', 'voice_design'].includes(method)) return 'wand';
  if (['adapter', 'lora', 'trained_voice'].includes(method)) return 'layers';
  if (method === 'alias') return 'link';
  return 'microphone';
};

const methodDescription = (selected) => {
  const method = castVoiceMethod(selected);
  if (['sound_effect', 'sound_effects', 'sfx', 'non_speech'].includes(method)) {
    return 'A persistent non-speech sound definition replaces spoken Voice synthesis for this character.';
  }
  if (selected.voice?.clone?.controlled_capability
    || ['controlled_clone', 'instruction_controlled_clone'].includes(method)) {
    return 'A supplied recording preserves vocal identity while line directions control tone, pacing, emphasis, and emotion.';
  }
  if (['clone', 'supplied_recording_clone'].includes(method)) {
    return 'A supplied recording and exact transcript preserve vocal identity across production lines.';
  }
  if (['design', 'designed', 'designed_voice', 'voice_design'].includes(method)) {
    return 'A persistent written description defines the production Voice.';
  }
  if (['adapter', 'lora', 'trained_voice'].includes(method)) {
    return 'A validated trained adapter supplies the production Voice.';
  }
  if (method === 'alias') return 'This character intentionally shares another production Voice.';
  if (!method && !selected.voice?.selected_voice) {
    return 'Choose a production-ready Voice before generating this character’s lines.';
  }
  return 'A saved Voice asset is assigned directly to this character.';
};

const factRows = (selected) => {
  const value = selected.voice || {};
  const clone = value.clone || {};
  const method = castVoiceMethod(selected);
  const listening = ['Listening check', previewStateLabel(value.preview), value.preview?.approved ? 'check' : 'play'];
  if (['sound_effect', 'sound_effects', 'sfx', 'non_speech'].includes(method)) return [
    ['Sound definition', value.sound_effect?.definition || 'Not recorded', 'waveform'],
    ['Production method', 'Non-speech sound generation', 'waveform'],
    ['Backend', value.sound_effect?.backend_status?.available ? 'Available' : 'Not installed', 'warning'],
  ];
  if (['clone', 'supplied_recording_clone', 'controlled_clone', 'instruction_controlled_clone'].includes(method)) {
    return [
      ['Clone source', clone.reference_source || 'Saved supplied recording', 'waveform'],
      ['Persistent description', value.persistent_voice_description || 'Not recorded', 'document'],
      ['Delivery control', clone.controlled_capability
        ? 'Instruction-controlled line delivery' : 'Standard per-line directions', 'sliders'],
      listening,
    ];
  }
  if (['design', 'designed', 'designed_voice', 'voice_design'].includes(method)) return [
    ['Voice definition', value.persistent_voice_description || 'Not described', 'document'],
    ['Production method', 'Generated directly from the Voice definition', 'microphone'],
    ['Delivery control', 'Line directions guide each performance', 'sliders'], listening,
  ];
  if (['adapter', 'lora', 'trained_voice'].includes(method)) return [
    ['Trained adapter', value.adapter?.id || value.selected_voice || 'Not selected', 'waveform'],
    ['Production method', 'Validated trained model', 'microphone'],
    ['Delivery control', 'Adapter-supported production settings', 'sliders'], listening,
  ];
  if (method === 'alias') return [
    ['Shared Voice', value.alias?.target || 'Not selected', 'users'],
    ['Production method', 'Uses another Cast identity’s Voice', 'users'],
    ['Delivery control', 'Uses this character’s line directions', 'sliders'], listening,
  ];
  return [
    ['Built-in Voice', value.selected_voice || 'Not selected', 'microphone'],
    ['Persistent description', value.persistent_voice_description || 'Not recorded', 'document'],
    ['Delivery control', 'Standard per-line directions', 'sliders'], listening,
  ];
};

export function createCastVoiceSummary({ editorFact }) {
  return function voiceFacts(selected) {
    const value = selected.voice || {};
    const method = castVoiceMethod(selected);
    const card = document.createElement('article');
    card.className = 'cast-profile__voice-card';
    card.dataset.voiceMethod = method || 'unassigned';
    const emblem = document.createElement('span');
    emblem.className = 'cast-profile__voice-emblem';
    emblem.setAttribute('aria-hidden', 'true');
    emblem.append(UI.icon(methodIcon(selected)));
    const copy = document.createElement('div');
    copy.className = 'cast-profile__voice-card-copy';
    copy.append(
      castText('span', 'metadata cast-profile__voice-card-eyebrow', 'Production method'),
      castText('h4', 'cast-profile__voice-card-title', castVoiceLabel(selected)),
      castText('p', 'cast-profile__voice-card-description', methodDescription(selected)),
    );
    const status = UI.status({ ...castStatus(selected), domain: 'cast' });
    status.classList.add('cast-profile__voice-card-status');
    const header = document.createElement('header');
    header.className = 'cast-profile__voice-card-header';
    header.append(emblem, copy, status);
    const facts = document.createElement('dl');
    facts.className = 'cast-profile__voice-facts';
    factRows(selected).forEach(([term, definition, iconName]) => {
      const item = document.createElement('div');
      if (term === 'Listening check') item.dataset.castListeningCheck = '';
      const marker = document.createElement('span');
      marker.className = 'cast-profile__voice-fact-icon';
      marker.setAttribute('aria-hidden', 'true');
      marker.append(UI.icon(iconName));
      item.append(marker, castText('dt', '', term), castText('dd', '', definition));
      facts.append(item);
    });
    card.append(header, facts);
    const dossier = value.imported_dossier || {};
    if (!Object.keys(dossier).length) return card;
    const dossierSection = document.createElement('section');
    dossierSection.className = 'cast-profile__voice-dossier';
    const dossierHeader = document.createElement('header');
    dossierHeader.append(
      castText('span', 'metadata cast-profile__voice-card-eyebrow', 'Imported Voice dossier'),
      castText('h5', '', 'Persona and acoustic design'),
      castText('p', 'metadata', 'ChatGPT-produced casting and design evidence. This does not assign or replace the production Voice.'),
    );
    dossierSection.append(dossierHeader);
    const summaries = document.createElement('div');
    summaries.className = 'cast-profile__voice-dossier-summaries';
    if (dossier.persona_summary) summaries.append(editorFact({
      iconName: 'users', label: 'Performance persona',
      title: 'Stable character identity', body: dossier.persona_summary,
    }).node);
    if (dossier.designed_voice_description) summaries.append(editorFact({
      iconName: 'microphone', label: 'Designed Voice definition',
      title: 'Synthesis-ready acoustic identity', body: dossier.designed_voice_description,
    }).node);
    dossierSection.append(summaries);
    const traitLabels = {
      vocal_age_impression: 'Vocal age impression', pitch: 'Pitch',
      weight_and_resonance: 'Weight and resonance', texture_and_timbre: 'Texture and timbre',
      accent_and_language: 'Accent and language', cadence_and_rhythm: 'Cadence and rhythm',
      energy_range: 'Energy range', emotional_range: 'Emotional range', casting_guidance: 'Casting guidance',
    };
    const traitGrid = document.createElement('dl');
    traitGrid.className = 'cast-profile__voice-dossier-traits';
    Object.entries(traitLabels).forEach(([key, label]) => {
      const trait = dossier[key];
      if (!trait?.value) return;
      const item = document.createElement('div');
      const basis = String(trait.basis || 'unknown').replaceAll('_', ' ');
      const normalizedBasis = basis.replace(/\s+/g, ' ').trim().toLocaleLowerCase();
      const normalizedKey = String(key).replaceAll('_', ' ').toLocaleLowerCase();
      const normalizedLabel = label.toLocaleLowerCase();
      const basisAddsMeaning = ![normalizedKey, normalizedLabel, 'casting recommendation']
        .includes(normalizedBasis);
      const quotes = (trait.evidence_quotes || []).join(' · ');
      const evidence = [basisAddsMeaning ? basis : '', quotes].filter(Boolean).join(' · ');
      item.append(castText('dt', '', label), castText('dd', '', trait.value));
      if (evidence) item.append(castText('span', 'metadata', evidence));
      traitGrid.append(item);
    });
    if (traitGrid.children.length) dossierSection.append(traitGrid);
    if (dossier.uncertainties?.length) {
      const uncertainty = document.createElement('div');
      uncertainty.className = 'cast-profile__voice-dossier-uncertainties';
      uncertainty.append(castText('strong', '', 'Uncertainties'),
        castList(dossier.uncertainties, 'No uncertainties recorded.'));
      dossierSection.append(uncertainty);
    }
    card.append(dossierSection);
    return card;
  };
}
