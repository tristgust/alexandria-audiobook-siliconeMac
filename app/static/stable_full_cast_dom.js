'use strict';

export function text(tag, value, className = '') {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

export function button(label, className) {
  const node = text('button', label, className);
  node.type = 'button';
  return node;
}

export function fileLabel(file) {
  if (!file) return '';
  const kb = Math.max(1, Math.round(Number(file.size || 0) / 1024));
  return `${file.name} · ${kb} KB`;
}
