'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const REPORT_PREFIX = 'EXTERNAL_WORKFLOW_UI_REPORT=';

function requireCheck(checks, name, condition, details = {}) {
  checks[name] = { ok: Boolean(condition), ...details };
  if (!condition) {
    throw new Error(`Task Bundle UI check failed: ${name}: ${JSON.stringify(details)}`);
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

class MockElement {
  constructor(id = '', tagName = 'div') {
    this.id = id;
    this.tagName = tagName.toUpperCase();
    this.style = { display: '' };
    this.dataset = {};
    this.disabled = false;
    this.hidden = false;
    this.open = false;
    this.value = '';
    this.children = [];
    this.files = [];
    this.clickCount = 0;
    this.focused = false;
    this._textContent = '';
    this._innerHTML = '';
    this.label = '';
  }
  set textContent(value) {
    this._textContent = String(value ?? '');
  }
  get textContent() {
    return this._textContent;
  }
  set innerHTML(value) {
    this._innerHTML = String(value ?? '');
  }
  get innerHTML() {
    return this._innerHTML;
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  replaceChildren(...children) {
    this.children = [...children];
  }
  get options() {
    const result = [];
    const collect = node => {
      if (node.tagName === 'OPTION') result.push(node);
      node.children.forEach(collect);
    };
    this.children.forEach(collect);
    return result;
  }
  click() {
    this.clickCount += 1;
  }
  remove() {}
  focus() {
    this.focused = true;
  }
  setAttribute(name, value) {
    this[name] = value;
  }
}

class MockFormData {
  constructor() {
    this.entries = [];
  }
  append(name, value) {
    this.entries.push([name, value]);
  }
  get(name) {
    const found = this.entries.find(([key]) => key === name);
    return found ? found[1] : null;
  }
}

function response(body, { ok = true, status = 200, statusText = 'OK' } = {}) {
  return {
    ok,
    status,
    statusText,
    async json() {
      return body;
    },
  };
}

function createHarness(source) {
  const ids = [
    'external-workflow-status',
    'task-bundle-task',
    'task-bundle-target-field',
    'task-bundle-target-label',
    'task-bundle-target',
    'task-bundle-selection-summary',
    'btn-export-task-bundle',
    'task-bundle-export-note',
    'completed-task-file',
    'original-task-file',
    'original-task-file-wrap',
    'btn-import-completed-task',
    'task-bundle-import-note',
    'external-structured-result',
    'script-external-workflow',
    'external-structured-result-status',
    'external-structured-result-note',
    'btn-open-structured-destination',
    'external-structured-result-title',
    'external-structured-result-task',
    'external-structured-result-destination',
    'external-structured-result-target',
    'external-structured-result-count',
    'external-structured-result-json',
    'persona-catalog-conflicts',
    'persona-catalog-conflict-list',
    'persona-catalog-new-count',
    'btn-apply-persona-catalog',
  ];
  const elements = new Map(ids.map(id => [id, new MockElement(id)]));
  elements.set('task-bundle-task', new MockElement('task-bundle-task', 'select'));
  const created = [];
  const apiCalls = [];
  const fetchCalls = [];
  const fetchQueue = [];
  const toasts = [];
  const activatedTabs = [];
  const refreshes = [];
  const scriptCandidates = [];
  const statusUpdates = [];
  const checkedPersonaReplacements = [];

  const document = {
    body: new MockElement('body', 'body'),
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new MockElement(id));
      return elements.get(id);
    },
    createElement(tagName) {
      const element = new MockElement('', tagName);
      created.push(element);
      return element;
    },
    querySelectorAll(selector) {
      if (selector === '.persona-catalog-replace:checked') {
        return checkedPersonaReplacements;
      }
      return [];
    },
  };

  const API = {
    getQueue: [],
    postQueue: [],
    async get(url) {
      apiCalls.push({ method: 'GET', url });
      if (!this.getQueue.length) throw new Error(`No GET response queued for ${url}`);
      const next = this.getQueue.shift();
      if (next instanceof Error) throw next;
      return next;
    },
    async post(url, body) {
      apiCalls.push({ method: 'POST', url, body });
      if (!this.postQueue.length) throw new Error(`No POST response queued for ${url}`);
      const next = this.postQueue.shift();
      if (next instanceof Error) throw next;
      return next;
    },
    async _handleError(result) {
      if (result.ok) return;
      const payload = await result.json();
      const detail = payload.detail || payload;
      const error = new Error(detail.message || result.statusText || 'Request failed');
      error.code = detail.code;
      error.details = detail.details || {};
      throw error;
    },
  };

  const context = {
    console,
    document,
    API,
    FormData: MockFormData,
    Option: function Option(text, value) {
      const option = new MockElement('', 'option');
      option.textContent = text;
      option.value = value;
      return option;
    },
    navigator: {},
    escapeHtml(value) {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
    },
    setTimeout,
    clearTimeout,
    externalStructuredResult: null,
    taskBundleRegistry: [],
    setPlainStatus(element, text, state) {
      element.textContent = text;
      element.dataset.state = state;
      statusUpdates.push({ id: element.id, text, state });
    },
    showToast(message, type) {
      toasts.push({ message, type });
    },
    async fetch(url, options) {
      fetchCalls.push({ url, options });
      if (!fetchQueue.length) throw new Error(`No fetch response queued for ${url}`);
      return fetchQueue.shift();
    },
    activateWorkspaceTab(tab) {
      activatedTabs.push(tab);
      return true;
    },
    async refreshCharacterRosterStatus() {
      refreshes.push('roster');
    },
    async refreshCharacterVisualStatus() {
      refreshes.push('visual');
    },
    async refreshVoiceTrainingStatus() {
      refreshes.push('voice-projects');
    },
    async refreshCharactersWorkspace() {
      refreshes.push('characters');
    },
    async refreshCharacterRosterImportReconciliation(options) {
      refreshes.push(options?.open ? 'roster-import-modal' : 'roster-import');
    },
    renderExternalScriptCandidate(candidate) {
      scriptCandidates.push(candidate);
    },
  };
  context.globalThis = context;
  context.window = context;
  vm.createContext(context);

  const functionNames = [
    'setExternalWorkflowStatus',
    'externalStructuredTaskLabel',
    'renderPersonaCatalogConflicts',
    'applyPersonaCatalogSelection',
    'renderExternalStructuredResult',
    'selectedTaskBundleDefinition',
    'taskBundleDestinationLabel',
    'updateTaskBundleTargetState',
    'loadTaskBundleRegistry',
    'exportTaskBundle',
    'routeCompletedTaskResult',
    'importCompletedTask',
    'postExternalWorkflowForm',
    'triggerExternalWorkflowDownload',
  ];
  const code = functionNames.map(name => extractFunction(source, name)).join('\n\n');
  vm.runInContext(code, context, { filename: 'task-bundle-ui-functions.js' });
  return {
    context,
    elements,
    created,
    apiCalls,
    fetchCalls,
    fetchQueue,
    toasts,
    activatedTabs,
    refreshes,
    scriptCandidates,
    statusUpdates,
    checkedPersonaReplacements,
    extractedFunctions: functionNames.length,
  };
}

async function main() {
  const args = process.argv.slice(2);
  const rootIndex = args.indexOf('--repo-root');
  if (rootIndex < 0 || !args[rootIndex + 1]) {
    throw new Error('--repo-root is required');
  }
  const repoRoot = path.resolve(args[rootIndex + 1]);
  const source = fs.readFileSync(
    path.join(repoRoot, 'app', 'static', 'index.html'),
    'utf8'
  );
  const checks = {};
  const harness = createHarness(source);
  const { context, elements } = harness;

  const registry = {
    schema_version: 2,
    tasks: [
      {
        task_type: 'script_generation',
        label: 'Generate annotated Script',
        stage: 'script',
        native_destination: 'script_review',
        transfer_policy: 'script_candidate',
        target_kind: null,
      },
      {
        task_type: 'persona_catalog_generation',
        label: 'Create voice profiles for all speakers',
        stage: 'persona',
        native_destination: 'expressive_voices',
        transfer_policy: 'persona_catalog_drafts',
        target_kind: null,
      },
      {
        task_type: 'persona_generation',
        label: 'Create one voice profile',
        stage: 'persona',
        native_destination: 'expressive_voices',
        transfer_policy: 'persona_draft',
        target_kind: 'speaker',
      },
      {
        task_type: 'visual_discovery',
        label: 'Discover visual evidence',
        stage: 'visual',
        native_destination: 'visual_dossiers',
        transfer_policy: 'visual_observations',
        target_kind: 'character',
      },
      {
        task_type: 'line_direction_audit',
        label: 'Audit line directions',
        stage: 'editor',
        native_destination: 'editor',
        transfer_policy: 'line_direction_review',
        target_kind: null,
      },
    ],
  };
  context.API.getQueue.push(registry);
  await context.loadTaskBundleRegistry();
  const select = elements.get('task-bundle-task');
  requireCheck(
    checks,
    'actual_source_extraction',
    harness.extractedFunctions === 14,
    { extractedFunctions: harness.extractedFunctions }
  );
  requireCheck(
    checks,
    'registry_populates_task_chooser',
    select.options.length === 5
      && select.value === 'script_generation'
      && elements.get('btn-export-task-bundle').disabled === false,
    { options: select.options.map(option => option.value), value: select.value }
  );
  requireCheck(
    checks,
    'default_task_explains_native_destination',
    elements.get('task-bundle-selection-summary').textContent.includes('Script review')
      && elements.get('task-bundle-target-field').hidden === true,
    { summary: elements.get('task-bundle-selection-summary').textContent }
  );

  select.value = 'persona_generation';
  context.updateTaskBundleTargetState();
  requireCheck(
    checks,
    'target_scope_is_registry_driven',
    elements.get('task-bundle-target-field').hidden === false
      && elements.get('task-bundle-target-label').textContent === 'Speaker'
      && elements.get('task-bundle-selection-summary').textContent.includes('Characters'),
    {
      hidden: elements.get('task-bundle-target-field').hidden,
      label: elements.get('task-bundle-target-label').textContent,
      summary: elements.get('task-bundle-selection-summary').textContent,
    }
  );

  elements.get('task-bundle-target').value = 'THE DOCTOR';
  context.API.postQueue.push({
    filename: 'persona.alexandria-task.zip',
    download_url: '/api/tasks/task_123/download',
  });
  await context.exportTaskBundle();
  const exportCall = harness.apiCalls.find(call => call.url === '/api/tasks/export');
  const downloadAnchor = harness.created.find(element => element.tagName === 'A');
  requireCheck(
    checks,
    'export_uses_task_and_scope_without_code',
    exportCall
      && exportCall.body.task_type === 'persona_generation'
      && exportCall.body.target === 'THE DOCTOR'
      && !Object.prototype.hasOwnProperty.call(exportCall.body, 'handoff_id')
      && downloadAnchor
      && downloadAnchor.href === '/api/tasks/task_123/download'
      && downloadAnchor.clickCount === 1,
    { exportCall, anchor: downloadAnchor && { href: downloadAnchor.href, clicks: downloadAnchor.clickCount } }
  );
  requireCheck(
    checks,
    'export_directs_user_to_attach_zip',
    elements.get('task-bundle-export-note').hidden === false
      && harness.toasts.some(item => item.message.includes('Attach the ZIP to ChatGPT')),
    { noteHidden: elements.get('task-bundle-export-note').hidden, toasts: harness.toasts }
  );

  const completedFile = { name: 'completed.json' };
  elements.get('completed-task-file').files = [completedFile];
  elements.get('btn-import-completed-task').disabled = false;
  harness.fetchQueue.push(response({
    kind: 'structured_result',
    task_type: 'persona_generation',
    task_label: 'Create one voice profile',
    target: { kind: 'speaker', value: 'THE DOCTOR' },
    status: 'transferred',
    review: { root_type: 'object', item_count: 2 },
    result: { description: 'Tenor, clear.', ref_text: 'Run,' },
    application: { destination: 'expressive_voices', tab: 'voice-projects' },
    routing: {
      status: 'review_ready',
      native_destination: 'expressive_voices',
      tab: 'voice-projects',
      message: 'Persona draft ready. Nothing was approved automatically.',
    },
  }));
  await context.importCompletedTask();
  const importCall = harness.fetchCalls[0];
  requireCheck(
    checks,
    'completed_task_import_uses_one_file_without_identifier',
    importCall.url === '/api/tasks/import'
      && importCall.options.body.get('file') === completedFile
      && importCall.options.body.get('original_task') === null
      && importCall.options.body.get('handoff_id') === null,
    { url: importCall.url, entries: importCall.options.body.entries }
  );
  requireCheck(
    checks,
    'persona_import_opens_native_review',
    harness.refreshes.includes('characters')
      && harness.activatedTabs.includes('characters')
      && elements.get('external-structured-result-status').textContent === 'Ready for review'
      && elements.get('btn-open-structured-destination').style.display === ''
      && elements.get('btn-open-structured-destination').textContent === 'Open Characters',
    {
      refreshes: harness.refreshes,
      tabs: harness.activatedTabs,
      status: elements.get('external-structured-result-status').textContent,
      openText: elements.get('btn-open-structured-destination').textContent,
    }
  );
  requireCheck(
    checks,
    'import_does_not_approve_or_assign',
    harness.fetchCalls.every(call => !/approve|assign/.test(call.url))
      && harness.apiCalls.every(call => !/approve|assign/.test(call.url)),
    { fetchCalls: harness.fetchCalls, apiCalls: harness.apiCalls }
  );

  elements.get('completed-task-file').files = [{ name: 'fallback.json' }];
  elements.get('original-task-file').files = [];
  harness.fetchQueue.push(response({
    detail: {
      code: 'original_task_required',
      message: 'Choose the original Alexandria task ZIP.',
    },
  }, { ok: false, status: 409, statusText: 'Conflict' }));
  await context.importCompletedTask();
  requireCheck(
    checks,
    'json_fallback_requests_original_zip_not_code',
    elements.get('original-task-file-wrap').hidden === false
      && elements.get('task-bundle-import-note').textContent.includes('original Alexandria task ZIP')
      && elements.get('external-workflow-status').textContent === 'Original task ZIP required',
    {
      originalHidden: elements.get('original-task-file-wrap').hidden,
      note: elements.get('task-bundle-import-note').textContent,
      status: elements.get('external-workflow-status').textContent,
    }
  );

  context.renderExternalStructuredResult({
    kind: 'structured_result',
    task_type: 'roster_discovery',
    task_label: 'Discover character roster',
    target: null,
    status: 'inspected',
    review: { root_type: 'object', item_count: 12 },
    result: { entities: [] },
    routing: {
      status: 'awaiting_reconciliation',
      native_destination: 'character_roster',
      tab: 'characters',
      message: 'An approved roster exists. Reconcile this saved result against it.',
    },
  });
  requireCheck(
    checks,
    'blocked_result_retains_clear_review_action',
    elements.get('external-structured-result-status').textContent === 'Reconciliation required'
      && elements.get('external-structured-result-note').textContent.includes('approved roster exists')
      && elements.get('btn-open-structured-destination').style.display === ''
      && elements.get('btn-open-structured-destination').dataset.tab === 'characters',
    {
      status: elements.get('external-structured-result-status').textContent,
      note: elements.get('external-structured-result-note').textContent,
      tab: elements.get('btn-open-structured-destination').dataset.tab,
    }
  );

  context.renderExternalStructuredResult({
    candidate_id: 'structured_persona_catalog_1234',
    kind: 'structured_result',
    task_type: 'persona_catalog_generation',
    task_label: 'Create voice profiles for all speakers',
    result_fingerprint: 'f'.repeat(64),
    status: 'inspected',
    review: { root_type: 'object', item_count: 2 },
    result: {
      personas: [
        { speaker: 'NARRATOR', description: 'Imported narrator.', ref_text: 'The room was quiet.' },
        { speaker: 'THE DOCTOR', description: 'Imported Doctor.', ref_text: 'Run,' },
      ],
      warnings: [],
    },
    routing: {
      status: 'awaiting_reconciliation',
      native_destination: 'expressive_voices',
      tab: 'voice-projects',
      code: 'persona_catalog_comparison_required',
      message: 'Compare current and imported voice profiles.',
      details: {
        new_speakers: ['NARRATOR'],
        conflicts: [
          {
            speaker: 'THE DOCTOR',
            current: {
              description: 'Current Doctor.',
              ref_text: 'Run,',
              approval_status: 'approved',
            },
            imported: {
              description: 'Imported Doctor.',
              ref_text: 'Run,',
            },
          },
        ],
      },
    },
  });
  requireCheck(
    checks,
    'persona_catalog_shows_current_imported_comparison',
    elements.get('persona-catalog-conflicts').hidden === false
      && elements.get('persona-catalog-new-count').textContent.includes('1 new identity draft')
      && elements.get('persona-catalog-conflict-list').innerHTML.includes('Current · approved')
      && elements.get('persona-catalog-conflict-list').innerHTML.includes('Imported Doctor.')
      && elements.get('persona-catalog-conflict-list').innerHTML.includes('Replace this identity draft'),
    {
      hidden: elements.get('persona-catalog-conflicts').hidden,
      count: elements.get('persona-catalog-new-count').textContent,
      html: elements.get('persona-catalog-conflict-list').innerHTML,
    }
  );
  harness.checkedPersonaReplacements.push({ dataset: { speaker: 'THE DOCTOR' } });
  context.API.postQueue.push({
    candidate_id: 'structured_persona_catalog_1234',
    task_type: 'persona_catalog_generation',
    result_fingerprint: 'f'.repeat(64),
    status: 'transferred',
    result: { personas: [], warnings: [] },
    review: { root_type: 'object', item_count: 2 },
    application: {
      destination: 'expressive_voices',
      tab: 'voice-projects',
      created_count: 1,
      replaced_count: 1,
      kept_count: 0,
    },
  });
  await context.applyPersonaCatalogSelection();
  const catalogApplyCall = harness.apiCalls.find(call =>
    call.url.includes('/api/external/structured-result/structured_persona_catalog_1234/transfer')
  );
  requireCheck(
    checks,
    'persona_catalog_applies_only_selected_replacements',
    catalogApplyCall
      && catalogApplyCall.body.persona_catalog_decision === true
      && JSON.stringify(catalogApplyCall.body.replace_persona_speakers) === JSON.stringify(['THE DOCTOR'])
      && harness.refreshes.includes('voice-projects')
      && elements.get('external-structured-result-note').textContent.includes('1 new advanced identity drafts added')
      && elements.get('external-structured-result-note').textContent.includes('1 replaced'),
    {
      call: catalogApplyCall,
      refreshes: harness.refreshes,
      note: elements.get('external-structured-result-note').textContent,
    }
  );

  elements.get('completed-task-file').files = [{ name: 'script-complete.json' }];
  elements.get('original-task-file').files = [];
  harness.fetchQueue.push(response({
    kind: 'annotated_script',
    status: 'inspected',
    routing: {
      status: 'review_ready',
      native_destination: 'script_review',
      tab: 'script',
      message: 'Script review ready.',
    },
  }));
  await context.importCompletedTask();
  requireCheck(
    checks,
    'script_result_uses_existing_candidate_review',
    harness.scriptCandidates.length === 1
      && harness.activatedTabs.includes('script'),
    { candidates: harness.scriptCandidates.length, tabs: harness.activatedTabs }
  );

  process.stdout.write(`${REPORT_PREFIX}${JSON.stringify({
    checks,
    extractedFunctions: harness.extractedFunctions,
  })}\n`);
}

main().catch(error => {
  console.error(error.stack || String(error));
  process.exitCode = 1;
});
