'use strict';

const EMPTY_LIBRARY = Object.freeze({ voices: [], methods: [], fingerprint: null });

export function createCastVoiceLibrary({
  api, signal, projectId, routeForCast, onStateChange,
}) {
  let controller = null;
  let state = { status: 'loading', data: EMPTY_LIBRARY, error: '' };

  function publish(nextState) {
    state = nextState;
    onStateChange?.();
  }

  function beginRequest() {
    controller?.abort('superseded');
    const current = new AbortController();
    controller = current;
    const abort = () => current.abort(signal.reason);
    if (signal.aborted) abort();
    else signal.addEventListener('abort', abort, { once: true });
    return current;
  }

  async function load() {
    const current = beginRequest();
    publish({ ...state, status: 'loading', error: '' });
    const query = new URLSearchParams();
    if (projectId) query.set('project_id', projectId);
    query.set('return_route', routeForCast());
    const response = await api.get(`/api/voice-library?${query}`, {
      signal: current.signal,
      timeout: 60000,
    });
    if (signal.aborted || current !== controller) return false;
    if (!response.ok) {
      if (response.kind !== 'canceled') {
        publish({
          ...state,
          status: 'error',
          error: 'Saved Voices could not be loaded.',
        });
      }
      return false;
    }
    publish({
      status: 'ready',
      data: response.data || EMPTY_LIBRARY,
      error: '',
    });
    return true;
  }

  function cleanup() {
    controller?.abort('cleanup');
    controller = null;
  }

  return Object.freeze({
    cleanup,
    getData: () => state.data,
    getState: () => state,
    load,
  });
}
