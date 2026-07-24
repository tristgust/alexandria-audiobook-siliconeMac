const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { spawn } = require('child_process');
const { chromium, firefox } = require('playwright');

const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_TARGETED_VOICE_FOLLOWUP_ROOT
    || '/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-e5ffde2d/.omo/evidence/b17-t40-targeted-voice-followup/review',
);
const EVIDENCE_ROOT = path.dirname(REVIEW_ROOT);
const PORT = Number(process.env.ALEXANDRIA_TARGETED_VOICE_FOLLOWUP_PORT || 18882);
const BASE_URL = `http://127.0.0.1:${PORT}`;

function httpRequest(url, headers = {}) {
  return new Promise((resolve, reject) => {
    const request = http.get(url, { headers }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => resolve({
        statusCode: response.statusCode,
        headers: response.headers,
        body: Buffer.concat(chunks),
      }));
    });
    request.on('error', reject);
  });
}

async function waitForServer() {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    try {
      const response = await httpRequest(`${BASE_URL}/manifest.json`);
      if (response.statusCode === 200) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('Range-capable review server did not start.');
}

async function runBrowser(browserType, browserName) {
  const browser = await browserType.launch({ headless: true });
  try {
    const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    page.setDefaultTimeout(25000);
    const browserErrors = [];
    page.on('console', (message) => {
      if (message.type() === 'error') browserErrors.push(`console: ${message.text()}`);
    });
    page.on('pageerror', (error) => browserErrors.push(`page: ${error.message}`));
    page.on('requestfailed', (request) => {
      if (request.failure()?.errorText !== 'NS_BINDING_ABORTED' && request.failure()?.errorText !== 'net::ERR_ABORTED') {
        browserErrors.push(`request: ${request.url()} ${request.failure()?.errorText}`);
      }
    });

    await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded' });
    await page.locator('#review-card').waitFor();
    assert.match(await page.title(), /targeted same-speaker follow-up/i);
    assert.strictEqual(await page.locator('audio').count(), 3);
    assert.strictEqual(await page.locator('#route-nav button').count(), 6);
    assert.match(await page.locator('#progress').innerText(), /0 \/ 6 complete/);
    const body = await page.locator('body').innerText();
    assert.match(body, /one card and three audio files load at a time/i);
    assert.match(body, /5 as best/i);
    assert.strictEqual(/OpenVoice|Seed-VC|SeedVC|IndexTTS2/i.test(body), false);

    const contract = await page.evaluate(() => window.LAZY_VOICE_FOLLOWUP_DATA);
    assert.strictEqual(contract.rows.length, 6);
    assert.strictEqual(contract.lazy_audio_loading, true);
    assert.strictEqual(contract.browser_audio_format, 'mp3_192kbps_mono_48khz');

    const decoded = [];
    for (let index = 0; index < contract.rows.length; index += 1) {
      await page.locator('#route-nav button').nth(index).click();
      const row = contract.rows[index];
      assert.strictEqual(await page.locator('#target-label').innerText(), row.target_label);
      const cardAudio = await page.evaluate(async () => {
        const elements = [...document.querySelectorAll('audio')];
        const deadline = Date.now() + 12000;
        while (Date.now() < deadline) {
          if (elements.every((audio) => audio.readyState >= HTMLMediaElement.HAVE_METADATA || audio.error)) break;
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        const results = [];
        for (const audio of elements) {
          if (audio.error) throw new Error(`Media error ${audio.error.code}: ${audio.error.message || ''}`);
          const bytes = await fetch(audio.src).then((response) => response.arrayBuffer());
          const audioContext = new AudioContext();
          const buffer = await audioContext.decodeAudioData(bytes.slice(0));
          await audioContext.close();
          results.push({
            src: new URL(audio.src).pathname,
            readyState: audio.readyState,
            bytes: bytes.byteLength,
            duration: buffer.duration,
            channels: buffer.numberOfChannels,
            sampleRate: buffer.sampleRate,
          });
        }
        return results;
      });
      assert.strictEqual(cardAudio.length, 3);
      cardAudio.forEach((item) => {
        assert.ok(item.readyState >= 1, JSON.stringify(item));
        assert.ok(item.bytes > 1000, JSON.stringify(item));
        assert.ok(item.duration > 0.8, JSON.stringify(item));
        assert.strictEqual(item.channels, 1);
      });
      decoded.push(...cardAudio.map((item) => ({ ...item, route: row.mode })));
    }
    assert.strictEqual(decoded.length, 18);

    await page.locator('#route-nav button').first().click();
    const sampleId = contract.rows[0].sample_id;
    for (const field of ['identity_1_to_5', 'delivery_1_to_5', 'naturalness_1_to_5', 'audio_cleanliness_1_to_5']) {
      await page.locator(`input[data-field="${field}"][value="5"]`).check({ force: true });
    }
    for (const field of ['spoken_text_matches_expected', 'requested_delivery_is_clear', 'approve_for_candidate']) {
      await page.locator(`input[data-field="${field}"]`).check({ force: true });
    }
    await page.locator('textarea[data-field="notes"]').fill(`${browserName} lazy-loading smoke review.`);
    await page.waitForTimeout(100);
    assert.match(await page.locator('#status').innerText(), /Complete/);
    assert.match(await page.locator('#progress').innerText(), /1 \/ 6 complete/);

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('#review-card').waitFor();
    const persisted = await page.evaluate((id) => {
      const key = Object.keys(localStorage).find((item) => item.startsWith('alexandria:lazy-voice-followup:'));
      return JSON.parse(localStorage.getItem(key))[id];
    }, sampleId);
    assert.strictEqual(persisted.identity_1_to_5, 5);
    assert.strictEqual(persisted.approve_for_candidate, true);
    assert.match(persisted.notes, new RegExp(browserName));

    await page.locator('#reload-audio').click();
    await page.waitForFunction(() => document.querySelectorAll('audio').length === 3);

    const downloadPromise = page.waitForEvent('download');
    await page.locator('#export').click();
    const download = await downloadPromise;
    assert.strictEqual(download.suggestedFilename(), 'alexandria_targeted_voice_followup_review.json');
    await download.cancel();

    await page.setViewportSize({ width: 1024, height: 768 });
    await page.waitForTimeout(100);
    const dimensions = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      audioCount: document.querySelectorAll('audio').length,
    }));
    assert.ok(dimensions.scrollWidth <= dimensions.clientWidth + 1, JSON.stringify(dimensions));
    assert.strictEqual(dimensions.audioCount, 3);

    assert.deepStrictEqual(browserErrors, []);
    return { browserName, decodedAudioCount: decoded.length, browserErrors: 0 };
  } finally {
    await browser.close();
  }
}

