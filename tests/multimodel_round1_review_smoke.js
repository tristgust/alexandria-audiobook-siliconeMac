const assert = require('assert');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const REVIEW_ROOT = path.join(ROOT, '.omo', 'evidence', 'b17-t05-multimodel-round1', 'review');
const SCREENSHOT = path.join(ROOT, '.omo', 'evidence', 'b17-t05-multimodel-round1', 'review-smoke.png');

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
  const target = path.resolve(REVIEW_ROOT, relative);
  if (!target.startsWith(REVIEW_ROOT + path.sep) && target !== REVIEW_ROOT) {
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
  if (!server.listening) return;
  await new Promise((resolve) => server.close(resolve));
}

(async () => {
  let browser;
  try {
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const port = server.address().port;
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1536, height: 1024 } });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    const consoleErrors = [];
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    page.on('pageerror', (error) => consoleErrors.push(error.message));

    console.log('step:navigate');
    await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'domcontentloaded' });
    await page.locator('.sample-card').first().waitFor();
    assert.match(await page.title(), /Round 1 Blind Review/);
    assert.strictEqual(await page.locator('#group-navigation .nav-button').count(), 5);
    assert.strictEqual(await page.locator('#style-navigation .nav-button').count(), 8);
    assert.ok((await page.locator('.sample-card').count()) > 0);
    assert.ok((await page.locator('.reference-card').count()) >= 5);
    assert.match(await page.locator('#overall-generated').innerText(), /116 generated/);

    console.log('step:score');
    const firstCard = page.locator('.sample-card').first();
    const sampleId = await firstCard.getAttribute('data-sample-id');
    for (const field of ['identity_1_to_5', 'delivery_1_to_5', 'naturalness_1_to_5', 'artifact_severity_1_to_5']) {
      await firstCard.locator(`input[data-field="${field}"][value="4"]`).check();
    }
    for (const field of ['spoken_text_matches_expected', 'requested_mode_is_clear', 'approve_for_comparison']) {
      await firstCard.locator(`input[data-field="${field}"][value="true"]`).check();
    }
    await firstCard.locator('input[data-field="flag_for_follow_up"]').check();
    await firstCard.locator('textarea[data-field="notes"]').fill('Focused smoke-test note.');
    await page.waitForTimeout(250);
    const stored = await page.evaluate((id) => {
      const key = Object.keys(localStorage).find((item) => item.startsWith('alexandria-round1-review:') && !item.endsWith(':group') && !item.endsWith(':style'));
      return JSON.parse(localStorage.getItem(key))[id];
    }, sampleId);
    assert.strictEqual(stored.identity_1_to_5, 4);
    assert.strictEqual(stored.flag_for_follow_up, true);
    assert.strictEqual(stored.notes, 'Focused smoke-test note.');
    assert.match(await firstCard.locator('.status-pill').innerText(), /Reviewed/);
    assert.match(await page.locator('#followup-count').innerText(), /1 flagged/);

    console.log('step:export');
    const downloadPromise = page.waitForEvent('download', { timeout: 10000 });
    await page.locator('#export-style').click();
    const download = await downloadPromise;
    assert.match(download.suggestedFilename(), /alexandria_round1_style_/);
    await download.cancel();

    console.log('step:navigate-style');
    const firstTitle = await page.locator('#style-title').innerText();
    await page.locator('.style-header').click();
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(100);
    assert.notStrictEqual(await page.locator('#style-title').innerText(), firstTitle);

    console.log('step:compact');
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.waitForTimeout(100);
    const dimensions = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    assert.ok(dimensions.scrollWidth <= dimensions.clientWidth + 1, JSON.stringify(dimensions));
    await page.screenshot({ path: SCREENSHOT, fullPage: true });

    assert.deepStrictEqual(consoleErrors, []);
    console.log(JSON.stringify({
      groupCount: 5,
      baselineStyleCount: 8,
      generatedSamples: 116,
      testedSampleId: sampleId,
      screenshot: SCREENSHOT,
      consoleErrors: 0,
    }, null, 2));
  } finally {
    if (browser) await browser.close().catch(() => {});
    await closeServer();
  }
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
