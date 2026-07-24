'use strict';

import { exportText } from './export_model.js';

const UI = globalThis.AlexandriaUI;

export function createExportPage(root, route) {
  const owner = document.createElement('article');
  owner.className = 'export-page';
  owner.dataset.routeOwner = route.path;
  owner.dataset.exportPage = '';
  owner.dataset.pageState = 'loading';
  const title = UI.pageTitleBlock({
    title: 'Export',
    subtitle: 'Review publication details before building the final output.',
  });
  title.querySelector('h1').dataset.pageHeading = '';
  const readiness = document.createElement('section');
  readiness.className = 'export-readiness';
  readiness.setAttribute('aria-label', 'Export preflight');
  const workspace = document.createElement('div');
  workspace.className = 'export-grid';
  owner.append(title, readiness, workspace);
  root.replaceChildren(owner);
  return { owner, readiness, workspace };
}

export function renderExportLoading({ owner, readiness, workspace, shell }) {
  owner.dataset.pageState = 'loading';
  readiness.replaceChildren(UI.skeleton({ label: 'Checking final output' }));
  workspace.replaceChildren(
    UI.skeleton({ label: 'Loading publication details' }),
    UI.skeleton({ label: 'Loading chapters' }),
    UI.skeleton({ label: 'Loading output validation' }),
  );
  shell.inspector.set({ state: 'hidden', title: 'Export details', content: null });
}

export function renderExportError({
  owner, readiness, workspace, shell, projectTitle, onRetry, onTracker, message,
}) {
  owner.dataset.pageState = 'error';
  readiness.replaceChildren();
  workspace.replaceChildren(UI.notice({
    tone: 'error',
    title: 'Export unavailable',
    body: message || 'Alexandria could not load Export status.',
    live: true,
    action: UI.button({ label: 'Retry', variant: 'secondary', onClick: onRetry }),
  }));
  shell.header.set({
    projectTitle,
    save: { state: 'saved', label: 'Saved' },
    status: { tone: 'error', label: 'Unavailable' },
    primaryAction: null,
  });
  onTracker();
}

export function renderExportReadiness({
  root, aggregate, actionMessage, selectedOutput, hardBlockers, blockerAction, onCancel,
  metadataReady, successAction,
}) {
  root.replaceChildren();
  if (actionMessage) {
    root.append(UI.notice({
      tone: actionMessage.tone,
      title: actionMessage.title,
      body: actionMessage.body,
      live: true,
    }));
  }
  if (aggregate.process?.running) {
    const total = Number(aggregate.process.total_count) || 0;
    const completed = Number(aggregate.process.completed_count) || 0;
    const progress = UI.progress({
      label: 'Building audiobook…',
      state: total ? 'running' : 'indeterminate',
      value: total ? Math.round((completed / total) * 100) : 0,
      message: total ? `${completed} of ${total} output steps finished.` : 'Preparing the final output.',
    });
    progress.classList.add('export-progress');
    const cancel = UI.button({
      label: aggregate.process.cancel_requested ? 'Cancelling…' : 'Cancel',
      variant: 'secondary',
      size: 'compact',
      disabled: Boolean(aggregate.process.cancel_requested),
      attributes: { 'data-export-cancel': '' },
      onClick: onCancel,
    });
    root.append(progress, cancel);
    return;
  }
  if (aggregate.summary?.complete) {
    root.append(UI.notice({
      tone: 'success',
      title: 'Audiobook built',
      body: selectedOutput?.filename
        ? `${selectedOutput.filename} is the verified current output.`
        : 'The selected output was built and verified.',
      action: successAction,
      live: true,
    }));
    return;
  }
  const hard = hardBlockers();
  const metadataCount = metadataReady ? 0
    : (aggregate.blockers || []).filter((blocker) => blocker.code === 'export_metadata_missing').length;
  if (hard.length || metadataCount) {
    const parts = [];
    if (hard.length) parts.push(`${hard.length} production issue${hard.length === 1 ? '' : 's'}`);
    if (metadataCount) parts.push(`${metadataCount} publication detail${metadataCount === 1 ? '' : 's'}`);
    root.append(UI.notice({
      tone: 'warning',
      title: 'Export is blocked',
      body: `${parts.join(' and ')} need attention before the audiobook can be built.`,
      action: hard[0] ? blockerAction(hard[0]) : null,
    }));
    return;
  }
  root.append(
    UI.status({ tone: 'success', label: 'Final preflight is clear', domain: 'export', value: 'ready' }),
    exportText('span', 'metadata', 'The current publication settings can be reviewed and built.'),
  );
}
