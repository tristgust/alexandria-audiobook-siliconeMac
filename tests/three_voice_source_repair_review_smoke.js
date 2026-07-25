const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { spawn } = require('child_process');
const { chromium, firefox } = require('playwright');

const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_SOURCE_REPAIR_REVIEW_ROOT
    || '.omo/evidence/b17-t54-three-voice-source-repair-review/review',
);
const PORT = Number(process.env.ALEXANDRIA_SOURCE_REPAIR_REVIEW_PORT || 18888);
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
  throw new Error('Source repair review server did not start.');
}

async function decodeCurrentAudio(page) {
  return page.evaluate(async () => {
    const results = [];
    for (const element of document.querySelectorAll('audio')) {
      const response = await fetch(element.src);
      const bytes = await response.arrayBuffer();
      const audioContext = new AudioContext();
      const buffer = await audioContext.decodeAudioData(bytes.slice(0));
      await audioContext.close();
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
    assert.match(await page.title(), /Three-Voice Source Repair Review/i);
    assert.strictEqual(await page.locator('audio').count(), 2);
    assert.strictEqual(await page.locator('#route-nav button').count(), 21);
    assert.match(await page.locator('#progress').innerText(), /0 \/ 21 complete/);

    const data = await page.evaluate(() => window.THREE_VOICE_SOURCE_REPAIR_DATA);
    assert.strictEqual(data.rows.length, 21);
    assert.deepStrictEqual(data.repair_type_counts, { boundary: 7, cleanup: 13, decision_only: 1 });
    assert.deepStrictEqual(data.target_counts, { benny: 4, doctor: 9, narrator: 8 });
    assert.strictEqual(data.rows.every((row) => row.selected_transcript && row.original_audio && row.repaired_audio), true);

    let decodedCount = 0;
    for (let index = 0; index < data.rows.length; index += 1) {
      await page.locator('#route-nav button').nth(index).click();
      const decoded = await decodeCurrentAudio(page);
      assert.strictEqual(decoded.length, 2);
      decoded.forEach((item) => {
        assert.ok(item.bytes > 1000, JSON.stringify(item));
        assert.ok(item.duration > 0.4, JSON.stringify(item));
        assert.strictEqual(item.channels, 1);
      });
      decodedCount += decoded.length;
    }
    assert.strictEqual(decodedCount, 42);

    await page.locator('#target-filter').selectOption('doctor');
    assert.strictEqual(await page.locator('#route-nav button').count(), 9);
    await page.locator('#type-filter').selectOption('cleanup');
    assert.strictEqual(await page.locator('#route-nav button').count(), 9);
    await page.locator('#target-filter').selectOption('all');
    assert.strictEqual(await page.locator('#route-nav button').count(), 13);
    await page.locator('#type-filter').selectOption('all');

    await page.locator('#search').fill('contemptuous disbelief');
    assert.strictEqual(await page.locator('#route-nav button').count(), 1);
    assert.match(await page.locator('#emotion').innerText(), /Contemptuous disbelief/i);
    await page.locator('#search').fill('');
    await page.locator('#search').blur();
    await page.locator('#route-nav button').first().click();

    const firstClip = data.rows[0].clip_id;
    const firstEmotion = await page.locator('#emotion').innerText();
    await page.locator('body').click({ position: { x: 5, y: 5 } });
    await page.keyboard.press('KeyA');
    assert.notStrictEqual(await page.locator('#emotion').innerText(), firstEmotion);
    const saved = await page.evaluate((clipId) => {
      const key = `alexandria:three-voice-source-repair:${window.THREE_VOICE_SOURCE_REPAIR_DATA.round_id}`;
      return JSON.parse(localStorage.getItem(key))[clipId];
    }, firstClip);
    assert.strictEqual(saved.decision, 'approve_repaired');

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('#card').waitFor();
    assert.match(await page.locator('#progress').innerText(), /1 \/ 21 complete/);
    await page.locator('#status-filter').selectOption('incomplete');
    assert.strictEqual(await page.locator('#route-nav button').count(), 20);

    const importPayload = {
      schema_version: 1,
      round_id: data.round_id,
      exported_at: new Date().toISOString(),
      rows: [{ clip_id: data.rows[1].clip_id, revision: 9, decision: 'boundary_still_wrong', notes: 'imported' }],
    };
    await page.setInputFiles('#import-file', {
      name: 'partial.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(importPayload)),
    });
    assert.match(await page.locator('#progress').innerText(), /2 \/ 21 complete/);

    const downloadPromise = page.waitForEvent('download');
    await page.locator('#export').click();
    const download = await downloadPromise;
    assert.strictEqual(download.suggestedFilename(), 'alexandria_three_voice_source_repair_review.json');
    await download.cancel();

    await page.setViewportSize({ width: 1024, height: 768 });
    const dimensions = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      audioCount: document.querySelectorAll('audio').length,
    }));
    assert.ok(dimensions.scrollWidth <= dimensions.clientWidth + 1, JSON.stringify(dimensions));
    assert.strictEqual(dimensions.audioCount, 2);
    assert.deepStrictEqual(errors, []);
    return { browserName, decodedCount, browserErrors: 0 };
  } finally {
    await browser.close();
  }
}

(async () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(REVIEW_ROOT, 'manifest.json'), 'utf8'));
  assert.strictEqual(manifest.candidate_count, 21);
  assert.deepStrictEqual(manifest.repair_type_counts, { boundary: 7, cleanup: 13, decision_only: 1 });
  assert.strictEqual(manifest.original_and_repaired_comparison, true);
  assert.strictEqual(manifest.maximum_simultaneous_audio_elements, 2);
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
    const prefix = 'window.THREE_VOICE_SOURCE_REPAIR_DATA = ';
    const data = JSON.parse(dataText.slice(prefix.length, -1));
    const sample = data.rows[0].repaired_audio;
    const ranged = await request(`${BASE_URL}/${sample}`, { Range: 'bytes=0-1023' });
    assert.strictEqual(ranged.status, 206);
    assert.strictEqual(ranged.body.length, 1024);
    assert.match(ranged.headers['content-range'], /^bytes 0-1023\//);

    const results = [
      await runBrowser(firefox, 'Firefox'),
      await runBrowser(chromium, 'Chromium'),
    ];
    console.log(JSON.stringify({
      candidateCount: manifest.candidate_count,
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
