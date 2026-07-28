'use strict';

const fs = require('fs');
const path = require('path');
const { BrowserSession } = require('./b19_t06_bootstrap_red.js');
const { runtimeErrors } = require('./cast_profile_browser_helpers.js');
const { realPointerClick } = require('./produce_export_browser_helpers.js');
const {
  CHARACTER_ID, EXCLUDED_AUDIT, SAMPLE_LINE, SCRIPT_SPEAKER,
} = require('./cast_profile_fixture_speaker_recovery.js');

const json = (value) => JSON.stringify(value);

function pressEnter(session) {
  const common = {
    key: 'Enter', code: 'Enter',
    windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13,
  };
  [{
    type: 'keyDown', ...common, text: '\r', unmodifiedText: '\r',
  }, { type: 'keyUp', ...common }].forEach((params) => {
    session.client.socket.send(JSON.stringify({
      id: session.client.nextId++, method: 'Input.dispatchKeyEvent', params,
    }));
  });
}

async function audit(session, selector) {
  return session.evaluate(`(() => {
    const root=document.querySelector(${json(selector)})||document.body;
    const visible=(node)=>{const style=getComputedStyle(node),box=node.getBoundingClientRect();
      return style.display!=='none'&&style.visibility!=='hidden'&&box.width>0&&box.height>0;};
    const controls=[...root.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])')].filter(visible);
    const textNodes=[...root.querySelectorAll('*')].filter((node)=>visible(node)&&node.textContent.trim());
    const name=(node)=>(node.getAttribute('aria-label')||node.textContent
      ||node.closest('label')?.textContent
      ||(node.id&&document.querySelector('label[for="'+CSS.escape(node.id)+'"]')?.textContent)||'').trim();
    return {
      overflow:Math.max(0,document.documentElement.scrollWidth-innerWidth),
      unnamedControls:controls.filter((node)=>!name(node)).map((node)=>node.outerHTML.slice(0,160)),
      minText:textNodes.length?Math.min(...textNodes.map((node)=>parseFloat(getComputedStyle(node).fontSize))):0,
      route:document.body.dataset.routePath||'', text:root.innerText||'',
    };
  })()`);
}

function requestFor(control, pathName, method) {
  return control.requests.findLast((item) => item.path === pathName && item.method === method) || null;
}

