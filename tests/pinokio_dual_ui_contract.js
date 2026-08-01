'use strict';

const assert = require('assert');
const childProcess = require('child_process');
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server.address().port));
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

function waitForUrl(child, timeoutMs = 15000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Timed out waiting for Alexandria URL')), timeoutMs);
    let output = '';
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk) => {
      output += chunk;
      const match = output.match(/Alexandria backend ready: (http:\/\/127\.0\.0\.1:\d+\/)/);
      if (!match) return;
      clearTimeout(timer);
      resolve(match[1]);
    });
    child.once('exit', (code) => {
      clearTimeout(timer);
      reject(new Error(`Alexandria runtime exited before readiness: ${code}`));
    });
  });
}

async function menuContract() {
  const launcher = require(path.join(ROOT, 'pinokio.js'));
  const makeInfo = ({ running = {}, local = {}, installed = true } = {}) => ({
    running: (name) => Boolean(running[name]),
    local: (name) => local[name] || null,
    exists: (name) => name === 'app/env' && installed,
  });

  const idle = await launcher.menu({}, makeInfo());
  assert.equal(idle[0].text, 'Start Alexandria');
  assert.equal(idle[0].href, 'start.js');
  assert.equal(idle[0].default, true);
  assert.equal(idle.some((item) => /preview|stable|new interface/i.test(item.text)), false);

  const starting = await launcher.menu({}, makeInfo({
    running: { 'start.js': true },
  }));
  assert.equal(starting.length, 1);
  assert.equal(starting[0].text, 'Starting Alexandria');
  assert.equal(starting[0].href, 'start.js');

  const online = await launcher.menu({}, makeInfo({
    running: { 'start.js': true },
    local: {
      'start.js': {
        backend_url: 'http://127.0.0.1:4200/',
        url: 'http://127.0.0.1:4200/?pinokio_reload=fixture-run',
      },
    },
  }));
  assert.equal(online[0].text, 'Open Alexandria');
  assert.equal(
    online[0].href,
    'http://127.0.0.1:4200/?pinokio_reload=fixture-run',
  );
  assert.equal(online[0].default, true);
  assert.equal(online[1].text, 'Alexandria Terminal');
  assert.equal(online.some((item) => /preview|stable|new interface/i.test(item.text)), false);
}

async function backendRuntimeOwnershipContract() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'alexandria-backend-runtime-'));
  const fixture = path.join(temporary, 'fixture-backend.js');
  fs.writeFileSync(fixture, `
    'use strict';
    const http = require('http');
    const host = process.env.ALEXANDRIA_HOST || '127.0.0.1';
    const port = Number(process.env.ALEXANDRIA_PORT);
    const server = http.createServer((request, response) => {
      if (request.url === '/api/runtime_status') {
        response.setHeader('Content-Type', 'application/json');
        response.end(JSON.stringify({ process_id: process.pid }));
        return;
      }
      if (request.url === '/') {
        response.setHeader('Content-Type', 'text/html; charset=utf-8');
        response.end('<!doctype html><title>Alexandria</title>CANONICAL_INTERFACE');
        return;
      }
      response.statusCode = 404;
      response.end('missing');
    });
    server.listen(port, host, () => process.stdout.write('fixture-ready\\n'));
    const stop = () => server.close(() => process.exit(0));
    process.once('SIGINT', stop);
    process.once('SIGTERM', stop);
  `);

  const reserve = http.createServer();
  const port = await listen(reserve);
  await close(reserve);

  const stale = childProcess.spawn(process.execPath, [fixture], {
    cwd: ROOT,
    env: {
      ...process.env,
      ALEXANDRIA_HOST: '127.0.0.1',
      ALEXANDRIA_PORT: String(port),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Fixture backend did not start')), 5000);
    stale.stdout.on('data', (chunk) => {
      if (!String(chunk).includes('fixture-ready')) return;
      clearTimeout(timer);
      resolve();
    });
    stale.once('error', reject);
  });

  const child = childProcess.spawn(process.execPath, [
    path.join(ROOT, 'backend_runtime.js'),
    '--host', '127.0.0.1',
    '--port', String(port),
    '--python', process.execPath,
    '--app', fixture,
  ], { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'] });

  try {
    const url = await waitForUrl(child);
    assert.equal(url, `http://127.0.0.1:${port}/`);
    if (stale.exitCode === null) await new Promise((resolve) => stale.once('exit', resolve));

    const runtime = await fetch(new URL('/api/runtime_status', url));
    const payload = await runtime.json();
    assert.notEqual(payload.process_id, stale.pid, 'Stale backend must be replaced');

    const interfaceResponse = await fetch(url);
    assert.equal(interfaceResponse.status, 200);
    assert.match(await interfaceResponse.text(), /CANONICAL_INTERFACE/);
    assert.equal(child.exitCode, null, 'Backend runtime must own the replacement process');
  } finally {
    if (child.exitCode === null) {
      child.kill('SIGTERM');
      await new Promise((resolve) => child.once('exit', resolve));
    }
    if (stale.exitCode === null) {
      stale.kill('SIGTERM');
      await new Promise((resolve) => stale.once('exit', resolve));
    }
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}

function sourceContract() {
  const startPath = path.join(ROOT, 'start.js');
  const start = fs.readFileSync(startPath, 'utf8');
  const startScript = require(startPath);
  const pinokio = fs.readFileSync(path.join(ROOT, 'pinokio.js'), 'utf8');
  const runtime = fs.readFileSync(path.join(ROOT, 'backend_runtime.js'), 'utf8');
  const preflight = fs.readFileSync(path.join(ROOT, 'preflight.js'), 'utf8');
  const readme = fs.readFileSync(path.join(ROOT, 'README.md'), 'utf8');

  assert.equal(startScript.run.length, 3);
  const pattern = startScript.run[1].params.on[0].event;
  const matcher = new RegExp(pattern.slice(1, pattern.lastIndexOf('/')));
  assert.equal(matcher.test('Alexandria backend ready: http://127.0.0.1:'), false);
  assert.equal(matcher.test('Alexandria backend ready: http://127.0.0.1:4200/'), true);

  assert.match(start, /backend_runtime\.js/);
  assert.match(start, /--config config\.json/);
  assert.match(runtime, /ALEXANDRIA_CONFIG_PATH/);
  assert.match(runtime, /resolveConfigPath/);
  assert.match(runtime, /path\.dirname\(python\)/);
  assert.equal(
    (start.match(/shell: "\{\{which\('bash'\)\}\}"/g) || []).length,
    2,
  );
  assert.match(start, /backend_url: "\{\{input\.event\[1\]\}\}"/);
  assert.match(
    start,
    /url: "\{\{input\.event\[1\] \+ '\?pinokio_reload=' \+ input\.id\}\}"/,
  );
  assert.doesNotMatch(start, /stable_ui_server|new_ui_server|preview\.js|stable_url|new_url/);
  assert.doesNotMatch(pinokio, /preview\.js|stable_url|new_url|Stable Build|New Interface|QA Preview/);
  assert.match(preflight, /\/api\/runtime_status/);
  assert.doesNotMatch(preflight, /stableUiCommit|stable-ui-source|stable_ui_server|preview\.js/);
  assert.doesNotMatch(readme, /Start Stable|Stable Build|New Interface \(Writable\)|Read-only QA Preview/);
}

async function main() {
  sourceContract();
  await menuContract();
  await backendRuntimeOwnershipContract();
  process.stdout.write('PINOKIO_SINGLE_UI_CONTRACT=PASS\n');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
