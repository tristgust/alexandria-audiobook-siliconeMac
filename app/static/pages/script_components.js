'use strict';

const UI = globalThis.AlexandriaUI;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

function displaySourceFilename(filename, sourceTitle, projectTitle) {
  const basename = String(filename || '').split('/').at(-1) || '';
  if (!basename) return null;
  const stem = basename.replace(/\.[^.]+$/, '');
  const normalizedStem = stem.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim().toLocaleLowerCase();
  const redundantTitles = [sourceTitle, projectTitle]
    .map((value) => String(value || '').trim().toLocaleLowerCase())
    .filter(Boolean);
  if (redundantTitles.includes(normalizedStem) || /[_-]/.test(stem) || /^[a-z0-9]{1,4}[_-]/i.test(stem)) {
    return null;
  }
  return basename;
}

export function createScriptPage(route) {
  const owner = document.createElement('article');
  owner.className = 'project-flow script-review';
  owner.dataset.routeOwner = route.path;
  owner.dataset.page = route.path;
  const title = UI.pageTitleBlock({
    title: 'Script',
    subtitle: 'Review speaker attribution, delivery, and source fidelity before production.',
  });
  title.querySelector('h1').dataset.pageHeading = '';
  title.querySelector('.page-subtitle').dataset.scriptPageSubtitle = '';
  owner.append(title);
  return owner;
}

export function scriptStageStates(flow) {
  const stages = flow?.stage_map || {};
  return Object.fromEntries(['script', 'cast', 'produce', 'export'].map((name) => [
    name, stages[name]?.state || (name === 'script' ? 'current' : 'future'),
  ]));
}

export function scriptEntryRow({ entry, index, issue, selected, select }) {
  const row = document.createElement('li');
  row.className = 'script-entry';
  row.tabIndex = selected ? 0 : -1;
  row.dataset.entryIndex = String(index);
  row.setAttribute('aria-selected', String(Boolean(selected)));
  if (selected) row.setAttribute('aria-current', 'true');
  if (issue) row.dataset.issueType = issue.type;
  const entryNumber = text('span', 'script-entry__index', index + 1);
  entryNumber.setAttribute('aria-hidden', 'true');
  if (issue) {
    const issueMark = document.createElement('span');
    issueMark.className = 'script-entry__issue';
    issueMark.append(UI.icon('warning'));
    entryNumber.prepend(issueMark);
  }
  const speaker = text('strong', 'script-entry__speaker', entry.speaker || 'NARRATOR');
  const copy = document.createElement('div');
  copy.className = 'script-entry__copy';
  const body = text('p', 'script-entry__text', entry.text || '');
  const direction = text('p', 'script-entry__direction', entry.instruct || 'No delivery direction recorded.');
  const directionLabel = text('span', 'visually-hidden', 'Delivery direction: ');
  direction.prepend(directionLabel);
  if (!entry.instruct) direction.dataset.empty = '';
  copy.append(body, direction);
  const menu = document.createElement('span');
  menu.className = 'script-entry__menu';
  menu.setAttribute('aria-hidden', 'true');
  menu.append(UI.icon('more'));
  const activate = () => select(entry, index, row);
  row.addEventListener('click', (event) => { if (!event.target.closest('button, a')) activate(); });
  row.addEventListener('keydown', (event) => {
    if (!['Enter', ' '].includes(event.key)) return;
    event.preventDefault(); activate();
  });
  row.append(entryNumber, speaker, copy, menu);
  return row;
}

export function scriptEntryContext({
  entry, index, total, issue, issuePosition, issueTotal, onPrevious, onNext, openWorkflow,
}) {
  const detail = document.createElement('div');
  detail.className = 'script-entry-detail';
  detail.setAttribute('aria-live', 'polite');
  detail.append(
    text('div', 'metadata', issue ? 'Selected issue' : 'Selected entry'),
    text('h3', 'entity-title', issue?.title || entry.speaker || 'NARRATOR'),
    text('div', 'metadata', issue
      ? `Entry ${index + 1} · ${issuePosition + 1} of ${issueTotal}`
      : `Entry ${index + 1} of ${total.toLocaleString()}`),
  );
  if (issue) {
    detail.append(
      UI.status({ tone: 'warning', label: issue.label, domain: 'script-review', value: issue.type }),
      UI.flatSection({ title: 'Why this needs review', body: issue.explanation }),
    );
    const comparison = document.createElement('div');
    comparison.className = 'script-comparison';
    comparison.append(
      UI.flatSection({ title: 'Source', eyebrow: 'Source versus Script', body: issue.sourceText }),
      UI.flatSection({ title: 'Script', body: issue.outputText }),
    );
    detail.append(comparison);
    const issueActions = document.createElement('div');
    issueActions.className = 'script-inspector-actions';
    issueActions.append(
      UI.button({ label: issue.presentation.action, variant: 'secondary', onClick: () => openWorkflow(issue.presentation.workflow) }),
      UI.button({ label: 'Leave unresolved', variant: 'quiet' }),
    );
    const issueNavigation = document.createElement('div');
    issueNavigation.className = 'script-issue-navigation';
    issueNavigation.append(
      UI.button({ label: 'Previous issue', variant: 'quiet', disabled: issuePosition <= 0, onClick: onPrevious }),
      text('span', 'metadata', `${issuePosition + 1} of ${issueTotal}`),
      UI.button({ label: 'Next issue', variant: 'quiet', disabled: issuePosition >= issueTotal - 1, onClick: onNext }),
    );
    detail.append(issueActions, issueNavigation);
  } else {
    detail.append(UI.flatSection({ title: 'Script', body: entry.text || 'No Script text is available.' }));
    if (entry.instruct) detail.append(UI.flatSection({
      title: 'Delivery direction', content: text('p', 'script-entry-detail__direction', entry.instruct),
    }));
    detail.append(UI.flatSection({ title: 'Review context', body: 'No entry-specific issue is recorded.' }));
  }
  return detail;
}

