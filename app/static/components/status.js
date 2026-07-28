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
    icon.append(UI.icon(tone === 'success' ? 'check' : tone === 'error' ? 'blocked' : 'current'));
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
    const live = document.createElement('div');
    live.className = 'progress__announcement visually-hidden';
    live.setAttribute('role', 'status');
    live.setAttribute('aria-live', 'polite');
    root.append(labels, track, live);
    root.setProgress = (state = 'running', requestedValue = 0, message = '') => {
      const value = Math.max(0, Math.min(100, Number(requestedValue) || 0));
      const indeterminate = state === 'indeterminate';
      root.dataset.state = state;
      root.toggleAttribute('aria-busy', indeterminate);
      if (indeterminate) {
        track.removeAttribute('aria-valuenow');
        track.setAttribute('aria-valuetext', message || 'Loading; progress is not yet measurable');
        output.textContent = 'Loading…';
        bar.style.removeProperty('--progress-value');
        live.textContent = message || `${options.label || 'Progress'} is loading.`;
      } else {
        track.setAttribute('aria-valuenow', String(value));
        track.setAttribute('aria-valuetext', `${state}, ${value} percent`);
        output.textContent = `${state} · ${value}%`;
        bar.style.setProperty('--progress-value', `${value}%`);
        live.textContent = message || `${options.label || 'Progress'} is ${state} at ${value} percent.`;
      }
    };
    root.setProgress(options.state || 'running', options.value ?? 0, options.message);
    return root;
  };

  UI.skeleton = function skeleton(options = {}) {
    const node = mark(document.createElement('div'), 'skeleton', 'skeleton');
    node.className = 'skeleton';
    node.setAttribute('aria-hidden', 'true');
    if (options.label) node.dataset.label = options.label;
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
    if (options.iconClass) {
      const markNode = document.createElement('span');
      markNode.className = 'empty-state__mark';
      markNode.setAttribute('aria-hidden', 'true');
      const icon = document.createElement('i');
      icon.className = options.iconClass;
      markNode.append(icon);
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
