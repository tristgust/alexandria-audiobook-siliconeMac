'use strict';

const path = require('node:path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');

const PAGE_STYLES = [
  'project_flow.css', 'cast.css', 'settings_more.css', 'produce_export.css',
];
const ROUTES = [
  ['projects', '#/projects'],
  ['script', '#/script?project=fixture-project'],
  ['cast', '#/cast?project=fixture-project'],
  ['produce', '#/produce?project=fixture-project'],
  ['export', '#/export?project=fixture-project'],
  ['library', '#/library'],
  ['voices', '#/voices'],
  ['templates', '#/templates'],
  ['more', '#/more'],
  ['more/help-center', '#/more/help-center'],
  ['more/model-cache', '#/more/model-cache'],
];

function assertion(id, pass, expected, observed) {
  return { id, pass: Boolean(pass), expected, observed };
}

async function settle(session) {
  await session.evaluate(`new Promise((resolve) => requestAnimationFrame(
    () => requestAnimationFrame(() => requestAnimationFrame(() => resolve(true)))
  ))`);
}

async function snapshot(session) {
  return session.evaluate(`(() => {
    const owner = document.querySelector('[data-route-owner]');
    const heading = owner?.querySelector('[data-page-heading],h1');
    const profile = owner?.querySelector('[data-cast-profile]');
    const persona = owner?.querySelector('[data-persona-visual]');
    const header = document.querySelector(
      '[data-project-header]:not([hidden]),[data-global-header]:not([hidden])'
    );
    const headingBox = heading?.getBoundingClientRect();
    const headerBox = header?.getBoundingClientRect();
    return {
      routePath: document.body.dataset.routePath || '',
      routeFailure: document.body.dataset.routeFailure || '',
      owner: owner?.dataset.routeOwner || '',
      ownerCount: document.querySelectorAll('[data-route-owner]').length,
      heading: heading?.textContent?.trim() || '',
      activeId: document.activeElement?.id || '',
      overflow: Math.max(0, document.documentElement.scrollWidth - innerWidth),
      legacyCount: document.querySelectorAll(
        '[data-tab-panel],#legacy-tab-store,#setup-tab,#characters-tab,#editor-tab,#audio-tab'
      ).length,
      personaInsideProfile: Boolean(profile && persona && profile.contains(persona)),
      personaOutsideProfile: Boolean(persona && (!profile || !profile.contains(persona))),
      headingTop: headingBox ? Math.round(headingBox.top) : null,
      headerBottom: headerBox ? Math.round(headerBox.bottom) : null,
    };
  })()`);
}

async function navigate(session, routePath, hash) {
  await session.evaluate(`globalThis.AlexandriaShell.navigate(${JSON.stringify(hash)})`);
  await session.waitFor(
    `document.body.dataset.routePath === ${JSON.stringify(routePath)}
      && Boolean(document.querySelector('[data-route-owner]'))`,
  );
  await settle(session);
  return snapshot(session);
}

function browserFailures(events, origin) {
  const responses = events.filter((event) => (
    event.method === 'Network.responseReceived'
    && event.params?.response?.url?.startsWith(origin)
    && event.params.response.status >= 400
  )).map((event) => ({
    url: event.params.response.url,
    status: event.params.response.status,
  }));
  const requestFailed = events.filter(
    (event) => event.method === 'Network.loadingFailed' && !event.params?.canceled,
  ).map((event) => event.params);
  const pageErrors = events.filter(
    (event) => event.method === 'Runtime.exceptionThrown',
  ).map((event) => event.params);
  const consoleErrors = events.filter((event) => (
    event.method === 'Runtime.consoleAPICalled' && event.params?.type === 'error'
  )).map((event) => event.params);
  return { responses, requestFailed, pageErrors, consoleErrors };
}

