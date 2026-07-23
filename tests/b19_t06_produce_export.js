'use strict';

const fs = require('fs');
const http = require('http');
const path = require('path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');

const ROOT = path.resolve(__dirname, '..');
const STATIC = path.join(ROOT, 'app', 'static');
const DEFAULT_VIEWPORTS = '390x844,768x1024,1280x800,1440x960';

const json = (value) => JSON.stringify(value);
const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const stateLabel = (state) => ({
  ready: 'Ready to generate', generating: 'Generating', needs_listening: 'Needs listening',
  current: 'Current', stale: 'Stale', failed: 'Failed', missing_voice: 'Blocked',
})[state] || state;

function produceRow(id, state, overrides = {}) {
  const speaker = overrides.speaker || 'Alistair Wren';
  return {
    chunk_id: `chunk:${id}`, index: Number.parseInt(id.replace(/\D/g, ''), 10) || 1,
    speaker, character_name: speaker, text: overrides.text || `Fixture excerpt for ${id}.`,
    text_excerpt: overrides.text || `Fixture excerpt for ${id}.`,
    delivery_direction: overrides.direction || 'Measured, with intent',
    pause_after_ms: 350, duration_ms: state === 'ready' || state === 'missing_voice' ? null : 9000,
    state, reason: overrides.reason || (state === 'stale' ? 'audio_fingerprint_mismatch' : null),
    selected: id === 'stale-1', required_for_completion: true,
    voice: { valid: state !== 'missing_voice', configuration_key: speaker, method: 'built_in' },
    audio: {
      available: ['current', 'needs_listening'].includes(state),
      url: ['current', 'needs_listening'].includes(state) ? `/fixture-audio/${id}.mp3` : null,
      stale_audio_available: state === 'stale',
    },
    review: { listening_required: state === 'needs_listening', listening_state: null },
    blockers: state === 'missing_voice' ? [{
      code: 'produce_voice_missing', title: 'Missing voice',
      explanation: 'Assign a production Voice in Cast.', native_destination: 'cast',
      target_id: 'cast:alistair', blocking: true,
    }] : state === 'failed' ? [{
      code: 'produce_audio_failed', title: 'Generation failed',
      explanation: 'Retry this chunk.', native_destination: 'produce',
      target_id: `chunk:${id}`, blocking: true,
    }] : [],
    regenerate_action: state === 'missing_voice' || state === 'generating' ? null : {
      id: state === 'ready' ? 'generate_chunk' : 'regenerate_chunk',
      label: state === 'ready' ? 'Generate' : 'Regenerate',
    },
  };
}

