const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { chromium } = require('playwright');

const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_NARRATOR_INDEXTTS2_FOLLOWUP_REVIEW_ROOT
    || '/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-78fc5814/.omo/evidence/b17-t13-narrator-indextts2-followup/review',
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
  }[path.extname(file).toLowerCase()] || 'application/octet-stream';
}

const server = http.createServer((request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
  const relative = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
  const target = path.resolve(REVIEW_ROOT, relative);
  if (!target.startsWith(`${REVIEW_ROOT}${path.sep}`) && target !== REVIEW_ROOT) {
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
    assert.strictEqual(manifest.candidate_count, 4);
    assert.strictEqual(manifest.reencoded_pcm_24khz, true);
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
    const errors = [];
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(`console: ${message.text()}`);
    });
    page.on('pageerror', (error) => errors.push(`page: ${error.message}`));
    page.on('requestfailed', (request) => {
      if (!request.failure()?.errorText.includes('ERR_ABORTED')) {
        errors.push(`request: ${request.url()} ${request.failure()?.errorText}`);
      }
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/`, { waitUntil: 'domcontentloaded' });
    await page.locator('.review-card').first().waitFor();
    assert.strictEqual(await page.locator('.review-card').count(), 4);
    assert.strictEqual(await page.locator('audio').count(), 12);
    assert.match(await page.locator('h1').innerText(), /strong-transfer follow-up/i);

    const rageCard = page.locator('[data-sample-id]').filter({ hasText: 'Wounded rage' });
    const rageAudio = rageCard.locator('.generated-audio');
    const rageState = await rageAudio.evaluate(async (audio) => {
      const response = await fetch(audio.src);
      const bytes = await response.arrayBuffer();
      const context = new AudioContext();
      try {
        const decoded = await context.decodeAudioData(bytes.slice(0));
        return {
          ok: response.ok,
          byteLength: bytes.byteLength,
          duration: decoded.duration,
          channels: decoded.numberOfChannels,
          sampleRate: decoded.sampleRate,
        };
      } finally {
        await context.close();
      }
    });
    assert.strictEqual(rageState.ok, true);
    assert.ok(rageState.byteLength > 1000, JSON.stringify(rageState));
    assert.ok(rageState.duration > 1, JSON.stringify(rageState));
    assert.strictEqual(rageState.channels, 1);

    const firstCard = page.locator('.review-card').first();
    const sampleId = await firstCard.getAttribute('data-sample-id');
    for (const field of ['identity_1_to_5', 'delivery_1_to_5', 'naturalness_1_to_5', 'artifact_severity_1_to_5']) {
      await firstCard.locator(`input[data-field="${field}"][value="4"]`).check({ force: true });
    }
    for (const field of ['spoken_text_matches_expected', 'requested_mode_is_clear', 'approve_for_candidate']) {
      await firstCard.locator(`input[data-field="${field}"]`).check();
    }
    await page.waitForTimeout(200);
    assert.match(await page.locator('#progress').innerText(), /1 \/ 4 complete/);
    const stored = await page.evaluate((id) => {
      const key = Object.keys(localStorage).find((item) => item.includes('alexandria:narrator-indextts2-reference'));
      return JSON.parse(localStorage.getItem(key))[id];
    }, sampleId);
    assert.strictEqual(stored.delivery_1_to_5, 4);

    const downloadPromise = page.waitForEvent('download');
    await page.locator('#export').click();
    const download = await downloadPromise;
    assert.match(download.suggestedFilename(), /indextts2_followup_validation/);
    await download.cancel();

    await page.setViewportSize({ width: 1024, height: 768 });
    await page.waitForTimeout(100);
    const dimensions = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    assert.ok(dimensions.scrollWidth <= dimensions.clientWidth + 1, JSON.stringify(dimensions));
    await page.screenshot({ path: SCREENSHOT, fullPage: true });
    assert.deepStrictEqual(errors, []);
    console.log(JSON.stringify({
      roundId: manifest.round_id,
      candidates: manifest.candidate_count,
      rageAudio: rageState,
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
