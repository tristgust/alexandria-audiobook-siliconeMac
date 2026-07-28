'use strict';

const fs = require('node:fs');
const path = require('node:path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');
const {
  CDP_MODIFIER, realKeyPress, realPointerClick,
} = require('./produce_export_browser_helpers.js');
const { fixtureServer: scriptFixtureServer } = require('./b19_t06_projects_script_supporting.js');
const { fixtureServer: produceFixtureServer } = require('./produce_export_fixture_server.js');

const RESPONSIVE_VIEWPORTS = Object.freeze([[1024, 768], [390, 844]]);
const WIDE_VIEWPORTS = Object.freeze([[1280, 800], [1440, 960]]);
const SURFACES = Object.freeze({
  script: Object.freeze({
    fixture: scriptFixtureServer,
    hash: 'script?project=project_meridian',
    ready: `document.body.dataset.routePath==='script'
      && Boolean(document.querySelector('.script-entry'))`,
    opener: '.script-entry',
  }),
  produce: Object.freeze({
    fixture: produceFixtureServer,
    hash: 'produce?chunk=stale-1',
    ready: `document.body.dataset.routePath==='produce'
      && Boolean(document.querySelector('[data-audio-row]'))`,
    opener: '[data-audio-row]',
  }),
});
const FOCUSABLE = [
  'button:not(:disabled)', 'a[href]', 'input:not(:disabled)', 'select:not(:disabled)',
  'textarea:not(:disabled)', '[tabindex]:not([tabindex="-1"])',
].join(',');
const json = (value) => JSON.stringify(value);

function errors(session) {
  return session.client.events.filter((item) => item.method === 'Runtime.exceptionThrown'
    || (item.method === 'Runtime.consoleAPICalled' && item.params?.type === 'error'));
}

async function settle(session) {
  await session.evaluate(`new Promise((resolve) => requestAnimationFrame(() =>
    requestAnimationFrame(() => setTimeout(resolve, 40))))`);
}

async function openInspector(session, opener) {
  const clicked = await realPointerClick(session, opener);
  if (clicked) {
    await session.waitFor(`(() => { const node=document.querySelector('[data-page-inspector]');
      return Boolean(node?.classList.contains('is-open') && node.getClientRects().length); })()`);
  }
  return clicked;
}

async function snapshot(session, opener) {
  return session.evaluate(`(() => {
    const node=document.querySelector('[data-page-inspector]');
    const scrim=document.querySelector('[data-page-inspector-scrim]');
    const shell=document.querySelector('[data-app-shell]');
    const style=node ? getComputedStyle(node) : null;
    const scrimStyle=scrim ? getComputedStyle(scrim) : null;
    const rect=node?.getBoundingClientRect();
    const visible=(item) => Boolean(item && !item.hidden && item.getClientRects().length
      && getComputedStyle(item).visibility!=='hidden');
    const nonOverlapping=(parent) => {
      const cells=[...(parent?.children||[])].filter(visible)
        .map((item)=>item.getBoundingClientRect());
      return cells.every((item,index)=>!cells[index+1]||item.right<=cells[index+1].left+.5);
    };
    const tableHeader=document.querySelector('.audio-table__header');
    const tableRow=document.querySelector('[data-audio-row]');
    const controls=node ? [...node.querySelectorAll(${json(FOCUSABLE)})].filter(visible) : [];
    const probe=document.createElement('span');
    probe.style.backgroundColor='var(--color-surface-primary)';
    document.body.append(probe);
    const surfaceToken=getComputedStyle(probe).backgroundColor;
    probe.style.backgroundColor='var(--color-scrim)';
    const scrimToken=getComputedStyle(probe).backgroundColor;
    probe.remove();
    const outside=document.elementFromPoint(1, Math.round(innerHeight / 2));
    return {
      tag:node?.tagName.toLowerCase()||'', mode:node?.dataset.inspectorMode||'',
      visible:visible(node), hidden:Boolean(node?.hidden), role:node?.getAttribute('role'),
      ariaModal:node?.getAttribute('aria-modal'), label:node?.getAttribute('aria-label')||'',
      activeInside:Boolean(node?.contains(document.activeElement)),
      openerFocused:document.activeElement===document.querySelector(${json(opener)}),
      scrimVisible:visible(scrim), scrimIntercepts:Boolean(scrim && (outside===scrim||scrim.contains(outside))),
      backgroundProtected:Boolean(shell?.inert), position:style?.position||'',
      surfaceBackground:style?.backgroundColor||'', surfaceToken,
      scrimBackground:scrimStyle?.backgroundColor||'', scrimToken,
      rect:rect?{left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom,width:rect.width,height:rect.height}:null,
      viewport:{width:innerWidth,height:innerHeight},
      contained:Boolean(rect&&rect.left>=-1&&rect.right<=innerWidth+1&&rect.top>=-1&&rect.bottom<=innerHeight+1),
      documentOverflowX:Math.max(0,document.documentElement.scrollWidth-innerWidth),
      scrollSafe:Boolean(node && (node.scrollHeight<=node.clientHeight+1
        || ['auto','scroll'].includes(style.overflowY))),
      produceColumnsNonOverlapping:!tableHeader
        || (nonOverlapping(tableHeader)&&nonOverlapping(tableRow)),
      bodyOverflow:document.body.style.overflow,
      priorInertPreserved:Boolean(document.querySelector('[data-inspector-inert-sentinel]')?.inert),
      controls:controls.length,
    };
  })()`);
}