function produceFixture(mode) {
  const base = [
    produceRow('ready-1', 'ready', { speaker: 'Clara Leighton' }),
    produceRow('current-1', 'current', { speaker: 'Edmund Fairfax' }),
    produceRow('stale-1', 'stale', { reason: 'Direction edited after audio generation.' }),
    produceRow('failed-1', 'failed', { speaker: 'Isobel Marwell' }),
    produceRow('listen-1', 'needs_listening', { speaker: 'Robert Bain' }),
    produceRow('blocked-1', 'missing_voice', { speaker: 'Jane Whitfield' }),
  ];
  const attentionStates = [
    ...Array(12).fill('ready'),
    ...Array(4).fill('stale'),
    ...Array(2).fill('failed'),
    ...Array(7).fill('needs_listening'),
  ];
  const chunks = mode === 'empty' ? [] : mode === 'dense'
    ? Array.from({ length: 24 }, (_, index) => produceRow(
      `dense-${index + 1}`, ['ready', 'current', 'stale', 'needs_listening'][index % 4],
      { text: index === 0 ? '<img src=x onerror="globalThis.fixtureInjection=true">' : `Dense fixture ${index + 1}` },
    )) : mode === 'mixed'
      ? Array.from({ length: 5275 }, (_, index) => {
        const state = index < 5250 ? 'current' : attentionStates[index - 5250];
        const row = produceRow(index === 5263 ? 'stale-1' : `scale-${index + 1}`, state, {
          speaker: index % 7 === 0 ? 'Clara Leighton' : 'Alistair Wren',
          text: index === 0
            ? '<img src=x onerror="globalThis.fixtureInjection=true"> Dense production fixture.'
            : `Production chunk ${index + 1} of 5275.`,
        });
        row.index = index + 1;
        return row;
      })
      : base;
  const selected = chunks.find((item) => item.chunk_id === 'chunk:stale-1') || chunks[0] || null;
  const running = mode === 'running';
  const counts = mode === 'running'
    ? { current: 178, ready: 12, stale: 4, failed: 2, needs_listening: 7, needs_review: 0, generating: 0, missing_voice: 0 }
    : Object.fromEntries(['current', 'ready', 'stale', 'failed', 'needs_listening', 'needs_review', 'generating', 'missing_voice']
      .map((state) => [state, chunks.filter((item) => item.state === state).length]));
  const required = Object.values(counts).reduce((sum, value) => sum + value, 0);
  return {
    schema_version: 1, state: running ? 'running' : mode === 'blocked' ? 'blocked' : chunks.length ? 'ready' : 'not_started',
    summary: {
      required_chunk_count: required, current_count: counts.current,
      needs_generation_count: counts.ready + counts.stale,
      needs_review_count: counts.needs_listening, failed_count: counts.failed,
      missing_voice_count: counts.missing_voice, blocker_count: counts.missing_voice + counts.failed,
      complete: false,
    },
    counts, chunks, all_chunk_count: required, visible_chunk_count: chunks.length,
    selected_chunk_id: selected?.chunk_id || null, selected_chunk: selected,
    selection_visible: Boolean(selected),
    process: {
      running, cancel_requested: false, total_count: 16, completed_count: running ? 6 : 0,
      failed_count: running ? 1 : 0, cancelled_count: 0, queued_chunk_ids: [],
      logs: running ? ['Generation is running.'] : [],
    },
    primary_action: running
      ? { id: 'cancel_produce_generation', label: 'Cancel generation', endpoint: '/api/produce/cancel' }
      : { id: 'generate_missing_stale_audio', label: 'Generate missing and stale audio', endpoint: '/api/produce/generate', mode: 'missing_stale' },
    secondary_actions: [{ id: 'regenerate_all_audio', label: 'Regenerate all audio', mode: 'regenerate_all', destructive: true }],
    fingerprints: { chunks: 'fixture-chunks' },
  };
}

function exportFixture(mode) {
  const complete = mode === 'complete';
  const running = mode === 'running';
  const blocked = ['blocked', 'empty'].includes(mode);
  const chapters = mode === 'empty' ? [] : Array.from({ length: mode === 'dense' ? 24 : 6 }, (_, index) => ({
    chapter_id: `chapter:${index}`, order: index, name: index === 0 ? 'The Letter Arrives' : `Chapter ${index + 1}`,
    start_ms: index * 1800000, end_ms: (index + 1) * 1800000,
  }));
  const current = {
    format: 'm4b', filename: 'audiobook.m4b', state: complete ? 'current' : 'missing',
    exists: complete, playback_url: complete ? '/fixture-audio/audiobook.m4b' : null,
    duration_ms: complete ? 45936000 : null, size_bytes: complete ? 1200000000 : null,
  };
  const blockers = blocked ? [{
    code: mode === 'empty' ? 'export_chapters_required' : 'export_produce_incomplete',
    title: mode === 'empty' ? 'No chapters are available' : 'Produce is incomplete',
    explanation: mode === 'empty' ? 'Review Script chapter structure.' : 'Finish required audio in Produce.',
    native_destination: mode === 'empty' ? 'script' : 'produce', target_id: 'fixture:blocker', blocking: true,
  }] : [];
  const metadata = blocked ? { title: '', author: '', narrator: '', year: '', description: '' } : {
    title: 'The First Correspondence', author: 'Isobel Marwell',
    narrator: 'Alistair Wren', year: '2026', description: 'A novel.',
  };
  const plan = {
    metadata, formats: ['m4b'], chapter_mode: 'smart', chapters, blockers,
    safe_to_execute: !blocked, plan_fingerprint: 'fixture-export-plan',
    dependency_fingerprint: 'fixture-export-dependencies',
    output_filenames: { m4b: 'audiobook.m4b' },
  };
  return {
    schema_version: 1, state: running ? 'running' : complete ? 'complete' : blocked ? 'blocked' : 'ready',
    metadata, formats: ['m4b'], chapter_mode: 'smart', chapters,
    cover: { exists: false, relative_path: null }, outputs: { m4b: current, mp3: { format: 'mp3', filename: 'cloned_audiobook.mp3', state: 'missing' }, audacity: { format: 'audacity', filename: 'audacity_export.zip', state: 'missing' } },
    selected_outputs: [current], summary: { selected_format_count: 1, current_output_count: complete ? 1 : 0, chapter_count: chapters.length, blocker_count: blockers.length, complete },
    blockers, process: { running, cancel_requested: false, logs: running ? ['Building selected output.'] : [], completed_count: running ? 2 : 0, total_count: 4 },
    primary_action: running ? { id: 'cancel_export_build', label: 'Cancel build', endpoint: '/api/export/cancel' }
      : !blocked && !complete ? { id: 'build_export', label: 'Build audiobook', endpoint: '/api/export/build' } : null,
    plan, receipt: complete ? { build_id: 'fixture-build', formats: ['m4b'] } : null,
    player: complete ? { format: 'm4b', url: current.playback_url, duration_ms: current.duration_ms } : null,
    fingerprints: { dependencies: plan.dependency_fingerprint, plan: plan.plan_fingerprint },
  };
}

