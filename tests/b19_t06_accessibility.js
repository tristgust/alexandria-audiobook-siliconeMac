'use strict';

const fs = require('fs');
const path = require('path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');

const VIEWPORTS = [[1536, 1024], [1024, 768], [1440, 1000], [390, 844]];
const ROUTES = [
  { name: 'cast', hash: '/cast', destination: 'cast' },
  { name: 'settings', hash: '/settings?mode=accessibility', destination: 'settings' },
  { name: 'maintenance', hash: '/more/maintenance?mode=recovery', destination: 'more' },
];

async function settle(session, destination) {
  await session.waitFor(`document.readyState === 'complete'
    && document.body.dataset.destination === ${JSON.stringify(destination)}`);
  await session.evaluate(`new Promise((resolve) => requestAnimationFrame(
    () => requestAnimationFrame(() => resolve(true))
  ))`);
}

async function tabIntoPage(session) {
  for (let count = 0; count < 8; count += 1) {
    await session.client.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Tab', code: 'Tab' });
    await session.client.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Tab', code: 'Tab' });
  }
}

async function auditDom(session) {
  return session.evaluate(`(() => {
    const visible = (node) => {
      if (!node || node.hidden) return false;
      const style = getComputedStyle(node); const rect = node.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden'
        && rect.width > 0 && rect.height > 0;
    };
    const name = (node) => {
      const labelledBy = (node.getAttribute('aria-labelledby') || '').split(/\s+/)
        .filter(Boolean).map((id) => document.getElementById(id)?.textContent?.trim() || '')
        .filter(Boolean).join(' ');
      const labels = node.labels
        ? [...node.labels].map((label) => label.textContent.trim()).filter(Boolean).join(' ') : '';
      return node.getAttribute('aria-label') || labelledBy || labels
        || node.textContent?.trim() || node.getAttribute('title') || node.getAttribute('alt') || '';
    };
    const controls = [...document.querySelectorAll('button,a[href],input,select,textarea,[tabindex]')]
      .filter(visible).filter((node) => !node.disabled && node.tabIndex >= 0);
    const unlabeled = controls.filter((node) => !name(node))
      .map((node) => node.id || node.outerHTML.slice(0, 160));
    const undersizedText = [...document.querySelectorAll('main *')].filter(visible)
      .filter((node) => node.children.length === 0 && node.textContent.trim())
      .filter((node) => parseFloat(getComputedStyle(node).fontSize) < 13)
      .map((node) => ({ id: node.id || null, text: node.textContent.trim().slice(0, 80),
        size: getComputedStyle(node).fontSize })).slice(0, 50);
    const active = document.activeElement;
    const dialog = [...document.querySelectorAll('[role="dialog"],dialog')].find(visible);
    return {
      mainCount: [...document.querySelectorAll('main')].filter(visible).length,
      navCount: [...document.querySelectorAll('nav')].filter(visible).length,
      visibleH1Count: [...document.querySelectorAll('main h1')].filter(visible).length,
      controlCount: controls.length, unlabeled, undersizedText,
      active: active ? { id: active.id || null, tag: active.tagName,
        name: name(active), visible: visible(active), focusVisible: active.matches(':focus-visible') } : null,
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 1,
      dialog: dialog ? { modal: dialog.getAttribute('aria-modal'), containsFocus: dialog.contains(active) } : null,
      reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
    };
  })()`);
}

function runtimeLog(events, baseUrl) {
  const sameOrigin = new URL(baseUrl).origin;
  const consoleErrors = events.filter((item) => item.method === 'Runtime.consoleAPICalled'
    && item.params?.type === 'error').map((item) => item.params);
  const exceptions = events.filter((item) => item.method === 'Runtime.exceptionThrown')
    .map((item) => item.params?.exceptionDetails || item.params);
  const badResponses = events.filter((item) => item.method === 'Network.responseReceived')
    .map((item) => item.params?.response).filter((response) => response
      && response.url.startsWith(sameOrigin) && response.status >= 400)
    .map((response) => ({ url: response.url, status: response.status }));
  const failedRequests = events.filter((item) => item.method === 'Network.loadingFailed')
    .map((item) => item.params).filter((item) => item?.type !== 'Image');
  return { consoleErrors, exceptions, badResponses, failedRequests };
}

