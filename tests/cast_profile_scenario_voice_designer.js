'use strict';

const { runtimeErrors } = require('./cast_profile_browser_helpers.js');

async function runVoiceDesignerScenario(context) {
  const { assertions, details, server, session, width } = context;
  await session.evaluate(`document.querySelector('[data-cast-more]').click()`);
  await session.waitFor(`Boolean([...document.querySelectorAll('[role="menuitem"]')].find((item) => item.textContent.includes('Voice designer')))`);
  await session.evaluate(`[...document.querySelectorAll('[role="menuitem"]')].find((item) => item.textContent.includes('Voice designer')).click()`);
  try {
    await session.waitFor(`document.querySelector('[data-cast-workflow="voice-designer"] [data-route-owner="more/voice-designer"]')?.dataset.viewState==='ready'`);
  } catch (error) {
    const diagnostic = await session.evaluate(`(() => {
      const layer = document.querySelector('[data-cast-workflow]');
      const owner = layer?.querySelector('[data-route-owner]');
      return {
        route: document.body.dataset.routePath,
        layer: layer?.dataset.castWorkflow || '',
        owner: owner?.dataset.routeOwner || '',
        state: owner?.dataset.viewState || '',
        text: layer?.innerText || '',
      };
    })()`);
    process.stderr.write(`CAST_WORKFLOW_DIAGNOSTIC=${JSON.stringify({ diagnostic, events: runtimeErrors(session), requests: server.control.requests.slice(-12) })}\n`);
    throw error;
  }
  const workflow = await session.evaluate(`(() => {
    const layer = document.querySelector('[data-cast-workflow="voice-designer"]');
    const drawer = layer?.querySelector('.cast-workflow-drawer');
    const rect = drawer?.getBoundingClientRect();
    return {
      route: document.body.dataset.routePath,
      focused: Boolean(layer?.contains(document.activeElement)),
      width: Math.round(rect?.width || 0),
      height: Math.round(rect?.height || 0),
      title: layer?.querySelector('h1')?.textContent || '',
    };
  })()`);
  assertions.workflowInCast = workflow.route === 'cast'
    && workflow.focused && workflow.title === 'Voice designer';
  assertions.workflowResponsive = width < 640
    ? workflow.width === width
    : workflow.width <= 760 && workflow.width >= 560;
  assertions.workflowViewport = workflow.height === context.height;
  await session.screenshot('cast-voice-designer-drawer.png');
  await session.evaluate(`(() => {
    const layer=document.querySelector('[data-cast-workflow="voice-designer"]');
    const name=layer.querySelector('#voice-designer-name');
    const description=layer.querySelector('#voice-designer-description');
    name.value='Fixture Designed Voice';
    description.value='A warm, precise project audition with restrained energy.';
    layer.querySelector('.voice-designer-form button[type="submit"]')?.click();
  })()`);
  await session.waitFor(`document.querySelector('#voice-designer-output')?.dataset.previewReady==='true'`);
  const designerPreview = await session.evaluate(`(() => ({
    noNativeTransport:document.querySelectorAll('#voice-designer-output audio[controls]').length===0,
    persistentSource:document.querySelector('audio.persistent-player__media')?.getAttribute('src')||'',
    saveVisible:[...document.querySelectorAll('.voice-designer-form button')]
      .some((button)=>button.textContent.trim()==='Save Voice'&&!button.hidden&&!button.disabled),
    reusableChecked:document.querySelector('[data-voice-designer-reusable]')?.checked===true,
  }))()`);
  assertions.voiceDesignerPersistentPreview = designerPreview.noNativeTransport
    && /fixture-designed-audition/.test(designerPreview.persistentSource)
    && designerPreview.saveVisible && !designerPreview.reusableChecked;
  await session.evaluate(`document.querySelector('#voice-designer-output')
    ?.scrollIntoView({block:'center',inline:'nearest'})`);
  await session.screenshot('cast-voice-designer-preview.png');
  await session.evaluate(`[...document.querySelectorAll('.voice-designer-form button')]
    .find((button)=>button.textContent.trim()==='Save Voice')?.click()`);
  await session.waitFor(`/Project Designed Voice saved/.test(document.querySelector('#voice-designer-output')?.innerText||'')`);
  details.voiceDesignerSavePayload = server.control.requests
    .filter((request) => request.path === '/api/voice_design/save').at(-1)?.body || null;
  assertions.voiceDesignerProjectScope = details.voiceDesignerSavePayload?.scope === 'project';
  await session.evaluate(`document.querySelector('#voice-designer-output')
    ?.scrollIntoView({block:'center',inline:'nearest'})`);
  await session.screenshot('cast-voice-designer-project-saved.png');
  await session.evaluate(`document.querySelector('[data-cast-workflow-close]')?.click()`);
  await session.waitFor(`!document.querySelector('[data-cast-workflow]')`);
  details.workflowClose = await session.evaluate(`(() => {
    const active = document.activeElement;
    const more = document.querySelector('[data-cast-more]');
    return {
      tag: active?.tagName || '',
      text: active?.textContent?.trim() || '',
      isMore: active === more,
      moreConnected: Boolean(more?.isConnected),
      state: document.querySelector('[data-cast-page]')?.dataset.castState || '',
    };
  })()`);
  assertions.workflowCloseReturns = details.workflowClose.isMore
    && details.workflowClose.state === 'ready';
}

module.exports = { runVoiceDesignerScenario };
