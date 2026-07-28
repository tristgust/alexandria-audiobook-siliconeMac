const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const BASE_URL = process.env.ALEXANDRIA_AUDIT_URL || 'http://127.0.0.1:4200';
const OUTPUT_DIR = process.env.ALEXANDRIA_AUDIT_OUTPUT || '/tmp/alexandria-runtime-ui-audit';

const ASSETS = new Map([
  ['/static/canonical_interface.js', {
    body: fs.readFileSync(path.join(ROOT, 'app/static/canonical_interface.js')),
    contentType: 'application/javascript',
  }],
  ['/static/canonical_pages.css', {
    body: fs.readFileSync(path.join(ROOT, 'app/static/canonical_pages.css')),
    contentType: 'text/css',
  }],
  ['/static/navigation_routes.js', {
    body: fs.readFileSync(path.join(ROOT, 'app/static/navigation_routes.js')),
    contentType: 'application/javascript',
  }],
]);
const INDEX = fs.readFileSync(path.join(ROOT, 'app/static/index.html'));

function visibleInfo(id) {
  const element = document.getElementById(id);
  if (!element) return null;
  const rect = element.getBoundingClientRect();
  const style = getComputedStyle(element);
  return {
    hidden: element.hidden,
    inert: element.hasAttribute('inert'),
    display: style.display,
    visibility: style.visibility,
    top: rect.top,
    bottom: rect.bottom,
    width: rect.width,
    height: rect.height,
  };
}

async function installWorktreeFrontend(page, { delayCanonicalMs = 0 } = {}) {
  await page.route(`${BASE_URL}/`, async route => {
    await route.fulfill({
      status: 200,
      body: INDEX,
      contentType: 'text/html; charset=utf-8',
    });
  });
  await page.route(`${BASE_URL}/static/**`, async route => {
    const pathname = new URL(route.request().url()).pathname;
    const asset = ASSETS.get(pathname);
    if (!asset) {
      await route.continue();
      return;
    }
    if (pathname === '/static/canonical_interface.js' && delayCanonicalMs > 0) {
      await new Promise(resolve => setTimeout(resolve, delayCanonicalMs));
    }
    await route.fulfill({ status: 200, ...asset });
  });
}

async function createPage(browser, viewport, options = {}) {
  const page = await browser.newPage({ viewport });
  const errors = [];
  page.on('console', message => {
    if (message.type() === 'error') errors.push(`console: ${message.text()}`);
  });
  page.on('pageerror', error => errors.push(`pageerror: ${String(error)}`));
  await installWorktreeFrontend(page, options);
  return { page, errors };
}

async function auditPreboot(browser, destination) {
  const { page, errors } = await createPage(
    browser,
    { width: 1536, height: 1024 },
    { delayCanonicalMs: 1800 },
  );
  const navigation = page.goto(`${BASE_URL}/#/${destination}`, { waitUntil: 'commit' });
  await page.waitForTimeout(450);
  const before = await page.evaluate(() => {
    const body = document.body;
    return {
      readyState: document.readyState,
      preboot: document.documentElement.classList.contains('alexandria-preboot'),
      bodyVisibility: body ? getComputedStyle(body).visibility : null,
      destination: body?.dataset.destination || null,
      visibleHeading: [...document.querySelectorAll('h1, h2, h3')]
        .find(element => {
          const rect = element.getBoundingClientRect();
          const style = getComputedStyle(element);
          return rect.width > 0 && rect.height > 0
            && style.display !== 'none'
            && style.visibility !== 'hidden';
        })?.textContent.trim() || null,
    };
  });
  assert.equal(before.preboot, true, `${destination} must remain cloaked before canonical routing`);
  assert.equal(before.bodyVisibility, 'hidden', `${destination} body painted before routing`);
  assert.equal(before.visibleHeading, null, `${destination} exposed the wrong page before routing`);

  await navigation;
  await page.waitForLoadState('networkidle');
  const after = await page.evaluate(() => ({
    preboot: document.documentElement.classList.contains('alexandria-preboot'),
    bodyVisibility: getComputedStyle(document.body).visibility,
    destination: document.body.dataset.destination,
  }));
  assert.equal(after.preboot, false);
  assert.equal(after.bodyVisibility, 'visible');
  assert.equal(after.destination, destination);
  assert.deepEqual(errors, []);
  await page.close();
  return { destination, before, after };
}