function assertionsFor(capture) {
  const { dom, runtime } = capture;
  return [
    { id: 'one-visible-main', pass: dom.mainCount === 1, expected: 1, observed: dom.mainCount },
    { id: 'navigation-landmark', pass: dom.navCount >= 1, expected: '>=1', observed: dom.navCount },
    { id: 'one-visible-h1', pass: dom.visibleH1Count === 1, expected: 1, observed: dom.visibleH1Count },
    { id: 'controls-have-accessible-names', pass: dom.unlabeled.length === 0, expected: [], observed: dom.unlabeled },
    { id: 'minimum-13px-text', pass: dom.undersizedText.length === 0, expected: [], observed: dom.undersizedText },
    { id: 'keyboard-focus-visible', pass: Boolean(dom.active?.visible && dom.active?.focusVisible && dom.active?.name),
      expected: 'visible named :focus-visible target', observed: dom.active },
    { id: 'dialog-focus-contained', pass: !dom.dialog || (dom.dialog.modal === 'true' && dom.dialog.containsFocus),
      expected: 'modal focus contained', observed: dom.dialog },
    { id: 'no-horizontal-overflow', pass: !dom.horizontalOverflow, expected: false, observed: dom.horizontalOverflow },
    { id: 'no-console-errors', pass: runtime.consoleErrors.length === 0, expected: [], observed: runtime.consoleErrors },
    { id: 'no-runtime-exceptions', pass: runtime.exceptions.length === 0, expected: [], observed: runtime.exceptions },
    { id: 'no-failed-application-responses', pass: runtime.badResponses.length === 0,
      expected: [], observed: runtime.badResponses },
    { id: 'no-failed-application-requests', pass: runtime.failedRequests.length === 0,
      expected: [], observed: runtime.failedRequests },
  ];
}

async function captureUrl(baseUrl, artifacts) {
  const captures = [];
  for (const [width, height] of VIEWPORTS) {
    const viewport = `${width}x${height}`;
    const session = await BrowserSession.open({
      url: baseUrl, artifacts: path.join(artifacts, viewport), width, height,
    });
    try {
      for (const route of ROUTES) {
        const eventIndex = session.client.events.length;
        await session.evaluate(`location.hash = ${JSON.stringify(route.hash)}`);
        await settle(session, route.destination);
        await tabIntoPage(session);
        const capture = { viewport, route, dom: await auditDom(session),
          runtime: runtimeLog(session.client.events.slice(eventIndex), baseUrl) };
        capture.assertions = assertionsFor(capture);
        await session.screenshot(`${route.name}-focus.png`);
        captures.push(capture);
      }
    } finally {
      await session.close();
    }
  }
  return captures;
}

function capturesFromManifest(manifestFile) {
  const manifest = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
  if (!Array.isArray(manifest.accessibilityCaptures)) {
    throw new Error('manifest.accessibilityCaptures must be an array');
  }
  return manifest.accessibilityCaptures.map((capture) => ({
    ...capture, assertions: assertionsFor(capture),
  }));
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required(args, 'artifacts'));
  const captures = args.url
    ? await captureUrl(String(args.url), artifacts)
    : capturesFromManifest(path.resolve(required(args, 'manifest')));
  const assertions = captures.flatMap((capture) => capture.assertions.map((assertion) => ({
    viewport: capture.viewport, route: capture.route.name, ...assertion,
  })));
  const report = { status: assertions.every((item) => item.pass) ? 'PASS' : 'RED', captures, assertions };
  writeJson(path.join(artifacts, 'report.json'), report);
  process.stdout.write(`B19_T06_ACCESSIBILITY=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