(async () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(REVIEW_ROOT, 'manifest.json'), 'utf8'));
  assert.strictEqual(manifest.candidate_count, 6);
  assert.strictEqual(manifest.lazy_audio_loading, true);
  assert.strictEqual(manifest.maximum_simultaneous_audio_elements, 3);
  assert.strictEqual(manifest.range_server_included, true);

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
    const full = await httpRequest(`${BASE_URL}/audio/generated/${manifest.candidate_count ? 'af83815308b071a1.mp3' : ''}`);
    assert.strictEqual(full.statusCode, 200);
    const range = await httpRequest(
      `${BASE_URL}/audio/generated/af83815308b071a1.mp3`,
      { Range: 'bytes=0-1023' },
    );
    assert.strictEqual(range.statusCode, 206);
    assert.strictEqual(range.body.length, 1024);
    assert.match(range.headers['content-range'], /^bytes 0-1023\//);
    assert.strictEqual(range.headers['accept-ranges'], 'bytes');

    const results = [];
    results.push(await runBrowser(firefox, 'Firefox'));
    results.push(await runBrowser(chromium, 'Chromium'));
    console.log(JSON.stringify({
      candidateCount: manifest.candidate_count,
      rangeStatus: range.statusCode,
      rangeBytes: range.body.length,
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