async function auditSettingsDestinations(browser, viewport) {
  const { page, errors } = await createPage(browser, viewport);
  const results = [];
  const destinations = [
    ['stage_profiles', 'llm-profiles', 'maintenance-stage-profiles-section'],
    ['runtime_diagnostics', 'runtime', 'maintenance-runtime-section'],
    ['advanced_generation', 'advanced-generation', 'maintenance-advanced-generation-section'],
  ];
  for (const [key, mode, sectionId] of destinations) {
    await page.goto(`${BASE_URL}/#/settings`, { waitUntil: 'networkidle' });
    await page.locator(`[data-settings-destination="${key}"]`).click();
    await page.waitForFunction(
      expectedMode => new URLSearchParams(location.hash.split('?')[1] || '').get('mode') === expectedMode,
      mode,
    );
    await page.waitForFunction(
      section => document.getElementById(section)?.hidden === false,
      sectionId,
      { timeout: 15000 },
    );
    await page.waitForTimeout(100);
    const state = await page.evaluate(section => {
      const info = id => {
        const element = document.getElementById(id);
        if (!element) return null;
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return {
          hidden: element.hidden,
          inert: element.hasAttribute('inert'),
          display: style.display,
          visibility: style.visibility,
          top: rect.top,
          bottom: rect.bottom,
          width: rect.width,
          height: rect.height,
        };
      };
      return {
        hash: location.hash,
        destination: document.body.dataset.destination,
        canonicalMaintenance: info('canonical-maintenance-workspace'),
        legacySettings: info('legacy-settings-workspace'),
        technicalSection: info(section),
        oldProfiles: info('llm-profiles-panel'),
        oldRuntime: info('llm-runtime-panel'),
        oldAdvanced: info('promptSettings'),
        scrollY: window.scrollY,
      };
    }, sectionId);
    console.log(`SETTINGS_${key}=${JSON.stringify(state)}`);
    assert.equal(state.destination, 'more');
    assert.equal(state.canonicalMaintenance.hidden, false);
    assert.equal(state.legacySettings.hidden, true);
    assert.equal(state.legacySettings.inert, true);
    assert.equal(state.legacySettings.display, 'none');
    assert.equal(state.technicalSection.hidden, false);
    assert.ok(
      state.technicalSection.top >= 0
        && state.technicalSection.top < Math.min(360, viewport.height / 2),
      `${key} opened outside the visible Maintenance workspace: ${state.technicalSection.top}`,
    );
    for (const oldPanel of [state.oldProfiles, state.oldRuntime, state.oldAdvanced]) {
      assert.equal(oldPanel.width, 0, `${key} exposed a legacy Settings panel`);
      assert.equal(oldPanel.height, 0, `${key} exposed a legacy Settings panel`);
    }
    results.push({ key, mode, state });
  }
  assert.deepEqual(errors, []);
  await page.close();
  return { viewport, results };
}

async function auditCast(browser, viewport) {
  const { page, errors } = await createPage(browser, viewport);
  await page.goto(`${BASE_URL}/#/cast`, { waitUntil: 'networkidle' });
  await page.waitForSelector('#cast-detail-content:not([hidden])');
  const state = await page.evaluate(() => {
    const info = id => {
      const element = document.getElementById(id);
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return {
        hidden: element.hidden,
        inert: element.hasAttribute('inert'),
        display: style.display,
        visibility: style.visibility,
        top: rect.top,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
      };
    };
    const top = id => document.getElementById(id)?.getBoundingClientRect().top ?? null;
    return {
      destination: document.body.dataset.destination,
      cast: info('cast-workspace'),
      castLayout: (() => {
        const layout = document.querySelector('.cast-layout');
        return layout ? {
          ...infoFromElement(layout),
          gridTemplateColumns: getComputedStyle(layout).gridTemplateColumns,
        } : null;
      })(),
      castMaster: (() => {
        const master = document.querySelector('.cast-master');
        return master ? infoFromElement(master) : null;
      })(),
      legacyCharacters: info('character-workspace'),
      selectedName: document.getElementById('cast-detail-name')?.textContent.trim(),
      listCount: document.getElementById('cast-list-count')?.textContent.trim(),
      order: {
        voice: top('cast-voice-heading'),
        reference: top('cast-reference-heading'),
        preview: top('cast-preview-heading'),
        character: top('cast-character-summary-heading'),
        appearance: top('cast-appearance-summary-heading'),
        advanced: top('cast-advanced-disclosure'),
      },
      characterSummary: info('cast-character-summary'),
      appearanceSummary: info('cast-appearance-summary'),
      firstRow: (() => {
        const row = document.querySelector('.cast-character-row');
        const role = row?.querySelector('.cast-character-role');
        const status = row?.querySelector('.cast-character-status');
        return row ? {
          gridTemplateColumns: getComputedStyle(row).gridTemplateColumns,
          row: infoFromElement(row),
          role: role ? {
            text: role.textContent.trim(),
            rect: infoFromElement(role),
          } : null,
          status: status ? {
            text: status.textContent.trim(),
            rect: infoFromElement(status),
          } : null,
        } : null;
      })(),
    };

    function infoFromElement(element) {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return {
        display: style.display,
        visibility: style.visibility,
        top: rect.top,
        left: rect.left,
        width: rect.width,
        height: rect.height,
      };
    }
  });
  assert.equal(state.destination, 'cast');
  assert.equal(state.cast.hidden, false);
  assert.equal(state.legacyCharacters.hidden, true);
  assert.equal(state.legacyCharacters.inert, true);
  assert.equal(state.legacyCharacters.display, 'none');
  assert.ok(state.selectedName);
  assert.match(state.listCount, /characters$/);
  const order = Object.values(state.order);
  for (let index = 1; index < order.length; index += 1) {
    assert.ok(order[index] >= order[index - 1], `Cast section order is wrong: ${JSON.stringify(state.order)}`);
  }
  assert.ok(state.characterSummary.height > 0, 'Character summary is not visible');
  assert.ok(state.appearanceSummary.height > 0, 'Appearance summary is not visible');

  const suffix = `${viewport.width}x${viewport.height}`;
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(OUTPUT_DIR, `cast-${suffix}.png`), fullPage: false });

  await page.locator('#cast-more-actions').click();
  await page.waitForFunction(() => location.hash.includes('advanced-character-operations'));
  const route = await page.evaluate(() => location.hash);
  assert.match(route, /^#\/more\?/);
  assert.match(route, /tool=advanced-character-operations/);
  assert.match(route, /character=/);
  assert.deepEqual(errors, []);
  await page.close();
  return { viewport, state, route };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const results = {
      preboot: [
        await auditPreboot(browser, 'settings'),
        await auditPreboot(browser, 'cast'),
      ],
      settings: [
        await auditSettingsDestinations(browser, { width: 1536, height: 1024 }),
        await auditSettingsDestinations(browser, { width: 1024, height: 768 }),
      ],
      cast: [
        await auditCast(browser, { width: 1536, height: 1024 }),
        await auditCast(browser, { width: 1024, height: 768 }),
      ],
    };
    console.log(JSON.stringify(results, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
