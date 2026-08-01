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
    const initialM4bSelected = await session.evaluate(`
      document.querySelector('input[name="export-format"][value="m4b"]')?.checked === true
    `);
    Object.assign(assertions, {
      readiness: /Ready to build/i.test(`${initial.text} ${initial.headerText}`),
      m4bSelectionIgnoresPriorMp3Receipt: initialM4bSelected,
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
    const buildRequest = server.control.requests
      .filter((request) => request.path === '/api/export/build').at(-1)?.body || null;
    assertions.m4bBuildRequestPreserved = Array.isArray(buildRequest?.formats)
      && buildRequest.formats.length === 1 && buildRequest.formats[0] === 'm4b';
    await setMode(session, server.control, 'export-running', route);
    assertions.m4bSelectionSurvivesPolling = await session.evaluate(`
      document.querySelector('input[name="export-format"][value="m4b"]')?.checked === true
        && document.querySelector('input[name="export-format"][value="mp3"]')?.checked === false
    `);
    const runningStatus = await session.evaluate(`(() => {
      const root = document.querySelector('[data-export-page]');
      const progress = root?.querySelector('.export-progress');
      return {
        finishLines: root?.querySelectorAll('.export-finish-line').length || 0,
        validationInReadiness: root?.querySelectorAll('.export-readiness .export-validation-panel').length || 0,
        notices: root?.querySelectorAll('.export-readiness > .notice').length || 0,
        heading: root?.querySelector('.export-finish-line h2')?.textContent || '',
        progressText: progress?.textContent || '',
        progressValue: progress?.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow') || '',
      };
    })()`);
    assertions.runningProgressIsReal = runningStatus.finishLines === 1
      && runningStatus.validationInReadiness === 0
      && runningStatus.notices === 0
      && /Building M4B audiobook/.test(runningStatus.heading)
      && /Loading production audio/.test(runningStatus.progressText)
      && /5,059 of 5,328/.test(runningStatus.progressText)
      && runningStatus.progressValue === '48';
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