async function focusLifecycle(session, opener) {
  const initial = await session.evaluate(`(() => { const node=document.querySelector('[data-page-inspector]');
    const items=[...node.querySelectorAll(${json(FOCUSABLE)})].filter((item)=>item.getClientRects().length);
    globalThis.__inspectorFocus={first:items[0],last:items.at(-1),opener:document.querySelector(${json(opener)})};
    return {inside:node.contains(document.activeElement),atFirst:document.activeElement===items[0],count:items.length}; })()`);
  await realKeyPress(session, 'Tab', 'Tab', CDP_MODIFIER.shift);
  const shiftWrap = await session.evaluate(`document.activeElement===globalThis.__inspectorFocus.last`);
  await realKeyPress(session, 'Tab', 'Tab');
  const tabWrap = await session.evaluate(`document.activeElement===globalThis.__inspectorFocus.first`);
  await realKeyPress(session, 'Escape', 'Escape');
  await settle(session);
  const closed = await session.evaluate(`(() => { const node=document.querySelector('[data-page-inspector]');
    return {hidden:Boolean(node?.hidden),open:Boolean(node?.classList.contains('is-open')),
      restored:document.activeElement===globalThis.__inspectorFocus.opener,
      bodyOverflow:document.body.style.overflow,
      priorInertPreserved:Boolean(document.querySelector('[data-inspector-inert-sentinel]')?.inert)}; })()`);
  return { initial, shiftWrap, tabWrap, closed };
}

function assertionsFor({ scenario, opened, observed, after, focus, runtimeErrors }) {
  if (scenario === 'semantics') return {
    opened, overlayMode: observed.mode === 'overlay', dialogRole: observed.role === 'dialog',
    modalSemantics: observed.ariaModal === 'true', labelled: Boolean(observed.label),
    scrimVisible: observed.scrimVisible, backgroundProtected: observed.backgroundProtected,
    scrimIntercepts: observed.scrimIntercepts,
    releasedOnClose: after.hidden && after.role === null && after.ariaModal === null
      && !after.scrimVisible && !after.backgroundProtected,
    restoresPriorGlobalState: after.bodyOverflow === 'clip' && after.priorInertPreserved,
    noRuntimeErrors: runtimeErrors.length === 0,
  };
  if (scenario === 'focus') return {
    opened, initialFocusInside: focus.initial.inside && focus.initial.atFirst,
    focusablePresent: focus.initial.count > 0, shiftTabWraps: focus.shiftWrap,
    tabWraps: focus.tabWrap, escapeCloses: focus.closed.hidden && !focus.closed.open,
    restoresExactOpener: focus.closed.restored,
    restoresPriorGlobalState: focus.closed.bodyOverflow === 'clip'
      && focus.closed.priorInertPreserved,
    noRuntimeErrors: runtimeErrors.length === 0,
  };
  if (scenario === 'visual') return {
    opened, opaqueSurface: observed.surfaceBackground === observed.surfaceToken,
    tokenScrim: observed.scrimBackground === observed.scrimToken,
    drawerContained: observed.contained, scrollSafe: observed.scrollSafe,
    noDocumentOverflow: observed.documentOverflowX <= 1,
    closeControlVisible: observed.controls > 0, noRuntimeErrors: runtimeErrors.length === 0,
  };
  return {
    inlineMode: observed.mode === 'inline', visibleAside: observed.visible && observed.tag === 'aside',
    nonmodal: observed.role === null && observed.ariaModal === null,
    noScrim: !observed.scrimVisible, backgroundInteractive: !observed.backgroundProtected,
    inlineContained: observed.contained, noDocumentOverflow: observed.documentOverflowX <= 1,
    produceColumnsNonOverlapping: observed.produceColumnsNonOverlapping,
    preservesUnownedGlobalState: observed.bodyOverflow === 'clip'
      && observed.priorInertPreserved,
    noRuntimeErrors: runtimeErrors.length === 0,
  };
}

