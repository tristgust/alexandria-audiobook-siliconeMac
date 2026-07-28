'use strict';

const fs = require('fs');
const path = require('path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');

async function inspectViewport(baseUrl, projectId, artifacts, width, height) {
  const viewport = `${width}x${height}`;
  const folder = path.join(artifacts, viewport);
  const route = `#/export?project=${encodeURIComponent(projectId)}`;
  const session = await BrowserSession.open({
    url: `${baseUrl}${route}`,
    artifacts: folder,
    width,
    height,
  });
  try {
    await session.waitFor(`document.body.dataset.shellState === 'ready'
      && document.querySelector('[data-route-owner="export"]')
      && document.querySelector('#export-title')?.value === 'Human Nature'
      && document.querySelector('.export-publication img.source-cover')?.complete
      && document.querySelector('.export-publication img.source-cover')?.naturalWidth > 0`, 30000);
    const observation = await session.evaluate(`(async () => {
      const owner = document.querySelector('[data-route-owner="export"]');
      const cover = owner.querySelector('.export-publication img.source-cover');
      const actions = owner.querySelector('.export-cover-actions');
      const visible = (node) => node.getBoundingClientRect().width > 0
        && node.getBoundingClientRect().height > 0;
      const controls = [...owner.querySelectorAll('button,a[href],input,select,textarea')]
        .filter((node) => !node.disabled && visible(node));
      const accessibleName = (node) => (node.getAttribute('aria-label')
        || node.textContent
        || node.closest('label')?.textContent
        || (node.id && document.querySelector(
          'label[for="' + CSS.escape(node.id) + '"]',
        )?.textContent)
        || '').trim();
      const replace = [...actions.querySelectorAll('button')]
        .find((button) => button.textContent.trim() === 'Replace');
      replace?.focus();
      const exportResponse = await fetch('/api/export');
      const aggregate = await exportResponse.json();
      const coverResponse = await fetch(cover.src);
      const coverBytes = await coverResponse.arrayBuffer();
      const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', coverBytes))]
        .map((value) => value.toString(16).padStart(2, '0')).join('');
      return {
        route: location.hash,
        pageState: owner.dataset.pageState,
        title: owner.querySelector('#export-title')?.value || '',
        author: owner.querySelector('#export-author')?.value || '',
        titleInvalid: owner.querySelector('#export-title')?.getAttribute('aria-invalid'),
        authorInvalid: owner.querySelector('#export-author')?.getAttribute('aria-invalid'),
        coverAlt: cover.alt,
        coverNaturalWidth: cover.naturalWidth,
        replaceVisible: Boolean(replace && visible(replace)),
        replaceFocused: document.activeElement === replace,
        removeCount: [...actions.querySelectorAll('button')]
          .filter((button) => button.textContent.trim() === 'Remove').length,
        unnamedControls: controls.filter((node) => !accessibleName(node)).map((node) => node.id),
        horizontalOverflow: document.documentElement.scrollWidth - innerWidth,
        aggregateMetadata: aggregate.metadata,
        aggregateCover: aggregate.cover,
        coverHttp: {
          ok: coverResponse.ok,
          contentType: coverResponse.headers.get('content-type'),
          sizeBytes: coverBytes.byteLength,
          sha256: digest,
        },
      };
    })()`);
    await session.screenshot('export-publication-source-cover.png');
    const expectedSha = '458f5e51fb3b208e9000407eade0a18a1ad7be1339741fd30203e8be15f968b0';
    const assertions = {
      route: observation.route.includes(`project=${projectId}`),
      titlePrefilled: observation.title === 'Human Nature' && observation.titleInvalid === null,
      authorPrefilled: observation.author === 'Paul Cornell' && observation.authorInvalid === null,
      sourceCoverVisible: observation.coverNaturalWidth > 0
        && observation.coverAlt === 'Cover for Human Nature',
      sourceCoverProvenance: observation.aggregateCover.kind === 'source_epub'
        && observation.aggregateCover.user_provided === false,
      sourceCoverBytes: observation.coverHttp.ok
        && observation.coverHttp.contentType === 'image/jpeg'
        && observation.coverHttp.sizeBytes === 73543
        && observation.coverHttp.sha256 === expectedSha,
      replaceAvailable: observation.replaceVisible && observation.replaceFocused,
      falseRemoveAbsent: observation.removeCount === 0,
      accessibleControls: observation.unnamedControls.length === 0,
      noHorizontalOverflow: observation.horizontalOverflow <= 1,
      apiMetadata: observation.aggregateMetadata.title === 'Human Nature'
        && observation.aggregateMetadata.author === 'Paul Cornell',
    };
    return {
      viewport,
      status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL',
      route: `${baseUrl}${route}`,
      selectors: {
        owner: '[data-route-owner="export"]',
        title: '#export-title',
        author: '#export-author',
        cover: '.export-publication img.source-cover',
        coverActions: '.export-cover-actions',
      },
      assertions,
      observation,
      runtimeErrors: session.client.events.filter((event) => (
        event.method === 'Runtime.exceptionThrown'
        || (event.method === 'Runtime.consoleAPICalled' && event.params?.type === 'error')
      )),
    };
  } finally {
    await session.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const baseUrl = required(args, 'base-url').replace(/\/?$/, '/');
  const projectId = required(args, 'project-id');
  const artifacts = path.resolve(required(args, 'artifacts'));
  const viewports = [[390, 844], [768, 1024], [1280, 800]];
  const results = [];
  for (const [width, height] of viewports) {
    results.push(await inspectViewport(baseUrl, projectId, artifacts, width, height));
  }
  const report = {
    status: results.every((result) => result.status === 'PASS') ? 'PASS' : 'FAIL',
    baseUrl,
    projectId,
    results,
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  fs.writeFileSync(
    path.join(artifacts, 'action.log'),
    results.map(
      (result) => `${result.viewport} ${result.status} ${JSON.stringify(result.assertions)}`,
    ).join('\n') + '\n',
  );
  process.stdout.write(`EXPORT_METADATA_HANDOFF_BROWSER=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
