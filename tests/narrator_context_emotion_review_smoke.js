const assert = require('assert');
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const { chromium } = require('playwright');

const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_NARRATOR_CONTEXT_REVIEW_ROOT
    || '/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-78fc5814/.omo/evidence/b17-t08-narrator-context-emotion-pass/review',
);
const EVIDENCE_ROOT = path.dirname(REVIEW_ROOT);
const DATA_SOURCE = fs.readFileSync(path.join(REVIEW_ROOT, 'data.js'), 'utf8');
const DATA = JSON.parse(DATA_SOURCE.replace(/^window\.NARRATOR_CONTEXT_DATA\s*=\s*/, '').replace(/;\s*$/, ''));
const SCREENSHOT = path.join(EVIDENCE_ROOT, 'review-smoke.png');

function contentType(file) {
  return {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.wav': 'audio/wav',
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
    if (error) return response.writeHead(404).end('Not found');
    response.writeHead(200, { 'Content-Type': contentType(target) });
    response.end(bytes);
  });
});

async function closeServer() {
  if (server.listening) await new Promise((resolve) => server.close(resolve));
}

(async () => {
  let browser;
  let importPath;
  try {
    assert.strictEqual(DATA.round_id, 'alexandria_narrator_context_emotion_v1');
    assert.strictEqual(DATA.corrections.length, 25);
    assert.strictEqual(DATA.supplement.length, 35);
    assert.strictEqual(fs.existsSync(path.join(REVIEW_ROOT, 'audio')), true);

    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const port = server.address().port;
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('console', (message) => { if (message.type() === 'error') errors.push(`console: ${message.text()}`); });
    page.on('pageerror', (error) => errors.push(`page: ${error.message}`));
    page.on('requestfailed', (request) => {
      const failure = request.failure()?.errorText || '';
      if (failure === 'net::ERR_ABORTED' && request.url().endsWith('.wav')) return;
      errors.push(`request: ${request.url()} ${failure}`);
    });

    await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle' });
    await page.waitForSelector('.queue-item');
    assert.strictEqual(await page.locator('.queue-item').count(), 25);
    assert.match(await page.locator('#progress-title').textContent(), /0 of 60/);

    const firstCorrection = DATA.corrections[0];
    assert.strictEqual(await page.locator('#correction-scene').textContent(), firstCorrection.scene);
    assert.ok((await page.locator('#correction-context').textContent()).length > 20);
    await page.click('#apply-context');
    const storedAfterApply = await page.evaluate((key) => JSON.parse(localStorage.getItem(key)), `alexandria:${DATA.round_id}:review`);
    assert.strictEqual(storedAfterApply.corrections[firstCorrection.sample_id].action, 'apply_recommendation');
    assert.strictEqual(storedAfterApply.corrections[firstCorrection.sample_id].category, firstCorrection.recommendation.category);

    const replacement = DATA.corrections.find((row) => row.recommendation.default_action === 'replace_audio');
    assert.ok(replacement);
    await page.click(`[data-id="${replacement.sample_id}"]`);
    assert.match(await page.locator('#apply-context').textContent(), /Replace with clean recut/);
    await page.click('#apply-context');
    const storedReplacement = await page.evaluate((key) => JSON.parse(localStorage.getItem(key)), `alexandria:${DATA.round_id}:review`);
    assert.strictEqual(storedReplacement.corrections[replacement.sample_id].action, 'replace_audio');

    const editable = DATA.corrections.find((row) => row.sample_id !== firstCorrection.sample_id && row.sample_id !== replacement.sample_id);
    await page.click(`[data-id="${editable.sample_id}"]`);
    await page.fill('#correction-instruction', 'Context-aware edited instruction.');
    await page.locator('#correction-instruction').dispatchEvent('change');
    const storedEdit = await page.evaluate((key) => JSON.parse(localStorage.getItem(key)), `alexandria:${DATA.round_id}:review`);
    assert.strictEqual(storedEdit.corrections[editable.sample_id].action, 'edited');

    await page.click('[data-mode="supplement"]');
    await page.waitForSelector('#supplement-view:not([hidden])');
    assert.strictEqual(await page.locator('.queue-item').count(), 35);
    const firstSupplement = DATA.supplement[0];
    assert.strictEqual(await page.locator('#supplement-scene').textContent(), firstSupplement.scene);
    await page.click('#accept-supplement');
    assert.match(await page.locator('#toast').textContent(), /Confirm the transcript/);
    await page.check('#supplement-confirmed');
    await page.click('#accept-supplement');
    const storedSupplement = await page.evaluate((key) => JSON.parse(localStorage.getItem(key)), `alexandria:${DATA.round_id}:review`);
    assert.strictEqual(storedSupplement.supplement[firstSupplement.sample_id].status, 'accepted');
    assert.strictEqual(storedSupplement.supplement[firstSupplement.sample_id].transcript_confirmed, true);

    await page.selectOption('#status-filter', 'pending');
    assert.ok(await page.locator('.queue-item').count() < 35);
    await page.selectOption('#status-filter', 'all');

    const downloadPromise = page.waitForEvent('download');
    await page.click('#export-button');
    const download = await downloadPromise;
    const exportPath = path.join(os.tmpdir(), `alexandria-context-export-${Date.now()}.json`);
    await download.saveAs(exportPath);
    const exported = JSON.parse(fs.readFileSync(exportPath, 'utf8'));
    fs.unlinkSync(exportPath);
    assert.strictEqual(exported.round_id, DATA.round_id);
    assert.strictEqual(exported.corrections.length, 25);
    assert.strictEqual(exported.supplement.length, 35);

    importPath = path.join(os.tmpdir(), `alexandria-context-import-${Date.now()}.json`);
    const importPayload = JSON.parse(JSON.stringify(exported));
    const secondSupplement = DATA.supplement[1];
    const importedRow = importPayload.supplement.find((row) => row.sample_id === secondSupplement.sample_id);
    importedRow.status = 'rejected';
    importedRow.notes = 'Imported rejection';
    fs.writeFileSync(importPath, JSON.stringify(importPayload));
    await page.setInputFiles('#import-file', importPath);
    await page.waitForTimeout(150);
    const afterImport = await page.evaluate((key) => JSON.parse(localStorage.getItem(key)), `alexandria:${DATA.round_id}:review`);
    assert.strictEqual(afterImport.supplement[secondSupplement.sample_id].status, 'rejected');

    await page.setViewportSize({ width: 1024, height: 768 });
    await page.waitForTimeout(120);
    const overflow = await page.evaluate(() => ({ body: document.body.scrollWidth - window.innerWidth, panel: document.querySelector('.review-panel').scrollWidth - document.querySelector('.review-panel').clientWidth }));
    assert.ok(overflow.body <= 1, JSON.stringify(overflow));
    assert.ok(overflow.panel <= 1, JSON.stringify(overflow));
    await page.screenshot({ path: SCREENSHOT, fullPage: true });

    assert.deepStrictEqual(errors, []);
    console.log(JSON.stringify({
      roundId: DATA.round_id,
      corrections: DATA.corrections.length,
      supplement: DATA.supplement.length,
      firstCorrection: firstCorrection.sample_id,
      replacementCorrection: replacement.sample_id,
      firstSupplement: firstSupplement.sample_id,
      screenshot: SCREENSHOT,
      browserErrors: errors.length,
    }, null, 2));
  } finally {
    if (importPath && fs.existsSync(importPath)) fs.unlinkSync(importPath);
    if (browser) await browser.close();
    await closeServer();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
