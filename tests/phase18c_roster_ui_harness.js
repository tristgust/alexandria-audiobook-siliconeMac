'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const REPORT_PREFIX = 'PHASE18C_UI_REPORT=';

function requireCheck(checks, name, condition, details = {}) {
  checks[name] = { ok: Boolean(condition), ...details };
  if (!condition) {
    throw new Error(
      `Phase 18C UI check failed: ${name}: ${JSON.stringify(details)}`
    );
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
      if (escaped) {
        escaped = false;
      } else if (char === '\\') {
        escaped = true;
      } else if (char === quote) {
        quote = null;
      }
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

  throw new Error(`Unbalanced ${openChar}${closeChar} at ${openIndex}`);
}

function extractFunction(source, name) {
  const pattern = new RegExp(`(?:async\\s+)?function\\s+${name}\\s*\\(`);
  const match = pattern.exec(source);
  if (!match) throw new Error(`Function not found: ${name}`);
  const brace = source.indexOf('{', match.index);
  const end = scanToMatching(source, brace, '{', '}');
  return source.slice(match.index, end + 1);
}

function extractStatement(source, pattern) {
  const match = pattern.exec(source);
  if (!match) throw new Error(`Statement not found: ${pattern}`);
  const start = match.index;
  let parens = 0;
  let braces = 0;
  let brackets = 0;
  let quote = null;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;

  for (let index = start; index < source.length; index += 1) {
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
      if (escaped) {
        escaped = false;
      } else if (char === '\\') {
        escaped = true;
      } else if (char === quote) {
        quote = null;
      }
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

    if (char === '(') parens += 1;
    if (char === ')') parens -= 1;
    if (char === '{') braces += 1;
    if (char === '}') braces -= 1;
    if (char === '[') brackets += 1;
    if (char === ']') brackets -= 1;

    if (
      char === ';'
      && parens === 0
      && braces === 0
      && brackets === 0
    ) {
      return source.slice(start, index + 1);
    }
  }

  throw new Error(`Unterminated statement: ${pattern}`);
}

class MockElement {
  constructor(id) {
    this.id = id;
    this.style = { display: '' };
    this.className = '';
    this.textContent = '';
    this.innerHTML = '';
    this.disabled = false;
    this.checked = false;
    this.value = '';
    this.dataset = {};
    this.listeners = {};
    this.scrollTop = 0;
    this.scrollHeight = 120;
    this.clientHeight = 60;
  }

  addEventListener(type, handler) {
    this.listeners[type] = handler;
  }

  reset() {
    this.style.display = '';
    this.className = '';
    this.textContent = '';
    this.innerHTML = '';
    this.disabled = false;
    this.checked = false;
    this.value = '';
    this.dataset = {};
    this.scrollTop = 0;
    this.scrollHeight = 120;
    this.clientHeight = 60;
  }
}

function sampleEvidence(quote = 'The Doctor') {
  return {
    source_quote: quote,
    source_location: 'characters 0-10',
    start_char: 0,
    end_char: quote.length,
    passage_index: 1,
    entry_index: null,
    batch_index: 1,
    category: 'name',
    confidence: 1,
    basis: 'explicit',
  };
}

function sampleEntry(overrides = {}) {
  return {
    id: 'character_0123456789abcdef01234567',
    canonical_name: 'THE DOCTOR',
    display_name: 'The Doctor',
    entity_kind: 'character',
    speaking_status: 'speaker',
    titles: ['Doctor'],
    aliases: ['DOCTOR'],
    nicknames: [],
    pronouns: [],
    species: [],
    relationships: [],
    first_evidence_location: 'characters 0-10',
    additional_evidence_locations: [],
    confidence: 0.95,
    resolution_status: 'resolved',
    possible_duplicate_ids: [],
    mistaken_merge_risk: false,
    unresolved_questions: [],
    evidence: [sampleEvidence()],
    voice_clues: [],
    sample_lines: ['"No."'],
    ...overrides,
  };
}

function sampleDraft(overrides = {}) {
  return {
    schema_version: 1,
    status: 'draft',
    source: {
      path: '/tmp/book.txt',
      basename: 'book.txt',
      fingerprint: 'source-fingerprint',
      character_count: 100,
    },
    discovery: {
      created_at_utc: '2026-07-16T20:00:00Z',
      model_name: 'qwen3.5:35b-mlx',
      backend: 'ollama-native',
      generation_fingerprint: 'generation-fingerprint',
      batch_count: 1,
      completed_batches: 1,
    },
    entries: [sampleEntry()],
    unresolved: [],
    duplicate_candidates: [],
    excluded_entities: [],
    warnings: [],
    review_history: [],
    draft_fingerprint: 'draft-fingerprint',
    ...overrides,
  };
}

function countsFor(roster) {
  const entries = roster.entries || [];
  return {
    entries: entries.length,
    resolved: entries.filter((entry) => entry.resolution_status === 'resolved').length,
    unresolved: entries.filter((entry) => entry.resolution_status === 'unresolved').length,
    unnamed: entries.filter((entry) => entry.resolution_status === 'unnamed').length,
    duplicate_candidates: (roster.duplicate_candidates || []).length,
    excluded: (roster.excluded_entities || []).length,
    speakers: entries.filter((entry) => ['speaker', 'narrator'].includes(entry.speaking_status)).length,
    named_non_speakers: entries.filter((entry) => entry.entity_kind === 'named_non_speaker').length,
  };
}

function sampleStatus({
  active = 'draft',
  roster = sampleDraft(),
  running = false,
  progress = null,
  sourceAvailable = true,
} = {}) {
  const missing = {
    exists: false,
    status: 'missing',
    compatible_source: null,
    counts: null,
    fingerprint: null,
    error: null,
  };
  const artifact = roster
    ? {
        exists: true,
        status: active,
        compatible_source: true,
        counts: countsFor(roster),
        fingerprint: active === 'draft'
          ? roster.draft_fingerprint
          : roster.roster_fingerprint,
        error: null,
      }
    : missing;
  return {
    source: {
      available: sourceAvailable,
      path: sourceAvailable ? '/tmp/book.txt' : null,
      basename: sourceAvailable ? 'book.txt' : null,
      fingerprint: sourceAvailable ? 'source-fingerprint' : null,
      character_count: sourceAvailable ? 100 : null,
      error: sourceAvailable ? null : 'No source selected.',
    },
    active,
    working_draft: active === 'draft',
    draft: active === 'draft' ? artifact : missing,
    approved: active === 'approved' ? artifact : missing,
    process: { running, logs: running ? ['running passage'] : [], cancel: false },
    progress: progress || {
      exists: false,
      status: 'missing',
      completed_passages: 0,
      total_passages: 0,
      next_passage: null,
      reconciliation_complete: false,
      compatible_source: null,
      error: null,
    },
  };
}

function createHarness(repoRoot) {
  const source = fs.readFileSync(
    path.join(repoRoot, 'app', 'static', 'index.html'),
    'utf8'
  );
  const functionNames = [
    'setPlainStatus',
    'humanizeVisualCategory',
    'characterRosterEvidenceHtml',
    'characterRosterVoiceProfileHtml',
    'characterRosterEntryHtml',
    'characterRosterDuplicateHtml',
    'renderCharacterRosterContent',
    'renderCharacterRosterLog',
    'renderCharacterRosterStatus',
    'humanizeVoiceTrainingValue',
    'activeCharacterRoster',
    'characterWorkspaceEntries',
    'characterRosterEntryForStatus',
    'characterIdentitySectionHtml',
    'characterDraftDuplicateCandidates',
    'characterDraftDetailHtml',
    'refreshCharacterRosterStatus',
    'performCharacterRosterAction',
  ];
  const functions = functionNames
    .map((name) => extractFunction(source, name))
    .join('\n\n');
  const statements = [
    /document\.getElementById\('character-roster-content'\)\.addEventListener/,
    /document\.getElementById\('btn-refresh-character-roster'\)\.addEventListener/,
    /document\.getElementById\('btn-discover-character-roster'\)\.addEventListener/,
    /document\.getElementById\('btn-cancel-character-roster'\)\.addEventListener/,
    /document\.getElementById\('btn-discard-character-roster-progress'\)\.addEventListener/,
    /document\.getElementById\('btn-rollback-character-roster'\)\.addEventListener/,
    /document\.getElementById\('btn-approve-character-roster'\)\.addEventListener/,
  ].map((pattern) => extractStatement(source, pattern));

  const ids = [
    'character-roster-status-badge',
    'character-roster-summary',
    'character-roster-progress',
    'character-roster-source',
    'character-roster-counts',
    'character-roster-error',
    'btn-discover-character-roster',
    'btn-cancel-character-roster',
    'btn-discard-character-roster-progress',
    'btn-rollback-character-roster',
    'btn-refresh-character-roster',
    'character-roster-approval',
    'character-roster-voice-profiles',
    'character-roster-voice-profile-status',
    'btn-export-roster-voice-profiles',
    'character-roster-unresolved-ack-wrap',
    'character-roster-unresolved-ack',
    'btn-approve-character-roster',
    'character-roster-content',
    'character-roster-logs',
    'character-roster-log-state',
    'voice-projects-list',
    'voice-projects-detail',
    'voice-projects-summary',
    'voice-projects-status',
    'voice-projects-counts',
    'voice-projects-error',
    'voice-save-status',
    'btn-gen-personas',
  ];
  const elements = new Map(ids.map((id) => [id, new MockElement(id)]));
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new MockElement(id));
      return elements.get(id);
    },
  };

  const queues = { get: [], post: [] };
  const calls = [];
  const confirmations = [];
  const prompts = [];
  const toasts = [];
  const timers = new Map();
  let nextTimer = 1;

  async function consume(queue, kind) {
    if (!queue.length) throw new Error(`No queued ${kind} response`);
    const item = queue.shift();
    if (item && item.__error) {
      const error = new Error(item.__error.message);
      error.code = item.__error.code;
      throw error;
    }
    if (typeof item === 'function') return item();
    return item;
  }

  const API = {
    async get(url) {
      calls.push({ method: 'GET', url });
      return consume(queues.get, 'GET');
    },
    async post(url, body) {
      calls.push({ method: 'POST', url, body });
      return consume(queues.post, 'POST');
    },
  };

  function setIntervalMock(callback, milliseconds) {
    const id = nextTimer++;
    timers.set(id, { callback, milliseconds });
    return id;
  }

  function clearIntervalMock(id) {
    timers.delete(id);
  }

  const context = {
    API,
    document,
    console,
    Date,
    Number,
    String,
    Boolean,
    Array,
    Object,
    JSON,
    Math,
    Promise,
    requestAnimationFrame(callback) {
      callback();
      return 1;
    },
    setInterval: setIntervalMock,
    clearInterval: clearIntervalMock,
    async showConfirm(message) {
      calls.push({ method: 'CONFIRM', message });
      return confirmations.length ? confirmations.shift() : false;
    },
    async showTextPrompt(options = {}) {
      calls.push({ method: 'PROMPT', options });
      return prompts.length ? prompts.shift() : '';
    },
    showToast(message, type) {
      toasts.push({ message, type });
    },
    escapeHtml(value) {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
    },
  };
  context.window = context;
  context.window.prompt = (message, fallback) => {
    calls.push({ method: 'PROMPT', message, fallback });
    return prompts.length ? prompts.shift() : '';
  };
  context.globalThis = context;
  vm.createContext(context);

  const program = `
let characterRosterStatusTimer = null;
let characterRosterDraft = null;
let characterRosterApproved = null;
let characterRosterLastStatus = null;
let characterRosterVoiceProfileStatus = null;
let characterRosterVoiceProfileById = {};
let characterRosterLogFollowTail = true;
let characterRosterImportReconciliation = null;
let voiceTrainingStatus = null;
let voiceTrainingSelectedId = null;
function releaseCharacterVoiceCard() {}
function renderVoiceTrainingList() {
  const entries = characterWorkspaceEntries();
  document.getElementById('voice-projects-list').innerHTML = entries
    .map(entry => '<button data-voice-training-character="' + escapeHtml(entry.character_id) + '">' + escapeHtml(entry.display_name) + '</button>')
    .join('');
}
async function selectVoiceTrainingCharacter(characterId) {
  voiceTrainingSelectedId = characterId;
  const entry = characterWorkspaceEntries().find(item => item.character_id === characterId);
  if (entry?.roster_state === 'draft') {
    document.getElementById('voice-projects-detail').innerHTML = characterDraftDetailHtml(entry);
  }
}
async function refreshCharactersWorkspace() {
  return refreshCharacterRosterStatus();
}
async function refreshCharacterRosterImportReconciliation() {
  return characterRosterImportReconciliation;
}
${functions}
${statements.join('\n')}
globalThis.__phase18c = {
  characterRosterEvidenceHtml,
  characterRosterVoiceProfileHtml,
  characterRosterEntryHtml,
  characterRosterDuplicateHtml,
  renderCharacterRosterContent,
  renderCharacterRosterLog,
  renderCharacterRosterStatus,
  characterIdentitySectionHtml,
  characterDraftDetailHtml,
  refreshCharacterRosterStatus,
  performCharacterRosterAction,
  getDraft: () => characterRosterDraft,
  setDraft: (value) => { characterRosterDraft = value; },
  getApproved: () => characterRosterApproved,
  setApproved: (value) => { characterRosterApproved = value; },
  getLastStatus: () => characterRosterLastStatus,
  setLastStatus: (value) => { characterRosterLastStatus = value; },
  setVoiceProfiles: (status) => {
    characterRosterVoiceProfileStatus = status;
    characterRosterVoiceProfileById = Object.fromEntries(
      (status?.entries || []).map(item => [item.character_id, item])
    );
  },
  getTimer: () => characterRosterStatusTimer,
  setTimer: (value) => { characterRosterStatusTimer = value; }
};
`;
  vm.runInContext(program, context, {
    filename: 'phase18c-extracted-index.js',
  });

  function reset() {
    elements.forEach((element) => element.reset());
    queues.get.length = 0;
    queues.post.length = 0;
    calls.length = 0;
    confirmations.length = 0;
    prompts.length = 0;
    toasts.length = 0;
    timers.clear();
    context.__phase18c.setTimer(null);
    context.__phase18c.setDraft(null);
    context.__phase18c.setApproved(null);
    context.__phase18c.setLastStatus(null);
  }

  async function settle() {
    await Promise.resolve();
    await new Promise((resolve) => setImmediate(resolve));
  }

  function queueDraftRefresh(draft) {
    queues.get.push(sampleStatus({ active: 'draft', roster: draft }));
    queues.get.push(draft);
  }

  return {
    source,
    context,
    elements,
    queues,
    calls,
    confirmations,
    prompts,
    toasts,
    timers,
    reset,
    settle,
    queueDraftRefresh,
  };
}

