'use strict';

import {
  createScriptFilterBar, createScriptPage, renderScriptReviewStatus,
  renderScriptSourceContext, scriptStageStates,
} from './script_components.js';
import { approvalState, normalizeIssues } from './script_review_model.js';
import { createScriptWorkflows } from './script_workflows.js';
import { createScriptReviewController } from './script_review_controller.js';

const UI = globalThis.AlexandriaUI;
const STATES = Object.freeze(['loading', 'empty', 'error', 'success', 'dense']);
const dataScriptContinue = 'data-script-continue';
const dataScriptApprove = 'data-script-approve';

export async function mount({ root, route, shell, api, signal }) {
  const projectId = route.projectId || route.context.project || '';
  const owner = createScriptPage(route);
  owner.dataset.page = route.path;
  const sourceContext = document.createElement('section');
  sourceContext.className = 'script-source-context';
  sourceContext.dataset.scriptSourceContext = '';
  sourceContext.setAttribute('aria-label', 'Script source context');
  sourceContext.append(UI.skeleton({ label: 'Loading source context' }));
  const search = UI.searchField({ label: 'Search Script', placeholder: 'Search Script…' });
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
  content.append(UI.skeleton({ label: 'Loading Script' }), UI.skeleton(), UI.skeleton());
  const workflowNotice = document.createElement('div');
  workflowNotice.className = 'script-workflow-notice';
  workflowNotice.setAttribute('aria-live', 'polite');
  owner.append(sourceContext, toolbar, lifecycleRegion, content, workflowNotice);
  root.replaceChildren(owner);
  shell.player.set({ state: 'inactive', title: 'No Script audio selected', subtitle: 'Select a line preview to enable transport' });

  let disposed = false;
  let model = { flow: null, lifecycle: null, entries: [], auditIssues: [] };
  const currentIssues = () => normalizeIssues({
    lifecycle: model.lifecycle, auditIssues: model.auditIssues, entries: model.entries,
  });
  const reportWorkflow = (title, body, tone = 'error') => {
    workflowNotice.replaceChildren(UI.notice({ tone, title, body, live: true }));
  };
  const workflows = createScriptWorkflows({
    api, signal, getModel: () => model, onReload: async () => load(), report: reportWorkflow,
  });
  owner.append(workflows.root);
  reviewController = createScriptReviewController({
    content, toolbar, search, shell, workflows,
    getModel: () => model,
    getFilter: () => issueFilter,
    setFilter: (value) => { issueFilter = value; },
  });

  const goToCast = () => {
    shell.navigate(shell.routes.routeForPath('cast', projectId ? { project: projectId } : {}).hash);
  };

  const renderSourceContext = () => renderScriptSourceContext({
    root: sourceContext, flow: model.flow, lifecycle: model.lifecycle,
    entries: model.entries, projectTitle: route.projectTitle,
    issueCount: currentIssues().length,
    onChangeChapter: () => reportWorkflow(
      'Chapter navigation is not available yet',
      'This source does not currently expose chapter boundaries. The full Script remains selected.',
      'information',
    ),
  });

  const renderHeader = () => {
    const lifecycle = model.lifecycle || {};
    const accepted = lifecycle.accepted || lifecycle.state === 'accepted';
    const issues = currentIssues();
    const approval = approvalState(lifecycle, issues);
    const primaryAction = accepted ? {
      label: 'Continue to Cast',
      attributes: { [dataScriptContinue]: '' },
      onClick: goToCast,
    } : {
      label: 'Approve Script',
      disabled: !approval.canApprove,
      description: approval.reason,
      attributes: { [dataScriptApprove]: '' },
      onClick: approve,
    };
    shell.header.set({
      projectTitle: model.flow?.project?.name || route.projectTitle || projectId || 'Current project',
      status: {
        tone: accepted ? 'success' : issues.some((issue) => issue.blocking) ? 'warning' : 'information',
        label: accepted ? 'Approved' : issues.length ? 'Review required' : 'Ready for approval',
      },
      stages: scriptStageStates(model.flow),
      primaryAction,
    });
  };

  const renderStatus = () => {
    const issues = currentIssues();
    renderScriptReviewStatus(lifecycleRegion, model.lifecycle || {}, issues);
    const subtitle = owner.querySelector('[data-script-page-subtitle]');
    if (subtitle) subtitle.textContent = model.lifecycle?.accepted
      ? 'Approved — the current Script is ready for Cast.'
      : issues.length
        ? `Generation complete — ${issues.length} issue${issues.length === 1 ? '' : 's'} require review before approval.`
        : 'Review complete — ready for approval.';
  };

  async function approve() {
    const lifecycle = model.lifecycle || {};
    const approval = approvalState(lifecycle, currentIssues());
    if (!approval.canApprove) return;
    lifecycleRegion.replaceChildren(UI.skeleton({ label: 'Approving Script' }));
    const fingerprints = lifecycle.fingerprints || {};
    const result = await api.post('/api/script_lifecycle/accept', {
      expected_script_fingerprint: fingerprints.script,
      expected_metadata_fingerprint: fingerprints.metadata,
      expected_source_fingerprint: fingerprints.source,
      expected_state_fingerprint: lifecycle.state_fingerprint,
    }, { signal });
    if (disposed || signal.aborted) return;
    if (!result.ok) {
      const detail = result.data?.detail && typeof result.data.detail === 'object'
        ? result.data.detail : {};
      const blocking = detail.context?.blocking_issues;
      if (detail.code === 'script_acceptance_blocked' && Array.isArray(blocking)) {
        model.auditIssues = blocking;
        const issues = currentIssues();
        reportWorkflow(
          'Approval found blocking issues',
          `${issues.length} issue${issues.length === 1 ? '' : 's'} must be reviewed before approval.`,
          'warning',
        );
        renderSourceContext(); renderHeader(); renderStatus();
        reviewController.selectFirstIssue();
        return;
      }
      lifecycleRegion.replaceChildren(UI.notice({
        tone: 'error', title: 'Script could not be approved',
        body: detail.message || result.error, live: true,
      }));
      return;
    }
    model.lifecycle = { ...lifecycle, ...result.data, accepted: true, state: 'accepted', blockers: [] };
    model.auditIssues = [];
    renderSourceContext(); renderHeader(); renderStatus(); reviewController.render();
  }

  const load = async () => {
    const [flow, lifecycle, entries] = await Promise.all([
      api.get('/api/project_flow/status', { signal }),
      api.get('/api/script_lifecycle/status', { signal }),
      api.get('/api/annotated_script', { signal }),
    ]);
    if (disposed || signal.aborted) return;
    const failed = [flow, lifecycle, entries].find((result) => !result.ok);
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
    const nextEntries = Array.isArray(entries.data) ? entries.data : [];
    const currentFingerprint = lifecycle.data?.fingerprints?.script;
    const auditFingerprint = model.lifecycle?.fingerprints?.script;
    model = {
      flow: flow.data, lifecycle: lifecycle.data, entries: nextEntries,
      auditIssues: currentFingerprint === auditFingerprint ? model.auditIssues : [],
    };
    renderSourceContext();
    renderHeader();
    renderStatus();
    reviewController.render();
  };

  const resetEntries = () => reviewController.render({ reset: true });
  search.querySelector('input').addEventListener('input', resetEntries);
  await load();
  return () => {
    if (disposed) return;
    disposed = true;
    search.querySelector('input').removeEventListener('input', resetEntries);
    reviewController.cleanup();
  };
}
