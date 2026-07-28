const assert = require('assert');
const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_ROUND1_REVIEW_ROOT
    || path.join(ROOT, '.omo', 'evidence', 'b17-t05-multimodel-round1', 'review'),
);
const REVIEW_MANIFEST = JSON.parse(
  fs.readFileSync(path.join(REVIEW_ROOT, 'manifest.json'), 'utf8'),
);
const EXPECTED_GENERATED = Number(REVIEW_MANIFEST.generated_sample_count || 0);
const SCREENSHOT = path.join(path.dirname(REVIEW_ROOT), 'review-smoke.png');

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
    const initialCardCount = await page.locator('.sample-card').count();
    assert.ok(initialCardCount > 0);
    assert.ok(initialCardCount <= 12, `Style view is too dense: ${initialCardCount} cards`);
    assert.ok((await page.locator('.reference-card').count()) >= 5);
    assert.match(
      await page.locator('#overall-generated').innerText(),
      new RegExp(`${EXPECTED_GENERATED} generated`),
    );
    const publicContract = await page.evaluate(() => ({
      sampleCount: window.ALEXANDRIA_ROUND1_DATA.samples.filter((sample) => sample.status === 'ready').length,
      modelNamesVisible: document.body.innerText.match(/IndexTTS2|VoxCPM2|Qwen3-TTS|Fish Audio|MOSS-TTS|Chatterbox Multilingual/i),
      nativeSamples: window.ALEXANDRIA_ROUND1_DATA.samples.filter((sample) => sample.review_section_key === 'model_native_voices' && sample.status === 'ready').length,
    }));
    assert.strictEqual(publicContract.sampleCount, EXPECTED_GENERATED);
    assert.strictEqual(publicContract.modelNamesVisible, null);
    if (publicContract.nativeSamples > 0) {
      assert.ok(await page.getByRole('heading', { name: 'Model-native voices' }).count());
    }

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

    console.log('step:import-merge');
    const importPath = path.join(os.tmpdir(), `alexandria-round1-import-${process.pid}.json`);
    fs.writeFileSync(importPath, JSON.stringify({
      schema_version: 1,
      round_id: await page.evaluate(() => window.ALEXANDRIA_ROUND1_DATA.round_id),
      export_scope: 'style',
      export_key: 'smoke',
      rows: [{
        sample_id: sampleId,
        notes: 'Imported cumulative note.',
        flag_for_follow_up: false,
      }],
    }, null, 2));
    await page.locator('#import-results').setInputFiles(importPath);
    await page.locator('#import-dialog').waitFor({ state: 'visible' });
    assert.match(await page.locator('#import-summary').innerText(), /1 result rows merged/);
    await page.locator('#import-dialog button').click();
    assert.strictEqual(
      await firstCard.locator('textarea[data-field="notes"]').inputValue(),
      'Imported cumulative note.',
    );
    assert.match(await page.locator('#followup-count').innerText(), /0 flagged/);
    fs.unlinkSync(importPath);

    console.log('step:navigate-style');
    const firstTitle = await page.locator('#style-title').innerText();
    await page.locator('.style-header').click();
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(100);
    assert.notStrictEqual(await page.locator('#style-title').innerText(), firstTitle);

    console.log('step:compact');
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.waitForTimeout(100);
    const dimensions = await page.evaluate(() => {
      const toolbar = document.querySelector('.toolbar').getBoundingClientRect();
      const sidebar = document.querySelector('.sidebar').getBoundingClientRect();
      const firstCard = document.querySelector('.sample-card').getBoundingClientRect();
      return {
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
        toolbarHeight: toolbar.height,
        sidebarWidth: sidebar.width,
        firstCardWidth: firstCard.width,
      };
    });
    assert.ok(dimensions.scrollWidth <= dimensions.clientWidth + 1, JSON.stringify(dimensions));
    assert.ok(dimensions.toolbarHeight < 110, JSON.stringify(dimensions));
    assert.ok(dimensions.sidebarWidth >= 260, JSON.stringify(dimensions));
    assert.ok(dimensions.firstCardWidth >= 600, JSON.stringify(dimensions));
    await page.screenshot({ path: SCREENSHOT, fullPage: true });

    assert.deepStrictEqual(consoleErrors, []);
    console.log(JSON.stringify({
      groupCount: 5,
      baselineStyleCount: 8,
      generatedSamples: EXPECTED_GENERATED,
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