function fixtureServer() {
  const control = { mode: 'produce-mixed', requests: [], pending: [], aborted: 0 };
  const projectModule = `export async function mount({root,route}){const n=document.createElement('article');n.dataset.routeOwner='projects';const h=document.createElement('h1');h.dataset.pageHeading='';h.textContent='Project Home';n.append(h);root.replaceChildren(n);}`;
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, 'http://fixture.invalid');
    const receipt = { method: request.method, path: url.pathname, body: null, completed: false };
    control.requests.push(receipt);
    request.once('aborted', () => { if (!receipt.completed) control.aborted += 1; });
    response.once('close', () => { if (!receipt.completed) control.aborted += 1; });
    const finish = (status, body = '', type = 'text/plain; charset=utf-8') => {
      if (response.destroyed || response.writableEnded) return;
      response.writeHead(status, { 'content-type': type, 'cache-control': 'no-store' });
      response.end(request.method === 'HEAD' ? '' : body); receipt.completed = true;
    };
    if (url.pathname === '/__fixture/mode') {
      control.mode = url.searchParams.get('value') || control.mode; return finish(204);
    }
    if (url.pathname.startsWith('/api/')) {
      const chunks = []; for await (const chunk of request) chunks.push(chunk);
      if (chunks.length) receipt.body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (control.mode.endsWith('-error')) return finish(500, json({ detail: 'Fixture read failed.' }), 'application/json');
      const delayed = control.mode.endsWith('-loading') && request.method === 'GET';
      const payload = url.pathname === '/api/projects' ? {
        schema_version: 1,
        catalog_fingerprint: 'fixture-catalog',
        current_project_id: 'fixture-project',
        last_selected_project_id: 'fixture-project',
        projects: [{
          id: 'fixture-project',
          name: 'The Meridian Archive',
          source_title: 'The Meridian Archive',
          current: true,
          selected: true,
          current_recommended_stage: control.mode.startsWith('export-') ? 'export' : 'produce',
          stage_summary: control.mode.startsWith('export-')
            ? 'Review publication details.' : 'Review production audio.',
          blocker_count: 0,
        }],
      } : url.pathname === '/api/produce' ? produceFixture(control.mode.replace('produce-', ''))
        : url.pathname === '/api/export' ? exportFixture(control.mode.replace('export-', ''))
          : url.pathname === '/api/produce/plan' ? { mode: receipt.body?.mode, indices: [0], plan_fingerprint: `fixture-${receipt.body?.mode}`, chunks_fingerprint: 'fixture-chunks', blockers: [], safe_to_execute: true }
            : url.pathname === '/api/export/plan' ? { ...exportFixture('ready').plan, ...receipt.body }
              : { status: url.pathname.endsWith('/cancel') ? 'cancelling' : 'accepted' };
      const send = () => finish(200, json(payload), 'application/json');
      if (delayed) control.pending.push(send); else send(); return;
    }
    if (url.pathname.startsWith('/fixture-audio/')) return finish(204);
    if (url.pathname === '/static/pages/projects.js') return finish(200, projectModule, 'text/javascript; charset=utf-8');
    const relative = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\/static\//, '');
    const filename = path.resolve(STATIC, relative);
    if (!filename.startsWith(`${STATIC}${path.sep}`) || !fs.existsSync(filename) || !fs.statSync(filename).isFile()) return finish(404, 'Not found');
    const type = path.extname(filename) === '.html' ? 'text/html' : path.extname(filename) === '.css' ? 'text/css' : 'text/javascript';
    return finish(200, fs.readFileSync(filename), `${type}; charset=utf-8`);
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve({
      server, control, url: `http://127.0.0.1:${server.address().port}/`,
      release: () => control.pending.splice(0).forEach((send) => send()),
      close: () => new Promise((done) => { server.close(done); server.closeAllConnections?.(); }),
    }));
  });
}

