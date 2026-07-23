'use strict';

(() => {
  const UI = globalThis.AlexandriaUI ||= {};
  let nextId = 0;
  const mark = (node, primitive, factory) => {
    node.dataset.primitive = primitive;
    node.dataset.productionFactory = factory;
    return node;
  };

  UI.disclosure = function disclosure(options = {}) {
    const root = mark(document.createElement('section'), 'disclosure', 'disclosure');
    root.className = 'disclosure';
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'disclosure__trigger';
    trigger.textContent = options.label || 'Details';
    const panel = document.createElement('div');
    panel.className = 'disclosure__panel';
    panel.id = options.id || `disclosure-panel-${++nextId}`;
    panel.hidden = !options.expanded;
    if (options.content) panel.append(options.content); else panel.textContent = options.body || 'Supporting details.';
    trigger.setAttribute('aria-controls', panel.id);
    trigger.setAttribute('aria-expanded', String(!panel.hidden));
    if (options.testId) trigger.dataset.test = options.testId;
    const toggle = () => {
      panel.hidden = !panel.hidden;
      trigger.setAttribute('aria-expanded', String(!panel.hidden));
    };
    trigger.addEventListener('click', toggle);
    trigger.addEventListener('keydown', (event) => {
      if (!['Enter', ' '].includes(event.key)) return;
      event.preventDefault();
      toggle();
    });
    root.append(trigger, panel);
    return root;
  };

  UI.popover = function popover(options = {}) {
    const root = document.createElement('span');
    root.className = 'popover-controller';
    const opener = options.opener || UI.button({ label: options.openerLabel || 'Open actions', variant: 'secondary' });
    if (options.testId) opener.dataset.test = options.testId;
    const panel = mark(document.createElement('div'), 'popover', 'popover');
    panel.className = 'popover';
    panel.id = options.id || `popover-panel-${++nextId}`;
    panel.hidden = true;
    panel.setAttribute('role', 'menu');
    panel.setAttribute('aria-label', options.label || 'Actions');
    opener.setAttribute('aria-controls', panel.id);
    opener.setAttribute('aria-expanded', 'false');
    opener.setAttribute('aria-haspopup', 'menu');
    (options.items || ['Open', 'Duplicate']).forEach((entry, index) => {
      const item = UI.button({ label: typeof entry === 'string' ? entry : entry.label, variant: 'quiet', size: 'compact' });
      item.setAttribute('role', 'menuitem');
      item.tabIndex = index === 0 ? 0 : -1;
      if (typeof entry.onSelect === 'function') item.addEventListener('click', () => entry.onSelect(entry));
      panel.append(item);
    });
    const close = (restore = true) => {
      panel.hidden = true;
      opener.setAttribute('aria-expanded', 'false');
      if (restore) opener.focus();
    };
    const open = () => {
      panel.hidden = false;
      opener.setAttribute('aria-expanded', 'true');
      const first = panel.querySelector('[role="menuitem"]');
      if (first) first.focus();
    };
    opener.addEventListener('click', () => panel.hidden ? open() : close());
    panel.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); close(); return; }
      if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      const items = [...panel.querySelectorAll('[role="menuitem"]')];
      const current = items.indexOf(document.activeElement);
      const target = event.key === 'Home' ? items[0] : event.key === 'End' ? items.at(-1)
        : items[(current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length];
      event.preventDefault();
      items.forEach((item) => { item.tabIndex = item === target ? 0 : -1; });
      target.focus();
    });
    const outside = (event) => { if (!panel.hidden && !root.contains(event.target)) close(); };
    document.addEventListener('click', outside);
    root.popoverCleanup = () => document.removeEventListener('click', outside);
    root.open = open;
    root.close = close;
    root.append(opener, panel);
    return root;
  };
})();
