'use strict';

const fs = require('fs');
const http = require('http');
const path = require('path');
const { BrowserSession, argsFrom, required, writeJson } = require('./b19_t06_bootstrap_red.js');

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const json = (value) => JSON.stringify(value);

function character(id, name) {
  return {
    character_id: id, display_name: name, canonical_name: name,
    speaking_role: 'speaking', readiness_state: 'ready', blocker_count: 0, blockers: [],
    identity: { display_name: name, canonical_name: name, role: 'Principal', speaking_state: 'speaking', script_voice_label: name },
    script_connection: { resolved_script_voice_label: name, script_line_count: 18 },
    voice: {
      configuration_key: name, selected_production_method: 'clone', selected_backend: 'qwen3_base',
      selected_voice: 'Avery', valid: true, blockers: [], persistent_voice_description: 'Measured and warm.',
      clone: { reference_source: 'owned recording', exact_reference_transcript: 'A measured fixture line.', reference_audio_state: 'ready' },
      preview: { status: 'approved', approved: true, audio_url: '' },
      adapter: {}, alias: {},
    },
    character: { summary: { canonical_name: name, role: 'Principal', speaking_state: 'speaking' }, expanded: { representative_script_lines: ['A measured fixture line.'] } },
    appearance: { status: 'not_started', optional: true, stable_traits: [], variants: [], conflicts: [], unknowns: [] },
    advanced_voice_setup: { optional: true, blockers: [] },
  };
}

function aggregate(selected) {
  const characters = [selected, character('cast:second', 'Second Character')];
  return {
    characters, selected_character_id: selected.character_id, selected_character: selected,
    selection_visible: true, summary: { complete: true, blocker_count: 0, state: 'complete' },
    process: { running: false }, progress: {}, filters: [],
  };
}

function library() {
  return {
    fingerprint: 'fixture-library', methods: [], voices: [{
      voice_id: 'voice:avery', key: 'Avery', name: 'Avery', method: 'built_in', method_label: 'Built-in',
      assignment: { supported: true }, preview: { available: false },
    }],
  };
}

function fixtureServer(repoRoot, scenario, castDelay, libraryDelay) {
  const staticRoot = path.join(repoRoot, 'app/static');
  const control = { requests: [], aborts: 0, libraryAttempts: 0 };
  const projects = `export async function mount({root}){const h=document.createElement('h1');h.dataset.pageHeading='';h.textContent='Projects';root.replaceChildren(h);}`;
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, 'http://fixture.invalid');
    const receipt = { method: request.method, path: url.pathname, started: Date.now(), ended: null };
    control.requests.push(receipt);
    request.once('aborted', () => { control.aborts += 1; });
    const finish = (status, body = '', type = 'application/json') => {
      if (response.destroyed || response.writableEnded) return;
      receipt.ended = Date.now();
      response.writeHead(status, { 'content-type': `${type}; charset=utf-8`, 'cache-control': 'no-store' });
      response.end(body);
    };
    if (url.pathname === '/api/cast') {
      await wait(castDelay);
      return finish(200, json(aggregate(character('cast:first', 'First Character'))));
    }
    if (url.pathname.startsWith('/api/cast/characters/')) {
      const id = decodeURIComponent(url.pathname.split('/').pop());
      return finish(200, json(character(id, id === 'cast:second' ? 'Second Character' : 'First Character')));
    }
    if (url.pathname === '/api/voice-library') {
      control.libraryAttempts += 1;
      await wait(libraryDelay);
      if (scenario === 'library-error' && control.libraryAttempts === 1) return finish(503, json({ detail: 'Voice catalog unavailable.' }));
      return finish(200, json(library()));
    }
    if (url.pathname === '/api/character_visuals/status') return finish(200, json({ approved_roster_available: true, entries: [], process: { running: false } }));
    if (url.pathname === '/api/voice_backend/capabilities') return finish(200, json({ expressive_clone: { supported: false } }));
    if (url.pathname === '/api/voices') return finish(200, '[]');
    if (url.pathname.startsWith('/api/')) return finish(200, '{}');
    if (url.pathname === '/static/pages/projects.js') return finish(200, projects, 'text/javascript');
    const relative = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\/static\//, '');
    const filename = path.resolve(staticRoot, relative);
    if (!filename.startsWith(`${staticRoot}${path.sep}`) || !fs.existsSync(filename)) return finish(404, 'Not found', 'text/plain');
    const extension = path.extname(filename);
    const type = extension === '.html' ? 'text/html' : extension === '.css' ? 'text/css' : 'text/javascript';
    return finish(200, fs.readFileSync(filename), type);
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve({
      control, url: `http://127.0.0.1:${server.address().port}/`,
      close: () => new Promise((done) => { server.close(done); server.closeAllConnections?.(); }),
    }));
  });
}

