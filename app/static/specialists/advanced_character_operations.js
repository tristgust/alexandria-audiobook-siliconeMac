'use strict';

import {
  resultMessage,
  supportOwner,
  supportReturn,
  textNode,
} from '/static/pages/more.js';

const UI = globalThis.AlexandriaUI;

function identityList(payload) {
  const list = document.createElement('div');
  list.className = 'support-list';
  (payload.entries || []).forEach((entry) => {
    const row = document.createElement('div');
    row.className = 'support-list-row';
    const copy = document.createElement('div');
    copy.append(
      textNode('strong', '', entry.display_name || entry.canonical_name),
      textNode(
        'p',
        'support-status-copy',
        `${Number(entry.line_count || 0)} script line${Number(entry.line_count || 0) === 1 ? '' : 's'}`,
      ),
    );
    row.append(copy, UI.status({
      label: entry.resolution_status === 'resolved' ? 'Resolved' : 'Needs review',
      tone: entry.resolution_status === 'resolved' ? 'success' : 'warning',
    }));
    list.append(row);
  });
  return list.childElementCount ? list : UI.emptyState({
    title: 'No identities to review',
    body: 'Approve the Script and Cast before using advanced identity operations.',
  });
}

function renameControl(payload, api, signal, route, shell) {
  const section = document.createElement('div');
  section.className = 'specialist-section';
  section.append(textNode('h2', '', 'Rename one approved identity'));
  const options = (payload.entries || []).map((entry) => ({
    value: entry.character_id,
    label: entry.display_name || entry.canonical_name,
  }));
  const character = UI.field({
    id: 'identity-rename-character',
    label: 'Identity',
    kind: 'select',
    options,
  });
  const name = UI.field({
    id: 'identity-rename-name',
    label: 'New canonical name',
    description: 'This can invalidate generated audio for affected lines.',
  });
  const feedback = document.createElement('div');
  feedback.setAttribute('role', 'status');
  const opener = UI.button({ label: 'Review rename', variant: 'secondary' });
  UI.dialog({
    opener,
    title: 'Review impact',
    body: 'Renaming updates Script identity references. Existing production Voice selection remains a Cast decision.',
    confirmLabel: 'Rename identity',
    destructive: true,
    onConfirm: async () => {
      const entryId = character.querySelector('select').value;
      const newName = name.querySelector('input').value.trim();
      if (!entryId || !newName) {
        feedback.replaceChildren(UI.notice({
          tone: 'warning',
          title: 'Name required',
          body: 'Choose an identity and enter its new canonical name.',
          live: true,
        }));
        return;
      }
      const result = await api.post('/api/speaker_management/action', {
        operation: 'rename',
        expected_script_fingerprint: payload.script_fingerprint,
        payload: { entry_id: entryId, new_name: newName },
      }, { signal });
      if (signal.aborted) return;
      feedback.replaceChildren(UI.notice({
        tone: result.ok ? 'success' : 'error',
        title: result.ok ? 'Identity renamed' : 'Identity was not renamed',
        body: result.ok
          ? 'Review Cast and regenerate any stale audio before Export.'
          : resultMessage(result, 'No changes were made.'),
        live: true,
      }));
      if (result.ok) window.setTimeout(
        () => shell.navigate(route.hash, { historyMode: 'replace' }),
        180,
      );
    },
  });
  section.append(character, name, opener, feedback);
  return section;
}

export async function mount({ root, route, shell, api, signal }) {
  const dataRouteOwner = route.path;
  const { owner, stateRegion } = supportOwner(root, route, {
    page: 'advanced-identity-operations',
    title: 'Advanced identity operations',
    subtitle: 'Guarded speaker-label, alias, identity, and rollback review.',
    className: 'specialist-workspace',
  });
  owner.dataset.routeOwner = dataRouteOwner;
  stateRegion.setAttribute('data-state-region', '');
  const params = new URLSearchParams();
  if (route.context.character) params.set('speaker', route.context.character);
  const result = await api.get(`/api/speaker_management/status${params.size ? `?${params}` : ''}`, {
    signal,
  });
  if (signal.aborted) return () => {};
  const toolbar = document.createElement('div');
  toolbar.className = 'support-toolbar';
  toolbar.append(supportReturn(route, shell));
  if (!result.ok) {
    owner.dataset.viewState = 'error';
    stateRegion.replaceChildren(toolbar, UI.notice({
      tone: 'error',
      title: 'Identity status could not be loaded',
      body: resultMessage(result, 'No identity changes are available.'),
      live: true,
    }));
    return () => {};
  }
  const payload = result.data;
  const content = document.createElement('div');
  content.className = 'specialist-section-grid';
  content.append(identityList(payload));
  if (payload.available && (payload.entries || []).length) {
    content.append(renameControl(payload, api, signal, route, shell));
  }
  stateRegion.replaceChildren(
    toolbar,
    UI.notice({
      tone: 'warning',
      title: 'Cast remains authoritative',
      body: 'These operations can change Script identities. Production Voice assignment happens only in Cast.',
    }),
    content,
  );
  owner.dataset.viewState = (payload.entries || []).length ? 'ready' : 'empty';
  return () => {};
}
