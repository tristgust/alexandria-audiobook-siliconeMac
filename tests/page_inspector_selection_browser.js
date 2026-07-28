'use strict';

const fs = require('node:fs');
const path = require('node:path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');
const {
  CDP_MODIFIER, realKeyPress, realPointerClick,
} = require('./produce_export_browser_helpers.js');
const { fixtureServer } = require('./produce_export_fixture_server.js');

const json = (value) => JSON.stringify(value);
const errors = (session) => session.client.events.filter((item) => (
  item.method === 'Runtime.exceptionThrown'
  || (item.method === 'Runtime.consoleAPICalled' && item.params?.type === 'error')
));

async function settle(session) {
  await session.evaluate(`new Promise((resolve) => requestAnimationFrame(() =>
    requestAnimationFrame(() => setTimeout(resolve, 40))))`);
}

async function readyRows(session) {
  await session.waitFor(`document.body.dataset.shellState==='ready'
    && document.querySelectorAll('[data-audio-row]').length>0`);
  await session.evaluate(`document.querySelector('[data-produce-filter="ready"]')?.click()`);
  await session.waitFor(`[...document.querySelectorAll('[data-audio-row]')].length===12
    && [...document.querySelectorAll('[data-audio-row]')]
      .every((row)=>row.dataset.audioState==='ready')`);
  return session.evaluate(`[...document.querySelectorAll('[data-audio-row]')]
    .slice(0,4).map((row)=>row.dataset.chunkId)`);
}

async function state(session, openerId = '') {
  return session.evaluate(`(() => {
    const node=document.querySelector('[data-page-inspector]');
    const scrim=document.querySelector('[data-page-inspector-scrim]');
    const visible=(item)=>Boolean(item&&item.getClientRects().length
      &&getComputedStyle(item).display!=='none');
    const nonOverlapping=(parent)=>{const cells=[...(parent?.children||[])]
      .filter(visible).map((item)=>item.getBoundingClientRect());
      return cells.every((item,index)=>!cells[index+1]||item.right<=cells[index+1].left+.5);};
    return {selected:[...document.querySelectorAll('[data-audio-row][aria-selected="true"]')]
      .map((row)=>row.dataset.chunkId), activeChunk:document.activeElement?.dataset.chunkId||'',
      drawerOpen:Boolean(node?.classList.contains('is-open')&&!node.hidden),
      visible:Boolean(node&&!node.hidden&&node.getClientRects().length),
      mode:node?.dataset.inspectorMode||'', role:node?.getAttribute('role'),
      ariaModal:node?.getAttribute('aria-modal'), backgroundProtected:Boolean(
        document.querySelector('[data-app-shell]')?.inert),
      scrimVisible:Boolean(scrim&&!scrim.hidden&&scrim.getClientRects().length),
      produceColumnsNonOverlapping:nonOverlapping(document.querySelector('.audio-table__header'))
        &&nonOverlapping(document.querySelector('[data-audio-row]')),
      openerFocused:document.activeElement?.dataset.chunkId===${json(openerId)}};
  })()`);
}

async function closeIfOpen(session) {
  const open = await session.evaluate(
    `document.querySelector('[data-page-inspector]')?.classList.contains('is-open')||false`,
  );
  if (open) {
    await realPointerClick(session, '[data-page-inspector-close]');
    await settle(session);
  }
}

