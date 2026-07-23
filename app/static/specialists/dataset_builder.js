'use strict';

import {
  resultMessage,
  supportOwner,
  supportReturn,
  textNode,
} from '/static/pages/more.js';

const UI = globalThis.AlexandriaUI;

function projectList(projects) {
  const list = document.createElement('div');
  list.className = 'support-list';
  projects.forEach((project) => {
    const row = document.createElement('div');
    row.className = 'support-list-row';
    const copy = document.createElement('div');
    const sampleCount = Number(project.sample_count || 0);
    const doneCount = Number(project.done_count || 0);
    copy.append(
      textNode('strong', '', project.name || 'Voice dataset'),
      textNode(
        'p',
        'support-status-copy',
        `${doneCount} of ${sampleCount} sample${sampleCount === 1 ? '' : 's'} prepared`,
      ),
    );
    row.append(copy, UI.status({
      label: sampleCount > 0 && doneCount === sampleCount ? 'Ready to review' : 'Draft',
      tone: sampleCount > 0 && doneCount === sampleCount ? 'success' : 'neutral',
    }));
    list.append(row);
  });
  return list.childElementCount ? list : UI.emptyState({
    title: 'No dataset drafts',
    body: 'Create a draft to organize prepared Voice clips.',
  });
}

function createForm(api, signal, route, shell) {
  const form = document.createElement('form');
  form.className = 'settings-form specialist-section';
  form.append(textNode('h2', '', 'Create a dataset draft'));
  const name = UI.field({
    id: 'dataset-builder-name',
    label: 'Dataset name',
    description: 'Use a short project label. Training does not begin here.',
  });
  const submit = UI.button({
    label: 'Create draft',
    variant: 'primary',
    type: 'submit',
  });
  const feedback = document.createElement('div');
  feedback.setAttribute('role', 'status');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const value = name.querySelector('input').value.trim();
    if (!value) {
      feedback.replaceChildren(UI.notice({
        tone: 'warning',
        title: 'Dataset name required',
        body: 'Enter a short name before creating the draft.',
        live: true,
      }));
      return;
    }
    submit.disabled = true;
    const result = await api.post('/api/dataset_builder/create', { name: value }, { signal });
    if (signal.aborted) return;
    submit.disabled = false;
    feedback.replaceChildren(UI.notice({
      tone: result.ok ? 'success' : 'error',
      title: result.ok ? 'Dataset draft created' : 'Draft was not created',
      body: result.ok
        ? 'Add and review samples before saving any training dataset.'
        : resultMessage(result, 'No dataset files were changed.'),
      live: true,
    }));
    if (result.ok) window.setTimeout(
      () => shell.navigate(route.hash, { historyMode: 'replace' }),
      180,
    );
  });
  form.append(name, submit, feedback);
  return form;
}

export async function mount({ root, route, shell, api, signal }) {
  const dataRouteOwner = route.path;
  const { owner, stateRegion } = supportOwner(root, route, {
    page: 'dataset-builder',
    title: 'Dataset builder',
    subtitle: 'Review and package prepared Voice clips without starting training.',
    className: 'specialist-workspace',
  });
  owner.dataset.routeOwner = dataRouteOwner;
  stateRegion.setAttribute('data-state-region', '');
  const result = await api.get('/api/dataset_builder/list', { signal });
  if (signal.aborted) return () => {};
  const toolbar = document.createElement('div');
  toolbar.className = 'support-toolbar';
  toolbar.append(supportReturn(route, shell));
  if (!result.ok) {
    owner.dataset.viewState = 'error';
    stateRegion.replaceChildren(toolbar, UI.notice({
      tone: 'error',
      title: 'Dataset builder could not be loaded',
      body: resultMessage(result, 'No dataset was changed.'),
      live: true,
    }));
    return () => {};
  }
  const projects = Array.isArray(result.data) ? result.data : [];
  const grid = document.createElement('div');
  grid.className = 'specialist-section-grid';
  grid.append(projectList(projects), createForm(api, signal, route, shell));
  stateRegion.replaceChildren(
    toolbar,
    UI.notice({
      tone: 'information',
      title: 'Packaging only',
      body: 'This workspace prepares material. It does not start training or change the production Voice.',
    }),
    grid,
  );
  owner.dataset.viewState = projects.length ? 'ready' : 'empty';
  return () => {};
}
