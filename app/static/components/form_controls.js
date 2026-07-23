'use strict';

(() => {
  const UI = globalThis.AlexandriaUI ||= {};
  let nextId = 0;
  const idFor = (prefix) => `${prefix}-${++nextId}`;
  const textNode = (tag, className, text) => {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  };
  const mark = (node, primitive, factory = primitive) => {
    node.dataset.primitive = primitive;
    node.dataset.productionFactory = factory;
    return node;
  };

  UI.field = function field(options = {}) {
    const kind = options.kind || 'input';
    const id = options.id || idFor('field');
    const primitive = kind === 'textarea' ? 'textarea' : kind === 'select' ? 'select' : 'field';
    const wrapper = mark(document.createElement('div'), primitive, 'field');
    wrapper.className = 'field';
    const label = textNode('label', 'field__label', options.label || 'Field');
    label.htmlFor = id;
    wrapper.append(label);
    const describedBy = [];
    if (options.description) {
      const description = textNode('div', 'field__description', options.description);
      description.id = `${id}-description`;
      describedBy.push(description.id);
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
    control.disabled = Boolean(options.disabled);
    control.readOnly = Boolean(options.readOnly);
    control.required = Boolean(options.required);
    if (options.invalid) control.setAttribute('aria-invalid', 'true');
    Object.entries(options.attributes || {}).forEach(([key, value]) => control.setAttribute(key, String(value)));
    wrapper.append(control);
    if (options.message) {
      const message = textNode('div', `field__message${options.invalid ? ' field__message--error' : ''}`, options.message);
      message.id = options.messageId || `${id}-message`;
      if (options.invalid) message.setAttribute('role', 'alert');
      describedBy.push(message.id);
      wrapper.append(message);
    }
    if (describedBy.length) control.setAttribute('aria-describedby', describedBy.join(' '));
    return wrapper;
  };

  function choice(options, type, decorate = false) {
    const label = document.createElement('label');
    label.className = 'choice';
    if (decorate) mark(label, 'checkbox', 'checkbox');
    if (options.visualFocus) label.dataset.visualFocus = 'true';
    const input = document.createElement('input');
    input.type = type;
    input.name = options.name || idFor(type);
    input.value = options.value || 'on';
    input.checked = Boolean(options.checked);
    input.disabled = Boolean(options.disabled);
    if (type === 'checkbox') input.indeterminate = Boolean(options.indeterminate);
    label.append(input, document.createTextNode(options.label || 'Option'));
    return label;
  }

  UI.checkbox = (options = {}) => choice(options, 'checkbox', true);

  UI.radioGroup = function radioGroup(options = {}) {
    const group = mark(document.createElement('fieldset'), 'radio-group', 'radioGroup');
    group.className = 'option-group';
    group.disabled = Boolean(options.disabled);
    group.append(textNode('legend', 'option-group__label', options.label || 'Choose one'));
    const name = options.name || idFor('radio');
    (options.options || []).forEach((entry, index) => group.append(choice({
      ...entry, name, disabled: options.disabled || entry.disabled,
      checked: entry.checked ?? index === 0,
    }, 'radio')));
    return group;
  };

  UI.toggle = function toggle(options = {}) {
    const label = mark(document.createElement('label'), 'toggle', 'toggle');
    label.className = 'toggle';
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
    const group = mark(document.createElement('div'), 'segmented-control', 'segmentedControl');
    group.className = 'segmented-control';
    group.setAttribute('role', 'radiogroup');
    group.setAttribute('aria-label', options.label || 'View');
    (options.options || []).forEach((entry, index) => {
      const button = document.createElement('button');
      const selected = entry.selected ?? index === 0;
      button.type = 'button';
      button.className = 'segment';
      button.textContent = entry.label || String(entry);
      button.disabled = Boolean(options.disabled || entry.disabled);
      button.setAttribute('role', 'radio');
      button.setAttribute('aria-checked', String(selected));
      button.tabIndex = selected && !button.disabled ? 0 : -1;
      button.addEventListener('click', () => {
        if (button.disabled) return;
        group.querySelectorAll('[role="radio"]').forEach((item) => {
          item.setAttribute('aria-checked', String(item === button));
          item.tabIndex = item === button ? 0 : -1;
        });
      });
      group.append(button);
    });
    group.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      const items = [...group.querySelectorAll('[role="radio"]:not(:disabled)')];
      const current = items.indexOf(document.activeElement);
      const target = event.key === 'Home' ? items[0] : event.key === 'End' ? items.at(-1)
        : items[(current + (event.key === 'ArrowRight' ? 1 : -1) + items.length) % items.length];
      if (!target) return;
      event.preventDefault();
      target.click();
      target.focus();
    });
    return group;
  };

  UI.filterChip = function filterChip(options = {}) {
    const button = mark(document.createElement('button'), 'filter-chip', 'filterChip');
    button.type = 'button';
    button.className = 'filter-chip';
    button.textContent = options.label || 'Filter';
    button.disabled = Boolean(options.disabled);
    button.setAttribute('aria-pressed', String(Boolean(options.pressed)));
    button.addEventListener('click', () => button.setAttribute('aria-pressed', String(button.getAttribute('aria-pressed') !== 'true')));
    return button;
  };

  UI.searchField = function searchField(options = {}) {
    const wrapper = mark(document.createElement('label'), 'search-field', 'searchField');
    wrapper.className = 'search-field';
    const icon = document.createElement('span');
    icon.className = 'search-field__mark';
    icon.append(UI.icon('search'));
    const input = document.createElement('input');
    input.type = 'search';
    input.className = 'search-field__control';
    input.placeholder = options.placeholder || 'Search';
    input.disabled = Boolean(options.disabled);
    input.setAttribute('aria-label', options.label || 'Search');
    wrapper.append(icon, input);
    return wrapper;
  };

  UI.listbox = function listbox(options = {}) {
    const root = mark(document.createElement('ul'), 'listbox', 'listbox');
    root.className = 'listbox';
    root.setAttribute('role', 'listbox');
    root.setAttribute('aria-label', options.label || 'Items');
    const select = (row) => root.querySelectorAll('[role="option"]').forEach((item) => {
      item.setAttribute('aria-selected', String(item === row));
      item.tabIndex = item === row ? 0 : -1;
    });
    (options.items || []).forEach((entry, index) => {
      const row = document.createElement('li');
      row.className = 'listbox__row';
      row.setAttribute('role', 'option');
      row.setAttribute('aria-selected', String(entry.selected ?? index === 0));
      row.tabIndex = entry.selected ?? index === 0 ? 0 : -1;
      if (entry.initials) row.append(UI.monogram({ initials: entry.initials, label: `Monogram for ${entry.label || 'item'}` }));
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
        const target = event.key === 'Home' ? rows[0] : event.key === 'End' ? rows.at(-1) : rows[(current + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length];
        event.preventDefault(); select(target); target.focus();
      });
      root.append(row);
    });
    return root;
  };

  UI.secretField = function secretField(options = {}) {
    const mode = ['preserve', 'replace', 'clear'].includes(options.mode) ? options.mode : 'preserve';
    const field = UI.field({ label: options.label || 'Credential', type: 'password', placeholder: mode === 'replace' ? 'Enter replacement credential' : 'Saved credential' });
    field.dataset.productionFactory = 'secretField';
    field.dataset.secretMode = mode;
    field.dataset.intent = mode;
    const input = field.querySelector('input');
    input.autocomplete = mode === 'replace' ? 'new-password' : 'off';
    input.disabled = mode !== 'replace';
    if (mode === 'clear') field.dataset.clearCredential = 'true';
    const live = textNode('div', 'field__message secret-intent', mode === 'preserve' ? 'Existing credential will be preserved.' : mode === 'clear' ? 'Existing credential will be cleared on save.' : 'Enter a replacement credential.');
    live.setAttribute('role', 'status');
    live.setAttribute('aria-live', 'polite');
    input.addEventListener('input', () => { live.textContent = input.value ? 'Replacement credential entered.' : 'Enter a replacement credential.'; });
    field.append(live);
    return field;
  };
})();