async function inspect({ fixture, surface, config, scenario, artifacts, width, height }) {
  const viewport = `${width}x${height}`;
  const folder = path.join(artifacts, scenario, viewport, surface);
  const session = await BrowserSession.open({
    url: `${fixture.url}#/${config.hash}`, artifacts: folder, width, height,
  });
  try {
    await session.waitFor(`document.body.dataset.shellState==='ready' && (${config.ready})`);
    await settle(session);
    if (['semantics', 'focus', 'wide'].includes(scenario)) {
      await session.evaluate(`(() => {
        document.body.style.overflow='clip';
        const sentinel=document.createElement('div');
        sentinel.dataset.inspectorInertSentinel='';
        sentinel.inert=true;
        document.body.append(sentinel);
        if (${json(scenario)}==='wide') {
          dispatchEvent(new Event('resize'));
          dispatchEvent(new Event('resize'));
        }
      })()`);
      await settle(session);
    }
    let opened = true;
    if (scenario !== 'wide') opened = await openInspector(session, config.opener);
    const observed = await snapshot(session, config.opener);
    let after = observed;
    let focus = null;
    if (scenario === 'semantics') {
      await realPointerClick(session, '[data-page-inspector-close]');
      await settle(session);
      after = await snapshot(session, config.opener);
    } else if (scenario === 'focus') {
      focus = await focusLifecycle(session, config.opener);
    } else {
      await session.screenshot(`${surface}-${scenario}.png`);
    }
    const runtimeErrors = errors(session);
    const assertions = assertionsFor({ scenario, opened, observed, after, focus, runtimeErrors });
    return {
      surface, viewport, status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL',
      assertions, observed, after, focus, runtimeErrors,
      screenshot: ['visual', 'wide'].includes(scenario)
        ? path.join(folder, `${surface}-${scenario}.png`) : null,
    };
  } finally {
    await session.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const scenario = String(required(args, 'scenario'));
  if (!['semantics', 'focus', 'visual', 'wide'].includes(scenario)) {
    throw new Error(`Unknown scenario: ${scenario}`);
  }
  const artifacts = path.resolve(required(args, 'evidence-dir'));
  const label = String(args.label || 'run');
  const viewports = scenario === 'wide' ? WIDE_VIEWPORTS : RESPONSIVE_VIEWPORTS;
  const results = [];
  const cleanup = [];
  for (const [surface, config] of Object.entries(SURFACES)) {
    const fixture = await config.fixture();
    try {
      for (const [width, height] of viewports) {
        results.push(await inspect({
          fixture, surface, config, scenario, artifacts, width, height,
        }));
      }
    } finally {
      fixture.release?.();
      await fixture.close();
      cleanup.push({ surface, fixtureClosed: fixture.server ? !fixture.server.listening : true });
    }
  }
  const report = {
    status: results.every((item) => item.status === 'PASS') ? 'PASS' : 'FAIL',
    scenario, label, viewports, results, cleanup,
  };
  writeJson(path.join(artifacts, `${scenario}-${label}.json`), report);
  fs.writeFileSync(path.join(artifacts, `${scenario}-${label}.log`),
    `${results.map((item) => `${item.surface} ${item.viewport} ${item.status} ${json(item.assertions)}`).join('\n')}\n`);
  process.stdout.write(`PAGE_INSPECTOR_BROWSER=${json(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
