'use strict';

const {
  HOST_ACCELERATOR,
  closeOverlayInspector, normalizeScroll, realKeyPress, realPointerClick, requestAfterClick,
  setMode, snapshot, waitForVisualReady,
} = require('./produce_export_browser_helpers.js');
const { exerciseColumnResize } = require('./produce_export_resize_scenario.js');
const { exerciseSelection } = require('./produce_export_selection_scenario.js');
const {
  openScenario, producePageSize, scenarioResult,
} = require('./produce_export_scenario_common.js');

const json = (value) => JSON.stringify(value);

async function inspectProduce(server, artifacts, width, height) {
  const context = await openScenario(server, artifacts, 'produce', width, height);
  const {
    assertions, initial, owner, route, session, viewport, visualReady: initialVisualReady,
  } = context;
  let actionTargets = null;
  try {
    if (!initial.installed) return scenarioResult(session, viewport, assertions, initial);
    const pageSize = producePageSize(width);
    const expectedInitialRows = pageSize + 1;
    const expectedRowsAfterLoad = pageSize * 2 + 1;
    await session.evaluate(`document.querySelector('[data-produce-load-more]')?.click()`);
    await session.waitFor(`document.querySelectorAll('[data-audio-row]').length === ${expectedRowsAfterLoad}`);
    await closeOverlayInspector(session);
    const rowsAfterLoad = await session.evaluate(`document.querySelectorAll('[data-audio-row]').length`);

    const sectionAction = await requestAfterClick(
      session, server.control, '[data-produce-section-generate]', '/api/produce/generate', true,
    );
    const eligibleSectionRequests = server.control.requests.filter((item) =>
      ['/api/produce/plan', '/api/produce/generate'].includes(item.path)).slice(-2);
    const expectedSectionIds = Array.from({ length: 18 }, (_, index) =>
      `chunk:scale-${5251 + index}`);
    expectedSectionIds[13] = 'chunk:stale-1';
    await session.waitFor(`Boolean(document.querySelector('[data-produce-primary]:not(:disabled)'))`);

    await session.evaluate(`document.querySelector('[data-produce-filter="ready"]')?.click()`);
    await session.waitFor(`[...document.querySelectorAll('[data-audio-row]')].length===12
      &&[...document.querySelectorAll('[data-audio-row]')].every((row)=>row.dataset.audioState==='ready')`);
    const selection = await exerciseSelection(session);
    const selectionState = await session.evaluate(`(() => {
      const selected=[...document.querySelectorAll('[data-audio-row][aria-selected="true"]')];
      const first=selected[0],name=first?.querySelector('.audio-row__speaker');
      return {count:selected.length,
        multiselect:selected[0]?.closest('[role="listbox"]')?.getAttribute('aria-multiselectable'),
        noCheckboxes:document.querySelectorAll('[data-produce-section-select]').length===0,
        button:document.querySelector('[data-produce-section-generate]')?.textContent.trim()||'',
        colored:first?getComputedStyle(first).backgroundColor!==getComputedStyle(
          document.querySelector('[data-audio-row]:not([aria-selected="true"])')).backgroundColor:false,
        readableName:Boolean(name?.textContent.trim())&&getComputedStyle(name).whiteSpace!=='nowrap'
          &&getComputedStyle(name).textOverflow!=='ellipsis',
        resizeHandles:document.querySelectorAll('[data-produce-column-resize]').length};
    })()`);
    const selectedVisualReady = await waitForVisualReady(session);
    await session.screenshot('produce-selected.png');
    const columnResize = await exerciseColumnResize(session);
    await closeOverlayInspector(session);
    const selectedSectionAction = await requestAfterClick(
      session, server.control, '[data-produce-section-generate]', '/api/produce/generate', true,
    );
    const selectedSectionRequests = server.control.requests.filter((item) =>
      ['/api/produce/plan', '/api/produce/generate'].includes(item.path)).slice(-2);
    await session.waitFor(`Boolean(document.querySelector('[data-produce-batch-clear]'))`);
    await realPointerClick(session, '[data-produce-batch-clear]');
    await session.evaluate(`document.querySelector('[data-produce-filter="ready"]')?.click()`);
    await session.evaluate(`document.querySelector('[data-produce-filter="failed"]')?.click()`);
    await session.waitFor(`[...document.querySelectorAll('[data-audio-row]')].length===2
      &&[...document.querySelectorAll('[data-audio-row]')].every((row)=>row.dataset.audioState==='failed')`);
    const failedSelectionIds = await session.evaluate(
      `[...document.querySelectorAll('[data-audio-row]')].map((row)=>row.dataset.chunkId)`,
    );
    await realPointerClick(session, `[data-chunk-id="${failedSelectionIds[0]}"]`);
    await closeOverlayInspector(session);
    await realPointerClick(
      session, `[data-chunk-id="${failedSelectionIds[1]}"]`, HOST_ACCELERATOR,
    );
    await closeOverlayInspector(session);
    const failedBatchState = await session.evaluate(`(() => ({
      selected:[...document.querySelectorAll('[data-audio-row][aria-selected="true"]')]
        .map((row)=>row.dataset.chunkId),
      label:document.querySelector('[data-produce-batch-action]')?.textContent.trim()||'',
      summary:document.querySelector('[data-produce-batch-selection]')?.textContent.trim()||'',
      sectionLabel:document.querySelector('[data-produce-section-generate]')?.textContent.trim()||''
    }))()`);
    const failedBatchAction = await requestAfterClick(
      session, server.control, '[data-produce-batch-action]', '/api/produce/generate', true,
    );
    const failedBatchRequests = server.control.requests.filter((item) =>
      ['/api/produce/plan', '/api/produce/generate'].includes(item.path)).slice(-2);
    await session.waitFor(`Boolean(document.querySelector('[data-produce-batch-clear]'))`);
    await realPointerClick(session, '[data-produce-batch-clear]');
    await session.evaluate(`document.querySelector('[data-produce-filter="failed"]')?.click()`);
    await session.waitFor(`document.querySelectorAll('[data-audio-row]').length>2`);

    actionTargets = {
      eligible: sectionAction.target,
      selected: selectedSectionAction.target,
      failedSelected: failedBatchAction.target,
      selectionHotkeys: selection.hotkeys,
      columnResize,
      visualReadiness: { initial: initialVisualReady, selected: selectedVisualReady },
    };

    await session.waitFor(`Boolean(document.querySelector('[data-produce-primary]:not(:disabled)'))`);
    await normalizeScroll(session);
    const readyMenuPointerOpened = await realPointerClick(
      session, '[aria-label="More production actions"]',
    );
    await session.waitFor(`document.querySelector('[aria-label="More production actions"]')?.getAttribute('aria-expanded')==='true'`);
    await realKeyPress(session, 'ArrowDown', 'ArrowDown');
    const readyMenuMoved = await session.evaluate(
      `document.activeElement?.getAttribute('data-produce-action')==='retry'`,
    );
    await realKeyPress(session, 'ArrowUp', 'ArrowUp');
    const readyMenuState = await session.evaluate(`(() => {
      const opener=document.querySelector('[aria-label="More production actions"]');
      const item=document.querySelector('[data-produce-action="generate-ready"]');
      const menu=opener?.closest('.popover-controller')?.querySelector('[role="menu"]');
      const openerRect=opener?.getBoundingClientRect(),menuRect=menu?.getBoundingClientRect();
      return {expanded:opener?.getAttribute('aria-expanded')==='true',visible:menu?.hidden===false,
        labelled:menu?.getAttribute('aria-label')==='Produce actions',item:Boolean(item),
        itemName:item?.textContent.trim()||'',focused:document.activeElement===item,
        anchored:Boolean(openerRect&&menuRect&&menuRect.left<=openerRect.right&&menuRect.right>=openerRect.left),
        itemNames:[...(menu?.querySelectorAll('[role="menuitem"]')||[])].map((node)=>node.textContent.trim())};
    })()`);
    const menuPointer = await session.evaluate(`(() => {
      const rect=document.querySelector('[role="menu"]')?.getBoundingClientRect();
      return rect?{x:rect.left+rect.width/2,y:rect.top+rect.height/2}:null;
    })()`);
    if (menuPointer) {
      await session.client.send('Input.dispatchMouseEvent', {
        type: 'mouseMoved', x: menuPointer.x, y: menuPointer.y,
      });
      await session.evaluate(`new Promise((resolve)=>setTimeout(resolve,150))`);
    }
    await session.screenshot('produce-generate-ready-menu.png');
    await realKeyPress(session, 'Escape', 'Escape');
    const readyMenuEscape = await session.evaluate(`(() => {
      const opener=document.querySelector('[aria-label="More production actions"]');
      const menu=opener?.closest('.popover-controller')?.querySelector('[role="menu"]');
      return menu?.hidden===true&&document.activeElement===opener;
    })()`);
    server.control.producePlanBehavior = 'pending';
    const readyRequestStart = server.control.requests.length;
    const readyPlanStarted = await requestAfterClick(
      session, server.control, '[data-produce-action="generate-ready"]', '/api/produce/plan', false,
    );
    const readyLoadingState = await session.evaluate(`(() => {
      const opener=document.querySelector('[aria-label="More production actions"]');
      return opener?.disabled===true&&opener?.getAttribute('aria-label')==='More production actions';
    })()`);
    server.control.producePlanBehavior = 'normal';
    server.release();
    await session.waitFor(`document.querySelector('.produce-activity')?.textContent.includes('Audio generation started')`);
    const readyRequests = server.control.requests.slice(readyRequestStart).filter((item) =>
      ['/api/produce/plan', '/api/produce/generate'].includes(item.path));
    const readySuccessNotice = await session.evaluate(
      `document.querySelector('.produce-activity')?.textContent.includes('Audio generation started')||false`,
    );
    server.control.producePlanBehavior = 'error';
    const readyErrorStarted = await requestAfterClick(
      session, server.control, '[data-produce-action="generate-ready"]', '/api/produce/plan', false,
    );
    await session.waitFor(`document.querySelector('.produce-activity')?.textContent.includes('Audio plan unavailable')`);
    const readyErrorNotice = await session.evaluate(
      `document.querySelector('.produce-activity')?.textContent.includes('Fixture audio plan failed.')||false`,
    );
    server.control.producePlanBehavior = 'normal';
    const generated = await requestAfterClick(
      session, server.control, '[data-produce-primary]', '/api/produce/generate', false,
    );
    await session.waitFor(`Boolean(document.querySelector('[data-produce-action="retry"]:not(:disabled)'))`);
    const retried = await requestAfterClick(
      session, server.control, '[data-produce-action="retry"]', '/api/produce/retry-failed', false,
    );
    Object.assign(assertions, {
      selectedStaysStale: initial.selected === 'stale',
      compactOnly: initial.compactPlay > 0 && initial.waveforms > 0,
      referenceCounts: /5250\s+current/i.test(initial.text.replaceAll(',', ''))
        && /16\s+need generation/i.test(initial.text.replaceAll(',', ''))
        && /7\s+need listening/i.test(initial.text.replaceAll(',', ''))
        && /2\s+failed/i.test(initial.text.replaceAll(',', ''))
        && ['Ready to generate', 'Needs listening', 'Failed', 'Stale', 'Current']
          .every((label) => initial.text.includes(label)),
      boundedInitialRows: initial.audioRows === expectedInitialRows,
      boundedAfterLoad: rowsAfterLoad === expectedRowsAfterLoad,
      boundedHeight: width < 641 ? initial.ownerScrollHeight < 60000
        : initial.ownerScrollHeight <= height && initial.ownerClientHeight <= height,
      internalChunkScroll: width < 641 || (initial.produceClientHeight > 0
        && initial.produceScrollHeight > initial.produceClientHeight),
      usableChunkViewport: width < 641
        || initial.produceClientHeight >= Math.min(260, Math.round(height * .25)),
      truthfulCollectionCount: initial.collectionText.replaceAll(',', '').includes(
        `Showing ${expectedInitialRows} of 5275 chunks`,
      ),
      groupedAudioRows: initial.chapterGroups >= 1,
      canonicalColumns: ['Character', 'Text', 'Direction', 'Duration', 'Audio', 'Status']
        .every((label) => initial.columnHeaders.includes(label)),
      staleInspectorReason: /Stale reason/i.test(initial.inspectorText)
        && /(Direction edited after audio generation|Audio Fingerprint Mismatch)/i
          .test(initial.inspectorText),
      noProduceKpiStrip: initial.produceStats === 0,
      onePagePrimary: initial.pagePrimary === 1,
      sectionGenerated: sectionAction.clicked,
      sectionActionVisible: sectionAction.target?.contained === true && sectionAction.target?.hit === true,
      sectionUsesSelectedPlan: eligibleSectionRequests[0]?.path === '/api/produce/plan'
        && eligibleSectionRequests[0]?.body?.mode === 'selected'
        && eligibleSectionRequests[1]?.path === '/api/produce/generate'
        && eligibleSectionRequests[1]?.body?.mode === 'selected',
      sectionUsesCompleteSafeIds: json(eligibleSectionRequests[0]?.body?.selected_chunk_ids)
        === json(expectedSectionIds)
        && json(eligibleSectionRequests[1]?.body?.selected_chunk_ids) === json(expectedSectionIds),
      colorMultiselect: selection.selectionIds.length === 2 && selectionState.count === 2
        && selectionState.multiselect === 'true' && selectionState.noCheckboxes
        && selectionState.colored && /Generate 2 selected/.test(selectionState.button),
      standardSelectionHotkeys: Object.entries(selection.hotkeys)
        .filter(([key]) => !['hostAccelerator', 'nonHostAccelerator', 'mechanism'].includes(key))
        .every(([, value]) => value === true),
      selectedSectionGenerated: selectedSectionAction.clicked,
      selectedSectionActionVisible: selectedSectionAction.target?.contained === true
        && selectedSectionAction.target?.hit === true,
      selectedSectionUsesOnlyChosenRows: json(selectedSectionRequests[0]?.body?.selected_chunk_ids)
          === json(selection.selectionIds)
        && json(selectedSectionRequests[1]?.body?.selected_chunk_ids) === json(selection.selectionIds),
      failedRowsAreMultiselectable: json(failedBatchState.selected) === json(failedSelectionIds)
        && failedBatchState.label === 'Retry 2 selected'
        && /2 audio chunks selected/.test(failedBatchState.summary)
        && /2 failed/.test(failedBatchState.summary)
        && failedBatchState.sectionLabel === 'Retry 2 selected',
      failedBulkRetryStarted: failedBatchAction.clicked,
      failedBulkRetryUsesExactSelection: failedBatchRequests.length === 2
        && failedBatchRequests[0]?.path === '/api/produce/plan'
        && failedBatchRequests[0]?.body?.mode === 'selected'
        && json(failedBatchRequests[0]?.body?.selected_chunk_ids) === json(failedSelectionIds)
        && failedBatchRequests[1]?.path === '/api/produce/generate'
        && failedBatchRequests[1]?.body?.mode === 'selected'
        && json(failedBatchRequests[1]?.body?.selected_chunk_ids) === json(failedSelectionIds),
      readableCharacterNames: selectionState.readableName,
      generateReadyMenuAccessible: readyMenuPointerOpened && readyMenuMoved
        && readyMenuState.expanded && readyMenuState.visible
        && readyMenuState.labelled && readyMenuState.item && readyMenuState.focused
        && readyMenuState.anchored && readyMenuState.itemName === 'Generate ready audio'
        && json(readyMenuState.itemNames) === json([
          'Generate ready audio', 'Retry failed audio', 'Regenerate all audio…',
          'Clean up old takes…',
        ]) && readyMenuEscape,
      generateReadyShowsLoading: readyPlanStarted && readyLoadingState,
      generateReadyStarted: readySuccessNotice,
      generateReadyShowsError: readyErrorStarted && readyErrorNotice,
      generateReadyUsesExactMode: readyRequests.length === 2
        && readyRequests[0]?.path === '/api/produce/plan'
        && readyRequests[0]?.body?.mode === 'ready_only'
        && json(readyRequests[0]?.body?.selected_chunk_ids) === '[]'
        && readyRequests[1]?.path === '/api/produce/generate'
        && readyRequests[1]?.body?.mode === 'ready_only'
        && json(readyRequests[1]?.body?.selected_chunk_ids) === '[]',
      ...(!columnResize.available
        ? { responsiveColumnControlsHidden: selectionState.resizeHandles === 6
          && columnResize.before.visibleHandles === 0 }
        : { adjustableColumns: selectionState.resizeHandles === 6 && columnResize.focused
          && columnResize.pointerDrag && columnResize.arrowLeft && columnResize.arrowRight
          && columnResize.homeReset && columnResize.savedCustomWidth }),
      generated,
      retried,
    });

    await setMode(session, server.control, 'produce-running', route);
    await normalizeScroll(session);
    const runningVisualReady = await waitForVisualReady(session);
    const running = await snapshot(session, owner);
    actionTargets.runningState = running;
    actionTargets.visualReadiness.running = runningVisualReady;
    actionTargets.columnResize.persistedAfterNavigation = Boolean(columnResize.persisted)
      && running.characterColumn === columnResize.persisted.width;
    if (columnResize.available) {
      assertions.columnResizePersistence = actionTargets.columnResize.persistedAfterNavigation;
    }
    assertions.runningScrollNormalized = running.scrollX === 0 && running.scrollY === 0
      && running.workspaceScrollX === 0 && running.workspaceScrollY === 0;
    assertions.runningNoOverflow = running.overflow <= 1;
    assertions.runningControlsInline = [...running.filterGeometry,
      ...running.progressGeometry, ...running.cancelGeometry].every((item) => item.inlineVisible)
      && running.filterGeometry.length > 0 && running.progressGeometry.length === 1
      && running.cancelGeometry.length === 1;
    assertions.progressBanner = await session.evaluate(
      `document.querySelectorAll('.produce-progress-banner [role="progressbar"]').length===1
        &&document.querySelector('[data-produce-composite-progress] [role="progressbar"]')?.getAttribute('aria-valuenow')==='53.125'
        &&document.querySelector('.produce-progress-banner__copy')?.textContent.includes('8 of 16 terminal')
        &&document.querySelector('.produce-progress-banner__copy')?.textContent.includes('6 generated')
        &&document.querySelector('.produce-progress-banner__copy')?.textContent.includes('1 failed')
        &&document.querySelector('.produce-progress-banner__copy')?.textContent.includes('1 cancelled')
        &&document.querySelector('.produce-progress-banner__copy')?.textContent.includes('current file 50%')
        &&Boolean(document.querySelector('[data-produce-cancel]'))`,
    );
    assertions.runningDisablesMoreActions = await session.evaluate(
      `document.querySelector('[aria-label="More production actions"]')?.disabled===true`,
    );
    assertions.cancelled = await requestAfterClick(
      session, server.control, '[data-produce-primary]', '/api/produce/cancel', false,
    );
    await session.screenshot('produce-running.png');
    return scenarioResult(session, viewport, assertions, initial, actionTargets);
  } finally {
    await session.close();
  }
}

module.exports = { inspectProduce };
