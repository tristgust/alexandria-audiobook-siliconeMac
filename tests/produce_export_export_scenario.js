'use strict';

const {
  requestAfterClick, setMode,
} = require('./produce_export_browser_helpers.js');
const {
  openScenario, scenarioResult,
} = require('./produce_export_scenario_common.js');

async function inspectExport(server, artifacts, width, height) {
  const context = await openScenario(server, artifacts, 'export', width, height);
  const {
    assertions, initial, route, session, viewport,
  } = context;
  try {
    if (!initial.installed) return scenarioResult(session, viewport, assertions, initial);
    Object.assign(assertions, {
      readiness: /Ready to build/i.test(`${initial.text} ${initial.headerText}`),
      boundedWorkflow: width < 641
        || (initial.ownerScrollHeight <= height && initial.ownerClientHeight <= height),
      internalAssemblyScroll: width < 641 || (initial.exportClientHeight > 0
        && initial.exportScrollHeight > initial.exportClientHeight),
      usableAssemblyViewport: width < 641
        || initial.exportClientHeight >= Math.min(320, Math.round(height * .25)),
      currentTakeTruth: initial.finalWaveformDisabled && !/Current Take/i.test(initial.text),
      formatLabels: /M4B audiobook/.test(initial.text)
        && /Separate chapter files/.test(initial.text),
      built: await requestAfterClick(
        session, server.control, '[data-export-primary]', '/api/export/build', false,
      ),
    });
    await setMode(session, server.control, 'export-running', route);
    assertions.cancelled = await requestAfterClick(
      session, server.control, '[data-export-cancel]', '/api/export/cancel', false,
    );
    await session.screenshot('export-running.png');
    return scenarioResult(session, viewport, assertions, initial);
  } finally {
    await session.close();
  }
}

module.exports = { inspectExport };
