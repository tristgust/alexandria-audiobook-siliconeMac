'use strict';

const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { BrowserSession, argsFrom, required, writeJson } = require('./b19_t06_bootstrap_red.js');

const ROOT = path.resolve(__dirname, '..');
const STATIC = path.join(ROOT, 'app', 'static');
const SHOWCASE = path.join(STATIC, 'primitive_showcase.html');
const DESIGN = path.join(ROOT, 'DESIGN.md');
const VIEWPORTS = [[1536, 1024], [1440, 1000], [1199, 900], [1180, 900], [1179, 900], [1024, 768], [390, 844]];
const SOURCES = ['primitive_showcase.html', 'styles/tokens.css', 'styles/shell.css', 'styles/components.css', 'components/button.js', 'components/icon_button.js', 'components/form_controls.js', 'components/status.js', 'components/notice.js', 'components/disclosure.js', 'components/dialog.js', 'components/transport.js'];
const PRIMITIVES = ['app-shell', 'nav-rail', 'global-header', 'project-header', 'stage-tracker', 'page-title', 'button', 'icon-button', 'field', 'textarea', 'select', 'checkbox', 'radio-group', 'toggle', 'segmented-control', 'filter-chip', 'search-field', 'flat-section', 'divider-list', 'listbox', 'master-detail', 'portrait', 'monogram', 'source-cover', 'status', 'notice', 'progress', 'popover', 'modal', 'drawer', 'skeleton', 'empty-state', 'inline-save', 'disclosure', 'compact-play', 'waveform', 'persistent-player'];
const FACTORIES = ['appShell', 'shellInspector', 'navRail', 'globalHeader', 'projectHeader', 'stageTracker', 'pageTitleBlock', 'flatSection', 'dividerList', 'masterDetail', 'portrait', 'monogram', 'sourceCover', 'button', 'iconButton', 'field', 'checkbox', 'radioGroup', 'toggle', 'segmentedControl', 'filterChip', 'searchField', 'listbox', 'secretField', 'status', 'notice', 'progress', 'popover', 'dialog', 'compactPlay', 'waveform', 'persistentPlayer'];
const PLAYER_CONTROLS = ['previous', 'skip-back', 'play-pause', 'skip-forward', 'next', 'volume', 'queue', 'overflow'];

