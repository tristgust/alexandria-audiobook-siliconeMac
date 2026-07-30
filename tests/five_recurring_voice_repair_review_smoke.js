'use strict';

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const URL = process.env.REVIEW_URL || 'http://127.0.0.1:8882/review/';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function runViewport(browser, viewport) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('pageerror', (error) => consoleErrors.push(String(error)));
  await page.goto(URL, { waitUntil: 'networkidle' });
  assert(await page.locator('.group').count() === 2, 'Expected two repair groups.');
  assert(await page.locator('.candidate').count() === 9, 'Expected nine repair candidates.');
  assert(await page.locator('audio').count() === 9, 'Expected nine audio players.');
  const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
  assert(bodyWidth <= viewport.width + 1, `Horizontal overflow at ${viewport.width}px.`);
  assert(consoleErrors.length === 0, `Browser console errors: ${consoleErrors.join(' | ')}`);
  await context.close();
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: CHROME,
  });
  try {
    await runViewport(browser, { width: 1280, height: 900 });
    await runViewport(browser, { width: 390, height: 844 });

    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    await page.goto(URL, { waitUntil: 'networkidle' });
    const first = page.locator('.candidate').first();
    await first.getByLabel('Identity').selectOption('4');
    await first.getByLabel('Audio quality').selectOption('5');
    await first.getByLabel('Delivery fit').selectOption('4');
    await first.getByLabel('Pass').check();
    await first.locator('textarea').fill('smoke persistence');
    await page.reload({ waitUntil: 'networkidle' });
    const restored = page.locator('.candidate').first();
    assert(await restored.getByLabel('Identity').inputValue() === '4', 'Autosaved identity score was lost.');
    assert(await restored.getByLabel('Audio quality').inputValue() === '5', 'Autosaved quality score was lost.');
    assert(await restored.getByLabel('Delivery fit').inputValue() === '4', 'Autosaved delivery score was lost.');
    assert(await restored.getByLabel('Pass').isChecked(), 'Autosaved decision was lost.');
    assert(await restored.locator('textarea').inputValue() === 'smoke persistence', 'Autosaved note was lost.');

    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Export review' }).click();
    const download = await downloadPromise;
    const downloadPath = path.join('/tmp', `five-recurring-repair-${Date.now()}.json`);
    await download.saveAs(downloadPath);
    const payload = JSON.parse(fs.readFileSync(downloadPath, 'utf8'));
    assert(payload.round_id === 'alexandria_five_recurring_voice_repair_v1', 'Wrong export round.');
    const rows = Object.values(payload.results);
    assert(rows.some((row) => row.decision === 'pass'), 'Export omitted saved decision.');
    fs.unlinkSync(downloadPath);
    await context.close();
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify({ status: 'PASS', viewports: ['1280x900', '390x844'], candidates: 9 }));
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
