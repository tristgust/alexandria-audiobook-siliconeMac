'use strict';

import {
  readCount,
  resultMessage,
  statusLine,
  supportOwner,
  supportReturn,
  textNode,
} from '/static/pages/more.js';

const UI = globalThis.AlexandriaUI;

const READS = [
  ['recovery', '/api/recovery/status'],
  ['models', '/api/model_registry/status'],
  ['memory', '/api/model_registry/memory'],
  ['library', '/api/library'],
  ['projects', '/api/projects'],
  ['migration', '/api/migration/status'],
  ['history', '/api/migration/history'],
];

function safeRead(settled, index) {
  if (settled[index]?.status !== 'fulfilled') return null;
  const result = settled[index].value;
  return result.ok ? result.data : null;
}

function metric(label, value, detail = '') {
  const row = document.createElement('div');
  row.className = 'support-metric';
  const copy = document.createElement('div');
  copy.append(textNode('span', 'metadata', label));
  if (detail) copy.append(textNode('p', 'support-status-copy', detail));
  row.append(copy, textNode('strong', '', value));
  return row;
}

function section(id, title, body, content) {
  const node = UI.flatSection({
    id: `maintenance-${id}`,
    title,
    body,
    content,
    className: 'specialist-section',
    headingTag: 'h2',
  });
  const heading = node.querySelector('h2');
  heading.id = `maintenance-${id}-heading`;
  heading.tabIndex = -1;
  return node;
}

function focusMode(mode, signal) {
  requestAnimationFrame(() => {
    if (signal.aborted) return;
    const sectionHeading = document.getElementById(`maintenance-${mode}-heading`);
    const pageHeading = document.getElementById('maintenance-page-heading');
    const heading = sectionHeading && mode !== 'health' ? sectionHeading : pageHeading;
    const scroller = heading?.closest('.workspace');
    if (!heading || !scroller) return;
    scroller.scrollTop = 0;
    requestAnimationFrame(() => {
      if (signal.aborted) return;
      if (sectionHeading && mode !== 'health') {
        const offset = heading.getBoundingClientRect().top
          - scroller.getBoundingClientRect().top - 20;
        if (Math.abs(offset) > 1) scroller.scrollTop += offset;
      }
      heading.focus({ preventScroll: true });
    });
  });
}

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

function guardedAction({
  label,
  phrase,
  body,
  destructive = false,
  region,
  api,
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

function renderHealth(data) {
  const recoveryStages = data.recovery?.stages || [];
  const models = data.models?.models || [];
  const projects = data.projects?.projects || data.projects?.items || [];
  const library = data.library?.artifacts || [];
  const cached = models.filter((item) => item.cached || item.state === 'cached').length;
  const content = document.createElement('div');
  content.className = 'maintenance-summary';
  content.append(
    metric('Recovery checks', data.recovery ? readCount(recoveryStages) : 'Unavailable',
      'Current project recovery stages'),
    metric('Available projects', data.projects ? readCount(projects) : 'Unavailable',
      'Projects currently known to Alexandria'),
    metric('Library entries', data.library ? readCount(library) : 'Unavailable',
      'Visible Voice and project material'),
    metric('Local models ready', data.models
      ? `${cached} of ${readCount(models)}` : 'Unavailable', 'Pinned model availability'),
  );
  return content;
}

function renderRuntime(data) {
  const content = document.createElement('div');
  content.className = 'support-list';
  const activeJobs = Number(data.memory?.active_jobs || 0);
  const loaded = readCount(data.memory?.loaded_model_keys);
  const operation = data.models?.operation?.status || 'idle';
  content.append(
    statusLine(
      activeJobs ? 'Synthesis active' : 'Synthesis idle',
      activeJobs ? `${activeJobs} audio job${activeJobs === 1 ? '' : 's'} running` : 'No active audio jobs',
      activeJobs ? 'warning' : 'success',
    ),
    statusLine(
      loaded ? 'Models loaded' : 'No model loaded',
      loaded ? `${loaded} model${loaded === 1 ? '' : 's'} held in memory` : 'Memory can be reclaimed as needed',
      loaded ? 'neutral' : 'success',
    ),
    statusLine(
      operation === 'idle' ? 'Cache idle' : 'Cache operation active',
      operation === 'idle' ? 'No model download or repair is running' : 'Review Local model cache for progress',
      operation === 'idle' ? 'success' : 'warning',
    ),
  );
  return content;
}

function renderMigration(data, actionRegion, actionOptions) {
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
      item.state === 'rolled_back' ? 'A prior migration was safely reversed.' : 'A recorded maintenance operation is available.',
      item.state === 'failed' ? 'error' : 'neutral',
    ));
  });
  const actions = document.createElement('div');
  actions.className = 'guarded-actions';
  if (!unavailable && migration.migration_required && !migration.migration_blocked) {
    actions.append(guardedAction({
      ...actionOptions,
      label: 'Apply migration',
      phrase: 'APPLY MIGRATION',
      body: 'Review the configuration plan and its recovery point before applying it.',
      request: () => actionOptions.api.post('/api/migration/apply', {
        plan_fingerprint: migration.plan_fingerprint,
        confirm: true,
      }, { signal: actionOptions.signal }),
      success: 'Migration applied',
    }));
  }
  const rollback = !unavailable && history.find(
    (item) => item.rollback_available && item.operation_id,
  );
  if (rollback) {
    actions.append(guardedAction({
      ...actionOptions,
      label: 'Roll back migration',
      phrase: 'ROLL BACK',
      body: 'Review the recovery point. Rollback restores the recorded pre-migration configuration.',
      destructive: true,
      request: () => actionOptions.api.post('/api/migration/rollback', {
        operation_id: rollback.operation_id,
      }, { signal: actionOptions.signal }),
      success: 'Migration rolled back',
    }));
  }
  if (actions.childElementCount) content.append(actions);
  content.append(actionRegion);
  return content;
}

