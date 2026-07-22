'use strict';

const {
  inspectBoundary13FinalAcceptance,
} = require('./boundary13_final_acceptance.js');

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index === -1 || !process.argv[index + 1]) {
    throw new Error(`${name} is required`);
  }
  return process.argv[index + 1];
}

function optionalArgument(name, fallback) {
  const index = process.argv.indexOf(name);
  return index === -1 || !process.argv[index + 1]
    ? fallback
    : process.argv[index + 1];
}

class CdpClient {
  constructor(webSocketUrl) {
    this.socket = new WebSocket(webSocketUrl);
    this.nextId = 1;
    this.pending = new Map();
    this.opened = new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, { once: true });
      this.socket.addEventListener('error', reject, { once: true });
    });
    this.socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (!message.id) return;
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(JSON.stringify(message.error)));
      else pending.resolve(message.result || {});
    });
  }

  async send(method, params = {}) {
    await this.opened;
    const id = this.nextId++;
    const promise = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.socket.send(JSON.stringify({ id, method, params }));
    return promise;
  }

  close() {
    this.socket.close();
  }
}

async function wait(milliseconds) {
  await new Promise(resolve => setTimeout(resolve, milliseconds));
}

async function evaluate(client, expression) {
  const result = await client.send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (result.exceptionDetails) {
    throw new Error(JSON.stringify(result.exceptionDetails));
  }
  return result.result && result.result.value;
}

async function writeScreenshot(client, filepath) {
  const capture = await client.send('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: false,
    fromSurface: true,
  });
  const bytes = Buffer.from(capture.data, 'base64');
  require('fs').writeFileSync(filepath, bytes);
  return bytes.length;
}

async function inspectTab(client, tab, width, height, screenshotPath) {
  const canonicalTab = ['voices', 'voice-projects'].includes(tab)
    ? 'characters'
    : tab;
  await client.send('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor: 1,
    mobile: false,
  });

  const routeSelector = ({
    setup: '[data-tab="setup"][data-route="settings"]',
    designer: '[data-tab="designer"][data-route="more"][data-route-tool="voice-designer"]',
    'speaker-management': '[data-tab="speaker-management"][data-route="more"][data-route-tool="advanced-character-operations"]',
    preparer: '[data-tab="preparer"][data-route="more"][data-route-tool="audio-preparer"]',
    'dataset-builder': '[data-tab="dataset-builder"][data-route="more"][data-route-tool="dataset-builder"]',
    training: '[data-tab="training"][data-route="more"][data-route-tool="voice-training"]',
    'project-recovery': '[data-tab="project-recovery"][data-route="more"][data-route-tool="maintenance"]',
  })[canonicalTab] || `[data-tab="${canonicalTab}"]`;
  await evaluate(client, `(() => {
    const target = document.querySelector(${JSON.stringify(routeSelector)});
    if (!target) throw new Error('Missing route ${canonicalTab}');
    target.click();
    window.scrollTo(0, 0);
  })()`);
  await wait(500);
  if (canonicalTab === 'characters') {
    await evaluate(client, `(async () => {
      const entry = (window.voiceTrainingStatus?.entries || []).find(
        item => item.canonical_name === 'THE DOCTOR'
      );
      if (entry && window.voiceTrainingSelectedId !== entry.character_id) {
        await selectVoiceTrainingCharacter(entry.character_id);
      }
    })()`);
    await wait(250);
  }

  const result = await evaluate(client, `(() => {
    const visible = element => {
      if (!element) return false;
      const closedDetails = element.closest('details:not([open])');
      const visibleSummary = element.closest('summary');
      if (
        closedDetails
        && (!visibleSummary || visibleSummary.parentElement !== closedDetails)
      ) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    const tab = document.getElementById('${canonicalTab}-tab');
    const rect = element => {
      if (!element) return null;
      const r = element.getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom };
    };
    const controls = [...tab.querySelectorAll('input, select, textarea')].filter(visible);
    const enabledControls = controls.filter(control => !control.disabled && !control.readOnly);
    const disabledControls = controls.filter(control => control.disabled || control.readOnly);
    const buttons = [...tab.querySelectorAll('button, a.btn')].filter(visible);
    const primary = buttons.filter(button => button.classList.contains('btn-primary'));
    const unlabeledIconButtons = buttons.filter(button => {
      const text = button.textContent.trim();
      const name = button.getAttribute('aria-label') || button.getAttribute('title');
      return !text && !name;
    });
    const headings = [...tab.querySelectorAll('h1, h2, h3, h4, h5, h6')]
      .filter(visible)
      .map(element => ({
        tag: element.tagName,
        text: element.textContent.trim().replace(/\\s+/g, ' '),
        size: getComputedStyle(element).fontSize,
        weight: getComputedStyle(element).fontWeight,
      }));
    const visibleCards = [...tab.querySelectorAll('.card')].filter(visible);
    const legacyStepIndicators = [...tab.querySelectorAll('.step-indicator')].filter(visible);
    const visibleBadges = [...tab.querySelectorAll('.badge')].filter(visible);
    const activeMasterRows = [...tab.querySelectorAll(
      '.visual-character-row.is-active, .voice-project-row.is-active, .llm-profile-row.is-active'
    )].filter(visible);
    const activeInsetShadowCount = activeMasterRows.filter(row =>
      getComputedStyle(row).boxShadow.includes('inset')
    ).length;
    const helperTexts = [...tab.querySelectorAll('.form-text, small.text-muted')].filter(visible);
    const filePickers = [...tab.querySelectorAll('[data-file-picker]')].filter(visible);
    const visibleNativeFileInputs = [...tab.querySelectorAll('input[type="file"]')]
      .filter(visible)
      .filter(input => !input.classList.contains('file-picker-input'));
    const tableState = [...tab.querySelectorAll('.table-state')].find(visible);
    const loadingTableRows = [...tab.querySelectorAll('tbody tr')]
      .filter(visible)
      .filter(row => /loading/i.test(row.textContent));
    const horizontalOverflow = document.documentElement.scrollWidth > window.innerWidth + 1;
    const outOfBounds = [...tab.querySelectorAll('button, input, select, textarea, table')]
      .filter(visible)
      .map(element => ({ element, rect: element.getBoundingClientRect() }))
      .filter(item => item.rect.left < -1 || item.rect.right > window.innerWidth + 1)
      .slice(0, 12)
      .map(item => ({
        tag: item.element.tagName,
        id: item.element.id || null,
        className: String(item.element.className || ''),
        left: item.rect.left,
        right: item.rect.right,
      }));
    const firstEnabled = enabledControls[0];
    const firstDisabled = disabledControls[0];
    const recoveryDetails = tab.id === 'setup-tab'
      ? document.getElementById('recovery-center')
      : null;
    const recoverySummary = recoveryDetails?.querySelector(':scope > summary') || null;
    const recoveryBody = recoveryDetails?.querySelector('.recovery-center-body') || null;
    return {
      tab: '${tab}',
      viewport: { width: window.innerWidth, height: window.innerHeight },
      tabRect: rect(tab),
      horizontalOverflow,
      outOfBounds,
      visibleCards: visibleCards.length,
      visibleBadges: visibleBadges.length,
      activeInsetShadowCount,
      legacyStepIndicators: legacyStepIndicators.length,
      helperTextCount: helperTexts.length,
      filePickerCount: filePickers.length,
      visibleNativeFileInputCount: visibleNativeFileInputs.length,
      visibleTableState: tableState ? tableState.dataset.state || 'visible' : null,
      loadingTableRowCount: loadingTableRows.length,
      primaryActionCount: primary.length,
      primaryActions: primary.map(button => button.textContent.trim().replace(/\\s+/g, ' ')),
      visibleButtonCount: buttons.length,
      unlabeledIconButtonCount: unlabeledIconButtons.length,
      unlabeledIconButtons: unlabeledIconButtons.slice(0, 12).map(button => ({
        id: button.id || null,
        className: String(button.className || ''),
      })),
      headings,
      recovery: recoveryDetails ? {
        open: recoveryDetails.open,
        summaryRect: rect(recoverySummary),
        bodyVisible: visible(recoveryBody),
        overallState: document.getElementById('recovery-overall-light')?.dataset.state || null,
        overallText: document.getElementById('recovery-overall-text')?.textContent.trim() || null,
        overallCount: document.getElementById('recovery-overall-count')?.textContent.trim() || null,
        overallColor: getComputedStyle(
          document.getElementById('recovery-overall-light')
        ).backgroundColor,
      } : null,
      externalWorkflow: tab.id === 'script-tab' ? (() => {
        const details = document.getElementById('script-external-workflow');
        const candidate = document.getElementById('external-script-candidate');
        const visuallyExposedFileInput = input => {
          if (!visible(input)) return false;
          const style = getComputedStyle(input);
          return style.opacity !== '0'
            && style.clipPath === 'none'
            && style.clip === 'auto';
        };
        return {
          open: details?.open ?? null,
          status: document.getElementById('external-workflow-status')?.textContent.trim() || null,
          candidateVisible: visible(candidate),
          structuredVisible: visible(
            document.getElementById('external-structured-result')
          ),
          resultInputVisible: visuallyExposedFileInput(
            document.getElementById('completed-task-file')
          ),
          importInputVisible: visuallyExposedFileInput(
            document.getElementById('external-annotated-script-file')
          ),
        };
      })() : null,
      colors: {
        body: getComputedStyle(document.body).backgroundColor,
        surface: (() => {
          const surface = tab.querySelector('.workflow-surface, .workspace-section, .utility-disclosure');
          return surface ? getComputedStyle(surface).backgroundColor : null;
        })(),
        enabledControl: firstEnabled ? getComputedStyle(firstEnabled).backgroundColor : null,
        disabledControl: firstDisabled ? getComputedStyle(firstDisabled).backgroundColor : null,
        primary: primary[0] ? getComputedStyle(primary[0]).backgroundColor : null,
      },
      typography: {
        body: getComputedStyle(document.body).fontFamily,
        bodySize: getComputedStyle(document.body).fontSize,
        controlSize: firstEnabled ? getComputedStyle(firstEnabled).fontSize : null,
        labelSize: (() => {
          const label = tab.querySelector('.form-label');
          return label ? getComputedStyle(label).fontSize : null;
        })(),
      },
      nav: {
        rect: rect(document.querySelector('.app-navbar-inner')),
        brandRect: rect(document.querySelector('.navbar-brand')),
        stagesRect: rect(document.querySelector('.app-stage-nav')),
        utilitiesRect: rect(document.querySelector('.app-nav-utilities')),
        computeText: document.getElementById('sys-gpu-val')?.textContent.trim() || null,
        computeTitle: document.getElementById('sys-gpu')?.title || null,
      },
    };
  })()`);

  result.screenshotBytes = await writeScreenshot(client, screenshotPath);
  result.screenshotPath = screenshotPath;
  return result;
}

async function installBoundary12Fixtures(client) {
  await evaluate(client, `(() => {
    if (window.__boundary12FetchInstalled) return;
    const originalFetch = window.fetch.bind(window);
    const jsonResponse = (payload, status = 200) => Promise.resolve(new Response(
      JSON.stringify(payload),
      { status, headers: { 'Content-Type': 'application/json' } },
    ));
    const makeChunk = ({ id, index, speaker, text, direction, state, reason, chapter, playable = false }) => ({
      chunk_id: id,
      index,
      speaker,
      character_name: speaker === 'NARRATOR' ? 'Narrator' : speaker,
      chapter_name: chapter,
      text,
      text_excerpt: text,
      delivery_direction: direction,
      pause_after_ms: index % 2 ? 250 : 500,
      duration_ms: playable ? 4200 + index * 100 : null,
      state,
      reason,
      selected: id === 'chunk:stale-1',
      voice: {
        valid: true,
        configuration_key: speaker,
        resolved_speaker: speaker,
        method: speaker === 'NARRATOR' ? 'custom' : 'design',
      },
      audio: {
        available: playable,
        url: playable ? '/api/chunks/' + index + '/audio' : null,
        verification_level: 'binding_and_hash',
      },
      review: {
        required: state === 'needs_review',
        listening_required: state === 'needs_listening',
        listening_state: state === 'needs_listening' ? 'pending' : null,
      },
      blockers: state === 'failed' ? [{
        code: 'produce_generation_failed',
        title: 'Audio generation failed',
        explanation: 'Retry this chunk after inspecting the generation log.',
        blocking: true,
      }] : [],
      regenerate_action: state === 'generating' ? null : {
        id: state === 'ready' ? 'generate_chunk' : 'regenerate_chunk',
        label: state === 'ready' ? 'Generate' : 'Regenerate',
      },
      technical_details: {
        recorded_audio_fingerprint: playable ? 'audio-fingerprint-' + index : null,
      },
    });
    const produceChunks = [
      makeChunk({ id: 'chunk:current-1', index: 0, speaker: 'NARRATOR', text: 'Chapter One', direction: 'Measured chapter heading.', state: 'current', chapter: 'Chapter One', playable: true }),
      makeChunk({ id: 'chunk:ready-1', index: 1, speaker: 'BERNICE', text: 'The archive doors opened before she touched them.', direction: 'Controlled unease with forward momentum.', state: 'ready', reason: 'audio_not_generated', chapter: 'Chapter One' }),
      makeChunk({ id: 'chunk:stale-1', index: 2, speaker: 'BERNICE', text: 'Something in the dark had learned her name.', direction: 'Low, precise alarm; do not rush.', state: 'stale', reason: 'audio_fingerprint_mismatch', chapter: 'Chapter One' }),
      makeChunk({ id: 'chunk:listening-1', index: 3, speaker: 'NARRATOR', text: 'Chapter Two', direction: 'Quiet transition into a colder scene.', state: 'needs_listening', reason: 'listening_required', chapter: 'Chapter Two', playable: true }),
      makeChunk({ id: 'chunk:failed-1', index: 4, speaker: 'WOLSEY', text: 'That machine is not supposed to answer back.', direction: 'Dry irritation covering real concern.', state: 'failed', reason: 'generation_failed', chapter: 'Chapter Two' }),
      makeChunk({ id: 'chunk:current-2', index: 5, speaker: 'NARRATOR', text: 'The indicator light went dark.', direction: 'Short, final, and ominous.', state: 'current', chapter: 'Chapter Two', playable: true }),
      makeChunk({ id: 'chunk:ready-2', index: 6, speaker: 'BERNICE', text: 'Do it again.', direction: 'Immediate and decisive.', state: 'ready', reason: 'audio_not_generated', chapter: 'Chapter Two' }),
    ];
    const produceCounts = {
      current: 178,
      ready: 12,
      stale: 4,
      failed: 2,
      needs_listening: 7,
      needs_review: 0,
      generating: 0,
      missing_voice: 0,
    };
    const filterCount = {
      all: 203,
      current: 178,
      ready: 12,
      stale: 4,
      failed: 2,
      needs_listening: 7,
      needs_review: 7,
      missing_voice: 0,
    };
    const producePayload = url => {
      const filter = url.searchParams.get('filter') || 'all';
      const selectedId = url.searchParams.get('selected_chunk_id') || 'chunk:stale-1';
      const filtered = filter === 'all'
        ? produceChunks
        : produceChunks.filter(chunk => (
            filter === 'needs_review'
              ? ['needs_review', 'needs_listening'].includes(chunk.state)
              : chunk.state === filter
          ));
      return {
        schema_version: 1,
        state: 'ready',
        summary: {
          required_chunk_count: 203,
          current_count: 178,
          needs_generation_count: 16,
          needs_review_count: 7,
          failed_count: 2,
          missing_voice_count: 0,
          blocker_count: 2,
          complete: false,
        },
        counts: produceCounts,
        filters: { available: Object.keys(filterCount), active: filter, search: null },
        chunks: filtered,
        all_chunk_count: 203,
        visible_chunk_count: filterCount[filter] ?? filtered.length,
        returned_chunk_count: filtered.length,
        selected_chunk_id: selectedId,
        selected_chunk: produceChunks.find(chunk => chunk.chunk_id === selectedId) || null,
        selection_visible: filtered.some(chunk => chunk.chunk_id === selectedId),
        process: {
          running: false,
          cancel_requested: false,
          total_count: 0,
          completed_count: 0,
          failed_count: 0,
          logs: [],
        },
        primary_action: {
          id: 'generate_missing_stale_audio',
          label: 'Generate missing and stale audio',
          mode: 'missing_stale',
        },
        secondary_actions: [{ id: 'regenerate_all_audio', destructive: true }],
        fingerprints: {
          aggregate: 'produce-aggregate-browser',
          chunks: 'produce-chunks-browser',
          voice_config: 'voice-browser',
          synthesis: 'synthesis-browser',
        },
        page: {
          offset: Number(url.searchParams.get('offset') || 0),
          limit: Number(url.searchParams.get('limit') || 80),
          filtered_chunk_count: filterCount[filter] ?? filtered.length,
          has_more: false,
          next_offset: null,
        },
      };
    };
    const chapterRows = [
      { chapter_id: 'chapter:0', order: 0, name: 'Prologue', start_ms: 0, end_ms: 660000 },
      { chapter_id: 'chapter:1', order: 1, name: 'Chapter One', start_ms: 660000, end_ms: 1980000 },
      { chapter_id: 'chapter:2', order: 2, name: 'Chapter Two', start_ms: 1980000, end_ms: 3420000 },
      { chapter_id: 'chapter:3', order: 3, name: 'Epilogue', start_ms: 3420000, end_ms: 3960000 },
    ];
    const exportPlan = request => {
      const metadata = request?.metadata || {};
      const formats = request?.formats?.length ? request.formats : ['mp3'];
      const format = formats[0];
      const blockers = [];
      if (!String(metadata.title || '').trim() || !String(metadata.author || '').trim()) {
        blockers.push({ code: 'export_metadata_missing', title: 'Publication metadata is incomplete', explanation: 'Enter title and author.', blocking: true });
      }
      if (format === 'chapter_separated') {
        blockers.push({ code: 'export_format_unavailable', title: 'Selected export format is unavailable', explanation: 'Separate chapter files are not available.', blocking: true });
      }
      return {
        schema_version: 1,
        metadata,
        formats,
        chapter_mode: request?.chapter_mode || 'smart',
        chapters: chapterRows,
        dependency_fingerprint: 'export-dependency-' + format,
        plan_fingerprint: 'export-plan-' + format,
        blockers,
        safe_to_execute: blockers.length === 0,
        output_filenames: {
          mp3: 'cloned_audiobook.mp3',
          m4b: 'audiobook.m4b',
          audacity: 'audacity_export.zip',
        },
      };
    };
    window.__boundary12ExportPhase = 'ready';
    window.__boundary12ExportPlan = exportPlan({
      metadata: {
        title: 'The Shadows of Avalon',
        author: 'Paul Cornell',
        narrator: 'Full Cast',
        year: '2026',
        description: 'An Alexandria browser acceptance fixture.',
      },
      formats: ['mp3'],
      chapter_mode: 'smart',
    });
    window.__boundary12Requests = [];
    const exportPayload = () => {
      const phase = window.__boundary12ExportPhase;
      const plan = window.__boundary12ExportPlan;
      const complete = phase === 'complete' || phase === 'failed';
      const output = {
        format: 'mp3',
        filename: 'cloned_audiobook.mp3',
        state: complete ? 'current' : 'missing',
        exists: complete,
        download_url: complete ? '/api/audiobook' : null,
        playback_url: complete ? 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=' : null,
        size_bytes: complete ? 128000000 : null,
        duration_ms: complete ? 3960000 : null,
      };
      return {
        schema_version: 1,
        state: phase === 'running' ? 'running' : phase === 'failed' ? 'complete' : phase,
        metadata: plan.metadata,
        formats: plan.formats,
        chapter_mode: plan.chapter_mode,
        chapters: plan.chapters,
        cover: { exists: false, sha256: null, relative_path: null },
        outputs: {
          mp3: output,
          m4b: { format: 'm4b', filename: 'audiobook.m4b', state: 'missing', exists: false },
          audacity: { format: 'audacity', filename: 'audacity_export.zip', state: 'missing', exists: false },
        },
        selected_outputs: [output],
        summary: {
          selected_format_count: 1,
          current_output_count: complete ? 1 : 0,
          chapter_count: 4,
          blocker_count: 0,
          complete,
        },
        blockers: [],
        process: {
          running: phase === 'running',
          cancel: false,
          last_error: phase === 'failed' ? 'Synthetic validation failure.' : null,
          result: complete ? { status: phase === 'failed' ? 'failed' : 'complete', build_id: 'export-browser' } : null,
          logs: phase === 'running' ? ['Starting Export build for mp3.', 'Validating temporary output.'] : [],
        },
        primary_action: phase === 'running' ? { id: 'cancel_export_build' } : { id: 'build_export' },
        plan,
        receipt: complete ? { build_id: 'export-browser', metadata: plan.metadata } : null,
        player: complete ? { format: 'mp3', url: 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=', duration_ms: 3960000 } : null,
        fingerprints: { dependencies: plan.dependency_fingerprint, plan: plan.plan_fingerprint },
        technical_details: { receipt_relative_path: 'export_build.json' },
      };
    };
    window.fetch = async (input, init = {}) => {
      const raw = typeof input === 'string' ? input : input?.url || '';
      const url = new URL(raw, window.location.origin);
      const method = String(init.method || 'GET').toUpperCase();
      if (url.pathname === '/api/produce' && method === 'GET') {
        return jsonResponse(producePayload(url));
      }
      if (url.pathname.startsWith('/api/produce/chunks/') && method === 'GET') {
        const id = decodeURIComponent(url.pathname.split('/').pop());
        const stable = id.startsWith('chunk:') ? id : 'chunk:' + id;
        const chunk = produceChunks.find(item => item.chunk_id === stable);
        return chunk ? jsonResponse(chunk) : jsonResponse({ detail: { message: 'Chunk not found' } }, 404);
      }
      if (url.pathname === '/api/produce/plan' && method === 'POST') {
        const body = JSON.parse(init.body || '{}');
        const selected = body.mode === 'selected'
          ? body.selected_chunk_ids || []
          : body.mode === 'retry_failed'
            ? ['chunk:failed-1']
            : body.mode === 'regenerate_all'
              ? produceChunks.map(chunk => chunk.chunk_id)
              : ['chunk:ready-1', 'chunk:ready-2', 'chunk:stale-1'];
        return jsonResponse({
          mode: body.mode,
          destructive: body.mode === 'regenerate_all',
          plan_fingerprint: 'produce-plan-' + body.mode,
          chunks_fingerprint: 'produce-chunks-browser',
          chunk_ids: selected,
          indices: selected.map((_, index) => index),
          total_count: selected.length,
          blockers: [],
          safe_to_execute: selected.length > 0,
        });
      }
      if ((url.pathname === '/api/produce/generate' || url.pathname === '/api/produce/retry-failed') && method === 'POST') {
        window.__boundary12Requests.push({ path: url.pathname, body: JSON.parse(init.body || '{}') });
        return jsonResponse({ status: 'accepted' });
      }
      if (url.pathname === '/api/produce/cancel' && method === 'POST') {
        return jsonResponse({ status: 'idle' });
      }
      if (url.pathname === '/api/export' && method === 'GET') {
        return jsonResponse(exportPayload());
      }
      if (url.pathname === '/api/export/plan' && method === 'POST') {
        const body = JSON.parse(init.body || '{}');
        window.__boundary12ExportPlan = exportPlan(body);
        return jsonResponse(window.__boundary12ExportPlan);
      }
      if (url.pathname === '/api/export/build' && method === 'POST') {
        const body = JSON.parse(init.body || '{}');
        window.__boundary12Requests.push({ path: url.pathname, body });
        window.__boundary12ExportPhase = 'running';
        window.setTimeout(() => { window.__boundary12ExportPhase = 'complete'; }, 250);
        return jsonResponse({ status: 'started', operation_id: 'export-browser', plan: window.__boundary12ExportPlan });
      }
      if (url.pathname === '/api/export/cancel' && method === 'POST') {
        window.__boundary12ExportPhase = 'ready';
        return jsonResponse({ status: 'cancelling' });
      }
      return originalFetch(input, init);
    };
    window.__boundary12FetchInstalled = true;
  })()`);
}