async function run(repoRoot) {
  const checks = {};
  const harness = createHarness(repoRoot);
  const ui = harness.context.__phase18c;
  const el = (id) => harness.context.document.getElementById(id);

  requireCheck(
    checks,
    'actual_source_extraction',
    typeof ui.renderCharacterRosterStatus === 'function'
      && typeof ui.performCharacterRosterAction === 'function'
      && typeof el('btn-discover-character-roster').listeners.click === 'function'
      && typeof el('btn-approve-character-roster').listeners.click === 'function'
      && typeof el('character-roster-content').listeners.click === 'function',
    { sourceLength: harness.source.length }
  );
  requireCheck(
    checks,
    'initial_refresh_present',
    harness.source.includes('refreshCharacterRosterStatus();')
  );

  harness.reset();
  ui.renderCharacterRosterStatus(
    sampleStatus({active: 'none', roster: null}),
    null
  );
  requireCheck(
    checks,
    'empty_source_ready_state',
    el('character-roster-status-badge').textContent === 'Not started'
      && el('character-roster-summary').textContent === 'No character roster exists for this source'
      && el('btn-discover-character-roster').style.display === ''
      && el('btn-discover-character-roster').disabled === false
      && el('character-roster-approval').style.display === 'none'
      && el('character-roster-content').innerHTML === '',
    {
      badge: el('character-roster-status-badge').textContent,
      summary: el('character-roster-summary').textContent,
    }
  );

  harness.reset();
  const malicious = sampleDraft({
    entries: [sampleEntry({
      display_name: '<img src=x onerror=alert(1)>',
      aliases: ['"><script>alert(1)</script>'],
      evidence: [sampleEvidence('<b>unsafe</b>')],
      unresolved_questions: ['<svg onload=alert(1)>'],
      resolution_status: 'unresolved',
    })],
  });
  ui.setDraft(malicious);
  const rendered = ui.characterDraftDetailHtml({
    character_id: malicious.entries[0].id,
    ...malicious.entries[0],
    roster_state: 'draft',
  });
  requireCheck(
    checks,
    'source_content_is_escaped',
    rendered.includes('&lt;img src=x onerror=alert(1)&gt;')
      && rendered.includes('&lt;script&gt;alert(1)&lt;/script&gt;')
      && rendered.includes('&lt;b&gt;unsafe&lt;/b&gt;')
      && rendered.includes('&lt;svg onload=alert(1)&gt;')
      && !rendered.includes('<script>')
      && !rendered.includes('<img src=x'),
    { rendered }
  );

  harness.reset();
  const draft = sampleDraft();
  ui.renderCharacterRosterStatus(
    sampleStatus({ active: 'draft', roster: draft }),
    draft
  );
  ui.setDraft(draft);
  const draftDetail = ui.characterDraftDetailHtml({
    character_id: draft.entries[0].id,
    ...draft.entries[0],
    roster_state: 'draft',
  });
  requireCheck(
    checks,
    'draft_status_and_actions',
    el('character-roster-status-badge').textContent === 'Ready to approve'
      && el('character-roster-summary').textContent === '1 character is ready for bulk approval'
      && el('character-roster-approval').style.display === ''
      && draftDetail.includes('data-roster-action="rename"')
      && draftDetail.includes('No individual approval needed')
      && !draftDetail.includes('data-roster-action="confirm"'),
    {
      badge: el('character-roster-status-badge').textContent,
      summary: el('character-roster-summary').textContent,
      detail: draftDetail,
    }
  );

  harness.reset();
  const approved = {
    ...draft,
    status: 'approved',
    approved_at_utc: '2026-07-16T22:00:00Z',
    approved_draft_fingerprint: draft.draft_fingerprint,
    roster_fingerprint: 'roster-fingerprint',
    approval_summary: {
      resolved_count: 1,
      unresolved_count: 0,
      merged_count: 0,
      excluded_count: 0,
      acknowledged_unresolved: false,
    },
  };
  delete approved.draft_fingerprint;
  ui.renderCharacterRosterStatus(
    sampleStatus({ active: 'approved', roster: approved }),
    approved
  );
  requireCheck(
    checks,
    'approved_is_read_only',
    el('character-roster-status-badge').textContent === 'Approved'
      && el('btn-discover-character-roster').style.display === 'none'
      && el('character-roster-approval').style.display === 'none'
      && el('character-roster-content').innerHTML === '',
    { html: el('character-roster-content').innerHTML }
  );

  harness.reset();
  const duplicateSecond = sampleEntry({
    id: 'character_abcdefabcdefabcdefabcdef',
    canonical_name: 'THE SHORT MAN',
    display_name: 'The short man',
    resolution_status: 'duplicate_candidate',
  });
  const duplicateFirst = sampleEntry({
    resolution_status: 'duplicate_candidate',
    possible_duplicate_ids: [duplicateSecond.id],
    mistaken_merge_risk: true,
  });
  const duplicateDraft = sampleDraft({
    entries: [duplicateFirst, duplicateSecond],
    duplicate_candidates: [{
      entry_ids: [duplicateFirst.id, duplicateSecond.id],
      reason: 'Possible identity overlap.',
      confidence: 0.7,
      evidence: [sampleEvidence()],
    }],
  });
  ui.setDraft(duplicateDraft);
  const duplicateDetail = ui.characterDraftDetailHtml({
    character_id: duplicateFirst.id,
    ...duplicateFirst,
    roster_state: 'draft',
  });
  requireCheck(
    checks,
    'duplicate_comparison_actions',
    duplicateDetail.includes('Keep separate')
      && duplicateDetail.includes(`Merge as ${duplicateFirst.display_name}`)
      && duplicateDetail.includes(`Merge as ${duplicateSecond.display_name}`),
    { html: duplicateDetail }
  );

  harness.reset();
  ui.renderCharacterRosterStatus(
    sampleStatus({
      active: 'none',
      roster: null,
      running: true,
      progress: {
        exists: true,
        status: 'resumable',
        completed_passages: 2,
        total_passages: 5,
        next_passage: 3,
        reconciliation_complete: false,
        compatible_source: true,
        error: null,
      },
    }),
    null
  );
  requireCheck(
    checks,
    'running_status_controls',
    el('character-roster-status-badge').textContent === 'Running'
      && el('btn-cancel-character-roster').style.display === ''
      && el('btn-discover-character-roster').disabled === true
      && el('btn-discard-character-roster-progress').style.display === 'none'
      && el('character-roster-progress').textContent.includes('2 of 5 passages'),
    { progress: el('character-roster-progress').textContent }
  );

  harness.reset();
  harness.queues.get.push(sampleStatus({ active: 'draft', roster: draft }));
  harness.queues.get.push(draft);
  await ui.refreshCharacterRosterStatus();
  requireCheck(
    checks,
    'refresh_fetches_status_and_active_artifact',
    harness.calls.length === 2
      && harness.calls[0].url === '/api/character_roster/status'
      && harness.calls[1].url === '/api/character_roster/draft'
      && ui.getDraft().draft_fingerprint === draft.draft_fingerprint,
    { calls: harness.calls }
  );

  harness.reset();
  harness.queues.get.push(sampleStatus({ active: 'none', roster: null, running: true }));
  await ui.refreshCharacterRosterStatus();
  const timerId = ui.getTimer();
  requireCheck(
    checks,
    'polling_starts',
    timerId !== null
      && harness.timers.get(timerId).milliseconds === 1200,
    { timerId }
  );
  harness.queues.get.push(sampleStatus({ active: 'none', roster: null, running: false }));
  await harness.timers.get(timerId).callback();
  requireCheck(
    checks,
    'polling_stops',
    ui.getTimer() === null && harness.timers.size === 0,
    { timer: ui.getTimer(), size: harness.timers.size }
  );

  harness.reset();
  ui.setTimer(harness.context.setInterval(() => {}, 1200));
  harness.queues.get.push({
    __error: { message: '<script>status failure</script>', code: 'status_error' },
  });
  await ui.refreshCharacterRosterStatus();
  requireCheck(
    checks,
    'polling_error_is_safe_and_cleans_timer',
    ui.getTimer() === null
      && harness.timers.size === 0
      && el('character-roster-error').textContent === '<script>status failure</script>'
      && el('character-roster-error').innerHTML === '',
    {
      text: el('character-roster-error').textContent,
      timer: ui.getTimer(),
    }
  );

  async function exerciseAction(action, options = {}) {
    harness.reset();
    const current = options.draft || sampleDraft();
    ui.setDraft(current);
    const entry = current.entries[0];
    const button = new MockElement(`button-${action}`);
    button.dataset = {
      rosterAction: action,
      entryId: entry.id,
      otherEntryId: options.otherEntryId || '',
      value: options.dataValue || '',
    };
    if (action === 'rename') {
      el(`roster-rename-${entry.id}`).value = 'THE SEVENTH DOCTOR';
      el(`roster-display-${entry.id}`).value = 'The Seventh Doctor';
    }
    if (action === 'add_alias') {
      el(`roster-alias-${entry.id}`).value = 'DOCTOR';
    }
    if (action === 'mark_unresolved') {
      el(`roster-unresolved-${entry.id}`).value = 'Which incarnation?';
    }
    if (action === 'exclude') {
      harness.prompts.push('Not a character.');
      harness.confirmations.push(true);
    }
    if (action === 'merge') {
      harness.confirmations.push(true);
    }
    const updated = {
      ...current,
      draft_fingerprint: `${action}-fingerprint`,
      review_history: [{ action }],
    };
    harness.queues.post.push({ status: 'updated', draft: updated });
    harness.queueDraftRefresh(updated);
    await ui.performCharacterRosterAction(button);
    const post = harness.calls.find((call) => call.method === 'POST');
    return { post, button, updated };
  }

  for (const [action, options] of [
    ['confirm', {}],
    ['rename', {}],
    ['add_alias', {}],
    ['reject_alias', { dataValue: 'DOCTOR' }],
    ['mark_unresolved', {}],
    ['keep_separate', { otherEntryId: 'character_abcdefabcdefabcdefabcdef' }],
    ['merge', { otherEntryId: 'character_abcdefabcdefabcdefabcdef' }],
    ['exclude', {}],
  ]) {
    const result = await exerciseAction(action, options);
    const body = result.post && result.post.body;
    requireCheck(
      checks,
      `action_${action}`,
      result.post
        && result.post.url === '/api/character_roster/draft/action'
        && body.action === action
        && body.draft_fingerprint === 'draft-fingerprint'
        && body.entry_id === sampleDraft().entries[0].id
        && result.button.disabled === false,
      { post: result.post }
    );
  }

  harness.reset();
  ui.setDraft(draft);
  const staleButton = new MockElement('stale-button');
  staleButton.dataset = {
    rosterAction: 'confirm',
    entryId: draft.entries[0].id,
  };
  harness.queues.post.push({
    __error: { message: 'Draft changed.', code: 'stale_draft' },
  });
  harness.queueDraftRefresh(draft);
  await ui.performCharacterRosterAction(staleButton);
  requireCheck(
    checks,
    'stale_action_refreshes',
    harness.calls.some((call) => call.url === '/api/character_roster/status')
      && harness.toasts.some((toast) => toast.type === 'warning')
      && staleButton.disabled === false,
    { calls: harness.calls, toasts: harness.toasts }
  );

  harness.reset();
  ui.setDraft(draft);
  harness.confirmations.push(false);
  await el('btn-discover-character-roster').listeners.click();
  requireCheck(
    checks,
    'rediscovery_cancel',
    !harness.calls.some((call) => call.url === '/api/character_roster/discover')
  );

  harness.reset();
  ui.setDraft(draft);
  harness.confirmations.push(true);
  harness.queues.post.push({ status: 'started' });
  harness.queueDraftRefresh(draft);
  await el('btn-discover-character-roster').listeners.click();
  const discoverCall = harness.calls.find(
    (call) => call.url === '/api/character_roster/discover'
  );
  requireCheck(
    checks,
    'rediscovery_confirmation_and_payload',
    discoverCall
      && discoverCall.body.replace_draft === true
      && discoverCall.body.passage_size === 12000
      && discoverCall.body.overlap_chars === 1200,
    { call: discoverCall }
  );

  harness.reset();
  harness.queues.post.push({ status: 'cancelling' });
  harness.queues.get.push(sampleStatus({ active: 'none', roster: null }));
  await el('btn-cancel-character-roster').listeners.click();
  requireCheck(
    checks,
    'cancel_control',
    harness.calls.some((call) => call.url === '/api/character_roster/cancel')
      && harness.toasts.some((toast) => toast.type === 'warning'),
    { calls: harness.calls }
  );

  harness.reset();
  harness.confirmations.push(false);
  await el('btn-discard-character-roster-progress').listeners.click();
  requireCheck(
    checks,
    'discard_cancel',
    !harness.calls.some((call) => call.url === '/api/character_roster/discard-progress')
  );

  harness.reset();
  harness.confirmations.push(true);
  harness.queues.post.push({ status: 'discarded' });
  harness.queues.get.push(sampleStatus({ active: 'none', roster: null }));
  await el('btn-discard-character-roster-progress').listeners.click();
  requireCheck(
    checks,
    'discard_success',
    harness.calls.some((call) => call.url === '/api/character_roster/discard-progress')
      && harness.toasts.some((toast) => toast.type === 'success'),
    { calls: harness.calls }
  );

  harness.reset();
  ui.setDraft(draft);
  harness.queues.post.push({ status: 'approved', roster: approved });
  harness.queues.get.push(sampleStatus({ active: 'approved', roster: approved }));
  harness.queues.get.push(approved);
  harness.queues.get.push({ available: true, entries: [] });
  await el('btn-approve-character-roster').listeners.click({
    currentTarget: el('btn-approve-character-roster'),
  });
  const approvalCall = harness.calls.find(
    (call) => call.url === '/api/character_roster/approve'
  );
  requireCheck(
    checks,
    'resolved_roster_approves_without_per_character_confirmation',
    approvalCall
      && approvalCall.body.draft_fingerprint === draft.draft_fingerprint
      && approvalCall.body.acknowledged_unresolved === false
      && !harness.calls.some((call) => call.method === 'CONFIRM')
      && harness.toasts.some((toast) => toast.type === 'success')
      && ui.getApproved().status === 'approved',
    { call: approvalCall, toasts: harness.toasts }
  );

  harness.reset();
  const replacementStatus = sampleStatus({ active: 'draft', roster: draft });
  replacementStatus.working_draft = true;
  replacementStatus.approved = {
    exists: true,
    status: 'approved',
    compatible_source: true,
    counts: countsFor(approved),
    fingerprint: approved.roster_fingerprint,
    error: null,
  };
  replacementStatus.revision_history = {
    count: 0,
    latest_available: null,
  };
  ui.setDraft(draft);
  ui.setLastStatus(replacementStatus);
  ui.renderCharacterRosterStatus(replacementStatus, draft);
  requireCheck(
    checks,
    'replacement_draft_has_one_explicit_bulk_action',
    el('character-roster-approval').style.display === ''
      && el('btn-approve-character-roster').innerHTML.includes('Replace approved roster')
      && el('character-roster-summary').textContent.includes('replace the approved roster'),
    {
      action: el('btn-approve-character-roster').innerHTML,
      summary: el('character-roster-summary').textContent,
    }
  );
  harness.confirmations.push(true);
  harness.queues.post.push({
    status: 'replaced',
    roster: approved,
    revision: { revision_id: 'roster_revision_1' },
  });
  harness.queues.get.push(sampleStatus({ active: 'approved', roster: approved }));
  harness.queues.get.push(approved);
  harness.queues.get.push({ available: true, entries: [] });
  await el('btn-approve-character-roster').listeners.click({
    currentTarget: el('btn-approve-character-roster'),
  });
  const replacementCall = harness.calls.find(
    (call) => call.url === '/api/character_roster/approve'
  );
  requireCheck(
    checks,
    'replacement_submits_both_stale_guards_and_one_confirmation',
    replacementCall
      && replacementCall.body.replace_existing === true
      && replacementCall.body.expected_approved_fingerprint === approved.roster_fingerprint
      && replacementCall.body.draft_fingerprint === draft.draft_fingerprint
      && harness.calls.filter((call) => call.method === 'CONFIRM').length === 1
      && harness.toasts.some((toast) => toast.message.includes('available for undo')),
    { call: replacementCall, calls: harness.calls, toasts: harness.toasts }
  );

  harness.reset();
  const rollbackStatus = sampleStatus({ active: 'approved', roster: approved });
  rollbackStatus.revision_history = {
    count: 1,
    latest_available: {
      revision_id: 'roster_revision_1',
      replacement_roster_fingerprint: approved.roster_fingerprint,
    },
  };
  ui.renderCharacterRosterStatus(rollbackStatus, approved);
  requireCheck(
    checks,
    'approved_replacement_exposes_persistent_undo',
    el('btn-rollback-character-roster').style.display === ''
      && el('btn-rollback-character-roster').dataset.revisionId === 'roster_revision_1'
      && el('btn-rollback-character-roster').dataset.currentFingerprint === approved.roster_fingerprint,
    { button: el('btn-rollback-character-roster') }
  );
  harness.confirmations.push(true);
  harness.queues.post.push({ status: 'restored', roster: approved });
  harness.queues.get.push(sampleStatus({ active: 'approved', roster: approved }));
  harness.queues.get.push(approved);
  harness.queues.get.push({ available: true, entries: [] });
  await el('btn-rollback-character-roster').listeners.click({
    currentTarget: el('btn-rollback-character-roster'),
  });
  const rollbackCall = harness.calls.find(
    (call) => call.url === '/api/character_roster/rollback'
  );
  requireCheck(
    checks,
    'rollback_submits_revision_and_current_fingerprint',
    rollbackCall
      && rollbackCall.body.revision_id === 'roster_revision_1'
      && rollbackCall.body.expected_current_fingerprint === approved.roster_fingerprint
      && harness.toasts.some((toast) => toast.message.includes('restored')),
    { call: rollbackCall, toasts: harness.toasts }
  );

  harness.reset();
  const unresolvedDraft = sampleDraft({
    entries: [sampleEntry({
      resolution_status: 'unresolved',
      unresolved_questions: ['Which incarnation?'],
    })],
  });
  ui.setDraft(unresolvedDraft);
  harness.confirmations.push(true);
  harness.queues.post.push({ status: 'approved', roster: approved });
  harness.queues.get.push(sampleStatus({ active: 'approved', roster: approved }));
  harness.queues.get.push(approved);
  harness.queues.get.push({ available: true, entries: [] });
  await el('btn-approve-character-roster').listeners.click({
    currentTarget: el('btn-approve-character-roster'),
  });
  const unresolvedApproval = harness.calls.find(
    (call) => call.url === '/api/character_roster/approve'
  );
  requireCheck(
    checks,
    'unresolved_roster_requires_one_bulk_acknowledgment',
    unresolvedApproval
      && unresolvedApproval.body.acknowledged_unresolved === true
      && harness.calls.filter((call) => call.method === 'CONFIRM').length === 1,
    { call: unresolvedApproval, confirmations: harness.calls }
  );

  harness.reset();
  ui.setDraft(draft);
  harness.queues.post.push({
    __error: { message: 'Draft changed.', code: 'stale_draft' },
  });
  harness.queueDraftRefresh(draft);
  await el('btn-approve-character-roster').listeners.click({
    currentTarget: el('btn-approve-character-roster'),
  });
  requireCheck(
    checks,
    'stale_approval_refreshes',
    harness.calls.some((call) => call.url === '/api/character_roster/status')
      && harness.toasts.some((toast) => toast.type === 'warning'),
    { calls: harness.calls, toasts: harness.toasts }
  );

  return {
    status: 'PASS',
    checkCount: Object.keys(checks).length,
    checks,
    extractedFunctions: 7,
    extractedHandlers: 7,
  };
}

async function main() {
  const index = process.argv.indexOf('--repo-root');
  if (index === -1 || !process.argv[index + 1]) {
    throw new Error('--repo-root is required');
  }
  const report = await run(path.resolve(process.argv[index + 1]));
  process.stdout.write(`${REPORT_PREFIX}${JSON.stringify(report)}\n`);
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
