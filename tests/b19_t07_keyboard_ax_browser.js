'use strict';

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const { BrowserSession, argsFrom, required, writeJson } = require('./b19_t06_bootstrap_red.js');

const KEY_EVENTS = {
  Tab: { key: 'Tab', code: 'Tab' },
  'Shift+Tab': { key: 'Tab', code: 'Tab', modifiers: 8 },
  ArrowUp: { key: 'ArrowUp', code: 'ArrowUp' },
  ArrowDown: { key: 'ArrowDown', code: 'ArrowDown' },
  ArrowLeft: { key: 'ArrowLeft', code: 'ArrowLeft' },
  ArrowRight: { key: 'ArrowRight', code: 'ArrowRight' },
  Home: { key: 'Home', code: 'Home' },
  End: { key: 'End', code: 'End' },
  Enter: { key: 'Enter', code: 'Enter', text: '\r', unmodifiedText: '\r' },
  Space: { key: ' ', code: 'Space', text: ' ', unmodifiedText: ' ' },
  Escape: { key: 'Escape', code: 'Escape' },
};

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function physicalKey(manifest, key) {
  if (!Array.isArray(manifest.physical_keys) || !manifest.physical_keys.includes(key) || !KEY_EVENTS[key]) {
    throw new Error(`Manifest does not permit physical key: ${key}`);
  }
  return KEY_EVENTS[key];
}

async function dispatchPhysicalKey(client, manifest, key) {
  const event = physicalKey(manifest, key);
  await client.send('Input.dispatchKeyEvent', { type: 'keyDown', ...event });
  await client.send('Input.dispatchKeyEvent', { type: 'keyUp', ...event });
}

async function axTree(client) {
  await client.send('Accessibility.enable');
  return client.send('Accessibility.getFullAXTree');
}

async function focusIdentity(session) {
  return session.evaluate(`(() => {
    const active = document.activeElement;
    const owner = document.querySelector('[data-route-owner]');
    const visible = (node) => {
      if (!node || node.hidden) return false;
      const style = getComputedStyle(node); const box = node.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0;
    };
    return { activeId: active?.id || null, activeTag: active?.tagName || null,
      visibleFocus: Boolean(visible(active) && active?.matches(':focus-visible')),
      finalUrl: location.href, bodyDestination: document.body?.dataset.destination || null,
      bodyRoutePath: document.body?.dataset.routePath || null, routeOwner: owner?.dataset.routeOwner || null };
  })()`);
}

async function activeBackendNodeId(client) {
  const active = await client.send('Runtime.evaluate', { expression: 'document.activeElement', objectGroup: 'b19-t07-focus' });
  const objectId = active.result?.objectId;
  if (!objectId) return null;
  try {
    const described = await client.send('DOM.describeNode', { objectId });
    return described.node?.backendNodeId || null;
  } finally {
    await client.send('Runtime.releaseObjectGroup', { objectGroup: 'b19-t07-focus' });
  }
}

function runtimeLog(events) {
  const selected = events.filter((event) => [
    'Runtime.consoleAPICalled', 'Runtime.exceptionThrown', 'Network.responseReceived', 'Network.loadingFailed',
  ].includes(event.method));
  return { consoleErrors: selected.filter((event) => event.method === 'Runtime.consoleAPICalled' && event.params?.type === 'error'),
    exceptions: selected.filter((event) => event.method === 'Runtime.exceptionThrown'),
    network: selected.filter((event) => event.method.startsWith('Network.')) };
}

function property(node, name) {
  return node.properties?.find((item) => item.name === name)?.value?.value;
}

function relationship(node, name) {
  const value = node.properties?.find((item) => item.name === name)?.value;
  return (value?.relatedNodes || []).map((item) => item.backendDOMNodeId).filter(Number.isInteger);
}

function semanticFromAxNode(node) {
  if (!node) return null;
  return {
    backendNodeId: node.backendDOMNodeId,
    ignored: Boolean(node.ignored),
    role: node.role?.value || '',
    name: node.name?.value || '',
    value: node.value?.value || '',
    description: node.description?.value || '',
    state: Object.fromEntries(['focusable', 'disabled', 'expanded', 'checked', 'selected', 'pressed']
      .filter((name) => property(node, name) !== undefined).map((name) => [name, property(node, name)])),
    relationships: Object.fromEntries(['controls', 'owns', 'labelledby', 'describedby']
      .map((name) => [name, relationship(node, name)]).filter(([, values]) => values.length > 0)),
  };
}

