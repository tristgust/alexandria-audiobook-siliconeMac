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
          native_route: { destination: 'script', context: { project: project.id } }, provenance: { format: 'EPUB' },
          delete: { supported: false, blocked: true }, usage: [], fingerprint: 'source-fingerprint' },
        { artifact_id: 'audio-1', kind: 'production_audio', name: 'Chapter 1 production', state: 'current',
          native_route: { destination: 'produce', context: { project: project.id } }, provenance: { format: 'WAV' },
          delete: { supported: false, blocked: true }, usage: [], fingerprint: 'audio-fingerprint' },
        { artifact_id: 'dataset-1', kind: 'dataset_builder_project', name: 'Meridian Voice Dataset', state: 'available',
          native_route: { destination: 'more', context: { project: project.id } }, provenance: { records: 42 },
          delete: { supported: true, blocked: false }, usage: [], fingerprint: 'dataset-fingerprint' },
      ],
    },
    voices: {
      assignment_mutation_supported: false, cast_is_authoritative: true,
      methods: [
        { method: 'built_in', description: 'Pinned local speaker.', production_supported: true,
          preview_supported: true, instruction_supported: false },
        { method: 'supplied_recording', description: 'Identity-preserving supplied recording.', production_supported: true,
          preview_supported: true, instruction_supported: false },
      ],
      voices: [
        { id: 'voice-1', name: 'Benny', method: 'built_in', method_label: 'Built-in Voice',
          description: 'Warm, articulate narration.', state: 'available', preview: { available: false }, usage: [] },
        { id: 'voice-2', name: 'Meridian reference', method: 'supplied_recording',
          method_label: 'Supplied recording', description: 'Identity-preserving reference.',
          state: 'current', preview: { available: false }, usage: [{
            character_id: 'character_mara', name: 'Mara',
            cast_route: { hash: `#/cast?project=${project.id}&character=character_mara&source=voice-library&return=%23%2Fvoices` },
          }] },
      ],
    },
    templates: {
      catalog_fingerprint: 'templates-1', default_template_id: 'builtin_standard',
      summary: { template_count: 2 },
      templates: [
        { id: 'builtin_standard', name: 'Standard production', description: 'Balanced local production.',
          intent: 'Reliable local audiobook', generation_method: 'local', preset: 'standard',
          source_language: 'English', output_language: 'English', built_in: true, default: true,
          editable: false, duplicable: true, deletable: false, fingerprint: 't1' },
        { id: 'custom_fidelity', name: 'Maximum fidelity', description: 'More deliberate review.',
          intent: 'Detailed publication review', generation_method: 'local', preset: 'maximum_fidelity',
          source_language: 'English', output_language: 'English', built_in: false, default: false,
          editable: true, duplicable: true, deletable: true, fingerprint: 't2' },
      ],
    },
  };
}

