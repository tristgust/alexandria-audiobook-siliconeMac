'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const REPORT_PREFIX = 'PHASE18C_UI_REPORT=';

function requireCheck(checks, name, condition, details = {}) {
  checks[name] = { ok: Boolean(condition), ...details };
  if (!condition) {
    throw new Error(`Phase 18C UI check failed: ${name}: ${JSON.stringify(details)}`);
  }
}

function scanToMatching(source, openIndex, openChar, closeChar) {
  let depth = 0;
  let quote = null;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;

  for (let index = openIndex; index < source.length; index += 1) {
    const char = source[index];
    const next = source[index + 1];
    if (lineComment) {
      if (char === '\n') lineComment = false;
      continue;
    }
    if (blockComment) {
      if (char === '*' && next === '/') {
        blockComment = false;
        index += 1;
      }
      continue;
    }
    if (quote) {
      if (escaped) escaped = false;
      else if (char === '\\') escaped = true;
      else if (char === quote) quote = null;
      continue;
    }
    if (char === '/' && next === '/') {
      lineComment = true;
      index += 1;
      continue;
    }
    if (char === '/' && next === '*') {
      blockComment = true;
      index += 1;
      continue;
    }
    if (char === '\'' || char === '"' || char === '`') {
      quote = char;
      continue;
    }
    if (char === openChar) depth += 1;
    if (char === closeChar) {
      depth -= 1;
      if (depth === 0) return index;
    }
  }
  throw new Error(`Unbalanced ${openChar}${closeChar}`);
}

function extractFunction(source, name) {
  const pattern = new RegExp(`(?:async\\s+)?function\\s+${name}\\s*\\(`);
  const match = pattern.exec(source);
  if (!match) throw new Error(`Function not found: ${name}`);
  const brace = source.indexOf('{', match.index);
  const end = scanToMatching(source, brace, '{', '}');
  return source.slice(match.index, end + 1);
}

class MockElement {
  constructor(id) {
    this.id = id;
    this.style = { display: '' };
    this.className = '';
    this.disabled = false;
    this.checked = false;
    this.value = '';
    this.dataset = {};
    this.listeners = {};
    this._innerHTML = '';
    this._textContent = '';
  }
  set innerHTML(value) { this._innerHTML = String(value ?? ''); }
  get innerHTML() { return this._innerHTML; }
  set textContent(value) { this._textContent = String(value ?? ''); }
  get textContent() { return this._textContent; }
  addEventListener(type, handler) { this.listeners[type] = handler; }
}

function createEnvironment() {
  const elements = new Map();
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, new MockElement(id));
    return elements.get(id);
  };
  const intervalHandlers = new Map();
  let nextInterval = 1;
  const apiGets = [];
  const apiPosts = [];
  const postCalls = [];
  const toastCalls = [];

  const sandbox = {
    console,
    document: {
      getElementById: getElement,
    },
    window: {
      prompt: () => '',
    },
    API: {
      get: async (url) => {
        if (!apiGets.length) throw new Error(`Unexpected GET ${url}`);
        const item = apiGets.shift();
        if (item instanceof Error) throw item;
        return item;
      },
      post: async (url, payload) => {
        postCalls.push({ url, payload });
        if (!apiPosts.length) throw new Error(`Unexpected POST ${url}`);
        const item = apiPosts.shift();
        if (item instanceof Error) throw item;
        return item;
      },
    },
    showToast: (message, type) => toastCalls.push({ message, type }),
    showConfirm: async () => true,
    setInterval: (handler, delay) => {
      const id = nextInterval;
      nextInterval += 1;
      intervalHandlers.set(id, { handler, delay });
      return id;
    },
    clearInterval: (id) => intervalHandlers.delete(id),
    Object,
    Array,
    Number,
    String,
    Boolean,
    Math,
    JSON,
    Error,
    Promise,
  };
  sandbox.globalThis = sandbox;
  return {
    sandbox,
    elements,
    getElement,
    intervalHandlers,
    apiGets,
    apiPosts,
    postCalls,
    toastCalls,
  };
}

