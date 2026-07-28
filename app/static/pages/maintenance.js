'use strict';

import {
  supportOwner,
} from '/static/pages/more.js';
import {
  MAINTENANCE_READS,
  safeMaintenanceRead,
} from './maintenance_model.js';
import { renderMaintenanceSections } from './maintenance_sections.js';

const UI = globalThis.AlexandriaUI;

export async function mount({ root, route, shell, api, signal }) {
  const dataRouteOwner = route.path;
  const { owner, stateRegion } = supportOwner(root, route, {
    page: 'maintenance',
    title: 'Maintenance',
    subtitle: 'Read-only health first, with explicit review for guarded technical actions.',
    className: 'maintenance-workspace specialist-workspace',
  });
  owner.dataset.routeOwner = dataRouteOwner;
  stateRegion.setAttribute('data-state-region', '');
  const settled = await Promise.allSettled(
    MAINTENANCE_READS.map(([, endpoint]) => api.get(endpoint, { signal })),
  );
  if (signal.aborted) return () => {};
  const data = Object.fromEntries(MAINTENANCE_READS.map(([key], index) => [
    key,
    safeMaintenanceRead(settled, index),
  ]));
  if (Object.values(data).every((value) => value === null)) {
    owner.dataset.viewState = 'error';
    stateRegion.replaceChildren(UI.notice({
      tone: 'error',
      title: 'Maintenance status could not be loaded',
      body: 'No authoritative health source responded. Nothing was changed.',
      live: true,
    }));
    return () => {};
  }
  renderMaintenanceSections({
    data,
    route,
    shell,
    api,
    signal,
    owner,
    stateRegion,
  });
  return () => {};
}