async function setMode(session, control, mode, route, loading = false) {
  control.mode = mode; control.pending.length = 0;
  const separator = route.includes('?') ? '&' : '?';
  await session.client.send('Page.navigate', { url: `${session.baseUrl}#/${route}${separator}source=fixture-run-${Date.now()}` });
  const expected = mode.split('-').at(-1);
  await session.waitFor(loading
    ? `Boolean(document.querySelector('[data-page-state="loading"]')) || document.body.dataset.shellState === 'ready'`
    : `document.body.dataset.shellState === 'ready' && Boolean(document.querySelector('[data-page-state="${expected}"]'))`);
}

function runtimeErrors(session) {
  return session.client.events.filter((item) => item.method === 'Runtime.exceptionThrown'
    || (item.method === 'Runtime.consoleAPICalled' && item.params?.type === 'error'));
}

async function snapshot(session, owner) {
  return session.evaluate(`(() => {
    const root=document.querySelector('[data-route-owner="${owner}"]');
    const controls=[...(root?.querySelectorAll('button,a[href],input,[tabindex]')||[])].filter(n=>!n.disabled);
    return {
      owner:Boolean(root), installed:Boolean(root?.matches('[data-${owner}-page]')), state:root?.dataset.pageState||null,
      overflow:document.documentElement.scrollWidth-innerWidth,
      focus:document.activeElement?.matches('[data-page-heading]')||false,
      injection:Boolean(globalThis.fixtureInjection||root?.querySelector('img')),
      minText:Math.min(...[...(root?.querySelectorAll('*')||[])].filter(n=>n.textContent.trim()&&getComputedStyle(n).display!=='none').map(n=>parseFloat(getComputedStyle(n).fontSize))),
      named:controls.filter(n=>(n.getAttribute('aria-label')||n.textContent
        ||n.closest('label')?.textContent
        ||(n.id&&document.querySelector('label[for="'+CSS.escape(n.id)+'"]')?.textContent)||'').trim()).length===controls.length,
      persistentInside:root?.querySelectorAll('[data-primitive="persistent-player"]').length||0,
      compactPlay:root?.querySelectorAll('[data-primitive="compact-play"]').length||0,
      waveforms:root?.querySelectorAll('[data-primitive="waveform"]').length||0,
      selected:root?.querySelector('[data-audio-row][aria-selected="true"]')?.dataset.audioState||null,
      audioRows:root?.querySelectorAll('[data-audio-row]').length||0,
      collectionText:root?.querySelector('[data-produce-collection-footer]')?.textContent||'',
      chapterGroups:root?.querySelectorAll('.produce-chapter-group').length||0,
      columnHeaders:root?.querySelector('.audio-table__header')?.textContent||'',
      produceStats:root?.querySelectorAll('.produce-stat').length||0,
      inspectorText:document.querySelector('[data-shell-inspector]')?.textContent||'',
      pagePrimary:document.querySelectorAll('[data-project-header] .ui-button[data-variant="primary"]:not(:disabled)').length,
      ownerScrollHeight:root?.scrollHeight||0,
      projectTitle:document.querySelector('[data-shell-project-title]')?.textContent||'',
      navProjectTitle:document.querySelector('[data-nav-project-title]')?.textContent||'',
      projectGroupVisible:Boolean(document.querySelector('[data-nav-group="project"]')&&!document.querySelector('[data-nav-group="project"]').hidden),
      projectContextVisible:Boolean(document.querySelector('[data-nav-project-context]')&&!document.querySelector('[data-nav-project-context]').hidden),
      projectHref:document.querySelector('[data-nav-project-link]')?.getAttribute('href')||'',
      statuses:[...(root?.querySelectorAll('[data-status-value]')||[])].map(n=>n.dataset.statusValue),
      text:root?.innerText||'',
      headerText:document.querySelector('[data-project-header]')?.innerText||''
    };
  })()`);
}

async function clickAndWait(session, control, selector, pathName) {
  const before = control.requests.length;
  const clicked = await session.evaluate(`document.querySelector(${json(selector)})?.click(); Boolean(document.querySelector(${json(selector)}))`);
  if (!clicked) return false;
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (control.requests.slice(before).some((item) => item.path === pathName)) return true;
    await wait(25);
  }
  return false;
}

