const assert = require('assert');
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_NARRATOR_RESCUE_REVIEW_ROOT
    || '/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-78fc5814/.omo/evidence/b17-t06-narrator-rescue-round2/review',
);
const EVIDENCE_ROOT = path.dirname(REVIEW_ROOT);
const REVIEW_MANIFEST = JSON.parse(
  fs.readFileSync(path.join(REVIEW_ROOT, 'manifest.json'), 'utf8'),
);
const ANSWER_KEY = JSON.parse(
  fs.readFileSync(path.join(EVIDENCE_ROOT, 'answer-key.json'), 'utf8'),
);
const SCREENSHOT = path.join(EVIDENCE_ROOT, 'review-smoke.png');

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
  let importPath;
  try {
    assert.strictEqual(REVIEW_MANIFEST.candidate_count, 25);
    assert.strictEqual(REVIEW_MANIFEST.style_count, 6);
    assert.strictEqual(fs.existsSync(path.join(REVIEW_ROOT, 'answer-key.json')), false);
    assert.strictEqual(ANSWER_KEY.length, 25);

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
    page.on('requestfailed', (request) => browserErrors.push(`request: ${request.url()} ${request.failure()?.errorText}`));

    console.log('step:load-contract');
    await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle' });
    await page.locator('.sample-card').first().waitFor();
    assert.match(await page.title(), /Narrator Rescue Blind Review/i);
    assert.strictEqual(await page.locator('#style-navigation .nav-button').count(), 6);
    assert.strictEqual(await page.locator('.sample-card').count(), 5);
    assert.ok((await page.locator('.reference-card audio').count()) >= 1);
    assert.match(await page.locator('#overall-summary').innerText(), /25 candidates · 6 styles/);

    const contract = await page.evaluate(() => {
      const review = window.ALEXANDRIA_NARRATOR_RESCUE_DATA;
      return {
        roundId: review.round_id,
        styleCount: review.styles.length,
        sampleCount: review.samples.length,
        styleCounts: Object.fromEntries(review.style_order.map((style) => [
          style,
          review.samples.filter((sample) => sample.style === style).length,
        ])),
        body: document.body.innerText,
      };
    });
    assert.strictEqual(contract.styleCount, 6);
    assert.strictEqual(contract.sampleCount, 25);
    assert.deepStrictEqual(contract.styleCounts, {
      neutral: 5,
      grief: 4,
      panic: 4,
      angry: 4,
      whisper: 4,
      laughing: 4,
    });
    assert.strictEqual(
      /IndexTTS|VoxCPM|Qwen3|Fish Audio|MOSS-TTS|Chatterbox|LoRA/i.test(contract.body),
      false,
    );

    console.log('step:autosave');
    const initialCard = page.locator('.sample-card').first();
    const scoredSampleId = await initialCard.getAttribute('data-sample-id');
    const scoredCard = page.locator(`[data-sample-id="${scoredSampleId}"]`);
    for (const field of [
      'identity_1_to_5',
      'delivery_1_to_5',
      'naturalness_1_to_5',
      'artifact_severity_1_to_5',
    ]) {
      await scoredCard.locator(`input[data-field="${field}"][value="4"]`).check();
    }
    for (const field of [
      'spoken_text_matches_expected',
      'requested_mode_is_clear',
      'approve_for_comparison',
    ]) {
      await scoredCard.locator(`input[data-field="${field}"][value="true"]`).check();
    }
    await scoredCard.locator('textarea[data-field="notes"]').fill('Round 2 browser smoke note.');
    await page.waitForTimeout(350);
    const stored = await page.evaluate((sampleId) => {
      const key = Object.keys(localStorage).find((item) => item.startsWith('alexandria:narrator-rescue:round2:') && !item.endsWith(':style'));
      return JSON.parse(localStorage.getItem(key))[sampleId];
    }, scoredSampleId);
    assert.strictEqual(stored.identity_1_to_5, 4);
    assert.strictEqual(stored.notes, 'Round 2 browser smoke note.');
    assert.match(await scoredCard.locator('.status-pill').innerText(), /Reviewed/);
    assert.match(await page.locator('#overall-progress').innerText(), /1 \/ 25 reviewed/);

    console.log('step:filters');
    await page.locator('#incomplete-only').check();
    assert.strictEqual(await page.locator('.sample-card').count(), 4);
    await page.locator('#incomplete-only').uncheck();
    await page.locator('#search').fill(scoredSampleId);
    assert.strictEqual(await page.locator('.sample-card').count(), 1);
    await page.locator('#search').fill('');
    assert.strictEqual(await page.locator('.sample-card').count(), 5);

    console.log('step:style-persistence');
    await page.locator('#style-navigation .nav-button').nth(1).click();
    assert.match(await page.locator('#style-title').innerText(), /Grief/i);
    assert.strictEqual(await page.locator('.sample-card').count(), 4);
    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('.sample-card').first().waitFor();
    assert.match(await page.locator('#style-title').innerText(), /Grief/i);
    const persisted = await page.evaluate((sampleId) => {
      const key = Object.keys(localStorage).find((item) => item.startsWith('alexandria:narrator-rescue:round2:') && !item.endsWith(':style'));
      return JSON.parse(localStorage.getItem(key))[sampleId];
    }, scoredSampleId);
    assert.strictEqual(persisted.notes, 'Round 2 browser smoke note.');

    console.log('step:import-merge');
    const importSampleId = await page.locator('.sample-card').first().getAttribute('data-sample-id');
    importPath = path.join(os.tmpdir(), `alexandria-narrator-rescue-import-${process.pid}.json`);
    fs.writeFileSync(importPath, JSON.stringify({
      schema_version: 1,
      round_id: contract.roundId,
      export_scope: 'style',
      export_key: 'grief',
      exported_at: '2099-01-01T00:00:00.000Z',
      revision: 999,
      rows: [{
        sample_id: importSampleId,
        updated_at: '2099-01-01T00:00:00.000Z',
        revision: 999,
        notes: 'Imported partial result.',
        flag_for_follow_up: true,
      }],
    }, null, 2));
    await page.locator('#import-results').setInputFiles(importPath);
    await page.locator('#import-dialog').waitFor({ state: 'visible' });
    assert.match(await page.locator('#import-summary').innerText(), /1 result rows merged/);
    await page.locator('#import-dialog button').click();
    const importCard = page.locator(`[data-sample-id="${importSampleId}"]`);
    assert.strictEqual(
      await importCard.locator('textarea[data-field="notes"]').inputValue(),
      'Imported partial result.',
    );
    assert.strictEqual(
      await importCard.locator('input[data-field="flag_for_follow_up"]').isChecked(),
      true,
    );

    console.log('step:exports');
    let downloadPromise = page.waitForEvent('download', { timeout: 10000 });
    await page.locator('#export-style').click();
    let download = await downloadPromise;
    assert.match(download.suggestedFilename(), /narrator_rescue_round2_style_grief/);
    await download.cancel();
    downloadPromise = page.waitForEvent('download', { timeout: 10000 });
    await page.locator('#export-all').click();
    download = await downloadPromise;
    assert.match(download.suggestedFilename(), /narrator_rescue_round2_cumulative_all/);
    await download.cancel();

    console.log('step:keyboard-navigation');
    const beforeTitle = await page.locator('#style-title').innerText();
    await page.locator('.style-header').click();
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(100);
    assert.notStrictEqual(await page.locator('#style-title').innerText(), beforeTitle);

    console.log('step:responsive');
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.waitForTimeout(150);
    const dimensions = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      toolbarHeight: document.querySelector('.toolbar').getBoundingClientRect().height,
      sidebarWidth: document.querySelector('.sidebar').getBoundingClientRect().width,
      firstCardWidth: document.querySelector('.sample-card').getBoundingClientRect().width,
    }));
    assert.ok(dimensions.scrollWidth <= dimensions.clientWidth + 1, JSON.stringify(dimensions));
    assert.ok(dimensions.toolbarHeight < 120, JSON.stringify(dimensions));
    assert.ok(dimensions.sidebarWidth >= 240, JSON.stringify(dimensions));
    assert.ok(dimensions.firstCardWidth >= 600, JSON.stringify(dimensions));
    await page.screenshot({ path: SCREENSHOT, fullPage: true });

    assert.deepStrictEqual(browserErrors, []);
    console.log(JSON.stringify({
      roundId: contract.roundId,
      candidates: contract.sampleCount,
      styles: contract.styleCount,
      neutralCards: contract.styleCounts.neutral,
      expressiveCardsPerStyle: contract.styleCounts.grief,
      scoredSampleId,
      importSampleId,
      screenshot: SCREENSHOT,
      browserErrors: 0,
    }, null, 2));
  } finally {
    if (importPath && fs.existsSync(importPath)) fs.unlinkSync(importPath);
    if (browser) await browser.close().catch(() => {});
    await closeServer();
  }
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
