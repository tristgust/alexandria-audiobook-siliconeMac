'use strict';

import { createTaskImportSurface } from '/static/components/task_import_surface.js';

const UI = globalThis.AlexandriaUI;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

function facts(items) {
  const list = document.createElement('dl');
  list.className = 'script-workflow-facts';
  items.forEach(([label, value]) => {
    const row = document.createElement('div');
    row.append(text('dt', '', label), text('dd', '', value || '—'));
    list.append(row);
  });
  return list;
}

async function runButton(button, label, operation) {
  const prior = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try { return await operation(); }
  finally { button.disabled = false; button.textContent = prior; }
}

function completedTaskRoute(routing = {}) {
  const destination = routing.native_destination || '';
  const tab = routing.tab || '';
  if (
    tab === 'characters'
    || ['character_roster', 'expressive_voices', 'visual_dossiers'].includes(destination)
  ) return 'cast';
  if (tab === 'editor' || destination === 'editor') return 'produce';
  return 'script';
}

export function createScriptImportWorkflows({
  api, signal, shell, projectId, onReload, report,
}) {
  let candidate = null;
  let renderImportedTask = null;
  const createCompletedTaskSection = ({ title, description, kind }) => {
    const surface = createTaskImportSurface({
      api, signal, title, description, report,
      onImported: async (imported, host, statusNode) => {
        await renderImportedTask?.(imported, host, statusNode);
      },
    });
    surface.section.classList.add('script-import-workflow');
    surface.section.dataset.taskImportKind = kind;
    return surface.section;
  };
  const completedScriptTaskSection = createCompletedTaskSection({
    title: 'Import completed Script task',
    description: 'Choose the completed Script ZIP returned by ChatGPT. Alexandria validates it, then lets you apply and review the Script before approval.',
    kind: 'script',
  });
  const completedReviewTaskSection = createCompletedTaskSection({
    title: 'Import completed Script review',
    description: 'Choose the completed review ZIP returned by ChatGPT. Alexandria validates source and Script fingerprints, then opens the corrected Script for approval.',
    kind: 'script-review',
  });
  const completedDeliveryTaskSection = createCompletedTaskSection({
    title: 'Import completed Qwen + Fish plan',
    description: 'Choose the completed delivery-plan ZIP returned by ChatGPT. Alexandria validates every chunk, text hash, and phrase anchor before attaching it to the approved Script.',
    kind: 'delivery-plan',
  });

  const importSection = document.createElement('section');
  importSection.className = 'script-import-workflow';
  importSection.append(text('h4', 'entity-title', 'Import an Alexandria Script file'));
  const importFile = UI.field({
    label: 'Script file', type: 'file', required: true,
    description: 'Alexandria Script JSON or annotated-script ZIP. Completed ChatGPT tasks belong in the stage-specific importers above.',
    attributes: { accept: '.json,.zip', 'data-script-import-file': '' },
  });
  const verify = UI.checkbox({ label: 'Verify against the selected source', checked: true });
  const inspect = UI.button({ label: 'Inspect Script file', variant: 'secondary' });
  const importStatus = text('div', 'transaction-status', 'No Script inspected.');
  const candidateHost = document.createElement('div');
  candidateHost.className = 'script-import-candidate';
  importSection.append(importFile, verify, inspect, importStatus, candidateHost);

  const renderScriptCandidate = (host, statusNode, message) => {
    statusNode.textContent = message;
    const summary = facts([
      ['Entries', String(candidate.summary?.entry_count ?? candidate.entry_count ?? '—')],
      ['Verification', candidate.provenance?.status || candidate.status],
      ['Candidate', candidate.candidate_id],
    ]);
    const apply = UI.button({
      label: 'Apply inspected Script', variant: 'secondary',
      attributes: { 'data-apply-inspected-script': '' },
    });
    apply.addEventListener('click', async () => {
      const selected = candidate;
      if (!selected?.candidate_id) return;
      const applied = await runButton(apply, 'Applying…', () => api.post(
        '/api/external/annotated-script/apply',
        {
          candidate_id: selected.candidate_id,
          checkpoint_decision: selected.consequences?.checkpoint_decision_required ? 'keep' : null,
        },
        { signal },
      ));
      if (!applied.ok) { report('Imported Script could not be applied', applied.error); return; }
      report('Imported Script applied', 'The new Script is ready for review.', 'success');
      candidate = null;
      candidateHost.replaceChildren();
      host.replaceChildren();
      await onReload();
    });
    host.replaceChildren(summary, apply);
  };

  renderImportedTask = async (imported, host, statusNode) => {
    if (imported.kind === 'annotated_script') {
      candidate = imported;
      renderScriptCandidate(
        host,
        statusNode,
        'Completed task validated. Nothing has been applied yet.',
      );
      return;
    }
    const routing = imported.routing || {};
    const deliveryPlan = imported.task_type === 'backend_render_plan_generation';
    const routePath = completedTaskRoute(routing);
    const label = deliveryPlan ? 'Return to Script'
      : routePath === 'cast' ? 'Open Cast review'
        : routePath === 'produce' ? 'Open Produce review' : 'Open Script review';
    const action = UI.button({
      label, variant: 'secondary',
      onClick: () => shell.navigate(shell.routes.routeForPath(
        routePath,
        projectId ? { project: projectId } : {},
      ).hash),
    });
    const tone = ['unsupported', 'blocked'].includes(routing.status) ? 'warning' : 'success';
    statusNode.textContent = 'Completed task validated.';
    host.replaceChildren(UI.notice({
      tone,
      title: routing.status === 'blocked'
        ? 'Review is temporarily blocked'
        : deliveryPlan ? 'Delivery plan applied' : 'Completed task imported',
      body: routing.message || (
        deliveryPlan
          ? 'The Qwen and Fish delivery plan is attached to the accepted Script. Existing audio was not regenerated.'
          : 'Alexandria routed the result to its native review workflow.'
      ),
      action,
      live: true,
    }));
  };

  inspect.addEventListener('click', async () => {
    const file = importFile.querySelector('input')?.files?.[0];
    if (!file) { importStatus.textContent = 'Choose an Alexandria Script file first.'; return; }
    const form = new FormData();
    form.append('verify_source', String(verify.querySelector('input')?.checked !== false));
    form.append('file', file);
    const result = await runButton(
      inspect,
      'Inspecting…',
      () => api.post('/api/external/annotated-script/inspect', form, { signal }),
    );
    if (!result.ok) { importStatus.textContent = result.error; return; }
    candidate = result.data;
    renderScriptCandidate(
      candidateHost,
      importStatus,
      'Inspection complete. Nothing has been applied yet.',
    );
  });

  return Object.freeze({
    completedTaskSection: completedScriptTaskSection,
    completedScriptTaskSection,
    completedReviewTaskSection,
    completedDeliveryTaskSection,
    importSection,
  });
}
