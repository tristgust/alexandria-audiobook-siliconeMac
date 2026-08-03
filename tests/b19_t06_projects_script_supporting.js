'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { BrowserSession, writeJson } = require('./b19_t06_bootstrap_red.js');

const ROOT = path.resolve(__dirname, '..');
const STATIC = path.join(ROOT, 'app', 'static');
const PAGE_NAMES = ['projects', 'new_project', 'script', 'library', 'voices', 'templates'];
const VIEWPORTS = [[390, 844], [768, 1024], [1024, 768], [1536, 1024]];
const scriptPageSize = (width) => width < 640 ? 30 : width < 1200 ? 60 : 80;
const FORBIDDEN = [
  'innerHTML', 'insertAdjacentHTML', 'getElementById', 'canonical_interface',
  'canonical_pages', 'activateWorkspaceTab', 'VoiceCardBridge', 'data-tab-panel',
];

function sourceContract() {
  const observations = [];
  for (const name of PAGE_NAMES) {
    const filename = path.join(STATIC, 'pages', `${name}.js`);
    assert.ok(fs.existsSync(filename), `${name}.js must exist`);
    const source = fs.readFileSync(filename, 'utf8');
    execFileSync('node', ['--check', filename], { cwd: ROOT });
    assert.match(source, name === 'new_project'
      ? /export function createNewProjectController/
      : /export async function mount/);
    assert.match(source, /textContent|createTextNode|\btext\(/);
    assert.match(source, /AbortSignal|signal/);
    for (const marker of FORBIDDEN) assert.doesNotMatch(source, new RegExp(marker), `${name}: ${marker}`);
    observations.push({ name, lines: source.split('\n').length });
  }
  const helperPaths = [
    path.join(STATIC, 'pages', 'script_workflows.js'),
    path.join(STATIC, 'pages', 'script_contextual_review.js'),
    path.join(STATIC, 'pages', 'script_approval_controller.js'),
    path.join(STATIC, 'pages', 'script_delivery_plan.js'),
    path.join(STATIC, 'pages', 'script_import_workflows.js'),
    path.join(STATIC, 'pages', 'script_inline_approval.js'),
    path.join(STATIC, 'pages', 'script_pronunciation_guidance.js'),
    path.join(STATIC, 'pages', 'script_workflow_dialog.js'),
    path.join(STATIC, 'pages', 'script_workflow_provenance.js'),
    path.join(STATIC, 'pages', 'script_workflow_state.js'),
    path.join(STATIC, 'pages', 'task_bundle_download.js'),
  ];
  for (const helper of helperPaths) {
    assert.ok(fs.existsSync(helper), helper);
    execFileSync('node', ['--check', helper], { cwd: ROOT });
    const pureLines = fs.readFileSync(helper, 'utf8').split('\n').filter(
      (line) => line.trim() && !line.trimStart().startsWith('//'),
    );
    assert.ok(pureLines.length <= 250, `${helper} exceeds 250 owned lines`);
  }
  const combined = [
    ...PAGE_NAMES.map((name) => fs.readFileSync(path.join(STATIC, 'pages', `${name}.js`), 'utf8')),
    ...helperPaths.map((helper) => fs.readFileSync(helper, 'utf8')),
    fs.readFileSync(path.join(STATIC, 'components', 'task_import_surface.js'), 'utf8'),
  ].join('\n');
  for (const endpoint of [
    '/api/projects', '/api/projects/inspect-source', '/api/project_flow/status',
    '/api/script_lifecycle/status', '/api/annotated_script',
    '/api/review_script_contextual/estimate', '/api/status/review',
    '/api/script_lifecycle/accept', '/api/tasks/import',
    '/api/backend_render_plan/status', '/api/backend_render_plan/generate',
    '/api/pronunciation-registry', '/api/pronunciation-registry/preview',
    '/api/library', '/api/voice-library', '/api/templates',
  ]) assert.ok(combined.includes(endpoint), endpoint);
  assert.ok(fs.existsSync(path.join(STATIC, 'styles', 'pages', 'project_flow.css')));
  return { status: 'PASS', observations };
}

function fixtureData() {
  const project = {
    id: 'project_meridian', name: 'The Meridian Archive', source_title: 'The Meridian Archive',
    source_author: 'Ada Marlowe', source_filename: 'meridian.epub', current: true,
    selected: true, archive_state: 'active', availability_state: 'available',
    compatibility_state: 'current', completion_state: 'requires_work', blocker_count: 0,
    current_recommended_stage: 'script', latest_meaningful_activity: '2026-07-22T18:00:00Z',
    stage_summary: 'Review and approve the Script.',
    stage_states: { script: 'needs_review', cast: 'not_started', produce: 'not_started', export: 'not_started' },
  };
  return {
    project,
    catalog: {
      schema_version: 1,
      catalog_fingerprint: 'catalog-1',
      current_project_id: project.id,
      last_selected_project_id: project.id,
      projects: [project],
    },
    flow: {
      schema_version: 1, project: { id: project.id, name: project.name },
      source: { title: project.source_title, filename: project.source_filename, available: true },
      stage_map: {
        script: { state: 'needs_review', summary: 'Review and approve the Script.' },
        cast: { state: 'future' }, produce: { state: 'future' }, export: { state: 'future' },
      },
    },
    lifecycle: {
      state: 'review_required', accepted: false, source_available: true,
      artifact: { script_exists: true, metadata_exists: true },
      fingerprints: { script: 'script-1', metadata: 'metadata-1', source: 'source-1' },
      state_fingerprint: 'lifecycle-1', generation_method: 'local',
      blockers: [],
    },
    scriptIssues: [
      { code: 'speaker_attribution_low', title: 'Speaker attribution is uncertain', entry_index: 0,
        explanation: 'The source does not clearly identify the speaker.', source_text: '“We catalogue what the sea returns.”' },
      { code: 'speaker_attribution_low', title: 'Speaker attribution is uncertain', entry_index: 1,
        explanation: 'Two nearby speakers are plausible.', source_text: 'A second voice answered from the stacks.' },
      { code: 'speaker_attribution_low', title: 'Speaker attribution is uncertain', entry_index: 2,
        explanation: 'The dialogue tag is ambiguous.', source_text: '“Then we begin,” came the reply.' },
      { code: 'delivery_direction_review', title: 'Delivery direction requires review', entry_index: 3,
        explanation: 'The direction may overstate the source.', source_text: 'He spoke without emphasis.' },
      { code: 'delivery_direction_review', title: 'Delivery direction requires review', entry_index: 4,
        explanation: 'The pause instruction may alter meaning.', source_text: 'She continued at once.' },
      { code: 'source_fidelity_mismatch', title: 'Script text does not match the source', entry_index: 5,
        explanation: 'The Script wording differs from the selected source.', source_text: 'The archive opened before dusk.' },
    ],
    entries: Array.from({ length: 5275 }, (_, index) => ({
      speaker: index % 5 === 0 ? 'MARA' : 'NARRATOR',
      text: index === 0
        ? '<img src=x onerror="globalThis.fixtureInjection=true"> The archive opened at dusk.'
        : `Script entry ${index + 1}. The archive preserves a bounded production fixture.`,
      instruct: index % 5 === 0 ? 'Quiet certainty.' : 'Measured narration.',
    })),
    library: {
      inventory_fingerprint: 'inventory-1',
      filters: { available_kinds: ['source_book', 'production_audio'], available_states: ['available', 'current'] },
      artifacts: [
        { artifact_id: 'source-1', kind: 'source_book', name: 'The Meridian Archive', state: 'available',
          native_route: { destination: 'script', context: { project: project.id } }, provenance: { format: 'EPUB' } },
        { artifact_id: 'audio-1', kind: 'production_audio', name: 'Chapter 1 production', state: 'current',
          native_route: { destination: 'produce', context: { project: project.id } }, provenance: { format: 'WAV' } },
      ],
    },
    voices: {
      assignment_mutation_supported: false, cast_is_authoritative: true,
      voices: [
        { id: 'voice-1', name: 'Benny', method: 'built_in', method_label: 'Built-in Voice',
          description: 'Warm, articulate narration.', state: 'available', preview: { available: false }, usage: [] },
        { id: 'voice-2', name: 'Meridian reference', method: 'supplied_recording',
          method_label: 'Supplied recording', description: 'Identity-preserving reference.',
          state: 'current', preview: { available: false }, usage: [{ character_id: 'character_mara', name: 'Mara' }] },
      ],
    },
    templates: {
      catalog_fingerprint: 'templates-1', default_template_id: 'builtin_standard',
      summary: { template_count: 2 },
      templates: [
        { id: 'builtin_standard', name: 'Standard production', description: 'Balanced local production.',
          intent: 'Reliable local audiobook', generation_method: 'local', preset: 'standard',
          source_language: 'English', output_language: 'English', built_in: true, default: true, fingerprint: 't1' },
        { id: 'custom_fidelity', name: 'Maximum fidelity', description: 'More deliberate review.',
          intent: 'Detailed publication review', generation_method: 'local', preset: 'maximum_fidelity',
          source_language: 'English', output_language: 'English', built_in: false, default: false, fingerprint: 't2' },
      ],
    },
  };
}

async function fixtureServer() {
  const data = fixtureData();
  const control = {
    scriptIssues: false,
    reviewStarted: false,
    acceptanceDelayMs: 220,
  };
  const requests = [];
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://fixture.invalid');
    requests.push(`${request.method} ${url.pathname}`);
    const send = (status, body, type = 'application/json; charset=utf-8') => {
      response.writeHead(status, { 'content-type': type, 'cache-control': 'no-store' });
      response.end(request.method === 'HEAD' ? '' : body);
    };
    const json = (value) => send(200, JSON.stringify(value));
    if (url.pathname === '/') {
      const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
      return send(200, html, 'text/html; charset=utf-8');
    }
    if (url.pathname === '/api/projects' && request.method === 'GET') return json(data.catalog);
    if (url.pathname === '/api/projects/inspect-source') return json({
      valid: true, filename: 'fixture.epub', title: 'Fixture Book', author: 'Alex Writer',
      source_type: 'epub', language: 'English', chapter_count: 3,
      generation_method: 'local', size_bytes: 1200,
      cover_data_url: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlL4k0AAAAASUVORK5CYII=',
    });
    if (url.pathname === '/api/projects' && request.method === 'POST') return json({
      project: data.project, catalog_fingerprint: 'catalog-2',
      activation: { state: 'current', native_destination: 'script' },
    });
    if (/^\/api\/projects\/[^/]+\/open$/.test(url.pathname)) return json({
      catalog_fingerprint: 'catalog-2', activation: { state: 'current', native_destination: 'script' },
    });
    if (/^\/api\/projects\/[^/]+\/duplicate$/.test(url.pathname)) return json({
      project: { ...data.project, id: 'project_meridian_copy', name: 'The Meridian Archive copy' },
      catalog_fingerprint: 'catalog-2',
    });
    if (url.pathname === '/__fixture/script-issues') {
      control.scriptIssues = url.searchParams.get('enabled') === '1';
      return json({ enabled: control.scriptIssues });
    }
    if (url.pathname === '/__fixture/review-reset') {
      control.reviewStarted = false;
      return json({ reviewStarted: false });
    }
    if (url.pathname === '/api/project_flow/status') return json(data.flow);
    if (url.pathname === '/api/script_lifecycle/status') {
      data.lifecycle.accepted = false;
      data.lifecycle.state = 'review_required';
      data.lifecycle.blockers = control.scriptIssues ? data.scriptIssues : [];
      return json(data.lifecycle);
    }
    if (url.pathname === '/api/annotated_script') return json(data.entries);
    if (url.pathname === '/api/script_lifecycle/import-candidate') return json({ status: 'none' });
    if (url.pathname === '/api/script_generation/status') return json({
      process: { running: false }, progress: { status: 'missing' },
    });
    if (url.pathname === '/api/review_script_contextual/estimate') return json({
      total_entries: data.entries.length, batch_size: 25, estimated_calls: 211,
    });
    if (url.pathname === '/api/status/review') return json(control.reviewStarted ? {
      running: false,
      mode: 'contextual',
      started_at: '2026-07-30T10:00:00Z',
      finished_at: '2026-07-30T10:00:01Z',
      return_code: 1,
      last_error: 'Fixture review failed visibly.',
      logs: ['Fixture review failed visibly.'],
    } : {
      running: false,
      started_at: null,
      finished_at: null,
      return_code: null,
      logs: [],
    });
    if (url.pathname === '/api/review_script_contextual' && request.method === 'POST') {
      control.reviewStarted = true;
      return json({
        status: 'started', mode: 'contextual', window_size: 4,
        total_entries: data.entries.length, batch_size: 25, estimated_calls: 211,
      });
    }
    if (url.pathname === '/api/backend_render_plan/status') return json({
      schema_version: 1,
      state: 'missing',
      available: data.lifecycle.accepted,
      current: false,
      chunk_count: 0,
      fish_inline_cue_count: 0,
      applied_to_audio_count: 0,
      process: { running: false, logs: [] },
    });
    if (url.pathname === '/api/pronunciation-registry') return json({
      schema_version: 1,
      registry_fingerprint: 'f'.repeat(64),
      entries: [],
      summary: { entry_count: 0, approved_count: 0, stale_anchor_count: 0 },
    });
    if (url.pathname === '/api/background-work' && request.method === 'GET') return json({
      schema_version: 1,
      max_pending: 32,
      active_count: 1,
      counts: { queued: 1, running: 0, cancelling: 0, succeeded: 1, failed: 0, cancelled: 0, stale: 0 },
      active: [{
        job_id: 'work_fixture_active', domain: 'audio_generation', operation: 'missing_stale',
        state: 'queued', priority: 50, sequence: 1, resources: ['model_runtime', 'project_audio'],
        dependency_fingerprint: 'a'.repeat(64), external_ref: { request_id: 'audio_fixture' },
        metadata: { label: 'Generate audiobook audio' }, resumable: true, attempt_count: 0,
        recovery_count: 0, cancel_requested: false,
        progress: { completed: 0, total: 3, message: 'Queued' },
        terminal_reason: null, terminal_receipt_fingerprint: null,
        created_at: '2026-08-02T22:00:00Z', queued_at: '2026-08-02T22:00:00Z',
        started_at: null, finished_at: null, updated_at: '2026-08-02T22:00:00Z',
      }],
      history: [{
        job_id: 'work_fixture_complete', domain: 'delivery_plan', operation: 'generate_backend_render_plan',
        state: 'succeeded', priority: 100, sequence: 0, resources: ['model_runtime', 'project_plan'],
        dependency_fingerprint: 'b'.repeat(64), external_ref: null,
        metadata: { label: 'Create delivery plan' }, resumable: true, attempt_count: 1,
        recovery_count: 0, cancel_requested: false,
        progress: { completed: 1, total: 1, message: 'Complete' },
        terminal_reason: 'completed', terminal_receipt_fingerprint: 'c'.repeat(64),
        created_at: '2026-08-02T21:00:00Z', queued_at: '2026-08-02T21:00:00Z',
        started_at: '2026-08-02T21:00:01Z', finished_at: '2026-08-02T21:00:02Z',
        updated_at: '2026-08-02T21:00:02Z',
      }],
      updated_at: '2026-08-02T22:00:00Z',
    });
    if (/^\/api\/background-work\/[^/]+\/cancel$/.test(url.pathname) && request.method === 'POST') return json({
      status: 'cancelled',
      job: { job_id: 'work_fixture_active', state: 'cancelled' },
    });
    if (url.pathname === '/api/script_lifecycle/versions') return json({ versions: [] });
    if (url.pathname === '/api/tasks/export' && request.method === 'POST') return json({
      task_id: 'task_script_fixture',
      download_url: '/api/tasks/task_script_fixture/download',
    });
    if (url.pathname === '/api/tasks/task_script_fixture/download') {
      return send(200, Buffer.from('fixture task zip'), 'application/zip');
    }
    if (url.pathname === '/api/tasks/import' && request.method === 'POST') return json({
      kind: 'annotated_script', status: 'inspected', candidate_id: 'candidate_completed_task',
      summary: { entry_count: data.entries.length }, provenance: { status: 'verified' },
      consequences: { checkpoint_decision_required: false },
      routing: { status: 'review_ready', native_destination: 'script_review', tab: 'script' },
    });
    if (url.pathname === '/api/external/annotated-script/apply' && request.method === 'POST') {
      return json({ status: 'applied' });
    }
    if (url.pathname === '/api/script_lifecycle/accept') {
      setTimeout(() => {
        data.lifecycle.accepted = true;
        data.lifecycle.state = 'accepted';
        data.lifecycle.blockers = [];
        json(data.lifecycle);
      }, control.acceptanceDelayMs);
      return;
    }
    if (url.pathname === '/api/library') return json(data.library);
    if (url.pathname === '/api/voice-library') return json(data.voices);
    if (url.pathname === '/api/templates') return json(data.templates);
    if (url.pathname === '/static/pages/cast.js') {
      return send(200, "export async function mount({root,route}){const a=document.createElement('article');a.dataset.routeOwner='cast';const h=document.createElement('h1');h.dataset.pageHeading='';h.textContent='Cast';a.append(h);root.replaceChildren(a);return()=>{};}", 'text/javascript');
    }
    if (!url.pathname.startsWith('/static/')) return send(404, 'Not found', 'text/plain');
    const filename = path.resolve(STATIC, url.pathname.slice('/static/'.length));
    if (!filename.startsWith(`${STATIC}${path.sep}`) || !fs.existsSync(filename)) return send(404, 'Not found', 'text/plain');
    return send(200, fs.readFileSync(filename), filename.endsWith('.css') ? 'text/css' : 'text/javascript');
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return {
    url: `http://127.0.0.1:${server.address().port}/`, requests,
    close: () => new Promise((resolve) => { server.close(resolve); server.closeAllConnections?.(); }),
  };
}

async function captureState(session, page, artifacts) {
  await session.waitFor(`document.body.dataset.shellState === 'ready'`);
  const observed = await session.evaluate(`(() => {
    const owner = document.querySelector('[data-route-owner]');
    const marker = owner?.querySelector('[data-page-heading]');
    const heading = document.body.dataset.shellMode === 'global'
      ? document.querySelector('[data-global-header]:not([hidden]) [data-global-title]')
      : owner?.querySelector('h1,[data-page-heading]');
    return {
      owner: owner?.dataset.routeOwner || '',
      marker: Boolean(marker),
      heading: heading?.textContent || '',
      focused: document.activeElement === heading,
      overflow: document.documentElement.scrollWidth > innerWidth + 1,
    };
  })()`);
  assert.equal(observed.overflow, false, `${page} overflowed`);
  assert.ok(observed.marker, `${page} route heading marker`);
  assert.ok(observed.heading, `${page} visible heading`);
  await session.evaluate(`document.querySelector('[data-route-owner]')?.scrollIntoView({ block: 'start' })`);
  await session.screenshot(`${page}.png`);
  return { page, observed, screenshot: path.join(artifacts, `${page}.png`) };
}

async function browserContract(evidenceDir) {
  const fixture = await fixtureServer();
  const captures = [];
  const actions = [];
  try {
    for (const [width, height] of VIEWPORTS) {
      const viewport = `${width}x${height}`;
      const artifacts = path.join(evidenceDir, viewport);
      const session = await BrowserSession.open({ url: fixture.url, artifacts, width, height });
      try {
        await session.waitFor(`document.body.dataset.routePath === 'projects'`);
        captures.push({ viewport, ...await captureState(session, 'projects', artifacts) });
        const homeObserved = await session.evaluate(`(() => {
          const root = document.querySelector('[data-route-owner="projects"]');
          const global = document.querySelector('[data-global-header]');
          return {
            globalTitle: global?.querySelector('[data-global-title]')?.textContent || '',
            globalSubtitle: global?.querySelector('[data-global-subtitle]')?.textContent || '',
            searchInHeader: Boolean(global?.querySelector('.project-home__search')),
            primaryActions: document.querySelectorAll('.ui-button[data-variant="primary"]:not(:disabled)').length,
            projectGroupHidden: Boolean(document.querySelector('[data-nav-group="project"]')?.hidden),
            projectContextHidden: Boolean(document.querySelector('[data-nav-project-context]')?.hidden),
            continuation: Boolean(root?.querySelector('[data-project-continue]')),
            stageTrackers: root?.querySelectorAll('.stage-tracker').length || 0,
            rows: root?.querySelectorAll('.project-list__row').length || 0,
            rowPrimaryActions: root?.querySelectorAll('.project-list__row .ui-button[data-variant="primary"]').length || 0,
            titleTargets: root?.querySelectorAll('.project-list__title').length || 0,
            coverTargets: root?.querySelectorAll('.project-list__cover-action').length || 0,
            contextTargets: root?.querySelectorAll('.project-list__context-link').length || 0,
            rowActionTargets: root?.querySelectorAll('.project-list__row [data-project-open]').length || 0,
            overflowMenus: root?.querySelectorAll('.project-list__overflow').length || 0,
            playerAbsent: Boolean(document.querySelector('[data-persistent-player]')?.hidden),
            playerState: document.querySelector('[data-persistent-player]')?.dataset.state || '',
            playerHeight: Math.round(document.querySelector('[data-persistent-player]')?.getBoundingClientRect().height || 0),
          };
        })()`);
        assert.deepEqual(homeObserved, {
          globalTitle: 'Project Home',
          globalSubtitle: 'Open an existing project or create a new one.',
          searchInHeader: true,
          primaryActions: 1,
          projectGroupHidden: false,
          projectContextHidden: true,
          continuation: true,
          stageTrackers: 1,
          rows: 1,
          rowPrimaryActions: 0,
          titleTargets: 1,
          coverTargets: 1,
          contextTargets: 0,
          rowActionTargets: 1,
          overflowMenus: 1,
          playerAbsent: false,
          playerState: 'inactive',
          playerHeight: width < 640 ? 112 : 80,
        });
        actions.push({ viewport, action: 'Project Home matches global continuation anatomy', pass: true });
        await session.evaluate(`document.querySelector('.project-list__overflow > button').click()`);
        await session.waitFor(`Boolean([...document.querySelectorAll('[role="menuitem"]')].find((button) => button.textContent === 'Duplicate project'))`);
        await session.evaluate(`[...document.querySelectorAll('[role="menuitem"]')].find((button) => button.textContent === 'Duplicate project').click()`);
        await session.waitFor(`Boolean([...document.querySelectorAll('[role="dialog"] button')].find((button) => button.textContent === 'Duplicate'))`);
        await session.evaluate(`[...document.querySelectorAll('[role="dialog"] button')].find((button) => button.textContent === 'Duplicate').click()`);
        await session.waitFor(`!document.querySelector('[role="dialog"]')`);
        assert.ok(fixture.requests.some((request) => request === 'POST /api/projects/project_meridian/duplicate'));
        actions.push({ viewport, action: 'Project overflow duplicates through modular API action', pass: true });
        await session.evaluate(`document.querySelector('[data-new-project-open]').click()`);
        await session.waitFor(`Boolean(document.querySelector('[data-new-project]'))`);
        await session.evaluate(`(() => {
          const input = document.querySelector('[data-new-project] input[type="file"]');
          const transfer = new DataTransfer();
          transfer.items.add(new File(['fixture source'], 'fixture.epub', { type: 'application/epub+zip' }));
          input.files = transfer.files;
          input.dispatchEvent(new Event('change', { bubbles: true }));
        })()`);
        await session.waitFor(`document.querySelector('[data-new-project] .transaction-status')?.textContent.includes('valid and ready')`);
        const dialogObserved = await session.evaluate(`(() => {
          const dialog = document.querySelector('[data-new-project] [role="dialog"]');
          const visualColumns = (selector, children) => {
            const node = document.querySelector(selector);
            if (!node) return 0;
            const items = [...node.querySelectorAll(children)].filter((item) => item.getBoundingClientRect().width > 0);
            return new Set(items.map((item) => Math.round(item.getBoundingClientRect().left))).size;
          };
          const create = [...dialog.querySelectorAll('button')].find((button) => button.textContent.includes('Create project'));
          const body = dialog.querySelector('.new-project__body');
          const fields = dialog.querySelector('.new-project__fields');
          const editorial = dialog.querySelector('.new-project__editorial');
          const sourceIdentity = dialog.querySelector('.new-project__source-identity');
          const sourceTitle = sourceIdentity.querySelector('.entity-title');
          const sourceAuthor = sourceIdentity.querySelector('.metadata');
          const legends = [...dialog.querySelectorAll('.new-project .option-group__label')];
          const groups = [...dialog.querySelectorAll('.new-project .option-group')];
          const choices = [...dialog.querySelectorAll('.new-project .choice')];
          return {
            focusContained: dialog?.contains(document.activeElement) || false,
            overflow: document.documentElement.scrollWidth > innerWidth + 1,
            bodyOverflow: getComputedStyle(body).overflowY,
            fieldsOverflow: getComputedStyle(fields).overflowY,
            editorialOverflow: getComputedStyle(editorial).overflowY,
            visibleGroupLabels: legends.filter((item) => item.getBoundingClientRect().width > 2).length,
            optionGroupFramed: groups.some((item) => getComputedStyle(item).borderTopWidth !== '0px'),
            optionGap: groups[0] ? getComputedStyle(groups[0]).gap : '',
            choiceBorders: choices.every((item) => getComputedStyle(item).borderTopWidth === '1px'),
            choiceRounded: choices.every((item) => parseFloat(getComputedStyle(item).borderRadius) > 0),
            sourceIdentityWidth: Math.round(sourceIdentity.getBoundingClientRect().width),
            sourceTitleHeight: Math.round(sourceTitle.getBoundingClientRect().height),
            sourceAuthorHeight: Math.round(sourceAuthor.getBoundingClientRect().height),
            sections: dialog?.querySelectorAll('.new-project__section').length || 0,
            bodyColumns: visualColumns('.new-project__body', ':scope > *'),
            methodColumns: visualColumns('.new-project__method-options', ':scope > .choice'),
            presetColumns: visualColumns('.new-project__preset-options', ':scope > .choice'),
            width: Math.round(dialog?.getBoundingClientRect().width || 0),
            height: Math.round(dialog?.getBoundingClientRect().height || 0),
            sourceTitle: dialog?.querySelector('.new-project__source-identity .entity-title')?.textContent || '',
            sourceState: dialog?.querySelector('.new-project__source-state')?.textContent || '',
            sourceFacts: dialog?.querySelectorAll('.new-project__source-facts > div').length || 0,
            coverTag: dialog?.querySelector('.new-project__cover')?.tagName || '',
            coverSrc: dialog?.querySelector('.new-project__cover')?.getAttribute('src') || '',
            fileAction: dialog?.querySelector('.new-project__file-action')?.textContent || '',
            optionDescriptions: dialog?.querySelectorAll('.choice__description').length || 0,
            createEnabled: Boolean(create && !create.disabled),
            title: dialog?.querySelector('input[name="book_title"]')?.value || '',
            author: dialog?.querySelector('input[name="author"]')?.value || '',
          };
        })()`);
        assert.equal(dialogObserved.focusContained, true);
        assert.equal(dialogObserved.overflow, false);
        assert.equal(dialogObserved.bodyOverflow, 'auto');
        assert.equal(dialogObserved.fieldsOverflow, 'visible');
        assert.equal(dialogObserved.editorialOverflow, 'visible');
        assert.equal(dialogObserved.visibleGroupLabels, 0);
        assert.equal(dialogObserved.optionGroupFramed, false);
        assert.equal(dialogObserved.optionGap, '12px');
        assert.equal(dialogObserved.choiceBorders, true);
        assert.equal(dialogObserved.choiceRounded, true);
        assert.ok(dialogObserved.sourceIdentityWidth >= 200);
        assert.ok(dialogObserved.sourceTitleHeight <= 32);
        assert.ok(dialogObserved.sourceAuthorHeight <= 24);
        assert.equal(dialogObserved.sections, 5);
        assert.equal(dialogObserved.bodyColumns, width < 640 ? 1 : 2);
        assert.equal(dialogObserved.methodColumns, width < 640 ? 1 : width < 1200 ? 2 : 3);
        assert.equal(dialogObserved.presetColumns, width < 640 ? 1 : width < 1200 ? 2 : 4);
        assert.ok(dialogObserved.width <= Math.min(1080, width));
        assert.ok(dialogObserved.height <= Math.min(848, height));
        assert.equal(dialogObserved.sourceTitle, 'Fixture Book');
        assert.equal(dialogObserved.sourceState, 'EPUB file selected');
        assert.equal(dialogObserved.sourceFacts, 4);
        assert.equal(dialogObserved.coverTag, 'IMG');
        assert.match(dialogObserved.coverSrc, /^data:image\/png;base64,/);
        assert.equal(dialogObserved.fileAction, 'Change');
        assert.equal(dialogObserved.optionDescriptions, 7);
        assert.equal(dialogObserved.createEnabled, true);
        assert.equal(dialogObserved.title, 'Fixture Book');
        assert.equal(dialogObserved.author, 'Alex Writer');
        await session.evaluate(`(() => {
          const radio = document.querySelector('[data-new-project] input[name="generation_method"][value="import_existing_script"]');
          radio.checked = true;
          radio.dispatchEvent(new Event('change', { bubbles: true }));
        })()`);
        await session.waitFor(`Boolean([...document.querySelectorAll('[data-new-project] button')].find((button) => button.textContent.includes('Create project'))?.disabled)`);
        actions.push({ viewport, action: 'New Project validates source and method requirements', pass: true });
        await session.screenshot('new-project.png');
        captures.push({ viewport, page: 'new-project', observed: dialogObserved,
          screenshot: path.join(artifacts, 'new-project.png') });
        await session.evaluate(`document.querySelector('[data-new-project-close]').click()`);
        await session.waitFor(`Boolean(document.querySelector('[data-new-project-discard]'))`);
        await session.evaluate(`[...document.querySelectorAll('[data-new-project-discard] button')].find((button) => button.textContent === 'Discard project setup').click()`);
        await session.waitFor(`!document.querySelector('[data-new-project]')`);
        actions.push({ viewport, action: 'New Project protects dirty close', pass: true });
        await session.evaluate(`document.querySelector('[data-project-open]').click()`);
        await session.waitFor(`document.body.dataset.routePath === 'script'`);
        actions.push({ viewport, action: 'Projects → Script', pass: true });
        captures.push({ viewport, ...await captureState(session, 'script', artifacts) });
        await session.evaluate(`AlexandriaShell.navigate('#/script')`);
        await session.waitFor(`document.body.dataset.routePath === 'script' && document.body.dataset.shellState === 'ready'`);
        const denseScript = await session.evaluate(`(() => {
          const owner = document.querySelector('[data-route-owner="script"]');
          const projectGroup = document.querySelector('[data-nav-group="project"]');
          const projectContext = document.querySelector('[data-nav-project-context]');
          const footer = document.querySelector('[data-script-collection-footer]');
          return {
            projectGroupVisible: Boolean(projectGroup && !projectGroup.hidden),
            projectContextVisible: Boolean(projectContext && !projectContext.hidden),
            headerTitle: document.querySelector('[data-shell-project-title]')?.textContent || '',
            navTitle: document.querySelector('[data-nav-project-title]')?.textContent || '',
            projectHref: document.querySelector('[data-nav-project-link]')?.getAttribute('href') || '',
            rows: owner?.querySelectorAll('.script-entry').length || 0,
            footer: footer?.textContent || '',
            loadMore: Boolean(owner?.querySelector('[data-script-load-more]')),
            scrollHeight: owner?.scrollHeight || 0,
            injection: Boolean(globalThis.fixtureInjection || owner?.querySelector('img')),
          };
        })()`);
        const expectedScriptRows = scriptPageSize(width);
        assert.deepEqual({
          projectGroupVisible: denseScript.projectGroupVisible,
          projectContextVisible: denseScript.projectContextVisible,
          headerTitle: denseScript.headerTitle,
          navTitle: denseScript.navTitle,
          rows: denseScript.rows,
          loadMore: denseScript.loadMore,
          injection: denseScript.injection,
        }, {
          projectGroupVisible: true,
          projectContextVisible: false,
          headerTitle: 'The Meridian Archive',
          navTitle: 'The Meridian Archive',
          rows: expectedScriptRows,
          loadMore: true,
          injection: false,
        });
        assert.match(denseScript.projectHref, /#\/script\?project=project_meridian/);
        assert.ok(denseScript.footer.replaceAll(',', '').includes(
          `Showing 1–${expectedScriptRows} of 5275 entries`,
        ));
        assert.ok(denseScript.scrollHeight < 50000, `Script DOM was not bounded: ${denseScript.scrollHeight}`);
        await session.evaluate(`document.querySelector('[data-script-load-more]').click()`);
        await session.waitFor(`document.querySelectorAll('.script-entry').length === ${expectedScriptRows * 2}`);
        const searchInput = `document.querySelector('.script-review input[type="search"]')`;
        await session.evaluate(`(() => { const input=${searchInput}; input.value='Script entry 5275'; input.dispatchEvent(new Event('input',{bubbles:true})); })()`);
        await session.waitFor(`document.querySelectorAll('.script-entry').length === 1`);
        await session.evaluate(`(() => { const input=${searchInput}; input.value=''; input.dispatchEvent(new Event('input',{bubbles:true})); })()`);
        await session.waitFor(`document.querySelectorAll('.script-entry').length === ${expectedScriptRows}`);
        actions.push({ viewport, action: 'Direct Script resolves project and bounds 5,275 entries', pass: true });

        await session.evaluate(`fetch('/__fixture/script-issues?enabled=1',{method:'POST'})`);
        await session.evaluate(`AlexandriaShell.navigate('#/projects')`);
        await session.waitFor(`document.body.dataset.routePath === 'projects'`);
        await session.evaluate(`AlexandriaShell.navigate('#/script')`);
        await session.waitFor(`document.body.dataset.routePath === 'script' && document.querySelectorAll('.script-entry[data-issue-type]').length === 6`);
        const issueState = await session.evaluate(`(() => {
          const filters = Object.fromEntries([...document.querySelectorAll('[data-script-filter]')].map((button) => [
            button.dataset.scriptFilter,
            Number(button.querySelector('.script-issue-filter__count')?.textContent || 0),
          ]));
          const inspector = document.querySelector('.script-review-inspector');
          return {
            filters,
            issueRows: document.querySelectorAll('.script-entry[data-issue-type]').length,
            selectedIssue: inspector?.textContent || '',
            comparisons: inspector?.querySelectorAll('.script-comparison .flat-section').length || 0,
            sourceColumns: getComputedStyle(document.querySelector('[data-script-source-context]')).gridTemplateColumns,
            approveDisabled: Boolean(document.querySelector('[data-script-approve]')?.disabled),
            subtitle: document.querySelector('[data-script-page-subtitle]')?.textContent || '',
          };
        })()`);
        assert.deepEqual(issueState.filters, {
          all: 6, uncertain_speaker: 3, delivery_direction: 2, source_mismatch: 1,
        });
        assert.equal(issueState.issueRows, 6);
        assert.equal(issueState.comparisons, 2);
        assert.equal(issueState.approveDisabled, true);
        assert.match(issueState.selectedIssue, /Speaker attribution is uncertain/);
        assert.match(issueState.selectedIssue, /Source versus Script/);
        assert.match(issueState.subtitle, /6 issues require review/);
        await session.evaluate(`[...document.querySelectorAll('.script-review-inspector button')].find((button) => button.textContent === 'Review speaker correction').click()`);
        await session.waitFor(`Boolean(document.querySelector('.script-generation-dialog-layer[role="dialog"]'))`);
        await session.evaluate(`document.querySelector('[data-script-filter="source_mismatch"]').click()`);
        await session.waitFor(`document.querySelectorAll('.script-entry').length === 1`);
        assert.match(await session.evaluate(`document.querySelector('.script-review-inspector')?.textContent || ''`), /Script text does not match the source/);
        actions.push({ viewport, action: 'Script issue filters, comparison, and correction routing', pass: true });

        await session.evaluate(`fetch('/__fixture/script-issues?enabled=0',{method:'POST'})`);
        await session.evaluate(`AlexandriaShell.navigate('#/projects')`);
        await session.waitFor(`document.body.dataset.routePath === 'projects'`);
        await session.evaluate(`AlexandriaShell.navigate('#/script')`);
        await session.waitFor(`document.body.dataset.routePath === 'script' && Boolean(document.querySelector('[data-script-approve]:not(:disabled)'))`);
        await session.evaluate(`fetch('/__fixture/review-reset',{method:'POST'})`);
        await session.evaluate(`document.querySelector('[data-script-generation-open]').click()`);
        await session.waitFor(`Boolean(document.querySelector('.script-generation-dialog-layer[role="dialog"]'))`);
        const workflowBeforeApproval = await session.evaluate(`(() => {
          const current = document.querySelector('[data-script-workflow-current]');
          const history = document.querySelector('.script-workflow-history');
          const historyTrigger = history?.querySelector('.disclosure__trigger');
          return {
            stage: current?.dataset.stage || '',
            currentText: current?.textContent || '',
            currentSteps: [...(current?.querySelectorAll(
              '[data-script-workflow-step], [data-task-import-kind]',
            ) || [])].map((node) => node.dataset.scriptWorkflowStep || node.dataset.taskImportKind),
            historyLabel: historyTrigger?.textContent || '',
            historyExpanded: historyTrigger?.getAttribute('aria-expanded') === 'true',
            historySteps: [...(history?.querySelectorAll(
              '[data-script-workflow-step], [data-task-import-kind]',
            ) || [])].map((node) => node.dataset.scriptWorkflowStep || node.dataset.taskImportKind),
            deliveryVisible: Boolean(document.querySelector('.script-delivery-plan')),
          };
        })()`);
        assert.equal(workflowBeforeApproval.stage, 'approval');
        assert.match(workflowBeforeApproval.currentText, /Approve the Script/);
        assert.match(workflowBeforeApproval.currentText, /one shared transaction/i);
        assert.match(workflowBeforeApproval.currentText, /advances directly to Qwen and Fish planning/i);
        assert.match(workflowBeforeApproval.currentText, /automated contextual review is optional/i);
        assert.deepEqual(workflowBeforeApproval.currentSteps, ['approve', 'script-review']);
        assert.match(workflowBeforeApproval.historyLabel, /Create or replace the Script/);
        assert.equal(workflowBeforeApproval.historyExpanded, false);
        assert.deepEqual(workflowBeforeApproval.historySteps, ['create', 'script']);
        assert.equal(workflowBeforeApproval.deliveryVisible, false);
        await session.evaluate(`[
          ...document.querySelectorAll('.script-optional-review-disclosure .disclosure__trigger'),
        ][0].click()`);
        await session.waitFor(`document.querySelector('.script-optional-review-disclosure .disclosure__trigger')?.getAttribute('aria-expanded') === 'true'`);
        await session.waitFor(`document.querySelector('.script-contextual-review .transaction-status')?.textContent.includes('approximately 211 LLM calls')`);
        await session.evaluate(`document.querySelector('.script-contextual-review button').click()`);
        await session.waitFor(`document.querySelector('.script-contextual-review .transaction-status')?.textContent.includes('Fixture review failed visibly')`);
        const contextualReview = await session.evaluate(`(() => ({
          status: document.querySelector('.script-contextual-review .transaction-status')?.textContent || '',
          disabled: document.querySelector('.script-contextual-review button')?.disabled === true,
        }))()`);
        assert.match(contextualReview.status, /Local contextual review failed.*Fixture review failed visibly/);
        assert.equal(contextualReview.disabled, false);
        assert.ok(fixture.requests.includes('POST /api/review_script_contextual'));
        actions.push({ viewport, action: 'Contextual review surfaces terminal failure and re-enables action', pass: true });
        await session.evaluate(`(() => {
          const original = HTMLAnchorElement.prototype.click;
          globalThis.__restoreTaskDownloadClick = () => {
            HTMLAnchorElement.prototype.click = original;
            delete globalThis.__restoreTaskDownloadClick;
          };
          HTMLAnchorElement.prototype.click = function clickWithoutOsDownload() {
            if (this.hasAttribute('download')) return;
            return original.call(this);
          };
        })()`);
        const reviewDownloadsBefore = fixture.requests.filter(
          (request) => request === 'GET /api/tasks/task_script_fixture/download',
        ).length;
        await session.evaluate(`[
          ...document.querySelectorAll('.script-contextual-review button'),
        ].find((button) => button.textContent === 'Download review task bundle').click()`);
        await session.waitFor(`document.querySelector('.script-contextual-review .transaction-status')?.textContent.includes('Review task bundle downloaded')`);
        assert.ok(fixture.requests.filter(
          (request) => request === 'GET /api/tasks/task_script_fixture/download',
        ).length > reviewDownloadsBefore);
        actions.push({ viewport, action: 'Script review task generates and downloads in one action', pass: true });
        assert.equal(await session.evaluate(`Boolean(
          document.querySelector('.script-delivery-plan')
          || document.querySelector('[data-backend-render-plan-local]')
          || document.querySelector('[data-backend-render-plan-export]'),
        )`), false);
        actions.push({ viewport, action: 'Locked delivery controls stay out of the approval path', pass: true });
        await session.evaluate(`document.querySelector('.script-workflow-history .disclosure__trigger').click()`);
        await session.waitFor(`document.querySelector('.script-workflow-history .disclosure__trigger')?.getAttribute('aria-expanded') === 'true'`);
        const scriptDownloadsBefore = fixture.requests.filter(
          (request) => request === 'GET /api/tasks/task_script_fixture/download',
        ).length;
        await session.evaluate(`document.querySelector('[data-script-task-export]').click()`);
        await session.waitFor(`document.querySelector('.script-workflow-notice')?.textContent.includes('Script task bundle downloaded')`);
        assert.ok(fixture.requests.filter(
          (request) => request === 'GET /api/tasks/task_script_fixture/download',
        ).length > scriptDownloadsBefore);
        await session.evaluate(`globalThis.__restoreTaskDownloadClick?.()`);
        const workflowModal = await session.evaluate(`(() => {
          const surface=document.querySelector('.script-generation-dialog-layer .dialog-surface');
          const body=surface.querySelector('.dialog__body');
          const importer=document.querySelector('[data-task-import-kind="script"]');
          const drop=importer.querySelector('[data-task-import-dropzone]');
          const icon=drop.querySelector('.task-import-dropzone__icon .ui-icon');
          const title=surface.querySelector('.dialog__header h2');
          const importTitle=importer.querySelector('.task-import-surface__header h2');
          const steps=importer.querySelector('.task-import-steps');
          const player=document.querySelector('[data-persistent-player]');
          const before={clientHeight:body.clientHeight,scrollHeight:body.scrollHeight};
          importer.scrollIntoView({block:'center'});
          const importerRect=importer.getBoundingClientRect();
          const bodyRect=body.getBoundingClientRect();
          const style=(node)=>getComputedStyle(node);
          return {
            ...before,
            maxScroll:body.scrollHeight-body.clientHeight,
            scrollTop:body.scrollTop,
            importerVisible:importerRect.bottom>bodyRect.top && importerRect.top<bodyRect.bottom,
            playerInert:Boolean(player?.closest('.app-shell')?.inert),
            modalTitleFamily:style(title).fontFamily,
            modalTitleSize:parseFloat(style(title).fontSize),
            importTitleFamily:style(importTitle).fontFamily,
            importTitleSize:parseFloat(style(importTitle).fontSize),
            dropDisplay:style(drop).display,
            dropHeight:drop.getBoundingClientRect().height,
            dropBorderStyle:style(drop).borderTopStyle,
            dropRadius:parseFloat(style(drop).borderRadius),
            iconWidth:icon.getBoundingClientRect().width,
            iconHeight:icon.getBoundingClientRect().height,
            stepsDisplay:style(steps).display,
          };
        })()`);
        assert.ok(workflowModal.maxScroll > 0);
        assert.ok(workflowModal.scrollTop > 0);
        assert.equal(workflowModal.importerVisible, true);
        assert.equal(workflowModal.playerInert, true);
        assert.match(workflowModal.modalTitleFamily, /Source Serif 4/);
        assert.ok(workflowModal.modalTitleSize >= 23 && workflowModal.modalTitleSize <= 25);
        assert.match(workflowModal.importTitleFamily, /Source Serif 4/);
        assert.ok(workflowModal.importTitleSize >= 19 && workflowModal.importTitleSize <= 21);
        assert.equal(workflowModal.dropDisplay, 'grid');
        assert.ok(workflowModal.dropHeight >= 72 && workflowModal.dropHeight <= (width < 640 ? 170 : 120));
        assert.notEqual(workflowModal.dropBorderStyle, 'outset');
        assert.ok(workflowModal.dropRadius >= 6);
        assert.ok(workflowModal.iconWidth >= 18 && workflowModal.iconWidth <= 22);
        assert.ok(workflowModal.iconHeight >= 18 && workflowModal.iconHeight <= 22);
        assert.equal(workflowModal.stepsDisplay, 'flex');
        assert.equal(await session.evaluate(`Boolean(
          document.querySelector('[data-script-task-download], [data-script-review-task-download]'),
        )`), false);
        actions.push({ viewport, action: 'Task bundles download directly without a redundant second action', pass: true });
        await session.evaluate(`(() => {
          const surface=document.querySelector('[data-task-import-kind="script"]');
          const drop=surface.querySelector('[data-task-import-dropzone]');
          const selected=surface.querySelector('.task-import-selected');
          const metadata=selected.querySelector('.metadata');
          const dispatch=(target,file)=>{
            const transfer=new DataTransfer();
            transfer.items.add(file);
            for(const type of ['dragenter','dragover','drop']) {
              target.dispatchEvent(new DragEvent(type,{bubbles:true,cancelable:true,dataTransfer:transfer}));
            }
          };
          dispatch(drop,new File(['completed fixture'], 'fixture.alexandria-completed-task.zip', {type:'application/zip'}));
          if(!drop.hidden || selected.hidden) throw new Error('Initial completed-task drop failed.');
          globalThis.confirm=()=>false;
          dispatch(selected,new File(['replacement fixture'], 'replacement.alexandria-completed-task.zip', {type:'application/zip'}));
          if(!metadata.textContent.includes('fixture.alexandria-completed-task.zip')) {
            throw new Error('Declined replacement did not preserve the selected file.');
          }
          globalThis.confirm=()=>true;
          dispatch(selected,new File(['replacement fixture'], 'replacement.alexandria-completed-task.zip', {type:'application/zip'}));
          if(!metadata.textContent.includes('replacement.alexandria-completed-task.zip')) {
            throw new Error('Confirmed replacement did not update the selected file.');
          }
          surface.querySelector('[data-import-completed-task]').click();
        })()`);
        actions.push({ viewport, action: 'Selected completed ZIP supports confirmed drag replacement', pass: true });
        await session.waitFor(`document.querySelector(
          '[data-task-import-kind="script"] [data-completed-task-result]',
        )?.textContent.includes('Apply inspected Script')`);
        const completedTask = await session.evaluate(`(() => {
          const surface = document.querySelector('[data-task-import-kind="script"]');
          return {
            guidance: surface?.textContent || '',
            status: surface?.querySelector('[data-completed-task-result]')?.textContent || '',
            directDescription: document.querySelector('[data-script-import-file]')?.closest('.field')?.textContent || '',
          };
        })()`);
        assert.match(completedTask.guidance, /Do not unzip|returned by ChatGPT/i);
        assert.match(completedTask.status, /Apply inspected Script/);
        assert.doesNotMatch(completedTask.directDescription, /completed task bundle/i);
        assert.ok(fixture.requests.includes('POST /api/tasks/import'));
        actions.push({ viewport, action: 'Import completed ChatGPT task through Task Bundle route', pass: true });
        const acceptanceRequestsBefore = fixture.requests.filter(
          (request) => request === 'POST /api/script_lifecycle/accept',
        ).length;
        await session.evaluate(`(() => {
          const modalButton = document.querySelector('[data-script-modal-approve]');
          const staleHeaderButton = document.querySelector('[data-script-approve]');
          modalButton.click();
          staleHeaderButton.click();
          modalButton.click();
        })()`);
        await session.waitFor(`
          document.querySelector('[data-script-workflow-current]')?.dataset.stage === 'approving'
          && document.querySelector('[data-script-modal-approve]')?.textContent.trim() === 'Approving…'
          && document.querySelector('[data-script-approve]')?.textContent.trim() === 'Approving…'
        `);
        const pendingApproval = await session.evaluate(`(() => ({
          modalDisabled: document.querySelector('[data-script-modal-approve]')?.disabled === true,
          modalBusy: document.querySelector('[data-script-modal-approve]')?.getAttribute('aria-busy'),
          modalStatus: document.querySelector('.script-inline-approval__status')?.textContent || '',
          headerDisabled: document.querySelector('[data-script-approve]')?.disabled === true,
          headerBusy: document.querySelector('[data-script-approve]')?.getAttribute('aria-busy'),
          headerStatus: document.querySelector('[data-project-actions]')?.textContent || '',
          historyExpanded: document.querySelector('.script-workflow-history .disclosure__trigger')
            ?.getAttribute('aria-expanded') === 'true',
        }))()`);
        assert.equal(pendingApproval.modalDisabled, true);
        assert.equal(pendingApproval.modalBusy, 'true');
        assert.match(pendingApproval.modalStatus, /Checking source fidelity.*saving the accepted Script version/i);
        assert.equal(pendingApproval.headerDisabled, true);
        assert.equal(pendingApproval.headerBusy, 'true');
        assert.match(pendingApproval.headerStatus, /Approving/);
        assert.equal(pendingApproval.historyExpanded, false);
        await session.waitFor(`document.querySelector('[data-script-workflow-current]')?.dataset.stage === 'delivery'`);
        await session.waitFor(`
          document.querySelector('[data-backend-render-plan-local]')?.disabled === false
          && document.querySelector('[data-backend-render-plan-export]')?.disabled === false
          && document.querySelector('[data-pronunciation-task-export]')?.disabled === false
          && Boolean(document.querySelector('[data-script-continue]'))
        `);
        const acceptanceRequestsAfter = fixture.requests.filter(
          (request) => request === 'POST /api/script_lifecycle/accept',
        ).length;
        assert.equal(acceptanceRequestsAfter - acceptanceRequestsBefore, 1);
        const approvedFlow = await session.evaluate(`(() => ({
          staleApprovalStatusPresent: Boolean(document.querySelector(
            '.script-inline-approval__status',
          )),
          step3Status: document.querySelector('.script-delivery-plan .transaction-status')?.textContent || '',
          step3Heading: document.querySelector('.script-delivery-plan h2')?.textContent || '',
          step3Copy: document.querySelector('.script-delivery-plan .metadata')?.textContent || '',
          headerAction: document.querySelector('[data-script-continue]')?.textContent || '',
          headerStatus: document.querySelector('[data-project-actions]')?.textContent || '',
          focusedHeading: document.activeElement === document.querySelector('.script-delivery-plan h2'),
          currentContainsDelivery: Boolean(document.querySelector(
            '[data-script-workflow-current] .script-delivery-plan',
          )),
          pronunciationHeading: document.querySelector(
            '.script-pronunciation-guidance h2',
          )?.textContent || '',
          pronunciationStatus: document.querySelector(
            '.script-pronunciation-guidance > .transaction-status',
          )?.textContent || '',
          pronunciationCopy: document.querySelector(
            '.script-pronunciation-guidance > .metadata',
          )?.textContent || '',
          currentContainsPronunciation: Boolean(document.querySelector(
            '[data-script-workflow-current] .script-pronunciation-guidance',
          )),
          currentContainsApproval: Boolean(document.querySelector(
            '[data-script-workflow-current] [data-script-workflow-step="approve"]',
          )),
          historyLabel: document.querySelector('.script-workflow-history .disclosure__trigger')
            ?.textContent || '',
          historyExpanded: document.querySelector('.script-workflow-history .disclosure__trigger')
            ?.getAttribute('aria-expanded') === 'true',
          historyContainsApproval: Boolean(document.querySelector(
            '.script-workflow-history [data-script-workflow-step="approve"]',
          )),
          historyContainsOptionalReview: Boolean(document.querySelector(
            '.script-workflow-history .script-optional-review-disclosure',
          )),
          historyCopyDisplay: getComputedStyle(document.querySelector(
            '.script-workflow-history .disclosure__copy',
          )).display,
        }))()`);
        assert.equal(approvedFlow.staleApprovalStatusPresent, false);
        assert.match(approvedFlow.step3Status, /No model-specific delivery plan exists yet/i);
        assert.match(approvedFlow.step3Heading, /Add Qwen and Fish delivery directions/i);
        assert.match(approvedFlow.step3Copy, /never rewrites spoken text or regenerates existing audio/i);
        assert.match(approvedFlow.headerAction, /Continue to Cast/i);
        assert.match(approvedFlow.headerStatus, /Approved/i);
        assert.equal(approvedFlow.focusedHeading, true);
        assert.equal(approvedFlow.currentContainsDelivery, true);
        assert.match(approvedFlow.pronunciationHeading, /Review names and pronunciation/i);
        assert.match(approvedFlow.pronunciationStatus, /0 approved exact pronunciation occurrences/i);
        assert.match(approvedFlow.pronunciationCopy, /draft until you preview and explicitly accept/i);
        assert.equal(approvedFlow.currentContainsPronunciation, true);
        assert.equal(approvedFlow.currentContainsApproval, false);
        assert.match(approvedFlow.historyLabel, /Change or replace the Script/);
        assert.match(approvedFlow.historyLabel, /Any change returns it to review/);
        assert.equal(approvedFlow.historyExpanded, false);
        assert.equal(approvedFlow.historyContainsApproval, false);
        assert.equal(approvedFlow.historyContainsOptionalReview, false);
        assert.equal(approvedFlow.historyCopyDisplay, 'grid');
        actions.push({ viewport, action: 'One shared approval transaction advances directly to delivery planning', pass: true });
        await session.evaluate(`document.querySelector('.script-generation-dialog-layer .dialog__header button').click()`);
        await session.waitFor(`!document.querySelector('.script-generation-dialog-layer')`);
        await session.evaluate(`document.querySelector('[data-script-continue]').click()`);
        await session.waitFor(`document.body.dataset.routePath === 'cast'`);
        actions.push({ viewport, action: 'Script → Cast', pass: true });
        for (const page of ['library', 'voices', 'templates']) {
          await session.evaluate(`AlexandriaShell.navigate('#/${page}')`);
          await session.waitFor(`document.body.dataset.routePath === '${page}'`);
          captures.push({ viewport, ...await captureState(session, page, artifacts) });
        }
        const cancelRequestsBefore = fixture.requests.filter(
          (request) => request === 'POST /api/background-work/work_fixture_active/cancel',
        ).length;
        await session.evaluate(`AlexandriaShell.navigate('#/more/background-work')`);
        await session.waitFor(`document.body.dataset.routePath === 'more/background-work'
          && document.querySelector('[data-route-owner="more/background-work"]')?.dataset.viewState === 'ready'`);
        captures.push({
          viewport,
          ...await captureState(session, 'background-work', artifacts),
        });
        const backgroundWork = await session.evaluate(`(() => {
          const owner = document.querySelector('[data-route-owner="more/background-work"]');
          const live = owner?.querySelector('[role="status"][aria-live="polite"]');
          const active = owner?.querySelector('[data-background-job="work_fixture_active"]');
          const history = owner?.querySelector('[data-background-job="work_fixture_complete"]');
          const cancel = active?.querySelector('[data-background-work-cancel]');
          const text = owner?.textContent || '';
          return {
            globalHeading: document.querySelector(
              '[data-global-header]:not([hidden]) [data-global-title]',
            )?.textContent?.trim() || '',
            sectionHeadings: [...owner?.querySelectorAll('h2') || []]
              .map((node) => node.textContent.trim()),
            activeState: active?.dataset.state || '',
            historyState: history?.dataset.state || '',
            cancelLabel: cancel?.textContent?.trim() || '',
            cancelDisabled: cancel?.disabled === true,
            liveRegion: Boolean(live),
            overflow: document.documentElement.scrollWidth > innerWidth + 1,
            leakedPrivateState: /owner_token|publication_token|secret_input/.test(text),
          };
        })()`);
        assert.equal(backgroundWork.globalHeading, 'Background Work');
        assert.deepEqual(backgroundWork.sectionHeadings, [
          'Current work', 'Recent history',
        ]);
        assert.equal(backgroundWork.activeState, 'queued');
        assert.equal(backgroundWork.historyState, 'succeeded');
        assert.equal(backgroundWork.cancelLabel, 'Cancel');
        assert.equal(backgroundWork.cancelDisabled, false);
        assert.equal(backgroundWork.liveRegion, true);
        assert.equal(backgroundWork.overflow, false);
        assert.equal(backgroundWork.leakedPrivateState, false);
        await session.evaluate(`document.querySelector(
          '[data-background-work-cancel="work_fixture_active"]',
        ).click()`);
        await session.waitFor(`document.querySelector(
          '[data-route-owner="more/background-work"] [role="status"]',
        )?.textContent.includes('Cancellation requested')`);
        const cancelRequestsAfter = fixture.requests.filter(
          (request) => request === 'POST /api/background-work/work_fixture_active/cancel',
        ).length;
        assert.equal(cancelRequestsAfter - cancelRequestsBefore, 1);
        actions.push({
          viewport,
          action: 'Background Work renders active/history receipts and sends one cancellation request',
          pass: true,
        });
        const runtimeErrors = session.client.events.filter((event) => (
          event.method === 'Runtime.exceptionThrown'
          || (event.method === 'Runtime.consoleAPICalled' && event.params?.type === 'error')
        ));
        assert.deepEqual(runtimeErrors, []);
      } finally {
        await session.close();
      }
    }
  } finally {
    await fixture.close();
  }
  const manifest = { status: 'PASS', viewports: VIEWPORTS, captures, actions, requests: fixture.requests };
  writeJson(path.join(evidenceDir, 'manifest.json'), manifest);
  writeJson(path.join(evidenceDir, 'actions.json'), actions);
  writeJson(path.join(evidenceDir, 'cleanup.json'), { serverExited: true, browserSessions: VIEWPORTS.length });
  return manifest;
}

async function main() {
  if (process.argv.includes('--contract')) {
    process.stdout.write(`B19_T06_PROJECT_FLOW=${JSON.stringify(sourceContract())}\n`);
    return;
  }
  const index = process.argv.indexOf('--evidence-dir');
  assert.ok(process.argv.includes('--browser') && index >= 0 && process.argv[index + 1]);
  const evidenceDir = path.resolve(process.argv[index + 1]);
  fs.mkdirSync(evidenceDir, { recursive: true });
  process.stdout.write(`B19_T06_PROJECT_FLOW_BROWSER=${JSON.stringify(await browserContract(evidenceDir))}\n`);
}

if (require.main === module) {
  main().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });
}

module.exports = { fixtureServer };
