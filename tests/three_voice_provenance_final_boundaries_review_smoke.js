const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { spawn } = require('child_process');
const { chromium, firefox } = require('playwright');

const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_FINAL_BOUNDARIES_REVIEW_ROOT
    || '.omo/evidence/b17-t71-three-voice-provenance-final-boundaries/review',
);
const PORT = Number(process.env.ALEXANDRIA_FINAL_BOUNDARIES_REVIEW_PORT || 18894);
const BASE_URL = `http://127.0.0.1:${PORT}`;

function request(url, headers = {}) {
  return new Promise((resolve, reject) => {
    http.get(url, { headers }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => resolve({
        status: response.statusCode,
        headers: response.headers,
        body: Buffer.concat(chunks),
      }));
    }).on('error', reject);
  });
}

async function waitForServer() {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    try {
      if ((await request(`${BASE_URL}/manifest.json`)).status === 200) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('Final boundary review server did not start.');
}

async function decodeVisibleAudio(page) {
  return page.evaluate(async () => {
    const results = [];
    const elements = [...document.querySelectorAll('audio')]
      .filter((audio) => !audio.closest('section').hidden);
    for (const element of elements) {
      const response = await fetch(element.src);
      const bytes = await response.arrayBuffer();
      const context = new AudioContext();
      const buffer = await context.decodeAudioData(bytes.slice(0));
      await context.close();
      results.push({
        bytes: bytes.byteLength,
        duration: buffer.duration,
        channels: buffer.numberOfChannels,
      });
    }
    return results;
  });
}

async function runBrowser(browserType, browserName) {
  const browser = await browserType.launch({ headless: true });
  try {
    const context = await browser.newContext({
      acceptDownloads: true,
      viewport: { width: 1100, height: 850 },
    });
    const page = await context.newPage();
    page.setDefaultTimeout(25000);
    const errors = [];
    page.on('pageerror', (error) => errors.push(`page: ${error.message}`));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(`console: ${message.text()}`);
    });
    page.on('requestfailed', (req) => {
      const reason = req.failure()?.errorText;
      if (reason !== 'NS_BINDING_ABORTED' && reason !== 'net::ERR_ABORTED') {
        errors.push(`request: ${req.url()} ${reason}`);
      }
    });

    await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded' });
    await page.locator('#card').waitFor();
    assert.match(await page.title(), /Final Two-Clip Boundary Gate/i);
    assert.strictEqual(await page.locator('audio').count(), 4);
    assert.strictEqual(await page.locator('#route-nav button').count(), 2);
    assert.match(await page.locator('#progress').innerText(), /0 \/ 2 complete/);

    const data = await page.evaluate(() => window.THREE_VOICE_FINAL_SALVAGE_DATA);
    assert.strictEqual(data.rows.length, 2);
    assert.deepStrictEqual(data.card_type_counts, { boundary_final: 1, source_separation: 1 });
    assert.strictEqual(
      JSON.stringify(data).match(/BS-RoFormer|MelBand|MDX-Net|model_bs|gabox|Voc_FT/),
      null,
    );

    let decodedCount = 0;
    for (let index = 0; index < data.rows.length; index += 1) {
      await page.locator('#route-nav button').nth(index).click();
      const row = data.rows[index];
      const decoded = await decodeVisibleAudio(page);
      assert.strictEqual(decoded.length, row.card_type === 'source_separation' ? 4 : 2);
      decoded.forEach((item) => {
        assert.ok(item.bytes > 1000, JSON.stringify(item));
        assert.ok(item.duration > 0.4, JSON.stringify(item));
        assert.ok([1, 2].includes(item.channels), JSON.stringify(item));
      });
      decodedCount += decoded.length;
    }
    assert.strictEqual(decodedCount, 6);

    const boundary = data.rows.find((row) => row.card_type === 'boundary_final');
    await page.locator('#route-nav button').nth(data.rows.indexOf(boundary)).click();
    await page.locator('body').click({ position: { x: 5, y: 5 } });
    await page.keyboard.press('KeyA');
    assert.match(await page.locator('#progress').innerText(), /1 \/ 2 complete/);
    const boundarySaved = await page.evaluate((cardId) => {
      const key = `alexandria:three-voice-final-boundaries:${window.THREE_VOICE_FINAL_SALVAGE_DATA.round_id}`;
      return JSON.parse(localStorage.getItem(key))[cardId];
    }, boundary.card_id);
    assert.strictEqual(boundarySaved.decision, 'approve_final');

    const separation = data.rows.find((row) => row.card_type === 'source_separation');
    const importPayload = {
      schema_version: 1,
      round_id: data.round_id,
      exported_at: new Date().toISOString(),
      rows: [{
        card_id: separation.card_id,
        revision: 8,
        decision: 'candidate_B',
        notes: 'imported',
      }],
    };
    await page.setInputFiles('#import-file', {
      name: 'partial.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(importPayload)),
    });
    assert.match(await page.locator('#progress').innerText(), /2 \/ 2 complete/);

    const downloadPromise = page.waitForEvent('download');
    await page.locator('#export').click();
    const download = await downloadPromise;
    assert.strictEqual(
      download.suggestedFilename(),
      'alexandria_three_voice_provenance_final_boundaries_review.json',
    );
    await download.cancel();

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('#card').waitFor();
    assert.match(await page.locator('#progress').innerText(), /2 \/ 2 complete/);
    await page.locator('#status-filter').selectOption('incomplete');
    assert.strictEqual(await page.locator('#route-nav button').count(), 0);

    await page.setViewportSize({ width: 1024, height: 768 });
    const dimensions = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      audioCount: document.querySelectorAll('audio').length,
    }));
    assert.ok(dimensions.scrollWidth <= dimensions.clientWidth + 1, JSON.stringify(dimensions));
    assert.strictEqual(dimensions.audioCount, 4);
    assert.deepStrictEqual(errors, []);
    return { browserName, decodedCount, browserErrors: 0 };
  } finally {
    await browser.close();
  }
}

(async () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(REVIEW_ROOT, 'manifest.json'), 'utf8'));
  assert.strictEqual(manifest.card_count, 2);
  assert.deepStrictEqual(manifest.card_type_counts, { boundary_final: 1, source_separation: 1 });
  assert.strictEqual(manifest.model_names_blinded, true);
  assert.strictEqual(manifest.maximum_simultaneous_audio_elements, 4);
  assert.strictEqual(manifest.production_promotion_allowed, false);

  const server = spawn('python3', [
    path.join(REVIEW_ROOT, 'serve_review.py'),
    '--directory', REVIEW_ROOT,
    '--bind', '127.0.0.1',
    '--port', String(PORT),
  ], { stdio: ['ignore', 'pipe', 'pipe'] });
  const serverErrors = [];
  server.stderr.on('data', (chunk) => serverErrors.push(chunk.toString()));
  try {
    await waitForServer();
    const dataText = fs.readFileSync(path.join(REVIEW_ROOT, 'data.js'), 'utf8').trim();
    const prefix = 'window.THREE_VOICE_FINAL_SALVAGE_DATA = ';
    const data = JSON.parse(dataText.slice(prefix.length, -1));
    const separation = data.rows.find((row) => row.card_type === 'source_separation');
    const ranged = await request(`${BASE_URL}/${separation.candidates[0].audio}`, {
      Range: 'bytes=0-1023',
    });
    assert.strictEqual(ranged.status, 206);
    assert.strictEqual(ranged.body.length, 1024);
    assert.match(ranged.headers['content-range'], /^bytes 0-1023\//);

    const results = [
      await runBrowser(firefox, 'Firefox'),
      await runBrowser(chromium, 'Chromium'),
    ];
    console.log(JSON.stringify({
      candidateCount: manifest.card_count,
      rangeStatus: ranged.status,
      browsers: results,
      serverErrors,
    }, null, 2));
  } finally {
    server.kill('SIGTERM');
  }
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
