'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { BrowserSession } = require('./b19_t06_bootstrap_red.js');

const PROJECT_ID = process.env.ALEXANDRIA_AUDIT_PROJECT || 'project_70efcfbfdf3344c08506';
const BASE_URL = process.argv.find((value) => value.startsWith('--url='))?.slice(6)
  || 'http://127.0.0.1:4200/';
const ARTIFACTS = process.argv.find((value) => value.startsWith('--artifacts='))?.slice(12)
  || path.join(os.tmpdir(), 'alexandria-interface-holistic-audit');

const VIEWPORTS = Object.freeze([
  { name: 'wide', width: 1536, height: 1024 },
  { name: 'compact', width: 1024, height: 768 },
  { name: 'narrow', width: 390, height: 844 },
]);

const ROUTES = Object.freeze([
  { name: 'projects', hash: '#/projects' },
  { name: 'script', hash: `#/script?project=${PROJECT_ID}` },
  { name: 'cast', hash: `#/cast?project=${PROJECT_ID}` },
  { name: 'produce', hash: `#/produce?project=${PROJECT_ID}` },
  { name: 'export', hash: `#/export?project=${PROJECT_ID}` },
  { name: 'library', hash: '#/library' },
  { name: 'voices', hash: '#/voices' },
  { name: 'templates', hash: '#/templates' },
  { name: 'settings', hash: '#/settings' },
  { name: 'more', hash: '#/more' },
]);

const TEXT_INPUT_SELECTOR = [
  'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"])',
  ':not([type="range"]):not([type="file"]):not([type="color"])',
  ':not([type="button"]):not([type="submit"]):not([type="reset"])',
  ', select, textarea',
].join('');