async function inspectCanonicalShell(
  client,
  { name, destination, width, height, screenshotPath },
) {
  const selectors = {
    projects: '[data-tab="setup"][data-route="projects"]',
    script: '[data-tab="script"][data-route="script"]',
    cast: '[data-tab="characters"][data-route="cast"]',
    produce: '[data-tab="editor"][data-route="produce"]',
    export: '[data-tab="audio"][data-route="export"]',
    library: '[data-tab="designer"][data-route="library"]',
    voices: '[data-tab="designer"][data-route="voices"]',
    templates: '[data-tab="designer"][data-route="templates"]',
    more: '[data-tab="speaker-management"][data-route="more"]:not([data-route-tool])',
    help: '[data-route="more"][data-route-tool="help-center"]',
    maintenance: '[data-tab="project-recovery"][data-route="more"][data-route-tool="maintenance"]',
    settings: '[data-tab="setup"][data-route="settings"]',
  };
  const selector = selectors[destination];
  if (!selector) throw new Error(`Unsupported canonical destination ${destination}`);
  if (destination === 'produce' || destination === 'export') {
    await installBoundary12Fixtures(client);
  }
  await client.send('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await evaluate(client, `(() => {
    document.body.classList.remove('rail-open');
    const target = document.querySelector(${JSON.stringify(selector)});
    if (!target) throw new Error('Missing canonical route ${destination}');
    target.click();
    window.scrollTo(0, 0);
  })()`);
  await wait(650);
  if (destination === 'script') {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      const ready = await evaluate(client, `(() => {
        const content = document.getElementById('script-review-content');
        const loading = document.getElementById('script-review-loading');
        return Boolean(content && !content.hidden && loading?.hidden);
      })()`);
      if (ready) break;
      await wait(100);
    }
    const approvalEnabled = await evaluate(client, `(() => {
      const action = document.getElementById('shell-primary-action');
      return Boolean(action && !action.hidden && !action.disabled && action.dataset.action === 'script-primary');
    })()`);
    if (approvalEnabled) {
      await evaluate(client, `document.getElementById('shell-primary-action').click()`);
      for (let attempt = 0; attempt < 120; attempt += 1) {
        const reviewed = await evaluate(client, `(() => {
          const summary = document.getElementById('script-blocker-summary');
          const notice = document.getElementById('script-review-notice');
          return Boolean((summary && !summary.hidden) || (notice && !notice.hidden));
        })()`);
        if (reviewed) break;
        await wait(100);
      }
    }
    if (width < 1180) {
      await evaluate(client, `(() => {
        const filter = [...document.querySelectorAll('[data-script-filter]')]
          .find(button => button.dataset.scriptFilter !== 'all' && !button.disabled);
        filter?.click();
      })()`);
      await wait(150);
    }
  } else if (destination === 'cast') {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      const ready = await evaluate(client, `(() => {
        const content = document.getElementById('cast-content');
        const loading = document.getElementById('cast-loading');
        return Boolean(
          content
          && !content.hidden
          && loading?.hidden
          && document.querySelectorAll('.cast-character-row').length
        );
      })()`);
      if (ready) break;
      await wait(100);
    }
  } else if (destination === 'produce') {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      const ready = await evaluate(client, `(() => (
        !document.getElementById('produce-content')?.hidden
        && document.getElementById('produce-loading')?.hidden
        && document.querySelectorAll('.produce-chunk-row').length > 0
      ))()`);
      if (ready) break;
      await wait(100);
    }
    if (width < 1180) {
      await evaluate(client, `document.querySelector('.produce-chunk-row[aria-selected="true"]')?.click()`);
      await wait(120);
    }
  } else if (destination === 'export') {
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const ready = await evaluate(client, `(() => (
        !document.getElementById('export-content')?.hidden
        && document.getElementById('export-loading')?.hidden
        && document.getElementById('export-validation-summary')?.textContent.trim() === 'No blocking issues'
        && document.getElementById('shell-primary-action')?.disabled === false
      ))()`);
      if (ready) break;
      await wait(100);
    }
  } else if (destination === 'library' || destination === 'voices') {
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const ready = await evaluate(client, `(() => (
        !document.getElementById('library-content')?.hidden
        && document.getElementById('library-loading')?.hidden
        && document.querySelectorAll('#library-artifact-list .supporting-list-row').length > 0
      ))()`);
      if (ready) break;
      await wait(100);
    }
  } else if (destination === 'templates') {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      const ready = await evaluate(client, `(() => {
        const workspace = document.getElementById('templates-workspace');
        return Boolean(workspace && !workspace.hidden && document.querySelectorAll('[data-template-preset]').length === 4);
      })()`);
      if (ready) break;
      await wait(100);
    }
  } else if (destination === 'more') {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      const ready = await evaluate(client, `(() => {
        const workspace = document.getElementById('more-workspace');
        return Boolean(workspace && !workspace.hidden && document.querySelectorAll('[data-more-tool]').length >= 8);
      })()`);
      if (ready) break;
      await wait(100);
    }
  } else if (destination === 'help') {
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const ready = await evaluate(client, `(() => (
        !document.getElementById('help-content')?.hidden
        && document.getElementById('help-loading')?.hidden
        && document.querySelectorAll('#help-topic-list .supporting-list-row').length >= 8
        && Boolean(document.querySelector('#help-topic-detail h2'))
      ))()`);
      if (ready) break;
      await wait(100);
    }
  } else if (destination === 'maintenance') {
    for (let attempt = 0; attempt < 160; attempt += 1) {
      const ready = await evaluate(client, `(() => (
        window.AlexandriaNavigation?.current()?.context?.tool === 'maintenance'
        && !document.getElementById('canonical-maintenance-workspace')?.hidden
        && !document.getElementById('maintenance-content')?.hidden
        && document.getElementById('maintenance-loading')?.hidden
        && document.querySelectorAll('#maintenance-health-list .maintenance-row').length >= 2
        && document.querySelectorAll('#maintenance-model-list .maintenance-row').length >= 1
      ))()`);
      if (ready) break;
      await wait(100);
    }
  }
  const result = await evaluate(client, `(() => {
    const visible = element => {
      if (!element) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return !element.hidden
        && style.display !== 'none'
        && style.visibility !== 'hidden'
        && rect.width > 0
        && rect.height > 0;
    };
    const rect = element => {
      if (!element) return null;
      const value = element.getBoundingClientRect();
      return {
        left: value.left,
        top: value.top,
        right: value.right,
        bottom: value.bottom,
        width: value.width,
        height: value.height,
      };
    };
    const globalHeader = document.getElementById('canonical-global-header');
    const projectHeader = document.getElementById('canonical-project-header');
    const pageTitleRegion = document.getElementById('canonical-page-title-region');
    const shellRoots = [
      document.querySelector('.alexandria-rail'),
      globalHeader,
      projectHeader,
      pageTitleRegion,
      document.getElementById('persistent-player-host'),
    ].filter(visible);
    const textNodes = shellRoots.flatMap(root => [...root.querySelectorAll('*')])
      .filter(visible)
      .filter(element => element.childElementCount === 0 && element.textContent.trim());
    const fontSizes = textNodes
      .map(element => Number.parseFloat(getComputedStyle(element).fontSize))
      .filter(Number.isFinite);
    const ordinaryPanels = [
      document.getElementById('project-continuation'),
      document.getElementById('project-list'),
      document.getElementById('shell-stage-tracker'),
      document.getElementById('library-workspace'),
    ].filter(visible);
    const main = document.getElementById('main-content');
    const mainStyle = getComputedStyle(main);
    const rail = document.querySelector('.alexandria-rail');
    const header = visible(projectHeader) ? projectHeader : globalHeader;
    const projectNavigation = document.getElementById('project-stage-navigation');
    const stageTracker = document.getElementById('shell-stage-tracker');
    const player = document.getElementById('persistent-player-host');
    const bodyText = document.body.innerText;
    return {
      name: '${name}',
      destination: window.AlexandriaNavigation?.current()?.destination || null,
      viewport: { width: window.innerWidth, height: window.innerHeight },
      railRect: rect(rail),
      headerRect: rect(header),
      mainRect: rect(main),
      mainPadding: {
        left: Number.parseFloat(mainStyle.paddingLeft),
        right: Number.parseFloat(mainStyle.paddingRight),
      },
      globalNavigationVisible: visible(document.querySelector('.alexandria-rail-section[aria-label="Global"]')),
      projectNavigationVisible: visible(projectNavigation),
      stageTrackerVisible: visible(stageTracker),
      playerVisible: visible(player),
      playerRect: rect(player),
      pageTitle: (visible(projectHeader)
        ? document.getElementById('shell-page-title')
        : document.getElementById('shell-global-title'))?.textContent.trim() || null,
      primaryAction: visible(document.getElementById('shell-primary-action'))
        ? document.getElementById('shell-primary-action').textContent.trim()
        : null,
      minimumShellFontSize: fontSizes.length ? Math.min(...fontSizes) : null,
      ordinaryPanelShadowCount: ordinaryPanels.filter(panel => {
        const shadow = getComputedStyle(panel).boxShadow;
        return shadow && shadow !== 'none';
      }).length,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      duplicateFullTransportCount: [...document.querySelectorAll('audio[controls]')].filter(visible).length,
      scriptReview: document.body.dataset.destination === 'script' ? (() => {
        const workspace = document.getElementById('script-review-workspace');
        const sourceContext = document.querySelector('.script-source-context');
        const list = document.getElementById('script-entry-list');
        const rows = [...document.querySelectorAll('.script-entry-row')];
        const selectedRow = rows.find(row => row.getAttribute('aria-selected') === 'true');
        const inspector = document.getElementById('script-review-inspector');
        const inspectorStyle = inspector ? getComputedStyle(inspector) : null;
        const summary = document.getElementById('script-blocker-summary');
        const action = document.getElementById('shell-primary-action');
        const reason = document.getElementById('script-approval-reason');
        const notice = document.getElementById('script-review-notice');
        const filters = [...document.querySelectorAll('[data-script-filter]')];
        const issueNavigation = document.querySelector('.script-issue-navigation');
        return {
          workspaceVisible: visible(workspace),
          sourceContextRect: rect(sourceContext),
          listRect: rect(list),
          listBorderWidth: list ? Number.parseFloat(getComputedStyle(list).borderTopWidth) : null,
          listRowGap: list ? Number.parseFloat(getComputedStyle(list).rowGap) : null,
          rowCount: rows.length,
          problematicRowCount: rows.filter(row => row.classList.contains('has-issue')).length,
          selectedRowCount: rows.filter(row => row.getAttribute('aria-selected') === 'true').length,
          selectedRowOutlineWidth: selectedRow
            ? Number.parseFloat(getComputedStyle(selectedRow).outlineWidth)
            : null,
          rowShadowCount: rows.filter(row => getComputedStyle(row).boxShadow !== 'none').length,
          filterLabels: filters.map(button => button.textContent.trim()),
          enabledIssueFilterCount: filters.filter(button => button.dataset.scriptFilter !== 'all' && !button.disabled).length,
          blockerSummaryVisible: visible(summary),
          blockerTitle: document.getElementById('script-blocker-summary-title')?.textContent.trim() || null,
          inspectorVisible: visible(inspector),
          inspectorRect: rect(inspector),
          inspectorPosition: inspectorStyle?.position || null,
          inspectorIssueTypeVisible: visible(document.getElementById('script-inspector-issue-type')),
          inspectorSourceVisible: visible(document.getElementById('script-inspector-source-section')),
          contextualActionVisible: visible(document.getElementById('script-issue-primary-action')),
          contextualActionLabel: document.getElementById('script-issue-primary-action')?.textContent.trim() || null,
          primaryActionLabel: visible(action) ? action.textContent.trim() : null,
          primaryActionDisabled: action?.disabled ?? null,
          approvalReasonVisible: visible(reason),
          approvalReasonText: reason?.textContent.trim() || null,
          issueNavigationVisible: visible(issueNavigation),
          issuePositionText: document.getElementById('script-issue-position')?.textContent.trim() || null,
          noticeVisible: visible(notice),
          disclosureCount: [...document.querySelectorAll('.script-review-disclosure')].filter(visible).length,
          fullTransportCount: workspace
            ? [...workspace.querySelectorAll('audio[controls]')].filter(visible).length
            : 0,
        };
      })() : null,
      castReview: document.body.dataset.destination === 'cast' ? (() => {
        const workspace = document.getElementById('cast-workspace');
        const lists = workspace
          ? [...workspace.querySelectorAll('[role="listbox"]')].filter(visible)
          : [];
        const rows = workspace
          ? [...workspace.querySelectorAll('.cast-character-row')].filter(visible)
          : [];
        const portraits = rows
          .map(row => row.querySelector('.cast-character-portrait'))
          .filter(Boolean);
        const portraitRects = portraits.map(rect).filter(Boolean);
        const selectedRows = rows.filter(
          row => row.getAttribute('aria-selected') === 'true'
        );
        const leafText = workspace
          ? [...workspace.querySelectorAll('*')]
              .filter(element => element.childElementCount === 0)
              .map(element => element.textContent.trim())
              .filter(Boolean)
          : [];
        const sectionSelectors = [
          '.cast-voice-section',
          '.cast-reference-section',
          '.cast-preview-section',
          '#cast-character-summary-disclosure',
          '#cast-appearance-summary-disclosure',
          '#cast-advanced-disclosure',
        ];
        const sections = sectionSelectors
          .map(selector => workspace?.querySelector(selector))
          .filter(Boolean);
        const detailPortrait = document.getElementById('cast-detail-portrait');
        return {
          workspaceVisible: visible(workspace),
          heading: document.getElementById('cast-characters-heading')?.textContent.trim() || null,
          listboxCount: lists.length,
          rowCount: rows.length,
          selectedRowCount: selectedRows.length,
          selectedBadgeCount: leafText.filter(value => value === 'Selected').length,
          statusTreatmentCount: rows.filter(
            row => row.querySelectorAll('.cast-character-status').length === 1
          ).length,
          portraitWidths: [...new Set(portraitRects.map(value => value.width))],
          portraitHeights: [...new Set(portraitRects.map(value => value.height))],
          detailPortraitRect: rect(detailPortrait),
          detailName: document.getElementById('cast-detail-name')?.textContent.trim() || null,
          detailState: document.getElementById('cast-detail-state')?.textContent.trim() || null,
          voiceSavedState: document.getElementById('cast-voice-saved-state')?.textContent.trim() || null,
          saveChangesVisible: visible(document.getElementById('cast-save-voice')),
          editVoiceVisible: visible(document.getElementById('cast-edit-voice')),
          sectionOrderTop: sections.map(section => section.getBoundingClientRect().top),
          sectionCount: sections.length,
          fullTransportCount: workspace
            ? [...workspace.querySelectorAll('audio[controls]')].filter(visible).length
            : 0,
        };
      })() : null,
      produceReview: document.body.dataset.destination === 'produce' ? (() => {
        const workspace = document.getElementById('produce-workspace');
        const rows = [...document.querySelectorAll('.produce-chunk-row')].filter(visible);
        const selected = rows.filter(row => row.getAttribute('aria-selected') === 'true');
        const inspector = document.getElementById('produce-inspector');
        const counts = Object.fromEntries([
          ['all', 'produce-count-all'],
          ['ready', 'produce-count-ready'],
          ['needs_listening', 'produce-count-listening'],
          ['failed', 'produce-count-failed'],
          ['stale', 'produce-count-stale'],
          ['current', 'produce-count-current'],
          ['blocked', 'produce-count-blocked'],
        ].map(([key, id]) => [key, Number((document.getElementById(id)?.textContent || '0').replace(/,/g, ''))]));
        return {
          workspaceVisible: visible(workspace),
          counts,
          reconciledCount: counts.ready + counts.needs_listening + counts.failed + counts.stale + counts.current + counts.blocked,
          rowCount: rows.length,
          chapterGroupCount: [...document.querySelectorAll('.produce-chapter-group')].filter(visible).length,
          selectedRowCount: selected.length,
          selectedRowState: selected[0]?.querySelector('.produce-row-state')?.textContent.trim() || null,
          inspectorVisible: visible(inspector),
          inspectorPosition: inspector ? getComputedStyle(inspector).position : null,
          inspectorState: document.getElementById('produce-inspector-state')?.textContent.trim() || null,
          inspectorReason: document.getElementById('produce-inspector-reason')?.textContent.trim() || null,
          internalChunkIdVisible: /chunk:/i.test(inspector?.innerText || ''),
          regenerateButtonClass: document.getElementById('produce-regenerate-selected')?.className || null,
          filledPrimaryCount: workspace
            ? [...workspace.querySelectorAll('.btn-primary')].filter(visible).length
            : 0,
          regenerateAllInOverflow: Boolean(document.querySelector('#produce-overflow #produce-regenerate-all')),
          fullTransportCount: workspace
            ? [...workspace.querySelectorAll('audio[controls]')].filter(visible).length
            : 0,
        };
      })() : null,
      exportReview: document.body.dataset.destination === 'export' ? (() => {
        const workspace = document.getElementById('export-workspace');
        const labels = [...document.querySelectorAll('#export-format-group label')];
        const validationText = [...document.querySelectorAll('.export-validation-row')]
          .map(row => row.textContent.trim());
        return {
          workspaceVisible: visible(workspace),
          workflowState: document.getElementById('shell-workflow-state')?.textContent.trim() || null,
          primaryActionLabel: document.getElementById('shell-primary-action')?.textContent.trim() || null,
          primaryActionDisabled: document.getElementById('shell-primary-action')?.disabled ?? null,
          saveState: document.getElementById('shell-save-state')?.textContent.trim() || null,
          publicationTitle: document.getElementById('export-publication-title')?.textContent.trim() || null,
          publicationAuthor: document.getElementById('export-publication-author')?.textContent.trim() || null,
          filename: document.getElementById('export-filename')?.value
            || document.getElementById('export-filename')?.textContent.trim()
            || null,
          filenameBehavior: document.getElementById('export-filename-behavior')?.textContent.trim() || null,
          formatLabels: labels.map(label => label.querySelector('strong')?.textContent.trim() || ''),
          enabledFormatCount: labels.filter(label => !label.querySelector('input')?.disabled).length,
          selectedFormat: document.querySelector('input[name="export-format"]:checked')?.value || null,
          validationSummary: document.getElementById('export-validation-summary')?.textContent.trim() || null,
          validationRowCount: validationText.length,
          repeatedPassedCount: validationText.filter(value => /^passed$/i.test(value)).length,
          chapterRowCount: [...document.querySelectorAll('.export-chapter-row')].filter(visible).length,
          waveformDisabled: document.getElementById('export-waveform')?.disabled ?? null,
          builtConfirmationVisible: visible(document.getElementById('export-built-confirmation')),
          technicalDetailsOpen: document.querySelector('.export-technical-details')?.open ?? null,
          legacyResultVisible: visible(document.querySelector('#audio-tab > .workflow-surface')),
          filledPrimaryCount: workspace
            ? [...workspace.querySelectorAll('.btn-primary')].filter(visible).length
            : 0,
          fullTransportCount: workspace
            ? [...workspace.querySelectorAll('audio[controls]')].filter(visible).length
            : 0,
        };
      })() : null,
      maintenanceReview: '${destination}' === 'maintenance' ? (() => {
        const workspace = document.getElementById('canonical-maintenance-workspace');
        const legacy = document.getElementById('legacy-settings-workspace');
        const recovery = document.getElementById('recovery-center');
        const text = workspace?.innerText || '';
        const summary = document.querySelector('.maintenance-summary-strip');
        const rows = [...workspace?.querySelectorAll('.maintenance-row') || []].filter(visible);
        const impactButtons = [...workspace?.querySelectorAll('[data-maintenance-library-impact], [data-maintenance-project-impact]') || []].filter(visible);
        const modelButtons = [...workspace?.querySelectorAll('[data-maintenance-model-action]') || []].filter(visible);
        return {
          workspaceVisible: visible(workspace),
          legacyVisible: visible(legacy),
          legacyRecoveryVisible: visible(recovery),
          summaryVisible: visible(summary),
          summaryColumnCount: summary ? getComputedStyle(summary).gridTemplateColumns.split(' ').length : 0,
          healthRowCount: [...document.querySelectorAll('#maintenance-health-list .maintenance-row')].filter(visible).length,
          modelRowCount: [...document.querySelectorAll('#maintenance-model-list .maintenance-row')].filter(visible).length,
          libraryRowCount: [...document.querySelectorAll('#maintenance-library-list .maintenance-row')].filter(visible).length,
          projectRowCount: [...document.querySelectorAll('#maintenance-project-list .maintenance-row')].filter(visible).length,
          historyRowCount: [...document.querySelectorAll('#maintenance-history-list .maintenance-row')].filter(visible).length,
          totalRowCount: rows.length,
          impactButtonCount: impactButtons.length,
          modelActionCount: modelButtons.length,
          refreshState: document.getElementById('maintenance-refresh-state')?.textContent.trim() || null,
          overallState: document.getElementById('maintenance-overall-state')?.textContent.trim() || null,
          migrationState: document.getElementById('maintenance-migration-state')?.textContent.trim() || null,
          settingsWorkspaceVisible: visible(document.getElementById('canonical-settings-workspace')),
          /* Cooked template literals can corrupt escaped regex delimiters below.
          rawAbsolutePathVisible: /\/(Users|private|var|tmp|home)\//.test(text),
          rawFingerprintVisible: /\b[a-f0-9]{64}\b/i.test(text),
          */
          rawAbsolutePathVisible: ['/Users/', '/private/', '/var/', '/tmp/', '/home/'].some(prefix => text.includes(prefix)),
          rawFingerprintVisible: new RegExp('[a-f0-9]{64}', 'i').test(text),
          rawSnapshotLabelVisible: new RegExp('snapshot path|cache dir|root dir|config path', 'i').test(text),
          dialogVisible: document.getElementById('maintenance-impact-dialog')?.open || false,
          primaryFilledCount: [...workspace?.querySelectorAll('.btn-primary') || []].filter(visible).length,
          destructiveButtonVisibleWithoutDialog: [...workspace?.querySelectorAll('.btn-danger') || []].filter(visible).length,
        };
      })() : null,
      settingsReview: document.body.dataset.destination === 'settings' ? (() => {
        const workspace = document.getElementById('canonical-settings-workspace');
        const form = document.getElementById('canonical-settings-form');
        const legacy = document.getElementById('legacy-settings-workspace');
        const recovery = document.getElementById('recovery-center');
        const normalText = workspace?.innerText || '';
        return {
          workspaceVisible: visible(workspace),
          formVisible: visible(form),
          legacyVisible: visible(legacy),
          recoveryVisible: visible(recovery),
          sectionCount: [...document.querySelectorAll('.canonical-settings-section')].filter(visible).length,
          saveButtonCount: [...document.querySelectorAll('#canonical-settings-save')].filter(visible).length,
          summaryVisible: visible(document.querySelector('.canonical-settings-summary')),
          defaultTemplate: document.getElementById('settings-default-template-name')?.textContent.trim() || null,
          outputLanguage: document.getElementById('settings-output-language')?.value || null,
          provider: document.getElementById('settings-provider-backend')?.value || null,
          model: document.getElementById('settings-provider-model')?.value || null,
          apiKeyValue: document.getElementById('settings-api-key')?.value || null,
          apiKeyState: document.getElementById('settings-api-key-state')?.textContent.trim() || null,
          structuredOutputDisabled: document.getElementById('settings-structured-output')?.disabled ?? null,
          storageTruth: document.getElementById('settings-storage-enforcement')?.textContent.trim() || null,
          advancedActionCount: [...document.querySelectorAll('[data-settings-destination]')].filter(visible).length,
          promptEditorVisible: visible(document.getElementById('promptSettings')),
          modelCacheVisible: visible(document.getElementById('model-cache-panel')),
          runtimeVisible: visible(document.getElementById('llm-runtime-panel')),
          repairControlVisible: [...document.querySelectorAll('[id*="repair"], [data-action*="repair"]')].some(visible),
          rawFingerprintVisible: /[a-f0-9]{64}/i.test(normalText),
        };
      })() : null,
      supportingReview: ['library', 'voices', 'templates', 'more', 'help'].includes('${destination}') ? (() => {
        const libraryWorkspace = document.getElementById('library-workspace');
        const inventoryView = document.getElementById('library-inventory-view');
        const templatesWorkspace = document.getElementById('templates-workspace');
        const moreWorkspace = document.getElementById('more-workspace');
        const helpWorkspace = document.getElementById('help-center-workspace');
        const selectedLibraryRows = [...document.querySelectorAll('#library-artifact-list [aria-selected="true"]')].filter(visible);
        const activeTab = document.querySelector('.tab-content[style*="display: block"], .tab-content:not([style*="display:none"]):not([style*="display: none"])');
        const technicalDetails = document.querySelector('#library-artifact-detail details');
        return {
          requestedDestination: '${destination}',
          actualDestination: window.AlexandriaNavigation?.current()?.destination || null,
          libraryWorkspaceVisible: visible(libraryWorkspace),
          inventoryViewVisible: visible(inventoryView),
          templatesWorkspaceVisible: visible(templatesWorkspace),
          libraryRowCount: [...document.querySelectorAll('#library-artifact-list .supporting-list-row')].filter(visible).length,
          librarySelectedRowCount: selectedLibraryRows.length,
          libraryDetailTitle: document.querySelector('#library-artifact-detail h2')?.textContent.trim() || null,
          libraryResultCount: document.getElementById('library-result-count')?.textContent.trim() || null,
          libraryTechnicalDetailsOpen: technicalDetails?.open ?? null,
          visibleRawFingerprint: /[a-f0-9]{64}/i.test(document.getElementById('library-artifact-detail')?.innerText || ''),
          deleteButtonCount: [...document.querySelectorAll('[data-library-delete]')].filter(visible).length,
          deleteEnabledCount: [...document.querySelectorAll('[data-library-delete]')].filter(button => visible(button) && !button.disabled).length,
          templateCount: [...document.querySelectorAll('#template-list [data-template-id]')].filter(visible).length,
          templateSelectedCount: [...document.querySelectorAll('#template-list [aria-selected="true"]')].filter(visible).length,
          templateDetailTitle: document.querySelector('#template-detail h2')?.textContent.trim() || null,
          templateRawFingerprintVisible: /[a-f0-9]{64}/i.test(document.getElementById('template-detail')?.innerText || ''),
          moreWorkspaceVisible: visible(moreWorkspace),
          moreToolCount: [...document.querySelectorAll('[data-more-tool]')].filter(visible).length,
          helpWorkspaceVisible: visible(helpWorkspace),
          helpTopicCount: [...document.querySelectorAll('#help-topic-list .supporting-list-row')].filter(visible).length,
          helpDetailTitle: document.querySelector('#help-topic-detail h2')?.textContent.trim() || null,
          helpScriptElementCount: document.querySelectorAll('#help-topic-detail script').length,
          legacyWorkflowVisible: activeTab
            ? [...activeTab.querySelectorAll(':scope > .workflow-surface')].some(visible)
            : false,
          primaryAction: visible(document.getElementById('shell-primary-action'))
            ? document.getElementById('shell-primary-action').textContent.trim()
            : null,
        };
      })() : null,
      placeholderCopyVisible: bodyText.includes('Status / Blocker')
        || bodyText.includes('Primary action'),
    };
  })()`);
  result.screenshotBytes = await writeScreenshot(client, screenshotPath);
  result.screenshotPath = screenshotPath;
  return result;
}

async function inspectCastVoiceEditor(client, screenshotPath) {
  await client.send('Emulation.setDeviceMetricsOverride', {
    width: 1536,
    height: 1024,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await evaluate(client, `(() => {
    document.body.classList.remove('rail-open');
    const target = document.querySelector('[data-tab="characters"][data-route="cast"]');
    if (!target) throw new Error('Missing canonical Cast route');
    target.click();
    window.scrollTo(0, 0);
  })()`);
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      document.body.dataset.destination === 'cast'
      && [...document.querySelectorAll('.cast-character-row')].length > 0
      && !document.getElementById('cast-edit-voice')?.disabled
    ))()`);
    if (ready) break;
    await wait(100);
  }
  await evaluate(client, `(() => {
    window.__castAuditVoiceSaves = [];
    if (!window.__castAuditFetchInstalled) {
      const originalFetch = window.fetch.bind(window);
      window.fetch = async (input, init = {}) => {
        const url = typeof input === 'string' ? input : input?.url || '';
        if (url.includes('/api/save_voice_config') && String(init.method || 'GET').toUpperCase() === 'POST') {
          window.__castAuditVoiceSaves.push(init.body || null);
        }
        return originalFetch(input, init);
      };
      window.__castAuditFetchInstalled = true;
    }
    const rows = [...document.querySelectorAll('.cast-character-row')];
    const row = rows.find(item => /the doctor/i.test(item.textContent)) || rows[0];
    if (!row) throw new Error('No Cast character row is available');
    row.click();
    const edit = document.getElementById('cast-edit-voice');
    if (!edit || edit.disabled) throw new Error('Cast Voice edit action is unavailable');
    edit.click();
  })()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const mounted = await evaluate(client, `(() => {
      const editor = document.getElementById('cast-voice-editor');
      const card = document.querySelector('#cast-voice-editor-slot .voice-card');
      return Boolean(editor && !editor.hidden && card);
    })()`);
    if (mounted) break;
    await wait(100);
  }
  const before = await evaluate(client, `(() => {
    const visible = element => {
      if (!element) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return !element.hidden
        && style.display !== 'none'
        && style.visibility !== 'hidden'
        && rect.width > 0
        && rect.height > 0;
    };
    return {
      destination: window.AlexandriaNavigation?.current()?.destination || null,
      canonicalCastVisible: visible(document.getElementById('cast-workspace')),
      legacyCharacterWorkspaceVisible: visible(document.getElementById('character-workspace')),
      legacyCharacterWorkspaceHidden: document.getElementById('character-workspace')?.hidden ?? null,
      legacyCharacterWorkspaceInert: document.getElementById('character-workspace')?.hasAttribute('inert') ?? null,
      legacyCharacterWorkspaceDisplay: getComputedStyle(document.getElementById('character-workspace')).display,
      bodyDestination: document.body.dataset.destination || null,
      legacyModeClassPresent: document.body.classList.contains('cast-legacy-mode'),
      editorVisible: visible(document.getElementById('cast-voice-editor')),
      editorVoiceName: document.querySelector('#cast-voice-editor-slot .voice-card')?.dataset.voice || null,
      editActionVisible: visible(document.getElementById('cast-edit-voice')),
      saveActionVisible: visible(document.getElementById('cast-save-voice')),
      savedState: document.getElementById('cast-voice-saved-state')?.textContent.trim() || null,
    };
  })()`);
  await evaluate(client, `(() => {
    const card = document.querySelector('#cast-voice-editor-slot .voice-card');
    const type = card?.querySelector('.voice-type:checked')?.value || 'custom';
    const selector = ({
      custom: '.character-style',
      clone: '.clone-character-style',
      builtin_lora: '.builtin-lora-style',
      lora: '.lora-character-style',
      design: '.design-description',
    })[type];
    const input = selector ? card?.querySelector(selector) : null;
    if (!input) throw new Error('No active Cast Voice field is mounted');
    input.value = String(input.value || '') + ' browser-audit-change';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await wait(150);
  const after = await evaluate(client, `(() => {
    const visible = element => {
      if (!element) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return !element.hidden
        && style.display !== 'none'
        && style.visibility !== 'hidden'
        && rect.width > 0
        && rect.height > 0;
    };
    return {
      canonicalCastVisible: visible(document.getElementById('cast-workspace')),
      legacyCharacterWorkspaceVisible: visible(document.getElementById('character-workspace')),
      legacyCharacterWorkspaceHidden: document.getElementById('character-workspace')?.hidden ?? null,
      legacyCharacterWorkspaceInert: document.getElementById('character-workspace')?.hasAttribute('inert') ?? null,
      legacyCharacterWorkspaceDisplay: getComputedStyle(document.getElementById('character-workspace')).display,
      bodyDestination: document.body.dataset.destination || null,
      legacyModeClassPresent: document.body.classList.contains('cast-legacy-mode'),
      editorVisible: visible(document.getElementById('cast-voice-editor')),
      saveActionVisible: visible(document.getElementById('cast-save-voice')),
      saveActionLabel: document.getElementById('cast-save-voice')?.textContent.trim() || null,
      savedState: document.getElementById('cast-voice-saved-state')?.textContent.trim() || null,
      editedValue: (() => {
        const card = document.querySelector('#cast-voice-editor-slot .voice-card');
        const serialized = collectVoiceConfigForCard(card);
        return serialized.character_style || serialized.description || null;
      })(),
      collectedValue: (() => {
        const card = document.querySelector('#cast-voice-editor-slot .voice-card');
        const serialized = collectVoiceConfigForCard(card);
        return serialized.character_style || serialized.description || null;
      })(),
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
    };
  })()`);
  const screenshotBytes = await writeScreenshot(client, screenshotPath);
  await evaluate(client, `(() => {
    const save = document.getElementById('cast-save-voice');
    if (!save || save.hidden || save.disabled) {
      throw new Error('Cast Save changes action is unavailable');
    }
    save.click();
  })()`);
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const complete = await evaluate(client, `(() => (
      document.getElementById('cast-voice-editor')?.hidden === true
      && document.getElementById('canonical-shell-live')?.textContent.includes('Voice configuration saved')
    ))()`);
    if (complete) break;
    await wait(100);
  }
  const saved = await evaluate(client, `(async () => {
    const response = await fetch('/api/voices?audit=' + Date.now(), {
      credentials: 'same-origin',
      cache: 'no-store',
    });
    const voices = await response.json();
    const doctor = voices.find(item => item.name === 'THE DOCTOR');
    const narrator = voices.find(item => item.name === 'NARRATOR');
    return {
      responseStatus: response.status,
      editorHidden: document.getElementById('cast-voice-editor')?.hidden ?? null,
      saveActionHidden: document.getElementById('cast-save-voice')?.hidden ?? null,
      liveStatus: document.getElementById('canonical-shell-live')?.textContent.trim() || null,
      savedVoiceCount: voices.length,
      savedValue: doctor?.config?.character_style || null,
      capturedRequestBody: window.__castAuditVoiceSaves?.at(-1) || null,
      otherVoicePreserved: Boolean(narrator?.config && Object.keys(narrator.config).length),
    };
  })()`);
  await evaluate(client, `(() => {
    const edit = document.getElementById('cast-edit-voice');
    if (!edit || edit.disabled) throw new Error('Cast Voice edit action did not return after saving');
    edit.click();
  })()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const reopened = await evaluate(client, `(() => Boolean(
      !document.getElementById('cast-voice-editor')?.hidden
      && document.querySelector('#cast-voice-editor-slot .voice-card')
    ))()`);
    if (reopened) break;
    await wait(100);
  }
  const reopened = await evaluate(client, `(() => {
    const card = document.querySelector('#cast-voice-editor-slot .voice-card');
    const serialized = card ? collectVoiceConfigForCard(card) : null;
    return {
      editorVisible: Boolean(document.getElementById('cast-voice-editor') && !document.getElementById('cast-voice-editor').hidden),
      editorVoiceName: card?.dataset.voice || null,
      activeValue: serialized?.character_style || serialized?.description || null,
      saveActionHidden: document.getElementById('cast-save-voice')?.hidden ?? null,
      savedState: document.getElementById('cast-voice-saved-state')?.textContent.trim() || null,
      legacyCharacterWorkspaceVisible: (() => {
        const workspace = document.getElementById('character-workspace');
        if (!workspace) return false;
        const style = getComputedStyle(workspace);
        const rect = workspace.getBoundingClientRect();
        return !workspace.hidden && style.display !== 'none' && rect.width > 0 && rect.height > 0;
      })(),
    };
  })()`);
  return { before, after, saved, reopened, screenshotBytes, screenshotPath };
}

async function inspectState(
  client,
  {
    name,
    tab,
    width,
    height,
    screenshotPath,
    prepare = '',
    fileInputs = [],
    afterFilePrepare = '',
  },
) {
  const canonicalTab = ['voices', 'voice-projects'].includes(tab)
    ? 'characters'
    : tab;
  await client.send('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await evaluate(client, `(() => {
    document.querySelectorAll('details[open]').forEach(details => {
      details.open = false;
    });
    document.querySelectorAll('.toast').forEach(element => element.remove());
    document.querySelectorAll('.modal.show').forEach(element => {
      bootstrap.Modal.getInstance(element)?.hide();
    });
    document.querySelectorAll('.modal-backdrop').forEach(element => element.remove());
    document.body.classList.remove('modal-open', 'rail-open');
    document.body.style.removeProperty('overflow');
    document.body.style.removeProperty('padding-right');
    document.getElementById('rail-mobile-toggle')?.setAttribute('aria-expanded', 'false');
    document.querySelectorAll('.is-dragover').forEach(element => {
      element.classList.remove('is-dragover');
    });
    delete window.__phase24dRenderMs;
    delete window.__auditProjectSwitch;
  })()`);
  await wait(400);
  const routeSelector = ({
    setup: '[data-tab="setup"][data-route="settings"]',
    designer: '[data-tab="designer"][data-route="more"][data-route-tool="voice-designer"]',
    'speaker-management': '[data-tab="speaker-management"][data-route="more"][data-route-tool="advanced-character-operations"]',
    preparer: '[data-tab="preparer"][data-route="more"][data-route-tool="audio-preparer"]',
    'dataset-builder': '[data-tab="dataset-builder"][data-route="more"][data-route-tool="dataset-builder"]',
    training: '[data-tab="training"][data-route="more"][data-route-tool="voice-training"]',
    'project-recovery': '[data-tab="project-recovery"][data-route="more"][data-route-tool="maintenance"]',
  })[canonicalTab] || `[data-tab="${canonicalTab}"]`;
  await evaluate(client, `(() => {
    const target = document.querySelector(${JSON.stringify(routeSelector)});
    if (!target) throw new Error('Missing route ${canonicalTab}');
    target.click();
    window.scrollTo(0, 0);
  })()`);
  await wait(350);
  if (canonicalTab === 'characters') {
    await evaluate(client, `(async () => {
      const entry = (window.voiceTrainingStatus?.entries || []).find(
        item => item.canonical_name === 'THE DOCTOR'
      );
      if (entry && window.voiceTrainingSelectedId !== entry.character_id) {
        await selectVoiceTrainingCharacter(entry.character_id);
      }
    })()`);
    await wait(250);
  }
  if (prepare) {
    try {
      await evaluate(client, `(async () => { ${prepare} })()`);
    } catch (error) {
      throw new Error(`State ${name} preparation failed: ${error.message}`);
    }
    await wait(
      (name.startsWith('characters-') || name.startsWith('model-cache-'))
        ? 160
        : 550
    );
  }
  for (const fileInput of fileInputs) {
    const documentNode = await client.send('DOM.getDocument', { depth: -1 });
    const node = await client.send('DOM.querySelector', {
      nodeId: documentNode.root.nodeId,
      selector: fileInput.selector,
    });
    if (!node.nodeId) {
      throw new Error(`State ${name} missing file input ${fileInput.selector}`);
    }
    await client.send('DOM.setFileInputFiles', {
      nodeId: node.nodeId,
      files: [fileInput.path],
    });
    await wait(50);
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      const complete = await evaluate(client, `(() => {
        const value = window.AlexandriaCanonicalInterface?.state()?.newProject;
        return Boolean(value && !value.inspecting);
      })()`);
      if (complete) break;
      await wait(100);
    }
    await wait(250);
  }
  if (afterFilePrepare) {
    try {
      await evaluate(client, `(async () => { ${afterFilePrepare} })()`);
    } catch (error) {
      throw new Error(`State ${name} post-file preparation failed: ${error.message}`);
    }
    await wait(650);
  }

  const result = await evaluate(client, `(() => {
    const visible = element => {
      if (!element) return false;
      const closedDetails = element.closest('details:not([open])');
      const visibleSummary = element.closest('summary');
      if (
        closedDetails
        && (!visibleSummary || visibleSummary.parentElement !== closedDetails)
      ) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    const root = document.getElementById('${canonicalTab}-tab');
    const elements = [...root.querySelectorAll('button, input, select, textarea, details, table')]
      .filter(visible);
    const outOfBounds = elements
      .map(element => ({ element, rect: element.getBoundingClientRect() }))
      .filter(item => item.rect.left < -1 || item.rect.right > window.innerWidth + 1)
      .slice(0, 12)
      .map(item => ({
        tag: item.element.tagName,
        id: item.element.id || null,
        className: String(item.element.className || ''),
        left: item.rect.left,
        right: item.rect.right,
      }));
    const openDetails = [...root.querySelectorAll('details[open]')]
      .filter(visible)
      .map(details => details.id || details.className || details.querySelector('summary')?.textContent.trim());
    const chevrons = [...root.querySelectorAll('.disclosure-chevron')]
      .filter(visible)
      .map(element => ({
        transform: getComputedStyle(element).transform,
        open: Boolean(element.closest('details')?.open),
      }));
    const modal = [...document.querySelectorAll('.modal.show')].find(visible);
    const modalPrimary = modal?.querySelector('.btn-primary, .btn-danger');
    const toast = [...document.querySelectorAll('.toast.show')].find(visible);
    const notice = [...root.querySelectorAll('.alert, .workspace-alert')].find(visible);
    const noticeStyle = notice ? getComputedStyle(notice) : null;
    const main = document.getElementById('main-content');
    const tableState = [...root.querySelectorAll('.table-state')].find(visible);
    const tableShell = [...root.querySelectorAll('.data-table-shell')].find(visible);
    return {
      name: '${name}',
      tab: '${tab}',
      viewport: { width: window.innerWidth, height: window.innerHeight },
      renderMs: Number.isFinite(window.__phase24dRenderMs)
        && window.__phase24dRenderMs >= 0
        ? window.__phase24dRenderMs
        : null,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      outOfBounds,
      openDetails,
      chevrons,
      visibleModal: modal?.id || null,
      modalTitle: modal?.querySelector('h1, h2, h3')?.textContent.trim() || null,
      modalPrimaryLabel: modalPrimary?.textContent.trim() || null,
      modalPrimaryClass: modalPrimary?.className || null,
      visibleToast: toast?.dataset.state || null,
      noticeBorders: noticeStyle ? {
        top: noticeStyle.borderTopWidth,
        right: noticeStyle.borderRightWidth,
        bottom: noticeStyle.borderBottomWidth,
        left: noticeStyle.borderLeftWidth,
      } : null,
      dragActiveCount: [...root.querySelectorAll('.is-dragover')].filter(visible).length,
      mobileNavigationOpen: document.body.classList.contains('rail-open'),
      projectSwitch: window.__auditProjectSwitch || null,
      newProject: (() => {
        const dialog = document.getElementById('newProjectModal');
        const body = dialog?.querySelector('.new-project-body');
        const footer = dialog?.querySelector('.new-project-footer');
        const footerRect = footer?.getBoundingClientRect();
        const status = document.getElementById('new-project-status');
        const projectState = window.AlexandriaCanonicalInterface?.state()?.newProject;
        return {
          visible: visible(dialog),
          sectionCount: dialog?.querySelectorAll('.new-project-section').length || 0,
          radiogroupCount: dialog?.querySelectorAll('[role="radiogroup"]').length || 0,
          fauxStepperCount: dialog?.querySelectorAll('.new-project-section-number, .new-project-stepper').length || 0,
          footerVisible: visible(footer),
          footerWithinViewport: Boolean(
            footerRect
            && footerRect.top >= -1
            && footerRect.bottom <= window.innerHeight + 1
          ),
          bodyScrollable: Boolean(body && body.scrollHeight > body.clientHeight + 1),
          submitDisabled: document.getElementById('new-project-submit')?.disabled ?? null,
          submitLabel: document.getElementById('new-project-submit')?.textContent.trim() || null,
          sourceSummaryVisible: visible(document.getElementById('new-project-source-summary')),
          sourceFilename: document.getElementById('new-project-source-filename')?.textContent.trim() || null,
          bookTitle: document.getElementById('new-project-title')?.value || null,
          author: document.getElementById('new-project-author')?.value || null,
          sourceLanguage: document.getElementById('new-project-source-language')?.value || null,
          outputLanguage: document.getElementById('new-project-output-language')?.value || null,
          method: document.querySelector('input[name="new-project-method"]:checked')?.value || null,
          preset: document.querySelector('input[name="new-project-preset"]:checked')?.value || null,
          inspectionValid: projectState?.inspection?.valid || false,
          inspectionMethod: projectState?.inspection?.generation_method || null,
          completed: projectState?.completed || false,
          destination: window.AlexandriaNavigation?.current()?.destination || null,
          pageTitle: document.getElementById('shell-page-title')?.textContent.trim() || null,
          primaryAction: visible(document.getElementById('shell-primary-action'))
            ? document.getElementById('shell-primary-action').textContent.trim()
            : null,
          scriptStageState: window.AlexandriaCanonicalInterface?.state()?.flow?.stage_map?.script?.state || null,
          scriptStageAction: window.AlexandriaCanonicalInterface?.state()?.flow?.stage_map?.script?.safe_next_action?.id || null,
          scriptStageBlockers: (window.AlexandriaCanonicalInterface?.state()?.flow?.stage_map?.script?.blockers || []).map(item => item.code),
          statusVisible: visible(status),
          statusState: status?.dataset.state || null,
          statusText: document.getElementById('new-project-status-copy')?.textContent.trim() || null,
          advancedOpen: document.getElementById('new-project-advanced')?.open || false,
          disallowedRuntimeCopy: /model names?|cache locations?|context length|prompt templates?/i.test(
            dialog?.innerText || ''
          ),
        };
      })(),
      visibleTableState: tableState?.dataset.state || null,
      tableShellVisible: visible(tableShell),
      characterToolContext: (() => {
        const context = [...root.querySelectorAll('[data-character-tool-context]')]
          .find(visible);
        return {
          visible: Boolean(context),
          tool: context?.dataset.characterToolContext || null,
          name: context?.querySelector('[data-character-context-name]')?.textContent.trim() || null,
          meta: context?.querySelector('[data-character-context-meta]')?.textContent.trim() || null,
          returnLabel: context?.querySelector('button')?.textContent.trim() || null,
          designerName: document.getElementById('design-voice-name')?.value || null,
        };
      })(),
      modelCache: '${tab}' === 'setup' ? (() => {
        const panel = document.getElementById('model-cache-panel');
        const progress = document.getElementById('model-cache-progress');
        const error = document.getElementById('model-cache-error');
        return {
          open: panel?.open ?? null,
          badge: document.getElementById('model-cache-badge')?.textContent.trim() || null,
          summary: document.getElementById('model-cache-summary')?.textContent.trim() || null,
          location: document.getElementById('model-cache-location')?.textContent.trim() || null,
          rowCount: document.querySelectorAll('#model-cache-list .model-cache-row').length,
          stateLabels: [...document.querySelectorAll('#model-cache-list .resource-row-title .resource-status')]
            .map(element => element.textContent.trim()),
          actionLabels: [...document.querySelectorAll('#model-cache-list [data-model-cache-action]')]
            .map(element => element.textContent.trim()),
          technicalCount: document.querySelectorAll('#model-cache-list .model-cache-technical').length,
          requiredButtonLabel: document.getElementById('btn-model-cache-download-required')?.textContent.trim() || null,
          requiredButtonDisabled: document.getElementById('btn-model-cache-download-required')?.disabled ?? null,
          progressVisible: visible(progress),
          progressLabel: document.getElementById('model-cache-progress-label')?.textContent.trim() || null,
          progressCount: document.getElementById('model-cache-progress-count')?.textContent.trim() || null,
          progressNow: progress?.querySelector('.progress')?.getAttribute('aria-valuenow') || null,
          errorVisible: visible(error),
          errorText: error?.textContent.trim() || null,
        };
      })() : null,
      llmProfiles: '${tab}' === 'setup' ? (() => {
        const selected = window.llmProfilesStatus?.stages?.find(
          stage => stage.stage === window.llmProfilesSelectedStage
        );
        return {
          selectedStage: window.llmProfilesSelectedStage || null,
          profilesFingerprint: window.llmProfilesStatus?.profiles_fingerprint || null,
          configured: selected?.configured || false,
          enabled: selected?.enabled || false,
          inheritsGlobal: selected?.inherits_global ?? null,
          effectiveModel: selected?.effective_model || null,
          modelChanged: selected?.model_changed || false,
          evidenceComplete: selected?.evidence_complete || false,
          contextOverride: selected?.overrides?.context_length ?? null,
          evidenceVisible: (() => {
            const evidence = document.getElementById('llm-profile-evidence');
            return evidence ? !evidence.hidden : null;
          })(),
          modelInput: document.getElementById('llm-profile-model')?.value || null,
        };
      })() : null,
      recoveryPollingActive: window.__recoveryPollingActive === true,
      recovery: '${tab}' === 'setup' ? (() => {
        const details = document.getElementById('recovery-center');
        const stageCards = [...document.querySelectorAll('[data-recovery-stage-card]')];
        return {
          open: details?.open ?? null,
          bodyVisible: visible(details?.querySelector('.recovery-center-body')),
          overallState: document.getElementById('recovery-overall-light')?.dataset.state || null,
          overallText: document.getElementById('recovery-overall-text')?.textContent.trim() || null,
          sourceText: document.getElementById('recovery-source-text')?.textContent.trim() || null,
          sourceState: document.getElementById('recovery-source-state')?.dataset.state || null,
          stageCount: stageCards.length,
          stages: stageCards.map(card => ({
            id: card.dataset.recoveryStageCard || null,
            state: card.dataset.state || null,
            primaryAction: card.querySelector(
              '.recovery-stage-action[data-recovery-destructive="false"]'
            )?.textContent.trim() || null,
            discardAction: card.querySelector(
              '.recovery-stage-action[data-recovery-destructive="true"]'
            )?.textContent.trim() || null,
          })),
        };
      })() : null,
      characterRosterLog: '${tab}' === 'characters' ? (() => {
        const disclosure = document.getElementById('character-roster-log-disclosure');
        const output = document.getElementById('character-roster-logs');
        return {
          open: disclosure?.open ?? null,
          lineCount: output
            ? output.textContent.split(/\\n/).filter(Boolean).length
            : 0,
          scrollTop: output?.scrollTop ?? null,
          scrollHeight: output?.scrollHeight ?? null,
          clientHeight: output?.clientHeight ?? null,
          audit: window.__characterRosterLogAudit || null,
        };
      })() : null,
      characterInspector: '${canonicalTab}' === 'characters' ? (() => {
        const detail = document.getElementById('voice-projects-detail');
        const voiceCard = detail?.querySelector('.voice-card');
        const topSections = [...(detail?.children || [])]
          .filter(element => element.matches?.('section.voice-project-section'));
        const topDetails = [...(detail?.children || [])]
          .filter(element => element.matches?.('details.character-secondary-details'));
        return {
          title: detail?.querySelector('.voice-project-title')?.textContent.trim() || null,
          subtitle: detail?.querySelector('.voice-project-subtitle')?.textContent.trim() || null,
          state: detail?.querySelector('.voice-project-header > .stage-page-state')?.textContent.trim() || null,
          sectionHeadings: topSections
            .map(section => section.querySelector(':scope > .voice-project-section-header h4')?.textContent.trim())
            .filter(Boolean),
          disclosureHeadings: topDetails
            .map(details => details.querySelector(':scope > summary strong')?.textContent.trim())
            .filter(Boolean),
          openDisclosures: topDetails
            .filter(details => details.open)
            .map(details => details.querySelector(':scope > summary strong')?.textContent.trim())
            .filter(Boolean),
          voiceType: voiceCard?.querySelector('.voice-type:checked')?.value || null,
          voiceName: voiceCard?.dataset.voice || null,
          aliasTarget: voiceCard?.querySelector('.alias-select')?.value || null,
          preparationIdentityVisible: visible(detail?.querySelector('.character-preparation-identity')),
          guidanceDraftButtonVisible: visible(detail?.querySelector('[data-voice-project-action="create"]')),
          descriptionEditable: (() => {
            const field = detail?.querySelector('#voice-project-persona-description, #voice-project-description');
            return Boolean(field && visible(field) && !field.readOnly && !field.disabled);
          })(),
          representativeTextEditable: (() => {
            const field = detail?.querySelector('#voice-project-persona-ref, #voice-project-ref-text');
            return Boolean(field && visible(field) && !field.readOnly && !field.disabled);
          })(),
          oldTopLevelHeadingCount: [...(detail?.querySelectorAll('h4') || [])]
            .filter(heading => ['Voice persona', 'Production voice'].includes(heading.textContent.trim()))
            .length,
          createVoicePersonaCount: [...(detail?.querySelectorAll('button') || [])]
            .filter(button => button.textContent.trim() === 'Create voice persona')
            .length,
          visibleCharacterListCount: [...document.querySelectorAll('#characters-tab #voice-projects-list')]
            .filter(visible).length,
          detailScrollHeight: detail?.scrollHeight || 0,
        };
      })() : null,
      externalWorkflow: '${tab}' === 'script' ? (() => {
        const details = document.getElementById('script-external-workflow');
        const candidate = document.getElementById('external-script-candidate');
        const summary = document.querySelector('.external-candidate-summary');
        const utilityStatusRows = [
          ...document.querySelectorAll(
            '.script-utility-stack > .utility-disclosure > summary'
          )
        ].filter(row => row.querySelector('.stage-page-state')).map(row => {
          const status = row.querySelector('.stage-page-state');
          const rowRect = row.getBoundingClientRect();
          const statusRect = status?.getBoundingClientRect();
          return {
            label: row.querySelector('span:first-child')?.textContent.trim() || '',
            rowWidth: rowRect.width,
            statusRightGap: statusRect ? rowRect.right - statusRect.right : null,
            statusCenterRatio: statusRect && rowRect.width
              ? ((statusRect.left + statusRect.width / 2) - rowRect.left) / rowRect.width
              : null,
          };
        });
        const visuallyExposedFileInput = input => {
          if (!visible(input)) return false;
          const style = getComputedStyle(input);
          return style.opacity !== '0'
            && style.clipPath === 'none'
            && style.clip === 'auto';
        };
        return {
          open: details?.open ?? null,
          candidateVisible: visible(candidate),
          statusText: document.getElementById('external-workflow-status')?.textContent.trim() || null,
          statusState: document.getElementById('external-workflow-status')?.dataset.state || null,
          provenanceText: document.getElementById('external-candidate-provenance')?.textContent.trim() || null,
          provenanceState: document.getElementById('external-candidate-provenance')?.dataset.state || null,
          entryText: document.getElementById('external-candidate-entries')?.textContent.trim() || null,
          speakerText: document.getElementById('external-candidate-speakers')?.textContent.trim() || null,
          characterText: document.getElementById('external-candidate-characters')?.textContent.trim() || null,
          comparisonText: document.getElementById('external-candidate-comparison')?.textContent.trim() || null,
          consequenceText: document.getElementById('external-candidate-consequences')?.textContent.trim() || null,
          warningCount: document.querySelectorAll('#external-candidate-warnings li').length,
          sourceWarningVisible: visible(
            document.getElementById('external-source-warning')
          ),
          checkpointVisible: visible(document.getElementById('external-checkpoint-choice')),
          checkpointRadioCount: document.querySelectorAll('input[name="external-checkpoint-decision"]').length,
          applyVisible: visible(document.getElementById('btn-apply-external-script')),
          rollbackVisible: visible(document.getElementById('btn-rollback-external-script')),
          resultInputVisible: visuallyExposedFileInput(
            document.getElementById('completed-task-file')
          ),
          importInputVisible: visuallyExposedFileInput(
            document.getElementById('external-annotated-script-file')
          ),
          structuredVisible: visible(
            document.getElementById('external-structured-result')
          ),
          structuredStatus: document.getElementById('external-structured-result-status')?.textContent.trim() || null,
          structuredState: document.getElementById('external-structured-result-status')?.dataset.state || null,
          structuredTask: document.getElementById('external-structured-result-task')?.textContent.trim() || null,
          structuredDestination: document.getElementById('external-structured-result-destination')?.textContent.trim() || null,
          structuredCount: document.getElementById('external-structured-result-count')?.textContent.trim() || null,
          structuredJson: document.getElementById('external-structured-result-json')?.textContent.trim() || null,
          taskOptionCount: document.getElementById('task-bundle-task')?.options.length || 0,
          taskSelectedValue: document.getElementById('task-bundle-task')?.value || null,
          taskSummaryText: document.getElementById('task-bundle-selection-summary')?.textContent.trim() || null,
          taskPanelCount: document.querySelectorAll('.task-bundle-panel').length,
          obsoleteControlCount: [
            'external-handoff-id',
            'btn-copy-external-handoff-id',
            'external-stage-task',
            'btn-transfer-structured-result'
          ].filter(id => document.getElementById(id)).length,
          importButtonVisible: visible(
            document.getElementById('btn-import-completed-task')
          ),
          openDestinationVisible: visible(
            document.getElementById('btn-open-structured-destination')
          ),
          personaConflictVisible: visible(
            document.getElementById('persona-catalog-conflicts')
          ),
          personaConflictCount: document.querySelectorAll('.persona-catalog-conflict').length,
          personaNewCount: document.getElementById('persona-catalog-new-count')?.textContent.trim() || null,
          personaReplacementCount: document.querySelectorAll('.persona-catalog-replace').length,
          personaComparisonColumnCount: document.querySelectorAll('.persona-catalog-version').length,
          targetLabel: document.getElementById('task-bundle-target-label')?.textContent.trim() || null,
          targetFieldHidden: document.getElementById('task-bundle-target-field')?.hidden ?? null,
          utilityStatusRows,
          summaryRect: summary ? {
            width: summary.getBoundingClientRect().width,
            height: summary.getBoundingClientRect().height,
          } : null,
        };
      })() : null,
      voiceCapabilities: '${tab}' === 'training' ? {
        stableOutcome: window._voiceBackendCapabilities?.stable_lora_outcome || null,
        trainingSupported: window._voiceBackendCapabilities?.lora_training_supported ?? null,
        sidecarTrainingSupported: window._voiceBackendCapabilities?.experimental_lora_sidecar?.training_supported ?? null,
        inferenceSupported: window._voiceBackendCapabilities?.lora_inference_supported ?? null,
        controlledCloneSupported: window._voiceBackendCapabilities?.expressive_clone?.supported ?? null,
        measurementRows: document.querySelectorAll('#voice-capability-measurements tr').length,
        trainingControlsDisabled: document.getElementById('lora-training-controls')?.disabled ?? null,
        trainingButtonDisabled: document.getElementById('btn-lora-train')?.disabled ?? null,
        trainingButtonLabel: document.getElementById('btn-lora-train')?.textContent.trim() || null,
        targetProfilePresent: Boolean(document.getElementById('lora-target-profile')),
        validationFractionPresent: Boolean(document.getElementById('lora-validation-fraction')),
        testFormVisible: visible(document.getElementById('lora-test-form')),
        primaryLabel: document.querySelector('#voice-backend-capability .btn-primary')?.textContent.trim() || null,
      } : null,
      cloneEditor: '${tab}' === 'voices' ? (() => {
        const card = [...root.querySelectorAll('.voice-panel[open]')].find(visible);
        const cloneOptions = card?.querySelector('.clone-opts');
        return {
          speaker: card?.dataset.voice || null,
          voiceType: card?.querySelector('.voice-type:checked')?.value || null,
          savedBackend: card?.dataset.savedCloneBackend || null,
          cloneOptionsVisible: visible(cloneOptions),
          labeledFieldCount: cloneOptions?.querySelectorAll('label.form-label').length || 0,
          identityCopy: cloneOptions?.querySelector('[data-clone-identity-copy]')?.textContent.trim() || null,
          referenceText: cloneOptions?.querySelector('.ref-text')?.value || null,
          characterStyle: cloneOptions?.querySelector('.clone-character-style')?.value || null,
          controlledDisclosureRendered: Boolean(cloneOptions?.querySelector('.clone-controlled-disclosure')),
          controlledDisclosureVisible: visible(cloneOptions?.querySelector('.clone-controlled-disclosure')),
          controlledBackendStatus: cloneOptions?.querySelector('.clone-controlled-backend-status')?.textContent.trim() || null,
          controlledPreviewStatus: cloneOptions?.querySelector('[data-controlled-preview-status]')?.textContent.trim() || null,
          controlledUseButtonDisabled: cloneOptions?.querySelector('.controlled-use-button')?.disabled ?? null,
          controlledUseButtonLabel: cloneOptions?.querySelector('.controlled-use-button')?.textContent.trim() || null,
          controlledStandardButtonHidden: cloneOptions?.querySelector('.controlled-standard-button')?.hidden ?? null,
          controlledCfg: cloneOptions?.querySelector('.controlled-clone-cfg')?.value || null,
          controlledSteps: cloneOptions?.querySelector('.controlled-clone-steps')?.value || null,
          controlledMaxTokens: cloneOptions?.querySelector('.controlled-clone-max-tokens')?.value || null,
          previewFingerprint: card?.dataset.controlledPreviewFingerprint || null,
          configurationFingerprint: card?.dataset.controlledConfigurationFingerprint || null,
          approvalTokenPresent: Boolean(card?.dataset.controlledCloneApprovalToken),
          previewPlayed: card?.dataset.controlledPreviewPlayed || null,
          previewListened: card?.dataset.controlledPreviewListened || null,
          aliasValue: card?.querySelector('.alias-select')?.value || null,
          independentHidden: card?.querySelector('.voice-independent-config')?.hidden ?? null,
          aliasSummaryHidden: card?.querySelector('.voice-alias-inheritance')?.hidden ?? null,
          aliasResolvedTarget: card?.querySelector('[data-alias-resolved-target]')?.textContent.trim() || null,
          aliasResolvedType: card?.querySelector('[data-alias-resolved-type]')?.textContent.trim() || null,
          aliasResolvedSource: card?.querySelector('[data-alias-resolved-source]')?.textContent.trim() || null,
          aliasChain: card?.querySelector('[data-alias-chain]')?.textContent.trim() || null,
          aliasEditTarget: card?.querySelector('.alias-edit-target')?.dataset.aliasEditTarget || null,
          audit: window.__controlledCloneAudit || null,
          uploadAudit: window.__cloneUploadAudit || null,
          audioAudit: window.__cloneAudioAudit || null,
          aliasAudit: window.__voiceAliasAudit || null,
        };
      })() : null,
      speakerManagement: '${tab}' === 'speaker-management' ? {
        selectedEntryId: window.speakerManagementSelectedId || null,
        selectedName: window.speakerManagementSelectedName || null,
        scriptFingerprint: window.speakerManagementStatus?.script_fingerprint || null,
        historyCount: window.speakerManagementStatus?.history?.length || 0,
        latestAudioInvalidationCount: window.speakerManagementStatus?.history?.[0]?.audio_invalidation_count || 0,
        selectedLineCount: window.speakerManagementStatus?.lines?.length || 0,
      } : null,
      datasetProgress: '${tab}' === 'dataset-builder' ? {
        hidden: document.getElementById('dsb-progress-wrap')?.hidden ?? null,
        label: document.getElementById('dsb-progress-label')?.textContent.trim() || null,
        barText: document.getElementById('dsb-progress-bar')?.textContent.trim() || null,
        ariaNow: document.getElementById('dsb-progress-bar')?.getAttribute('aria-valuenow') || null,
      } : null,
      voiceProject: '${tab}' === 'voice-projects' ? (() => {
        const referenceSection = document.getElementById('voice-reference-bank-section');
        const approveButton = referenceSection?.querySelector(
          '[data-reference-bank-action="approve-bank"]'
        );
        const summary = referenceSection?.querySelector('.reference-bank-summary');
        const styleCards = [...(
          referenceSection?.querySelectorAll('[data-reference-style-card]') || []
        )];
        return {
          selectedCharacterId: window.voiceTrainingSelectedId || null,
          personaStatus: window.voiceTrainingProject?.desired_base_persona?.approval_status || null,
          syntheticStatus: window.voiceTrainingProject?.designed_voice_project?.status || null,
          recordingStatus: window.voiceTrainingProject?.existing_recordings?.status || null,
          readinessStatus: window.voiceTrainingProject?.training_readiness?.status || null,
          fingerprint: window.voiceTrainingProject?.project_fingerprint || null,
          referenceBankVisible: visible(referenceSection),
          referenceBankStatus: window.expressiveReferenceBank?.status || null,
          referenceBankStyleCount: styleCards.length,
          referenceBankAudioCount: referenceSection?.querySelectorAll('audio').length || 0,
          referenceBankComparisonOutputCount: referenceSection?.querySelectorAll(
            '.reference-comparison-output'
          ).length || 0,
          referenceBankReviewButtonCount: referenceSection?.querySelectorAll(
            '[data-reference-bank-action="review-reference"]'
          ).length || 0,
          referenceBankApproveDisabled: approveButton?.disabled ?? null,
          referenceBankAssignVisible: visible(referenceSection?.querySelector(
            '[data-reference-bank-action="assign"]'
          )),
          referenceBankIdentityCopy: referenceSection?.textContent
            .replace(/\\s+/g, ' ')
            .trim() || null,
          referenceBankSummaryColumns: summary
            ? getComputedStyle(summary).gridTemplateColumns.split(' ').length
            : 0,
          referenceBankStyleColumns: styleCards[0]
            ? getComputedStyle(styleCards[0].parentElement).gridTemplateColumns.split(' ').length
            : 0,
        };
      })() : null,
      mainOutline: main ? {
        style: getComputedStyle(main).outlineStyle,
        width: getComputedStyle(main).outlineWidth,
      } : null,
      activeElement: document.activeElement?.id || document.activeElement?.tagName || null,
    };
  })()`);
  result.screenshotBytes = await writeScreenshot(client, screenshotPath);
  result.screenshotPath = screenshotPath;
  if (canonicalTab === 'characters') {
    await evaluate(client, `(async () => {
      await refreshCharactersWorkspace();
      window.scrollTo(0, 0);
    })()`);
    await wait(250);
  }
  return result;
}

async function inspectBoundary13Interactions(client, screenshotPath) {
  await client.send('Emulation.setDeviceMetricsOverride', {
    width: 1536,
    height: 1024,
    deviceScaleFactor: 1,
    mobile: false,
  });

  await evaluate(client, `document.querySelector('[data-route="voices"]')?.click()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'voices'
      && document.getElementById('shell-primary-action')?.textContent.trim() === 'Create Voice'
      && !document.getElementById('shell-primary-action')?.disabled
    ))()`);
    if (ready) break;
    await wait(100);
  }
  await evaluate(client, `document.getElementById('shell-primary-action')?.click()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const routed = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.context?.tool === 'voice-designer'
      && document.getElementById('shell-global-title')?.textContent.trim() === 'Voice designer'
    ))()`);
    if (routed) break;
    await wait(100);
  }
  const createVoice = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    return {
      destination: route?.destination || null,
      tool: route?.context?.tool || null,
      returnRoute: route?.context?.return || null,
      pageTitle: document.getElementById('shell-global-title')?.textContent.trim() || null,
    };
  })()`);

  await evaluate(client, `document.querySelector('[data-route="templates"]')?.click()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'templates'
      && !document.getElementById('template-content')?.hidden
      && document.querySelectorAll('#template-list [data-template-id]').length >= 6
      && document.getElementById('shell-primary-action')?.textContent.trim() === 'New Template'
    ))()`);
    if (ready) break;
    await wait(100);
  }
  const templateInitial = await evaluate(client, `(() => ({
    rowCount: document.querySelectorAll('#template-list [data-template-id]').length,
    defaultId: document.querySelector('#template-list [data-template-id] .supporting-state')?.textContent.trim() === 'Default'
      ? document.querySelector('#template-list [data-template-id]')?.dataset.templateId
      : null,
    hiddenInternalLabels: [...document.querySelectorAll('#templates-workspace label')]
      .map(label => label.textContent.trim())
      .filter(label => /model|prompt|context|cache|api key/i.test(label)),
  }))()`);

  await evaluate(client, `document.getElementById('shell-primary-action')?.click()`);
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const visible = await evaluate(client, `document.getElementById('templateEditorModal')?.classList.contains('show')`);
    if (visible) break;
    await wait(100);
  }
  await evaluate(client, `(() => {
    document.getElementById('template-editor-name').value = 'QA Swedish Template';
    document.getElementById('template-editor-intent').value = 'High-fidelity Swedish production';
    document.getElementById('template-editor-description-field').value = 'Browser-created reviewed template.';
    document.getElementById('template-editor-method').value = 'local';
    document.getElementById('template-editor-preset').value = 'maximum_fidelity';
    document.getElementById('template-editor-source-language').value = 'English';
    document.getElementById('template-editor-output-language').value = 'Swedish';
    document.getElementById('template-editor-form').requestSubmit();
  })()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const saved = await evaluate(client, `(() => (
      !document.getElementById('templateEditorModal')?.classList.contains('show')
      && [...document.querySelectorAll('#template-list [data-template-id]')]
        .some(row => row.textContent.includes('QA Swedish Template'))
    ))()`);
    if (saved) break;
    await wait(100);
  }
  const createdTemplateId = await evaluate(client, `(() => (
    [...document.querySelectorAll('#template-list [data-template-id]')]
      .find(row => row.textContent.includes('QA Swedish Template'))?.dataset.templateId || null
  ))()`);
  await evaluate(client, `(() => {
    const row = [...document.querySelectorAll('#template-list [data-template-id]')]
      .find(item => item.dataset.templateId === ${JSON.stringify(createdTemplateId)});
    row?.click();
    document.querySelector('[data-template-edit]')?.click();
  })()`);
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const visible = await evaluate(client, `document.getElementById('templateEditorModal')?.classList.contains('show')`);
    if (visible) break;
    await wait(100);
  }
  await evaluate(client, `(() => {
    document.getElementById('template-editor-name').value = 'QA Swedish Publication';
    document.getElementById('template-editor-preset').value = 'standard';
    document.getElementById('template-editor-form').requestSubmit();
  })()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const saved = await evaluate(client, `(() => (
      !document.getElementById('templateEditorModal')?.classList.contains('show')
      && [...document.querySelectorAll('#template-list [data-template-id]')]
        .some(row => row.textContent.includes('QA Swedish Publication'))
    ))()`);
    if (saved) break;
    await wait(100);
  }

  await evaluate(client, `document.querySelector('[data-template-duplicate]')?.click()`);
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const visible = await evaluate(client, `document.getElementById('textPromptModal')?.classList.contains('show')`);
    if (visible) break;
    await wait(100);
  }
  await evaluate(client, `(() => {
    document.getElementById('textPromptInput').value = 'QA Swedish Publication Copy';
    document.getElementById('textPromptForm').requestSubmit();
  })()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const saved = await evaluate(client, `(() => (
      !document.getElementById('textPromptModal')?.classList.contains('show')
      && [...document.querySelectorAll('#template-list [data-template-id]')]
        .some(row => row.textContent.includes('QA Swedish Publication Copy'))
    ))()`);
    if (saved) break;
    await wait(100);
  }
  await evaluate(client, `document.querySelector('[data-template-default]')?.click()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `document.getElementById('template-detail')?.innerText.includes('Current default')`);
    if (ready) break;
    await wait(100);
  }
  const defaultCopyId = await evaluate(client, `document.querySelector('#template-list [aria-selected="true"]')?.dataset.templateId || null`);

  await evaluate(client, `document.querySelector('[data-template-use]')?.click()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      document.getElementById('newProjectModal')?.classList.contains('show')
      && !document.getElementById('new-project-template-context')?.hidden
      && document.getElementById('new-project-output-language')?.value === 'Swedish'
    ))()`);
    if (ready) break;
    await wait(100);
  }
  const templateApplied = await evaluate(client, `(() => ({
    destination: window.AlexandriaNavigation?.current()?.destination || null,
    modalVisible: document.getElementById('newProjectModal')?.classList.contains('show') || false,
    templateName: document.getElementById('new-project-template-name')?.textContent.trim() || null,
    method: document.querySelector('input[name="new-project-method"]:checked')?.value || null,
    preset: document.querySelector('input[name="new-project-preset"]:checked')?.value || null,
    sourceLanguage: document.getElementById('new-project-source-language')?.value || null,
    outputLanguage: document.getElementById('new-project-output-language')?.value || null,
    advancedOpen: document.getElementById('new-project-advanced')?.open ?? null,
  }))()`);
  await evaluate(client, `document.querySelector('#newProjectModal [data-bs-dismiss="modal"]')?.click()`);
  await wait(350);

  await evaluate(client, `document.querySelector('[data-route="templates"]')?.click()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `document.querySelectorAll('#template-list [data-template-id]').length >= 8`);
    if (ready) break;
    await wait(100);
  }
  await evaluate(client, `(() => {
    document.querySelector('[data-template-id="builtin_standard"]')?.click();
    document.querySelector('[data-template-default]')?.click();
  })()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `document.getElementById('template-detail')?.innerText.includes('Current default')`);
    if (ready) break;
    await wait(100);
  }
  await evaluate(client, `(() => {
    const row = [...document.querySelectorAll('#template-list [data-template-id]')]
      .find(item => item.textContent.includes('QA Swedish Publication') && !item.textContent.includes('Copy'));
    row?.click();
    document.querySelector('[data-template-delete]')?.click();
  })()`);
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const visible = await evaluate(client, `document.getElementById('textPromptModal')?.classList.contains('show')`);
    if (visible) break;
    await wait(100);
  }
  await evaluate(client, `(() => {
    document.getElementById('textPromptInput').value = 'QA Swedish Publication';
    document.getElementById('textPromptForm').requestSubmit();
  })()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const deleted = await evaluate(client, `(() => (
      ![...document.querySelectorAll('#template-list [data-template-id]')]
        .some(row => row.textContent.includes('QA Swedish Publication') && !row.textContent.includes('Copy'))
    ))()`);
    if (deleted) break;
    await wait(100);
  }
  await evaluate(client, `(() => {
    const search = document.getElementById('template-search');
    search.value = 'Copy';
    search.dispatchEvent(new Event('input', { bubbles: true }));
    const scope = document.getElementById('template-scope-filter');
    scope.value = 'custom';
    scope.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const ready = await evaluate(client, `(() => {
      const route = window.AlexandriaNavigation?.current();
      return route?.context?.search === 'Copy'
        && route?.context?.filter === 'custom'
        && document.querySelectorAll('#template-list [data-template-id]').length === 1;
    })()`);
    if (ready) break;
    await wait(100);
  }
  const templateCrud = await evaluate(client, `(() => ({
    initialRowCount: ${JSON.stringify(null)},
    createdTemplateId: ${JSON.stringify(createdTemplateId)},
    defaultCopyId: ${JSON.stringify(defaultCopyId)},
    rowCount: document.querySelectorAll('#template-list [data-template-id]').length,
    rowText: document.querySelector('#template-list [data-template-id]')?.textContent.trim() || null,
    search: document.getElementById('template-search')?.value || null,
    scope: document.getElementById('template-scope-filter')?.value || null,
    hash: window.AlexandriaNavigation?.current()?.hash || window.location.hash,
    deletedOriginal: ![...document.querySelectorAll('#template-list [data-template-id]')]
      .some(row => row.textContent.includes('QA Swedish Publication') && !row.textContent.includes('Copy')),
    hiddenInternalLabels: ${JSON.stringify(null)},
  }))()`);
  templateCrud.initialRowCount = templateInitial.rowCount;
  templateCrud.hiddenInternalLabels = templateInitial.hiddenInternalLabels;

  await evaluate(client, `document.querySelector('[data-route="library"]')?.click()`);
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const ready = await evaluate(client, `window.AlexandriaNavigation?.current()?.destination === 'library'`);
    if (ready) break;
    await wait(100);
  }
  await evaluate(client, `window.history.back()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'templates'
      && document.getElementById('template-search')?.value === 'Copy'
      && document.getElementById('template-scope-filter')?.value === 'custom'
      && document.querySelectorAll('#template-list [data-template-id]').length === 1
    ))()`);
    if (ready) break;
    await wait(100);
  }
  const templateRestored = await evaluate(client, `(() => ({
    destination: window.AlexandriaNavigation?.current()?.destination || null,
    search: document.getElementById('template-search')?.value || null,
    scope: document.getElementById('template-scope-filter')?.value || null,
    rowCount: document.querySelectorAll('#template-list [data-template-id]').length,
    hash: window.AlexandriaNavigation?.current()?.hash || window.location.hash,
  }))()`);

  await evaluate(client, `window.AlexandriaNavigation?.navigate('cast', {
    project: 'help-browser-project',
    source: 'library_fixture_source',
    issue: 'issue_help_context',
    mode: 'review',
  }, { historyMode: 'push' })`);
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'cast'
      && !document.getElementById('cast-content')?.hidden
      && document.querySelectorAll('[data-cast-character-id]').length > 0
    ))()`);
    if (ready) break;
    await wait(100);
  }
  const contextualHelpCharacter = await evaluate(client, `document.querySelector('[data-cast-character-id]')?.dataset.castCharacterId || null`);
  await evaluate(client, `window.AlexandriaNavigation?.navigate('cast', {
    project: 'help-browser-project',
    character: ${JSON.stringify(contextualHelpCharacter)},
    source: 'library_fixture_source',
    issue: 'issue_help_context',
    mode: 'review',
  }, { historyMode: 'replace' })`);
  await wait(250);
  const contextualHelpOrigin = await evaluate(client, `window.AlexandriaNavigation?.current()?.hash || window.location.hash`);
  await evaluate(client, `(() => {
    document.querySelectorAll('.modal').forEach(modal => {
      window.bootstrap?.Modal?.getInstance(modal)?._focustrap?.deactivate?.();
    });
    document.activeElement?.blur?.();
    document.getElementById('main-content')?.focus({ preventScroll: true });
  })()`);
  await evaluate(client, `document.getElementById('project-help-action')?.click()`);
  for (let attempt = 0; attempt < 140; attempt += 1) {
    const ready = await evaluate(client, `(() => {
      const route = window.AlexandriaNavigation?.current();
      return route?.context?.tool === 'help-center'
        && route?.context?.help === 'cast'
        && route?.context?.source === 'library_fixture_source'
        && route?.context?.issue === 'issue_help_context'
        && route?.context?.mode === 'review'
        && route?.context?.return === ${JSON.stringify(contextualHelpOrigin)}
        && !document.getElementById('help-content')?.hidden
        && document.querySelectorAll('#help-topic-list .supporting-list-row').length === 9
        && document.querySelector('#help-topic-detail h2')?.textContent.trim() === 'Assign and verify Voices';
    })()`);
    if (ready) break;
    await wait(100);
  }
  const helpContextualEntry = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    return {
      destination: route?.destination || null,
      tool: route?.context?.tool || null,
      helpContext: route?.context?.help || null,
      topic: route?.context?.topic || null,
      project: route?.context?.project || null,
      character: route?.context?.character || null,
      source: route?.context?.source || null,
      issue: route?.context?.issue || null,
      mode: route?.context?.mode || null,
      returnRoute: route?.context?.return || null,
      origin: ${JSON.stringify(contextualHelpOrigin)},
      detailTitle: document.querySelector('#help-topic-detail h2')?.textContent.trim() || null,
      topicCount: document.querySelectorAll('[data-help-topic]').length,
      helpButtonsVisible: [
        document.getElementById('global-help-action'),
        document.getElementById('project-help-action'),
      ].some(button => button && !button.hidden),
    };
  })()`);
  const helpScreenshotBase = screenshotPath.replace(/\.png$/i, '');
  const helpContextualScreenshotPath = `${helpScreenshotBase}-help-contextual-wide.png`;
  helpContextualEntry.screenshotBytes = await writeScreenshot(client, helpContextualScreenshotPath);
  helpContextualEntry.screenshotPath = helpContextualScreenshotPath;

  await evaluate(client, `(() => {
    const search = document.getElementById('help-search');
    search.value = 'post-migration file hash';
    search.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  for (let attempt = 0; attempt < 140; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.context?.search === 'post-migration file hash'
      && document.querySelectorAll('[data-help-topic]').length === 1
      && document.querySelector('[data-help-topic]')?.dataset.helpTopic === 'maintenance'
      && document.querySelector('#help-topic-detail h2')?.textContent.trim() === 'Maintenance and recovery'
    ))()`);
    if (ready) break;
    await wait(100);
  }
  const helpBodySearch = await evaluate(client, `(() => ({
    search: document.getElementById('help-search')?.value || null,
    routeSearch: window.AlexandriaNavigation?.current()?.context?.search || null,
    rowCount: document.querySelectorAll('[data-help-topic]').length,
    selectedSlug: document.querySelector('[data-help-topic][aria-selected="true"]')?.dataset.helpTopic || null,
    resultCount: document.getElementById('help-result-count')?.textContent.trim() || null,
    detailTitle: document.querySelector('#help-topic-detail h2')?.textContent.trim() || null,
  }))()`);
  await client.send('Emulation.setDeviceMetricsOverride', {
    width: 1024,
    height: 768,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await wait(160);
  Object.assign(helpBodySearch, await evaluate(client, `(() => {
    const detail = document.getElementById('help-topic-detail');
    return {
      viewport: { width: window.innerWidth, height: window.innerHeight },
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      detailScrollHeight: detail?.scrollHeight || 0,
      detailClientHeight: detail?.clientHeight || 0,
      pageScrollHeight: document.documentElement.scrollHeight,
      pageClientHeight: document.documentElement.clientHeight,
    };
  })()`));
  const helpSearchCompactScreenshotPath = `${helpScreenshotBase}-help-search-compact.png`;
  helpBodySearch.screenshotBytes = await writeScreenshot(client, helpSearchCompactScreenshotPath);
  helpBodySearch.screenshotPath = helpSearchCompactScreenshotPath;
  await client.send('Emulation.setDeviceMetricsOverride', {
    width: 1536,
    height: 1024,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await wait(160);

  await evaluate(client, `(() => {
    const search = document.getElementById('help-search');
    search.value = '';
    search.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  for (let attempt = 0; attempt < 140; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      !window.AlexandriaNavigation?.current()?.context?.search
      && document.querySelectorAll('[data-help-topic]').length === 9
      && document.querySelector('[data-help-topic][aria-selected="true"]')?.dataset.helpTopic === 'cast'
    ))()`);
    if (ready) break;
    await wait(100);
  }
  await evaluate(client, `(() => {
    const selected = document.querySelector('[data-help-topic][aria-selected="true"]');
    selected?.focus({ preventScroll: true });
    selected?.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
  })()`);
  await wait(180);
  const helpKeyboardEnd = await evaluate(client, `(() => ({
    selectedSlug: document.querySelector('[data-help-topic][aria-selected="true"]')?.dataset.helpTopic || null,
    focusedSlug: document.activeElement?.dataset?.helpTopic || null,
    routeTopic: window.AlexandriaNavigation?.current()?.context?.topic || null,
    detailTitle: document.querySelector('#help-topic-detail h2')?.textContent.trim() || null,
  }))()`);
  await evaluate(client, `document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }))`);
  await wait(180);
  const helpKeyboardHome = await evaluate(client, `(() => ({
    selectedSlug: document.querySelector('[data-help-topic][aria-selected="true"]')?.dataset.helpTopic || null,
    focusedSlug: document.activeElement?.dataset?.helpTopic || null,
    routeTopic: window.AlexandriaNavigation?.current()?.context?.topic || null,
    detailTitle: document.querySelector('#help-topic-detail h2')?.textContent.trim() || null,
  }))()`);

  await evaluate(client, `document.querySelector('[data-help-topic="export"]')?.click()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `document.querySelector('#help-topic-detail h2')?.textContent.trim() === 'Validate and build the audiobook'`);
    if (ready) break;
    await wait(100);
  }
  await evaluate(client, `document.querySelector('#help-topic-detail .help-related-list button')?.click()`);
  await wait(180);
  const helpRelated = await evaluate(client, `(() => ({
    selectedSlug: document.querySelector('[data-help-topic][aria-selected="true"]')?.dataset.helpTopic || null,
    routeTopic: window.AlexandriaNavigation?.current()?.context?.topic || null,
    detailTitle: document.querySelector('#help-topic-detail h2')?.textContent.trim() || null,
  }))()`);

  await evaluate(client, `document.querySelector('[data-help-topic="cast"]')?.click()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `document.querySelector('#help-topic-detail h2')?.textContent.trim() === 'Assign and verify Voices'`);
    if (ready) break;
    await wait(100);
  }
  const helpRouteBeforeWorkflow = await evaluate(client, `window.AlexandriaNavigation?.current()?.hash || window.location.hash`);
  await evaluate(client, `(() => {
    const button = [...document.querySelectorAll('#help-topic-detail .help-context-link button')]
      .find(item => item.textContent.trim() === 'Voices');
    button?.click();
  })()`);
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'voices'
      && window.AlexandriaNavigation?.current()?.context?.return === ${JSON.stringify(helpRouteBeforeWorkflow)}
    ))()`);
    if (ready) break;
    await wait(100);
  }
  const helpWorkflowRoute = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    return {
      destination: route?.destination || null,
      returnRoute: route?.context?.return || null,
      project: route?.context?.project || null,
      character: route?.context?.character || null,
      source: route?.context?.source || null,
      issue: route?.context?.issue || null,
      mode: route?.context?.mode || null,
      help: route?.context?.help || null,
      topic: route?.context?.topic || null,
      search: route?.context?.search || null,
    };
  })()`);
  await evaluate(client, `window.history.back()`);
  for (let attempt = 0; attempt < 140; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.hash === ${JSON.stringify(helpRouteBeforeWorkflow)}
      && !document.getElementById('help-content')?.hidden
      && document.querySelector('#help-topic-detail h2')?.textContent.trim() === 'Assign and verify Voices'
    ))()`);
    if (ready) break;
    await wait(100);
  }
  const helpBack = await evaluate(client, `(() => ({
    hash: window.AlexandriaNavigation?.current()?.hash || window.location.hash,
    locationHash: window.location.hash,
    detailTitle: document.querySelector('#help-topic-detail h2')?.textContent.trim() || null,
  }))()`);
  await evaluate(client, `document.getElementById('help-return-more')?.click()`);
  for (let attempt = 0; attempt < 140; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.hash === ${JSON.stringify(contextualHelpOrigin)}
      && window.AlexandriaCanonicalInterface?.state()?.route?.hash === ${JSON.stringify(contextualHelpOrigin)}
      && document.body.dataset.destination === 'cast'
    ))()`);
    if (ready) break;
    await wait(100);
  }
  await wait(180);
  const helpReturned = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    return {
      hash: route?.hash || window.location.hash,
      locationHash: window.location.hash,
      destination: route?.destination || null,
      project: route?.context?.project || null,
      character: route?.context?.character || null,
      source: route?.context?.source || null,
      issue: route?.context?.issue || null,
      mode: route?.context?.mode || null,
    };
  })()`);
  const help = {
    ...helpContextualEntry,
    bodySearch: helpBodySearch,
    keyboardEnd: helpKeyboardEnd,
    keyboardHome: helpKeyboardHome,
    related: helpRelated,
    workflowRoute: helpWorkflowRoute,
    helpRouteBeforeWorkflow,
    back: helpBack,
    returned: helpReturned,
    workflowActionLabels: ['Cast', 'Voices'],
    relatedCount: 3,
    scriptElementCount: await evaluate(client, `document.querySelectorAll('#help-topic-detail script, #help-topic-detail iframe, #help-topic-detail object, #help-topic-detail embed, #help-topic-detail form, #help-topic-detail img, #help-topic-detail svg').length`),
  };

  await evaluate(client, `window.AlexandriaNavigation?.navigate('library', {}, { historyMode: 'push' })`);
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'library'
      && window.AlexandriaCanonicalInterface?.state()?.route?.destination === 'library'
      && document.querySelectorAll('#library-artifact-list .supporting-list-row').length > 0
    ))()`);
    if (ready) break;
    await wait(100);
  }
  await evaluate(client, `document.querySelector('[data-library-open]')?.click()`);
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const routed = await evaluate(client, `window.AlexandriaNavigation?.current()?.destination === 'more'`);
    if (routed) break;
    await wait(100);
  }
  const nativeTool = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    return {
      destination: route?.destination || null,
      tool: route?.context?.tool || null,
      returnRoute: route?.context?.return || null,
      character: route?.context?.character || null,
    };
  })()`);

  await evaluate(client, `document.querySelector('[data-route="library"]')?.click()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `document.querySelector('[data-library-kind="source_book"]') !== null`);
    if (ready) break;
    await wait(100);
  }
  await evaluate(client, `(() => {
    const search = document.getElementById('library-search');
    search.value = 'book';
    search.dispatchEvent(new Event('input', { bubbles: true }));
    const kind = document.getElementById('library-kind-filter');
    kind.value = 'source_book';
    kind.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const ready = await evaluate(client, `(() => {
      const route = window.AlexandriaNavigation?.current();
      return route?.destination === 'library'
        && route?.context?.search === 'book'
        && new URLSearchParams(route?.context?.filter || '').get('kind') === 'source_book'
        && document.querySelectorAll('[data-library-kind="source_book"]').length === 1;
    })()`);
    if (ready) break;
    await wait(100);
  }
  const filteredLibrary = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    return {
      hash: route?.hash || window.location.hash,
      search: document.getElementById('library-search')?.value || null,
      kind: document.getElementById('library-kind-filter')?.value || null,
      rowCount: document.querySelectorAll('#library-artifact-list .supporting-list-row').length,
      rowKind: document.querySelector('#library-artifact-list .supporting-list-row')?.dataset.libraryKind || null,
    };
  })()`);

  await evaluate(client, `document.querySelector('[data-route="templates"]')?.click()`);
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const ready = await evaluate(client, `window.AlexandriaNavigation?.current()?.destination === 'templates'`);
    if (ready) break;
    await wait(100);
  }
  await evaluate(client, `window.history.back()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'library'
      && document.getElementById('library-search')?.value === 'book'
      && document.getElementById('library-kind-filter')?.value === 'source_book'
      && document.querySelectorAll('[data-library-kind="source_book"]').length === 1
    ))()`);
    if (ready) break;
    await wait(100);
  }
  const restoredLibrary = await evaluate(client, `(() => ({
    hash: window.AlexandriaNavigation?.current()?.hash || window.location.hash,
    search: document.getElementById('library-search')?.value || null,
    kind: document.getElementById('library-kind-filter')?.value || null,
    rowCount: document.querySelectorAll('#library-artifact-list .supporting-list-row').length,
  }))()`);

  async function openLibraryKind(kind, destination) {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      const ready = await evaluate(client, `(() => (
        window.AlexandriaNavigation?.current()?.destination === 'library'
        && window.AlexandriaCanonicalInterface?.state()?.route?.destination === 'library'
        && !document.getElementById('library-content')?.hidden
        && document.getElementById('library-loading')?.hidden
        && [...document.getElementById('library-kind-filter')?.options || []]
          .some(option => option.value === ${JSON.stringify(kind)})
      ))()`);
      if (ready) break;
      await wait(100);
    }
    await evaluate(client, `(() => {
      const search = document.getElementById('library-search');
      search.value = '';
      search.dispatchEvent(new Event('input', { bubbles: true }));
      const select = document.getElementById('library-kind-filter');
      select.value = ${JSON.stringify(kind)};
      select.dispatchEvent(new Event('change', { bubbles: true }));
    })()`);
    for (let attempt = 0; attempt < 120; attempt += 1) {
      const ready = await evaluate(client, `(() => {
        const rows = [...document.querySelectorAll('#library-artifact-list .supporting-list-row')];
        const route = window.AlexandriaNavigation?.current();
        return document.getElementById('library-kind-filter')?.value === ${JSON.stringify(kind)}
          && new URLSearchParams(route?.context?.filter || '').get('kind') === ${JSON.stringify(kind)}
          && rows.length >= 1
          && rows.every(row => row.dataset.libraryKind === ${JSON.stringify(kind)});
      })()`);
      if (ready) break;
      await wait(100);
    }
    await evaluate(client, `document.querySelector('[data-library-kind=${JSON.stringify(kind)}]')?.click()`);
    await wait(100);
    await evaluate(client, `document.querySelector('[data-library-open]')?.click()`);
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const ready = await evaluate(client, `window.AlexandriaNavigation?.current()?.destination === ${JSON.stringify(destination)}`);
      if (ready) break;
      await wait(100);
    }
    const result = await evaluate(client, `(() => {
      const route = window.AlexandriaNavigation?.current();
      return {
        destination: route?.destination || null,
        source: route?.context?.source || null,
        returnRoute: route?.context?.return || null,
      };
    })()`);
    await evaluate(client, `window.history.back()`);
    for (let attempt = 0; attempt < 120; attempt += 1) {
      const ready = await evaluate(client, `(() => {
        const route = window.AlexandriaNavigation?.current();
        const rows = [...document.querySelectorAll('#library-artifact-list .supporting-list-row')];
        return route?.destination === 'library'
          && window.AlexandriaCanonicalInterface?.state()?.route?.destination === 'library'
          && document.getElementById('library-kind-filter')?.value === ${JSON.stringify(kind)}
          && rows.length >= 1
          && rows.every(row => row.dataset.libraryKind === ${JSON.stringify(kind)});
      })()`);
      if (ready) break;
      await wait(100);
    }
    return result;
  }

  const workflowRoutes = {
    sourceBook: await openLibraryKind('source_book', 'script'),
    productionAudio: await openLibraryKind('production_audio', 'produce'),
    exportOutput: await openLibraryKind('export_output', 'export'),
  };

  await evaluate(client, `document.querySelector('[data-route="voices"]')?.click()`);
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'voices'
      && document.querySelectorAll('#library-artifact-list .supporting-list-row').length >= 10
      && document.querySelector('[data-library-kind="instruction_controlled"]') !== null
      && document.querySelector('[data-library-kind="alias"]') !== null
    ))()`);
    if (ready) break;
    await wait(100);
  }
  const voiceLibrary = await evaluate(client, `(() => ({
    rowCount: document.querySelectorAll('#library-artifact-list .supporting-list-row').length,
    methods: [...document.querySelectorAll('#library-artifact-list .supporting-list-row')]
      .map(row => row.dataset.libraryKind),
    searchPlaceholder: document.getElementById('library-search')?.placeholder || null,
    listHeading: document.getElementById('library-list-heading')?.textContent?.trim() || null,
  }))()`);

  await evaluate(client, `document.querySelector('[data-library-kind="instruction_controlled"]')?.click()`);
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const ready = await evaluate(client, `document.getElementById('library-artifact-detail')?.innerText.includes('Line instruction')`);
    if (ready) break;
    await wait(100);
  }
  const controlledVoice = await evaluate(client, `(() => ({
    text: document.getElementById('library-artifact-detail')?.innerText || '',
    hasCastAction: document.querySelector('[data-voice-cast]') !== null,
  }))()`);

  const suppliedVoiceId = await evaluate(client, `(() => {
    const state = window.AlexandriaCanonicalInterface?.state();
    return (state?.library?.inventory?.artifacts || [])
      .find(item => item.kind === 'supplied_recording' && Number(item.dependency_count || 0) > 0)?.artifact_id || null;
  })()`);
  await evaluate(client, `document.querySelector('[data-library-artifact=${JSON.stringify(suppliedVoiceId)}]')?.click()`);
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      document.getElementById('library-artifact-detail')?.innerText.includes('Line instruction')
      && document.querySelector('[data-voice-preview]') !== null
      && document.querySelector('[data-voice-cast-character]') !== null
    ))()`);
    if (ready) break;
    await wait(100);
  }
  await evaluate(client, `document.querySelector('[data-voice-preview]')?.click()`);
  await wait(250);
  const suppliedVoice = await evaluate(client, `(() => ({
    text: document.getElementById('library-artifact-detail')?.innerText || '',
    playerHidden: document.getElementById('persistent-player-host')?.hidden ?? true,
    playerTitle: document.getElementById('persistent-player-title')?.textContent?.trim() || null,
    audioSource: document.querySelector('#main-audio source')?.getAttribute('src') || document.getElementById('main-audio')?.getAttribute('src') || null,
    usageCount: document.querySelectorAll('[data-voice-cast-character]').length,
  }))()`);
  const voiceReturnRoute = await evaluate(client, `window.AlexandriaNavigation?.current()?.hash || window.location.hash`);
  await evaluate(client, `document.querySelector('[data-voice-cast-character]')?.click()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `window.AlexandriaNavigation?.current()?.destination === 'cast'`);
    if (ready) break;
    await wait(100);
  }
  const voiceCastRoute = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    return {
      destination: route?.destination || null,
      project: route?.context?.project || null,
      character: route?.context?.character || null,
      returnRoute: route?.context?.return || null,
    };
  })()`);
  await evaluate(client, `window.history.back()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'voices'
      && document.querySelectorAll('#library-artifact-list .supporting-list-row').length >= 10
    ))()`);
    if (ready) break;
    await wait(100);
  }
  const restoredVoices = await evaluate(client, `(() => ({
    destination: window.AlexandriaNavigation?.current()?.destination || null,
    rowCount: document.querySelectorAll('#library-artifact-list .supporting-list-row').length,
  }))()`);

  await evaluate(client, `window.AlexandriaNavigation?.navigate('voices', {}, { historyMode: 'replace' })`);
  const moreProject = voiceCastRoute.project || 'browser-audit-project';
  const moreCharacter = voiceCastRoute.character || 'browser-audit-character';
  const moreSource = `cast:character:${moreCharacter}`;
  const moreReturnRoute = `#/cast?project=${encodeURIComponent(moreProject)}&character=${encodeURIComponent(moreCharacter)}&filter=needs_attention`;
  await evaluate(client, `window.AlexandriaNavigation?.navigate('more', ${JSON.stringify({
    project: moreProject,
    character: moreCharacter,
    source: moreSource,
    return: moreReturnRoute,
    search: 'voice',
  })}, { historyMode: 'push' })`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `(() => {
      const route = window.AlexandriaNavigation?.current();
      return route?.destination === 'more'
        && !route?.context?.tool
        && route?.context?.search === 'voice'
        && !document.getElementById('more-workspace')?.hidden
        && document.getElementById('more-search')?.value === 'voice'
        && document.querySelectorAll('[data-more-tool]').length >= 2;
    })()`);
    if (ready) break;
    await wait(100);
  }
  const moreContextual = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    return {
      destination: route?.destination || null,
      tool: route?.context?.tool || null,
      hash: route?.hash || null,
      locationHash: window.location.hash,
      historyLength: window.history.length,
      project: route?.context?.project || null,
      character: route?.context?.character || null,
      source: route?.context?.source || null,
      returnRoute: route?.context?.return || null,
      search: route?.context?.search || null,
      searchValue: document.getElementById('more-search')?.value || null,
      bannerLabel: document.getElementById('more-context-label')?.textContent.trim() || null,
      returnLabel: document.getElementById('more-return-action')?.textContent.trim() || null,
      visibleToolCount: [...document.querySelectorAll('[data-more-tool]')]
        .filter(row => !row.closest('[hidden]')).length,
    };
  })()`);
  await evaluate(client, `document.querySelector('[data-more-tool="voice-designer"]')?.click()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `(() => {
      const route = window.AlexandriaNavigation?.current();
      return route?.destination === 'more'
        && route?.context?.tool === 'voice-designer'
        && document.getElementById('shell-global-title')?.textContent.trim() === 'Voice designer';
    })()`);
    if (ready) break;
    await wait(100);
  }
  const moreToolOpened = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    return {
      destination: route?.destination || null,
      tool: route?.context?.tool || null,
      hash: route?.hash || null,
      locationHash: window.location.hash,
      historyLength: window.history.length,
      project: route?.context?.project || null,
      character: route?.context?.character || null,
      source: route?.context?.source || null,
      returnRoute: route?.context?.return || null,
      pageTitle: document.getElementById('shell-global-title')?.textContent.trim() || null,
    };
  })()`);
  await evaluate(client, `window.history.back()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `(() => {
      const route = window.AlexandriaNavigation?.current();
      return route?.destination === 'more'
        && !route?.context?.tool
        && route?.context?.search === 'voice'
        && document.getElementById('more-search')?.value === 'voice'
        && !document.getElementById('more-workspace')?.hidden;
    })()`);
    if (ready) break;
    await wait(100);
  }
  const moreBackRestored = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    return {
      hash: route?.hash || window.location.hash,
      locationHash: window.location.hash,
      historyLength: window.history.length,
      destination: route?.destination || null,
      tool: route?.context?.tool || null,
      project: route?.context?.project || null,
      character: route?.context?.character || null,
      source: route?.context?.source || null,
      returnRoute: route?.context?.return || null,
      search: route?.context?.search || null,
      searchValue: document.getElementById('more-search')?.value || null,
      workspaceVisible: !document.getElementById('more-workspace')?.hidden,
    };
  })()`);
  await evaluate(client, `window.history.forward()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `window.AlexandriaNavigation?.current()?.context?.tool === 'voice-designer'`);
    if (ready) break;
    await wait(100);
  }
  const moreForwardRestored = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    return {
      destination: route?.destination || null,
      tool: route?.context?.tool || null,
      hash: route?.hash || null,
      locationHash: window.location.hash,
      historyLength: window.history.length,
      project: route?.context?.project || null,
      character: route?.context?.character || null,
      source: route?.context?.source || null,
      returnRoute: route?.context?.return || null,
      pageTitle: document.getElementById('shell-global-title')?.textContent.trim() || null,
    };
  })()`);
  await evaluate(client, `window.history.back()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'more'
      && !window.AlexandriaNavigation?.current()?.context?.tool
      && !document.getElementById('more-workspace')?.hidden
    ))()`);
    if (ready) break;
    await wait(100);
  }
  await evaluate(client, `document.getElementById('more-return-action')?.click()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `(() => {
      const route = window.AlexandriaNavigation?.current();
      return route?.destination === 'cast'
        && route?.context?.project === ${JSON.stringify(moreProject)}
        && route?.context?.character === ${JSON.stringify(moreCharacter)}
        && route?.context?.filter === 'needs_attention';
    })()`);
    if (ready) break;
    await wait(100);
  }
  const moreReturned = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    return {
      destination: route?.destination || null,
      project: route?.context?.project || null,
      character: route?.context?.character || null,
      filter: route?.context?.filter || null,
    };
  })()`);

  const specialistScreenshotBase = screenshotPath.replace(/\.png$/i, '');
  const advancedWideScreenshotPath = `${specialistScreenshotBase}-advanced-wide.png`;
  const advancedCompactScreenshotPath = `${specialistScreenshotBase}-advanced-compact.png`;
  const voiceLabWideScreenshotPath = `${specialistScreenshotBase}-voice-lab-wide.png`;
  const voiceLabCompactScreenshotPath = `${specialistScreenshotBase}-voice-lab-compact.png`;
  const specialistContext = {
    project: moreProject,
    character: moreCharacter,
    source: moreSource,
    return: moreReturnRoute,
  };

  await client.send('Emulation.setDeviceMetricsOverride', {
    width: 1536,
    height: 1024,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await evaluate(client, `window.AlexandriaNavigation?.navigate('more', ${JSON.stringify({
    ...specialistContext,
    tool: 'advanced-character-operations',
    mode: 'identity',
  })}, { historyMode: 'push' })`);
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const ready = await evaluate(client, `(() => {
      const route = window.AlexandriaNavigation?.current();
      const banner = document.querySelector('[data-character-tool-context="speaker-management"]');
      const name = banner?.querySelector('[data-character-context-name]')?.textContent.trim();
      const meta = banner?.querySelector('[data-character-context-meta]')?.textContent.trim();
      return route?.destination === 'more'
        && route?.context?.tool === 'advanced-character-operations'
        && route?.context?.mode === 'identity'
        && window.speakerManagementSelectedId === ${JSON.stringify(moreCharacter)}
        && banner
        && !banner.hidden
        && name
        && name !== 'Selected character'
        && /Script label:/.test(meta || '');
    })()`);
    if (ready) break;
    await wait(100);
  }
  const advancedWide = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    const panel = document.getElementById('speaker-management-tab');
    const banner = panel?.querySelector('[data-character-tool-context="speaker-management"]');
    const notice = panel?.querySelector('.canonical-notice span');
    const rect = banner?.getBoundingClientRect();
    return {
      destination: route?.destination || null,
      tool: route?.context?.tool || null,
      mode: route?.context?.mode || null,
      project: route?.context?.project || null,
      character: route?.context?.character || null,
      source: route?.context?.source || null,
      returnRoute: route?.context?.return || null,
      hash: route?.hash || null,
      locationHash: window.location.hash,
      pageTitle: document.getElementById('shell-global-title')?.textContent.trim() || null,
      kicker: banner?.nextElementSibling?.querySelector('.canonical-kicker')?.textContent.trim() || null,
      contextVisible: Boolean(banner && !banner.hidden),
      contextName: banner?.querySelector('[data-character-context-name]')?.textContent.trim() || null,
      contextMeta: banner?.querySelector('[data-character-context-meta]')?.textContent.trim() || null,
      returnLabel: banner?.querySelector('button')?.textContent.trim() || null,
      selectedId: window.speakerManagementSelectedId || null,
      statusEntryCount: window.speakerManagementStatus?.entries?.length || 0,
      authorityCopy: notice?.textContent.replace(/\\s+/g, ' ').trim() || null,
      assignmentMutationControlCount: panel
        ? [...panel.querySelectorAll('button')].filter(button => /assign(?:ment)? to production|remove production assignment/i.test(button.textContent)).length
        : 0,
      referenceAssignmentActionCount: document.querySelectorAll('[data-reference-bank-action="assign"], [data-reference-bank-action="unassign"]').length,
      bannerRect: rect ? {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, width: rect.width} : null,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      viewport: {width: window.innerWidth, height: window.innerHeight},
    };
  })()`);
  advancedWide.screenshotBytes = await writeScreenshot(client, advancedWideScreenshotPath);
  advancedWide.screenshotPath = advancedWideScreenshotPath;

  await client.send('Emulation.setDeviceMetricsOverride', {
    width: 1024,
    height: 768,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await wait(150);
  const advancedCompact = await evaluate(client, `(() => {
    const panel = document.getElementById('speaker-management-tab');
    const banner = panel?.querySelector('[data-character-tool-context="speaker-management"]');
    const rect = banner?.getBoundingClientRect();
    return {
      contextVisible: Boolean(banner && !banner.hidden),
      contextName: banner?.querySelector('[data-character-context-name]')?.textContent.trim() || null,
      contextMeta: banner?.querySelector('[data-character-context-meta]')?.textContent.trim() || null,
      bannerRect: rect ? {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, width: rect.width} : null,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      viewport: {width: window.innerWidth, height: window.innerHeight},
    };
  })()`);
  advancedCompact.screenshotBytes = await writeScreenshot(client, advancedCompactScreenshotPath);
  advancedCompact.screenshotPath = advancedCompactScreenshotPath;

  await client.send('Emulation.setDeviceMetricsOverride', {
    width: 1536,
    height: 1024,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await evaluate(client, `window.AlexandriaNavigation?.navigate('more', ${JSON.stringify({
    ...specialistContext,
    tool: 'voice-training',
    mode: 'training',
  })}, { historyMode: 'push' })`);
  for (let attempt = 0; attempt < 140; attempt += 1) {
    const ready = await evaluate(client, `(() => {
      const route = window.AlexandriaNavigation?.current();
      const banner = document.querySelector('[data-character-tool-context="training"]');
      const name = banner?.querySelector('[data-character-context-name]')?.textContent.trim();
      const meta = banner?.querySelector('[data-character-context-meta]')?.textContent.trim();
      return route?.destination === 'more'
        && route?.context?.tool === 'voice-training'
        && route?.context?.mode === 'training'
        && window.voiceTrainingSelectedId === ${JSON.stringify(moreCharacter)}
        && banner
        && !banner.hidden
        && name
        && name !== 'Selected character'
        && /Script label:/.test(meta || '');
    })()`);
    if (ready) break;
    await wait(100);
  }
  const voiceLabWide = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    const panel = document.getElementById('training-tab');
    const banner = panel?.querySelector('[data-character-tool-context="training"]');
    const notice = panel?.querySelector('.canonical-notice span');
    const rect = banner?.getBoundingClientRect();
    return {
      destination: route?.destination || null,
      tool: route?.context?.tool || null,
      mode: route?.context?.mode || null,
      project: route?.context?.project || null,
      character: route?.context?.character || null,
      source: route?.context?.source || null,
      returnRoute: route?.context?.return || null,
      hash: route?.hash || null,
      locationHash: window.location.hash,
      pageTitle: document.getElementById('shell-global-title')?.textContent.trim() || null,
      kicker: banner?.nextElementSibling?.querySelector('.canonical-kicker')?.textContent.trim() || null,
      contextVisible: Boolean(banner && !banner.hidden),
      contextName: banner?.querySelector('[data-character-context-name]')?.textContent.trim() || null,
      contextMeta: banner?.querySelector('[data-character-context-meta]')?.textContent.trim() || null,
      returnLabel: banner?.querySelector('button')?.textContent.trim() || null,
      selectedId: window.voiceTrainingSelectedId || null,
      statusEntryCount: window.voiceTrainingStatus?.entries?.length || 0,
      authorityCopy: notice?.textContent.replace(/\\s+/g, ' ').trim() || null,
      assignmentMutationControlCount: panel
        ? [...panel.querySelectorAll('button')].filter(button => /assign(?:ment)? to production|remove production assignment/i.test(button.textContent)).length
        : 0,
      referenceAssignmentActionCount: document.querySelectorAll('[data-reference-bank-action="assign"], [data-reference-bank-action="unassign"]').length,
      bannerRect: rect ? {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, width: rect.width} : null,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      viewport: {width: window.innerWidth, height: window.innerHeight},
    };
  })()`);
  voiceLabWide.screenshotBytes = await writeScreenshot(client, voiceLabWideScreenshotPath);
  voiceLabWide.screenshotPath = voiceLabWideScreenshotPath;

  await client.send('Emulation.setDeviceMetricsOverride', {
    width: 1024,
    height: 768,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await wait(150);
  const voiceLabCompact = await evaluate(client, `(() => {
    const panel = document.getElementById('training-tab');
    const banner = panel?.querySelector('[data-character-tool-context="training"]');
    const rect = banner?.getBoundingClientRect();
    return {
      contextVisible: Boolean(banner && !banner.hidden),
      contextName: banner?.querySelector('[data-character-context-name]')?.textContent.trim() || null,
      contextMeta: banner?.querySelector('[data-character-context-meta]')?.textContent.trim() || null,
      bannerRect: rect ? {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, width: rect.width} : null,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      viewport: {width: window.innerWidth, height: window.innerHeight},
    };
  })()`);
  voiceLabCompact.screenshotBytes = await writeScreenshot(client, voiceLabCompactScreenshotPath);
  voiceLabCompact.screenshotPath = voiceLabCompactScreenshotPath;

  await client.send('Emulation.setDeviceMetricsOverride', {
    width: 1536,
    height: 1024,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await evaluate(client, `window.openCastAssignmentForCharacter?.()`);
  for (let attempt = 0; attempt < 140; attempt += 1) {
    const ready = await evaluate(client, `(() => {
      const route = window.AlexandriaNavigation?.current();
      return route?.destination === 'cast'
        && route?.context?.project === ${JSON.stringify(moreProject)}
        && route?.context?.character === ${JSON.stringify(moreCharacter)}
        && route?.context?.filter === 'needs_attention'
        && !document.getElementById('cast-content')?.hidden
        && document.getElementById('cast-loading')?.hidden
        && Boolean(document.getElementById('cast-detail-name')?.textContent.trim());
    })()`);
    if (ready) break;
    await wait(100);
  }
  const specialistCastReturned = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    return {
      destination: route?.destination || null,
      project: route?.context?.project || null,
      character: route?.context?.character || null,
      filter: route?.context?.filter || null,
      hash: route?.hash || null,
      productionAssignmentControlVisible: [
        document.getElementById('cast-save-voice'),
        document.getElementById('cast-edit-voice'),
      ].some(element => {
        if (!element || element.hidden) return false;
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== 'none'
          && style.visibility !== 'hidden'
          && rect.width > 0
          && rect.height > 0;
      }),
    };
  })()`);

  await evaluate(client, `window.history.back()`);
  for (let attempt = 0; attempt < 140; attempt += 1) {
    const ready = await evaluate(client, `(() => {
      const route = window.AlexandriaNavigation?.current();
      const banner = document.querySelector('[data-character-tool-context="training"]');
      return route?.destination === 'more'
        && route?.context?.tool === 'voice-training'
        && route?.context?.mode === 'training'
        && banner
        && !banner.hidden;
    })()`);
    if (ready) break;
    await wait(100);
  }
  const voiceLabBack = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    const banner = document.querySelector('[data-character-tool-context="training"]');
    return {
      destination: route?.destination || null,
      tool: route?.context?.tool || null,
      mode: route?.context?.mode || null,
      project: route?.context?.project || null,
      character: route?.context?.character || null,
      source: route?.context?.source || null,
      returnRoute: route?.context?.return || null,
      hash: route?.hash || null,
      locationHash: window.location.hash,
      contextVisible: Boolean(banner && !banner.hidden),
      contextName: banner?.querySelector('[data-character-context-name]')?.textContent.trim() || null,
      contextMeta: banner?.querySelector('[data-character-context-meta]')?.textContent.trim() || null,
    };
  })()`);

  await evaluate(client, `window.history.back()`);
  for (let attempt = 0; attempt < 140; attempt += 1) {
    const ready = await evaluate(client, `(() => {
      const route = window.AlexandriaNavigation?.current();
      const banner = document.querySelector('[data-character-tool-context="speaker-management"]');
      return route?.destination === 'more'
        && route?.context?.tool === 'advanced-character-operations'
        && route?.context?.mode === 'identity'
        && banner
        && !banner.hidden;
    })()`);
    if (ready) break;
    await wait(100);
  }
  const advancedBack = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    const banner = document.querySelector('[data-character-tool-context="speaker-management"]');
    return {
      destination: route?.destination || null,
      tool: route?.context?.tool || null,
      mode: route?.context?.mode || null,
      project: route?.context?.project || null,
      character: route?.context?.character || null,
      source: route?.context?.source || null,
      returnRoute: route?.context?.return || null,
      hash: route?.hash || null,
      locationHash: window.location.hash,
      contextVisible: Boolean(banner && !banner.hidden),
      contextName: banner?.querySelector('[data-character-context-name]')?.textContent.trim() || null,
      contextMeta: banner?.querySelector('[data-character-context-meta]')?.textContent.trim() || null,
    };
  })()`);

  await evaluate(client, `window.history.forward()`);
  for (let attempt = 0; attempt < 140; attempt += 1) {
    const ready = await evaluate(client, `(() => {
      const route = window.AlexandriaNavigation?.current();
      const banner = document.querySelector('[data-character-tool-context="training"]');
      return route?.destination === 'more'
        && route?.context?.tool === 'voice-training'
        && route?.context?.mode === 'training'
        && banner
        && !banner.hidden;
    })()`);
    if (ready) break;
    await wait(100);
  }
  const voiceLabForward = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    const banner = document.querySelector('[data-character-tool-context="training"]');
    return {
      destination: route?.destination || null,
      tool: route?.context?.tool || null,
      mode: route?.context?.mode || null,
      project: route?.context?.project || null,
      character: route?.context?.character || null,
      source: route?.context?.source || null,
      returnRoute: route?.context?.return || null,
      hash: route?.hash || null,
      locationHash: window.location.hash,
      contextVisible: Boolean(banner && !banner.hidden),
      contextName: banner?.querySelector('[data-character-context-name]')?.textContent.trim() || null,
      contextMeta: banner?.querySelector('[data-character-context-meta]')?.textContent.trim() || null,
    };
  })()`);

  await evaluate(client, `(() => {
    document.querySelectorAll('.modal').forEach(modal => {
      window.bootstrap?.Modal?.getInstance(modal)?._focustrap?.deactivate?.();
    });
    document.activeElement?.blur?.();
    document.getElementById('main-content')?.focus({ preventScroll: true });
  })()`);
  const maintenanceReturnRoute = '#/settings';
  await evaluate(client, `window.AlexandriaNavigation?.navigate('more', ${JSON.stringify({
    project: moreProject,
    character: moreCharacter,
    tool: 'maintenance',
    mode: 'dependencies',
    return: maintenanceReturnRoute,
  })}, { historyMode: 'push' })`);
  for (let attempt = 0; attempt < 160; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.context?.tool === 'maintenance'
      && !document.getElementById('canonical-maintenance-workspace')?.hidden
      && !document.getElementById('maintenance-content')?.hidden
      && document.getElementById('maintenance-loading')?.hidden
      && document.querySelectorAll('#maintenance-health-list .maintenance-row').length >= 2
      && document.querySelectorAll('#maintenance-model-list .maintenance-row').length >= 1
    ))()`);
    if (ready) break;
    await wait(100);
  }
  const maintenanceInitial = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    const workspace = document.getElementById('canonical-maintenance-workspace');
    const visible = element => {
      if (!element || element.hidden) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    return {
      destination: route?.destination || null,
      tool: route?.context?.tool || null,
      mode: route?.context?.mode || null,
      project: route?.context?.project || null,
      character: route?.context?.character || null,
      returnRoute: route?.context?.return || null,
      hash: route?.hash || null,
      locationHash: window.location.hash,
      workspaceVisible: visible(workspace),
      settingsHidden: document.getElementById('canonical-settings-workspace')?.hidden ?? null,
      legacyHidden: document.getElementById('legacy-settings-workspace')?.hidden ?? null,
      recoveryHidden: document.getElementById('recovery-center')?.hidden ?? null,
      healthRows: document.querySelectorAll('#maintenance-health-list .maintenance-row').length,
      modelRows: document.querySelectorAll('#maintenance-model-list .maintenance-row').length,
      libraryRows: document.querySelectorAll('#maintenance-library-list .maintenance-row').length,
      projectRows: document.querySelectorAll('#maintenance-project-list .maintenance-row').length,
      impactButtons: document.querySelectorAll('[data-maintenance-library-impact], [data-maintenance-project-impact]').length,
      modelActions: document.querySelectorAll('[data-maintenance-model-action]').length,
      /* Avoid regex slash escaping inside this nested browser expression.
      rawAbsolutePathVisible: /\\/(Users|private|var|tmp|home)\\//.test(workspace?.innerText || ''),
      rawFingerprintVisible: /\\b[a-f0-9]{64}\\b/i.test(workspace?.innerText || ''),
      */
      rawAbsolutePathVisible: ['/Users/', '/private/', '/var/', '/tmp/', '/home/'].some(prefix => (workspace?.innerText || '').includes(prefix)),
      rawFingerprintVisible: new RegExp('[a-f0-9]{64}', 'i').test(workspace?.innerText || ''),
      destructiveButtonVisible: [...workspace?.querySelectorAll('.btn-danger') || []].some(visible),
    };
  })()`);

  const safeArtifactId = await evaluate(client, `(() => {
    const maintenance = window.AlexandriaCanonicalInterface?.state()?.maintenance;
    return (maintenance?.library?.artifacts || []).find(item => item.delete?.supported && !item.delete?.blocked)?.artifact_id || null;
  })()`);
  await evaluate(client, `(() => {
    const trigger = document.querySelector('[data-maintenance-library-impact="${safeArtifactId || ''}"]');
    trigger?.focus({ preventScroll: true });
    trigger?.click();
  })()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `document.getElementById('maintenance-impact-dialog')?.open === true`);
    if (ready) break;
    await wait(100);
  }
  const maintenanceImpactInitial = await evaluate(client, `(() => {
    const maintenance = window.AlexandriaCanonicalInterface?.state()?.maintenance;
    const dialog = document.getElementById('maintenance-impact-dialog');
    const action = document.getElementById('maintenance-delete-action');
    return {
      safeArtifactId: ${JSON.stringify(safeArtifactId)},
      dialogOpen: dialog?.open || false,
      title: document.getElementById('maintenance-impact-title')?.textContent.trim() || null,
      kind: maintenance?.impact?.kind || null,
      confirmText: maintenance?.impact?.confirmText || null,
      actionLabel: action?.textContent.trim() || null,
      actionDisabled: action?.disabled ?? null,
      activeElement: document.activeElement?.id || null,
    };
  })()`);
  const maintenanceImpactScreenshotPath = screenshotPath.replace(/\.png$/i, '-maintenance-impact.png');
  maintenanceImpactInitial.screenshotBytes = await writeScreenshot(client, maintenanceImpactScreenshotPath);
  maintenanceImpactInitial.screenshotPath = maintenanceImpactScreenshotPath;
  const maintenanceImpactWrong = await evaluate(client, `(() => {
    const input = document.getElementById('maintenance-confirm-input');
    input.value = 'wrong confirmation';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    return {
      actionDisabled: document.getElementById('maintenance-delete-action')?.disabled ?? null,
      inputValue: input.value,
    };
  })()`);
  const maintenanceImpactExact = await evaluate(client, `(() => {
    const maintenance = window.AlexandriaCanonicalInterface?.state()?.maintenance;
    const input = document.getElementById('maintenance-confirm-input');
    input.value = maintenance?.impact?.confirmText || '';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    return {
      actionDisabled: document.getElementById('maintenance-delete-action')?.disabled ?? null,
      inputValue: input.value,
    };
  })()`);
  await evaluate(client, `document.getElementById('maintenance-impact-dialog')?.close()`);
  await wait(120);
  const maintenanceImpactClosed = await evaluate(client, `(() => ({
    dialogOpen: document.getElementById('maintenance-impact-dialog')?.open || false,
    focusRestored: document.activeElement?.dataset?.maintenanceLibraryImpact || null,
  }))()`);

  const maintenanceHash = await evaluate(client, `window.AlexandriaNavigation?.current()?.hash || window.location.hash`);
  const nativeArtifactId = await evaluate(client, `document.querySelector('[data-maintenance-artifact-open]')?.dataset.maintenanceArtifactOpen || null`);
  await evaluate(client, `document.querySelector('[data-maintenance-artifact-open="${nativeArtifactId || ''}"]')?.click()`);
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const ready = await evaluate(client, `(() => {
      const route = window.AlexandriaNavigation?.current();
      return route?.hash !== ${JSON.stringify(maintenanceHash)} && route?.context?.return === ${JSON.stringify(maintenanceHash)};
    })()`);
    if (ready) break;
    await wait(100);
  }
  const maintenanceNativeRoute = await evaluate(client, `(() => {
    const route = window.AlexandriaNavigation?.current();
    return {
      nativeArtifactId: ${JSON.stringify(nativeArtifactId)},
      destination: route?.destination || null,
      tool: route?.context?.tool || null,
      source: route?.context?.source || null,
      returnRoute: route?.context?.return || null,
      hash: route?.hash || null,
    };
  })()`);
  await evaluate(client, `window.history.back()`);
  for (let attempt = 0; attempt < 160; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.hash === ${JSON.stringify(maintenanceHash)}
      && !document.getElementById('canonical-maintenance-workspace')?.hidden
      && !document.getElementById('maintenance-content')?.hidden
    ))()`);
    if (ready) break;
    await wait(100);
  }
  const maintenanceBack = await evaluate(client, `(() => ({
    hash: window.AlexandriaNavigation?.current()?.hash || window.location.hash,
    locationHash: window.location.hash,
    workspaceVisible: !document.getElementById('canonical-maintenance-workspace')?.hidden,
    contentVisible: !document.getElementById('maintenance-content')?.hidden,
  }))()`);

  await evaluate(client, `document.querySelector('[data-route="settings"]')?.click()`);
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'settings'
      && !document.getElementById('canonical-settings-form')?.hidden
      && document.getElementById('canonical-settings-save-state')?.textContent.trim() === 'Saved'
    ))()`);
    if (ready) break;
    await wait(100);
  }
  const settingsInitial = await evaluate(client, `(() => ({
    workspaceVisible: !document.getElementById('canonical-settings-workspace')?.hidden,
    legacyHidden: document.getElementById('legacy-settings-workspace')?.hidden ?? null,
    recoveryHidden: document.getElementById('recovery-center')?.hidden ?? null,
    defaultTemplate: document.getElementById('settings-default-template-name')?.textContent.trim() || null,
    outputLanguage: document.getElementById('settings-output-language')?.value || null,
    apiKeyValue: document.getElementById('settings-api-key')?.value || null,
    apiKeyState: document.getElementById('settings-api-key-state')?.textContent.trim() || null,
    advancedActionCount: document.querySelectorAll('[data-settings-destination]').length,
    rawFingerprintVisible: /[a-f0-9]{64}/i.test(document.getElementById('canonical-settings-workspace')?.innerText || ''),
  }))()`);

  await evaluate(client, `(() => {
    const setValue = (id, value, type = 'input') => {
      const target = document.getElementById(id);
      target.value = value;
      target.dispatchEvent(new Event(type, { bubbles: true }));
    };
    setValue('settings-output-language', 'Swedish');
    setValue('settings-provider-backend', 'ollama', 'change');
    setValue('settings-provider-url', 'https://remote.example/v1');
    setValue('settings-speech-language', 'Swedish');
    setValue('settings-motion', 'reduced', 'change');
    setValue('settings-contrast', 'more', 'change');
    setValue('settings-density', 'compact', 'change');
    setValue('settings-rollback-days', '45');
    setValue('settings-intermediate-days', '10');
    setValue('settings-backup-gib', '24');
    document.getElementById('canonical-settings-form').requestSubmit();
  })()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const retained = await evaluate(client, `(() => (
      document.getElementById('canonical-settings-save-state')?.textContent.trim() === 'Not saved'
      && document.getElementById('canonical-settings-status-copy')?.textContent.includes('Native Ollama')
    ))()`);
    if (retained) break;
    await wait(100);
  }
  const settingsInvalid = await evaluate(client, `(() => ({
    saveState: document.getElementById('canonical-settings-save-state')?.textContent.trim() || null,
    error: document.getElementById('canonical-settings-status-copy')?.textContent.trim() || null,
    outputLanguage: document.getElementById('settings-output-language')?.value || null,
    providerUrl: document.getElementById('settings-provider-url')?.value || null,
    motion: document.getElementById('settings-motion')?.value || null,
    bodyMotion: document.body.dataset.settingsMotion || null,
  }))()`);

  await evaluate(client, `(() => {
    const url = document.getElementById('settings-provider-url');
    url.value = 'http://127.0.0.1:11434/v1';
    url.dispatchEvent(new Event('input', { bubbles: true }));
    document.getElementById('canonical-settings-form').requestSubmit();
  })()`);
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const saved = await evaluate(client, `(() => (
      document.getElementById('canonical-settings-save-state')?.textContent.trim() === 'Saved'
      && document.getElementById('settings-output-language')?.value === 'Swedish'
    ))()`);
    if (saved) break;
    await wait(100);
  }
  const settingsSaved = await evaluate(client, `(async () => {
    const response = await fetch('/api/settings');
    const payload = await response.json();
    return {
      uiSaveState: document.getElementById('canonical-settings-save-state')?.textContent.trim() || null,
      persistedOutputLanguage: payload.settings?.preferences?.default_output_language || null,
      persistedProviderUrl: payload.settings?.provider?.base_url || null,
      persistedMotion: payload.settings?.accessibility?.motion || null,
      persistedContrast: payload.settings?.accessibility?.contrast || null,
      persistedDensity: payload.settings?.accessibility?.density || null,
      persistedRollbackDays: payload.settings?.storage?.rollback_retention_days || null,
      persistedIntermediateDays: payload.settings?.storage?.intermediate_retention_days || null,
      persistedBackupGib: payload.settings?.storage?.maximum_backup_gib || null,
      apiKeyValueExposed: payload.settings?.provider?.api_key || null,
      apiKeyConfigured: payload.settings?.provider?.api_key_configured ?? null,
    };
  })()`);

  await client.send('Page.reload', { ignoreCache: true });
  for (let attempt = 0; attempt < 160; attempt += 1) {
    let ready = false;
    try {
      ready = await evaluate(client, `(() => (
        window.AlexandriaNavigation?.current()?.destination === 'settings'
        && !document.getElementById('canonical-settings-form')?.hidden
        && document.getElementById('settings-output-language')?.value === 'Swedish'
      ))()`);
    } catch (error) {
      ready = false;
    }
    if (ready) break;
    await wait(100);
  }
  const settingsReloaded = await evaluate(client, `(() => ({
    destination: window.AlexandriaNavigation?.current()?.destination || null,
    outputLanguage: document.getElementById('settings-output-language')?.value || null,
    providerUrl: document.getElementById('settings-provider-url')?.value || null,
    motion: document.getElementById('settings-motion')?.value || null,
    contrast: document.getElementById('settings-contrast')?.value || null,
    density: document.getElementById('settings-density')?.value || null,
    bodyMotion: document.body.dataset.settingsMotion || null,
    bodyContrast: document.body.dataset.settingsContrast || null,
    bodyDensity: document.body.dataset.settingsDensity || null,
    apiKeyValue: document.getElementById('settings-api-key')?.value || null,
  }))()`);

  await evaluate(client, `document.querySelector('[data-settings-destination="runtime_diagnostics"]')?.click()`);
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.context?.tool === 'maintenance'
      && window.AlexandriaNavigation?.current()?.context?.mode === 'runtime'
      && !document.getElementById('legacy-settings-workspace')?.hidden
      && document.getElementById('canonical-settings-workspace')?.hidden
    ))()`);
    if (ready) break;
    await wait(100);
  }
  const settingsMaintenance = await evaluate(client, `(() => ({
    destination: window.AlexandriaNavigation?.current()?.destination || null,
    tool: window.AlexandriaNavigation?.current()?.context?.tool || null,
    mode: window.AlexandriaNavigation?.current()?.context?.mode || null,
    returnRoute: window.AlexandriaNavigation?.current()?.context?.return || null,
    canonicalHidden: document.getElementById('canonical-settings-workspace')?.hidden ?? null,
    legacyVisible: !document.getElementById('legacy-settings-workspace')?.hidden,
    runtimeOpen: document.getElementById('llm-runtime-panel')?.open ?? null,
  }))()`);
  await evaluate(client, `window.history.back()`);
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'settings'
      && !document.getElementById('canonical-settings-form')?.hidden
    ))()`);
    if (ready) break;
    await wait(100);
  }
  await evaluate(client, `document.getElementById('settings-manage-templates')?.click()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `window.AlexandriaNavigation?.current()?.destination === 'templates'`);
    if (ready) break;
    await wait(100);
  }
  const settingsTemplates = await evaluate(client, `(() => ({
    destination: window.AlexandriaNavigation?.current()?.destination || null,
    returnRoute: window.AlexandriaNavigation?.current()?.context?.return || null,
  }))()`);
  await evaluate(client, `window.history.back()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `window.AlexandriaNavigation?.current()?.destination === 'settings'`);
    if (ready) break;
    await wait(100);
  }

  const screenshotBytes = await writeScreenshot(client, screenshotPath);
  return {
    createVoice,
    templateCrud,
    templateApplied,
    templateRestored,
    help,
    nativeTool,
    filteredLibrary,
    restoredLibrary,
    workflowRoutes,
    voiceLibrary,
    controlledVoice,
    suppliedVoice,
    voiceReturnRoute,
    voiceCastRoute,
    restoredVoices,
    moreContextual,
    moreToolOpened,
    moreBackRestored,
    moreForwardRestored,
    moreReturned,
    advancedWide,
    advancedCompact,
    voiceLabWide,
    voiceLabCompact,
    specialistCastReturned,
    voiceLabBack,
    advancedBack,
    voiceLabForward,
    maintenanceInitial,
    maintenanceImpactInitial,
    maintenanceImpactWrong,
    maintenanceImpactExact,
    maintenanceImpactClosed,
    maintenanceNativeRoute,
    maintenanceBack,
    settingsInitial,
    settingsInvalid,
    settingsSaved,
    settingsReloaded,
    settingsMaintenance,
    settingsTemplates,
    screenshotBytes,
    screenshotPath,
  };
}

async function inspectBoundary12Interactions(client, screenshotPath) {
  await client.send('Emulation.setDeviceMetricsOverride', {
    width: 1536,
    height: 1024,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await installBoundary12Fixtures(client);
  await evaluate(client, `(() => {
    window.__boundary12Requests = [];
    window.__boundary12ExportPhase = 'ready';
    window.dispatchEvent(new CustomEvent('alexandria:routechange', {
      detail: { route: { destination: 'export', context: {} } },
    }));
  })()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `(() => {
      const action = document.getElementById('shell-primary-action');
      return document.body.dataset.destination === 'export'
        && action?.dataset.action === 'export-primary'
        && !action.disabled
        && action.textContent.trim() === 'Build Audiobook';
    })()`);
    if (ready) break;
    await wait(100);
  }
  await evaluate(client, `document.getElementById('shell-primary-action').click()`);
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const built = await evaluate(client, `(() => (
      !document.getElementById('export-built-confirmation')?.hidden
      && document.getElementById('shell-workflow-state')?.textContent.trim() === 'Built'
      && window.__boundary12Requests.some(item => item.path === '/api/export/build')
    ))()`);
    if (built) break;
    await wait(100);
  }
  const built = await evaluate(client, `(() => {
    const request = window.__boundary12Requests.find(item => item.path === '/api/export/build') || null;
    const source = document.querySelector('#main-audio source');
    return {
      request,
      workflowState: document.getElementById('shell-workflow-state')?.textContent.trim() || null,
      primaryAction: document.getElementById('shell-primary-action')?.textContent.trim() || null,
      builtConfirmationVisible: !document.getElementById('export-built-confirmation')?.hidden,
      builtCopy: document.getElementById('export-built-copy')?.textContent.trim() || null,
      waveformDisabled: document.getElementById('export-waveform')?.disabled ?? null,
      playerSource: source?.getAttribute('src') || null,
      playerTitle: document.getElementById('persistent-player-title')?.textContent.trim() || null,
      playerContext: document.getElementById('persistent-player-context')?.textContent.trim() || null,
      validationSummary: document.getElementById('export-validation-summary')?.textContent.trim() || null,
    };
  })()`);
  const screenshotBytes = await writeScreenshot(client, screenshotPath);

  await evaluate(client, `(() => {
    window.__boundary12ExportPhase = 'failed';
    window.dispatchEvent(new CustomEvent('alexandria:routechange', {
      detail: { route: { destination: 'export', context: {} } },
    }));
  })()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const failed = await evaluate(client, `document.getElementById('shell-workflow-state')?.textContent.trim() === 'Failed'`);
    if (failed) break;
    await wait(100);
  }
  const failed = await evaluate(client, `(() => ({
    workflowState: document.getElementById('shell-workflow-state')?.textContent.trim() || null,
    builtConfirmationVisible: !document.getElementById('export-built-confirmation')?.hidden,
    validationText: [...document.querySelectorAll('.export-validation-row')].map(row => row.textContent.replace(/\\s+/g, ' ').trim()),
    primaryAction: document.getElementById('shell-primary-action')?.textContent.trim() || null,
    currentOutputStillVisible: document.getElementById('export-size')?.textContent.trim() || null,
  }))()`);

  await evaluate(client, `(() => {
    window.__boundary12Requests = [];
    document.querySelector('[data-tab="editor"][data-route="produce"]')?.click();
  })()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      document.body.dataset.destination === 'produce'
      && document.querySelectorAll('.produce-chunk-row').length > 0
    ))()`);
    if (ready) break;
    await wait(100);
  }
  await evaluate(client, `(() => {
    const overflow = document.getElementById('produce-overflow');
    if (overflow) overflow.open = true;
    document.getElementById('produce-regenerate-all')?.click();
  })()`);
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const ready = await evaluate(client, `(() => (
      document.getElementById('confirmModal')?.classList.contains('show')
      && document.getElementById('confirmModalOk')?.disabled === false
    ))()`);
    if (ready) break;
    await wait(100);
  }
  const confirmation = await evaluate(client, `(() => ({
    visible: document.getElementById('confirmModal')?.classList.contains('show') || false,
    title: document.getElementById('confirmModalTitle')?.textContent.trim() || null,
    body: document.getElementById('confirmModalBody')?.textContent.trim() || null,
    action: document.getElementById('confirmModalOk')?.textContent.trim() || null,
    actionClass: document.getElementById('confirmModalOk')?.className || null,
  }))()`);
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const settled = await evaluate(client, `(() => {
      const element = document.getElementById('confirmModal');
      const instance = window.bootstrap?.Modal?.getInstance(element);
      return Boolean(instance && instance._isShown && !instance._isTransitioning);
    })()`);
    if (settled) break;
    await wait(50);
  }
  await evaluate(client, `document.getElementById('confirmModalOk')?.click()`);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const sent = await evaluate(client, `window.__boundary12Requests.some(item => item.path === '/api/produce/generate')`);
    if (sent) break;
    await wait(100);
  }
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const hidden = await evaluate(client, `!document.getElementById('confirmModal')?.classList.contains('show')`);
    if (hidden) break;
    await wait(50);
  }
  const regenerateAll = await evaluate(client, `(() => {
    const modal = document.getElementById('confirmModal');
    return {
      request: window.__boundary12Requests.find(item => item.path === '/api/produce/generate') || null,
      confirmationHidden: !modal?.classList.contains('show'),
      confirmationDisplay: modal ? getComputedStyle(modal).display : null,
      confirmationAriaHidden: modal?.getAttribute('aria-hidden') || null,
      backdropCount: document.querySelectorAll('.modal-backdrop.show').length,
    };
  })()`);
  return {
    built,
    failed,
    confirmation,
    regenerateAll,
    screenshotBytes,
    screenshotPath,
  };
}

async function main() {
  const port = argument('--port');
  const targetUrl = argument('--url');
  const outputDir = argument('--output-dir');
  const mode = optionalArgument('--mode', 'full');
  const endpoint = `http://127.0.0.1:${port}/json/new?${encodeURIComponent(targetUrl)}`;
  const response = await fetch(endpoint, { method: 'PUT' });
  if (!response.ok) throw new Error(`Failed to create target: ${response.status}`);
  const target = await response.json();
  const client = new CdpClient(target.webSocketDebuggerUrl);
  const consoleErrors = [];
  const runtimeErrors = [];
  const networkErrors = [];
  const networkRequests = [];
  let expectedSettingsValidationLogs = 0;

  try {
    await client.send('Page.enable');
    await client.send('Runtime.enable');
    await client.send('Log.enable');
    await client.send('Network.enable');
    client.socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (message.method === 'Runtime.exceptionThrown') {
        runtimeErrors.push(message.params?.exceptionDetails?.text || 'Runtime exception');
      }
      if (message.method === 'Log.entryAdded' && message.params?.entry?.level === 'error') {
        const text = message.params.entry.text;
        if (
          expectedSettingsValidationLogs > 0
          && /Failed to load resource/i.test(text)
          && /422/.test(text)
        ) {
          expectedSettingsValidationLogs -= 1;
        } else {
          consoleErrors.push(text);
        }
      }
      if (message.method === 'Network.requestWillBeSent') {
        networkRequests.push({
          method: message.params?.request?.method || null,
          url: message.params?.request?.url || null,
          type: message.params?.type || null,
        });
      }
      if (message.method === 'Network.responseReceived') {
        const status = Number(message.params?.response?.status || 0);
        const url = message.params?.response?.url || null;
        if (
          mode === 'boundary13'
          && status === 422
          && typeof url === 'string'
          && url.endsWith('/api/settings')
        ) {
          expectedSettingsValidationLogs += 1;
        } else if (status >= 400) {
          networkErrors.push({
            status,
            url,
            type: message.params?.type || null,
          });
        }
      }
      if (message.method === 'Network.loadingFailed') {
        networkErrors.push({
          status: 0,
          url: message.params?.requestId || null,
          type: message.params?.type || null,
          error: message.params?.errorText || null,
        });
      }
    });

    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      const ready = await evaluate(client, 'document.readyState');
      if (ready === 'complete') break;
      await wait(100);
    }

    const computeDeadline = Date.now() + 10000;
    while (Date.now() < computeDeadline) {
      const computeReady = await evaluate(client, `(() => {
        const text = document.getElementById('sys-gpu-val')?.textContent.trim() || '';
        return text.startsWith('CPU ') || text.startsWith('GPU ');
      })()`);
      if (computeReady) break;
      await wait(100);
    }

    const tabs = ['setup', 'script', 'voices', 'editor', 'audio', 'characters', 'speaker-management', 'voice-projects', 'designer', 'preparer', 'dataset-builder', 'training'];
    const desktop = {};
    const narrow = {};
    const canonicalShell = {};
    let castVoiceEditor = null;
    let boundary12Interactions = null;
    let boundary13Interactions = null;
    let boundary13FinalAcceptance = null;
    if (mode === 'full' || mode === 'legacy') {
      for (const tab of tabs) {
        desktop[tab] = await inspectTab(
          client,
          tab,
          1440,
          1000,
          `${outputDir}/${tab}-desktop.png`,
        );
      }
      for (const tab of tabs) {
        narrow[tab] = await inspectTab(
          client,
          tab,
          390,
          844,
          `${outputDir}/${tab}-narrow.png`,
        );
      }
    }
    if (mode !== 'new-project') {
      const canonicalCases = mode === 'boundary12'
        ? [
            { name: 'produce-wide', destination: 'produce', width: 1536, height: 1024 },
            { name: 'produce-compact', destination: 'produce', width: 1024, height: 768 },
            { name: 'export-wide', destination: 'export', width: 1536, height: 1024 },
            { name: 'export-compact', destination: 'export', width: 1024, height: 768 },
          ]
        : mode === 'boundary13' || mode === 'boundary13-final'
          ? [
              { name: 'library-wide', destination: 'library', width: 1536, height: 1024 },
              { name: 'library-compact', destination: 'library', width: 1024, height: 768 },
              { name: 'voices-wide', destination: 'voices', width: 1536, height: 1024 },
              { name: 'voices-compact', destination: 'voices', width: 1024, height: 768 },
              { name: 'templates-wide', destination: 'templates', width: 1536, height: 1024 },
              { name: 'settings-wide', destination: 'settings', width: 1536, height: 1024 },
              { name: 'settings-compact', destination: 'settings', width: 1024, height: 768 },
              { name: 'maintenance-wide', destination: 'maintenance', width: 1536, height: 1024 },
              { name: 'maintenance-compact', destination: 'maintenance', width: 1024, height: 768 },
              { name: 'more-wide', destination: 'more', width: 1536, height: 1024 },
              { name: 'more-compact', destination: 'more', width: 1024, height: 768 },
              { name: 'help-wide', destination: 'help', width: 1536, height: 1024 },
              { name: 'help-compact', destination: 'help', width: 1024, height: 768 },
            ]
          : [
            { name: 'home-wide', destination: 'projects', width: 1536, height: 1024 },
            { name: 'home-compact', destination: 'projects', width: 1024, height: 768 },
            { name: 'script-wide', destination: 'script', width: 1536, height: 1024 },
            { name: 'script-compact', destination: 'script', width: 1024, height: 768 },
            { name: 'cast-wide', destination: 'cast', width: 1536, height: 1024 },
            { name: 'cast-compact', destination: 'cast', width: 1024, height: 768 },
            { name: 'produce-wide', destination: 'produce', width: 1536, height: 1024 },
            { name: 'produce-compact', destination: 'produce', width: 1024, height: 768 },
            { name: 'export-wide', destination: 'export', width: 1536, height: 1024 },
            { name: 'export-compact', destination: 'export', width: 1024, height: 768 },
          ];
      for (const shellCase of canonicalCases) {
        canonicalShell[shellCase.name] = await inspectCanonicalShell(client, {
          ...shellCase,
          screenshotPath: `${outputDir}/canonical-${shellCase.name}.png`,
        });
      }
      if (mode === 'shell') {
        castVoiceEditor = await inspectCastVoiceEditor(
          client,
          `${outputDir}/canonical-cast-voice-editor.png`,
        );
      }
      if (mode === 'shell' || mode === 'boundary12') {
        boundary12Interactions = await inspectBoundary12Interactions(
          client,
          `${outputDir}/canonical-boundary12-built.png`,
        );
      }
      if (mode === 'boundary13') {
        boundary13Interactions = await inspectBoundary13Interactions(
          client,
          `${outputDir}/canonical-boundary13-interactions.png`,
        );
      }
      if (mode === 'boundary13-final') {
        boundary13FinalAcceptance = await inspectBoundary13FinalAcceptance({
          client,
          evaluate,
          wait,
          writeScreenshot,
          outputDir,
        });
      }
    }

    const states = {};
    const referenceBankStatePrepare = `
      if (!window.voiceTrainingProject) await refreshVoiceTrainingStatus();
      const project = structuredClone(window.voiceTrainingProject);
      project.desired_base_persona.approval_status = 'approved';
      project.desired_base_persona.approved_fingerprint = 'persona-approved-browser';
      project.existing_recordings = {
        status: 'approved',
        same_speaker_declared: true,
        speaker_declaration: project.character.canonical_name,
        clips: [
          {
            clip_id: 'clip_owned_neutral',
            inclusion_decision: 'included',
            style_label: 'neutral',
            transcript: 'Tell me exactly what happened.',
          },
          {
            clip_id: 'clip_owned_urgent',
            inclusion_decision: 'included',
            style_label: 'urgent',
            transcript: 'Move. Now.',
          },
        ],
      };
      project.selected_reference_sample = {
        clip_id: 'clip_owned_neutral',
        source_kind: 'existing_recordings',
        audio_path: 'recordings/clips/clip_owned_neutral.wav',
        audio_sha256: '${'a'.repeat(64)}',
      };
      window.voiceTrainingProject = project;
      renderVoiceTrainingProject(project);
      window.expressiveReferenceBankStatus = {
        available: true,
        style_definitions: {
          neutral: {
            label: 'Neutral continuity',
            instruction: 'Natural neutral delivery.',
          },
          urgency: {
            label: 'Urgency',
            instruction: 'Urgent, clear, and identity-stable.',
          },
        },
        entries: [
          {
            character_id: project.character.id,
            status: 'draft',
          },
        ],
      };
      window.expressiveReferenceBank = {
        schema_version: 1,
        bank_fingerprint: '${'b'.repeat(64)}',
        character_id: project.character.id,
        status: 'draft',
        identity_seed: 1842,
        identity_source: {
          kind: 'owned_recording',
          source_clip_id: 'clip_owned_neutral',
        },
        neutral_style_key: 'neutral',
        required_style_keys: ['neutral', 'urgency'],
        references: [
          {
            reference_id: 'reference_neutral_browser',
            style_key: 'neutral',
            source_kind: 'owned_recording',
            source_clip_id: 'clip_owned_neutral',
            reference_text: 'Tell me exactly what happened.',
            instruction: 'Natural neutral delivery.',
            model: 'none',
            review: {
              approved: true,
              source_identity_retention_passed: true,
              identity_drift_passed: true,
              emotion_match_passed: true,
              pronunciation_passed: true,
              pace_passed: true,
              notes: 'Stable owned identity.',
            },
          },
          {
            reference_id: 'reference_urgency_browser',
            style_key: 'urgency',
            source_kind: 'controlled_clone_experimental',
            source_clip_id: 'clip_owned_neutral',
            reference_text: 'Move. Now.',
            instruction: 'Urgent, clear, and identity-stable.',
            model: 'mlx-community/VoxCPM2-4bit',
            review: {
              approved: false,
              source_identity_retention_passed: true,
              identity_drift_passed: true,
              emotion_match_passed: false,
              pronunciation_passed: true,
              pace_passed: true,
              notes: 'Urgency is still too theatrical.',
            },
          },
        ],
        comparison: {
          status: 'generated',
          test_lines: ['We have to leave now.'],
          outputs: [
            {
              mode: 'reference_bank_clone',
              line_index: 0,
              style_key: 'urgency',
            },
            {
              mode: 'single_reference_clone',
              line_index: 0,
              style_key: null,
            },
            {
              mode: 'direct_voice_design',
              line_index: 0,
              style_key: null,
            },
          ],
          source_identity_retention_passed: false,
          identity_consistency_passed: false,
          emotion_match_passed: false,
          pronunciation_passed: false,
          pace_passed: false,
          long_form_drift_passed: false,
          notes: '',
        },
        production_assignment: {
          status: 'unassigned',
          voice_name: null,
        },
      };
      window.renderExpressiveReferenceBank(project);
      const referenceSection = document.getElementById('voice-reference-bank-section');
      const secondary = referenceSection?.closest('details.character-secondary-details');
      if (secondary) secondary.open = true;
      referenceSection?.scrollIntoView({ block: 'start' });
    `;
    const characterStatePrelude = `
      await refreshCharactersWorkspace();
      const auditEntry = structuredClone(
        characterWorkspaceEntries().find(item => item.canonical_name === 'THE DOCTOR')
        || characterWorkspaceEntries().find(item => item.eligible)
      );
      if (!auditEntry) throw new Error('No character is available for inspector audit');
      auditEntry.status = 'absent';
      auditEntry.eligible = true;
      auditEntry.resolution_status = 'resolved';
      auditEntry.speaking_status = 'speaker';
      voiceTrainingProject = null;
    `;
    const modelCacheFixture = `
      const cacheModels = [
        {
          schema_version: 1,
          model: {
            key: 'mlx_clone',
            repo_id: 'mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit',
            revision: '${'a'.repeat(40)}',
            runtime: 'mlx-audio',
            purpose: 'Standard supplied-clip voice cloning',
            estimated_size_bytes: 3104156243,
            required_paths: ['config.json', 'model.safetensors'],
            required_by_default: true,
            cache_name: 'models--mlx-community--Qwen3-TTS-12Hz-1.7B-Base-8bit',
          },
          state: 'cached',
          cached: true,
          repair_required: false,
          action: null,
          snapshot_path: '/shared-cache/models--mlx-community--Qwen3-TTS-12Hz-1.7B-Base-8bit/snapshots/${'a'.repeat(40)}',
          cache_root: '/shared-cache',
          file_count: 12,
          size_bytes: 3104156243,
          missing_required_paths: [],
          broken_symlinks: [],
        },
        {
          schema_version: 1,
          model: {
            key: 'mlx_voice_design',
            repo_id: 'mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit',
            revision: '${'b'.repeat(40)}',
            runtime: 'mlx-audio',
            purpose: 'Qwen VoiceDesign preview and synthesis',
            estimated_size_bytes: 3080138280,
            required_paths: ['config.json', 'model.safetensors'],
            required_by_default: true,
            cache_name: 'models--mlx-community--Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit',
          },
          state: 'missing',
          cached: false,
          repair_required: false,
          action: 'download',
          snapshot_path: null,
          cache_root: '/shared-cache',
          file_count: 0,
          size_bytes: 0,
          missing_required_paths: ['config.json', 'model.safetensors'],
          broken_symlinks: [],
        },
        {
          schema_version: 1,
          model: {
            key: 'mlx_whisper_large_v3_turbo',
            repo_id: 'mlx-community/whisper-large-v3-turbo',
            revision: '${'c'.repeat(40)}',
            runtime: 'mlx-whisper',
            purpose: 'Audio-preparer transcription',
            estimated_size_bytes: 1613979758,
            required_paths: ['config.json', 'weights.safetensors'],
            required_by_default: false,
            cache_name: 'models--mlx-community--whisper-large-v3-turbo',
          },
          state: 'incomplete',
          cached: false,
          repair_required: true,
          action: 'repair',
          snapshot_path: '/shared-cache/models--mlx-community--whisper-large-v3-turbo/snapshots/${'c'.repeat(40)}',
          cache_root: '/shared-cache',
          file_count: 1,
          size_bytes: 2048,
          missing_required_paths: ['weights.safetensors'],
          broken_symlinks: ['weights.safetensors'],
        },
      ];
      const cacheStatus = {
        schema_version: 1,
        models: cacheModels,
        cached_count: 1,
        missing_count: 1,
        incomplete_count: 1,
        cached_size_bytes: 3104156243,
        estimated_total_bytes: 7798274281,
        required_count: 2,
        required_missing_count: 1,
        required_incomplete_count: 0,
        cache_dir: '/shared-cache',
        operation: {
          running: false,
          logs: [],
          status: 'idle',
          action: null,
          model_keys: [],
          current_model_key: null,
          current_operation: null,
          completed_count: 0,
          total_count: 0,
          results: [],
          error: null,
          error_code: null,
        },
      };
    `;
    const newProjectPrepare = `
      document.querySelector('[data-tab="setup"][data-route="projects"]')?.click();
      await new Promise(resolve => setTimeout(resolve, 220));
      window.dispatchEvent(new CustomEvent('alexandria:new-project-requested'));
      await new Promise(resolve => setTimeout(resolve, 180));
    `;
    const stateCases = [
      {
        name: 'new-project-empty',
        tab: 'setup',
        width: 1536,
        height: 1024,
        prepare: newProjectPrepare,
      },
      {
        name: 'new-project-empty-compact',
        tab: 'setup',
        width: 1024,
        height: 768,
        prepare: newProjectPrepare,
      },
      {
        name: 'new-project-valid-epub',
        tab: 'setup',
        width: 1536,
        height: 1024,
        prepare: newProjectPrepare,
        fileInputs: [
          {
            selector: '#new-project-source',
            path: `${outputDir}/new-project-valid.epub`,
          },
        ],
      },
      {
        name: 'new-project-import-script',
        tab: 'setup',
        width: 1536,
        height: 1024,
        prepare: `${newProjectPrepare}
          const method = document.querySelector(
            'input[name="new-project-method"][value="import_existing_script"]'
          );
          method.checked = true;
          method.dispatchEvent(new Event('change', { bubbles: true }));
          await new Promise(resolve => setTimeout(resolve, 120));
        `,
        fileInputs: [
          {
            selector: '#new-project-source',
            path: `${outputDir}/new-project-valid-script.json`,
          },
        ],
      },
      {
        name: 'new-project-invalid-replacement',
        tab: 'setup',
        width: 1536,
        height: 1024,
        prepare: newProjectPrepare,
        fileInputs: [
          {
            selector: '#new-project-source',
            path: `${outputDir}/new-project-valid.epub`,
          },
          {
            selector: '#new-project-source',
            path: `${outputDir}/new-project-invalid.pdf`,
          },
        ],
        expectedConsoleErrorIncludes: ['422 (Unprocessable Entity)'],
      },
      {
        name: 'new-project-create-success',
        tab: 'setup',
        width: 1536,
        height: 1024,
        prepare: newProjectPrepare,
        fileInputs: [
          {
            selector: '#new-project-source',
            path: `${outputDir}/new-project-valid.epub`,
          },
        ],
        afterFilePrepare: `
          document.getElementById('new-project-submit')?.click();
          const deadline = Date.now() + 10000;
          while (
            !window.AlexandriaCanonicalInterface?.state()?.newProject?.completed
            && Date.now() < deadline
          ) {
            await new Promise(resolve => setTimeout(resolve, 100));
          }
        `,
      },
      {
        name: 'model-cache-inventory',
        tab: 'setup',
        width: 1440,
        height: 1000,
        prepare: `${modelCacheFixture}
          const panel = document.getElementById('model-cache-panel');
          panel.dataset.auditSkipLoad = 'true';
          panel.open = true;
          renderModelRegistryStatus(cacheStatus);
          panel.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'model-cache-inventory-narrow',
        tab: 'setup',
        width: 390,
        height: 844,
        prepare: `${modelCacheFixture}
          const panel = document.getElementById('model-cache-panel');
          panel.dataset.auditSkipLoad = 'true';
          panel.open = true;
          renderModelRegistryStatus(cacheStatus);
          panel.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'model-cache-running',
        tab: 'setup',
        width: 1440,
        height: 1000,
        prepare: `${modelCacheFixture}
          cacheStatus.operation = {
            running: true,
            logs: ['Downloading pinned model.'],
            status: 'running',
            action: 'download_required',
            model_keys: ['mlx_voice_design'],
            current_model_key: 'mlx_voice_design',
            current_operation: 'download',
            completed_count: 0,
            total_count: 1,
            results: [],
            error: null,
            error_code: null,
          };
          const panel = document.getElementById('model-cache-panel');
          panel.dataset.auditSkipLoad = 'true';
          panel.open = true;
          renderModelRegistryStatus(cacheStatus);
          stopModelCachePolling();
          panel.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'model-cache-failure',
        tab: 'setup',
        width: 1440,
        height: 1000,
        prepare: `${modelCacheFixture}
          cacheStatus.operation = {
            running: false,
            logs: ['Model cache operation failed.'],
            status: 'failed',
            action: 'repair',
            model_keys: ['mlx_whisper_large_v3_turbo'],
            current_model_key: null,
            current_operation: null,
            completed_count: 0,
            total_count: 1,
            results: [],
            error: 'Not enough disk space to repair this pinned snapshot.',
            error_code: 'insufficient_model_cache_space',
          };
          const panel = document.getElementById('model-cache-panel');
          panel.dataset.auditSkipLoad = 'true';
          panel.open = true;
          renderModelRegistryStatus(cacheStatus);
          panel.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'characters-existing-preparation',
        tab: 'characters',
        width: 1440,
        height: 1000,
        prepare: `
          await refreshCharactersWorkspace();
          const entry = characterWorkspaceEntries().find(item => item.canonical_name === 'THE DOCTOR');
          if (!entry) throw new Error('Prepared Doctor entry is unavailable');
          await selectVoiceTrainingCharacter(entry.character_id);
          window.scrollTo(0, 0);
        `,
      },
      {
        name: 'characters-existing-preparation-narrow',
        tab: 'characters',
        width: 390,
        height: 844,
        prepare: `
          await refreshCharactersWorkspace();
          const entry = characterWorkspaceEntries().find(item => item.canonical_name === 'THE DOCTOR');
          if (!entry) throw new Error('Prepared Doctor entry is unavailable');
          await selectVoiceTrainingCharacter(entry.character_id);
          window.scrollTo(0, 0);
        `,
      },
      {
        name: 'characters-existing-preparation-open',
        tab: 'characters',
        width: 1440,
        height: 1000,
        prepare: `
          await refreshCharactersWorkspace();
          const entry = characterWorkspaceEntries().find(item => item.canonical_name === 'THE DOCTOR');
          if (!entry) throw new Error('Prepared Doctor entry is unavailable');
          await selectVoiceTrainingCharacter(entry.character_id);
          const tools = document.querySelector('#voice-projects-detail > details.character-secondary-details:last-of-type');
          if (!tools) throw new Error('More voice tools is unavailable');
          tools.open = true;
          tools.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'characters-absent-preparation',
        tab: 'characters',
        width: 1440,
        height: 1000,
        prepare: `${characterStatePrelude}
          auditEntry.script_voice_name = 'ROZ';
          auditEntry.script_voice_mapping = { status: 'resolved', script_voice_name: 'ROZ' };
          characterVisualLastStatus = {
            entries: [{ entry_id: auditEntry.character_id, status: 'absent' }],
            process: { running: false },
            progress: { exists: false, status: 'none', character_ids: [] },
          };
          renderVoiceTrainingEntryDetail(auditEntry);
          await renderCharacterInlineVisual(auditEntry);
          window.scrollTo(0, 0);
        `,
      },
      {
        name: 'characters-absent-preparation-open',
        tab: 'characters',
        width: 1440,
        height: 1000,
        prepare: `${characterStatePrelude}
          auditEntry.script_voice_name = 'ROZ';
          auditEntry.script_voice_mapping = { status: 'resolved', script_voice_name: 'ROZ' };
          characterVisualLastStatus = {
            entries: [{ entry_id: auditEntry.character_id, status: 'absent' }],
            process: { running: false },
            progress: { exists: false, status: 'none', character_ids: [] },
          };
          renderVoiceTrainingEntryDetail(auditEntry);
          const tools = document.querySelector('#voice-projects-detail > details.character-specialist-tools');
          if (!tools) throw new Error('More voice tools is unavailable');
          tools.open = true;
          await renderCharacterInlineVisual(auditEntry);
          tools.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'characters-designed-voice',
        tab: 'characters',
        width: 1440,
        height: 1000,
        prepare: `${characterStatePrelude}
          auditEntry.script_voice_name = 'ROZ';
          auditEntry.script_voice_mapping = { status: 'resolved', script_voice_name: 'ROZ' };
          renderVoiceTrainingEntryDetail(auditEntry);
          window.scrollTo(0, 0);
        `,
      },
      {
        name: 'characters-alias',
        tab: 'characters',
        width: 1440,
        height: 1000,
        prepare: `${characterStatePrelude}
          auditEntry.script_voice_name = 'MARCUS';
          auditEntry.script_voice_mapping = { status: 'resolved', script_voice_name: 'MARCUS' };
          renderVoiceTrainingEntryDetail(auditEntry);
          window.scrollTo(0, 0);
        `,
      },
      {
        name: 'characters-missing-voice',
        tab: 'characters',
        width: 1440,
        height: 1000,
        prepare: `${characterStatePrelude}
          auditEntry.script_voice_name = 'UNCONFIGURED CHARACTER';
          auditEntry.script_voice_mapping = { status: 'missing', script_voice_name: null };
          renderVoiceTrainingEntryDetail(auditEntry);
          window.scrollTo(0, 0);
        `,
      },
      {
        name: 'characters-unresolved',
        tab: 'characters',
        width: 390,
        height: 844,
        prepare: `${characterStatePrelude}
          auditEntry.eligible = false;
          auditEntry.resolution_status = 'unresolved';
          auditEntry.blockers = ['Canonical identity remains unresolved.'];
          renderVoiceTrainingEntryDetail(auditEntry);
          await renderCharacterInlineVisual(auditEntry);
          window.scrollTo(0, 0);
        `,
      },
      {
        name: 'characters-visual-complete',
        tab: 'characters',
        width: 1440,
        height: 1000,
        prepare: `${characterStatePrelude}
          auditEntry.script_voice_name = 'NARRATOR';
          auditEntry.script_voice_mapping = { status: 'resolved', script_voice_name: 'NARRATOR' };
          characterVisualLastStatus = {
            entries: [{ entry_id: auditEntry.character_id, status: 'complete' }],
            process: { running: false },
            progress: { exists: false, status: 'none', character_ids: [] },
          };
          characterVisualDetailCache.set(auditEntry.character_id, {
            visual: {
              image_prompt_summary: 'Compact older traveler in a dark coat, alert posture, and weathered features.',
              profile: {
                build: [{ detail: 'Short, compact frame' }],
                clothing: [{ detail: 'Dark coat and practical hat' }],
                demeanor: [{ detail: 'Watchful, controlled, faintly amused' }],
              },
              variants: [{ label: 'Formal disguise', details: ['Cleaner coat', 'restrained posture'] }],
              conflicts: [],
              unknowns: [{ category: 'eye_color', question: 'Eye color is not stated.' }],
            },
          });
          renderVoiceTrainingEntryDetail(auditEntry);
          await renderCharacterInlineVisual(auditEntry);
          const appearance = document.getElementById('character-visual-inline');
          if (appearance) appearance.open = true;
          appearance?.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'characters-visual-incompatible',
        tab: 'characters',
        width: 390,
        height: 844,
        prepare: `${characterStatePrelude}
          auditEntry.script_voice_name = 'NARRATOR';
          auditEntry.script_voice_mapping = { status: 'resolved', script_voice_name: 'NARRATOR' };
          characterVisualLastStatus = {
            entries: [{
              entry_id: auditEntry.character_id,
              status: 'incompatible_source',
              error: 'The dossier belongs to an earlier source revision.',
            }],
            process: { running: false },
            progress: { exists: false, status: 'none', character_ids: [] },
          };
          renderVoiceTrainingEntryDetail(auditEntry);
          await renderCharacterInlineVisual(auditEntry);
          document.getElementById('character-visual-inline')?.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'characters-long-dense-data',
        tab: 'characters',
        width: 390,
        height: 844,
        prepare: `${characterStatePrelude}
          const rosterEntry = characterRosterApproved?.entries?.find(item => item.id === auditEntry.character_id);
          const statusEntry = voiceTrainingStatus?.entries?.find(item => item.character_id === auditEntry.character_id);
          const longName = 'Professor Bernice Surprise Summerfield of the Braxiatel Collection and Temporary Keeper of the Archive';
          if (rosterEntry) {
            rosterEntry.display_name = longName;
            rosterEntry.aliases = Array.from({ length: 12 }, (_, index) => 'Dense alias ' + (index + 1));
            rosterEntry.relationships = Array.from({ length: 8 }, (_, index) => 'Source-linked relationship ' + (index + 1));
            rosterEntry.species = ['Human'];
            rosterEntry.sample_lines = Array.from({ length: 10 }, (_, index) => 'Representative Script line ' + (index + 1) + ' with deliberately long source wording.');
            rosterEntry.evidence = Array.from({ length: 14 }, (_, index) => ({
              excerpt: 'Dense source evidence excerpt ' + (index + 1) + ' with enough wording to stress narrow wrapping.',
              start_char: index * 80,
              end_char: index * 80 + 72,
              category: 'name',
              confidence: 0.94,
              rationale: 'Explicit source evidence',
            }));
          }
          if (statusEntry) statusEntry.display_name = longName;
          auditEntry.display_name = longName;
          auditEntry.script_voice_name = 'NARRATOR';
          auditEntry.script_voice_mapping = { status: 'resolved', script_voice_name: 'NARRATOR' };
          renderVoiceTrainingEntryDetail(auditEntry);
          const details = document.querySelector('.character-profile-details');
          if (details) details.open = true;
          details?.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'voice-project-source-warning',
        tab: 'voice-projects',
        width: 1440,
        height: 1000,
        prepare: `
          const notice = document.getElementById('voice-projects-error');
          notice.textContent = 'The approved character roster belongs to a different source file.';
          notice.style.display = '';
          notice.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'voice-project-persona-approved',
        tab: 'voice-projects',
        width: 1440,
        height: 1000,
        prepare: `
          if (!window.voiceTrainingProject) await refreshVoiceTrainingStatus();
          await mutateVoiceTrainingProject('approve_persona', currentVoicePersonaPayload());
          window.scrollTo(0, 0);
        `,
      },
      {
        name: 'voice-project-synthetic-created',
        tab: 'voice-projects',
        width: 1440,
        height: 1000,
        prepare: `
          await mutateVoiceTrainingProject('create_synthetic_project', {
            seed_supported: true,
            global_seed: 1842,
            sample_target: 24,
          });
          window.scrollTo(0, 0);
        `,
      },
      {
        name: 'voice-project-reference-bank-review',
        tab: 'voice-projects',
        width: 1440,
        height: 1200,
        prepare: referenceBankStatePrepare,
      },
      {
        name: 'voice-project-reference-bank-review-narrow',
        tab: 'voice-projects',
        width: 390,
        height: 1000,
        prepare: referenceBankStatePrepare,
      },
      {
        name: 'character-designer-context',
        tab: 'designer',
        width: 1440,
        height: 1000,
        prepare: `
          activateWorkspaceTab('characters');
          await refreshCharactersWorkspace();
          const entry = characterWorkspaceEntries().find(
            item => item.canonical_name === 'THE DOCTOR'
          ) || characterWorkspaceEntries().find(item => item.eligible);
          if (!entry) throw new Error('No speaking character is available for contextual designer audit');
          await selectVoiceTrainingCharacter(entry.character_id);
          document.getElementById('design-voice-name').value = '';
          if (!openCharacterTool('designer')) {
            throw new Error('Contextual Voice designer did not open');
          }
          document.querySelector('[data-character-tool-context="designer"]')
            ?.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'character-designer-context-narrow',
        tab: 'designer',
        width: 390,
        height: 844,
        prepare: `
          activateWorkspaceTab('characters');
          await refreshCharactersWorkspace();
          const entry = characterWorkspaceEntries().find(
            item => item.canonical_name === 'THE DOCTOR'
          ) || characterWorkspaceEntries().find(item => item.eligible);
          if (!entry) throw new Error('No speaking character is available for contextual designer audit');
          await selectVoiceTrainingCharacter(entry.character_id);
          document.getElementById('design-voice-name').value = '';
          if (!openCharacterTool('designer')) {
            throw new Error('Contextual Voice designer did not open');
          }
          document.querySelector('[data-character-tool-context="designer"]')
            ?.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'llm-profile-runtime-override',
        tab: 'setup',
        width: 1440,
        height: 1000,
        prepare: `
          document.getElementById('llm-profiles-panel').open = true;
          const loaded = await loadLLMProfiles({ selectedStage: 'script' });
          const contextInput = document.getElementById('llm-profile-context');
          if (!contextInput) {
            throw new Error(JSON.stringify({
              loaded,
              selectedStage: window.llmProfilesSelectedStage,
              statusAvailable: Boolean(window.llmProfilesStatus),
              detailText: document.getElementById('llm-profile-detail')?.textContent.trim() || null,
              errorText: document.getElementById('llm-profiles-error')?.textContent.trim() || null,
            }));
          }
          contextInput.value = '8192';
          await saveLLMStageProfile();
          document.getElementById('setup-stage-profiles').scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'llm-profile-model-evidence-required',
        tab: 'setup',
        width: 1440,
        height: 1000,
        prepare: `
          document.getElementById('llm-profiles-panel').open = true;
          const loaded = await loadLLMProfiles({ selectedStage: 'script' });
          const modelInput = document.getElementById('llm-profile-model');
          if (!modelInput) {
            throw new Error(JSON.stringify({
              loaded,
              selectedStage: window.llmProfilesSelectedStage,
              statusAvailable: Boolean(window.llmProfilesStatus),
              detailText: document.getElementById('llm-profile-detail')?.textContent.trim() || null,
              errorText: document.getElementById('llm-profiles-error')?.textContent.trim() || null,
            }));
          }
          modelInput.value = 'qwen3.5:32b-unverified';
          updateLLMProfileEvidenceVisibility();
          await saveLLMStageProfile();
          document.getElementById('setup-stage-profiles').scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'llm-profile-removed',
        tab: 'setup',
        width: 1440,
        height: 1000,
        prepare: `
          await API.post('/api/llm_profiles/script/remove', {
            expected_profiles_fingerprint: window.llmProfilesStatus.profiles_fingerprint,
          });
          await loadLLMProfiles({ selectedStage: 'script' });
          document.getElementById('llm-profiles-panel').open = true;
          document.getElementById('setup-stage-profiles').scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'speaker-management-renamed',
        tab: 'speaker-management',
        width: 1440,
        height: 1000,
        prepare: `
          if (!window.speakerManagementStatus) await refreshSpeakerManagementStatus();
          const entry = speakerManagementEntries().find(item => item.canonical_name === 'THE DOCTOR');
          if (!entry) throw new Error('THE DOCTOR speaker entry is unavailable');
          if (window.speakerManagementSelectedId !== entry.character_id) {
            await selectSpeakerManagementEntry(entry.character_id);
          }
          await runSpeakerManagementOperation('rename', {
            entry_id: entry.character_id,
            new_name: 'THE TRAVELER',
            display_name: 'The Traveler',
            preserve_old_as_alias: true,
          });
          window.scrollTo(0, 0);
        `,
      },
      {
        name: 'speaker-management-undo',
        tab: 'speaker-management',
        width: 1440,
        height: 1000,
        prepare: `
          const latest = window.speakerManagementStatus?.history?.[0];
          if (!latest) throw new Error('No speaker-management operation is available to undo');
          await API.post('/api/speaker_management/undo', {
            operation_id: latest.operation_id,
          });
          await refreshSpeakerManagementStatus({ selectedId: window.speakerManagementSelectedId });
          window.scrollTo(0, 0);
        `,
      },
      {
        name: 'setup-recovery-log-behavior',
        tab: 'setup',
        width: 1440,
        height: 1000,
        prepare: `
          await refreshRecoveryStatus();
          const recovery = document.getElementById('recovery-center');
          if (!recovery) throw new Error('Project status disclosure is unavailable');
          recovery.open = true;
          await new Promise(resolve => requestAnimationFrame(resolve));
          recovery.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'characters-roster-log-behavior',
        tab: 'characters',
        width: 1440,
        height: 1000,
        prepare: `
          window.__characterRosterLogAudit = {};
          const rosterStatus = await refreshCharacterRosterStatus();
          rosterStatus.process.logs = [
            ...rosterStatus.process.logs,
            ...Array.from(
              { length: 60 },
              (_, index) => 'Persisted roster audit line ' + (index + 1) + '.'
            ),
          ];
          let logDetails = document.getElementById('character-roster-log-disclosure');
          if (!logDetails) throw new Error('Roster log disclosure is unavailable');
          logDetails.open = true;
          characterRosterLogFollowTail = true;
          renderCharacterRosterLog(rosterStatus.process);
          await new Promise(resolve => requestAnimationFrame(resolve));
          let output = document.getElementById('character-roster-logs');
          if (!output) throw new Error('Roster log output is unavailable');
          output.scrollTop = output.scrollHeight;
          window.__characterRosterLogAudit.openedAtTail = (
            output.scrollHeight - output.scrollTop - output.clientHeight
          ) <= 20;

          rosterStatus.process.logs = [
            ...rosterStatus.process.logs,
            'Live roster line while following the tail.'
          ];
          renderCharacterRosterLog(rosterStatus.process);
          await new Promise(resolve => requestAnimationFrame(resolve));
          logDetails = document.getElementById('character-roster-log-disclosure');
          output = document.getElementById('character-roster-logs');
          window.__characterRosterLogAudit.followedTailAfterRefresh = (
            logDetails.open
            && output.scrollHeight - output.scrollTop - output.clientHeight <= 20
          );

          output.scrollTop = 0;
          output.dispatchEvent(new Event('scroll'));
          window.__characterRosterLogAudit.manualPositionHadOverflow = (
            output.scrollHeight - output.clientHeight > 20
          );
          rosterStatus.process.logs = [
            ...rosterStatus.process.logs,
            'Live roster line while the user is reading older entries.'
          ];
          renderCharacterRosterLog(rosterStatus.process);
          await new Promise(resolve => requestAnimationFrame(resolve));
          logDetails = document.getElementById('character-roster-log-disclosure');
          output = document.getElementById('character-roster-logs');
          window.__characterRosterLogAudit.manualScrollPreserved = (
            logDetails.open && output.scrollTop === 0
          );
          logDetails.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'setup-runtime-open',
        tab: 'setup',
        width: 1440,
        height: 1000,
        prepare: `
          const details = document.getElementById('llm-runtime-panel');
          details.open = true;
          document.getElementById('setup-runtime').scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'setup-advanced-prompts-open',
        tab: 'setup',
        width: 1440,
        height: 1000,
        prepare: `
          document.getElementById('promptSettings').open = true;
          const nested = document.querySelector('#promptSettings .advanced-prompt-disclosure');
          if (nested) nested.open = true;
          document.getElementById('setup-advanced').scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'voices-speaker-open',
        tab: 'voices',
        width: 1440,
        height: 1000,
        prepare: `
          const details = [...document.querySelectorAll('.voice-panel')].find(
            item => item.dataset.voice === 'THE DOCTOR'
          );
          if (!details) throw new Error('THE DOCTOR voice panel is unavailable');
          details.open = true;
          details.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'voices-clone-open-narrow',
        tab: 'voices',
        width: 390,
        height: 844,
        prepare: `
          const details = [...document.querySelectorAll('.voice-panel')].find(
            item => item.dataset.voice === 'THE DOCTOR'
          );
          if (!details) throw new Error('THE DOCTOR voice panel is unavailable');
          details.open = true;
          details.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'voices-clone-upload-restored',
        tab: 'voices',
        width: 1440,
        height: 1000,
        prepare: `
          window.__cloneUploadAudit = {};
          let details = [...document.querySelectorAll('.voice-panel')].find(
            item => item.dataset.voice === 'THE DOCTOR'
          );
          if (!details) throw new Error('THE DOCTOR voice panel is unavailable');
          details.open = true;
          const controlled = details.querySelector('.clone-controlled-disclosure');
          if (controlled) controlled.open = true;
          const input = details.querySelector('.clone-voice-file-input');
          const transfer = new DataTransfer();
          transfer.items.add(new File(
            [new Uint8Array([82, 73, 70, 70, 0, 0, 0, 0, 87, 65, 86, 69])],
            'audit uploaded clone.wav',
            { type: 'audio/wav' },
          ));
          input.files = transfer.files;
          await handleCloneVoiceUpload(input);
          details = [...document.querySelectorAll('.voice-panel')].find(
            item => item.dataset.voice === 'THE DOCTOR'
          );
          const select = details?.querySelector('.designed-voice-select');
          window.__cloneUploadAudit = {
            panelOpen: Boolean(details?.open),
            controlledOpen: Boolean(details?.querySelector('.clone-controlled-disclosure')?.open),
            selectedUploadedClone: Boolean(select?.value?.startsWith('clone:audit_uploaded_clone_')),
            referencePath: details?.querySelector('.ref-audio')?.value || null,
          };
          details?.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'voices-clone-play-pause',
        tab: 'voices',
        width: 1440,
        height: 1000,
        prepare: `
          window.__cloneAudioAudit = {};
          const details = [...document.querySelectorAll('.voice-panel')].find(
            item => item.dataset.voice === 'THE DOCTOR'
          );
          if (!details) throw new Error('THE DOCTOR voice panel is unavailable');
          details.open = true;
          const button = details.querySelector('.clone-play-btn');
          if (!button) throw new Error('Clone reference play button is unavailable');
          const OriginalAudio = window.Audio;
          class AuditAudio extends EventTarget {
            constructor(src) {
              super();
              this.src = src;
              this.paused = true;
              this.currentTime = 0;
            }
            async play() {
              this.paused = false;
              this.dispatchEvent(new Event('play'));
            }
            pause() {
              if (this.paused) return;
              this.paused = true;
              this.dispatchEvent(new Event('pause'));
            }
          }
          window.Audio = AuditAudio;
          try {
            await playCloneVoice(button);
            window.__cloneAudioAudit.pauseShownWhilePlaying = button.querySelector('i')?.classList.contains('fa-pause') || false;
            window.__cloneAudioAudit.pauseLabel = button.getAttribute('aria-label');
            await playCloneVoice(button);
            window.__cloneAudioAudit.playShownWhilePaused = button.querySelector('i')?.classList.contains('fa-play') || false;
            window.__cloneAudioAudit.playLabel = button.getAttribute('aria-label');
          } finally {
            stopCloneReferenceAudio();
            window.Audio = OriginalAudio;
          }
          details.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'voices-alias-inherited',
        skip: true,
        tab: 'voices',
        width: 1440,
        height: 1000,
        prepare: `
          window.__voiceAliasAudit = {};
          let aliasCard = [...document.querySelectorAll('.voice-panel')].find(
            item => item.dataset.voice === 'MARCUS'
          );
          const targetCard = [...document.querySelectorAll('.voice-panel')].find(
            item => item.dataset.voice === 'NARRATOR'
          );
          if (!aliasCard || !targetCard) throw new Error('Alias browser fixture is unavailable');
          aliasCard.open = true;
          window.__voiceAliasAudit.initialIndependentHidden = aliasCard.querySelector('.voice-independent-config')?.hidden === true;
          window.__voiceAliasAudit.initialAliasVisible = aliasCard.querySelector('.voice-alias-inheritance')?.hidden === false;
          window.__voiceAliasAudit.initialDormantVoice = aliasCard.querySelector('.voice-select')?.value || null;
          const targetVoice = targetCard.querySelector('.voice-select');
          if (!targetVoice) throw new Error('Alias target voice selector is unavailable');
          targetVoice.value = 'Serena';
          await saveVoicesNow();
          window.__voiceAliasAudit.propagatedSource = aliasCard.querySelector('[data-alias-resolved-source]')?.textContent.trim() || null;
          const editButton = aliasCard.querySelector('.alias-edit-target');
          openAliasTarget(editButton);
          window.__voiceAliasAudit.targetOpened = targetCard.open === true;
          aliasCard.open = true;
          aliasCard.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'voices-alias-cleared',
        skip: true,
        tab: 'voices',
        width: 1440,
        height: 1000,
        prepare: `
          window.__voiceAliasAudit = {};
          let aliasCard = [...document.querySelectorAll('.voice-panel')].find(
            item => item.dataset.voice === 'MARCUS'
          );
          if (!aliasCard) throw new Error('MARCUS voice panel is unavailable');
          aliasCard.open = true;
          const select = aliasCard.querySelector('.alias-select');
          select.value = '';
          await handleVoiceAliasChange(select);
          aliasCard = [...document.querySelectorAll('.voice-panel')].find(
            item => item.dataset.voice === 'MARCUS'
          );
          if (!aliasCard) throw new Error('MARCUS voice panel did not reload');
          aliasCard.open = true;
          window.__voiceAliasAudit.restoredIndependentVisible = aliasCard.querySelector('.voice-independent-config')?.hidden === false;
          window.__voiceAliasAudit.aliasSummaryHidden = aliasCard.querySelector('.voice-alias-inheritance')?.hidden === true;
          window.__voiceAliasAudit.restoredType = aliasCard.querySelector('.voice-type:checked')?.value || null;
          window.__voiceAliasAudit.restoredVoice = aliasCard.querySelector('.voice-select')?.value || null;
          window.__voiceAliasAudit.savedAlias = aliasCard.dataset.savedAlias || null;
          aliasCard.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'dataset-progress-partial',
        tab: 'dataset-builder',
        width: 1440,
        height: 1000,
        prepare: `
          dsbRows = [
            { status: 'done' },
            { status: 'generating' },
          ];
          dsbBatchRunning = true;
          dsbUpdateProgress();
          document.getElementById('dsb-progress-wrap')?.scrollIntoView({ block: 'center' });
        `,
      },
      {
        name: 'voices-controlled-preview-approved',
        tab: 'voices',
        width: 1440,
        height: 1000,
        prepare: `
          window.__controlledCloneAudit = {};
          let details = [...document.querySelectorAll('.voice-panel')].find(
            item => item.dataset.voice === 'THE DOCTOR'
          );
          if (!details) throw new Error('THE DOCTOR voice panel is unavailable');
          details.open = true;
          const disclosure = details.querySelector('.clone-controlled-disclosure');
          if (!disclosure) throw new Error('Controlled clone disclosure is unavailable');
          disclosure.open = true;
          details.querySelector('.ref-text').value = 'The library is never empty; it merely changes who is listening.';
          details.querySelector('.controlled-clone-cfg').value = '2.5';
          details.querySelector('.controlled-clone-steps').value = '12';
          details.querySelector('.controlled-clone-max-tokens').value = '1536';
          const originalPost = API.post.bind(API);
          let savedVoiceConfig = null;
          API.post = async (path, body) => {
            if (path === '/api/clone_voices/controlled_preview') {
              return {
                backend: 'voxcpm2_controlled',
                model: 'mlx-community/VoxCPM2-4bit',
                audio_url: '/clone_voices/doctor_reference.wav',
                preview_fingerprint: 'audit-controlled-preview-fingerprint',
                configuration_fingerprint: 'audit-controlled-configuration-fingerprint',
                elapsed_seconds: 2.4,
                audio_duration_seconds: 3.2,
                real_time_factor: 0.75,
                settings: {
                  cfg_value: body.cfg_value,
                  inference_timesteps: body.inference_timesteps,
                  max_tokens: body.max_tokens,
                },
                requires_listen_confirmation: true,
              };
            }
            if (path === '/api/clone_voices/controlled_preview/confirm') {
              if (
                body.speaker !== 'THE DOCTOR'
                || body.preview_fingerprint !== 'audit-controlled-preview-fingerprint'
                || body.configuration_fingerprint !== 'audit-controlled-configuration-fingerprint'
              ) {
                throw new Error('Controlled preview confirmation payload did not match');
              }
              return {
                status: 'confirmed',
                approval_token: 'audit-controlled-approval-token',
              };
            }
            if (path === '/api/save_voice_config') {
              savedVoiceConfig = body;
              return { status: 'saved', aliases: {} };
            }
            return originalPost(path, body);
          };
          try {
            await generateControlledClonePreview(
              details.querySelector('.controlled-preview-button')
            );
            const audio = details.querySelector('[data-controlled-preview-audio] audio');
            const useButton = details.querySelector('.controlled-use-button');
            if (!audio) throw new Error('Controlled preview audio was not rendered');
            window.__controlledCloneAudit.lockedBeforePlayback = useButton.disabled;
            audio.dispatchEvent(new Event('ended'));
            await new Promise(resolve => setTimeout(resolve, 0));
            window.__controlledCloneAudit.lockedAfterEndedWithoutPlay = useButton.disabled;
            audio.dispatchEvent(new Event('play'));
            window.__controlledCloneAudit.lockedAfterPlay = useButton.disabled;
            audio.dispatchEvent(new Event('ended'));
            for (let attempt = 0; attempt < 20 && useButton.disabled; attempt += 1) {
              await new Promise(resolve => setTimeout(resolve, 10));
            }
            window.__controlledCloneAudit.enabledAfterPlayedToEnd = !useButton.disabled;
            window.__controlledCloneAudit.receiptRecorded = Boolean(
              details.dataset.controlledCloneApprovalToken
            );
            await useControlledClone(useButton);
            window.__controlledCloneAudit.savedAfterReceipt = (
              details.dataset.savedCloneBackend === 'voxcpm2_controlled'
            );
            window.__controlledCloneAudit.approvalTokenSent = Boolean(
              savedVoiceConfig?.['THE DOCTOR']?.controlled_clone_approval_token
            );
            window.__controlledCloneAudit.approvalTokenCleared = (
              !details.dataset.controlledCloneApprovalToken
            );
          } finally {
            API.post = originalPost;
          }
          details.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'voices-controlled-edit-fallback',
        tab: 'voices',
        width: 1440,
        height: 1000,
        prepare: `
          let details = [...document.querySelectorAll('.voice-panel')].find(
            item => item.dataset.voice === 'THE DOCTOR'
          );
          if (!details) throw new Error('THE DOCTOR voice panel is unavailable');
          details.open = true;
          const instruct = details.querySelector('.controlled-preview-instruct');
          if (!instruct) throw new Error('Controlled preview instruction is unavailable');
          instruct.value = instruct.value + ' Sharper urgency.';
          instruct.dispatchEvent(new Event('input', { bubbles: true }));
          window.__controlledCloneAudit.fallbackImmediate = (
            details.dataset.savedCloneBackend === 'qwen3_base'
            && !details.dataset.controlledPreviewFingerprint
            && !details.dataset.controlledPreviewPlayed
            && !details.dataset.controlledPreviewListened
          );
          await new Promise(resolve => setTimeout(resolve, 950));
          await loadVoices();
          details = [...document.querySelectorAll('.voice-panel')].find(
            item => item.dataset.voice === 'THE DOCTOR'
          );
          if (!details) throw new Error('THE DOCTOR voice panel did not reload');
          details.open = true;
          window.__controlledCloneAudit.fallbackPersistedAfterReload = (
            details.dataset.savedCloneBackend === 'qwen3_base'
          );
          details.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'dataset-project-loaded',
        tab: 'dataset-builder',
        width: 1440,
        height: 1000,
        prepare: `
          const select = document.getElementById('dsb-project-select');
          const option = select && [...select.options].find(item => item.value);
          if (option) {
            select.value = option.value;
            await window.dsbOnProjectChange();
          }
          window.scrollTo(0, 0);
        `,
      },
      {
        name: 'voice-capability-adapter-open',
        tab: 'training',
        width: 1440,
        height: 1000,
        prepare: `
          await loadVoiceBackendCapabilities();
          const details = document.getElementById('voice-capability-adapter-panel');
          if (details) details.open = true;
          details?.scrollIntoView({ block: 'center' });
        `,
      },
      {
        name: 'script-task-bundle-open',
        tab: 'script',
        width: 1440,
        height: 1000,
        prepare: `
          const details = document.getElementById('script-external-workflow');
          details.open = true;
          const select = document.getElementById('task-bundle-task');
          select.value = 'persona_generation';
          updateTaskBundleTargetState();
          document.getElementById('task-bundle-target').value = 'THE DOCTOR';
          details.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'script-task-bundle-open-narrow',
        tab: 'script',
        width: 390,
        height: 844,
        prepare: `
          const details = document.getElementById('script-external-workflow');
          details.open = true;
          const select = document.getElementById('task-bundle-task');
          select.value = 'visual_discovery';
          updateTaskBundleTargetState();
          document.getElementById('task-bundle-target').value = 'THE DOCTOR';
          details.scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'script-external-import-verified',
        tab: 'script',
        width: 1440,
        height: 1000,
        prepare: `
          const renderStarted = performance.now();
          renderExternalScriptCandidate({
            schema_version: 1,
            candidate_id: 'candidate_browser_verified_12345678',
            kind: 'annotated_script',
            status: 'inspected',
            created_at_utc: '2026-07-17T22:00:00Z',
            origin: { type: 'chatgpt_handoff_result' },
            summary: {
              entry_count: 1472,
              speaker_count: 18,
              speaker_labels: ['THE DOCTOR', 'ROZ', 'NARRATOR'],
              character_count: 184230,
              narrator_entry_count: 731,
              directed_entry_count: 1472,
            },
            provenance: {
              status: 'verified',
              label: 'Imported — source fidelity verified',
            },
            warnings: [
              'The existing voice configuration will be preserved; newly imported speakers may still require voice assignment.'
            ],
            snapshot: {
              current_script_fingerprint: '${'a'.repeat(64)}',
              checkpoint_status: 'resumable',
              generated_audio_count: 28,
            },
            consequences: {
              replace_script: true,
              remove_unrelated_metadata: true,
              replace_voice_config: false,
              rebuild_chunks: true,
              mark_generated_audio_stale: true,
              checkpoint_decision_required: true,
            },
            comparison: {
              current: {
                entry_count: 1360,
                speaker_count: 16,
                character_count: 177000,
              },
              imported: {
                entry_count: 1472,
                speaker_count: 18,
                character_count: 184230,
              },
              deltas: {
                entry_count: 112,
                speaker_count: 2,
                character_count: 7230,
              },
            },
            import_fingerprint: '${'b'.repeat(64)}',
            application: null,
          });
          document.getElementById('script-external-workflow').open = true;
          window.__phase24dRenderMs = performance.now() - renderStarted;
          document.getElementById('external-script-candidate').scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'script-external-import-unverified-narrow',
        tab: 'script',
        width: 390,
        height: 844,
        prepare: `
          const renderStarted = performance.now();
          renderExternalScriptCandidate({
            schema_version: 1,
            candidate_id: 'candidate_browser_unverified_1234',
            kind: 'annotated_script',
            status: 'inspected',
            created_at_utc: '2026-07-17T22:05:00Z',
            origin: { type: 'annotated_script_upload' },
            summary: {
              entry_count: 93,
              speaker_count: 7,
              speaker_labels: ['DOCTOR', 'NARRATOR'],
              character_count: 11280,
              narrator_entry_count: 44,
              directed_entry_count: 93,
            },
            provenance: {
              status: 'unverified',
              label: 'Imported — source fidelity not verified',
            },
            warnings: [
              'No selected source was available, so source fidelity was not verified.'
            ],
            snapshot: {
              current_script_fingerprint: null,
              checkpoint_status: 'none',
              generated_audio_count: 0,
            },
            consequences: {
              replace_script: true,
              remove_unrelated_metadata: false,
              replace_voice_config: true,
              rebuild_chunks: true,
              mark_generated_audio_stale: false,
              checkpoint_decision_required: false,
            },
            comparison: {
              current: null,
              imported: {
                entry_count: 93,
                speaker_count: 7,
                character_count: 11280,
              },
              deltas: null,
            },
            import_fingerprint: '${'c'.repeat(64)}',
            application: null,
          });
          document.getElementById('script-external-workflow').open = true;
          window.__phase24dRenderMs = performance.now() - renderStarted;
          document.getElementById('external-script-candidate').scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'script-external-structured-result-narrow',
        tab: 'script',
        width: 390,
        height: 844,
        prepare: `
          const observations = Array.from({ length: 1500 }, (_, offset) => {
            const index = offset + 1;
            const mentionText = index === 1
              ? 'The Doctor'
              : (index === 1500 ? 'Roz' : 'Character ' + index);
            return {
              observation_id: 'obs-' + index,
              mention_text: mentionText,
              normalized_label: mentionText.toUpperCase().replaceAll(' ', '_'),
              evidence_quote: mentionText + ' crossed the room.',
              notes: 'Representative whole-book roster observation.'
            };
          });
          const renderStarted = performance.now();
          renderExternalStructuredResult({
            candidate_id: 'structured_roster_browser_1234',
            kind: 'structured_result',
            status: 'inspected',
            task_id: 'task_${'d'.repeat(32)}',
            task_type: 'roster_discovery',
            task_label: 'Discover character roster',
            result_fingerprint: '${'d'.repeat(64)}',
            review: {
              root_type: 'object',
              item_count: observations.length,
              source_fingerprint_verified: true,
              artifact_fingerprints_verified: [],
            },
            result: {
              observations
            },
            native_transfer: {
              supported: true,
              destination: 'character_roster',
              label: 'Review roster observations',
              tab: 'characters',
            },
            routing: {
              status: 'awaiting_reconciliation',
              native_destination: 'character_roster',
              tab: 'characters',
              message: 'An approved roster exists. Reconcile the saved observations against the current roster.'
            },
            application: null,
          });
          document.getElementById('script-external-workflow').open = true;
          window.__phase24dRenderMs = performance.now() - renderStarted;
          document.getElementById('external-structured-result').scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'script-voice-profile-comparison-narrow',
        tab: 'script',
        width: 390,
        height: 844,
        prepare: `
          const renderStarted = performance.now();
          renderExternalStructuredResult({
            candidate_id: 'structured_voice_profiles_browser_1234',
            kind: 'structured_result',
            status: 'inspected',
            task_id: 'task_${'e'.repeat(32)}',
            task_type: 'persona_catalog_generation',
            task_label: 'Create voice profiles for all speaking identities',
            result_fingerprint: '${'e'.repeat(64)}',
            review: {
              root_type: 'object',
              item_count: 3,
              source_fingerprint_verified: true,
              artifact_fingerprints_verified: ['annotated_script', 'character_roster'],
            },
            result: {
              personas: [
                {
                  speaker: 'NARRATOR',
                  description: 'A measured neutral literary voice with warm resonance.',
                  ref_text: 'The library doors closed behind them.'
                },
                {
                  speaker: 'THE DOCTOR',
                  description: 'A compact incisive tenor with a light nasal edge.',
                  ref_text: 'The library is never empty; it merely changes who is listening.'
                },
                {
                  speaker: 'ROZ',
                  description: 'A grounded lower voice with dry authority and crisp diction.',
                  ref_text: 'That is not remotely reassuring.'
                }
              ],
              warnings: []
            },
            routing: {
              status: 'awaiting_reconciliation',
              native_destination: 'expressive_voices',
              tab: 'voice-projects',
              code: 'persona_catalog_comparison_required',
              message: 'Compare the current and imported Voice profile for each existing speaker, then choose which profiles to replace.',
              details: {
                new_speakers: ['ROZ'],
                conflicts: [
                  {
                    speaker: 'NARRATOR',
                    current: {
                      description: 'Warm literary narration with restrained tension.',
                      ref_text: 'The library doors closed behind them.',
                      approval_status: 'approved'
                    },
                    imported: {
                      description: 'A measured neutral literary voice with warm resonance.',
                      ref_text: 'The library doors closed behind them.'
                    }
                  },
                  {
                    speaker: 'THE DOCTOR',
                    current: {
                      description: 'Quick, precise, and alert with controlled warmth.',
                      ref_text: 'The library is never empty; it merely changes who is listening.',
                      approval_status: 'draft'
                    },
                    imported: {
                      description: 'A compact incisive tenor with a light nasal edge.',
                      ref_text: 'The library is never empty; it merely changes who is listening.'
                    }
                  }
                ]
              }
            },
            application: null,
          });
          document.getElementById('script-external-workflow').open = true;
          window.__phase24dRenderMs = performance.now() - renderStarted;
          document.getElementById('persona-catalog-conflicts').scrollIntoView({ block: 'start' });
        `,
      },
      {
        name: 'file-picker-drag-state',
        tab: 'script',
        width: 1440,
        height: 1000,
        prepare: `
          const picker = document.querySelector('#script-tab [data-file-picker]');
          picker?.classList.add('is-dragover');
        `,
      },
      {
        name: 'mobile-navigation-open',
        tab: 'setup',
        width: 390,
        height: 844,
        prepare: `document.getElementById('rail-mobile-toggle')?.click();`,
      },
      {
        name: 'success-toast',
        tab: 'setup',
        width: 1440,
        height: 1000,
        prepare: `showToast('Settings saved', 'success', 30000);`,
      },
      {
        name: 'destructive-confirmation',
        tab: 'editor',
        width: 1440,
        height: 1000,
        prepare: `window.__auditConfirm = showConfirm('Regenerate all generated audio? This will replace existing audio.');`,
      },
      {
        name: 'text-entry-dialog',
        tab: 'dataset-builder',
        width: 1440,
        height: 1000,
        prepare: `
          window.__auditPrompt = showTextPrompt({
            title: 'New dataset project',
            description: 'Create a workspace for one root voice identity.',
            label: 'Project name',
            placeholder: 'narrator_warm',
            confirmLabel: 'Create project',
          });
        `,
      },
    ];
    const selectedStateCases = mode === 'new-project'
      ? stateCases.filter(stateCase => stateCase.name.startsWith('new-project-'))
      : mode === 'legacy'
        ? stateCases.filter(stateCase => !stateCase.name.startsWith('new-project-'))
        : mode === 'shell' || mode === 'boundary12' || mode === 'boundary13' || mode === 'boundary13-final'
          ? []
          : stateCases;
    for (const stateCase of selectedStateCases) {
      if (stateCase.skip) continue;
      const consoleErrorStart = consoleErrors.length;
      states[stateCase.name] = await inspectState(client, {
        ...stateCase,
        screenshotPath: `${outputDir}/${stateCase.name}.png`,
      });
      if (stateCase.expectedConsoleErrorIncludes?.length) {
        const observed = consoleErrors.slice(consoleErrorStart);
        states[stateCase.name].expectedConsoleErrors = observed.filter(message =>
          stateCase.expectedConsoleErrorIncludes.some(fragment => message.includes(fragment))
        );
        const unexpected = observed.filter(message =>
          !stateCase.expectedConsoleErrorIncludes.some(fragment => message.includes(fragment))
        );
        consoleErrors.splice(consoleErrorStart, observed.length, ...unexpected);
      }
      await evaluate(client, `(() => {
        document.querySelectorAll('.toast').forEach(element => element.remove());
        document.querySelectorAll('.modal.show').forEach(element => {
          bootstrap.Modal.getInstance(element)?.hide();
        });
        document.querySelectorAll('.is-dragover').forEach(element => element.classList.remove('is-dragover'));
        document.querySelectorAll('[data-audit-skip-load]').forEach(element => {
          delete element.dataset.auditSkipLoad;
        });
      })()`);
      await wait(
        (stateCase.name.startsWith('characters-') || stateCase.name.startsWith('model-cache-'))
          ? 120
          : 400
      );
    }

    const payload = {
      desktop,
      narrow,
      canonicalShell,
      castVoiceEditor,
      boundary12Interactions,
      boundary13Interactions,
      boundary13FinalAcceptance,
      states,
      consoleErrors,
      runtimeErrors,
      networkErrors,
      networkRequests,
      mode,
    };
    process.stdout.write(`INTERFACE_CDP_AUDIT=${JSON.stringify(payload)}\n`);
  } finally {
    client.close();
  }
}

main().catch(error => {
  console.error(error.stack || error);
  process.exit(1);
});
