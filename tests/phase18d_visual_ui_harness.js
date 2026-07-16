'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const REPORT_PREFIX = 'PHASE18D_VISUAL_UI_REPORT=';

function requireCheck(checks, name, condition, details = {}) {
  checks[name] = { ok: Boolean(condition), ...details };
  if (!condition) {
    throw new Error(
      `Phase 18D visual UI check failed: ${name}: ${JSON.stringify(details)}`
    );
  }
}

class MockClassList {
  constructor(owner) {
    this.owner = owner;
    this.values = new Set();
  }
  add(...values) { values.forEach((value) => this.values.add(value)); }
  remove(...values) { values.forEach((value) => this.values.delete(value)); }
  contains(value) { return this.values.has(value); }
  toggle(value, force) {
    const next = force === undefined ? !this.values.has(value) : Boolean(force);
    if (next) this.values.add(value);
    else this.values.delete(value);
    this.owner.className = [...this.values].join(' ');
    return next;
  }
}

class MockElement {
  constructor(id = '') {
    this.id = id;
    this.style = { display: '' };
    this.className = '';
    this.classList = new MockClassList(this);
    this.disabled = false;
    this.checked = false;
    this.value = '';
    this.dataset = {};
    this.listeners = {};
    this.attributes = {};
    this.hidden = false;
    this._innerHTML = '';
    this._textContent = '';
    this.focused = false;
    this.stateText = null;
    this.heading = null;
  }
  set innerHTML(value) {
    this._innerHTML = String(value ?? '');
    if (this.id === 'character-visual-detail' && /<h6\b/.test(this._innerHTML)) {
      this.heading = new MockElement('visual-detail-heading');
    }
  }
  get innerHTML() { return this._innerHTML; }
  set textContent(value) { this._textContent = String(value ?? ''); }
  get textContent() { return this._textContent; }
  addEventListener(type, handler) { this.listeners[type] = handler; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  focus() { this.focused = true; }
  querySelector(selector) {
    if (selector === 'span:last-child') {
      if (!this.stateText) this.stateText = new MockElement(`${this.id}-text`);
      return this.stateText;
    }
    if (selector === 'h6') return this.heading;
    return null;
  }
  matches(selector) {
    return selector === '.character-visual-entry'
      && this.className.includes('character-visual-entry');
  }
  closest(selector) {
    if (
      selector === '[data-character-visual-view]'
      && this.dataset.characterVisualView
    ) return this;
    if (
      selector === '[data-visual-panel-target]'
      && this.dataset.visualPanelTarget
    ) return this;
    return null;
  }
}

function createEnvironment() {
  const elements = new Map();
  const checkboxes = [];
  const rows = [];
  const detailButtons = [];
  const detailPanels = [];
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, new MockElement(id));
    return elements.get(id);
  };
  const workspaceState = getElement('character-visual-status-badge');
  workspaceState.stateText = new MockElement('character-visual-status-text');
  const detail = getElement('character-visual-detail');

  const apiGets = [];
  const getCalls = [];
  const postCalls = [];
  const toastCalls = [];
  const intervals = new Map();
  let nextInterval = 1;

  const document = {
    getElementById: getElement,
    querySelectorAll: (selector) => {
      if (selector === '.character-visual-entry') return checkboxes;
      if (selector === '.character-visual-entry:checked') {
        return checkboxes.filter((item) => item.checked);
      }
      if (selector === '.visual-character-row') return rows;
      if (selector === '.visual-character-row:not(.is-hidden) .character-visual-entry') {
        return rows
          .filter((row) => !row.classList.contains('is-hidden'))
          .map((row) => row.checkbox)
          .filter(Boolean);
      }
      if (selector === '#character-visual-detail [data-visual-panel-target]') {
        return detailButtons;
      }
      if (selector === '#character-visual-detail [data-visual-panel]') {
        return detailPanels;
      }
      return [];
    },
  };

  const sandbox = {
    console,
    document,
    window: { prompt: () => '' },
    API: {
      get: async (url) => {
        getCalls.push(url);
        if (!apiGets.length) throw new Error(`Unexpected GET ${url}`);
        const item = apiGets.shift();
        if (item instanceof Error) throw item;
        return item;
      },
      post: async (url, payload) => {
        postCalls.push({ url, payload });
        throw new Error(`Unexpected POST ${url}`);
      },
    },
    showToast: (message, type) => toastCalls.push({ message, type }),
    showConfirm: async () => true,
    escapeHtml: (value) => String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;'),
    encodeURIComponent,
    setInterval: (handler, delay) => {
      const id = nextInterval++;
      intervals.set(id, { handler, delay });
      return id;
    },
    clearInterval: (id) => intervals.delete(id),
    Object,
    Array,
    Map,
    Set,
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
    checkboxes,
    rows,
    detailButtons,
    detailPanels,
    detail,
    getElement,
    apiGets,
    getCalls,
    postCalls,
    toastCalls,
    intervals,
  };
}

