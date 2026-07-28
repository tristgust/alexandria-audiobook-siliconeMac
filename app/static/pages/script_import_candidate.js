'use strict';

const UI = globalThis.AlexandriaUI;

export function importCandidateState(model) {
  const lifecycle = model.lifecycle || {};
  const candidate = model.importCandidate;
  const pending = !lifecycle.artifact?.script_exists;
  return {
    candidate,
    lifecycle,
    ready: pending && candidate?.status === 'ready',
    invalid: pending && candidate?.status === 'invalid',
  };
}

export function renderImportCandidateStatus({ root, model }) {
  const state = importCandidateState(model);
  if (state.ready) {
    root.hidden = false;
    root.replaceChildren(UI.notice({
      tone: 'information',
      title: 'Imported Script is ready to apply',
      body: `${state.candidate.entry_count.toLocaleString()} entries across ${state.candidate.speaker_count.toLocaleString()} speakers are shown for review. Applying them creates the authoritative Script; approval remains a separate step.`,
    }));
    return 'Review the imported entries, then apply them to create the authoritative Script.';
  }
  if (state.invalid) {
    root.hidden = false;
    root.replaceChildren(UI.notice({
      tone: 'error',
      title: 'Imported Script is unavailable',
      body: state.candidate.message
        || 'The stored imported Script could not be reviewed.',
      live: true,
    }));
    return 'Resolve the imported Script problem before continuing.';
  }
  return null;
}

export async function applyImportCandidate({
  model, api, signal, root, report, reload, isDisposed,
}) {
  const state = importCandidateState(model);
  if (!state.ready) return false;
  root.hidden = false;
  root.replaceChildren(UI.skeleton({ label: 'Applying imported Script' }));
  const result = await api.post('/api/script_lifecycle/import-candidate/apply', {
    expected_candidate_fingerprint: state.candidate.fingerprint,
  }, { signal });
  if (isDisposed() || signal.aborted) return false;
  if (!result.ok) {
    const detail = result.data?.detail && typeof result.data.detail === 'object'
      ? result.data.detail : {};
    root.replaceChildren(UI.notice({
      tone: 'error',
      title: 'Imported Script could not be applied',
      body: detail.message || result.error,
      live: true,
    }));
    return false;
  }
  report(
    'Imported Script applied',
    'The authoritative Script is ready for final review and approval.',
    'success',
  );
  await reload();
  return true;
}

export function entriesForImportCandidate({
  authoritativeEntries, candidate, lifecycle,
}) {
  if (candidate?.status === 'ready'
    && !lifecycle?.artifact?.script_exists
    && authoritativeEntries.length === 0) {
    return candidate.entries || [];
  }
  return authoritativeEntries;
}
