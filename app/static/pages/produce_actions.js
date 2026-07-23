'use strict';

import { PRODUCE_FILTERS } from './produce_model.js';

const UI = globalThis.AlexandriaUI;

function resultMessage(result, fallback) {
  const detail = result?.data?.detail;
  return (detail && typeof detail === 'object' ? detail.message : detail)
    || result?.data?.message || result?.error || fallback;
}

export function createProduceActions({
  api, signal, toolbar, getAggregate, onRender, onReload, onFilterChange,
}) {
  const activeFilters = new Set();
  let busy = false;
  let message = null;
  let popover = null;
  let regenerateDialog = null;

  const filterCount = (value) => Number(getAggregate()?.counts?.[value]) || 0;

  const renderToolbar = () => {
    const aggregate = getAggregate() || {};
    const filters = document.createElement('div');
    filters.className = 'produce-filters';
    filters.setAttribute('aria-label', 'Filter audio chunks');
    PRODUCE_FILTERS.forEach(([value, label]) => {
      const chip = UI.filterChip({
        label: `${label} ${filterCount(value).toLocaleString()}`,
        pressed: activeFilters.has(value),
        multiple: true,
      });
      chip.dataset.produceFilter = value;
      chip.querySelector('button').addEventListener('click', () => {
        if (activeFilters.has(value)) activeFilters.delete(value);
        else activeFilters.add(value);
        renderToolbar();
        onFilterChange?.();
      });
      filters.append(chip);
    });
    const actions = document.createElement('div');
    actions.className = 'produce-toolbar__actions';
    if (filterCount('failed') > 0) actions.append(UI.button({
      label: 'Retry failed',
      variant: 'secondary',
      size: 'compact',
      attributes: { 'data-produce-action': 'retry' },
      disabled: busy || aggregate.process?.running,
      onClick: () => execute('retry_failed', [], '/api/produce/retry-failed'),
    }));
    const more = UI.iconButton({
      name: 'more', label: 'More production actions', size: 'compact',
      disabled: busy || aggregate.process?.running,
    });
    popover?.popoverCleanup?.();
    popover = UI.popover({
      opener: more,
      label: 'Produce actions',
      items: [{ label: 'Regenerate all audio', onSelect: () => regenerateDialog?.open(more) }],
    });
    regenerateDialog = UI.dialog({
      title: 'Regenerate all audio?',
      body: 'Every current audio file will be replaced only after its new audio validates against the latest Script, Cast, and direction.',
      confirmLabel: 'Regenerate all',
      destructive: true,
      onConfirm: () => execute('regenerate_all', [], '/api/produce/generate', true),
    });
    actions.append(popover);
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
    return {
      label: aggregate.primary_action.label || 'Generate missing and stale audio',
      attributes: { 'data-produce-primary': '' },
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
    primaryAction,
    matches(chunk) { return !activeFilters.size || activeFilters.has(chunk.state); },
    clearFilters() { activeFilters.clear(); renderToolbar(); onFilterChange?.(); },
    get busy() { return busy; },
    get message() { return message; },
    cleanup() {
      popover?.popoverCleanup?.();
      regenerateDialog?.forceClose?.();
    },
  });
}
