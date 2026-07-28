'use strict';

import { resultMessage } from '/static/pages/more.js';

const UI = globalThis.AlexandriaUI;

export async function applyIdentityOperation({
  api, signal, payload, operation, operationPayload, feedback, route, shell,
}) {
  const result = await api.post('/api/speaker_management/action', {
    operation,
    expected_script_fingerprint: payload.script_fingerprint,
    payload: operationPayload,
  }, { signal });
  if (signal.aborted) return;
  feedback.replaceChildren(UI.notice({
    tone: result.ok ? 'success' : 'error',
    title: result.ok ? 'Identity operation applied' : 'Identity operation was rejected',
    body: result.ok
      ? 'Cast and Script were updated together. Any affected audio is now marked stale and can be regenerated.'
      : resultMessage(result, 'No changes were made.'),
    live: true,
  }));
  if (result.ok) window.setTimeout(
    () => shell.navigate(route.hash, { historyMode: 'replace' }),
    180,
  );
}
