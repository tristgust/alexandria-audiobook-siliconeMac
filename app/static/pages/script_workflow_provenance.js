'use strict';

const UI = globalThis.AlexandriaUI;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

function facts(items) {
  const list = document.createElement('dl');
  list.className = 'script-workflow-facts';
  items.forEach(([label, value]) => {
    const row = document.createElement('div');
    row.append(text('dt', '', label), text('dd', '', value || '—'));
    list.append(row);
  });
  return list;
}

export function createScriptWorkflowProvenance({ api, signal, getModel }) {
  const content = document.createElement('div');
  content.className = 'script-workflow-panel';
  const provenanceFacts = document.createElement('div');
  const versionsHost = document.createElement('div');
  versionsHost.className = 'script-version-list';
  content.append(provenanceFacts, versionsHost);

  const disclosure = UI.disclosure({
    label: 'Provenance and versions', content,
  });
  disclosure.dataset.scriptWorkflow = 'provenance';

  const refresh = async () => {
    const lifecycle = getModel().lifecycle || {};
    const provenance = lifecycle.provenance || {};
    provenanceFacts.replaceChildren(facts([
      ['Generation method', lifecycle.generation_method || provenance.method],
      ['Origin', provenance.origin_type || provenance.mode],
      ['Verification', provenance.provenance_status],
      ['Current version', lifecycle.accepted_version_id || 'Not approved'],
    ]));
    versionsHost.replaceChildren(UI.loadingState({
      label: 'Loading Script versions',
      detail: 'Reading approved versions and provenance.',
      size: 'compact',
    }));
    const result = await api.get('/api/script_lifecycle/versions', { signal });
    if (!result.ok) {
      versionsHost.replaceChildren(UI.notice({
        tone: 'error', title: 'Versions could not load', body: result.error,
      }));
      return;
    }
    const versions = result.data?.versions || [];
    if (!versions.length) {
      versionsHost.replaceChildren(text('p', 'metadata', 'No approved Script versions yet.'));
      return;
    }
    const list = document.createElement('ul');
    list.className = 'divider-list';
    versions.forEach((version) => {
      const row = document.createElement('li');
      row.className = 'script-version-row';
      row.append(
        text('strong', '', version.label || version.version_id || 'Script version'),
        text('span', 'metadata', version.created_at_utc || version.accepted_at_utc || 'Saved version'),
      );
      list.append(row);
    });
    versionsHost.replaceChildren(list);
  };

  const trigger = disclosure.querySelector('.disclosure__trigger');
  trigger.addEventListener('click', refresh);
  return Object.freeze({
    root: disclosure,
    open() {
      if (trigger.getAttribute('aria-expanded') !== 'true') trigger.click();
      disclosure.scrollIntoView({ block: 'nearest' });
      trigger.focus();
    },
  });
}
