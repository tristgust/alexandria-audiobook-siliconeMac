'use strict';

const fs = require('fs');
const http = require('http');
const path = require('path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');

const VIEWPORTS = [[1536, 1024], [1440, 1000], [1024, 768], [390, 844]];
const json = (value) => JSON.stringify(value);
const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function sourceCheck(name, ok, observed) {
  return { name, ok: Boolean(ok), observed };
}

function inspectSources(repoRoot) {
  const castPath = path.join(repoRoot, 'app/static/pages/cast.js');
  const personaPath = path.join(repoRoot, 'app/static/components/persona_visual.js');
  const stylePath = path.join(repoRoot, 'app/static/styles/pages/cast.css');
  const cast = fs.readFileSync(castPath, 'utf8');
  const persona = fs.readFileSync(personaPath, 'utf8');
  const style = fs.readFileSync(stylePath, 'utf8');
  const markers = ['voice', 'reference', 'preview', 'character', 'appearance', 'advanced']
    .map((name) => cast.indexOf(`section("${name}"`));
  const checks = [
    sourceCheck('one_roster_one_profile',
      (cast.match(/data-cast-roster/g) || []).length === 1
        && (cast.match(/data-cast-profile/g) || []).length === 1,
      'one roster marker and one profile marker'),
    sourceCheck('profile_order',
      markers.every((value) => value >= 0)
        && markers.every((value, index) => !index || value > markers[index - 1]),
      markers),
    sourceCheck('shell_lifecycle',
      /export async function mount/.test(cast)
        && /AbortController/.test(cast)
        && /return cleanup/.test(cast),
      'mount, abort, cleanup'),
    sourceCheck('real_api_paths',
      ['/api/cast', '/api/cast/characters/', '/api/save_voice_config']
        .every((endpoint) => cast.includes(endpoint)),
      'cast detail and save paths'),
    sourceCheck('loading_empty_error_retry',
      ['loading', 'empty', 'error', 'Retry'].every((term) => cast.includes(term)),
      'four route states'),
    sourceCheck('dirty_save_retry',
      ['dirty', 'Save changes', 'saving', 'Retry save'].every((term) => cast.includes(term)),
      'dirty/save/error states'),
    sourceCheck('keyboard_listbox',
      ['role", "listbox', 'aria-selected', 'ArrowDown', 'Home', 'End']
        .every((term) => cast.includes(term)),
      'ARIA listbox keyboard model'),
    sourceCheck('safe_dom',
      cast.includes('textContent') && persona.includes('textContent')
        && !cast.includes('innerHTML') && !persona.includes('innerHTML'),
      'text-only DOM construction'),
    sourceCheck('return_context',
      ['source:', 'return:', 'shell.routes.routeForPath'].every((term) => cast.includes(term)),
      'specialist route context'),
    sourceCheck('persona_states',
      ['disabled', 'idle', 'running', 'error', 'completed']
        .every((term) => persona.includes(`"${term}"`)),
      'exclusive Persona states'),
    sourceCheck('persona_opt_in',
      persona.includes('enabled: true') && persona.includes('Collect appearance details'),
      'explicit opt-in before discovery'),
    sourceCheck('persona_cleanup',
      persona.includes('clearTimeout') && persona.includes('signal.addEventListener'),
      'poll timer and abort cleanup'),
    sourceCheck('no_legacy_workspace',
      !cast.includes('character-workspace') && !cast.includes('character-visual-panel')
        && !persona.includes('character-visual-list'),
      'no legacy or global visual workspace'),
    sourceCheck('token_style',
      style.includes('var(--master-wide)') && style.includes('var(--master-compact)')
        && !/#[0-9a-f]{3,8}/i.test(style),
      'design tokens only'),
  ];
  return {
    status: checks.every((check) => check.ok) ? 'PASS' : 'RED',
    mode: 'source',
    checks: Object.fromEntries(checks.map((check) => [check.name, check])),
  };
}

