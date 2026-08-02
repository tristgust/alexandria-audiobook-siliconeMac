'use strict';

const fs = require('fs');
const path = require('path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');
const { fixtureServer } = require('./produce_export_fixture_server.js');

const VIEWPORTS = [[390, 844], [768, 1024], [1024, 768], [1536, 1024]];

function pageUrl(baseUrl, hash) {
  const target = new URL(baseUrl);
  target.hash = hash;
  return target.href;
}

async function settle(session) {
  await session.evaluate(`new Promise((resolve) => requestAnimationFrame(
    () => requestAnimationFrame(() => resolve(true))
  ))`);
}

async function physicalKey(session, key, code = key) {
  const printable = key === 'Enter' ? '\r' : key === ' ' ? ' ' : undefined;
  const event = { key, code };
  if (printable !== undefined) {
    event.text = printable;
    event.unmodifiedText = printable;
  }
  await session.client.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', ...event });
  await session.client.send('Input.dispatchKeyEvent', { type: 'keyUp', key, code });
  await settle(session);
}

async function waitReady(session, owner) {
  await session.waitFor(`document.body.dataset.shellState === 'ready'
    && document.querySelector('[data-route-owner="${owner}"]')
    && document.querySelector('[data-route-owner="${owner}"]')?.dataset.viewState !== 'loading'`);
  await settle(session);
}

function assertion(id, pass, expected, observed) {
  return { id, pass: Boolean(pass), expected, observed };
}

async function commonSnapshot(session) {
  return session.evaluate(`(() => {
    const active = document.activeElement;
    const style = active ? getComputedStyle(active) : null;
    const rect = active?.getBoundingClientRect();
    const visible = Boolean(active && !active.hidden && style?.display !== 'none'
      && style?.visibility !== 'hidden' && rect?.width > 0 && rect?.height > 0);
    const offenders = [...document.querySelectorAll('body *')].filter((node) => {
      const box = node.getBoundingClientRect();
      return box.width > 0 && box.right > document.documentElement.clientWidth + 1;
    }).slice(0, 12).map((node) => ({tag: node.tagName, id: node.id, className: String(node.className || '')}));
    return {
      active: {id: active?.id || '', tag: active?.tagName || '', role: active?.getAttribute('role') || '',
        name: active?.getAttribute('aria-label') || active?.textContent?.trim().slice(0, 160) || '', visible},
      destination: document.body.dataset.destination || '', routePath: document.body.dataset.routePath || '',
      owner: document.querySelector('[data-route-owner]')?.dataset.routeOwner || '',
      overflow: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth), offenders,
    };
  })()`);
}

async function capture(session, folder, name, trace) {
  const tree = await session.client.send('Accessibility.getFullAXTree');
  await session.screenshot(`${name}.png`);
  writeJson(path.join(folder, `${name}-ax.json`), tree);
  writeJson(path.join(folder, `${name}-focus.json`), trace);
}

function runtimeErrors(session) {
  return session.client.events.filter((event) => event.method === 'Runtime.exceptionThrown'
    || (event.method === 'Runtime.consoleAPICalled' && event.params?.type === 'error'))
    .map((event) => event.params);
}

