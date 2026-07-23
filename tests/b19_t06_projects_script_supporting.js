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
    assert.match(source, /textContent|createTextNode/);
    assert.match(source, /AbortSignal|signal/);
    for (const marker of FORBIDDEN) assert.doesNotMatch(source, new RegExp(marker), `${name}: ${marker}`);
    observations.push({ name, lines: source.split('\n').length });
  }
  const combined = PAGE_NAMES.map(
    (name) => fs.readFileSync(path.join(STATIC, 'pages', `${name}.js`), 'utf8'),
  ).join('\n');
  for (const endpoint of [
    '/api/projects', '/api/projects/inspect-source', '/api/project_flow/status',
    '/api/script_lifecycle/status', '/api/annotated_script',
    '/api/script_lifecycle/accept', '/api/library', '/api/voice-library', '/api/templates',
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
      valid: true, filename: 'fixture.txt', title: 'Fixture Book', author: 'Alex Writer',
      source_type: 'text', language: 'English', generation_method: 'local', size_bytes: 1200,
    });
    if (url.pathname === '/api/projects' && request.method === 'POST') return json({
      project: data.project, catalog_fingerprint: 'catalog-2',
      activation: { state: 'current', native_destination: 'script' },
    });
    if (/^\/api\/projects\/[^/]+\/open$/.test(url.pathname)) return json({
      catalog_fingerprint: 'catalog-2', activation: { state: 'current', native_destination: 'script' },
    });
    if (url.pathname === '/api/project_flow/status') return json(data.flow);
    if (url.pathname === '/api/script_lifecycle/status') {
      data.lifecycle.accepted = false; data.lifecycle.state = 'review_required'; data.lifecycle.blockers = [];
      return json(data.lifecycle);
    }
    if (url.pathname === '/api/annotated_script') return json(data.entries);
    if (url.pathname === '/api/script_lifecycle/accept') {
      data.lifecycle.accepted = true; data.lifecycle.state = 'accepted'; data.lifecycle.blockers = [];
      return json(data.lifecycle);
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
    return { owner: owner?.dataset.routeOwner || '', heading: owner?.querySelector('h1')?.textContent || '',
      focused: document.activeElement === owner?.querySelector('h1'),
      overflow: document.documentElement.scrollWidth > innerWidth + 1 };
  })()`);
  assert.equal(observed.overflow, false, `${page} overflowed`);
  assert.ok(observed.heading, `${page} heading`);
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
            playerState: document.querySelector('[data-persistent-player]')?.dataset.state || '',
          };
        })()`);
        assert.deepEqual(homeObserved, {
          globalTitle: 'Project Home',
          globalSubtitle: 'Open an existing project or create a new one.',
          searchInHeader: true,
          primaryActions: 1,
          projectGroupHidden: true,
          projectContextHidden: true,
          continuation: true,
          stageTrackers: 1,
          rows: 1,
          rowPrimaryActions: 0,
          playerState: 'inactive',
        });
        actions.push({ viewport, action: 'Project Home matches global continuation anatomy', pass: true });
        await session.evaluate(`document.querySelector('[data-new-project-open]').click()`);
        await session.waitFor(`Boolean(document.querySelector('[data-new-project]'))`);
        await session.evaluate(`(() => {
          const input = document.querySelector('[data-new-project] input[type="file"]');
          const transfer = new DataTransfer();
          transfer.items.add(new File(['fixture source'], 'fixture.txt', { type: 'text/plain' }));
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
          const create = [...dialog.querySelectorAll('button')].find((button) => button.textContent.includes('Create Project'));
          return {
            focusContained: dialog?.contains(document.activeElement) || false,
            overflow: document.documentElement.scrollWidth > innerWidth + 1,
            sections: dialog?.querySelectorAll('.new-project__section').length || 0,
            bodyColumns: visualColumns('.new-project__body', ':scope > *'),
            methodColumns: visualColumns('.new-project__method-options', ':scope > .choice'),
            presetColumns: visualColumns('.new-project__preset-options', ':scope > .choice'),
            width: Math.round(dialog?.getBoundingClientRect().width || 0),
            height: Math.round(dialog?.getBoundingClientRect().height || 0),
            sourceTitle: dialog?.querySelector('.new-project__source-identity .entity-title')?.textContent || '',
            createEnabled: Boolean(create && !create.disabled),
            title: dialog?.querySelector('input[name="book_title"]')?.value || '',
            author: dialog?.querySelector('input[name="author"]')?.value || '',
          };
        })()`);
        assert.equal(dialogObserved.focusContained, true);
        assert.equal(dialogObserved.overflow, false);
        assert.equal(dialogObserved.sections, 5);
        assert.equal(dialogObserved.bodyColumns, width < 640 ? 1 : 2);
        assert.equal(dialogObserved.methodColumns, width < 640 ? 1 : 3);
        assert.equal(dialogObserved.presetColumns, width < 640 ? 1 : width < 1200 ? 2 : 4);
        assert.ok(dialogObserved.width <= Math.min(1080, width));
        assert.ok(dialogObserved.height <= Math.min(848, height));
        assert.equal(dialogObserved.sourceTitle, 'Fixture Book');
        assert.equal(dialogObserved.createEnabled, true);
        assert.equal(dialogObserved.title, 'Fixture Book');
        assert.equal(dialogObserved.author, 'Alex Writer');
        await session.evaluate(`(() => {
          const radio = document.querySelector('[data-new-project] input[name="generation_method"][value="import_existing_script"]');
          radio.checked = true;
          radio.dispatchEvent(new Event('change', { bubbles: true }));
        })()`);
        await session.waitFor(`Boolean([...document.querySelectorAll('[data-new-project] button')].find((button) => button.textContent.includes('Create Project'))?.disabled)`);
        actions.push({ viewport, action: 'New Project validates source and method requirements', pass: true });
        await session.screenshot('new-project.png');
        captures.push({ viewport, page: 'new-project', observed: dialogObserved,
          screenshot: path.join(artifacts, 'new-project.png') });
        await session.evaluate(`document.querySelector('[data-new-project-close]').click()`);
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
          projectContextVisible: true,
          headerTitle: 'The Meridian Archive',
          navTitle: 'The Meridian Archive',
          rows: 120,
          loadMore: true,
          injection: false,
        });
        assert.match(denseScript.projectHref, /#\/script\?project=project_meridian/);
        assert.match(denseScript.footer.replaceAll(',', ''), /Showing 120 of 5275 entries/);
        assert.ok(denseScript.scrollHeight < 50000, `Script DOM was not bounded: ${denseScript.scrollHeight}`);
        await session.evaluate(`document.querySelector('[data-script-load-more]').click()`);
        await session.waitFor(`document.querySelectorAll('.script-entry').length === 240`);
        const searchInput = `document.querySelector('.script-review input[type="search"]')`;
        await session.evaluate(`(() => { const input=${searchInput}; input.value='Script entry 5275'; input.dispatchEvent(new Event('input',{bubbles:true})); })()`);
        await session.waitFor(`document.querySelectorAll('.script-entry').length === 1`);
        await session.evaluate(`(() => { const input=${searchInput}; input.value=''; input.dispatchEvent(new Event('input',{bubbles:true})); })()`);
        await session.waitFor(`document.querySelectorAll('.script-entry').length === 120`);
        actions.push({ viewport, action: 'Direct Script resolves project and bounds 5,275 entries', pass: true });
        await session.evaluate(`document.querySelector('[data-script-approve]').click()`);
        await session.waitFor(`Boolean(document.querySelector('[data-script-continue]'))`);
        actions.push({ viewport, action: 'Approve Script', pass: true });
        await session.evaluate(`document.querySelector('[data-script-continue]').click()`);
        await session.waitFor(`document.body.dataset.routePath === 'cast'`);
        actions.push({ viewport, action: 'Script → Cast', pass: true });
        for (const page of ['library', 'voices', 'templates']) {
          await session.evaluate(`AlexandriaShell.navigate('#/${page}')`);
          await session.waitFor(`document.body.dataset.routePath === '${page}'`);
          captures.push({ viewport, ...await captureState(session, page, artifacts) });
        }
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

main().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });
