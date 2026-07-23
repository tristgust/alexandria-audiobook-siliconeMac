'use strict';

(() => {
  const UI = globalThis.AlexandriaUI ||= {};

  UI.notice = function notice(options = {}) {
    const root = document.createElement('section');
    root.className = 'notice';
    root.dataset.primitive = 'notice';
    root.dataset.tone = options.tone || 'information';
    if (options.live) {
      root.setAttribute('role', options.tone === 'error' ? 'alert' : 'status');
      root.setAttribute('aria-live', options.tone === 'error' ? 'assertive' : 'polite');
    }
    const marker = document.createElement('span');
    marker.setAttribute('aria-hidden', 'true');
    marker.textContent = options.marker || '•';
    const content = document.createElement('div');
    const title = document.createElement('h3');
    title.className = 'notice__title';
    title.textContent = options.title || 'Information';
    const body = document.createElement('p');
    body.className = 'notice__body';
    body.textContent = options.body || 'Additional context is available.';
    content.append(title, body);
    root.append(marker, content);
    if (options.action) root.append(options.action);
    return root;
  };
})();
