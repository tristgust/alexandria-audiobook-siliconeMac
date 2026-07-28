'use strict';

import {
  ARTIFACT_GROUP_ORDER, PROVENANCE_LABELS, REDUNDANT_PROVENANCE,
  artifactGroup, artifactMark, artifactMeta, artifactName, artifactPresentation,
  formatBytes, ownerForLibrary, provenanceValue, text, uniqueArtifactLabels, words,
} from './library_model.js';

const UI = globalThis.AlexandriaUI;
const STATES = Object.freeze(['loading', 'empty', 'error', 'success', 'dense']);
const ACTION_LABELS = Object.freeze({
  script: 'Open Script',
  produce: 'Open Produce',
  export: 'Open Export',
});
const SPECIALIST_PATHS = Object.freeze({
  'advanced-character-operations': 'more/advanced-character-operations',
  'voice-designer': 'more/voice-designer',
  'audio-preparer': 'more/audio-preparer',
  'dataset-builder': 'more/dataset-builder',
  'voice-training': 'more/voice-training',
  maintenance: 'more/maintenance',
  'model-cache': 'more/model-cache',
  'help-center': 'more/help-center',
});
const SPECIALIST_ACTIONS = Object.freeze({
  'advanced-character-operations': 'Review identities',
  'voice-designer': 'Design a Voice',
  'audio-preparer': 'Prepare audio',
  'dataset-builder': 'Build a dataset',
  'voice-training': 'Open Voice Lab',
  maintenance: 'Open Maintenance',
  'model-cache': 'Manage model cache',
  'help-center': 'Open Help Center',
});
export async function mount({ root, route, shell, api, signal }) {
  shell.globalHeader.set({
    title: 'Library',
    subtitle: 'Inspect project artifacts and open them in their native workflow stage.',
  });
  const owner = ownerForLibrary(route);
  const toolbar = document.createElement('div');
  toolbar.className = 'page-toolbar';
  const search = UI.searchField({
    label: 'Search Library', placeholder: 'Search artifacts',
    iconClass: 'fas fa-magnifying-glass',
  });
  const kind = UI.field({
    kind: 'select',
    label: 'Show',
    options: [{ value: 'all', label: 'Everything' }],
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
  shell.inspector.set({ state: 'hidden', title: 'Artifact details', content: null });

  let disposed = false;
  let artifacts = [];
  let selected = null;

  const openArtifact = (artifact) => {
    const native = artifact.native_route || {};
    const specialistPath = SPECIALIST_PATHS[native.tool];
    if (specialistPath) {
      shell.navigate(shell.routes.routeForPath(specialistPath, native.context || {}).hash);
      return;
    }
    if (native.destination) {
      shell.navigate(shell.routes.routeForPath(native.destination, native.context || {}).hash);
      return;
    }
    shell.navigate(native.hash || shell.routes.routeForPath('library').hash);
  };

  const actionLabel = (artifact) => {
    const native = artifact.native_route || {};
    return SPECIALIST_ACTIONS[native.tool]
      || ACTION_LABELS[native.destination]
      || 'Open artifact';
  };

  const renderDetail = (artifact, displayLabel) => {
    selected = artifact;
    const [kindLabel] = artifactPresentation(artifact);
    const detail = document.createElement('section');
    detail.className = 'supporting-detail library-detail';
    const identity = document.createElement('header');
    identity.className = 'library-detail__identity';
    const copy = document.createElement('div');
    copy.append(
      text('div', 'metadata', kindLabel),
      text('h2', 'section-title', displayLabel),
    );
    identity.append(artifactMark(artifact, 'library-detail__mark'), copy);
    detail.append(
      identity,
      UI.status({
        tone: artifact.state === 'invalid' || artifact.state === 'missing'
          ? 'error' : artifact.state === 'stale' ? 'warning' : 'success',
        label: words(artifact.state, 'Available'),
      }),
      text('p', 'flat-section__body', `${formatBytes(artifact.size_bytes)} · ${artifact.file_count || 0} file${artifact.file_count === 1 ? '' : 's'}`),
    );
    if (artifact.native_route) {
      detail.append(UI.button({
        label: actionLabel(artifact),
        variant: 'primary',
        onClick: () => openArtifact(artifact),
      }));
    }
    const provenance = artifact.provenance || {};
    const facts = document.createElement('dl');
    facts.className = 'fact-list';
    Object.entries(provenance)
      .filter(([label, value]) => (
        !REDUNDANT_PROVENANCE.has(label) && value != null && typeof value !== 'object'
      ))
      .slice(0, 5)
      .forEach(([label, value]) => {
        facts.append(
          text('dt', 'metadata', PROVENANCE_LABELS[label] || words(label)),
          text('dd', '', provenanceValue(label, value)),
        );
      });
    if (facts.children.length) detail.append(facts);
    return detail;
  };

  const render = () => {
    if (disposed || signal.aborted) return;
    const query = search.querySelector('input').value.trim().toLocaleLowerCase();
    const chosenKind = kind.querySelector('select').value;
    const visible = artifacts.filter((artifact) => (
      (chosenKind === 'all'
        || artifactGroup(artifact) === chosenKind
        || artifact.kind === chosenKind)
      && (!query || `${artifactName(artifact)} ${artifact.kind || ''} ${artifact.state || ''}`.toLocaleLowerCase().includes(query))
    )).sort((left, right) => {
      const groupDelta = ARTIFACT_GROUP_ORDER.indexOf(artifactGroup(left))
        - ARTIFACT_GROUP_ORDER.indexOf(artifactGroup(right));
      if (groupDelta) return groupDelta;
      return artifactName(left).localeCompare(artifactName(right));
    });
    const displayLabels = uniqueArtifactLabels(visible);
    content.replaceChildren();
    content.dataset.state = visible.length > 25 ? STATES[4] : STATES[3];
    if (!visible.length) {
      content.dataset.state = STATES[1];
      content.append(UI.emptyState({
        iconClass: artifacts.length ? 'fas fa-filter-circle-xmark' : 'fas fa-book-open',
        title: artifacts.length ? 'No artifacts match' : 'Library is empty',
        body: artifacts.length ? 'Clear the search or choose another group.' : 'Artifacts appear here as the project workflow creates them.',
      }));
      return;
    }
    if (!visible.includes(selected)) {
      selected = visible.find((artifact) => (
        ['production_audio', 'source_book', 'export_output'].includes(artifact.kind)
        && !['invalid', 'missing'].includes(artifact.state)
      )) || visible.find((artifact) => !['invalid', 'missing'].includes(artifact.state)) || visible[0];
    }
    const list = document.createElement('ul');
    list.className = 'supporting-list';
    list.setAttribute('aria-label', 'Library artifacts');
    let activeGroup = '';
    visible.forEach((artifact) => {
      const group = artifactGroup(artifact);
      if (group !== activeGroup) {
        activeGroup = group;
        const label = document.createElement('li');
        label.className = 'supporting-list__group-label';
        label.append(text('span', 'utility-heading', group));
        list.append(label);
      }
      const row = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'supporting-list__button';
      button.setAttribute('aria-pressed', String(artifact === selected));
      const copy = document.createElement('span');
      copy.className = 'library-artifact__copy';
      copy.append(
        text('strong', 'entity-title', displayLabels.get(artifact)),
        text('span', 'metadata', artifactMeta(artifact)),
      );
      button.classList.add('library-artifact');
      button.append(artifactMark(artifact), copy);
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
    content.append(UI.masterDetail({
      master,
      detail: renderDetail(selected, displayLabels.get(selected)),
    }));
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
    const presentGroups = new Set(artifacts.map(artifactGroup));
    const options = [
      { value: 'all', label: 'Everything' },
      ...ARTIFACT_GROUP_ORDER
        .filter((group) => presentGroups.has(group))
        .map((group) => ({ value: group, label: group })),
    ];
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
    shell.inspector.hide();
  };
}