function rosterFixture({ unresolved = false } = {}) {
  const first = {
    id: 'character_11111111111111111111',
    canonical_name: 'THE DOCTOR',
    display_name: '<img src=x onerror=alert(1)>',
    entity_kind: 'character',
    speaking_status: 'speaker',
    titles: ['Doctor'],
    aliases: ['DOCTOR'],
    nicknames: [],
    pronouns: [],
    species: [],
    relationships: [],
    first_evidence_location: 'characters 1-11',
    additional_evidence_locations: [],
    confidence: 0.9,
    resolution_status: unresolved ? 'unresolved' : 'duplicate_candidate',
    possible_duplicate_ids: ['character_22222222222222222222'],
    mistaken_merge_risk: true,
    unresolved_questions: unresolved ? ['Which incarnation?'] : [],
    evidence: [{
      source_quote: '<script>alert(1)</script>',
      source_location: 'characters 1-26',
      category: 'name',
      confidence: 1,
      basis: 'explicit',
    }],
    voice_clues: [],
    sample_lines: [],
  };
  const second = {
    ...first,
    id: 'character_22222222222222222222',
    canonical_name: 'DOCTOR SEN',
    display_name: 'Doctor Sen',
    aliases: [],
    possible_duplicate_ids: ['character_11111111111111111111'],
    evidence: [{
      source_quote: 'Doctor Sen',
      source_location: 'characters 40-50',
      category: 'name',
      confidence: 1,
      basis: 'explicit',
    }],
  };
  return {
    schema_version: 1,
    status: 'draft',
    source: { basename: 'book.txt' },
    entries: [first, second],
    unresolved: unresolved ? [{ entry_id: first.id }] : [],
    duplicate_candidates: [{
      entry_ids: [first.id, second.id],
      reason: 'Potential overlap',
      confidence: 0.5,
      evidence: [...first.evidence, ...second.evidence],
    }],
    excluded_entities: [],
    warnings: [],
    review_history: [],
    draft_fingerprint: 'draft-fingerprint',
  };
}

function statusFixture(active, options = {}) {
  const counts = {
    entries: 2,
    resolved: active === 'approved' ? 2 : 0,
    unresolved: options.unresolved ? 1 : 0,
    unnamed: 0,
    duplicate_candidates: active === 'draft' ? 1 : 0,
    excluded: 0,
    speakers: 2,
    named_non_speakers: 0,
  };
  return {
    source: {
      available: true,
      basename: 'book.txt',
      fingerprint: 'source-fingerprint',
      character_count: 100,
      error: null,
    },
    active,
    draft: active === 'draft'
      ? { status: 'draft', fingerprint: 'draft-fingerprint', counts }
      : { status: 'missing', counts: null },
    approved: active === 'approved'
      ? { status: 'approved', fingerprint: 'roster-fingerprint', counts }
      : { status: 'missing', counts: null },
    process: {
      running: Boolean(options.running),
      logs: options.running ? ['Passage 2/4'] : [],
    },
    progress: options.progress || {
      exists: false,
      status: 'missing',
      completed_passages: 0,
      total_passages: 0,
      next_passage: null,
    },
  };
}

