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

function compactIdentity(value, length = 12) {
  const text = String(value || '').trim();
  return text ? text.slice(0, length) : 'unknown';
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return 'not measured';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KiB', 'MiB', 'GiB', 'TiB'];
  let amount = bytes;
  let unit = -1;
  do {
    amount /= 1024;
    unit += 1;
  } while (amount >= 1024 && unit < units.length - 1);
  return `${amount >= 10 ? amount.toFixed(1) : amount.toFixed(2)} ${units[unit]}`;
}

function ownerLabel(owner) {
  if (!owner || typeof owner !== 'object') return '';
  return owner.label || [owner.domain, owner.operation, owner.job_id]
    .filter(Boolean).join(' · ');
}

function residencyList(memory) {
  const residents = Array.isArray(memory.residents) ? memory.residents : [];
  if (!residents.length) return null;
  const section = document.createElement('div');
  section.className = 'support-list';
  section.setAttribute('data-model-residents', '');
  residents.forEach((resident) => {
    const row = document.createElement('div');
    row.className = 'support-list-row';
    row.dataset.modelResident = resident.slot_id || resident.component_id || 'unknown';
    row.dataset.state = resident.state || 'unknown';
    const copy = document.createElement('div');
    const leaseCount = Number(resident.active_lease_count || 0);
    const identity = [
      resident.runtime,
      resident.device,
      `revision ${compactIdentity(resident.revision)}`,
      `build ${compactIdentity(resident.build_id)}`,
    ].filter(Boolean).join(' · ');
    const owner = Array.isArray(resident.owners)
      ? ownerLabel(resident.owners[0]) : '';
    copy.append(
      textNode('strong', '', resident.component_id || resident.slot_id || 'Loaded model'),
      textNode('p', 'support-status-copy', identity),
      textNode(
        'p',
        'support-status-copy',
        leaseCount
          ? `${leaseCount} in-flight lease${leaseCount === 1 ? '' : 's'}${owner ? ` · ${owner}` : ''}`
          : `Idle · estimated ${formatBytes(resident.estimated_loaded_memory_bytes)}`,
      ),
    );
    row.append(
      copy,
      UI.status({
        label: leaseCount ? 'In use' : resident.state === 'resident' ? 'Loaded' : resident.state || 'Unknown',
        tone: leaseCount ? 'warning' : resident.state === 'resident' ? 'information' : 'error',
      }),
    );
    section.append(row);
  });
  return section;
}

function modelList(payload, api, signal, route, shell, feedback) {
  const list = document.createElement('div');
  list.className = 'support-list';
  (payload.models || []).forEach((item) => {
    const row = document.createElement('div');
    row.className = 'support-list-row';
    const copy = document.createElement('div');
    const ready = item.cached || item.state === 'cached';
    const required = item.model?.required_by_default === true;
    const missingRequired = Array.isArray(item.missing_required_paths)
      ? item.missing_required_paths.length : 0;
    copy.append(
      textNode('strong', '', modelLabel(item)),
      textNode(
        'p',
        'support-status-copy',
        `${required ? 'Required' : 'Optional'} · ${ready
          ? 'Pinned model files are available'
          : missingRequired
            ? `${missingRequired} required file${missingRequired === 1 ? '' : 's'} missing`
            : 'Files are missing or incomplete'}`,
      ),
    );
    if (ready) {
      row.append(copy, UI.status({ label: 'Ready', tone: 'success' }));
    } else {
      const opener = UI.button({ label: 'Download or Repair', variant: 'secondary' });
      opener.setAttribute('data-maintenance-model-action', item.model?.key || 'unknown');
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
  const residents = Array.isArray(memory.residents) ? memory.residents : [];
  const loaded = residents.length || (Array.isArray(memory.loaded_model_keys)
    ? memory.loaded_model_keys.length : 0);
  const activeJobs = Number(memory.active_jobs || 0);
  const currentOwner = ownerLabel(memory.current_owner);
  const transition = memory.current_transition;
  const operationActive = Boolean(currentOwner);
  const releaseBlocked = activeJobs > 0 || operationActive || Boolean(transition);
  section.append(UI.notice({
    tone: loaded ? 'information' : 'success',
    title: loaded ? `${loaded} model${loaded === 1 ? '' : 's'} loaded` : 'No model currently loaded',
    body: activeJobs
      ? `${activeJobs} in-flight model lease${activeJobs === 1 ? '' : 's'} prevent manual release.`
      : operationActive
        ? `${currentOwner} owns the model runtime, so manual release is unavailable.`
        : transition
          ? `A ${transition.kind || 'model'} transition is active, so manual release is unavailable.`
      : loaded
        ? 'Manual release clears runtime memory only. Cached model files remain available.'
        : 'Cached models remain available and will load when synthesis needs them.',
  }));
  const list = residencyList(memory);
  if (list) section.append(list);
  if (memory.planned_eviction) {
    const eviction = memory.planned_eviction;
    section.append(UI.notice({
      tone: eviction.status === 'completed' ? 'success'
        : eviction.status === 'blocked' || eviction.status === 'failed' ? 'error' : 'warning',
      title: `Eviction ${eviction.status || 'planned'}`,
      body: `${Number(eviction.selected_slots?.length || 0)} idle resident${Number(eviction.selected_slots?.length || 0) === 1 ? '' : 's'} selected · ${formatBytes(eviction.deficit_bytes)} pressure deficit.`,
    }));
  }
  const blockers = Array.isArray(memory.blockers) ? memory.blockers : [];
  if (blockers.length) {
    section.append(UI.notice({
      tone: 'warning',
      title: `${blockers.length} residency blocker${blockers.length === 1 ? '' : 's'}`,
      body: blockers.map((item) => `${item.component_id || item.slot_id}: ${String(item.reason || 'blocked').replaceAll('_', ' ')}`).join(' · '),
    }));
  }
  if (memory.last_release) {
    section.append(UI.notice({
      tone: memory.last_release.failures?.length ? 'error' : 'information',
      title: `Last release: ${memory.last_release.reason || 'unknown'}`,
      body: `${Number(memory.last_release.released_slots?.length || 0)} resident${Number(memory.last_release.released_slots?.length || 0) === 1 ? '' : 's'} released · ${formatBytes(memory.last_release.measured_available_bytes_recovered)} measured recovery.`,
    }));
  }
  if (!loaded) return section;
  const opener = UI.button({
    label: 'Release loaded models',
    variant: 'secondary',
    disabled: releaseBlocked,
  });
  opener.setAttribute('data-release-model-residents', '');
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
    shell,
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
