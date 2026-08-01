'use strict';

async function runFullCastScenario({ assertions, details, server, session }) {
  await session.evaluate(`document.querySelector('[data-full-cast-tasks]').click()`);
  await session.waitFor(`Boolean(document.querySelector('[data-cast-workflow="advanced-character-operations"] [data-full-cast-tasks]'))`, 12000);
  details.fullCastTasks = await session.evaluate(`(() => {
    const layer=document.querySelector('[data-cast-workflow="advanced-character-operations"]');
    return {
      title: layer?.querySelector('h1')?.textContent || '',
      taskCount: layer?.querySelectorAll('[data-full-cast-task]').length || 0,
      text: layer?.innerText || '',
      importFile: Boolean(layer?.querySelector('[data-completed-task-file]')),
      dropzone: Boolean(layer?.querySelector('[data-task-import-dropzone]')),
      stepCount: layer?.querySelectorAll('.task-import-steps li').length || 0,
      fallbackCollapsed: layer?.querySelector('.task-import-fallback .disclosure__trigger')
        ?.getAttribute('aria-expanded') === 'false',
      overflow: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
    };
  })()`);
  assertions.fullCastTasksVisible = [
    'Full Cast and identity tasks',
    'Advanced identity operations',
  ].includes(details.fullCastTasks.title)
    && details.fullCastTasks.taskCount === 3
    && /relationships/i.test(details.fullCastTasks.text)
    && /aliases/i.test(details.fullCastTasks.text)
    && /Voice personas & designs/i.test(details.fullCastTasks.text)
    && details.fullCastTasks.importFile
    && details.fullCastTasks.dropzone
    && details.fullCastTasks.stepCount === 3
    && details.fullCastTasks.fallbackCollapsed
    && details.fullCastTasks.overflow <= 1;
  details.completeCastBundle = await session.evaluate(`(() => {
    const panel=document.querySelector('[data-complete-cast-bundle]');
    return {
      checked:[...panel.querySelectorAll('[data-cast-dossier-option] input')]
        .filter(input=>input.checked).length,
      labels:[...panel.querySelectorAll('[data-cast-dossier-option]')]
        .map(node=>node.textContent.trim()),
      local:panel.querySelector('[data-run-complete-cast-local]')?.textContent.trim()||'',
      external:panel.querySelector('[data-export-complete-cast]')?.textContent.trim()||'',
      status:panel.querySelector('.complete-cast-bundle__status')?.textContent.trim()||'',
      advancedOpen:document.querySelector('.full-cast-task-advanced')?.open===true,
    };
  })()`);
  assertions.completeCastBundleVisible = details.completeCastBundle.checked === 3
    && /Roster & relationships/.test(details.completeCastBundle.labels.join(' '))
    && /Voice personas & designs/.test(details.completeCastBundle.labels.join(' '))
    && /Visual dossiers/.test(details.completeCastBundle.labels.join(' '))
    && details.completeCastBundle.local === 'Run selected work locally'
    && details.completeCastBundle.external === 'Download Cast task bundle'
    && /No approved roster exists/.test(details.completeCastBundle.status)
    && details.completeCastBundle.advancedOpen === false;
  await session.evaluate(`document.querySelector('[data-run-complete-cast-local]').click()`);
  await session.waitFor(`document.querySelector('[data-complete-cast-bundle]')?.textContent.includes('Roster discovery started')`, 12000);
  details.localRosterDiscoveryRequest = server.control.requests
    .filter((request) => request.path === '/api/character_roster/discover').at(-1)?.body || null;
  assertions.localRunUsesRosterDiscovery = server.control.rosterDiscoveryStarted
    && details.localRosterDiscoveryRequest?.replace_draft === false;
  await session.evaluate(`document.querySelector('[data-cast-workflow-close]').click()`);
  await session.waitFor(`!document.querySelector('[data-cast-workflow]')`);
  server.control.approvedRosterAvailable = true;
  server.control.rosterDiscoveryStarted = false;
  server.control.enrichmentStarted = false;
  server.control.enrichmentReads = 0;
  await session.evaluate(`document.querySelector('[data-full-cast-tasks]').click()`);
  await session.waitFor(`Boolean(document.querySelector('[data-cast-workflow="advanced-character-operations"] [data-full-cast-tasks]'))`, 12000);
  details.approvedLocalAction = await session.evaluate(`(() => {
    const panel=document.querySelector('[data-complete-cast-bundle]');
    return {
      label:panel.querySelector('[data-run-complete-cast-local]')?.textContent.trim()||'',
      disabled:panel.querySelector('[data-run-complete-cast-local]')?.disabled===true,
      status:panel.querySelector('.complete-cast-bundle__status')?.textContent.trim()||'',
    };
  })()`);
  assertions.approvedLocalActionVisible = details.approvedLocalAction.label === 'Run selected work locally'
    && !details.approvedLocalAction.disabled
    && /approved roster will be preserved/i.test(details.approvedLocalAction.status);
  await session.evaluate(`document.querySelector('[data-run-complete-cast-local]').click()`);
  await session.waitFor(`document.querySelector('[data-complete-cast-bundle]')?.textContent.includes('Local Cast enrichment complete')`, 12000);
  details.localEnrichmentRequest = server.control.requests
    .filter((request) => request.path === '/api/character_roster/enrichment/run-selected').at(-1)?.body || null;
  assertions.localRunUsesSequentialEnrichment = server.control.enrichmentStarted
    && details.localEnrichmentRequest?.expected_roster_fingerprint === 'a'.repeat(64)
    && details.localEnrichmentRequest?.create_designed_voice_profiles === true
    && details.localEnrichmentRequest?.discover_visual_details === true
    && server.control.enrichmentReads >= 1;
  await session.evaluate(`document.querySelector('[data-export-complete-cast]').click()`);
  await session.waitFor(`document.querySelector('[data-complete-cast-bundle]')?.textContent.includes('Cast task bundle downloaded')`, 12000);
  details.completeCastExportRequest = server.control.requests
    .filter((request) => request.path === '/api/tasks/export').at(-1)?.body || null;
  details.completeCastDownloadRequest = server.control.requests
    .filter((request) => request.method === 'GET'
      && request.path === '/api/tasks/complete_cast_dossier/download').at(-1) || null;
  details.completeCastGeneratedLink = await session.evaluate(`Boolean(document.querySelector('[data-complete-cast-bundle] a[href*="/api/tasks/"]'))`);
  assertions.completeCastExport = details.completeCastExportRequest?.task_type === 'complete_cast_dossier'
    && details.completeCastExportRequest?.options?.roster_and_relationships === true
    && details.completeCastExportRequest?.options?.voice_personas_and_designs === true
    && details.completeCastExportRequest?.options?.visual_dossiers === true
    && Boolean(details.completeCastDownloadRequest)
    && details.completeCastGeneratedLink === false;
  await session.evaluate(`document.querySelector('.full-cast-task-advanced summary').click()`);
  await session.evaluate(`document.querySelector('[data-full-cast-task="roster_discovery"] button').click()`);
  await session.waitFor(`document.querySelector('[data-full-cast-task="roster_discovery"]')?.textContent.includes('Task bundle downloaded')`, 12000);
  details.fullCastExportRequest = server.control.requests
    .filter((request) => request.path === '/api/tasks/export').at(-1)?.body || null;
  details.fullCastDownloadRequest = server.control.requests
    .filter((request) => request.method === 'GET'
      && request.path === '/api/tasks/roster_discovery/download').at(-1) || null;
  details.fullCastGeneratedLink = await session.evaluate(`Boolean(document.querySelector('[data-full-cast-task="roster_discovery"] a[href*="/api/tasks/"]'))`);
  assertions.fullCastDiscoveryExport = details.fullCastExportRequest?.task_type === 'roster_discovery'
    && details.fullCastExportRequest?.target === null
    && Boolean(details.fullCastDownloadRequest)
    && details.fullCastGeneratedLink === false;
  await session.evaluate(`(() => {
    const input=document.querySelector('[data-completed-task-file]');
    const transfer=new DataTransfer();
    transfer.items.add(new File(
      ['fixture completed roster'],
      'cast-roster.alexandria-completed-task.zip',
      {type:'application/zip'},
    ));
    input.files=transfer.files;
    input.dispatchEvent(new Event('change',{bubbles:true}));
  })()`);
  await session.waitFor(`document.querySelector('[data-import-completed-task]')?.disabled === false`);
  details.taskImportSelected = await session.evaluate(`(() => ({
    dropHidden: document.querySelector('[data-task-import-dropzone]')?.hidden === true,
    selectedText: document.querySelector('.task-import-selected')?.innerText || '',
    validationCopy: document.querySelector('.task-import-surface__footer')?.innerText || '',
  }))()`);
  assertions.taskImportFileSelection = details.taskImportSelected.dropHidden
    && /cast-roster\.alexandria-completed-task\.zip/.test(details.taskImportSelected.selectedText)
    && /Nothing has changed/.test(details.taskImportSelected.validationCopy);
  await session.evaluate(`document.querySelector('[data-import-completed-task]').click()`);
  await session.waitFor(`Boolean(document.querySelector('[data-roster-import-review]'))`, 12000);
  details.rosterImportReview = await session.evaluate(`(() => {
    const review=document.querySelector('[data-roster-import-review]');
    const list=review?.querySelector('.roster-import-list');
    const listStyle=list?getComputedStyle(list):null;
    const relationships=[...review.querySelectorAll('.roster-import-metric')]
      .find((node)=>node.textContent.includes('Relationships'))?.textContent || '';
    const relationshipChoice=[...review.querySelectorAll('.choice')]
      .find((node)=>node.textContent.includes('Import relationships'));
    const voice=review.querySelector('[data-enrichment-option="voices"] input');
    const visuals=review.querySelector('[data-enrichment-option="visuals"] input');
    return {
      relationships,
      rowText: review.querySelector('.roster-import-row')?.innerText || '',
      safeText: review.querySelector('.roster-safe-changes')?.textContent || '',
      safeCollapsed: review.querySelector('.roster-safe-changes')?.open === false,
      relationshipChecked: relationshipChoice?.querySelector('input')?.checked === true,
      relationshipDisabled: relationshipChoice?.querySelector('input')?.disabled === true,
      voiceChecked: voice?.checked === true,
      visualChecked: visuals?.checked === true,
      listOverflow: listStyle?.overflowY || '',
      listMaxHeight: listStyle?.maxHeight || '',
      documentOverflow: Math.max(0,document.documentElement.scrollWidth-document.documentElement.clientWidth),
      applyVisible: Boolean(review.querySelector('[data-apply-roster-import]')),
    };
  })()`);
  assertions.rosterImportShowsEnrichment = /1/.test(details.rosterImportReview.relationships)
    && /Clara Leighton/.test(details.rosterImportReview.safeText)
    && details.rosterImportReview.safeCollapsed
    && details.rosterImportReview.relationshipChecked
    && details.rosterImportReview.relationshipDisabled
    && details.rosterImportReview.voiceChecked
    && details.rosterImportReview.visualChecked
    && details.rosterImportReview.listOverflow === 'auto'
    && details.rosterImportReview.documentOverflow <= 1
    && details.rosterImportReview.applyVisible;
  await session.screenshot('cast-roster-import-review.png');
  await session.evaluate(`document.querySelector('[data-apply-roster-import]').click()`);
  await session.waitFor(`Boolean(document.querySelector('[data-roster-approval-resume]'))`, 12000);
  details.rosterApplyRequest = server.control.requests
    .filter((request) => request.path === '/api/character_roster/reconciliation/apply').at(-1)?.body || null;
  assertions.rosterImportCheckmarksPersist = details.rosterApplyRequest?.create_designed_voice_profiles === true
    && details.rosterApplyRequest?.discover_visual_details === true
    && Array.isArray(details.rosterApplyRequest?.decisions)
    && details.rosterApplyRequest.decisions.length === 0;
  await session.evaluate(`document.querySelector('[data-roster-approval-resume] .ui-button').click()`);
  await session.waitFor(`document.querySelector('[data-roster-approval-resume]')?.textContent.includes('Cast enrichment complete')`, 12000);
  details.rosterApprovalRequest = server.control.requests
    .filter((request) => request.path === '/api/character_roster/reconciliation/approve').at(-1)?.body || null;
  details.rosterEnrichmentRequest = server.control.requests
    .filter((request) => request.path === '/api/character_roster/enrichment/start').at(-1)?.body || null;
  assertions.rosterImportApprovalAndEnrichment = server.control.rosterDraftApplied
    && server.control.rosterApproved
    && server.control.enrichmentStarted
    && details.rosterApprovalRequest?.action === 'approve_resolved'
    && details.rosterEnrichmentRequest?.expected_plan_fingerprint === 'p1'.padEnd(64, '1')
    && details.rosterEnrichmentRequest?.expected_roster_fingerprint === 'a'.repeat(64)
    && server.control.enrichmentReads >= 1;
  await session.screenshot('cast-roster-enrichment-complete.png');
  await session.evaluate(`document.querySelector('[data-cast-workflow-close]').click()`);
  await session.waitFor(`!document.querySelector('[data-cast-workflow]')`);
}

module.exports = { runFullCastScenario };
