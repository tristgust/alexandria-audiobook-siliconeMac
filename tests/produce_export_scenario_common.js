'use strict';

const fs = require('fs');
const path = require('path');
const { BrowserSession } = require('./b19_t06_bootstrap_red.js');
const {
  runtimeErrors, snapshot, waitForVisualReady,
} = require('./produce_export_browser_helpers.js');

const producePageSize = (width) => width < 640 ? 30 : width < 1200 ? 75 : 120;

async function openScenario(server, artifacts, scenario, width, height) {
  const viewport = `${width}x${height}`;
  const folder = path.join(artifacts, viewport);
  fs.mkdirSync(folder, { recursive: true });
  server.control.mode = scenario === 'export' ? 'export-ready' : 'produce-mixed';
  const route = scenario === 'export' ? 'export' : 'produce?chunk=stale-1';
  const session = await BrowserSession.open({
    url: `${server.url}#/${route}`, artifacts: folder, width, height,
  });
  session.baseUrl = server.url;
  await session.waitFor(`document.body.dataset.shellState === 'ready'`);
  const visualReady = await waitForVisualReady(session);
  const owner = scenario === 'export' ? 'export' : 'produce';
  const initial = await snapshot(session, owner);
  await session.screenshot(`${scenario}-ready.png`);
  const assertions = {
    directOwner: initial.installed,
    noOverflow: initial.overflow <= 1,
    titleFocused: initial.focus,
    safeDom: !initial.injection,
    namedControls: initial.named,
    textFloor: initial.minText >= 13,
    noDuplicateTransport: initial.persistentInside === 0,
    noRuntimeErrors: runtimeErrors(session).length === 0,
    visualAssetsReady: visualReady.rendered && visualReady.fontStatus === 'loaded',
    resolvedProject: initial.projectTitle === 'The Meridian Archive'
      && initial.navProjectTitle === 'The Meridian Archive',
    completeProjectShell: initial.projectGroupVisible && !initial.projectContextVisible,
    projectContextRoute: /project=fixture-project/.test(initial.projectHref),
  };
  return { assertions, folder, initial, owner, route, session, viewport, visualReady };
}

function scenarioResult(session, viewport, assertions, initial, actionTargets = null) {
  return {
    viewport,
    status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL',
    assertions,
    initial,
    actionTargets,
    runtimeErrors: runtimeErrors(session),
  };
}

module.exports = { openScenario, producePageSize, scenarioResult };
