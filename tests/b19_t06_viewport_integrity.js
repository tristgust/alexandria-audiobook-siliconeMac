'use strict';

const fs = require('fs');
const path = require('path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');

const DEFAULT_VIEWPORTS = '1536x1024,1366x768,1024x768,768x900,640x800,390x844,320x568';

function parseViewports(value) {
  return String(value || DEFAULT_VIEWPORTS).split(',').map((item) => {
    const [width, height] = item.split('x').map(Number);
    if (!Number.isFinite(width) || !Number.isFinite(height)) {
      throw new Error(`Invalid viewport: ${item}`);
    }
    return [width, height];
  });
}

async function settle(session, delay = 120) {
  await session.evaluate(`new Promise((resolve) => requestAnimationFrame(() => (
    requestAnimationFrame(() => setTimeout(resolve, ${Number(delay)}))
  )))`);
}

async function currentProjectId(baseUrl) {
  const response = await fetch(new URL('/api/projects', baseUrl));
  if (!response.ok) return '';
  const payload = await response.json();
  return String(payload.current_project_id || payload.last_selected_project_id || '').trim();
}

function routes(projectId) {
  const project = projectId ? `?project=${encodeURIComponent(projectId)}` : '';
  return [
    { key: 'projects', path: 'projects', destination: 'projects' },
    { key: 'script', path: `script${project}`, destination: 'script' },
    { key: 'cast', path: `cast${project}`, destination: 'cast' },
    { key: 'produce', path: `produce${project}`, destination: 'produce' },
    { key: 'export', path: `export${project}`, destination: 'export' },
    { key: 'library', path: 'library', destination: 'library' },
    { key: 'voices', path: 'voices', destination: 'voices' },
    { key: 'templates', path: 'templates', destination: 'templates' },
    { key: 'settings', path: 'settings', destination: 'settings' },
    { key: 'more', path: 'more', destination: 'more' },
    { key: 'advanced-character-operations', path: 'more/advanced-character-operations?return=cast', destination: 'more' },
    { key: 'voice-designer', path: 'more/voice-designer?return=voices', destination: 'more' },
    { key: 'audio-preparer', path: 'more/audio-preparer?return=voices', destination: 'more' },
    { key: 'dataset-builder', path: 'more/dataset-builder?return=voices', destination: 'more' },
    { key: 'voice-training', path: 'more/voice-training?return=voices', destination: 'more' },
    { key: 'maintenance', path: 'more/maintenance?mode=recovery&return=settings', destination: 'more' },
    { key: 'model-cache', path: 'more/model-cache?return=maintenance', destination: 'more' },
    { key: 'help-center', path: 'more/help-center?help=getting-started&return=more', destination: 'more' },
  ];
}

async function navigate(session, route) {
  const routePath = route.path.split('?')[0];
  await session.evaluate(`location.hash = ${JSON.stringify(`/${route.path}`)}`);
  await session.waitFor(`document.body.dataset.destination === ${JSON.stringify(route.destination)}
    && (!document.body.dataset.routePath
      || document.body.dataset.routePath === ${JSON.stringify(routePath)})`);
  await settle(session, 180);
  try {
    await session.waitFor(`![...document.querySelectorAll('.canonical-loading-list')].some((node) => {
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      return rect.width > .5 && rect.height > .5
        && style.display !== 'none' && style.visibility !== 'hidden';
    })`, 3000);
  } catch (_error) {
    // Some error/loading states intentionally keep their loading shell visible.
  }
  await settle(session, 80);
}