function renderGuardedModelActions(actionRegion, options) {
  const content = document.createElement('div');
  content.className = 'guarded-actions';
  content.append(
    guardedAction({
      ...options,
      label: 'Download or Repair required models',
      phrase: 'DOWNLOAD MODELS',
      body: 'Review impact before starting a local model cache operation. One cache operation runs at a time.',
      request: () => options.api.post('/api/model_registry/action', {
        action: 'download_required',
      }, { signal: options.signal }),
      success: 'Model cache operation started',
    }),
    guardedAction({
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

function renderMaintenance({
  data,
  route,
  shell,
  api,
  signal,
  owner,
  stateRegion,
}) {
  const toolbar = document.createElement('div');
  toolbar.className = 'support-toolbar';
  toolbar.append(supportReturn(route, shell));
  const health = renderHealth(data);
  const runtime = renderRuntime(data);
  const migrationFeedback = document.createElement('div');
  migrationFeedback.setAttribute('role', 'status');
  const modelFeedback = document.createElement('div');
  modelFeedback.setAttribute('role', 'status');
  const refresh = () => shell.navigate(route.hash, { historyMode: 'replace' });
  const actionOptions = {
    region: migrationFeedback,
    api,
    signal,
    refresh,
  };
  const sections = document.createElement('div');
  sections.className = 'maintenance-section-grid';
  sections.append(
    section(
      'health',
      'Read-only health',
      'A compact view of recovery, project, library, and local model state.',
      health,
    ),
    section(
      'runtime',
      'Runtime diagnostics',
      'Current activity and memory state. Internal locations and credentials are never displayed.',
      runtime,
    ),
    section(
      'llm-profiles',
      'Stage model profiles',
      'Stage routing remains evidence-gated and separate from ordinary Settings.',
      UI.notice({
        tone: 'information',
        title: 'Profile editing is intentionally isolated',
        body: 'Use this read-only overview to confirm runtime health before changing a stage profile.',
      }),
    ),
    section(
      'advanced-generation',
      'Advanced generation',
      'Low-level generation controls remain separate from the Script, Cast, Produce, and Export workflow.',
      renderGuardedModelActions(modelFeedback, {
        ...actionOptions,
        region: modelFeedback,
      }),
    ),
    section(
      'migration',
      'Migration and recovery history',
      'Guarded actions require an exact confirmation phrase and create recoverable history.',
      renderMigration(data, migrationFeedback, actionOptions),
    ),
  );
  stateRegion.replaceChildren(toolbar, sections);
  owner.dataset.viewState = 'ready';
  focusMode(route.context.mode || 'health', signal);
}

export async function mount({ root, route, shell, api, signal }) {
  const dataRouteOwner = route.path;
  const { owner, stateRegion } = supportOwner(root, route, {
    shell,
    page: 'maintenance',
    title: 'Maintenance',
    subtitle: 'Read-only health first, with explicit review for guarded technical actions.',
    className: 'maintenance-workspace specialist-workspace',
  });
  owner.dataset.routeOwner = dataRouteOwner;
  stateRegion.setAttribute('data-state-region', '');
  const settled = await Promise.allSettled(
    READS.map(([, endpoint]) => api.get(endpoint, { signal })),
  );
  if (signal.aborted) return () => {};
  const data = Object.fromEntries(READS.map(([key], index) => [
    key,
    safeRead(settled, index),
  ]));
  if (Object.values(data).every((value) => value === null)) {
    owner.dataset.viewState = 'error';
    stateRegion.replaceChildren(UI.notice({
      tone: 'error',
      title: 'Maintenance status could not be loaded',
      body: 'No authoritative health source responded. Nothing was changed.',
      live: true,
    }));
    return () => {};
  }
  renderMaintenance({
    data,
    route,
    shell,
    api,
    signal,
    owner,
    stateRegion,
  });
  return () => {};
}
