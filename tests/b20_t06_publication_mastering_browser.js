'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert').strict;
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');
const { fixtureServer } = require('./produce_export_fixture_server.js');

const VIEWPORTS = [[390, 844], [768, 1024], [1024, 768], [1536, 1024]];

function reset(server) {
  const take = server.control.takeState;
  take.currentId = 'take-newest';
  take.pinnedId = 'take-newest';
  take.pauseAfterMs = 350;
  take.renditions = [];
  take.nextRendition = 1;
  take.kept = new Set(['take-newest']);
  take.deleted.clear();
  take.undo.clear();
  Object.assign(server.control.mastering, {
    running: false, cancel_requested: false, status: 'idle',
    chunk_id: null, source_take_id: null, completed_count: 0,
    total_count: 7, progress_message: null, result: null,
    background_job_id: null,
  });
  server.control.mode = 'produce-takes';
  server.control.requests.length = 0;
}

async function clickByText(session, selector, text) {
  await session.evaluate(`(() => {
    const node=[...document.querySelectorAll(${JSON.stringify(selector)})]
      .find((item)=>item.textContent.trim()===${JSON.stringify(text)});
    node?.click();
  })()`);
}

async function applyMastering(session, server, roomProfile) {
  await session.evaluate(`(() => {
    const room=document.querySelector('[data-mastering-room-enabled]');
    room.checked=${roomProfile ? 'true' : 'false'};
    room.dispatchEvent(new Event('change',{bubbles:true}));
    const id=document.querySelector('[data-mastering-room-id]');
    if(id) id.value=${JSON.stringify(roomProfile || '')};
    document.querySelector('[data-mastering-gain]').value='1.5';
    document.querySelector('[data-mastering-high-pass]').value='80';
    document.querySelector('[data-mastering-low-pass]').value='10000';
    document.querySelector('[data-mastering-ratio]').value='2.5';
    document.querySelector('[data-mastering-loudness]').value='-20';
    document.querySelector('[data-mastering-ceiling]').value='-1';
    document.querySelector('[data-mastering-review]')?.click();
  })()`);
  await session.waitFor(`document.querySelector('.dialog-layer[role="dialog"] h2')?.textContent==='Create mastered child rendition?'`);
  const dialog = await session.evaluate(`(() => {
    const layer=document.querySelector('.dialog-layer[role="dialog"]');
    const rect=layer?.querySelector('.dialog-surface')?.getBoundingClientRect();
    return {
      title:layer?.querySelector('h2')?.textContent||'',
      body:layer?.querySelector('.dialog__body')?.textContent||'',
      contained:Boolean(rect&&rect.left>=0&&rect.right<=innerWidth&&rect.top>=0&&rect.bottom<=innerHeight),
      focusInside:Boolean(layer?.contains(document.activeElement)),
    };
  })()`);
  assert.match(dialog.body, /Gain 1\.5 dB/);
  assert.match(dialog.body, /80–10000 Hz/);
  assert.match(dialog.body, /2\.5:1/);
  assert.match(dialog.body, /source Take remains immutable/i);
  if (roomProfile) assert.match(dialog.body, new RegExp(roomProfile));
  const before = server.control.requests.filter((item) => /\/mastering\/apply$/.test(item.path)).length;
  await clickByText(session, '.dialog-layer button', 'Start mastering');
  await session.waitFor(`document.querySelector('[data-produce-take^="rendition-mastered-"]')?.dataset.current==='true'`);
  const after = server.control.requests.filter((item) => /\/mastering\/apply$/.test(item.path)).length;
  assert.equal(after - before, 1);
  return dialog;
}

