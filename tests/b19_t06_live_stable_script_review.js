'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { BrowserSession, argsFrom, required, writeJson } = require('./b19_t06_bootstrap_red.js');

const VIEWPORTS = [[1024, 768], [390, 844]];

function runtimeErrors(session) {
  return session.client.events.filter((item) => item.method === 'Runtime.exceptionThrown'
    || (item.method === 'Runtime.consoleAPICalled' && item.params?.type === 'error'));
}

async function inspect(baseUrl, artifacts, width, height) {
  const key = `${width}x${height}`;
  const folder = path.join(artifacts, key);
  fs.mkdirSync(folder, { recursive: true });
  const target = new URL(baseUrl);
  target.hash = '/script';
  const session = await BrowserSession.open({ url: target.href, artifacts: folder, width, height });
  try {
    await session.waitFor(`document.body?.dataset.destination === 'script'
      && globalThis.__alexandriaStableRuntimePatch === true
      && Boolean(document.querySelector('#shell-primary-action'))`);
    await session.waitFor(`document.querySelector('#shell-primary-action')?.textContent.trim() === 'Approve Script'
      && document.querySelector('#shell-primary-action')?.disabled === false`);
    await session.evaluate(`new Promise((resolve) => setTimeout(resolve, 300))`);
    await session.evaluate(`document.querySelector('#shell-primary-action').click()`);
    await session.waitFor(`Boolean(document.querySelector('#stable-source-review-title'))`);
    const observed = await session.evaluate(`(() => {
      const approve = [...document.querySelectorAll('button')]
        .find((node) => node.textContent.trim() === 'Approve reviewed version');
      const footer = document.querySelector('.stable-managed-import-footer');
      const source = document.querySelector('.stable-source-difference section:first-child pre');
      const script = document.querySelector('.stable-source-difference section:last-child pre');
      const rect = approve.getBoundingClientRect();
      const footerRect = footer.getBoundingClientRect();
      return {
        source: source?.textContent || '',
        script: script?.textContent || '',
        buttonInside: rect.top >= 0 && rect.bottom <= innerHeight
          && rect.left >= 0 && rect.right <= innerWidth,
        footerInside: footerRect.top >= 0 && footerRect.bottom <= innerHeight,
        focused: document.activeElement === approve,
        overflow: Math.max(0, document.documentElement.scrollWidth - innerWidth),
      };
    })()`);
    await session.screenshot('live-source-difference-review.png');
    assert.equal(observed.source, 'Prologue');
    assert.equal(observed.script, 'Cover Prologue');
    assert.equal(observed.buttonInside, true, `${key}: reviewed approval must remain visible`);
    assert.equal(observed.footerInside, true, `${key}: review footer must remain visible`);
    assert.equal(observed.focused, true);
    assert.equal(observed.overflow, 0);
    assert.deepEqual(runtimeErrors(session), []);
    return { viewport: key, status: 'PASS', observed };
  } finally {
    await session.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const baseUrl = required(args, 'url');
  const artifacts = path.resolve(required(args, 'artifacts'));
  const results = [];
  for (const [width, height] of VIEWPORTS) results.push(await inspect(baseUrl, artifacts, width, height));
  const report = { status: 'PASS', baseUrl, viewports: VIEWPORTS, results };
  writeJson(path.join(artifacts, 'report.json'), report);
  process.stdout.write(`B19_T06_LIVE_STABLE_SCRIPT_REVIEW=${JSON.stringify(report)}\n`);
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
