'use strict';

const VOICE_GROUPS = Object.freeze({
  built_in: 'Built-in Voices',
  designed: 'Designed Voices',
  designed_voice: 'Designed Voices',
  supplied_recording: 'Recorded and cloned Voices',
  clone_reference: 'Recorded and cloned Voices',
  instruction_controlled: 'Recorded and cloned Voices',
  adapter: 'Voice adapters',
  alias: 'Aliases',
});

export const VOICE_GROUP_ORDER = Object.freeze([
  'Built-in Voices', 'Designed Voices', 'Recorded and cloned Voices',
  'Voice adapters', 'Aliases', 'Other Voices',
]);

export const words = (value, fallback = '') => String(value || fallback)
  .replaceAll('_', ' ')
  .replace(/\b\w/g, (letter) => letter.toUpperCase());

function voiceNameBase(voice) {
  const raw = String(voice?.name || 'Unnamed voice').replace(/_\d{10,}$/, '').trim();
  const slugLike = /_|[a-z][A-Z]|[A-Za-z]\d|\d[A-Za-z]/.test(raw);
  if (!slugLike) return raw;
  return raw
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .replace(/([A-Za-z])(\d+)/g, '$1 $2')
    .replace(/(\d+)([A-Za-z])/g, '$1 $2')
    .replace(/([a-z])voice\b/gi, '$1 voice')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
    .replace(/\bDw\s+(\d+)/g, 'DW$1')
    .replace(/\bR\s+(\d+)\b/g, 'R$1');
}

export function voiceName(voice, collection = []) {
  const base = voiceNameBase(voice);
  const matches = collection.filter((item) => voiceNameBase(item) === base);
  if (matches.length <= 1) return base;
  return `${base} · ${matches.indexOf(voice) + 1}`;
}

export const voicePresentation = (voice) => ({
  built_in: ['Built-in Voice', 'fas fa-microphone-lines'],
  designed: ['Designed Voice', 'fas fa-wand-magic-sparkles'],
  designed_voice: ['Designed Voice', 'fas fa-wand-magic-sparkles'],
  supplied_recording: ['Supplied recording', 'fas fa-wave-square'],
  clone_reference: ['Clone reference', 'fas fa-microphone-lines'],
  instruction_controlled: ['Instruction-controlled Voice', 'fas fa-sliders'],
  adapter: ['Voice adapter', 'fas fa-layer-group'],
  alias: ['Voice alias', 'fas fa-link'],
}[voice?.method] || [voice?.method_label || words(voice?.method, 'Voice'), 'fas fa-microphone-lines']);

export const voiceGroup = (voice) => VOICE_GROUPS[voice?.method] || 'Other Voices';

export function voiceMark(voice, className = 'supporting-list__mark') {
  const [, iconClass] = voicePresentation(voice);
  const mark = document.createElement('span');
  mark.className = className;
  mark.setAttribute('aria-hidden', 'true');
  const icon = document.createElement('i');
  icon.className = iconClass;
  mark.append(icon);
  return mark;
}

export function text(tag, className, value) {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

export function bindVoiceOptionKeyboard(list, button) {
  button.addEventListener('keydown', (event) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const options = [...list.querySelectorAll('[role="option"]')];
    const current = options.indexOf(button);
    const next = event.key === 'Home' ? options[0]
      : event.key === 'End' ? options.at(-1)
        : options[(current + (event.key === 'ArrowDown' ? 1 : -1) + options.length)
          % options.length];
    next?.click();
  });
}

export function ownerForVoices(route) {
  const owner = document.createElement('article');
  owner.className = 'supporting-page voices-page';
  owner.dataset.routeOwner = route.path;
  owner.dataset.page = route.path;
  const heading = text('span', 'visually-hidden', 'Voices');
  heading.id = 'voices-page-heading';
  heading.dataset.pageHeading = '';
  owner.append(heading);
  return owner;
}