async function inspectViewport(server, artifacts, width, height) {
  reset(server);
  const viewport = `${width}x${height}`;
  const folder = path.join(artifacts, viewport);
  fs.mkdirSync(folder, { recursive: true });
  const session = await BrowserSession.open({
    url: `${server.url}#/produce?chunk=current-1`,
    artifacts: folder,
    width,
    height,
  });
  try {
    await session.waitFor(`document.body.dataset.shellState==='ready'`);
    await session.waitFor(`document.querySelector('[data-produce-mastering] [data-mastering-review]')`);
    await session.screenshot('mastering-ready.png');
    const initial = await session.evaluate(`(() => {
      const section=document.querySelector('[data-produce-mastering]');
      const rect=document.querySelector('.produce-inspector')?.getBoundingClientRect();
      const controls=[...section.querySelectorAll('button,input,summary')].filter((node)=>{
        const box=node.getBoundingClientRect(); return box.width>0&&box.height>0;
      });
      return {
        heading:section.querySelector('h3')?.textContent.trim()||'',
        fieldCount:section.querySelectorAll('.field').length,
        rejected:/Pitch shifting, chorus, dramatic reverb, voice transformation/.test(section.textContent),
        named:controls.every((node)=>node.tagName==='INPUT'
          ? node.type==='checkbox'||node.labels?.length>0
          : Boolean(node.textContent.trim()||node.getAttribute('aria-label'))),
        targetFloor:controls.filter((node)=>node.tagName!=='INPUT')
          .every((node)=>node.getBoundingClientRect().height>=32),
        noOverflow:document.documentElement.scrollWidth<=innerWidth+1,
        contained:Boolean(rect&&rect.left>=-1&&rect.right<=innerWidth+1),
      };
    })()`);
    assert.equal(initial.heading, 'Mastering');
    assert.ok(initial.fieldCount >= 8);
    assert.equal(initial.rejected, true);
    assert.equal(initial.named, true);
    assert.equal(initial.targetFloor, true);
    assert.equal(initial.noOverflow, true);
    assert.equal(initial.contained, true);

    const dialog = await applyMastering(session, server, 'room-a-2026');
    await session.screenshot('mastering-complete.png');
    const mastered = await session.evaluate(`(() => {
      const section=document.querySelector('[data-produce-mastering]');
      const current=document.querySelector('[data-produce-take][data-current="true"]');
      return {
        current:current?.dataset.produceTake||null,
        kindText:current?.textContent||'',
        summary:section.querySelector('[data-mastering-current]')?.textContent||'',
        pinRequired:/Final Listen pin required/.test(section.textContent),
        undo:Boolean(section.querySelector('[data-mastering-undo]')),
        noOverflow:document.documentElement.scrollWidth<=innerWidth+1,
      };
    })()`);
    assert.equal(mastered.current, 'rendition-mastered-1');
    assert.match(mastered.kindText, /needs listening|processed version/i);
    assert.match(mastered.summary, /Estimated loudness -20\.0 dBFS/);
    assert.match(mastered.summary, /C2PA not_present; signer trust not_evaluated/i);
    assert.match(mastered.summary, /does not establish Voice authorization/i);
    assert.equal(mastered.pinRequired, true);
    assert.equal(mastered.undo, true);
    assert.equal(mastered.noOverflow, true);

    await session.evaluate(`document.querySelector('[data-mastering-undo]')?.click()`);
    await session.waitFor(`document.querySelector('[data-produce-take="take-newest"]')?.dataset.current==='true'`);
    assert.equal(server.control.takeState.renditions.length, 0);
    await applyMastering(session, server, null);
    await session.waitFor(`document.querySelector('[data-mastering-bypass]')`);
    await session.evaluate(`document.querySelector('[data-mastering-bypass]')?.click()`);
    await session.waitFor(`document.querySelector('[data-produce-take="take-newest"]')?.dataset.current==='true'`);
    const bypass = await session.evaluate(`(() => ({
      current:document.querySelector('[data-produce-take][data-current="true"]')?.dataset.produceTake||null,
      retained:document.querySelectorAll('[data-produce-take]').length,
      masteringRetained:Boolean(document.querySelector('[data-produce-take^="rendition-mastered-"]')),
    }))()`);
    assert.equal(bypass.current, 'take-newest');
    assert.equal(bypass.masteringRetained, true);
    assert.equal(bypass.retained, 5);

    Object.assign(server.control.mastering, {
      running: true, cancel_requested: false, status: 'running',
      chunk_id: 'chunk:current-1', source_take_id: 'take-newest',
      completed_count: 3, total_count: 7,
      progress_message: 'Applying bounded dynamics', result: null,
      background_job_id: 'work_mastering_running',
    });
    await session.evaluate(`location.reload()`);
    await session.waitFor(`document.querySelector('[data-mastering-cancel]')`);
    const running = await session.evaluate(`(() => ({
      message:document.querySelector('[data-mastering-process]')?.textContent||'',
      noOverflow:document.documentElement.scrollWidth<=innerWidth+1,
    }))()`);
    assert.match(running.message, /Applying bounded dynamics/);
    assert.equal(running.noOverflow, true);
    await session.screenshot('mastering-running.png');
    await session.evaluate(`document.querySelector('[data-mastering-cancel]')?.click()`);
    await session.waitFor(`document.querySelector('[data-mastering-process="cancelled"]')`);
    const cancelled = await session.evaluate(`document.querySelector('[data-mastering-process="cancelled"]')?.textContent||''`);
    assert.match(cancelled, /No mastered rendition was published/);

    const runtimeErrors = session.client.events.filter((event) => (
      event.method === 'Runtime.exceptionThrown'
      || (event.method === 'Runtime.consoleAPICalled' && event.params?.type === 'error')
    ));
    assert.deepEqual(runtimeErrors, []);
    return {
      viewport, status: 'PASS', initial, dialog, mastered, bypass, running,
      requests: {
        plan: server.control.requests.filter((item) => /\/mastering\/plan$/.test(item.path)).length,
        apply: server.control.requests.filter((item) => /\/mastering\/apply$/.test(item.path)).length,
        cancel: server.control.requests.filter((item) => /\/background-work\/.*\/cancel$/.test(item.path)).length,
      },
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
    for (const [width, height] of VIEWPORTS) {
      results.push(await inspectViewport(server, artifacts, width, height));
    }
  } finally {
    server.release();
    await server.close();
  }
  const report = {
    status: results.every((item) => item.status === 'PASS') ? 'PASS' : 'FAIL',
    scenario: 'B20-T06 publication-safe mastering',
    viewports: VIEWPORTS,
    results,
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  writeJson(path.join(artifacts, 'cleanup.json'), {
    serverClosed: !server.server.listening,
    pendingResponses: server.control.pending.length,
  });
  process.stdout.write(`B20_T06_MASTERING=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
