const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { spawn } = require('child_process');
const { chromium, firefox } = require('playwright');

const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_PAIRED_SEED_REVIEW_ROOT
    || '.omo/evidence/b17-t76-three-voice-paired-seed-reliability/review',
);
const PORT = Number(process.env.ALEXANDRIA_PAIRED_SEED_REVIEW_PORT || 18896);
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
  throw new Error('Paired-seed reliability server did not start.');
}

async function decodeCurrentAudio(page) {
  return page.evaluate(async () => {
    const results = [];
    for (const element of document.querySelectorAll('audio')) {
      const response = await fetch(element.src);
      const bytes = await response.arrayBuffer();
      const context = new AudioContext();
      const buffer = await context.decodeAudioData(bytes.slice(0));
      await context.close();
      results.push({ bytes: bytes.byteLength, duration: buffer.duration, channels: buffer.numberOfChannels });
    }
    return results;
  });
}

async function runBrowser(browserType, browserName) {
  const browser = await browserType.launch({ headless: true });
  try {
    const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1160, height: 900 } });
    const page = await context.newPage();
    page.setDefaultTimeout(25000);
    const errors = [];
    page.on('pageerror', (error) => errors.push(`page: ${error.message}`));
    page.on('console', (message) => { if (message.type() === 'error') errors.push(`console: ${message.text()}`); });
    page.on('requestfailed', (req) => {
      const reason = req.failure()?.errorText;
      if (reason !== 'NS_BINDING_ABORTED' && reason !== 'net::ERR_ABORTED') errors.push(`request: ${req.url()} ${reason}`);
    });

    await page.goto(`${BASE_URL}/`, { waitUntil: 'networkidle' });
    await page.locator('#card').waitFor();
    assert.match(await page.title(), /Paired-Seed Prompt Reliability/i);
    assert.strictEqual(await page.locator('audio').count(), 4);
    assert.strictEqual(await page.locator('#route-nav button').count(), 9);
    assert.match(await page.locator('#progress').innerText(), /0 \/ 9 complete/);

    const data = await page.evaluate(() => window.THREE_VOICE_BANK_BENCHMARK_DATA);
    assert.strictEqual(data.rows.length, 9);
    assert.deepStrictEqual(data.target_counts, { benny: 3, doctor: 3, narrator: 3 });
    const publicText = JSON.stringify(data);
    assert.strictEqual(publicText.includes('generation_seed'), false);
    assert.strictEqual(publicText.includes('repeat_of_run_id'), false);
    assert.strictEqual(publicText.includes('legacy_reference'), false);
    assert.strictEqual(data.rows.every((row) => (
      row.identity_audio
      && row.performance_reference_audio
      && row.candidate_A.audio
      && row.candidate_B.audio
      && !('prompt_role' in row.candidate_A)
      && !('prompt_role' in row.candidate_B)
    )), true);

    let decodedCount = 0;
    for (let index = 0; index < data.rows.length; index += 1) {
      await page.locator('#route-nav button').nth(index).click();
      const decoded = await decodeCurrentAudio(page);
      assert.strictEqual(decoded.length, 4);
      decoded.forEach((item) => {
        assert.ok(item.bytes > 1000, JSON.stringify(item));
        assert.ok(item.duration > 0.4, JSON.stringify(item));
        assert.strictEqual(item.channels, 1);
      });
      decodedCount += decoded.length;
    }
    assert.strictEqual(decodedCount, 36);

    await page.locator('#target-filter').selectOption('narrator');
    assert.strictEqual(await page.locator('#route-nav button').count(), 3);
    await page.locator('#target-filter').selectOption('benny');
    assert.strictEqual(await page.locator('#route-nav button').count(), 3);
    await page.locator('#target-filter').selectOption('doctor');
    assert.strictEqual(await page.locator('#route-nav button').count(), 3);
    await page.locator('#target-filter').selectOption('all');

    await page.locator('#search').fill('Fatalistic dread · Run 2 of 3');
    assert.strictEqual(await page.locator('#route-nav button').count(), 1);
    assert.match(await page.locator('#function-label').innerText(), /Fatalistic dread.*Run 2 of 3/i);
    await page.locator('#search').fill('');
    await page.locator('#search').blur();
    await page.locator('#route-nav button').first().click();

    const firstRoute = data.rows[0].route_id;
    const firstFunction = await page.locator('#function-label').innerText();
    await page.locator('.issue-tags[data-candidate="A"] [data-issue="identity_drift"]').click();
    await page.locator('body').click({ position: { x: 5, y: 5 } });
    await page.keyboard.press('KeyA');
    assert.notStrictEqual(await page.locator('#function-label').innerText(), firstFunction);
    const saved = await page.evaluate((routeId) => {
      const key = `alexandria:three-voice-bank-benchmark:${window.THREE_VOICE_BANK_BENCHMARK_DATA.round_id}`;
      return JSON.parse(localStorage.getItem(key))[routeId];
    }, firstRoute);
    assert.strictEqual(saved.decision, 'candidate_A');
    assert.deepStrictEqual(saved.candidate_A_issues, ['identity_drift']);

    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('#card').waitFor();
    assert.match(await page.locator('#progress').innerText(), /1 \/ 9 complete/);
    await page.locator('#status-filter').selectOption('incomplete');
    assert.strictEqual(await page.locator('#route-nav button').count(), 8);

    const importPayload = {
      schema_version: 1,
      round_id: data.round_id,
      exported_at: new Date().toISOString(),
      rows: [{
        route_id: data.rows[1].route_id,
        revision: 9,
        decision: 'neither',
        notes: 'imported',
        candidate_A_issues: ['artifacts'],
        candidate_B_issues: ['weak_delivery'],
      }],
    };
    await page.setInputFiles('#import-file', {
      name: 'partial.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(importPayload)),
    });
    assert.match(await page.locator('#progress').innerText(), /2 \/ 9 complete/);

    const downloadPromise = page.waitForEvent('download');
    await page.locator('#export').click();
    const download = await downloadPromise;
    assert.strictEqual(download.suggestedFilename(), 'alexandria_three_voice_paired_seed_reliability_review.json');
    await download.cancel();

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
  assert.strictEqual(manifest.candidate_count, 9);
  assert.deepStrictEqual(manifest.target_counts, { benny: 3, doctor: 3, narrator: 3 });
  assert.strictEqual(manifest.paired_generation_seed, true);
  assert.strictEqual(manifest.same_seed_within_prompt_pair, true);
  assert.strictEqual(manifest.hidden_fixed_seed_repeat_count, 3);
  assert.strictEqual(manifest.repeat_relationship_exposed, false);
  assert.strictEqual(manifest.candidate_mapping_exposed, false);
  assert.strictEqual(manifest.production_promotion_allowed, false);

  const server = spawn('python3', [
    path.join(REVIEW_ROOT, 'serve_review.py'), '--directory', REVIEW_ROOT,
    '--bind', '127.0.0.1', '--port', String(PORT),
  ], { stdio: ['ignore', 'pipe', 'pipe'] });
  const serverErrors = [];
  server.stderr.on('data', (chunk) => serverErrors.push(chunk.toString()));
  try {
    await waitForServer();
    const dataText = fs.readFileSync(path.join(REVIEW_ROOT, 'data.js'), 'utf8').trim();
    const prefix = 'window.THREE_VOICE_BANK_BENCHMARK_DATA = ';
    const data = JSON.parse(dataText.slice(prefix.length, -1));
    const sample = data.rows[0].candidate_A.audio;
    const ranged = await request(`${BASE_URL}/${sample}`, { Range: 'bytes=0-1023' });
    assert.strictEqual(ranged.status, 206);
    assert.strictEqual(ranged.body.length, 1024);
    assert.match(ranged.headers['content-range'], /^bytes 0-1023\//);
    const results = [await runBrowser(firefox, 'Firefox'), await runBrowser(chromium, 'Chromium')];
    console.log(JSON.stringify({ candidateCount: manifest.candidate_count, rangeStatus: ranged.status, browsers: results, serverErrors }, null, 2));
  } finally {
    server.kill('SIGTERM');
  }
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
