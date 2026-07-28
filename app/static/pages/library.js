'use strict';

import { libraryDeleteAction } from './library_actions.js';

const UI = globalThis.AlexandriaUI;
const STATES = Object.freeze(['loading', 'empty', 'error', 'success', 'dense']);
const ACTION_LABELS = Object.freeze({
  script: 'Open Script', produce: 'Open Produce', export: 'Open Export',
});

function text(tag, className, value) {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

function ownerFor(route) {
  const owner = document.createElement('article');
  owner.className = 'supporting-page library-page';
  owner.dataset.routeOwner = route.path;
  owner.dataset.page = route.path;
  const title = UI.pageTitleBlock({
    title: 'Library',
    subtitle: 'Inspect project artifacts and open them in their native workflow stage.',
  });
  title.querySelector('h1').dataset.pageHeading = '';
  owner.append(title);
  return owner;
}

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export async function mount({ root, route, shell, api, signal }) {
  const owner = ownerFor(route);
  const toolbar = document.createElement('div');
  toolbar.className = 'page-toolbar';
  const search = UI.searchField({ label: 'Search Library', placeholder: 'Search artifacts' });
  const kind = UI.field({
    kind: 'select',
    label: 'Artifact type',
    options: [{ value: 'all', label: 'All types' }],
    value: route.context.filter || 'all',
  });
  toolbar.append(search, kind);
  const content = document.createElement('section');
  content.className = 'content-state';
  content.dataset.state = STATES[0];
  content.append(UI.skeleton({ label: 'Loading Library' }), UI.skeleton());
  owner.append(toolbar, content);
  root.replaceChildren(owner);
  shell.player.set({ state: 'inactive', title: 'No Library audio selected' });
  shell.inspector.set({ state: 'collapsed', title: 'Artifact details', content: null });

  let disposed = false;
  let artifacts = [];
  let inventoryFingerprint = '';
  let selected = null;

  const openArtifact = (artifact) => {
    const native = artifact.native_route || {};
    if (native.hash) {
      shell.navigate(native.hash);
      return;
    }
    const destination = native.destination || 'library';
    shell.navigate(shell.routes.routeForPath(destination, native.context || {}).hash);
  };

  const renderDetail = (artifact) => {
    selected = artifact;
    const detail = document.createElement('section');
    detail.className = 'supporting-detail';
    detail.append(
      text('div', 'metadata', String(artifact.kind || 'artifact').replaceAll('_', ' ')),
      text('h2', 'section-title', artifact.name || 'Unnamed artifact'),
      UI.status({
        tone: artifact.state === 'invalid' || artifact.state === 'missing' ? 'error' : 'success',
        label: artifact.state || 'available',
      }),
      text('p', 'flat-section__body', `${formatBytes(artifact.size_bytes)} · ${artifact.file_count || 0} file${artifact.file_count === 1 ? '' : 's'}`),
    );
    const destination = artifact.native_route?.destination;
    if (destination) {
      detail.append(UI.button({
        label: ACTION_LABELS[destination] || `Open ${destination}`,
        variant: 'primary',
        onClick: () => openArtifact(artifact),
      }));
    }
    const provenance = artifact.provenance || {};
    const facts = document.createElement('dl');
    facts.className = 'fact-list';
    for (const [label, value] of Object.entries(provenance).slice(0, 5)) {
      if (value == null || typeof value === 'object') continue;
      facts.append(text('dt', 'metadata', label.replaceAll('_', ' ')), text('dd', '', value));
    }
    if (facts.children.length) detail.append(facts);
    const usage = Array.isArray(artifact.usage) ? artifact.usage : [];
    if (usage.length) {
      const dependencies = document.createElement('section');
      dependencies.className = 'library-dependencies';
      dependencies.append(
        text('h3', 'entity-title', 'Dependencies'),
        text('p', 'metadata', `${usage.length} workflow reference${usage.length === 1 ? '' : 's'} · ${artifact.blocking_dependency_count || 0} blocking`),
      );
      detail.append(dependencies);
    }
    const deleteAction = libraryDeleteAction({
      artifact,
      inventoryFingerprint,
      route,
      api,
      signal,
      onDeleted: load,
    });
    if (deleteAction) detail.append(deleteAction);
    return detail;
  };

  const render = () => {
    if (disposed || signal.aborted) return;
    const query = search.querySelector('input').value.trim().toLocaleLowerCase();
    const chosenKind = kind.querySelector('select').value;
    const visible = artifacts.filter((artifact) => (
      (chosenKind === 'all' || artifact.kind === chosenKind)
      && (!query || `${artifact.name || ''} ${artifact.kind || ''} ${artifact.state || ''}`.toLocaleLowerCase().includes(query))
    ));
    content.replaceChildren();
    content.dataset.state = visible.length > 25 ? STATES[4] : STATES[3];
    if (!visible.length) {
      content.dataset.state = STATES[1];
      content.append(UI.emptyState({
        title: artifacts.length ? 'No artifacts match' : 'Library is empty',
        body: artifacts.length ? 'Clear the search or choose another type.' : 'Artifacts appear here as the project workflow creates them.',
      }));
      return;
    }
    if (!visible.includes(selected)) selected = visible[0];
    const list = document.createElement('ul');
    list.className = 'supporting-list';
    list.setAttribute('aria-label', 'Library artifacts');
    visible.forEach((artifact) => {
      const row = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'supporting-list__button';
      button.setAttribute('aria-pressed', String(artifact === selected));
      button.append(
        text('strong', 'entity-title', artifact.name || 'Unnamed artifact'),
        text('span', 'metadata', `${String(artifact.kind || '').replaceAll('_', ' ')} · ${artifact.state || 'unknown'}`),
      );
      button.addEventListener('click', () => {
        selected = artifact;
        render();
      });
      row.append(button);
      list.append(row);
    });
    const master = document.createElement('section');
    master.className = 'supporting-master';
    master.append(list);
    content.append(UI.masterDetail({ master, detail: renderDetail(selected) }));
  };

  const load = async () => {
    const result = await api.get('/api/library', { signal });
    if (disposed || signal.aborted) return;
    if (!result.ok) {
      content.dataset.state = STATES[2];
      content.replaceChildren(UI.notice({
        tone: 'error', title: 'Library could not load', body: result.error, live: true,
        action: UI.button({ label: 'Retry', onClick: load }),
      }));
      return;
    }
    artifacts = Array.isArray(result.data?.artifacts) ? result.data.artifacts : [];
    inventoryFingerprint = result.data?.inventory_fingerprint || '';
    if (selected && !artifacts.some((artifact) => artifact.artifact_id === selected.artifact_id)) {
      selected = null;
    }
    const options = [{ value: 'all', label: 'All types' }, ...new Set(artifacts.map((item) => item.kind))]
      .map((entry) => typeof entry === 'string' ? { value: entry, label: entry.replaceAll('_', ' ') } : entry);
    const select = kind.querySelector('select');
    select.replaceChildren();
    options.forEach((entry) => {
      const option = document.createElement('option');
      option.value = entry.value;
      option.textContent = entry.label;
      select.append(option);
    });
    render();
  };

  search.querySelector('input').addEventListener('input', render);
  kind.querySelector('select').addEventListener('change', render);
  await load();
  return () => {
    if (disposed) return;
    disposed = true;
    shell.inspector.close();
  };
}
