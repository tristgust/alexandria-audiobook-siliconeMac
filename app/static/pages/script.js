'use strict';

import {
  createScriptPage, renderScriptReviewStatus, renderScriptSourceContext,
  scriptEntryContext, scriptEntryRow, scriptStageStates,
} from './script_components.js';

const UI = globalThis.AlexandriaUI;
const STATES = Object.freeze(['loading', 'empty', 'error', 'success', 'dense']);
const dataScriptContinue = 'data-script-continue';
const dataScriptApprove = 'data-script-approve';
const INITIAL_ENTRY_LIMIT = 120;
const ENTRY_BATCH_SIZE = 120;

function text(tag, className, value) {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

export async function mount({ root, route, shell, api, signal }) {
  const projectId = route.projectId || route.context.project || '';
  const owner = createScriptPage(route);
  owner.dataset.page = route.path;
  const sourceContext = document.createElement('section');
  sourceContext.className = 'script-source-context';
  sourceContext.dataset.scriptSourceContext = '';
  sourceContext.setAttribute('aria-label', 'Script source context');
  sourceContext.append(UI.skeleton({ label: 'Loading source context' }));
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
  owner.append(sourceContext, toolbar, lifecycleRegion, content);
  root.replaceChildren(owner);
  shell.player.set({ state: 'inactive', title: 'No Script audio selected' });

  let disposed = false;
  let visibleEntryLimit = INITIAL_ENTRY_LIMIT;
  let selectedEntryIndex = null;
  let inspectorState = document.querySelector('.app-shell')?.dataset.inspectorLayout === 'inline'
    ? 'open' : 'collapsed';
  let model = { flow: null, lifecycle: null, entries: [] };

  const goToCast = () => {
    shell.navigate(shell.routes.routeForPath('cast', projectId ? { project: projectId } : {}).hash);
  };

  const blockerForEntry = (index) => (model.lifecycle?.blockers || [])
    .find((item) => item.entry_index === index) || null;

  const showEntryContext = (entry, index, state = inspectorState) => {
    inspectorState = state;
    const blocker = blockerForEntry(index);
    shell.inspector.set({
      state,
      title: blocker ? 'Selected issue' : 'Script context',
      content: scriptEntryContext({
        entry, index, total: model.entries.length, blocker,
      }),
    });
  };

  const selectEntry = (entry, index, row, state = 'open') => {
    selectedEntryIndex = index;
    content.querySelectorAll('.script-entry').forEach((item) => item.removeAttribute('aria-current'));
    row?.setAttribute('aria-current', 'true');
    showEntryContext(entry, index, state);
  };

  const renderSourceContext = () => renderScriptSourceContext({
    root: sourceContext,
    flow: model.flow,
    lifecycle: model.lifecycle,
    entries: model.entries,
    projectTitle: route.projectTitle,
  });

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
      projectTitle: model.flow?.project?.name || route.projectTitle || projectId || 'Current project',
      status: {
        tone: accepted ? 'success' : blockers.length ? 'warning' : 'information',
        label: accepted ? 'Approved' : 'Review required',
      },
      stages: scriptStageStates(model.flow),
      primaryAction,
    });
  };

  const renderStatus = () => renderScriptReviewStatus(
    lifecycleRegion, model.lifecycle || {},
  );

  const renderEntries = ({ reset = false } = {}) => {
    if (reset) {
      visibleEntryLimit = INITIAL_ENTRY_LIMIT;
      selectedEntryIndex = null;
    }
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
      selectedEntryIndex = null;
      inspectorState = 'collapsed';
      shell.inspector.close();
      content.dataset.state = STATES[1];
      content.append(UI.emptyState({
        title: model.entries.length ? 'No entries match these filters' : 'No Script entries',
        body: model.entries.length ? 'Clear the search or choose another filter.' : 'Generate or import a Script before continuing.',
      }));
      return;
    }
    if (!entries.some((item) => item.index === selectedEntryIndex)) {
      selectedEntryIndex = entries.find((item) => blockerIndexes.has(item.index))?.index
        ?? entries[0].index;
    }
    const visibleEntries = entries.slice(0, visibleEntryLimit);
    const selectedEntry = entries.find((item) => item.index === selectedEntryIndex);
    if (selectedEntry && !visibleEntries.some((item) => item.index === selectedEntryIndex)) {
      visibleEntries.push(selectedEntry);
    }
    const list = document.createElement('ol');
    list.className = 'script-entry-list';
    visibleEntries.forEach(({ entry, index }) => {
      const row = scriptEntryRow(entry, index, selectEntry);
      if (index === selectedEntryIndex) row.setAttribute('aria-current', 'true');
      list.append(row);
    });
    const footer = document.createElement('div');
    footer.className = 'collection-footer';
    footer.dataset.scriptCollectionFooter = '';
    footer.append(text('span', 'metadata', `Showing ${visibleEntries.length.toLocaleString()} of ${entries.length.toLocaleString()} entries`));
    if (visibleEntryLimit < entries.length) {
      const remaining = entries.length - visibleEntryLimit;
      footer.append(UI.button({
        label: `Load ${Math.min(ENTRY_BATCH_SIZE, remaining).toLocaleString()} more`,
        variant: 'secondary',
        size: 'compact',
        attributes: { 'data-script-load-more': '' },
        onClick: () => {
          visibleEntryLimit += ENTRY_BATCH_SIZE;
          renderEntries();
        },
      }));
    }
    content.append(list, footer);
    if (selectedEntry) showEntryContext(selectedEntry.entry, selectedEntry.index, inspectorState);
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
    renderSourceContext();
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
      sourceContext.replaceChildren(UI.notice({
        tone: 'error', title: 'Source context could not load', body: failed.error, live: true,
      }));
      content.dataset.state = STATES[2];
      content.replaceChildren(UI.notice({
        tone: 'error', title: 'Script could not load', body: failed.error, live: true,
        action: UI.button({ label: 'Retry', onClick: load }),
      }));
      return;
    }
    model = { flow: flow.data, lifecycle: lifecycle.data, entries: Array.isArray(entries.data) ? entries.data : [] };
    renderSourceContext();
    renderHeader();
    renderStatus();
    renderEntries();
  };

  const resetEntries = () => renderEntries({ reset: true });
  search.querySelector('input').addEventListener('input', resetEntries);
  filter.querySelector('select').addEventListener('change', resetEntries);
  await load();
  return () => {
    if (disposed) return;
    disposed = true;
    search.querySelector('input').removeEventListener('input', resetEntries);
    filter.querySelector('select').removeEventListener('change', resetEntries);
    shell.inspector.close();
  };
}