function castCharacter(id, name, readiness = 'ready', speaking = 'speaking') {
  const nonSpeaking = speaking === 'non_speaking';
  const voiceValid = nonSpeaking || readiness !== 'needs_voice';
  const previewApproved = nonSpeaking || readiness === 'ready';
  return {
    character_id: id, display_name: name, canonical_name: name,
    speaking_role: speaking, required_for_completion: !nonSpeaking,
    readiness_state: readiness, voice_summary: voiceValid ? 'Avery · measured alto' : 'No production Voice',
    blocker_count: voiceValid ? 0 : 1,
    blockers: voiceValid ? [] : [{ code: 'cast_voice_missing', title: 'Missing voice', blocking: true }],
    identity: {
      stable_character_id: id, display_name: name, canonical_name: name,
      aliases: id === 'cast:clara' ? ['C. Leighton'] : [], role: nonSpeaking ? 'Silent witness' : 'Principal',
      speaking_state: speaking, species_or_type: 'Human', relationships: ['Connected to the household'],
      source_confidence: 'high', script_voice_label: name,
    },
    script_connection: {
      resolved_script_voice_label: name, mapping_method: 'exact', mapping_confidence: 'high',
      ambiguity_state: 'resolved', script_line_count: nonSpeaking ? 0 : 18,
      representative_lines: ['A fixture line supported by the script.'],
    },
    voice: {
      selected_production_method: 'custom', selected_backend: 'local',
      selected_voice: voiceValid ? 'Avery' : null,
      clone: {
        reference_source: 'owned recording',
        exact_reference_transcript: 'I knew the letter would arrive before dusk.',
        reference_audio_state: 'ready',
      },
      persistent_voice_description: 'Measured, warm, and exact.',
      representative_text: 'I knew the letter would arrive before dusk.',
      preview: { status: previewApproved ? 'approved' : 'failed', listened: previewApproved, approved: previewApproved },
      designed_voice_state: 'not_selected',
      adapter: { state: 'not_selected' }, alias: { state: 'not_selected' },
      valid: voiceValid, blockers: voiceValid ? [] : [{ code: 'cast_voice_missing' }],
    },
    character: {
      summary: {
        canonical_name: name, display_name: name, aliases: [], role: nonSpeaking ? 'Silent witness' : 'Principal',
        speaking_state: speaking, species_or_type: 'Human', relationships: ['Connected to the household'],
        source_confidence: 'high',
      },
      expanded: {
        titles: [], nicknames: [], representative_script_lines: [
          'I knew the letter would arrive before dusk.',
          '<img src=x onerror="globalThis.fixtureInjection=true">',
        ],
        script_line_count: nonSpeaking ? 0 : 18, unresolved_questions: [], conflicts: [],
      },
    },
    appearance: {
      status: 'not_started', summary: 'Dark hair, practical dress, and a weathered travelling coat.',
      stable_traits: ['Dark hair'], variants: [], conflicts: [], unknowns: [], optional: true,
    },
    advanced_voice_setup: {
      expressive_reference_state: 'not_started', owned_recording_preparation_state: 'not_started',
      dataset_state: 'not_started', adapter_training_state: 'not_started',
      compatibility_state: 'current', blockers: [], optional: true,
    },
  };
}

function roster(mode) {
  if (mode === 'empty') return [];
  if (mode === 'dense') {
    return Array.from({ length: 24 }, (_, index) => castCharacter(
      `cast:dense-${index + 1}`,
      index === 0 ? '<img src=x onerror="globalThis.fixtureInjection=true">' : `Character ${index + 1}`,
      index % 4 === 1 ? 'needs_voice' : index % 4 === 2 ? 'preview_recommended' : 'ready',
      index % 7 === 0 ? 'non_speaking' : 'speaking',
    ));
  }
  return [
    castCharacter('cast:clara', 'Clara Leighton'),
    castCharacter('cast:edmund', 'Edmund Fairfax', 'needs_voice'),
    castCharacter('cast:isobel', 'Isobel Marwell', 'preview_recommended'),
    castCharacter('cast:witness', 'The Witness', 'ready', 'non_speaking'),
  ];
}

