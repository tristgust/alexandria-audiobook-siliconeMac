'use strict';

(() => {
  if (globalThis.__alexandriaStableRuntimePatch) return;
  globalThis.__alexandriaStableRuntimePatch = true;

  const state = {
    lifecycle: null,
    candidate: null,
    refreshTimer: null,
    modal: null,
    returnFocus: null,
    castRefreshActive: false,
    reviewOverride: null,
    reviewBusy: false,
    allowLegacyVoiceEditor: false,
    stableSelectedCharacterId: null,
    legacyVoiceButton: null,
  };

  function text(tag, value, className = '') {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value == null ? '' : String(value);
    return node;
  }

  async function apiJson(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin',
      cache: 'no-store',
      ...options,
    });
    const raw = await response.text();
    let data = null;
    try {
      data = raw ? JSON.parse(raw) : null;
    } catch (_error) {
      data = raw;
    }
    if (!response.ok) {
      const detail = data?.detail;
      const message = typeof detail === 'object'
        ? detail.message || detail.code
        : detail || data?.message || raw || response.statusText;
      const error = new Error(String(message || `Request failed (${response.status})`));
      error.detail = typeof detail === 'object' ? detail : null;
      error.status = response.status;
      throw error;
    }
    return data;
  }

  async function refreshCastStageAction() {
    if (state.castRefreshActive || document.body?.dataset.destination !== 'cast') return;
    const action = document.getElementById('shell-primary-action');
    if (!action || action.dataset.stableDestination === 'produce') return;
    state.castRefreshActive = true;
    try {
      const flow = await apiJson('/api/project_flow/status');
      if (flow?.stage_map?.cast?.state !== 'complete') return;
      action.textContent = 'Continue to Produce';
      action.disabled = false;
      action.dataset.stableDestination = 'produce';
      action.title = 'Open Produce for the current project.';
    } catch (_error) {
      // The pinned stable page remains authoritative when the compatibility probe fails.
    } finally {
      state.castRefreshActive = false;
    }
  }

  function managedImportReady() {
    return document.body?.dataset.destination === 'script'
      && state.lifecycle?.primary_action?.id === 'review_imported_script'
      && state.lifecycle?.artifact?.script_exists !== true
      && state.candidate?.status === 'ready';
  }

  function updatePrimaryAction() {
    const action = document.getElementById('shell-primary-action');
    if (!action) return;
    if (managedImportReady()) {
      action.dataset.stableManagedImport = 'true';
      delete action.dataset.stableReviewedOverride;
      action.textContent = 'Review imported Script';
      action.disabled = false;
      action.title = 'Review the stored imported Script before applying it.';
    } else if (state.reviewOverride?.auditFingerprint) {
      delete action.dataset.stableManagedImport;
      action.dataset.stableReviewedOverride = 'true';
      action.textContent = 'Approve reviewed differences';
      action.disabled = state.reviewBusy;
      action.title = 'Approve this exact imported Script with its reviewed source-difference receipt.';
    } else {
      delete action.dataset.stableManagedImport;
      delete action.dataset.stableReviewedOverride;
      if (
        action.title === 'Review the stored imported Script before applying it.'
        || action.title === 'Approve this exact imported Script with its reviewed source-difference receipt.'
      ) {
        action.removeAttribute('title');
      }
    }
  }

  async function refreshManagedImport() {
    clearTimeout(state.refreshTimer);
    state.refreshTimer = null;
    if (document.body?.dataset.destination !== 'script') {
      state.lifecycle = null;
      state.candidate = null;
      return;
    }
    installStableTaskBundleWorkflow();
    try {
      const [lifecycle, candidate] = await Promise.all([
        apiJson('/api/script_lifecycle/status'),
        apiJson('/api/script_lifecycle/import-candidate'),
      ]);
      const currentScript = lifecycle?.fingerprints?.script || null;
      const currentSource = lifecycle?.fingerprints?.source || null;
      if (
        state.reviewOverride
        && (state.reviewOverride.scriptFingerprint !== currentScript
          || state.reviewOverride.sourceFingerprint !== currentSource)
      ) {
        state.reviewOverride = null;
      }
      state.lifecycle = lifecycle;
      state.candidate = candidate;
      installStableTaskBundleWorkflow();
      updatePrimaryAction();
    } catch (_error) {
      state.lifecycle = null;
      state.candidate = null;
      updatePrimaryAction();
    }
  }

  function installStableTaskBundleWorkflow() {
    if (document.body?.dataset.destination !== 'script') return false;
    const workflow = document.getElementById('script-external-workflow');
    const host = document.querySelector('#script-review-workspace .script-review-main');
    if (!workflow || !host) return false;

    workflow.classList.add('script-review-disclosure');
    if (!workflow.hasAttribute('data-stable-task-bundle-workflow')) {
      workflow.dataset.stableTaskBundleWorkflow = '';
    }
    const entryList = host.querySelector('#script-entry-list');
    const provenance = host.querySelector('#script-provenance-disclosure');
    const insertionPoint = entryList || provenance || null;
    if (workflow.parentElement !== host || workflow.nextElementSibling !== insertionPoint) {
      host.insertBefore(workflow, insertionPoint);
    }

    const intro = workflow.querySelector('.external-workflow-intro');
    const introCopy = 'Export the task ZIP, attach it directly to an ordinary ChatGPT conversation, then download the completed ZIP ChatGPT returns and import it here. Do not unzip either file.';
    if (intro && intro.textContent !== introCopy) intro.textContent = introCopy;
    const exportCopy = workflow.querySelector('#task-bundle-export-heading')?.parentElement?.querySelector('p');
    const exportCopyText = 'Choose the task and download one self-contained ZIP. Attach that ZIP directly to ChatGPT; its instructions, schema, source material, and checksums are already inside.';
    if (exportCopy && exportCopy.textContent !== exportCopyText) exportCopy.textContent = exportCopyText;
    const importCopy = workflow.querySelector('#task-bundle-import-heading')?.parentElement?.querySelector('p');
    const importCopyText = 'Choose the completed ZIP returned by ChatGPT. Alexandria validates it and opens the correct native review. Do not unzip it.';
    if (importCopy && importCopy.textContent !== importCopyText) importCopy.textContent = importCopyText;
    const completedName = document.getElementById('completed-task-file')
      ?.closest('[data-file-picker]')?.querySelector('.file-picker-name');
    const completedNameCopy = 'Completed task ZIP or result JSON';
    if (completedName && completedName.textContent !== completedNameCopy) {
      completedName.textContent = completedNameCopy;
    }
    const originalHelp = document.getElementById('original-task-file-wrap')
      ?.querySelector('.file-picker-meta');
    const originalHelpCopy = 'Only needed when a fallback or legacy JSON result cannot be matched to Alexandria’s local task library.';
    if (originalHelp && originalHelp.textContent !== originalHelpCopy) {
      originalHelp.textContent = originalHelpCopy;
    }
    if (state.lifecycle?.generation_method === 'chatgpt_task_bundle'
      && state.lifecycle?.accepted !== true) {
      workflow.open = true;
    }
    return true;
  }

  function scheduleRefresh(delay = 120) {
    clearTimeout(state.refreshTimer);
    state.refreshTimer = window.setTimeout(refreshManagedImport, delay);
  }

  function syncRailAccessibility() {
    const rail = document.querySelector('.alexandria-rail');
    const toggle = document.getElementById('rail-mobile-toggle');
    if (!rail || !toggle) return;
    const mobile = window.matchMedia('(max-width: 760px)').matches;
    const open = mobile && document.body.classList.contains('rail-open');
    document.documentElement.style.setProperty(
      '--stable-layout-viewport',
      `${document.documentElement.clientWidth}px`,
    );
    document.documentElement.style.setProperty(
      '--stable-layout-height',
      `${document.documentElement.clientHeight}px`,
    );
    if (mobile) {
      rail.inert = !open;
      rail.setAttribute('aria-hidden', String(!open));
      toggle.setAttribute('aria-expanded', String(open));
      toggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
    } else {
      rail.inert = false;
      rail.removeAttribute('aria-hidden');
      toggle.setAttribute('aria-expanded', 'false');
      toggle.setAttribute('aria-label', 'Open navigation');
    }
  }

  function installStyles() {
    if (document.getElementById('stable-managed-import-styles')) return;
    const style = document.createElement('style');
    style.id = 'stable-managed-import-styles';
    style.textContent = `
      body.canonical-shell details:not([open]) > :not(summary) {
        display: none !important;
      }
      body.canonical-shell #shell-project-title {
        min-height: 32px;
      }
      body.canonical-shell #persistent-player-timeline,
      body.canonical-shell #persistent-player-volume {
        min-height: 32px;
      }
      body.canonical-shell .form-label,
      body.canonical-shell .file-picker-name,
      body.canonical-shell .file-picker-action,
      body.canonical-shell .file-picker-meta,
      body.canonical-shell .stage-page-state,
      body.canonical-shell .voice-projects-overview-main,
      body.canonical-shell .diagnostic-status,
      body.canonical-shell .voice-capability-copy,
      body.canonical-shell .workflow-help,
      body.canonical-shell .character-tool-context-label,
      body.canonical-shell .design-route-label,
      body.canonical-shell .more-tool-context,
      body.canonical-shell .more-tool-state,
      body.canonical-shell .settings-save-state,
      body.canonical-shell .resource-row-copy,
      body.canonical-shell .resource-status,
      body.canonical-shell .resource-row-facts dt,
      body.canonical-shell .resource-row-facts dd,
      body.canonical-shell .voice-capability-route strong,
      body.canonical-shell .voice-capability-route span,
      body.canonical-shell .voice-capability-supported strong,
      body.canonical-shell .voice-capability-supported span,
      body.canonical-shell .training-dataset-empty strong,
      body.canonical-shell .training-dataset-empty span,
      body.canonical-shell .maintenance-summary-strip span,
      body.canonical-shell .maintenance-row-state,
      body.canonical-shell .maintenance-section-state,
      body.canonical-shell .maintenance-section-copy,
      body.canonical-shell .maintenance-list span,
      body.canonical-shell .model-cache-overview,
      body.canonical-shell .model-cache-overview small,
      body.canonical-shell #speaker-management-tab p,
      body.canonical-shell #designer-tab p,
      body.canonical-shell #dataset-builder-tab p,
      body.canonical-shell #training-tab p,
      body.canonical-shell #training-tab li,
      body.canonical-shell #training-tab dt,
      body.canonical-shell #training-tab dd,
      body.canonical-shell #training-tab code,
      body.canonical-shell #training-tab .voice-capability-flow *,
      body.canonical-shell #training-tab .voice-capability-comparison * {
        font-size: 13px !important;
      }
      body.canonical-shell summary,
      body.canonical-shell .form-check,
      body.canonical-shell .maintenance-memory-controls label {
        min-height: 32px;
      }
      body.canonical-shell .form-check,
      body.canonical-shell .maintenance-memory-controls label {
        display: flex;
        align-items: center;
      }
      body.canonical-shell .maintenance-memory-controls select {
        min-height: 32px;
      }
      @media (min-width: 761px) and (max-width: 900px) {
        body.canonical-shell .canonical-global-heading {
          min-width: 0;
          overflow: hidden;
        }
        body.canonical-shell #shell-global-title {
          font-size: 32px;
          line-height: 34px;
        }
        body.canonical-shell #shell-global-subtitle {
          overflow: hidden;
          white-space: nowrap;
          text-overflow: ellipsis;
        }
        body.canonical-shell .canonical-project-header {
          height: auto;
          min-height: 136px;
          grid-template-columns: minmax(0, 1fr) auto;
          grid-template-rows: auto auto;
          gap: 10px 12px;
          padding: 14px 18px;
        }
        body.canonical-shell .canonical-project-identity {
          grid-column: 1;
          grid-row: 1;
          min-width: 0;
        }
        body.canonical-shell .canonical-project-actions {
          grid-column: 2;
          grid-row: 1;
          min-width: 0;
          justify-content: flex-end;
        }
        body.canonical-shell .canonical-project-actions .canonical-shell-workflow-state {
          display: none;
        }
        body.canonical-shell .canonical-project-header .canonical-stage-tracker {
          grid-column: 1 / -1;
          grid-row: 2;
          width: 100%;
          min-width: 0;
          max-width: 100%;
          grid-template-columns: repeat(4, minmax(80px, 1fr));
          overflow-x: auto;
        }
        body.canonical-shell .persistent-player-host {
          grid-template-columns: 40px 58px 40px minmax(0, 1fr) minmax(90px, 130px);
          gap: 10px;
          padding-inline: 12px;
        }
        body.canonical-shell .persistent-player-speed,
        body.canonical-shell .persistent-player-volume {
          display: none !important;
        }
        body.canonical-shell .persistent-player-timeline {
          min-width: 0;
          grid-template-columns: 38px minmax(0, 1fr) 38px;
          gap: 8px;
        }
        body.canonical-shell #persistent-player-timeline,
        body.canonical-shell .persistent-player-meta {
          width: 100%;
          min-width: 0;
        }
        body.canonical-shell .project-home-workspace {
          width: 100%;
          min-width: 0;
          max-width: 100%;
          grid-template-columns: minmax(0, 1fr) !important;
        }
        body.canonical-shell .project-home-toolbar {
          display: grid !important;
          width: 100%;
          min-width: 0;
          max-width: 100%;
          grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
          gap: 12px !important;
        }
        body.canonical-shell .project-home-toolbar > div {
          display: grid !important;
          min-width: 0;
          grid-template-columns: minmax(0, 1fr) !important;
          gap: 6px !important;
        }
        body.canonical-shell .project-home-toolbar .form-select {
          width: 100%;
          min-width: 0;
        }
        body.canonical-shell .project-continuation,
        body.canonical-shell .project-continuation-panel,
        body.canonical-shell .project-row,
        body.canonical-shell .project-list {
          width: 100%;
          min-width: 0;
          max-width: 100%;
        }
        body.canonical-shell .project-continuation-panel {
          grid-template-columns: 88px minmax(0, 1fr) !important;
          gap: 12px 16px !important;
        }
        body.canonical-shell .project-continuation-panel > .project-cover-placeholder {
          grid-column: 1;
          grid-row: 1 / 3;
        }
        body.canonical-shell .project-continuation-copy {
          grid-column: 2;
          grid-row: 1;
          min-width: 0;
        }
        body.canonical-shell .project-continuation-next {
          grid-column: 2;
          grid-row: 2;
          min-width: 0;
        }
        body.canonical-shell .project-continuation-panel > .project-open-action {
          grid-column: 2;
          grid-row: 3;
          justify-self: start;
        }
        body.canonical-shell .project-row {
          grid-template-columns: 64px minmax(0, 1fr) 40px !important;
          gap: 10px 12px !important;
          overflow: hidden;
        }
        body.canonical-shell .project-row-cover {
          grid-column: 1;
          grid-row: 1 / 4;
        }
        body.canonical-shell .project-row-identity {
          grid-column: 2;
          grid-row: 1;
          min-width: 0;
          overflow: hidden;
        }
        body.canonical-shell .project-row-status {
          grid-column: 2;
          grid-row: 2;
          min-width: 0;
        }
        body.canonical-shell .project-row-stage {
          grid-column: 2;
          grid-row: 3;
          min-width: 0;
        }
        body.canonical-shell .project-row > .project-open-action {
          grid-column: 3;
          grid-row: 1;
        }
        body.canonical-shell .project-row > .project-more-action {
          grid-column: 3;
          grid-row: 2;
        }
        body.canonical-shell .project-mini-tracker {
          width: 100%;
          min-width: 0;
          max-width: 100%;
          grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
          overflow: hidden;
        }
        body.canonical-shell #script-review-workspace,
        body.canonical-shell #script-review-content,
        body.canonical-shell .script-source-context,
        body.canonical-shell .script-review-layout,
        body.canonical-shell .script-review-main,
        body.canonical-shell .script-list-footer,
        body.canonical-shell #script-external-workflow {
          width: 100%;
          min-width: 0;
          max-width: 100%;
        }
        body.canonical-shell .script-source-context {
          grid-template-columns: 112px minmax(0, 1fr) !important;
          gap: 12px 16px !important;
        }
        body.canonical-shell .script-source-cover {
          grid-column: 1;
          grid-row: 1 / 3;
        }
        body.canonical-shell .script-source-identity {
          grid-column: 2;
          grid-row: 1;
          min-width: 0;
        }
        body.canonical-shell .script-source-location {
          grid-column: 2;
          grid-row: 2;
          width: 100%;
          min-width: 0;
          max-width: 100%;
          justify-items: start;
          text-align: left;
        }
        body.canonical-shell .script-list-footer {
          grid-template-columns: minmax(0, 1fr) auto !important;
          gap: 10px 12px !important;
        }
        body.canonical-shell .script-issue-navigation {
          width: 100%;
          min-width: 0;
          max-width: 100%;
          grid-column: 1 / -1;
          justify-content: space-between;
        }
        body.canonical-shell #export-workspace,
        body.canonical-shell #export-content,
        body.canonical-shell .export-publication,
        body.canonical-shell .export-publication-copy,
        body.canonical-shell .export-metadata-grid,
        body.canonical-shell .export-preview,
        body.canonical-shell .export-panel {
          width: 100%;
          min-width: 0;
          max-width: 100%;
        }
        body.canonical-shell #export-content {
          grid-template-columns: minmax(0, 1fr) !important;
        }
        body.canonical-shell .export-publication {
          grid-template-columns: 100px minmax(0, 1fr) !important;
        }
        body.canonical-shell .export-metadata-grid {
          grid-template-columns: minmax(0, 1fr) !important;
        }
        body.canonical-shell .export-summary-metrics {
          grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
        }
      }
      @media (max-width: 760px) {
        body.canonical-shell .alexandria-rail {
          height: var(--stable-layout-height, 100dvh);
          max-height: var(--stable-layout-height, 100dvh);
        }
        body.canonical-shell .persistent-player-host {
          right: auto;
          width: var(--stable-layout-viewport, 100vw);
          max-width: var(--stable-layout-viewport, 100vw);
        }
      }
      @media (max-width: 480px) {
        body.canonical-shell .project-home-workspace {
          width: calc(var(--stable-layout-viewport, 100vw) - 40px);
          min-width: 0;
          max-width: calc(var(--stable-layout-viewport, 100vw) - 40px);
          grid-template-columns: minmax(0, 1fr) !important;
        }
        body.canonical-shell .project-home-toolbar {
          display: grid !important;
          width: 100%;
          min-width: 0;
          max-width: 100%;
          grid-template-columns: minmax(0, 1fr) !important;
          gap: 12px !important;
        }
        body.canonical-shell .project-home-toolbar > * {
          width: 100%;
          min-width: 0;
          max-width: 100%;
        }
        body.canonical-shell .project-home-toolbar > div {
          display: grid !important;
          grid-template-columns: minmax(0, 1fr) !important;
          gap: 8px !important;
        }
        body.canonical-shell .project-home-toolbar .form-select,
        body.canonical-shell .project-continuation,
        body.canonical-shell .project-continuation-panel,
        body.canonical-shell .project-list,
        body.canonical-shell .project-row {
          box-sizing: border-box;
          width: 100%;
          min-width: 0;
          max-width: 100%;
        }
        body.canonical-shell .project-continuation,
        body.canonical-shell .project-continuation-panel,
        body.canonical-shell .project-list,
        body.canonical-shell .project-row {
          overflow: hidden;
        }
        body.canonical-shell .project-row > .project-open-action {
          max-width: 100%;
        }
        body.canonical-shell #export-workspace,
        body.canonical-shell #export-content,
        body.canonical-shell .export-publication,
        body.canonical-shell .export-publication-copy,
        body.canonical-shell .export-metadata-grid,
        body.canonical-shell .export-preview,
        body.canonical-shell .export-panel {
          box-sizing: border-box;
          width: 100%;
          min-width: 0;
          max-width: 100%;
        }
        body.canonical-shell #export-content {
          grid-template-columns: minmax(0, 1fr) !important;
        }
        body.canonical-shell .export-publication {
          grid-template-columns: 88px minmax(0, 1fr) !important;
          overflow: hidden;
        }
        body.canonical-shell .export-publication-copy {
          overflow: hidden;
        }
        body.canonical-shell #export-publication-title {
          max-width: 100%;
          overflow: hidden;
          white-space: nowrap;
          text-overflow: ellipsis;
        }
        body.canonical-shell .export-metadata-grid,
        body.canonical-shell .export-summary-metrics,
        body.canonical-shell .export-format-group {
          grid-template-columns: minmax(0, 1fr) !important;
        }
        body.canonical-shell .persistent-player-host {
          grid-template-columns: 40px 52px 40px minmax(0, 1fr);
          gap: 8px;
          padding-inline: 10px;
        }
        body.canonical-shell .persistent-player-timeline {
          min-width: 0;
          grid-template-columns: minmax(0, 1fr);
          gap: 0;
        }
        body.canonical-shell .persistent-player-timeline time {
          display: none;
        }
        body.canonical-shell #persistent-player-timeline {
          width: 100%;
          min-width: 0;
        }
      }
      @media (max-width: 360px) {
        body.canonical-shell .project-home-workspace {
          width: 100%;
          min-width: 0;
          max-width: 100%;
          grid-template-columns: minmax(0, 1fr) !important;
        }
        body.canonical-shell .project-home-toolbar {
          display: grid !important;
          width: 100%;
          min-width: 0;
          max-width: 100%;
          grid-template-columns: minmax(0, 1fr) !important;
          gap: 12px !important;
        }
        body.canonical-shell .project-home-toolbar > * {
          width: 100%;
          min-width: 0;
          max-width: 100%;
        }
        body.canonical-shell .project-home-toolbar > div {
          display: grid !important;
          grid-template-columns: minmax(0, 1fr) !important;
          gap: 8px !important;
        }
        body.canonical-shell .project-home-toolbar .form-select {
          width: 100%;
          min-width: 0;
        }
        body.canonical-shell .project-continuation,
        body.canonical-shell .project-continuation-panel,
        body.canonical-shell .project-continuation-copy,
        body.canonical-shell .project-row,
        body.canonical-shell .project-row-identity {
          min-width: 0;
          max-width: 100%;
        }
        body.canonical-shell .project-continuation,
        body.canonical-shell .project-continuation-panel,
        body.canonical-shell .project-row {
          overflow: hidden;
        }
        body.canonical-shell .project-mini-tracker {
          width: 100%;
          min-width: 0;
          max-width: 100%;
          grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
          overflow: hidden;
        }
        body.canonical-shell .project-mini-tracker > li {
          min-width: 0;
        }
        body.canonical-shell #toast-container:empty {
          display: none !important;
        }
        body.canonical-shell .persistent-player-host {
          grid-template-columns: 40px 52px 40px minmax(0, 1fr);
          gap: 8px;
          padding-inline: 10px;
        }
        body.canonical-shell .persistent-player-timeline {
          min-width: 0;
          grid-template-columns: minmax(0, 1fr);
          gap: 0;
        }
        body.canonical-shell .persistent-player-timeline time {
          display: none;
        }
        body.canonical-shell #persistent-player-timeline {
          width: 100%;
          min-width: 0;
        }
      }
      .stable-managed-import-layer {
        position: fixed;
        box-sizing: border-box;
        z-index: 10050;
        inset: 0;
        display: grid;
        min-width: 0;
        min-height: 0;
        place-items: center;
        padding: clamp(12px, 3vw, 32px);
        background: rgba(35, 33, 30, .42);
      }
      .stable-managed-import-dialog {
        display: grid;
        box-sizing: border-box;
        width: min(900px, 100%);
        height: min(820px, 100%);
        min-height: 0;
        max-height: 100%;
        grid-template-rows: auto minmax(0, 1fr) auto;
        overflow: hidden;
        border: 1px solid #bdb2a5;
        border-radius: 12px;
        background: #faf8f2;
        box-shadow: 0 24px 60px rgba(35, 33, 30, .28);
        color: #23211e;
      }
      .stable-managed-import-header,
      .stable-managed-import-footer {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        padding: 18px 22px;
      }
      .stable-managed-import-header { border-bottom: 1px solid #d8d0c5; }
      .stable-managed-import-header h2 { margin: 0; font-family: Georgia, serif; font-size: 1.45rem; }
      .stable-managed-import-close {
        width: 36px;
        height: 36px;
        border: 0;
        background: transparent;
        font-size: 1.35rem;
      }
      .stable-managed-import-body {
        display: grid;
        min-height: 0;
        align-content: start;
        gap: 18px;
        padding: 22px;
        overflow: auto;
        overscroll-behavior: contain;
        scrollbar-gutter: stable;
      }
      .stable-managed-import-summary {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
        margin: 0;
      }
      .stable-managed-import-summary div {
        padding: 12px;
        border: 1px solid #d8d0c5;
        border-radius: 8px;
        background: #fffdf9;
      }
      .stable-managed-import-summary dt { color: #68635d; font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; }
      .stable-managed-import-summary dd { margin: 4px 0 0; font-weight: 600; }
      .stable-managed-import-list { display: grid; gap: 0; padding: 0; margin: 0; list-style: none; border-top: 1px solid #d8d0c5; }
      .stable-managed-import-entry {
        display: grid;
        grid-template-columns: 140px minmax(0, 1fr);
        gap: 16px;
        padding: 14px 0;
        border-bottom: 1px solid #d8d0c5;
      }
      .stable-managed-import-speaker { font-size: .78rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }
      .stable-managed-import-copy { display: grid; gap: 5px; }
      .stable-managed-import-copy p { margin: 0; }
      .stable-managed-import-direction { color: #68635d; font-size: .88rem; font-style: italic; }
      .stable-source-difference {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 14px;
      }
      .stable-source-difference section {
        min-width: 0;
        padding: 14px;
        border: 1px solid #d8d0c5;
        border-radius: 8px;
        background: #fffdf9;
      }
      .stable-source-difference h3 {
        margin: 0 0 8px;
        color: #68635d;
        font-size: .78rem;
        letter-spacing: .04em;
        text-transform: uppercase;
      }
      .stable-source-difference pre {
        max-height: 240px;
        margin: 0;
        overflow: auto;
        font: inherit;
        line-height: 1.5;
        white-space: pre-wrap;
        overflow-wrap: anywhere;
      }
      .stable-voice-picker-intro {
        display: grid;
        gap: 6px;
      }
      .stable-voice-picker-intro p { margin: 0; color: #68635d; }
      .stable-voice-picker-field {
        display: grid;
        min-width: 0;
        gap: 8px;
      }
      .stable-voice-picker-field label {
        font-weight: 700;
      }
      .stable-voice-picker-select {
        width: 100%;
        min-width: 0;
        min-height: 44px;
        padding: 8px 10px;
        border: 1px solid #bdb2a5;
        border-radius: 8px;
        background: #fffdf9;
        color: #23211e;
        font: inherit;
      }
      .stable-voice-picker-summary {
        display: grid;
        min-width: 0;
        grid-template-columns: minmax(0, 1fr) auto;
        align-items: center;
        gap: 14px;
        padding: 16px;
        border: 1px solid #d8d0c5;
        border-radius: 8px;
        background: #fffdf9;
      }
      .stable-voice-picker-copy {
        display: grid;
        min-width: 0;
        gap: 4px;
      }
      .stable-voice-picker-copy strong {
        font-family: Georgia, serif;
        font-size: 1.15rem;
      }
      .stable-voice-picker-copy span { color: #68635d; }
      .stable-voice-picker-preview {
        width: min(100%, 480px);
      }
      .stable-voice-picker-note {
        padding: 12px 14px;
        border-left: 4px solid #9b7046;
        background: #f3ece2;
        color: #4f4439;
      }
      .stable-managed-import-footer {
        position: relative;
        z-index: 1;
        flex: 0 0 auto;
        border-top: 1px solid #d8d0c5;
        background: #faf8f2;
      }
      .stable-managed-import-status { min-width: 0; min-height: 20px; color: #68635d; }
      .stable-managed-import-actions { display: flex; flex: 0 0 auto; flex-wrap: wrap; gap: 10px; }
      @media (max-width: 720px) {
        .stable-managed-import-layer { padding: 8px; }
        .stable-managed-import-dialog {
          height: 100%;
          max-height: 100%;
        }
        .stable-managed-import-header,
        .stable-managed-import-footer { padding: 14px 16px; }
        .stable-managed-import-body { gap: 14px; padding: 16px; }
        .stable-managed-import-summary { grid-template-columns: 1fr; }
        .stable-managed-import-entry,
        .stable-source-difference,
        .stable-voice-picker-summary { grid-template-columns: 1fr; gap: 10px; }
        .stable-managed-import-footer { align-items: stretch; flex-direction: column; }
        .stable-managed-import-actions { justify-content: flex-end; }
      }
    `;
    document.head.append(style);
  }

  function closeModal() {
    if (!state.modal) return;
    state.modal.remove();
    state.modal = null;
    state.returnFocus?.focus();
    state.returnFocus = null;
  }

  function openManagedImportReview(opener) {
    if (!managedImportReady() || state.modal) return;
    installStyles();
    state.returnFocus = opener || document.activeElement;

    const layer = document.createElement('div');
    layer.className = 'stable-managed-import-layer';
    layer.setAttribute('role', 'dialog');
    layer.setAttribute('aria-modal', 'true');
    layer.setAttribute('aria-labelledby', 'stable-managed-import-title');

    const dialog = document.createElement('section');
    dialog.className = 'stable-managed-import-dialog';
    const header = document.createElement('header');
    header.className = 'stable-managed-import-header';
    const heading = text('h2', 'Review imported Script');
    heading.id = 'stable-managed-import-title';
    const close = text('button', '×', 'stable-managed-import-close');
    close.type = 'button';
    close.setAttribute('aria-label', 'Close imported Script review');
    close.addEventListener('click', closeModal);
    header.append(heading, close);

    const body = document.createElement('div');
    body.className = 'stable-managed-import-body';
    body.append(text(
      'p',
      'Review the stored Script before applying it. Applying creates the authoritative Script; approval remains a separate step.',
    ));
    const summary = document.createElement('dl');
    summary.className = 'stable-managed-import-summary';
    [
      ['Entries', state.candidate.entry_count?.toLocaleString() || '0'],
      ['Speakers', state.candidate.speaker_count?.toLocaleString() || '0'],
      ['Source', state.candidate.filename || 'Imported Script'],
    ].forEach(([label, value]) => {
      const item = document.createElement('div');
      item.append(text('dt', label), text('dd', value));
      summary.append(item);
    });
    body.append(summary);

    const entries = Array.isArray(state.candidate.entries) ? state.candidate.entries : [];
    const visible = entries.slice(0, 30);
    const list = document.createElement('ol');
    list.className = 'stable-managed-import-list';
    visible.forEach((entry) => {
      const row = document.createElement('li');
      row.className = 'stable-managed-import-entry';
      row.append(text('span', entry.speaker || 'Speaker', 'stable-managed-import-speaker'));
      const copy = document.createElement('div');
      copy.className = 'stable-managed-import-copy';
      copy.append(text('p', entry.text || ''));
      if (entry.instruct) copy.append(text('p', entry.instruct, 'stable-managed-import-direction'));
      row.append(copy);
      list.append(row);
    });
    body.append(list);
    if (entries.length > visible.length) {
      body.append(text(
        'p',
        `${(entries.length - visible.length).toLocaleString()} additional entries will appear in Script review after applying.`,
      ));
    }

    const footer = document.createElement('footer');
    footer.className = 'stable-managed-import-footer';
    const status = text('div', '', 'stable-managed-import-status');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const actions = document.createElement('div');
    actions.className = 'stable-managed-import-actions';
    const cancel = text('button', 'Cancel', 'btn btn-outline-secondary');
    cancel.type = 'button';
    cancel.addEventListener('click', closeModal);
    const apply = text('button', 'Apply imported Script', 'btn btn-primary');
    apply.type = 'button';
    apply.addEventListener('click', async () => {
      if (apply.disabled) return;
      apply.disabled = true;
      cancel.disabled = true;
      status.textContent = 'Applying imported Script…';
      try {
        await apiJson('/api/script_lifecycle/import-candidate/apply', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            expected_candidate_fingerprint: state.candidate.fingerprint,
          }),
        });
        status.textContent = 'Imported Script applied. Opening final Script review…';
        window.setTimeout(() => window.location.reload(), 120);
      } catch (error) {
        status.textContent = error.message || 'The imported Script could not be applied.';
        apply.disabled = false;
        cancel.disabled = false;
      }
    });
    actions.append(cancel, apply);
    footer.append(status, actions);
    dialog.append(header, body, footer);
    layer.append(dialog);
    layer.addEventListener('mousedown', (event) => {
      if (event.target === layer) closeModal();
    });
    layer.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeModal();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = [...layer.querySelectorAll('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])')];
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
    document.body.append(layer);
    state.modal = layer;
    apply.focus();
  }

  function scriptAcceptancePayload(reviewed = false) {
    const fingerprints = state.lifecycle?.fingerprints || {};
    return {
      expected_script_fingerprint: fingerprints.script,
      expected_metadata_fingerprint: fingerprints.metadata,
      expected_source_fingerprint: fingerprints.source,
      expected_state_fingerprint: state.lifecycle?.state_fingerprint || null,
      allow_reviewed_source_differences: reviewed,
      expected_audit_fingerprint: reviewed
        ? state.reviewOverride?.auditFingerprint || null : null,
    };
  }

  async function submitScriptAcceptance({ reviewed = false, status = null } = {}) {
    if (state.reviewBusy) return;
    if (!state.lifecycle) await refreshManagedImport();
    state.reviewBusy = true;
    updatePrimaryAction();
    if (status) status.textContent = reviewed
      ? 'Approving reviewed Script…' : 'Checking Script against the selected source…';
    try {
      await apiJson('/api/script_lifecycle/accept', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(scriptAcceptancePayload(reviewed)),
      });
      if (status) status.textContent = 'Script approved. Opening Cast…';
      window.setTimeout(() => {
        const query = location.hash.includes('?')
          ? location.hash.slice(location.hash.indexOf('?')) : '';
        location.hash = `#/cast${query}`;
        window.location.reload();
      }, 120);
    } catch (error) {
      const detail = error.detail || {};
      const context = detail.context || {};
      if (
        detail.code === 'script_acceptance_blocked'
        && context.reviewed_override_available
        && context.audit_fingerprint
      ) {
        state.reviewOverride = {
          auditFingerprint: context.audit_fingerprint,
          scriptFingerprint: state.lifecycle?.fingerprints?.script || null,
          sourceFingerprint: state.lifecycle?.fingerprints?.source || null,
          issue: Array.isArray(context.blocking_issues)
            ? context.blocking_issues[0] || null : null,
        };
        if (status) status.textContent = 'Review the recorded source difference before approval.';
        updatePrimaryAction();
        openSourceDifferenceReview(document.getElementById('shell-primary-action'));
      } else {
        if (detail.code === 'script_review_override_stale') state.reviewOverride = null;
        if (status) status.textContent = error.message || 'The Script could not be approved.';
        updatePrimaryAction();
      }
    } finally {
      state.reviewBusy = false;
      updatePrimaryAction();
    }
  }

  function openSourceDifferenceReview(opener) {
    if (!state.reviewOverride?.auditFingerprint || state.modal) return;
    installStyles();
    state.returnFocus = opener || document.activeElement;
    const issue = state.reviewOverride.issue || {};
    const layer = document.createElement('div');
    layer.className = 'stable-managed-import-layer';
    layer.setAttribute('role', 'dialog');
    layer.setAttribute('aria-modal', 'true');
    layer.setAttribute('aria-labelledby', 'stable-source-review-title');
    const dialog = document.createElement('section');
    dialog.className = 'stable-managed-import-dialog';
    const header = document.createElement('header');
    header.className = 'stable-managed-import-header';
    const heading = text('h2', 'Review source difference');
    heading.id = 'stable-source-review-title';
    const close = text('button', '×', 'stable-managed-import-close');
    close.type = 'button';
    close.setAttribute('aria-label', 'Close source-difference review');
    close.addEventListener('click', closeModal);
    header.append(heading, close);
    const body = document.createElement('div');
    body.className = 'stable-managed-import-body';
    body.append(text(
      'p',
      issue.message || issue.explanation
        || 'This imported Script differs from the prepared source or its speaker boundaries.',
    ));
    body.append(text(
      'p',
      'Approving keeps this exact imported Script and records the reviewed difference in the accepted-version receipt. It does not rewrite the source or hide the mismatch.',
    ));
    const comparison = document.createElement('div');
    comparison.className = 'stable-source-difference';
    const source = document.createElement('section');
    source.append(text('h3', 'Prepared source'), text('pre', issue.source_text || 'Source passage unavailable.'));
    const script = document.createElement('section');
    script.append(text('h3', 'Imported Script'), text('pre', issue.output_text || 'Script passage unavailable.'));
    comparison.append(source, script);
    body.append(comparison);
    const footer = document.createElement('footer');
    footer.className = 'stable-managed-import-footer';
    const status = text('div', '', 'stable-managed-import-status');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const actions = document.createElement('div');
    actions.className = 'stable-managed-import-actions';
    const cancel = text('button', 'Cancel', 'btn btn-outline-secondary');
    cancel.type = 'button';
    cancel.addEventListener('click', closeModal);
    const approve = text('button', 'Approve reviewed version', 'btn btn-primary');
    approve.type = 'button';
    approve.addEventListener('click', async () => {
      approve.disabled = true;
      cancel.disabled = true;
      await submitScriptAcceptance({ reviewed: true, status });
      if (state.modal) {
        approve.disabled = false;
        cancel.disabled = false;
      }
    });
    actions.append(cancel, approve);
    footer.append(status, actions);
    dialog.append(header, body, footer);
    layer.append(dialog);
    layer.addEventListener('mousedown', (event) => {
      if (event.target === layer) closeModal();
    });
    document.body.append(layer);
    state.modal = layer;
    approve.focus();
  }

  async function openStableFullCastTasks(opener) {
    if (state.modal) return;
    state.returnFocus = opener || document.activeElement;
    opener.disabled = true;
    try {
      const module = await import('/static/stable_full_cast_tasks.js');
      if (state.modal) return;
      const view = module.createStableFullCastDialog({
        apiJson,
        onClose: closeModal,
      });
      document.body.append(view.layer);
      state.modal = view.layer;
      view.focus();
    } catch (error) {
      const message = error?.message || 'Full Cast tasks could not be opened.';
      window.alert(message);
      state.returnFocus = null;
    } finally {
      opener.disabled = false;
    }
  }

  function installStableFullCastButton() {
    if (document.body?.dataset.destination !== 'cast') return false;
    const header = document.querySelector('.cast-master-header');
    if (!header || header.querySelector('[data-stable-full-cast-tasks]')) return false;
    const action = text('button', 'Full Cast tasks', 'btn btn-outline-secondary');
    action.type = 'button';
    action.dataset.stableFullCastTasks = '';
    action.addEventListener('click', () => openStableFullCastTasks(action));
    header.append(action);
    return true;
  }

  function installStableVoiceButton() {
    const current = document.getElementById('cast-edit-voice');
    if (!current || current.dataset.stableVoicePickerBound === 'true') return false;
    const button = current.cloneNode(true);
    button.dataset.stableVoicePickerBound = 'true';
    state.legacyVoiceButton = current;
    current.replaceWith(button);
    button.addEventListener('click', (event) => {
      if (state.allowLegacyVoiceEditor) {
        state.allowLegacyVoiceEditor = false;
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      openStableVoiceChooser(button);
    }, true);
    return true;
  }

  function selectedStableCastCharacter() {
    const row = document.querySelector(
      '.cast-character-row[aria-selected="true"][data-cast-character-id]',
    ) || (
      state.stableSelectedCharacterId
        ? document.querySelector(
          `.cast-character-row[data-cast-character-id="${CSS.escape(state.stableSelectedCharacterId)}"]`,
        )
        : null
    ) || document.querySelector('.cast-character-row[data-cast-character-id]');
    if (!row) return null;
    return {
      characterId: row.dataset.castCharacterId || '',
      name: row.querySelector('.cast-character-name')?.textContent.trim()
        || row.textContent.trim().split(/\n/)[0]
        || 'selected character',
    };
  }

  async function openStableVoiceChooser(opener) {
    if (state.modal) return;
    let character = selectedStableCastCharacter();
    if (!character?.characterId) {
      try {
        const aggregate = await apiJson('/api/cast');
        const selected = aggregate?.selected_character
          || (aggregate?.characters || [])[0]
          || null;
        if (selected?.character_id) {
          character = {
            characterId: selected.character_id,
            name: selected.display_name || selected.canonical_name || 'selected character',
          };
        }
      } catch (_error) {
        // The blocked state below provides the truthful fallback.
      }
    }
    if (!character?.characterId) {
      return;
    }
    installStyles();
    state.returnFocus = opener || document.activeElement;

    const layer = document.createElement('div');
    layer.className = 'stable-managed-import-layer';
    layer.setAttribute('role', 'dialog');
    layer.setAttribute('aria-modal', 'true');
    layer.setAttribute('aria-labelledby', 'stable-voice-picker-title');
    const dialog = document.createElement('section');
    dialog.className = 'stable-managed-import-dialog';
    const header = document.createElement('header');
    header.className = 'stable-managed-import-header';
    const heading = text('h2', `Choose a Voice for ${character.name}`);
    heading.id = 'stable-voice-picker-title';
    const close = text('button', '×', 'stable-managed-import-close');
    close.type = 'button';
    close.setAttribute('aria-label', 'Close Voice chooser');
    close.addEventListener('click', closeModal);
    header.append(heading, close);

    const body = document.createElement('div');
    body.className = 'stable-managed-import-body';
    const intro = document.createElement('div');
    intro.className = 'stable-voice-picker-intro';
    intro.append(
      text('strong', 'Choose by name'),
      text(
        'p',
        'Saved clones such as Benny, Narrator, and the Doctor appear alongside built-in Voices. Nothing changes until Assign Voice is pressed.',
      ),
    );
    const field = document.createElement('div');
    field.className = 'stable-voice-picker-field';
    const label = text('label', 'Voice');
    label.htmlFor = 'stable-voice-picker-select';
    const select = document.createElement('select');
    select.id = 'stable-voice-picker-select';
    select.className = 'stable-voice-picker-select';
    select.disabled = true;
    select.append(new Option('Loading Voices…', ''));
    field.append(label, select);
    const summary = document.createElement('div');
    summary.className = 'stable-voice-picker-summary';
    const summaryCopy = document.createElement('div');
    summaryCopy.className = 'stable-voice-picker-copy';
    const summaryTitle = text('strong', 'No Voice selected');
    const summaryBody = text(
      'span',
      'Choose a saved or built-in Voice from the list.',
    );
    summaryCopy.append(summaryTitle, summaryBody);
    const preview = document.createElement('audio');
    preview.className = 'stable-voice-picker-preview';
    preview.controls = true;
    preview.preload = 'metadata';
    preview.hidden = true;
    summary.append(summaryCopy, preview);
    const note = text(
      'div',
      'Experimental adapters that have not passed listening review are intentionally excluded from assignment.',
      'stable-voice-picker-note',
    );
    body.append(intro, field, summary, note);

    const footer = document.createElement('footer');
    footer.className = 'stable-managed-import-footer';
    const status = text('div', 'Loading the Voice catalog…', 'stable-managed-import-status');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const actions = document.createElement('div');
    actions.className = 'stable-managed-import-actions';
    const advanced = text('button', 'Advanced setup', 'btn btn-outline-secondary');
    advanced.type = 'button';
    advanced.addEventListener('click', () => {
      const current = document.getElementById('cast-edit-voice');
      const legacy = state.legacyVoiceButton;
      state.allowLegacyVoiceEditor = true;
      if (current && legacy && current !== legacy) current.replaceWith(legacy);
      closeModal();
      legacy?.click();
    });
    const cancel = text('button', 'Cancel', 'btn btn-outline-secondary');
    cancel.type = 'button';
    cancel.addEventListener('click', closeModal);
    const assign = text('button', 'Assign Voice', 'btn btn-primary');
    assign.type = 'button';
    assign.disabled = true;
    actions.append(advanced, cancel, assign);
    footer.append(status, actions);
    dialog.append(header, body, footer);
    layer.append(dialog);
    layer.addEventListener('mousedown', (event) => {
      if (event.target === layer) closeModal();
    });
    layer.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeModal();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = [...layer.querySelectorAll(
        'button:not(:disabled), audio[controls], select:not(:disabled), [tabindex]:not([tabindex="-1"])',
      )];
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    });
    document.body.append(layer);
    state.modal = layer;
    close.focus();

    let resources = [];
    const updateSelection = () => {
      const resource = resources.find((item) => item.voice_id === select.value);
      assign.disabled = !resource;
      if (!resource) {
        summaryTitle.textContent = 'No Voice selected';
        summaryBody.textContent = 'Choose a saved or built-in Voice from the list.';
        preview.pause();
        preview.removeAttribute('src');
        preview.hidden = true;
        status.textContent = 'Choose a Voice to continue.';
        return;
      }
      summaryTitle.textContent = resource.name;
      summaryBody.textContent = `${resource.method_label}. ${resource.description || ''}`.trim();
      if (resource.preview?.available && resource.preview?.url) {
        preview.src = resource.preview.url;
        preview.hidden = false;
      } else {
        preview.pause();
        preview.removeAttribute('src');
        preview.hidden = true;
      }
      status.textContent = `${resource.name} selected. Nothing has been saved yet.`;
    };
    select.addEventListener('change', updateSelection);
    assign.addEventListener('click', async () => {
      const resource = resources.find((item) => item.voice_id === select.value);
      if (!resource) return;
      assign.disabled = true;
      cancel.disabled = true;
      advanced.disabled = true;
      select.disabled = true;
      status.textContent = `Assigning ${resource.name}…`;
      try {
        await apiJson('/api/voice-library/assign', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            character_id: character.characterId,
            voice_id: resource.voice_id,
          }),
        });
        status.textContent = `${resource.name} assigned. Reloading Cast…`;
        window.setTimeout(() => window.location.reload(), 120);
      } catch (error) {
        status.textContent = error.message || 'The Voice could not be assigned.';
        cancel.disabled = false;
        advanced.disabled = false;
        select.disabled = false;
        updateSelection();
      }
    });

    try {
      const library = await apiJson(
        `/api/voice-library?return_route=${encodeURIComponent(location.hash || '#/cast')}`,
      );
      resources = (library?.voices || [])
        .filter((item) => item.assignment?.supported === true)
        .sort((left, right) => {
          const leftReusable = left.technical_details?.scope === 'reusable' ? 0 : 1;
          const rightReusable = right.technical_details?.scope === 'reusable' ? 0 : 1;
          return leftReusable - rightReusable
            || String(left.name).localeCompare(String(right.name));
        });
      select.replaceChildren(new Option('Choose a Voice…', ''));
      const groups = new Map();
      for (const resource of resources) {
        const groupLabel = resource.technical_details?.scope === 'reusable'
          ? 'Saved Voices' : 'Built-in Voices';
        if (!groups.has(groupLabel)) {
          const group = document.createElement('optgroup');
          group.label = groupLabel;
          groups.set(groupLabel, group);
          select.append(group);
        }
        groups.get(groupLabel).append(
          new Option(`${resource.name} — ${resource.method_label}`, resource.voice_id),
        );
      }
      select.disabled = resources.length === 0;
      status.textContent = resources.length
        ? `${resources.length} assignable Voices available.`
        : 'No production-ready Voices are currently available.';
      if (resources.length) select.focus();
    } catch (error) {
      select.replaceChildren(new Option('Voice catalog unavailable', ''));
      status.textContent = error.message || 'The Voice catalog could not be loaded.';
    }
  }

  document.addEventListener('click', (event) => {
    const characterRow = event.target.closest?.(
      '.cast-character-row[data-cast-character-id]',
    );
    if (characterRow) {
      state.stableSelectedCharacterId = characterRow.dataset.castCharacterId || null;
    }
    const voiceAction = event.target.closest?.('#cast-edit-voice');
    if (voiceAction && document.body?.dataset.destination === 'cast') {
      if (state.allowLegacyVoiceEditor) {
        state.allowLegacyVoiceEditor = false;
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      openStableVoiceChooser(voiceAction);
      return;
    }
    const destination = event.target.closest?.('#shell-primary-action[data-stable-destination]');
    if (destination) {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      const query = location.hash.includes('?')
        ? location.hash.slice(location.hash.indexOf('?'))
        : '';
      location.hash = `#/${destination.dataset.stableDestination}${query}`;
      return;
    }
    const action = event.target.closest?.('#shell-primary-action');
    if (!action || document.body?.dataset.destination !== 'script') return;
    if (action.dataset.stableManagedImport === 'true') {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      openManagedImportReview(action);
      return;
    }
    if (action.dataset.stableReviewedOverride === 'true') {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      openSourceDifferenceReview(action);
      return;
    }
    if (/^Approve Script$/i.test(action.textContent.trim())) {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      submitScriptAcceptance();
    }
  }, true);

  window.addEventListener('hashchange', () => scheduleRefresh(40));
  window.addEventListener('resize', syncRailAccessibility);
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !document.body.classList.contains('rail-open')) return;
    document.body.classList.remove('rail-open');
    syncRailAccessibility();
    document.getElementById('rail-mobile-toggle')?.focus();
  });
  const railObserver = new MutationObserver(syncRailAccessibility);
  railObserver.observe(document.body, { attributes: true, attributeFilter: ['class'] });
  const observer = new MutationObserver(() => {
    if (document.body?.dataset.destination === 'script') {
      installStableTaskBundleWorkflow();
      scheduleRefresh(180);
    } else if (document.body?.dataset.destination === 'cast') {
      installStableFullCastButton();
      installStableVoiceButton();
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.setInterval(() => {
    refreshCastStageAction();
    if (document.body?.dataset.destination === 'cast') {
      installStableFullCastButton();
      installStableVoiceButton();
    }
  }, 800);
  installStyles();
  syncRailAccessibility();
  installStableTaskBundleWorkflow();
  installStableFullCastButton();
  installStableVoiceButton();
  scheduleRefresh(0);
})();
