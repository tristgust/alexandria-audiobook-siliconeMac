'use strict';

const fs = require('fs');
const path = require('path');
const {
  argsFrom, BrowserSession, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');
const { fixtureServer } = require('./produce_export_fixture_server.js');
const {
  CDP_MODIFIER, closeOverlayInspector, realKeyPress, realPointerClick,
} = require('./produce_export_browser_helpers.js');

const jsonEqual = (left, right) => JSON.stringify(left) === JSON.stringify(right);
const expectedEligible = ['chunk:ready-1', 'chunk:stale-1'];
const eligibleRows = `([...document.querySelectorAll('[data-audio-row]')]
  .filter((row) => ['ready', 'stale'].includes(row.dataset.audioState)))`;
const currentRows = `([...document.querySelectorAll('[data-audio-row]')]
  .filter((row) => row.dataset.audioState === 'current'))`;

async function observeSelection(session) {
  return session.evaluate(`(() => {
    const pageState = document.querySelector('[data-produce-page]')?.dataset.pageState || null;
    return {
      selected: [...document.querySelectorAll('[data-audio-row][aria-selected="true"]')]
        .map((row) => row.dataset.chunkId),
      pageState,
      running: pageState === 'running',
    };
  })()`);
}

async function openProduce(server, folder, mode) {
  server.control.mode = mode;
  const session = await BrowserSession.open({
    url: `${server.url}#/produce?chunk=ready-1`, artifacts: folder, width: 1024, height: 900,
  });
  session.baseUrl = server.url;
  await session.waitFor(`document.body.dataset.shellState === 'ready'
    && Boolean(document.querySelector('[data-produce-page]'))
    && document.querySelectorAll('[data-audio-row]').length > 0`);
  return session;
}

async function focusRows(session, rowsExpression = eligibleRows, index = 0) {
  return session.evaluate(`(() => {
    const rows = ${rowsExpression};
    const row = rows[${index}];
    row?.focus();
    return { id: row?.dataset.chunkId || null, focused: document.activeElement === row };
  })()`);
}

async function inspectBusy(server, artifacts, modifiers, label) {
  const folder = path.join(artifacts, label);
  fs.mkdirSync(folder, { recursive: true });
  const session = await openProduce(server, folder, 'produce-running');
  try {
    const focus = await focusRows(session);
    const pointerActivated = await realPointerClick(session, `[data-chunk-id="${focus.id}"]`);
    const pointerSelection = await observeSelection(session);
    await focusRows(session);
    await realKeyPress(session, ' ', 'Space');
    const spaceSelection = await observeSelection(session);
    await focusRows(session);
    await realKeyPress(session, 'a', 'KeyA', modifiers);
    const selectAllSelection = await observeSelection(session);
    await session.screenshot('busy-select-all.png');
    return {
      label, mode: 'produce-running', modifiers,
      mechanism: 'CDP Input.dispatchMouseEvent + Input.dispatchKeyEvent',
      focus, pointerActivated, pointerSelection, spaceSelection, selectAllSelection,
      pass: focus.focused && pointerActivated
        && pointerSelection.selected.length === 0
        && spaceSelection.selected.length === 0
        && selectAllSelection.running
        && selectAllSelection.selected.length === 0,
    };
  } finally {
    await session.close();
  }
}

async function inspectBusyToIdle(server, artifacts) {
  const folder = path.join(artifacts, 'busy-to-idle');
  fs.mkdirSync(folder, { recursive: true });
  const session = await openProduce(server, folder, 'produce-running');
  try {
    await focusRows(session);
    await realKeyPress(session, 'a', 'KeyA', CDP_MODIFIER.meta);
    const busySelection = await observeSelection(session);
    server.control.mode = 'produce-ready';
    await session.waitFor(`document.querySelector('[data-produce-page]')?.dataset.pageState === 'ready'`);
    const idleSelection = await observeSelection(session);
    await session.screenshot('busy-to-idle.png');
    return {
      label: 'busy-to-idle', mechanism: 'CDP Input.dispatchKeyEvent + existing Produce poll',
      busySelection, idleSelection,
      pass: busySelection.running && busySelection.selected.length === 0
        && !idleSelection.running && idleSelection.selected.length === 0,
    };
  } finally {
    await session.close();
  }
}

async function inspectIdleSelectAll(server, artifacts, modifiers, rowsExpression, label) {
  const folder = path.join(artifacts, label);
  fs.mkdirSync(folder, { recursive: true });
  const session = await openProduce(server, folder, 'produce-ready');
  try {
    const focus = await focusRows(session, rowsExpression);
    await realKeyPress(session, 'a', 'KeyA', modifiers);
    const selected = await observeSelection(session);
    await session.screenshot('idle-select-all.png');
    return {
      label, mode: 'produce-ready', modifiers,
      mechanism: 'CDP Input.dispatchKeyEvent', focus, selected,
      pass: focus.focused && !selected.running && jsonEqual(selected.selected, expectedEligible),
    };
  } finally {
    await session.close();
  }
}

async function inspectIdleSpace(server, artifacts) {
  const folder = path.join(artifacts, 'idle-space');
  fs.mkdirSync(folder, { recursive: true });
  const session = await openProduce(server, folder, 'produce-ready');
  try {
    const focus = await focusRows(session, eligibleRows, 1);
    await realKeyPress(session, ' ', 'Space');
    const selected = await observeSelection(session);
    await session.screenshot('idle-space.png');
    return {
      label: 'idle-space', mechanism: 'CDP Input.dispatchKeyEvent', focus, selected,
      pass: focus.focused && jsonEqual(selected.selected, ['chunk:stale-1']),
    };
  } finally {
    await session.close();
  }
}

async function inspectIdleShiftSpace(server, artifacts) {
  const folder = path.join(artifacts, 'idle-shift-space');
  fs.mkdirSync(folder, { recursive: true });
  const session = await openProduce(server, folder, 'produce-ready');
  try {
    const first = await focusRows(session, eligibleRows, 0);
    const pointerActivated = await realPointerClick(session, `[data-chunk-id="${first.id}"]`);
    await closeOverlayInspector(session);
    const second = await focusRows(session, eligibleRows, 1);
    await realKeyPress(session, ' ', 'Space', CDP_MODIFIER.shift);
    const selected = await observeSelection(session);
    await session.screenshot('idle-shift-space.png');
    return {
      label: 'idle-shift-space', mechanism: 'CDP Input.dispatchMouseEvent + Input.dispatchKeyEvent',
      first, pointerActivated, second, selected,
      pass: first.focused && pointerActivated && second.focused
        && jsonEqual(selected.selected, expectedEligible),
    };
  } finally {
    await session.close();
  }
}

async function inspectIdleEscape(server, artifacts) {
  const folder = path.join(artifacts, 'idle-escape');
  fs.mkdirSync(folder, { recursive: true });
  const session = await openProduce(server, folder, 'produce-ready');
  try {
    await focusRows(session);
    await realKeyPress(session, 'a', 'KeyA', CDP_MODIFIER.meta);
    await focusRows(session);
    await realKeyPress(session, 'Escape', 'Escape');
    const selected = await observeSelection(session);
    await session.screenshot('idle-escape.png');
    return {
      label: 'idle-escape', mechanism: 'CDP Input.dispatchKeyEvent', selected,
      pass: jsonEqual(selected.selected, []),
    };
  } finally {
    await session.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required(args, 'artifacts'));
  const server = await fixtureServer();
  const report = { scenario: 'Produce selection keyboard behavior', status: 'FAIL' };
  try {
    const busy = [
      await inspectBusy(server, artifacts, CDP_MODIFIER.control, 'busy-control-a'),
      await inspectBusy(server, artifacts, CDP_MODIFIER.meta, 'busy-meta-a'),
    ];
    const busyToIdle = await inspectBusyToIdle(server, artifacts);
    const idleSelectAll = [
      await inspectIdleSelectAll(server, artifacts, CDP_MODIFIER.control, eligibleRows, 'idle-control-a'),
      await inspectIdleSelectAll(server, artifacts, CDP_MODIFIER.meta, eligibleRows, 'idle-meta-a'),
      await inspectIdleSelectAll(server, artifacts, CDP_MODIFIER.meta, currentRows, 'idle-ineligible-meta-a'),
    ];
    const idleSpace = await inspectIdleSpace(server, artifacts);
    const idleShiftSpace = await inspectIdleShiftSpace(server, artifacts);
    const idleEscape = await inspectIdleEscape(server, artifacts);
    const all = [...busy, busyToIdle, ...idleSelectAll, idleSpace, idleShiftSpace, idleEscape];
    report.status = all.every((result) => result.pass) ? 'PASS' : 'FAIL';
    report.results = { busy, busyToIdle, idleSelectAll, idleSpace, idleShiftSpace, idleEscape };
    report.assertions = {
      busyCtrlAUnavailable: busy[0].pass,
      busyCmdAUnavailable: busy[1].pass,
      busyToIdleNoLatentSelection: busyToIdle.pass,
      idleCrossPlatformSelectAll: idleSelectAll[0].pass && idleSelectAll[1].pass,
      idleIneligibleSelectAll: idleSelectAll[2].pass,
      idleSpace: idleSpace.pass,
      idleShiftSpace: idleShiftSpace.pass,
      idleEscape: idleEscape.pass,
    };
  } finally {
    server.release();
    await server.close();
  }
  writeJson(path.join(artifacts, 'report.json'), report);
  writeJson(path.join(artifacts, 'cleanup.json'), {
    serverClosed: !server.server.listening,
    pendingResponses: server.control.pending.length,
    backendWrites: server.control.requests.filter((item) => (
      ['POST', 'PUT', 'PATCH', 'DELETE'].includes(item.method)
    )),
  });
  process.stdout.write(`PRODUCE_BUSY_SELECTION=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.stack || error);
    process.exitCode = 2;
  });
}

module.exports = { inspectBusy, inspectBusyToIdle, inspectIdleSelectAll };