function castPayload(control, url) {
  let characters = roster(control.mode);
  const filter = url.searchParams.get('filter') || 'all';
  const search = (url.searchParams.get('search') || '').toLowerCase();
  const all = characters;
  if (search) characters = characters.filter((item) => item.display_name.toLowerCase().includes(search));
  if (filter === 'needs_attention') characters = characters.filter((item) => item.readiness_state !== 'ready');
  if (filter === 'unassigned') characters = characters.filter((item) => item.required_for_completion && !item.voice.valid);
  if (filter === 'speaking_roles') characters = characters.filter((item) => item.speaking_role === 'speaking');
  if (filter === 'non_speaking') characters = characters.filter((item) => item.speaking_role === 'non_speaking');
  if (filter === 'ready') characters = characters.filter((item) => item.readiness_state === 'ready');
  const requested = url.searchParams.get('selected_character_id');
  const selected = all.find((item) => item.character_id === requested)
    || all.find((item) => item.character_id === control.selected)
    || all[0] || null;
  const required = all.filter((item) => item.required_for_completion);
  const blockers = required.reduce((sum, item) => sum + item.blocker_count, 0);
  return {
    schema_version: 1,
    summary: {
      state: blockers ? 'blocked' : all.length ? 'complete' : 'not_started',
      character_count: all.length, required_speaking_count: required.length,
      ready_required_count: required.filter((item) => item.readiness_state === 'ready').length,
      blocker_count: blockers, complete: all.length > 0 && blockers === 0,
    },
    filters: {
      active: filter, search,
      counts: {
        all: all.length, needs_attention: all.filter((item) => item.readiness_state !== 'ready').length,
        unassigned: all.filter((item) => item.required_for_completion && !item.voice.valid).length,
        speaking_roles: all.filter((item) => item.speaking_role === 'speaking').length,
        non_speaking: all.filter((item) => item.speaking_role === 'non_speaking').length,
        ready: all.filter((item) => item.readiness_state === 'ready').length,
      },
    },
    characters, selected_character_id: selected?.character_id || null, selected_character: selected,
    selection_visible: Boolean(selected && characters.some((item) => item.character_id === selected.character_id)),
    blockers: [], fingerprints: { script: 'fixture-script', roster: 'fixture-roster', voice_config: 'fixture-voice' },
  };
}

function visualStatus(control) {
  const state = control.visual;
  const entry = {
    entry_id: control.selected, canonical_name: 'Clara Leighton', display_name: 'Clara Leighton',
    entity_kind: 'character', status: state === 'complete' ? 'complete' : state === 'error' ? 'invalid' : 'absent',
    observation_count: state === 'complete' ? 4 : 0, variant_count: 0, conflict_count: 0,
    image_prompt_summary: state === 'complete' ? 'Dark hair and a weathered travelling coat.' : null,
    error: state === 'error' ? 'Fixture dossier invalid.' : null,
  };
  return {
    enabled_by_default: false, approved_roster_available: state !== 'disabled',
    context_error: state === 'disabled' ? 'Fixture roster unavailable.' : null,
    process: { running: state === 'running' },
    progress: {
      status: state === 'running' ? 'running' : state === 'complete' ? 'complete' : 'idle',
      completed_passages: state === 'running' ? 2 : state === 'complete' ? 6 : 0,
      total_passages: 6,
    },
    complete_count: state === 'complete' ? 1 : 0, absent_count: state === 'idle' ? 1 : 0,
    invalid_count: state === 'error' ? 1 : 0, entries: [entry],
  };
}