async function scanRoute(session) {
  return session.evaluate(`(async () => {
    const viewportWidth = document.documentElement.clientWidth || innerWidth;
    const viewportHeight = document.documentElement.clientHeight || innerHeight;
    const visible = (node) => {
      if (!node || node.hidden || node.closest('[hidden],[aria-hidden="true"]')) return false;
      const closedDisclosures = [];
      for (let ancestor = node.parentElement; ancestor; ancestor = ancestor.parentElement) {
        if (ancestor.matches?.('details:not([open])')) closedDisclosures.push(ancestor);
      }
      if (closedDisclosures.some((disclosure) => !(
        node.tagName === 'SUMMARY' && node.parentElement === disclosure
      ))) return false;
      if (node.matches?.('.visually-hidden,.skip-link') && !node.matches(':focus,:focus-visible')) return false;
      const style = getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden'
        && Number(style.opacity || 1) > 0 && rect.width > .5 && rect.height > .5;
    };
    const nameOf = (node) => String(
      node.getAttribute('aria-label') || node.getAttribute('title')
      || node.labels?.[0]?.textContent || node.textContent || node.name || node.id || node.tagName
    ).trim().replace(/\\s+/g, ' ').slice(0, 140);
    const descriptor = (node) => ({
      tag: node.tagName.toLowerCase(), name: nameOf(node), id: node.id || null,
      className: typeof node.className === 'string' ? node.className.slice(0, 160) : '',
      routeOwner: node.closest('[data-route-owner],[data-page]')?.dataset.routeOwner
        || node.closest('[data-route-owner],[data-page]')?.dataset.page || null,
    });
    const rectValue = (rect) => ({
      top: Math.round(rect.top * 10) / 10, right: Math.round(rect.right * 10) / 10,
      bottom: Math.round(rect.bottom * 10) / 10, left: Math.round(rect.left * 10) / 10,
      width: Math.round(rect.width * 10) / 10, height: Math.round(rect.height * 10) / 10,
    });
    const axisOverflow = (style, axis) => axis === 'x' ? style.overflowX : style.overflowY;
    const scrollExtent = (node, axis) => axis === 'x'
      ? node.scrollWidth - node.clientWidth : node.scrollHeight - node.clientHeight;
    const crosses = (rect, ancestorRect, axis) => axis === 'x'
      ? rect.left < ancestorRect.left - 1 || rect.right > ancestorRect.right + 1
      : rect.top < ancestorRect.top - 1 || rect.bottom > ancestorRect.bottom + 1;
    const clippedByAncestor = (node) => {
      const rect = node.getBoundingClientRect();
      const failures = [];
      const protectedByScroller = { x: false, y: false };
      for (let ancestor = node.parentElement; ancestor && ancestor !== document.body; ancestor = ancestor.parentElement) {
        if (!visible(ancestor)) continue;
        const style = getComputedStyle(ancestor);
        const ancestorRect = ancestor.getBoundingClientRect();
        for (const axis of ['x', 'y']) {
          if (protectedByScroller[axis]) continue;
          const overflow = axisOverflow(style, axis);
          const scrollable = ['auto', 'scroll'].includes(overflow) && scrollExtent(ancestor, axis) > 1;
          if (scrollable) {
            protectedByScroller[axis] = true;
            continue;
          }
          if (!crosses(rect, ancestorRect, axis)) continue;
          if (['hidden', 'clip'].includes(overflow) || overflow !== 'visible') {
            failures.push({
              axis, overflow, ancestor: {
                tag: ancestor.tagName.toLowerCase(), id: ancestor.id || null,
                className: typeof ancestor.className === 'string' ? ancestor.className.slice(0, 120) : '',
              },
              ancestorRect: rectValue(ancestorRect), rect: rectValue(rect),
            });
          }
        }
      }
      return failures;
    };
    const inHorizontalScroller = (node) => {
      for (let ancestor = node.parentElement; ancestor; ancestor = ancestor.parentElement) {
        const style = getComputedStyle(ancestor);
        if (['auto', 'scroll'].includes(style.overflowX) && ancestor.scrollWidth > ancestor.clientWidth + 1) return true;
      }
      return false;
    };
    const interactiveSelector = [
      'button', 'a[href]', 'input', 'select', 'textarea', 'summary',
      '[role="button"]', '[role="menuitem"]', '[role="option"]',
      '[tabindex]:not([tabindex="-1"])'
    ].join(',');
    const controls = [...new Set([...document.querySelectorAll(interactiveSelector)].filter(visible))];
    const clippedControls = controls.flatMap((node) => {
      const failures = clippedByAncestor(node);
      return failures.length ? [{ ...descriptor(node), failures }] : [];
    }).slice(0, 30);
    const horizontalOutside = controls.filter((node) => {
      const rect = node.getBoundingClientRect();
      return (rect.left < -1 || rect.right > viewportWidth + 1) && !inHorizontalScroller(node);
    }).map((node) => ({ ...descriptor(node), rect: rectValue(node.getBoundingClientRect()) })).slice(0, 30);
    const fixedSurfaces = [...document.querySelectorAll('.dialog-layer,.popover:not([hidden]),[data-overlay-root] > *,[data-kind="drawer"],[data-kind="modal"]')]
      .filter(visible).map((node) => ({ node, rect: node.getBoundingClientRect() }));
    const fixedOutside = fixedSurfaces.filter(({ rect }) => (
      rect.left < -1 || rect.right > viewportWidth + 1 || rect.top < -1 || rect.bottom > viewportHeight + 1
    )).map(({ node, rect }) => ({ ...descriptor(node), rect: rectValue(rect) })).slice(0, 30);

    const probeFailures = [];
    const probeControls = controls.filter((node) => !node.disabled
      && getComputedStyle(node).pointerEvents !== 'none').slice(0, 120);
    for (const node of probeControls) {
      if (!visible(node)) continue;
      node.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      if (!visible(node)) continue;
      const rect = node.getBoundingClientRect();
      const left = Math.max(0, rect.left); const right = Math.min(viewportWidth, rect.right);
      const top = Math.max(0, rect.top); const bottom = Math.min(viewportHeight, rect.bottom);
      const visibleWidth = Math.max(0, right - left); const visibleHeight = Math.max(0, bottom - top);
      const enough = visibleWidth >= Math.min(24, rect.width) - .5
        && visibleHeight >= Math.min(24, rect.height) - .5;
      let hit = false; let topNode = null;
      if (enough) {
        const x = Math.min(viewportWidth - 1, Math.max(1, left + visibleWidth / 2));
        const y = Math.min(viewportHeight - 1, Math.max(1, top + visibleHeight / 2));
        topNode = document.elementFromPoint(x, y);
        hit = Boolean(topNode && (node === topNode || node.contains(topNode) || topNode.contains(node)));
      }
      if (!enough || !hit) {
        probeFailures.push({
          ...descriptor(node), rect: rectValue(rect), visibleWidth, visibleHeight,
          enough, hit, topNode: topNode ? descriptor(topNode) : null,
        });
      }
      if (probeFailures.length >= 30) break;
    }

    const popoverFailures = [];
    const triggers = [...new Set([...document.querySelectorAll(
      'button[aria-haspopup="menu"],button[aria-haspopup="listbox"],.popover-controller > button,[data-popover-trigger]'
    )].filter(visible))].slice(0, 30);
    for (const trigger of triggers) {
      if (trigger.disabled) continue;
      trigger.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
      trigger.click();
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const panels = [...document.querySelectorAll('.popover:not([hidden]),[role="menu"]:not([hidden])')]
        .filter(visible);
      for (const panel of panels) {
        const rect = panel.getBoundingClientRect();
        if (rect.left < -1 || rect.right > viewportWidth + 1 || rect.top < -1 || rect.bottom > viewportHeight + 1) {
          popoverFailures.push({
            trigger: descriptor(trigger), panel: descriptor(panel), rect: rectValue(rect),
            scrollableY: panel.scrollHeight > panel.clientHeight + 1,
          });
        }
      }
      if (trigger.getAttribute('aria-expanded') === 'true') trigger.click();
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      await new Promise((resolve) => requestAnimationFrame(resolve));
      if (popoverFailures.length >= 20) break;
    }

    const drawerFailures = [];
    let drawerToggle = document.getElementById('rail-mobile-toggle');
    let drawer = document.querySelector('.alexandria-rail');
    if (drawerToggle && drawer && visible(drawerToggle)) {
      const initiallyClosed = drawerToggle.getAttribute('aria-expanded') === 'false'
        && drawer.inert === true && drawer.getAttribute('aria-hidden') === 'true';
      if (!initiallyClosed) drawerFailures.push({
        state: 'closed', toggle: descriptor(drawerToggle), drawer: descriptor(drawer),
        expanded: drawerToggle.getAttribute('aria-expanded'), inert: drawer.inert,
        ariaHidden: drawer.getAttribute('aria-hidden'),
      });
      drawerToggle.click();
      await new Promise((resolve) => setTimeout(resolve, 220));
      drawerToggle = document.getElementById('rail-mobile-toggle');
      drawer = document.querySelector('.alexandria-rail');
      if (drawerToggle?.getAttribute('aria-expanded') !== 'true') {
        drawerToggle?.click();
        await new Promise((resolve) => setTimeout(resolve, 220));
        drawerToggle = document.getElementById('rail-mobile-toggle');
        drawer = document.querySelector('.alexandria-rail');
      }
      const openRect = drawer.getBoundingClientRect();
      const openValid = drawerToggle.getAttribute('aria-expanded') === 'true'
        && drawer.inert === false && drawer.getAttribute('aria-hidden') !== 'true'
        && openRect.left >= -1 && openRect.right <= viewportWidth + 1
        && openRect.top >= -1 && openRect.bottom <= viewportHeight + 1;
      if (!openValid) drawerFailures.push({
        state: 'open', toggle: descriptor(drawerToggle), drawer: descriptor(drawer),
        expanded: drawerToggle.getAttribute('aria-expanded'), inert: drawer.inert,
        ariaHidden: drawer.getAttribute('aria-hidden'), rect: rectValue(openRect),
      });
      drawerToggle.click();
      await new Promise((resolve) => setTimeout(resolve, 220));
      drawerToggle = document.getElementById('rail-mobile-toggle');
      drawer = document.querySelector('.alexandria-rail');
      const closedAgain = drawerToggle.getAttribute('aria-expanded') === 'false'
        && drawer.inert === true && drawer.getAttribute('aria-hidden') === 'true';
      if (!closedAgain) drawerFailures.push({
        state: 'reclosed', toggle: descriptor(drawerToggle), drawer: descriptor(drawer),
        expanded: drawerToggle.getAttribute('aria-expanded'), inert: drawer.inert,
        ariaHidden: drawer.getAttribute('aria-hidden'),
      });
    }

    const textNodes = [...document.querySelectorAll('main *,[data-canonical-destination-root] *')]
      .filter((node) => visible(node) && [...node.childNodes].some((child) => (
        child.nodeType === Node.TEXT_NODE && child.textContent.trim()
      )));
    const tinyText = textNodes.filter((node) => parseFloat(getComputedStyle(node).fontSize) < 13)
      .map((node) => ({ ...descriptor(node), fontSize: getComputedStyle(node).fontSize })).slice(0, 30);
    const narrowTargets = controls.filter((node) => {
      if (!visible(node)) return false;
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      if (style.position === 'absolute' && node.closest('.search-field')) return false;
      if (node.matches('input[type="checkbox"],input[type="radio"]')) {
        const targets = [
          ...(node.labels ? [...node.labels] : []),
          node.closest('label'),
          node.closest('.form-check'),
        ].filter((candidate, index, values) => candidate
          && values.indexOf(candidate) === index && visible(candidate));
        if (targets.some((target) => {
          const targetRect = target.getBoundingClientRect();
          return Math.min(targetRect.width, targetRect.height) >= 32 - .5;
        })) return false;
      }
      return Math.min(rect.width, rect.height) < 32 - .5;
    }).map((node) => ({ ...descriptor(node), rect: rectValue(node.getBoundingClientRect()) })).slice(0, 30);
    const runtime = {
      routePath: document.body.dataset.routePath || null,
      destination: document.body.dataset.destination || null,
      shellLayout: document.querySelector('.app-shell')?.dataset.layout || null,
      viewport: { width: viewportWidth, height: viewportHeight },
      documentOverflowX: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
      documentOverflowY: Math.max(0, document.documentElement.scrollHeight - document.documentElement.clientHeight),
      controlCount: controls.length,
      clippedControls, horizontalOutside, fixedOutside, probeFailures,
      popoverFailures, drawerFailures, tinyText, narrowTargets,
      visibleDialogs: document.querySelectorAll('.dialog-layer:not([hidden])').length,
      visiblePopovers: [...document.querySelectorAll('.popover:not([hidden])')].filter(visible).length,
      activeName: document.activeElement ? nameOf(document.activeElement) : null,
    };
    window.scrollTo(0, 0);
    return runtime;
  })()`);
}