async function main() {
  const repoRoot = path.resolve(process.argv[2] || '.');
  const html = fs.readFileSync(
    path.join(repoRoot, 'app', 'static', 'index.html'),
    'utf8'
  );
  const names = [
    'characterRosterEvidenceHtml',
    'characterRosterEntryHtml',
    'characterRosterDuplicateHtml',
    'renderCharacterRosterContent',
    'renderCharacterRosterStatus',
    'refreshCharacterRosterStatus',
    'performCharacterRosterAction',
  ];
  const source = [
    `function escapeHtml(str) {
      if (str == null) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }`,
    'let characterRosterStatusTimer = null;',
    'let characterRosterDraft = null;',
    'let characterRosterApproved = null;',
    'let characterRosterLastStatus = null;',
    ...names.map((name) => {
      try {
        return extractFunction(html, name);
      } catch (error) {
        throw new Error(`Failed to extract ${name}: ${error.message}`);
      }
    }),
    `globalThis.__roster = {
      ${names.join(',')},
      setDraft: (value) => { characterRosterDraft = value; },
      getDraft: () => characterRosterDraft,
      getTimer: () => characterRosterStatusTimer,
    };`,
  ].join('\n\n');

  const env = createEnvironment();
  vm.createContext(env.sandbox);
  vm.runInContext(source, env.sandbox, { filename: 'phase18c-production.js' });
  const ui = env.sandbox.__roster;
  const checks = {};

  const unsafe = ui.characterRosterEvidenceHtml([{
    source_quote: '<img src=x onerror=alert(1)>',
    source_location: '<script>x</script>',
    category: 'name',
    confidence: 1,
    basis: 'explicit',
  }]);
  requireCheck(
    checks,
    'evidence_is_html_escaped',
    unsafe.includes('&lt;img')
      && unsafe.includes('&lt;script&gt;')
      && !unsafe.includes('<img src=x'),
    { unsafe }
  );

  const draft = rosterFixture({ unresolved: true });
  const draftStatus = statusFixture('draft', { unresolved: true });
  ui.renderCharacterRosterStatus(draftStatus, draft);
  requireCheck(
    checks,
    'draft_renders_actions_duplicates_and_acknowledgment',
    env.getElement('character-roster-status-badge').textContent === 'Draft'
      && env.getElement('character-roster-content').innerHTML.includes('data-roster-action="rename"')
      && env.getElement('character-roster-content').innerHTML.includes('Possible duplicate identities')
      && env.getElement('character-roster-content').innerHTML.includes('&lt;img')
      && env.getElement('character-roster-unresolved-ack-wrap').style.display === '',
    {
      badge: env.getElement('character-roster-status-badge').textContent,
      content: env.getElement('character-roster-content').innerHTML.slice(0, 300),
    }
  );

  const approvedRoster = { ...draft, status: 'approved', duplicate_candidates: [] };
  ui.renderCharacterRosterStatus(statusFixture('approved'), approvedRoster);
  requireCheck(
    checks,
    'approved_roster_is_read_only',
    env.getElement('character-roster-status-badge').textContent === 'Approved'
      && !env.getElement('character-roster-content').innerHTML.includes('data-roster-action=')
      && env.getElement('character-roster-approval').style.display === 'none',
    {
      content: env.getElement('character-roster-content').innerHTML.slice(0, 300),
    }
  );

  ui.renderCharacterRosterStatus(
    statusFixture('none', {
      running: true,
      progress: {
        exists: true,
        status: 'resumable',
        completed_passages: 2,
        total_passages: 4,
        next_passage: 3,
      },
    }),
    null
  );
  requireCheck(
    checks,
    'running_state_guards_controls',
    env.getElement('btn-cancel-character-roster').style.display === ''
      && env.getElement('btn-discover-character-roster').disabled === true
      && env.getElement('btn-discard-character-roster-progress').style.display === 'none'
      && env.getElement('character-roster-progress').textContent.includes('2 of 4'),
    {
      progress: env.getElement('character-roster-progress').textContent,
    }
  );

  env.apiGets.push(statusFixture('draft'), draft);
  await ui.refreshCharacterRosterStatus();
  requireCheck(
    checks,
    'refresh_fetches_status_and_draft',
    ui.getDraft().draft_fingerprint === 'draft-fingerprint'
      && env.getElement('character-roster-status-badge').textContent === 'Draft',
    { draft: ui.getDraft() && ui.getDraft().draft_fingerprint }
  );

  const runningStatus = statusFixture('none', {
    running: true,
    progress: {
      exists: true,
      status: 'resumable',
      completed_passages: 1,
      total_passages: 3,
      next_passage: 2,
    },
  });
  env.apiGets.push(runningStatus);
  await ui.refreshCharacterRosterStatus();
  const timerStarted = ui.getTimer() !== null && env.intervalHandlers.size === 1;
  env.apiGets.push(statusFixture('none'));
  await ui.refreshCharacterRosterStatus();
  requireCheck(
    checks,
    'polling_starts_and_stops',
    timerStarted && ui.getTimer() === null && env.intervalHandlers.size === 0,
    { timerStarted, timers: env.intervalHandlers.size }
  );

  ui.setDraft(draft);
  env.getElement(`roster-rename-${draft.entries[0].id}`).value = 'THE SEVENTH DOCTOR';
  env.getElement(`roster-display-${draft.entries[0].id}`).value = 'The Seventh Doctor';
  const button = new MockElement('rename-button');
  button.dataset = {
    rosterAction: 'rename',
    entryId: draft.entries[0].id,
  };
  const updatedDraft = {
    ...draft,
    entries: [{ ...draft.entries[0], canonical_name: 'THE SEVENTH DOCTOR' }, draft.entries[1]],
    draft_fingerprint: 'updated-fingerprint',
  };
  env.apiPosts.push({ status: 'updated', draft: updatedDraft });
  env.apiGets.push(statusFixture('draft'), updatedDraft);
  await ui.performCharacterRosterAction(button);
  requireCheck(
    checks,
    'action_uses_current_fingerprint_and_refreshes',
    env.postCalls[0].url === '/api/character_roster/draft/action'
      && env.postCalls[0].payload.draft_fingerprint === 'draft-fingerprint'
      && env.postCalls[0].payload.value === 'THE SEVENTH DOCTOR'
      && ui.getDraft().draft_fingerprint === 'updated-fingerprint',
    { post: env.postCalls[0], draft: ui.getDraft().draft_fingerprint }
  );

  const stale = new Error('Draft changed');
  stale.code = 'stale_draft';
  ui.setDraft(updatedDraft);
  env.apiPosts.push(stale);
  env.apiGets.push(statusFixture('draft'), updatedDraft);
  const confirmButton = new MockElement('confirm-button');
  confirmButton.dataset = {
    rosterAction: 'confirm',
    entryId: draft.entries[0].id,
  };
  await ui.performCharacterRosterAction(confirmButton);
  requireCheck(
    checks,
    'stale_action_refreshes_instead_of_overwriting',
    env.toastCalls.some((item) => item.type === 'warning')
      && ui.getDraft().draft_fingerprint === 'updated-fingerprint',
    { toasts: env.toastCalls }
  );

  console.log(REPORT_PREFIX + JSON.stringify({ checks }));
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
