'use strict';

(() => {
  const UI = globalThis.AlexandriaUI ||= {};
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const ICONS = {
    close: ['M6 6l12 12', 'M18 6L6 18'],
    menu: ['M4 7h16', 'M4 12h16', 'M4 17h16'],
    more: ['M6 12h.01', 'M12 12h.01', 'M18 12h.01'],
    play: ['M8 5l11 7-11 7z'],
    search: ['M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14z', 'M16 16l5 5'],
  };

  function icon(name) {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    (ICONS[name] || ICONS.more).forEach((data) => {
      const path = document.createElementNS(SVG_NS, 'path');
      path.setAttribute('d', data);
      svg.append(path);
    });
    return svg;
  }

  UI.iconButton = function iconButton(options = {}) {
    const {
      label = 'More actions', name = 'more', size = 'default', disabled = false,
      tooltip = label, attributes = {}, onClick,
    } = options;
    const node = document.createElement('button');
    node.type = 'button';
    node.className = 'ui-icon-button';
    if (size === 'compact') node.classList.add('ui-icon-button--compact');
    node.dataset.primitive = 'icon-button';
    node.disabled = disabled;
    node.setAttribute('aria-label', label);
    if (tooltip) node.dataset.tooltip = tooltip;
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
    node.append(icon(name));
    if (typeof onClick === 'function') node.addEventListener('click', onClick);
    return node;
  };
})();