function fixtureServer(repoRoot) {
  const staticRoot = path.join(repoRoot, 'app/static');
  const control = {
    mode: 'normal', visual: 'idle', selected: 'cast:clara',
    requests: [], pending: [], aborted: 0, savedVoice: 'Avery',
  };
  const projectsModule = `export async function mount({root}){const a=document.createElement('article');a.dataset.projectsPage='';const h=document.createElement('h1');h.dataset.pageHeading='';h.textContent='Project Home';a.append(h);root.replaceChildren(a);}`;
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, 'http://fixture.invalid');
    const receipt = { method: request.method, path: url.pathname, body: null, completed: false };
    control.requests.push(receipt);
    request.once('aborted', () => { if (!receipt.completed) control.aborted += 1; });
    response.once('close', () => { if (!receipt.completed) control.aborted += 1; });
    const finish = (status, value = '', type = 'text/plain; charset=utf-8') => {
      if (response.destroyed || response.writableEnded) return;
      response.writeHead(status, { 'content-type': type, 'cache-control': 'no-store' });
      response.end(request.method === 'HEAD' ? '' : value);
      receipt.completed = true;
    };
    if (url.pathname.startsWith('/api/')) {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      if (chunks.length) receipt.body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (url.pathname === '/api/cast') {
        if (control.mode === 'error') return finish(503, json({ detail: 'Fixture Cast unavailable.' }), 'application/json');
        if (control.mode === 'loading') {
          control.pending.push(() => finish(200, json(castPayload(control, url)), 'application/json'));
          return;
        }
        return finish(200, json(castPayload(control, url)), 'application/json');
      }
      if (url.pathname.startsWith('/api/cast/characters/')) {
        const id = decodeURIComponent(url.pathname.slice('/api/cast/characters/'.length));
        const character = roster(control.mode).find((item) => item.character_id === id) || roster('normal')[0];
        character.voice.selected_voice = control.savedVoice;
        control.selected = character.character_id;
        return finish(200, json(character), 'application/json');
      }
      if (url.pathname === '/api/save_voice_config') {
        if (control.mode === 'save-error') return finish(500, json({ detail: 'Fixture save failed.' }), 'application/json');
        const update = Object.values(receipt.body || {})[0] || {};
        control.savedVoice = update.voice || control.savedVoice;
        return finish(200, json({ status: 'saved' }), 'application/json');
      }
      if (url.pathname === '/api/character_visuals/status') {
        if (control.visual === 'running' && control.visualReads++ > 0) control.visual = 'complete';
        return finish(200, json(visualStatus(control)), 'application/json');
      }
      if (url.pathname === '/api/character_visuals/discover') {
        control.visual = 'running'; control.visualReads = 0;
        return finish(200, json({ status: 'started', started: true }), 'application/json');
      }
      if (url.pathname === '/api/character_visuals/cancel') {
        control.visual = 'idle';
        return finish(200, json({ status: 'cancelling' }), 'application/json');
      }
      if (url.pathname.startsWith('/api/character_visuals/')) {
        return finish(200, json({
          entry_id: control.selected, canonical_name: 'Clara Leighton', display_name: 'Clara Leighton',
          visual: {
            image_prompt_summary: 'Dark hair, practical dress, and a weathered travelling coat.',
            stable_traits: ['Dark hair', '<img src=x onerror="globalThis.fixtureInjection=true">'],
            variants: ['Travelling coat in exterior scenes'], conflicts: [],
          },
        }), 'application/json');
      }
      return finish(404, json({ detail: 'Fixture endpoint missing.' }), 'application/json');
    }
    if (url.pathname === '/static/pages/projects.js') return finish(200, projectsModule, 'text/javascript; charset=utf-8');
    const relative = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\/static\//, '');
    const filename = path.resolve(staticRoot, relative);
    if (!filename.startsWith(`${staticRoot}${path.sep}`) || !fs.existsSync(filename)
      || !fs.statSync(filename).isFile()) return finish(404, 'Not found');
    const extension = path.extname(filename);
    const type = extension === '.html' ? 'text/html' : extension === '.css' ? 'text/css' : 'text/javascript';
    return finish(200, fs.readFileSync(filename), `${type}; charset=utf-8`);
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve({
      server, control, url: `http://127.0.0.1:${server.address().port}/`,
      release: () => control.pending.splice(0).forEach((send) => send()),
      close: () => new Promise((done) => {
        server.close(done);
        server.closeAllConnections?.();
      }),
    }));
  });
}

async function press(session, key) {
  await session.client.send('Input.dispatchKeyEvent', { type: 'keyDown', key });
  await session.client.send('Input.dispatchKeyEvent', { type: 'keyUp', key });
}

function runtimeErrors(session) {
  return session.client.events.filter((item) => item.method === 'Runtime.exceptionThrown'
    || (item.method === 'Runtime.consoleAPICalled' && item.params?.type === 'error'));
}

