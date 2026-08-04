'use strict';

const UI = globalThis.AlexandriaUI;

export const CAST_FILTERS = Object.freeze([
  ['needs_attention', 'Needs attention'],
  ['unassigned', 'Missing voice'],
  ['speaking_roles', 'Speaking roles'],
  ['ready', 'Ready'],
]);

export const VOICE_METHODS = Object.freeze([
  ['custom', 'Built-in Voice'],
  ['clone', 'Supplied recording'],
  ['design', 'Designed Voice'],
  ['adapter', 'Trained adapter'],
  ['alias', 'Share another character’s Voice'],
]);

const DESIGNED_VOICE_AUDITION_FALLBACK = 'I thought I understood the danger, but tonight everything changed, and now we have one chance to make this right.';

function castAuditionCandidate(value) {
  const text = typeof value === 'string' ? value : value?.text;
  return String(text || '').replace(/\s+/g, ' ').trim();
}

function castAuditionIdentity(value) {
  return castAuditionCandidate(value).replace(/[.!?…,:;"'’”)]+$/u, '').trim().toLocaleLowerCase();
}

function castAuditionValues(value) {
  if (value == null) return [];
  return Array.isArray(value) ? value : [value];
}

export function castAuditionText(selected) {
  const names = new Set([
    selected?.display_name,
    selected?.canonical_name,
    selected?.character?.display_name,
    selected?.character?.canonical_name,
    selected?.character?.summary?.display_name,
    selected?.character?.summary?.canonical_name,
    ...castAuditionValues(selected?.character?.summary?.aliases),
    selected?.identity?.display_name,
    selected?.identity?.canonical_name,
    ...castAuditionValues(selected?.identity?.aliases),
    selected?.identity?.script_voice_label,
    selected?.script_connection?.resolved_script_voice_label,
  ].map(castAuditionIdentity).filter(Boolean));
  const candidates = [
    ...castAuditionValues(selected?.character?.expanded?.representative_script_lines),
    ...castAuditionValues(selected?.identity?.representative_script_lines),
    ...castAuditionValues(selected?.script_connection?.representative_lines),
    selected?.voice?.representative_text,
  ].map(castAuditionCandidate).filter(Boolean);
  const eligible = candidates.map((text, sourceOrder) => {
    const words = text.match(/[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu) || [];
    const completeSentence = /[.!?]["'’”)]?$/.test(text);
    const isAllCapsHeading = words.length <= 12 && /\p{L}/u.test(text)
      && text === text.toLocaleUpperCase();
    const isHeading = /^(prelude|prologue|epilogue|chapter\b|act\b|scene\b|part\b)/i.test(text)
      || isAllCapsHeading;
    const isMarkup = /<[^>]+>/.test(text);
    if (names.has(castAuditionIdentity(text)) || isHeading || isMarkup || !completeSentence
      || words.length < 5 || words.length > 36) return null;
    const usefulRange = words.length >= 8 && words.length <= 24 ? 40 : 0;
    return {
      text,
      sourceOrder,
      score: usefulRange - Math.abs(words.length - 16),
    };
  }).filter(Boolean);
  eligible.sort((left, right) => right.score - left.score || left.sourceOrder - right.sourceOrder);
  return eligible[0]?.text || DESIGNED_VOICE_AUDITION_FALLBACK;
}

function castAuditionPersonaValue(value) {
  if (typeof value === 'string') return value.trim();
  if (value && typeof value === 'object') return String(value.value || '').trim();
  return '';
}

export function castAuditionPersonaContext(selected) {
  const dossier = selected?.voice?.imported_dossier || {};
  const values = [
    dossier.persona_summary,
    dossier.cadence_and_rhythm,
    dossier.emotional_range,
    dossier.casting_guidance,
    ...(Array.isArray(selected?.identity?.voice_clues)
      ? selected.identity.voice_clues.slice(0, 8) : []),
  ].map(castAuditionPersonaValue).filter(Boolean);
  return [...new Set(values)].join(' ').slice(0, 6000);
}

export function castText(tag, className, value, empty = 'Not yet described') {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null || value === '' ? empty : String(value);
  return node;
}

export function castInitials(name) {
  return String(name || '?').split(/\s+/).filter(Boolean).slice(0, 2)
    .map((part) => part[0]?.toUpperCase()).join('') || '?';
}

export function castWords(value) {
  return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function castVoiceMethod(character) {
  return String(character?.voice?.selected_production_method || '').toLowerCase();
}

export function castVoiceLabel(character) {
  if (!character?.speaking_role || character.speaking_role === 'non_speaking') {
    return 'No production Voice required';
  }
  const method = castVoiceMethod(character);
  const clone = character?.voice?.clone || {};
  if (['clone', 'supplied_recording_clone'].includes(method)) return 'Supplied-recording clone';
  if (['controlled_clone', 'instruction_controlled_clone'].includes(method)
    || clone.controlled_capability) return 'Instruction-controlled clone';
  if (['design', 'designed', 'designed_voice', 'voice_design'].includes(method)) return 'Designed Voice';
  if (['adapter', 'lora', 'trained_voice'].includes(method)) return 'Voice adapter';
  if (method === 'alias') return 'Shared Voice';
  if (character?.voice?.selected_voice) return character.voice.selected_voice;
  return method ? 'Production Voice incomplete' : 'No production Voice';
}

export function castStatus(character) {
  if (!character?.speaking_role || character.speaking_role === 'non_speaking') {
    return { label: 'Non-speaking', tone: 'neutral' };
  }
  const state = character.readiness_state;
  if (state === 'ready') {
    const method = castVoiceMethod(character);
    if (['clone', 'supplied_recording_clone', 'controlled_clone', 'instruction_controlled_clone']
      .includes(method)) return { label: 'Clone ready', tone: 'success' };
    if (['design', 'designed', 'designed_voice', 'voice_design'].includes(method)) {
      return { label: 'Designed Voice ready', tone: 'success' };
    }
    if (['adapter', 'lora', 'trained_voice'].includes(method)) {
      return { label: 'Adapter ready', tone: 'success' };
    }
    if (method === 'alias') return { label: 'Shared Voice ready', tone: 'success' };
    return { label: 'Voice assigned', tone: 'success' };
  }
  if (character.identity?.review_required || state === 'identity_review' || state === 'needs_identity_review') {
    return { label: 'Identity review', tone: 'warning' };
  }
  if (character.voice?.preview?.status === 'missing' || state === 'preview_recommended') {
    return { label: 'Preview recommended', tone: 'warning' };
  }
  return { label: 'Missing voice', tone: 'error' };
}

export function castList(values, emptyCopy = 'None recorded') {
  const items = Array.isArray(values) ? values.filter((value) => value != null && value !== '') : [];
  if (!items.length) return castText('p', 'cast-profile__muted', emptyCopy);
  const node = document.createElement('ul');
  node.className = 'cast-profile__facts';
  items.forEach((value) => node.append(castText(
    'li', '', typeof value === 'string' ? value : value.summary || value.description || value.label,
  )));
  return node;
}

export function castSection(name, title, content) {
  const node = UI.flatSection({ title, content });
  node.classList.add('cast-profile__section');
  node.dataset.castSection = name;
  return node;
}

export function castStyle() {
  const existing = document.querySelector('link[data-page-style="cast"]');
  if (existing) return { node: existing, owned: false };
  const node = document.createElement('link');
  node.rel = 'stylesheet';
  node.href = '/static/styles/pages/cast.css';
  node.dataset.pageStyle = 'cast';
  document.head.append(node);
  return { node, owned: true };
}

export function castProfileValues(profile, selected) {
  return {
    voiceId: profile.querySelector('[data-cast-voice-choice]')?.value || '',
    method: profile.querySelector('[data-cast-voice-method]')?.value || 'custom',
    assigned: profile.querySelector('[data-cast-assigned-voice]')?.value || '',
    description: profile.querySelector('[data-cast-voice-description]')?.value || '',
    designedPreviewFile: profile.querySelector('[data-cast-designed-preview]')?.value || '',
    designedPreviewText: profile.querySelector('[data-cast-designed-preview]')?.dataset.sampleText || '',
    designedPreviewFingerprint: profile.querySelector('[data-cast-designed-preview]')
      ?.dataset.previewFingerprint || '',
    designedPreviewUseAsClone: profile.querySelector('[data-cast-designed-preview]')
      ?.dataset.useAsClone === 'true',
    transcript: profile.querySelector('[data-cast-reference-transcript]')?.value || '',
    scriptLabel: selected?.script_connection?.resolved_script_voice_label
      || selected?.identity?.script_voice_label || selected?.display_name,
  };
}
