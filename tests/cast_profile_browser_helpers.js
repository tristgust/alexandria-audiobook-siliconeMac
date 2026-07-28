'use strict';

async function press(session, key) {
  await session.client.send('Input.dispatchKeyEvent', { type: 'keyDown', key });
  await session.client.send('Input.dispatchKeyEvent', { type: 'keyUp', key });
}

function runtimeErrors(session) {
  return session.client.events.filter((item) => item.method === 'Runtime.exceptionThrown'
    || (item.method === 'Runtime.consoleAPICalled' && item.params?.type === 'error'));
}

async function snapshot(session) {
  return session.evaluate(`(() => {
    const page=document.querySelector('[data-cast-page]');
    const sections=[...document.querySelectorAll('[data-cast-section]')].map(n=>n.dataset.castSection);
    const textNodes=[...(page?.querySelectorAll('*')||[])].filter(n=>n.textContent.trim()&&getComputedStyle(n).display!=='none');
    const clipped=[...(page?.querySelectorAll('.cast-roster__row,[data-cast-identity]')||[])]
      .filter(n=>n.scrollWidth>n.clientWidth+1).map(n=>n.className);
    const pageBox=page?.getBoundingClientRect();
    const workspace=page?.querySelector('.cast-workspace');
    const workspaceStyle=workspace?getComputedStyle(workspace):null;
    const profile=page?.querySelector('[data-cast-profile]');
    const controlsOutside=[...(page?.querySelectorAll('button,input,select,textarea')||[])]
      .filter(n=>{const r=n.getBoundingClientRect(),s=getComputedStyle(n);return s.display!=='none'&&!n.closest('[hidden],.cast-roster__filters')&&(r.left<pageBox.left-1||r.right>pageBox.right+1)})
      .map(n=>n.getAttribute('aria-label')||n.textContent.trim()).slice(0,10);
    return {
      page:Boolean(page), state:page?.dataset.castState, roster:document.querySelectorAll('[data-cast-roster]').length,
      profile:document.querySelectorAll('[data-cast-profile]').length,
      workspaceColumns:workspaceStyle?.gridTemplateColumns||'',
      profileWidth:Math.round(profile?.getBoundingClientRect().width||0),
      listboxes:page?.querySelectorAll('[role="listbox"]').length||0,
      sections, identityBefore:page?.querySelector('[data-cast-identity]')?.compareDocumentPosition(page.querySelector('[data-cast-section]'))&Node.DOCUMENT_POSITION_FOLLOWING?true:false,
      overflow:Math.max(0,document.documentElement.scrollWidth-innerWidth),
      clipped, controlsOutside,
      focused:document.activeElement?.matches('[data-page-heading]')||false,
      injection:Boolean(globalThis.fixtureInjection||page?.querySelector('img')),
      minText:textNodes.length?Math.min(...textNodes.map(n=>parseFloat(getComputedStyle(n).fontSize))):0,
      selected:page?.querySelector('[role="option"][aria-selected="true"]')?.dataset.characterId||null,
      status:document.querySelector('[data-project-actions] [data-primitive="status"]')?.textContent.trim()||'',
      unsafeLiteral:Boolean(page?.textContent.includes('<img src=x onerror=')),
      profileText:page?.querySelector('[data-cast-profile]')?.innerText||'',
      text:page?.innerText||''
    };
  })()`);
}

module.exports = { press, runtimeErrors, snapshot };
