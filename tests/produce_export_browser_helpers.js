'use strict';

const json = (value) => JSON.stringify(value);
const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const CDP_MODIFIER = Object.freeze({ control: 2, meta: 4, shift: 8 });
const HOST_ACCELERATOR = process.platform === 'darwin'
  ? CDP_MODIFIER.meta : CDP_MODIFIER.control;
const NON_HOST_ACCELERATOR = process.platform === 'darwin'
  ? CDP_MODIFIER.control : CDP_MODIFIER.meta;

async function setMode(session, control, mode, route, loading = false) {
  control.mode = mode;
  control.pending.length = 0;
  const separator = route.includes('?') ? '&' : '?';
  await session.client.send('Page.navigate', {
    url: `${session.baseUrl}#/${route}${separator}source=fixture-run-${Date.now()}`,
  });
  const expected = mode.split('-').at(-1);
  await session.waitFor(loading
    ? `Boolean(document.querySelector('[data-page-state="loading"]')) || document.body.dataset.shellState === 'ready'`
    : `document.body.dataset.shellState === 'ready' && Boolean(document.querySelector('[data-page-state="${expected}"]'))`);
}

function runtimeErrors(session) {
  return session.client.events.filter((item) => item.method === 'Runtime.exceptionThrown'
    || (item.method === 'Runtime.consoleAPICalled' && item.params?.type === 'error'));
}

