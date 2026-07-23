'use strict';

(() => {
  const UI = globalThis.AlexandriaUI ||= {};
  let nextId = 0;

  UI.disclosure = function disclosure(options = {}) {
    nextId += 1;
    const root = document.createElement('section');
    root.className = 'disclosure';
    root.dataset.primitive = 'disclosure';
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'disclosure__trigger';
    trigger.textContent = options.label || 'Details';
    const panel = document.createElement('div');
    panel.className = 'disclosure__panel';
    panel.id = options.id || `disclosure-panel-${nextId}`;
    panel.hidden = !options.expanded;
    panel.textContent = options.body || 'Supporting details.';
    trigger.setAttribute('aria-controls', panel.id);
    trigger.setAttribute('aria-expanded', String(!panel.hidden));
    if (options.testId) trigger.dataset.test = options.testId;
    const toggle = () => {
      panel.hidden = !panel.hidden;
      trigger.setAttribute('aria-expanded', String(!panel.hidden));
    };
    trigger.addEventListener('click', toggle);
    trigger.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      toggle();
    });
    root.append(trigger, panel);
    return root;
  };

  UI.popover = function popover(options = {}) {
    const panel = document.createElement('div');
    panel.className = 'popover';
    panel.dataset.primitive = 'popover';
    panel.setAttribute('role', 'menu');
    panel.setAttribute('aria-label', options.label || 'Actions');
    (options.items || ['Open', 'Duplicate']).forEach((label, index) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'ui-button ui-button--quiet ui-button--compact';
      item.setAttribute('role', 'menuitem');
      item.tabIndex = index === 0 ? 0 : -1;
      item.textContent = label;
      panel.append(item);
    });
    panel.addEventListener('keydown', (event) => {
      if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      const items = [...panel.querySelectorAll('[role="menuitem"]')];
      const current = items.indexOf(document.activeElement);
      const target = event.key === 'Home' ? items[0] : event.key === 'End' ? items.at(-1)
        : items[(current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length];
      event.preventDefault();
      items.forEach((item) => { item.tabIndex = item === target ? 0 : -1; });
      target.focus();
    });
    return panel;
  };
})();