async function runGlobal(baseUrl, artifacts, width, height) {
  const viewport = `${width}x${height}`;
  const folder = path.join(artifacts, viewport, 'global');
  const session = await BrowserSession.open({
    url: pageUrl(baseUrl, '/more/help-center'), artifacts: folder, width, height,
  });
  const assertions = [];
  const trace = [];
  try {
    await session.client.send('Accessibility.enable');
    await waitReady(session, 'more/help-center');
    const helpBefore = await session.evaluate(`(() => {
      const nav = document.querySelector('.help-topic-list');
      const links = [...nav?.querySelectorAll('a') || []];
      links[1]?.focus();
      return {navRole: nav?.getAttribute('role'), activeDescendant: nav?.getAttribute('aria-activedescendant'),
        linkRoles: links.map((link) => link.getAttribute('role')), linkCount: links.length,
        selected: links.map((link) => link.getAttribute('aria-selected')), hash: location.hash};
    })()`);
    trace.push({step: 'help-topic-focus', ...(await commonSnapshot(session))});
    await physicalKey(session, 'Enter', 'Enter');
    await waitReady(session, 'more/help-center');
    const helpAfter = {hash: await session.evaluate('location.hash'), ...(await commonSnapshot(session))};
    trace.push({step: 'help-topic-enter', ...helpAfter});
    assertions.push(
      assertion('help-ordinary-navigation-semantics', helpBefore.linkCount > 1
        && helpBefore.navRole === null && helpBefore.activeDescendant === null
        && helpBefore.linkRoles.every((role) => role === null)
        && helpBefore.selected.every((selected) => selected === null),
      'labelled nav containing ordinary links with aria-current only', helpBefore),
      assertion('help-physical-enter-navigation', helpAfter.hash !== helpBefore.hash
        && helpAfter.owner === 'more/help-center', 'physical Enter changes the topic in Help', helpAfter),
    );
    await capture(session, folder, 'help', trace);

    await session.evaluate(`globalThis.AlexandriaShell.navigate('#/more/maintenance?mode=recovery&return=settings')`);
    await waitReady(session, 'more/maintenance');
    const maintenance = await commonSnapshot(session);
    trace.push({step: 'maintenance-recovery-ready', ...maintenance});
    assertions.push(assertion('maintenance-visible-route-focus', maintenance.active.visible
      && /^H[1-6]$/.test(maintenance.active.tag)
      && !maintenance.active.id.includes('maintenance-page-heading'),
    'visible route-relevant H1/H2 receives focus after recovery route settles', maintenance.active));
    await capture(session, folder, 'maintenance', trace);

    await session.evaluate(`document.documentElement.style.fontSize='32px'`);
    await settle(session);
    const text200 = await commonSnapshot(session);
    trace.push({step: 'maintenance-text-200', ...text200});
    assertions.push(assertion('global-text-200-no-horizontal-overflow', text200.overflow <= 1,
      'no document horizontal overflow at 200% root text', text200));
    if (width === 768) {
      await session.client.send('Emulation.setEmulatedMedia', {
        features: [{ name: 'forced-colors', value: 'active' }],
      });
      await settle(session);
      assertions.push(assertion('forced-colors-active', await session.evaluate(`matchMedia('(forced-colors: active)').matches`), true, true));
    }
    if (width === 1024) {
      await session.client.send('Emulation.setEmulatedMedia', {
        features: [{ name: 'prefers-reduced-motion', value: 'reduce' }],
      });
      await settle(session);
      assertions.push(assertion('reduced-motion-active', await session.evaluate(`matchMedia('(prefers-reduced-motion: reduce)').matches`), true, true));
    }
    await session.screenshot('global-text-200.png');
    const errors = runtimeErrors(session);
    assertions.push(assertion('global-no-runtime-errors', errors.length === 0, [], errors));
    return { viewport, status: assertions.every((item) => item.pass) ? 'PASS' : 'RED', assertions, trace };
  } catch (error) {
    assertions.push(assertion('global-scenario-completed', false, 'scenario completes', error.stack || String(error)));
    return { viewport, status: 'RED', assertions, trace };
  } finally {
    await session.close();
  }
}

