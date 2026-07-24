'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');

const VIEWPORTS = [[1536, 1024], [1024, 768], [1440, 1000], [390, 844]];
const ROUTES = [
  { name: 'cast', hash: '/cast', mode: 'project' },
  { name: 'settings', hash: '/settings?mode=accessibility', mode: 'global' },
  { name: 'maintenance', hash: '/more/maintenance?mode=recovery', mode: 'global' },
];

async function settle(session, destination) {
  await session.waitFor(`document.readyState === 'complete'
    && document.body.dataset.destination === ${JSON.stringify(destination)}`);
  await session.evaluate(`new Promise((resolve) => requestAnimationFrame(
    () => requestAnimationFrame(() => resolve(true))
  ))`);
}

async function metrics(session) {
  return session.evaluate(`(() => {
    const visible = (node) => node && !node.hidden && getComputedStyle(node).display !== 'none';
    const rect = (node) => {
      if (!visible(node)) return null;
      const value = node.getBoundingClientRect();
      return { x: Math.round(value.x), y: Math.round(value.y),
        width: Math.round(value.width), height: Math.round(value.height),
        bottom: Math.round(value.bottom), right: Math.round(value.right) };
    };
    const rail = document.querySelector('[data-app-rail],.app-sidebar,.app-rail');
    const projectHeader = document.querySelector('[data-project-header],#canonical-project-header');
    const globalHeader = document.querySelector('[data-global-header],#canonical-global-header');
    const root = document.querySelector('#canonical-destination-root,[data-canonical-destination-root],main');
    const heading = [...document.querySelectorAll('[data-page-heading],main h1,main h2')].find(visible);
    const rootStyle = getComputedStyle(document.documentElement);
    const bodyStyle = getComputedStyle(document.body);
    const headingStyle = heading ? getComputedStyle(heading) : null;
    return {
      viewport: { width: innerWidth, height: innerHeight },
      rail: rect(rail), projectHeader: rect(projectHeader), globalHeader: rect(globalHeader),
      root: rect(root), heading: rect(heading),
      tokens: {
        canvas: rootStyle.getPropertyValue('--color-canvas').trim()
          || rootStyle.getPropertyValue('--alexandria-canvas').trim(),
        action: rootStyle.getPropertyValue('--color-action').trim()
          || rootStyle.getPropertyValue('--alexandria-accent').trim(),
        bodyFont: bodyStyle.fontFamily, headingFont: headingStyle?.fontFamily || null,
        bodySize: bodyStyle.fontSize, headingSize: headingStyle?.fontSize || null,
      },
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 1,
      destinationRoots: document.querySelectorAll('#canonical-destination-root,[data-canonical-destination-root]').length,
    };
  })()`);
}

function geometryAssertions(capture) {
  const { width } = capture.metrics.viewport;
  const wide = width >= 1180;
  const compact = width === 1024;
  const expectedRail = wide ? 224 : compact ? 184 : null;
  const header = capture.route.mode === 'project'
    ? capture.metrics.projectHeader : capture.metrics.globalHeader;
  const expectedHeader = capture.route.mode === 'project' ? 104 : 128;
  return [
    { id: 'shell-rail-geometry', pass: expectedRail === null || capture.metrics.rail?.width === expectedRail,
      expected: expectedRail, observed: capture.metrics.rail },
    { id: 'shell-header-geometry', pass: width < 600 || header?.height === expectedHeader,
      expected: width < 600 ? 'responsive' : expectedHeader, observed: header },
    { id: 'canvas-token', pass: capture.metrics.tokens.canvas.toUpperCase() === '#F6F3EC',
      expected: '#F6F3EC', observed: capture.metrics.tokens.canvas },
    { id: 'action-token', pass: capture.metrics.tokens.action.toUpperCase() === '#3F6E6A',
      expected: '#3F6E6A', observed: capture.metrics.tokens.action },
    { id: 'body-font', pass: capture.metrics.tokens.bodyFont.includes('Source Sans 3'),
      expected: 'Source Sans 3', observed: capture.metrics.tokens.bodyFont },
    { id: 'heading-font', pass: capture.metrics.tokens.headingFont?.includes('Source Serif 4'),
      expected: 'Source Serif 4', observed: capture.metrics.tokens.headingFont },
    { id: 'one-destination-root', pass: capture.metrics.destinationRoots === 1,
      expected: 1, observed: capture.metrics.destinationRoots },
    { id: 'no-horizontal-overflow', pass: !capture.metrics.horizontalOverflow,
      expected: false, observed: capture.metrics.horizontalOverflow },
  ];
}

async function captureUrl(baseUrl, artifacts) {
  const captures = [];
  for (const [width, height] of VIEWPORTS) {
    const viewportName = `${width}x${height}`;
    const session = await BrowserSession.open({
      url: baseUrl, artifacts: path.join(artifacts, viewportName), width, height,
    });
    try {
      for (const route of ROUTES) {
        await session.evaluate(`location.hash = ${JSON.stringify(route.hash)}`);
        const destination = route.name === 'maintenance' ? 'more' : route.name;
        await settle(session, destination);
        const capture = { viewport: viewportName, route, metrics: await metrics(session) };
        capture.assertions = geometryAssertions(capture);
        await session.screenshot(`${route.name}.png`);
        captures.push(capture);
      }
    } finally {
      await session.close();
    }
  }
  return captures;
}

function capturesFromManifest(manifestFile) {
  const manifest = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
  if (!Array.isArray(manifest.visualCaptures)) {
    throw new Error('manifest.visualCaptures must be an array');
  }
  return manifest.visualCaptures.map((capture) => ({
    ...capture, assertions: geometryAssertions(capture),
  }));
}

function referenceEvidence(referenceRoot) {
  const root = path.resolve(referenceRoot);
  if (!fs.statSync(root).isDirectory()) throw new Error(`Reference root is not a directory: ${root}`);
  const files = fs.readdirSync(root).filter((name) => name.endsWith('.png')).sort();
  if (files.length === 0) throw new Error(`Reference root contains no PNG files: ${root}`);
  return files.map((name) => {
    const bytes = fs.readFileSync(path.join(root, name));
    return { name, sha256: crypto.createHash('sha256').update(bytes).digest('hex') };
  });
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required(args, 'artifacts'));
  const captures = args.url
    ? await captureUrl(String(args.url), artifacts)
    : capturesFromManifest(path.resolve(required(args, 'manifest')));
  const references = args.manifest
    ? referenceEvidence(required(args, 'reference-root')) : [];
  const assertions = captures.flatMap((capture) => capture.assertions.map((assertion) => ({
    viewport: capture.viewport, route: capture.route.name, ...assertion,
  })));
  const requiredPairs = VIEWPORTS.flatMap(([width, height]) => ROUTES.map(
    (route) => `${width}x${height}:${route.name}`,
  ));
  const capturedPairs = new Set(captures.map((item) => `${item.viewport}:${item.route.name}`));
  assertions.push({ id: 'complete-viewport-route-matrix',
    pass: requiredPairs.every((item) => capturedPairs.has(item)),
    expected: requiredPairs, observed: [...capturedPairs] });
  const report = {
    status: assertions.every((item) => item.pass) ? 'PASS' : 'RED',
    references,
    captures,
    assertions,
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  process.stdout.write(`B19_T06_VISUAL=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