async function snapshot(session, owner) {
  return session.evaluate(`(() => {
    const root=document.querySelector('[data-route-owner="${owner}"]');
    const controls=[...(root?.querySelectorAll('button,a[href],input,[tabindex]')||[])].filter(n=>!n.disabled);
    const produceScroll=root?.querySelector('.produce-content');
    const exportScroll=root?.querySelector('.export-grid');
    const inlineGeometry=(selector)=>[...(root?.querySelectorAll(selector)||[])]
      .filter((node)=>node.offsetParent!==null)
      .map((node)=>{const rect=node.getBoundingClientRect();return {
        left:rect.left,right:rect.right,width:rect.width,viewportWidth:innerWidth,
        inlineVisible:rect.width>0&&rect.left>=0&&rect.right<=innerWidth,
      };});
    return {
      owner:Boolean(root), installed:Boolean(root?.matches('[data-${owner}-page]')), state:root?.dataset.pageState||null,
      overflow:document.documentElement.scrollWidth-innerWidth,
      scrollX:window.scrollX, scrollY:window.scrollY,
      workspaceScrollX:document.querySelector('.workspace')?.scrollLeft||0,
      workspaceScrollY:document.querySelector('.workspace')?.scrollTop||0,
      characterColumn:produceScroll
        ? parseFloat(getComputedStyle(produceScroll).getPropertyValue('--produce-character-column'))||0
        : 0,
      focus:document.activeElement?.matches('[data-page-heading]')||false,
      injection:Boolean(globalThis.fixtureInjection||root?.querySelector('img')),
      minText:Math.min(...[...(root?.querySelectorAll('*')||[])].filter(n=>n.textContent.trim()&&getComputedStyle(n).display!=='none').map(n=>parseFloat(getComputedStyle(n).fontSize))),
      unnamedControls:controls.filter(n=>!(n.getAttribute('aria-label')||n.textContent
        ||n.closest('label')?.textContent
        ||(n.id&&document.querySelector('label[for="'+CSS.escape(n.id)+'"]')?.textContent)||'').trim())
        .map(n=>({tag:n.tagName,id:n.id,className:n.className,role:n.getAttribute('role'),type:n.getAttribute('type')})),
      named:controls.every(n=>(n.getAttribute('aria-label')||n.textContent
        ||n.closest('label')?.textContent
        ||(n.id&&document.querySelector('label[for="'+CSS.escape(n.id)+'"]')?.textContent)||'').trim()),
      persistentInside:root?.querySelectorAll('[data-primitive="persistent-player"]').length||0,
      compactPlay:root?.querySelectorAll('[data-primitive="compact-play"]').length||0,
      waveforms:root?.querySelectorAll('[data-primitive="waveform"]').length||0,
      selected:root?.querySelector('[data-audio-row][data-active="true"]')?.dataset.audioState||null,
      audioRows:root?.querySelectorAll('[data-audio-row]').length||0,
      collectionText:root?.querySelector('[data-produce-collection-footer]')?.textContent||'',
      chapterGroups:root?.querySelectorAll('.produce-chapter-group').length||0,
      columnHeaders:root?.querySelector('.audio-table__header')?.textContent||'',
      produceStats:root?.querySelectorAll('.produce-stat').length||0,
      filterGeometry:inlineGeometry('.produce-filter'),
      progressGeometry:inlineGeometry('.produce-progress-banner [role="progressbar"]'),
      cancelGeometry:inlineGeometry('[data-produce-cancel]'),
      inspectorText:root?.querySelector('.produce-inspector')?.textContent
        ||document.querySelector('[data-shell-inspector]')?.textContent||'',
      pagePrimary:document.querySelectorAll('[data-project-header] .ui-button[data-variant="primary"]:not(:disabled)').length,
      publicationCover:Boolean(root?.querySelector('.export-publication .source-cover')),
      finalWaveformDisabled:root?.querySelector('.export-preview .waveform__slider')?.getAttribute('aria-disabled')==='true',
      downloadAction:Boolean(root?.querySelector('[data-export-download]')),
      ownerScrollHeight:root?.scrollHeight||0, ownerClientHeight:root?.clientHeight||0,
      produceScrollHeight:produceScroll?.scrollHeight||0, produceClientHeight:produceScroll?.clientHeight||0,
      exportScrollHeight:exportScroll?.scrollHeight||0, exportClientHeight:exportScroll?.clientHeight||0,
      pageTitleHeight:root?.querySelector('.page-title-block')?.getBoundingClientRect().height||0,
      readinessHeight:root?.querySelector('.export-readiness:not([hidden])')?.getBoundingClientRect().height||0,
      finishLineHeight:root?.querySelector('.export-finish-line')?.getBoundingClientRect().height||0,
      validationHeight:root?.querySelector('.export-validation-panel')?.getBoundingClientRect().height||0,
      projectTitle:document.querySelector('[data-shell-project-title]')?.textContent||'',
      navProjectTitle:document.querySelector('[data-nav-project-title]')?.textContent||'',
      projectGroupVisible:Boolean(document.querySelector('[data-nav-group="project"]')&&!document.querySelector('[data-nav-group="project"]').hidden),
      projectContextVisible:Boolean(document.querySelector('[data-nav-project-context]')&&!document.querySelector('[data-nav-project-context]').hidden),
      projectHref:document.querySelector('[data-nav-project-link]')?.getAttribute('href')||'',
      statuses:[...(root?.querySelectorAll('[data-status-value]')||[])].map(n=>n.dataset.statusValue),
      text:root?.innerText||'', headerText:document.querySelector('[data-project-header]')?.innerText||''
    };
  })()`);
}

async function requestAfterClick(session, control, selector, pathName, realPointer = false) {
  const before = control.requests.length;
  let target = null;
  if (realPointer) {
    target = await pointerTarget(session, selector);
    if (!target?.contained || !target.hit) return { clicked: false, target };
    await dispatchPointer(session, target.x, target.y);
  } else {
    const clicked = await session.evaluate(`document.querySelector(${json(selector)})?.click(); Boolean(document.querySelector(${json(selector)}))`);
    if (!clicked) return false;
  }
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (control.requests.slice(before).some((item) => item.path === pathName)) {
      return realPointer ? { clicked: true, target } : true;
    }
    await wait(25);
  }
  return realPointer ? { clicked: false, target } : false;
}

