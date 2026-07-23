'use strict';

(() => {
  const UI = globalThis.AlexandriaUI ||= {};
  let nextId = 0;

  function idFor(prefix) {
    nextId += 1;
    return `${prefix}-${nextId}`;
  }

  function textNode(tag, className, text) {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  }

  function applyControlState(control, options) {
    control.disabled = Boolean(options.disabled);
    control.readOnly = Boolean(options.readOnly);
    if (options.required) control.required = true;
    if (options.invalid) control.setAttribute('aria-invalid', 'true');
    if (options.attributes) {
      Object.entries(options.attributes).forEach(([key, value]) => control.setAttribute(key, String(value)));
    }
  }

  UI.field = function field(options = {}) {
    const kind = options.kind || 'input';
    const id = options.id || idFor('field');
    const wrapper = document.createElement('div');
    wrapper.className = 'field';
    wrapper.dataset.primitive = kind === 'textarea' ? 'textarea' : kind === 'select' ? 'select' : 'field';
    const label = textNode('label', 'field__label', options.label || 'Field');
    label.htmlFor = id;
    wrapper.append(label);
    if (options.description) {
      const description = textNode('div', 'field__description', options.description);
      description.id = `${id}-description`;
      wrapper.append(description);
    }
    const control = document.createElement(kind === 'textarea' || kind === 'select' ? kind : 'input');
    control.id = id;
    control.className = 'field__control';
    if (kind === 'input') control.type = options.type || 'text';
    if (kind === 'select') {
      (options.options || []).forEach((entry) => {
        const option = document.createElement('option');
        option.value = typeof entry === 'string' ? entry : entry.value;
        option.textContent = typeof entry === 'string' ? entry : entry.label;
        option.selected = option.value === options.value;
        control.append(option);
      });
    } else {
      control.value = options.value || '';
      if (options.placeholder) control.placeholder = options.placeholder;
    }
    applyControlState(control, options);
    const describedBy = [];
    if (options.description) describedBy.push(`${id}-description`);
    wrapper.append(control);
    if (options.message) {
      const messageId = options.messageId || `${id}-message`;
      const message = textNode('div', `field__message${options.invalid ? ' field__message--error' : ''}`, options.message);
      message.id = messageId;
      if (options.invalid) message.setAttribute('role', 'alert');
      wrapper.append(message);
      describedBy.push(messageId);
    }
    if (describedBy.length) control.setAttribute('aria-describedby', describedBy.join(' '));
    return wrapper;
  };

  function choice(options, type) {
    const label = document.createElement('label');
    label.className = 'choice';
    label.dataset.primitive = type === 'checkbox' ? 'checkbox' : 'radio-group';
    const input = document.createElement('input');
    input.type = type;
    input.name = options.name || idFor(type);
    input.value = options.value || 'on';
    input.checked = Boolean(options.checked);
    input.disabled = Boolean(options.disabled);
    label.append(input, document.createTextNode(options.label || 'Option'));
    return label;
  }

  UI.checkbox = (options = {}) => choice(options, 'checkbox');

  UI.radioGroup = function radioGroup(options = {}) {
    const group = document.createElement('fieldset');
    group.className = 'option-group';
    group.dataset.primitive = 'radio-group';
    const legend = textNode('legend', 'option-group__label', options.label || 'Choose one');
    group.append(legend);
    const name = options.name || idFor('radio');
    (options.options || []).forEach((entry, index) => {
      group.append(choice({ ...entry, name, checked: entry.checked ?? index === 0 }, 'radio'));
    });
    return group;
  };

  UI.toggle = function toggle(options = {}) {
    const label = document.createElement('label');
    label.className = 'toggle';
    label.dataset.primitive = 'toggle';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = Boolean(options.checked);
    input.disabled = Boolean(options.disabled);
    input.setAttribute('aria-label', options.label || 'Toggle');
    const track = document.createElement('span');
    track.className = 'toggle__track';
    const thumb = document.createElement('span');
    thumb.className = 'toggle__thumb';
    track.append(thumb);
    label.append(input, track, document.createTextNode(options.label || 'Toggle'));
    return label;
  };

  UI.segmentedControl = function segmentedControl(options = {}) {
    const group = document.createElement('div');
    group.className = 'segmented-control';
    group.dataset.primitive = 'segmented-control';
    group.setAttribute('role', 'radiogroup');
    group.setAttribute('aria-label', options.label || 'View');
    (options.options || []).forEach((entry, index) => {
      const button = document.createElement('button');
      const selected = entry.selected ?? index === 0;
      button.type = 'button';
      button.className = 'segment';
      button.textContent = entry.label || String(entry);
      button.setAttribute('role', 'radio');
      button.setAttribute('aria-checked', String(selected));
      button.tabIndex = selected ? 0 : -1;
      button.addEventListener('click', () => {
        group.querySelectorAll('[role="radio"]').forEach((item) => {
          item.setAttribute('aria-checked', String(item === button));
          item.tabIndex = item === button ? 0 : -1;
        });
      });
      group.append(button);
    });
    group.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      const items = [...group.querySelectorAll('[role="radio"]')];
      const current = items.indexOf(document.activeElement);
      const target = event.key === 'Home' ? items[0] : event.key === 'End' ? items.at(-1)
        : items[(current + (event.key === 'ArrowRight' ? 1 : -1) + items.length) % items.length];
      event.preventDefault();
      target.click();
      target.focus();
    });
    return group;
  };

  UI.filterChip = function filterChip(options = {}) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'filter-chip';
    button.dataset.primitive = 'filter-chip';
    button.textContent = options.label || 'Filter';
    button.setAttribute('aria-pressed', String(Boolean(options.pressed)));
    button.addEventListener('click', () => button.setAttribute('aria-pressed', String(button.getAttribute('aria-pressed') !== 'true')));
    return button;
  };

  UI.searchField = function searchField(options = {}) {
    const wrapper = document.createElement('label');
    wrapper.className = 'search-field';
    wrapper.dataset.primitive = 'search-field';
    const mark = textNode('span', 'search-field__mark', '⌕');
    mark.setAttribute('aria-hidden', 'true');
    const input = document.createElement('input');
    input.type = 'search';
    input.className = 'search-field__control';
    input.placeholder = options.placeholder || 'Search';
    input.setAttribute('aria-label', options.label || 'Search');
    wrapper.append(mark, input);
    return wrapper;
  };

  UI.listbox = function listbox(options = {}) {
    const root = document.createElement('ul');
    root.className = 'listbox';
    root.dataset.primitive = 'listbox';
    root.setAttribute('role', 'listbox');
    root.setAttribute('aria-label', options.label || 'Items');
    const select = (row) => {
      root.querySelectorAll('[role="option"]').forEach((item) => {
        item.setAttribute('aria-selected', String(item === row));
        item.tabIndex = item === row ? 0 : -1;
      });
    };
    (options.items || []).forEach((entry, index) => {
      const row = document.createElement('li');
      row.className = 'listbox__row';
      row.setAttribute('role', 'option');
      row.setAttribute('aria-selected', String(entry.selected ?? index === 0));
      row.tabIndex = entry.selected ?? index === 0 ? 0 : -1;
      if (entry.initials) {
        const monogram = textNode('span', 'monogram', entry.initials);
        monogram.setAttribute('aria-hidden', 'true');
        row.append(monogram);
      }
      const body = document.createElement('span');
      body.className = 'listbox__body';
      body.append(textNode('strong', 'listbox__title', entry.label || 'Item'));
      if (entry.meta) body.append(textNode('span', 'metadata', entry.meta));
      row.append(body);
      row.addEventListener('click', () => select(row));
      row.addEventListener('keydown', (event) => {
        if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
        const rows = [...root.querySelectorAll('[role="option"]')];
        const current = rows.indexOf(row);
        const target = event.key === 'Home' ? rows[0] : event.key === 'End' ? rows.at(-1)
          : rows[(current + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length];
        event.preventDefault();
        select(target);
        target.focus();
      });
      root.append(row);
    });
    return root;
  };

  UI.secretField = function secretField(options = {}) {
    const field = UI.field({ label: options.label || 'Credential', type: 'password', value: options.value || '' });
    field.dataset.secretMode = options.mode || 'preserve';
    field.querySelector('input').autocomplete = 'new-password';
    return field;
  };
})();
