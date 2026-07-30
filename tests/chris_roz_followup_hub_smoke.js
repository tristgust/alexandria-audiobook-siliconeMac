const { chromium } = require('playwright');
const assert = require('assert');

const BASE = 'http://127.0.0.1:8879';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

const cases = [
  { path: '/source-repair/', selector: '.card', count: 11, audio: 11, interact: async (page) => {
    const card = page.locator('.card').first();
    await card.locator('select[data-name="dryness"]').selectOption('4');
    await card.locator('select[data-name="identity"]').selectOption('5');
    await card.locator('select[data-name="naturalness"]').selectOption('4');
  } },
  { path: '/repair-validation/', selector: '.pair', count: 12, audio: 36, interact: async (page) => {
    await page.locator('.pair').first().locator('input[value="a"]').check();
  } },
  { path: '/model-tiebreakers/', selector: '#pair .card', count: 1, audio: 3, interact: async (page) => {
    const dataCount = await page.evaluate(() => window.CHRIS_ROZ_PAIRWISE.pairs.length);
    assert.strictEqual(dataCount, 4);
    await page.locator('#pair .card').first().locator('input[value="a"]').check();
  } },
  { path: '/urgency-controls/', selector: '.card', count: 12, audio: 24, interact: async (page) => {
    const card = page.locator('.card').first();
    await card.locator('select[data-name="identity"]').selectOption('5');
    await card.locator('select[data-name="delivery"]').selectOption('4');
    await card.locator('select[data-name="naturalness"]').selectOption('4');
    await card.locator('select[data-name="artifacts"]').selectOption('1');
  } },
];

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
      await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
      assert.strictEqual(await page.locator('.card').count(), 4);
      assert.ok((await page.locator('body').innerText()).includes('Chris and Roz follow-up reviews'));
      assert.ok((await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)) <= 1);
      for (const test of cases) {
        await page.goto(BASE + test.path, { waitUntil: 'domcontentloaded' });
        await page.locator(test.selector).first().waitFor();
        assert.strictEqual(await page.locator(test.selector).count(), test.count, test.path);
        assert.strictEqual(await page.locator('audio').count(), test.audio, test.path);
        const statuses = await page.locator('audio').evaluateAll(async nodes => Promise.all(nodes.slice(0, 4).map(async node => (await fetch(node.src)).status)));
        assert.ok(statuses.every(status => status === 200));
        await test.interact(page);
        await page.waitForTimeout(100);
        assert.ok((await page.evaluate(() => Object.keys(localStorage).length)) > 0);
        assert.ok((await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)) <= 1, test.path);
        assert.strictEqual(errors.length, 0, `${test.path}: ${errors.join('; ')}`);
      }
      results.push({ viewport, status: 'PASS' });
      await context.close();
    }
    console.log(JSON.stringify({ status: 'PASS', results }, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.stack || error); process.exit(1); });
