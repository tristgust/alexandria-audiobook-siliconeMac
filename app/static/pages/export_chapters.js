'use strict';

import { exportClock, exportPanel, exportText } from './export_model.js';

const UI = globalThis.AlexandriaUI;

export function createExportChapters({ aggregate, projectId, shell }) {
  const chapters = aggregate.chapters || [];
  const totalDuration = chapters.reduce((sum, chapter) => (
    sum + Math.max(0, Number(chapter.end_ms) - Number(chapter.start_ms))
  ), 0);
  const node = exportPanel(
    'export-chapters',
    'Chapters',
    `${chapters.length.toLocaleString()} chapter${chapters.length === 1 ? '' : 's'}`,
    'Publication structure',
  );
  if (!chapters.length) {
    node.append(UI.emptyState({
      iconClass: 'fas fa-book-open',
      title: 'No chapters are available to export',
      body: 'Review Script chapter structure before building an audiobook.',
      action: UI.button({
        label: 'Review Script',
        variant: 'secondary',
        onClick: () => shell.navigate(shell.routes.routeForPath(
          'script', projectId ? { project: projectId } : {},
        ).hash),
      }),
    }));
    return node;
  }

  const select = (row, chapter) => {
    node.querySelectorAll('[data-export-chapter]').forEach((item) => {
      const current = item === row;
      if (current) item.setAttribute('aria-current', 'true');
      else item.removeAttribute('aria-current');
      item.tabIndex = current ? 0 : -1;
    });
    if (aggregate.player) {
      shell.player.set({
        state: 'active',
        src: aggregate.player.url || null,
        position: Math.max(0, Number(chapter.start_ms) || 0) / 1000,
        duration: Math.max(.01, (Number(aggregate.player.duration_ms) || Number(chapter.end_ms) || 1000) / 1000),
        title: chapter.name || `Chapter ${Number(chapter.order) + 1}`,
        subtitle: `Current Take · starts ${exportClock(chapter.start_ms)}`,
      });
    }
  };

  const chapterRow = (chapter, index) => {
    const row = document.createElement('li');
    row.className = 'export-chapter';
    row.dataset.exportChapter = chapter.chapter_id || String(index);
    row.tabIndex = index === 0 ? 0 : -1;
    row.append(
      exportText('span', 'export-chapter__number', Number(chapter.order ?? index) + 1),
      exportText('strong', 'export-chapter__title', chapter.name || `Chapter ${index + 1}`),
      exportText('span', 'timecode export-chapter__duration', exportClock(Number(chapter.end_ms) - Number(chapter.start_ms))),
    );
    row.addEventListener('click', () => select(row, chapter));
    row.addEventListener('keydown', (event) => {
      if (!['ArrowUp', 'ArrowDown', 'Home', 'End', 'Enter', ' '].includes(event.key)) return;
      event.preventDefault();
      if (event.key === 'Enter' || event.key === ' ') {
        select(row, chapter);
        return;
      }
      const rows = [...node.querySelectorAll('[data-export-chapter]')];
      const current = rows.indexOf(row);
      const target = event.key === 'Home' ? rows[0] : event.key === 'End' ? rows.at(-1)
        : rows[(current + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length];
      target?.click();
      target?.focus();
    });
    return row;
  };

  const chapterList = (items, offset = 0) => {
    const list = document.createElement('ol');
    list.className = 'export-chapter-list';
    items.forEach((chapter, index) => list.append(chapterRow(chapter, offset + index)));
    return list;
  };
  const visible = chapters.slice(0, 8);
  node.append(chapterList(visible));
  if (chapters.length > visible.length) {
    node.append(UI.disclosure({
      label: `Show all ${chapters.length} chapters`,
      content: chapterList(chapters.slice(visible.length), visible.length),
    }));
  }
  return node;
}
