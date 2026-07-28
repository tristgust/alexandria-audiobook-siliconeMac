'use strict';

const UI = globalThis.AlexandriaUI;
const STYLESHEET = '/static/styles/pages/settings_more.css';

export const SETTINGS_SECTIONS = Object.freeze([
  ['preferences', 'Project preferences'],
  ['provider', 'Language model'],
  ['speech', 'Speech defaults'],
  ['accessibility', 'Accessibility and density'],
  ['storage', 'Retention limits'],
  ['advanced', 'Diagnostics and specialist configuration'],
]);

export const SETTINGS_HEADING_IDS = Object.freeze({
  preferences: 'settings-preferences-heading',
  provider: 'settings-provider-heading',
  speech: 'settings-speech-heading',
  accessibility: 'settings-accessibility-heading',
  storage: 'settings-storage-heading',
  advanced: 'settings-advanced-heading',
});

export function ensureSettingsStyles() {
  if (document.querySelector(`link[href="${STYLESHEET}"]`)) return;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = STYLESHEET;
  link.dataset.settingsMoreStyles = '';
  document.head.append(link);
}

export function settingsText(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

export function settingsControl(options, group, key, kind = 'text') {
  const wrapper = UI.field(options);
  const input = wrapper.querySelector('input,select,textarea');
  input.dataset.settingsGroup = group;
  input.dataset.settingsKey = key;
  input.dataset.settingsKind = kind;
  return wrapper;
}

export function settingsCheckbox(label, checked, id, group, key, disabled = false) {
  const wrapper = UI.checkbox({ label, checked, disabled });
  const input = wrapper.querySelector('input');
  input.id = id;
  input.dataset.settingsGroup = group;
  input.dataset.settingsKey = key;
  input.dataset.settingsKind = 'boolean';
  return wrapper;
}

export function settingsSection(id, title, body, content) {
  const node = UI.flatSection({
    id: `settings-${id}`,
    title,
    body,
    content,
    className: 'settings-section',
    headingTag: 'h2',
  });
  const heading = node.querySelector('h2');
  heading.id = SETTINGS_HEADING_IDS[id];
  heading.tabIndex = -1;
  return node;
}

export function setSettingsSaveState(node, state, label) {
  node.dataset.state = state;
  node.textContent = label;
}

export function applyAccessibilityPreferences(preferences) {
  document.body.dataset.settingsMotion = preferences.motion;
  document.body.dataset.settingsContrast = preferences.contrast;
  document.body.dataset.settingsDensity = preferences.density;
  document.body.dataset.settingsAnnouncements = String(preferences.status_announcements);
}

export function settingsFieldError(result) {
  return result.data?.detail?.message || result.data?.message || result.error
    || 'Settings could not be saved.';
}

export function focusSettingsSection(mode, signal) {
  requestAnimationFrame(() => {
    if (signal.aborted) return;
    const heading = document.getElementById(
      SETTINGS_HEADING_IDS[mode] || SETTINGS_HEADING_IDS.preferences,
    );
    const scroller = heading?.closest('.workspace');
    if (!heading || !scroller) return;
    scroller.scrollTop = 0;
    requestAnimationFrame(() => {
      if (signal.aborted) return;
      if (mode !== 'preferences') {
        const delta = heading.getBoundingClientRect().top
          - scroller.getBoundingClientRect().top - 20;
        if (Math.abs(delta) > 1) scroller.scrollTop += delta;
      }
      heading.focus({ preventScroll: true });
    });
  });
}
