'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { BrowserSession, writeJson } = require('./b19_t06_bootstrap_red.js');

const ROOT = path.resolve(__dirname, '..');
const PATCH = fs.readFileSync(path.join(ROOT, 'app/static/stable_runtime_patch.js'));
const VIEWPORTS = [[1024, 768], [390, 844]];

async function fixtureServer() {
  const requests = [];
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    if (url.pathname === '/') {
      response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      response.end(`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Stable Script fixture</title></head>
        <body data-destination="script"><button id="shell-primary-action">Approve Script</button>
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
        primary_action: { id: 'review_script' },
        artifact: { script_exists: true, metadata_exists: true, entry_count: 5664 },
        fingerprints: { script: 'script-fingerprint', metadata: 'metadata-fingerprint', source: 'source-fingerprint' },
        state_fingerprint: 'state-fingerprint',
      }));
      return;
    }
    if (url.pathname === '/api/script_lifecycle/import-candidate') {
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end('{"status":"none"}');
      return;
    }
    if (url.pathname === '/api/script_lifecycle/accept') {
      let body = '';
      request.setEncoding('utf8');
      request.on('data', (chunk) => { body += chunk; });
      request.on('end', () => {
        const parsed = JSON.parse(body);
        requests.push(parsed);
        response.writeHead(parsed.allow_reviewed_source_differences ? 200 : 409, {
          'Content-Type': 'application/json',
        });
        if (parsed.allow_reviewed_source_differences) {
          response.end(JSON.stringify({ status: 'accepted' }));
        } else {
          response.end(JSON.stringify({ detail: {
            code: 'script_acceptance_blocked',
            message: 'The Script differs from the selected source.',
            context: {
              reviewed_override_available: true,
              audit_fingerprint: 'audit-fingerprint',
              blocking_issues: [{
                code: 'source_text_changed',
                message: 'Generated text does not preserve the corresponding source segment.',
                source_text: 'Dare say it will,',
                output_text: "Dare say it will,' Timbo!",
              }],
            },
          } }));
        }
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
    await session.waitFor(`document.querySelector('#shell-primary-action')?.textContent.trim() === 'Approve Script'`);
    await session.evaluate(`document.querySelector('#shell-primary-action').click()`);
    await session.waitFor(`Boolean(document.querySelector('#stable-source-review-title'))`);
    const review = await session.evaluate(`(() => {
      const approve = [...document.querySelectorAll('button')].find((node) => node.textContent.trim() === 'Approve reviewed version');
      const footer = document.querySelector('.stable-managed-import-footer');
      const rect = approve.getBoundingClientRect();
      const footerRect = footer.getBoundingClientRect();
      return {
        source: document.querySelector('.stable-source-difference section:first-child pre')?.textContent,
        script: document.querySelector('.stable-source-difference section:last-child pre')?.textContent,
        buttonInside: rect.top >= 0 && rect.bottom <= innerHeight && rect.left >= 0 && rect.right <= innerWidth,
        footerInside: footerRect.top >= 0 && footerRect.bottom <= innerHeight,
        focused: document.activeElement === approve,
      };
    })()`);
    assert.equal(review.source, 'Dare say it will,');
    assert.equal(review.script, "Dare say it will,' Timbo!");
    assert.equal(review.buttonInside, true);
    assert.equal(review.footerInside, true);
    assert.equal(review.focused, true);
    await session.screenshot('source-difference-review.png');
    await session.evaluate(`document.activeElement.click()`);
    const deadline = Date.now() + 3000;
    while (server.requests.length < 2 && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    assert.equal(server.requests.length >= 2, true);
    assert.deepEqual(server.requests[0], {
      expected_script_fingerprint: 'script-fingerprint',
      expected_metadata_fingerprint: 'metadata-fingerprint',
      expected_source_fingerprint: 'source-fingerprint',
      expected_state_fingerprint: 'state-fingerprint',
      allow_reviewed_source_differences: false,
      expected_audit_fingerprint: null,
    });
    assert.deepEqual(server.requests[1], {
      expected_script_fingerprint: 'script-fingerprint',
      expected_metadata_fingerprint: 'metadata-fingerprint',
      expected_source_fingerprint: 'source-fingerprint',
      expected_state_fingerprint: 'state-fingerprint',
      allow_reviewed_source_differences: true,
      expected_audit_fingerprint: 'audit-fingerprint',
    });
    return { viewport: key, status: 'PASS', review };
  } finally {
    await session.close();
  }
}

async function main() {
  const index = process.argv.indexOf('--artifacts');
  const artifacts = path.resolve(index >= 0 && process.argv[index + 1]
    ? process.argv[index + 1] : '/tmp/alexandria-stable-script-override');
  const server = await fixtureServer();
  const results = [];
  try {
    for (const [width, height] of VIEWPORTS) results.push(await inspect(server, artifacts, width, height));
  } finally {
    await server.close();
  }
  const report = { status: 'PASS', viewports: VIEWPORTS, results };
  writeJson(path.join(artifacts, 'report.json'), report);
  process.stdout.write(`B19_T06_STABLE_SCRIPT_OVERRIDE=${JSON.stringify(report)}\n`);
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