async function snapshot(session) {
  return session.evaluate(`(() => {
    const page=document.querySelector('[data-cast-page]');
    const sections=[...document.querySelectorAll('[data-cast-section]')].map(n=>n.dataset.castSection);
    const textNodes=[...(page?.querySelectorAll('*')||[])].filter(n=>n.textContent.trim()&&getComputedStyle(n).display!=='none');
    const clipped=[...(page?.querySelectorAll('.cast-roster__row,[data-cast-identity]')||[])]
      .filter(n=>n.scrollWidth>n.clientWidth+1).map(n=>n.className);
    const pageBox=page?.getBoundingClientRect();
    const controlsOutside=[...(page?.querySelectorAll('button,input,select,textarea')||[])]
      .filter(n=>{const r=n.getBoundingClientRect(),s=getComputedStyle(n);return s.display!=='none'&&!n.closest('[hidden],.cast-roster__filters')&&(r.left<pageBox.left-1||r.right>pageBox.right+1)})
      .map(n=>n.getAttribute('aria-label')||n.textContent.trim()).slice(0,10);
    return {
      page:Boolean(page), state:page?.dataset.castState, roster:document.querySelectorAll('[data-cast-roster]').length,
      profile:document.querySelectorAll('[data-cast-profile]').length,
      listboxes:page?.querySelectorAll('[role="listbox"]').length||0,
      sections, identityBefore:page?.querySelector('[data-cast-identity]')?.compareDocumentPosition(page.querySelector('[data-cast-section]'))&Node.DOCUMENT_POSITION_FOLLOWING?true:false,
      overflow:Math.max(0,document.documentElement.scrollWidth-innerWidth),
      clipped, controlsOutside,
      focused:document.activeElement?.matches('[data-page-heading]')||false,
      injection:Boolean(globalThis.fixtureInjection||page?.querySelector('img')),
      minText:textNodes.length?Math.min(...textNodes.map(n=>parseFloat(getComputedStyle(n).fontSize))):0,
      selected:page?.querySelector('[role="option"][aria-selected="true"]')?.dataset.characterId||null,
      status:document.querySelector('[data-project-actions] [data-primitive="status"]')?.textContent.trim()||'',
      unsafeLiteral:Boolean(page?.textContent.includes('<img src=x onerror=')),
      text:page?.innerText||''
    };
  })()`);
}

