const assert = require('assert');
const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_ROUND1_REVIEW_ROOT
    || path.join(ROOT, '.omo', 'evidence', 'b17-t05-multimodel-round1', 'review'),
);
const SCREENSHOT = path.join(path.dirname(REVIEW_ROOT), 'review-smoke.png');
const REQUIRED_IDENTITIES = [
  'narrator',
  'benny',
  'doctor',
  'ryan_neutral',
  'ryan_acted',
];
const REQUIRED_STYLES = [
  'neutral',
  'happy',
  'tender',
  'grief',
  'panic',
  'angry',
  'menacing',
  'sarcastic',
  'whisper',
  'laughing',
];
const REQUIRED_REVIEW_FIELDS = [
  'identity_1_to_5',
  'delivery_1_to_5',
  'naturalness_1_to_5',
  'artifact_severity_1_to_5',
  'spoken_text_matches_expected',
  'requested_mode_is_clear',
  'approve_for_comparison',
];
const MODEL_NAME_PATTERN = /IndexTTS2|VoxCPM2|Qwen3[- ]TTS|Fish(?: Audio)? S2 Pro|MOSS[- ]TTS|Chatterbox Multilingual|Higgs Audio/i;

function parseAssignedJson(file, prefix) {
  const text = fs.readFileSync(file, 'utf8').trim();
  assert.ok(text.startsWith(prefix), `Unexpected assignment prefix in ${file}`);
  assert.ok(text.endsWith(';'), `Unexpected assignment suffix in ${file}`);
  return JSON.parse(text.slice(prefix.length, -1));
}

const REVIEW_MANIFEST = JSON.parse(
  fs.readFileSync(path.join(REVIEW_ROOT, 'manifest.json'), 'utf8'),
);
const PUBLIC_DATA = parseAssignedJson(
  path.join(REVIEW_ROOT, 'data.js'),
  'window.ALEXANDRIA_ROUND1_DATA = ',
);
const SEED = JSON.parse(
  fs.readFileSync(path.join(REVIEW_ROOT, 'alexandria_round1_v2_existing_results.json'), 'utf8'),
);

const ANSWER_BY_SAMPLE = new Map();
for (const filename of fs.readdirSync(path.join(REVIEW_ROOT, 'answer-keys')).sort()) {
  if (!filename.endsWith('.json')) continue;
  const rows = JSON.parse(
    fs.readFileSync(path.join(REVIEW_ROOT, 'answer-keys', filename), 'utf8'),
  );
  rows.forEach((row) => ANSWER_BY_SAMPLE.set(row.sample_id, row));
}

function contentType(file) {
  return {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.wav': 'audio/wav',
    '.mp3': 'audio/mpeg',
    '.json': 'application/json; charset=utf-8',
  }[path.extname(file).toLowerCase()] || 'application/octet-stream';
}

function resultStorageKey(keys) {
  return keys.find((key) => (
    key.startsWith('alexandria:multimodel-review:round1:')
      && !key.endsWith(':group')
      && !key.endsWith(':style')
      && !key.endsWith(':identity')
  ));
}

