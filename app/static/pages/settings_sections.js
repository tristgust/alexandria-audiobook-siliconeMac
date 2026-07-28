'use strict';

import {
  settingsCheckbox, settingsControl, settingsSection, settingsText,
} from './settings_model.js';

const UI = globalThis.AlexandriaUI;

export function createSettingsSections({ payload, route, shell, draft }) {
  const preferences = document.createElement('div');
  preferences.className = 'settings-control-grid';
  preferences.append(
    settingsControl({ id: 'settings-source-language', label: 'Default source language',
      value: draft.preferences.default_source_language }, 'preferences', 'default_source_language'),
    settingsControl({ id: 'settings-output-language', label: 'Default output language',
      value: draft.preferences.default_output_language }, 'preferences', 'default_output_language'),
    settingsCheckbox('Confirm before destructive actions', draft.preferences.confirm_before_destructive,
      'settings-confirm-destructive', 'preferences', 'confirm_before_destructive'),
    settingsCheckbox('Remember the last valid managed project', draft.preferences.remember_last_project,
      'settings-remember-project', 'preferences', 'remember_last_project'),
  );
  const template = payload.generation_defaults?.default_template;
  const templateRow = document.createElement('div');
  templateRow.className = 'settings-destination-row';
  templateRow.append(settingsText('div', '', template
    ? `Default template: ${template.name}` : 'No default template is available.'));
  const manageTemplates = UI.button({ label: 'Manage Templates', variant: 'quiet' });
  manageTemplates.addEventListener('click', () => {
    shell.navigate(shell.routes.routeForPath('templates', { return: route.hash }).hash);
  });
  templateRow.append(manageTemplates);
  preferences.append(templateRow);

  const provider = document.createElement('div');
  provider.className = 'settings-control-grid';
  provider.append(
    settingsControl({ id: 'settings-provider-backend', label: 'Provider', kind: 'select',
      value: draft.provider.backend, options: [
        { value: 'auto', label: 'Auto detect' },
        { value: 'ollama', label: 'Native Ollama' },
        { value: 'openai', label: 'OpenAI-compatible' },
      ] }, 'provider', 'backend'),
    settingsControl({ id: 'settings-provider-model', label: 'Model name',
      value: draft.provider.model_name }, 'provider', 'model_name'),
    settingsControl({ id: 'settings-provider-url', label: 'Base URL',
      value: draft.provider.base_url }, 'provider', 'base_url'),
    settingsControl({ id: 'settings-context-length', label: 'Context length', type: 'number',
      value: draft.provider.context_length }, 'provider', 'context_length', 'integer'),
    settingsControl({ id: 'settings-keep-alive', label: 'Keep alive',
      value: draft.provider.keep_alive }, 'provider', 'keep_alive', 'keep-alive'),
    settingsControl({ id: 'settings-timeout', label: 'Timeout in seconds', type: 'number',
      value: draft.provider.timeout }, 'provider', 'timeout', 'integer'),
    settingsCheckbox('Thinking where supported', draft.provider.thinking,
      'settings-thinking', 'provider', 'thinking'),
    settingsCheckbox('Corrective retry', draft.provider.corrective_retry,
      'settings-corrective-retry', 'provider', 'corrective_retry'),
    settingsCheckbox('Structured output required', true,
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
    settingsControl({ id: 'settings-speech-mode', label: 'Speech engine', kind: 'select',
      value: draft.speech.mode, options: [
        { value: 'local', label: 'Local' },
        { value: 'external', label: 'External server' },
      ] }, 'speech', 'mode'),
    settingsControl({ id: 'settings-speech-url', label: 'External server URL',
      value: draft.speech.url }, 'speech', 'url'),
    settingsControl({ id: 'settings-speech-language', label: 'Speech language',
      value: draft.speech.language }, 'speech', 'language'),
    settingsControl({ id: 'settings-workers', label: 'Parallel workers', type: 'number',
      value: draft.speech.parallel_workers }, 'speech', 'parallel_workers', 'integer'),
    settingsControl({ id: 'settings-speaker-pause', label: 'Pause between speakers (ms)', type: 'number',
      value: draft.speech.pause_between_speakers_ms },
    'speech', 'pause_between_speakers_ms', 'integer'),
    settingsControl({ id: 'settings-line-pause', label: 'Pause for the same speaker (ms)', type: 'number',
      value: draft.speech.pause_same_speaker_ms },
    'speech', 'pause_same_speaker_ms', 'integer'),
  );

  const accessibility = document.createElement('div');
  accessibility.className = 'settings-control-grid';
  accessibility.append(
    settingsControl({ id: 'settings-motion', label: 'Motion', kind: 'select',
      value: draft.accessibility.motion, options: ['system', 'reduced', 'full'] },
    'accessibility', 'motion'),
    settingsControl({ id: 'settings-contrast', label: 'Contrast', kind: 'select',
      value: draft.accessibility.contrast, options: ['system', 'more', 'standard'] },
    'accessibility', 'contrast'),
    settingsControl({ id: 'settings-density', label: 'Density', kind: 'select',
      value: draft.accessibility.density, options: ['comfortable', 'compact'] },
    'accessibility', 'density'),
    settingsCheckbox('Announce status changes', draft.accessibility.status_announcements,
      'settings-status-announcements', 'accessibility', 'status_announcements'),
  );

  const storage = document.createElement('div');
  storage.className = 'settings-control-grid';
  storage.append(
    settingsControl({ id: 'settings-rollback-days', label: 'Rollback retention days', type: 'number',
      value: draft.storage.rollback_retention_days },
    'storage', 'rollback_retention_days', 'integer'),
    settingsControl({ id: 'settings-intermediate-days', label: 'Intermediate retention days', type: 'number',
      value: draft.storage.intermediate_retention_days },
    'storage', 'intermediate_retention_days', 'integer'),
    settingsControl({ id: 'settings-backup-gib', label: 'Maximum backup storage (GiB)', type: 'number',
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
    settingsText('strong', '', 'Maintenance'),
    settingsText('p', 'metadata', 'Review recovery, storage, and guarded cleanup without leaving the canonical shell.'),
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
  const destinations = {
    stage_profiles: ['Stage model profiles', 'Evidence-gated model routing for each stage.'],
    runtime_diagnostics: ['Runtime diagnostics', 'Inspect current local runtime health.'],
    model_cache: ['Local model cache', 'Inspect pinned model availability and explicit repair.'],
    advanced_generation: ['Advanced generation', 'Review low-level generation controls separately.'],
  };
  Object.entries(destinations).forEach(([key, [label, description]]) => {
    const row = document.createElement('div');
    row.className = 'settings-destination-row';
    const copy = document.createElement('div');
    copy.append(settingsText('strong', '', label), settingsText('p', 'metadata', description));
    const open = UI.button({
      label: `Open ${label}`,
      variant: 'quiet',
      attributes: { 'data-settings-destination': key },
    });
    open.addEventListener('click', () => {
      const destination = payload.advanced_destinations?.[key];
      if (!destination) return;
      const target = shell.routes.routeForDestination(
        destination.destination,
        { ...destination.context, return: route.hash },
      );
      shell.navigate(target.hash);
    });
    row.append(copy, open);
    advanced.append(row);
  });

  return {
    secret,
    sections: [
      settingsSection('preferences', 'Project preferences',
        'Defaults apply to future projects and never rewrite an existing project.', preferences),
      settingsSection('provider', 'Language model',
        'Connection and runtime defaults for structured local generation.', provider),
      settingsSection('speech', 'Speech defaults',
        'These values affect future synthesis; saving does not regenerate current audio.', speech),
      settingsSection('accessibility', 'Accessibility and density',
        'Preview these presentation preferences immediately.', accessibility),
      settingsSection('storage', 'Retention limits',
        'Save policy now; guarded cleanup is reviewed separately.', storage),
      settingsSection('advanced', 'Diagnostics and specialist configuration',
        'Technical surfaces open as separate destinations and preserve this return route.', advanced),
    ],
  };
}
