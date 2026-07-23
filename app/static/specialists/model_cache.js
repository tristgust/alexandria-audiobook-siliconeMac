'use strict';

import {
  resultMessage,
  supportOwner,
  supportReturn,
  textNode,
} from '/static/pages/more.js';

const UI = globalThis.AlexandriaUI;

function modelLabel(item) {
  return item.model?.purpose || 'Local Voice model';
}

function modelList(payload, api, signal, route, shell, feedback) {
  const list = document.createElement('div');
  list.className = 'support-list';
  (payload.models || []).forEach((item) => {
    const row = document.createElement('div');
    row.className = 'support-list-row';
    const copy = document.createElement('div');
    const ready = item.cached || item.state === 'cached';
    copy.append(
      textNode('strong', '', modelLabel(item)),
      textNode(
        'p',
        'support-status-copy',
        ready ? 'Pinned model files are available' : 'Files are missing or incomplete',
      ),
    );
    if (ready) {
      row.append(copy, UI.status({ label: 'Ready', tone: 'success' }));
    } else {
      const opener = UI.button({ label: 'Download or Repair', variant: 'secondary' });
      UI.dialog({
        opener,
        title: 'Review impact',
        body: 'This starts one explicit local cache operation. It does not change any project or Voice assignment.',
        confirmLabel: 'Download or Repair',
        onConfirm: async () => {
          const result = await api.post('/api/model_registry/action', {
            action: 'download',
            model_key: item.model?.key,
          }, { signal });
          if (signal.aborted) return;
          feedback.replaceChildren(UI.notice({
            tone: result.ok ? 'success' : 'error',
            title: result.ok ? 'Cache operation started' : 'Cache operation did not start',
            body: result.ok
              ? 'Only the selected pinned model will be downloaded or repaired.'
              : resultMessage(result, 'No cache files were changed.'),
            live: true,
          }));
          if (result.ok) window.setTimeout(
            () => shell.navigate(route.hash, { historyMode: 'replace' }),
            180,
          );
        },
      });
      row.append(copy, opener);
    }
    list.append(row);
  });
  return list.childElementCount ? list : UI.emptyState({
    title: 'No local models registered',
    body: 'Alexandria did not advertise any pinned local model requirements.',
  });
}

function memoryActions(memory, api, signal, route, shell, feedback) {
  const section = document.createElement('section');
  section.className = 'specialist-section';
  section.append(textNode('h2', '', 'Runtime memory'));
  const loaded = Array.isArray(memory.loaded_model_keys)
    ? memory.loaded_model_keys.length : 0;
  section.append(UI.notice({
    tone: loaded ? 'information' : 'success',
    title: loaded ? `${loaded} model${loaded === 1 ? '' : 's'} loaded` : 'No model currently loaded',
    body: Number(memory.active_jobs || 0)
      ? 'Synthesis is active, so manual release is unavailable.'
      : 'Manual release clears runtime memory only. Cached model files remain available.',
  }));
  const opener = UI.button({
    label: 'Release loaded models',
    variant: 'secondary',
    disabled: !loaded || Number(memory.active_jobs || 0) > 0,
  });
  UI.dialog({
    opener,
    title: 'Review impact',
    body: 'Release loaded model memory now. Saved projects, generated audio, and local cache files remain unchanged.',
    confirmLabel: 'Release models',
    onConfirm: async () => {
      const result = await api.post('/api/model_registry/memory/release', {}, { signal });
      if (signal.aborted) return;
      feedback.replaceChildren(UI.notice({
        tone: result.ok ? 'success' : 'error',
        title: result.ok ? 'Runtime memory released' : 'Models remain loaded',
        body: result.ok
          ? 'The models can load again when synthesis needs them.'
          : resultMessage(result, 'No runtime memory was released.'),
        live: true,
      }));
      if (result.ok) window.setTimeout(
        () => shell.navigate(route.hash, { historyMode: 'replace' }),
        180,
      );
    },
  });
  section.append(opener);
  return section;
}

export async function mount({ root, route, shell, api, signal }) {
  const dataRouteOwner = route.path;
  const { owner, stateRegion } = supportOwner(root, route, {
    page: 'model-cache',
    title: 'Local model cache',
    subtitle: 'Inspect pinned availability and start only explicit Download or Repair actions.',
    className: 'specialist-workspace',
  });
  owner.dataset.routeOwner = dataRouteOwner;
  stateRegion.setAttribute('data-state-region', '');
  const [statusResult, memoryResult] = await Promise.all([
    api.get('/api/model_registry/status', { signal }),
    api.get('/api/model_registry/memory', { signal }),
  ]);
  if (signal.aborted) return () => {};
  const toolbar = document.createElement('div');
  toolbar.className = 'support-toolbar';
  toolbar.append(supportReturn(route, shell));
  if (!statusResult.ok || !memoryResult.ok) {
    owner.dataset.viewState = 'error';
    const failed = !statusResult.ok ? statusResult : memoryResult;
    stateRegion.replaceChildren(toolbar, UI.notice({
      tone: 'error',
      title: 'Local model cache could not be inspected',
      body: resultMessage(failed, 'No model download or repair was started.'),
      live: true,
    }));
    return () => {};
  }
  const feedback = document.createElement('div');
  feedback.setAttribute('role', 'status');
  const grid = document.createElement('div');
  grid.className = 'specialist-section-grid';
  grid.append(
    modelList(statusResult.data, api, signal, route, shell, feedback),
    memoryActions(memoryResult.data, api, signal, route, shell, feedback),
  );
  stateRegion.replaceChildren(
    toolbar,
    UI.notice({
      tone: 'information',
      title: 'No automatic downloads',
      body: 'Alexandria never downloads a model from this page without an explicit reviewed action.',
    }),
    grid,
    feedback,
  );
  owner.dataset.viewState = 'ready';
  return () => {};
}
