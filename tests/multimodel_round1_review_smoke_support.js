const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const REVIEW_ROOT = path.resolve(process.env.MULTIMODEL_REVIEW_ROOT
  || path.join(ROOT, '.omo', 'evidence', 'b17-t05-multimodel-round1', 'review-round1-complete-final'));
const ASSET_ROOT = path.join(ROOT, 'benchmarks', 'multimodel_review_assets');
const EVIDENCE_ROOT = path.resolve(process.env.MULTIMODEL_REVIEW_EVIDENCE_ROOT
  || path.join(ROOT, '.omo', 'evidence', 'b17-t05-multimodel-round1', 'recovery', 'browser-ui-fix'));
const USE_PACKAGED_ASSETS = process.env.MULTIMODEL_REVIEW_USE_PACKAGED_ASSETS === '1';
const ASSET_FILES = new Set([
  'index.html',
  'styles.css',
  'review-core.js',
  'review-content.js',
  'review-navigation.js',
  'review-io.js',
  'app.js',
]);

function contentType(file) {
  return {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.wav': 'audio/wav',
    '.mp3': 'audio/mpeg',
    '.json': 'application/json; charset=utf-8',
  }[path.extname(file).toLowerCase()] || 'application/octet-stream';
}

const server = http.createServer((request, response) => {
  const requested = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
  const relative = requested === '/' ? 'index.html' : requested.replace(/^\/+/, '');
  const root = ASSET_FILES.has(relative) && !USE_PACKAGED_ASSETS ? ASSET_ROOT : REVIEW_ROOT;
  const target = path.resolve(root, relative);
  if (!target.startsWith(root + path.sep) && target !== root) {
    response.writeHead(403).end('Forbidden');
    return;
  }
  fs.readFile(target, (error, bytes) => {
    if (error) {
      response.writeHead(404).end('Not found');
      return;
    }
    response.writeHead(200, { 'Content-Type': contentType(target) });
    response.end(bytes);
  });
});

async function closeServer() {
  if (server.listening) await new Promise((resolve) => server.close(resolve));
}

async function navigate(page, baseUrl, reviewer, session = `${reviewer}-session`) {
  const target = `${baseUrl}/?reviewer=${encodeURIComponent(reviewer)}&session=${encodeURIComponent(session)}`;
  await page.goto(target, { waitUntil: 'domcontentloaded' });
  await page.locator('.sample-card').first().waitFor();
}

async function scoreCard(card) {
  for (const field of ['identity_1_to_5', 'delivery_1_to_5', 'naturalness_1_to_5', 'artifact_severity_1_to_5']) {
    await card.locator(`input[data-field="${field}"][value="4"]`).check();
  }
  for (const field of ['spoken_text_matches_expected', 'requested_mode_is_clear', 'approve_for_comparison']) {
    await card.locator(`input[data-field="${field}"][value="true"]`).check();
  }
}

async function storedState(page, sampleId) {
  return page.evaluate((id) => {
    const query = new URLSearchParams(window.location.search);
    const reviewer = query.get('reviewer');
    const session = query.get('session');
    const belongsToPage = (key) => key.includes(encodeURIComponent(reviewer)) && key.includes(encodeURIComponent(session));
    const keys = Object.keys(localStorage).sort((left, right) => Number(belongsToPage(right)) - Number(belongsToPage(left)));
    for (const key of keys) {
      try {
        const value = JSON.parse(localStorage.getItem(key));
        if (value && typeof value === 'object' && value[id]) return { key, row: value[id] };
      } catch (_) {}
    }
    return null;
  }, sampleId);
}

async function importPayloads(page, payloads) {
  await page.locator('#import-results').setInputFiles(payloads.map(({ name, payload }) => ({
    name,
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(payload)),
  })));
  await page.locator('#import-dialog').waitFor({ state: 'visible' });
}

async function writeEvidence(filename, payload) {
  await fs.promises.mkdir(EVIDENCE_ROOT, { recursive: true });
  await fs.promises.writeFile(path.join(EVIDENCE_ROOT, filename), JSON.stringify(payload, null, 2));
}

async function captureClearDrawer(page) {
  const geometry = await page.evaluate(() => {
    const toolbar = document.querySelector('.toolbar').getBoundingClientRect();
    const panel = document.querySelector('#reference-panel').getBoundingClientRect();
    const close = document.querySelector('#close-reference-drawer').getBoundingClientRect();
    return { toolbarBottom: toolbar.bottom, panelTop: panel.top, closeTop: close.top, closeBottom: close.bottom };
  });
  assert.ok(geometry.panelTop >= geometry.toolbarBottom + 8, JSON.stringify(geometry));
  assert.ok(geometry.closeTop >= geometry.panelTop && geometry.closeBottom <= 900, JSON.stringify(geometry));
  await page.screenshot({ path: path.join(EVIDENCE_ROOT, 'reference-drawer-top-1280.png') });
  await writeEvidence('reference-drawer-top-observable.json', geometry);
}

async function captureRejectedImport(page, card) {
  const observable = {
    checkedControls: await card.locator('input:checked').count(),
    status: await card.locator('.status-pill').innerText(),
    overall: await page.locator('#overall-progress').innerText(),
    importSummary: await page.locator('#import-summary').innerText(),
  };
  assert.deepStrictEqual([observable.checkedControls, observable.status], [0, 'Not reviewed']);
  assert.match(observable.overall, /^0 \/ /);
  assert.match(observable.importSummary, /1 malformed row/i);
  await page.screenshot({ path: path.join(EVIDENCE_ROOT, 'malformed-import-rejected-1280.png') });
  await writeEvidence('malformed-import-rejected-observable.json', observable);
}

async function assertResponsiveLayouts(page) {
  for (const [width, height] of [[1280, 900], [1024, 768], [768, 900], [375, 812]]) {
    await page.setViewportSize({ width, height });
    await page.waitForFunction(() => document.documentElement.style.getPropertyValue('--toolbar-offset') === `${document.querySelector('.toolbar').offsetHeight}px`);
    const dimensions = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    assert.ok(dimensions.scrollWidth <= dimensions.clientWidth + 1, `${width}: ${JSON.stringify(dimensions)}`);
    if (width === 375) assert.ok(await page.locator('.header-progress').isVisible());
    await page.screenshot({ path: path.join(EVIDENCE_ROOT, `review-${width}.png`) });
  }
  await page.setViewportSize({ width: 1024, height: 768 });
  const offsets = await page.evaluate(() => ({
    toolbar: document.querySelector('.toolbar').offsetHeight,
    sidebar: Number.parseFloat(getComputedStyle(document.querySelector('.sidebar')).top),
    identity: Number.parseFloat(getComputedStyle(document.querySelector('.identity-section-header')).top),
  }));
  assert.ok(offsets.toolbar > 61, JSON.stringify(offsets));
  assert.ok(Math.abs(offsets.sidebar - offsets.toolbar) <= 1, JSON.stringify(offsets));
  assert.ok(Math.abs(offsets.identity - offsets.toolbar) <= 1, JSON.stringify(offsets));
}

module.exports = {
  EVIDENCE_ROOT,
  REVIEW_ROOT,
  assert,
  assertResponsiveLayouts,
  captureClearDrawer,
  captureRejectedImport,
  chromium,
  closeServer,
  fs,
  importPayloads,
  navigate,
  path,
  scoreCard,
  server,
  storedState,
  writeEvidence,
};