async function inspectSelection(fixture, artifacts) {
  const folder = path.join(artifacts, 'selection', '1024x768');
  const session = await BrowserSession.open({
    url: `${fixture.url}#/produce`, artifacts: folder, width: 1024, height: 768,
  });
  try {
    const ids = await readyRows(session);
    const commandFirstClicked = await realPointerClick(
      session, `[data-chunk-id="${ids[0]}"]`, CDP_MODIFIER.meta,
    );
    const commandFirst = await state(session);
    const commandSecondClicked = await realPointerClick(
      session, `[data-chunk-id="${ids[1]}"]`, CDP_MODIFIER.meta,
    );
    const commandAdditive = await state(session);
    await closeIfOpen(session);
    await session.evaluate(`document.querySelector('[data-produce-clear-selection]')?.click()`);
    const controlClick = (id) => session.evaluate(`(() => {
      const row=document.querySelector('[data-chunk-id="${id}"]');
      return Boolean(row&&row.dispatchEvent(new MouseEvent('click',{
        bubbles:true,button:0,ctrlKey:true,
      })));
    })()`);
    const controlFirstDispatched = await controlClick(ids[0]);
    await settle(session);
    const controlFirst = await state(session);
    const controlSecondDispatched = await controlClick(ids[1]);
    await settle(session);
    const controlAdditive = await state(session);
    await closeIfOpen(session);
    await session.evaluate(`document.querySelector('[data-produce-clear-selection]')?.click()`);
    await realPointerClick(session, `[data-chunk-id="${ids[0]}"]`, CDP_MODIFIER.meta);
    const anchor = await state(session);
    await closeIfOpen(session);
    const rangeClicked = await realPointerClick(
      session, `[data-chunk-id="${ids[3]}"]`, CDP_MODIFIER.shift,
    );
    const range = await state(session);
    await session.screenshot('produce-modifier-selection.png');
    await realKeyPress(session, 'Enter', 'Enter');
    await settle(session);
    const enter = await state(session);
    await realKeyPress(session, 'Escape', 'Escape');
    await settle(session);
    await session.evaluate(`document.querySelector('[data-produce-clear-selection]')?.click();
      document.querySelector('[data-chunk-id="${ids[0]}"]')?.focus({preventScroll:true})`);
    await realKeyPress(session, ' ', 'Space');
    await settle(session);
    const space = await state(session);
    await closeIfOpen(session);
    await session.evaluate(`document.querySelector('[data-chunk-id="${ids[3]}"]')
      ?.focus({preventScroll:true})`);
    await realKeyPress(session, ' ', 'Space', CDP_MODIFIER.shift);
    await settle(session);
    const shiftSpace = await state(session);
    await session.screenshot('produce-keyboard-selection.png');
    const assertions = {
      commandFirstClicked,
      commandFirstSelected: json(commandFirst.selected) === json(ids.slice(0, 1)),
      commandFirstNonmodal: !commandFirst.drawerOpen && !commandFirst.backgroundProtected,
      uninterruptedCommandSecondClick: commandSecondClicked,
      commandAdditiveSelection: json(commandAdditive.selected) === json(ids.slice(0, 2)),
      commandAdditiveNonmodal: !commandAdditive.drawerOpen
        && !commandAdditive.backgroundProtected,
      controlFirstDispatched,
      controlFirstSelected: json(controlFirst.selected) === json(ids.slice(0, 1)),
      controlFirstNonmodal: !controlFirst.drawerOpen && !controlFirst.backgroundProtected,
      uninterruptedControlSecondDispatch: controlSecondDispatched,
      controlAdditiveSelection: json(controlAdditive.selected) === json(ids.slice(0, 2)),
      controlAdditiveNonmodal: !controlAdditive.drawerOpen
        && !controlAdditive.backgroundProtected,
      anchorNonmodal: !anchor.drawerOpen && !anchor.backgroundProtected,
      rangeClicked, rangeSelection: json(range.selected) === json(ids),
      rangeNonmodal: !range.drawerOpen && !range.backgroundProtected,
      rangeFocus: range.activeChunk === ids[3], enterInspects: enter.drawerOpen
        && enter.role === 'dialog' && enter.ariaModal === 'true',
      spaceSelection: json(space.selected) === json(ids.slice(0, 1)),
      spaceNonmodal: !space.drawerOpen && !space.backgroundProtected,
      shiftSpaceSelection: json(shiftSpace.selected) === json(ids),
      shiftSpaceNonmodal: !shiftSpace.drawerOpen && !shiftSpace.backgroundProtected,
      keyboardFocus: shiftSpace.activeChunk === ids[3],
      noRuntimeErrors: errors(session).length === 0,
    };
    return { name: 'modifier-selection', status: Object.values(assertions).every(Boolean)
      ? 'PASS' : 'FAIL', assertions, ids, commandFirst, commandAdditive, controlFirst,
    controlAdditive, anchor, range, enter, space, shiftSpace,
    controlMechanism: 'MouseEvent click with ctrlKey through the live row DOM handler' };
  } finally {
    await session.close();
  }
}

