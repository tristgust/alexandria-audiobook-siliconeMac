'use strict';

import { castText } from './cast_model.js';

const UI = globalThis.AlexandriaUI;

export function createCastPage(root) {
  const page = document.createElement('article');
  page.className = 'cast-page';
  page.dataset.routeOwner = 'cast';
  page.dataset.page = 'cast';
  page.dataset.castPage = '';
  const title = UI.pageTitleBlock({
    title: 'Cast',
    subtitle: 'Assign production voices and review each character before producing audio.',
  });
  title.querySelector('h1').dataset.pageHeading = '';
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
  workspace.append(master, profile);
  page.append(title, workspace);
  root.replaceChildren(page);
  return { page, master, profile };
}

export function renderCastLoading({ roster, profile, page }) {
  roster.loading();
  const appearance = document.createElement('div');
  appearance.dataset.appearanceSummary = '';
  appearance.append(
    UI.skeleton({ label: 'Loading appearance summary' }),
    castText('p', 'cast-profile__muted', 'Visual evidence not available while this profile is loading.'),
  );
  profile.replaceChildren(
    UI.skeleton({ label: 'Loading selected character' }),
    UI.skeleton({ label: 'Loading voice profile' }),
    appearance,
  );
  page.dataset.castState = 'loading';
}

export function renderCastError({ master, profile, page, onRetry, message }) {
  const retry = UI.button({
    label: 'Retry', variant: 'secondary', attributes: { 'data-cast-retry': '' }, onClick: onRetry,
  });
  master.replaceChildren(castText('h2', 'cast-roster__title', 'Characters'));
  profile.replaceChildren(UI.notice({
    tone: 'error', title: 'Cast unavailable',
    body: message || 'Alexandria could not load this Cast profile.',
    action: retry, live: true,
  }));
  page.dataset.castState = 'error';
}

export function renderCastEmpty({ roster, profile, page }) {
  roster.empty();
  profile.replaceChildren(UI.emptyState({
    title: 'No character selected',
    body: 'Review Script to identify speaking roles before assigning voices.',
  }));
  page.dataset.castState = 'empty';
}

export function renderCastSelectionLoading(profile) {
  const appearance = document.createElement('div');
  appearance.dataset.appearanceSummary = '';
  appearance.append(
    UI.skeleton({ label: 'Loading appearance summary' }),
    castText('p', 'cast-profile__muted', 'Visual evidence not available while this profile is loading.'),
  );
  profile.replaceChildren(
    UI.skeleton({ label: 'Loading character profile' }),
    appearance,
  );
}
