'use strict';

import {
  SETTINGS_SECTIONS, applyAccessibilityPreferences, focusSettingsSection,
  setSettingsSaveState, settingsFieldError,
} from './settings_model.js';
import { createSettingsSections } from './settings_sections.js';

const UI = globalThis.AlexandriaUI;

export function createSettingsView({ payload, route, shell, api, signal, owner, stateRegion }) {
  let fingerprint = payload.config_fingerprint;
  let draft = structuredClone(payload.settings);
  const initialBodyState = {
    motion: document.body.dataset.settingsMotion,
    contrast: document.body.dataset.settingsContrast,
    density: document.body.dataset.settingsDensity,
    announcements: document.body.dataset.settingsAnnouncements,
  };
  const form = document.createElement('form');
  form.className = 'settings-form';
  form.noValidate = true;
  const { sections, secret } = createSettingsSections({ payload, route, shell, draft });
  form.append(...sections);

  const nav = document.createElement('nav');
  nav.className = 'settings-section-nav';
  nav.setAttribute('aria-label', 'Settings sections');
  SETTINGS_SECTIONS.forEach(([key, label]) => {
    const target = shell.routes.withContext(route, { mode: key });
    const link = document.createElement('a');
    link.href = target.hash;
    link.textContent = label;
    link.dataset.settingsSectionLink = key;
    if ((route.context.mode || 'preferences') === key) link.setAttribute('aria-current', 'location');
    link.addEventListener('click', (event) => {
      event.preventDefault();
      shell.navigate(target.hash);
    });
    nav.append(link);
  });

  const feedback = document.createElement('div');
  feedback.className = 'settings-save-feedback';
  feedback.setAttribute('role', 'status');
  feedback.setAttribute('aria-live', 'polite');
  const saveState = document.createElement('span');
  saveState.dataset.settingsSaveState = '';
  setSettingsSaveState(saveState, 'clean', 'Saved');
  const saveButton = UI.button({
    label: 'Save Settings',
    variant: 'primary',
    type: 'submit',
    attributes: { 'data-settings-save': 'true' },
  });
  feedback.append(saveState, saveButton);

  const setDraftValue = (input) => {
    const { settingsGroup: group, settingsKey: key, settingsKind: kind } = input.dataset;
    if (!group || !key) return;
    const value = kind === 'boolean' ? input.checked
      : kind === 'integer' ? Number.parseInt(input.value, 10)
        : kind === 'number' ? Number.parseFloat(input.value) : input.value;
    draft[group][key] = value;
    if (group === 'accessibility') applyAccessibilityPreferences(draft.accessibility);
    setSettingsSaveState(saveState, 'dirty', 'Not saved');
  };
  const onInput = (event) => setDraftValue(event.target);
  form.addEventListener('input', onInput);
  form.addEventListener('change', onInput);
  const onSecret = () => {
    Object.assign(draft.provider, {
      api_key_mode: secret.getSecretChange().mode,
      api_key: secret.getSecretChange().value || '',
    });
    setSettingsSaveState(saveState, 'dirty', 'Not saved');
  };
  secret.addEventListener('click', onSecret);

  const saveSettings = async () => {
    if (saveButton.disabled || signal.aborted) return;
    const secretChange = secret.getSecretChange();
    Object.assign(draft.provider, {
      api_key_mode: secretChange.mode,
      api_key: secretChange.value || '',
    });
    setSettingsSaveState(saveState, 'validating', 'Validating');
    saveButton.disabled = true;
    setSettingsSaveState(saveState, 'saving', 'Saving');
    const result = await api.put('/api/settings', {
      expected_config_fingerprint: fingerprint,
      settings: draft,
    }, { signal });
    if (signal.aborted) return;
    saveButton.disabled = false;
    if (!result.ok) {
      const conflict = result.status === 409
        && result.data?.detail?.code === 'settings_config_conflict';
      setSettingsSaveState(saveState, conflict ? 'conflict' : 'error',
        conflict ? 'Settings changed elsewhere' : 'Not saved');
      feedback.prepend(UI.notice({
        tone: conflict ? 'warning' : 'error',
        title: conflict ? 'Reload before overwriting' : 'Settings were not saved',
        body: settingsFieldError(result),
        live: true,
      }));
      return;
    }
    fingerprint = result.data.config_fingerprint;
    draft = structuredClone(result.data.settings);
    applyAccessibilityPreferences(draft.accessibility);
    setSettingsSaveState(saveState, 'saved', 'Saved');
  };
  const onSubmit = (event) => {
    event.preventDefault();
    saveSettings();
  };
  form.addEventListener('submit', onSubmit);
  saveButton.addEventListener('click', saveSettings);
  const keyboardSave = (event) => {
    if (!(event.metaKey || event.ctrlKey)
      || event.key.toLocaleLowerCase() !== 's') return;
    event.preventDefault();
    saveSettings();
  };
  document.addEventListener('keydown', keyboardSave);

  const layout = document.createElement('div');
  layout.className = 'settings-layout';
  const main = document.createElement('div');
  main.className = 'settings-sections';
  main.append(form);
  layout.append(nav, main);
  stateRegion.replaceChildren(layout, feedback);
  owner.dataset.viewState = 'ready';
  applyAccessibilityPreferences(draft.accessibility);
  focusSettingsSection(route.context.mode || 'preferences', signal);

  return () => {
    document.removeEventListener('keydown', keyboardSave);
    form.removeEventListener('input', onInput);
    form.removeEventListener('change', onInput);
    form.removeEventListener('submit', onSubmit);
    secret.removeEventListener('click', onSecret);
    if (!['clean', 'saved'].includes(saveState.dataset.state)) {
      const restore = (name, value) => {
        if (value === undefined) delete document.body.dataset[name];
        else document.body.dataset[name] = value;
      };
      restore('settingsMotion', initialBodyState.motion);
      restore('settingsContrast', initialBodyState.contrast);
      restore('settingsDensity', initialBodyState.density);
      restore('settingsAnnouncements', initialBodyState.announcements);
    }
  };
}
