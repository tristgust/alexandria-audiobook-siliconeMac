'use strict';

(() => {
  const UI = globalThis.AlexandriaUI ||= {};

  UI.status = function status(options = {}) {
    const node = document.createElement('span');
    node.className = 'status-indicator';
    node.dataset.primitive = 'status';
    node.dataset.tone = options.tone || 'neutral';
    node.textContent = options.label || 'Ready';
    if (options.live) {
      node.setAttribute('role', 'status');
      node.setAttribute('aria-live', 'polite');
    }
    return node;
  };

  UI.progress = function progress(options = {}) {
    const value = Math.max(0, Math.min(100, Number(options.value) || 0));
    const root = document.createElement('div');
    root.className = 'progress';
    root.dataset.primitive = 'progress';
    const labels = document.createElement('div');
    labels.className = 'progress__label';
    const name = document.createElement('span');
    name.textContent = options.label || 'Progress';
    const output = document.createElement('span');
    output.textContent = `${value}%`;
    labels.append(name, output);
    const track = document.createElement('div');
    track.className = 'progress__track';
    track.setAttribute('role', 'progressbar');
    track.setAttribute('aria-label', options.label || 'Progress');
    track.setAttribute('aria-valuemin', '0');
    track.setAttribute('aria-valuemax', '100');
    track.setAttribute('aria-valuenow', String(value));
    const bar = document.createElement('div');
    bar.className = 'progress__bar';
    bar.style.setProperty('--progress-value', `${value}%`);
    track.append(bar);
    root.append(labels, track);
    return root;
  };

  UI.skeleton = function skeleton(options = {}) {
    const node = document.createElement('div');
    node.className = 'skeleton';
    node.dataset.primitive = 'skeleton';
    node.setAttribute('aria-hidden', 'true');
    if (options.label) node.dataset.label = options.label;
    return node;
  };

  UI.emptyState = function emptyState(options = {}) {
    const node = document.createElement('div');
    node.className = 'empty-state';
    node.dataset.primitive = 'empty-state';
    const heading = document.createElement('strong');
    heading.textContent = options.title || 'Nothing here yet';
    const body = document.createElement('span');
    body.textContent = options.body || 'Add an item to continue.';
    node.append(heading, body);
    if (options.action) node.append(options.action);
    return node;
  };

  UI.inlineSave = function inlineSave(options = {}) {
    const node = document.createElement('span');
    node.className = 'inline-save';
    node.dataset.primitive = 'inline-save';
    node.dataset.state = options.state || 'saved';
    node.setAttribute('role', 'status');
    node.setAttribute('aria-live', 'polite');
    node.textContent = options.label || 'Saved';
    return node;
  };
})();
