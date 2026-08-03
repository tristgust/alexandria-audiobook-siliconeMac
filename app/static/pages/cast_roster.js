'use strict';

import {
  CAST_FILTERS, castInitials, castStatus, castText, castVoiceLabel, castVoiceMethod,
} from './cast_model.js';
import { castScriptLineCount } from './cast_line_count.js';

const UI = globalThis.AlexandriaUI;

export function createCastRoster({
  master, getAggregate, getSelected, getFilter, getSearch, getSort,
  onSearch, onFilter, onSort, onSelect, onReviewScript, onOpenFullCastTasks,
}) {
  const lineCount = castScriptLineCount;

  function loading() {
    const list = document.createElement('ul');
    list.className = 'cast-roster__list';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', 'Characters');
    list.setAttribute('aria-busy', 'true');
    list.append(
      UI.skeleton({ kind: 'row', label: 'Loading character row' }),
      UI.skeleton({ kind: 'row', label: 'Loading character row' }),
      UI.skeleton({ kind: 'row', label: 'Loading character row' }),
    );
    const heading = castText('h1', 'cast-roster__title', 'Characters');
    heading.dataset.pageHeading = '';
    master.replaceChildren(
      heading,
      UI.skeleton({ kind: 'field', label: 'Loading character filters' }),
      list,
    );
  }

  function empty() {
    const heading = castText('h1', 'cast-roster__title', 'Characters');
    heading.dataset.pageHeading = '';
    master.replaceChildren(
      heading,
      UI.emptyState({
        title: 'No characters yet',
        body: 'Review Script to identify speaking roles before assigning voices.',
        action: UI.button({ label: 'Review Script', variant: 'secondary', onClick: onReviewScript }),
      }),
    );
  }

  function filters() {
    const aggregate = getAggregate() || {};
    const group = document.createElement('div');
    group.className = 'cast-roster__filters';
    group.setAttribute('role', 'group');
    group.setAttribute('aria-label', 'Filter characters');
    CAST_FILTERS.forEach(([value, label]) => {
      const count = aggregate.filters?.counts?.[value];
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'cast-roster__filter';
      button.dataset.castFilter = value;
      button.setAttribute('aria-pressed', String(getFilter() === value));
      button.append(
        castText('span', '', label),
        castText('span', 'timecode', Number.isFinite(count) ? count : '—'),
      );
      button.addEventListener('click', () => onFilter(getFilter() === value ? 'all' : value));
      group.append(button);
    });
    return group;
  }

  function selectRow(row, characterId, focus = false) {
    if (focus) row.focus();
    if (onSelect(characterId, row) === false) return;
    master.querySelectorAll('[role="option"]').forEach((item) => {
      const current = item === row;
      item.setAttribute('aria-selected', String(current));
      item.tabIndex = current ? 0 : -1;
    });
  }

  const methodIcon = (character) => {
    const method = castVoiceMethod(character);
    if (['clone', 'supplied_recording_clone'].includes(method)) return 'waveform';
    if (['controlled_clone', 'instruction_controlled_clone'].includes(method)) return 'sliders';
    if (['design', 'designed', 'designed_voice', 'voice_design'].includes(method)) return 'wand';
    if (['adapter', 'lora', 'trained_voice'].includes(method)) return 'layers';
    if (method === 'alias') return 'link';
    return character.speaking_role === 'non_speaking' ? 'volume-off' : 'microphone';
  };

  const statusIcon = (tone) => ({
    success: 'check',
    warning: 'warning',
    error: 'error',
    neutral: 'minus',
  })[tone] || 'info';

  function rowFor(character, index) {
    const aggregate = getAggregate() || {};
    const row = document.createElement('li');
    row.className = 'cast-roster__row';
    row.setAttribute('role', 'option');
    row.setAttribute('aria-selected', String(character.character_id === aggregate.selected_character_id));
    row.tabIndex = character.character_id === aggregate.selected_character_id
      || (!aggregate.selected_character_id && index === 0) ? 0 : -1;
    row.dataset.characterId = character.character_id;
    const portrait = UI.monogram({
      initials: castInitials(character.display_name),
      label: `Monogram for ${character.display_name}`,
    });
    const body = document.createElement('span');
    body.className = 'cast-roster__row-body';
    const scriptLabel = character.identity?.script_voice_label
      || character.script_connection?.resolved_script_voice_label;
    const role = character.character?.summary?.role || character.identity?.role;
    const meta = document.createElement('span');
    meta.className = 'cast-roster__meta';
    meta.append(
      castText('span', 'metadata', scriptLabel || role
        || (character.speaking_role === 'speaking' ? 'Speaking role' : 'Non-speaking')),
    );
    if (lineCount(character) > 0) meta.append(castText(
      'span', 'metadata', `${lineCount(character).toLocaleString()} lines`,
    ));
    const status = castStatus(character);
    const state = document.createElement('span');
    state.className = 'cast-roster__status';
    state.dataset.tone = status.tone;
    state.append(UI.icon(statusIcon(status.tone)), document.createTextNode(status.label));
    meta.append(state);
    const voiceSummary = document.createElement('span');
    voiceSummary.className = 'cast-roster__voice-summary metadata';
    voiceSummary.append(
      UI.icon(methodIcon(character)),
      document.createTextNode(castVoiceLabel(character)),
    );
    body.append(
      castText('strong', 'cast-roster__name', character.display_name),
      meta,
      voiceSummary,
    );
    row.append(portrait, body);
    row.addEventListener('click', () => selectRow(row, character.character_id));
    row.addEventListener('keydown', (event) => {
      if (!['ArrowUp', 'ArrowDown', 'Home', 'End', 'Enter', ' '].includes(event.key)) return;
      event.preventDefault();
      if (['Enter', ' '].includes(event.key)) {
        selectRow(row, character.character_id);
        return;
      }
      const rows = [...master.querySelectorAll('[role="option"]')];
      const current = rows.indexOf(row);
      const target = event.key === 'Home' ? rows[0] : event.key === 'End' ? rows.at(-1)
        : rows[(current + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length];
      selectRow(target, target.dataset.characterId, true);
    });
    return row;
  }

  function render() {
    const aggregate = getAggregate() || {};
    const selected = getSelected();
    const header = document.createElement('header');
    header.className = 'cast-roster__header';
    const title = castText('h1', 'cast-roster__title', 'Characters');
    title.dataset.pageHeading = '';
    const headerActions = document.createElement('div');
    headerActions.className = 'cast-roster__header-actions';
    headerActions.append(
      castText('span', 'metadata', `${aggregate.characters?.length || 0} shown`),
      UI.button({
        label: 'Full Cast tasks',
        variant: 'quiet',
        size: 'compact',
        onClick: (event) => onOpenFullCastTasks?.(event.currentTarget),
        attributes: { 'data-full-cast-tasks': '' },
      }),
    );
    header.append(title, headerActions);
    const searchField = UI.searchField({
      label: 'Search characters', placeholder: 'Search characters…',
      iconClass: 'fas fa-magnifying-glass',
    });
    const searchInput = searchField.querySelector('input');
    searchInput.value = getSearch();
    searchInput.addEventListener('input', () => onSearch(searchInput.value));
    const sortField = UI.field({
      kind: 'select',
      label: 'Sort characters',
      value: getSort?.() || 'script_order',
      options: [
        { value: 'script_order', label: 'Script order' },
        { value: 'lines_desc', label: 'Most Script lines' },
        { value: 'lines_asc', label: 'Fewest Script lines' },
      ],
    });
    sortField.classList.add('cast-roster__sort');
    sortField.querySelector('select').dataset.castSort = '';
    sortField.querySelector('select').addEventListener('change', (event) => onSort?.(event.target.value));
    const rows = document.createElement('ul');
    rows.className = 'cast-roster__list';
    rows.setAttribute('role', 'listbox');
    rows.setAttribute('aria-label', 'Characters');
    const characters = [...(aggregate.characters || [])];
    if (getSort?.() === 'lines_desc') characters.sort((left, right) => lineCount(right) - lineCount(left));
    if (getSort?.() === 'lines_asc') characters.sort((left, right) => lineCount(left) - lineCount(right));
    characters.forEach((character, index) => rows.append(rowFor(character, index)));
    const tools = document.createElement('div');
    tools.className = 'cast-roster__tools';
    tools.append(searchField, sortField, filters());
    master.replaceChildren(header, tools);
    if (aggregate.selection_visible === false && selected) {
      master.append(UI.notice({
        tone: 'information',
        title: 'Selected character is filtered out',
        body: `${selected.display_name} remains open in the profile.`,
      }));
    }
    if (rows.children.length) {
      master.append(rows);
      const selectedId = aggregate.selected_character_id || selected?.character_id;
      if (selectedId) {
        const selectedRow = [...rows.querySelectorAll('[role="option"]')]
          .find((row) => row.dataset.characterId === selectedId);
        if (selectedRow) {
          requestAnimationFrame(() => {
            if (selectedRow.isConnected) selectedRow.scrollIntoView({ block: 'center' });
          });
        }
      }
    } else master.append(UI.emptyState({
      title: 'No matching characters',
      body: 'Clear the search or choose another filter.',
    }));
  }

  return Object.freeze({ loading, empty, render });
}
