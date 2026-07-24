const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { spawn } = require('child_process');
const { chromium, firefox } = require('playwright');

const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_AUDIODRAMA_FUNCTION_REVIEW_ROOT
    || '/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-e5ffde2d/.omo/evidence/b17-t46-audiodrama-function-final-review/review',
);
const PORT = Number(process.env.ALEXANDRIA_AUDIODRAMA_FUNCTION_REVIEW_PORT || 18884);
const BASE_URL = `http://127.0.0.1:${PORT}`;

function request(url, headers = {}) {
  return new Promise((resolve, reject) => {
    const call = http.get(url, { headers }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => resolve({
        statusCode: response.statusCode,
        headers: response.headers,
        body: Buffer.concat(chunks),
      }));
    });
    call.on('error', reject);
  });
}

async function waitForServer() {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    try {
      if ((await request(`${BASE_URL}/manifest.json`)).statusCode === 200) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('Audiodrama review server did not start.');
}

async function runBrowser(browserType, browserName) {
  const browser = await browserType.launch({ headless: true });
  try {
    const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    page.setDefaultTimeout(25000);
    const errors = [];
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(`console: ${message.text()}`);
    });
    page.on('pageerror', (error) => errors.push(`page: ${error.message}`));
    page.on('requestfailed', (failed) => {
      const text = failed.failure()?.errorText;
      if (text !== 'NS_BINDING_ABORTED' && text !== 'net::ERR_ABORTED') {
        errors.push(`request: ${failed.url()} ${text}`);
      }
    });

    await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded' });
    await page.locator('#review-card').waitFor();
    assert.match(await page.title(), /audiodrama function coverage/i);
    assert.strictEqual(await page.locator('audio').count(), 3);
    assert.strictEqual(await page.locator('#route-nav button').count(), 15);
    assert.match(await page.locator('#progress').innerText(), /0 \/ 15 complete/);
    const body = await page.locator('body').innerText();
    assert.match(body, /five each for Narrator, Benny, and Doctor/i);
    assert.match(body, /marked Experimental/i);
    assert.strictEqual(/IndexTTS2|VoxCPM|Fish S2|Qwen/i.test(body), false);

    const contract = await page.evaluate(() => window.LAZY_VOICE_FOLLOWUP_DATA);
    assert.strictEqual(contract.rows.length, 15);
    assert.deepStrictEqual(
      Object.fromEntries(['narrator', 'benny', 'doctor'].map((key) => [key, contract.rows.filter((row) => row.target_key === key).length])),
      { narrator: 5, benny: 5, doctor: 5 },
    );
    assert.strictEqual(contract.rows.filter((row) => row.technical_pass).length, 11);
    assert.strictEqual(contract.rows.filter((row) => !row.technical_pass).length, 4);

    let decodedCount = 0;
    for (let index = 0; index < contract.rows.length; index += 1) {
      await page.locator('#route-nav button').nth(index).click();
      const row = contract.rows[index];
      assert.strictEqual(await page.locator('#target-label').innerText(), row.target_label);
      if (!row.technical_pass) assert.match(await page.locator('#mode-label').innerText(), /Experimental/);
      const decoded = await page.evaluate(async () => {
        const results = [];
        for (const audio of document.querySelectorAll('audio')) {
          const bytes = await fetch(audio.src).then((response) => response.arrayBuffer());
          const audioContext = new AudioContext();
          const buffer = await audioContext.decodeAudioData(bytes.slice(0));
          await audioContext.close();
          results.push({ bytes: bytes.byteLength, duration: buffer.duration, channels: buffer.numberOfChannels });
        }
        return results;
      });
      assert.strictEqual(decoded.length, 3);
      decoded.forEach((item) => {
        assert.ok(item.bytes > 1000, JSON.stringify(item));
        assert.ok(item.duration > 0.7, JSON.stringify(item));
        assert.strictEqual(item.channels, 1);
      });
      decodedCount += decoded.length;
    }
    assert.strictEqual(decodedCount, 45);

    await page.locator('#route-nav button').first().click();
    const firstId = contract.rows[0].sample_id;
    for (const field of ['identity_1_to_5', 'delivery_1_to_5', 'naturalness_1_to_5', 'audio_cleanliness_1_to_5']) {
      await page.locator(`input[data-field="${field}"][value="5"]`).check({ force: true });
    }
    for (const field of ['spoken_text_matches_expected', 'requested_delivery_is_clear', 'approve_for_candidate']) {
      await page.locator(`input[data-field="${field}"]`).check({ force: true });
    }
    await page.locator('textarea[data-field="notes"]').fill(`${browserName} audiodrama smoke review.`);
    await page.waitForTimeout(100);
    assert.match(await page.locator('#status').innerText(), /Complete/);
    assert.match(await page.locator('#progress').innerText(), /1 \/ 15 complete/);

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('#review-card').waitFor();
    const persisted = await page.evaluate((id) => {
      const key = Object.keys(localStorage).find((item) => item.startsWith('alexandria:audiodrama-function-final:'));
      return JSON.parse(localStorage.getItem(key))[id];
    }, firstId);
    assert.strictEqual(persisted.identity_1_to_5, 5);
    assert.strictEqual(persisted.approve_for_candidate, true);

    const downloadPromise = page.waitForEvent('download');
    await page.locator('#export').click();
    const download = await downloadPromise;
    assert.strictEqual(download.suggestedFilename(), 'alexandria_audiodrama_function_review.json');
    await download.cancel();

    await page.setViewportSize({ width: 1024, height: 768 });
    const dimensions = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      audioCount: document.querySelectorAll('audio').length,
    }));
    assert.ok(dimensions.scrollWidth <= dimensions.clientWidth + 1, JSON.stringify(dimensions));
    assert.strictEqual(dimensions.audioCount, 3);
    assert.deepStrictEqual(errors, []);
    return { browserName, decodedAudioCount: decodedCount, browserErrors: 0 };
  } finally {
    await browser.close();
  }
}

(async () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(REVIEW_ROOT, 'manifest.json'), 'utf8'));
  assert.strictEqual(manifest.candidate_count, 15);
  assert.strictEqual(manifest.qualified_count, 11);
  assert.strictEqual(manifest.experimental_count, 4);
  assert.strictEqual(manifest.lazy_audio_loading, true);
  assert.strictEqual(manifest.range_requests_required, true);

  const dataText = fs.readFileSync(path.join(REVIEW_ROOT, 'data.js'), 'utf8');
  const data = JSON.parse(dataText.slice(dataText.indexOf('=') + 1).trim().replace(/;$/, ''));
  const firstAudio = data.rows[0].generated_audio;

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
    const range = await request(`${BASE_URL}/${firstAudio}`, { Range: 'bytes=0-1023' });
    assert.strictEqual(range.statusCode, 206);
    assert.strictEqual(range.body.length, 1024);
    assert.match(range.headers['content-range'], /^bytes 0-1023\//);
    const browsers = [
      await runBrowser(firefox, 'Firefox'),
      await runBrowser(chromium, 'Chromium'),
    ];
    console.log(JSON.stringify({
      candidateCount: manifest.candidate_count,
      qualifiedCount: manifest.qualified_count,
      experimentalCount: manifest.experimental_count,
      rangeStatus: range.statusCode,
      browsers,
      serverErrors,
    }, null, 2));
  } finally {
    server.kill('SIGTERM');
  }
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
