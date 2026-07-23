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
    subtitle: 'Review the narration text and approve this exact version before casting.',
  });
  title.querySelector('h1').dataset.pageHeading = '';
  owner.append(title);
  return owner;
}

export function scriptStageStates(flow) {
  const stages = flow?.stage_map || {};
  return Object.fromEntries(['script', 'cast', 'produce', 'export'].map((name) => [
    name, stages[name]?.state || (name === 'script' ? 'current' : 'future'),
  ]));
}

export function scriptEntryRow(entry, index, select) {
  const row = document.createElement('li');
  row.className = 'script-entry';
  row.tabIndex = 0;
  row.dataset.entryIndex = String(index);
  row.append(
    text('strong', 'script-entry__speaker', entry.speaker || 'NARRATOR'),
    text('p', 'script-entry__text', entry.text || ''),
  );
  if (entry.instruct) row.append(text('p', 'script-entry__direction', entry.instruct));
  const activate = () => select(entry, index, row);
  row.addEventListener('click', activate);
  row.addEventListener('keydown', (event) => {
    if (!['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    activate();
  });
  return row;
}

export function scriptEntryContext({ entry, index, total, blocker }) {
  const detail = document.createElement('div');
  detail.className = 'script-entry-detail';
  detail.append(
    text('div', 'metadata', blocker ? 'Selected issue' : 'Selected entry'),
    text('h3', 'entity-title', entry.speaker || 'NARRATOR'),
    text('div', 'metadata', `Entry ${index + 1} of ${total.toLocaleString()}`),
  );
  if (blocker) {
    detail.append(UI.status({
      tone: 'warning',
      label: blocker.title || blocker.message || 'Review issue',
      domain: 'script-review',
      value: blocker.code || 'review_issue',
    }));
  }
  detail.append(UI.flatSection({
    title: 'Script', body: entry.text || 'No Script text is available for this entry.',
  }));
  if (entry.instruct) {
    detail.append(UI.flatSection({
      title: 'Delivery direction',
      content: text('p', 'script-entry-detail__direction', entry.instruct),
    }));
  }
  detail.append(UI.flatSection({
    title: 'Review context',
    body: blocker?.explanation || blocker?.message
      || 'No blocking review issue is attached to this entry.',
  }));
  return detail;
}

export function renderScriptSourceContext({ root, flow, lifecycle, entries, projectTitle }) {
  const source = flow?.source || {};
  const blockers = (lifecycle?.blockers || []).filter((item) => item.blocking !== false);
  const identity = document.createElement('div');
  identity.className = 'script-source-context__identity';
  identity.append(
    text('div', 'utility-heading', 'Source'),
    text('h2', 'entity-title', source.title || source.filename || projectTitle || 'Current source'),
    text('p', 'metadata', [
      source.type ? `${String(source.type).toUpperCase()} source` : 'Source selected',
      source.source_language ? `Language: ${source.source_language}` : null,
      source.filename || null,
    ].filter(Boolean).join(' · ')),
  );
  const review = document.createElement('div');
  review.className = 'script-source-context__review';
  review.append(
    text('div', 'utility-heading', 'Current review'),
    text('strong', '', `${entries.length.toLocaleString()} Script entries`),
    text('span', 'metadata', blockers.length
      ? `${blockers.length} blocking issue${blockers.length === 1 ? '' : 's'} remain`
      : 'Ready for final review and approval'),
  );
  root.replaceChildren(identity, review);
}

export function renderScriptReviewStatus(root, lifecycle) {
  const blockers = (lifecycle?.blockers || []).filter((item) => item.blocking !== false);
  root.replaceChildren();
  if (blockers.length) {
    const list = document.createElement('ul');
    list.className = 'blocker-list';
    blockers.forEach((item) => list.append(text('li', '', item.title || item.message || item.code)));
    const notice = UI.notice({
      tone: 'warning',
      title: 'Review required',
      body: `${blockers.length} blocking issue${blockers.length === 1 ? '' : 's'} must be resolved before approval.`,
      blocking: true,
    });
    notice.append(list);
    root.append(notice);
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
