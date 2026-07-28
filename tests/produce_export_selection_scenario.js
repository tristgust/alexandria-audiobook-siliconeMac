'use strict';

const {
  CDP_MODIFIER, HOST_ACCELERATOR, NON_HOST_ACCELERATOR,
  closeOverlayInspector, realKeyPress, realPointerClick,
} = require('./produce_export_browser_helpers.js');

const json = (value) => JSON.stringify(value);

async function selectedIds(session) {
  return session.evaluate(`[...document.querySelectorAll('[data-audio-row][aria-selected="true"]')]
    .map((row)=>row.dataset.chunkId)`);
}

async function exerciseSelection(session) {
  const visibleEligibleIds = await session.evaluate(
    `[...document.querySelectorAll('[data-audio-row]')].map((row)=>row.dataset.chunkId)`,
  );
  const selectionIds = visibleEligibleIds.slice(0, 4);
  const rowSelector = (id) => `[data-chunk-id="${id}"]`;
  const clickRow = async (id, modifiers = 0) => {
    await realPointerClick(session, rowSelector(id), modifiers);
    await closeOverlayInspector(session);
  };
  const clear = () => realPointerClick(session, '[data-produce-clear-selection]');
  const exact = async (expected) => json(await selectedIds(session)) === json(expected);

  await clickRow(selectionIds[0]);
  await clickRow(selectionIds[3], CDP_MODIFIER.shift);
  const plainShiftRange = await exact(selectionIds);
  await clear();

  await clickRow(selectionIds[0]);
  await clickRow(selectionIds[1], HOST_ACCELERATOR);
  const hostToggle = await exact(selectionIds.slice(0, 2));
  await clickRow(selectionIds[1], HOST_ACCELERATOR);
  const hostDeselect = await exact(selectionIds.slice(0, 1));
  await clear();

  await clickRow(selectionIds[0]);
  await realKeyPress(session, 'a', 'KeyA', NON_HOST_ACCELERATOR);
  const nonHostSelectAllExact = await exact(visibleEligibleIds);
  await realKeyPress(session, 'Escape', 'Escape');

  await clickRow(selectionIds[0]);
  await clickRow(selectionIds[3], HOST_ACCELERATOR);
  await clickRow(selectionIds[1], HOST_ACCELERATOR | CDP_MODIFIER.shift);
  const acceleratorShiftExtend = await exact(selectionIds);
  await clear();

  await clickRow(selectionIds[0]);
  await realPointerClick(session, '[data-produce-filter="stale"]');
  await realPointerClick(session, '[data-produce-filter="ready"]');
  await session.waitFor(`[...document.querySelectorAll('[data-audio-row]')].length===4
    &&[...document.querySelectorAll('[data-audio-row]')].every((row)=>row.dataset.audioState==='stale')`);
  const staleId = await session.evaluate(`document.querySelector('[data-audio-row]')?.dataset.chunkId`);
  await clickRow(staleId, CDP_MODIFIER.shift);
  const hiddenAnchorFallback = await exact([staleId]);
  await realPointerClick(session, '[data-produce-filter="stale"]');
  await realPointerClick(session, '[data-produce-filter="ready"]');
  await session.waitFor(`[...document.querySelectorAll('[data-audio-row]')].length===12
    &&[...document.querySelectorAll('[data-audio-row]')].every((row)=>row.dataset.audioState==='ready')`);
  await clear();

  await clickRow(selectionIds[0]);
  const focusBeforeSelectAll = await session.evaluate(
    `document.activeElement?.dataset.chunkId===${json(selectionIds[0])}`,
  );
  await realKeyPress(session, 'a', 'KeyA', HOST_ACCELERATOR);
  const selectAllExact = await exact(visibleEligibleIds);
  await realKeyPress(session, 'Escape', 'Escape');
  const escapeClears = await exact([]);

  await clickRow(selectionIds[0]);
  await clickRow(selectionIds[1], HOST_ACCELERATOR);
  return {
    selectionIds: selectionIds.slice(0, 2), visibleEligibleIds,
    hotkeys: {
      hostAccelerator: process.platform === 'darwin' ? 'Command' : 'Control',
      nonHostAccelerator: process.platform === 'darwin' ? 'Control' : 'Meta',
      plainShiftRange, hostToggle, hostDeselect, nonHostSelectAllExact,
      acceleratorShiftExtend, hiddenAnchorFallback, focusBeforeSelectAll,
      selectAllExact, escapeClears,
      mechanism: 'CDP Input.dispatchMouseEvent/Input.dispatchKeyEvent',
    },
  };
}

module.exports = { exerciseSelection, selectedIds };
