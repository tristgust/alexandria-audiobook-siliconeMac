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
    catalog: { schema_version: 1, catalog_fingerprint: 'catalog-1', projects: [project] },
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
    entries: [
      { speaker: 'NARRATOR', text: 'The archive opened at dusk.', instruct: 'Measured narration.' },
      { speaker: 'MARA', text: 'We catalogue what the sea returns.', instruct: 'Quiet certainty.' },
    ],
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
      const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8')
        .replace('</head>', '<link rel="stylesheet" href="/static/styles/pages/project_flow.css"></head>');
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
        await session.evaluate(`document.querySelector('[data-new-project-open]').click()`);
        await session.waitFor(`Boolean(document.querySelector('[data-new-project]'))`);
        const dialogObserved = await session.evaluate(`(() => {
          const dialog = document.querySelector('[data-new-project] [role="dialog"]');
          return { focusContained: dialog?.contains(document.activeElement) || false,
            overflow: document.documentElement.scrollWidth > innerWidth + 1 };
        })()`);
        assert.deepEqual(dialogObserved, { focusContained: true, overflow: false });
        await session.screenshot('new-project.png');
        captures.push({ viewport, page: 'new-project', observed: dialogObserved,
          screenshot: path.join(artifacts, 'new-project.png') });
        await session.evaluate(`document.querySelector('[data-new-project-close]').click()`);
        await session.evaluate(`document.querySelector('[data-project-open]').click()`);
        await session.waitFor(`document.body.dataset.routePath === 'script'`);
        actions.push({ viewport, action: 'Projects → Script', pass: true });
        captures.push({ viewport, ...await captureState(session, 'script', artifacts) });
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
