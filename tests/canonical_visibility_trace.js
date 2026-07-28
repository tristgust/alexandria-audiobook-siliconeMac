const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1536, height: 1024 } });
  const events = [];
  page.on('console', message => events.push({ type: `console:${message.type()}`, text: message.text() }));
  page.on('pageerror', error => events.push({ type: 'pageerror', text: String(error), stack: error.stack }));
  await page.addInitScript(() => {
    const descriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'hidden');
    if (!descriptor?.set || !descriptor?.get) return;
    Object.defineProperty(HTMLElement.prototype, 'hidden', {
      configurable: descriptor.configurable,
      enumerable: descriptor.enumerable,
      get: descriptor.get,
      set(value) {
        if (['project-home-workspace', 'canonical-destination-root'].includes(this.id)) {
          window.__alexandriaVisibilityTrace ||= [];
          window.__alexandriaVisibilityTrace.push({
            id: this.id,
            value: Boolean(value),
            destination: document.body?.dataset.destination || null,
            stack: new Error().stack,
          });
        }
        return descriptor.set.call(this, value);
      },
    });
  });
  await page.goto('http://127.0.0.1:4201/#/projects', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3500);
  const state = await page.evaluate(() => {
    const project = document.getElementById('project-home-workspace');
    const root = document.getElementById('canonical-destination-root');
    return {
      destination: document.body?.dataset.destination || null,
      booting: document.documentElement.classList.contains('alexandria-booting'),
      projectHidden: project?.hidden,
      projectDisplay: project ? getComputedStyle(project).display : null,
      rootHidden: root?.hidden,
      rootDisplay: root ? getComputedStyle(root).display : null,
      trace: window.__alexandriaVisibilityTrace || [],
    };
  });
  console.log(JSON.stringify({ state, events }, null, 2));
  await browser.close();
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