async function partialAxSemantic(client, backendNodeId) {
  if (!Number.isInteger(backendNodeId)) return null;
  const result = await client.send('Accessibility.getPartialAXTree', {
    backendNodeId,
    fetchRelatives: false,
  });
  const node = (result.nodes || []).find((item) => item.backendDOMNodeId === backendNodeId)
    || result.nodes?.[0];
  return semanticFromAxNode(node);
}

async function focusTraceEntry(session, sequence, key) {
  const backendNodeId = await activeBackendNodeId(session.client);
  return {
    sequence,
    key,
    activeBackendNodeId: backendNodeId,
    ...(await focusIdentity(session)),
    axSemantic: await partialAxSemantic(session.client, backendNodeId),
  };
}

function axSemantics(nodes, focusTrace) {
  const focused = new Set(focusTrace.map((entry) => entry.activeBackendNodeId).filter(Number.isInteger));
  const captured = focusTrace.map((entry) => entry.axSemantic).filter(Boolean);
  const final = nodes.filter((node) => focused.has(node.backendDOMNodeId)).map(semanticFromAxNode).filter(Boolean);
  const unique = new Map();
  for (const semantic of [...captured, ...final]) {
    const key = `${semantic.backendNodeId}:${semantic.role}:${semantic.name}`;
    if (!unique.has(key)) unique.set(key, semantic);
  }
  return [...unique.values()];
}

function liveRegions(nodes) {
  return nodes.filter((node) => typeof property(node, 'live') === 'string' && property(node, 'live') !== 'off')
    .map((node) => ({ backendNodeId: node.backendDOMNodeId, role: node.role?.value || '',
      name: node.name?.value || '', value: node.value?.value || '', live: property(node, 'live'),
      atomic: property(node, 'atomic'), relevant: property(node, 'relevant') }));
}

function assertAxFocusAgreement(nodes, focusTrace) {
  const byBackendId = new Map(nodes.map((node) => [String(node.backendDOMNodeId), node]));
  const failures = [];
  for (const entry of focusTrace) {
    const id = String(entry.activeBackendNodeId);
    const node = byBackendId.get(id);
    const semantic = entry.axSemantic || semanticFromAxNode(node);
    if (!entry.visibleFocus || semantic?.ignored) failures.push({ id: `hidden-focus:${id}`, pass: false });
    if (!semantic || !String(semantic.name || '').trim()) failures.push({ id: `unlabeled-focus:${id}`, pass: false });
    if (!semantic || !String(semantic.role || '').trim()) failures.push({ id: `unroled-focus:${id}`, pass: false });
    if (!semantic || semantic.state?.focusable !== true) failures.push({ id: `untabbable-focus:${id}`, pass: false });
  }
  return failures;
}

async function captureKeyboardAx(session, manifest, keys) {
  const focusTrace = [];
  for (const key of keys) {
    await dispatchPhysicalKey(session.client, manifest, key);
    focusTrace.push(await focusTraceEntry(session, focusTrace.length + 1, key));
  }
  const tree = await axTree(session.client);
  return { axTree: tree, focusTrace, axSemantics: axSemantics(tree.nodes || [], focusTrace),
    liveRegions: liveRegions(tree.nodes || []), runtime: runtimeLog(session.client.events) };
}

function withRunIdentity(capture, runId) {
  const identify = (entry) => ({ ...entry, runId });
  return { ...capture, focusTrace: capture.focusTrace.map(identify), axSemantics: capture.axSemantics.map(identify),
    liveRegions: capture.liveRegions.map(identify) };
}

async function writeFreshArtifacts(session, artifacts, capture, runId = crypto.randomUUID()) {
  fs.mkdirSync(artifacts, { recursive: true });
  await session.screenshot('screenshot.png');
  const files = [
    { kind: 'screenshot', name: 'screenshot.png' },
    { kind: 'ax_tree', name: 'ax-tree-sample.json', payload: capture.axTree },
    { kind: 'focus_trace', name: 'focus-trace-sample.json', payload: capture.focusTrace },
    { kind: 'live_region', name: 'live-region-sample.json', payload: capture.liveRegions },
    { kind: 'console_network_log', name: 'console-network-sample.json', payload: capture.runtime },
    { kind: 'identity', name: 'artifact-identity.json', payload: { runId, capturedAt: new Date().toISOString() } },
  ];
  const records = files.map((file) => {
    const target = path.join(artifacts, file.name);
    if ('payload' in file) writeJson(target, file.payload);
    return { kind: file.kind, path: file.name, sha256: sha256(target), runId };
  });
  return { runId, artifacts: records };
}

