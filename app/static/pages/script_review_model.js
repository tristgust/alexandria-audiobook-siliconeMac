'use strict';

function words(value) {
  return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

const LIFECYCLE_ONLY_BLOCKERS = new Set([
  'script_acceptance_stale',
]);

export function isEntryReviewIssue(issue) {
  return !LIFECYCLE_ONLY_BLOCKERS.has(String(issue?.code || '').trim());
}

export function issueType(issue) {
  const value = [issue?.code, issue?.title, issue?.message, issue?.explanation]
    .filter(Boolean).join(' ').toLocaleLowerCase();
  if (/delivery|direction|instruct|prosody|pause/.test(value)) return 'delivery_direction';
  if (/speaker|attribution|dialogue|voice label/.test(value)) return 'uncertain_speaker';
  return 'source_mismatch';
}

export function issuePresentation(type) {
  if (type === 'uncertain_speaker') {
    return {
      label: 'Uncertain speaker', title: 'Speaker attribution is uncertain',
      action: 'Review speaker correction', workflow: 'review',
    };
  }
  if (type === 'delivery_direction') {
    return {
      label: 'Delivery direction', title: 'Delivery direction requires review',
      action: 'Review delivery correction', workflow: 'review',
    };
  }
  return {
    label: 'Source mismatch', title: 'Script text does not match the source',
    action: 'Import corrected Script', workflow: 'import',
  };
}

function issueEntryIndex(issue, entries) {
  const context = issue?.context && typeof issue.context === 'object' ? issue.context : {};
  const candidate = [
    issue?.entry_index,
    context.entry_index,
    Array.isArray(issue?.output_indices) ? issue.output_indices[0] : null,
    Array.isArray(context.output_indices) ? context.output_indices[0] : null,
  ].find((value) => value !== null && value !== undefined && value !== ''
    && Number.isInteger(Number(value)));
  const index = candidate === undefined ? null : Number(candidate);
  return index !== null && index >= 0 && index < entries.length ? index : null;
}

export function normalizeIssues({ lifecycle, auditIssues = [], entries = [] }) {
  const rawIssues = [
    ...(Array.isArray(lifecycle?.blockers)
      ? lifecycle.blockers.filter(isEntryReviewIssue) : []),
    ...(Array.isArray(auditIssues) ? auditIssues : []),
  ];
  const seen = new Set();
  return rawIssues.map((issue, position) => {
    const type = issueType(issue);
    const entryIndex = issueEntryIndex(issue, entries);
    const context = issue?.context && typeof issue.context === 'object' ? issue.context : {};
    const entry = entryIndex === null ? null : entries[entryIndex];
    const sourceText = String(issue?.source_text || context.source_text || 'Source passage unavailable.');
    const outputText = String(issue?.output_text || context.output_text || entry?.text
      || 'No single Script entry is associated with this issue.');
    const seed = [issue?.code, entryIndex, sourceText, outputText].join('|');
    let id = `script-issue-${seed.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '').slice(0, 96) || position}`;
    while (seen.has(id)) id = `${id}-${position}`;
    seen.add(id);
    const presentation = issuePresentation(type);
    return {
      id, type, entryIndex,
      code: String(issue?.code || 'script_review_issue'),
      label: presentation.label,
      title: String(issue?.title || issue?.message || presentation.title),
      explanation: String(issue?.explanation || issue?.message
        || 'Review this issue before approving the Script.'),
      sourceText, outputText, presentation,
      blocking: issue?.blocking !== false && issue?.severity !== 'warning',
      raw: issue,
    };
  });
}

export function issueCounts(issues) {
  return issues.reduce((counts, issue) => {
    counts.all += 1;
    counts[issue.type] += 1;
    return counts;
  }, { all: 0, uncertain_speaker: 0, delivery_direction: 0, source_mismatch: 0 });
}

export function filteredIssues(issues, filter, query, entries) {
  const normalizedQuery = String(query || '').trim().toLocaleLowerCase();
  return issues.filter((issue) => {
    if (filter !== 'all' && issue.type !== filter) return false;
    if (!normalizedQuery) return true;
    const entry = issue.entryIndex === null ? null : entries[issue.entryIndex];
    return [issue.label, issue.title, issue.explanation, issue.sourceText, issue.outputText,
      entry?.speaker, entry?.text, entry?.instruct]
      .some((value) => String(value || '').toLocaleLowerCase().includes(normalizedQuery));
  });
}

export function filteredEntries({ entries, issues, filter, query }) {
  const normalizedQuery = String(query || '').trim().toLocaleLowerCase();
  const issueByEntry = new Map(issues.filter((issue) => issue.entryIndex !== null)
    .map((issue) => [issue.entryIndex, issue]));
  return entries.map((entry, index) => ({ entry, index, issue: issueByEntry.get(index) || null }))
    .filter(({ entry, issue }) => {
      if (filter !== 'all' && issue?.type !== filter) return false;
      if (!normalizedQuery) return true;
      return [entry.speaker, entry.text, entry.instruct, issue?.sourceText]
        .some((value) => String(value || '').toLocaleLowerCase().includes(normalizedQuery));
    });
}

export function approvalState(lifecycle, issues) {
  if (!lifecycle) return { canApprove: false, reason: 'Wait for the current Script review to load.' };
  if (lifecycle.accepted) return { canApprove: false, reason: 'This Script version is already approved.' };
  if (lifecycle.process?.running) return { canApprove: false, reason: 'Wait for Script generation to finish.' };
  if (lifecycle.process?.resumable) return { canApprove: false, reason: 'Resume or discard saved generation first.' };
  if (lifecycle.source_available === false) return { canApprove: false, reason: 'Select a readable source first.' };
  if (!lifecycle.artifact?.script_exists || !lifecycle.artifact?.metadata_exists) {
    return { canApprove: false, reason: 'Generate or import a complete Script first.' };
  }
  const blocking = issues.filter((issue) => issue.blocking);
  if (blocking.length) {
    return {
      canApprove: false,
      reason: `Resolve ${blocking.length} blocking issue${blocking.length === 1 ? '' : 's'} before approval.`,
    };
  }
  const fingerprints = lifecycle.fingerprints || {};
  if (!fingerprints.script || !fingerprints.metadata || !fingerprints.source) {
    return { canApprove: false, reason: 'Current Script and source fingerprints are unavailable.' };
  }
  return { canApprove: true, reason: '' };
}

export function provenanceSummary(lifecycle) {
  const provenance = lifecycle?.provenance || {};
  return {
    method: words(lifecycle?.generation_method || provenance.method || 'Unknown'),
    origin: words(provenance.origin_type || provenance.mode || 'Unknown'),
    status: words(provenance.provenance_status || 'Unknown'),
    version: lifecycle?.accepted_version_id || 'Not approved',
  };
}
