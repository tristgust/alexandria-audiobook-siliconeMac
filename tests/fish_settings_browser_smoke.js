'use strict';

const path = require('path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');

const VIEWPORTS = [[1280, 900], [390, 844]];

function route(base, value) {
  const target = new URL(base);
  target.hash = value;
  return target.href;
}

async function snapshot(session) {
  return session.evaluate(`(() => {
    const panel = document.querySelector('[data-fish-setup]');
    const key = document.getElementById('settings-fish-api-key');
    const enabled = document.getElementById('settings-fish-enabled');
    const primary = document.querySelector('[data-settings-fish-primary]');
    const status = panel?.querySelector('[data-status-domain="fish-provider"]');
    const rect = panel?.getBoundingClientRect();
    return {
      hash: location.hash,
      ready: document.body.dataset.shellState === 'ready',
      sectionTitle: document.getElementById('settings-speech-heading')?.textContent?.trim() || null,
      panelTitle: panel?.querySelector('h3')?.textContent?.trim() || null,
      panelState: panel?.dataset.state || null,
      status: status?.textContent?.trim() || null,
      primary: primary?.textContent?.trim() || null,
      keyLabel: key?.closest('.field')?.querySelector('.field__label')?.textContent?.trim() || null,
      keyMode: key?.closest('[data-secret-mode]')?.dataset.secretMode || null,
      keyEnabled: key ? !key.disabled : false,
      activeId: document.activeElement?.id || null,
      fishEnabled: Boolean(enabled?.checked),
      stepCount: panel?.querySelectorAll('.settings-fish-steps > li').length || 0,
      migrationHidden: Boolean(panel?.querySelector('.settings-fish-migration')?.hidden),
      panelRect: rect ? { left: Math.round(rect.left), right: Math.round(rect.right), width: Math.round(rect.width) } : null,
      viewport: { width: innerWidth, height: innerHeight },
      horizontalOverflow: Math.max(0, document.documentElement.scrollWidth - innerWidth),
    };
  })()`);
}

async function runViewport(baseUrl, artifacts, width, height) {
  const viewport = `${width}x${height}`;
  const session = await BrowserSession.open({
    url: route(baseUrl, '/settings?mode=speech'),
    artifacts: path.join(artifacts, viewport),
    width,
    height,
  });
  try {
    await session.waitFor(`document.readyState === 'complete'
      && document.body.dataset.destination === 'settings'
      && document.body.dataset.shellState === 'ready'
      && document.querySelector('[data-route-owner="settings"][data-view-state="ready"]')
      && document.querySelector('[data-fish-setup]')`);
    await session.evaluate(`new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))`);
    const initial = await snapshot(session);
    await session.screenshot('fish-settings-initial.png');

    await session.evaluate(`document.querySelector('[data-settings-fish-primary]')?.click()`);
    await session.waitFor(`document.getElementById('settings-fish-api-key')?.disabled === false`);
    const connectedAction = await snapshot(session);

    await session.evaluate(`(() => {
      const input = document.getElementById('settings-fish-api-key');
      input.value = 'browser-smoke-placeholder-key';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    })()`);
    await session.waitFor(`document.querySelector('[data-settings-fish-primary]')?.textContent.includes('Save Fish setup')`);
    const keyEntered = await snapshot(session);
    await session.screenshot('fish-settings-key-entered.png');

    const errors = session.client.events.filter((event) => (
      event.method === 'Runtime.exceptionThrown'
      || (event.method === 'Log.entryAdded' && event.params?.entry?.level === 'error')
    ));
    const assertions = [
      { id: 'dedicated-speech-provider-section', pass:
        initial.sectionTitle === 'Speech & voice providers'
          && initial.panelTitle === 'Fish Audio S2.1 Pro',
        expected: ['Speech & voice providers', 'Fish Audio S2.1 Pro'],
        observed: [initial.sectionTitle, initial.panelTitle] },
      { id: 'first-run-state-is-obvious', pass:
        initial.panelState === 'not-connected'
          && initial.status?.includes('Not connected')
          && initial.primary === 'Connect Fish Audio',
        expected: ['not-connected', 'Not connected', 'Connect Fish Audio'],
        observed: [initial.panelState, initial.status, initial.primary] },
      { id: 'provider-setup-steps-visible', pass:
        initial.stepCount === 3
          && initial.keyLabel === 'Fish Audio API key'
          && initial.migrationHidden,
        expected: { stepCount: 3, keyLabel: 'Fish Audio API key', migrationHidden: true },
        observed: initial },
      { id: 'connect-action-prepares-key-and-enable-state', pass:
        connectedAction.keyMode === 'replace'
          && connectedAction.keyEnabled
          && connectedAction.activeId === 'settings-fish-api-key'
          && connectedAction.fishEnabled,
        expected: { mode: 'replace', enabled: true, focused: true, fishEnabled: true },
        observed: connectedAction },
      { id: 'entered-key-produces-clear-next-action', pass:
        keyEntered.panelState === 'ready'
          && keyEntered.status?.includes('Ready after save')
          && keyEntered.primary === 'Save Fish setup',
        expected: ['ready', 'Ready after save', 'Save Fish setup'],
        observed: [keyEntered.panelState, keyEntered.status, keyEntered.primary] },
      { id: 'responsive-without-horizontal-overflow', pass:
        initial.horizontalOverflow === 0 && keyEntered.horizontalOverflow === 0,
        expected: 0,
        observed: [initial.horizontalOverflow, keyEntered.horizontalOverflow] },
      { id: 'no-browser-errors', pass: errors.length === 0,
        expected: 0, observed: errors },
    ];
    return { viewport, initial, connectedAction, keyEntered, assertions };
  } finally {
    await session.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const baseUrl = required(args, 'url');
  const artifacts = path.resolve(required(args, 'artifacts'));
  const viewports = [];
  for (const [width, height] of VIEWPORTS) {
    viewports.push(await runViewport(baseUrl, artifacts, width, height));
  }
  const assertions = viewports.flatMap((result) => result.assertions.map((assertion) => ({
    viewport: result.viewport,
    ...assertion,
  })));
  const report = {
    status: assertions.every((assertion) => assertion.pass) ? 'PASS' : 'RED',
    assertions,
    viewports,
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  process.stdout.write(`FISH_SETTINGS_BROWSER=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
