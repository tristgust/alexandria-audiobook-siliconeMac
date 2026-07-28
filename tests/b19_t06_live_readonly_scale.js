'use strict';

const fs = require('fs');
const http = require('http');
const path = require('path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');

const scriptPageSize = (width) => width < 640 ? 30 : width < 1200 ? 60 : 80;
const producePageSize = (width) => width < 640 ? 30 : width < 1200 ? 75 : 80;
const displayProjectTitle = (project, fallback = '') => {
  const raw = String(project?.name || project?.source_title || fallback || '').trim();
  if (!raw) return 'Project workspace';
  const filename = String(project?.source_filename || '').split('/').at(-1) || '';
  const stem = filename.replace(/\.[^.]+$/, '');
  const derived = raw === stem || String(project?.source_title || '').trim() === stem;
  if (!derived) return raw;
  const parts = stem.split(/[_-]+/).filter(Boolean);
  while (parts.length > 1 && /\d/.test(parts[0])) parts.shift();
  const readable = parts.join(' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/\s+/g, ' ')
    .trim();
  return readable ? readable.replace(/\b\w/g, (letter) => letter.toUpperCase()) : raw;
};

const MIME = Object.freeze({
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.wav': 'audio/wav',
});

function runtimeErrors(events) {
  return events.filter((event) => (
    (event.method === 'Runtime.exceptionThrown')
    || (event.method === 'Runtime.consoleAPICalled' && event.params?.type === 'error')
    || (event.method === 'Network.responseReceived' && event.params?.response?.status >= 500)
    || (event.method === 'Network.loadingFailed' && event.params?.canceled !== true)
  ));
}

function safeStaticFile(staticRoot, pathname) {
  const relative = pathname === '/' ? '../static/index.html' : pathname.replace(/^\/static\//, '');
  const candidate = pathname === '/'
    ? path.resolve(staticRoot, '..', 'static', 'index.html')
    : path.resolve(staticRoot, relative);
  if (candidate !== path.resolve(staticRoot, 'index.html')
    && !candidate.startsWith(`${path.resolve(staticRoot)}${path.sep}`)) return null;
  return candidate;
}

function sendJson(response, status, payload) {
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'x-alexandria-preview': 'read-only',
  });
  response.end(JSON.stringify(payload));
}

async function handlePreviewNavigation({ request, requestUrl, response, upstream, previewActions }) {
  const match = request.method === 'POST'
    ? requestUrl.pathname.match(/^\/api\/projects\/([^/]+)\/open$/)
    : null;
  if (!match) return false;
  const projectId = decodeURIComponent(match[1]);
  const catalogResponse = await fetch(new URL('/api/projects', upstream), {
    headers: { accept: 'application/json' },
  });
  if (!catalogResponse.ok) {
    sendJson(response, 502, { detail: `Could not read project catalog: HTTP ${catalogResponse.status}` });
    return true;
  }
  const catalog = await catalogResponse.json();
  const project = (catalog.projects || []).find((item) => item.id === projectId);
  if (!project) {
    sendJson(response, 404, { detail: 'Project not found in the read-only preview catalog.' });
    return true;
  }
  const destination = project.current_recommended_stage || 'script';
  previewActions.push({ type: 'open-project', projectId, destination });
  sendJson(response, 200, {
    preview_read_only: true,
    catalog_fingerprint: catalog.catalog_fingerprint,
    project,
    activation: { state: 'preview', native_destination: destination },
    native_destination: destination,
  });
  return true;
}