async function runViewport(baseUrl, artifacts, width, height) {
  const viewport = `${width}x${height}`;
  const folder = path.join(artifacts, viewport);
  const session = await BrowserSession.open({
    url: baseUrl, artifacts: folder, width, height, gateBootstrap: true,
  });
  const assertions = [];
  const routes = [];
  try {
    const paused = await session.client.event(
      'Fetch.requestPaused',
      ({ request }) => /\/app_shell\.js(?:\?|$)/.test(request.url),
    );
    const firstPaint = await session.evaluate(`(() => ({
      legacyCount: document.querySelectorAll(
        '[data-tab-panel],#legacy-tab-store,#setup-tab,#characters-tab,#editor-tab,#audio-tab'
      ).length,
      destinationRoots: document.querySelectorAll('[data-canonical-destination-root]').length,
      bootstrapErrorVisible: !document.querySelector('[data-bootstrap-error]')?.hidden,
    }))()`);
    await session.screenshot('first-paint.png');
    await session.client.send('Fetch.continueRequest', { requestId: paused.requestId });
    await session.client.send('Fetch.disable');
    await session.waitFor(
      `document.body.dataset.shellState === 'ready' && Boolean(globalThis.AlexandriaShell)`,
    );
    const shell = await session.evaluate(`(() => {
      const pageLinks = [...document.querySelectorAll(
        'link[rel="stylesheet"][href*="/styles/pages/"]'
      )];
      return {
        pageStyles: pageLinks.map((link) => link.href.split('/').pop()),
        allStylesLoaded: pageLinks.length === 4 && pageLinks.every((link) => Boolean(link.sheet)),
        projectOrder: [...document.querySelectorAll(
          '[data-nav-group="project"] [data-route-link]'
        )].map((link) => link.textContent.trim()),
        globalOrder: [...document.querySelectorAll(
          '[data-nav-group="global"] [data-route-link]'
        )].map((link) => link.textContent.trim()),
      };
    })()`);
    assertions.push(
      assertion('first-paint-canonical', firstPaint.legacyCount === 0
        && firstPaint.destinationRoots === 1 && !firstPaint.bootstrapErrorVisible,
      { legacyCount: 0, destinationRoots: 1, bootstrapErrorVisible: false }, firstPaint),
      assertion('all-page-styles-loaded', shell.allStylesLoaded
        && JSON.stringify(shell.pageStyles) === JSON.stringify(PAGE_STYLES),
      PAGE_STYLES, shell),
      assertion('project-workflow-order',
        JSON.stringify(shell.projectOrder) === JSON.stringify(
          ['Script', 'Cast', 'Produce', 'Export'],
        ), ['Script', 'Cast', 'Produce', 'Export'], shell.projectOrder),
      assertion('global-order',
        JSON.stringify(shell.globalOrder) === JSON.stringify(
          ['Home', 'Library', 'Voices', 'Templates'],
        ), ['Home', 'Library', 'Voices', 'Templates'], shell.globalOrder),
    );
    for (const [routePath, hash] of ROUTES) {
      const state = await navigate(session, routePath, hash);
      routes.push(state);
      await session.screenshot(`${routePath.replaceAll('/', '-')}.png`);
      assertions.push(assertion(
        `${routePath}-direct-owner`,
        state.owner === routePath && state.ownerCount === 1 && !state.routeFailure
          && state.legacyCount === 0 && state.overflow === 0,
        { owner: routePath, ownerCount: 1, routeFailure: '', legacyCount: 0, overflow: 0 },
        state,
      ));
    }
    const cast = routes.find((state) => state.routePath === 'cast');
    assertions.push(assertion(
      'cast-persona-inside-selected-profile',
      cast?.personaInsideProfile && !cast?.personaOutsideProfile,
      true,
      cast,
    ));
    const settings = await navigate(session, 'settings', '#/settings?mode=advanced');
    const opened = await session.evaluate(`(() => {
      const link = document.querySelector('[data-settings-destination="runtime_diagnostics"]');
      link?.click();
      return Boolean(link);
    })()`);
    await session.waitFor(`document.body.dataset.routePath === 'more/maintenance'`);
    await settle(session);
    const maintenance = await snapshot(session);
    await session.evaluate('history.back()');
    await session.waitFor(`document.body.dataset.routePath === 'settings'`);
    await settle(session);
    const returned = await snapshot(session);
    await session.screenshot('settings-maintenance-back.png');
    assertions.push(assertion(
      'settings-maintenance-focus-position-back',
      settings.owner === 'settings' && opened
        && maintenance.owner === 'more/maintenance'
        && maintenance.activeId === 'maintenance-runtime-heading'
        && maintenance.headingTop >= maintenance.headerBottom + 16
        && returned.owner === 'settings' && returned.activeId === 'settings-advanced-heading',
      'focused Maintenance runtime below header, Back restores Settings advanced',
      { settings, opened, maintenance, returned },
    ));
    const failures = browserFailures(session.client.events, new URL(baseUrl).origin);
    assertions.push(assertion(
      'zero-browser-network-errors',
      Object.values(failures).every((items) => items.length === 0),
      { responses: [], requestFailed: [], pageErrors: [], consoleErrors: [] },
      failures,
    ));
    return {
      viewport,
      status: assertions.every((item) => item.pass) ? 'PASS' : 'RED',
      assertions,
      routes,
    };
  } catch (error) {
    assertions.push(assertion(
      'scenario-completed', false, 'completed workflow', error.stack || String(error),
    ));
    return { viewport, status: 'RED', assertions, routes };
  } finally {
    await session.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required(args, 'artifacts'));
  const baseUrl = required(args, 'url');
  const widths = required(args, 'widths').split(',').map((item) => (
    item.split('x').map((value) => Number(value))
  ));
  const viewports = [];
  for (const [width, height] of widths) {
    viewports.push(await runViewport(baseUrl, artifacts, width, height));
  }
  const report = {
    status: viewports.every((item) => item.status === 'PASS') ? 'PASS' : 'RED',
    viewports,
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  process.stdout.write(`B19_T06_CHECKPOINT=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
