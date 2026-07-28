'use strict';

import {
  filteredEntries, filteredIssues, issueCounts, normalizeIssues,
} from './script_review_model.js';
import {
  scriptEntryContext, scriptEntryRow, updateScriptFilterBar,
} from './script_components.js';

const UI = globalThis.AlexandriaUI;
const entryPageSize = () => {
  const layout = document.querySelector('.app-shell')?.dataset.layout;
  return layout === 'narrow' ? 30 : layout === 'compact' ? 60 : 80;
};

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

export function createScriptReviewController({
  content, toolbar, search, inspector, workflows, getModel, getFilter, setFilter,
}) {
  let visibleEntryLimit = entryPageSize();
  let selectedEntryIndex = null;
  let selectedIssueId = null;
  let inspectorState = document.querySelector('.app-shell')?.dataset.inspectorLayout === 'inline'
    ? 'open' : 'collapsed';

  const currentIssues = () => {
    const model = getModel();
    return normalizeIssues({
      lifecycle: model.lifecycle, auditIssues: model.auditIssues, entries: model.entries,
    });
  };
  const issueForEntry = (index, issues = currentIssues()) => issues
    .find((item) => item.entryIndex === index) || null;
  const currentQuery = () => search.querySelector('input').value;

  const visibleIssueSet = () => {
    const model = getModel();
    return filteredIssues(currentIssues(), getFilter(), currentQuery(), model.entries);
  };

  function showEntryContext(entry, index, state = inspectorState, opener = null) {
    inspectorState = state;
    const issues = visibleIssueSet();
    const issue = issues.find((item) => item.id === selectedIssueId) || issueForEntry(index, issues);
    const issuePosition = issue ? issues.findIndex((item) => item.id === issue.id) : -1;
    inspector.setContent(scriptEntryContext({
      entry, index, total: getModel().entries.length, issue,
      issuePosition, issueTotal: issues.length,
      onPrevious: () => moveIssue(-1), onNext: () => moveIssue(1),
      openWorkflow: workflows.open,
    }));
    if (state === 'open') inspector.open(opener);
    else inspector.close({ restoreFocus: false });
  }

  function selectIssue(issue, { open = true, scroll = false } = {}) {
    if (!issue) return;
    selectedIssueId = issue.id;
    if (issue.entryIndex !== null) selectedEntryIndex = issue.entryIndex;
    if (open) inspectorState = 'open';
    render();
    if (scroll) content.querySelector(`[data-entry-index="${selectedEntryIndex}"]`)
      ?.scrollIntoView({ block: 'center' });
  }

  function moveIssue(direction) {
    const issues = visibleIssueSet();
    const current = issues.findIndex((issue) => issue.id === selectedIssueId);
    const target = issues[Math.max(0, Math.min(issues.length - 1, current + direction))];
    if (target) selectIssue(target, { scroll: true });
  }

  function selectEntry(entry, index, row, state = 'open') {
    selectedEntryIndex = index;
    selectedIssueId = issueForEntry(index)?.id || '__none__';
    content.querySelectorAll('.script-entry').forEach((item) => {
      item.removeAttribute('aria-current');
      item.setAttribute('aria-selected', 'false');
      item.tabIndex = -1;
    });
    row?.setAttribute('aria-current', 'true');
    row?.setAttribute('aria-selected', 'true');
    if (row) row.tabIndex = 0;
    showEntryContext(entry, index, state, row);
  }

  function emptyState(model) {
    selectedEntryIndex = null;
    selectedIssueId = null;
    inspectorState = 'collapsed';
    inspector.setContent(UI.emptyState({
      iconClass: 'far fa-file-lines',
      title: 'No entry selected',
      body: 'Choose a Script entry to inspect its full text and review context.',
    }));
    inspector.close({ restoreFocus: false });
    content.dataset.state = 'empty';
    content.append(UI.emptyState({
      iconClass: model.entries.length ? 'fas fa-filter-circle-xmark' : 'far fa-file-lines',
      title: model.entries.length ? 'No Script entries match these filters' : 'No Script yet',
      body: model.entries.length
        ? 'Clear the issue filter or search to restore the Script.'
        : 'Use Generation options to generate or import a Script.',
      action: model.entries.length ? UI.button({
        label: 'Clear filters', variant: 'quiet', onClick: () => {
          setFilter('all');
          search.querySelector('input').value = '';
          render({ reset: true });
        },
      }) : UI.button({
        label: 'Generation options', variant: 'secondary',
        onClick: () => workflows.open('generation'),
      }),
    }));
  }

  function footerNode(entries, visibleEntries, issues) {
    const footer = document.createElement('div');
    footer.className = 'script-list-footer';
    footer.dataset.scriptCollectionFooter = '';
    const range = visibleEntries.length
      ? `${visibleEntries[0].index + 1}–${visibleEntries.at(-1).index + 1}` : '0';
    footer.append(text('span', 'metadata',
      `Showing ${range} of ${getModel().entries.length.toLocaleString()} entries`));
    if (issues.length) {
      const position = issues.findIndex((issue) => issue.id === selectedIssueId);
      footer.append(
        UI.button({ label: 'Previous issue', variant: 'quiet', size: 'compact', disabled: position <= 0, onClick: () => moveIssue(-1) }),
        text('strong', 'metadata', `${Math.max(position + 1, 1)} of ${issues.length}`),
        UI.button({ label: 'Next issue', variant: 'quiet', size: 'compact', disabled: position >= issues.length - 1, onClick: () => moveIssue(1) }),
      );
    }
    if (visibleEntryLimit < entries.length) {
      const remaining = entries.length - visibleEntryLimit;
      footer.append(UI.button({
        label: `Load ${Math.min(entryPageSize(), remaining).toLocaleString()} more`,
        variant: 'secondary', size: 'compact', attributes: { 'data-script-load-more': '' },
        onClick: () => { visibleEntryLimit += entryPageSize(); render(); },
      }));
    }
    return footer;
  }

  function render({ reset = false } = {}) {
    if (reset) { visibleEntryLimit = entryPageSize(); selectedEntryIndex = null; }
    const model = getModel();
    const issues = currentIssues();
    const entries = filteredEntries({
      entries: model.entries, issues, filter: getFilter(), query: currentQuery(),
    });
    const visibleIssues = visibleIssueSet();
    updateScriptFilterBar(toolbar, issueCounts(issues), getFilter());
    content.querySelectorAll('.popover-controller').forEach((node) => node.popoverCleanup?.());
    content.replaceChildren();
    content.dataset.state = entries.length > 30 ? 'dense' : 'success';
    if (!entries.length) { emptyState(model); return; }
    const selectedIssue = visibleIssues.find((issue) => issue.id === selectedIssueId)
      || visibleIssues.find((issue) => issue.entryIndex === selectedEntryIndex)
      || (selectedIssueId !== '__none__' ? visibleIssues[0] : null);
    if (selectedIssue) {
      selectedIssueId = selectedIssue.id;
      if (selectedIssue.entryIndex !== null) selectedEntryIndex = selectedIssue.entryIndex;
    }
    if (!entries.some((item) => item.index === selectedEntryIndex)) {
      selectedEntryIndex = selectedIssue?.entryIndex ?? entries[0].index;
    }
    const visibleEntries = entries.slice(0, visibleEntryLimit);
    const selectedEntry = entries.find((item) => item.index === selectedEntryIndex);
    if (selectedEntry && !visibleEntries.some((item) => item.index === selectedEntryIndex)) {
      visibleEntries.push(selectedEntry);
    }
    const list = document.createElement('ol');
    list.className = 'script-entry-list';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', 'Script entries');
    visibleEntries.forEach(({ entry, index, issue }) => list.append(scriptEntryRow({
      entry, index, issue, selected: index === selectedEntryIndex,
      select: selectEntry,
    })));
    content.append(list, footerNode(entries, visibleEntries, visibleIssues));
    if (selectedEntry) showEntryContext(selectedEntry.entry, selectedEntry.index, inspectorState);
  }

  return Object.freeze({
    render,
    setFilter(value) { setFilter(value); render({ reset: true }); },
    selectFirstIssue() {
      const issue = currentIssues()[0];
      if (issue) selectIssue(issue, { open: true, scroll: true });
    },
    issueCount: () => currentIssues().length,
    issues: currentIssues,
    cleanup() {
      content.querySelectorAll('.popover-controller').forEach((node) => node.popoverCleanup?.());
      inspector.close({ restoreFocus: false });
    },
  });
}