async function snapshot(session) {
  return session.evaluate(`(() => {
    const page = document.querySelector('[data-cast-page]');
    const edit = document.querySelector('[data-cast-edit-voice]');
    const status = document.querySelector('[data-cast-voice-library-status]');
    return {
      ready: page?.dataset.castState === 'ready', selected: document.querySelector('[data-cast-identity] h2')?.textContent || '',
      editDisabled: Boolean(edit?.disabled), status: status?.textContent?.trim() || '',
      retry: Boolean(document.querySelector('[data-cast-voice-library-retry]')),
      owners: document.querySelectorAll('[data-route-owner="cast"]').length,
      overflow: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
      focus: document.activeElement?.getAttribute('data-page-heading') === '' ? 'heading' : document.activeElement?.textContent?.trim() || '',
    };
  })()`);
}

async function inspect(args, width, height) {
  const scenario = String(args.scenario || 'delayed-library');
  const castDelay = Number(args['cast-delay-ms'] || (scenario === 'library-error' ? 30 : 300));
  const libraryDelay = Number(args['library-delay-ms'] || (scenario === 'library-error' ? 30 : 6500));
  const artifacts = path.resolve(required(args, 'artifacts'), `${width}x${height}`);
  const fixture = await fixtureServer(process.cwd(), scenario, castDelay, libraryDelay);
  const session = await BrowserSession.open({ url: `${fixture.url}#/cast?project=fixture-project`, artifacts, width, height });
  try {
    await session.waitFor(`document.querySelector('[data-cast-page]')?.dataset.castState==='ready'`, 12000);
    const readyMs = Math.round(await session.evaluate('performance.now()'));
    const pending = await snapshot(session);
    await session.screenshot('cast-ready.png');
    let resolved = pending;
    if (scenario === 'library-error') {
      await session.waitFor(`document.querySelector('[data-cast-voice-library-retry]')`, 3000);
      const error = await snapshot(session);
      await session.evaluate(`document.querySelector('[data-cast-voice-library-retry]').click()`);
      await session.waitFor(`!document.querySelector('[data-cast-edit-voice]')?.disabled`, 3000);
      resolved = await snapshot(session);
      resolved.errorState = error;
    } else {
      if (scenario === 'selection-during-library-load') {
        await session.evaluate(`document.querySelector('[data-character-id="cast:second"]').click()`);
        await session.waitFor(`document.querySelector('[data-cast-identity] h2')?.textContent==='Second Character'`, 3000);
      }
      await session.waitFor(`!document.querySelector('[data-cast-edit-voice]')?.disabled`, libraryDelay + 3000);
      resolved = await snapshot(session);
    }
    const castGets = fixture.control.requests.filter((item) => item.path === '/api/cast').length;
    const detailGets = fixture.control.requests.filter((item) => item.path.startsWith('/api/cast/characters/')).length;
    const libraryGets = fixture.control.requests.filter((item) => item.path === '/api/voice-library').length;
    const selectedDuringLoad = scenario === 'selection-during-library-load';
    const assertions = {
      readyWithinBudget: readyMs <= 1000, oneAggregate: castGets === 1,
      expectedDetailRequests: detailGets === (selectedDuringLoad ? 1 : 0),
      pendingTruthful: scenario === 'library-error' || (pending.editDisabled && pending.status === 'Loading saved Voices…'),
      recovery: scenario !== 'library-error' || (resolved.errorState?.retry && resolved.errorState.status.includes('could not be loaded') && libraryGets === 2),
      resolved: !resolved.editDisabled && resolved.selected === (selectedDuringLoad ? 'Second Character' : 'First Character'),
      stableSurface: resolved.owners === 1 && resolved.overflow <= 1, independentRequests: fixture.control.aborts === 0,
    };
    return { width, height, scenario, readyMs, pending, resolved, requests: fixture.control.requests, assertions,
      status: Object.values(assertions).every(Boolean) ? 'PASS' : 'RED' };
  } finally {
    await session.close();
    await fixture.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const viewports = String(args.viewports || '1024x900').split(',').map((value) => value.split('x').map(Number));
  const results = [];
  for (const [width, height] of viewports) results.push(await inspect(args, width, height));
  const report = { status: results.every((item) => item.status === 'PASS') ? 'PASS' : 'RED', results };
  writeJson(path.join(path.resolve(required(args, 'artifacts')), 'report.json'), report);
  process.stdout.write(`B19_T06_CAST_LOAD=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

if (require.main === module) main().catch((error) => { console.error(error.stack || error); process.exitCode = 2; });
