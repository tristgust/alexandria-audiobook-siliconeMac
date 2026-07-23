'use strict';

const UI = globalThis.AlexandriaUI;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

export function createScriptPage(route) {
  const owner = document.createElement('article');
  owner.className = 'project-flow script-review';
  owner.dataset.routeOwner = route.path;
  owner.dataset.page = route.path;
  const title = UI.pageTitleBlock({
    title: 'Script',
    subtitle: 'Loading the authoritative Script and review state…',
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

function durationLabel(entry) {
  const seconds = Number(entry.duration_seconds ?? entry.duration ?? entry.estimated_duration_seconds);
  if (!Number.isFinite(seconds) || seconds <= 0) return '—';
  const rounded = Math.round(seconds);
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, '0')}`;
}

export function scriptEntryRow({ entry, index, issue, selected, select, openWorkflow }) {
  const row = document.createElement('li');
  row.className = 'script-entry';
  row.tabIndex = selected ? 0 : -1;
  row.dataset.entryIndex = String(index);
  row.setAttribute('aria-selected', String(Boolean(selected)));
  if (selected) row.setAttribute('aria-current', 'true');
  if (issue) row.dataset.issueType = issue.type;
  const speaker = text('strong', 'script-entry__speaker', entry.speaker || 'NARRATOR');
  if (issue) speaker.prepend(UI.icon('warning'));
  const body = text('p', 'script-entry__text', entry.text || '');
  const direction = text('p', 'script-entry__direction', entry.instruct || 'No delivery direction');
  if (!entry.instruct) direction.dataset.empty = '';
  const duration = text('span', 'timecode script-entry__duration', durationLabel(entry));
  const opener = UI.iconButton({ name: 'more', label: `Actions for entry ${index + 1}`, tooltip: 'Entry actions' });
  const menu = UI.popover({
    opener,
    label: `Script entry ${index + 1} actions`,
    items: [
      { label: issue ? issue.presentation.action : 'Generation options', onSelect: () => openWorkflow(issue?.presentation.workflow || 'generation') },
      { label: 'Provenance and versions', onSelect: () => openWorkflow('provenance') },
    ],
  });
  const activate = () => select(entry, index, row);
  row.addEventListener('click', (event) => { if (!event.target.closest('button, a')) activate(); });
  row.addEventListener('keydown', (event) => {
    if (!['Enter', ' '].includes(event.key)) return;
    event.preventDefault(); activate();
  });
  row.append(speaker, body, direction, duration, menu);
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
  const cover = UI.sourceCover({
    label: `No source cover is available for ${source.title || projectTitle || 'this source'}`,
    emptyLabel: String(source.type || 'Source').toUpperCase(),
  });
  cover.classList.add('script-source-cover');
  const identity = document.createElement('div');
  identity.className = 'script-source-context__identity';
  identity.append(
    text('div', 'utility-heading', 'Source'),
    text('h2', 'entity-title', source.title || source.filename || projectTitle || 'Current source'),
    text('p', 'metadata', [
      source.type ? `Source: ${String(source.type).toUpperCase()}` : 'Source selected',
      `Language: ${source.source_language || 'Not recorded'}`,
      source.filename || null,
    ].filter(Boolean).join(' · ')),
  );
  const location = document.createElement('div');
  location.className = 'script-source-context__location';
  location.append(
    text('div', 'utility-heading', 'Current location'),
    text('strong', '', 'Entire Script'),
    text('span', 'metadata', `${entries.length.toLocaleString()} entries · ${issueCount.toLocaleString()} unresolved issues`),
  );
  if (onChangeChapter) location.append(UI.button({
    label: 'Change chapter…', variant: 'quiet', size: 'compact', onClick: onChangeChapter,
  }));
  root.replaceChildren(cover, identity, location);
}

export function createScriptFilterBar({ search, onFilter }) {
  const toolbar = document.createElement('div');
  toolbar.className = 'script-review-toolbar';
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
  toolbar.append(filters, search);
  return toolbar;
}

export function updateScriptFilterBar(root, counts, selected) {
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
  if (blockers.length) {
    const summary = document.createElement('div');
    summary.className = 'script-review-summary';
    summary.append(
      UI.status({ tone: 'warning', label: `${blockers.length} blocking issue${blockers.length === 1 ? '' : 's'} remaining` }),
      text('p', 'metadata', 'Select an issue to review its source comparison and correction route.'),
    );
    root.append(summary);
    return;
  }
  const accepted = lifecycle?.accepted || lifecycle?.state === 'accepted';
  const summary = document.createElement('div');
  summary.className = 'script-review-summary';
  summary.append(
    UI.status({
      tone: accepted ? 'success' : 'information',
      label: accepted ? 'Script approved' : 'Ready for approval',
      domain: 'script-review',
      value: accepted ? 'approved' : 'ready_for_approval',
    }),
    text('p', 'metadata', accepted
      ? 'This exact Script version is ready for Cast.'
      : 'Review the selected entry context, then approve the current Script version.'),
  );
  root.append(summary);
}