async function runBoundedProbe(url, artifacts, manifestFile, keys) {
  const manifest = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
  if (!Number.isInteger(manifest.expected_case_count) || manifest.expected_case_count < 1) throw new Error('Invalid release manifest');
  const session = await BrowserSession.open({ url, artifacts });
  try {
    const runId = crypto.randomUUID();
    const capture = withRunIdentity(await captureKeyboardAx(session, manifest, keys), runId);
    return { expectedCaseCount: manifest.expected_case_count, ...capture, ...await writeFreshArtifacts(session, artifacts, capture, runId) };
  } finally {
    await session.close();
  }
}

function matrixCases(manifest) {
  const byId = new Map((manifest.viewports || []).map((item) => [item.id, item]));
  const cases = [];
  for (const [mode, configuration] of Object.entries(manifest.mode_matrix || {})) {
    for (const surface of manifest.surfaces || []) for (const viewportId of configuration.viewports || []) {
      const viewport = byId.get(viewportId);
      for (const profile of configuration.profiles || ['default']) {
        if (!viewport) throw new Error(`Unknown viewport: ${viewportId}`);
        cases.push({ caseId: `${mode}:${surface.id}:${viewportId}:${profile}`, mode, surface, viewport, profile });
      }
    }
  }
  if (cases.length !== manifest.expected_case_count) throw new Error(`Case matrix mismatch: ${cases.length}`);
  return cases;
}

function caseFolder(artifacts, caseId) {
  return path.join(artifacts, 'cases', caseId.replaceAll(':', '__'));
}

function scenarioFocusPolicy(surfaceId, inspectorMode = null) {
  if (surfaceId === 'persistent-player') return 'retain';
  if (surfaceId === 'produce-inspector' && inspectorMode === 'inline') return 'inline';
  return 'restore';
}

