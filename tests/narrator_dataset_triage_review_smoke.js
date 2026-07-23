const assert = require('assert');
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const { chromium } = require('playwright');

const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_NARRATOR_TRIAGE_REVIEW_ROOT
    || '/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-78fc5814/.omo/evidence/b17-t07-narrator-dataset-triage/review',
);
const EVIDENCE_ROOT = path.dirname(REVIEW_ROOT);
const TRIAGE_MANIFEST = JSON.parse(
  fs.readFileSync(path.join(EVIDENCE_ROOT, 'triage-manifest.json'), 'utf8'),
);
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
    response.writeHead(200, { 'Content-Type': contentType(target) });
    response.end(bytes);
  });
});

async function closeServer() {
  if (!server.listening) return;
  await new Promise((resolve) => server.close(resolve));
}

(async () => {
  let browser;
  let importPath;
  try {
    assert.strictEqual(TRIAGE_MANIFEST.round_id, 'alexandria_narrator_dataset_triage_v1');
    assert.strictEqual(TRIAGE_MANIFEST.shortlist_count, 60);
    assert.strictEqual(TRIAGE_MANIFEST.rows.length, 60);
    assert.strictEqual(new Set(TRIAGE_MANIFEST.rows.map((row) => row.sample_id)).size, 60);
    assert.strictEqual(
      fs.readdirSync(path.join(REVIEW_ROOT, 'audio')).filter((name) => name.endsWith('.wav')).length,
      60,
    );

    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const port = server.address().port;
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      acceptDownloads: true,
      viewport: { width: 1440, height: 1000 },
    });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    const browserErrors = [];
    page.on('console', (message) => {
      if (message.type() === 'error') browserErrors.push(`console: ${message.text()}`);
    });
    page.on('pageerror', (error) => browserErrors.push(`page: ${error.message}`));
    page.on('requestfailed', (request) => browserErrors.push(`request: ${request.url()} ${request.failure()?.errorText}`));

    await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle' });
    await page.locator('#review-shell').waitFor();
    assert.strictEqual(await page.locator('#reviewed-count').textContent(), '0 / 60');
    assert.strictEqual(await page.locator('#accepted-count').textContent(), '0');
    assert.strictEqual(await page.locator('#rejected-count').textContent(), '0');
    assert.strictEqual(await page.locator('#accept-button').isDisabled(), true);
    assert.ok((await page.locator('#transcript-input').inputValue()).trim().length > 0);
    assert.ok((await page.locator('#instruction-input').inputValue()).trim().length > 0);

    const firstId = await page.evaluate(() => window.NARRATOR_TRIAGE_DATA.rows[0].sample_id);
    await page.locator('#transcript-confirmed').check();
    assert.strictEqual(await page.locator('#accept-button').isDisabled(), false);
    await page.locator('#accept-button').click();
    assert.strictEqual(await page.locator('#accepted-count').textContent(), '1');
    assert.strictEqual(await page.locator('#reviewed-count').textContent(), '1 / 60');

    const secondId = await page.evaluate(() => {
      const key = Object.keys(localStorage).find((item) => item.includes('alexandria_narrator_dataset_triage_v1'));
      const state = JSON.parse(localStorage.getItem(key));
      return state.current_id;
    });
    assert.notStrictEqual(secondId, firstId);
    await page.locator('#reject-button').click();
    assert.strictEqual(await page.locator('#rejected-count').textContent(), '1');
    assert.strictEqual(await page.locator('#reviewed-count').textContent(), '2 / 60');

    const thirdId = await page.evaluate(() => {
      const key = Object.keys(localStorage).find((item) => item.includes('alexandria_narrator_dataset_triage_v1'));
      return JSON.parse(localStorage.getItem(key)).current_id;
    });
    const editedTranscript = 'A corrected and confirmed transcript for the selected clip.';
    const editedInstruction = 'Dry, deliberate narration with restrained amusement.';
    await page.locator('#transcript-input').fill(editedTranscript);
    await page.locator('#instruction-input').fill(editedInstruction);
    await page.locator('#category-input').selectOption('dry_amused');
    await page.locator('#notes-input').fill('Smoke-test edit.');
    await page.waitForTimeout(250);
    await page.reload({ waitUntil: 'networkidle' });
    assert.strictEqual(await page.locator('#transcript-input').inputValue(), editedTranscript);
    assert.strictEqual(await page.locator('#instruction-input').inputValue(), editedInstruction);
    assert.strictEqual(await page.locator('#category-input').inputValue(), 'dry_amused');
    assert.strictEqual(await page.locator('#notes-input').inputValue(), 'Smoke-test edit.');

    await page.locator('#status-filter').selectOption('accepted');
    await page.waitForTimeout(100);
    assert.match(await page.locator('#clip-position').textContent(), /Clip 1 of 1/);
    assert.strictEqual(await page.locator('#status-pill').textContent(), 'Accepted');
    await page.locator('#clear-filters').click();
    assert.strictEqual(await page.locator('#status-filter').inputValue(), 'pending');

    await page.locator('#search-input').fill('corrected and confirmed');
    await page.waitForTimeout(100);
    assert.match(await page.locator('#clip-position').textContent(), /Clip 1 of 1/);
    await page.locator('#clear-filters').click();

    const currentBeforeKeyboard = await page.evaluate(() => {
      const key = Object.keys(localStorage).find((item) => item.includes('alexandria_narrator_dataset_triage_v1'));
      return JSON.parse(localStorage.getItem(key)).current_id;
    });
    await page.keyboard.press('k');
    const currentAfterKeyboard = await page.evaluate(() => {
      const key = Object.keys(localStorage).find((item) => item.includes('alexandria_narrator_dataset_triage_v1'));
      return JSON.parse(localStorage.getItem(key)).current_id;
    });
    assert.notStrictEqual(currentAfterKeyboard, currentBeforeKeyboard);

    const downloadPromise = page.waitForEvent('download');
    await page.locator('#export-button').click();
    const download = await downloadPromise;
    const downloadPath = await download.path();
    const exported = JSON.parse(fs.readFileSync(downloadPath, 'utf8'));
    assert.strictEqual(exported.round_id, TRIAGE_MANIFEST.round_id);
    assert.strictEqual(exported.rows.length, 60);
    assert.strictEqual(exported.summary.accepted_sample_count, 1);
    assert.strictEqual(exported.summary.rejected_sample_count, 1);

    const importedRow = exported.rows.find((row) => row.sample_id === thirdId);
    importedRow.status = 'accepted';
    importedRow.transcript_confirmed = true;
    importedRow.updated_at = new Date(Date.now() + 5000).toISOString();
    importedRow.revision = Number(importedRow.revision || 0) + 100;
    importPath = path.join(os.tmpdir(), `alexandria-triage-import-${process.pid}.json`);
    fs.writeFileSync(importPath, JSON.stringify(exported));
    await page.locator('#import-file').setInputFiles(importPath);
    await page.waitForTimeout(200);
    await page.locator('#status-filter').selectOption('accepted');
    assert.match(await page.locator('#clip-position').textContent(), /of 2/);

    await page.setViewportSize({ width: 1024, height: 768 });
    await page.waitForTimeout(100);
    const layout = await page.evaluate(() => ({
      bodyWidth: document.body.scrollWidth,
      viewportWidth: window.innerWidth,
      shellRight: document.querySelector('#review-shell').getBoundingClientRect().right,
    }));
    assert.ok(layout.bodyWidth <= layout.viewportWidth + 1, JSON.stringify(layout));
    assert.ok(layout.shellRight <= layout.viewportWidth + 1, JSON.stringify(layout));
    await page.screenshot({ path: SCREENSHOT, fullPage: true });

    assert.deepStrictEqual(browserErrors, []);
    console.log(JSON.stringify({
      roundId: TRIAGE_MANIFEST.round_id,
      sourceClips: TRIAGE_MANIFEST.source_clip_count,
      uniqueClips: TRIAGE_MANIFEST.deduplicated_clip_count,
      eligibleClips: TRIAGE_MANIFEST.eligible_candidate_count,
      shortlist: TRIAGE_MANIFEST.shortlist_count,
      acceptedAfterImport: 2,
      screenshot: SCREENSHOT,
      browserErrors: browserErrors.length,
    }, null, 2));
  } finally {
    if (importPath) fs.rmSync(importPath, { force: true });
    if (browser) await browser.close();
    await closeServer();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
