'use strict';

import { openReviewedDifferenceDialog, requestScriptAcceptance } from './script_acceptance.js';
import { scriptApprovalLoading } from './script_loading.js';
import { approvalState } from './script_review_model.js';

const UI = globalThis.AlexandriaUI;

export function createScriptApprovalController({
  api,
  signal,
  acceptEndpoint,
  lifecycleRegion,
  getModel,
  currentIssues,
  isDisposed,
  report,
  renderSourceContext,
  renderHeader,
  renderStatus,
  renderReview,
  selectFirstIssue,
  refreshWorkflow,
}) {
  let pending = false;
  let transaction = null;

  const getState = () => {
    const model = getModel();
    const lifecycle = model.lifecycle || {};
    const approval = approvalState(lifecycle, currentIssues());
    const reviewedOverrideReady = Boolean(model.reviewOverride?.auditFingerprint);
    return {
      ...approval,
      pending,
      hasScript: model.entries.length > 0,
      accepted: Boolean(lifecycle.accepted || lifecycle.state === 'accepted'),
      reviewedOverrideReady,
      canApprove: !pending && (reviewedOverrideReady || approval.canApprove),
    };
  };

  const refreshSharedState = async ({ focusDelivery = false } = {}) => {
    renderHeader();
    await refreshWorkflow({ focusDelivery });
  };

  const performAcceptance = async (reviewedOverride) => {
    const model = getModel();
    const lifecycle = model.lifecycle || {};
    lifecycleRegion.replaceChildren(scriptApprovalLoading());
    const outcome = await requestScriptAcceptance({
      api,
      signal,
      endpoint: acceptEndpoint,
      lifecycle,
      reviewOverride: model.reviewOverride,
      reviewedOverride,
    });
    if (isDisposed() || outcome.kind === 'aborted') return false;
    if (outcome.kind === 'review') {
      model.auditIssues = outcome.auditIssues;
      model.reviewOverride = outcome.reviewOverride;
      const issues = currentIssues();
      report(
        model.reviewOverride ? 'Review the recorded source difference' : 'Approval found blocking issues',
        model.reviewOverride
          ? 'Compare the highlighted source and Script text. After review, use Approve reviewed differences to continue with this exact imported version.'
          : `${issues.length} issue${issues.length === 1 ? '' : 's'} must be reviewed before approval.`,
        'warning',
      );
      renderSourceContext();
      renderStatus();
      selectFirstIssue();
      return false;
    }
    if (outcome.kind === 'stale') {
      model.reviewOverride = null;
      model.auditIssues = [];
      report(
        'Source review changed',
        'The Script or source changed after review. Run approval again to inspect the current difference.',
        'warning',
      );
      renderStatus();
      return false;
    }
    if (outcome.kind === 'error') {
      lifecycleRegion.replaceChildren(UI.notice({
        tone: 'error',
        title: 'Script could not be approved',
        body: outcome.message,
        live: true,
      }));
      return false;
    }
    model.lifecycle = outcome.lifecycle;
    model.auditIssues = [];
    model.reviewOverride = null;
    renderSourceContext();
    renderStatus();
    renderReview();
    return true;
  };

  const approve = ({ reviewedOverride = false } = {}) => {
    if (transaction) return transaction;
    const model = getModel();
    if (!reviewedOverride && currentIssues().some((issue) => issue.blocking)) {
      return Promise.resolve(false);
    }
    if (reviewedOverride && !model.reviewOverride?.auditFingerprint) {
      return Promise.resolve(false);
    }
    pending = true;
    transaction = (async () => {
      let accepted = false;
      try {
        accepted = await performAcceptance(reviewedOverride);
        return accepted;
      } finally {
        pending = false;
        transaction = null;
        if (!isDisposed()) await refreshSharedState({ focusDelivery: accepted });
      }
    })();
    void refreshSharedState();
    return transaction;
  };

  const confirmReviewedDifferences = (event) => {
    if (!getModel().reviewOverride?.auditFingerprint) return;
    openReviewedDifferenceDialog({
      event,
      onConfirm: () => approve({ reviewedOverride: true }),
    });
  };

  return Object.freeze({
    approve,
    confirmReviewedDifferences,
    getState,
    isPending: () => pending,
  });
}
