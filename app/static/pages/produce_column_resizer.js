'use strict';

const STORAGE_KEY = 'alexandria.produce.columns.v3';

const COLUMNS = Object.freeze([
  { key: 'character', label: 'Character', width: '--produce-character-default', min: '--produce-character-resize-min', max: '--produce-character-resize-max' },
  { key: 'text', label: 'Text', width: '--produce-text-default', min: '--produce-text-resize-min', max: '--produce-text-resize-max' },
  { key: 'direction', label: 'Direction', width: '--produce-direction-default', min: '--produce-direction-resize-min', max: '--produce-direction-resize-max' },
  { key: 'duration', label: 'Duration', width: '--produce-duration-default', min: '--produce-duration-resize-min', max: '--produce-duration-resize-max' },
  { key: 'audio', label: 'Audio', width: '--produce-audio-default', min: '--produce-audio-resize-min', max: '--produce-audio-resize-max' },
  { key: 'state', label: 'Status', width: '--produce-state-default', min: '--produce-state-resize-min', max: '--produce-state-resize-max' },
]);

const tokenPixels = (element, token) => Number.parseFloat(
  getComputedStyle(element).getPropertyValue(token),
) || 0;

function savedWidths() {
  try {
    const value = JSON.parse(globalThis.localStorage?.getItem(STORAGE_KEY) || '{}');
    return value && typeof value === 'object' ? value : {};
  } catch (_) {
    return {};
  }
}

function persist(widths) {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(widths));
  } catch (_) {
    // Column sizing remains available for this page when storage is unavailable.
  }
}

export function createProduceColumnHeader(content) {
  const widths = savedWidths();
  const columns = COLUMNS.map((column) => ({
    ...column,
    width: tokenPixels(content, column.width),
    min: tokenPixels(content, column.min),
    max: tokenPixels(content, column.max),
  }));
  const resizeStep = tokenPixels(content, '--produce-column-resize-step');
  const header = document.createElement('div');
  header.className = 'audio-table__header';
  header.dataset.primitive = 'resizable-audio-columns';
  header.dataset.productionFactory = 'resizableAudioColumns';
  header.setAttribute('role', 'group');
  header.setAttribute('aria-label', 'Resizable audio table columns');

  const applyWidth = (column, requested) => {
    const width = clamp(column, requested);
    widths[column.key] = width;
    content.style.setProperty(`--produce-${column.key}-column`, `${width}px`);
    const handle = header.querySelector(`[data-produce-column-resize="${column.key}"]`);
    handle?.setAttribute('aria-valuenow', String(width));
    handle?.setAttribute('aria-valuetext', `${width} pixels`);
    return width;
  };

  const clamp = (column, value) => Math.min(column.max, Math.max(column.min, Math.round(value)));

  columns.forEach((column) => {
    applyWidth(column, Number(widths[column.key]) || column.width);
    const cell = document.createElement('span');
    cell.className = 'audio-table__column-heading';
    cell.append(document.createTextNode(column.label));
    const handle = document.createElement('span');
    handle.className = 'audio-table__resize-handle';
    handle.dataset.produceColumnResize = column.key;
    handle.setAttribute('role', 'separator');
    handle.setAttribute('aria-orientation', 'vertical');
    handle.tabIndex = 0;
    handle.setAttribute('aria-label', `Resize ${column.label} column`);
    handle.setAttribute('aria-valuemin', String(column.min));
    handle.setAttribute('aria-valuemax', String(column.max));
    handle.setAttribute('aria-valuenow', String(widths[column.key]));
    handle.setAttribute('aria-valuetext', `${widths[column.key]} pixels`);
    handle.title = `Drag to resize ${column.label}. Arrow keys resize; Home resets.`;
    handle.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = Number(widths[column.key]) || column.width;
      handle.setPointerCapture?.(event.pointerId);
      const move = (moveEvent) => applyWidth(column, startWidth + moveEvent.clientX - startX);
      const finish = () => {
        handle.removeEventListener('pointermove', move);
        handle.removeEventListener('pointerup', finish);
        handle.removeEventListener('pointercancel', finish);
        persist(widths);
      };
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', finish);
      handle.addEventListener('pointercancel', finish);
    });
    handle.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return;
      event.preventDefault();
      const current = Number(widths[column.key]) || column.width;
      applyWidth(column, event.key === 'Home' ? column.width
        : current + (event.key === 'ArrowRight' ? resizeStep : -resizeStep));
      persist(widths);
    });
    cell.append(handle);
    header.append(cell);
  });
  return header;
}
