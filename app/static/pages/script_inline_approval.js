'use strict';

import { openReviewedDifferenceDialog } from './script_acceptance.js';

const UI = globalThis.AlexandriaUI;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

export function createScriptInlineApproval({
  getApprovalState, approveScript,
}) {
  const status = text(
    'div',
    'transaction-status script-inline-approval__status',
    'Approval state not loaded.',
  );
  status.setAttribute('aria-live', 'polite');
  const actions = document.createElement('div');
  actions.className = 'script-workflow-actions script-inline-approval__actions';
  const button = UI.button({
    label: 'Approve Script',
    variant: 'primary',
    attributes: { 'data-script-modal-approve': '' },
  });
  actions.append(button);

  const refresh = async () => {
    const state = getApprovalState();
    button.removeAttribute('aria-busy');
    button.dataset.state = 'default';
    if (state.pending) {
      button.disabled = true;
      button.textContent = 'Approving…';
      button.dataset.state = 'loading';
      button.setAttribute('aria-busy', 'true');
      status.textContent = 'Checking source fidelity and saving the accepted Script version.';
      return;
    }
    if (state.accepted) {
      button.disabled = true;
      button.textContent = 'Script approved';
      status.textContent = 'Approved. Qwen and Fish delivery planning is ready.';
      return;
    }
    button.textContent = state.reviewedOverrideReady
      ? 'Approve reviewed version'
      : 'Approve Script';
    button.disabled = !state.canApprove;
    status.textContent = state.canApprove
      ? 'Ready to approve. Contextual review is optional.'
      : state.reason || 'Resolve the current Script blockers before approval.';
  };

  const complete = async ({ reviewedOverride = false } = {}) => {
    await approveScript({ reviewedOverride });
  };

  const handleClick = (event) => {
    const state = getApprovalState();
    if (state.pending || state.accepted || !state.canApprove) return;
    if (state.reviewedOverrideReady) {
      openReviewedDifferenceDialog({
        event,
        onConfirm: () => { void complete({ reviewedOverride: true }); },
      });
      return;
    }
    void complete();
  };

  button.addEventListener('click', handleClick);
  return Object.freeze({
    status,
    actions,
    refresh,
    cleanup() {
      button.removeEventListener('click', handleClick);
    },
  });
}
