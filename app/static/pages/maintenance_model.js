'use strict';

import { textNode } from '/static/pages/more.js';

const UI = globalThis.AlexandriaUI;

export const MAINTENANCE_READS = Object.freeze([
  ['recovery', '/api/recovery/status'],
  ['models', '/api/model_registry/status'],
  ['memory', '/api/model_registry/memory'],
  ['library', '/api/library'],
  ['projects', '/api/projects'],
  ['migration', '/api/migration/status'],
  ['history', '/api/migration/history'],
]);

export function safeMaintenanceRead(settled, index) {
  if (settled[index]?.status !== 'fulfilled') return null;
  const result = settled[index].value;
  return result.ok ? result.data : null;
}

export function maintenanceMetric(label, value, detail = '') {
  const row = document.createElement('div');
  row.className = 'support-metric';
  const copy = document.createElement('div');
  copy.append(textNode('span', 'metadata', label));
  if (detail) copy.append(textNode('p', 'support-status-copy', detail));
  row.append(copy, textNode('strong', '', value));
  return row;
}

export function maintenanceSection(id, title, body, content) {
  const node = UI.flatSection({
    id: `maintenance-${id}`,
    title,
    body,
    content,
    className: 'specialist-section',
    headingTag: 'h2',
  });
  const heading = node.querySelector('h2');
  heading.id = `maintenance-${id}-heading`;
  heading.tabIndex = -1;
  return node;
}

export function focusMaintenanceMode(mode, signal) {
  requestAnimationFrame(() => {
    if (signal.aborted) return;
    const sectionHeading = document.getElementById(`maintenance-${mode}-heading`);
    const pageHeading = document.getElementById('maintenance-page-heading');
    const heading = sectionHeading && mode !== 'health' ? sectionHeading : pageHeading;
    const scroller = heading?.closest('.workspace');
    if (!heading || !scroller) return;
    scroller.scrollTop = 0;
    requestAnimationFrame(() => {
      if (signal.aborted) return;
      if (sectionHeading && mode !== 'health') {
        const offset = heading.getBoundingClientRect().top
          - scroller.getBoundingClientRect().top - 20;
        if (Math.abs(offset) > 1) scroller.scrollTop += offset;
      }
      heading.focus({ preventScroll: true });
    });
  });
}
