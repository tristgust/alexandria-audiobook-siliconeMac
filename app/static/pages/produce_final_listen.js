'use strict';

import {
  produceDuration,
  produceText,
} from './produce_model.js';

const UI = globalThis.AlexandriaUI;

function currentTake(selected) {
  const takes = Array.isArray(selected.takes?.items) ? selected.takes.items : [];
  return takes.find((take) => take.current) || null;
}

function playTransition(shell, row, label) {
  if (!row?.audio?.available || !row.audio.url) return;
  shell.player.set({
    state: 'playing',
    src: row.audio.url,
    position: 0,
    duration: Math.max(.01, (Number(row.duration_ms) || 1000) / 1000),
    title: `${label} · ${row.speaker || 'Narrator'}`,
    subtitle: row.text || 'Chapter transition audio',
  });
}

function transitionRow(shell, row, label) {
  const item = document.createElement('article');
  item.className = 'produce-final-listen-transition';
  item.dataset.finalListenTransition = label.toLocaleLowerCase();
  const copy = document.createElement('div');
  copy.append(
    produceText('span', 'metadata', label),
    produceText('strong', '', row?.speaker || 'Not available'),
    produceText('p', 'metadata', row?.text || 'No adjacent production chunk.'),
  );
  const play = UI.button({
    label: `Play ${label.toLocaleLowerCase()}`,
    variant: 'secondary',
    size: 'compact',
    disabled: !row?.audio?.available || !row?.audio?.url,
    attributes: { 'data-final-listen-play': label.toLocaleLowerCase() },
    onClick: () => playTransition(shell, row, label),
  });
  item.append(copy, play);
  return item;
}

function numberField({ id, label, value, description, min = 0, max = 30000, data }) {
  return UI.field({
    id,
    label,
    type: 'number',
    value: value == null ? '' : String(value),
    description,
    attributes: {
      min,
      max,
      step: 10,
      inputmode: 'numeric',
      [data]: '',
    },
  });
}

function actionPanel(title, body) {
  const panel = document.createElement('fieldset');
  panel.className = 'produce-final-listen-operation';
  const legend = document.createElement('legend');
  legend.textContent = title;
  panel.append(legend, produceText('p', 'metadata', body));
  return panel;
}

