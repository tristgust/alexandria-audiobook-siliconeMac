'use strict';

const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { BrowserSession, argsFrom, required, writeJson } = require('./b19_t06_bootstrap_red.js');

const ROOT = path.resolve(__dirname, '..');
const STATIC = path.join(ROOT, 'app', 'static');
const SHOWCASE = path.join(STATIC, 'primitive_showcase.html');
const VIEWPORTS = [[1536, 1024], [1440, 1000], [1024, 768], [390, 844]];
const SOURCES = ['primitive_showcase.html', 'styles/tokens.css', 'styles/shell.css', 'styles/components.css', 'components/button.js', 'components/icon_button.js', 'components/form_controls.js', 'components/status.js', 'components/notice.js', 'components/disclosure.js', 'components/dialog.js', 'components/transport.js'];
const PRIMITIVES = ['app-shell', 'nav-rail', 'global-header', 'project-header', 'stage-tracker', 'page-title', 'button', 'icon-button', 'field', 'textarea', 'select', 'checkbox', 'radio-group', 'toggle', 'segmented-control', 'filter-chip', 'search-field', 'flat-section', 'divider-list', 'listbox', 'master-detail', 'portrait', 'monogram', 'source-cover', 'status', 'notice', 'progress', 'popover', 'modal', 'drawer', 'skeleton', 'empty-state', 'inline-save', 'disclosure', 'compact-play', 'waveform', 'persistent-player'];
const FACTORIES = ['appShell', 'navRail', 'globalHeader', 'projectHeader', 'stageTracker', 'pageTitleBlock', 'flatSection', 'dividerList', 'masterDetail', 'portrait', 'monogram', 'sourceCover', 'button', 'iconButton', 'field', 'checkbox', 'radioGroup', 'toggle', 'segmentedControl', 'filterChip', 'searchField', 'listbox', 'secretField', 'status', 'notice', 'progress', 'popover', 'dialog', 'compactPlay', 'waveform', 'persistentPlayer'];
const STATES = ['checkbox:checked', 'checkbox:unchecked', 'checkbox:indeterminate', 'checkbox:focused', 'checkbox:disabled', 'radio-group:disabled', 'toggle:disabled', 'segmented-control:disabled', 'filter-chip:disabled', 'secret:preserve', 'secret:replace', 'secret:clear', 'progress:idle', 'progress:running', 'progress:resumable', 'progress:canceled', 'progress:complete', 'progress:error', 'notice:information', 'notice:warning', 'notice:success', 'notice:blocking', 'content:partial', 'content:recoverable', 'content:dense', 'dialog:dirty', 'compact-play:loading', 'compact-play:ready', 'compact-play:playing', 'compact-play:paused', 'compact-play:failed', 'compact-play:disabled', 'persistent-player:loading', 'persistent-player:playing', 'persistent-player:paused', 'persistent-player:failed', 'persona:expanded', 'persona:no-evidence', 'maintenance:deep-link'];
const PLAYER_CONTROLS = ['previous', 'skip-back', 'play-pause', 'skip-forward', 'next', 'volume', 'queue', 'overflow'];

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
  const requiredTokens = ['--breakpoint-narrow', '--breakpoint-compact', '--nav-current-rule', '--project-context-wide-min', '--stage-wide-min', '--master-wide', '--player-track-min', '--showcase-min'];
  return {
    sha256: crypto.createHash('sha256').update(joined).digest('hex'),
    noStaticPrimitiveLabels: !/<[^>]+data-primitive=/.test(source['primitive_showcase.html']),
    noUnicodeIconSubstitutes: !/[◈▦☷○▷⚙⌧⌕▶❚•]/u.test(joined),
    noOrphanPixels: !/[-+]?\d*\.?\d+px\b/.test(`${source['styles/shell.css']}\n${source['styles/components.css']}`),
    namedLayoutTokens: requiredTokens.every((token) => source['styles/tokens.css'].includes(token)),
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
  result.secretBehavior = await session.evaluate(`(() => { const p=document.querySelector('[data-secret-mode="preserve"]'),r=document.querySelector('[data-secret-mode="replace"]'),c=document.querySelector('[data-secret-mode="clear"]'); if(!p||!r||!c)return false; const input=r.querySelector('input'); input.value='new-token'; input.dispatchEvent(new Event('input',{bubbles:true})); return p.dataset.intent==='preserve'&&p.querySelector('input').disabled&&!input.disabled&&r.querySelector('[aria-live]').textContent.includes('Replacement')&&c.dataset.intent==='clear'&&c.querySelector('input').disabled; })()`);
  const maintenanceExists = await session.evaluate(`Boolean(document.querySelector('[data-test="maintenance-link"]'))`);
  if (maintenanceExists) {
    await session.evaluate(`document.querySelector('[data-test="maintenance-link"]').click()`);
    result.maintenanceDeepLink = await session.evaluate(`location.hash==='#maintenance'&&document.activeElement===document.querySelector('[data-test="maintenance-heading"]')`);
    await session.evaluate(`history.back(); new Promise(r=>setTimeout(r,80))`);
    result.maintenanceRestore = await session.evaluate(`location.hash!=='#maintenance'&&document.activeElement===document.querySelector('[data-test="maintenance-link"]')`);
  } else Object.assign(result, { maintenanceDeepLink: false, maintenanceRestore: false });
  return { ...result, ...await overlayProbe(session) };
}

