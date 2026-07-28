'use strict';

const UI = globalThis.AlexandriaUI;
const STYLESHEET = '/static/styles/pages/settings_more.css';
const SECTION_DEFINITIONS = [
  ['preferences', 'Project preferences'],
  ['provider', 'Language model'],
  ['speech', 'Speech defaults'],
  ['accessibility', 'Accessibility and density'],
  ['storage', 'Retention limits'],
  ['advanced', 'Diagnostics and specialist configuration'],
];
const SECTION_HEADING_IDS = Object.freeze({
  preferences: 'settings-preferences-heading',
  provider: 'settings-provider-heading',
  speech: 'settings-speech-heading',
  accessibility: 'settings-accessibility-heading',
  storage: 'settings-storage-heading',
  advanced: 'settings-advanced-heading',
});

function ensureStylesheet() {
  if (document.querySelector(`link[href="${STYLESHEET}"]`)) return;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = STYLESHEET;
  link.dataset.settingsMoreStyles = '';
  document.head.append(link);
}

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value;
  return node;
}

function control(options, group, key, kind = 'text') {
  const wrapper = UI.field(options);
  const input = wrapper.querySelector('input,select,textarea');
  input.dataset.settingsGroup = group;
  input.dataset.settingsKey = key;
  input.dataset.settingsKind = kind;
  return wrapper;
}

function checkbox(label, checked, id, group, key, disabled = false) {
  const wrapper = UI.checkbox({ label, checked, disabled });
  const input = wrapper.querySelector('input');
  input.id = id;
  input.dataset.settingsGroup = group;
  input.dataset.settingsKey = key;
  input.dataset.settingsKind = 'boolean';
  return wrapper;
}

function section(id, title, body, content) {
  const node = UI.flatSection({
    id: `settings-${id}`,
    title,
    body,
    content,
    className: 'settings-section',
    headingTag: 'h2',
  });
  const heading = node.querySelector('h2');
  heading.id = SECTION_HEADING_IDS[id];
  heading.tabIndex = -1;
  return node;
}

function setSaveState(node, state, label) {
  node.dataset.state = state;
  node.textContent = label;
}

function applyAccessibilityPreferences(preferences) {
  document.body.dataset.settingsMotion = preferences.motion;
  document.body.dataset.settingsContrast = preferences.contrast;
  document.body.dataset.settingsDensity = preferences.density;
  document.body.dataset.settingsAnnouncements = String(preferences.status_announcements);
}

function fieldError(result) {
  return result.data?.detail?.message || result.data?.message || result.error || 'Settings could not be saved.';
}

function focusSection(mode, signal) {
  requestAnimationFrame(() => {
    if (signal.aborted) return;
    const heading = document.getElementById(
      SECTION_HEADING_IDS[mode] || SECTION_HEADING_IDS.preferences,
    );
    const scroller = heading?.closest('.workspace');
    if (!heading || !scroller) return;
    scroller.scrollTop = 0;
    requestAnimationFrame(() => {
      if (!signal.aborted) heading.focus({ preventScroll: true });
    });
  });
}

