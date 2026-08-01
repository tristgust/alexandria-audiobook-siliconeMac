'use strict';

import { downloadTaskBundle } from './task_bundle_download.js';

const UI = globalThis.AlexandriaUI;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

async function runButton(button, label, operation) {
  const prior = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try { return await operation(); }
  finally { button.disabled = false; button.textContent = prior; }
}

export function createScriptDeliveryPlan({ api, signal, report }) {
  const root = document.createElement('section');
  root.className = 'script-workflow-panel script-delivery-plan';
  const heading = text(
    'h2',
    'section-title',
    'Add Qwen and Fish delivery directions',
  );
  heading.tabIndex = -1;
  const state = text('div', 'transaction-status', 'Delivery-plan status not loaded.');
  const actions = document.createElement('div');
  actions.className = 'script-workflow-actions';
  const generate = UI.button({
    label: 'Generate delivery plan locally',
    variant: 'secondary',
    attributes: { 'data-backend-render-plan-local': '' },
  });
  const exportTask = UI.button({
    label: 'Download Qwen + Fish task bundle',
    variant: 'secondary',
    attributes: { 'data-backend-render-plan-export': '' },
  });
  const cancel = UI.button({
    label: 'Cancel planning',
    variant: 'quiet',
    attributes: { 'data-backend-render-plan-cancel': '' },
  });
  cancel.hidden = true;
  actions.append(generate, exportTask, cancel);
  root.append(
    heading,
    text(
      'p',
      'metadata',
      'Generate locally or download a task bundle for ChatGPT. The plan is attached to the approved Script; it never rewrites spoken text or regenerates existing audio.',
    ),
    state,
    actions,
  );

  const refresh = async () => {
    const response = await api.get('/api/backend_render_plan/status', { signal });
    if (!response.ok) {
      state.textContent = response.error;
      generate.disabled = true;
      exportTask.disabled = true;
      cancel.hidden = true;
      return;
    }
    const status = response.data || {};
    const process = status.process || {};
    if (process.running) {
      const last = (process.logs || []).at(-1);
      state.textContent = last
        ? `Planning is running. ${last}`
        : 'Planning is running in resumable batches.';
    } else if (status.current) {
      const cues = Number(status.fish_inline_cue_count) || 0;
      const planned = Number(status.chunk_count) || 0;
      const applied = Number(status.applied_to_audio_count) || 0;
      state.textContent = `${planned.toLocaleString()} chunks planned; ${cues.toLocaleString()} inline Fish cues. ${applied.toLocaleString()} recordings have been regenerated under this plan.`;
    } else if (status.state === 'stale') {
      state.textContent = 'The accepted Script or synthesis chunk structure changed. Create a replacement delivery plan.';
    } else if (!status.available) {
      state.textContent = 'Approve the current Script to unlock delivery planning. Contextual review is optional.';
    } else {
      state.textContent = 'No model-specific delivery plan exists yet.';
    }
    generate.disabled = process.running || !status.available || status.current;
    exportTask.disabled = process.running || !status.available || status.current;
    const lockedReason = !status.available
      ? 'Approve the current Script to unlock this action. Contextual review is optional.'
      : '';
    generate.title = lockedReason;
    exportTask.title = lockedReason;
    cancel.hidden = !process.running;
  };

  generate.addEventListener('click', async () => {
    const response = await runButton(
      generate,
      'Starting planner…',
      () => api.post('/api/backend_render_plan/generate', {}, { signal }),
    );
    if (!response.ok) {
      report('Delivery planning could not start', response.error);
    } else if (response.data?.status === 'current') {
      report('Delivery plan is already current', 'No planning work was needed.', 'information');
    } else {
      report(
        'Delivery planning started',
        'Alexandria is creating the Qwen and Fish plans in resumable local batches.',
        'information',
      );
    }
    await refresh();
  });

  exportTask.addEventListener('click', () => {
    void downloadTaskBundle({
      api,
      signal,
      button: exportTask,
      taskType: 'backend_render_plan_generation',
      onError: (error) => report('Qwen + Fish task could not be downloaded', error),
      onDownloaded: () => report(
        'Qwen + Fish task bundle downloaded',
        'Attach the ZIP to ChatGPT, then import the completed plan below.',
        'success',
      ),
    });
  });

  cancel.addEventListener('click', async () => {
    const response = await api.post('/api/backend_render_plan/cancel', {}, { signal });
    if (!response.ok) report('Delivery planning could not be cancelled', response.error);
    await refresh();
  });

  return Object.freeze({
    root,
    refresh,
    focus() {
      root.scrollIntoView({ block: 'nearest' });
      heading.focus({ preventScroll: true });
    },
  });
}
