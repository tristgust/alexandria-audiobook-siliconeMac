'use strict';

import {
  resultMessage,
  supportOwner,
  supportReturn,
  textNode,
} from '/static/pages/more.js';

const UI = globalThis.AlexandriaUI;

const STATE_LABELS = Object.freeze({
  queued: 'Queued',
  running: 'Running',
  cancelling: 'Cancelling',
  succeeded: 'Complete',
  failed: 'Failed',
  cancelled: 'Cancelled',
  stale: 'Stale',
});

function toneFor(state) {
  if (state === 'succeeded') return 'success';
  if (state === 'failed' || state === 'stale') return 'error';
  if (state === 'cancelled' || state === 'cancelling') return 'warning';
  if (state === 'running') return 'information';
  return 'neutral';
}

function domainLabel(job) {
  return String(job.metadata?.label || job.domain || 'Background operation')
    .replaceAll('_', ' ');
}

function progressLabel(job) {
  const progress = job.progress || {};
  const completed = Number(progress.completed) || 0;
  const total = Number(progress.total) || 0;
  const message = String(progress.message || '').trim();
  if (total > 0) return `${completed}/${total}${message ? ` · ${message}` : ''}`;
  return message || 'No progress details yet.';
}

function jobRow(job, { api, signal, feedback, refresh }) {
  const row = document.createElement('article');
  row.className = 'support-list-row background-work-row';
  row.dataset.backgroundJob = job.job_id;
  row.dataset.state = job.state;
  const copy = document.createElement('div');
  copy.className = 'more-tool-copy';
  copy.append(
    textNode('strong', '', domainLabel(job)),
    textNode('p', 'support-status-copy', progressLabel(job)),
  );
  const detail = [];
  if (job.operation) detail.push(String(job.operation).replaceAll('_', ' '));
  if (job.attempt_count) detail.push(`attempt ${job.attempt_count}`);
  if (job.recovery_count) detail.push(`recovered ${job.recovery_count}×`);
  if (job.terminal_reason) detail.push(String(job.terminal_reason).replaceAll('_', ' '));
  if (detail.length) copy.append(textNode('p', 'metadata', detail.join(' · ')));
  const controls = document.createElement('div');
  controls.className = 'script-workflow-actions';
  controls.append(UI.status({
    label: STATE_LABELS[job.state] || job.state,
    tone: toneFor(job.state),
  }));
  if (['queued', 'running', 'cancelling'].includes(job.state)) {
    const cancel = UI.button({
      label: job.state === 'cancelling' ? 'Cancelling…' : 'Cancel',
      variant: 'secondary',
      size: 'compact',
      disabled: job.state === 'cancelling',
      attributes: { 'data-background-work-cancel': job.job_id },
    });
    cancel.addEventListener('click', async () => {
      cancel.disabled = true;
      const result = await api.post(
        `/api/background-work/${encodeURIComponent(job.job_id)}/cancel`,
        {},
        { signal },
      );
      if (signal.aborted) return;
      feedback.replaceChildren(UI.notice({
        tone: result.ok ? 'success' : 'error',
        title: result.ok ? 'Cancellation requested' : 'Work was not cancelled',
        body: result.ok
          ? 'Alexandria will stop at the next safe cancellation boundary.'
          : resultMessage(result, 'The operation remains in its current state.'),
        live: true,
      }));
      await refresh();
    });
    controls.append(cancel);
  }
  row.append(copy, controls);
  return row;
}

export async function mount({ root, route, shell, api, signal }) {
  const { owner, stateRegion } = supportOwner(root, route, {
    shell,
    page: 'background-work',
    title: 'Background Work',
    subtitle: 'Queued, running, recovering, cancelling, and recently completed operations.',
    className: 'specialist-workspace',
  });
  owner.dataset.routeOwner = route.path;
  const toolbar = document.createElement('div');
  toolbar.className = 'support-toolbar';
  toolbar.append(supportReturn(route, shell));
  const feedback = document.createElement('div');
  feedback.setAttribute('role', 'status');
  feedback.setAttribute('aria-live', 'polite');
  let timer = 0;

  const refresh = async () => {
    const result = await api.get('/api/background-work?history_limit=20', { signal });
    if (signal.aborted) return;
    if (!result.ok) {
      owner.dataset.viewState = 'error';
      stateRegion.replaceChildren(toolbar, UI.notice({
        tone: 'error',
        title: 'Background Work could not be inspected',
        body: resultMessage(result, 'No operation was changed.'),
        live: true,
      }));
      return;
    }
    const payload = result.data;
    const active = Array.isArray(payload.active) ? payload.active : [];
    const history = Array.isArray(payload.history) ? payload.history : [];
    const content = document.createDocumentFragment();
    content.append(toolbar);
    const summary = document.createElement('section');
    summary.className = 'specialist-section';
    summary.append(
      textNode('h2', '', 'Current work'),
      UI.notice({
        tone: active.length ? 'information' : 'success',
        title: active.length
          ? `${active.length} active operation${active.length === 1 ? '' : 's'}`
          : 'No work is active',
        body: active.length
          ? `The bounded queue accepts up to ${payload.max_pending} active or queued operations and serializes incompatible resources.`
          : 'Completed and interrupted work remains available in recent history.',
      }),
    );
    const activeList = document.createElement('div');
    activeList.className = 'support-list';
    active.forEach((job) => activeList.append(jobRow(job, {
      api, signal, feedback, refresh,
    })));
    summary.append(activeList.childElementCount ? activeList : UI.emptyState({
      title: 'Nothing queued or running',
      body: 'New audio, delivery, model-cache, Voice-preparation, and Export work will appear here.',
    }));
    content.append(summary);
    const recent = document.createElement('section');
    recent.className = 'specialist-section';
    recent.append(textNode('h2', '', 'Recent history'));
    const historyList = document.createElement('div');
    historyList.className = 'support-list';
    history.forEach((job) => historyList.append(jobRow(job, {
      api, signal, feedback, refresh,
    })));
    recent.append(historyList.childElementCount ? historyList : UI.emptyState({
      title: 'No completed work recorded',
      body: 'Terminal receipts will appear after an operation completes, fails, is cancelled, or becomes stale.',
    }));
    content.append(recent, feedback);
    stateRegion.replaceChildren(content);
    owner.dataset.viewState = active.length || history.length ? 'ready' : 'empty';
  };

  await refresh();
  timer = window.setInterval(() => { void refresh(); }, 1500);
  return () => window.clearInterval(timer);
}