function renderForm({ payload, route, shell, api, signal, owner, stateRegion }) {
  let fingerprint = payload.config_fingerprint;
  let savedSettings = structuredClone(payload.settings);
  let draft = structuredClone(savedSettings);
  const initialBodyState = {
    motion: document.body.dataset.settingsMotion,
    contrast: document.body.dataset.settingsContrast,
    density: document.body.dataset.settingsDensity,
    announcements: document.body.dataset.settingsAnnouncements,
  };
  const form = document.createElement('form');
  form.className = 'settings-form';
  form.noValidate = true;

  const preferences = document.createElement('div');
  preferences.className = 'settings-control-grid settings-preferences-grid';
  const sourceLanguagePreference = control({
    id: 'settings-source-language', label: 'Default source language',
    value: draft.preferences.default_source_language,
  }, 'preferences', 'default_source_language');
  sourceLanguagePreference.classList.add('settings-preference-field');
  const outputLanguagePreference = control({
    id: 'settings-output-language', label: 'Default output language',
    value: draft.preferences.default_output_language,
  }, 'preferences', 'default_output_language');
  outputLanguagePreference.classList.add('settings-preference-field');
  const destructivePreference = checkbox(
    'Confirm before destructive actions', draft.preferences.confirm_before_destructive,
    'settings-confirm-destructive', 'preferences', 'confirm_before_destructive',
  );
  destructivePreference.classList.add('settings-preference-choice');
  const rememberPreference = checkbox(
    'Remember the last valid managed project', draft.preferences.remember_last_project,
    'settings-remember-project', 'preferences', 'remember_last_project',
  );
  rememberPreference.classList.add('settings-preference-choice');
  preferences.append(
    sourceLanguagePreference, outputLanguagePreference,
    destructivePreference, rememberPreference,
  );
  const template = payload.generation_defaults?.default_template;
  const templateRow = document.createElement('div');
  templateRow.className = 'settings-destination-row settings-template-row';
  const templateCopy = document.createElement('div');
  templateCopy.className = 'settings-template-copy';
  templateCopy.append(
    text('strong', '', 'Default template'),
    text('span', 'metadata', template?.name || 'No default template is available.'),
  );
  templateRow.append(templateCopy);
  const manageTemplates = UI.button({ label: 'Manage Templates', variant: 'quiet' });
  manageTemplates.addEventListener('click', () => {
    const target = shell.routes.routeForPath('templates', { return: route.hash });
    shell.navigate(target.hash);
  });
  templateRow.append(manageTemplates);
  preferences.append(templateRow);

  const provider = document.createElement('div');
  provider.className = 'settings-control-grid';
  provider.append(
    control({ id: 'settings-provider-backend', label: 'Provider', kind: 'select',
      value: draft.provider.backend, options: [
        { value: 'auto', label: 'Auto detect' },
        { value: 'ollama', label: 'Native Ollama' },
        { value: 'openai', label: 'OpenAI-compatible' },
      ] }, 'provider', 'backend'),
    control({ id: 'settings-provider-model', label: 'Model name',
      value: draft.provider.model_name }, 'provider', 'model_name'),
    control({ id: 'settings-provider-url', label: 'Base URL',
      value: draft.provider.base_url }, 'provider', 'base_url'),
    control({ id: 'settings-context-length', label: 'Context length', type: 'number',
      value: draft.provider.context_length }, 'provider', 'context_length', 'integer'),
    control({ id: 'settings-keep-alive', label: 'Keep alive',
      value: draft.provider.keep_alive }, 'provider', 'keep_alive', 'keep-alive'),
    control({ id: 'settings-timeout', label: 'Timeout in seconds', type: 'number',
      value: draft.provider.timeout }, 'provider', 'timeout', 'integer'),
    checkbox('Thinking where supported', draft.provider.thinking,
      'settings-thinking', 'provider', 'thinking'),
    checkbox('Corrective retry', draft.provider.corrective_retry,
      'settings-corrective-retry', 'provider', 'corrective_retry'),
    checkbox('Structured output required', true,
      'settings-structured-output', 'provider', 'structured_output', true),
  );
  const secret = UI.secretField({
    label: 'API key',
    mode: draft.provider.api_key_mode || 'preserve',
    testId: 'settings-api-key-intent',
  });
  secret.querySelector('input').id = 'settings-api-key';
  provider.append(secret);

  const speech = document.createElement('div');
  speech.className = 'settings-control-grid';
  speech.append(
    control({ id: 'settings-speech-mode', label: 'Speech engine', kind: 'select',
      value: draft.speech.mode, options: [
        { value: 'local', label: 'Local' },
        { value: 'external', label: 'External server' },
      ] }, 'speech', 'mode'),
    control({ id: 'settings-speech-url', label: 'External server URL',
      value: draft.speech.url }, 'speech', 'url'),
    control({ id: 'settings-speech-language', label: 'Speech language',
      value: draft.speech.language }, 'speech', 'language'),
    control({ id: 'settings-workers', label: 'Parallel workers', type: 'number',
      value: draft.speech.parallel_workers }, 'speech', 'parallel_workers', 'integer'),
    control({ id: 'settings-speaker-pause', label: 'Pause between speakers (ms)', type: 'number',
      value: draft.speech.pause_between_speakers_ms },
    'speech', 'pause_between_speakers_ms', 'integer'),
    control({ id: 'settings-line-pause', label: 'Pause for the same speaker (ms)', type: 'number',
      value: draft.speech.pause_same_speaker_ms },
    'speech', 'pause_same_speaker_ms', 'integer'),
  );

  const accessibility = document.createElement('div');
  accessibility.className = 'settings-control-grid';
  accessibility.append(
    control({ id: 'settings-motion', label: 'Motion', kind: 'select',
      value: draft.accessibility.motion, options: ['system', 'reduced', 'full'] },
    'accessibility', 'motion'),
    control({ id: 'settings-contrast', label: 'Contrast', kind: 'select',
      value: draft.accessibility.contrast, options: ['system', 'more', 'standard'] },
    'accessibility', 'contrast'),
    control({ id: 'settings-density', label: 'Density', kind: 'select',
      value: draft.accessibility.density, options: ['comfortable', 'compact'] },
    'accessibility', 'density'),
    checkbox('Announce status changes', draft.accessibility.status_announcements,
      'settings-status-announcements', 'accessibility', 'status_announcements'),
  );

  const storage = document.createElement('div');
  storage.className = 'settings-control-grid';
  storage.append(
    control({ id: 'settings-rollback-days', label: 'Rollback retention days', type: 'number',
      value: draft.storage.rollback_retention_days },
    'storage', 'rollback_retention_days', 'integer'),
    control({ id: 'settings-intermediate-days', label: 'Intermediate retention days', type: 'number',
      value: draft.storage.intermediate_retention_days },
    'storage', 'intermediate_retention_days', 'integer'),
    control({ id: 'settings-backup-gib', label: 'Maximum backup storage (GiB)', type: 'number',
      value: draft.storage.maximum_backup_gib },
    'storage', 'maximum_backup_gib', 'number'),
  );
  storage.append(UI.notice({
    tone: 'information',
    title: 'Manual cleanup policy',
    body: 'Retention values are saved now. Guarded cleanup remains a separate Maintenance action; saving this policy deletes nothing. cleanup_mode remains manual_only.',
  }));
  const maintenanceRow = document.createElement('div');
  maintenanceRow.className = 'settings-destination-row';
  const maintenanceCopy = document.createElement('div');
  maintenanceCopy.append(
    text('strong', '', 'Maintenance'),
    text('p', 'metadata', 'Review recovery, storage, and guarded cleanup without leaving the canonical shell.'),
  );
  const maintenanceTarget = shell.routes.routeForPath('more/maintenance', {
    mode: 'recovery',
    return: route.hash,
  });
  const openMaintenance = UI.button({
    label: 'Open Maintenance',
    variant: 'quiet',
    attributes: { 'data-settings-maintenance-link': '' },
  });
  openMaintenance.addEventListener('click', () => shell.navigate(maintenanceTarget.hash));
  maintenanceRow.append(maintenanceCopy, openMaintenance);
  storage.append(maintenanceRow);

  const advanced = document.createElement('div');
  advanced.className = 'settings-destination-list';
  const destinationLabels = {
    stage_profiles: ['Stage model profiles', 'Evidence-gated model routing for each stage.'],
    runtime_diagnostics: ['Runtime diagnostics', 'Inspect current local runtime health.'],
    model_cache: ['Local model cache', 'Inspect pinned model availability and explicit repair.'],
    advanced_generation: ['Advanced generation', 'Review low-level generation controls separately.'],
  };
  Object.entries(destinationLabels).forEach(([key, [label, description]]) => {
    const row = document.createElement('div');
    row.className = 'settings-destination-row';
    const copy = document.createElement('div');
    copy.append(text('strong', '', label), text('p', 'metadata', description));
    const open = UI.button({
      label: `Open ${label}`,
      variant: 'quiet',
      attributes: { 'data-settings-destination': key },
    });
    open.addEventListener('click', () => {
      const destination = payload.advanced_destinations?.[key];
      if (!destination) return;
      const context = { ...destination.context, return: route.hash };
      const target = shell.routes.routeForDestination(destination.destination, context);
      shell.navigate(target.hash);
    });
    row.append(copy, open);
    advanced.append(row);
  });

  const activeMode = SECTION_DEFINITIONS.some(([key]) => key === route.context.mode)
    ? route.context.mode : 'preferences';
  const sections = [
    section('preferences', 'Project preferences',
      'Defaults apply to future projects and never rewrite an existing project.', preferences),
    section('provider', 'Language model',
      'Connection and runtime defaults for structured local generation.', provider),
    section('speech', 'Speech defaults',
      'These values affect future synthesis; saving does not regenerate current audio.', speech),
    section('accessibility', 'Accessibility and density',
      'Preview these presentation preferences immediately.', accessibility),
    section('storage', 'Retention limits',
      'Save policy now; guarded cleanup is reviewed separately.', storage),
    section('advanced', 'Diagnostics and specialist configuration',
      'Technical surfaces open as separate destinations and preserve this return route.', advanced),
  ];
  sections.forEach((node, index) => {
    const key = SECTION_DEFINITIONS[index][0];
    node.dataset.settingsSection = key;
    node.hidden = key !== activeMode;
  });
  form.append(...sections);

  const nav = document.createElement('nav');
  nav.className = 'settings-section-nav';
  nav.setAttribute('aria-label', 'Settings sections');
  SECTION_DEFINITIONS.forEach(([key, label]) => {
    const target = shell.routes.withContext(route, { mode: key });
    const link = document.createElement('a');
    link.href = target.hash;
    link.textContent = label;
    link.dataset.settingsSectionLink = key;
    if (activeMode === key) link.setAttribute('aria-current', 'location');
    link.addEventListener('click', (event) => {
      event.preventDefault();
      shell.navigate(target.hash);
    });
    nav.append(link);
  });

  const feedback = document.createElement('div');
  feedback.className = 'settings-save-feedback';
  feedback.hidden = true;
  feedback.setAttribute('role', 'status');
  feedback.setAttribute('aria-live', 'polite');
  const saveState = document.createElement('span');
  saveState.dataset.settingsSaveState = '';
  setSaveState(saveState, 'clean', 'Saved');
  const saveButton = UI.button({
    label: 'Save Settings',
    variant: 'primary',
    type: 'submit',
    disabled: true,
    attributes: { 'data-settings-save': 'true' },
  });
  feedback.append(saveState, saveButton);

  const syncDirtyState = () => {
    const dirty = JSON.stringify(draft) !== JSON.stringify(savedSettings);
    setSaveState(saveState, dirty ? 'dirty' : 'clean', dirty ? 'Not saved' : 'Saved');
    saveButton.disabled = !dirty;
    feedback.hidden = !dirty;
    return dirty;
  };

  const setDraftValue = (input) => {
    const { settingsGroup: group, settingsKey: key, settingsKind: kind } = input.dataset;
    if (!group || !key) return;
    const value = kind === 'boolean' ? input.checked
      : kind === 'integer' ? Number.parseInt(input.value, 10)
        : kind === 'number' ? Number.parseFloat(input.value) : input.value;
    draft[group][key] = value;
    if (group === 'accessibility') applyAccessibilityPreferences(draft.accessibility);
    syncDirtyState();
  };
  form.addEventListener('input', (event) => setDraftValue(event.target));
  form.addEventListener('change', (event) => setDraftValue(event.target));
  const syncSecretDraft = () => {
    const change = secret.getSecretChange();
    Object.assign(draft.provider, {
      api_key_mode: change.mode,
      api_key: change.value || '',
    });
    syncDirtyState();
  };
  secret.addEventListener('click', (event) => {
    if (!event.target.closest('[data-secret-intent]')) return;
    syncSecretDraft();
  });
  secret.querySelector('input').addEventListener('input', syncSecretDraft);

  const saveSettings = async () => {
    if (saveButton.disabled || signal.aborted) return;
    const secretChange = secret.getSecretChange();
    Object.assign(draft.provider, {
      api_key_mode: secretChange.mode,
      api_key: secretChange.value || '',
    });
    feedback.hidden = false;
    setSaveState(saveState, 'validating', 'Validating');
    saveButton.disabled = true;
    setSaveState(saveState, 'saving', 'Saving');
    const result = await api.put("/api/settings", {
      expected_config_fingerprint: fingerprint,
      settings: draft,
    }, { signal });
    if (signal.aborted) return;
    if (!result.ok) {
      saveButton.disabled = false;
      const conflict = result.status === 409
        && result.data?.detail?.code === 'settings_config_conflict';
      setSaveState(saveState, conflict ? 'conflict' : 'error',
        conflict ? 'Settings changed elsewhere' : 'Not saved');
      feedback.prepend(UI.notice({
        tone: conflict ? 'warning' : 'error',
        title: conflict ? 'Reload before overwriting' : 'Settings were not saved',
        body: fieldError(result),
        live: true,
      }));
      return;
    }
    fingerprint = result.data.config_fingerprint;
    savedSettings = structuredClone(result.data.settings);
    draft = structuredClone(savedSettings);
    secret.setSecretIntent('preserve');
    applyAccessibilityPreferences(draft.accessibility);
    setSaveState(saveState, 'saved', 'Saved');
    saveButton.disabled = true;
    feedback.hidden = false;
  };
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    saveSettings();
  });
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
  focusSection(activeMode, signal);

  return () => {
    document.removeEventListener('keydown', keyboardSave);
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

export async function mount({ root, route, shell, api, signal }) {
  ensureStylesheet();
  const dataRouteOwner = route.path;
  const owner = document.createElement('article');
  owner.dataset.routeOwner = dataRouteOwner;
  owner.dataset.page = 'settings';
  owner.dataset.viewState = 'loading';
  owner.className = 'support-page settings-workspace';
  shell.globalHeader.set({
    title: 'Settings',
    subtitle: 'Global preferences and approved defaults for Alexandria.',
  });
  const heading = document.createElement('span');
  heading.id = 'settings-page-heading';
  heading.className = 'visually-hidden';
  heading.dataset.pageHeading = '';
  heading.textContent = 'Settings';
  const stateRegion = document.createElement('div');
  stateRegion.setAttribute('data-state-region', '');
  stateRegion.setAttribute('aria-live', 'polite');
  stateRegion.append(UI.skeleton({ label: 'Loading Settings' }));
  owner.append(heading, stateRegion);
  root.replaceChildren(owner);
  const result = await api.get("/api/settings", { signal });
  if (signal.aborted) return () => {};
  if (!result.ok) {
    owner.dataset.viewState = 'error';
    stateRegion.replaceChildren(UI.notice({
      tone: 'error',
      title: 'Settings could not be loaded',
      body: fieldError(result),
      live: true,
      action: UI.button({
        label: 'Retry',
        variant: 'quiet',
        onClick: () => shell.navigate(route.hash, { historyMode: 'replace' }),
      }),
    }));
    return () => {};
  }
  const cleanup = renderForm({
    payload: result.data,
    route,
    shell,
    api,
    signal,
    owner,
    stateRegion,
  });
  return cleanup;
}
