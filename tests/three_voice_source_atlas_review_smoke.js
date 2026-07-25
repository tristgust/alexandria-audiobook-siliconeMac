const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { spawn } = require('child_process');
const { chromium, firefox } = require('playwright');

const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_THREE_VOICE_ATLAS_REVIEW_ROOT
    || '.omo/evidence/b17-t51-three-voice-source-atlas-review/review',
);
const PORT = Number(process.env.ALEXANDRIA_THREE_VOICE_ATLAS_REVIEW_PORT || 18887);
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
  throw new Error('Three-voice source atlas review server did not start.');
}

async function decodeSource(page, url) {
  return page.evaluate(async (sourceUrl) => {
    const response = await fetch(sourceUrl);
    if (!response.ok) throw new Error(`Fetch failed: ${response.status} ${sourceUrl}`);
    const bytes = await response.arrayBuffer();
    const audioContext = new AudioContext();
    const buffer = await audioContext.decodeAudioData(bytes.slice(0));
    await audioContext.close();
    return {
      bytes: bytes.byteLength,
      duration: buffer.duration,
      channels: buffer.numberOfChannels,
    };
  }, url);
}

async function runBrowser(browserType, browserName) {
  const browser = await browserType.launch({ headless: true });
  try {
    const context = await browser.newContext({
      acceptDownloads: true,
      viewport: { width: 1100, height: 850 },
    });
    const page = await context.newPage();
    page.setDefaultTimeout(30000);
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

    await page.goto(`${BASE_URL}/`, { waitUntil: 'networkidle' });
    await page.evaluate(() => {
      localStorage.clear();
      location.reload();
    });
    await page.waitForLoadState('networkidle');
    await page.locator('#card').waitFor();

    assert.match(await page.title(), /Three-Voice Source Atlas/i);
    assert.strictEqual(await page.locator('audio').count(), 2);
    assert.strictEqual(await page.locator('#route-nav button').count(), 45);
    assert.match(await page.locator('#progress').innerText(), /0 \/ 45 complete/);

    const data = await page.evaluate(() => window.THREE_VOICE_SOURCE_ATLAS_DATA);
    assert.strictEqual(data.rows.length, 45);
    assert.deepStrictEqual(data.target_counts, { narrator: 23, benny: 10, doctor: 12 });
    assert.ok(data.total_listening_seconds > 450 && data.total_listening_seconds < 500);
    assert.strictEqual(
      data.rows.every((row) => row.selected_transcript && row.selection_reason && row.coverage_gap),
      true,
    );

    const uniqueAudio = new Set();
    for (const row of data.rows) {
      uniqueAudio.add(row.candidate_audio);
      uniqueAudio.add(row.target_audio);
    }
    assert.strictEqual(uniqueAudio.size, 48);
    let decodedCount = 0;
    for (const relative of uniqueAudio) {
      const decoded = await decodeSource(page, `${BASE_URL}/${relative}`);
      assert.ok(decoded.bytes > 1000, JSON.stringify({ relative, decoded }));
      assert.ok(decoded.duration > 0.4, JSON.stringify({ relative, decoded }));
      assert.strictEqual(decoded.channels, 1, JSON.stringify({ relative, decoded }));
      decodedCount += 1;
    }
    assert.strictEqual(decodedCount, 48);

    await page.getByRole('button', { name: 'Doctor', exact: true }).click();
    assert.strictEqual(await page.locator('#route-nav button').count(), 12);
    await page.getByRole('button', { name: 'All', exact: true }).first().click();
    assert.strictEqual(await page.locator('#route-nav button').count(), 45);

    const firstClipId = data.rows[0].clip_id;
    const firstHeading = await page.locator('#primary-heading').innerText();
    await page.locator('#approve-clean').click();
    assert.notStrictEqual(await page.locator('#primary-heading').innerText(), firstHeading);
    assert.match(await page.locator('#progress').innerText(), /1 \/ 45 complete/);
    const stored = await page.evaluate((clipId) => {
      const key = Object.keys(localStorage).find((item) => item.includes('three-voice-source-atlas') && !item.endsWith(':filters'));
      return JSON.parse(localStorage.getItem(key))[clipId];
    }, firstClipId);
    assert.strictEqual(stored.reference_decision, 'approve');
    assert.strictEqual(stored.audio_cleanliness_decision, 'clean');

    await page.getByRole('button', { name: 'Pending', exact: true }).click();
    assert.strictEqual(await page.locator('#route-nav button').count(), 44);
    await page.getByRole('button', { name: 'Narrator', exact: true }).click();
    assert.strictEqual(await page.locator('#route-nav button').count(), 22);

    await page.locator('#search').fill('abandonment');
    assert.ok(await page.locator('#route-nav button').count() >= 1);
    await page.getByRole('button', { name: 'All', exact: true }).first().click();
    await page.getByRole('button', { name: 'All', exact: true }).nth(1).click();
    await page.locator('#search').fill('');
    await page.locator('h1').click();
    assert.strictEqual(await page.locator('#route-nav button').count(), 45);

    const headingBefore = await page.locator('#primary-heading').innerText();
    await page.keyboard.press('k');
    const headingAfter = await page.locator('#primary-heading').innerText();
    assert.notStrictEqual(headingAfter, headingBefore);
    await page.keyboard.press('a');
    assert.match(await page.locator('#progress').innerText(), /2 \/ 45 complete/);

    await page.reload({ waitUntil: 'networkidle' });
    assert.match(await page.locator('#progress').innerText(), /2 \/ 45 complete/);

    const downloadPromise = page.waitForEvent('download');
    await page.locator('#export').click();
    const download = await downloadPromise;
    assert.strictEqual(download.suggestedFilename(), 'alexandria_three_voice_source_atlas_review.json');
    await download.cancel();

    await page.setViewportSize({ width: 1024, height: 768 });
    const dimensions = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      audioCount: document.querySelectorAll('audio').length,
      visibleCards: [...document.querySelectorAll('#card')].filter((item) => !item.hidden).length,
    }));
    assert.ok(dimensions.scrollWidth <= dimensions.clientWidth + 1, JSON.stringify(dimensions));
    assert.strictEqual(dimensions.audioCount, 2);
    assert.strictEqual(dimensions.visibleCards, 1);
    assert.deepStrictEqual(errors, []);
    return { browserName, decodedCount, browserErrors: 0 };
  } finally {
    await browser.close();
  }
}

(async () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(REVIEW_ROOT, 'manifest.json'), 'utf8'));
  assert.strictEqual(manifest.candidate_count, 45);
  assert.deepStrictEqual(manifest.target_counts, { narrator: 23, benny: 10, doctor: 12 });
  assert.strictEqual(manifest.transcript_guided, true);
  assert.strictEqual(manifest.one_click_approval, true);
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
    const prefix = 'window.THREE_VOICE_SOURCE_ATLAS_DATA = ';
    const data = JSON.parse(dataText.slice(prefix.length, -1));
    const sample = data.rows[0].candidate_audio;
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
