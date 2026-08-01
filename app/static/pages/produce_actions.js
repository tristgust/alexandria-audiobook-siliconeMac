'use strict';

import { PRODUCE_FILTERS } from './produce_model.js';

const UI = globalThis.AlexandriaUI;

function resultMessage(result, fallback) {
  const detail = result?.data?.detail;
  return (detail && typeof detail === 'object' ? detail.message : detail)
    || result?.data?.message || result?.error || fallback;
}

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes.toLocaleString()} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let amount = bytes / 1024;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount >= 10 ? amount.toFixed(1) : amount.toFixed(2)} ${units[index]}`;
}

export function createProduceActions({
  api, signal, toolbar, getAggregate, onRender, onReload, onFilterChange,
}) {
  const activeFilters = new Set();
  let busy = false;
  let message = null;
  let query = '';
  let popover = null;
  let regenerateDialog = null;
  let takeDialog = null;
  let cleanupDialog = null;

  const filterCount = (value) => Number(getAggregate()?.counts?.[value]) || 0;

  const renderToolbar = () => {
    const aggregate = getAggregate() || {};
    const filters = document.createElement('div');
    filters.className = 'produce-filters';
    filters.setAttribute('aria-label', 'Filter audio chunks');
    PRODUCE_FILTERS.forEach(([value, label]) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'produce-filter';
      button.dataset.produceFilter = value;
      button.setAttribute('aria-pressed', String(activeFilters.has(value)));
      button.append(document.createTextNode(label));
      const count = document.createElement('span');
      count.textContent = filterCount(value).toLocaleString();
      button.append(count);
      button.addEventListener('click', () => {
        if (activeFilters.has(value)) activeFilters.delete(value);
        else activeFilters.add(value);
        renderToolbar();
        onFilterChange?.();
      });
      filters.append(button);
    });
    const actions = document.createElement('div');
    actions.className = 'produce-toolbar__actions';
    const search = UI.searchField({
      label: 'Search audio chunks', placeholder: 'Search audio…',
      iconClass: 'fas fa-magnifying-glass',
    });
    search.classList.add('produce-search');
    const searchInput = search.querySelector('input');
    searchInput.value = query;
    searchInput.addEventListener('input', () => {
      query = searchInput.value;
      onFilterChange?.();
    });
    const more = UI.iconButton({
      iconClass: 'fas fa-ellipsis', label: 'More production actions', size: 'compact',
      disabled: busy || aggregate.process?.running,
    });
    popover?.popoverCleanup?.();
    const menuItems = [];
    if (filterCount('ready') > 0) menuItems.push({
      label: 'Generate ready audio',
      attributes: { 'data-produce-action': 'generate-ready' },
      disabled: busy || aggregate.process?.running,
      onSelect: () => execute('ready_only'),
    });
    if (filterCount('failed') > 0) menuItems.push({
      label: 'Retry failed audio',
      attributes: { 'data-produce-action': 'retry' },
      disabled: busy || aggregate.process?.running,
      onSelect: () => execute('retry_failed', [], '/api/produce/retry-failed'),
    });
    menuItems.push({ label: 'Regenerate all audio…', onSelect: () => regenerateDialog?.open(more) });
    menuItems.push({
      label: 'Clean up old takes…',
      attributes: { 'data-produce-action': 'cleanup-takes' },
      disabled: busy || aggregate.process?.running,
      onSelect: () => reviewTakeCleanup(more),
    });
    popover = UI.popover({ opener: more, label: 'Produce actions', items: menuItems });
    regenerateDialog = UI.dialog({
      title: 'Regenerate all audio?',
      body: 'Every current audio file will be replaced only after its new audio validates against the latest Script, Cast, and direction.',
      confirmLabel: 'Regenerate all',
      destructive: true,
      onConfirm: () => execute('regenerate_all', [], '/api/produce/generate', true),
    });
    actions.append(search, popover);
    toolbar.replaceChildren(filters, actions);
  };

  async function execute(mode, selectedChunkIds = [], endpoint = '/api/produce/generate', confirm = false) {
    if (busy || signal.aborted) return;
    busy = true;
    message = null;
    onRender?.();
    const planResponse = await api.post('/api/produce/plan', {
      mode,
      selected_chunk_ids: selectedChunkIds,
    }, { signal });
    if (signal.aborted) return;
    if (!planResponse.ok) {
      busy = false;
      message = { tone: 'error', title: 'Audio plan unavailable', body: resultMessage(planResponse, 'The audio plan could not be created.') };
      onRender?.();
      return;
    }
    const plan = planResponse.data || {};
    if (!plan.safe_to_execute) {
      busy = false;
      message = {
        tone: 'warning', title: 'Generation is blocked',
        body: plan.empty_reason || plan.blockers?.[0]?.explanation
          || 'Resolve the listed blockers before generating audio.',
      };
      onRender?.();
      return;
    }
    const executeResponse = await api.post(endpoint, {
      mode,
      selected_chunk_ids: selectedChunkIds,
      plan_fingerprint: plan.plan_fingerprint,
      chunks_fingerprint: plan.chunks_fingerprint,
      confirm_regenerate_all: confirm,
    }, { signal });
    if (signal.aborted) return;
    busy = false;
    message = executeResponse.ok
      ? { tone: 'success', title: 'Audio generation started', body: 'Alexandria accepted the reviewed generation plan.' }
      : { tone: 'error', title: 'Audio generation did not start', body: resultMessage(executeResponse, 'The generation request failed.') };
    await onReload?.(false);
  }

  async function cancel() {
    if (busy || signal.aborted) return;
    busy = true;
    onRender?.();
    const response = await api.post('/api/produce/cancel', {}, { signal });
    if (signal.aborted) return;
    busy = false;
    message = response.ok
      ? { tone: 'information', title: 'Cancellation requested', body: 'Running audio work will stop at the next safe boundary. Completed chunks remain Current.' }
      : { tone: 'error', title: 'Could not cancel generation', body: resultMessage(response, 'The cancellation request failed.') };
    await onReload?.(false);
  }

  async function mutateTake({
    path, body, method = 'POST', successTitle, successBody, undoLabel = null,
  }) {
    if (busy || signal.aborted) return null;
    busy = true;
    message = null;
    onRender?.();
    const response = method === 'DELETE'
      ? await api.request(path, { method: 'DELETE', body, signal })
      : await api.post(path, body, { signal });
    if (signal.aborted) return null;
    busy = false;
    if (!response.ok) {
      message = {
        tone: 'error',
        title: 'Take action failed',
        body: resultMessage(response, 'Alexandria could not update this Take.'),
      };
      onRender?.();
      return null;
    }
    const data = response.data || {};
    message = {
      tone: 'success',
      title: successTitle,
      body: successBody,
      ...(undoLabel && data.operation_id && data.registry_fingerprint ? {
        action: UI.button({
          label: undoLabel,
          variant: 'secondary',
          size: 'compact',
          onClick: () => undoTakeOperation(data.operation_id, data.registry_fingerprint),
        }),
      } : {}),
    };
    await onReload?.(false);
    return data;
  }

  async function useTake(chunk, take) {
    return mutateTake({
      path: `/api/produce/chunks/${encodeURIComponent(chunk.chunk_id)}/takes/use`,
      body: {
        take_id: take.take_id,
        registry_fingerprint: take.registry_fingerprint,
        record_fingerprint: take.record_fingerprint,
      },
      successTitle: 'Current Take changed',
      successBody: 'Export and playback now use the selected Take. No prior Take was deleted.',
      undoLabel: 'Undo selection',
    });
  }

  async function toggleTakeKeep(chunk, take) {
    return mutateTake({
      path: `/api/produce/chunks/${encodeURIComponent(chunk.chunk_id)}/takes/keep`,
      body: {
        take_id: take.take_id,
        registry_fingerprint: take.registry_fingerprint,
        record_fingerprint: take.record_fingerprint,
        kept: !take.kept,
      },
      successTitle: take.kept ? 'Keep removed' : 'Take protected',
      successBody: take.kept
        ? 'This Take may be eligible for reviewed cleanup when it is no longer current or referenced.'
        : 'This Take and its source lineage are protected from cleanup.',
    });
  }

  async function reviewTakeDelete(chunk, take, opener) {
    if (busy || signal.aborted) return;
    busy = true;
    message = null;
    onRender?.();
    const impactResponse = await api.get(
      `/api/produce/chunks/${encodeURIComponent(chunk.chunk_id)}/takes/${encodeURIComponent(take.take_id)}/delete-impact`,
      { signal },
    );
    if (signal.aborted) return;
    busy = false;
    if (!impactResponse.ok) {
      message = {
        tone: 'error', title: 'Delete impact unavailable',
        body: resultMessage(impactResponse, 'Alexandria could not review this Take deletion.'),
      };
      onRender?.();
      return;
    }
    const impact = impactResponse.data || {};
    if (!impact.safe_to_delete) {
      message = {
        tone: 'warning',
        title: 'Take is protected',
        body: impact.blockers?.map((item) => item.message).filter(Boolean).join(' ') || 'This Take cannot be deleted.',
      };
      onRender?.();
      return;
    }
    takeDialog?.forceClose?.();
    takeDialog = UI.dialog({
      title: 'Delete this Take?',
      body: `This removes one non-current Take (${formatBytes(impact.size_bytes)}). Current, kept, referenced, and source-lineage Takes are never eligible.`,
      confirmLabel: 'Delete Take',
      destructive: true,
      onConfirm: () => mutateTake({
        path: `/api/produce/chunks/${encodeURIComponent(chunk.chunk_id)}/takes/${encodeURIComponent(take.take_id)}`,
        method: 'DELETE',
        body: {
          take_id: take.take_id,
          impact_fingerprint: impact.impact_fingerprint,
        },
        successTitle: 'Take deleted',
        successBody: 'The Take moved to rollback storage and can be restored with Undo.',
        undoLabel: 'Undo deletion',
      }),
    });
    takeDialog.open(opener);
    onRender?.();
  }

  async function reviewTakeCleanup(opener) {
    if (busy || signal.aborted) return;
    busy = true;
    message = null;
    onRender?.();
    const policy = { older_than_days: 30, reclaim_at_least_bytes: 0 };
    const impactResponse = await api.post('/api/produce/takes/cleanup-impact', policy, { signal });
    if (signal.aborted) return;
    busy = false;
    if (!impactResponse.ok) {
      message = {
        tone: 'error', title: 'Cleanup impact unavailable',
        body: resultMessage(impactResponse, 'Alexandria could not review old Takes.'),
      };
      onRender?.();
      return;
    }
    const impact = impactResponse.data || {};
    if (!Number(impact.candidate_count)) {
      message = {
        tone: 'information',
        title: 'No old Takes are eligible',
        body: 'Current, kept, referenced, active-job, receipt, and source-lineage Takes remain protected.',
      };
      onRender?.();
      return;
    }
    cleanupDialog?.forceClose?.();
    cleanupDialog = UI.dialog({
      title: 'Clean up old Takes?',
      body: `${Number(impact.candidate_count).toLocaleString()} eligible Takes older than 30 days would reclaim ${formatBytes(impact.reclaimable_bytes)}. Protected Takes and all referenced artifacts are excluded.`,
      confirmLabel: 'Clean up old Takes',
      destructive: true,
      onConfirm: () => mutateTake({
        path: '/api/produce/takes/cleanup',
        body: {
          ...policy,
          impact_fingerprint: impact.impact_fingerprint,
        },
        successTitle: 'Old Takes cleaned up',
        successBody: `${Number(impact.candidate_count).toLocaleString()} eligible Takes moved to rollback storage.`,
        undoLabel: 'Undo cleanup',
      }),
    });
    cleanupDialog.open(opener);
    onRender?.();
  }

  async function undoTakeOperation(operationId, registryFingerprint) {
    return mutateTake({
      path: '/api/produce/takes/undo',
      body: {
        operation_id: operationId,
        registry_fingerprint: registryFingerprint,
      },
      successTitle: 'Take change undone',
      successBody: 'The prior Take registry and exact audio bytes were restored.',
    });
  }

  const primaryAction = (goToExport) => {
    const aggregate = getAggregate() || {};
    if (aggregate.summary?.complete) return {
      label: 'Continue to Export',
      attributes: { 'data-produce-primary': '' },
      onClick: goToExport,
    };
    if (aggregate.process?.running) return null;
    const eligible = filterCount('ready') + filterCount('stale');
    if (!aggregate.primary_action || eligible <= 0) return null;
    const label = aggregate.primary_action.label || 'Generate missing and stale audio';
    return {
      label,
      attributes: {
        'data-produce-primary': '',
        'data-narrow-label': 'Generate audio',
        'aria-label': label,
      },
      disabled: busy,
      description: '',
      state: busy ? 'loading' : 'default',
      onClick: () => execute('missing_stale'),
    };
  };

  return Object.freeze({
    renderToolbar,
    execute,
    cancel,
    useTake,
    toggleTakeKeep,
    reviewTakeDelete,
    reviewTakeCleanup,
    undoTakeOperation,
    primaryAction,
    matches(chunk) {
      const filterMatch = !activeFilters.size || activeFilters.has(chunk.state);
      const needle = query.trim().toLocaleLowerCase();
      if (!filterMatch || !needle) return filterMatch;
      const haystack = [
        chunk.character_name, chunk.speaker, chunk.text, chunk.text_excerpt,
        chunk.delivery_direction, chunk.state,
      ].filter(Boolean).join(' ').toLocaleLowerCase();
      return haystack.includes(needle);
    },
    clearFilters() { activeFilters.clear(); query = ''; renderToolbar(); onFilterChange?.(); },
    get busy() { return busy; },
    get message() { return message; },
    cleanup() {
      popover?.popoverCleanup?.();
      regenerateDialog?.forceClose?.();
      takeDialog?.forceClose?.();
      cleanupDialog?.forceClose?.();
    },
  });
}
