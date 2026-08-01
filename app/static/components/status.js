'use strict';

(() => {
  const UI = globalThis.AlexandriaUI ||= {};
  const mark = (node, primitive, factory) => {
    node.dataset.primitive = primitive;
    node.dataset.productionFactory = factory;
    return node;
  };
  const textNode = (tag, className, text) => {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  };

  UI.stageTracker = function stageTracker(options = {}) {
    const stages = options.stages || [
      { label: 'Project', state: 'complete' }, { label: 'Cast', state: 'current' },
      { label: 'Produce', state: 'future' }, { label: 'Export', state: 'blocked' },
    ];
    const root = mark(document.createElement('ol'), 'stage-tracker', 'stageTracker');
    root.className = 'stage-tracker';
    root.setAttribute('aria-label', options.label || 'Project stages');
    stages.forEach((stage, index) => {
      const state = stage.state || 'future';
      const step = document.createElement('li');
      step.className = 'stage-step';
      step.dataset.state = state;
      step.setAttribute('aria-label', `${stage.label}, ${state}`);
      if (state === 'current') step.setAttribute('aria-current', 'step');
      if (index < stages.length - 1) {
        const line = document.createElement('span');
        line.className = 'stage-tracker__line';
        line.setAttribute('aria-hidden', 'true');
        step.append(line);
      }
      const marker = document.createElement('span');
      marker.className = 'stage-step__marker';
      if (state === 'complete') marker.append(UI.icon('check'));
      else if (state === 'blocked') marker.textContent = '!';
      step.append(marker, textNode('span', 'stage-step__label', stage.label));
      root.append(step);
    });
    return root;
  };

  UI.status = function status(options = {}) {
    const tone = options.tone || 'neutral';
    const node = mark(document.createElement('span'), 'status', 'status');
    node.className = 'status-indicator';
    node.dataset.tone = tone;
    if (options.domain) node.dataset.statusDomain = options.domain;
    if (options.value) node.dataset.statusValue = options.value;
    const icon = document.createElement('span');
    icon.className = 'status-indicator__icon';
    icon.append(UI.icon(
      tone === 'success' ? 'check'
        : tone === 'warning' ? 'warning'
          : tone === 'error' ? 'error'
            : tone === 'information' ? 'info'
              : 'current',
    ));
    node.append(icon, document.createTextNode(options.label || 'Ready'));
    if (options.live) { node.setAttribute('role', 'status'); node.setAttribute('aria-live', 'polite'); }
    return node;
  };

  UI.progress = function progress(options = {}) {
    const root = mark(document.createElement('div'), 'progress', 'progress');
    root.className = 'progress';
    const labels = document.createElement('div');
    labels.className = 'progress__label';
    const name = textNode('span', '', options.label || 'Progress');
    const output = document.createElement('span');
    labels.append(name, output);
    const track = document.createElement('div');
    track.className = 'progress__track';
    track.setAttribute('role', 'progressbar');
    track.setAttribute('aria-label', options.label || 'Progress');
    track.setAttribute('aria-valuemin', '0');
    track.setAttribute('aria-valuemax', '100');
    const bar = document.createElement('div');
    bar.className = 'progress__bar';
    track.append(bar);
    const detail = document.createElement('p');
    detail.className = 'progress__message';
    detail.hidden = !options.showMessage;
    const live = document.createElement('div');
    live.className = 'progress__announcement visually-hidden';
    if (options.live) {
      live.setAttribute('role', 'status');
      live.setAttribute('aria-live', 'polite');
      live.setAttribute('aria-atomic', 'true');
    } else live.setAttribute('aria-hidden', 'true');
    root.append(labels, track, detail, live);
    root.setProgress = (state = 'running', requestedValue = 0, message = '') => {
      const value = Math.max(0, Math.min(100, Number(requestedValue) || 0));
      const indeterminate = state === 'indeterminate';
      root.dataset.state = state;
      root.toggleAttribute('aria-busy', indeterminate);
      detail.textContent = options.showMessage ? message : '';
      detail.hidden = !options.showMessage || !message;
      if (indeterminate) {
        track.removeAttribute('aria-valuenow');
        track.setAttribute(
          'aria-valuetext',
          message || 'Work is in progress; completion cannot be measured yet.',
        );
        output.textContent = options.indeterminateLabel || 'In progress';
        bar.style.removeProperty('--progress-value');
        live.textContent = options.live
          ? message || `${options.label || 'Progress'} is in progress.` : '';
      } else {
        track.setAttribute('aria-valuenow', String(value));
        track.setAttribute(
          'aria-valuetext',
          message || `${value} percent; ${state}`,
        );
        output.textContent = `${value}%`;
        bar.style.setProperty('--progress-value', `${value}%`);
        live.textContent = options.live
          ? message || `${options.label || 'Progress'} is ${value} percent complete.` : '';
      }
    };
    root.setProgress(options.state || 'running', options.value ?? 0, options.message);
    return root;
  };

  UI.loadingState = function loadingState(options = {}) {
    const node = mark(document.createElement('div'), 'loading-state', 'loadingState');
    node.className = 'loading-state';
    node.dataset.size = options.size === 'compact' ? 'compact' : 'default';
    node.setAttribute('role', 'status');
    node.setAttribute('aria-live', 'polite');
    node.setAttribute('aria-atomic', 'true');
    node.setAttribute('aria-busy', 'true');
    const spinner = document.createElement('span');
    spinner.className = 'loading-state__spinner';
    spinner.setAttribute('aria-hidden', 'true');
    const copy = document.createElement('span');
    copy.className = 'loading-state__copy';
    copy.append(textNode('strong', 'loading-state__label', options.label || 'Loading'));
    if (options.detail) {
      copy.append(textNode('span', 'loading-state__detail', options.detail));
    }
    node.append(spinner, copy);
    return node;
  };

  UI.skeleton = function skeleton(options = {}) {
    const kinds = ['line', 'heading', 'row', 'field', 'panel', 'portrait'];
    const kind = kinds.includes(options.kind) ? options.kind : 'line';
    const node = mark(document.createElement('div'), 'skeleton', 'skeleton');
    node.className = 'skeleton';
    node.dataset.kind = kind;
    node.setAttribute('aria-hidden', 'true');
    if (options.label) node.dataset.label = options.label;
    if (options.width) node.style.setProperty('--skeleton-width', String(options.width));
    return node;
  };

  UI.emptyState = function emptyState(options = {}) {
    const node = mark(document.createElement('div'), 'empty-state', 'emptyState');
    node.className = 'empty-state';
    const copy = document.createElement('div');
    copy.className = 'empty-state__copy';
    copy.append(
      textNode('strong', '', options.title || 'Nothing here yet'),
      textNode('span', '', options.body || 'Add an item to continue.'),
    );
    if (options.iconClass || options.icon) {
      const markNode = document.createElement('span');
      markNode.className = 'empty-state__mark';
      markNode.setAttribute('aria-hidden', 'true');
      markNode.append(
        options.iconClass
          ? UI.iconFromClass(options.iconClass, options.icon || 'book-open')
          : UI.icon(options.icon),
      );
      node.append(markNode, copy);
    } else {
      node.append(copy);
    }
    if (options.action) node.append(options.action);
    return node;
  };

  UI.inlineSave = function inlineSave(options = {}) {
    const node = mark(document.createElement('span'), 'inline-save', 'inlineSave');
    node.className = 'inline-save';
    node.dataset.state = options.state || 'saved';
    node.setAttribute('role', 'status');
    node.setAttribute('aria-live', 'polite');
    node.textContent = options.label || 'Saved';
    return node;
  };
})();
