'use strict';

const UI = globalThis.AlexandriaUI;

export const CAST_FILTERS = Object.freeze([
  ['needs_attention', 'Needs attention'],
  ['unassigned', 'Missing voice'],
  ['speaking_roles', 'Speaking roles'],
  ['ready', 'Ready'],
]);

export const VOICE_METHODS = Object.freeze([
  ['custom', 'Assigned voice'],
  ['clone', 'Cloned voice'],
  ['controlled_clone', 'Controlled clone'],
  ['designed_voice', 'Designed voice'],
  ['adapter', 'Trained adapter'],
  ['alias', 'Shared voice'],
]);

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

export function castStatus(character) {
  if (!character?.speaking_role || character.speaking_role === 'non_speaking') {
    return { label: 'Non-speaking', tone: 'neutral' };
  }
  const state = character.readiness_state;
  if (state === 'ready') return { label: 'Voice assigned', tone: 'success' };
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
    method: profile.querySelector('[data-cast-voice-method]')?.value || 'custom',
    assigned: profile.querySelector('[data-cast-assigned-voice]')?.value || '',
    description: profile.querySelector('[data-cast-voice-description]')?.value || '',
    transcript: profile.querySelector('[data-cast-reference-transcript]')?.value || '',
    scriptLabel: selected?.script_connection?.resolved_script_voice_label
      || selected?.identity?.script_voice_label || selected?.display_name,
  };
}
