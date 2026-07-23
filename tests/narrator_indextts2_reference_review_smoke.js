const assert = require('assert');
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const { chromium } = require('playwright');

const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_INDEXTTS2_REFERENCE_REVIEW_ROOT
    || '/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-78fc5814/.omo/evidence/b17-t12-narrator-indextts2-reference-bank/review',
);
const EVIDENCE_ROOT = path.dirname(REVIEW_ROOT);
const SCREENSHOT = path.join(EVIDENCE_ROOT, 'review-smoke.png');
const manifest = JSON.parse(fs.readFileSync(path.join(REVIEW_ROOT, 'manifest.json'), 'utf8'));

function contentType(file) {
  return {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.wav': 'audio/wav',
    '.json': 'application/json; charset=utf-8',
    '.txt': 'text/plain; charset=utf-8',
  }[path.extname(file).toLowerCase()] || 'application/octet-stream';
}

const server = http.createServer((request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
  const relative = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
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

(async () => {
  let browser;
  try {
    assert.strictEqual(manifest.candidate_count, 6);
    assert.strictEqual(fs.existsSync(path.join(REVIEW_ROOT, 'answer-key.json')), false);
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const port = server.address().port;
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
    const errors = [];
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('requestfailed', (request) => {
      const failure = request.failure()?.errorText || '';
      if (!failure.includes('ERR_ABORTED')) errors.push(`${request.url()} ${failure}`);
    });

    await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'domcontentloaded' });
    await page.locator('.review-card').first().waitFor();
    assert.match(await page.title(), /IndexTTS2 Reference Validation/);
    assert.strictEqual(await page.locator('.review-card').count(), 6);
    assert.strictEqual(await page.locator('.generated-audio').count(), 6);
    assert.strictEqual(await page.locator('.emotion-audio').count(), 6);
    assert.strictEqual(await page.locator('.identity-audio').count(), 6);
    assert.match(await page.locator('#progress').innerText(), /0 \/ 6 complete/);

    const first = page.locator('.review-card').first();
    const sampleId = await first.getAttribute('data-sample-id');
    for (const field of ['identity_1_to_5', 'delivery_1_to_5', 'naturalness_1_to_5', 'artifact_severity_1_to_5']) {
      await first.locator(`input[data-field="${field}"][value="4"]`).check({ force: true });
    }
    for (const field of ['spoken_text_matches_expected', 'requested_mode_is_clear', 'approve_for_candidate']) {
      await first.locator(`input[data-field="${field}"]`).check();
    }
    await first.locator('textarea[data-field="notes"]').fill('Reference validation smoke note.');
    await page.waitForTimeout(150);
    assert.match(await page.locator('#progress').innerText(), /1 \/ 6 complete/);
    assert.match(await first.locator('.status').innerText(), /Complete/);
    const stored = await page.evaluate((id) => {
      const key = Object.keys(localStorage).find((item) => item.startsWith('alexandria:narrator-indextts2-reference:'));
      return JSON.parse(localStorage.getItem(key))[id];
    }, sampleId);
    assert.strictEqual(stored.delivery_1_to_5, 4);
    assert.strictEqual(stored.notes, 'Reference validation smoke note.');

    const downloadPromise = page.waitForEvent('download');
    await page.locator('#export').click();
    const download = await downloadPromise;
    assert.strictEqual(download.suggestedFilename(), 'alexandria_narrator_indextts2_reference_validation.json');
    await download.cancel();
    await page.locator('#export-dialog').waitFor({ state: 'visible' });
    await page.locator('#export-dialog button').click();

    await page.setViewportSize({ width: 1024, height: 768 });
    await page.waitForTimeout(100);
    const dimensions = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      cardWidth: document.querySelector('.review-card').getBoundingClientRect().width,
    }));
    assert.ok(dimensions.scrollWidth <= dimensions.clientWidth + 1, JSON.stringify(dimensions));
    assert.ok(dimensions.cardWidth > 800, JSON.stringify(dimensions));
    await page.screenshot({ path: SCREENSHOT, fullPage: true });
    assert.deepStrictEqual(errors, []);
    console.log(JSON.stringify({
      roundId: manifest.round_id,
      candidates: manifest.candidate_count,
      scoredSampleId: sampleId,
      screenshot: SCREENSHOT,
      browserErrors: 0,
    }, null, 2));
  } finally {
    if (browser) await browser.close().catch(() => {});
    if (server.listening) await new Promise((resolve) => server.close(resolve));
  }
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