function assertStaticContract() {
  const ready = PUBLIC_DATA.samples.filter((sample) => sample.status === 'ready' && sample.audio);
  const identities = [...new Set(ready.map((sample) => sample.identity_key))];
  const styles = PUBLIC_DATA.styles.map((style) => style.key);
  const native = ready.filter((sample) => (
    sample.identity_key.startsWith('native_')
      || sample.review_section_key === 'model_native_voices'
  ));
  assert.strictEqual(ready.length, 264);
  assert.deepStrictEqual(PUBLIC_DATA.identity_order, REQUIRED_IDENTITIES);
  assert.deepStrictEqual(identities.sort(), [...REQUIRED_IDENTITIES].sort());
  assert.deepStrictEqual(styles, REQUIRED_STYLES);
  assert.strictEqual(native.length, 0);
  assert.strictEqual(REVIEW_MANIFEST.generated_sample_count, 264);
  assert.strictEqual(REVIEW_MANIFEST.identity_count, 5);
  assert.strictEqual(REVIEW_MANIFEST.style_count, 10);
  assert.strictEqual(REVIEW_MANIFEST.native_voice_matrix_removed, true);
  assert.strictEqual(REVIEW_MANIFEST.seed_results_embedded, true);
  assert.strictEqual(SEED.rows.length, 36);
  assert.strictEqual(SEED.summary.carried_forward_row_count, 36);
  assert.strictEqual(SEED.summary.complete_sample_count, 35);
  assert.strictEqual(SEED.summary.incomplete_sample_count, 229);
  assert.ok(PUBLIC_DATA.samples.every((sample) => !Object.hasOwn(sample, 'model_key')));

  const narratorOrders = PUBLIC_DATA.styles.map((style) => PUBLIC_DATA.samples
    .filter((sample) => (
      sample.status === 'ready'
        && sample.identity_key === 'narrator'
        && sample.style === style.key
    ))
    .sort((left, right) => (
      left.review_section_label.localeCompare(right.review_section_label)
        || left.sample_id.localeCompare(right.sample_id)
    ))
    .map((sample) => ANSWER_BY_SAMPLE.get(sample.sample_id)?.model_key)
    .join(','));
  assert.ok(narratorOrders.every((order) => order.split(',').length >= 5));
  assert.ok(new Set(narratorOrders).size >= 8, 'Candidate order is not sufficiently mixed');
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

async function getSavedState(page) {
  return page.evaluate(() => {
    const keys = Object.keys(localStorage);
    const key = keys.find((candidate) => (
      candidate.startsWith('alexandria:multimodel-review:round1:')
        && !candidate.endsWith(':group')
        && !candidate.endsWith(':style')
        && !candidate.endsWith(':identity')
    ));
    return { key, saved: key ? JSON.parse(localStorage.getItem(key)) : null };
  });
}

async function completeCard(card, score, note) {
  for (const field of [
    'identity_1_to_5',
    'delivery_1_to_5',
    'naturalness_1_to_5',
    'artifact_severity_1_to_5',
  ]) {
    await card.locator(`input[data-field="${field}"][value="${score}"]`).check();
  }
  for (const field of [
    'spoken_text_matches_expected',
    'requested_mode_is_clear',
    'approve_for_comparison',
  ]) {
    await card.locator(`input[data-field="${field}"][value="true"]`).check();
  }
  await card.locator('textarea[data-field="notes"]').fill(note);
}

(async () => {
  assertStaticContract();
  let browser;
  let importPath;
  try {
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const port = server.address().port;
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      acceptDownloads: true,
      viewport: { width: 1536, height: 1024 },
    });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    const browserErrors = [];
    page.on('console', (message) => {
      if (message.type() === 'error') browserErrors.push(`console: ${message.text()}`);
    });
    page.on('pageerror', (error) => browserErrors.push(`page: ${error.message}`));
    page.on('requestfailed', (request) => {
      browserErrors.push(`request: ${request.url()} ${request.failure()?.errorText || 'failed'}`);
    });

    console.log('step:first-load-seed');
    await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle' });
    await page.locator('.sample-card').first().waitFor();
    assert.match(await page.title(), /Round 1 Blind Review/i);
    assert.strictEqual(await page.locator('#group-navigation .nav-button').count(), 5);
    assert.strictEqual(
      await page.locator('#style-navigation .nav-button').count(),
      PUBLIC_DATA.groups[Object.keys(PUBLIC_DATA.groups)[0]].styles.length,
    );

    const options = await page.locator('#identity-filter option').evaluateAll((nodes) => (
      nodes.map((node) => ({ value: node.value, label: node.textContent.trim() }))
    ));
    assert.deepStrictEqual(options, [
      { value: 'narrator', label: 'Narrator' },
      { value: 'benny', label: 'Benny' },
      { value: 'doctor', label: 'Doctor' },
      { value: 'ryan_neutral', label: 'Ryan — neutral anchor' },
      { value: 'ryan_acted', label: 'Ryan — acted anchor' },
    ]);
    assert.strictEqual(await page.locator('#identity-filter').inputValue(), 'narrator');
    assert.strictEqual(await page.locator('.reference-card').count(), 1);
    assert.strictEqual(await page.locator('.reference-card h3').innerText(), 'Narrator');
    const initialCardCount = await page.locator('.sample-card').count();
    assert.ok(initialCardCount >= 4 && initialCardCount <= 6, `Unexpected card count: ${initialCardCount}`);
    assert.ok(initialCardCount <= 12);
    assert.strictEqual(MODEL_NAME_PATTERN.test(await page.locator('body').innerText()), false);
    assert.match(await page.locator('#overall-generated').innerText(), /264 ready · 0 pending · 264 planned/);
    assert.match(await page.locator('#overall-progress').innerText(), /35 \/ 264 reviewed/);
    assert.match(await page.locator('#style-progress-text').innerText(), /reviewed for Narrator/);

    const seeded = await getSavedState(page);
    assert.ok(seeded.key, 'Review result localStorage key was not created');
    assert.strictEqual(Object.keys(seeded.saved).length, 36);
    const seededComplete = await page.evaluate(({ saved, fields }) => (
      Object.values(saved).filter((row) => fields.every((field) => Object.hasOwn(row, field))).length
    ), { saved: seeded.saved, fields: REQUIRED_REVIEW_FIELDS });
    assert.strictEqual(seededComplete, 35);

    console.log('step:autosave-and-filters');
    let card = page.locator('.sample-card:not(.complete)').first();
    if (await card.count() === 0) {
      await page.locator('#next-style').click();
      card = page.locator('.sample-card:not(.complete)').first();
    }
    assert.strictEqual(await card.count(), 1, 'No incomplete visible card was available');
    const scoredSampleId = await card.getAttribute('data-sample-id');
    card = page.locator(`.sample-card[data-sample-id="${scoredSampleId}"]`);
    await completeCard(card, 4, 'Browser smoke autosave note.');
    await page.waitForTimeout(450);
    const afterScore = await getSavedState(page);
    assert.strictEqual(afterScore.saved[scoredSampleId].identity_1_to_5, 4);
    assert.strictEqual(afterScore.saved[scoredSampleId].notes, 'Browser smoke autosave note.');
    assert.match(await card.locator('.status-pill').innerText(), /Reviewed/);

    await page.locator('#incomplete-only').check();
    assert.strictEqual(
      await page.locator(`.sample-card[data-sample-id="${scoredSampleId}"]`).count(),
      0,
    );
    await page.locator('#incomplete-only').uncheck();
    await page.locator('#search').fill(scoredSampleId);
    assert.strictEqual(await page.locator('.sample-card').count(), 1);
    await page.locator('#search').fill('');
    assert.ok((await page.locator('.sample-card').count()) >= 4);

    console.log('step:identity-persistence-and-seed-precedence');
    const seedSampleId = SEED.rows[0].sample_id;
    await page.evaluate(({ seedSampleId: id }) => {
      const keys = Object.keys(localStorage);
      const key = keys.find((candidate) => (
        candidate.startsWith('alexandria:multimodel-review:round1:')
          && !candidate.endsWith(':group')
          && !candidate.endsWith(':style')
          && !candidate.endsWith(':identity')
      ));
      const saved = JSON.parse(localStorage.getItem(key));
      saved[id] = {
        ...saved[id],
        identity_1_to_5: 1,
        notes: 'Existing browser value must win over seed.',
        updated_at: '2099-01-01T00:00:00.000Z',
        revision: 999,
      };
      localStorage.setItem(key, JSON.stringify(saved));
    }, { seedSampleId });
    await page.locator('#identity-filter').selectOption('benny');
    await page.locator('.sample-card').first().waitFor();
    assert.strictEqual(await page.locator('.reference-card').count(), 1);
    assert.strictEqual(await page.locator('.reference-card h3').innerText(), 'Benny');
    await page.locator('#group-navigation .nav-button').nth(1).click();
    assert.strictEqual(await page.locator('#identity-filter').inputValue(), 'benny');
    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('.sample-card').first().waitFor();
    assert.strictEqual(await page.locator('#identity-filter').inputValue(), 'benny');
    assert.strictEqual(await page.locator('.reference-card h3').innerText(), 'Benny');
    const afterReload = await getSavedState(page);
    assert.strictEqual(afterReload.saved[seedSampleId].identity_1_to_5, 1);
    assert.strictEqual(afterReload.saved[seedSampleId].notes, 'Existing browser value must win over seed.');

    console.log('step:partial-import-merge');
    let importCard = page.locator('.sample-card').first();
    const importSampleId = await importCard.getAttribute('data-sample-id');
    importCard = page.locator(`.sample-card[data-sample-id="${importSampleId}"]`);
    await completeCard(importCard, 3, 'Before partial import.');
    await page.waitForTimeout(450);
    importPath = path.join(os.tmpdir(), `alexandria-round1-import-${process.pid}.json`);
    fs.writeFileSync(importPath, JSON.stringify({
      schema_version: 1,
      round_id: PUBLIC_DATA.round_id,
      export_scope: 'style',
      export_key: 'smoke',
      exported_at: '2100-01-01T00:00:00.000Z',
      revision: 1000,
      rows: [{
        sample_id: importSampleId,
        notes: 'Imported partial note.',
        flag_for_follow_up: false,
        updated_at: '2100-01-01T00:00:00.000Z',
        revision: 1000,
      }],
    }, null, 2));
    await page.locator('#import-results').setInputFiles(importPath);
    await page.locator('#import-dialog').waitFor({ state: 'visible' });
    assert.match(await page.locator('#import-summary').innerText(), /1 result rows merged/);
    await page.locator('#import-dialog button').click();
    assert.strictEqual(
      await page.locator(`.sample-card[data-sample-id="${importSampleId}"] textarea[data-field="notes"]`).inputValue(),
      'Imported partial note.',
    );
    assert.strictEqual(
      await page.locator(`.sample-card[data-sample-id="${importSampleId}"] input[data-field="identity_1_to_5"][value="3"]`).isChecked(),
      true,
    );
    const afterImport = await getSavedState(page);
    assert.strictEqual(afterImport.saved[importSampleId].delivery_1_to_5, 3);
    assert.strictEqual(afterImport.saved[importSampleId].notes, 'Imported partial note.');

    console.log('step:export-and-keyboard-navigation');
    const downloadPromise = page.waitForEvent('download', { timeout: 10000 });
    await page.locator('#export-style').click();
    const download = await downloadPromise;
    assert.match(download.suggestedFilename(), /alexandria_round1_style_/);
    await download.cancel();

    await page.locator('#group-navigation .nav-button').first().click();
    const firstTitle = await page.locator('#style-title').innerText();
    await page.locator('.style-header').click();
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(100);
    assert.notStrictEqual(await page.locator('#style-title').innerText(), firstTitle);
    assert.strictEqual(await page.locator('#identity-filter').inputValue(), 'benny');
    assert.ok((await page.locator('.sample-card').count()) <= 12);

    console.log('step:responsive');
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.waitForTimeout(150);
    const dimensions = await page.evaluate(() => {
      const card = document.querySelector('.sample-card').getBoundingClientRect();
      const identity = document.querySelector('#identity-filter').getBoundingClientRect();
      const toolbar = document.querySelector('.toolbar').getBoundingClientRect();
      return {
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
        cardLeft: card.left,
        cardRight: card.right,
        cardWidth: card.width,
        identityWidth: identity.width,
        toolbarHeight: toolbar.height,
        innerWidth: window.innerWidth,
      };
    });
    assert.ok(dimensions.scrollWidth <= dimensions.clientWidth + 1, JSON.stringify(dimensions));
    assert.ok(dimensions.cardLeft >= 0 && dimensions.cardRight <= dimensions.innerWidth + 1, JSON.stringify(dimensions));
    assert.ok(dimensions.cardWidth >= 500, JSON.stringify(dimensions));
    assert.ok(dimensions.identityWidth >= 180, JSON.stringify(dimensions));
    assert.ok(dimensions.toolbarHeight < 160, JSON.stringify(dimensions));
    await page.screenshot({ path: SCREENSHOT, fullPage: true });

    assert.deepStrictEqual(browserErrors, []);
    console.log(JSON.stringify({
      generatedSamples: 264,
      identityLanes: 5,
      styles: 10,
      nativeSamples: 0,
      initialCards: initialCardCount,
      seededRows: SEED.rows.length,
      seededCompleteRows: SEED.summary.complete_sample_count,
      scoredSampleId,
      importSampleId,
      screenshot: SCREENSHOT,
      browserErrors: 0,
    }, null, 2));
  } finally {
    if (importPath && fs.existsSync(importPath)) fs.unlinkSync(importPath);
    if (browser) await browser.close().catch(() => {});
    await closeServer();
  }
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
