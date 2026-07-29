const {
  EVIDENCE_ROOT,
  REVIEW_ROOT,
  assert,
  assertResponsiveLayouts,
  captureClearDrawer,
  captureRejectedImport,
  chromium,
  closeServer,
  fs,
  importPayloads,
  navigate,
  path,
  scoreCard,
  server,
  storedState,
} = require('./multimodel_round1_review_smoke_support');

(async () => {
  let browser;
  const results = [];
  const consoleErrors = [];
  const check = async (name, action) => {
    try {
      await action();
      results.push({ name, status: 'pass' });
      console.log(`PASS ${name}`);
    } catch (error) {
      results.push({ name, status: 'fail', message: error.message });
      console.error(`FAIL ${name}: ${error.message}`);
    }
  };

  try {
    fs.mkdirSync(EVIDENCE_ROOT, { recursive: true });
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const baseUrl = `http://127.0.0.1:${server.address().port}`;
    browser = await chromium.launch({
      headless: true,
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
        || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    });
    const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    page.on('pageerror', (error) => consoleErrors.push(error.message));

    await check('public data drives counts, planned progress, and references', async () => {
      await navigate(page, baseUrl, 'counts');
      const publicData = await page.evaluate(() => window.ALEXANDRIA_ROUND1_DATA);
      const manifest = JSON.parse(await fs.promises.readFile(path.join(REVIEW_ROOT, 'manifest.json'), 'utf8'));
      const ready = publicData.samples.filter((sample) => sample.status === 'ready' && sample.audio).length;
      const pending = publicData.samples.length - ready;
      assert.strictEqual(manifest.group_count, Object.keys(publicData.groups).length);
      assert.strictEqual(manifest.style_count, publicData.styles.length);
      assert.strictEqual(manifest.generated_sample_count, ready);
      assert.strictEqual(await page.locator('#group-navigation .nav-button').count(), manifest.group_count);
      const styleTitle = await page.locator('#style-title').innerText();
      const activeStyle = publicData.styles.find((style) => style.label === styleTitle);
      const group = publicData.groups[activeStyle.group];
      assert.strictEqual(await page.locator('#style-navigation .nav-button').count(), group.styles.length);
      assert.match(await page.locator('#overall-generated').innerText(), new RegExp(`${ready} ready.*${pending} pending.*${publicData.samples.length} planned`));
      const styleKey = activeStyle.key;
      const referenceKeys = [...new Set(publicData.samples.filter((sample) => sample.style === styleKey && sample.status === 'ready' && sample.audio).map((sample) => sample.identity_reference_key))];
      const referenceAudio = referenceKeys.flatMap((key) => [...new Set([publicData.identities[key]?.original_audio, publicData.identities[key]?.conditioning_audio].filter(Boolean))]);
      const expectedAudio = referenceAudio.length;
      assert.strictEqual(await page.locator('.reference-card audio').count(), expectedAudio);
      assert.strictEqual(await page.locator('audio:not([aria-label])').count(), 0);
      referenceAudio.forEach((audio) => assert.ok(fs.existsSync(path.join(REVIEW_ROOT, audio)), audio));
    });

    await check('autosave shows pending state and is round, reviewer, and session isolated', async () => {
      await navigate(page, baseUrl, 'profile-a');
      const data = await page.evaluate(() => window.ALEXANDRIA_ROUND1_DATA);
      const card = page.locator('.sample-card').first();
      const sampleId = await card.getAttribute('data-sample-id');
      await card.locator('.saved-indicator').scrollIntoViewIfNeeded();
      await card.locator('textarea[data-field="notes"]').fill('Autosave pending evidence.');
      assert.match(await card.locator('.saved-indicator').innerText(), /Saving/);
      await page.screenshot({ path: path.join(EVIDENCE_ROOT, 'autosave-pending-1280.png') });
      await scoreCard(card);
      await page.waitForFunction((id) => document.querySelector(`[data-sample-id="${CSS.escape(id)}"] .saved-indicator`)?.textContent === 'Saved', sampleId);
      assert.strictEqual(await card.locator('.saved-indicator').innerText(), 'Saved');
      const stored = await storedState(page, sampleId);
      assert.ok(stored.key.includes(encodeURIComponent(data.round_id)), stored.key);
      assert.ok(stored.key.includes('profile-a'), stored.key);
      assert.ok(stored.key.includes('profile-a-session'), stored.key);
      assert.strictEqual(stored.row.identity_1_to_5, 4);
      assert.ok(Number.isInteger(stored.row.revision) && stored.row.revision > 0);
      assert.match(await card.locator('.status-pill').innerText(), /Reviewed/);
      assert.match(await page.locator('#overall-progress').innerText(), /^1 \/ /);
      await navigate(page, baseUrl, 'profile-a', 'profile-a-other-session');
      assert.strictEqual(await page.locator(`.sample-card[data-sample-id="${sampleId}"] input[data-field="identity_1_to_5"]:checked`).count(), 0);
      await page.evaluate(({ roundId, id }) => {
        localStorage.setItem(`alexandria-round1-review:${roundId}`, JSON.stringify({ [id]: { sample_id: id, identity_1_to_5: 5 } }));
      }, { roundId: data.round_id, id: sampleId });
      await navigate(page, baseUrl, 'default', 'isolated-session');
      assert.strictEqual(await page.locator(`.sample-card[data-sample-id="${sampleId}"] input[data-field="identity_1_to_5"]:checked`).count(), 0);
    });

    await check('shortcuts are documented and long-page references open in a persistent drawer', async () => {
      await navigate(page, baseUrl, 'reference-drawer');
      assert.match(await page.locator('#keyboard-shortcuts').innerText(), /Arrow|←/);
      assert.match(await page.locator('#keyboard-shortcuts').innerText(), /N/);
      assert.strictEqual(await page.locator('link[rel="icon"]').count(), 1);
      await page.locator('#reference-toggle').click();
      await captureClearDrawer(page);
      await page.locator('#close-reference-drawer').click();
      await page.locator('#keyboard-shortcuts').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(EVIDENCE_ROOT, 'keyboard-shortcuts-1280.png') });
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await page.locator('#reference-toggle').hover();
      await page.screenshot({ path: path.join(EVIDENCE_ROOT, 'reference-button-hover-1280.png') });
      await page.locator('#reference-toggle').click();
      assert.ok(await page.locator('#reference-panel').evaluate((panel) => panel.classList.contains('docked')));
      assert.ok(await page.locator('#reference-panel audio').first().isVisible());
      await page.screenshot({ path: path.join(EVIDENCE_ROOT, 'reference-drawer-1280.png') });
      await page.locator('#close-reference-drawer').click();
      assert.ok(await page.locator('#reference-panel').evaluate((panel) => !panel.classList.contains('docked')));
    });

    await check('import merge prefers newer timestamp and revision regardless of file order', async () => {
      await navigate(page, baseUrl, 'import-conflict');
      const { roundId, sampleIds } = await page.evaluate(() => ({
        roundId: window.ALEXANDRIA_ROUND1_DATA.round_id,
        sampleIds: [...document.querySelectorAll('.sample-card')].slice(0, 3).map((card) => card.dataset.sampleId),
      }));
      const payload = (updatedAt, revision, rows) => ({ schema_version: 1, round_id: roundId, exported_at: updatedAt, revision, rows });
      await importPayloads(page, [
        { name: 'newer.json', payload: payload('2030-01-02T00:00:00.000Z', 7, [{ sample_id: sampleIds[0], updated_at: '2030-01-02T00:00:00.000Z', revision: 7, notes: 'newer result', identity_1_to_5: 5 }]) },
        { name: 'older-partial.json', payload: payload('2030-01-01T00:00:00.000Z', 3, [{ sample_id: sampleIds[0], updated_at: '2030-01-01T00:00:00.000Z', revision: 3, notes: 'older partial' }]) },
        { name: 'same-time-high-revision.json', payload: payload('2030-02-01T00:00:00.000Z', 9, [{ sample_id: sampleIds[1], updated_at: '2030-02-01T00:00:00.000Z', revision: 9, notes: 'revision nine' }]) },
        { name: 'same-time-low-revision.json', payload: payload('2030-02-01T00:00:00.000Z', 2, [{ sample_id: sampleIds[1], updated_at: '2030-02-01T00:00:00.000Z', revision: 2, notes: 'revision two' }]) },
        { name: 'reverse-older-first.json', payload: payload('2030-03-01T00:00:00.000Z', 1, [{ sample_id: sampleIds[2], updated_at: '2030-03-01T00:00:00.000Z', revision: 1, notes: 'reverse older' }]) },
        { name: 'reverse-newer-last.json', payload: payload('2030-03-02T00:00:00.000Z', 2, [{ sample_id: sampleIds[2], updated_at: '2030-03-02T00:00:00.000Z', revision: 2, notes: 'reverse newer' }]) },
        { name: 'wrong-round.json', payload: payload('2040-01-01T00:00:00.000Z', 99, [{ sample_id: sampleIds[0], updated_at: '2040-01-01T00:00:00.000Z', revision: 99, notes: 'wrong round' }]) },
      ].map((item) => item.name === 'wrong-round.json' ? { ...item, payload: { ...item.payload, round_id: `${roundId}-other` } } : item));
      assert.strictEqual((await storedState(page, sampleIds[0])).row.notes, 'newer result');
      assert.strictEqual((await storedState(page, sampleIds[1])).row.notes, 'revision nine');
      assert.strictEqual((await storedState(page, sampleIds[2])).row.notes, 'reverse newer');
      assert.match(await page.locator('#import-summary').innerText(), /older|conflict/i);
      await fs.promises.writeFile(path.join(EVIDENCE_ROOT, 'import-conflict-observable.json'), JSON.stringify({ sampleIds, winnerByTimestamp: 'newer result', winnerByRevision: 'revision nine', winnerWhenNewerIsLast: 'reverse newer' }, null, 2));
    });

    await check('malformed import rows cannot mark a sample reviewed', async () => {
      await navigate(page, baseUrl, 'import-malformed');
      const { roundId, sampleId } = await page.evaluate(() => ({
        roundId: window.ALEXANDRIA_ROUND1_DATA.round_id,
        sampleId: document.querySelector('.sample-card').dataset.sampleId,
      }));
      await importPayloads(page, [{
        name: 'malformed.json',
        payload: {
          schema_version: 1,
          round_id: roundId,
          exported_at: '2030-04-01T00:00:00.000Z',
          revision: 1,
          rows: [{
            sample_id: sampleId,
            identity_1_to_5: 999,
            delivery_1_to_5: 'bad',
            naturalness_1_to_5: -10,
            artifact_severity_1_to_5: 6,
            spoken_text_matches_expected: 'yes',
            requested_mode_is_clear: 1,
            approve_for_comparison: {},
          }],
        },
      }]);
      const card = page.locator(`.sample-card[data-sample-id="${sampleId}"]`);
      await captureRejectedImport(page, card);
    });

    await check('next incomplete terminates for a completed identity filter', async () => {
      await navigate(page, baseUrl, 'next-incomplete');
      const firstCard = page.locator('.sample-card').first();
      const sampleId = await firstCard.getAttribute('data-sample-id');
      await firstCard.locator('input[data-field="identity_1_to_5"][value="4"]').check();
      await page.waitForFunction((id) => document.querySelector(`[data-sample-id="${CSS.escape(id)}"] .saved-indicator`)?.textContent === 'Saved', sampleId);
      const stored = await storedState(page, sampleId);
      const setup = await page.evaluate(({ key, id }) => {
        const data = window.ALEXANDRIA_ROUND1_DATA;
        const identity = data.samples.find((sample) => sample.sample_id === id).identity_key;
        const rows = {};
        for (const sample of data.samples.filter((item) => item.identity_key === identity && item.status === 'ready' && item.audio)) {
          rows[sample.sample_id] = { sample_id: sample.sample_id, updated_at: '2031-01-01T00:00:00.000Z', revision: 1, identity_1_to_5: 4, delivery_1_to_5: 4, naturalness_1_to_5: 4, artifact_severity_1_to_5: 1, spoken_text_matches_expected: true, requested_mode_is_clear: true, approve_for_comparison: true };
        }
        localStorage.setItem(key, JSON.stringify(rows));
        return { identity, count: Object.keys(rows).length };
      }, { key: stored.key, id: sampleId });
      assert.ok(setup.count > 0);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.locator('.sample-card').first().waitFor();
      await page.locator('#identity-filter').selectOption(setup.identity);
      const title = await page.locator('#style-title').innerText();
      await page.locator('#next-incomplete').click();
      await page.locator('#notice').waitFor({ state: 'visible' });
      assert.strictEqual(await page.locator('#style-title').innerText(), title);
      assert.match(await page.locator('#notice').innerText(), /identity|filter|reviewed/i);
      assert.ok(await page.locator('#notice').isVisible());
    });

    await check('keyboard navigation is guarded while audio has focus', async () => {
      await navigate(page, baseUrl, 'keyboard');
      const title = await page.locator('#style-title').innerText();
      await page.locator('#reference-toggle').focus();
      assert.strictEqual(await page.locator('#reference-toggle').evaluate((button) => getComputedStyle(button).outlineColor), 'rgb(49, 92, 85)');
      await page.screenshot({ path: path.join(EVIDENCE_ROOT, 'reference-button-focus-1280.png') });
      await page.locator('.sample-card audio').first().focus();
      await page.keyboard.press('ArrowRight');
      assert.strictEqual(await page.locator('#style-title').innerText(), title);
      await page.locator('.reference-card audio').first().focus();
      await page.screenshot({ path: path.join(EVIDENCE_ROOT, 'reference-audio-focus-1280.png') });
      await page.keyboard.press('n');
      assert.ok(await page.evaluate(() => document.activeElement?.closest('.reference-card') !== null));
    });

    await check('exports preserve style, group, and cumulative partial behavior', async () => {
      await navigate(page, baseUrl, 'exports');
      const card = page.locator('.sample-card').first();
      await scoreCard(card);
      await card.locator('textarea[data-field="notes"]').fill('Export evidence note.');
      for (const [button, scope] of [['#export-style', 'style'], ['#export-group', 'group'], ['#export-all', 'cumulative']]) {
        const downloadPromise = page.waitForEvent('download');
        await page.locator(button).click();
        const download = await downloadPromise;
        const target = path.join(EVIDENCE_ROOT, `export-${scope}.json`);
        await download.saveAs(target);
        const exported = JSON.parse(await fs.promises.readFile(target, 'utf8'));
        assert.strictEqual(exported.export_scope, scope);
        assert.strictEqual(exported.rows.length, 1);
        assert.ok(exported.rows.some((row) => row.notes === 'Export evidence note.'));
      }
    });

    await check('desktop and tablet layouts have no overflow and use measured sticky offsets', async () => {
      await navigate(page, baseUrl, 'layout');
      await assertResponsiveLayouts(page);
    });

    await check('browser console is clean', async () => assert.deepStrictEqual(consoleErrors, []));
    await fs.promises.writeFile(path.join(EVIDENCE_ROOT, 'smoke-results.json'), JSON.stringify(results, null, 2));
    const failures = results.filter((result) => result.status === 'fail');
    if (failures.length) throw new Error(`${failures.length} smoke checks failed: ${failures.map((failure) => failure.name).join(', ')}`);
  } finally {
    if (browser) await browser.close().catch(() => {});
    await closeServer();
    await fs.promises.mkdir(EVIDENCE_ROOT, { recursive: true });
    await fs.promises.writeFile(path.join(EVIDENCE_ROOT, 'cleanup.json'), JSON.stringify({ browserClosed: true, serverListening: server.listening, recordedAt: new Date().toISOString() }, null, 2));
  }
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
