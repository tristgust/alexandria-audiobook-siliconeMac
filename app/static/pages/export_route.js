'use strict';

import { createExportActions } from './export_actions.js';
import { createExportChapters } from './export_chapters.js';
import { EXPORT_FORMATS, exportStyle, waitForExportStyle } from './export_model.js';
import {
  createExportPage, renderExportError, renderExportLoading, renderExportReadiness,
} from './export_page_view.js';
import { createExportOutput } from './export_output.js';
import { createExportPublication } from './export_publication.js';

const UI = globalThis.AlexandriaUI;

export async function mountExport({ root, route, shell, api, signal }) {
  if (!UI) throw new Error('Export requires Alexandria UI primitives.');
  const projectId = route.projectId || route.context.project || '';
  const style = exportStyle();
  const { owner, readiness, workspace } = createExportPage(root, route);
  let aggregate = null;
  let selectedFormat = 'm4b';
  let publicationView = null;
  let outputView = null;
  let disposed = false;
  let loadEpoch = 0;
  let pollTimer = null;

  const metadata = () => publicationView?.metadata()
    || aggregate?.metadata
    || { title: '', author: '', narrator: '', year: '', description: '' };
  const chapterMode = () => outputView?.controls?.chapterMode?.value
    || aggregate?.chapter_mode
    || 'smart';
  const chooseFormat = () => {
    const available = (aggregate?.formats || []).filter((value) => (
      EXPORT_FORMATS.some((format) => format.value === value && !format.disabled)
    ));
    if (available.includes(selectedFormat)) return selectedFormat;
    return available[0] || 'm4b';
  };

  const actions = createExportActions({
    shell, api, signal, route, projectId,
    getAggregate: () => aggregate,
    getMetadata: metadata,
    getSelectedFormat: () => selectedFormat,
    getChapterMode: chapterMode,
    onRender: render,
    onReload: loadExport,
  });

  const metadataReady = () => {
    const value = metadata();
    return Boolean(value.title && value.author);
  };

  function renderReadiness() {
    renderExportReadiness({
      root: readiness,
      aggregate,
      actionMessage: actions.message,
      selectedOutput: actions.selectedOutput(),
      hardBlockers: actions.hardBlockers,
      blockerAction: actions.blockerAction,
      onCancel: actions.cancel,
      metadataReady: metadataReady(),
      successAction: actions.downloadAction(),
    });
  }

  function onMetadataChange() {
    outputView?.refreshValidation();
    renderReadiness();
    actions.header();
  }

  function onFormatChange(format) {
    selectedFormat = format;
    renderWorkspace();
    renderReadiness();
    actions.header();
  }

  function renderWorkspace() {
    workspace.replaceChildren();
    publicationView = createExportPublication({
      aggregate,
      projectId,
      selectedOutput: actions.selectedOutput(),
      shell,
      api,
      signal,
      onChange: onMetadataChange,
      onRefresh: () => loadExport(false),
    });
    const chapters = createExportChapters({ aggregate, projectId, shell });
    outputView = createExportOutput({
      aggregate,
      selectedFormat,
      getMetadata: metadata,
      onFormatChange,
      onChange: actions.header,
    });
    workspace.append(publicationView.node, chapters, outputView.node);
    if (!aggregate.chapters?.length) owner.dataset.pageState = 'empty';
    else if (aggregate.process?.running) owner.dataset.pageState = 'running';
    else if (aggregate.summary?.complete) owner.dataset.pageState = 'complete';
    else if (actions.hardBlockers().length) owner.dataset.pageState = 'blocked';
    else if (aggregate.chapters.length > 20) owner.dataset.pageState = 'dense';
    else owner.dataset.pageState = 'ready';
  }

  function render() {
    selectedFormat = chooseFormat();
    renderWorkspace();
    renderReadiness();
    actions.header();
    if (aggregate.process?.running) {
      clearTimeout(pollTimer);
      pollTimer = setTimeout(() => loadExport(false), 1500);
    }
  }

  async function loadExport(showLoading = true) {
    const epoch = ++loadEpoch;
    clearTimeout(pollTimer);
    if (showLoading) renderExportLoading({ owner, readiness, workspace, shell });
    const response = await api.get('/api/export', { signal });
    if (disposed || signal.aborted || epoch !== loadEpoch) return;
    if (!response.ok) {
      if (response.kind !== 'canceled') {
        renderExportError({
          owner,
          readiness,
          workspace,
          shell,
          projectTitle: route.projectTitle || projectId || 'Project workspace',
          onRetry: loadExport,
          onTracker: actions.tracker,
          message: response.error,
        });
      }
      return;
    }
    aggregate = response.data || {};
    selectedFormat = chooseFormat();
    render();
  }

  const cleanup = () => {
    if (disposed) return;
    disposed = true;
    loadEpoch += 1;
    clearTimeout(pollTimer);
    shell.inspector.hide();
    if (style.owned) style.node.remove();
    signal.removeEventListener('abort', cleanup);
  };
  signal.addEventListener('abort', cleanup, { once: true });

  shell.player.set({ state: 'inactive', title: 'No current Export audio' });
  renderExportLoading({ owner, readiness, workspace, shell });
  actions.header();
  await waitForExportStyle(style.node, signal);
  await loadExport(false);
  return cleanup;
}
