'use strict';

import {
  readCount, statusLine, supportReturn,
} from '/static/pages/more.js';
import {
  focusMaintenanceMode, maintenanceMetric, maintenanceSection,
} from './maintenance_model.js';
import {
  renderMigrationActions, renderModelActions,
} from './maintenance_actions.js';

const UI = globalThis.AlexandriaUI;

function renderHealth(data) {
  const recoveryStages = data.recovery?.stages || [];
  const models = data.models?.models || [];
  const projects = data.projects?.projects || data.projects?.items || [];
  const library = data.library?.artifacts || [];
  const cached = models.filter((item) => item.cached || item.state === 'cached').length;
  const content = document.createElement('div');
  content.className = 'maintenance-summary';
  content.append(
    maintenanceMetric('Recovery checks', data.recovery ? readCount(recoveryStages) : 'Unavailable',
      'Current project recovery stages'),
    maintenanceMetric('Available projects', data.projects ? readCount(projects) : 'Unavailable',
      'Projects currently known to Alexandria'),
    maintenanceMetric('Library entries', data.library ? readCount(library) : 'Unavailable',
      'Visible Voice and project material'),
    maintenanceMetric('Local models ready', data.models
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
      activeJobs
        ? `${activeJobs} audio job${activeJobs === 1 ? '' : 's'} running`
        : 'No active audio jobs',
      activeJobs ? 'warning' : 'success',
    ),
    statusLine(
      loaded ? 'Models loaded' : 'No model loaded',
      loaded
        ? `${loaded} model${loaded === 1 ? '' : 's'} held in memory`
        : 'Memory can be reclaimed as needed',
      loaded ? 'neutral' : 'success',
    ),
    statusLine(
      operation === 'idle' ? 'Cache idle' : 'Cache operation active',
      operation === 'idle'
        ? 'No model download or repair is running'
        : 'Review Local model cache for progress',
      operation === 'idle' ? 'success' : 'warning',
    ),
  );
  return content;
}

export function renderMaintenanceSections({
  data, route, shell, api, signal, owner, stateRegion,
}) {
  const toolbar = document.createElement('div');
  toolbar.className = 'support-toolbar';
  toolbar.append(supportReturn(route, shell));
  const migrationFeedback = document.createElement('div');
  migrationFeedback.setAttribute('role', 'status');
  const modelFeedback = document.createElement('div');
  modelFeedback.setAttribute('role', 'status');
  const refresh = () => shell.navigate(route.hash, { historyMode: 'replace' });
  const actionOptions = { region: migrationFeedback, api, signal, refresh };
  const sections = document.createElement('div');
  sections.className = 'maintenance-section-grid';
  sections.append(
    maintenanceSection(
      'health',
      'Read-only health',
      'A compact view of recovery, project, library, and local model state.',
      renderHealth(data),
    ),
    maintenanceSection(
      'runtime',
      'Runtime diagnostics',
      'Current activity and memory state. Internal locations and credentials are never displayed.',
      renderRuntime(data),
    ),
    maintenanceSection(
      'llm-profiles',
      'Stage model profiles',
      'Stage routing remains evidence-gated and separate from ordinary Settings.',
      UI.notice({
        tone: 'information',
        title: 'Profile editing is intentionally isolated',
        body: 'Use this read-only overview to confirm runtime health before changing a stage profile.',
      }),
    ),
    maintenanceSection(
      'advanced-generation',
      'Advanced generation',
      'Low-level generation controls remain separate from the Script, Cast, Produce, and Export workflow.',
      renderModelActions(modelFeedback, {
        ...actionOptions,
        region: modelFeedback,
      }),
    ),
    maintenanceSection(
      'migration',
      'Migration and recovery history',
      'Guarded actions require an exact confirmation phrase and create recoverable history.',
      renderMigrationActions(data, migrationFeedback, actionOptions),
    ),
  );
  stateRegion.replaceChildren(toolbar, sections);
  owner.dataset.viewState = 'ready';
  focusMaintenanceMode(route.context.mode || 'health', signal);
}
