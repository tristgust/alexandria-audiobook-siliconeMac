'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { BrowserSession, writeJson } = require('./b19_t06_bootstrap_red.js');

const ROOT = path.resolve(__dirname, '..');
const PATCH = fs.readFileSync(path.join(ROOT, 'app/static/stable_runtime_patch.js'));
const VIEWPORTS = [[1024, 768], [768, 900], [390, 844]];

function fixtureEntries(count = 120) {
  return Array.from({ length: count }, (_value, index) => ({
    speaker: index % 2 ? 'ADA' : 'NARRATOR',
    text: `Imported Script entry ${index + 1}.`,
    instruct: 'Measured delivery.',
  }));
}

async function fixtureServer() {
  const entries = fixtureEntries();
  const requests = [];
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    if (url.pathname === '/') {
      response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      response.end(`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Stable import fixture</title></head>
        <body data-destination="script"><button id="shell-primary-action">Approve Script</button>
        <section id="script-review-workspace"><div class="script-review-main">
          <details id="script-generation-options-disclosure"><summary>Generation options</summary></details>
          <div id="script-entry-list">Script entries</div>
          <details id="script-provenance-disclosure"><summary>Provenance and versions</summary></details>
        </div></section>
        <section id="script-generation-workflow" hidden>
          <details class="utility-disclosure script-external-disclosure" id="script-external-workflow">
            <summary><span>Work with ChatGPT</span><span>Ready</span></summary>
            <div class="utility-disclosure-body">
              <p class="external-workflow-intro">Old instructions.</p>
              <div class="task-bundle-workspace">
                <section class="task-bundle-panel"><div><h4 id="task-bundle-export-heading">Export task</h4><p>Old export copy.</p></div></section>
                <section class="task-bundle-panel"><div><h4 id="task-bundle-import-heading">Import completed task</h4><p>Old import copy.</p></div>
                  <div class="file-picker" data-file-picker><input id="completed-task-file" type="file"><span class="file-picker-name">Completed task</span></div>
                  <div class="file-picker" data-file-picker id="original-task-file-wrap"><input id="original-task-file" type="file"><span class="file-picker-meta">Old original help.</span></div>
                  <button id="btn-import-completed-task">Import completed task</button>
                </section>
              </div>
            </div>
          </details>
        </section>
        <script src="/static/stable_runtime_patch.js"></script></body></html>`);
      return;
    }
    if (url.pathname === '/static/stable_runtime_patch.js') {
      response.writeHead(200, { 'Content-Type': 'text/javascript; charset=utf-8' });
      response.end(PATCH);
      return;
    }
    if (url.pathname === '/api/script_lifecycle/status') {
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({
        state: 'review_required',
        primary_action: { id: 'review_imported_script' },
        artifact: { script_exists: false },
      }));
      return;
    }
    if (url.pathname === '/api/script_lifecycle/import-candidate') {
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({
        status: 'ready', fingerprint: 'fixture-fingerprint', filename: 'imported-script.json',
        entry_count: entries.length, speaker_count: 2, entries,
      }));
      return;
    }
    if (url.pathname === '/api/script_lifecycle/import-candidate/apply') {
      let body = '';
      request.setEncoding('utf8');
      request.on('data', (chunk) => { body += chunk; });
      request.on('end', () => {
        requests.push({ method: request.method, path: url.pathname, body: JSON.parse(body) });
        response.writeHead(200, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify({ status: 'applied' }));
      });
      return;
    }
    response.writeHead(404).end('missing');
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  return {
    server,
    requests,
    url: `http://127.0.0.1:${server.address().port}/#/script`,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

async function inspect(server, artifacts, width, height) {
  const key = `${width}x${height}`;
  const folder = path.join(artifacts, key);
  fs.mkdirSync(folder, { recursive: true });
  const session = await BrowserSession.open({ url: server.url, artifacts: folder, width, height });
  try {
    await session.waitFor(`document.querySelector('#shell-primary-action')?.dataset.stableManagedImport === 'true'`);
    await session.waitFor(`document.querySelector('#script-external-workflow')?.parentElement?.classList.contains('script-review-main')`);
    const taskWorkflow = await session.evaluate(`(() => {
      const workflow=document.querySelector('#script-external-workflow');
      workflow.open=true;
      const rect=workflow.getBoundingClientRect();
      return {
        mounted: workflow.parentElement?.classList.contains('script-review-main'),
        beforeEntries: workflow.nextElementSibling?.id === 'script-entry-list',
        visible: rect.width > 0 && rect.height > 0,
        text: workflow.textContent,
        completedLabel: document.querySelector('#completed-task-file')?.closest('[data-file-picker]')?.querySelector('.file-picker-name')?.textContent,
        originalHelp: document.querySelector('#original-task-file-wrap .file-picker-meta')?.textContent,
        importButton: document.querySelector('#btn-import-completed-task')?.textContent,
        overflow: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
      };
    })()`);
    assert.equal(taskWorkflow.mounted, true, `${key}: ChatGPT workflow must be in the visible Script review`);
    assert.equal(taskWorkflow.beforeEntries, true, `${key}: ChatGPT workflow should be above the Script entries`);
    assert.equal(taskWorkflow.visible, true, `${key}: ChatGPT workflow must be visible when opened`);
    assert.match(taskWorkflow.text, /attach it directly to an ordinary ChatGPT conversation/i);
    assert.match(taskWorkflow.text, /Do not unzip/i);
    assert.equal(taskWorkflow.completedLabel, 'Completed task ZIP or result JSON');
    assert.match(taskWorkflow.originalHelp, /fallback or legacy JSON/i);
    assert.equal(taskWorkflow.importButton, 'Import completed task');
    assert.equal(taskWorkflow.overflow, 0, `${key}: ChatGPT workflow must not overflow`);
    await session.evaluate(`document.querySelector('#shell-primary-action').click()`);
    await session.waitFor(`Boolean(document.querySelector('.stable-managed-import-dialog'))`);
    const observed = await session.evaluate(`(() => {
      const dialog = document.querySelector('.stable-managed-import-dialog');
      const body = document.querySelector('.stable-managed-import-body');
      const footer = document.querySelector('.stable-managed-import-footer');
      const apply = [...document.querySelectorAll('button')].find((node) => node.textContent.trim() === 'Apply imported Script');
      const rect = (node) => { const value = node.getBoundingClientRect(); return { top: value.top, right: value.right, bottom: value.bottom, left: value.left, width: value.width, height: value.height }; };
      return {
        viewport: { width: innerWidth, height: innerHeight },
        dialog: rect(dialog), body: rect(body), footer: rect(footer), apply: rect(apply),
        bodyScrolls: body.scrollHeight > body.clientHeight,
        previewRows: document.querySelectorAll('.stable-managed-import-entry').length,
        overflow: Math.max(0, document.documentElement.scrollWidth - innerWidth),
        focused: document.activeElement === apply,
      };
    })()`);
    await session.screenshot('import-review.png');
    const inside = observed.apply.left >= 0 && observed.apply.top >= 0
      && observed.apply.right <= observed.viewport.width
      && observed.apply.bottom <= observed.viewport.height;
    const footerInside = observed.footer.top >= 0
      && observed.footer.bottom <= observed.viewport.height;
    assert.equal(inside, true, `${key}: Apply imported Script must be visible at 100% zoom`);
    assert.equal(footerInside, true, `${key}: import footer must remain inside the viewport; ${JSON.stringify(observed)}`);
    assert.equal(observed.bodyScrolls, true, `${key}: only the review body should scroll`);
    assert.equal(observed.previewRows, 30, `${key}: preview should remain bounded`);
    assert.equal(observed.overflow, 0, `${key}: no horizontal overflow`);
    assert.equal(observed.focused, true, `${key}: Apply should receive focus`);
    await session.evaluate(`document.activeElement.click()`);
    const deadline = Date.now() + 3000;
    while (!server.requests.length && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    assert.deepEqual(server.requests.at(-1), {
      method: 'POST',
      path: '/api/script_lifecycle/import-candidate/apply',
      body: { expected_candidate_fingerprint: 'fixture-fingerprint' },
    });
    return { viewport: key, status: 'PASS', taskWorkflow, observed };
  } finally {
    await session.close();
  }
}

async function main() {
  const index = process.argv.indexOf('--artifacts');
  const artifacts = path.resolve(index >= 0 && process.argv[index + 1]
    ? process.argv[index + 1] : '/tmp/alexandria-stable-import-visibility');
  const server = await fixtureServer();
  const results = [];
  try {
    for (const [width, height] of VIEWPORTS) results.push(await inspect(server, artifacts, width, height));
  } finally {
    await server.close();
  }
  const report = { status: 'PASS', viewports: VIEWPORTS, results };
  writeJson(path.join(artifacts, 'report.json'), report);
  process.stdout.write(`B19_T06_STABLE_IMPORT_VISIBILITY=${JSON.stringify(report)}\n`);
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