const INSPECTION = String.raw`(() => {
  const routeName = __ROUTE_NAME__;
  const viewportName = __VIEWPORT_NAME__;
  const visible = (selector) => Array.from(document.querySelectorAll(selector)).filter((node) => {
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return !node.hidden && style.display !== 'none' && style.visibility !== 'hidden'
      && rect.width > 0 && rect.height > 0;
  });
  const accessibleName = (node) => {
    const labelledBy = node.getAttribute('aria-labelledby');
    if (labelledBy) {
      const value = labelledBy.split(/\s+/)
        .map((id) => document.getElementById(id)?.textContent?.trim() || '')
        .filter(Boolean).join(' ');
      if (value) return value;
    }
    return node.getAttribute('aria-label')?.trim()
      || node.textContent?.trim()
      || node.getAttribute('title')?.trim()
      || node.querySelector('img[alt]')?.getAttribute('alt')?.trim()
      || '';
  };
  const labelFor = (control) => {
    if (control.labels?.length) return true;
    if (control.getAttribute('aria-label')?.trim()) return true;
    const labelledBy = control.getAttribute('aria-labelledby');
    return Boolean(labelledBy && labelledBy.split(/\s+/)
      .some((id) => document.getElementById(id)?.textContent?.trim()));
  };
  const selectorFor = (node) => {
    if (node.id) return '#' + node.id;
    const bits = [node.tagName.toLowerCase()];
    if (node.classList.length) bits.push('.' + Array.from(node.classList).slice(0, 3).join('.'));
    if (node.getAttribute('data-action')) {
      bits.push('[data-action="' + node.getAttribute('data-action') + '"]');
    }
    return bits.join('');
  };
  const unlabeledControls = visible('button, a[href], [role="button"], [role="link"]')
    .filter((node) => !accessibleName(node))
    .map(selectorFor);
  const unlabeledFields = visible('input:not([type="hidden"]), select, textarea')
    .filter((node) => !labelFor(node))
    .map(selectorFor);
  const smallTargets = visible(
    'button, a[href], [role="button"], [role="link"], input, select, textarea',
  ).map((node) => ({ node, rect: node.getBoundingClientRect() }))
    .filter(({ node, rect }) => {
      if (node.matches('.visually-hidden, .skip-link')) return false;
      if (node.matches('a[href]') && getComputedStyle(node).display === 'inline') return false;
      return rect.width < 24 || rect.height < 24;
    })
    .map(({ node, rect }) => ({
      selector: selectorFor(node),
      width: Math.round(rect.width * 10) / 10,
      height: Math.round(rect.height * 10) / 10,
      name: accessibleName(node),
    }));
  const mobileSmallInputs = viewportName === 'narrow'
    ? visible(__TEXT_INPUT_SELECTOR__)
      .filter((node) => Number.parseFloat(getComputedStyle(node).fontSize) < 16)
      .map((node) => ({ selector: selectorFor(node), fontSize: getComputedStyle(node).fontSize }))
    : [];
  const visibleHeadings = visible('h1, h2, h3, h4, h5, h6').map((node) => ({
    level: Number(node.tagName.slice(1)),
    text: node.textContent.trim().replace(/\s+/g, ' '),
    selector: selectorFor(node),
  }));
  const headingSkips = [];
  visibleHeadings.forEach((heading, index) => {
    const previous = visibleHeadings[index - 1];
    if (previous && heading.level > previous.level + 1) {
      headingSkips.push({ from: previous, to: heading });
    }
  });
  const visibleH1 = visibleHeadings.filter((heading) => heading.level === 1);
  const root = document.documentElement;
  const app = document.querySelector('.app-shell');
  const workspace = document.querySelector('[data-shell-workspace]');
  const bodyText = document.body.innerText || '';
  const genericCopy = Array.from(bodyText.matchAll(
    /loading\.\.\.|loading destination|surface unavailable|something went wrong|oops!?|click here|please wait/gi,
  )).map((match) => match[0]);
  const rawProjectIds = Array.from(new Set(bodyText.match(/project_[0-9a-f]{8,}/gi) || []));
  const legacyIcons = visible('[data-legacy-icon="true"]')
    .map((node) => ({ selector: selectorFor(node), className: node.className || '' }));
  const primaryActions = visible('.ui-button--primary, [data-variant="primary"]')
    .map((node) => accessibleName(node));
  const nestedPanels = visible('.panel .panel, .card .card, .surface .surface')
    .map(selectorFor).slice(0, 20);
  const repeatedCopy = Object.entries(
    visible('main strong, main p, main [role="status"], main [role="alert"]')
      .map((node) => node.textContent.trim().replace(/\s+/g, ' '))
      .filter((value) => value.length >= 8)
      .reduce((counts, value) => {
        counts[value] = (counts[value] || 0) + 1;
        return counts;
      }, {}),
  ).filter(([, count]) => count >= 3).map(([text, count]) => ({ text, count }));
  return {
    route: routeName,
    viewport: viewportName,
    title: document.title,
    routeState: document.querySelector('[data-route-owner]')?.dataset.routeState || '',
    pageHeading: document.querySelector('[data-page-heading]')?.textContent?.trim() || '',
    visibleH1,
    headingSkips,
    unlabeledControls,
    unlabeledFields,
    smallTargets: smallTargets.slice(0, 30),
    mobileSmallInputs,
    horizontalOverflow: {
      document: root.scrollWidth > root.clientWidth + 1,
      app: app ? app.scrollWidth > app.clientWidth + 1 : false,
      workspace: workspace ? workspace.scrollWidth > workspace.clientWidth + 1 : false,
      documentWidth: root.scrollWidth,
      viewportWidth: root.clientWidth,
    },
    rawProjectIds,
    legacyIcons,
    genericCopy,
    primaryActions,
    nestedPanels,
    repeatedCopy: repeatedCopy.slice(0, 12),
    bodyPreview: bodyText.trim().replace(/\s+/g, ' ').slice(0, 360),
  };
})()`;

function inspectionExpression(route, viewport) {
  return INSPECTION
    .replace('__ROUTE_NAME__', JSON.stringify(route.name))
    .replace('__VIEWPORT_NAME__', JSON.stringify(viewport.name))
    .replace('__TEXT_INPUT_SELECTOR__', JSON.stringify(TEXT_INPUT_SELECTOR));
}