const listFrom = (text) => text.replace(/`/g, '').replace(/\band\b/g, ',').split(/[,/]/).map(value => value.trim().replace(/\s+with linked message$/, '')).filter(Boolean);
const slug = (value) => value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
function contractFromDesign() {
  const design = fs.readFileSync(DESIGN, 'utf8');
  const take = (pattern, label) => { const match = design.match(pattern); if (!match) throw new Error(`DESIGN.md contract missing: ${label}`); return match.slice(1); };
  const buttonVariants = listFrom(take(/`Button` variants are ([^;]+);/, 'button variants')[0]);
  const buttonStates = listFrom(take(/Required states are ([^.]+)\./, 'button states')[0]);
  const fieldStates = listFrom(take(/`Field` \/ `Textarea` \/ `Select` \|[^|]*optional description, ([^;]+); preserves/, 'field states')[0]);
  const [workflow, cast, audio, exportStates] = take(/Canonical state vocabularies are: workflow ([^;]+); Cast ([^;]+); audio ([^;]+); Export ([^.]+)\./, 'status vocabularies').map(value => value.split('/').map(item => item.trim()));
  const inlineSave = listFrom(take(/Save states are ([^.]+)\./, 'inline-save states')[0]);
  const progress = [...listFrom(take(/`Progress` covers ([^.]+)\./, 'progress states')[0]), 'indeterminate'];
  const playerText = design.slice(design.indexOf('`PersistentPlayer` is the single full transport.'));
  const player = listFrom((playerText.match(/States are ([^.]+)\./) || [])[1] || '');
  const castText = take(/Order is ([^.]+)\./, 'Cast order')[0].split('→').map(value => value.trim()).slice(1);
  const castOrder = castText.map(value => slug(value.replace(/^(dominant|approved|subordinate)\s+/i, '').replace(/\/transcript/i, '').replace(/\s+summary$/i, '')));
  const status = { workflow, cast, audio, export: exportStates };
  return {
    designSha256: crypto.createHash('sha256').update(design).digest('hex'), buttonVariants, buttonStates, fieldStates, status, inlineSave, progress, player, castOrder,
    domStates: [...buttonVariants.flatMap(variant => buttonStates.map(state => `button:${variant}:${state}`)), ...buttonStates.map(state => `icon-button:${state}`), ...fieldStates.map(state => `field:${state}`), ...Object.entries(status).flatMap(([domain, values]) => values.map(value => `status:${domain}:${slug(value)}`)), ...inlineSave.map(state => `inline-save:${slug(state)}`), ...progress.map(state => `progress:${slug(state)}`), ...player.filter(state => state !== 'absent').map(state => `persistent-player:${slug(state)}`)],
  };
}
const DESIGN_CONTRACT = contractFromDesign();
const STATES = [...new Set(['checkbox:checked', 'checkbox:unchecked', 'checkbox:indeterminate', 'checkbox:focused', 'checkbox:disabled', 'radio-group:disabled', 'toggle:disabled', 'segmented-control:disabled', 'filter-chip:disabled', 'notice:information', 'notice:warning', 'notice:success', 'notice:blocking', 'content:partial', 'content:recoverable', 'content:dense', 'dialog:dirty', 'compact-play:loading', 'compact-play:ready', 'compact-play:playing', 'compact-play:paused', 'compact-play:failed', 'compact-play:disabled', ...DESIGN_CONTRACT.domStates])];

function startServer() {
  const server = http.createServer((request, response) => {
    const relative = decodeURIComponent(new URL(request.url, 'http://localhost').pathname).replace(/^\/+/, '');
    const filename = path.resolve(STATIC, relative || 'primitive_showcase.html');
    if (!filename.startsWith(`${STATIC}${path.sep}`) || !fs.existsSync(filename)) return response.writeHead(404).end('Not found');
    const type = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript' }[path.extname(filename)] || 'application/octet-stream';
    response.writeHead(200, { 'Content-Type': `${type}; charset=utf-8` });
    fs.createReadStream(filename).pipe(response);
  });
  return new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', () => resolve(server)); });
}

function sourceContract() {
  const source = Object.fromEntries(SOURCES.map((name) => [name, fs.readFileSync(path.join(STATIC, name), 'utf8')]));
  const joined = SOURCES.map((name) => source[name]).join('\n');
  const requiredTokens = ['--breakpoint-narrow', '--breakpoint-compact', '--breakpoint-inspector', '--nav-current-rule', '--project-context-wide-min', '--stage-wide-min', '--master-wide', '--inspector-open', '--inspector-collapsed', '--player-track-min', '--showcase-min'];
  return {
    sha256: crypto.createHash('sha256').update(joined).digest('hex'),
    actionTokenAlias: source['styles/tokens.css'].includes('--color-action: var(--color-action-primary);'),
    shellInspectorFactory: source['components/notice.js'].includes('UI.shellInspector = function shellInspector'),
    inspectorLayoutFactory: source['components/notice.js'].includes("token('--breakpoint-inspector')") && source['components/notice.js'].includes('root.dataset.inspectorLayout') && source['components/notice.js'].includes('root.syncLayout'),
    inspectorLayoutSelectors: source['styles/shell.css'].includes('.app-shell[data-inspector-layout="inline"] [data-shell-inspector-slot]') && !/@media\s*\(min-width:\s*73\.75rem\)/.test(source['styles/shell.css']),
    inspectorStyleContract: source['styles/shell.css'].includes('[data-shell-inspector-slot]') && source['styles/shell.css'].includes('[data-shell-inspector]') && source['styles/shell.css'].includes('[data-overlay-root]'),
    skipLinkFocusStyle: source['styles/shell.css'].includes('.skip-link:focus-visible'),
    noStaticPrimitiveLabels: !/<[^>]+data-primitive=/.test(source['primitive_showcase.html']),
    noUnicodeIconSubstitutes: !/[◈▦☷○▷⚙⌧⌕▶❚•]/u.test(joined),
    noOrphanPixels: !/[-+]?\d*\.?\d+px\b/.test(`${source['styles/shell.css']}\n${source['styles/components.css']}`),
    namedLayoutTokens: requiredTokens.every((token) => source['styles/tokens.css'].includes(token)),
    designContractSha256: DESIGN_CONTRACT.designSha256,
    designMatrixDerived: DESIGN_CONTRACT.buttonVariants.length === 4 && DESIGN_CONTRACT.buttonStates.length === 6 && DESIGN_CONTRACT.fieldStates.length === 6 && Object.values(DESIGN_CONTRACT.status).flat().length === 21 && DESIGN_CONTRACT.inlineSave.length === 7 && DESIGN_CONTRACT.progress.includes('indeterminate') && DESIGN_CONTRACT.player.includes('absent') && DESIGN_CONTRACT.player.includes('inactive'),
  };
}

async function press(session, key, modifiers = 0) {
  await session.client.send('Input.dispatchKeyEvent', { type: 'keyDown', key, modifiers });
  await session.client.send('Input.dispatchKeyEvent', { type: 'keyUp', key, modifiers });
}

async function overlayProbe(session) {
  const result = {};
  for (const kind of ['modal', 'drawer']) {
    const opener = `[data-test="${kind}-opener"]`;
    if (!await session.evaluate(`Boolean(document.querySelector('${opener}'))`)) { result[`${kind}Contract`] = false; continue; }
    await session.evaluate(`document.querySelector('${opener}').click()`);
    await session.screenshot(`${kind}-open.png`);
    result[`${kind}Contained`] = await session.evaluate(`(() => { const n=document.querySelector('[data-kind="${kind}"] .dialog-surface'); if(!n)return false; const r=n.getBoundingClientRect(); return n.scrollWidth<=n.clientWidth&&r.left>=0&&r.right<=innerWidth; })()`);
    await press(session, 'Tab');
    result[`${kind}Trapped`] = await session.evaluate(`document.querySelector('[data-kind="${kind}"]')?.contains(document.activeElement)`);
    await press(session, 'Escape');
    result[`${kind}Restored`] = await session.evaluate(`document.activeElement===document.querySelector('${opener}')`);
  }
  const dirtyExists = await session.evaluate(`Boolean(document.querySelector('[data-test="dirty-opener"]'))`);
  if (!dirtyExists) return { ...result, dirtyConfirmation: false, dirtyCancel: false, dirtyDiscard: false, dirtySave: false };
  await session.evaluate(`document.querySelector('[data-test="dirty-opener"]').click()`); await press(session, 'Escape');
  await session.screenshot('dirty-confirmation.png');
  result.dirtyConfirmation = await session.evaluate(`['Save','Discard','Cancel'].every(t=>[...document.querySelectorAll('[data-dirty-confirmation] button')].some(b=>b.textContent.trim()===t))`);
  await session.evaluate(`document.querySelector('[data-dirty-action="cancel"]')?.click()`);
  result.dirtyCancel = await session.evaluate(`!document.querySelector('[data-dirty-confirmation]')&&document.querySelector('[data-kind="modal"]')?.contains(document.activeElement)`);
  await press(session, 'Escape'); await session.evaluate(`document.querySelector('[data-dirty-action="discard"]')?.click()`);
  result.dirtyDiscard = await session.evaluate(`!document.querySelector('[data-kind="modal"]')&&document.activeElement===document.querySelector('[data-test="dirty-opener"]')`);
  await session.evaluate(`document.querySelector('[data-test="dirty-opener"]').click()`); await press(session, 'Escape');
  await session.evaluate(`document.querySelector('[data-dirty-action="save"]')?.click()`);
  result.dirtySave = await session.evaluate(`document.documentElement.dataset.dirtyResolution==='save'&&!document.querySelector('[data-kind="modal"]')`);
  return result;
}

async function interactionProbe(session) {
  const result = {};
  await session.evaluate(`document.querySelector('[data-test="disclosure-trigger"]')?.focus()`); await press(session, 'Enter');
  result.disclosureExpanded = await session.evaluate(`document.querySelector('[data-test="disclosure-trigger"]')?.getAttribute('aria-expanded')==='true'`);
  await session.screenshot('disclosure-expanded.png');
  const popoverExists = await session.evaluate(`Boolean(document.querySelector('[data-test="popover-opener"]'))`);
  if (popoverExists) {
    await session.evaluate(`document.querySelector('[data-test="popover-opener"]').click()`);
    result.popoverOpened = await session.evaluate(`document.querySelector('[data-test="popover-opener"]').getAttribute('aria-expanded')==='true'&&!document.querySelector('[data-primitive="popover"]').hidden`);
    await press(session, 'ArrowDown'); result.popoverKeyboard = await session.evaluate(`document.activeElement?.getAttribute('role')==='menuitem'`);
    await press(session, 'Escape'); result.popoverEscapeRestore = await session.evaluate(`document.querySelector('[data-primitive="popover"]').hidden&&document.activeElement===document.querySelector('[data-test="popover-opener"]')`);
    await session.evaluate(`document.querySelector('[data-test="popover-opener"]').click(); document.querySelector('[data-test="outside-target"]').click()`);
    result.popoverOutsideRestore = await session.evaluate(`document.querySelector('[data-primitive="popover"]').hidden&&document.activeElement===document.querySelector('[data-test="popover-opener"]')`);
  } else Object.assign(result, { popoverOpened: false, popoverKeyboard: false, popoverEscapeRestore: false, popoverOutsideRestore: false });
  await session.evaluate(`document.querySelector('[data-test="waveform-slider"]')?.focus()`); const before = await session.evaluate(`Number(document.querySelector('[data-test="waveform-slider"]')?.getAttribute('aria-valuenow'))`); await press(session, 'ArrowRight');
  result.waveformKeyboard = await session.evaluate(`Number(document.querySelector('[data-test="waveform-slider"]')?.getAttribute('aria-valuenow'))>${before}`);
  await session.evaluate(`document.querySelector('.segmented-control [role="radio"][tabindex="0"]')?.focus()`); await press(session, 'ArrowRight');
  result.segmentedKeyboard = await session.evaluate(`document.activeElement?.getAttribute('role')==='radio'&&document.activeElement?.getAttribute('aria-checked')==='true'`);
  await session.evaluate(`document.querySelector('[data-primitive="listbox"] [aria-selected="true"]')?.focus()`); await press(session, 'ArrowDown');
  result.listboxKeyboard = await session.evaluate(`document.activeElement?.getAttribute('role')==='option'&&document.activeElement?.getAttribute('aria-selected')==='true'`);
  result.secretBehavior = await session.evaluate(`(() => { const root=document.querySelector('[data-test="secret-intent"]'),input=root?.querySelector('input'),live=root?.querySelector('[aria-live]'); if(!root||typeof root.setSecretIntent!=='function'||typeof root.getSecretChange!=='function')return false; const safe=!root.textContent.includes('stored-secret-must-not-appear')&&!input.value; root.querySelector('[data-secret-intent="replace"]')?.click(); input.value='replacement-token'; input.dispatchEvent(new Event('input',{bubbles:true})); const replace=root.getSecretChange(); root.querySelector('[data-secret-intent="clear"]')?.click(); const clear=root.getSecretChange(),cleared=!input.value&&input.disabled; root.querySelector('[data-secret-intent="preserve"]')?.click(); const preserve=root.getSecretChange(); root.querySelector('[data-secret-intent="replace"]')?.click(); return safe&&replace.mode==='replace'&&replace.value==='replacement-token'&&clear.mode==='clear'&&!('value' in clear)&&cleared&&preserve.mode==='preserve'&&!('value' in preserve)&&!input.value&&!input.disabled&&live.textContent.includes('replacement')&&root.dataset.intent==='replace'; })()`);
  await session.evaluate(`document.querySelector('[data-test="secret-intent"]')?.scrollIntoView({block:'center'})`); await session.screenshot('secret-replace.png');
  await session.evaluate(`document.querySelector('[data-test="secret-intent"] [data-secret-intent="clear"]')?.click()`); await session.screenshot('secret-clear.png');
  await session.evaluate(`document.querySelector('[data-test="secret-intent"] [data-secret-intent="preserve"]')?.click()`);
  await session.evaluate(`document.querySelector('[data-test="removable-chip"]')?.scrollIntoView({block:'center'})`); await session.screenshot('chip-selected.png');
  result.chipRemoval = await session.evaluate(`(() => { const root=document.querySelector('[data-test="removable-chip"]'),select=root?.querySelector('[aria-pressed]'),remove=root?.querySelector('[data-chip-remove]'); const prepared=select?.getAttribute('aria-pressed')==='true'&&Boolean(select.querySelector('[data-selection-indicator="check"]'))&&remove?.getAttribute('aria-label')==='Remove Needs voice'; remove?.click(); return prepared&&root.hidden&&root.dataset.removed==='true'&&root.querySelector('[aria-live]')?.textContent==='Needs voice removed.'; })()`);
  result.personaExclusive = await session.evaluate(`(() => { const host=document.querySelector('[data-persona-host]'),toggle=document.querySelector('[data-test="persona-toggle"]'); const one=()=>host?.querySelectorAll('[data-persona-state]').length===1; const expanded=one()&&host.firstElementChild?.dataset.personaState==='expanded'; toggle?.click(); const empty=one()&&host.firstElementChild?.dataset.personaState==='no-evidence'; toggle?.click(); return expanded&&empty&&one()&&host.firstElementChild?.dataset.personaState==='expanded'; })()`);
  await session.evaluate(`document.querySelector('[data-persona-host]')?.scrollIntoView({block:'center'})`); await session.screenshot('cast-persona-expanded.png');
  await session.evaluate(`document.querySelector('[data-test="persona-toggle"]')?.click()`); await session.screenshot('cast-persona-no-evidence.png'); await session.evaluate(`document.querySelector('[data-test="persona-toggle"]')?.click()`);
  const maintenanceExists = await session.evaluate(`Boolean(document.querySelector('[data-test="maintenance-link"]'))`);
  if (maintenanceExists) {
    result.routeInitialExclusive = await session.evaluate(`document.querySelectorAll('[data-route-host] > [data-route-destination]').length===1&&document.querySelector('[data-route-host] > [data-route-destination]')?.dataset.routeDestination==='settings'`);
    await session.evaluate(`document.querySelector('[data-route-host]')?.scrollIntoView({block:'center'})`); await session.screenshot('settings-route.png');
    await session.evaluate(`document.querySelector('[data-test="maintenance-link"]').click()`);
    result.maintenanceDeepLink = await session.evaluate(`location.hash==='#maintenance'&&document.activeElement===document.querySelector('[data-test="maintenance-heading"]')&&document.querySelectorAll('[data-route-host] > [data-route-destination]').length===1&&document.querySelector('[data-route-host] > [data-route-destination]')?.dataset.routeDestination==='maintenance'`);
    await session.screenshot('maintenance-route.png');
    await session.evaluate(`history.back(); new Promise(r=>setTimeout(r,80))`);
    result.maintenanceRestore = await session.evaluate(`location.hash!=='#maintenance'&&document.activeElement===document.querySelector('[data-test="maintenance-link"]')&&document.querySelectorAll('[data-route-host] > [data-route-destination]').length===1&&document.querySelector('[data-route-host] > [data-route-destination]')?.dataset.routeDestination==='settings'`);
    await session.screenshot('settings-restored.png');
  } else Object.assign(result, { routeInitialExclusive: false, maintenanceDeepLink: false, maintenanceRestore: false });
  return { ...result, ...await overlayProbe(session) };
}

async function inspectorProbe(session) {
  const skip = await session.evaluate(`(() => { const link=document.createElement('a'),reference=document.createElement('span');link.className='visually-hidden skip-link';link.href='#foundation';link.textContent='Skip to workspace';link.dataset.skipProbe='';reference.style.cssText='background:var(--color-surface-primary);color:var(--color-text-primary)';document.body.prepend(link,reference);link.focus();const box=link.getBoundingClientRect(),style=getComputedStyle(link),expected=getComputedStyle(reference),result={focusVisible:link.matches(':focus-visible'),visible:box.width>1&&box.height>=32&&box.top>=0&&box.left>=0&&box.right<=innerWidth,target:box.height,position:style.position,clip:style.clip,background:style.backgroundColor,surface:expected.backgroundColor,color:style.color,text:expected.color,outline:style.outlineStyle};reference.remove();return result;})()`);
  await session.screenshot('skip-link-focused.png'); await session.evaluate(`document.querySelector('[data-skip-probe]')?.remove()`);
  const available = await session.evaluate(`typeof AlexandriaUI.shellInspector==='function'`);
  if (!available) return { shellInspectorFactory:false,shellInspectorProvenance:false,shellInspectorStateApi:false,inspectorOpen:false,inspectorCollapsed:false,inspectorCollapsedTrigger:false,inspectorPlacement:false,inspectorContentPreserved:false,genericOverlayCompatible:false,inspectorCleanup:true,skipLinkFocusVisible:skip.focusVisible&&skip.visible&&skip.position==='fixed'&&skip.clip==='auto'&&skip.background===skip.surface&&skip.color===skip.text&&skip.outline!=='none',metrics:{skip,reason:'shellInspector factory unavailable'} };
  const open = await session.evaluate(`(() => { const documentRoot=document.documentElement,root=getComputedStyle(documentRoot),breakpoint=Number.parseFloat(root.getPropertyValue('--breakpoint-inspector')),expectedLayout=innerWidth<breakpoint?'overlay':'inline',inline=expectedLayout==='inline',shell=document.querySelector('.app-shell'),initialLayout=shell?.dataset.inspectorLayout||'',host=document.querySelector('.showcase-main');let tokenSync=false;if(typeof shell?.syncLayout==='function'){const value=documentRoot.style.getPropertyValue('--breakpoint-inspector'),priority=documentRoot.style.getPropertyPriority('--breakpoint-inspector');documentRoot.style.setProperty('--breakpoint-inspector',innerWidth+'px');shell.syncLayout();const inlineFromToken=shell.dataset.inspectorLayout==='inline';documentRoot.style.setProperty('--breakpoint-inspector',(innerWidth+1)+'px');shell.syncLayout();const overlayFromToken=shell.dataset.inspectorLayout==='overlay';if(value)documentRoot.style.setProperty('--breakpoint-inspector',value,priority);else documentRoot.style.removeProperty('--breakpoint-inspector');shell.syncLayout();tokenSync=inlineFromToken&&overlayFromToken&&shell.dataset.inspectorLayout===expectedLayout;}let overlay=document.querySelector('[data-overlay-root]');if(!overlay){overlay=document.createElement('div');overlay.id='overlay-root';overlay.dataset.overlayRoot='';document.body.append(overlay);}const slot=document.createElement('section'),content=document.createElement('div');slot.dataset.inspectorProbe='';content.dataset.inspectorContent='';content.textContent='Primary working area';slot.append(content);host.prepend(slot);globalThis.__inspectorStates=[];const inspector=AlexandriaUI.shellInspector({state:inline?'open':'overlay',label:'Context inspector',title:'Notes and references',content:'Reference context for the current workspace.',onStateChange:state=>globalThis.__inspectorStates.push(state)});const baseline=content.getBoundingClientRect().width;(inline?inspector.mountInline(slot):inspector.mountOverlay(overlay));const box=inspector.getBoundingClientRect(),after=content.getBoundingClientRect().width,slotWidth=slot.getBoundingClientRect().width,style=getComputedStyle(inspector),body=inspector.querySelector('[data-inspector-body]'),trigger=inspector.querySelector('[data-inspector-trigger]');slot.scrollIntoView({block:'center'});return{inline,layout:initialLayout,expectedLayout,tokenSync,alias:root.getPropertyValue('--color-action').trim(),primary:root.getPropertyValue('--color-action-primary').trim(),breakpoint,width:box.width,position:style.position,baseline,after,slotWidth,provenance:inspector.dataset.primitive==='shell-inspector'&&inspector.dataset.productionFactory==='shellInspector',api:typeof inspector.setState==='function'&&typeof inspector.getState==='function'&&typeof inspector.mountInline==='function'&&typeof inspector.mountOverlay==='function',state:inspector.getState(),content:Boolean(body?.textContent.includes('Reference context')),controls:trigger?.getAttribute('aria-controls')===body?.id}; })()`);
  await session.screenshot('inspector-open.png');
  const collapsed = await session.evaluate(`(() => { const slot=document.querySelector('[data-inspector-probe]'),inspector=document.querySelector('[data-shell-inspector]');inspector.setState('collapsed');const box=inspector.getBoundingClientRect(),content=slot.querySelector('[data-inspector-content]').getBoundingClientRect(),trigger=inspector.querySelector('[data-inspector-trigger]'),triggerBox=trigger.getBoundingClientRect(),body=inspector.querySelector('[data-inspector-body]');return{width:box.width,contentWidth:content.width,position:getComputedStyle(inspector).position,api:inspector.getState()==='collapsed'&&trigger.getAttribute('aria-expanded')==='false',trigger:Boolean(trigger.getAttribute('aria-label'))&&triggerBox.width>=32&&triggerBox.height>=32,bodyHidden:body.hidden&&getComputedStyle(body).display==='none',slotState:slot.dataset.inspectorState||''}; })()`);
  await session.screenshot('inspector-collapsed.png');
  const reopened = await session.evaluate(`(() => { const inspector=document.querySelector('[data-shell-inspector]'),trigger=inspector.querySelector('[data-inspector-trigger]');trigger.click();return{state:inspector.getState(),expanded:trigger.getAttribute('aria-expanded')==='true',callback:globalThis.__inspectorStates.includes('collapsed')&&globalThis.__inspectorStates.some(state=>state==='open'||state==='overlay')}; })()`);
  const generic = await session.evaluate(`(() => { const inspector=document.querySelector('[data-shell-inspector]'),overlay=document.querySelector('[data-overlay-root]');inspector.mountOverlay(overlay);const box=inspector.getBoundingClientRect();return{directChild:inspector.parentElement===overlay,width:box.width,position:getComputedStyle(inspector).position,state:inspector.getState()}; })()`);
  await session.screenshot('inspector-overlay.png');
  const cleaned = await session.evaluate(`(() => { document.querySelector('[data-inspector-probe]')?.remove();document.querySelector('[data-shell-inspector]')?.remove();document.querySelector('[data-overlay-root]')?.remove();delete globalThis.__inspectorStates;return!document.querySelector('[data-inspector-probe],[data-shell-inspector],[data-overlay-root]'); })()`);
  const openWidth = Math.abs(open.width - 360) <= 1, collapsedWidth = Math.abs(collapsed.width - 40) <= 1;
  return { actionTokenAlias:Boolean(open.alias)&&open.alias===open.primary,shellInspectorFactory:true,shellInspectorProvenance:open.provenance,shellInspectorStateApi:open.api&&open.controls&&collapsed.api&&reopened.expanded&&reopened.callback,inspectorBreakpoint:open.breakpoint===1180,inspectorLayoutSync:open.layout===open.expectedLayout&&open.tokenSync,inspectorOpen:openWidth&&open.content,inspectorCollapsed:collapsedWidth,inspectorCollapsedTrigger:collapsed.trigger&&collapsed.bodyHidden,inspectorPlacement:open.inline?open.position!=='fixed':open.position==='fixed',inspectorContentPreserved:open.inline?open.slotWidth-open.after>=359:Math.abs(open.baseline-open.after)<=1,genericOverlayCompatible:generic.directChild&&generic.position==='fixed'&&generic.state==='overlay'&&Math.abs(generic.width-360)<=1,inspectorCleanup:cleaned,skipLinkFocusVisible:skip.focusVisible&&skip.visible&&skip.position==='fixed'&&skip.clip==='auto'&&skip.background===skip.surface&&skip.color===skip.text&&skip.outline!=='none',metrics:{skip,open,collapsed,reopened,generic} };
}

async function accessibilityProbe(session) {
  const textZoom = await session.evaluate(`(() => { const keys=['--type-page-size','--type-page-line','--type-page-compact-size','--type-page-compact-line','--type-section-size','--type-section-line','--type-entity-size','--type-entity-line','--type-body-size','--type-body-line','--type-control-size','--type-control-line','--type-metadata-size','--type-metadata-line','--type-utility-size','--type-utility-line','--type-mono-size','--type-mono-line','--type-delivery-size','--type-delivery-line']; const root=document.documentElement,before=parseFloat(getComputedStyle(document.querySelector('.page-title')).fontSize); keys.forEach(key=>root.style.setProperty(key,(parseFloat(getComputedStyle(root).getPropertyValue(key))*2)+'px')); const offenders=[...document.querySelectorAll('body *')].filter(n=>{const r=n.getBoundingClientRect();return r.right>root.clientWidth+.5||r.left<-.5}).slice(0,20).map(n=>({tag:n.tagName,className:n.className?.baseVal||n.className||'',primitive:n.dataset.primitive||'',left:Math.round(n.getBoundingClientRect().left),right:Math.round(n.getBoundingClientRect().right)})); const result={scaled:parseFloat(getComputedStyle(document.querySelector('.page-title')).fontSize)>=before*1.9,overflow:Math.max(0,root.scrollWidth-root.clientWidth),boundaryOffenders:offenders}; keys.forEach(key=>root.style.removeProperty(key)); return result; })()`);
  await session.client.send('Emulation.setEmulatedMedia', { features: [{ name: 'forced-colors', value: 'active' }] });
  const forcedColors = await session.evaluate(`(() => { const selected=document.querySelector('.nav-item[aria-current="page"]'),chip=document.querySelector('.filter-chip__selection[aria-pressed="true"]'); const stages=[...document.querySelectorAll('.stage-step')]; return getComputedStyle(selected).forcedColorAdjust==='none'&&getComputedStyle(chip).forcedColorAdjust==='none'&&Boolean(chip.querySelector('[data-selection-indicator="check"]'))&&stages.every(n=>n.textContent.trim()&&n.querySelector('svg')); })()`);
  await session.client.send('Emulation.setEmulatedMedia', { features: [] });
  const rest = await session.evaluate(`getComputedStyle(document.querySelector('[data-motion-probe]')).transform`); await session.screenshot('motion-rest.png');
  await session.evaluate(`document.querySelector('[data-motion-probe]').classList.add('is-active')`); await session.evaluate(`new Promise(r=>setTimeout(r,70))`);
  const mid = await session.evaluate(`getComputedStyle(document.querySelector('[data-motion-probe]')).transform`); await session.screenshot('motion-mid.png'); await session.evaluate(`new Promise(r=>setTimeout(r,220))`);
  const settled = await session.evaluate(`getComputedStyle(document.querySelector('[data-motion-probe]')).transform`); await session.screenshot('motion-settled.png');
  await session.client.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
  const reducedMotion = await session.evaluate(`(() => { const s=getComputedStyle(document.querySelector('[data-motion-probe]')); return s.transitionDuration==='0s'&&s.animationDuration==='0s'; })()`);
  return { textZoom200: textZoom.scaled && textZoom.overflow === 0, textZoom, forcedColors, motionFrames: rest !== mid && mid !== settled, motionValues: { rest, mid, settled }, reducedMotion };
}

async function matrixProbe(session) {
  return session.evaluate(`(() => {
    const buttonNodes=[...document.querySelectorAll('[data-contract-state^="button:"]')],iconNodes=[...document.querySelectorAll('[data-contract-state^="icon-button:"]')],fieldNodes=[...document.querySelectorAll('[data-contract-state^="field:"]')];
    const stateOf=n=>n.dataset.contractState.split(':').at(-1),buttonApi=buttonNodes.every(n=>n.dataset.variant&&n.dataset.state===stateOf(n)&&(stateOf(n)!=='loading'||(n.disabled&&n.getAttribute('aria-busy')==='true'))&&(stateOf(n)!=='disabled'||n.disabled));
    const iconButtonApi=iconNodes.every(n=>n.dataset.state===stateOf(n)&&n.getAttribute('aria-label')&&n.hasAttribute('data-tooltip')&&n.querySelector('svg')&&(stateOf(n)!=='loading'||(n.disabled&&n.getAttribute('aria-busy')==='true')))&&(document.querySelector('.ui-icon-button--compact')?.getBoundingClientRect().width===32)&&(document.querySelector('.ui-icon-button:not(.ui-icon-button--compact)')?.getBoundingClientRect().width===40);
    const fieldApi=fieldNodes.every(n=>n.dataset.state===stateOf(n)&&(stateOf(n)!=='loading'||(n.getAttribute('aria-busy')==='true'&&n.querySelector('.field__control')?.disabled))&&(stateOf(n)!=='focused'||n.dataset.visualFocus==='true'));
    const statusVocabulary=[...document.querySelectorAll('[data-contract-state^="status:"]')].every(n=>n.dataset.statusDomain&&n.dataset.statusValue&&n.querySelector('svg')&&n.textContent.trim()&&!/\bSelected\b/.test(n.textContent));
    const inlineSave=[...document.querySelectorAll('[data-contract-state^="inline-save:"]')].every(n=>n.getAttribute('role')==='status'&&n.getAttribute('aria-live')==='polite');
    const indeterminate=document.querySelector('[data-contract-state="progress:indeterminate"]'),progressIndeterminate=Boolean(indeterminate&&!indeterminate.querySelector('[role="progressbar"]').hasAttribute('aria-valuenow')&&!/%/.test(indeterminate.textContent)&&indeterminate.querySelector('[aria-live]')?.textContent.trim());
    const absent=AlexandriaUI.persistentPlayer({state:'absent'}),inactive=document.querySelector('[data-contract-state="persistent-player:inactive"]'),playerAbsent=absent===null,playerInactive=Boolean(inactive&&Math.abs(inactive.getBoundingClientRect().height-80)<=1&&inactive.querySelector('[data-player-control="play-pause"]')?.disabled);
    const castOrder=[...document.querySelectorAll('[data-cast-section]')].map(n=>n.dataset.castSection),castOrderPass=JSON.stringify(castOrder)===JSON.stringify(${JSON.stringify(DESIGN_CONTRACT.castOrder)}),reference=document.querySelector('[data-cast-section="reference"]'),castReference=Boolean(reference?.querySelector('[data-reference-audio="true"] [data-primitive="compact-play"]')&&reference.querySelector('[data-reference-transcript="exact"]')?.textContent.trim());
    return {buttonApi,iconButtonApi,fieldApi,statusVocabulary,inlineSave,progressIndeterminate,playerAbsent,playerInactive,castOrder,castOrderPass,castReference};
  })()`);
}

async function inspectViewport(baseUrl, artifacts, width, height) {
  const key = `${width}x${height}`; const viewportArtifacts = path.join(artifacts, key);
  const session = await BrowserSession.open({ url: `${baseUrl}/primitive_showcase.html`, artifacts: viewportArtifacts, width, height });
  try {
    await session.waitFor(`document.documentElement.dataset.showcaseReady==='true'`); await session.client.send('Log.enable');
    const metrics = await session.evaluate(`(() => { const shown=n=>{const r=n.getBoundingClientRect(),s=getComputedStyle(n);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'}; const nodes=[...document.querySelectorAll('body *')].filter(n=>shown(n)&&[...n.childNodes].some(c=>c.nodeType===3&&c.textContent.trim())); const targets=[...document.querySelectorAll('button,a[href],input,select,textarea,[tabindex]:not([tabindex="-1"])')].filter(shown).map(n=>Math.min(n.getBoundingClientRect().width,n.getBoundingClientRect().height)); const widths=[...document.querySelectorAll('[data-width-group]')].reduce((a,n)=>((a[n.dataset.widthGroup]||=[]).push(n.getBoundingClientRect().width),a),{}); const primitives=[...new Set([...document.querySelectorAll('[data-primitive]')].map(n=>n.dataset.primitive))]; const states=[...new Set([...document.querySelectorAll('[data-contract-state]')].map(n=>n.dataset.contractState))]; const factories=Object.fromEntries(${JSON.stringify(FACTORIES)}.map(n=>[n,typeof AlexandriaUI[n]==='function'])); const player=[...document.querySelectorAll('[data-player-control]')].map(n=>n.dataset.playerControl); const rail=document.querySelector('.nav-rail')?.getBoundingClientRect().width||0,header=document.querySelector('.app-header--global')?.getBoundingClientRect().height||0; const expectedRail=innerWidth>=1200?224:innerWidth>=640?184:null; const expectedHeader=88; const bookMark=Boolean(document.querySelector('.book-mark svg')); return { primitives,states,factories,orphanFactoryNodes:document.querySelectorAll('[data-primitive]:not([data-production-factory])').length,layout:document.querySelector('.app-shell')?.dataset.layout,minTextPx:Math.min(...nodes.map(n=>parseFloat(getComputedStyle(n).fontSize))),minTargetPx:Math.min(...targets),overflow:Math.max(0,document.documentElement.scrollWidth-document.documentElement.clientWidth),stableWidth:Object.values(widths).every(v=>Math.max(...v)-Math.min(...v)<=1),invalidLinked:document.querySelector('[aria-invalid="true"]')?.getAttribute('aria-describedby')==='project-name-error',progressLive:[...document.querySelectorAll('[data-primitive="progress"]')].every(n=>n.querySelector('[role="progressbar"]')&&n.querySelector('[aria-live]')),waveformAlternative:Boolean(document.querySelector('[data-test="waveform-output"]')?.textContent.trim()),playerControls:player,reference:{expectedRail,actualRail:rail,expectedHeader,actualHeader:header,bookMark,stageConnected:Boolean(document.querySelector('.stage-tracker__line')),pass:(expectedRail===null||Math.abs(rail-expectedRail)<=1)&&(innerWidth<640||Math.abs(header-expectedHeader)<=1)&&bookMark&&Boolean(document.querySelector('.stage-tracker__line'))}}; })()`);
    const matrix = await matrixProbe(session); const { castOrder: measuredCastOrder, ...matrixAssertions } = matrix; const interaction = await interactionProbe(session); const inspector = await inspectorProbe(session); const { metrics: inspectorMetrics, ...inspectorAssertions } = inspector; const accessibility = await accessibilityProbe(session);
    await session.evaluate(`scrollTo(0,0)`); await session.screenshot('viewport.png'); const capture = await session.client.send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: true }); const full = path.join(viewportArtifacts, 'showcase.png'); fs.writeFileSync(full, Buffer.from(capture.data, 'base64'));
    const errors = session.client.events.filter(e=>e.method==='Runtime.exceptionThrown'||(e.method==='Runtime.consoleAPICalled'&&e.params.type==='error')||(e.method==='Log.entryAdded'&&e.params.entry?.level==='error'));
    const missing = { primitives: PRIMITIVES.filter(n=>!metrics.primitives.includes(n)), states: STATES.filter(n=>!metrics.states.includes(n)), factories: FACTORIES.filter(n=>!metrics.factories[n]), playerControls: PLAYER_CONTROLS.filter(n=>!metrics.playerControls.includes(n)) };
    const assertions = { allPrimitives:!missing.primitives.length,allStates:!missing.states.length,allFactories:!missing.factories.length,productionFactoryInstances:metrics.orphanFactoryNodes===0,playerContract:!missing.playerControls.length,noErrors:!errors.length,noOverflow:metrics.overflow===0,textFloor:metrics.minTextPx>=13,targetFloor:metrics.minTargetPx>=32,stableWidth:metrics.stableWidth,invalidLinked:metrics.invalidLinked,progressLive:metrics.progressLive,waveformAlternative:metrics.waveformAlternative,referenceComparison:metrics.reference.pass,...matrixAssertions,...interaction,...inspectorAssertions,...accessibility };
    return { key,width,height,status:Object.values(assertions).every(value=>typeof value==='object'||Boolean(value))?'PASS':'FAIL',assertions,missing,metrics:{...metrics,matrix:{...matrix,castOrder:measuredCastOrder},inspector:inspectorMetrics},errorCount:errors.length,screenshot:path.join(viewportArtifacts,'viewport.png'),fullPageScreenshot:full };
  } finally { await session.close(); }
}

