'use strict';

export function configureSupportingListbox(list, button, {
  selected = false,
  key = '',
  onSelect,
} = {}) {
  list.setAttribute('role', 'listbox');
  button.setAttribute('role', 'option');
  button.setAttribute('aria-selected', String(Boolean(selected)));
  button.tabIndex = selected ? 0 : -1;
  if (key) button.dataset.supportingSelectionKey = key;

  button.addEventListener('click', () => onSelect?.(button));
  button.addEventListener('keydown', (event) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    const options = [...list.querySelectorAll('[role="option"]')];
    if (!options.length) return;
    const current = options.indexOf(button);
    const next = event.key === 'Home' ? options[0]
      : event.key === 'End' ? options.at(-1)
        : options[(current + (event.key === 'ArrowDown' ? 1 : -1) + options.length)
          % options.length];
    if (!next) return;
    event.preventDefault();
    next.click();
  });
}

export function restoreSupportingSelectionFocus(root, key) {
  if (!key) return;
  requestAnimationFrame(() => {
    const target = [...root.querySelectorAll('[data-supporting-selection-key]')]
      .find((node) => node.dataset.supportingSelectionKey === key);
    target?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: 'nearest' });
  });
}