async function inspectScenario(server, artifacts, scenario, width, height) {
  const viewport = `${width}x${height}`;
  const folder = path.join(artifacts, viewport); fs.mkdirSync(folder, { recursive: true });
  server.control.mode = scenario === 'export' ? 'export-ready' : 'produce-mixed';
  const route = scenario === 'export' ? 'export' : 'produce?chunk=stale-1';
  const session = await BrowserSession.open({ url: `${server.url}#/${route}`, artifacts: folder, width, height });
  session.baseUrl = server.url;
  try {
    await session.waitFor(`document.body.dataset.shellState === 'ready'`);
    const owner = scenario === 'export' ? 'export' : 'produce';
    const initial = await snapshot(session, owner);
    await session.screenshot(`${scenario}-ready.png`);
    const assertions = {
      directOwner: initial.installed, noOverflow: initial.overflow <= 1, titleFocused: initial.focus,
      safeDom: !initial.injection, namedControls: initial.named, textFloor: initial.minText >= 13,
      noDuplicateTransport: initial.persistentInside === 0, noRuntimeErrors: runtimeErrors(session).length === 0,
      resolvedProject: initial.projectTitle === 'The Meridian Archive'
        && initial.navProjectTitle === 'The Meridian Archive',
      completeProjectShell: initial.projectGroupVisible && initial.projectContextVisible,
      projectContextRoute: /project=fixture-project/.test(initial.projectHref),
    };
    if (!initial.installed) return { viewport, status: 'FAIL', assertions, initial, runtimeErrors: runtimeErrors(session) };
    if (scenario === 'produce') {
      await session.evaluate(`document.querySelector('[data-produce-load-more]')?.click()`);
      await session.waitFor(`document.querySelectorAll('[data-audio-row]').length === 301`);
      const rowsAfterLoad = await session.evaluate(`document.querySelectorAll('[data-audio-row]').length`);
      const generated = await clickAndWait(session, server.control,
        '[data-produce-primary]', '/api/produce/generate');
      await session.waitFor(`Boolean(document.querySelector('[data-produce-action="retry"]:not(:disabled)'))`);
      const retried = await clickAndWait(session, server.control,
        '[data-produce-action="retry"]', '/api/produce/retry-failed');
      Object.assign(assertions, {
        selectedStaysStale: initial.selected === 'stale',
        compactOnly: initial.compactPlay > 0 && initial.waveforms > 0,
        referenceCounts: /5250\s+current/i.test(initial.text.replaceAll(',', ''))
          && /25\s+need attention/i.test(initial.text.replaceAll(',', ''))
          && ['Ready to generate', 'Needs listening', 'Failed', 'Stale', 'Current']
            .every((label) => initial.text.includes(label)),
        boundedInitialRows: initial.audioRows === 151,
        boundedAfterLoad: rowsAfterLoad === 301,
        boundedHeight: initial.ownerScrollHeight < 60000,
        truthfulCollectionCount: /Showing 151 of 5275 chunks/.test(initial.collectionText.replaceAll(',', '')),
        groupedAudioRows: initial.chapterGroups >= 1,
        canonicalColumns: ['Character', 'Text excerpt', 'Delivery direction', 'Duration', 'Audio', 'State', 'Action']
          .every((label) => initial.columnHeaders.includes(label)),
        staleInspectorReason: /Stale reason/i.test(initial.inspectorText)
          && /(Direction edited after audio generation|Audio Fingerprint Mismatch)/i
            .test(initial.inspectorText),
        noProduceKpiStrip: initial.produceStats === 0,
        onePagePrimary: initial.pagePrimary === 1,
        generated,
        retried,
      });
      await setMode(session, server.control, 'produce-running', route);
      assertions.progressBanner = await session.evaluate(`Boolean(document.querySelector('.produce-progress-banner [role="progressbar"]'))
        && Boolean(document.querySelector('[data-produce-cancel]'))`);
      assertions.cancelled = await clickAndWait(session, server.control, '[data-produce-primary]', '/api/produce/cancel');
      await session.screenshot('produce-running.png');
    } else {
      Object.assign(assertions, {
        readiness: /Ready to build/i.test(`${initial.text} ${initial.headerText}`),
        currentTakeTruth: /No current Take/i.test(initial.text),
        formatLabels: /M4B audiobook/.test(initial.text) && /Separate chapter files/.test(initial.text),
        built: await clickAndWait(session, server.control, '[data-export-primary]', '/api/export/build'),
      });
      await setMode(session, server.control, 'export-running', route);
      assertions.cancelled = await clickAndWait(session, server.control, '[data-export-primary]', '/api/export/cancel');
      await session.screenshot('export-running.png');
    }
    return { viewport, status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL', assertions, initial, runtimeErrors: runtimeErrors(session) };
  } finally { await session.close(); }
}

async function inspectStates(server, artifacts, width, height) {
  const viewport = `${width}x${height}`, folder = path.join(artifacts, viewport);
  fs.mkdirSync(folder, { recursive: true });
  const session = await BrowserSession.open({ url: `${server.url}#/projects`, artifacts: folder, width, height });
  session.baseUrl = server.url;
  const captures = [], assertions = {};
  try {
    for (const [owner, modes] of Object.entries({
      produce: ['loading', 'empty', 'error', 'blocked', 'dense'],
      export: ['loading', 'empty', 'error', 'blocked', 'ready', 'dense', 'complete'],
    })) {
      for (const mode of modes) {
        const route = `${owner}?project=fixture-project${owner === 'produce' ? '&chunk=stale-1' : ''}`;
        await setMode(session, server.control, `${owner}-${mode}`, route, mode === 'loading');
        if (!await session.evaluate(`Boolean(document.querySelector('[data-${owner}-page]'))`)) {
          const state = await snapshot(session, owner);
          captures.push({ owner, mode, state });
          assertions[`${owner}-${mode}`] = false;
          return { viewport, status: 'FAIL', assertions, captures, runtimeErrors: runtimeErrors(session) };
        }
        if (mode === 'loading') { await session.screenshot(`${owner}-${mode}.png`); server.release(); await session.waitFor(`document.body.dataset.shellState === 'ready'`); }
        const state = await snapshot(session, owner); captures.push({ owner, mode, state });
        if (mode !== 'loading') await session.screenshot(`${owner}-${mode}.png`);
        assertions[`${owner}-${mode}`] = state.owner && state.overflow <= 1 && !state.injection && state.named;
      }
    }
    server.control.mode = 'produce-loading'; server.control.pending.length = 0;
    await session.client.send('Page.navigate', { url: `${server.url}#/produce?project=fixture-project` });
    await session.waitFor(`Boolean(document.querySelector('[data-page-state="loading"]'))`);
    const beforeAbort = server.control.aborted;
    await session.evaluate(`location.hash='#/projects'`);
    await session.waitFor(`document.body.dataset.destination==='projects'`);
    const deadline = Date.now() + 3000;
    while (server.control.aborted === beforeAbort && Date.now() < deadline) await wait(25);
    assertions.routeAbort = server.control.aborted > beforeAbort;
    assertions.noRuntimeErrors = runtimeErrors(session).length === 0;
    assertions.focusRestored = await session.evaluate(`document.activeElement?.matches('[data-page-heading]')`);
    server.release();
    return { viewport, status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL', assertions, captures, runtimeErrors: runtimeErrors(session) };
  } finally { await session.close(); }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required({ ...args, artifacts: args['evidence-dir'] || args.artifacts }, 'artifacts'));
  const scenario = args.scenario || 'all';
  const viewports = String(args.viewports || DEFAULT_VIEWPORTS).split(',').map((value) => value.split('x').map(Number));
  const server = await fixtureServer(); const results = [];
  try {
    for (const [width, height] of viewports) {
      if (scenario === 'states') results.push(await inspectStates(server, artifacts, width, height));
      else if (scenario === 'all') {
        results.push(await inspectScenario(server, artifacts, 'produce', width, height));
        results.push(await inspectScenario(server, artifacts, 'export', width, height));
      } else results.push(await inspectScenario(server, artifacts, scenario, width, height));
    }
  } finally { server.release(); await server.close(); }
  const report = {
    status: results.every((item) => item.status === 'PASS') ? 'PASS' : 'FAIL',
    scenario, viewports, results,
    requests: server.control.requests.filter((item) => item.path.startsWith('/api/')),
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  fs.writeFileSync(path.join(artifacts, 'action.log'), results.map((item) => `${item.viewport} ${item.status} ${json(item.assertions)}`).join('\n') + '\n');
  writeJson(path.join(artifacts, 'cleanup.json'), { serverClosed: !server.server.listening, pendingResponses: server.control.pending.length });
  process.stdout.write(`B19_T06_PRODUCE_EXPORT=${json(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => { console.error(error.stack || error); process.exitCode = 2; });