async function startReadonlyServer({
  repoRoot, upstream, host = '127.0.0.1', port = 0, allowPreviewNavigation = false,
}) {
  const staticRoot = path.join(repoRoot, 'app', 'static');
  const requests = [];
  const blockedMutations = [];
  const previewActions = [];
  const server = http.createServer(async (request, response) => {
    const requestUrl = new URL(request.url, `http://${host}`);
    const pathname = requestUrl.pathname;
    requests.push({ method: request.method, path: `${pathname}${requestUrl.search}` });
    try {
      if (pathname === '/__preview/status') {
        sendJson(response, 200, {
          ready: true,
          mode: 'read-only',
          upstream: String(upstream),
          mutations: 'disabled',
        });
        return;
      }
      if (pathname.startsWith('/api/')) {
        if (!['GET', 'HEAD'].includes(request.method)) {
          if (allowPreviewNavigation && await handlePreviewNavigation({
            request, requestUrl, response, upstream, previewActions,
          })) return;
          blockedMutations.push({ method: request.method, path: pathname });
          sendJson(response, 405, {
            detail: 'This is a read-only repair preview. Changes, generation, and exports are disabled.',
          });
          return;
        }
        const target = new URL(`${pathname}${requestUrl.search}`, upstream);
        const upstreamResponse = await fetch(target, {
          method: request.method,
          headers: { accept: request.headers.accept || '*/*' },
        });
        const headers = {
          'cache-control': 'no-store',
          'x-alexandria-preview': 'read-only',
        };
        const contentType = upstreamResponse.headers.get('content-type');
        if (contentType) headers['content-type'] = contentType;
        response.writeHead(upstreamResponse.status, headers);
        if (request.method === 'HEAD') response.end();
        else response.end(Buffer.from(await upstreamResponse.arrayBuffer()));
        return;
      }
      if (pathname !== '/' && !pathname.startsWith('/static/')) {
        if (!['GET', 'HEAD'].includes(request.method)) {
          blockedMutations.push({ method: request.method, path: pathname });
          sendJson(response, 405, {
            detail: 'This is a read-only repair preview. Asset mutations are disabled.',
          });
          return;
        }
        const target = new URL(`${pathname}${requestUrl.search}`, upstream);
        const upstreamResponse = await fetch(target, {
          method: request.method,
          headers: {
            accept: request.headers.accept || '*/*',
            ...(request.headers.range ? { range: request.headers.range } : {}),
          },
        });
        const headers = {
          'cache-control': 'no-store',
          'x-alexandria-preview': 'read-only',
        };
        for (const name of [
          'accept-ranges', 'content-length', 'content-range', 'content-type',
          'etag', 'last-modified',
        ]) {
          const value = upstreamResponse.headers.get(name);
          if (value) headers[name] = value;
        }
        response.writeHead(upstreamResponse.status, headers);
        if (request.method === 'HEAD') response.end();
        else response.end(Buffer.from(await upstreamResponse.arrayBuffer()));
        return;
      }
      const file = safeStaticFile(staticRoot, pathname);
      if (!file || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
        response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
        response.end('Not found');
        return;
      }
      response.writeHead(200, {
        'content-type': MIME[path.extname(file)] || 'application/octet-stream',
        'cache-control': 'no-store',
        'x-alexandria-preview': 'read-only',
      });
      if (request.method === 'HEAD') response.end();
      else {
        const body = fs.readFileSync(file);
        response.end(allowPreviewNavigation && pathname === '/'
          ? Buffer.from(body.toString('utf8')
            .replace(
              '<title>Alexandria</title>',
              '<title>Alexandria · Read-only repair preview</title>',
            )
            .replace(
              '<body data-shell-state="loading">',
              '<body data-shell-state="loading" data-preview-mode="read-only">',
            ))
          : body);
      }
    } catch (error) {
      sendJson(response, 502, { detail: String(error?.message || error) });
    }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, resolve);
  });
  const address = server.address();
  return {
    url: `http://${host}:${address.port}/`,
    requests,
    blockedMutations,
    previewActions,
    close: () => new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())),
    server,
  };
}

async function waitForRoute(session, pathName, stateExpression = 'true') {
  await session.waitFor(`document.body.dataset.routePath === ${JSON.stringify(pathName)}
    && document.body.dataset.shellState === 'ready' && (${stateExpression})`);
}

