'use strict';

const path = require('path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');

const VIEWPORTS = [[1536, 1024], [1024, 768], [1440, 1000], [390, 844]];

function route(base, value) {
  const target = new URL(base);
  target.hash = value;
  return target.href;
}

async function settleLayout(session) {
  await session.evaluate(`new Promise((resolve) => requestAnimationFrame(
    () => requestAnimationFrame(() => resolve(true))
  ))`);
}

async function settingsSnapshot(session, headingId) {
  return session.evaluate(`(() => {
    const heading = document.getElementById(${JSON.stringify(headingId)});
    const header = document.querySelector('[data-global-header],#canonical-global-header,header');
    const root = document.querySelector('#canonical-destination-root,[data-canonical-destination-root],main');
    const rect = (node) => {
      if (!node) return null;
      const value = node.getBoundingClientRect();
      return { top: Math.round(value.top), left: Math.round(value.left),
        bottom: Math.round(value.bottom), width: Math.round(value.width) };
    };
    return { hash: location.hash, heading: heading?.textContent?.trim() || null,
      headingId: heading?.id || null, headingRect: rect(heading), headerRect: rect(header),
      rootRect: rect(root), activeId: document.activeElement?.id || null,
      activeText: document.activeElement?.textContent?.trim().slice(0, 120) || null,
      scrollY: Math.round(scrollY),
      destination: document.body.dataset.destination || null };
  })()`);
}

async function navigateSection(session, key, headingId) {
  const result = await session.evaluate(`(() => {
    const link = document.querySelector('[data-settings-section-link="${key}"]');
    if (link) link.click();
    else location.hash = '/settings?mode=${key}';
    return { linkExists: Boolean(link) };
  })()`);
  await session.waitFor(`location.hash.includes('mode=${key}')`);
  await settleLayout(session);
  return { ...result, snapshot: await settingsSnapshot(session, headingId) };
}

async function historyMove(session, direction, expectedMode, headingId) {
  await session.evaluate(`history.${direction}()`);
  await session.waitFor(`location.hash.includes('mode=${expectedMode}')`);
  await settleLayout(session);
  return settingsSnapshot(session, headingId);
}

async function reloadSection(session, expectedMode, headingId) {
  session.client.events = [];
  await session.client.send('Page.reload', { ignoreCache: true });
  await session.client.event('Page.loadEventFired');
  await session.waitFor(`document.body.dataset.destination === 'settings'
    && location.hash.includes('mode=${expectedMode}')`);
  await settleLayout(session);
  return settingsSnapshot(session, headingId);
}

async function runViewport(baseUrl, artifacts, width, height) {
  const viewportName = `${width}x${height}`;
  const viewportArtifacts = path.join(artifacts, viewportName);
  const session = await BrowserSession.open({
    url: route(baseUrl, '/settings?mode=preferences'),
    artifacts: viewportArtifacts, width, height,
  });
  try {
    await session.waitFor(`document.readyState === 'complete' && document.body.dataset.destination === 'settings'`);
    const provider = await navigateSection(session, 'provider', 'settings-provider-heading');
    const accessibility = await navigateSection(
      session, 'accessibility', 'settings-accessibility-heading',
    );
    const reload = await reloadSection(
      session, 'accessibility', 'settings-accessibility-heading',
    );
    const back = await historyMove(session, 'back', 'provider', 'settings-provider-heading');
    const forward = await historyMove(
      session, 'forward', 'accessibility', 'settings-accessibility-heading',
    );
    const maintenanceLink = await session.evaluate(`(() => {
      const link = document.querySelector('[data-settings-maintenance-link]');
      if (link) link.click();
      else location.hash = '/more/maintenance?mode=recovery&return=settings';
      return Boolean(link);
    })()`);
    await session.waitFor(`location.hash.includes('more/maintenance') || (
      document.body.dataset.destination === 'more'
      && document.body.textContent.includes('Maintenance')
    )`);
    await settleLayout(session);
    const maintenance = await settingsSnapshot(session, 'maintenance-page-heading');
    await session.screenshot('settings-maintenance-history.png');

    const geometryPass = [provider.snapshot, accessibility.snapshot, reload, back, forward]
      .every((item) => item.headingRect && item.headerRect && item.rootRect
        && item.headingRect.top >= item.headerRect.bottom + 20
        && item.headingRect.top <= item.headerRect.bottom + 64
        && item.headingRect.left >= item.rootRect.left);
    const focusPass = provider.snapshot.activeId === 'settings-provider-heading'
      && accessibility.snapshot.activeId === 'settings-accessibility-heading'
      && reload.activeId === 'settings-accessibility-heading'
      && back.activeId === 'settings-provider-heading'
      && forward.activeId === 'settings-accessibility-heading';
    const assertions = [
      { id: 'section-links-exist', pass: provider.linkExists && accessibility.linkExists,
        expected: true, observed: { provider: provider.linkExists, accessibility: accessibility.linkExists } },
      { id: 'headings-at-visible-workspace-inset', pass: geometryPass,
        expected: 'header bottom + 20..64px',
        observed: [provider.snapshot, accessibility.snapshot, reload, back, forward] },
      { id: 'section-navigation-moves-focus', pass: focusPass, expected: true,
        observed: [provider.snapshot.activeId, accessibility.snapshot.activeId,
          reload.activeId, back.activeId, forward.activeId] },
      { id: 'reload-restores-deep-linked-section', pass:
        reload.hash.includes('mode=accessibility')
          && reload.headingId === 'settings-accessibility-heading',
        expected: 'accessibility section and heading', observed: reload },
      { id: 'back-forward-restores-exact-section', pass:
        back.hash.includes('mode=provider') && forward.hash.includes('mode=accessibility'),
        expected: ['provider', 'accessibility'], observed: [back.hash, forward.hash] },
      { id: 'settings-maintenance-link-exists', pass: maintenanceLink,
        expected: true, observed: maintenanceLink },
      { id: 'maintenance-context-visible', pass:
        maintenance.heading === 'Maintenance' && maintenance.headingRect?.top >= maintenance.headerRect?.bottom + 20,
        expected: 'visible Maintenance heading below header', observed: maintenance },
    ];
    return { viewport: viewportName, assertions, provider, accessibility,
      reload, back, forward, maintenance };
  } finally {
    await session.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required(args, 'artifacts'));
  const baseUrl = required(args, 'url');
  const viewports = [];
  for (const [width, height] of VIEWPORTS) {
    viewports.push(await runViewport(baseUrl, artifacts, width, height));
  }
  const assertions = viewports.flatMap((item) => item.assertions.map((assertion) => ({
    viewport: item.viewport, ...assertion,
  })));
  const report = { status: assertions.every((item) => item.pass) ? 'PASS' : 'RED', viewports, assertions };
  writeJson(path.join(artifacts, 'report.json'), report);
  process.stdout.write(`B19_T06_SETTINGS=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
