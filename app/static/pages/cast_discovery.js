'use strict';

export function createCastDiscovery({
  api, signal, beginRequest, isDisposed, setAggregate,
  renderEmpty, renderError, renderHeader, reloadCast,
}) {
  let timer = null;
  let running = false;

  const isActive = () => !isDisposed() && !signal.aborted;

  function sync(aggregate) {
    running = aggregate?.process?.running === true;
  }

  function schedule(delay) {
    timer = window.setTimeout(poll, delay);
  }

  async function poll() {
    if (!isActive() || !running) return;
    const response = await api.get('/api/cast', { signal: beginRequest() });
    if (!isActive()) return;
    if (!response.ok) {
      running = false;
      if (response.kind !== 'canceled') {
        renderError('Character reconciliation status could not be loaded.');
      }
      renderHeader();
      return;
    }
    const aggregate = response.data || {};
    setAggregate(aggregate);
    if ((aggregate.characters || []).length) {
      running = false;
      await reloadCast();
      return;
    }
    sync(aggregate);
    renderEmpty();
    renderHeader();
    if (running) schedule(800);
  }

  async function cancel() {
    if (!running || !isActive()) return;
    const response = await api.post('/api/character_roster/cancel', {}, {
      signal: beginRequest(),
    });
    if (!isActive()) return;
    if (!response.ok) {
      renderError('Character reconciliation could not be canceled safely.');
      return;
    }
    running = false;
    await reloadCast();
  }

  async function start() {
    if (running || !isActive()) return;
    running = true;
    renderEmpty();
    renderHeader();
    const response = await api.post('/api/character_roster/discover', {}, {
      signal: beginRequest(),
    });
    if (!isActive()) return;
    if (!response.ok) {
      running = false;
      const detail = response.data?.detail;
      renderError(typeof detail === 'object' ? detail.message : detail || response.error);
      renderHeader();
      return;
    }
    if (response.data?.status === 'complete') {
      running = false;
      await reloadCast();
      return;
    }
    schedule(400);
  }

  function cleanup() {
    clearTimeout(timer);
    running = false;
  }

  return Object.freeze({
    cancel,
    cleanup,
    isRunning: () => running,
    schedule,
    start,
    sync,
  });
}