async function shellSnapshot(session, ownerName) {
  return session.evaluate(`(() => {
    const owner = document.querySelector('[data-route-owner="${ownerName}"]');
    const projectGroup = document.querySelector('[data-nav-group="project"]');
    const projectContext = document.querySelector('[data-nav-project-context]');
    const rail = document.querySelector('.nav-rail')?.getBoundingClientRect();
    const header = document.querySelector('.app-header:not([hidden])')?.getBoundingClientRect();
    return {
      owner: Boolean(owner),
      projectTitle: document.querySelector('[data-shell-project-title]')?.textContent || '',
      navProjectTitle: document.querySelector('[data-nav-project-title]')?.textContent || '',
      projectGroupVisible: Boolean(projectGroup && !projectGroup.hidden),
      projectContextVisible: Boolean(projectContext && !projectContext.hidden),
      projectHref: document.querySelector('[data-nav-project-link]')?.getAttribute('href') || '',
      scrollHeight: owner?.scrollHeight || 0,
      railHeight: rail?.height || 0,
      headerTop: header?.top || 0,
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 1,
      shellState: document.body.dataset.shellState || '',
      pageState: owner?.dataset.pageState || owner?.dataset.castState || '',
    };
  })()`);
}

async function liveExpectations(upstream) {
  const get = async (pathname) => {
    const response = await fetch(new URL(pathname, upstream));
    if (!response.ok) throw new Error(`${pathname} returned HTTP ${response.status}`);
    return response.json();
  };
  const [catalog, script, cast, produce] = await Promise.all([
    get('/api/projects'), get('/api/annotated_script'), get('/api/cast'), get('/api/produce'),
  ]);
  const projects = Array.isArray(catalog.projects) ? catalog.projects : [];
  const projectId = catalog.current_project_id || catalog.last_selected_project_id || projects[0]?.id || '';
  const project = projects.find((item) => item.id === projectId) || projects[0] || {};
  return {
    projectId,
    projectTitle: displayProjectTitle(project, projectId),
    projectCount: projects.length,
    scriptEntries: Array.isArray(script) ? script.length : 0,
    castCharacters: Array.isArray(cast.characters) ? cast.characters.length : 0,
    produceChunks: Array.isArray(produce.chunks) ? produce.chunks.length : 0,
    produceCurrent: Number(produce.summary?.current_count ?? produce.counts?.current) || 0,
  };
}

