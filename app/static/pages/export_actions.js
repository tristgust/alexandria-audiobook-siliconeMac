'use strict';

import { exportDisplayFilename, exportWords } from './export_model.js';

const UI = globalThis.AlexandriaUI;

export function createExportActions({
  shell, api, signal, route, projectId,
  getAggregate, getMetadata, getSelectedFormat, getChapterMode,
  onRender, onReload,
}) {
  let busy = false;
  let message = null;

  const selectedOutput = () => {
    const aggregate = getAggregate();
    const format = getSelectedFormat();
    return aggregate?.outputs?.[format]
      || aggregate?.selected_outputs?.find((output) => output.format === format)
      || aggregate?.selected_outputs?.[0]
      || null;
  };

  const downloadAction = () => {
    const output = selectedOutput();
    if (!output?.download_url) return null;
    const link = document.createElement('a');
    link.className = 'ui-button ui-button--secondary';
    link.href = output.download_url;
    link.download = exportDisplayFilename(output.filename);
    link.textContent = 'Download audiobook';
    link.dataset.exportDownload = '';
    return link;
  };

  const hardBlockers = () => (getAggregate()?.blockers || [])
    .filter((blocker) => blocker.code !== 'export_metadata_missing');

  const canBuild = () => {
    const aggregate = getAggregate();
    const metadata = getMetadata();
    return !aggregate?.process?.running
      && hardBlockers().length === 0
      && Boolean(metadata.title && metadata.author)
      && Boolean(aggregate?.chapters?.length);
  };

  const tracker = () => {
    const aggregate = getAggregate();
    shell.tracker.set({
      script: 'complete',
      cast: 'complete',
      produce: 'complete',
      export: aggregate?.summary?.complete ? 'complete' : 'current',
    });
  };

  const header = () => {
    const aggregate = getAggregate();
    const running = Boolean(aggregate?.process?.running);
    const complete = Boolean(aggregate?.summary?.complete);
    const metadata = getMetadata();
    const metadataMissing = !metadata.title || !metadata.author;
    const requirementCount = hardBlockers().length + (metadataMissing ? 1 : 0);
    shell.header.set({
      projectTitle: route.projectTitle || projectId || 'Project workspace',
      save: { state: 'saved', label: 'Saved' },
      status: {
        tone: running ? 'information' : complete ? 'success' : canBuild()
          ? 'success' : requirementCount ? 'warning' : 'information',
        label: running ? 'Building audiobook…' : complete ? 'Built' : canBuild()
          ? 'Ready to build'
          : 'Blocked',
      },
      primaryAction: null,
    });
    tracker();
  };

  const blockerAction = (blocker) => {
    if (!blocker.native_destination) return null;
    return UI.button({
      label: `Open ${exportWords(blocker.native_destination)}`,
      variant: 'secondary',
      size: 'compact',
      onClick: () => shell.navigate(shell.routes.routeForPath(
        blocker.native_destination,
        {
          ...(projectId ? { project: projectId } : {}),
          source: blocker.target_id || 'export:preflight',
        },
      ).hash),
    });
  };

  async function build() {
    if (busy || !canBuild() || signal.aborted) return;
    busy = true;
    message = null;
    header();
    const request = {
      metadata: getMetadata(),
      formats: [getSelectedFormat()],
      chapter_mode: getChapterMode(),
    };
    const planResponse = await api.post('/api/export/plan', request, { signal });
    if (signal.aborted) return;
    if (!planResponse.ok) {
      busy = false;
      message = { tone: 'error', title: 'Final preflight failed', body: planResponse.error };
      onRender();
      return;
    }
    const plan = planResponse.data || {};
    if (!plan.safe_to_execute) {
      busy = false;
      message = {
        tone: 'warning',
        title: 'Export is blocked',
        body: plan.blockers?.[0]?.explanation || 'Resolve final preflight blockers before building.',
      };
      onRender();
      return;
    }
    const buildResponse = await api.post('/api/export/build', {
      ...request,
      plan_fingerprint: plan.plan_fingerprint,
      dependency_fingerprint: plan.dependency_fingerprint,
    }, { signal });
    if (signal.aborted) return;
    busy = false;
    if (!buildResponse.ok) {
      message = {
        tone: 'error',
        title: 'Audiobook could not be built',
        body: `${buildResponse.error}. Existing generated audio and settings are unchanged.`,
      };
      onRender();
      return;
    }
    message = {
      tone: 'success',
      title: 'Build started',
      body: 'Alexandria accepted the reviewed Export plan.',
    };
    await onReload(false);
  }

  async function cancel() {
    if (busy || signal.aborted) return;
    busy = true;
    header();
    const response = await api.post('/api/export/cancel', {}, { signal });
    if (signal.aborted) return;
    busy = false;
    message = response.ok
      ? {
        tone: 'information',
        title: 'Cancellation requested',
        body: 'The Export build will stop at the next safe boundary.',
      }
      : { tone: 'error', title: 'Could not cancel Export', body: response.error };
    await onReload(false);
  }

  return Object.freeze({
    header,
    tracker,
    canBuild,
    hardBlockers,
    selectedOutput,
    downloadAction,
    blockerAction,
    build,
    cancel,
    get busy() { return busy; },
    get message() { return message; },
  });
}
