'use strict';

import { resultMessage, statusLine } from '/static/pages/more.js';

const UI = globalThis.AlexandriaUI;

function actionFeedback(region, result, success) {
  region.replaceChildren(UI.notice({
    tone: result.ok ? 'success' : 'error',
    title: result.ok ? success : 'Action did not complete',
    body: result.ok
      ? 'The authoritative status will refresh now.'
      : resultMessage(result, 'No changes were made.'),
    live: true,
  }));
}

export function guardedMaintenanceAction({
  label,
  phrase,
  body,
  destructive = false,
  region,
  signal,
  request,
  success,
  refresh,
}) {
  const opener = UI.button({
    label,
    variant: destructive ? 'destructive' : 'secondary',
  });
  const confirmation = UI.field({
    id: `maintenance-confirm-${phrase.toLocaleLowerCase().replaceAll(' ', '-')}`,
    label: `Type ${phrase} to continue`,
    description: 'The action remains unavailable unless the phrase matches exactly.',
  });
  const input = confirmation.querySelector('input');
  const dialog = UI.dialog({
    opener,
    title: 'Review impact',
    body,
    content: confirmation,
    confirmLabel: label,
    destructive,
    onConfirm: async () => {
      if (input.value !== phrase || signal.aborted) {
        region.replaceChildren(UI.notice({
          tone: 'warning',
          title: 'Confirmation did not match',
          body: `Type ${phrase} exactly, then review the action again.`,
          live: true,
        }));
        return;
      }
      const result = await request();
      if (signal.aborted) return;
      actionFeedback(region, result, success);
      if (result.ok) window.setTimeout(refresh, 180);
    },
  });
  return opener;
}

export function renderMigrationActions(data, actionRegion, options) {
  const unavailable = !data.migration;
  const migration = data.migration || {};
  const history = data.history?.operations || [];
  const content = document.createElement('div');
  content.className = 'support-list';
  content.append(statusLine(
    unavailable
      ? 'Migration status unavailable'
      : migration.migration_required ? 'Migration review required' : 'Configuration current',
    unavailable
      ? 'Alexandria could not inspect configuration status. No migration action is available.'
      : migration.migration_blocked
      ? 'A blocker must be resolved before applying changes.'
      : migration.migration_required
        ? 'Review the proposed changes before applying them.'
        : 'No configuration migration is waiting.',
    unavailable || migration.migration_blocked
      ? 'error' : migration.migration_required ? 'warning' : 'success',
  ));
  history.slice(0, 5).forEach((item) => {
    content.append(statusLine(
      item.operation === 'rollback' ? 'Rollback completed' : 'Migration reviewed',
      item.state === 'rolled_back'
        ? 'A prior migration was safely reversed.'
        : 'A recorded maintenance operation is available.',
      item.state === 'failed' ? 'error' : 'neutral',
    ));
  });
  const actions = document.createElement('div');
  actions.className = 'guarded-actions';
  if (!unavailable && migration.migration_required && !migration.migration_blocked) {
    actions.append(guardedMaintenanceAction({
      ...options,
      label: 'Apply migration',
      phrase: 'APPLY MIGRATION',
      body: 'Review the configuration plan and its recovery point before applying it.',
      request: () => options.api.post('/api/migration/apply', {
        plan_fingerprint: migration.plan_fingerprint,
        confirm: true,
      }, { signal: options.signal }),
      success: 'Migration applied',
    }));
  }
  const rollback = !unavailable && history.find(
    (item) => item.rollback_available && item.operation_id,
  );
  if (rollback) {
    actions.append(guardedMaintenanceAction({
      ...options,
      label: 'Roll back migration',
      phrase: 'ROLL BACK',
      body: 'Review the recovery point. Rollback restores the recorded pre-migration configuration.',
      destructive: true,
      request: () => options.api.post('/api/migration/rollback', {
        operation_id: rollback.operation_id,
      }, { signal: options.signal }),
      success: 'Migration rolled back',
    }));
  }
  if (actions.childElementCount) content.append(actions);
  content.append(actionRegion);
  return content;
}

export function renderModelActions(actionRegion, options) {
  const content = document.createElement('div');
  content.className = 'guarded-actions';
  content.append(
    guardedMaintenanceAction({
      ...options,
      label: 'Download or Repair required models',
      phrase: 'DOWNLOAD MODELS',
      body: 'Review impact before starting a local model cache operation. One cache operation runs at a time.',
      request: () => options.api.post('/api/model_registry/action', {
        action: 'download_required',
      }, { signal: options.signal }),
      success: 'Model cache operation started',
    }),
    guardedMaintenanceAction({
      ...options,
      label: 'Release loaded models',
      phrase: 'RELEASE MODELS',
      body: 'Release local model memory only while synthesis is idle. Saved audio and cached models are unchanged.',
      request: () => options.api.post('/api/model_registry/memory/release', {}, {
        signal: options.signal,
      }),
      success: 'Loaded model memory released',
    }),
  );
  content.append(actionRegion);
  return content;
}
