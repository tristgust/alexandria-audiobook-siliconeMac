'use strict';

const fs = require('fs');
const path = require('path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');

async function settle(session) {
  await session.evaluate(`new Promise((resolve) => requestAnimationFrame(
    () => requestAnimationFrame(() => resolve(true))
  ))`);
}

function pathOnly(routePath) {
  return routePath.split('?')[0];
}

async function snapshot(session, expected) {
  return session.evaluate(`(() => {
    const visible = (node) => node && !node.hidden
      && getComputedStyle(node).display !== 'none'
      && node.getBoundingClientRect().width > 0 && node.getBoundingClientRect().height > 0;
    const roots = [...document.querySelectorAll('#canonical-destination-root,[data-canonical-destination-root]')];
    const ownerKey = ${JSON.stringify(pathOnly(expected.path))};
    const owner = document.querySelector('[data-route-owner="' + ownerKey + '"],'
      + '[data-page="' + ownerKey + '"]');
    const headings = [...document.querySelectorAll('[data-page-heading],main h1,main h2,#shell-page-title,#settings-surface-title')]
      .filter(visible);
    const parsed = window.AlexandriaRoutes?.parseHash(location.hash) || null;
    return {
      hash: location.hash, title: document.title,
      destination: document.body.dataset.destination || null,
      tool: document.body.dataset.routeTool || parsed?.context?.tool || null,
      context: parsed?.context || Object.fromEntries(new URLSearchParams(location.hash.split('?')[1] || '')),
      destinationRootCount: roots.length,
      directOwner: Boolean(owner && roots[0]?.contains(owner) && visible(owner)),
      visibleHeadings: headings.map((node) => ({ id: node.id || null, text: node.textContent.trim() })),
      activeId: document.activeElement?.id || null,
      activeText: document.activeElement?.textContent?.trim().slice(0, 120) || null,
      legacyActivePanels: [...document.querySelectorAll('[data-tab-panel]')]
        .filter(visible).map((node) => node.id || node.dataset.tabPanel),
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 1,
    };
  })()`);
}

function runtimeLog(events, baseUrl) {
  const origin = new URL(baseUrl).origin;
  return {
    consoleErrors: events.filter((item) => item.method === 'Runtime.consoleAPICalled'
      && item.params?.type === 'error').map((item) => item.params),
    exceptions: events.filter((item) => item.method === 'Runtime.exceptionThrown')
      .map((item) => item.params?.exceptionDetails || item.params),
    serverErrors: events.filter((item) => item.method === 'Network.responseReceived')
      .map((item) => item.params?.response).filter((response) => response
        && response.url.startsWith(origin) && response.status >= 500)
      .map((response) => ({ url: response.url, status: response.status })),
    failedRequests: events.filter((item) => item.method === 'Network.loadingFailed')
      .map((item) => item.params)
      .filter((item) => item?.type !== 'Image' && item?.canceled !== true),
  };
}

async function navigate(session, value, expected, baseUrl) {
  const eventIndex = session.client.events.length;
  await session.evaluate(`location.hash = ${JSON.stringify(`/${value}`)}`);
  await session.waitFor(`document.body.dataset.destination === ${JSON.stringify(expected.destination)}`);
  await settle(session);
  return {
    ...await snapshot(session, expected),
    runtime: runtimeLog(session.client.events.slice(eventIndex), baseUrl),
  };
}

