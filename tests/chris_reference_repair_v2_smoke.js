const assert = require('assert');
const { chromium } = require('playwright');

const URL = process.env.CHRIS_REPAIR_V2_URL || 'http://127.0.0.1:8880/';
const CHROME = process.env.CHROME_EXECUTABLE_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: CHROME });
  const results = [];
  try {
    for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
      const context = await browser.newContext({ viewport, acceptDownloads: true });
      const page = await context.newPage();
      const errors = [];
      page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(URL, { waitUntil: 'domcontentloaded' });
      await page.locator('.pair').first().waitFor();
      assert.strictEqual(await page.locator('.pair').count(), 12);
      assert.strictEqual(await page.locator('.pair audio').count(), 36);
      assert.strictEqual(await page.locator('input[type="radio"]').count(), 36);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
      assert.strictEqual(overflow, false);
      const first = page.locator('.pair').first();
      await first.locator('input[value="tie"]').check();
      await first.locator('textarea').fill('smoke');
      const saved = await page.evaluate(() => Object.values(JSON.parse(localStorage.getItem('chris-repair-pairs:alexandria_chris_reference_repair_pairwise_v2') || '{}'))[0]);
      assert.strictEqual(saved.choice, 'tie');
      assert.strictEqual(saved.notes, 'smoke');
      const downloadPromise = page.waitForEvent('download');
      await page.locator('#export').click();
      const download = await downloadPromise;
      assert.ok(download.suggestedFilename().includes('alexandria_chris_reference_repair_pairwise_v2'));
      assert.deepStrictEqual(errors, []);
      results.push({ viewport, status: 'PASS' });
      await context.close();
    }
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify({ status: 'PASS', results }, null, 2));
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
