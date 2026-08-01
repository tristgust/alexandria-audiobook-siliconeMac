'use strict';

const fs = require('fs');
const path = require('path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');
const { fixtureServer } = require('./produce_export_fixture_server.js');

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function inspectViewport(server, artifacts, width, height) {
  server.control.mode = 'produce-takes';
  server.control.takeState.currentId = 'take-newest';
  server.control.takeState.kept.clear();
  server.control.takeState.deleted.clear();
  const viewport = `${width}x${height}`;
  const folder = path.join(artifacts, viewport);
  fs.mkdirSync(folder, { recursive: true });
  const session = await BrowserSession.open({
    url: `${server.url}#/produce?chunk=current-1`,
    artifacts: folder,
    width,
    height,
  });
  session.baseUrl = server.url;
  try {
    await session.waitFor(`document.body.dataset.shellState==='ready'`);
    await session.waitFor(`document.querySelectorAll('[data-produce-take]').length===4`);
    const openAttempt = await session.evaluate(`(() => {
      const row=document.querySelector('[data-audio-row][data-chunk-id="chunk:current-1"]');
      const before=document.querySelector('.produce-inspector');
      row?.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));
      const after=document.querySelector('.produce-inspector');
      return {
        row:Boolean(row),
        mode:after?.dataset.inspectorMode||before?.dataset.inspectorMode||null,
        hidden:Boolean(after?.hidden),
        open:Boolean(after?.classList.contains('is-open')),
        overlay:Boolean(document.querySelector('[data-overlay-root]')),
      };
    })()`);
    if (width <= 1180) {
      await session.waitFor(`!document.querySelector('.produce-inspector')?.hidden`);
    }
    await session.screenshot('takes-initial.png');
    const initial = await session.evaluate(`(() => {
      const rows=[...document.querySelectorAll('[data-produce-take]')];
      const inspector=document.querySelector('.produce-inspector');
      const rect=inspector?.getBoundingClientRect();
      const buttons=[...document.querySelectorAll('[data-produce-take] button')];
      return {
        ids:rows.map((row)=>row.dataset.produceTake),
        current:rows.find((row)=>row.dataset.current==='true')?.dataset.produceTake||null,
        overflow:Math.max(0,document.documentElement.scrollWidth-innerWidth),
        inspectorContained:Boolean(rect&&rect.left>=-1&&rect.right<=innerWidth+1&&rect.top>=-1&&rect.bottom<=innerHeight+1),
        named:buttons.every((button)=>Boolean(button.textContent.trim()||button.getAttribute('aria-label'))),
        targetFloor:buttons.every((button)=>button.getBoundingClientRect().height>=32),
        buttonHeights:buttons.map((button)=>({
          label:button.textContent.trim()||button.getAttribute('aria-label'),
          height:button.getBoundingClientRect().height,
          display:getComputedStyle(button).display,
          minHeight:getComputedStyle(button).minHeight,
        })),
        deleteCurrentDisabled:Boolean(rows.find((row)=>row.dataset.current==='true')?.querySelector('[data-produce-take-delete]')?.disabled),
        incompatibleUseDisabled:Boolean(document.querySelector('[data-produce-take-use="take-incompatible"]')?.disabled),
      };
    })()`);

    await session.evaluate(`document.querySelector('[data-produce-take-play="take-older"]')?.click()`);
    await wait(100);
    const playback = await session.evaluate(`(() => ({
      source:document.querySelector('[data-persistent-player]')?.dataset.mediaSource||'',
      state:document.querySelector('[data-persistent-player]')?.dataset.state||'',
    }))()`);

    await session.evaluate(`document.querySelector('[data-produce-take-keep="take-older"]')?.click()`);
    await session.waitFor(`document.querySelector('[data-produce-take="take-older"]')?.dataset.kept==='true'`);
    const kept = await session.evaluate(`document.querySelector('[data-produce-take="take-older"] [data-produce-take-keep]')?.textContent.trim()`);

    await session.evaluate(`document.querySelector('[aria-label="More production actions"]')?.click()`);
    await session.waitFor(`[role="menu"]`);
    await session.evaluate(`[...document.querySelectorAll('[role="menu"] button')].find((button)=>button.textContent.includes('Clean up old takes'))?.click()`);
    await session.waitFor(`document.querySelector('.dialog-layer[role="dialog"]')`);
    const cleanupDialog = await session.evaluate(`(() => {
      const layer=document.querySelector('.dialog-layer[role="dialog"]');
      const rect=layer?.querySelector('.dialog-surface')?.getBoundingClientRect();
      return {
        title:layer?.querySelector('h2')?.textContent||'',
        body:layer?.querySelector('.dialog__body')?.textContent||'',
        contained:Boolean(rect&&rect.left>=0&&rect.right<=innerWidth&&rect.top>=0&&rect.bottom<=innerHeight),
        focusInside:Boolean(layer?.contains(document.activeElement)),
      };
    })()`);
    await session.evaluate(`document.querySelector('.dialog-layer [aria-label^="Close"]')?.click()`);

    await session.evaluate(`document.querySelector('[data-produce-take-delete="take-incompatible"]')?.click()`);
    await session.waitFor(`document.querySelector('.dialog-layer[role="dialog"] h2')?.textContent==='Delete this Take?'`);
    const deleteDialog = await session.evaluate(`(() => {
      const layer=document.querySelector('.dialog-layer[role="dialog"]');
      const rect=layer?.querySelector('.dialog-surface')?.getBoundingClientRect();
      return {
        title:layer?.querySelector('h2')?.textContent||'',
        body:layer?.querySelector('.dialog__body')?.textContent||'',
        contained:Boolean(rect&&rect.left>=0&&rect.right<=innerWidth&&rect.top>=0&&rect.bottom<=innerHeight),
        focusInside:Boolean(layer?.contains(document.activeElement)),
        destructive:Boolean(layer?.querySelector('.dialog__footer .ui-button--destructive')),
      };
    })()`);
    await session.evaluate(`document.querySelector('.dialog-layer [aria-label^="Close"]')?.click()`);

    await session.evaluate(`document.querySelector('[data-produce-take-use="rendition-reviewed"]')?.click()`);
    await session.waitFor(`document.querySelector('[data-produce-take="rendition-reviewed"]')?.dataset.current==='true'`);
    const selected = await session.evaluate(`(() => ({
      current:document.querySelector('[data-produce-take][data-current="true"]')?.dataset.produceTake||null,
      currentDeleteDisabled:Boolean(document.querySelector('[data-produce-take][data-current="true"] [data-produce-take-delete]')?.disabled),
      retained:document.querySelectorAll('[data-produce-take]').length,
      overflow:Math.max(0,document.documentElement.scrollWidth-innerWidth),
    }))()`);
    await session.screenshot('takes-selected.png');

    const assertions = {
      newestFirst: JSON.stringify(initial.ids) === JSON.stringify([
        'take-newest', 'rendition-reviewed', 'take-older', 'take-incompatible',
      ]),
      initialCurrent: initial.current === 'take-newest',
      noOverflow: initial.overflow <= 1 && selected.overflow <= 1,
      inspectorContained: initial.inspectorContained,
      namedControls: initial.named,
      compactTargets: initial.targetFloor,
      currentProtected: initial.deleteCurrentDisabled && selected.currentDeleteDisabled,
      incompatiblePromotionBlocked: initial.incompatibleUseDisabled,
      persistentPlayback: /take-older/.test(playback.source),
      keepPersisted: kept === 'Unkeep',
      cleanupReviewed: cleanupDialog.title === 'Clean up old Takes?'
        && /eligible Takes/.test(cleanupDialog.body)
        && /Protected Takes/.test(cleanupDialog.body)
        && cleanupDialog.contained && cleanupDialog.focusInside,
      deleteReviewed: deleteDialog.title === 'Delete this Take?'
        && /non-current Take/.test(deleteDialog.body)
        && deleteDialog.contained && deleteDialog.focusInside && deleteDialog.destructive,
      selectionChanged: selected.current === 'rendition-reviewed' && selected.retained === 4,
    };
    return {
      viewport,
      status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL',
      assertions,
      initial,
      openAttempt,
      playback,
      cleanupDialog,
      deleteDialog,
      selected,
      runtimeErrors: session.client.consoleErrors || [],
    };
  } finally {
    await session.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required(args, 'artifacts'));
  fs.mkdirSync(artifacts, { recursive: true });
  const server = await fixtureServer();
  const results = [];
  try {
    for (const [width, height] of [[390, 844], [1536, 1024]]) {
      results.push(await inspectViewport(server, artifacts, width, height));
    }
  } finally {
    server.release();
    await server.close();
  }
  const report = {
    status: results.every((item) => item.status === 'PASS') ? 'PASS' : 'FAIL',
    scenario: 'B16-T05 immutable Take lineage Produce acceptance',
    results,
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  writeJson(path.join(artifacts, 'cleanup.json'), {
    serverClosed: !server.server.listening,
    pendingResponses: server.control.pending.length,
  });
  process.stdout.write(`B16_T05_TAKE_LINEAGE=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
