'use strict';

const UI = globalThis.AlexandriaUI;
const STATES = Object.freeze(['loading', 'empty', 'error', 'success', 'dense']);
const dataScriptContinue = 'data-script-continue';
const dataScriptApprove = 'data-script-approve';

function text(tag, className, value) {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

function pageOwner(route) {
  const owner = document.createElement('article');
  owner.className = 'project-flow script-review';
  owner.dataset.routeOwner = route.path;
  owner.dataset.page = route.path;
  const title = UI.pageTitleBlock({
    title: 'Script',
    subtitle: 'Review the narration text and approve this exact version before casting.',
  });
  title.querySelector('h1').dataset.pageHeading = '';
  owner.append(title);
  return owner;
}

function stageStates(flow) {
  const stages = flow?.stage_map || {};
  return Object.fromEntries(['script', 'cast', 'produce', 'export'].map((name) => [
    name, stages[name]?.state || (name === 'script' ? 'current' : 'future'),
  ]));
}

function entryRow(entry, index, select) {
  const row = document.createElement('li');
  row.className = 'script-entry';
  row.tabIndex = 0;
  row.dataset.entryIndex = String(index);
  const speaker = text('strong', 'script-entry__speaker', entry.speaker || 'NARRATOR');
  const body = text('p', 'script-entry__text', entry.text || '');
  row.append(speaker, body);
  if (entry.instruct) row.append(text('p', 'metadata', entry.instruct));
  const activate = () => select(entry, index, row);
  row.addEventListener('click', activate);
  row.addEventListener('keydown', (event) => {
    if (!['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    activate();
  });
  return row;
}

export async function mount({ root, route, shell, api, signal }) {
  const owner = pageOwner(route);
  const toolbar = document.createElement('div');
  toolbar.className = 'page-toolbar';
  const search = UI.searchField({ label: 'Search Script entries', placeholder: 'Search Script' });
  const filter = UI.field({
    kind: 'select',
    label: 'Show',
    options: [
      { value: 'all', label: 'All entries' },
      { value: 'issues', label: 'Review issues' },
      { value: 'narration', label: 'Narration' },
      { value: 'dialogue', label: 'Dialogue' },
    ],
    value: route.context.filter || 'all',
  });
  toolbar.append(search, filter);
  const lifecycleRegion = document.createElement('section');
  lifecycleRegion.className = 'script-review__status';
  const content = document.createElement('section');
  content.className = 'content-state';
  content.dataset.state = STATES[0];
  content.append(UI.skeleton({ label: 'Loading Script' }), UI.skeleton(), UI.skeleton());
  owner.append(toolbar, lifecycleRegion, content);
  root.replaceChildren(owner);
  shell.player.set({ state: 'inactive', title: 'No Script audio selected' });

  let disposed = false;
  let model = { flow: null, lifecycle: null, entries: [] };

  const goToCast = () => {
    if (route.context.project) {
      shell.navigate('#/cast?project=' + encodeURIComponent(route.context.project));
    } else {
      shell.navigate('#/cast');
    }
  };

  const selectEntry = (entry, index, row) => {
    content.querySelectorAll('.script-entry').forEach((item) => item.removeAttribute('aria-current'));
    row.setAttribute('aria-current', 'true');
    const detail = document.createElement('div');
    detail.className = 'script-entry-detail';
    detail.append(
      text('div', 'metadata', `Entry ${index + 1}`),
      text('h3', 'entity-title', entry.speaker || 'NARRATOR'),
      text('p', 'flat-section__body', entry.text || ''),
    );
    if (entry.instruct) detail.append(text('p', 'metadata', `Direction: ${entry.instruct}`));
    shell.inspector.set({ state: 'open', title: 'Script entry', content: detail });
  };

  const renderHeader = () => {
    const lifecycle = model.lifecycle || {};
    const accepted = lifecycle.accepted || lifecycle.state === 'accepted';
    const blockers = (lifecycle.blockers || []).filter((item) => item.blocking !== false);
    const primaryAction = accepted ? {
      label: 'Continue to Cast',
      attributes: { [dataScriptContinue]: '' },
      onClick: goToCast,
    } : {
      label: 'Approve Script',
      disabled: blockers.length > 0,
      attributes: { [dataScriptApprove]: '' },
      onClick: approve,
    };
    shell.header.set({
      projectTitle: model.flow?.project?.name || route.context.project || 'Current project',
      status: {
        tone: accepted ? 'success' : blockers.length ? 'warning' : 'information',
        label: accepted ? 'Approved' : 'Review required',
      },
      stages: stageStates(model.flow),
      primaryAction,
    });
  };

  const renderStatus = () => {
    const lifecycle = model.lifecycle || {};
    const blockers = (lifecycle.blockers || []).filter((item) => item.blocking !== false);
    lifecycleRegion.replaceChildren();
    if (lifecycle.accepted || lifecycle.state === 'accepted') {
      lifecycleRegion.append(UI.notice({
        tone: 'success',
        title: 'Script approved',
        body: 'This exact Script version is ready for Cast.',
      }));
      return;
    }
    if (blockers.length) {
      const list = document.createElement('ul');
      list.className = 'blocker-list';
      blockers.forEach((item) => list.append(text('li', '', item.title || item.message || item.code)));
      const notice = UI.notice({
        tone: 'warning',
        title: 'Review required',
        body: `${blockers.length} blocking issue${blockers.length === 1 ? '' : 's'} must be resolved before approval.`,
        blocking: true,
      });
      notice.append(list);
      lifecycleRegion.append(notice);
      return;
    }
    lifecycleRegion.append(UI.notice({
      tone: 'information',
      title: 'Review required',
      body: 'Check the Script below, then approve its current fingerprints.',
      action: UI.button({
        label: 'Approve Script', variant: 'primary',
        attributes: { [dataScriptApprove]: '' }, onClick: approve,
      }),
    }));
  };

  const renderEntries = () => {
    const query = search.querySelector('input').value.trim().toLocaleLowerCase();
    const mode = filter.querySelector('select').value;
    const blockerIndexes = new Set((model.lifecycle?.blockers || [])
      .map((item) => item.entry_index).filter(Number.isInteger));
    const entries = model.entries.map((entry, index) => ({ entry, index })).filter(({ entry, index }) => {
      const issue = blockerIndexes.has(index);
      const speaker = String(entry.speaker || '').toUpperCase();
      const matchesMode = mode === 'all' || (mode === 'issues' && issue)
        || (mode === 'narration' && speaker === 'NARRATOR')
        || (mode === 'dialogue' && speaker !== 'NARRATOR');
      const haystack = `${entry.speaker || ''} ${entry.text || ''} ${entry.instruct || ''}`.toLocaleLowerCase();
      return matchesMode && (!query || haystack.includes(query));
    });
    content.replaceChildren();
    content.dataset.state = entries.length > 30 ? STATES[4] : STATES[3];
    if (!entries.length) {
      content.dataset.state = STATES[1];
      content.append(UI.emptyState({
        title: model.entries.length ? 'No entries match these filters' : 'No Script entries',
        body: model.entries.length ? 'Clear the search or choose another filter.' : 'Generate or import a Script before continuing.',
      }));
      return;
    }
    const list = document.createElement('ol');
    list.className = 'script-entry-list';
    entries.forEach(({ entry, index }) => list.append(entryRow(entry, index, selectEntry)));
    content.append(list);
  };

  async function approve() {
    const lifecycle = model.lifecycle || {};
    const blockers = (lifecycle.blockers || []).filter((item) => item.blocking !== false);
    if (blockers.length) return;
    lifecycleRegion.replaceChildren(UI.skeleton({ label: 'Approving Script' }));
    const fingerprints = lifecycle.fingerprints || {};
    const result = await api.post('/api/script_lifecycle/accept', {
      expected_script_fingerprint: fingerprints.script,
      expected_metadata_fingerprint: fingerprints.metadata,
      expected_source_fingerprint: fingerprints.source,
      expected_state_fingerprint: lifecycle.state_fingerprint,
    }, { signal });
    if (disposed || signal.aborted) return;
    if (!result.ok) {
      lifecycleRegion.replaceChildren(UI.notice({
        tone: 'error', title: 'Script approval failed', body: result.error, live: true,
      }));
      return;
    }
    model.lifecycle = { ...lifecycle, ...result.data, accepted: true, state: 'accepted', blockers: [] };
    renderHeader();
    renderStatus();
  }

  const load = async () => {
    const [flow, lifecycle, entries] = await Promise.all([
      api.get('/api/project_flow/status', { signal }),
      api.get('/api/script_lifecycle/status', { signal }),
      api.get('/api/annotated_script', { signal }),
    ]);
    if (disposed || signal.aborted) return;
    const failed = [flow, lifecycle, entries].find((result) => !result.ok);
    if (failed) {
      content.dataset.state = STATES[2];
      content.replaceChildren(UI.notice({
        tone: 'error', title: 'Script could not load', body: failed.error, live: true,
        action: UI.button({ label: 'Retry', onClick: load }),
      }));
      return;
    }
    model = { flow: flow.data, lifecycle: lifecycle.data, entries: Array.isArray(entries.data) ? entries.data : [] };
    renderHeader();
    renderStatus();
    renderEntries();
  };

  search.querySelector('input').addEventListener('input', renderEntries);
  filter.querySelector('select').addEventListener('change', renderEntries);
  await load();
  return () => {
    if (disposed) return;
    disposed = true;
    shell.inspector.close();
  };
}