async function inspectViewport(server, artifacts, width, height) {
  const viewport = `${width}x${height}`;
  const folder = path.join(artifacts, viewport);
  fs.mkdirSync(folder, { recursive: true });
  Object.assign(server.control, {
    mode: 'speaker-recovery', recoveryActive: false, recoveryUndone: false,
    recoveryRejectNext: true, undoRejectNext: true,
    selected: 'cast:clara', savedConfig: null,
  });
  server.control.requests.length = 0;
  const session = await BrowserSession.open({
    url: `${server.url}#/produce?project=fixture-project&chunk=chunk%3Ablocked-1`,
    artifacts: folder, width, height,
  });
  const assertions = {};
  const details = { actions: [] };
  try {
    await session.waitFor(`document.body.dataset.shellState==='ready'&&document.querySelector('[data-produce-page]')?.dataset.pageState==='blocked'`);
    await realPointerClick(session, '[data-audio-row]');
    await session.waitFor(`Boolean(document.querySelector('.produce-inspector__blockers'))`);
    await session.screenshot('01-produce-blocker.png');
    const produce = await audit(session, '[data-produce-page]');
    details.produce = produce;
    details.produceActions = await session.evaluate(`[...document.querySelectorAll('.produce-inspector__blockers button')].map((node)=>({text:node.textContent.trim(),recovery:node.hasAttribute('data-produce-speaker-recovery')}))`);
    details.produceBlockerLayout = await session.evaluate(`(() => {
      const notice=document.querySelector('.produce-inspector__blockers .notice');
      const copy=notice?.querySelector(':scope > div');
      const button=notice?.querySelector(':scope > button');
      if(!notice||!copy||!button)return null;
      const n=notice.getBoundingClientRect(),c=copy.getBoundingClientRect(),b=button.getBoundingClientRect();
      return {noticeWidth:n.width,copyWidth:c.width,copyBottom:c.bottom,buttonTop:b.top,buttonHeight:b.height};
    })()`);
    assertions.recoveryActionPresent = await session.evaluate(`Boolean(document.querySelector('[data-produce-speaker-recovery]'))`);
    assertions.produceBlockerCompact = Boolean(details.produceBlockerLayout)
      && details.produceBlockerLayout.copyWidth >= 150
      && details.produceBlockerLayout.buttonHeight <= 52
      && details.produceBlockerLayout.buttonTop >= details.produceBlockerLayout.copyBottom - 1;
    assertions.produceResponsive = produce.overflow <= 1;
    assertions.produceControlsNamed = produce.unnamedControls.length === 0;
    if (!assertions.recoveryActionPresent) {
      details.actions.push('Produce blocker inspected; recovery action is absent.');
      assertions.noRuntimeErrors = runtimeErrors(session).length === 0;
      return {
        viewport, status: 'FAIL', assertions, details,
        runtimeErrors: runtimeErrors(session), screenshot: path.join(folder, '01-produce-blocker.png'),
      };
    }

    details.actions.push('Opened recovery from the real Produce blocker action.');
    assertions.recoveryActionClicked = await realPointerClick(session, '[data-produce-speaker-recovery]');
    await session.waitFor(`document.body.dataset.routePath==='more/advanced-character-operations'&&Boolean(document.querySelector('[data-speaker-recovery]'))`);
    await session.screenshot('02-recovery-evidence.png');
    const before = await audit(session, '[data-speaker-recovery]');
    details.before = before;
    details.recoveryHash = await session.evaluate('location.hash');
    assertions.routeContextPinned = details.recoveryHash.includes('character=VICAR')
      && details.recoveryHash.includes('mode=speaker-recovery')
      && details.recoveryHash.includes('return=');
    assertions.exactEvidenceVisible = before.text.includes(SCRIPT_SPEAKER)
      && before.text.includes('1 Script line') && before.text.includes(SAMPLE_LINE);
    assertions.exclusionAuditVisible = before.text.includes(EXCLUDED_AUDIT.reason)
      && before.text.includes(EXCLUDED_AUDIT.evidence[0].source_quote);
    assertions.recoveryEligible = await session.evaluate(`document.querySelector('[data-speaker-recovery]')?.dataset.speakerRecoveryState==='eligible'`);
    assertions.recoveryResponsive = before.overflow <= 1 && before.minText >= 13;
    assertions.recoveryControlsNamed = before.unnamedControls.length === 0;

    const actionsBeforeReview = server.control.requests.filter((item) => item.path === '/api/speaker_management/action').length;
    await realPointerClick(session, '[data-speaker-recovery-review]');
    await session.waitFor(`Boolean(document.querySelector('[role="dialog"]'))`);
    await session.screenshot('03-recovery-confirmation.png');
    assertions.confirmationDoesNotMutate = server.control.requests.filter(
      (item) => item.path === '/api/speaker_management/action',
    ).length === actionsBeforeReview;
    assertions.explicitConfirmation = await session.evaluate(`(() => {
      const button=[...document.querySelectorAll('.dialog__footer button')].find((node)=>node.textContent.trim()==='Recover speaker');
      button?.focus();return document.activeElement===button;
    })()`);
    await pressEnter(session);
    await session.waitFor(`document.querySelector('[data-speaker-recovery]')?.innerText.includes('Speaker recovery was rejected')`);
    await session.screenshot('03a-recovery-rejected.png');
    assertions.rejectionPreservesStateAndFocus = server.control.recoveryActive === false
      && await session.evaluate(`document.activeElement===document.querySelector('[data-speaker-recovery-review]')`);
    assertions.recoveryFeedbackSingleLiveRegion = await session.evaluate(`(() => {
      const panel=document.querySelector('[data-speaker-recovery]');
      return panel?.querySelectorAll('[aria-live]').length===1
        && !panel.querySelector('[aria-live] [aria-live]');
    })()`);
    details.actions.push('Verified a rejected confirmation preserves state and restores focus.');
    await realPointerClick(session, '[data-speaker-recovery-review]');
    await session.waitFor(`Boolean(document.querySelector('[role="dialog"]'))`);
    await session.evaluate(`[...document.querySelectorAll('.dialog__footer button')].find((node)=>node.textContent.trim()==='Recover speaker')?.focus()`);
    await pressEnter(session);
    try {
      await session.waitFor(`document.querySelector('[data-speaker-recovery]')?.dataset.speakerRecoveryState==='active'`, 5000);
    } catch (error) {
      const diagnostic = await session.evaluate(`({
        hash:location.hash, shellState:document.body.dataset.shellState,
        recoveryState:document.querySelector('[data-speaker-recovery]')?.dataset.speakerRecoveryState||null,
        dialog:Boolean(document.querySelector('[role="dialog"]')),
        focus:document.activeElement?.textContent?.trim()||'',
        text:document.body.innerText.slice(0,3000)
      })`);
      throw new Error(`Recovery confirmation failed: ${JSON.stringify({
        diagnostic, requests: server.control.requests, runtimeErrors: runtimeErrors(session),
      })}; ${error.message}`);
    }
    details.actions.push('Confirmed recovery with Enter and waited for guarded add refresh.');
    const addRequest = requestFor(server.control, '/api/speaker_management/action', 'POST');
    details.addRequest = addRequest?.body || null;
    assertions.guardedAddPayload = addRequest?.body?.operation === 'add'
      && addRequest?.body?.expected_script_fingerprint === 'fixture-script'
      && addRequest?.body?.payload?.script_speaker === SCRIPT_SPEAKER
      && addRequest?.body?.payload?.display_name === 'Vicar'
      && addRequest?.body?.payload?.expected_roster_fingerprint === 'fixture-roster'
      && addRequest?.body?.payload?.require_exclusion_audit === true
      && !Object.hasOwn(addRequest?.body?.payload || {}, 'voice')
      && !Object.hasOwn(addRequest?.body?.payload || {}, 'designed_voice_description');

    assertions.castActionAvailable = await session.evaluate(`Boolean(document.querySelector('[data-speaker-recovery-open-cast]'))`);
    await realPointerClick(session, '[data-speaker-recovery-open-cast]');
    await session.waitFor(`document.body.dataset.routePath==='cast'&&document.querySelector('[data-cast-page]')?.dataset.castState==='ready'`);
    const castBeforeEdit = await session.evaluate(`({
      selected:document.querySelector('[data-cast-profile] [data-cast-identity] h2')?.textContent||'',
      profile:document.querySelector('[data-cast-profile]')?.innerText||''
    })`);
    details.castBeforeEdit = castBeforeEdit;
    assertions.activeIdentityInCast = castBeforeEdit.selected === 'Vicar'
      && (castBeforeEdit.profile.includes('Missing voice')
        || castBeforeEdit.profile.includes('Production Voice incomplete'))
      && server.control.voiceAssignments === 0 && server.control.savedConfig === null;
    await realPointerClick(session, '[data-cast-edit-voice]');
    await session.waitFor(`Boolean(document.querySelector('[data-cast-voice-method]'))`);
    await session.screenshot('04-cast-voice-editor.png');
    assertions.normalVoiceEditorReachable = await session.evaluate(`document.activeElement===document.querySelector('[data-cast-voice-method]')`);

    await session.evaluate('history.back()');
    await session.waitFor(`document.body.dataset.routePath==='more/advanced-character-operations'&&document.querySelector('[data-speaker-recovery]')?.dataset.speakerRecoveryState==='active'`);
    await realPointerClick(session, '.full-cast-operation-history > summary');
    await session.waitFor(`Boolean(document.querySelector('[data-speaker-operation-undo]'))`);
    assertions.undoFocused = await session.evaluate(`(() => {const button=document.querySelector('[data-speaker-operation-undo]');button?.focus();return document.activeElement===button;})()`);
    await pressEnter(session);
    await session.waitFor(`document.querySelector('.full-cast-operation-history')?.innerText.includes('Undo is no longer available')`);
    await session.evaluate(`document.querySelector('.full-cast-operation-history__feedback')?.scrollIntoView({block:'center'})`);
    details.undoRejection = await session.evaluate(`(() => {
      const host=document.querySelector('.full-cast-operation-history__feedback');
      const content=host?.parentElement,notice=host?.querySelector('.notice');
      const body=notice?.querySelector('.notice__body'),player=document.querySelector('[data-persistent-player]');
      if(!host||!content||!notice||!body)return null;
      const h=host.getBoundingClientRect(),c=content.getBoundingClientRect();
      const n=notice.getBoundingClientRect(),b=body.getBoundingClientRect();
      const p=player?.getBoundingClientRect(),visibleBottom=Math.min(innerHeight,p?.top??innerHeight);
      return {
        hostLeft:h.left,hostRight:h.right,hostWidth:h.width,
        contentLeft:c.left,contentRight:c.right,contentWidth:c.width,
        noticeWidth:n.width,bodyText:body.textContent.trim(),
        bodyWidth:b.width,bodyHeight:b.height,bodyTop:b.top,bodyBottom:b.bottom,
        visibleBottom,liveNodeCount:host.querySelectorAll('[aria-live]').length,
        nestedLiveNodeCount:host.querySelectorAll('[aria-live] [aria-live]').length,
      };
    })()`);
    await session.screenshot('04a-undo-rejected.png');
    assertions.undoRejectionPreservesStateAndFocus = server.control.recoveryActive === true
      && server.control.recoveryUndone === false
      && await session.evaluate(`(() => {
        const button=document.querySelector('[data-speaker-operation-undo]');
        return Boolean(button&&!button.disabled&&document.activeElement===button);
      })()`);
    assertions.undoRejectionFullSpan = Boolean(details.undoRejection)
      && Math.abs(details.undoRejection.hostLeft - details.undoRejection.contentLeft) <= 2
      && Math.abs(details.undoRejection.hostRight - details.undoRejection.contentRight) <= 2
      && details.undoRejection.noticeWidth >= details.undoRejection.contentWidth - 2;
    assertions.undoRejectionReadable = Boolean(details.undoRejection)
      && details.undoRejection.bodyText === 'A newer identity change must be reviewed first.'
      && details.undoRejection.bodyWidth > 0 && details.undoRejection.bodyHeight > 0
      && details.undoRejection.bodyTop >= 0
      && details.undoRejection.bodyBottom <= details.undoRejection.visibleBottom + 1;
    assertions.undoFeedbackSingleLiveRegion = Boolean(details.undoRejection)
      && details.undoRejection.liveNodeCount === 1 && details.undoRejection.nestedLiveNodeCount === 0;
    details.actions.push('Verified a rejected Undo preserves state, feedback, and focus.');
    await pressEnter(session);
    await session.waitFor(`document.querySelector('[data-speaker-recovery]')?.dataset.speakerRecoveryState==='eligible'`);
    await realPointerClick(session, '.full-cast-operation-history > summary');
    await session.waitFor(`document.querySelector('.full-cast-operation-history')?.open===true`);
    await session.screenshot('05-recovery-after-undo.png');
    details.actions.push('Returned from Cast and invoked Undo with Enter.');
    const after = await audit(session, '[data-page="advanced-identity-operations"]');
    details.after = after;
    details.undoRequest = requestFor(server.control, '/api/speaker_management/undo', 'POST')?.body || null;
    assertions.undoRemovedIdentity = server.control.recoveryActive === false
      && details.undoRequest?.operation_id === 'speaker-add-vicar';
    assertions.undoKeptExclusionAudit = after.text.includes(EXCLUDED_AUDIT.reason)
      && after.text.includes(EXCLUDED_AUDIT.evidence[0].source_quote);
    assertions.historyStillAuditable = after.text.includes('Recent identity operations (2)')
      && after.text.includes('Audit record') && after.text.includes('Undone');
    assertions.undoCannotRepeat = await session.evaluate(`!document.querySelector('[data-speaker-operation-undo]')`);
    assertions.finalResponsive = after.overflow <= 1 && after.minText >= 13;
    assertions.finalControlsNamed = after.unnamedControls.length === 0;
    assertions.noRuntimeErrors = runtimeErrors(session).length === 0;
    return {
      viewport, status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL',
      assertions, details, runtimeErrors: runtimeErrors(session),
      screenshots: fs.readdirSync(folder).filter((name) => name.endsWith('.png')),
    };
  } finally {
    server.release();
    await session.close();
  }
}

module.exports = { inspectViewport };
