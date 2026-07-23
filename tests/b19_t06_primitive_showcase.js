'use strict';

const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const path = require('path');
const {
  BrowserSession,
  argsFrom,
  required,
  writeJson,
} = require('./b19_t06_bootstrap_red.js');

const ROOT = path.resolve(__dirname, '..');
const STATIC_ROOT = path.join(ROOT, 'app', 'static');
const SHOWCASE = path.join(STATIC_ROOT, 'primitive_showcase.html');
const VIEWPORTS = [[1536, 1024], [1440, 1000], [1024, 768], [390, 844]];
const REQUIRED_PRIMITIVES = [
  'app-shell', 'nav-rail', 'global-header', 'project-header', 'stage-tracker',
  'page-title', 'button', 'icon-button', 'field', 'textarea', 'select',
  'checkbox', 'radio-group', 'toggle', 'segmented-control', 'filter-chip',
  'search-field', 'flat-section', 'divider-list', 'listbox', 'master-detail',
  'portrait', 'monogram', 'source-cover', 'status', 'notice', 'progress',
  'popover', 'modal', 'drawer', 'skeleton', 'empty-state', 'inline-save',
  'disclosure', 'compact-play', 'waveform', 'persistent-player',
];

function mime(filename) {
  return ({ '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript' })[
    path.extname(filename)
  ] || 'application/octet-stream';
}

