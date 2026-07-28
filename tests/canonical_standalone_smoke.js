const { chromium } = require('playwright');

const BASE_URL = process.env.ALEXANDRIA_TEST_URL || 'http://127.0.0.1:4201';
const DIRECT_DESTINATIONS = {
  projects: 'project-home-workspace',
  script: 'script-review-workspace',
  cast: 'cast-workspace',
  produce: 'produce-workspace',
  export: 'export-workspace',
  library: 'library-workspace',
  voices: 'library-workspace',
  templates: 'templates-workspace',
  settings: 'canonical-settings-workspace',
};

function assert(condition, message, context = {}) {
  if (!condition) {
    const error = new Error(message);
    error.context = context;
    throw error;
  }
}

async function inspectDestination(page, destination, workspaceId) {
  await page.goto(`${BASE_URL}/#/${destination}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(({ expected, workspaceId }) => {
    const workspace = document.getElementById(workspaceId);
    return document.body?.dataset.destination === expected
      && !document.documentElement.classList.contains('alexandria-booting')
      && workspace?.parentElement?.id === 'canonical-destination-root'
      && workspace.hidden === false
      && getComputedStyle(workspace).display !== 'none';
  }, { expected: destination, workspaceId });
  const result = await page.evaluate(({ destination, workspaceId }) => {
    const workspace = document.getElementById(workspaceId);
    const legacyTabs = [...document.querySelectorAll('.tab-content')].map(tab => ({
      id: tab.id,
      hidden: tab.hidden,
      inert: tab.hasAttribute('inert'),
      display: getComputedStyle(tab).display,
    }));
    return {
      destination: document.body.dataset.destination,
      booting: document.documentElement.classList.contains('alexandria-booting'),
      rootId: workspace?.parentElement?.id || null,
      workspaceHidden: workspace?.hidden,
      workspaceDisplay: workspace ? getComputedStyle(workspace).display : null,
      legacyTabs,
    };
  }, { destination, workspaceId });
  assert(result.destination === destination, 'Destination did not render canonically.', result);
  assert(result.rootId === 'canonical-destination-root', 'Canonical workspace is still nested in a legacy tab.', result);
  assert(result.workspaceHidden === false && result.workspaceDisplay !== 'none', 'Canonical workspace is not visible.', result);
  assert(result.booting === false, 'Boot cloak was not released.', result);
  assert(result.legacyTabs.every(tab => tab.hidden && tab.inert && tab.display === 'none'), 'A legacy tab is active for a canonical route.', result);
  return result;
}

async function delayedBootstrap(browser) {
  const page = await browser.newPage({ viewport: { width: 1536, height: 1024 } });
  await page.route('**/static/canonical_interface.js', async route => {
    await new Promise(resolve => setTimeout(resolve, 1800));
    await route.continue();
  });
  const navigation = page.goto(`${BASE_URL}/#/cast`, { waitUntil: 'commit' });
  await page.waitForTimeout(600);
  const before = await page.evaluate(() => ({
    readyState: document.readyState,
    booting: document.documentElement.classList.contains('alexandria-booting'),
    bodyVisibility: getComputedStyle(document.body).visibility,
    visibleHeadings: [...document.querySelectorAll('h1, h2, h3')]
      .filter(element => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden';
      })
      .map(element => element.textContent.trim()),
  }));
  assert(before.booting === true, 'Delayed bootstrap did not retain the boot gate.', before);
  assert(before.bodyVisibility === 'hidden', 'The wrong page can still paint before canonical routing.', before);
  await navigation;
  await page.waitForLoadState('domcontentloaded');
  await page.waitForFunction(() => !document.documentElement.classList.contains('alexandria-booting'));
  const after = await page.evaluate(() => ({
    destination: document.body.dataset.destination,
    booting: document.documentElement.classList.contains('alexandria-booting'),
    bodyVisibility: getComputedStyle(document.body).visibility,
  }));
  assert(after.destination === 'cast' && after.booting === false && after.bodyVisibility === 'visible', 'Cast did not reveal after canonical bootstrap.', after);
  await page.close();
  return { before, after };
}