async function settle(session) {
  await session.evaluate('new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
}

async function waitForPageLoad(session) {
  await session.client.event('Page.loadEventFired', () => true, 30000);
}

async function dispatchBrowserShortcut(session, key, code, text = '') {
  const event = { key, code, modifiers: 4 };
  await session.client.send('Input.dispatchKeyEvent', { type: 'keyDown', ...event,
    ...(text ? { text, unmodifiedText: text } : {}) });
  await session.client.send('Input.dispatchKeyEvent', { type: 'keyUp', ...event });
}

async function resetBrowserZoom(session) {
  await dispatchBrowserShortcut(session, '0', 'Digit0');
}

async function configureProfile(session, mode, profile) {
  const features = mode === 'forced-colors' ? [{ name: 'forced-colors', value: 'active' }]
    : mode === 'reduced-motion' ? [{ name: 'prefers-reduced-motion', value: 'reduce' }] : [];
  if (features.length) await session.client.send('Emulation.setEmulatedMedia', { features });
  if (profile === 'browser-zoom-200') {
    await resetBrowserZoom(session);
    for (let index = 0; index < 6; index += 1) await dispatchBrowserShortcut(session, '+', 'Equal', '+');
  }
  if (profile === 'text-size-200') await session.evaluate(`(() => {
    const style = document.createElement('style'); style.id = 'b19-t07-text-size';
    style.textContent = 'html { font-size: 32px !important; }'; document.head.append(style);
  })()`);
}

async function injectHostileContent(session) {
  return session.evaluate(`(() => {
    const fragment = ${JSON.stringify("<script>alert('inert')</script>&\"'‮ שלום 漢字 é 🙂")};
    const exactField = (label) => {
      const prefix = label + ' ' + fragment + ' ';
      return prefix + 'x'.repeat(Math.max(0, 512 - Array.from(prefix).length));
    };
    const owner = document.querySelector('[data-route-owner]');
    const controlSelector = 'button,a[href],input,textarea,select,[tabindex],[role="button"],[role="option"],[role="tab"],[role="menuitem"]';
    const controlsBefore = owner?.querySelectorAll(controlSelector).length || 0;
    const scriptsBefore = document.querySelectorAll('script').length;
    const dataSelector = [
      '[role="option"] strong', '[role="option"] p',
      '[data-audio-row] .audio-row__speaker', '[data-audio-row] .audio-row__excerpt > span:first-child',
      '.project-list__row strong', '.project-list__row p',
      '.script-entry strong', '.script-entry p', '.cast-profile strong', '.cast-profile p',
      '.export-chapter strong', '.export-chapter span', 'td', 'label',
      '[role="status"]', '[role="alert"]', '.help-article p', '[data-state-region] p',
    ].join(',');
    const candidates = [...(owner?.querySelectorAll(dataSelector) || [])]
      .filter((node) => !node.closest('[hidden],button,a[href],[role="button"],[role="tab"],[role="menuitem"]')
        && !node.matches(controlSelector)
        && !node.querySelector(controlSelector)).slice(0, 12);
    let programmaticEquivalents = 0;
    for (const [index, node] of candidates.entries()) {
      const value = exactField('hostile operational content ' + index);
      node.textContent = value;
      node.dataset.b19T07Hostile = 'true';
      const style = getComputedStyle(node);
      if ([style.overflow, style.overflowX, style.overflowY].some((item) => ['hidden', 'clip'].includes(item))) {
        node.setAttribute('aria-label', value);
        const option = node.closest('[role="option"]');
        if (option) {
          const previous = option.getAttribute('aria-label') || '';
          const suffix = previous.includes(',') ? previous.slice(previous.indexOf(',')) : '';
          option.setAttribute('aria-label', value + suffix);
        }
        programmaticEquivalents += 1;
      }
    }
    const input = owner?.querySelector('input[type="text"], textarea');
    if (input) {
      input.value = exactField('hostile editable value');
      input.dataset.b19T07Hostile = 'true';
    }
    const controlsAfter = owner?.querySelectorAll(controlSelector).length || 0;
    const scriptsAfter = document.querySelectorAll('script').length;
    return {
      injected: candidates.length + (input ? 1 : 0),
      targets: candidates.map((node, index) => ({
        index,
        tag: node.tagName,
        className: String(node.className || ''),
        id: node.id || null,
        role: node.getAttribute('role'),
        overflow: getComputedStyle(node).overflow,
        overflowX: getComputedStyle(node).overflowX,
        overflowY: getComputedStyle(node).overflowY,
      })),
      controlsBefore,
      controlsAfter,
      scriptsBefore,
      scriptsAfter,
      programmaticEquivalents,
      structurePreserved: controlsBefore === controlsAfter && scriptsBefore === scriptsAfter,
    };
  })()`);
}

async function routeSnapshot(session) {
  return session.evaluate(`(() => {
    const visible = (node) => { const style = getComputedStyle(node), box = node.getBoundingClientRect();
      return !node.hidden && style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0; };
    const rgba = (color) => color.match(/[\d.]+/g)?.map(Number) || [];
    const foreground = (color) => rgba(color).slice(0, 3);
    const contrast = (a, b) => { const linear = (value) => { const c = value / 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; };
      const luminance = (rgb) => .2126 * linear(rgb[0]) + .7152 * linear(rgb[1]) + .0722 * linear(rgb[2]);
      return a.length === 3 && b.length === 3 ? (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05) : null; };
    const owner = document.querySelector('[data-route-owner]'); const active = document.activeElement;
    const opaqueBackground = (node) => { let current = node; while (current) { const color = rgba(getComputedStyle(current).backgroundColor); if (color.length === 3 || color[3] >= .999) return color.slice(0, 3); current = current.parentElement; } return [255,255,255]; };
    const textNodes = [...(owner?.querySelectorAll('button,a,input,textarea,select,h1,h2,h3,p,strong,label,[role="status"],[role="alert"]') || [])]
      .filter((node) => visible(node) && ((node.textContent || node.value || '').trim()));
    const contrasts = textNodes.map((node) => { const style = getComputedStyle(node); const size = Number.parseFloat(style.fontSize); const large = size >= 24 || (size >= 18.66 && Number.parseInt(style.fontWeight, 10) >= 700);
      return { tag: node.tagName, text: (node.textContent || node.value || '').trim().slice(0, 80), ratio: contrast(foreground(style.color), opaqueBackground(node)), required: large ? 3 : 4.5 }; }).filter((item) => item.ratio !== null);
    const controls = [...document.querySelectorAll('button,a[href],input,textarea,select,[tabindex],[role="button"],[role="option"],[role="tab"],[role="menuitem"]')].filter(visible);
    const referencedText = (node, attribute) => String(node.getAttribute(attribute) || '').split(/\s+/)
      .filter(Boolean).map((id) => document.getElementById(id)?.textContent || '').join(' ').trim();
    const labelText = (node) => [...(node.labels || [])].map((label) => label.textContent || '').join(' ').trim();
    const name = (node) => (node.getAttribute('aria-label') || referencedText(node, 'aria-labelledby')
      || labelText(node) || node.getAttribute('alt') || node.textContent || node.value || '').trim();
    const landmarks = [...document.querySelectorAll('main,nav,aside,header,footer,[role="main"],[role="navigation"],[role="complementary"],[role="banner"],[role="contentinfo"]')].filter(visible)
      .map((node) => ({ role: node.getAttribute('role') || node.tagName.toLowerCase(), name: node.getAttribute('aria-label') || node.getAttribute('aria-labelledby') || '' }));
    const landmarkKeys = landmarks.map((item) => item.role + ':' + item.name).filter((key) => key.split(':')[1]);
    const operational = [...(owner?.querySelectorAll('h1,h2,h3,label,[role="status"],[role="alert"],[data-b19-t07-hostile]') || [])].filter(visible);
    const clippedDetails = operational.map((node) => {
      const style = getComputedStyle(node);
      const horizontal = node.scrollWidth > node.clientWidth + 1
        && ['hidden','clip'].includes(style.overflowX || style.overflow);
      const vertical = node.scrollHeight > node.clientHeight + 1
        && ['hidden','clip'].includes(style.overflowY || style.overflow);
      const text = (node.textContent || '').trim();
      const programmatic = name(node);
      return {
        tag: node.tagName, className: String(node.className || ''), id: node.id || null,
        text: text.slice(0, 160), programmatic: programmatic.slice(0, 160),
        horizontal, vertical, clientWidth: node.clientWidth, scrollWidth: node.scrollWidth,
        clientHeight: node.clientHeight, scrollHeight: node.scrollHeight,
        overflow: style.overflow, overflowX: style.overflowX, overflowY: style.overflowY,
        inaccessible: (horizontal || vertical) && !programmatic && node.getAttribute('aria-hidden') !== 'true',
      };
    }).filter((item) => item.horizontal || item.vertical);
    const clipped = clippedDetails.filter((item) => item.inaccessible).map((item) => item.text);
    const focusBox = active?.getBoundingClientRect();
    const rect = (node) => {
      const box = node?.getBoundingClientRect();
      return box ? { left: box.left, top: box.top, right: box.right, bottom: box.bottom,
        width: box.width, height: box.height } : null;
    };
    const focusAncestors = [];
    for (let current = active?.parentElement; current; current = current.parentElement) {
      const style = getComputedStyle(current);
      if (['auto', 'scroll', 'hidden', 'clip'].includes(style.overflowY || style.overflow)) {
        focusAncestors.push({ tag: current.tagName, className: String(current.className || ''),
          id: current.id || null, overflowY: style.overflowY, rect: rect(current),
          scrollTop: current.scrollTop, scrollHeight: current.scrollHeight, clientHeight: current.clientHeight });
      }
    }
    const numericMissing = [...document.querySelectorAll('[role="slider"],input[type="range"],[data-waveform]')].filter(visible).filter((node) => {
      if (node.matches('input[type="range"]')) return !node.value;
      return !node.getAttribute('aria-valuenow') && !node.getAttribute('aria-valuetext') && !node.textContent?.trim();
    });
    return { finalUrl: location.hash, bodyDestination: document.body.dataset.destination || null,
      bodyRoutePath: document.body.dataset.routePath || null, routeOwner: owner?.dataset.routeOwner || null,
      activeId: active?.id || null, activeTag: active?.tagName || null,
      activeRect: focusBox ? { left: focusBox.left, top: focusBox.top, right: focusBox.right,
        bottom: focusBox.bottom, width: focusBox.width, height: focusBox.height } : null,
      focusAncestors,
      workspaceRect: rect(document.querySelector('[data-canonical-destination-root]')),
      produceContentRect: rect(document.querySelector('.produce-content')),
      playerRect: rect(document.querySelector('[data-persistent-player]:not([hidden])')),
      overflow: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
      focusVisible: Boolean(active && visible(active) && active.matches(':focus-visible')),
      focusContained: !focusBox || (focusBox.left >= -1 && focusBox.top >= -1 && focusBox.right <= innerWidth + 1 && focusBox.bottom <= innerHeight + 1),
      unlabeledControls: controls.filter((node) => !name(node) && !node.getAttribute('aria-labelledby')).length,
      titleOnlyControls: controls.filter((node) => node.title && !name(node) && !node.getAttribute('aria-labelledby')).length,
      duplicateIds: [...document.querySelectorAll('[id]')].map((node) => node.id).filter((id, index, all) => all.indexOf(id) !== index),
      duplicateLandmarks: landmarkKeys.filter((key, index, all) => all.indexOf(key) !== index),
      clippedOperationalText: clipped, visualClippedOperationalText: clippedDetails,
      numericEquivalentMissing: numericMissing.length,
      contrastFailures: contrasts.filter((item) => item.ratio + .01 < item.required),
      reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
      forcedColors: matchMedia('(forced-colors: active)').matches,
      viewportScale: visualViewport?.scale || 1 };
  })()`);
}

async function exerciseScenario(session, manifest, surface) {
  const interaction = await session.evaluate(`(() => {
    const id = ${JSON.stringify(surface.id)};
    const candidates = [...document.querySelectorAll('button,a,[role="button"]')];
    const find = (selector, words) => document.querySelector(selector)
      || candidates.find((item) => words.test((item.getAttribute('aria-label') || '') + ' ' + (item.textContent || '')));
    const node = id === 'new-project-dialog'
      ? find('[data-new-project-open]', /new project/i)
      : id === 'script-generation-dialog'
        ? find('[data-script-generation-open]', /generation options/i)
        : id === 'produce-inspector'
          ? document.querySelector('[data-audio-row]')
        : id === 'persistent-player'
            ? find('[data-produce-play-selected], .produce-play', /play/i)
            : null;
    node?.focus();
    const controls = candidates.filter((item) => item.getClientRects().length).slice(0, 40).map((item) => ({
      tag: item.tagName,
      id: item.id || null,
      classes: item.className || null,
      ariaLabel: item.getAttribute('aria-label'),
      text: item.textContent?.trim().replace(/\s+/g, ' ').slice(0, 120) || null,
      disabled: Boolean(item.disabled || item.getAttribute('aria-disabled') === 'true'),
    }));
    return { targetFound: Boolean(node), targetId: node?.id || null,
      targetLabel: node?.getAttribute('aria-label') || node?.textContent?.trim() || null,
      playerState: document.querySelector('[data-persistent-player]')?.dataset.state || null,
      controls };
  })()`);
  const focusTrace = [];
  const press = async (key) => { await dispatchPhysicalKey(session.client, manifest, key); await settle(session);
    focusTrace.push(await focusTraceEntry(session, focusTrace.length + 1, key)); };
  await press('Tab');
  await press('Shift+Tab');
  if (interaction.targetFound) {
    const originalBackendNodeId = await activeBackendNodeId(session.client);
    const originalFocus = await focusIdentity(session);
    await press('Enter');
    const opened = await session.evaluate(`(() => {
      const id = ${JSON.stringify(surface.id)};
      const player = document.querySelector('[data-persistent-player]');
      const inspector = document.querySelector('[data-page-inspector]');
      let success = true;
      if (id === 'new-project-dialog') success = Boolean(document.querySelector('[data-new-project], [role="dialog"]'));
      if (id === 'script-generation-dialog') success = Boolean(document.querySelector('[role="dialog"]'));
      if (id === 'produce-inspector') success = Boolean(document.querySelector('.produce-inspector:not([hidden]), [data-page-inspector][data-state="open"]'));
      if (id === 'persistent-player') success = player?.dataset.state === 'playing';
      return { success, playerState: player?.dataset.state || null,
        inspectorMode: inspector?.dataset.inspectorMode || null,
        inspectorRole: inspector?.getAttribute('role') || null,
        inspectorModal: inspector?.getAttribute('aria-modal') || null,
        activeId: document.activeElement?.id || null,
        activeLabel: document.activeElement?.getAttribute?.('aria-label') || document.activeElement?.textContent?.trim() || null };
    })()`);
    if (!opened.success) throw new Error(`Scenario did not open its real surface: ${surface.id}; interaction=${JSON.stringify(interaction)}; after=${JSON.stringify(opened)}`);
    const focusPolicy = scenarioFocusPolicy(surface.id, opened.inspectorMode);
    if (focusPolicy === 'restore') {
      await press('Tab');
      await press('Escape');
      const restoredBackendNodeId = await activeBackendNodeId(session.client);
      if (restoredBackendNodeId !== originalBackendNodeId) {
        const restoredFocus = await focusIdentity(session);
        throw new Error(`Scenario did not restore focus: ${surface.id}; original=${JSON.stringify({ backendNodeId: originalBackendNodeId, ...originalFocus })}; restored=${JSON.stringify({ backendNodeId: restoredBackendNodeId, ...restoredFocus })}`);
      }
    } else if (focusPolicy === 'inline') {
      const focusedBackendNodeId = await activeBackendNodeId(session.client);
      if (opened.inspectorRole || opened.inspectorModal || focusedBackendNodeId !== originalBackendNodeId) {
        const inlineFocus = await focusIdentity(session);
        throw new Error(`Inline inspector contract failed: ${surface.id}; opened=${JSON.stringify(opened)}; original=${JSON.stringify({ backendNodeId: originalBackendNodeId, ...originalFocus })}; current=${JSON.stringify({ backendNodeId: focusedBackendNodeId, ...inlineFocus })}`);
      }
    }
    interaction.focusPolicy = focusPolicy;
    interaction.inspectorMode = opened.inspectorMode;
  }
  return { ...interaction, focusTrace };
}

async function runMatrix(baseUrl, artifacts, manifestFile) {
  const manifest = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
  const startedAt = new Date().toISOString();
  const runId = crypto.randomUUID();
  const sha = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: path.resolve(__dirname, '..'), encoding: 'utf8' }).trim();
  const manifestSha = sha256(manifestFile);
  const records = [];
  const cleanup = [];
  const failures = [];
  for (const item of matrixCases(manifest)) {
    const transient = fs.mkdtempSync(path.join(os.tmpdir(), 'alexandria-b19-t07-case-'));
    const url = new URL(baseUrl); url.hash = item.surface.requested_url.replace(/^#/, '');
    let session = null;
    try {
      session = await BrowserSession.open({ url: url.href, artifacts: transient,
        width: item.viewport.width, height: item.viewport.height });
      await waitForPageLoad(session);
      await configureProfile(session, item.mode, item.profile);
      await session.client.send('Page.reload', { ignoreCache: true });
      await waitForPageLoad(session);
      await session.waitFor(`document.body.dataset.shellState === 'ready' && document.querySelector('[data-route-owner]')`, 30000);
      const hostile = item.mode === 'hostile' ? await injectHostileContent(session) : { injected: 0 };
      if (item.mode === 'hostile') await settle(session);
      const scenario = await exerciseScenario(session, manifest, item.surface);
      const tree = await axTree(session.client);
      const focusTrace = scenario.focusTrace;
      const capture = withRunIdentity({ axTree: tree, focusTrace, axSemantics: axSemantics(tree.nodes || [], focusTrace),
        liveRegions: liveRegions(tree.nodes || []), runtime: runtimeLog(session.client.events) }, runId);
      const snapshot = await routeSnapshot(session);
      const caseArtifacts = caseFolder(artifacts, item.caseId);
      const transientArtifacts = session.artifacts;
      session.artifacts = caseArtifacts;
      const proof = await writeFreshArtifacts(session, caseArtifacts, capture, runId);
      session.artifacts = transientArtifacts;
      const routePass = snapshot.finalUrl === item.surface.requested_url
        && snapshot.bodyDestination === item.surface.destination
        && snapshot.bodyRoutePath === item.surface.route_path
        && snapshot.routeOwner === item.surface.route_owner;
      const contrastPass = item.mode !== 'contrast' || snapshot.contrastFailures.length === 0;
      const scenarioPass = item.surface.kind !== 'scenario' || scenario.targetFound;
      const agreementFailures = assertAxFocusAgreement(tree.nodes || [], focusTrace);
      const integrityPass = snapshot.overflow <= 1 && snapshot.focusContained && snapshot.unlabeledControls === 0
        && snapshot.titleOnlyControls === 0 && snapshot.duplicateIds.length === 0 && snapshot.duplicateLandmarks.length === 0
        && snapshot.clippedOperationalText.length === 0 && snapshot.numericEquivalentMissing === 0
        && capture.runtime.exceptions.length === 0 && capture.runtime.consoleErrors.length === 0 && agreementFailures.length === 0
        && (item.mode !== 'hostile' || (hostile.injected > 0 && hostile.structurePreserved))
        && (item.mode !== 'forced-colors' || snapshot.forcedColors)
        && (item.mode !== 'reduced-motion' || snapshot.reducedMotion);
      if (!routePass || !contrastPass || !scenarioPass || !integrityPass) failures.push(item.caseId);
      records.push({ case_id: item.caseId, run_id: runId, base_sha: sha, final_sha: sha,
        captured_at: new Date().toISOString(), mode: item.mode, surface: item.surface.id,
        viewport: item.viewport.id, profile: item.profile, requested_url: item.surface.requested_url,
        final_url: snapshot.finalUrl, body_destination: snapshot.bodyDestination,
        body_route_path: snapshot.bodyRoutePath, route_owner: snapshot.routeOwner,
        assertions: { route: routePass, contrast: contrastPass, scenario: scenarioPass, integrity: integrityPass },
        observation: { ...snapshot, scenario, hostile, agreementFailures }, artifacts: proof.artifacts.map((record) => ({ ...record,
          path: path.relative(artifacts, path.join(caseFolder(artifacts, item.caseId), record.path)).split(path.sep).join('/') })) });
    } finally {
      if (session && item.profile === 'browser-zoom-200') await resetBrowserZoom(session);
      if (session) await session.close();
      const receipt = path.join(transient, 'cleanup.json');
      const startupReceipt = path.join(transient, 'chrome-startup-attempts.json');
      cleanup.push(fs.existsSync(receipt)
        ? JSON.parse(fs.readFileSync(receipt, 'utf8'))
        : fs.existsSync(startupReceipt)
          ? { browserExited: true, profileRemoved: true,
            startupRetryCount: JSON.parse(fs.readFileSync(startupReceipt, 'utf8')).attempts.length }
          : { browserExited: false, profileRemoved: false, startupRetryCount: 0 });
      fs.rmSync(transient, { recursive: true, force: true });
    }
  }
  const run = { run_id: runId, base_sha: sha, final_sha: sha, started_at: startedAt, manifest_sha256: manifestSha,
    cleanup: { cases: cleanup.length, allBrowsersExited: cleanup.every((item) => item.browserExited),
      allProfilesRemoved: cleanup.every((item) => item.profileRemoved),
      startupRetries: cleanup.reduce((total, item) => total + Number(item.startupRetryCount || 0), 0) } };
  writeJson(path.join(artifacts, 'run.json'), run);
  writeJson(path.join(artifacts, 'matrix.json'), { cases: records });
  if (failures.length) throw new Error(`Case contracts failed: ${failures.join(', ')}`);
  return { run, cases: records };
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(String(args.artifacts || args['evidence-dir'] || required(args, 'artifacts')));
  const manifestFile = path.resolve(String(args.manifest || path.join(__dirname, 'b19_t07_routes.json')));
  const keys = String(args.keys || 'Tab,Escape').split(',').filter(Boolean);
  const expectedViewports = '390x844,768x1024,1024x768,1536x1024';
  if (args.viewports && args.viewports !== expectedViewports) throw new Error(`--viewports must be ${expectedViewports}`);
  if (args['fresh-only'] && args['fresh-only'] !== true) throw new Error('--fresh-only accepts no value');
  const report = args.matrix
    ? await runMatrix(required(args, 'url'), artifacts, manifestFile)
    : await runBoundedProbe(required(args, 'url'), artifacts, manifestFile, keys);
  if (!args.matrix) writeJson(path.join(artifacts, 'report.json'), report);
  process.stdout.write(`B19_T07_KEYBOARD_AX=${JSON.stringify(report)}\n`);
}

if (require.main === module) main().catch((error) => { console.error(error.stack || error); process.exitCode = 2; });

module.exports = { assertAxFocusAgreement, axSemantics, captureKeyboardAx, dispatchPhysicalKey, focusTraceEntry, liveRegions, matrixCases, partialAxSemantic, routeSnapshot, runBoundedProbe, runMatrix, scenarioFocusPolicy, waitForPageLoad, withRunIdentity, writeFreshArtifacts };
