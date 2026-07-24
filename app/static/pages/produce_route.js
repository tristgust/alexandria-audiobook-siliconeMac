'use strict';

import { createProduceActions } from './produce_actions.js';
import { createProduceInspector } from './produce_inspector.js';
import { createProduceList } from './produce_list.js';
import { produceStyle, waitForProduceStyle } from './produce_model.js';
import {
  createProducePage, renderProduceActivity, renderProduceError,
  renderProduceLoading, updateProduceSubtitle,
} from './produce_page_view.js';

const UI = globalThis.AlexandriaUI;

export async function mountProduce({ root, route, shell, api, signal }) {
  if (!UI) throw new Error('Produce requires Alexandria UI primitives.');
  const projectId = route.projectId || route.context.project || '';
  const style = produceStyle();
  const { owner, activity, toolbar, content } = createProducePage(root, route);
  let aggregate = null;
  let selected = null;
  let disposed = false;
  let loadEpoch = 0;
  let pollTimer = null;
  let inspectorRequested = false;
  let actions = null;
  let list = null;
  let inspector = null;

  const inspectorState = () => {
    const breakpoint = Number.parseFloat(getComputedStyle(document.documentElement)
      .getPropertyValue('--breakpoint-inspector'));
    return inspectorRequested || innerWidth >= breakpoint ? 'open' : 'collapsed';
  };

  const goTo = (destination, context = {}) => shell.navigate(
    shell.routes.routeForPath(destination, { ...(projectId ? { project: projectId } : {}), ...context }).hash,
  );

  const tracker = () => shell.tracker.set({
    script: 'complete',
    cast: 'complete',
    produce: 'current',
    export: aggregate?.summary?.complete ? 'future' : 'blocked',
  });

  const header = () => {
    const running = Boolean(aggregate?.process?.running);
    const complete = Boolean(aggregate?.summary?.complete);
    const blockers = Number(aggregate?.summary?.blocker_count) || 0;
    shell.header.set({
      projectTitle: route.projectTitle || projectId || 'Project workspace',
      save: { state: 'saved', label: 'Saved' },
      status: {
        tone: running ? 'information' : complete ? 'success' : blockers ? 'warning' : 'information',
        label: running ? 'Generating audio…'
          : complete ? 'Production complete'
            : blockers ? `${blockers} item${blockers === 1 ? '' : 's'} need attention`
              : 'Ready to produce',
      },
      primaryAction: actions?.primaryAction(() => goTo('export')) || null,
    });
    tracker();
  };

  const render = () => {
    if (!aggregate) return;
    header();
    updateProduceSubtitle(owner, aggregate);
    renderProduceActivity({
      activity,
      aggregate,
      actionMessage: actions.message,
      onCancel: actions.cancel,
    });
    actions.renderToolbar();
    list.render();
    inspector.render();
    if (aggregate.process?.running) {
      clearTimeout(pollTimer);
      pollTimer = setTimeout(() => loadProduce(false), 1500);
    }
  };

  actions = createProduceActions({
    api,
    signal,
    toolbar,
    getAggregate: () => aggregate,
    onRender: render,
    onReload: (showLoading) => loadProduce(showLoading),
    onFilterChange: () => list?.render({ reset: true }),
  });
  list = createProduceList({
    content,
    owner,
    shell,
    actions,
    getAggregate: () => aggregate,
    getSelected: () => selected,
    setSelected: (value) => { selected = value; inspectorRequested = true; },
    onSelectionChange: () => inspector.render(),
    onReviewScript: () => goTo('script'),
    projectId,
  });
  inspector = createProduceInspector({
    shell,
    projectId,
    getAggregate: () => aggregate,
    getSelected: () => selected,
    actions,
    inspectorState,
  });

  async function loadProduce(showLoading = true) {
    const epoch = ++loadEpoch;
    clearTimeout(pollTimer);
    if (showLoading) renderProduceLoading({ owner, activity, toolbar, content, shell, inspectorState });
    const query = new URLSearchParams();
    const selectedId = selected?.chunk_id || route.context.chunk;
    if (selectedId) query.set('selected_chunk_id', selectedId);
    const response = await api.get(`/api/produce${query.size ? `?${query}` : ''}`, { signal });
    if (disposed || signal.aborted || epoch !== loadEpoch) return;
    if (!response.ok) {
      if (response.kind !== 'canceled') {
        renderProduceError({
          owner, activity, toolbar, content, shell,
          retry: loadProduce,
          message: response.error,
        });
        shell.header.set({
          projectTitle: route.projectTitle || projectId || 'Project workspace',
          save: { state: 'saved', label: 'Saved' },
          status: { tone: 'error', label: 'Unavailable' },
          primaryAction: null,
        });
        tracker();
      }
      return;
    }
    aggregate = response.data || {};
    const chunks = aggregate.chunks || [];
    selected = aggregate.selected_chunk
      || chunks.find((chunk) => chunk.chunk_id === aggregate.selected_chunk_id)
      || chunks.find((chunk) => chunk.state === 'stale')
      || chunks.find((chunk) => ['failed', 'needs_listening', 'needs_review', 'ready'].includes(chunk.state))
      || chunks[0]
      || null;
    if (selected) aggregate.selected_chunk_id = selected.chunk_id;
    render();
  }

  const cleanup = () => {
    if (disposed) return;
    disposed = true;
    loadEpoch += 1;
    clearTimeout(pollTimer);
    actions.cleanup();
    list.cleanup();
    shell.inspector.hide();
    if (style.owned) style.node.remove();
    signal.removeEventListener('abort', cleanup);
  };
  signal.addEventListener('abort', cleanup, { once: true });

  shell.player.set({ state: 'inactive', title: 'No active production audio' });
  renderProduceLoading({ owner, activity, toolbar, content, shell, inspectorState });
  header();
  await waitForProduceStyle(style.node, signal);
  await loadProduce(false);
  return cleanup;
}
