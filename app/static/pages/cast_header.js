'use strict';

export function createCastHeader({
  shell, route, projectId, getAggregate, getDiscoveryState, getSaveState,
}) {
  return function renderCastHeader() {
    const summary = getAggregate()?.summary || {};
    const complete = Boolean(summary.complete);
    const blockers = Number(summary.blocker_count) || 0;
    const discoveryState = getDiscoveryState() || summary.state;
    const saveState = getSaveState();
    shell.header.set({
      projectTitle: route.projectTitle || projectId || 'Project workspace',
      save: {
        state: saveState === 'error' ? 'recoverable error'
          : saveState === 'refreshing' ? 'saved' : saveState,
        label: saveState === 'dirty' ? 'Unsaved changes'
          : saveState === 'saving' ? 'Saving…'
            : saveState === 'refreshing' ? 'Saved · refreshing'
              : saveState === 'error' ? 'Save failed' : 'Saved',
      },
      status: {
        tone: complete ? 'success' : blockers ? 'warning' : 'information',
        label: complete ? 'Cast ready'
          : discoveryState === 'running' ? 'Reconciling identities'
            : discoveryState === 'resumable' ? 'Reconciliation paused'
              : blockers ? `${blockers} item${blockers === 1 ? ' needs' : 's need'} attention`
                : 'Cast in progress',
      },
      primaryAction: complete ? {
        label: 'Continue to Produce',
        onClick: () => shell.navigate(shell.routes.routeForPath('produce',
          projectId ? { project: projectId } : {}).hash),
      } : null,
    });
    shell.tracker.set({ script: 'complete', cast: 'current', produce: 'future', export: 'future' });
  };
}
