'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert').strict;
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');
const { fixtureServer } = require('./produce_export_fixture_server.js');

const VIEWPORTS = [[390, 844], [768, 1024], [1024, 768], [1536, 1024]];

function resetState(server) {
  const state = server.control.takeState;
  state.currentId = 'take-newest';
  state.pinnedId = null;
  state.pauseAfterMs = 350;
  state.renditions = [];
  state.nextRendition = 1;
  state.kept.clear();
  state.deleted.clear();
  state.undo.clear();
  server.control.mode = 'produce-takes';
}

function requests(server, suffix) {
  return server.control.requests.filter((item) => (
    `${item.method} ${item.path}` === suffix
  ));
}

async function keyEnter(session, selector) {
  await session.evaluate(`(() => {
    const summary=document.querySelector(${JSON.stringify(selector)});
    summary?.focus();
    summary?.dispatchEvent(new KeyboardEvent('keydown',{
      key:'Enter',code:'Enter',bubbles:true,cancelable:true,
    }));
  })()`);
}

async function inspectViewport(server, artifacts, width, height) {
  resetState(server);
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
    await session.waitFor(`document.querySelector('[data-produce-final-listen]')`);
    await session.screenshot('final-listen-initial.png');
    const initial = await session.evaluate(`(() => {
      const section=document.querySelector('[data-produce-final-listen]');
      const inspector=document.querySelector('.produce-inspector');
      const rect=inspector?.getBoundingClientRect();
      const controls=[...section.querySelectorAll('button,input,summary')];
      const visibleControls=controls.filter((control)=>{
        const box=control.getBoundingClientRect();
        const style=getComputedStyle(control);
        return box.width>0&&box.height>0&&style.display!=='none'&&style.visibility!=='hidden';
      });
      return {
        heading:section.querySelector('h3')?.textContent?.trim()||'',
        chapter:section.querySelector('.produce-final-listen-chapter')?.textContent?.trim()||'',
        status:section.querySelector('[data-status]')?.textContent?.trim()||section.textContent,
        transitions:[...section.querySelectorAll('[data-final-listen-transition]')]
          .map((item)=>item.dataset.finalListenTransition),
        correctionsOpen:Boolean(section.querySelector('[data-final-listen-corrections]')?.open),
        controlsNamed:controls.every((control)=>control.tagName==='INPUT'
          ? control.labels?.length>0
          : Boolean(control.textContent.trim()||control.getAttribute('aria-label'))),
        controlFloor:visibleControls.filter((control)=>control.tagName!=='INPUT')
          .every((control)=>control.getBoundingClientRect().height>=32),
        noOverflow:document.documentElement.scrollWidth<=innerWidth+1,
        inspectorContained:Boolean(rect&&rect.left>=-1&&rect.right<=innerWidth+1),
      };
    })()`);
    assert.equal(initial.heading, 'Final Listen');
    assert.match(initial.chapter, /Chapter Two/);
    assert.deepEqual(initial.transitions, ['previous', 'current', 'next']);
    assert.equal(initial.correctionsOpen, false);
    assert.equal(initial.controlsNamed, true);
    assert.equal(initial.controlFloor, true);
    assert.equal(initial.noOverflow, true);
    assert.equal(initial.inspectorContained, true);

    for (const target of ['previous', 'current', 'next']) {
      await session.evaluate(`document.querySelector('[data-final-listen-play="${target}"]')?.click()`);
      await session.waitFor(`document.querySelector('[data-persistent-player]')?.dataset.mediaSource?.includes('${target === 'current' ? 'current-1' : `${target}-1`}')`);
    }

    const pinBefore = server.control.requests.filter((item) => (
      item.method === 'POST' && /\/final-listen\/pin$/.test(item.path)
    )).length;
    await session.evaluate(`document.querySelector('[data-final-listen-pin]')?.click()`);
    await session.waitFor(`document.querySelector('[data-final-listen-pin]')?.textContent.includes('Remove Final Listen pin')`);
    const pinAfter = server.control.requests.filter((item) => (
      item.method === 'POST' && /\/final-listen\/pin$/.test(item.path)
    )).length;
    assert.equal(pinAfter - pinBefore, 1);
    assert.equal(server.control.takeState.pinnedId, 'take-newest');

    await keyEnter(session, '[data-final-listen-corrections] > summary');
    await session.waitFor(`document.querySelector('[data-final-listen-corrections]')?.open===true`);
    const keyboard = await session.evaluate(`(() => ({
      focusable:document.querySelector('[data-final-listen-corrections] > summary')?.tabIndex>=0,
      open:document.querySelector('[data-final-listen-corrections]')?.open===true,
    }))()`);
    assert.equal(keyboard.focusable, true);
    assert.equal(keyboard.open, true);

    await session.evaluate(`(() => {
      const input=document.querySelector('[data-final-listen-pause]');
      input.value='900'; input.dispatchEvent(new Event('input',{bubbles:true}));
      document.querySelector('[data-final-listen-pause-apply]')?.click();
    })()`);
    await session.waitFor(`document.querySelector('[data-final-listen-pause]')?.value==='900'`);
    assert.equal(server.control.takeState.pauseAfterMs, 900);
    const pauseRequest = [...server.control.requests].reverse().find((item) => (
      item.method === 'POST' && /\/final-listen\/pause$/.test(item.path)
    ));
    assert.equal(pauseRequest.body.pause_after_ms, 900);
    assert.equal(pauseRequest.body.source_order_fingerprint, 's'.repeat(64));
    await session.evaluate(`[...document.querySelectorAll('button')].find((button)=>button.textContent.trim()==='Undo pause')?.click()`);
    await session.waitFor(`document.querySelector('[data-final-listen-pause]')?.value==='350'`);
    assert.equal(server.control.takeState.pauseAfterMs, 350);

    await keyEnter(session, '[data-final-listen-corrections] > summary');
    await session.waitFor(`document.querySelector('[data-final-listen-corrections]')?.open===true`);
    await session.evaluate(`(() => {
      document.querySelector('[data-final-listen-trim-start]').value='120';
      document.querySelector('[data-final-listen-trim-end]').value='140';
      document.querySelector('[data-final-listen-trim-apply]')?.click();
    })()`);
    await session.waitFor(`document.querySelector('[data-produce-take="rendition-final-1"]')?.dataset.current==='true'`);
    assert.equal(server.control.takeState.currentId, 'rendition-final-1');
    const trimRequest = [...server.control.requests].reverse().find((item) => (
      item.method === 'POST' && /\/final-listen\/rendition$/.test(item.path)
    ));
    assert.equal(trimRequest.body.operation, 'trim_edges');
    assert.equal(trimRequest.body.trim_start_ms, 120);
    assert.equal(trimRequest.body.trim_end_ms, 140);
    assert.equal(trimRequest.body.take_id, 'take-newest');
    await session.evaluate(`[...document.querySelectorAll('button')].find((button)=>button.textContent.trim()==='Undo rendition')?.click()`);
    await session.waitFor(`document.querySelector('[data-produce-take="take-newest"]')?.dataset.current==='true'`);
    assert.equal(server.control.takeState.currentId, 'take-newest');

    await keyEnter(session, '[data-final-listen-corrections] > summary');
    await session.waitFor(`document.querySelector('[data-final-listen-corrections]')?.open===true`);
    await session.evaluate(`(() => {
      document.querySelector('[data-final-listen-split-at]').value='4100';
      document.querySelector('[data-final-listen-split-pause]').value='360';
      document.querySelector('[data-final-listen-split-apply]')?.click();
    })()`);
    await session.waitFor(`document.querySelector('[data-produce-take="rendition-final-1"]')?.dataset.current==='true'`);
    const splitRequest = [...server.control.requests].reverse().find((item) => (
      item.method === 'POST' && /\/final-listen\/rendition$/.test(item.path)
    ));
    assert.equal(splitRequest.body.operation, 'split_with_pause');
    assert.equal(splitRequest.body.split_at_ms, 4100);
    assert.equal(splitRequest.body.pause_ms, 360);
    assert.equal(server.control.takeState.renditions.length, 1);

    await session.evaluate(`document.querySelector('[data-produce-earlier-takes]')?.setAttribute('open','')`);
    await session.evaluate(`document.querySelector('[data-produce-take-use="rendition-reviewed"]')?.click()`);
    await session.waitFor(`document.querySelector('[data-produce-take="rendition-reviewed"]')?.dataset.current==='true'`);
    assert.equal(server.control.takeState.currentId, 'rendition-reviewed');
    assert.equal(server.control.takeState.pinnedId, null);

    await session.screenshot('final-listen-edited.png');
    const finalState = await session.evaluate(`(() => {
      const section=document.querySelector('[data-produce-final-listen]');
      const rect=document.querySelector('.produce-inspector')?.getBoundingClientRect();
      return {
        current:document.querySelector('[data-produce-take][data-current="true"]')?.dataset.produceTake||null,
        pinLabel:section.querySelector('[data-final-listen-pin]')?.textContent.trim()||'',
        takeCount:document.querySelectorAll('[data-produce-take]').length,
        noOverflow:document.documentElement.scrollWidth<=innerWidth+1,
        inspectorContained:Boolean(rect&&rect.left>=-1&&rect.right<=innerWidth+1),
        runtimeText:section.textContent,
      };
    })()`);
    assert.equal(finalState.current, 'rendition-reviewed');
    assert.equal(finalState.pinLabel, 'Pin current Take');
    assert.equal(finalState.takeCount, 5);
    assert.equal(finalState.noOverflow, true);
    assert.equal(finalState.inspectorContained, true);
    assert.match(finalState.runtimeText, /raw Take remains unchanged/i);
    assert.match(finalState.runtimeText, /Script remains one chunk/i);

    const runtimeErrors = session.client.events.filter((event) => (
      event.method === 'Runtime.exceptionThrown'
      || (event.method === 'Runtime.consoleAPICalled' && event.params?.type === 'error')
    ));
    assert.deepEqual(runtimeErrors, []);
    return {
      viewport,
      status: 'PASS',
      initial,
      keyboard,
      finalState,
      requests: {
        pin: server.control.requests.filter((item) => /\/final-listen\/pin$/.test(item.path)).length,
        pause: server.control.requests.filter((item) => /\/final-listen\/pause$/.test(item.path)).length,
        rendition: server.control.requests.filter((item) => /\/final-listen\/rendition$/.test(item.path)).length,
        undo: server.control.requests.filter((item) => item.path === '/api/produce/takes/undo').length,
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
    scenario: 'B20-T05 Chapter Assembly and Final Listen',
    viewports: VIEWPORTS,
    results,
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  writeJson(path.join(artifacts, 'cleanup.json'), {
    serverClosed: !server.server.listening,
    pendingResponses: server.control.pending.length,
  });
  process.stdout.write(`B20_T05_CHAPTER_ASSEMBLY=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