async function accessibilityProbe(session) {
  const textZoom = await session.evaluate(`(() => { const keys=['--type-page-size','--type-page-line','--type-page-compact-size','--type-page-compact-line','--type-section-size','--type-section-line','--type-entity-size','--type-entity-line','--type-body-size','--type-body-line','--type-control-size','--type-control-line','--type-metadata-size','--type-metadata-line','--type-utility-size','--type-utility-line','--type-mono-size','--type-mono-line','--type-delivery-size','--type-delivery-line']; const root=document.documentElement,before=parseFloat(getComputedStyle(document.querySelector('.page-title')).fontSize); keys.forEach(key=>root.style.setProperty(key,(parseFloat(getComputedStyle(root).getPropertyValue(key))*2)+'px')); const offenders=[...document.querySelectorAll('body *')].filter(n=>{const r=n.getBoundingClientRect();return r.right>root.clientWidth+.5||r.left<-.5}).slice(0,20).map(n=>({tag:n.tagName,className:n.className?.baseVal||n.className||'',primitive:n.dataset.primitive||'',left:Math.round(n.getBoundingClientRect().left),right:Math.round(n.getBoundingClientRect().right)})); const result={scaled:parseFloat(getComputedStyle(document.querySelector('.page-title')).fontSize)>=before*1.9,overflow:Math.max(0,root.scrollWidth-root.clientWidth),boundaryOffenders:offenders}; keys.forEach(key=>root.style.removeProperty(key)); return result; })()`);
  await session.client.send('Emulation.setEmulatedMedia', { features: [{ name: 'forced-colors', value: 'active' }] });
  const forcedColors = await session.evaluate(`(() => { const selected=document.querySelector('.nav-item[aria-current="page"]'); const stages=[...document.querySelectorAll('.stage-step')]; return getComputedStyle(selected).forcedColorAdjust==='none'&&stages.every(n=>n.textContent.trim()&&n.querySelector('svg')); })()`);
  await session.client.send('Emulation.setEmulatedMedia', { features: [] });
  const rest = await session.evaluate(`getComputedStyle(document.querySelector('[data-motion-probe]')).transform`); await session.screenshot('motion-rest.png');
  await session.evaluate(`document.querySelector('[data-motion-probe]').classList.add('is-active')`); await session.evaluate(`new Promise(r=>setTimeout(r,70))`);
  const mid = await session.evaluate(`getComputedStyle(document.querySelector('[data-motion-probe]')).transform`); await session.screenshot('motion-mid.png'); await session.evaluate(`new Promise(r=>setTimeout(r,220))`);
  const settled = await session.evaluate(`getComputedStyle(document.querySelector('[data-motion-probe]')).transform`); await session.screenshot('motion-settled.png');
  await session.client.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
  const reducedMotion = await session.evaluate(`(() => { const s=getComputedStyle(document.querySelector('[data-motion-probe]')); return s.transitionDuration==='0s'&&s.animationDuration==='0s'; })()`);
  return { textZoom200: textZoom.scaled && textZoom.overflow === 0, textZoom, forcedColors, motionFrames: rest !== mid && mid !== settled, motionValues: { rest, mid, settled }, reducedMotion };
}

