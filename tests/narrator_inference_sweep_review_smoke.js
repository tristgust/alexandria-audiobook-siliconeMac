const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { chromium } = require('playwright');

const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_NARRATOR_SWEEP_REVIEW_ROOT
    || '/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-78fc5814/.omo/evidence/b17-t11-narrator-inference-sweep/review',
);
const EVIDENCE_ROOT = path.dirname(REVIEW_ROOT);
const MANIFEST = JSON.parse(fs.readFileSync(path.join(REVIEW_ROOT, 'manifest.json'), 'utf8'));
const ANSWER_KEY = JSON.parse(fs.readFileSync(path.join(EVIDENCE_ROOT, 'answer-key.json'), 'utf8'));
const SCREENSHOT = path.join(EVIDENCE_ROOT, 'review-smoke.png');

function contentType(file) {
  return {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.wav': 'audio/wav',
    '.mp3': 'audio/mpeg',
    '.json': 'application/json; charset=utf-8',
    '.txt': 'text/plain; charset=utf-8',
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

(async () => {
  let browser;
  try {
    assert.strictEqual(MANIFEST.round_id, 'alexandria_narrator_inference_sweep_v1');
    assert.strictEqual(MANIFEST.candidate_count, 24);
    assert.strictEqual(MANIFEST.style_count, 6);
    assert.strictEqual(ANSWER_KEY.length, 24);
    assert.strictEqual(fs.existsSync(path.join(REVIEW_ROOT, 'answer-key.json')), false);

    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const port = server.address().port;
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      acceptDownloads: true,
      viewport: { width: 1440, height: 1000 },
    });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    const browserErrors = [];
    page.on('console', (message) => {
      if (message.type() === 'error') browserErrors.push(`console: ${message.text()}`);
    });
    page.on('pageerror', (error) => browserErrors.push(`page: ${error.message}`));
    page.on('requestfailed', (request) => {
      const failure = request.failure()?.errorText || '';
      if (!failure.includes('ERR_ABORTED')) {
        browserErrors.push(`request: ${request.url()} ${failure}`);
      }
    });

    await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle' });
    await page.locator('.sample-card').first().waitFor();
    assert.strictEqual(await page.locator('#style-navigation .nav-button').count(), 6);
    assert.strictEqual(await page.locator('.sample-card').count(), 4);
    assert.match(await page.locator('#overall-summary').innerText(), /24 candidates · 6 styles/);

    const contract = await page.evaluate(() => {
      const review = window.ALEXANDRIA_NARRATOR_RESCUE_DATA;
      return {
        roundId: review.round_id,
        sampleCount: review.samples.length,
        styleCounts: Object.fromEntries(review.style_order.map((style) => [
          style,
          review.samples.filter((sample) => sample.style === style).length,
        ])),
        body: document.body.innerText,
      };
    });
    assert.strictEqual(contract.roundId, MANIFEST.round_id);
    assert.strictEqual(contract.sampleCount, 24);
    assert.deepStrictEqual(contract.styleCounts, {
      neutral: 4,
      grief: 4,
      panic: 4,
      angry: 4,
      whisper: 4,
      laughing: 4,
    });
    assert.strictEqual(/temperature|seed|Qwen3-TTS|answer-key/i.test(contract.body), false);

    const first = page.locator('.sample-card').first();
    const sampleId = await first.getAttribute('data-sample-id');
    for (const field of [
      'identity_1_to_5',
      'delivery_1_to_5',
      'naturalness_1_to_5',
      'artifact_severity_1_to_5',
    ]) {
      await first.locator(`input[data-field="${field}"][value="4"]`).check();
    }
    for (const field of [
      'spoken_text_matches_expected',
      'requested_mode_is_clear',
      'approve_for_comparison',
    ]) {
      await first.locator(`input[data-field="${field}"][value="true"]`).check();
    }
    await first.locator('textarea[data-field="notes"]').fill('Deterministic sweep smoke note.');
    await page.waitForTimeout(300);
    assert.match(await page.locator('#overall-progress').innerText(), /1 \/ 24 reviewed/);

    await page.locator('#incomplete-only').check();
    assert.strictEqual(await page.locator('.sample-card').count(), 3);
    await page.locator('#incomplete-only').uncheck();

    await page.locator('#style-navigation .nav-button').nth(1).click();
    assert.match(await page.locator('#style-title').innerText(), /Grief/i);
    assert.strictEqual(await page.locator('.sample-card').count(), 4);
    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('.sample-card').first().waitFor();
    assert.match(await page.locator('#style-title').innerText(), /Grief/i);

    let downloadPromise = page.waitForEvent('download', { timeout: 10000 });
    await page.locator('#export-all').click();
    const download = await downloadPromise;
    assert.match(download.suggestedFilename(), /narrator_inference_sweep_cumulative_all/);
    await download.cancel();

    await page.setViewportSize({ width: 1024, height: 768 });
    await page.waitForTimeout(150);
    const dimensions = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      sidebarWidth: document.querySelector('.sidebar').getBoundingClientRect().width,
      firstCardWidth: document.querySelector('.sample-card').getBoundingClientRect().width,
    }));
    assert.ok(dimensions.scrollWidth <= dimensions.clientWidth + 1, JSON.stringify(dimensions));
    assert.ok(dimensions.sidebarWidth >= 240, JSON.stringify(dimensions));
    assert.ok(dimensions.firstCardWidth >= 600, JSON.stringify(dimensions));
    await page.screenshot({ path: SCREENSHOT, fullPage: true });

    assert.deepStrictEqual(browserErrors, []);
    console.log(JSON.stringify({
      roundId: contract.roundId,
      candidates: contract.sampleCount,
      candidatesPerStyle: 4,
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
