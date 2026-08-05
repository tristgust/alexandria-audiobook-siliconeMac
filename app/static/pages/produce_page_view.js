'use strict';

import { produceText } from './produce_model.js';

const UI = globalThis.AlexandriaUI;

export function createProducePage(root, route) {
  const owner = document.createElement('article');
  owner.className = 'produce-page';
  owner.dataset.routeOwner = route.path;
  owner.dataset.producePage = '';
  owner.dataset.pageState = 'loading';
  const title = UI.pageTitleBlock({
    title: 'Produce',
    subtitle: 'Generate, review, and repair production audio across the accepted Script.',
  });
  title.querySelector('h1').dataset.pageHeading = '';
  title.querySelector('.page-subtitle').dataset.producePageSubtitle = '';
  const activity = document.createElement('section');
  activity.className = 'produce-activity';
  activity.setAttribute('aria-label', 'Production activity');
  const toolbar = document.createElement('div');
  toolbar.className = 'produce-toolbar';
  const content = document.createElement('section');
  content.className = 'produce-content';
  content.setAttribute('aria-label', 'Audio chunks');
  const main = document.createElement('div');
  main.className = 'produce-main';
  const groupHeader = document.createElement('header');
  groupHeader.className = 'produce-group-header';
  const groupHeading = document.createElement('div');
  groupHeading.append(
    produceText('span', 'utility-heading', 'Production sequence'),
    produceText('h2', 'entity-title', 'Entire Script'),
  );
  const visibleSummary = produceText('span', 'metadata', '');
  visibleSummary.dataset.produceVisibleSummary = '';
  groupHeader.append(groupHeading, visibleSummary);
  main.append(groupHeader, content);
  const layout = document.createElement('div');
  layout.className = 'produce-layout';
  layout.append(main);
  owner.append(title, toolbar, activity, layout);
  root.replaceChildren(owner);
  return { owner, title, activity, toolbar, layout, main, content, visibleSummary };
}

export function updateProduceSubtitle(owner, aggregate) {
  const summary = aggregate?.summary || {};
  const counts = aggregate?.counts || {};
  const total = Number(summary.required_chunk_count || aggregate?.all_chunk_count || aggregate?.chunks?.length) || 0;
  const current = Number(summary.current_count ?? counts.current) || 0;
  const needsGeneration = Number(summary.needs_generation_count)
    || (Number(counts.ready) || 0) + (Number(counts.stale) || 0);
  const needsListening = Number(summary.needs_review_count)
    || (Number(counts.needs_listening) || 0) + (Number(counts.needs_review) || 0);
  const failed = Number(summary.failed_count ?? counts.failed) || 0;
  const missingVoices = Number(summary.missing_voice_count ?? counts.missing_voice) || 0;
  const subtitle = owner.querySelector('[data-produce-page-subtitle]');
  const workload = [
    `${current.toLocaleString()} current`,
    needsGeneration ? `${needsGeneration.toLocaleString()} need generation` : '',
    needsListening ? `${needsListening.toLocaleString()} need listening` : '',
    failed ? `${failed.toLocaleString()} failed` : '',
    missingVoices ? `${missingVoices.toLocaleString()} blocked by Cast` : '',
  ].filter(Boolean).join(' · ');
  if (subtitle) subtitle.textContent = aggregate?.process?.running
    ? `${total.toLocaleString()} audio chunks — generation is active.`
    : summary.complete
      ? `${total.toLocaleString()} audio chunks — production is complete.`
      : `${total.toLocaleString()} audio chunks — ${workload}.`;
}

export function renderProduceLoading({ owner, activity, toolbar, content, inspector }) {
  owner.dataset.pageState = 'loading';
  activity.replaceChildren();
  toolbar.replaceChildren(UI.skeleton({ kind: 'field', label: 'Loading audio filters' }));
  content.replaceChildren(
    UI.loadingState({ label: 'Loading production audio', detail: 'Reading chunk state and current audio bindings.' }),
    UI.skeleton({ kind: 'row', label: 'Loading audio chunk' }),
    UI.skeleton({ kind: 'row', label: 'Loading audio chunk' }),
    UI.skeleton({ kind: 'row', label: 'Loading audio chunk' }),
  );
  const inspectorLoading = document.createElement('div');
  inspectorLoading.className = 'produce-inspector-loading';
  inspectorLoading.append(UI.loadingState({
    size: 'compact',
    label: 'Loading chunk details',
    detail: 'Reading audio and Take history.',
  }));
  inspector.setContent(inspectorLoading);
  inspector.close({ restoreFocus: false });
}