function browserErrors(events, startIndex) {
  return Array.from(new Set(events.slice(startIndex).flatMap((event) => {
    if (event.method === 'Runtime.exceptionThrown') {
      return [event.params?.exceptionDetails?.exception?.description
        || event.params?.exceptionDetails?.text || 'Uncaught browser exception'];
    }
    if (event.method === 'Runtime.consoleAPICalled' && event.params?.type === 'error') {
      return [(event.params.args || []).map((arg) => arg.value || arg.description || '').join(' ')];
    }
    if (event.method === 'Log.entryAdded' && event.params?.entry?.level === 'error') {
      return [event.params.entry.text || 'Browser log error'];
    }
    return [];
  }).filter(Boolean)));
}

async function inspectRoute(session, route, viewport) {
  const eventStart = session.client.events.length;
  await session.client.send('Page.navigate', { url: `${BASE_URL}${route.hash}` });
  await session.waitFor(`(() => {
    const owner = document.querySelector('[data-route-owner]');
    return document.readyState !== 'loading' && owner && owner.dataset.routeState !== 'loading';
  })()`, 20_000);
  const metrics = await session.evaluate(inspectionExpression(route, viewport));
  return { ...metrics, browserErrors: browserErrors(session.client.events, eventStart) };
}

function summaryFor(results) {
  return {
    routes: results.length,
    auditErrors: results.filter((item) => item.auditError).length,
    browserErrors: results.reduce((count, item) => count + (item.browserErrors?.length || 0), 0),
    horizontalOverflow: results.filter((item) => item.horizontalOverflow?.document
      || item.horizontalOverflow?.app || item.horizontalOverflow?.workspace).length,
    duplicateOrMissingH1: results.filter(
      (item) => item.visibleH1 && item.visibleH1.length !== 1,
    ).length,
    headingSkips: results.reduce((count, item) => count + (item.headingSkips?.length || 0), 0),
    unlabeledControls: results.reduce(
      (count, item) => count + (item.unlabeledControls?.length || 0), 0,
    ),
    unlabeledFields: results.reduce(
      (count, item) => count + (item.unlabeledFields?.length || 0), 0,
    ),
    smallTargets: results.reduce((count, item) => count + (item.smallTargets?.length || 0), 0),
    mobileSmallInputs: results.reduce(
      (count, item) => count + (item.mobileSmallInputs?.length || 0), 0,
    ),
    rawProjectIdLeaks: results.reduce(
      (count, item) => count + (item.rawProjectIds?.length || 0), 0,
    ),
    legacyIconFallbacks: results.reduce(
      (count, item) => count + (item.legacyIcons?.length || 0), 0,
    ),
    genericFallbackCopy: results.reduce(
      (count, item) => count + (item.genericCopy?.length || 0), 0,
    ),
  };
}

async function main() {
  fs.rmSync(ARTIFACTS, { recursive: true, force: true });
  fs.mkdirSync(ARTIFACTS, { recursive: true });
  const results = [];
  for (const viewport of VIEWPORTS) {
    const viewportArtifacts = path.join(ARTIFACTS, viewport.name);
    const session = await BrowserSession.open({
      url: `${BASE_URL}${ROUTES[0].hash}`,
      artifacts: viewportArtifacts,
      width: viewport.width,
      height: viewport.height,
    });
    try {
      await session.client.send('Log.enable');
      for (const route of ROUTES) {
        try {
          results.push(await inspectRoute(session, route, viewport));
        } catch (error) {
          results.push({
            route: route.name,
            viewport: viewport.name,
            auditError: error.message || String(error),
          });
        }
      }
    } finally {
      await session.close();
    }
  }
  const report = { summary: summaryFor(results), results };
  fs.writeFileSync(path.join(ARTIFACTS, 'report.json'), `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`INTERFACE_HOLISTIC_AUDIT=${JSON.stringify(report.summary)}\n`);
  const summary = report.summary;
  const exitCode = summary.auditErrors || summary.browserErrors || summary.horizontalOverflow
    || summary.unlabeledControls || summary.unlabeledFields || summary.mobileSmallInputs
    || summary.rawProjectIdLeaks || summary.legacyIconFallbacks
    || summary.genericFallbackCopy ? 1 : 0;
  setImmediate(() => process.exit(exitCode));
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