async function inspectViewport(server, artifacts, width, height, interactive) {
  server.control.mode = 'normal'; server.control.visual = 'idle'; server.control.selected = 'cast:clara';
  server.control.savedVoice = 'Avery'; server.control.requests.length = 0;
  const folder = path.join(artifacts, `${width}x${height}`);
  const session = await BrowserSession.open({
    url: `${server.url}#/cast?project=fixture-project&character=cast%3Aclara`,
    artifacts: folder, width, height,
  });
  const details = {};
  try {
    await session.waitFor(`document.body.dataset.shellState==='ready'&&document.querySelector('[data-cast-page]')?.dataset.castState==='ready'`);
    await session.waitFor(`document.activeElement?.matches('[data-page-heading]')`);
    const initial = await snapshot(session);
    if (width <= 640) {
      await session.evaluate(`document.querySelector('[data-cast-page]').scrollIntoView({block:'start'})`);
    }
    await session.screenshot('cast-ready.png');
    const assertions = {
      directCastOwner: initial.page,
      oneRosterOneProfile: initial.roster === 1 && initial.profile === 1 && initial.listboxes === 1,
      profileOrder: json(initial.sections) === json(['voice', 'reference', 'preview', 'character', 'appearance', 'advanced']),
      identityFirst: initial.identityBefore,
      noOverflow: initial.overflow <= 1,
      noClippedContent: initial.clipped.length === 0 && initial.controlsOutside.length === 0,
      titleFocused: initial.focused,
      safeDom: !initial.injection && initial.unsafeLiteral,
      textFloor: initial.minText >= 13,
      noRuntimeErrors: runtimeErrors(session).length === 0,
    };
    if (interactive) {
      await session.evaluate(`document.querySelector('[role="option"][aria-selected="true"]').focus()`);
      await press(session, 'ArrowDown');
      await session.waitFor(`document.querySelector('[data-cast-profile] h2')?.textContent==='Edmund Fairfax'`);
      details.keyboardSelected = await session.evaluate(`document.querySelector('[role="option"][aria-selected="true"]')?.dataset.characterId`);
      assertions.keyboardSelection = details.keyboardSelected === 'cast:edmund';
      await session.evaluate(`document.querySelector('[data-character-id="cast:clara"]').click()`);
      await session.waitFor(`document.querySelector('[data-cast-profile] h2')?.textContent==='Clara Leighton'`);

      await session.evaluate(`{const n=document.querySelector('[data-cast-assigned-voice]');n.value='Lark';n.dispatchEvent(new Event('input',{bubbles:true}))}`);
      assertions.dirtyState = await session.evaluate(`document.querySelector('[data-shell-save]')?.textContent.includes('Unsaved')&&Boolean(document.querySelector('[data-cast-save]'))`);
      await session.evaluate(`document.querySelector('[data-character-id="cast:edmund"]').click()`);
      await session.waitFor(`Boolean(document.querySelector('.dialog-layer'))`);
      details.dirtyActions = await session.evaluate(`[...document.querySelectorAll('.dialog-layer button')].map(n=>n.textContent.trim())`);
      assertions.dirtyDialog = ['Save', 'Discard', 'Cancel'].every((label) => details.dirtyActions.includes(label));
      await session.screenshot('cast-dirty-confirmation.png');
      await session.evaluate(`[...document.querySelectorAll('.dialog-layer button')].find(n=>n.textContent.trim()==='Cancel')?.click()`);
      assertions.cancelRestores = await session.evaluate(`!document.querySelector('.dialog-layer')&&document.activeElement?.dataset.characterId==='cast:edmund'`);

      server.control.mode = 'save-error';
      await session.evaluate(`document.querySelector('[data-cast-save]').click()`);
      await session.waitFor(`document.querySelector('[data-cast-save]')?.textContent.includes('Retry save')`);
      assertions.saveFailureRetains = await session.evaluate(`document.querySelector('[data-cast-assigned-voice]')?.value==='Lark'`);
      await session.screenshot('cast-save-error.png');
      server.control.mode = 'normal';
      await session.evaluate(`document.querySelector('[data-cast-save]').click()`);
      await session.waitFor(`document.querySelector('[data-shell-save]')?.textContent.trim()==='Saved'`);
      assertions.saved = server.control.savedVoice === 'Lark';

      await session.evaluate(`document.querySelector('[data-cast-preview-play]').click()`);
      assertions.previewUsesShellPlayer = await session.evaluate(`document.querySelector('[data-persistent-player]')?.dataset.state==='playing'`);

      const discoverBefore = server.control.requests.filter((item) => item.path === '/api/character_visuals/discover').length;
      await session.evaluate(`[...document.querySelectorAll('.disclosure__trigger')].find(n=>n.textContent.trim()==='More details')?.click()`);
      await session.waitFor(`document.querySelector('[data-persona-state]')?.dataset.personaState==='idle'`);
      assertions.personaNoAutoStart = server.control.requests.filter((item) => item.path === '/api/character_visuals/discover').length === discoverBefore;
      await session.evaluate(`{const n=document.querySelector('[data-persona-enable]');n.click()}`);
      await session.evaluate(`document.querySelector('[data-persona-collect]').click()`);
      await session.waitFor(`document.querySelector('[data-persona-state]')?.dataset.personaState==='running'`);
      await session.evaluate(`document.querySelector('[data-persona-state]').scrollIntoView({block:'center'})`);
      await session.screenshot('persona-running.png');
      await session.waitFor(`document.querySelector('[data-persona-state]')?.dataset.personaState==='completed'`, 7000);
      assertions.personaCompleted = true;
      assertions.personaSafe = await session.evaluate(`!globalThis.fixtureInjection&&!document.querySelector('[data-persona-state] img')`);
      await session.evaluate(`document.querySelector('[data-persona-state]').scrollIntoView({block:'center'})`);
      await session.screenshot('persona-completed.png');

      await session.evaluate(`document.querySelector('[data-cast-more]').click()`);
      details.returnContext = await session.evaluate(`document.querySelector('.popover-controller')?.dataset.returnContext||''`);
      assertions.returnContext = details.returnContext.includes('#/cast') && details.returnContext.includes('character=');
      await press(session, 'Escape');

      server.control.mode = 'error';
      await session.client.send('Page.reload');
      await session.waitFor(`document.querySelector('[data-cast-page]')?.dataset.castState==='error'`);
      assertions.errorRetry = await session.evaluate(`document.querySelector('[data-cast-retry]')?.textContent.includes('Retry')`);
      await session.screenshot('cast-error.png');

      server.control.mode = 'empty';
      await session.client.send('Page.reload');
      await session.waitFor(`document.querySelector('[data-cast-page]')?.dataset.castState==='empty'`);
      assertions.emptyReviewScript = await session.evaluate(`document.querySelector('[data-cast-page]')?.innerText.includes('Review Script')`);
      await session.screenshot('cast-empty.png');

      server.control.mode = 'dense';
      await session.client.send('Page.reload');
      await session.waitFor(`document.querySelector('[data-cast-page]')?.dataset.castState==='ready'`);
      const dense = await snapshot(session);
      assertions.denseRoster = await session.evaluate(`document.querySelectorAll('[role="option"]').length===24`);
      assertions.denseNoOverflow = dense.overflow <= 1 && !dense.injection;
      await session.screenshot('cast-dense.png');

      server.control.mode = 'normal'; server.control.visual = 'error';
      await session.client.send('Page.reload');
      await session.waitFor(`document.querySelector('[data-persona-state]')?.dataset.personaState==='error'`);
      assertions.personaError = await session.evaluate(`document.querySelector('[data-persona-retry]')?.textContent.includes('Retry')`);
      await session.screenshot('persona-error.png');

      server.control.visual = 'disabled';
      await session.client.send('Page.reload');
      await session.waitFor(`document.querySelector('[data-persona-state]')?.dataset.personaState==='disabled'`);
      assertions.personaDisabled = await session.evaluate(`document.querySelector('[data-persona-state]')?.textContent.includes('unavailable')`);
      await session.screenshot('persona-disabled.png');

      server.control.mode = 'loading'; server.control.pending.length = 0;
      const beforeAbort = server.control.aborted;
      await session.client.send('Page.reload');
      await session.waitFor(`document.querySelector('[data-cast-page]')?.dataset.castState==='loading'`);
      await session.evaluate(`location.hash='#/projects'`);
      await session.waitFor(`document.body.dataset.destination==='projects'`);
      const deadline = Date.now() + 3000;
      while (server.control.aborted === beforeAbort && Date.now() < deadline) await wait(25);
      assertions.routeAbort = server.control.aborted > beforeAbort;
      server.release();
    }
    assertions.noRuntimeErrors = runtimeErrors(session).length === 0;
    return {
      viewport: `${width}x${height}`,
      status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL',
      assertions, initial, details, runtimeErrors: runtimeErrors(session),
      screenshot: path.join(folder, 'cast-ready.png'),
    };
  } finally {
    server.release();
    await session.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required(args, 'artifacts'));
  const repoRoot = path.resolve(required(args, 'repo-root'));
  const source = inspectSources(repoRoot);
  if (args['source-only']) {
    writeJson(path.join(artifacts, 'report.json'), source);
    process.stdout.write(`B19_T06_CAST=${JSON.stringify(source)}\n`);
    if (source.status !== 'PASS') process.exitCode = 1;
    return;
  }
  const server = await fixtureServer(repoRoot);
  const results = [];
  try {
    for (const [index, [width, height]] of VIEWPORTS.entries()) {
      results.push(await inspectViewport(server, artifacts, width, height, index === 0));
    }
  } finally {
    server.release();
    await server.close();
  }
  const report = {
    status: source.status === 'PASS' && results.every((result) => result.status === 'PASS')
      ? 'PASS' : 'FAIL',
    source, viewports: VIEWPORTS, results,
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  fs.writeFileSync(path.join(artifacts, 'action.log'), `${results.map((result) => (
    `${result.viewport} ${result.status} ${json(result.assertions)}`
  )).join('\n')}\n`);
  writeJson(path.join(artifacts, 'cleanup.json'), {
    serverClosed: !server.server.listening,
    pendingResponses: server.control.pending.length,
  });
  process.stdout.write(`B19_T06_CAST=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.stack || error);
    process.exitCode = 2;
  });
}

module.exports = { inspectSources };
