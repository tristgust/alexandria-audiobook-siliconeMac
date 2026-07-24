const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { chromium } = require('playwright');

const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_THREE_VOICE_SEEDVC_REVIEW_ROOT
    || '/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-e5ffde2d/.omo/evidence/b17-t30-three-voice-seedvc-final/review',
);
const EVIDENCE_ROOT = path.dirname(REVIEW_ROOT);
const SCREENSHOT = path.join(EVIDENCE_ROOT, 'review-smoke.png');

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
    response.writeHead(200, {
      'Content-Type': contentType(target),
      'Content-Length': bytes.length,
      'Accept-Ranges': 'bytes',
    });
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
    const manifest = JSON.parse(fs.readFileSync(path.join(REVIEW_ROOT, 'manifest.json'), 'utf8'));
    assert.strictEqual(manifest.candidate_count, 8);
    assert.strictEqual(manifest.excluded_route_count, 1);
    assert.strictEqual(fs.existsSync(path.join(REVIEW_ROOT, 'answer-key.json')), false);

    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const port = server.address().port;
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    const browserErrors = [];
    page.on('console', (message) => {
      if (message.type() === 'error') browserErrors.push(`console: ${message.text()}`);
    });
    page.on('pageerror', (error) => browserErrors.push(`page: ${error.message}`));
    page.on('requestfailed', (request) => {
      if (request.failure()?.errorText !== 'net::ERR_ABORTED') {
        browserErrors.push(`request: ${request.url()} ${request.failure()?.errorText}`);
      }
    });

    await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'domcontentloaded' });
    await page.locator('.review-card').first().waitFor();
    assert.match(await page.title(), /performance conversion proof/i);
    assert.strictEqual(await page.locator('.review-card').count(), 8);
    assert.strictEqual(await page.locator('#target-nav button').count(), 4);
    assert.match(await page.locator('#progress').innerText(), /0 \/ 8 complete/);

    const contract = await page.evaluate(() => ({
      roundId: window.THREE_VOICE_OPENVOICE_DATA.round_id,
      rows: window.THREE_VOICE_OPENVOICE_DATA.rows,
      targets: window.THREE_VOICE_OPENVOICE_DATA.target_order,
      excluded: window.THREE_VOICE_OPENVOICE_DATA.excluded_routes,
      body: document.body.innerText,
    }));
    assert.strictEqual(contract.rows.length, 8);
    assert.deepStrictEqual(contract.targets, ['narrator', 'benny', 'doctor']);
    assert.deepStrictEqual(
      contract.rows.map((row) => [row.target_key, row.mode]),
      [
        ['narrator', 'calm'],
        ['narrator', 'pleading'],
        ['narrator', 'angry'],
        ['benny', 'calm'],
        ['benny', 'pleading'],
        ['benny', 'angry'],
        ['doctor', 'pleading'],
        ['doctor', 'angry'],
      ],
    );
    assert.deepStrictEqual(contract.excluded, [{
      target_key: 'doctor',
      mode: 'calm',
      reason: 'Failed stable identity checks and is intentionally omitted.',
    }]);
    assert.strictEqual(/OpenVoice/i.test(contract.body), false);

    const first = page.locator('.review-card').first();
    const sampleId = await first.getAttribute('data-sample-id');
    for (const field of ['identity_1_to_5', 'delivery_1_to_5', 'naturalness_1_to_5', 'artifact_severity_1_to_5']) {
      await first.locator(`input[data-field="${field}"][value="4"]`).check({ force: true });
    }
    for (const field of ['spoken_text_matches_expected', 'performance_is_preserved', 'approve_for_candidate']) {
      await first.locator(`input[data-field="${field}"]`).check({ force: true });
    }
    await first.locator('textarea[data-field="notes"]').fill('Three-voice Seed-VC smoke review.');
    await page.waitForTimeout(150);
    assert.match(await first.locator('.status').innerText(), /Complete/);
    assert.match(await page.locator('#progress').innerText(), /1 \/ 8 complete/);

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('.review-card').first().waitFor();
    const persisted = await page.evaluate((id) => {
      const key = Object.keys(localStorage).find((item) => item.startsWith('alexandria:three-voice-seedvc-final:'));
      return JSON.parse(localStorage.getItem(key))[id];
    }, sampleId);
    assert.strictEqual(persisted.notes, 'Three-voice Seed-VC smoke review.');
    assert.strictEqual(persisted.approve_for_candidate, true);

    const expectedCounts = [8, 3, 3, 2];
    const expectedTargets = [null, 'narrator', 'benny', 'doctor'];
    for (let index = 0; index < expectedCounts.length; index += 1) {
      await page.locator('#target-nav button').nth(index).click();
      assert.strictEqual(await page.locator('.review-card').count(), expectedCounts[index]);
      if (expectedTargets[index]) {
        assert.strictEqual(
          await page.locator('.review-card').first().getAttribute('data-target-key'),
          expectedTargets[index],
        );
      }
    }

    const decode = await page.evaluate(async () => {
      const rows = [...document.querySelectorAll('.review-card')];
      const results = [];
      for (const card of rows) {
        const url = card.querySelector('.converted-audio').src;
        const bytes = await fetch(url).then((response) => response.arrayBuffer());
        const context = new AudioContext();
        const decoded = await context.decodeAudioData(bytes.slice(0));
        await context.close();
        results.push({
          bytes: bytes.byteLength,
          duration: decoded.duration,
          channels: decoded.numberOfChannels,
        });
      }
      return results;
    });
    assert.strictEqual(decode.length, 2);
    decode.forEach((row) => {
      assert.ok(row.bytes > 10000, JSON.stringify(row));
      assert.ok(row.duration > 1, JSON.stringify(row));
      assert.strictEqual(row.channels, 1);
    });

    await page.locator('#target-nav button').first().click();
    const downloadPromise = page.waitForEvent('download');
    await page.locator('#export').click();
    const download = await downloadPromise;
    assert.match(download.suggestedFilename(), /three_voice_seedvc_final_review/);
    await download.cancel();

    await page.setViewportSize({ width: 1024, height: 768 });
    await page.waitForTimeout(100);
    const dimensions = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      cards: document.querySelectorAll('.review-card').length,
    }));
    assert.ok(dimensions.scrollWidth <= dimensions.clientWidth + 1, JSON.stringify(dimensions));
    assert.strictEqual(dimensions.cards, 8);
    await page.screenshot({ path: SCREENSHOT, fullPage: true });

    assert.deepStrictEqual(browserErrors, []);
    console.log(JSON.stringify({
      roundId: contract.roundId,
      candidates: contract.rows.length,
      scoredSampleId: sampleId,
      decodedDoctorAudio: decode,
      screenshot: SCREENSHOT,
      browserErrors: 0,
    }, null, 2));
  } finally {
    if (browser) await browser.close().catch(() => {});
    await closeServer();
  }
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
