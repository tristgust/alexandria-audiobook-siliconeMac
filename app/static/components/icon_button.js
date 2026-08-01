'use strict';

(() => {
  const UI = globalThis.AlexandriaUI ||= {};
  const NS = 'http://www.w3.org/2000/svg';
  const ICONS = {
    'book-open': ['M3.5 5.5c2.8-.8 5.6 0 8.5 2.3v11c-2.9-2.3-5.7-3.1-8.5-2.3z', 'M20.5 5.5c-2.8-.8-5.6 0-8.5 2.3v11c2.9-2.3 5.7-3.1 8.5-2.3z'],
    home: ['M3 11.2 12 3l9 8.2', 'M5.5 10v10h5v-6h3v6h5V10'],
    document: ['M6 3h8l4 4v14H6z', 'M14 3v5h5', 'M9 12h6', 'M9 16h6'],
    users: ['M9 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z', 'M2.5 21c.7-4 3-6 6.5-6s5.8 2 6.5 6', 'M16 12a3 3 0 1 0 0-6', 'M16.5 15c3.1.2 4.7 2 5 5'],
    waveform: ['M4 10v4', 'M8 7v10', 'M12 4v16', 'M16 8v8', 'M20 10v4'],
    export: ['M12 3v12', 'M7 8l5-5 5 5', 'M5 14v6h14v-6'],
    microphone: ['M12 3a3 3 0 0 0-3 3v5a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z', 'M6 10v1a6 6 0 0 0 12 0v-1', 'M12 17v4', 'M9 21h6'],
    grid: ['M4 4h6v6H4z', 'M14 4h6v6h-6z', 'M4 14h6v6H4z', 'M14 14h6v6h-6z'],
    sliders: ['M4 6h8', 'M16 6h4', 'M4 12h3', 'M11 12h9', 'M4 18h10', 'M18 18h2', 'M12 4v4', 'M7 10v4', 'M14 16v4'],
    user: ['M12 13a4 4 0 1 0 0-8 4 4 0 0 0 0 8z', 'M4 21c.8-4 3.5-6 8-6s7.2 2 8 6'],
    settings: ['M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z', 'M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5v.1h-4v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3v-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.5V3h4v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.5 1h.1v4h-.1a1.7 1.7 0 0 0-1.5 1z'],
    maintenance: ['M14.8 6.2a4 4 0 0 0-5-5L12 3.4 9.4 6 7.2 3.8a4 4 0 0 0 5 5L20 16.6 16.6 20l-7.8-7.8a4 4 0 0 0-5-5L6 9.4 3.4 12 1.2 9.8a4 4 0 0 0 5 5'],
    close: ['M6 6l12 12', 'M18 6L6 18'], menu: ['M4 7h16', 'M4 12h16', 'M4 17h16'],
    more: ['M6 12h.01', 'M12 12h.01', 'M18 12h.01'], search: ['M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14z', 'M16 16l5 5'],
    help: ['M9.5 9a2.7 2.7 0 1 1 4.2 2.25c-1.15.75-1.7 1.3-1.7 2.75', 'M12 18h.01', 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z'],
    play: ['M8 5l11 7-11 7z'], pause: ['M8 5v14', 'M16 5v14'], previous: ['M6 5v14', 'M18 5l-9 7 9 7z'], next: ['M18 5v14', 'M6 5l9 7-9 7z'],
    'skip-back': ['M5 5v5h5', 'M5.8 10a7 7 0 1 1 .2 5'], 'skip-forward': ['M19 5v5h-5', 'M18.2 10a7 7 0 1 0-.2 5'],
    volume: ['M4 10v4h4l5 4V6l-5 4z', 'M16 9a4 4 0 0 1 0 6', 'M18 6a8 8 0 0 1 0 12'], queue: ['M5 6h12', 'M5 12h12', 'M5 18h8', 'M19 17v4', 'M17 19h4'],
    check: ['M5 12l4 4L19 6'], warning: ['M12 3 2.5 20h19z', 'M12 9v4', 'M12 17h.01'], blocked: ['M5 5l14 14', 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z'], current: ['M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16z', 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z'],
    future: ['M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16z'], chevron: ['M7 9l5 5 5-5'], loader: ['M12 3a9 9 0 1 1-9 9'],
    refresh: ['M20 7v5h-5', 'M18.5 9A7 7 0 1 0 19 15'],
    bookmark: ['M6 3h12v18l-6-4-6 4z'],
    copy: ['M9 8h10v12H9z', 'M5 16H4V4h10v1'],
    image: ['M4 5h16v14H4z', 'M8 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z', 'M5 17l4-4 3 3 2-2 5 3'],
    wand: ['M5 19 18 6', 'M15 4l1-2 1 2 2 1-2 1-1 2-1-2-2-1z', 'M6 4l.8-1.5L7.5 4 9 5l-1.5.8L6.8 7.5 6 6 4.5 5z'],
    layers: ['M12 3 3 8l9 5 9-5z', 'M5 12l7 4 7-4', 'M5 16l7 4 7-4'],
    link: ['M9.5 14.5 14.5 9.5', 'M7 16.5H5.5a4 4 0 0 1 0-8H9', 'M15 7.5h3.5a4 4 0 0 1 0 8H15'],
    'volume-off': ['M4 10v4h4l5 4V6l-5 4z', 'M17 9l4 4', 'M21 9l-4 4'],
    info: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M12 11v5', 'M12 8h.01'],
    error: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M12 7v6', 'M12 17h.01'],
    minus: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M8 12h8'],
    'filter-clear': ['M4 5h16l-6 7v5l-4 2v-7z', 'M16 16l5 5', 'M21 16l-5 5'],
    database: ['M4 6c0-2 16-2 16 0s-16 2-16 0z', 'M4 6v6c0 2 16 2 16 0V6', 'M4 12v6c0 2 16 2 16 0v-6'],
    'file-audio': ['M6 3h8l4 4v14H6z', 'M14 3v5h5', 'M9 14v2', 'M12 12v6', 'M15 14v2'],
    folder: ['M3 6h7l2 2h9v11H3z'],
  };

  const ICON_CLASS_MAP = Object.freeze({
    'fa-house': 'home',
    'fa-file-lines': 'document',
    'fa-user-group': 'users',
    'fa-wave-square': 'waveform',
    'fa-arrow-up-from-bracket': 'export',
    'fa-book-open': 'book-open',
    'fa-microphone-lines': 'microphone',
    'fa-sliders': 'sliders',
    'fa-ellipsis': 'more',
    'fa-ellipsis-vertical': 'more',
    'fa-magnifying-glass': 'search',
    'fa-play': 'play',
    'fa-pause': 'pause',
    'fa-rotate-right': 'refresh',
    'fa-rotate-left': 'skip-back',
    'fa-spinner': 'loader',
    'fa-bookmark': 'bookmark',
    'fa-copy': 'copy',
    'fa-chevron-right': 'chevron',
    'fa-user': 'user',
    'fa-person': 'user',
    'fa-image': 'image',
    'fa-wand-magic-sparkles': 'wand',
    'fa-layer-group': 'layers',
    'fa-link': 'link',
    'fa-volume-xmark': 'volume-off',
    'fa-circle-check': 'check',
    'fa-triangle-exclamation': 'warning',
    'fa-circle-exclamation': 'error',
    'fa-minus-circle': 'minus',
    'fa-circle-info': 'info',
    'fa-filter-circle-xmark': 'filter-clear',
    'fa-database': 'database',
    'fa-file-audio': 'file-audio',
    'fa-file': 'document',
    'fa-folder-open': 'folder',
    'fa-list': 'queue',
    'fa-volume-high': 'volume',
    'fa-circle-question': 'help',
  });

  UI.iconNameFromClass = function iconNameFromClass(value = '') {
    const classes = String(value || '').split(/\s+/).filter(Boolean);
    const matched = classes.find((name) => ICON_CLASS_MAP[name]);
    return matched ? ICON_CLASS_MAP[matched] : null;
  };

  UI.icon = function icon(name = 'more') {
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    svg.classList.add('ui-icon');
    svg.dataset.icon = ICONS[name] ? name : 'more';
    (ICONS[name] || ICONS.more).forEach((data) => {
      const path = document.createElementNS(NS, 'path');
      path.setAttribute('d', data);
      svg.append(path);
    });
    return svg;
  };

  UI.iconFromClass = function iconFromClass(value = '', fallback = 'more') {
    const name = UI.iconNameFromClass(value);
    if (name) return UI.icon(name);
    const icon = document.createElement('i');
    icon.className = value;
    icon.setAttribute('aria-hidden', 'true');
    icon.dataset.legacyIcon = 'true';
    if (!value) return UI.icon(fallback);
    return icon;
  };

  UI.iconButton = function iconButton(options = {}) {
    const states = ['default', 'hover', 'pressed', 'focused', 'disabled', 'loading'];
    const state = states.includes(options.state) ? options.state : 'default';
    const label = options.label || 'More actions';
    const node = document.createElement('button');
    node.type = 'button';
    node.className = 'ui-icon-button';
    if (options.size === 'compact') node.classList.add('ui-icon-button--compact');
    node.dataset.primitive = 'icon-button';
    node.dataset.productionFactory = 'iconButton';
    node.dataset.state = state;
    node.dataset.size = options.size === 'compact' ? 'compact' : 'default';
    node.disabled = Boolean(options.disabled) || state === 'disabled' || state === 'loading';
    node.setAttribute('aria-label', label);
    node.dataset.tooltip = options.tooltip ?? label;
    if (state === 'loading') node.setAttribute('aria-busy', 'true');
    Object.entries(options.attributes || {}).forEach(([key, value]) => node.setAttribute(key, String(value)));
    if (state === 'loading') {
      node.append(UI.icon('loader'));
    } else if (options.iconClass) {
      node.append(UI.iconFromClass(options.iconClass, options.name || 'more'));
    } else {
      node.append(UI.icon(options.name || 'more'));
    }
    if (typeof options.onClick === 'function') node.addEventListener('click', options.onClick);
    return node;
  };
})();
