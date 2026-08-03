'use strict';

import { downloadTaskBundle } from './task_bundle_download.js';

const UI = globalThis.AlexandriaUI;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

function lastLog(state) {
  const logs = Array.isArray(state?.logs) ? state.logs : [];
  return logs.length ? String(logs[logs.length - 1]) : '';
}

export function createScriptContextualReview({ api, signal, onReload, report }) {
  const root = document.createElement('div');
  root.className = 'script-contextual-review';
  const explanation = text(
    'p',
    'metadata',
    'Optional quality pass: review locally with Alexandria’s configured model, or download the same source-bound task for ChatGPT. Approval does not require either automated review.',
  );
  const actions = document.createElement('div');
  actions.className = 'script-workflow-actions';
  const button = UI.button({ label: 'Review locally', variant: 'secondary' });
  const exportButton = UI.button({ label: 'Download review task bundle', variant: 'secondary' });
  const status = text('div', 'transaction-status', 'Review workload not loaded.');
  status.setAttribute('aria-live', 'polite');
  actions.append(button, exportButton);
  root.append(explanation, actions, status);

  let pollTimer = null;
  let reloadedForFinishedAt = null;
  let reportedForFinishedAt = null;

  const setButton = (running) => {
    button.disabled = running;
    exportButton.disabled = running;
    button.textContent = running ? 'Local review running…' : 'Review locally';
  };

  const schedulePoll = () => {
    clearTimeout(pollTimer);
    if (signal.aborted) return;
    pollTimer = setTimeout(() => { void refresh(); }, 1000);
  };

  const showEstimate = async () => {
    const result = await api.get('/api/review_script_contextual/estimate', { signal });
    if (!result.ok) {
      status.textContent = result.error === 'Not Found'
        ? 'Restart Alexandria to load the updated review service.'
        : result.error;
      return;
    }
    const estimate = result.data || {};
    const calls = Number(estimate.estimated_calls || 0);
    const entries = Number(estimate.total_entries || 0);
    const batchSize = Number(estimate.batch_size || 0);
    status.textContent = calls
      ? `Reviews ${entries.toLocaleString()} entries in batches of ${batchSize}; approximately ${calls.toLocaleString()} LLM calls.`
      : 'No Script entries are available for contextual review.';
  };

  async function refresh() {
    if (signal.aborted) return;
    const result = await api.get('/api/status/review', { signal });
    if (!result.ok) {
      setButton(false);
      status.textContent = result.error;
      return;
    }
    const state = result.data || {};
    if (state.running) {
      setButton(true);
      const progress = lastLog(state);
      status.textContent = progress
        ? `Local contextual review is running. ${progress}`
        : 'Local contextual review is running. Waiting for the first batch update…';
      schedulePoll();
      return;
    }
    if (state.started_at && !state.finished_at && state.return_code == null) {
      setButton(true);
      status.textContent = 'Local contextual review is finalizing…';
      schedulePoll();
      return;
    }

    setButton(false);
    if (state.finished_at && state.return_code === 0) {
      status.textContent = 'Local contextual review completed. Review any changes and approve the new Script version.';
      if (reloadedForFinishedAt !== state.finished_at) {
        reloadedForFinishedAt = state.finished_at;
        await onReload();
      }
      if (reportedForFinishedAt !== state.finished_at) {
        reportedForFinishedAt = state.finished_at;
        report(
          'Contextual review completed',
          'The Script was reloaded. Review any changes and approve the new version.',
          'success',
        );
      }
      return;
    }
    if (state.finished_at && state.return_code != null) {
      const failure = state.last_error || lastLog(state) || 'The review process failed.';
      status.textContent = `Local contextual review failed. ${failure}`;
      if (reportedForFinishedAt !== state.finished_at) {
        reportedForFinishedAt = state.finished_at;
        report('Contextual review failed', failure);
      }
      return;
    }
    await showEstimate();
  }

  const run = async () => {
    setButton(true);
    status.textContent = 'Starting local contextual review…';
    const result = await api.post(
      '/api/review_script_contextual',
      { window_size: 4 },
      { signal },
    );
    if (!result.ok) {
      setButton(false);
      status.textContent = result.error;
      report('Contextual review could not start', result.error);
      return;
    }
    const workload = result.data || {};
    const calls = Number(workload.estimated_calls || 0);
    status.textContent = calls
      ? `Local review started; approximately ${calls.toLocaleString()} LLM calls are queued.`
      : 'Local review started.';
    report(
      'Local contextual review started',
      calls
        ? `Alexandria queued approximately ${calls.toLocaleString()} calls to the configured local review model.`
        : 'Alexandria queued the local review process.',
      'information',
    );
    schedulePoll();
  };

  const exportReview = () => downloadTaskBundle({
    api,
    signal,
    button: exportButton,
    taskType: 'script_review',
    onError: (error) => report('Review task could not be downloaded', error),
    onDownloaded: () => {
      status.textContent = 'Review task bundle downloaded. Import the completed ZIP below when ChatGPT returns it.';
      report(
        'Review task bundle downloaded',
        'Attach the ZIP to ChatGPT, then import the completed ZIP below.',
        'success',
      );
    },
  });

  const onClick = () => { void run(); };
  const onExport = () => { void exportReview(); };
  button.addEventListener('click', onClick);
  exportButton.addEventListener('click', onExport);
  signal.addEventListener('abort', () => clearTimeout(pollTimer), { once: true });

  return Object.freeze({
    root,
    refresh,
    cleanup() {
      clearTimeout(pollTimer);
      button.removeEventListener('click', onClick);
      exportButton.removeEventListener('click', onExport);
    },
  });
}
