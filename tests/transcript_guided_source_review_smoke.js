const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { spawn } = require('child_process');
const { chromium, firefox } = require('playwright');

const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_TRANSCRIPT_GUIDED_REVIEW_ROOT
    || '.omo/evidence/b17-t48-transcript-guided-source-review/review',
);
const PORT = Number(process.env.ALEXANDRIA_TRANSCRIPT_GUIDED_REVIEW_PORT || 18886);
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
  throw new Error('Transcript-guided review server did not start.');
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
    assert.match(await page.title(), /Transcript-Guided Source Review/i);
    assert.strictEqual(await page.locator('audio').count(), 2);
    assert.strictEqual(await page.locator('#route-nav button').count(), 14);
    assert.match(await page.locator('#progress').innerText(), /0 \/ 14 complete/);

    const data = await page.evaluate(() => window.TRANSCRIPT_GUIDED_SOURCE_DATA);
    assert.strictEqual(data.rows.length, 14);
    assert.strictEqual(
      data.rows.every((row) => row.selected_transcript && row.selection_reason && row.assistant_primary_emotion),
      true,
    );

    let decodedCount = 0;
    for (let index = 0; index < data.rows.length; index += 1) {
      await page.locator('#route-nav button').nth(index).click();
      const decoded = await page.evaluate(async () => {
        const results = [];
        for (const element of document.querySelectorAll('audio')) {
          const bytes = await fetch(element.src).then((response) => response.arrayBuffer());
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
      assert.strictEqual(decoded.length, 2);
      decoded.forEach((item) => {
        assert.ok(item.bytes > 1000, JSON.stringify(item));
        assert.ok(item.duration > 0.5, JSON.stringify(item));
        assert.strictEqual(item.channels, 1);
      });
      decodedCount += decoded.length;
    }
    assert.strictEqual(decodedCount, 28);

    await page.locator('#route-nav button').first().click();
    const first = data.rows[0];
    assert.strictEqual(
      await page.locator('[data-field="primary_emotion"]').inputValue(),
      first.assistant_primary_emotion,
    );
    assert.strictEqual(
      await page.locator('[data-field="dramatic_function"]').inputValue(),
      first.assistant_dramatic_function,
    );
    await page.locator('[data-field="speaker_role_decision"]').selectOption('correct');
    await page.locator('[data-field="boundary_decision"]').selectOption('correct');
    await page.locator('[data-field="audio_cleanliness_decision"]').selectOption('clean');
    await page.locator('[data-field="reference_decision"]').selectOption('approve');
    assert.match(await page.locator('#status').innerText(), /Complete/);

    const downloadPromise = page.waitForEvent('download');
    await page.locator('#export').click();
    const download = await downloadPromise;
    assert.strictEqual(download.suggestedFilename(), 'alexandria_transcript_guided_source_review.json');
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
  assert.strictEqual(manifest.candidate_count, 14);
  assert.strictEqual(manifest.transcript_guided, true);
  assert.strictEqual(manifest.assistant_labels_prefilled, true);
  assert.strictEqual(manifest.maximum_simultaneous_audio_elements, 2);

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
    const prefix = 'window.TRANSCRIPT_GUIDED_SOURCE_DATA = ';
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
