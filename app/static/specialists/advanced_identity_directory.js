'use strict';

import { textNode } from '/static/pages/more.js';

const UI = globalThis.AlexandriaUI;

export function identityList(payload) {
  const list = document.createElement('div');
  list.className = 'support-list';
  (payload.entries || []).forEach((entry) => {
    const row = document.createElement('div');
    row.className = 'support-list-row';
    const copy = document.createElement('div');
    copy.append(
      textNode('strong', '', entry.display_name || entry.canonical_name),
      textNode(
        'p',
        'support-status-copy',
        `${Number(entry.line_count || 0)} script line${Number(entry.line_count || 0) === 1 ? '' : 's'}`,
      ),
    );
    row.append(copy, UI.status({
      label: entry.resolution_status === 'resolved' ? 'Resolved' : 'Needs review',
      tone: entry.resolution_status === 'resolved' ? 'success' : 'warning',
    }));
    list.append(row);
  });
  return list.childElementCount ? list : UI.emptyState({
    title: payload.available ? 'No identities to review' : 'Identity operations unavailable',
    body: payload.available
      ? 'Approve the Script and Cast before using advanced identity operations.'
      : payload.reason || 'Repair or reapprove the character roster before using guarded identity operations.',
  });
}

export function identityDirectory(payload) {
  const directory = document.createElement('details');
  directory.className = 'full-cast-identity-directory';
  directory.append(
    textNode(
      'summary',
      '',
      `View all Cast identities (${(payload.entries || []).length})`,
    ),
    identityList(payload),
  );
  return directory;
}

export function entryOptions(payload, excludedId = '') {
  return (payload.entries || [])
    .filter((entry) => entry.character_id !== excludedId)
    .map((entry) => ({
      value: entry.character_id,
      label: [
        entry.display_name || entry.canonical_name,
        entry.resolution_status !== 'resolved' ? 'unresolved' : '',
        `${Number(entry.line_count || 0)} lines`,
      ].filter(Boolean).join(' · '),
    }));
}
