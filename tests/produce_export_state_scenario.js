'use strict';

const fs = require('fs');
const path = require('path');
const { BrowserSession } = require('./b19_t06_bootstrap_red.js');
const {
  runtimeErrors, setMode, snapshot, wait,
} = require('./produce_export_browser_helpers.js');

async function inspectStates(server, artifacts, width, height) {
  const viewport = `${width}x${height}`;
  const folder = path.join(artifacts, viewport);
  fs.mkdirSync(folder, { recursive: true });
  const session = await BrowserSession.open({
    url: `${server.url}#/projects`, artifacts: folder, width, height,
  });
  session.baseUrl = server.url;
  const captures = [];
  const assertions = {};
  try {
    const states = {
      produce: ['loading', 'empty', 'error', 'blocked', 'running', 'dense'],
      export: ['loading', 'empty', 'error', 'blocked', 'ready', 'dense', 'complete'],
    };
    for (const [owner, modes] of Object.entries(states)) {
      for (const mode of modes) {
        const route = `${owner}?project=fixture-project${owner === 'produce' ? '&chunk=stale-1' : ''}`;
        await setMode(session, server.control, `${owner}-${mode}`, route, mode === 'loading');
        if (!await session.evaluate(`Boolean(document.querySelector('[data-${owner}-page]'))`)) {
          const state = await snapshot(session, owner);
          captures.push({ owner, mode, state });
          assertions[`${owner}-${mode}`] = false;
          return { viewport, status: 'FAIL', assertions, captures, runtimeErrors: runtimeErrors(session) };
        }
        if (mode === 'loading') {
          await session.screenshot(`${owner}-${mode}.png`);
          server.release();
          await session.waitFor(`document.body.dataset.shellState === 'ready'`);
        }
        const state = await snapshot(session, owner);
        captures.push({ owner, mode, state });
        if (mode !== 'loading') await session.screenshot(`${owner}-${mode}.png`);
        assertions[`${owner}-${mode}`] = state.owner && state.overflow <= 1
          && !state.injection && state.named;
        if (owner === 'export' && mode === 'ready') {
          assertions['export-publication-cover'] = state.publicationCover;
        }
        if (owner === 'produce' && mode === 'running') {
          assertions['produce-composite-progress'] = await session.evaluate(`(() => {
            const bars=document.querySelectorAll('.produce-progress-banner [role="progressbar"]');
            const composite=document.querySelector('[data-produce-composite-progress]');
            const copy=document.querySelector('.produce-progress-banner__copy')?.textContent || '';
            return bars.length===1
              && composite?.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')==='53.125'
              && copy.includes('8 of 16 terminal')
              && copy.includes('6 generated')
              && copy.includes('1 failed')
              && copy.includes('1 cancelled')
              && copy.includes('current file 50%');
          })()`);
        }
        if (owner === 'export' && mode === 'complete') {
          assertions['export-download-current-output'] = state.downloadAction;
        }
      }
    }
    server.control.mode = 'produce-loading';
    server.control.pending.length = 0;
    await session.client.send('Page.navigate', { url: `${server.url}#/produce?project=fixture-project` });
    await session.waitFor(`Boolean(document.querySelector('[data-page-state="loading"]'))`);
    const beforeAbort = server.control.aborted;
    await session.evaluate(`location.hash='#/projects'`);
    await session.waitFor(`document.body.dataset.destination==='projects'`);
    const deadline = Date.now() + 3000;
    while (server.control.aborted === beforeAbort && Date.now() < deadline) await wait(25);
    assertions.routeAbort = server.control.aborted > beforeAbort;
    assertions.noRuntimeErrors = runtimeErrors(session).length === 0;
    assertions.focusRestored = await session.evaluate(
      `document.activeElement?.matches('[data-page-heading]')`,
    );
    server.release();
    return {
      viewport,
      status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL',
      assertions,
      captures,
      runtimeErrors: runtimeErrors(session),
    };
  } finally {
    await session.close();
  }
}

module.exports = { inspectStates };