async function inspectViewport(server, artifacts, expected, width, height) {
  const viewport = `${width}x${height}`;
  const folder = path.join(artifacts, viewport);
  fs.mkdirSync(folder, { recursive: true });
  const session = await BrowserSession.open({
    url: `${server.url}#/projects`, artifacts: folder, width, height,
  });
  const routes = {};
  try {
    await waitForRoute(session, 'projects', `Boolean(document.querySelector('[data-route-owner="projects"]'))`);
    const home = await session.evaluate(`(() => {
      const root = document.querySelector('[data-route-owner="projects"]');
      const global = document.querySelector('[data-global-header]');
      const rail = document.querySelector('.nav-rail')?.getBoundingClientRect();
      const header = global?.getBoundingClientRect();
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
        playerAbsent: Boolean(document.querySelector('[data-persistent-player]')?.hidden),
        playerState: document.querySelector('[data-persistent-player]')?.dataset.state || '',
        playerHeight: Math.round(document.querySelector('[data-persistent-player]')?.getBoundingClientRect().height || 0),
        horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 1,
        railHeight: rail?.height || 0,
        headerTop: header?.top || 0,
      };
    })()`);
    await session.screenshot('projects-live.png');
    await session.evaluate(`document.querySelector('[data-new-project-open]').click()`);
    await session.waitFor(`Boolean(document.querySelector('[data-new-project] [role="dialog"]'))`);
    const newProject = await session.evaluate(`(() => {
      const dialog = document.querySelector('[data-new-project] [role="dialog"]');
      const visualColumns = (selector, children) => {
        const node = document.querySelector(selector);
        if (!node) return 0;
        const items = [...node.querySelectorAll(children)].filter((item) => item.getBoundingClientRect().width > 0);
        return new Set(items.map((item) => Math.round(item.getBoundingClientRect().left))).size;
      };
      const create = [...dialog.querySelectorAll('button')].find((button) => button.textContent.includes('Create Project'));
      return {
        sections: dialog?.querySelectorAll('.new-project__section').length || 0,
        bodyColumns: visualColumns('.new-project__body', ':scope > *'),
        methodColumns: visualColumns('.new-project__method-options', ':scope > .choice'),
        presetColumns: visualColumns('.new-project__preset-options', ':scope > .choice'),
        width: Math.round(dialog?.getBoundingClientRect().width || 0),
        height: Math.round(dialog?.getBoundingClientRect().height || 0),
        createDisabled: Boolean(create?.disabled),
        described: dialog?.getAttribute('aria-describedby') === 'new-project-description',
        overflow: document.documentElement.scrollWidth > innerWidth + 1,
      };
    })()`);
    await session.screenshot('new-project-live.png');
    await session.evaluate(`document.querySelector('[data-new-project-close]').click()`);
    await session.waitFor(`!document.querySelector('[data-new-project]')`);

    const started = Date.now();
    await session.evaluate(`AlexandriaShell.navigate('#/script')`);
    await waitForRoute(session, 'script', `Boolean(document.querySelector('[data-route-owner="script"]'))`);
    routes.script = {
      ...await shellSnapshot(session, 'script'),
      ...await session.evaluate(`(() => {
        const owner = document.querySelector('[data-route-owner="script"]');
        return {
          rows: owner?.querySelectorAll('.script-entry').length || 0,
          footer: owner?.querySelector('[data-script-collection-footer]')?.textContent || '',
          loadMore: Boolean(owner?.querySelector('[data-script-load-more]')),
        };
      })()`),
      readyMs: Date.now() - started,
    };
    await session.screenshot('script-live.png');

    await session.evaluate(`AlexandriaShell.navigate('#/cast')`);
    await waitForRoute(session, 'cast', `document.querySelector('[data-cast-page]')?.dataset.castState !== 'loading'`);
    routes.cast = {
      ...await shellSnapshot(session, 'cast'),
      ...await session.evaluate(`(() => {
        const columns = (selector) => {
          const node = document.querySelector(selector);
          if (!node) return 0;
          return getComputedStyle(node).gridTemplateColumns.split(' ').filter(Boolean).length;
        };
        const profile = document.querySelector('[data-cast-profile]');
        return {
          rows: document.querySelectorAll('[data-cast-roster] [role="option"]').length,
          selected: document.querySelector('[data-cast-roster] [role="option"][aria-selected="true"]')?.dataset.characterId || '',
          voiceColumns: columns('.cast-profile__voice-facts'),
          referenceColumns: columns('.cast-profile__reference-grid'),
          collapsedSections: [...(profile?.querySelectorAll(':scope > .cast-profile__section > .cast-profile__disclosure > .disclosure__trigger') || [])]
            .filter((trigger) => trigger.getAttribute('aria-expanded') === 'false').length,
          editableByDefault: Boolean(profile?.querySelector('[data-cast-assigned-voice]')),
          exposesBackendName: /qwen|index[- ]?tts|backend/i.test(profile?.innerText || ''),
        };
      })()`),
    };
    await session.screenshot('cast-live.png');

    await session.evaluate(`AlexandriaShell.navigate('#/produce')`);
    await waitForRoute(session, 'produce', `Boolean(document.querySelector('[data-produce-page]'))`);
    routes.produce = {
      ...await shellSnapshot(session, 'produce'),
      ...await session.evaluate(`(() => {
        const owner = document.querySelector('[data-produce-page]');
        const rows = owner?.querySelectorAll('[data-audio-row]') || [];
        return {
          rows: rows.length,
          durationCells: owner?.querySelectorAll('.audio-row__duration').length || 0,
          monograms: owner?.querySelectorAll('.audio-row__identity .monogram').length || 0,
          footer: owner?.querySelector('[data-produce-collection-footer]')?.textContent || '',
          filterText: owner?.querySelector('.produce-filters')?.textContent || '',
          selectedState: owner?.querySelector('[data-audio-row][aria-selected="true"]')?.dataset.audioState || '',
          inspectorState: (() => {
            const inspector = owner?.querySelector('.produce-inspector');
            if (!inspector || inspector.hidden) return 'hidden';
            return inspector.dataset.inspectorMode === 'overlay' ? 'overlay' : 'open';
          })(),
        };
      })()`),
    };
    await session.screenshot('produce-live.png');
    const playablePoint = await session.evaluate(`(() => {
      const control = document.querySelector('[data-produce-page] .produce-play:not(:disabled)');
      if (!control) return null;
      control.scrollIntoView({ block: 'center', inline: 'nearest' });
      const rect = control.getBoundingClientRect();
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    })()`);
    if (playablePoint) {
      await session.client.send('Input.dispatchMouseEvent', {
        type: 'mousePressed', x: playablePoint.x, y: playablePoint.y,
        button: 'left', clickCount: 1,
      });
      await session.client.send('Input.dispatchMouseEvent', {
        type: 'mouseReleased', x: playablePoint.x, y: playablePoint.y,
        button: 'left', clickCount: 1,
      });
      await session.waitFor(`document.querySelector('[data-persistent-player] audio')?.readyState >= 1`);
      routes.produce.playback = await session.evaluate(`(() => {
        const player = document.querySelector('[data-persistent-player]');
        const media = player?.querySelector('audio.persistent-player__media');
        return {
          native: player?.getPlayerState?.().native === true,
          src: media?.currentSrc || '',
          readyState: media?.readyState || 0,
          duration: Number(media?.duration) || 0,
          mediaError: media?.error?.code || 0,
        };
      })()`);
      await session.evaluate(`document.querySelector('[data-persistent-player] audio')?.pause()`);
    } else routes.produce.playback = null;

    await session.evaluate(`AlexandriaShell.navigate('#/export')`);
    await waitForRoute(session, 'export', `Boolean(document.querySelector('[data-export-page]'))`);
    routes.export = {
      ...await shellSnapshot(session, 'export'),
      ...await session.evaluate(`(() => {
        const visualColumns = (selector, children) => {
          const node = document.querySelector(selector);
          if (!node) return 0;
          const items = [...node.querySelectorAll(children)].filter((item) => (
            getComputedStyle(item).display !== 'none' && item.getBoundingClientRect().width > 0
          ));
          return new Set(items.map((item) => Math.round(item.getBoundingClientRect().left))).size;
        };
        const page = document.querySelector('[data-export-page]');
        return {
          workspaceColumns: visualColumns('.export-grid', ':scope > .export-panel'),
          formatColumns: visualColumns('.export-formats .option-group', ':scope > .choice'),
          metricColumns: visualColumns('.export-summary-metrics', ':scope > div'),
          metricCount: page?.querySelectorAll('.export-summary-metrics > div').length || 0,
          coverWidth: document.querySelector('.export-publication .source-cover')?.getBoundingClientRect().width || 0,
          readinessNotices: page?.querySelectorAll('.export-readiness .notice').length || 0,
          readinessText: page?.querySelector('.export-readiness')?.textContent || '',
          formatText: page?.querySelector('.export-formats')?.textContent || '',
        };
      })()`),
    };
    await session.screenshot('export-live.png');

    const errors = runtimeErrors(session.client.events);
    const escapedProjectId = expected.projectId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const assertions = {
      noRuntimeErrors: errors.length === 0,
      noMutations: server.blockedMutations.length === 0,
      referenceProjectHome: home.globalTitle === 'Project Home'
        && home.globalSubtitle === 'Open an existing project or create a new one.'
        && home.searchInHeader && home.primaryActions === 1
        && !home.projectGroupHidden && home.projectContextHidden
        && home.continuation && home.stageTrackers === 1
        && home.rows === expected.projectCount && home.rowPrimaryActions === 0
        && !home.playerAbsent
        && home.playerState === 'inactive'
        && home.playerHeight === 80,
      referenceNewProject: newProject.sections === 5
        && newProject.bodyColumns === (width < 640 ? 1 : 2)
        && newProject.methodColumns === (width < 640 ? 1 : width < 900 ? 2 : 3)
        && newProject.presetColumns === (width < 640 ? 1 : width < 1200 ? 2 : 4)
        && newProject.width <= Math.min(1080, width)
        && newProject.height <= Math.min(848, height)
        && newProject.createDisabled && newProject.described && !newProject.overflow,
      projectResolvedEverywhere: Object.values(routes).every((route) => (
        route.projectTitle === expected.projectTitle && route.navProjectTitle === expected.projectTitle
        && route.projectGroupVisible && !route.projectContextVisible
        && new RegExp(`project=${escapedProjectId}`).test(route.projectHref)
      )),
      noHorizontalOverflow: !home.horizontalOverflow
        && !newProject.overflow && Object.values(routes).every((route) => !route.horizontalOverflow),
      narrowNavigationLeavesPageVisible: width >= 640 || (
        home.railHeight <= height * 0.7 && home.headerTop <= height * 0.7
        && Object.values(routes).every((route) => (
          route.railHeight <= height * 0.7 && route.headerTop <= height * 0.7
        ))
      ),
      boundedScript: routes.script.rows === Math.min(scriptPageSize(width), expected.scriptEntries)
        && routes.script.loadMore
        && routes.script.footer.replaceAll(',', '').includes(
          `Showing 1–${Math.min(scriptPageSize(width), expected.scriptEntries)} of ${expected.scriptEntries} entries`
        ) && routes.script.scrollHeight < 50000,
      completeCast: expected.castCharacters === 0
        ? routes.cast.rows === 0 && routes.cast.pageState === 'empty' && !routes.cast.selected
        : routes.cast.rows === expected.castCharacters && Boolean(routes.cast.selected),
      referenceCastComposition: expected.castCharacters === 0
        ? routes.cast.pageState === 'empty'
          && routes.cast.collapsedSections === 0
          && routes.cast.voiceColumns === 0
          && routes.cast.referenceColumns === 0
        : routes.cast.collapsedSections === 3
          && !routes.cast.editableByDefault
          && (width < 640
            ? routes.cast.voiceColumns === 1 && routes.cast.referenceColumns === 1
            : width < 1200
              ? routes.cast.voiceColumns === 2 && routes.cast.referenceColumns === 1
              : routes.cast.voiceColumns === 2 && routes.cast.referenceColumns === 2),
      castHidesBackendNames: !routes.cast.exposesBackendName,
      boundedProduce: routes.produce.rows >= Math.min(producePageSize(width), expected.produceChunks)
        && routes.produce.rows <= Math.min(producePageSize(width) + 1, expected.produceChunks)
        && routes.produce.footer.replaceAll(',', '').includes(
          `Showing ${routes.produce.rows} of ${expected.produceChunks} chunks`
        )
        && routes.produce.scrollHeight < 60000,
      completeProduceRows: routes.produce.durationCells === routes.produce.rows
        && routes.produce.monograms === routes.produce.rows,
      countedProduceFilters: [
        'Ready to generate', 'Needs listening', 'Failed', 'Stale', 'Current',
      ].every((label) => routes.produce.filterText.includes(label))
        && !routes.produce.filterText.includes('All '),
      responsiveProduceInspector: width >= 1180
        ? routes.produce.inspectorState === 'open'
        : routes.produce.inspectorState === 'hidden',
      verifiedProducePlayback: expected.produceCurrent > 0
        ? Boolean(
          routes.produce.playback?.native
          && routes.produce.playback.readyState >= 1
          && routes.produce.playback.duration > 0
          && routes.produce.playback.mediaError === 0
          && /\/voicelines\//.test(routes.produce.playback.src)
        )
        : routes.produce.playback == null,
      exportMounted: routes.export.owner && routes.export.shellState === 'ready',
      referenceExportComposition: width < 640
        ? routes.export.workspaceColumns === 1 && routes.export.formatColumns === 1
        : width <= 900
          ? routes.export.workspaceColumns === 1 && routes.export.formatColumns === 2
          : routes.export.workspaceColumns === 2 && routes.export.formatColumns === 2,
      publicationScale: routes.export.metricCount === 3
        && routes.export.metricColumns === 3
        && routes.export.coverWidth >= (width < 640 ? 88 : width < 1200 ? 96 : 112),
      consolidatedExportReadiness: routes.export.readinessNotices <= 1,
      canonicalExportFormats: [
        'M4B audiobook', 'MP3 audio file', 'Audacity project package', 'Separate chapter files',
      ].every((label) => routes.export.formatText.includes(label)),
      directScriptReady: routes.script.readyMs < 15000,
    };
    return {
      viewport,
      status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL',
      assertions,
      home,
      newProject,
      routes,
      errors,
    };
  } finally {
    await session.close();
  }
}