async function runExport(server, artifacts, width, height) {
  const viewport = `${width}x${height}`;
  const folder = path.join(artifacts, viewport, 'export');
  server.control.mode = 'export-ready';
  const session = await BrowserSession.open({
    url: `${server.url}#/export`, artifacts: folder, width, height,
  });
  const assertions = [];
  const trace = [];
  try {
    await session.client.send('Accessibility.enable');
    await waitReady(session, 'export');
    const sentinel = `Hostile title ${viewport} ${'Z'.repeat(96)}`;
    const before = await session.evaluate(`(() => {
      const title = document.getElementById('export-title');
      title.value = ${JSON.stringify(sentinel)};
      title.dispatchEvent(new Event('input',{bubbles:true}));
      const radio = document.querySelector('input[name="export-format"]:checked');
      radio?.focus();
      return {title: title.value, radio: radio?.value || '', radioCount: document.querySelectorAll('input[name="export-format"]:not(:disabled)').length};
    })()`);
    trace.push({step: 'export-format-before', ...(await commonSnapshot(session))});
    await physicalKey(session, 'ArrowRight', 'ArrowRight');
    const after = await session.evaluate(`(() => ({
      title: document.getElementById('export-title')?.value || '',
      selected: document.querySelector('input[name="export-format"]:checked')?.value || '',
      activeName: document.activeElement?.getAttribute('name') || '',
      activeValue: document.activeElement?.value || ''
    }))()`);
    trace.push({step: 'export-format-after', ...(await commonSnapshot(session)), detail: after});
    assertions.push(assertion('export-format-preserves-draft-and-focus', before.radioCount > 1
      && after.title === sentinel && after.selected !== before.radio
      && after.activeName === 'export-format' && after.activeValue === after.selected,
    'physical ArrowRight changes format while preserving metadata and focus', {before, after}));

    const chapterBefore = await session.evaluate(`(() => {
      const list = document.querySelector('.export-chapter-list');
      const rows = [...document.querySelectorAll('[data-export-chapter]')];
      rows[0]?.focus();
      return {listRole: list?.getAttribute('role'), rowRoles: rows.map((row)=>row.getAttribute('role')),
        selected: rows.map((row)=>row.getAttribute('aria-selected')), tabStops: rows.filter((row)=>row.tabIndex===0).length};
    })()`);
    await physicalKey(session, 'ArrowDown', 'ArrowDown');
    const chapterAfter = await session.evaluate(`(() => {
      const rows = [...document.querySelectorAll('[data-export-chapter]')];
      return {activeIndex: rows.indexOf(document.activeElement), selected: rows.map((row)=>row.getAttribute('aria-selected')),
        tabStops: rows.filter((row)=>row.tabIndex===0).length};
    })()`);
    trace.push({step: 'export-chapter-arrow', ...(await commonSnapshot(session)), detail: chapterAfter});
    assertions.push(assertion('export-chapter-composite-semantics', chapterBefore.listRole === 'listbox'
      && chapterBefore.rowRoles.every((role) => role === 'option')
      && chapterAfter.activeIndex === 1 && chapterAfter.selected[1] === 'true'
      && chapterAfter.tabStops === 1,
    'one listbox with roving selected options exposed after physical ArrowDown', {chapterBefore, chapterAfter}));

    const layout = await session.evaluate(`(() => {
      const cover = document.querySelector('.source-cover--placeholder[role="img"]');
      const title = document.querySelector('.export-chapter__title');
      return {placeholderName: cover?.getAttribute('aria-label') || '', placeholderText: cover?.textContent?.trim() || '',
        fullChapterTitle: title?.textContent || '', titleClientWidth: title?.clientWidth || 0, titleScrollWidth: title?.scrollWidth || 0};
    })()`);
    assertions.push(assertion('export-no-cover-labelled-fallback', layout.placeholderName === 'Source cover not provided',
      'labelled no-cover placeholder', layout));
    const defaultLayout = await commonSnapshot(session);
    assertions.push(assertion('export-no-horizontal-overflow', defaultLayout.overflow <= 1,
      'no document horizontal overflow', defaultLayout));
    await session.evaluate(`document.documentElement.style.fontSize='32px'`);
    await settle(session);
    const text200 = await commonSnapshot(session);
    assertions.push(assertion('export-text-200-no-horizontal-overflow', text200.overflow <= 1,
      'no document horizontal overflow at 200% root text', text200));
    await capture(session, folder, 'export', trace);
    await session.screenshot('export-text-200.png');
    const errors = runtimeErrors(session);
    assertions.push(assertion('export-no-runtime-errors', errors.length === 0, [], errors));
    return { viewport, status: assertions.every((item) => item.pass) ? 'PASS' : 'RED', assertions, trace };
  } catch (error) {
    assertions.push(assertion('export-scenario-completed', false, 'scenario completes', error.stack || String(error)));
    return { viewport, status: 'RED', assertions, trace };
  } finally {
    await session.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const baseUrl = required(args, 'url');
  const artifacts = path.resolve(required(args, 'artifacts'));
  fs.mkdirSync(artifacts, { recursive: true });
  const server = await fixtureServer();
  const results = [];
  try {
    for (const [width, height] of VIEWPORTS) {
      results.push(await runGlobal(baseUrl, artifacts, width, height));
      results.push(await runExport(server, artifacts, width, height));
    }
  } finally {
    server.release();
    await server.close();
  }
  const assertions = results.flatMap((result) => result.assertions.map((item) => ({ viewport: result.viewport, ...item })));
  const report = {
    status: assertions.length && assertions.every((item) => item.pass) ? 'PASS' : 'RED',
    viewports: VIEWPORTS.map(([width, height]) => `${width}x${height}`), results, assertions,
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  writeJson(path.join(artifacts, 'cleanup.json'), {
    fixtureServerClosed: !server.server.listening,
    browserReceipts: [...fs.readdirSync(artifacts, { recursive: true })]
      .filter((name) => String(name).endsWith('cleanup.json')).length,
  });
  process.stdout.write(`B19_T07_SUPPORTING_ROUTES=${JSON.stringify({status: report.status, assertions: assertions.filter((item)=>!item.pass)})}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => { console.error(error.stack || error); process.exitCode = 2; });