export function renderProduceError({ owner, activity, toolbar, content, inspector, retry, message }) {
  owner.dataset.pageState = 'error';
  activity.replaceChildren();
  toolbar.replaceChildren();
  content.replaceChildren(UI.notice({
    tone: 'error',
    title: 'Produce unavailable',
    body: message || 'Alexandria could not load production status.',
    live: true,
    action: UI.button({ label: 'Retry', variant: 'secondary', onClick: retry }),
  }));
  inspector.setContent(UI.emptyState({
    title: 'No chunk selected',
    body: 'Production details are unavailable until the audio list loads.',
  }));
  inspector.close({ restoreFocus: false });
}

export function renderProduceActivity({ activity, aggregate, actionMessage, onCancel }) {
  activity.replaceChildren();
  if (actionMessage) activity.append(UI.notice({
    tone: actionMessage.tone,
    title: actionMessage.title,
    body: actionMessage.body,
    live: true,
    action: actionMessage.action || null,
  }));
  const process = aggregate?.process || {};
  if (!process.running) return;
  const total = Number(process.total_count) || 0;
  const completed = Number(process.completed_count) || 0;
  const failed = Number(process.failed_count) || 0;
  const cancelled = Number(process.cancelled_count) || 0;
  const terminal = Number(process.terminal_count)
    || Math.min(total, completed + failed + cancelled);
  const activeFractions = Object.values(process.active_file_fractions || {})
    .map(Number).filter(Number.isFinite);
  const activeCount = Number(process.active_file_count) || activeFractions.length;
  const compositePercent = Number.isFinite(Number(process.composite_percent))
    ? Math.max(0, Math.min(100, Number(process.composite_percent)))
    : total ? Math.max(0, Math.min(100, ((terminal
      + activeFractions.reduce((sum, value) => sum + Math.max(0, Math.min(1, value)), 0))
      / total) * 100)) : 0;
  const activeDetail = activeCount === 1 && activeFractions.length === 1
    ? ` · current file ${Math.round(activeFractions[0] * 100)}%`
    : activeCount ? ` · ${activeCount.toLocaleString()} active` : '';
  const terminalDetail = [
    `${terminal.toLocaleString()} of ${total.toLocaleString()} terminal`,
    completed ? `${completed.toLocaleString()} generated` : '',
    failed ? `${failed.toLocaleString()} failed` : '',
    cancelled ? `${cancelled.toLocaleString()} cancelled` : '',
  ].filter(Boolean).join(' · ');
  const banner = document.createElement('section');
  banner.className = 'produce-progress-banner';
  banner.setAttribute('aria-label', 'Audio generation progress');
  const copy = document.createElement('div');
  copy.className = 'produce-progress-banner__copy';
  copy.append(
    produceText('strong', '', process.cancel_requested ? 'Cancelling audio…' : 'Generating audio…'),
    produceText('span', 'metadata', total
      ? `${terminalDetail}${activeDetail}`
      : 'Preparing the generation queue.'),
  );
  const progress = UI.progress({
    label: 'Audio generation',
    state: total ? 'running' : 'indeterminate',
    value: total ? compositePercent : 0,
    message: total
      ? `${terminal} of ${total} files are terminal; composite progress ${Math.round(compositePercent)} percent.`
      : 'Preparing audio generation.',
  });
  progress.dataset.produceCompositeProgress = '';
  const cancel = UI.button({
    label: process.cancel_requested ? 'Cancelling…' : 'Cancel',
    variant: 'secondary',
    disabled: process.cancel_requested,
    attributes: { 'data-produce-primary': '', 'data-produce-cancel': '' },
    onClick: onCancel,
  });
  banner.append(copy, progress, cancel);
  activity.append(banner);
}
