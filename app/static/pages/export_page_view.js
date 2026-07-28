'use strict';

import { exportDisplayFilename } from './export_model.js';

const UI = globalThis.AlexandriaUI;

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

  if (actionMessage) {
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

  if (aggregate.process?.running) {
    finish.dataset.tone = 'information';
    heading.textContent = 'Building the audiobook';
    body.textContent = 'Alexandria is assembling and validating the selected deliverable.';
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
        label: 'Build Audiobook',
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
  if (validationNode) root.append(validationNode);
}
