'use strict';

import { createScriptContextualReview } from './script_contextual_review.js';
import { createScriptDeliveryPlan } from './script_delivery_plan.js';
import { createScriptImportWorkflows } from './script_import_workflows.js';
import { createScriptInlineApproval } from './script_inline_approval.js';
import { createScriptPronunciationGuidance } from './script_pronunciation_guidance.js';
import { createScriptWorkflowDialog } from './script_workflow_dialog.js';
import { createScriptWorkflowProvenance } from './script_workflow_provenance.js';
import { createScriptWorkflowState } from './script_workflow_state.js';
import { downloadTaskBundle } from './task_bundle_download.js';

const UI = globalThis.AlexandriaUI;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

async function runButton(button, label, operation) {
  const prior = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try { return await operation(); }
  finally { button.disabled = false; button.textContent = prior; }
}

export function createScriptWorkflows({
  api, signal, shell, projectId, getModel, getApprovalState,
  approveScript, onReload, report,
}) {
  const root = document.createElement('section');
  root.className = 'script-workflows';
  const generationState = text('div', 'transaction-status', 'Generation status not loaded.');
  const generationActions = document.createElement('div');
  generationActions.className = 'script-workflow-actions';
  const generate = UI.button({ label: 'Generate locally', variant: 'secondary' });
  const exportTask = UI.button({
    label: 'Download Script task bundle',
    variant: 'secondary',
    attributes: { 'data-script-task-export': '' },
  });
  generationActions.append(generate, exportTask);
  const taskScope = UI.notice({
    tone: 'information',
    title: 'This bundle creates the Script only',
    body: 'It does not discover relationships, aliases, roles, groups, non-speaking figures, visual dossiers, or production Voices. Use Full Cast tasks in Cast for whole-book identity enrichment.',
  });
  const imports = createScriptImportWorkflows({
    api, signal, shell, projectId, onReload, report,
  });
  const deliveryPlan = createScriptDeliveryPlan({ api, signal, report });
  const pronunciationGuidance = createScriptPronunciationGuidance({
    api, signal, report,
  });
  const inlineApproval = createScriptInlineApproval({
    getApprovalState, approveScript,
  });
  const contextualReview = createScriptContextualReview({
    api, signal, onReload, report,
  });

  const creationStep = document.createElement('section');
  creationStep.className = 'script-workflow-step';
  creationStep.dataset.scriptWorkflowStep = 'create';
  creationStep.append(
    text('h2', 'section-title', 'Create or replace the Script'),
    text(
      'p',
      'metadata',
      'Generate locally, or export a Script task bundle for ChatGPT and import the completed ZIP directly in this step.',
    ),
    generationState,
    generationActions,
    taskScope,
    imports.completedScriptTaskSection,
  );

  const approvalStep = document.createElement('section');
  approvalStep.className = 'script-workflow-step script-approval-step';
  approvalStep.dataset.scriptWorkflowStep = 'approve';
  const optionalReviewContent = document.createElement('div');
  optionalReviewContent.className = 'script-optional-review';
  optionalReviewContent.append(
    contextualReview.root,
    imports.completedReviewTaskSection,
  );
  const optionalReview = UI.disclosure({
    label: 'Optional contextual review',
    content: optionalReviewContent,
  });
  optionalReview.classList.add('script-optional-review-disclosure');
  approvalStep.append(
    text('h2', 'section-title', 'Approve the Script'),
    text(
      'p',
      'metadata',
      'Inspect the Script entries and resolve any blocking issues. Approval here and in the main header is one shared transaction; when it completes, this dialog advances directly to Qwen and Fish planning. Automated contextual review is optional.',
    ),
    inlineApproval.status,
    inlineApproval.actions,
    optionalReview,
  );

  const directImportDisclosure = UI.disclosure({
    label: 'Import an existing Alexandria Script file',
    content: imports.importSection,
  });
  directImportDisclosure.classList.add('script-direct-import');
  const workflowState = createScriptWorkflowState({
    creationStep,
    approvalStep,
    deliveryPlan,
    pronunciationGuidance,
    completedDeliveryTaskSection: imports.completedDeliveryTaskSection,
    directImportDisclosure,
  });

  const refreshApprovalState = async ({ focusDelivery = false } = {}) => {
    const approval = getApprovalState();
    const stage = workflowState.render(approval);
    await inlineApproval.refresh();
    if (stage === 'delivery') {
      await Promise.all([
        deliveryPlan.refresh(),
        pronunciationGuidance.refresh(),
      ]);
    }
    if (focusDelivery && stage === 'delivery') deliveryPlan.focus();
    return approval;
  };

  const generationDialog = createScriptWorkflowDialog({
    content: workflowState.root,
    signal,
    onOpen: async () => {
      const approval = await refreshApprovalState();
      await Promise.all([
        refreshGeneration(),
        approval.hasScript && !approval.accepted
          ? contextualReview.refresh() : Promise.resolve(),
      ]);
    },
  });
  const provenance = createScriptWorkflowProvenance({
    api, signal, getModel,
  });
  root.append(generationDialog.launcher, provenance.root);

  const refreshGeneration = async () => {
    const result = await api.get('/api/script_generation/status', { signal });
    if (!result.ok) {
      generationState.textContent = result.error;
      return;
    }
    const status = result.data || {};
    generationState.textContent = status.process?.running
      ? 'Script generation is running.'
      : status.progress?.status === 'resumable'
        ? 'Saved generation progress can resume.'
        : 'No Script generation is currently running.';
  };

  generate.addEventListener('click', async () => {
    const result = await runButton(
      generate,
      'Starting…',
      () => api.post('/api/generate_script', {}, { signal }),
    );
    if (!result.ok) report('Local generation could not start', result.error);
    else {
      report(
        'Script generation started',
        'Alexandria is preparing the Script locally.',
        'information',
      );
      await refreshGeneration();
    }
  });
  exportTask.addEventListener('click', () => {
    void downloadTaskBundle({
      api,
      signal,
      button: exportTask,
      taskType: 'script_generation',
      onError: (error) => report('Script task could not be downloaded', error),
      onDownloaded: () => report(
        'Script task bundle downloaded',
        'Attach the ZIP to ChatGPT, then import the completed ZIP below without unzipping it.',
        'success',
      ),
    });
  });

  return Object.freeze({
    root,
    refreshApprovalState,
    open(kind = 'generation') {
      if (kind !== 'provenance') {
        void generationDialog.open();
        return;
      }
      provenance.open();
    },
    cleanup() {
      inlineApproval.cleanup();
      contextualReview.cleanup();
      generationDialog.cleanup();
    },
  });
}