function runtimeErrors(events, baseUrl) {
  const origin = new URL(baseUrl).origin;
  return {
    console: events.filter((item) => item.method === 'Runtime.consoleAPICalled'
      && item.params?.type === 'error').map((item) => item.params),
    exceptions: events.filter((item) => item.method === 'Runtime.exceptionThrown')
      .map((item) => item.params?.exceptionDetails || item.params),
    serverErrors: events.filter((item) => item.method === 'Network.responseReceived')
      .map((item) => item.params?.response).filter((item) => item
        && item.url.startsWith(origin) && item.status >= 500)
      .map((item) => ({ url: item.url, status: item.status })),
  };
}

function assertionsFor(scan, errors) {
  return {
    noDocumentOverflowX: scan.documentOverflowX <= 1,
    noAncestorClipping: scan.clippedControls.length === 0,
    noUnscrollableHorizontalOutside: scan.horizontalOutside.length === 0,
    fixedSurfacesContained: scan.fixedOutside.length === 0,
    everyControlReachableAndUncovered: scan.probeFailures.length === 0,
    popoversContained: scan.popoverFailures.length === 0,
    mobileDrawersContained: scan.drawerFailures.length === 0,
    textFloor: scan.tinyText.length === 0,
    targetFloor: scan.narrowTargets.length === 0,
    noRuntimeErrors: errors.console.length === 0 && errors.exceptions.length === 0,
    noServerErrors: errors.serverErrors.length === 0,
  };
}