export function createFinalListenSection({ selected, aggregate, shell, actions }) {
  const section = document.createElement('section');
  section.className = 'produce-final-listen';
  section.dataset.produceFinalListen = '';
  const current = currentTake(selected);
  const state = selected.final_listen || {};
  const transition = state.transition || {};
  const heading = document.createElement('header');
  heading.className = 'produce-final-listen-heading';
  const copy = document.createElement('div');
  copy.append(
    produceText('span', 'utility-heading', 'Chapter Assembly'),
    produceText('h3', '', 'Final Listen'),
  );
  const pinState = state.current_take_pinned ? 'Pinned' : 'Not pinned';
  heading.append(copy, UI.status({
    label: pinState,
    tone: state.current_take_pinned ? 'success' : 'warning',
  }));
  section.append(heading);

  if (!current || !state.can_process) {
    section.append(UI.notice({
      tone: 'information',
      title: 'Current audio required',
      body: 'Generate or select one current Take before making Final Listen corrections.',
    }));
    return section;
  }

  const chapter = transition.chapter;
  const chapterCopy = chapter
    ? `${chapter.name} · ${produceDuration(Number(chapter.end_ms) - Number(chapter.start_ms))}`
    : 'Chapter markers are not available for this chunk.';
  section.append(produceText('p', 'produce-final-listen-chapter', chapterCopy));

  const pinRow = document.createElement('div');
  pinRow.className = 'produce-final-listen-primary';
  pinRow.append(
    produceText(
      'p',
      'metadata',
      state.current_take_pinned
        ? 'This exact Take, lineage, and canonical source order are approved for publication.'
        : 'Pin only after listening to this exact current Take in chapter context.',
    ),
    UI.button({
      label: state.current_take_pinned ? 'Remove Final Listen pin' : 'Pin current Take',
      variant: state.current_take_pinned ? 'secondary' : 'primary',
      disabled: actions.busy || aggregate.process?.running,
      attributes: { 'data-final-listen-pin': '' },
      onClick: () => actions.pinFinalListen(selected, current),
    }),
  );
  section.append(pinRow);

  const transitions = document.createElement('div');
  transitions.className = 'produce-final-listen-transitions';
  transitions.setAttribute('aria-label', 'Adjacent chapter transition audio');
  transitions.append(
    transitionRow(shell, transition.previous, 'Previous'),
    transitionRow(shell, transition.current, 'Current'),
    transitionRow(shell, transition.next, 'Next'),
  );
  section.append(transitions);

  const pausePanel = actionPanel(
    'Pause after this line',
    'This changes assembly timing only. Leave blank to return to the configured speaker-transition default.',
  );
  const pause = numberField({
    id: `final-listen-pause-${selected.index}`,
    label: 'Pause (milliseconds)',
    value: selected.pause_after_ms,
    description: `Current transition after this line: ${Number(transition.transition_after_ms || 0)} ms.`,
    data: 'data-final-listen-pause',
  });
  pausePanel.append(pause, UI.button({
    label: 'Apply pause',
    variant: 'secondary',
    disabled: actions.busy || aggregate.process?.running,
    attributes: { 'data-final-listen-pause-apply': '' },
    onClick: () => {
      const raw = pause.querySelector('input').value.trim();
      actions.updateFinalListenPause(
        selected,
        current,
        raw === '' ? null : Number.parseInt(raw, 10),
      );
    },
  }));

  const trimPanel = actionPanel(
    'Trim edge defects',
    'Creates a child rendition. It can remove only the beginning or end; the raw Take remains unchanged.',
  );
  const trimFields = document.createElement('div');
  trimFields.className = 'produce-final-listen-fields';
  const trimStart = numberField({
    id: `final-listen-trim-start-${selected.index}`,
    label: 'Trim from start (ms)',
    value: 0,
    data: 'data-final-listen-trim-start',
  });
  const trimEnd = numberField({
    id: `final-listen-trim-end-${selected.index}`,
    label: 'Trim from end (ms)',
    value: 0,
    data: 'data-final-listen-trim-end',
  });
  trimFields.append(trimStart, trimEnd);
  trimPanel.append(trimFields, UI.button({
    label: 'Create trimmed rendition',
    variant: 'secondary',
    disabled: actions.busy || aggregate.process?.running,
    attributes: { 'data-final-listen-trim-apply': '' },
    onClick: () => actions.createFinalListenRendition(selected, current, 'trim_edges', {
      trim_start_ms: Number.parseInt(trimStart.querySelector('input').value || '0', 10),
      trim_end_ms: Number.parseInt(trimEnd.querySelector('input').value || '0', 10),
    }),
  }));

  const splitPanel = actionPanel(
    'Split one problematic delivery',
    'Creates one child audio rendition with an internal pause. The Script remains one chunk in the same canonical order.',
  );
  const splitFields = document.createElement('div');
  splitFields.className = 'produce-final-listen-fields';
  const splitAt = numberField({
    id: `final-listen-split-at-${selected.index}`,
    label: 'Split at (ms)',
    value: Math.max(50, Math.round(Number(selected.duration_ms || 1000) / 2)),
    min: 50,
    max: Math.max(50, Number(selected.duration_ms || 1000) - 50),
    data: 'data-final-listen-split-at',
  });
  const splitPause = numberField({
    id: `final-listen-split-pause-${selected.index}`,
    label: 'Inserted pause (ms)',
    value: 300,
    min: 20,
    max: 5000,
    data: 'data-final-listen-split-pause',
  });
  splitFields.append(splitAt, splitPause);
  splitPanel.append(splitFields, UI.button({
    label: 'Create split rendition',
    variant: 'secondary',
    disabled: actions.busy || aggregate.process?.running,
    attributes: { 'data-final-listen-split-apply': '' },
    onClick: () => actions.createFinalListenRendition(selected, current, 'split_with_pause', {
      split_at_ms: Number.parseInt(splitAt.querySelector('input').value || '0', 10),
      pause_ms: Number.parseInt(splitPause.querySelector('input').value || '0', 10),
    }),
  }));

  const corrections = document.createElement('details');
  corrections.className = 'produce-inspector-disclosure produce-final-listen-corrections';
  corrections.dataset.finalListenCorrections = '';
  const summary = document.createElement('summary');
  summary.append(
    produceText('span', '', 'Bounded corrections'),
    UI.icon('chevron'),
  );
  summary.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    corrections.open = !corrections.open;
  });
  corrections.append(summary, pausePanel, trimPanel, splitPanel);
  section.append(corrections);
  return section;
}
