'use strict';

import { castText } from './cast_model.js';

const UI = globalThis.AlexandriaUI;

export function createCastPage(root) {
  const page = document.createElement('article');
  page.className = 'cast-page';
  page.dataset.routeOwner = 'cast';
  page.dataset.page = 'cast';
  page.dataset.castPage = '';
  const workspace = document.createElement('div');
  workspace.className = 'cast-workspace';
  const master = document.createElement('aside');
  master.className = 'cast-roster';
  master.dataset.castRoster = '';
  master.setAttribute('aria-label', 'Characters');
  const profile = document.createElement('section');
  profile.className = 'cast-profile';
  profile.dataset.castProfile = '';
  profile.dataset.selectedCharacterProfile = '';
  profile.setAttribute('aria-label', 'Selected character profile');
  const heading = document.createElement('h1');
  heading.className = 'cast-roster__title';
  heading.dataset.pageHeading = '';
  heading.textContent = 'Characters';
  master.append(heading);
  workspace.append(master, profile);
  page.append(workspace);
  root.replaceChildren(page);
  return { page, master, profile };
}

export function renderCastLoading({ roster, profile, page }) {
  roster.loading();
  const appearance = document.createElement('div');
  appearance.dataset.appearanceSummary = '';
  appearance.append(UI.skeleton({ kind: 'panel', label: 'Loading appearance summary' }));
  profile.replaceChildren(
    UI.loadingState({ label: 'Loading Cast', detail: 'Reading character identities and Voice assignments.' }),
    UI.skeleton({ kind: 'heading', label: 'Loading selected character' }),
    UI.skeleton({ kind: 'panel', label: 'Loading Voice profile' }),
    appearance,
  );
  page.dataset.castState = 'loading';
}

export function renderCastError({ master, profile, page, onRetry, message }) {
  const retry = UI.button({
    label: 'Retry', variant: 'secondary', attributes: { 'data-cast-retry': '' }, onClick: onRetry,
  });
  const heading = castText('h1', 'cast-roster__title', 'Characters');
  heading.dataset.pageHeading = '';
  master.replaceChildren(heading);
  profile.replaceChildren(UI.notice({
    tone: 'error', title: 'Cast unavailable',
    body: message || 'Alexandria could not load this Cast profile.',
    action: retry, live: true,
  }));
  page.dataset.castState = 'error';
}

export function renderCastEmpty({
  roster, profile, page, onDiscover, onCancel,
  discovering = false, process = {}, progress = {}, state = 'not_started',
}) {
  roster.empty();
  const running = discovering || process.running === true || state === 'running';
  const resumable = !running && progress.status === 'resumable';
  const failed = !running && state === 'failed';
  const completed = Math.max(0, Number(progress.completed_passages) || 0);
  const total = Math.max(0, Number(progress.total_passages) || 0);
  const next = Math.max(1, Number(progress.next_passage) || completed + 1);
  const percentage = total > 0 ? Math.round((completed / total) * 100) : 0;
  const title = running ? 'Reconciling character identities'
    : failed ? 'Character reconciliation stopped'
      : resumable ? 'Character reconciliation paused'
        : 'No Cast identities yet';
  const body = running || resumable || failed
    ? 'The approved Script already supplies the speaking-role labels. Alexandria is checking the source only for aliases, duplicate identities, and supporting character details.'
    : 'Create the initial Cast from the approved Script speakers before assigning production voices.';
  const empty = UI.emptyState({ title, body });

  if (running || resumable || failed) {
    const message = total > 0
      ? `${completed} of ${total} source passages analyzed${running && next <= total ? `; passage ${next} is next` : ''}.`
      : 'Preparing measurable character-reconciliation progress.';
    const progressNode = UI.progress({
      label: 'Character reconciliation',
      state: total > 0 ? running ? 'running' : resumable ? 'resumable' : 'error' : 'indeterminate',
      value: percentage,
      message,
    });
    progressNode.classList.add('cast-discovery-progress');
    const detail = castText('p', 'cast-discovery-progress__detail metadata', message);
    empty.append(progressNode, detail);
  }

  if (running && onCancel) {
    empty.append(UI.button({
      label: 'Cancel reconciliation',
      variant: 'secondary',
      attributes: { 'data-cast-cancel-discovery': '' },
      onClick: onCancel,
    }));
  } else {
    empty.append(UI.button({
      label: resumable || failed ? 'Resume reconciliation' : 'Create Cast from Script',
      variant: 'primary',
      attributes: { 'data-cast-discover': '' },
      onClick: onDiscover,
    }));
  }
  profile.replaceChildren(empty);
  page.dataset.castState = running ? 'running' : resumable ? 'resumable' : failed ? 'error' : 'empty';
}

export function renderCastSelectionLoading(profile) {
  const appearance = document.createElement('div');
  appearance.dataset.appearanceSummary = '';
  appearance.append(UI.skeleton({ kind: 'panel', label: 'Loading appearance summary' }));
  profile.replaceChildren(
    UI.loadingState({ label: 'Loading character', detail: 'Reading the selected profile and Voice configuration.' }),
    UI.skeleton({ kind: 'heading', label: 'Loading character profile' }),
    UI.skeleton({ kind: 'panel', label: 'Loading Voice configuration' }),
    appearance,
  );
}
