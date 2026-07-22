'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const REPORT_PREFIX = 'PHASE17E_UI_REPORT=';

function requireCheck(checks, name, condition, details = {}) {
  checks[name] = { ok: Boolean(condition), ...details };
  if (!condition) {
    throw new Error(`Phase 17E UI check failed: ${name}: ${JSON.stringify(details)}`);
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
  const start = match.index;
  const brace = source.indexOf('{', start);
  const end = scanToMatching(source, brace, '{', '}');
  return source.slice(start, end + 1);
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

class MockClassList {
  constructor() {
    this.values = new Set();
  }
  add(...names) {
    names.forEach((name) => this.values.add(name));
  }
  contains(name) {
    return this.values.has(name);
  }
  clear() {
    this.values.clear();
  }
}

class MockElement {
  constructor(id = '') {
    this.id = id;
    this.style = { display: '' };
    this.className = '';
    this.classList = new MockClassList();
    this.dataset = {};
    this.disabled = false;
    this.files = [];
    this.value = '';
    this.checked = false;
    this.title = '';
    this.children = [];
    this.listeners = {};
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this._textContent = '';
    this._innerText = '';
    this._innerHTML = '';
    this.innerHTMLWrites = 0;
  }
  set textContent(value) {
    this._textContent = String(value ?? '');
  }
  get textContent() {
    return this._textContent;
  }
  set innerText(value) {
    this._innerText = String(value ?? '');
  }
  get innerText() {
    return this._innerText;
  }
  set innerHTML(value) {
    this._innerHTML = String(value ?? '');
    this.children = [];
    this.innerHTMLWrites += 1;
  }
  get innerHTML() {
    return this._innerHTML;
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  addEventListener(type, handler) {
    this.listeners[type] = handler;
  }
  removeAttribute(name) {
    if (name === 'title') this.title = '';
  }
  resetVisual() {
    this.style.display = '';
    this.className = '';
    this.classList.clear();
    this.dataset = {};
    this.disabled = false;
    this.title = '';
    this.children = [];
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this._textContent = '';
    this._innerText = '';
    this._innerHTML = '';
    this.innerHTMLWrites = 0;
  }
}

function makeStatus(checkpoint, result = { status: 'missing' }, process = {}) {
  return {
    process: { running: false, logs: [], ...process },
    checkpoint: {
      status: 'none',
      completed_chunks: 0,
      total_chunks: 0,
      next_chunk: null,
      percent_complete: 0,
      reasons: [],
      ...checkpoint,
    },
    result,
  };
}

function createHarness(repoRoot) {
  const htmlPath = path.join(repoRoot, 'app', 'static', 'index.html');
  const source = fs.readFileSync(htmlPath, 'utf8');
  const functionNames = [
    'setPlainStatus',
    'scriptGenerationResultText',
    'scriptGenerationProvenancePresentation',
    'scriptGenerationDateText',
    'setScriptGenerationProvenanceText',
    'renderScriptGenerationProvenance',
    'renderScriptGenerationStatus',
    'refreshScriptGenerationStatus',
    'startScriptGenerationStatusPolling',
    'loadScript',
  ];
  const extractedFunctions = functionNames
    .map((name) => extractFunction(source, name))
    .join('\n\n');

  const discardStatement = extractStatement(
    source,
    /document\.getElementById\(\s*'btn-discard-generation-state'\s*\)\.addEventListener/
  );
  const uploadStatement = extractStatement(
    source,
    /document\.getElementById\('file-upload'\)\.addEventListener/
  );
  const generateStatement = extractStatement(
    source,
    /document\.getElementById\('btn-gen-script'\)\.addEventListener/
  );

  const discardEnd = source.indexOf(discardStatement) + discardStatement.length;
  const scriptTabMarker = source.indexOf('// --- Script Tab ---', discardEnd);
  const initialSlice = source.slice(discardEnd, scriptTabMarker);

  const requiredIds = [
    'script-generation-actions-panel',
    'script-generation-summary',
    'script-generation-progress',
    'script-generation-reasons',
    'script-generation-result-status',
    'btn-discard-generation-state',
    'btn-gen-script',
    'script-logs',
    'script-generation-provenance',
    'script-generation-metadata-status',
    'script-generation-provenance-note',
    'script-generation-source-name',
    'script-generation-generated-at',
    'script-generation-model-name',
    'script-generation-backend',
    'script-generation-chunk-count',
    'script-generation-entry-count',
    'script-generation-speakers',
    'script-generation-resume-status',
    'script-generation-script-fingerprint',
    'file-upload',
    'upload-status',
  ];
  const elements = new Map(
    requiredIds.map((id) => [id, new MockElement(id)])
  );

  const document = {
    getElementById(id) {
      return elements.get(id) || null;
    },
    createElement(tagName) {
      return new MockElement(tagName);
    },
  };

  const apiQueues = { get: [], post: [], upload: [] };
  const apiCalls = [];
  const fetchQueue = [];
  const fetchCalls = [];
  const confirmations = [];
  const toasts = [];
  const auxiliaryCalls = [];
  const timers = new Map();
  let nextTimerId = 1;

  async function consume(queue, kind, payload) {
    if (!queue.length) {
      throw new Error(`No queued ${kind} response for ${JSON.stringify(payload)}`);
    }
    const item = queue.shift();
    if (item && item.__throw) throw new Error(item.__throw);
    if (typeof item === 'function') return item(payload);
    return item;
  }

  const API = {
    async get(url) {
      apiCalls.push({ method: 'GET', url });
      return consume(apiQueues.get, 'GET', { url });
    },
    async post(url, body) {
      apiCalls.push({ method: 'POST', url, body });
      return consume(apiQueues.post, 'POST', { url, body });
    },
    async upload(file) {
      apiCalls.push({ method: 'UPLOAD', file });
      return consume(apiQueues.upload, 'UPLOAD', { file });
    },
  };

  function setIntervalMock(callback, milliseconds) {
    const id = nextTimerId;
    nextTimerId += 1;
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
    setInterval: setIntervalMock,
    clearInterval: clearIntervalMock,
    setTimeout,
    clearTimeout,
    async showConfirm(message) {
      auxiliaryCalls.push({ name: 'showConfirm', message });
      return confirmations.length ? confirmations.shift() : false;
    },
    showToast(message, type) {
      toasts.push({ message, type });
    },
    escapeHtml(value) {
      return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
    },
    async loadChunks(force) {
      auxiliaryCalls.push({ name: 'loadChunks', force });
    },
    async loadVoices() {
      auxiliaryCalls.push({ name: 'loadVoices' });
    },
    loadSavedScripts() {
      auxiliaryCalls.push({ name: 'loadSavedScripts' });
    },
    async refreshCharacterRosterStatus() {
      auxiliaryCalls.push({
        name: 'refreshCharacterRosterStatus',
      });
    },
    async refreshCharacterVisualStatus() {
      auxiliaryCalls.push({
        name: 'refreshCharacterVisualStatus',
      });
    },
    resetDesignerForm() {
      auxiliaryCalls.push({ name: 'resetDesignerForm' });
    },
    loadDesignedVoices() {
      auxiliaryCalls.push({ name: 'loadDesignedVoices' });
    },
    async fetch(url, options) {
      fetchCalls.push({ url, options });
      if (!fetchQueue.length) throw new Error('No queued fetch response');
      const item = fetchQueue.shift();
      if (item && item.__throw) throw new Error(item.__throw);
      return item;
    },
  };
  context.globalThis = context;
  vm.createContext(context);

  const program = `
let scriptGenerationStatusTimer = null;
${extractedFunctions}
${discardStatement}
${uploadStatement}
${generateStatement}
globalThis.__phase17e = {
  scriptGenerationResultText,
  scriptGenerationProvenancePresentation,
  renderScriptGenerationProvenance,
  renderScriptGenerationStatus,
  refreshScriptGenerationStatus,
  startScriptGenerationStatusPolling,
  loadScript,
  getTimer: () => scriptGenerationStatusTimer,
  setTimer: (value) => { scriptGenerationStatusTimer = value; }
};
`;
  vm.runInContext(program, context, {
    filename: 'phase17e-extracted-index.js',
  });

  function reset() {
    elements.forEach((element) => element.resetVisual());
    elements.get('file-upload').files = [];
    apiQueues.get.length = 0;
    apiQueues.post.length = 0;
    apiQueues.upload.length = 0;
    apiCalls.length = 0;
    fetchQueue.length = 0;
    fetchCalls.length = 0;
    confirmations.length = 0;
    toasts.length = 0;
    auxiliaryCalls.length = 0;
    timers.clear();
    context.__phase17e.setTimer(null);
  }

  async function settle() {
    await Promise.resolve();
    await new Promise((resolve) => setImmediate(resolve));
  }

  return {
    source,
    initialSlice,
    context,
    elements,
    apiQueues,
    apiCalls,
    fetchQueue,
    fetchCalls,
    confirmations,
    toasts,
    auxiliaryCalls,
    timers,
    reset,
    settle,
  };
}

async function run(repoRoot) {
  const checks = {};
  const harness = createHarness(repoRoot);
  const ui = harness.context.__phase17e;
  const el = (id) => harness.elements.get(id);

  requireCheck(
    checks,
    'actual_source_extraction',
    typeof ui.renderScriptGenerationStatus === 'function'
      && typeof ui.refreshScriptGenerationStatus === 'function'
      && typeof el('btn-gen-script').listeners.click === 'function'
      && typeof el('file-upload').listeners.change === 'function'
      && typeof el('btn-discard-generation-state').listeners.click === 'function',
    { sourceLength: harness.source.length }
  );
  requireCheck(
    checks,
    'initial_status_fetch_present',
    harness.initialSlice.includes('refreshScriptGenerationStatus();'),
    { initialSlice: harness.initialSlice.trim() }
  );

  harness.reset();
  ui.renderScriptGenerationStatus(makeStatus({ status: 'none' }));
  requireCheck(
    checks,
    'render_no_checkpoint',
    el('script-generation-summary').textContent === 'No saved generation progress'
      && el('script-generation-progress').textContent.includes('chunk 1')
      && el('btn-gen-script').disabled === false
      && el('btn-gen-script').innerHTML.includes('Generate Annotated Script')
      && el('btn-discard-generation-state').style.display === 'none',
    {
      summary: el('script-generation-summary').textContent,
      button: el('btn-gen-script').innerHTML,
    }
  );

  harness.reset();
  ui.renderScriptGenerationStatus(makeStatus({
    status: 'compatible',
    completed_chunks: 1,
    total_chunks: 3,
    next_chunk: 2,
    percent_complete: 33.33,
  }));
  requireCheck(
    checks,
    'render_resume_checkpoint',
    el('script-generation-summary').textContent === 'Resume from chunk 2 of 3'
      && el('btn-gen-script').innerHTML.includes('Resume Script')
      && el('btn-discard-generation-state').style.display === '',
    { summary: el('script-generation-summary').textContent }
  );

  harness.reset();
  ui.renderScriptGenerationStatus(makeStatus({
    status: 'finalization_pending',
    completed_chunks: 3,
    total_chunks: 3,
    percent_complete: 100,
  }, { status: 'finalization_pending' }));
  requireCheck(
    checks,
    'render_finalization_checkpoint',
    el('script-generation-summary').textContent === 'All chunks are complete'
      && el('btn-gen-script').innerHTML.includes('Retry Finalization')
      && el('script-generation-resume-status').textContent.includes('3 of 3'),
    {
      button: el('btn-gen-script').innerHTML,
      resume: el('script-generation-resume-status').textContent,
    }
  );

  for (const status of ['incompatible', 'unknown']) {
    harness.reset();
    ui.renderScriptGenerationStatus(makeStatus({
      status,
      total_chunks: 3,
      reasons: [{ code: 'source_changed', title: 'Source changed', explanation: 'Mismatch.' }],
    }));
    requireCheck(
      checks,
      `render_${status}_blocked`,
      el('btn-gen-script').disabled === true
        && el('script-generation-summary').textContent === 'Saved progress cannot be resumed'
        && el('script-generation-reasons').children.length === 1
        && el('script-generation-reasons').children[0].children[0].textContent
          === 'Source changed: Mismatch.',
      { summary: el('script-generation-summary').textContent }
    );
  }

  for (const status of ['corrupt', 'invalid']) {
    harness.reset();
    ui.renderScriptGenerationStatus(makeStatus({ status }));
    requireCheck(
      checks,
      `render_${status}_blocked`,
      el('btn-gen-script').disabled === true
        && el('script-generation-summary').textContent === 'Saved progress is unusable',
      { summary: el('script-generation-summary').textContent }
    );
  }

  harness.reset();
  ui.renderScriptGenerationStatus(makeStatus({
    status: 'compatible',
    completed_chunks: 1,
    total_chunks: 3,
    percent_complete: 33.33,
  }, { status: 'missing' }, {
    running: true,
    logs: ['first', 'second'],
  }));
  requireCheck(
    checks,
    'render_running_state',
    el('btn-gen-script').disabled === true
      && el('btn-gen-script').innerHTML.includes('Generating…')
      && el('btn-discard-generation-state').style.display === 'none'
      && el('script-logs').innerText === 'first\nsecond',
    { button: el('btn-gen-script').innerHTML }
  );

  harness.reset();
  const completeResult = {
    status: 'complete',
    metadata_status: 'valid',
    script_entry_count: 2,
    script_fingerprint: 'full-script-fingerprint',
    errors: [],
    metadata: {
      generated_at_utc: '2026-07-16T12:00:00Z',
      source: { basename: 'book.txt', chunk_count: 4 },
      generation: {
        fingerprint: 'generation-fingerprint',
        effective_identity: {
          model_name: 'qwen3.5:35b-mlx',
          backend: 'ollama-native',
          base_url: 'http://secret.example/v1',
          system_prompt: 'SECRET PROMPT',
          temperature: 0.9,
          raw_telemetry: 'SECRET TELEMETRY',
        },
      },
      result: {
        entry_count: 2,
        script_fingerprint: 'full-script-fingerprint',
        speaker_labels: ['DOCTOR', 'NARRATOR'],
      },
      resume: { resumed: true, previously_completed_chunks: 2 },
    },
  };
  ui.renderScriptGenerationStatus(makeStatus({
    status: 'none',
    total_chunks: 4,
  }, completeResult));
  const provenanceText = [
    'script-generation-source-name',
    'script-generation-model-name',
    'script-generation-backend',
    'script-generation-chunk-count',
    'script-generation-entry-count',
    'script-generation-speakers',
    'script-generation-resume-status',
    'script-generation-script-fingerprint',
    'script-generation-provenance-note',
  ].map((id) => el(id).textContent).join(' | ');
  requireCheck(
    checks,
    'valid_provenance_rendering',
    el('script-generation-metadata-status').textContent === 'Valid metadata'
      && el('script-generation-source-name').textContent === 'book.txt'
      && el('script-generation-model-name').textContent === 'qwen3.5:35b-mlx'
      && el('script-generation-backend').textContent === 'ollama-native'
      && el('script-generation-entry-count').textContent === '2'
      && el('script-generation-speakers').textContent === 'DOCTOR, NARRATOR'
      && el('script-generation-script-fingerprint').textContent === 'full-script-fingerprint'
      && !provenanceText.includes('SECRET')
      && !provenanceText.includes('secret.example')
      && !provenanceText.includes('0.9'),
    { provenanceText }
  );

  harness.reset();
  const importedResult = JSON.parse(JSON.stringify(completeResult));
  importedResult.metadata.source = {
    basename: 'incoming.json',
    fingerprint: null,
    verification_status: 'unverified',
    character_count: 0,
    chunk_count: 0,
  };
  importedResult.metadata.generation.effective_identity = {
    model_name: 'Imported annotated script',
    backend: 'external',
    mode: 'external_import',
  };
  importedResult.metadata.import = {
    origin: { type: 'annotated_script_upload' },
    provenance: {
      status: 'unverified',
      label: 'Imported — source fidelity not verified',
    },
  };
  ui.renderScriptGenerationStatus(makeStatus({ status: 'none' }, importedResult));
  requireCheck(
    checks,
    'imported_provenance_persists_after_reload',
    el('script-generation-metadata-status').textContent === 'Imported — source fidelity not verified'
      && el('script-generation-metadata-status').dataset.state === 'warning'
      && el('script-generation-provenance-note').textContent.includes('No source-fidelity claim was made')
      && el('script-generation-model-name').textContent === 'Imported annotated script'
      && el('script-generation-backend').textContent === 'external',
    {
      status: el('script-generation-metadata-status').textContent,
      note: el('script-generation-provenance-note').textContent,
    }
  );

  harness.reset();
  ui.renderScriptGenerationStatus(makeStatus({ status: 'none' }, {
    status: 'metadata_invalid',
    metadata_status: 'invalid',
    metadata: completeResult.metadata,
    script_entry_count: 7,
    script_fingerprint: 'current-script-only',
    errors: ['<script>alert(1)</script>'],
  }));
  requireCheck(
    checks,
    'invalid_metadata_not_trusted',
    el('script-generation-metadata-status').textContent === 'Invalid metadata'
      && el('script-generation-source-name').textContent === '—'
      && el('script-generation-model-name').textContent === '—'
      && el('script-generation-entry-count').textContent === '7'
      && el('script-generation-script-fingerprint').textContent === 'current-script-only'
      && el('script-generation-provenance-note').textContent.includes('<script>alert(1)</script>')
      && el('script-generation-provenance-note').innerHTMLWrites === 0,
    {
      note: el('script-generation-provenance-note').textContent,
      source: el('script-generation-source-name').textContent,
    }
  );

  const resultStatuses = [
    'complete', 'legacy', 'finalization_pending', 'missing',
    'metadata_corrupt', 'metadata_invalid', 'orphan_metadata',
    'script_corrupt', 'script_invalid', 'unexpected_state',
  ];
  const presentations = resultStatuses.map((status) => ({
    status,
    resultText: ui.scriptGenerationResultText({ status }),
    presentation: ui.scriptGenerationProvenancePresentation({ status }),
  }));
  requireCheck(
    checks,
    'all_result_presentations',
    presentations.every((item) => item.resultText && item.presentation.label && item.presentation.note),
    { presentations }
  );

  harness.reset();
  harness.apiQueues.get.push(makeStatus({ status: 'none' }));
  await ui.refreshScriptGenerationStatus();
  requireCheck(
    checks,
    'initial_refresh_behavior',
    harness.apiCalls.length === 1
      && harness.apiCalls[0].url === '/api/script_generation/status'
      && el('script-generation-summary').textContent === 'No saved generation progress',
    { calls: harness.apiCalls }
  );

  harness.reset();
  harness.apiQueues.get.push(makeStatus({
    status: 'compatible', completed_chunks: 1, total_chunks: 3, next_chunk: 2,
  }, { status: 'missing' }, { running: true, logs: ['running'] }));
  ui.startScriptGenerationStatusPolling();
  await harness.settle();
  const firstTimer = ui.getTimer();
  const firstTimerRecord = harness.timers.get(firstTimer);
  requireCheck(
    checks,
    'polling_start',
    firstTimer !== null
      && Boolean(firstTimerRecord)
      && firstTimerRecord.milliseconds === 1200
      && el('script-generation-summary').textContent === 'Script generation is running',
    { timer: firstTimer, calls: harness.apiCalls }
  );
  harness.apiQueues.get.push(makeStatus({
    status: 'compatible', completed_chunks: 2, total_chunks: 3, next_chunk: 3,
  }, { status: 'missing' }, { running: true, logs: ['updated'] }));
  await firstTimerRecord.callback();
  requireCheck(
    checks,
    'polling_update',
    ui.getTimer() === firstTimer
      && el('script-logs').innerText === 'updated'
      && el('script-generation-progress').textContent.includes('2 of 3'),
    { logs: el('script-logs').innerText }
  );
  harness.apiQueues.get.push(makeStatus({ status: 'none' }));
  await firstTimerRecord.callback();
  requireCheck(
    checks,
    'polling_stop',
    ui.getTimer() === null && harness.timers.size === 0,
    { timer: ui.getTimer(), timerCount: harness.timers.size }
  );

  harness.reset();
  ui.setTimer(harness.context.setInterval(() => {}, 1200));
  harness.apiQueues.get.push({ __throw: '<img src=x onerror=alert(1)>' });
  await ui.refreshScriptGenerationStatus();
  requireCheck(
    checks,
    'status_error_safe_output_and_timer_cleanup',
    el('script-generation-summary').textContent === 'Unable to inspect generation status'
      && el('script-generation-progress').textContent === '<img src=x onerror=alert(1)>'
      && el('script-generation-progress').innerHTMLWrites === 0
      && ui.getTimer() === null
      && harness.timers.size === 0,
    {
      text: el('script-generation-progress').textContent,
      timer: ui.getTimer(),
      timerCount: harness.timers.size,
    }
  );

  const discardHandler = el('btn-discard-generation-state').listeners.click;
  harness.reset();
  harness.confirmations.push(false);
  await discardHandler();
  requireCheck(
    checks,
    'discard_cancel',
    harness.apiCalls.length === 0 && harness.toasts.length === 0,
    { calls: harness.apiCalls, toasts: harness.toasts }
  );

  harness.reset();
  harness.confirmations.push(true);
  harness.apiQueues.post.push({ status: 'discarded' });
  harness.apiQueues.get.push(makeStatus({ status: 'none' }));
  await discardHandler();
  requireCheck(
    checks,
    'discard_success',
    harness.apiCalls.some((call) => call.url === '/api/script_generation/discard')
      && harness.apiCalls.some((call) => call.url === '/api/script_generation/status')
      && harness.toasts.some((toast) => toast.type === 'success'),
    { calls: harness.apiCalls, toasts: harness.toasts }
  );

  harness.reset();
  harness.confirmations.push(true);
  harness.apiQueues.post.push({ __throw: 'running conflict' });
  await discardHandler();
  requireCheck(
    checks,
    'discard_failure',
    harness.toasts.length === 1
      && harness.toasts[0].type === 'error'
      && harness.toasts[0].message.includes('running conflict'),
    { toasts: harness.toasts }
  );

  const uploadHandler = el('file-upload').listeners.change;
  harness.reset();
  el('file-upload').files = [{ name: 'book.txt' }];
  harness.apiQueues.upload.push({ filename: 'book.txt' });
  harness.apiQueues.get.push(makeStatus({ status: 'none' }));
  await uploadHandler();
  requireCheck(
    checks,
    'upload_refresh_success',
    el('upload-status').innerHTML.includes('Loaded: book.txt')
      && harness.apiCalls.some((call) => call.method === 'UPLOAD')
      && harness.apiCalls.some((call) => call.url === '/api/script_generation/status')
      && harness.auxiliaryCalls.some(
        (call) => call.name === 'refreshCharacterRosterStatus'
      ),
    { html: el('upload-status').innerHTML, calls: harness.apiCalls }
  );

  harness.reset();
  el('file-upload').files = [{ name: 'bad.txt' }];
  harness.apiQueues.upload.push({ __throw: '<bad upload>' });
  harness.apiQueues.get.push(makeStatus({ status: 'none' }));
  await uploadHandler();
  requireCheck(
    checks,
    'upload_error_escaped',
    el('upload-status').innerHTML.includes('&lt;bad upload&gt;')
      && !el('upload-status').innerHTML.includes('<bad upload>'),
    { html: el('upload-status').innerHTML }
  );

  const generateHandler = el('btn-gen-script').listeners.click;
  harness.reset();
  await generateHandler();
  requireCheck(
    checks,
    'generate_requires_source',
    harness.apiCalls.length === 0
      && el('upload-status').innerHTML.includes('Please select a text file first'),
    { html: el('upload-status').innerHTML }
  );

  async function runGenerateMode(mode, payload, expectedToast) {
    harness.reset();
    el('upload-status').innerHTML = '<span class="text-success">Loaded</span>';
    harness.apiQueues.post.push({ mode, ...payload });
    harness.apiQueues.get.push(makeStatus({ status: 'none' }));
    await generateHandler();
    await harness.settle();
    const matchingToast = expectedToast
      ? harness.toasts.some((toast) => toast.message.includes(expectedToast))
      : true;
    return {
      ok: harness.apiCalls.some((call) => call.url === '/api/generate_script')
        && harness.apiCalls.some((call) => call.url === '/api/script_generation/status')
        && matchingToast,
      calls: [...harness.apiCalls],
      toasts: [...harness.toasts],
    };
  }

  const newMode = await runGenerateMode('new', {}, null);
  requireCheck(checks, 'generate_new_feedback', newMode.ok, newMode);
  const resumeMode = await runGenerateMode(
    'resume', { next_chunk: 2, total_chunks: 5 }, 'Resuming from chunk 2 of 5'
  );
  requireCheck(checks, 'generate_resume_feedback', resumeMode.ok, resumeMode);
  const finalizeMode = await runGenerateMode(
    'finalize', {}, 'Retrying finalization without regenerating chunks'
  );
  requireCheck(checks, 'generate_finalize_feedback', finalizeMode.ok, finalizeMode);

  harness.reset();
  el('upload-status').innerHTML = '<span class="text-success">Loaded</span>';
  harness.apiQueues.post.push({ __throw: '<blocked>' });
  harness.apiQueues.get.push(makeStatus({ status: 'incompatible' }));
  await generateHandler();
  requireCheck(
    checks,
    'generate_error_escaped_and_refreshed',
    el('upload-status').innerHTML.includes('&lt;blocked&gt;')
      && !el('upload-status').innerHTML.includes('<blocked>')
      && harness.apiCalls.some((call) => call.url === '/api/script_generation/status'),
    { html: el('upload-status').innerHTML, calls: harness.apiCalls }
  );

  harness.reset();
  harness.confirmations.push(true);
  harness.fetchQueue.push({ ok: true, async json() { return {}; } });
  harness.apiQueues.get.push(makeStatus({ status: 'none' }, completeResult));
  await ui.loadScript('demo');
  requireCheck(
    checks,
    'saved_script_refresh',
    harness.fetchCalls.length === 1
      && harness.fetchCalls[0].url === '/api/scripts/load'
      && harness.apiCalls.some((call) => call.url === '/api/script_generation/status')
      && harness.auxiliaryCalls.some((call) => call.name === 'loadChunks')
      && harness.auxiliaryCalls.some((call) => call.name === 'loadVoices')
      && harness.auxiliaryCalls.some((call) => call.name === 'loadSavedScripts'),
    {
      fetchCalls: harness.fetchCalls,
      apiCalls: harness.apiCalls,
      auxiliaryCalls: harness.auxiliaryCalls,
    }
  );

  return {
    status: 'PASS',
    checkCount: Object.keys(checks).length,
    checks,
    extractedFunctions: 9,
    extractedHandlers: 3,
  };
}

async function main() {
  const repoRootIndex = process.argv.indexOf('--repo-root');
  if (repoRootIndex === -1 || !process.argv[repoRootIndex + 1]) {
    throw new Error('--repo-root is required');
  }
  const report = await run(path.resolve(process.argv[repoRootIndex + 1]));
  process.stdout.write(`${REPORT_PREFIX}${JSON.stringify(report)}\n`);
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