async function inspectViewport(baseUrl, artifacts, width, height) {
  const key = `${width}x${height}`; const viewportArtifacts = path.join(artifacts, key);
  const session = await BrowserSession.open({ url: `${baseUrl}/primitive_showcase.html`, artifacts: viewportArtifacts, width, height });
  try {
    await session.waitFor(`document.documentElement.dataset.showcaseReady==='true'`); await session.client.send('Log.enable');
    const metrics = await session.evaluate(`(() => { const shown=n=>{const r=n.getBoundingClientRect(),s=getComputedStyle(n);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'}; const nodes=[...document.querySelectorAll('body *')].filter(n=>shown(n)&&[...n.childNodes].some(c=>c.nodeType===3&&c.textContent.trim())); const targets=[...document.querySelectorAll('button,a[href],input,select,textarea,[tabindex]:not([tabindex="-1"])')].filter(shown).map(n=>Math.min(n.getBoundingClientRect().width,n.getBoundingClientRect().height)); const widths=[...document.querySelectorAll('[data-width-group]')].reduce((a,n)=>((a[n.dataset.widthGroup]||=[]).push(n.getBoundingClientRect().width),a),{}); const primitives=[...new Set([...document.querySelectorAll('[data-primitive]')].map(n=>n.dataset.primitive))]; const states=[...new Set([...document.querySelectorAll('[data-contract-state]')].map(n=>n.dataset.contractState))]; const factories=Object.fromEntries(${JSON.stringify(FACTORIES)}.map(n=>[n,typeof AlexandriaUI[n]==='function'])); const player=[...document.querySelectorAll('[data-player-control]')].map(n=>n.dataset.playerControl); const rail=document.querySelector('.nav-rail')?.getBoundingClientRect().width||0,header=document.querySelector('.app-header--global')?.getBoundingClientRect().height||0; const expectedRail=innerWidth>=1200?224:innerWidth>=640?184:null; return { primitives,states,factories,orphanFactoryNodes:document.querySelectorAll('[data-primitive]:not([data-production-factory])').length,layout:document.querySelector('.app-shell')?.dataset.layout,minTextPx:Math.min(...nodes.map(n=>parseFloat(getComputedStyle(n).fontSize))),minTargetPx:Math.min(...targets),overflow:Math.max(0,document.documentElement.scrollWidth-document.documentElement.clientWidth),stableWidth:Object.values(widths).every(v=>Math.max(...v)-Math.min(...v)<=1),invalidLinked:document.querySelector('[aria-invalid="true"]')?.getAttribute('aria-describedby')==='project-name-error',progressLive:[...document.querySelectorAll('[data-primitive="progress"]')].every(n=>n.querySelector('[role="progressbar"]')&&n.querySelector('[aria-live]')),waveformAlternative:Boolean(document.querySelector('[data-test="waveform-output"]')?.textContent.trim()),playerControls:player,reference:{expectedRail,actualRail:rail,expectedHeader:88,actualHeader:header,bookMark:Boolean(document.querySelector('[data-icon="book-open"]')),stageConnected:Boolean(document.querySelector('.stage-tracker__line')),pass:(expectedRail===null||Math.abs(rail-expectedRail)<=1)&&header>=88&&Boolean(document.querySelector('[data-icon="book-open"]'))&&Boolean(document.querySelector('.stage-tracker__line'))}}; })()`);
    const interaction = await interactionProbe(session); const accessibility = await accessibilityProbe(session);
    await session.evaluate(`scrollTo(0,0)`); await session.screenshot('viewport.png'); const capture = await session.client.send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: true }); const full = path.join(viewportArtifacts, 'showcase.png'); fs.writeFileSync(full, Buffer.from(capture.data, 'base64'));
    const errors = session.client.events.filter(e=>e.method==='Runtime.exceptionThrown'||(e.method==='Runtime.consoleAPICalled'&&e.params.type==='error')||(e.method==='Log.entryAdded'&&e.params.entry?.level==='error'));
    const missing = { primitives: PRIMITIVES.filter(n=>!metrics.primitives.includes(n)), states: STATES.filter(n=>!metrics.states.includes(n)), factories: FACTORIES.filter(n=>!metrics.factories[n]), playerControls: PLAYER_CONTROLS.filter(n=>!metrics.playerControls.includes(n)) };
    const assertions = { allPrimitives:!missing.primitives.length,allStates:!missing.states.length,allFactories:!missing.factories.length,productionFactoryInstances:metrics.orphanFactoryNodes===0,playerContract:!missing.playerControls.length,noErrors:!errors.length,noOverflow:metrics.overflow===0,textFloor:metrics.minTextPx>=13,targetFloor:metrics.minTargetPx>=32,stableWidth:metrics.stableWidth,invalidLinked:metrics.invalidLinked,progressLive:metrics.progressLive,waveformAlternative:metrics.waveformAlternative,referenceComparison:metrics.reference.pass,...interaction,...accessibility };
    return { key,width,height,status:Object.values(assertions).every(Boolean)?'PASS':'FAIL',assertions,missing,metrics,errorCount:errors.length,screenshot:path.join(viewportArtifacts,'viewport.png'),fullPageScreenshot:full };
  } finally { await session.close(); }
}