async function fixtureServer() {
  const data = fixtureData();
  const control = { scriptIssues: false };
  const requests = [];
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, 'http://fixture.invalid');
    requests.push(`${request.method} ${url.pathname}`);
    let requestBody = null;
    if (!['GET', 'HEAD'].includes(request.method)) {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const raw = Buffer.concat(chunks).toString('utf8');
      if (raw && String(request.headers['content-type'] || '').includes('application/json')) {
        requestBody = JSON.parse(raw);
      }
    }
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
    if (/^\/api\/projects\/[^/]+\/duplicate$/.test(url.pathname)) return json({
      project: { ...data.project, id: 'project_meridian_copy', name: 'The Meridian Archive copy' },
      catalog_fingerprint: 'catalog-2',
    });
    if (url.pathname === '/__fixture/script-issues') {
      control.scriptIssues = url.searchParams.get('enabled') === '1';
      return json({ enabled: control.scriptIssues });
    }
    if (url.pathname === '/api/project_flow/status') return json(data.flow);
    if (url.pathname === '/api/script_lifecycle/status') {
      data.lifecycle.accepted = false;
      data.lifecycle.state = 'review_required';
      data.lifecycle.blockers = control.scriptIssues ? data.scriptIssues : [];
      return json(data.lifecycle);
    }
    if (url.pathname === '/api/annotated_script') return json(data.entries);
    if (url.pathname === '/api/script_lifecycle/accept') {
      data.lifecycle.accepted = true; data.lifecycle.state = 'accepted'; data.lifecycle.blockers = [];
      return json(data.lifecycle);
    }
    if (url.pathname === '/api/library') return json(data.library);
    if (/^\/api\/library\/artifacts\/[^/]+\/delete-impact$/.test(url.pathname)) {
      const artifactId = decodeURIComponent(url.pathname.split('/')[4]);
      const artifact = data.library.artifacts.find((item) => item.artifact_id === artifactId);
      return json({
        artifact_id: artifactId,
        artifact_fingerprint: artifact?.fingerprint,
        inventory_fingerprint: data.library.inventory_fingerprint,
        kind: artifact?.kind,
        key: artifactId,
        name: artifact?.name,
        supported: Boolean(artifact?.delete?.supported),
        blocked: Boolean(artifact?.delete?.blocked),
        blockers: artifact?.usage || [],
        reason: artifact?.delete?.blocked ? 'Repair dependencies before deletion.' : null,
        confirm_name: artifact?.name,
        safe_to_delete: Boolean(artifact?.delete?.supported && !artifact?.delete?.blocked),
      });
    }
    if (/^\/api\/library\/artifacts\/[^/]+$/.test(url.pathname) && request.method === 'DELETE') {
      const artifactId = decodeURIComponent(url.pathname.split('/')[4]);
      data.library.artifacts = data.library.artifacts.filter((item) => item.artifact_id !== artifactId);
      data.library.inventory_fingerprint = 'inventory-2';
      return json({ status: 'deleted', artifact_id: artifactId });
    }
    if (url.pathname === '/api/voice-library') return json(data.voices);
    if (/^\/api\/templates\/[^/]+\/duplicate$/.test(url.pathname)) {
      const templateId = decodeURIComponent(url.pathname.split('/')[3]);
      const source = data.templates.templates.find((item) => item.id === templateId);
      const copy = {
        ...source,
        id: `custom_copy_${data.templates.templates.length}`,
        name: requestBody?.name || `${source.name} copy`,
        built_in: false,
        default: false,
        editable: true,
        duplicable: true,
        deletable: true,
        fingerprint: `copy-${Date.now()}`,
      };
      data.templates.templates.push(copy);
      data.templates.catalog_fingerprint = 'templates-2';
      return json({ ...data.templates, template: copy, duplicated_from: templateId });
    }
    if (/^\/api\/templates\/[^/]+\/default$/.test(url.pathname)) {
      const templateId = decodeURIComponent(url.pathname.split('/')[3]);
      data.templates.default_template_id = templateId;
      data.templates.templates.forEach((item) => { item.default = item.id === templateId; });
      data.templates.catalog_fingerprint = 'templates-3';
      return json(data.templates);
    }
    if (/^\/api\/templates\/[^/]+\/delete-impact$/.test(url.pathname)) {
      const templateId = decodeURIComponent(url.pathname.split('/')[3]);
      const template = data.templates.templates.find((item) => item.id === templateId);
      return json({
        template,
        catalog_fingerprint: data.templates.catalog_fingerprint,
        usage: [{ project_id: data.project.id, name: data.project.name, blocking: false }],
        usage_count: 1,
        blocking_reasons: [],
        safe_to_delete: true,
        requires_usage_acknowledgement: true,
        confirmation_text: template.name,
        message: 'Deleting this template does not rewrite existing projects.',
      });
    }
    if (/^\/api\/templates\/[^/]+$/.test(url.pathname) && request.method === 'PUT') {
      const templateId = decodeURIComponent(url.pathname.split('/')[3]);
      const index = data.templates.templates.findIndex((item) => item.id === templateId);
      const updated = {
        ...data.templates.templates[index],
        ...(requestBody?.template || {}),
        fingerprint: `edited-${Date.now()}`,
      };
      data.templates.templates[index] = updated;
      data.templates.catalog_fingerprint = 'templates-4';
      return json({ ...data.templates, template: updated });
    }
    if (/^\/api\/templates\/[^/]+$/.test(url.pathname) && request.method === 'DELETE') {
      const templateId = decodeURIComponent(url.pathname.split('/')[3]);
      data.templates.templates = data.templates.templates.filter((item) => item.id !== templateId);
      data.templates.catalog_fingerprint = 'templates-5';
      return json({ ...data.templates, deleted_template_id: templateId });
    }
    if (url.pathname === '/api/templates' && request.method === 'POST') {
      const created = {
        id: `custom_created_${data.templates.templates.length}`,
        ...(requestBody?.template || {}),
        built_in: false,
        default: false,
        editable: true,
        duplicable: true,
        deletable: true,
        fingerprint: `created-${Date.now()}`,
      };
      data.templates.templates.push(created);
      data.templates.catalog_fingerprint = 'templates-6';
      return json({ ...data.templates, template: created });
    }
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
            titleTargets: root?.querySelectorAll('.project-list__title').length || 0,
            coverTargets: root?.querySelectorAll('.project-list__cover-action').length || 0,
            contextTargets: root?.querySelectorAll('.project-list__context-link').length || 0,
            overflowMenus: root?.querySelectorAll('.project-list__overflow').length || 0,
            playerAbsent: Boolean(document.querySelector('[data-persistent-player]')?.hidden),
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
          titleTargets: 1,
          coverTargets: 1,
          contextTargets: 1,
          overflowMenus: 1,
          playerAbsent: true,
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
            sourceState: dialog?.querySelector('.new-project__source-state')?.textContent || '',
            sourceFacts: dialog?.querySelectorAll('.new-project__source-facts > div').length || 0,
            fileAction: dialog?.querySelector('.new-project__file-action')?.textContent || '',
            optionDescriptions: dialog?.querySelectorAll('.choice__description').length || 0,
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
        assert.equal(dialogObserved.sourceState, 'TEXT file selected');
        assert.equal(dialogObserved.sourceFacts, 4);
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
        await session.waitFor(`Boolean([...document.querySelectorAll('[data-new-project] button')].find((button) => button.textContent.includes('Create Project'))?.disabled)`);
        actions.push({ viewport, action: 'New Project validates source and method requirements', pass: true });
        await session.screenshot('new-project.png');
        captures.push({ viewport, page: 'new-project', observed: dialogObserved,
          screenshot: path.join(artifacts, 'new-project.png') });
        await session.evaluate(`document.querySelector('[data-new-project-close]').click()`);
        await session.waitFor(`Boolean(document.querySelector('[data-new-project-discard]'))`);
        await session.evaluate(`[...document.querySelectorAll('[data-new-project-discard] button')].find((button) => button.textContent === 'Discard').click()`);
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
        assert.match(denseScript.footer.replaceAll(',', ''), /Showing 1–120 of 5275 entries/);
        assert.ok(denseScript.scrollHeight < 50000, `Script DOM was not bounded: ${denseScript.scrollHeight}`);
        await session.evaluate(`document.querySelector('[data-script-load-more]').click()`);
        await session.waitFor(`document.querySelectorAll('.script-entry').length === 240`);
        const searchInput = `document.querySelector('.script-review input[type="search"]')`;
        await session.evaluate(`(() => { const input=${searchInput}; input.value='Script entry 5275'; input.dispatchEvent(new Event('input',{bubbles:true})); })()`);
        await session.waitFor(`document.querySelectorAll('.script-entry').length === 1`);
        await session.evaluate(`(() => { const input=${searchInput}; input.value=''; input.dispatchEvent(new Event('input',{bubbles:true})); })()`);
        await session.waitFor(`document.querySelectorAll('.script-entry').length === 120`);
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
          const inspector = document.querySelector('[data-shell-inspector]');
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
        await session.evaluate(`[...document.querySelectorAll('[data-shell-inspector] button')].find((button) => button.textContent === 'Review speaker correction').click()`);
        await session.waitFor(`document.querySelector('[data-script-workflow="generation"] .disclosure__trigger').getAttribute('aria-expanded') === 'true'`);
        await session.evaluate(`document.querySelector('[data-script-filter="source_mismatch"]').click()`);
        await session.waitFor(`document.querySelectorAll('.script-entry').length === 1`);
        assert.match(await session.evaluate(`document.querySelector('[data-shell-inspector]')?.textContent || ''`), /Script text does not match the source/);
        actions.push({ viewport, action: 'Script issue filters, comparison, and correction routing', pass: true });

        await session.evaluate(`fetch('/__fixture/script-issues?enabled=0',{method:'POST'})`);
        await session.evaluate(`AlexandriaShell.navigate('#/projects')`);
        await session.waitFor(`document.body.dataset.routePath === 'projects'`);
        await session.evaluate(`AlexandriaShell.navigate('#/script')`);
        await session.waitFor(`document.body.dataset.routePath === 'script' && Boolean(document.querySelector('[data-script-approve]:not(:disabled)'))`);
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
        if (width === 1536) {
          await session.evaluate(`AlexandriaShell.navigate('#/voices')`);
          await session.waitFor(`document.body.dataset.routePath === 'voices'`);
          await session.evaluate(`[
            ...document.querySelectorAll('.supporting-list__button')
          ].find((button) => button.textContent.includes('Meridian reference')).click()`);
          await session.evaluate(`[
            ...document.querySelectorAll('.supporting-detail button')
          ].find((button) => button.textContent === 'Open usage in Cast').click()`);
          await session.waitFor(`document.body.dataset.routePath === 'cast'`);
          assert.match(await session.evaluate(`location.hash`), /character=character_mara.*source=voice-library/);
          actions.push({ viewport, action: 'Voices uses the authoritative Cast usage route', pass: true });

          await session.evaluate(`AlexandriaShell.navigate('#/library')`);
          await session.waitFor(`document.body.dataset.routePath === 'library'`);
          await session.evaluate(`[
            ...document.querySelectorAll('.supporting-list__button')
          ].find((button) => button.textContent.includes('Meridian Voice Dataset')).click()`);
          await session.waitFor(`Boolean(document.querySelector('[data-library-delete-review="dataset-1"]'))`);
          await session.evaluate(`document.querySelector('[data-library-delete-review="dataset-1"]').click()`);
          await session.waitFor(`Boolean(document.querySelector('.dialog-layer'))`);
          await session.evaluate(`(() => {
            const input = document.querySelector('.dialog-layer input');
            input.value = 'Meridian Voice Dataset';
            input.dispatchEvent(new Event('input', { bubbles: true }));
          })()`);
          await session.evaluate(`document.querySelector('.dialog-layer .ui-button[data-variant="destructive"]').click()`);
          await session.waitFor(`![...document.querySelectorAll('.supporting-list__button')]
            .some((button) => button.textContent.includes('Meridian Voice Dataset'))`);
          assert.ok(fixture.requests.includes('DELETE /api/library/artifacts/dataset-1'));
          actions.push({ viewport, action: 'Library deletion requires reviewed impact and exact name', pass: true });

          await session.evaluate(`AlexandriaShell.navigate('#/templates')`);
          await session.waitFor(`document.body.dataset.routePath === 'templates'`);
          await session.evaluate(`[
            ...document.querySelectorAll('.supporting-list__button')
          ].find((button) => button.textContent.includes('Maximum fidelity')).click()`);
          await session.evaluate(`[
            ...document.querySelectorAll('.template-actions button')
          ].find((button) => button.textContent === 'Edit').click()`);
          await session.waitFor(`document.querySelector('.template-editor h2')?.textContent === 'Edit Template'`);
          await session.evaluate(`(() => {
            const input = document.querySelector('.template-editor input[name="name"]');
            input.value = 'Maximum fidelity revised';
            input.dispatchEvent(new Event('input', { bubbles: true }));
            document.querySelector('.template-editor form').requestSubmit();
          })()`);
          await session.waitFor(`[...document.querySelectorAll('.supporting-list__button')]
            .some((button) => button.textContent.includes('Maximum fidelity revised'))`);
          actions.push({ viewport, action: 'Template editor updates custom templates through PUT', pass: true });

          await session.evaluate(`[
            ...document.querySelectorAll('.template-actions button')
          ].find((button) => button.textContent === 'Duplicate').click()`);
          await session.waitFor(`Boolean(document.querySelector('.dialog-layer input'))`);
          await session.evaluate(`document.querySelector('.dialog-layer .ui-button[data-variant="primary"]').click()`);
          try {
            await session.waitFor(`[...document.querySelectorAll('.supporting-list__button')]
              .some((button) => button.textContent.includes('Maximum fidelity revised copy'))`);
          } catch (error) {
            const diagnostic = await session.evaluate(`(() => ({
              dialog: document.querySelector('.dialog-layer')?.innerText || '',
              templates: [...document.querySelectorAll('.supporting-list__button')].map((button) => button.textContent.trim()),
              feedback: document.querySelector('.template-actions .transaction-status')?.textContent || '',
            }))()`);
            console.error(`TEMPLATE_DUPLICATE_DIAGNOSTIC=${JSON.stringify({ diagnostic, requests: fixture.requests.slice(-20) })}`);
            throw error;
          }
          actions.push({ viewport, action: 'Templates duplicate through the modular action API', pass: true });

          await session.evaluate(`[
            ...document.querySelectorAll('.template-actions button')
          ].find((button) => button.textContent === 'Set as Default').click()`);
          await session.waitFor(`document.querySelector('.supporting-detail > .metadata')?.textContent === 'Default template'`);
          actions.push({ viewport, action: 'Template default selection refreshes the catalog', pass: true });

          await session.evaluate(`[
            ...document.querySelectorAll('.supporting-list__button')
          ].find((button) => button.textContent.includes('Maximum fidelity revised')
            && !button.textContent.includes('copy')).click()`);
          await session.evaluate(`[
            ...document.querySelectorAll('.template-actions button')
          ].find((button) => button.textContent === 'Review deletion').click()`);
          await session.waitFor(`Boolean(document.querySelector('.dialog-layer'))`);
          await session.evaluate(`(() => {
            const layer = document.querySelector('.dialog-layer');
            const input = layer.querySelector('input[type="text"]');
            input.value = 'Maximum fidelity revised';
            input.dispatchEvent(new Event('input', { bubbles: true }));
            const check = layer.querySelector('input[type="checkbox"]');
            check.checked = true;
            check.dispatchEvent(new Event('change', { bubbles: true }));
          })()`);
          await session.evaluate(`document.querySelector('.dialog-layer .ui-button[data-variant="destructive"]').click()`);
          await session.waitFor(`![...document.querySelectorAll('.supporting-list__button')]
            .some((button) => button.textContent.includes('Maximum fidelity revised')
              && !button.textContent.includes('copy'))`);
          actions.push({ viewport, action: 'Template deletion requires usage acknowledgement and exact name', pass: true });
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