function assertionsFor(expected, observed) {
  const headingPass = observed.visibleHeadings.some(
    (heading) => heading.text === expected.heading,
  );
  const contextPass = Object.entries(expected.context || {}).every(
    ([key, value]) => observed.context[key] === value,
  );
  return [
    { id: 'destination', pass: observed.destination === expected.destination,
      expected: expected.destination, observed: observed.destination },
    { id: 'specialist-tool', pass: !expected.tool || observed.tool === expected.tool,
      expected: expected.tool || null, observed: observed.tool },
    { id: 'visible-heading', pass: headingPass,
      expected: expected.heading, observed: observed.visibleHeadings },
    { id: 'one-destination-root', pass: observed.destinationRootCount === 1,
      expected: 1, observed: observed.destinationRootCount },
    { id: 'direct-route-owner', pass: observed.directOwner,
      expected: pathOnly(expected.path), observed: observed.directOwner },
    { id: 'context-restored', pass: contextPass,
      expected: expected.context || {}, observed: observed.context },
    { id: 'no-legacy-panel-activation', pass: observed.legacyActivePanels.length === 0,
      expected: [], observed: observed.legacyActivePanels },
    { id: 'no-horizontal-overflow', pass: !observed.horizontalOverflow,
      expected: false, observed: observed.horizontalOverflow },
    { id: 'no-console-or-runtime-errors', pass:
      observed.runtime.consoleErrors.length === 0 && observed.runtime.exceptions.length === 0,
      expected: [], observed: { console: observed.runtime.consoleErrors, exceptions: observed.runtime.exceptions } },
    { id: 'no-server-error-responses', pass: observed.runtime.serverErrors.length === 0,
      expected: [], observed: observed.runtime.serverErrors },
    { id: 'no-failed-requests', pass: observed.runtime.failedRequests.length === 0,
      expected: [], observed: observed.runtime.failedRequests },
  ];
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required(args, 'artifacts'));
  const routesFile = path.resolve(args.routes || path.join(__dirname, 'b19_t06_routes.json'));
  const manifest = JSON.parse(fs.readFileSync(routesFile, 'utf8'));
  const baseUrl = required(args, 'url');
  const session = await BrowserSession.open({ url: baseUrl, artifacts });
  const routes = [];
  const aliases = [];
  let unknown;
  let history;
  try {
    await session.waitFor(`document.readyState === 'complete'`);
    for (const definition of manifest.routes) {
      const observed = await navigate(session, definition.path, definition, baseUrl);
      routes.push({ definition, observed, assertions: assertionsFor(definition, observed) });
    }
    for (const alias of manifest.aliases) {
      const canonicalDefinition = manifest.routes.find(
        (item) => pathOnly(item.path) === pathOnly(alias.canonical),
      ) || { path: alias.canonical, destination: pathOnly(alias.canonical).split('/')[0], heading: '' };
      const observed = await navigate(session, alias.path, canonicalDefinition, baseUrl);
      const canonicalPath = pathOnly(alias.canonical);
      aliases.push({ alias, observed, assertions: [
        { id: 'alias-translates-to-canonical-route',
          pass: observed.hash === `#/${alias.canonical}`,
          expected: `#/${alias.canonical}`, observed: observed.hash },
        { id: 'alias-does-not-activate-legacy-panel',
          pass: observed.legacyActivePanels.length === 0,
          expected: [], observed: observed.legacyActivePanels },
      ] });
    }
    const unknownDefinition = { path: 'projects', destination: 'projects', heading: 'Project Home' };
    unknown = await navigate(session, 'definitely-unknown?project=fixture-project', unknownDefinition, baseUrl);

    const cast = manifest.routes.find((item) => pathOnly(item.path) === 'cast');
    const produce = manifest.routes.find((item) => pathOnly(item.path) === 'produce');
    await navigate(session, cast.path, cast, baseUrl);
    await navigate(session, produce.path, produce, baseUrl);
    await session.evaluate('history.back()');
    await session.waitFor(`location.hash.includes('/cast')`);
    await settle(session);
    const back = await snapshot(session, cast);
    await session.evaluate('history.forward()');
    await session.waitFor(`location.hash.includes('/produce')`);
    await settle(session);
    const forward = await snapshot(session, produce);
    history = { back, forward, assertions: [
      { id: 'back-restores-character-context',
        pass: back.context.character === 'character_bernice',
        expected: 'character_bernice', observed: back.context },
      { id: 'forward-restores-chunk-context',
        pass: forward.context.chunk === 'fixture-chunk',
        expected: 'fixture-chunk', observed: forward.context },
      { id: 'history-moves-focus-to-heading',
        pass: Boolean(back.activeId && forward.activeId
          && back.visibleHeadings.some((item) => item.id === back.activeId)
          && forward.visibleHeadings.some((item) => item.id === forward.activeId)),
        expected: 'visible page heading', observed: [back.activeId, forward.activeId] },
    ] };
    await session.screenshot('route-history-forward.png');
  } finally {
    await session.close();
  }
  const assertions = [
    ...routes.flatMap((item) => item.assertions.map((value) => ({ route: item.definition.path, ...value }))),
    ...aliases.flatMap((item) => item.assertions.map((value) => ({ alias: item.alias.path, ...value }))),
    { id: 'unknown-route-falls-back-to-projects', pass: unknown.destination === 'projects'
      && unknown.hash.startsWith('#/projects'), expected: '#/projects', observed: unknown },
    ...history.assertions,
  ];
  const report = { status: assertions.every((item) => item.pass) ? 'PASS' : 'RED', routes, aliases, unknown, history, assertions };
  writeJson(path.join(artifacts, 'report.json'), report);
  process.stdout.write(`B19_T06_ROUTES=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