async function main() {
  const artifacts = path.resolve(required(argsFrom(process.argv.slice(2)), 'artifacts')); fs.mkdirSync(artifacts, { recursive: true });
  if (!fs.existsSync(SHOWCASE)) { const report={status:'RED',reason:'primitive showcase is absent',showcase:SHOWCASE}; writeJson(path.join(artifacts,'report.json'),report); fs.writeFileSync(path.join(artifacts,'action.log'),'RED: production showcase not found\n'); process.stdout.write(`B19_T06_PRIMITIVES=${JSON.stringify(report)}\n`); process.exitCode=1; return; }
  const source = sourceContract(); const server = await startServer(); const results=[];
  try { for (const [width,height] of VIEWPORTS) results.push(await inspectViewport(`http://127.0.0.1:${server.address().port}`,artifacts,width,height)); } finally { await new Promise(r=>server.close(r)); }
  const sourcePass = Object.entries(source).filter(([key])=>!key.endsWith('Sha256')).every(([,value])=>value); const report={status:sourcePass&&results.every(r=>r.status==='PASS')?'PASS':'FAIL',source,designContract:DESIGN_CONTRACT,referenceSources:['phase1_designBoard.png','phase2_shellB.png','phase3d_navigationStatusComponents.png'],results};
  writeJson(path.join(artifacts,'report.json'),report); writeJson(path.join(artifacts,'cleanup.json'),{serverClosed:!server.listening,port:server.address()?.port||null}); fs.writeFileSync(path.join(artifacts,'action.log'),`source ${sourcePass?'PASS':'FAIL'} ${JSON.stringify(source)}\n${results.map(r=>`${r.key} ${r.status} ${JSON.stringify(r.assertions)}`).join('\n')}\n`); process.stdout.write(`B19_T06_PRIMITIVES=${JSON.stringify(report)}\n`); if(report.status!=='PASS')process.exitCode=1;
}

main().catch(error=>{console.error(error.stack||error);process.exitCode=2;});
