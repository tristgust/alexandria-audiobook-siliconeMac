'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { BrowserSession, argsFrom, required, writeJson } = require('./b19_t06_bootstrap_red.js');

const VIEWPORTS = [[1024, 768], [768, 900], [390, 844]];

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
    await session.waitFor(`document.body.dataset.destination === 'script'
      && document.body.dataset.shellState === 'ready'
      && Boolean(document.querySelector('[data-route-owner="script"]'))`);
    await session.waitFor(`Boolean(document.querySelector('[data-script-approve]:not(:disabled)'))`);
    const before = await session.evaluate(`(() => ({
      entryCount: document.querySelectorAll('[data-script-entry]').length,
      lifecycleText: document.querySelector('[data-script-lifecycle-region]')?.textContent || '',
      label: document.querySelector('[data-script-approve]')?.textContent.trim() || '',
    }))()`);
    await session.evaluate(`document.querySelector('[data-script-approve]').click()`);
    await session.waitFor(`Boolean(document.querySelector('[data-script-reviewed-override]'))`);
    const observed = await session.evaluate(`(() => {
      const action = document.querySelector('[data-script-reviewed-override]');
      const rect = action.getBoundingClientRect();
      const issue = document.querySelector('.script-entry-detail');
      return {
        label: action.textContent.trim(),
        visible: rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.bottom <= innerHeight
          && rect.left >= 0 && rect.right <= innerWidth,
        sourceDifferenceText: document.querySelector('.script-review__status')?.textContent || '',
        issueText: issue?.textContent || '',
        entryCountText: document.querySelector('.script-source-context__location')?.textContent || '',
        overflow: Math.max(0, document.documentElement.scrollWidth - innerWidth),
      };
    })()`);
    await session.screenshot('reviewed-source-difference.png');
    assert.equal(before.label, 'Approve Script');
    assert.equal(observed.label, 'Approve reviewed differences');
    assert.equal(observed.visible, true, `${key}: reviewed override action must remain visible at 100% zoom`);
    assert.match(`${observed.sourceDifferenceText} ${observed.issueText}`, /source difference|source text|does not preserve|review/i);
    assert.match(observed.entryCountText, /5,664/);
    assert.equal(observed.overflow, 0);
    assert.deepEqual(runtimeErrors(session), []);
    return { viewport: key, status: 'PASS', before, observed };
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
  process.stdout.write(`B19_T06_LIVE_SCRIPT_REVIEW_OVERRIDE=${JSON.stringify(report)}\n`);
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