function statusFixture({
  approved = true,
  running = false,
  progress = null,
  entryStatus = 'absent',
} = {}) {
  return {
    enabled_by_default: false,
    approved_roster_available: approved,
    context_error: approved ? null : 'Approve a roster first.',
    source_fingerprint: approved ? 'source-fingerprint' : null,
    roster_fingerprint: approved ? 'roster-fingerprint' : null,
    process: {
      running,
      logs: running ? ['Validated passage 2 of 4.'] : [],
      cancel: false,
    },
    progress: progress || {
      exists: false,
      status: 'none',
      completed_passages: 0,
      total_passages: 0,
      next_passage: null,
      character_ids: [],
      error: null,
    },
    complete_count: entryStatus === 'complete' ? 1 : 0,
    absent_count: entryStatus === 'absent' ? 1 : 0,
    invalid_count: entryStatus === 'invalid' ? 1 : 0,
    entries: approved ? [
      {
        entry_id: 'character_11111111111111111111',
        canonical_name: 'THE KHEPRI',
        display_name: '<img src=x onerror=alert(1)>',
        entity_kind: 'character',
        status: entryStatus,
        observation_count: entryStatus === 'complete' ? 2 : 0,
        variant_count: entryStatus === 'complete' ? 1 : 0,
        conflict_count: 0,
        image_prompt_summary: entryStatus === 'complete'
          ? '<script>alert(1)</script>'
          : null,
        error: null,
      },
    ] : [],
  };
}

function visualPayload() {
  const observationOne = 'visual_111111111111111111111111';
  const observationTwo = 'visual_222222222222222222222222';
  const emptyProfile = {
    apparent_age: [],
    species_or_ancestry: [],
    skin_and_complexion: [],
    face_and_features: [],
    eyes: [],
    hair: [],
    height_and_build: [],
    body_features: [],
    distinguishing_marks: [],
    cybernetics_or_modifications: [],
    posture_and_movement: [],
    clothing: [],
    accessories_weapons_equipment: [],
    nonhuman_anatomy: [],
  };
  emptyProfile.nonhuman_anatomy = [
    {
      detail: '<b>four wings</b>',
      certainty: 0.96,
      observation_ids: [observationOne],
    },
  ];
  return {
    entry_id: 'character_11111111111111111111',
    canonical_name: 'THE KHEPRI',
    display_name: '<img src=x onerror=alert(1)>',
    visual: {
      schema_version: 1,
      image_prompt_summary: '<script>alert(1)</script>',
      observations: [
        {
          character_id: 'character_11111111111111111111',
          observation_id: observationOne,
          category: 'nonhuman_anatomy',
          detail: '<b>four wings</b>',
          scope: 'stable',
          certainty: 0.96,
          basis: 'explicit',
          quote: '<svg onload=alert(1)>',
          source_location: 'characters 1-22',
          start_char: 1,
          end_char: 22,
          passage_index: 1,
        },
        {
          character_id: 'character_11111111111111111111',
          observation_id: observationTwo,
          category: 'clothing',
          detail: 'mud-covered clothing',
          scope: 'scene_specific',
          certainty: 0.82,
          basis: 'explicit',
          quote: 'mud-covered',
          source_location: 'characters 30-41',
          start_char: 30,
          end_char: 41,
          passage_index: 2,
        },
      ],
      profile: emptyProfile,
      variants: [
        {
          label: '<img src=x>',
          scope: 'scene_specific',
          details: ['mud-covered clothing'],
          observation_ids: [observationTwo],
        },
      ],
      conflicts: [],
      unknowns: [
        {
          category: 'eyes',
          question: '<script>unknown</script>',
        },
      ],
    },
  };
}

