'use strict';

import { exportDisplayFilename } from './export_model.js';

const UI = globalThis.AlexandriaUI;

function elapsedLabel(startedAt) {
  const started = Date.parse(String(startedAt || ''));
  if (!Number.isFinite(started)) return '';
  const seconds = Math.max(0, Math.round((Date.now() - started) / 1000));
  if (seconds < 60) return `${seconds}s elapsed`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes}m ${String(remainder).padStart(2, '0')}s elapsed`;
}

function buildProgressMessage(process) {
  const message = String(process.progress_message || '').trim();
  const total = Number(process.total_count) || 0;
  const completed = Number(process.completed_count) || 0;
  const count = total > 1 ? `${completed.toLocaleString()} of ${total.toLocaleString()}` : '';
  const elapsed = elapsedLabel(process.started_at);
  return [message, count, elapsed].filter(Boolean).join(' · ')
    || 'Preparing the selected output.';
}

export function createExportPage(root, route) {
  const owner = document.createElement('article');
  owner.className = 'export-page';
  owner.dataset.routeOwner = route.path;
  owner.dataset.exportPage = '';
  owner.dataset.pageState = 'loading';
  const title = UI.pageTitleBlock({
    title: 'Export',
    subtitle: 'Validate the publication, then build the finished audiobook.',
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
  readiness.hidden = true;
  readiness.replaceChildren();
  workspace.replaceChildren(
    UI.loadingState({ label: 'Loading Export', detail: 'Checking publication metadata, chapters, and production readiness.' }),
    UI.skeleton({ kind: 'panel', label: 'Loading publication details' }),
    UI.skeleton({ kind: 'panel', label: 'Loading chapters' }),
    UI.skeleton({ kind: 'panel', label: 'Loading output validation' }),
  );
  shell.inspector.set({ state: 'hidden', title: 'Export details', content: null });
}

export function renderExportError({
  owner, readiness, workspace, shell, projectTitle, onRetry, onTracker, message,
}) {
  owner.dataset.pageState = 'error';
  readiness.hidden = true;
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
  root, aggregate, actionMessage, selectedOutput, hardBlockers, onCancel,
  metadataReady, successAction, validationNode, canBuild, onBuild,
  blockerAction, onFocusMetadata,
}) {
  root.replaceChildren();
  root.hidden = false;

  const running = Boolean(aggregate.process?.running);
  root.dataset.exportState = running ? 'running' : aggregate.summary?.complete ? 'complete' : 'ready';

  if (actionMessage && !running) {
    root.append(UI.notice({
      tone: actionMessage.tone,
      title: actionMessage.title,
      body: actionMessage.body,
      live: true,
    }));
  }

  const finish = document.createElement('div');
  finish.className = 'export-finish-line';
  const copy = document.createElement('div');
  copy.className = 'export-finish-line__copy';
  const eyebrow = document.createElement('span');
  eyebrow.className = 'utility-heading';
  eyebrow.textContent = 'Finish line';
  const heading = document.createElement('h2');
  heading.className = 'section-title';
  const body = document.createElement('p');
  body.className = 'metadata';
  const actions = document.createElement('div');
  actions.className = 'export-finish-line__actions';
  copy.append(eyebrow, heading, body);
  finish.append(copy, actions);

  if (running) {
    const process = aggregate.process;
    const format = String(process.formats?.[0] || 'audiobook').toUpperCase();
    const percentValue = Number(process.overall_percent);
    const hasPercent = Number.isFinite(percentValue);
    const percent = hasPercent ? Math.max(0, Math.min(100, Math.round(percentValue))) : 0;
    finish.dataset.tone = 'information';
    finish.classList.add('export-finish-line--running');
    heading.textContent = `Building ${format} audiobook`;
    body.textContent = String(process.progress_message
      || 'Alexandria is assembling and validating the selected deliverable.');
    const progress = UI.progress({
      label: process.phase_label || 'Building audiobook',
      state: hasPercent ? 'running' : 'indeterminate',
      value: percent,
      message: buildProgressMessage(process),
      showMessage: true,
    });
    progress.classList.add('export-progress');
    const cancel = UI.button({
      label: aggregate.process.cancel_requested ? 'Cancelling…' : 'Cancel build',
      variant: 'secondary',
      disabled: Boolean(aggregate.process.cancel_requested),
      attributes: { 'data-export-cancel': '' },
      onClick: onCancel,
    });
    actions.append(cancel);
    finish.append(progress);
  } else if (aggregate.summary?.complete) {
    finish.dataset.tone = 'success';
    heading.textContent = 'Audiobook ready';
    body.textContent = selectedOutput?.filename
      ? `${exportDisplayFilename(selectedOutput.filename)} is the verified current build.`
      : 'The selected output was built and verified.';
    if (successAction) actions.append(successAction);
    if (canBuild) {
      actions.append(UI.button({
        label: 'Build again',
        variant: 'quiet',
        attributes: { 'data-export-build-again': '' },
        onClick: async (event) => {
          event.currentTarget.disabled = true;
          await onBuild();
        },
      }));
    }
  } else {
    const hard = hardBlockers();
    const missingMetadata = !metadataReady;
    const issueCount = hard.length + (missingMetadata ? 1 : 0);
    if (issueCount) {
      finish.dataset.tone = 'warning';
      heading.textContent = `${issueCount} ${issueCount === 1 ? 'requirement' : 'requirements'} before build`;
      body.textContent = 'Resolve the remaining items before building. Existing work is preserved.';
      if (missingMetadata) {
        actions.append(UI.button({
          label: 'Enter metadata',
          variant: 'secondary',
          size: 'compact',
          onClick: onFocusMetadata,
        }));
      }
      const destinations = new Set();
      hard.forEach((blocker) => {
        if (!blocker.native_destination || destinations.has(blocker.native_destination)) return;
        destinations.add(blocker.native_destination);
        const action = blockerAction(blocker);
        if (action) actions.append(action);
      });
    } else {
      finish.dataset.tone = 'success';
      heading.textContent = 'Ready to build';
      body.textContent = 'Publication metadata, chapter structure, production audio, and the selected format are ready.';
      actions.append(UI.button({
        label: 'Build audiobook',
        variant: 'primary',
        attributes: { 'data-export-primary': '' },
        onClick: async (event) => {
          const button = event.currentTarget;
          button.disabled = true;
          button.textContent = 'Checking preflight…';
          await onBuild();
        },
      }));
    }
  }

  root.append(finish);
  if (validationNode && !running) root.append(validationNode);
}
