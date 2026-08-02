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
    if (options.description) {
      const copy = document.createElement('span');
      copy.className = 'disclosure__copy';
      const label = document.createElement('strong');
      label.textContent = options.label || 'Details';
      const description = document.createElement('small');
      description.textContent = options.description;
      copy.append(label, description);
      trigger.append(copy);
    } else {
      trigger.textContent = options.label || 'Details';
    }
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
      const item = UI.button({
        label: typeof entry === 'string' ? entry : entry.label,
        variant: 'quiet',
        size: 'compact',
        attributes: typeof entry === 'string' ? undefined : entry.attributes,
        disabled: typeof entry === 'string' ? false : entry.disabled,
      });
      item.setAttribute('role', 'menuitem');
      item.tabIndex = index === 0 ? 0 : -1;
      if (typeof entry.onSelect === 'function') {
        item.addEventListener('click', () => {
          entry.onSelect(entry);
          close(false);
        });
      }
      panel.append(item);
    });
    let positionFrame = 0;
    const clearPositionListeners = () => {
      cancelAnimationFrame(positionFrame);
      positionFrame = 0;
      window.removeEventListener('resize', schedulePosition);
      document.removeEventListener('scroll', schedulePosition, true);
    };
    const positionPanel = () => {
      positionFrame = 0;
      if (panel.hidden || !opener.isConnected) return;
      const margin = 8;
      const gap = 8;
      const viewportWidth = document.documentElement.clientWidth || innerWidth;
      const viewportHeight = document.documentElement.clientHeight || innerHeight;
      panel.style.position = 'fixed';
      panel.style.right = 'auto';
      panel.style.bottom = 'auto';
      panel.style.left = '0px';
      panel.style.top = '0px';
      panel.style.maxWidth = `${Math.max(0, viewportWidth - margin * 2)}px`;
      panel.style.maxHeight = 'none';
      panel.style.overflowY = '';
      panel.style.visibility = 'hidden';
      const openerRect = opener.getBoundingClientRect();
      const naturalRect = panel.getBoundingClientRect();
      const width = Math.min(naturalRect.width, Math.max(0, viewportWidth - margin * 2));
      const below = Math.max(0, viewportHeight - openerRect.bottom - gap - margin);
      const above = Math.max(0, openerRect.top - gap - margin);
      const openAbove = naturalRect.height > below && above > below;
      const available = Math.max(0, openAbove ? above : below);
      const height = Math.min(naturalRect.height, available);
      const left = Math.min(
        Math.max(margin, openerRect.right - width),
        Math.max(margin, viewportWidth - width - margin),
      );
      const preferredTop = openAbove
        ? openerRect.top - gap - height
        : openerRect.bottom + gap;
      const top = Math.min(
        Math.max(margin, preferredTop),
        Math.max(margin, viewportHeight - height - margin),
      );
      panel.style.left = `${left}px`;
      panel.style.top = `${top}px`;
      panel.style.maxHeight = `${available}px`;
      panel.style.overflowY = naturalRect.height > available ? 'auto' : '';
      panel.style.visibility = '';
    };
    function schedulePosition() {
      cancelAnimationFrame(positionFrame);
      positionFrame = requestAnimationFrame(positionPanel);
    }
    const close = (restore = true) => {
      clearPositionListeners();
      panel.hidden = true;
      opener.setAttribute('aria-expanded', 'false');
      if (restore) opener.focus();
    };
    const open = () => {
      panel.hidden = false;
      opener.setAttribute('aria-expanded', 'true');
      positionPanel();
      window.addEventListener('resize', schedulePosition);
      document.addEventListener('scroll', schedulePosition, true);
      const first = panel.querySelector('[role="menuitem"]');
      if (first) first.focus();
    };
    opener.addEventListener('click', () => panel.hidden ? open() : close());
    panel.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); close(); return; }
      if (event.key === 'Tab') { close(false); return; }
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
    root.popoverCleanup = () => {
      clearPositionListeners();
      document.removeEventListener('click', outside);
    };
    root.open = open;
    root.close = close;
    root.append(opener, panel);
    return root;
  };
})();
