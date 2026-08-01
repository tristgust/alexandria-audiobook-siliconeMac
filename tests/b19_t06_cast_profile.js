'use strict';

const fs = require('fs');
const path = require('path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');
const { runtimeErrors, snapshot } = require('./cast_profile_browser_helpers.js');
const { fixtureServer } = require('./cast_profile_fixture_server.js');
const { runFullCastScenario } = require('./cast_profile_scenario_full_cast.js');
const { runRosterCatalogScenario } = require('./cast_profile_scenario_roster_catalog.js');
const { runStateMatrixScenario } = require('./cast_profile_scenario_states.js');
const { runVoiceDesignerScenario } = require('./cast_profile_scenario_voice_designer.js');
const { runVoiceEditingScenario } = require('./cast_profile_scenario_voice_editing.js');
const { inspectSources } = require('./cast_profile_source_checks.js');

const VIEWPORTS = [[1536, 1024], [1440, 1000], [1024, 768], [768, 900], [390, 844]];
const json = (value) => JSON.stringify(value);

function resetViewportState(control) {
  control.mode = 'normal';
  control.visual = 'idle';
  control.selected = 'cast:clara';
  control.savedVoice = 'Avery';
  control.savedConfig = null;
  control.designedRollbacks = 0;
  control.requests.length = 0;
  control.libraryAssignments = {};
  control.voiceAssignments = 0;
  control.taskImported = false;
  control.rosterDraftApplied = false;
  control.rosterApproved = false;
  control.enrichmentStarted = false;
  control.enrichmentReads = 0;
  control.approvedRosterAvailable = false;
  control.rosterDiscoveryStarted = false;
}

function initialAssertions(initial, width) {
  return {
    directCastOwner: initial.page,
    oneRosterOneProfile: initial.roster === 1 && initial.profile === 1 && initial.listboxes === 1,
    profileOrder: json(initial.sections) === json(['voice', 'reference', 'preview', 'character', 'appearance', 'advanced']),
    identityFirst: initial.identityBefore,
    noOverflow: initial.overflow <= 1,
    noClippedContent: initial.clipped.length === 0 && initial.controlsOutside.length === 0,
    responsiveWorkspace: width > 900 || width < 640
      ? true
      : initial.workspaceColumns.split(' ').filter(Boolean).length === 1
        && initial.profileWidth >= 480,
    titleFocused: initial.focused,
    safeDom: !initial.injection && initial.unsafeLiteral,
    cloneMethodSemantics: initial.profileText.includes('Clone source')
      && !initial.profileText.includes('Assigned Voice'),
    textFloor: initial.minText >= 13,
    noRuntimeErrors: true,
  };
}

async function waitForCastReady(session) {
  try {
    await session.waitFor(`document.body.dataset.shellState==='ready'&&document.querySelector('[data-cast-page]')?.dataset.castState==='ready'`, 12000);
  } catch (error) {
    const diagnostic = await session.evaluate(`(() => ({
      shellState: document.body.dataset.shellState || '',
      route: document.body.dataset.routePath || '',
      castState: document.querySelector('[data-cast-page]')?.dataset.castState || '',
      text: document.body.innerText.slice(0, 4000),
    }))()`);
    console.error(`CAST_READY_DIAGNOSTIC=${JSON.stringify({ diagnostic, errors: runtimeErrors(session) })}`);
    throw error;
  }
  await session.waitFor(`document.activeElement?.matches('[data-page-heading]')`);
}

async function inspectViewport(server, artifacts, width, height, interactive) {
  resetViewportState(server.control);
  const folder = path.join(artifacts, `${width}x${height}`);
  const session = await BrowserSession.open({
    url: `${server.url}#/cast?project=fixture-project&character=cast%3Aclara`,
    artifacts: folder, width, height,
  });
  const details = {};
  try {
    await waitForCastReady(session);
    const initial = await snapshot(session);
    if (width <= 640) {
      await session.evaluate(`document.querySelector('[data-cast-page]').scrollIntoView({block:'start'})`);
    }
    await session.screenshot('cast-ready.png');
    const assertions = initialAssertions(initial, width);
    const context = { assertions, details, height, server, session, width };
    await runVoiceDesignerScenario(context);
    if (interactive) {
      await runFullCastScenario(context);
      await runRosterCatalogScenario(context);
      await runVoiceEditingScenario(context);
      await runStateMatrixScenario(context);
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
  const selectedViewports = args['desktop-only']
    ? VIEWPORTS.filter(([width]) => width >= 1024)
    : VIEWPORTS;
  const server = await fixtureServer(repoRoot);
  const results = [];
  try {
    for (const [index, [width, height]] of selectedViewports.entries()) {
      results.push(await inspectViewport(server, artifacts, width, height, index === 0));
    }
  } finally {
    server.release();
    await server.close();
  }
  const report = {
    status: source.status === 'PASS' && results.every((result) => result.status === 'PASS')
      ? 'PASS' : 'FAIL',
    source, viewports: selectedViewports, results,
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