async function pointerTarget(session, selector) {
  return session.evaluate(`(async () => {
    const node=document.querySelector(${json(selector)});
    if(!node) return null;
    node.scrollIntoView({block:'center',inline:'nearest'});
    await new Promise((resolve)=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    const rect=node.getBoundingClientRect(),x=rect.left+rect.width/2,y=rect.top+rect.height/2;
    const hit=document.elementFromPoint(x,y);
    return {x,y,width:rect.width,height:rect.height,
      contained:rect.width>0&&rect.height>0&&rect.left>=0&&rect.top>=0&&rect.right<=innerWidth&&rect.bottom<=innerHeight,
      hit:Boolean(hit&&(hit===node||node.contains(hit))),
      hitNode:hit?(hit.tagName+'.'+hit.className+' '+(hit.textContent||'')).slice(0,180):''};
  })()`);
}

async function dispatchPointer(session, x, y, modifiers = 0) {
  await session.client.send('Input.dispatchMouseEvent', {
    type: 'mousePressed', x, y, button: 'left', clickCount: 1, modifiers,
  });
  await session.client.send('Input.dispatchMouseEvent', {
    type: 'mouseReleased', x, y, button: 'left', clickCount: 1, modifiers,
  });
}

async function realPointerClick(session, selector, modifiers = 0) {
  const target = await pointerTarget(session, selector);
  if (!target?.hit) return false;
  await dispatchPointer(session, target.x, target.y, modifiers);
  await session.evaluate(`new Promise((resolve)=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
  return true;
}

async function realKeyPress(session, key, code, modifiers = 0) {
  await session.client.send('Input.dispatchKeyEvent', {
    type: 'keyDown', key, code, modifiers,
  });
  await session.client.send('Input.dispatchKeyEvent', {
    type: 'keyUp', key, code, modifiers,
  });
  await session.evaluate(`new Promise((resolve)=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
}

async function normalizeScroll(session) {
  await session.evaluate(`(async () => {
    window.scrollTo(0,0);
    for(const selector of ['.workspace','.nav-rail','.produce-content','.produce-main']) {
      const node=document.querySelector(selector);if(node) node.scrollTo({top:0,left:0,behavior:'instant'});
    }
    await new Promise((resolve)=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  })()`);
}

async function waitForVisualReady(session) {
  await session.waitFor(`Boolean(document.querySelector('link[href*="font-awesome"]')?.sheet)`);
  await session.evaluate(`(async () => {await document.fonts.ready;await Promise.all([
    document.fonts.load('900 16px "Font Awesome 6 Free"','\uf015\uf1da\uf04b'),
    document.fonts.load('400 16px "Font Awesome 6 Free"','\uf15c')]);})()`);
  const ready = await session.waitFor(`(() => {
    const icons=[...document.querySelectorAll('.nav-item i,.ui-icon-button i')].filter((node)=>node.offsetParent!==null);
    const rendered=icons.length>0&&icons.every((icon)=>{const style=getComputedStyle(icon,'::before');
      return style.fontFamily.includes('Font Awesome')&&style.content!=='none'&&style.content!=='normal'&&style.content!=='""';});
    return document.fonts.status==='loaded'&&document.fonts.check('900 16px "Font Awesome 6 Free"')
      &&document.fonts.check('400 16px "Font Awesome 6 Free"')&&rendered
      ?{fontStatus:document.fonts.status,icons:icons.length,rendered}:false;})()`);
  await session.evaluate(`new Promise((resolve)=>requestAnimationFrame(()=>requestAnimationFrame(()=>setTimeout(resolve,100))))`);
  return ready;
}

async function closeOverlayInspector(session) {
  const selector = '[data-page-inspector-close]';
  const target = await pointerTarget(session, selector);
  if (!target?.hit) return false;
  await dispatchPointer(session, target.x, target.y);
  await session.waitFor(`document.querySelector(${json(selector)})?.offsetParent===null`);
  return true;
}

module.exports = {
  CDP_MODIFIER, HOST_ACCELERATOR, NON_HOST_ACCELERATOR, closeOverlayInspector,
  normalizeScroll, realKeyPress, realPointerClick, requestAfterClick, runtimeErrors,
  setMode, snapshot, wait, waitForVisualReady,
};