async function inspectTransition(fixture, artifacts) {
  const folder = path.join(artifacts, 'transition', '1024x768');
  const session = await BrowserSession.open({
    url: `${fixture.url}#/produce`, artifacts: folder, width: 1024, height: 768,
  });
  try {
    const [openerId] = await readyRows(session);
    const opened = await realPointerClick(session, `[data-chunk-id="${openerId}"]`);
    await settle(session);
    const overlay = await state(session, openerId);
    await session.client.send('Emulation.setDeviceMetricsOverride', {
      width: 1280, height: 800, deviceScaleFactor: 1, mobile: false,
    });
    await session.waitFor(`document.querySelector('[data-page-inspector]')?.dataset.inspectorMode==='inline'`);
    await settle(session);
    const wide = await state(session, openerId);
    await session.screenshot('produce-transition-wide.png');
    await session.client.send('Emulation.setDeviceMetricsOverride', {
      width: 1024, height: 768, deviceScaleFactor: 1, mobile: false,
    });
    await session.waitFor(`document.querySelector('[data-page-inspector]')?.getAttribute('role')==='dialog'`);
    await settle(session);
    const returned = await state(session, openerId);
    await session.screenshot('produce-transition-return.png');
    await realKeyPress(session, 'Escape', 'Escape');
    await settle(session);
    const closed = await state(session, openerId);
    const assertions = {
      plainClickInspects: opened && overlay.drawerOpen && overlay.mode === 'overlay'
        && overlay.role === 'dialog' && overlay.ariaModal === 'true'
        && overlay.backgroundProtected && overlay.scrimVisible,
      wideInline: wide.visible && wide.mode === 'inline' && wide.role === null
        && wide.ariaModal === null && !wide.backgroundProtected && !wide.scrimVisible,
      wideColumnsNonOverlapping: wide.produceColumnsNonOverlapping,
      returnRemodalizes: returned.drawerOpen && returned.mode === 'overlay'
        && returned.role === 'dialog' && returned.ariaModal === 'true'
        && returned.backgroundProtected && returned.scrimVisible,
      escapeCloses: !closed.drawerOpen && !closed.backgroundProtected && !closed.scrimVisible,
      restoresOpener: closed.openerFocused, noRuntimeErrors: errors(session).length === 0,
    };
    return { name: 'resize-transition', status: Object.values(assertions).every(Boolean)
      ? 'PASS' : 'FAIL', assertions, openerId, overlay, wide, returned, closed };
  } finally {
    await session.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required(args, 'evidence-dir'));
  const label = String(args.label || 'run');
  const fixture = await fixtureServer();
  const results = [];
  try {
    results.push(await inspectSelection(fixture, artifacts));
    results.push(await inspectTransition(fixture, artifacts));
  } finally {
    fixture.release();
    await fixture.close();
  }
  const report = { status: results.every((item) => item.status === 'PASS') ? 'PASS' : 'FAIL',
    label, results, cleanup: { fixtureClosed: !fixture.server.listening } };
  writeJson(path.join(artifacts, `selection-transition-${label}.json`), report);
  fs.writeFileSync(path.join(artifacts, `selection-transition-${label}.log`),
    `${results.map((item) => `${item.name} ${item.status} ${json(item.assertions)}`).join('\n')}\n`);
  process.stdout.write(`PAGE_INSPECTOR_SELECTION=${json(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => { console.error(error.stack || error); process.exitCode = 2; });