async function settingsAdvanced(page) {
  await page.goto(`${BASE_URL}/#/settings`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.body?.dataset.destination === 'settings');
  const button = page.locator('[data-settings-destination="stage_profiles"]');
  await button.scrollIntoViewIfNeeded();
  await button.click();
  await page.waitForFunction(() => (
    document.body?.dataset.destination === 'more'
    && document.getElementById('maintenance-stage-profiles-section')?.hidden === false
  ));
  await page.waitForTimeout(100);
  const result = await page.evaluate(() => {
    const maintenance = document.getElementById('canonical-maintenance-workspace');
    const section = document.getElementById('maintenance-stage-profiles-section');
    const panel = document.getElementById('llm-profiles-panel');
    const legacy = document.getElementById('legacy-settings-workspace');
    return {
      hash: window.location.hash,
      destination: document.body.dataset.destination,
      maintenanceParent: maintenance?.parentElement?.id || null,
      maintenanceHidden: maintenance?.hidden,
      sectionHidden: section?.hidden,
      sectionTop: section?.getBoundingClientRect().top,
      panelParent: panel?.parentElement?.id || null,
      legacyHidden: legacy?.hidden,
      legacyInert: legacy?.hasAttribute('inert'),
      legacyDisplay: legacy ? getComputedStyle(legacy).display : null,
    };
  });
  assert(result.destination === 'more', 'Advanced Settings did not route to Maintenance.', result);
  assert(result.maintenanceParent === 'canonical-destination-root' && result.maintenanceHidden === false, 'Maintenance is not standalone.', result);
  assert(result.sectionHidden === false && result.panelParent === 'maintenance-stage-profiles-slot', 'Stage profiles did not mount in canonical Maintenance.', result);
  assert(result.legacyHidden && result.legacyInert && result.legacyDisplay === 'none', 'Legacy Settings became visible.', result);
  assert(result.sectionTop >= 0 && result.sectionTop < 260, 'Maintenance subpage is positioned outside the expected viewport start.', result);
  return result;
}

async function castStructure(page) {
  await page.goto(`${BASE_URL}/#/cast`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.body?.dataset.destination === 'cast');
  const result = await page.evaluate(() => {
    const canonical = document.getElementById('cast-workspace');
    const legacy = document.getElementById('character-workspace');
    const title = document.getElementById('shell-page-title');
    const listTitle = document.getElementById('cast-characters-heading');
    const order = [
      'cast-voice-heading',
      'cast-reference-heading',
      'cast-preview-heading',
      'cast-character-summary-disclosure',
      'cast-appearance-summary-disclosure',
      'cast-advanced-disclosure',
    ].map(id => document.getElementById(id)?.getBoundingClientRect().top ?? null);
    return {
      canonicalParent: canonical?.parentElement?.id || null,
      legacyHidden: legacy?.hidden,
      legacyInert: legacy?.hasAttribute('inert'),
      legacyDisplay: legacy ? getComputedStyle(legacy).display : null,
      pageTitle: title?.textContent.trim(),
      pageTitleVisible: title ? getComputedStyle(title).display !== 'none' : false,
      listTitle: listTitle?.textContent.trim(),
      layoutColumns: canonical ? getComputedStyle(canonical.querySelector('.cast-layout')).gridTemplateColumns : null,
      order,
    };
  });
  assert(result.canonicalParent === 'canonical-destination-root', 'Cast is not standalone.', result);
  assert(result.legacyHidden && result.legacyInert && result.legacyDisplay === 'none', 'The old Characters workspace participates in Cast.', result);
  assert(result.pageTitle === 'Cast' && result.pageTitleVisible, 'The required Cast page title is not visible.', result);
  assert(result.listTitle === 'Characters', 'The required Characters section title is missing.', result);
  const positions = result.order.filter(value => Number.isFinite(value));
  assert(positions.every((value, index) => index === 0 || value >= positions[index - 1]), 'Cast inspector section order is wrong.', result);
  return result;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1536, height: 1024 } });
  const consoleErrors = [];
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('pageerror', error => consoleErrors.push(String(error)));

  const bootstrap = await delayedBootstrap(browser);
  const destinations = {};
  for (const [destination, workspaceId] of Object.entries(DIRECT_DESTINATIONS)) {
    destinations[destination] = await inspectDestination(page, destination, workspaceId);
  }
  const settings = await settingsAdvanced(page);
  const cast = await castStructure(page);
  assert(consoleErrors.length === 0, 'Browser console errors occurred.', { consoleErrors });

  console.log(JSON.stringify({ bootstrap, destinations, settings, cast, consoleErrors }, null, 2));
  await page.close();
  await browser.close();
})().catch(error => {
  console.error(error.message);
  if (error.context) console.error(JSON.stringify(error.context, null, 2));
  process.exitCode = 1;
});