async function main() {
  const artifacts = path.resolve(required(argsFrom(process.argv.slice(2)), 'artifacts')); fs.mkdirSync(artifacts, { recursive: true });
  if (!fs.existsSync(SHOWCASE)) { const report={status:'RED',reason:'primitive showcase is absent',showcase:SHOWCASE}; writeJson(path.join(artifacts,'report.json'),report); fs.writeFileSync(path.join(artifacts,'action.log'),'RED: production showcase not found\n'); process.stdout.write(`B19_T06_PRIMITIVES=${JSON.stringify(report)}\n`); process.exitCode=1; return; }
  const source = sourceContract(); const server = await startServer(); const results=[];
  try { for (const [width,height] of VIEWPORTS) results.push(await inspectViewport(`http://127.0.0.1:${server.address().port}`,artifacts,width,height)); } finally { await new Promise(r=>server.close(r)); }
  const sourcePass = Object.entries(source).filter(([key])=>key!=='sha256').every(([,value])=>value); const report={status:sourcePass&&results.every(r=>r.status==='PASS')?'PASS':'FAIL',source,referenceSources:['phase1_designBoard.png','phase2_shellB.png','phase3d_navigationStatusComponents.png'],results};
  writeJson(path.join(artifacts,'report.json'),report); writeJson(path.join(artifacts,'cleanup.json'),{serverClosed:!server.listening,port:server.address()?.port||null}); fs.writeFileSync(path.join(artifacts,'action.log'),`source ${sourcePass?'PASS':'FAIL'} ${JSON.stringify(source)}\n${results.map(r=>`${r.key} ${r.status} ${JSON.stringify(r.assertions)}`).join('\n')}\n`); process.stdout.write(`B19_T06_PRIMITIVES=${JSON.stringify(report)}\n`); if(report.status!=='PASS')process.exitCode=1;
}

main().catch(error=>{console.error(error.stack||error);process.exitCode=2;});
