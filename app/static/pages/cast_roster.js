'use strict';

import {
  CAST_FILTERS, castInitials, castStatus, castText,
} from './cast_model.js';

const UI = globalThis.AlexandriaUI;

export function createCastRoster({
  master, getAggregate, getSelected, getFilter, getSearch,
  onSearch, onFilter, onSelect, onReviewScript,
}) {
  function loading() {
    const list = document.createElement('ul');
    list.className = 'cast-roster__list';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', 'Characters');
    list.setAttribute('aria-busy', 'true');
    list.append(
      UI.skeleton({ label: 'Loading character list' }),
      UI.skeleton({ label: 'Loading character list' }),
    );
    const heading = castText('h1', 'cast-roster__title', 'Characters');
    heading.dataset.pageHeading = '';
    master.replaceChildren(
      heading,
      UI.skeleton({ label: 'Loading character filters' }),
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
    group.setAttribute('aria-label', 'Filter characters');
    CAST_FILTERS.forEach(([value, label]) => {
      const count = aggregate.filters?.counts?.[value];
      const chip = UI.filterChip({
        label: Number.isFinite(count) ? `${label} ${count}` : label,
        pressed: getFilter() === value,
      });
      chip.dataset.castFilter = value;
      chip.querySelector('button').addEventListener('click', () => {
        onFilter(getFilter() === value ? 'all' : value);
      });
      group.append(chip);
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
    body.append(
      castText('strong', 'cast-roster__name', character.display_name),
      castText('span', 'metadata', [
        scriptLabel ? `Script label: ${scriptLabel}` : null,
        role || (character.speaking_role === 'speaking' ? 'Speaking role' : 'Non-speaking'),
      ].filter(Boolean).join(' · ')),
    );
    row.append(portrait, body, UI.status({ ...castStatus(character), domain: 'cast' }));
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
    const heading = document.createElement('header');
    heading.className = 'cast-roster__header';
    const title = castText('h1', 'cast-roster__title', 'Characters');
    title.dataset.pageHeading = '';
    heading.append(
      title,
      castText('span', 'metadata', `${aggregate.characters?.length || 0} shown`),
    );
    const searchField = UI.searchField({ label: 'Search characters', placeholder: 'Search characters…' });
    const searchInput = searchField.querySelector('input');
    searchInput.value = getSearch();
    searchInput.addEventListener('input', () => onSearch(searchInput.value));
    const rows = document.createElement('ul');
    rows.className = 'cast-roster__list';
    rows.setAttribute('role', 'listbox');
    rows.setAttribute('aria-label', 'Characters');
    (aggregate.characters || []).forEach((character, index) => rows.append(rowFor(character, index)));
    master.replaceChildren(heading, searchField, filters());
    if (aggregate.selection_visible === false && selected) {
      master.append(UI.notice({
        tone: 'information',
        title: 'Selected character is filtered out',
        body: `${selected.display_name} remains open in the profile.`,
      }));
    }
    if (rows.children.length) master.append(rows);
    else master.append(UI.emptyState({
      title: 'No matching characters',
      body: 'Clear the search or choose another filter.',
    }));
  }

  return Object.freeze({ loading, empty, render });
}