async function servePreview({ repoRoot, upstream, host, port }) {
  const expected = await liveExpectations(upstream);
  const server = await startReadonlyServer({
    repoRoot, upstream, host, port, allowPreviewNavigation: true,
  });
  process.stdout.write(`Alexandria read-only repair preview: ${server.url}\n`);
  process.stdout.write(`Serving ${expected.projectCount} projects, ${expected.scriptEntries} Script entries, ${expected.castCharacters} Cast characters, and ${expected.produceChunks} Produce chunks.\n`);
  let closing = false;
  const close = async () => {
    if (closing) return;
    closing = true;
    await server.close();
  };
  process.once('SIGINT', close);
  process.once('SIGTERM', close);
  await new Promise((resolve) => server.server.once('close', resolve));
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const repoRoot = path.resolve(args['repo-root'] || path.join(__dirname, '..'));
  const upstream = required(args, 'upstream');
  if (args['serve-only']) {
    const host = String(args.host || '127.0.0.1');
    const port = Number(args.port || 0);
    if (!Number.isInteger(port) || port < 0 || port > 65535) {
      throw new Error('--port must be an integer from 0 through 65535');
    }
    await servePreview({ repoRoot, upstream, host, port });
    return;
  }
  const artifacts = path.resolve(required(args, 'artifacts'));
  const viewports = String(args.viewports || '1536x1024,1024x768')
    .split(',').map((value) => value.split('x').map(Number));
  fs.mkdirSync(artifacts, { recursive: true });
  const expected = await liveExpectations(upstream);
  const server = await startReadonlyServer({ repoRoot, upstream });
  const results = [];
  try {
    for (const [width, height] of viewports) {
      results.push(await inspectViewport(server, artifacts, expected, width, height));
    }
  } finally {
    await server.close();
  }
  const report = {
    status: results.every((result) => result.status === 'PASS') ? 'PASS' : 'FAIL',
    upstream,
    expected,
    viewports,
    results,
    requestCount: server.requests.length,
    apiRequests: server.requests.filter((request) => request.path.startsWith('/api/')),
    blockedMutations: server.blockedMutations,
    cleanup: { serverClosed: !server.server.listening },
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  process.stdout.write(`B19_T06_LIVE_READONLY_SCALE=${JSON.stringify({
    status: report.status,
    results: results.map((result) => ({ viewport: result.viewport, status: result.status, assertions: result.assertions })),
    requestCount: report.requestCount,
    blockedMutations: report.blockedMutations,
    cleanup: report.cleanup,
  })}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
