const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { spawn } = require('child_process');
const { chromium, firefox } = require('playwright');

const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_THREE_VOICE_PROVENANCE_REVIEW_ROOT
    || '.omo/evidence/b17-t66-three-voice-historical-provenance-review/review',
);
const PORT = Number(process.env.ALEXANDRIA_THREE_VOICE_PROVENANCE_REVIEW_PORT || 18892);
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
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try {
      if ((await request(`${BASE_URL}/manifest.json`)).status === 200) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('Historical provenance review server did not start.');
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
    await page.locator('#review-card').waitFor();
    assert.match(await page.title(), /Historical Provenance Review/i);
    assert.strictEqual(await page.locator('audio').count(), 3);
    assert.strictEqual(await page.locator('#candidate-nav button').count(), 14);
    assert.match(await page.locator('#progress').innerText(), /1 warning locked · 0 \/ 13 decisions/);

    const data = await page.evaluate(() => window.THREE_VOICE_HISTORICAL_PROVENANCE_DATA);
    assert.strictEqual(data.rows.length, 14);
    assert.strictEqual(data.actionable_count, 13);
    assert.strictEqual(data.warning_count, 1);
    assert.deepStrictEqual(data.target_counts, { benny: 10, doctor: 4 });
    assert.strictEqual(data.rows.filter((row) => row.warning_only).length, 1);
    assert.strictEqual(
      data.rows.find((row) => row.warning_only).clip_id,
      'doctor_acf_emergency_command',
    );

    let decodedCount = 0;
    for (let index = 0; index < data.rows.length; index += 1) {
      await page.locator('#candidate-nav button').nth(index).click();
      const decoded = await page.evaluate(async () => {
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
      assert.strictEqual(decoded.length, 3);
      decoded.forEach((item) => {
        assert.ok(item.bytes > 1000, JSON.stringify(item));
        assert.ok(item.duration > 0.5, JSON.stringify(item));
        assert.strictEqual(item.channels, 1);
      });
      decodedCount += decoded.length;
    }
    assert.strictEqual(decodedCount, 42);

    await page.locator('#candidate-nav button').first().click();
    await page.keyboard.press('a');
    await page.waitForTimeout(180);
    assert.match(await page.locator('#ordinal').innerText(), /Candidate 2 of 14/i);
    assert.match(await page.locator('#progress').innerText(), /1 \/ 13 decisions/);

    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('#review-card').waitFor();
    assert.match(await page.locator('#progress').innerText(), /1 \/ 13 decisions/);
    await page.locator('#candidate-nav button').first().click();
    assert.strictEqual(
      await page.locator('[data-decision="approve_usable"]').getAttribute('aria-pressed'),
      'true',
    );

    const importPayload = {
      schema_version: 1,
      round_id: data.round_id,
      rows: [{
        clip_id: data.rows[1].clip_id,
        decision: 'wrong_boundary',
        notes: 'Imported boundary note',
        revision: 7,
        updated_at: new Date().toISOString(),
      }],
    };
    await page.locator('#import-file').setInputFiles({
      name: 'review.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(importPayload)),
    });
    await page.locator('#candidate-nav button').nth(1).click();
    assert.strictEqual(
      await page.locator('[data-decision="wrong_boundary"]').getAttribute('aria-pressed'),
      'true',
    );
    assert.strictEqual(await page.locator('#notes').inputValue(), 'Imported boundary note');
    assert.match(await page.locator('#progress').innerText(), /2 \/ 13 decisions/);

    await page.locator('#target-filter').selectOption('doctor');
    assert.strictEqual(await page.locator('#candidate-nav button:visible').count(), 4);
    await page.locator('#status-filter').selectOption('warning');
    assert.strictEqual(await page.locator('#candidate-nav button:visible').count(), 1);
    assert.match(await page.locator('#status-badge').innerText(), /Locked warning/);
    assert.strictEqual(await page.locator('#decision-panel').isHidden(), true);
    assert.match(await page.locator('#warning-reason').innerText(), /not the Seventh Doctor/i);

    await page.locator('#status-filter').selectOption('all');
    await page.locator('#target-filter').selectOption('all');
    await page.locator('#search').fill('doctor_acf_dismissive_contempt');
    assert.strictEqual(await page.locator('#candidate-nav button:visible').count(), 1);
    assert.match(await page.locator('#selected-transcript').innerText(), /potty little bully/i);
    await page.locator('#search').fill('');

    const downloadPromise = page.waitForEvent('download');
    await page.locator('#export-button').click();
    const download = await downloadPromise;
    assert.strictEqual(
      download.suggestedFilename(),
      'alexandria_three_voice_historical_provenance_review.json',
    );
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
    return { browserName, decodedCount, browserErrors: 0 };
  } finally {
    await browser.close();
  }
}

(async () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(REVIEW_ROOT, 'manifest.json'), 'utf8'));
  assert.strictEqual(manifest.candidate_count, 14);
  assert.strictEqual(manifest.actionable_count, 13);
  assert.strictEqual(manifest.warning_count, 1);
  assert.deepStrictEqual(manifest.target_counts, { benny: 10, doctor: 4 });
  assert.strictEqual(manifest.maximum_simultaneous_audio_elements, 3);
  assert.strictEqual(manifest.source_context_audio_included, true);
  assert.strictEqual(manifest.known_wrong_speaker_locked, true);
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
    const prefix = 'window.THREE_VOICE_HISTORICAL_PROVENANCE_DATA = ';
    const data = JSON.parse(dataText.slice(prefix.length, -1));
    const ranged = await request(`${BASE_URL}/${data.rows[0].candidate_audio}`, {
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
      candidateCount: manifest.candidate_count,
      actionableCount: manifest.actionable_count,
      warningCount: manifest.warning_count,
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