export function renderScriptSourceContext({
  root, flow, lifecycle, entries, projectTitle, issueCount, onChangeChapter,
}) {
  const source = flow?.source || {};
  const filenameStem = String(source.filename || '').split('/').at(-1)?.replace(/\.[^.]+$/, '') || '';
  const sourceTitle = source.title && source.title !== filenameStem
    ? source.title : projectTitle || source.title || source.filename || 'Current source';
  const cover = UI.sourceCover({
    label: `No source cover is available for ${sourceTitle || 'this source'}`,
    iconClass: 'fas fa-book-open',
  });
  cover.classList.add('script-source-cover');
  const identity = document.createElement('div');
  identity.className = 'script-source-context__identity';
  identity.append(
    text('div', 'utility-heading', 'Source'),
    text('h2', 'entity-title', sourceTitle),
    text('p', 'metadata', [
      source.type ? `Source: ${String(source.type).toUpperCase()}` : 'Source selected',
      `Language: ${source.source_language || 'Not recorded'}`,
      displaySourceFilename(source.filename, source.title, projectTitle),
    ].filter(Boolean).join(' · ')),
  );
  const location = document.createElement('div');
  location.className = 'script-source-context__location';
  location.append(
    text('div', 'utility-heading', 'Current location'),
    text('strong', '', 'Entire Script'),
    text('span', 'metadata', [
      `${entries.length.toLocaleString()} entries`,
      issueCount ? `${issueCount.toLocaleString()} unresolved issue${issueCount === 1 ? '' : 's'}` : '',
    ].filter(Boolean).join(' · ')),
  );
  if (onChangeChapter) location.append(UI.button({
    label: 'Change chapter…', variant: 'quiet', size: 'compact', onClick: onChangeChapter,
  }));
  root.replaceChildren(cover, identity, location);
}

export function createScriptFilterBar({ search, onFilter }) {
  const toolbar = document.createElement('div');
  toolbar.className = 'script-review-toolbar';
  const filterWrap = document.createElement('div');
  filterWrap.className = 'script-issue-filter-wrap';
  const filterLabel = text('span', 'script-filter-label', 'Issue filter:');
  const filters = document.createElement('div');
  filters.className = 'script-issue-filters';
  filters.setAttribute('role', 'radiogroup');
  filters.setAttribute('aria-label', 'Issue filter');
  [
    ['all', 'All'],
    ['uncertain_speaker', 'Uncertain speaker'],
    ['delivery_direction', 'Delivery direction'],
    ['source_mismatch', 'Source mismatch'],
  ].forEach(([value, label], index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'script-issue-filter';
    button.dataset.scriptFilter = value;
    button.setAttribute('role', 'radio');
    button.setAttribute('aria-checked', String(index === 0));
    button.append(
      text('span', 'script-issue-filter__label', label),
      text('strong', 'script-issue-filter__count', '0'),
    );
    button.addEventListener('click', () => onFilter(value));
    filters.append(button);
  });
  filterWrap.append(filterLabel, filters);
  toolbar.append(filterWrap, search);
  return toolbar;
}

export function updateScriptFilterBar(root, counts, selected) {
  const unresolved = ['uncertain_speaker', 'delivery_direction', 'source_mismatch']
    .reduce((total, value) => total + (Number(counts[value]) || 0), 0);
  const wrap = root.querySelector('.script-issue-filter-wrap');
  const label = root.querySelector('.script-filter-label');
  const filters = root.querySelector('.script-issue-filters');
  if (wrap) {
    wrap.dataset.empty = String(unresolved === 0);
    wrap.hidden = unresolved === 0;
  }
  if (label) label.textContent = 'Issue filter:';
  if (filters) filters.hidden = false;
  root.querySelectorAll('[data-script-filter]').forEach((button) => {
    const value = button.dataset.scriptFilter;
    const count = Number(counts[value]) || 0;
    const active = value === selected;
    button.querySelector('.script-issue-filter__count').textContent = count.toLocaleString();
    button.setAttribute('aria-checked', String(active));
    button.tabIndex = active ? 0 : -1;
    button.disabled = value !== 'all' && count === 0;
    button.setAttribute('aria-label', `${button.querySelector('.script-issue-filter__label').textContent}, ${count} unresolved`);
  });
}

export function renderScriptReviewStatus(root, lifecycle, issues = []) {
  const blockers = issues.filter((item) => item.blocking);
  root.replaceChildren();
  root.hidden = blockers.length === 0;
  if (!blockers.length) return;

  const counts = blockers.reduce((result, issue) => {
    result[issue.type] = (result[issue.type] || 0) + 1;
    return result;
  }, {});
  const summary = document.createElement('section');
  summary.className = 'script-blocker-summary';
  summary.setAttribute('aria-label', 'Blocking Script issues');
  const marker = document.createElement('span');
  marker.className = 'script-blocker-summary__marker';
  marker.setAttribute('aria-hidden', 'true');
  marker.append(UI.icon('warning'));
  const copy = document.createElement('div');
  copy.append(
    text('strong', '', `${blockers.length} blocking issue${blockers.length === 1 ? '' : 's'} remaining`),
    text('span', '', 'Use the selected issue inspector to choose the appropriate reviewed correction path.'),
  );
  const countList = document.createElement('div');
  countList.className = 'script-blocker-summary-counts';
  Object.entries(counts).forEach(([type, count]) => {
    countList.append(text('span', '', `${type.replaceAll('_', ' ')} · ${count}`));
  });
  summary.append(marker, copy, countList);
  root.append(summary);
}