function startServer() {
  const server = http.createServer((request, response) => {
    const relative = decodeURIComponent(new URL(request.url, 'http://localhost').pathname)
      .replace(/^\/+/, '');
    const filename = path.resolve(STATIC_ROOT, relative || 'primitive_showcase.html');
    if (!filename.startsWith(`${STATIC_ROOT}${path.sep}`) || !fs.existsSync(filename)) {
      response.writeHead(404).end('Not found');
      return;
    }
    response.writeHead(200, { 'Content-Type': `${mime(filename)}; charset=utf-8` });
    fs.createReadStream(filename).pipe(response);
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function sourceDigest() {
  const files = [
    'primitive_showcase.html', 'styles/tokens.css', 'styles/shell.css',
    'styles/components.css', 'components/button.js', 'components/icon_button.js',
    'components/form_controls.js', 'components/status.js', 'components/notice.js',
    'components/disclosure.js', 'components/dialog.js', 'components/transport.js',
  ];
  const hash = crypto.createHash('sha256');
  files.forEach((name) => hash.update(fs.readFileSync(path.join(STATIC_ROOT, name))));
  return hash.digest('hex');
}

async function press(session, key, modifiers = 0) {
  await session.client.send('Input.dispatchKeyEvent', { type: 'keyDown', key, modifiers });
  await session.client.send('Input.dispatchKeyEvent', { type: 'keyUp', key, modifiers });
}

async function interactionProbe(session) {
  await session.evaluate(`document.querySelector('[data-test="disclosure-trigger"]').focus()`);
  await press(session, 'Enter');
  await session.screenshot('disclosure-expanded.png');
  const disclosureExpanded = await session.evaluate(
    `document.querySelector('[data-test="disclosure-trigger"]').getAttribute('aria-expanded') === 'true'`,
  );
  await session.evaluate(`document.querySelector('[data-test="modal-opener"]').click()`);
  await session.screenshot('modal-open.png');
  const modalContained = await session.evaluate(`(() => { const node = document.querySelector('[data-kind="modal"] .dialog-surface');
    const rect = node.getBoundingClientRect(); return node.scrollWidth <= node.clientWidth && rect.left >= 0 && rect.right <= innerWidth; })()`);
  await press(session, 'Tab');
  const modalTrapped = await session.evaluate(
    `document.querySelector('[role="dialog"][data-kind="modal"]')?.contains(document.activeElement)`,
  );
  await press(session, 'Escape');
  const modalRestored = await session.evaluate(
    `document.activeElement === document.querySelector('[data-test="modal-opener"]')`,
  );
  await session.evaluate(`document.querySelector('[data-test="drawer-opener"]').click()`);
  await session.screenshot('drawer-open.png');
  const drawerContained = await session.evaluate(`(() => { const node = document.querySelector('[data-kind="drawer"] .dialog-surface');
    const rect = node.getBoundingClientRect(); return node.scrollWidth <= node.clientWidth && rect.left >= 0 && rect.right <= innerWidth; })()`);
  await press(session, 'Escape');
  const drawerRestored = await session.evaluate(
    `document.activeElement === document.querySelector('[data-test="drawer-opener"]')`,
  );
  await session.evaluate(`document.querySelector('[data-test="waveform-slider"]').focus()`);
  const waveformBefore = await session.evaluate(
    `Number(document.querySelector('[data-test="waveform-slider"]').getAttribute('aria-valuenow'))`,
  );
  await press(session, 'ArrowRight');
  const waveformAfter = await session.evaluate(
    `Number(document.querySelector('[data-test="waveform-slider"]').getAttribute('aria-valuenow'))`,
  );
  await session.evaluate(`document.querySelector('.segmented-control [role="radio"][tabindex="0"]').focus()`);
  await press(session, 'ArrowRight');
  const segmentedKeyboardStep = await session.evaluate(
    `document.activeElement?.getAttribute('role') === 'radio' && document.activeElement?.getAttribute('aria-checked') === 'true'`,
  );
  await session.evaluate(`document.querySelector('[data-primitive="listbox"] [aria-selected="true"]').focus()`);
  await press(session, 'ArrowDown');
  const listboxKeyboardStep = await session.evaluate(
    `document.activeElement?.getAttribute('role') === 'option' && document.activeElement?.getAttribute('aria-selected') === 'true'`,
  );
  await session.evaluate(`document.querySelector('[data-primitive="popover"] [role="menuitem"]').focus()`);
  await press(session, 'ArrowDown');
  const popoverKeyboardStep = await session.evaluate(
    `document.activeElement?.getAttribute('role') === 'menuitem' && document.activeElement?.tabIndex === 0`,
  );
  return {
    disclosureExpanded, modalTrapped, modalRestored, modalContained, drawerRestored, drawerContained,
    waveformKeyboardStep: waveformAfter > waveformBefore,
    segmentedKeyboardStep, listboxKeyboardStep, popoverKeyboardStep,
  };
}

async function inspectViewport(baseUrl, artifacts, width, height) {
  const key = `${width}x${height}`;
  const viewportArtifacts = path.join(artifacts, key);
  const session = await BrowserSession.open({
    url: `${baseUrl}/primitive_showcase.html`, artifacts: viewportArtifacts, width, height,
  });
  try {
    await session.waitFor(`document.documentElement.dataset.showcaseReady === 'true'`);
    await session.client.send('Log.enable');
    const metrics = await session.evaluate(`(() => {
      const shown = (node) => { const r = node.getBoundingClientRect(); const s = getComputedStyle(node);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
      const textNodes = [...document.querySelectorAll('body *')].filter((node) => shown(node)
        && [...node.childNodes].some((child) => child.nodeType === Node.TEXT_NODE && child.textContent.trim()));
      const targets = [...document.querySelectorAll('button,a[href],input,select,textarea,[tabindex]:not([tabindex="-1"])')]
        .filter(shown).map((node) => { const r = node.getBoundingClientRect(); return Math.min(r.width, r.height); });
      const widths = [...document.querySelectorAll('[data-width-group]')].reduce((all, node) => {
        (all[node.dataset.widthGroup] ||= []).push(node.getBoundingClientRect().width); return all;
      }, {});
      return {
        primitives: [...new Set([...document.querySelectorAll('[data-primitive]')].map((node) => node.dataset.primitive))],
        viewportWidthPx: document.documentElement.clientWidth,
        viewportHeightPx: document.documentElement.clientHeight,
        contentHeightPx: document.documentElement.scrollHeight,
        minTextPx: Math.min(...textNodes.map((node) => parseFloat(getComputedStyle(node).fontSize))),
        minTargetPx: Math.min(...targets),
        horizontalOverflowPx: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
        stableWidth: Object.values(widths).every((values) => Math.max(...values) - Math.min(...values) <= 1),
        invalidFieldLinked: document.querySelector('[aria-invalid="true"]')?.getAttribute('aria-describedby') === 'project-name-error',
        secretModes: [...document.querySelectorAll('[data-secret-mode]')].map((node) => node.dataset.secretMode),
        progressNumeric: document.querySelector('[role="progressbar"]')?.hasAttribute('aria-valuenow'),
        waveformAlternative: Boolean(document.querySelector('[data-test="waveform-output"]')?.textContent.trim()),
      };
    })()`);
    const interaction = await interactionProbe(session);
    await session.client.send('Emulation.setEmulatedMedia', {
      features: [{ name: 'prefers-reduced-motion', value: 'reduce' }],
    });
    const reducedMotion = await session.evaluate(`(() => { const s = getComputedStyle(document.querySelector('[data-motion-probe]'));
      return s.transitionDuration === '0s' && s.animationDuration === '0s'; })()`);
    await session.evaluate('scrollTo(0, 0)');
    await session.screenshot('viewport.png');
    const viewportScreenshot = path.join(viewportArtifacts, 'viewport.png');
    const capture = await session.client.send('Page.captureScreenshot', {
      format: 'png', fromSurface: true, captureBeyondViewport: true,
    });
    const fullPageScreenshot = path.join(viewportArtifacts, 'showcase.png');
    fs.writeFileSync(fullPageScreenshot, Buffer.from(capture.data, 'base64'));
    const errors = session.client.events.filter((event) => (
      event.method === 'Runtime.exceptionThrown'
      || (event.method === 'Runtime.consoleAPICalled' && event.params.type === 'error')
      || (event.method === 'Log.entryAdded' && event.params.entry?.level === 'error')
    ));
    const missing = REQUIRED_PRIMITIVES.filter((name) => !metrics.primitives.includes(name));
    const assertions = {
      allPrimitives: missing.length === 0, noConsoleErrors: errors.length === 0,
      noOverflow: metrics.horizontalOverflowPx === 0, textFloor: metrics.minTextPx >= 13,
      targetFloor: metrics.minTargetPx >= 24, stableWidth: metrics.stableWidth,
      invalidFieldLinked: metrics.invalidFieldLinked,
      secretModes: ['preserve', 'replace', 'clear'].every((mode) => metrics.secretModes.includes(mode)),
      progressNumeric: metrics.progressNumeric, waveformAlternative: metrics.waveformAlternative,
      reducedMotion, ...interaction,
    };
    return { key, width, height, status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL',
      assertions, missing, metrics, errorCount: errors.length,
      screenshot: viewportScreenshot, fullPageScreenshot };
  } finally {
    await session.close();
  }
}

async function main() {
  const artifacts = path.resolve(required(argsFrom(process.argv.slice(2)), 'artifacts'));
  fs.mkdirSync(artifacts, { recursive: true });
  if (!fs.existsSync(SHOWCASE)) {
    const report = { status: 'RED', reason: 'primitive showcase is absent', showcase: SHOWCASE };
    writeJson(path.join(artifacts, 'report.json'), report);
    fs.writeFileSync(path.join(artifacts, 'action.log'), 'RED: production showcase not found\n');
    process.stdout.write(`B19_T06_PRIMITIVES=${JSON.stringify(report)}\n`);
    process.exitCode = 1;
    return;
  }
  const server = await startServer();
  const port = server.address().port;
  const results = [];
  try {
    for (const [width, height] of VIEWPORTS) {
      results.push(await inspectViewport(`http://127.0.0.1:${port}`, artifacts, width, height));
    }
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
  const report = { status: results.every((item) => item.status === 'PASS') ? 'PASS' : 'FAIL',
    sourceSha256: sourceDigest(), results };
  writeJson(path.join(artifacts, 'report.json'), report);
  writeJson(path.join(artifacts, 'cleanup.json'), { serverClosed: !server.listening, port });
  fs.writeFileSync(path.join(artifacts, 'action.log'), results.map((item) => (
    `${item.key} ${item.status} ${JSON.stringify(item.assertions)}`
  )).join('\n') + '\n');
  process.stdout.write(`B19_T06_PRIMITIVES=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