async function main() {
  const repoRoot = path.resolve(process.argv[2] || '.');
  const html = fs.readFileSync(
    path.join(repoRoot, 'app', 'static', 'index.html'),
    'utf8'
  );
  const startMarker = '        let characterVisualStatusTimer = null;';
  const endMarker = '        refreshScriptGenerationStatus();\n        refreshCharacterRosterStatus();\n        refreshCharacterVisualStatus();';
  const start = html.indexOf(startMarker);
  const end = html.indexOf(endMarker, start);
  if (start < 0 || end < 0) throw new Error('Visual production block not found');
  const block = html.slice(start, end);
  const exports = `
    globalThis.__visual = {
      selectedCharacterVisualEntryIds,
      updateCharacterVisualActionState,
      updateCharacterVisualSelectionCount,
      filterCharacterVisualList,
      characterVisualStatusPresentation,
      characterVisualEntryHtml,
      renderCharacterVisualStatus,
      characterVisualEvidenceHtml,
      renderCharacterVisualDetail,
      renderCharacterVisualPlaceholder,
      loadCharacterVisualDetail,
      refreshCharacterVisualStatus,
      getTimer: () => characterVisualStatusTimer,
      getStatus: () => characterVisualLastStatus,
    };
  `;

  const env = createEnvironment();
  vm.createContext(env.sandbox);
  vm.runInContext(block + exports, env.sandbox, {
    filename: 'phase18d-visual-production.js',
  });
  const ui = env.sandbox.__visual;
  const checks = {};

  requireCheck(
    checks,
    'feature_checkbox_is_unchecked_in_markup',
    html.includes('id="character-visual-enabled"')
      && !/id="character-visual-enabled"[^>]*checked/.test(html),
    {}
  );

  const unavailable = statusFixture({ approved: false });
  ui.renderCharacterVisualStatus(unavailable);
  requireCheck(
    checks,
    'panel_is_approved_roster_only',
    env.getElement('character-visual-panel').style.display === 'none',
    { display: env.getElement('character-visual-panel').style.display }
  );

  const idle = statusFixture();
  ui.renderCharacterVisualStatus(idle);
  const idleHtml = env.getElement('character-visual-list').innerHTML;
  requireCheck(
    checks,
    'idle_visual_collection_is_disabled_and_safe',
    env.getElement('character-visual-panel').style.display === ''
      && env.getElement('character-visual-status-badge').stateText.textContent === 'Not started'
      && env.getElement('btn-discover-character-visuals').disabled === true
      && idleHtml.includes('&lt;img')
      && !idleHtml.includes('<img src=x'),
    {
      state: env.getElement('character-visual-status-badge').stateText.textContent,
      html: idleHtml.slice(0, 300),
    }
  );
  requireCheck(
    checks,
    'master_list_avoids_raw_id_and_counter_presentation',
    !idleHtml.includes('<code')
      && !idleHtml.includes('Observations:')
      && !idleHtml.includes('Variants:')
      && !idleHtml.includes('Conflicts:'),
    { html: idleHtml.slice(0, 500) }
  );

  const checkbox = new MockElement('visual-checkbox');
  checkbox.className = 'character-visual-entry';
  checkbox.classList.add('character-visual-entry');
  checkbox.value = 'character_11111111111111111111';
  checkbox.checked = true;
  env.checkboxes.push(checkbox);
  env.getElement('character-visual-enabled').checked = true;
  ui.updateCharacterVisualActionState();
  requireCheck(
    checks,
    'explicit_enable_and_selection_unlock_action',
    env.getElement('btn-discover-character-visuals').disabled === false
      && env.getElement('character-visual-selection-count').textContent === '1 selected',
    {
      disabled: env.getElement('btn-discover-character-visuals').disabled,
      count: env.getElement('character-visual-selection-count').textContent,
    }
  );

  const running = statusFixture({
    running: true,
    progress: {
      exists: true,
      status: 'resumable',
      completed_passages: 2,
      total_passages: 4,
      next_passage: 3,
      character_ids: ['character_11111111111111111111'],
      error: null,
    },
  });
  ui.renderCharacterVisualStatus(running);
  requireCheck(
    checks,
    'running_state_guards_actions_and_shows_progress',
    env.getElement('character-visual-status-badge').stateText.textContent === 'Collecting'
      && env.getElement('character-visual-enabled').disabled === true
      && env.getElement('btn-cancel-character-visuals').style.display === ''
      && env.getElement('btn-discard-character-visual-progress').style.display === 'none'
      && env.getElement('character-visual-progress').textContent.includes('2 of 4'),
    { progress: env.getElement('character-visual-progress').textContent }
  );

  ui.renderCharacterVisualStatus(statusFixture({ entryStatus: 'complete' }));
  const completeHtml = env.getElement('character-visual-list').innerHTML;
  requireCheck(
    checks,
    'complete_status_escapes_derived_summary',
    completeHtml.includes('&lt;script&gt;')
      && !completeHtml.includes('<script>alert'),
    { html: completeHtml.slice(0, 400) }
  );

  ui.renderCharacterVisualDetail(visualPayload());
  const detailHtml = env.getElement('character-visual-detail').innerHTML;
  requireCheck(
    checks,
    'dossier_detail_escapes_all_source_and_derived_text',
    detailHtml.includes('&lt;svg')
      && detailHtml.includes('&lt;b&gt;')
      && detailHtml.includes('&lt;script&gt;unknown')
      && !detailHtml.includes('<svg onload')
      && !detailHtml.includes('<script>unknown'),
    { html: detailHtml.slice(0, 700) }
  );
  requireCheck(
    checks,
    'dossier_uses_progressive_disclosure',
    detailHtml.includes('data-visual-panel-target="overview"')
      && detailHtml.includes('data-visual-panel-target="evidence"')
      && detailHtml.includes('<summary>Technical details</summary>')
      && detailHtml.indexOf('<summary>Technical details</summary>')
        < detailHtml.indexOf('character_11111111111111111111'),
    { html: detailHtml.slice(-700) }
  );

  const rowOne = new MockElement('row-one');
  rowOne.classList.add('visual-character-row');
  rowOne.dataset.characterVisualSearch = 'the doctor';
  rowOne.checkbox = checkbox;
  const rowTwo = new MockElement('row-two');
  rowTwo.classList.add('visual-character-row');
  rowTwo.dataset.characterVisualSearch = 'roz forrester';
  rowTwo.checkbox = new MockElement('roz-checkbox');
  rowTwo.checkbox.className = 'character-visual-entry';
  env.rows.push(rowOne, rowTwo);
  env.getElement('character-visual-search').value = 'roz';
  ui.filterCharacterVisualList();
  requireCheck(
    checks,
    'master_list_search_filters_without_destroying_rows',
    rowOne.classList.contains('is-hidden')
      && !rowTwo.classList.contains('is-hidden')
      && env.getElement('character-visual-empty').style.display === 'none',
    {
      doctorHidden: rowOne.classList.contains('is-hidden'),
      rozHidden: rowTwo.classList.contains('is-hidden'),
    }
  );

  env.apiGets.push(statusFixture({ running: true }));
  await ui.refreshCharacterVisualStatus();
  const timerStarted = ui.getTimer() !== null && env.intervals.size === 1;
  env.apiGets.push(statusFixture());
  await ui.refreshCharacterVisualStatus();
  requireCheck(
    checks,
    'polling_starts_and_stops_without_posting',
    timerStarted
      && ui.getTimer() === null
      && env.intervals.size === 0
      && env.postCalls.length === 0,
    {
      timerStarted,
      timers: env.intervals.size,
      posts: env.postCalls,
      gets: env.getCalls,
    }
  );

  const failure = new Error('<img src=x onerror=alert(1)>');
  env.apiGets.push(failure);
  await ui.refreshCharacterVisualStatus();
  requireCheck(
    checks,
    'status_errors_use_safe_text_and_clear_timer',
    env.getElement('character-visual-error').textContent === failure.message
      && env.getElement('character-visual-error').innerHTML === ''
      && ui.getTimer() === null,
    {
      text: env.getElement('character-visual-error').textContent,
      html: env.getElement('character-visual-error').innerHTML,
    }
  );

  console.log(REPORT_PREFIX + JSON.stringify({ checks }));
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
