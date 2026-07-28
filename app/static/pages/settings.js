'use strict';

import {
  ensureSettingsStyles,
  settingsFieldError,
} from './settings_model.js';
import { createSettingsView } from './settings_view.js';

const UI = globalThis.AlexandriaUI;

export async function mount({ root, route, shell, api, signal }) {
  ensureSettingsStyles();
  const dataRouteOwner = route.path;
  const owner = document.createElement('article');
  owner.dataset.routeOwner = dataRouteOwner;
  owner.dataset.page = 'settings';
  owner.dataset.viewState = 'loading';
  owner.className = 'support-page settings-workspace';
  const title = UI.pageTitleBlock({
    id: 'settings-page-heading',
    title: 'Settings',
    subtitle: 'Global preferences and approved defaults for Alexandria.',
  });
  title.querySelector('h1').dataset.pageHeading = '';
  const stateRegion = document.createElement('div');
  stateRegion.setAttribute('data-state-region', '');
  stateRegion.setAttribute('aria-live', 'polite');
  stateRegion.append(UI.skeleton({ label: 'Loading Settings' }));
  owner.append(title, stateRegion);
  root.replaceChildren(owner);

  const result = await api.get('/api/settings', { signal });
  if (signal.aborted) return () => {};
  if (!result.ok) {
    owner.dataset.viewState = 'error';
    stateRegion.replaceChildren(UI.notice({
      tone: 'error',
      title: 'Settings could not be loaded',
      body: settingsFieldError(result),
      live: true,
      action: UI.button({
        label: 'Retry',
        variant: 'quiet',
        onClick: () => shell.navigate(route.hash, { historyMode: 'replace' }),
      }),
    }));
    return () => {};
  }
  const cleanup = createSettingsView({
    payload: result.data,
    route,
    shell,
    api,
    signal,
    owner,
    stateRegion,
  });
  return cleanup;
}
