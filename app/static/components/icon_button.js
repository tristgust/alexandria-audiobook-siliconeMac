'use strict';

(() => {
  const UI = globalThis.AlexandriaUI ||= {};
  const NS = 'http://www.w3.org/2000/svg';
  const ICONS = {
    'book-open': ['M3.5 5.5c2.8-.8 5.6 0 8.5 2.3v11c-2.9-2.3-5.7-3.1-8.5-2.3z', 'M20.5 5.5c-2.8-.8-5.6 0-8.5 2.3v11c2.9-2.3 5.7-3.1 8.5-2.3z'],
    grid: ['M4 4h6v6H4z', 'M14 4h6v6h-6z', 'M4 14h6v6H4z', 'M14 14h6v6h-6z'],
    sliders: ['M4 6h8', 'M16 6h4', 'M4 12h3', 'M11 12h9', 'M4 18h10', 'M18 18h2', 'M12 4v4', 'M7 10v4', 'M14 16v4'],
    user: ['M12 13a4 4 0 1 0 0-8 4 4 0 0 0 0 8z', 'M4 21c.8-4 3.5-6 8-6s7.2 2 8 6'],
    settings: ['M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z', 'M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5v.1h-4v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3v-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.5V3h4v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.5 1h.1v4h-.1a1.7 1.7 0 0 0-1.5 1z'],
    maintenance: ['M14.8 6.2a4 4 0 0 0-5-5L12 3.4 9.4 6 7.2 3.8a4 4 0 0 0 5 5L20 16.6 16.6 20l-7.8-7.8a4 4 0 0 0-5-5L6 9.4 3.4 12 1.2 9.8a4 4 0 0 0 5 5'],
    close: ['M6 6l12 12', 'M18 6L6 18'], menu: ['M4 7h16', 'M4 12h16', 'M4 17h16'],
    more: ['M6 12h.01', 'M12 12h.01', 'M18 12h.01'], search: ['M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14z', 'M16 16l5 5'],
    play: ['M8 5l11 7-11 7z'], pause: ['M8 5v14', 'M16 5v14'], previous: ['M6 5v14', 'M18 5l-9 7 9 7z'], next: ['M18 5v14', 'M6 5l9 7-9 7z'],
    'skip-back': ['M5 5v5h5', 'M5.8 10a7 7 0 1 1 .2 5'], 'skip-forward': ['M19 5v5h-5', 'M18.2 10a7 7 0 1 0-.2 5'],
    volume: ['M4 10v4h4l5 4V6l-5 4z', 'M16 9a4 4 0 0 1 0 6', 'M18 6a8 8 0 0 1 0 12'], queue: ['M5 6h12', 'M5 12h12', 'M5 18h8', 'M19 17v4', 'M17 19h4'],
    check: ['M5 12l4 4L19 6'], blocked: ['M5 5l14 14', 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z'], current: ['M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16z', 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z'],
    future: ['M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16z'], chevron: ['M7 9l5 5 5-5'], loader: ['M12 3a9 9 0 1 1-9 9'],
  };

  UI.icon = function icon(name = 'more') {
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    svg.dataset.icon = ICONS[name] ? name : 'more';
    (ICONS[name] || ICONS.more).forEach((data) => {
      const path = document.createElementNS(NS, 'path');
      path.setAttribute('d', data);
      svg.append(path);
    });
    return svg;
  };

  UI.iconButton = function iconButton(options = {}) {
    const node = document.createElement('button');
    node.type = 'button';
    node.className = 'ui-icon-button';
    if (options.size === 'compact') node.classList.add('ui-icon-button--compact');
    node.dataset.primitive = 'icon-button';
    node.dataset.productionFactory = 'iconButton';
    node.disabled = Boolean(options.disabled);
    node.setAttribute('aria-label', options.label || 'More actions');
    if (options.tooltip ?? options.label) node.dataset.tooltip = options.tooltip ?? options.label;
    Object.entries(options.attributes || {}).forEach(([key, value]) => node.setAttribute(key, String(value)));
    node.append(UI.icon(options.name || 'more'));
    if (typeof options.onClick === 'function') node.addEventListener('click', options.onClick);
    return node;
  };
})();
