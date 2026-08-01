'use strict';

import {
  createScriptFilterBar, createScriptPage, renderScriptReviewStatus,
  renderScriptSourceContext, scriptStageStates,
} from './script_components.js';
import { normalizeIssues } from './script_review_model.js';
import { createScriptApprovalController } from './script_approval_controller.js';
import { createPageInspector } from '../components/page_inspector.js';
import { createScriptWorkflows } from './script_workflows.js';
import { createScriptReviewController } from './script_review_controller.js';
import {
  applyImportCandidate, entriesForImportCandidate, renderImportCandidateStatus,
} from './script_import_candidate.js';
import { scriptHeaderState } from './script_header.js';
import { scriptLoadingEntries } from './script_loading.js';

const UI = globalThis.AlexandriaUI;
const STATES = Object.freeze(['loading', 'empty', 'error', 'success', 'dense']);
const dataScriptContinue = 'data-script-continue';
const dataScriptApprove = 'data-script-approve';
const dataScriptApplyImport = 'data-script-apply-import';
const scriptAcceptEndpoint = '/api/script_lifecycle/accept';
const scriptApproveLabel = 'Approve Script';
const scriptReviewRequiredLabel = 'Review required';

export async function mount({ root, route, shell, api, signal }) {
  const projectId = route.projectId || route.context.project || '';
  const owner = createScriptPage(route);
  owner.dataset.page = route.path;
  const sourceContext = document.createElement('section');
  sourceContext.className = 'script-source-context';
  sourceContext.dataset.scriptSourceContext = '';
  sourceContext.setAttribute('aria-label', 'Script source context');
  sourceContext.append(UI.skeleton({ kind: 'panel', label: 'Loading source context' }));
  const search = UI.searchField({
    label: 'Search Script', placeholder: 'Search Script…',
    iconClass: 'fas fa-magnifying-glass',
  });
  search.classList.add('script-review-search');
  search.querySelector('.field__label')?.classList.add('visually-hidden');
  let issueFilter = ['all', 'uncertain_speaker', 'delivery_direction', 'source_mismatch']
    .includes(route.context.filter) ? route.context.filter : 'all';
  let reviewController = null;
  const toolbar = createScriptFilterBar({
    search, onFilter: (value) => reviewController?.setFilter(value),
  });
  const lifecycleRegion = document.createElement('section');
  lifecycleRegion.className = 'script-review__status';
  const content = document.createElement('section');
  content.className = 'content-state';
  content.dataset.state = STATES[0];
  content.append(scriptLoadingEntries());
  const workflowNotice = document.createElement('div');
  workflowNotice.className = 'script-workflow-notice';
  workflowNotice.setAttribute('aria-live', 'polite');
  const reviewLayout = document.createElement('div');
  reviewLayout.className = 'script-review-layout';
  const reviewMain = document.createElement('div');
  reviewMain.className = 'script-review-main';
  reviewMain.append(toolbar, content, lifecycleRegion, workflowNotice);
  const pageInspector = createPageInspector({
    className: 'script-review-inspector',
    label: 'Selected Script entry',
    emptyContent: UI.emptyState({
      title: 'Select an entry',
      body: 'The full Script text and delivery context will appear here.',
    }),
  });
  reviewLayout.append(reviewMain, pageInspector.node);
  owner.append(sourceContext, reviewLayout);
  root.replaceChildren(owner);
  shell.inspector.hide();
  shell.player.set({ state: 'inactive', title: 'No Script audio selected', subtitle: 'Select a line preview to enable transport' });

  let disposed = false;
  let model = {
    flow: null, lifecycle: null, entries: [], auditIssues: [], importCandidate: null,
    reviewOverride: null,
  };
  let workflows = null;
  const currentIssues = () => normalizeIssues({
    lifecycle: model.lifecycle, auditIssues: model.auditIssues, entries: model.entries,
  });
  const reportWorkflow = (title, body, tone = 'error') => {
    workflowNotice.replaceChildren(UI.notice({ tone, title, body, live: true }));
  };
  const approvalController = createScriptApprovalController({
    api,
    signal,
    acceptEndpoint: scriptAcceptEndpoint,
    lifecycleRegion,
    getModel: () => model,
    currentIssues,
    isDisposed: () => disposed,
    report: reportWorkflow,
    renderSourceContext,
    renderHeader,
    renderStatus,
    renderReview: () => reviewController.render(),
    selectFirstIssue: () => reviewController.selectFirstIssue(),
    refreshWorkflow: (options) => workflows?.refreshApprovalState(options),
  });
  workflows = createScriptWorkflows({
    api, signal, shell, projectId,
    getModel: () => model,
    getApprovalState: approvalController.getState,
    approveScript: approvalController.approve,
    onReload: async () => load(),
    report: reportWorkflow,
  });
  reviewMain.append(workflows.root);
  reviewController = createScriptReviewController({
    content, toolbar, search, inspector: pageInspector, workflows,
    getModel: () => model,
    getFilter: () => issueFilter,
    setFilter: (value) => { issueFilter = value; },
  });

  function goToCast() {
    shell.navigate(shell.routes.routeForPath('cast', projectId ? { project: projectId } : {}).hash);
  }

  function renderSourceContext() {
    renderScriptSourceContext({
      root: sourceContext, flow: model.flow, lifecycle: model.lifecycle,
      entries: model.entries, projectTitle: route.projectTitle,
      issueCount: currentIssues().length,
    });
  }

  function renderHeader() {
    const state = scriptHeaderState({
      model, issues: currentIssues(), goToCast, applyImportedScript,
      confirmReviewedDifferences: approvalController.confirmReviewedDifferences,
      approve: approvalController.approve,
      continueAttribute: dataScriptContinue,
      approveAttribute: dataScriptApprove,
      applyImportAttribute: dataScriptApplyImport,
      approveLabel: scriptApproveLabel,
      reviewRequiredLabel: scriptReviewRequiredLabel,
      approvalPending: approvalController.isPending(),
    });
    shell.header.set({
      projectTitle: route.projectTitle || model.flow?.project?.name || projectId || 'Current project',
      status: state.status,
      stages: scriptStageStates(model.flow),
      primaryAction: state.primaryAction,
    });
  }

  function renderStatus() {
    const issues = currentIssues();
    const importSubtitle = renderImportCandidateStatus({ root: lifecycleRegion, model });
    if (importSubtitle == null) {
      renderScriptReviewStatus(lifecycleRegion, model.lifecycle || {}, issues);
    }
    const subtitle = owner.querySelector('[data-script-page-subtitle]');
    if (!subtitle) return;
    subtitle.textContent = importSubtitle || (issues.length
      ? `${issues.length} issue${issues.length === 1 ? '' : 's'} require review before approval.`
      : 'Review speaker attribution, delivery, and source fidelity.');
  }

  async function applyImportedScript() {
    await applyImportCandidate({
      model,
      api,
      signal,
      root: lifecycleRegion,
      report: reportWorkflow,
      reload: load,
      isDisposed: () => disposed,
    });
  }

  const load = async () => {
    const [flow, lifecycle, entries, importCandidate] = await Promise.all([
      api.get('/api/project_flow/status', { signal }),
      api.get('/api/script_lifecycle/status', { signal }),
      api.get('/api/annotated_script', { signal }),
      api.get('/api/script_lifecycle/import-candidate', { signal }),
    ]);
    if (disposed || signal.aborted) return;
    const failed = [flow, lifecycle, entries, importCandidate]
      .find((result) => !result.ok);
    if (failed) {
      sourceContext.replaceChildren(UI.notice({
        tone: 'error', title: 'Source context could not load', body: failed.error, live: true,
      }));
      content.dataset.state = STATES[2];
      content.replaceChildren(UI.notice({
        tone: 'error', title: 'Script could not load', body: failed.error, live: true,
        action: UI.button({ label: 'Retry', onClick: load }),
      }));
      return;
    }
    const authoritativeEntries = Array.isArray(entries.data) ? entries.data : [];
    const nextEntries = entriesForImportCandidate({
      authoritativeEntries,
      candidate: importCandidate.data,
      lifecycle: lifecycle.data,
    });
    const currentFingerprint = lifecycle.data?.fingerprints?.script;
    const auditFingerprint = model.lifecycle?.fingerprints?.script;
    const keepReviewOverride = currentFingerprint === auditFingerprint
      && model.reviewOverride?.scriptFingerprint === currentFingerprint
      && model.reviewOverride?.sourceFingerprint === lifecycle.data?.fingerprints?.source;
    model = {
      flow: flow.data,
      lifecycle: lifecycle.data,
      entries: nextEntries,
      importCandidate: importCandidate.data?.status === 'none'
        ? null : importCandidate.data,
      auditIssues: currentFingerprint === auditFingerprint ? model.auditIssues : [],
      reviewOverride: keepReviewOverride ? model.reviewOverride : null,
    };
    renderSourceContext();
    renderHeader();
    renderStatus();
    reviewController.render();
    void workflows.refreshApprovalState();
  };

  const resetEntries = () => reviewController.render({ reset: true });
  search.querySelector('input').addEventListener('input', resetEntries);
  await load();
  return () => {
    if (disposed) return;
    disposed = true;
    search.querySelector('input').removeEventListener('input', resetEntries);
    workflows.cleanup(); reviewController.cleanup();
    pageInspector.cleanup();
  };
}