async function inspectViewport(baseUrl, artifactRoot, definitions, width, height) {
  const viewport = `${width}x${height}`;
  const folder = path.join(artifactRoot, viewport);
  const session = await BrowserSession.open({ url: baseUrl, artifacts: folder, width, height });
  const results = [];
  try {
    await session.waitFor(`document.readyState === 'complete'`);
    for (const route of definitions) {
      const eventIndex = session.client.events.length;
      await navigate(session, route);
      const scan = await scanRoute(session);
      const errors = runtimeErrors(session.client.events.slice(eventIndex), baseUrl);
      const assertions = assertionsFor(scan, errors);
      results.push({
        route: route.key, path: route.path,
        status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL',
        assertions, scan, errors,
      });
      if (results.at(-1).status !== 'PASS') {
        await session.screenshot(`${route.key.replaceAll('/', '-')}.png`);
      }
    }
  } finally {
    await session.close();
  }
  return {
    viewport, status: results.every((item) => item.status === 'PASS') ? 'PASS' : 'FAIL', results,
  };
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const baseUrl = required(args, 'url');
  const artifacts = path.resolve(required(args, 'artifacts'));
  const viewports = parseViewports(args.viewports);
  const projectId = await currentProjectId(baseUrl);
  const definitions = routes(projectId);
  const results = [];
  for (const [width, height] of viewports) {
    results.push(await inspectViewport(baseUrl, artifacts, definitions, width, height));
  }
  const failures = results.flatMap((viewport) => viewport.results
    .filter((item) => item.status !== 'PASS')
    .map((item) => ({ viewport: viewport.viewport, route: item.route,
      assertions: item.assertions, scan: item.scan, errors: item.errors })));
  const report = {
    status: failures.length ? 'FAIL' : 'PASS', baseUrl, projectId, viewports,
    results, failures,
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  process.stdout.write(`B19_T06_VIEWPORT_INTEGRITY=${JSON.stringify({
    status: report.status,
    failureCount: failures.length,
    failures: failures.map((item) => ({
      viewport: item.viewport,
      route: item.route,
      failedAssertions: Object.entries(item.assertions)
        .filter(([, pass]) => !pass).map(([name]) => name),
      counts: {
        clippedControls: item.scan.clippedControls.length,
        horizontalOutside: item.scan.horizontalOutside.length,
        fixedOutside: item.scan.fixedOutside.length,
        probeFailures: item.scan.probeFailures.length,
        popoverFailures: item.scan.popoverFailures.length,
        drawerFailures: item.scan.drawerFailures.length,
        tinyText: item.scan.tinyText.length,
        narrowTargets: item.scan.narrowTargets.length,
        consoleErrors: item.errors.console.length,
        exceptions: item.errors.exceptions.length,
        serverErrors: item.errors.serverErrors.length,
      },
      firstOffenders: {
        clippedControl: item.scan.clippedControls[0]?.name || null,
        horizontalOutside: item.scan.horizontalOutside[0]?.name || null,
        fixedOutside: item.scan.fixedOutside[0]?.name || null,
        probeFailure: item.scan.probeFailures[0]?.name || null,
        popoverFailure: item.scan.popoverFailures[0]?.panel?.name || null,
        drawerFailure: item.scan.drawerFailures[0]?.state || null,
        tinyText: item.scan.tinyText[0]?.name || null,
        narrowTarget: item.scan.narrowTargets[0]?.name || null,
      },
    })),
  })}\n`);
  if (failures.length) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
