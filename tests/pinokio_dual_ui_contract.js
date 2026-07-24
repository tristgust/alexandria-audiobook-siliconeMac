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
    const timer = setTimeout(() => reject(new Error('Timed out waiting for stable UI URL')), timeoutMs);
    let output = '';
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk) => {
      output += chunk;
      const match = output.match(/(http:\/\/127\.0\.0\.1:\d+\/)/);
      if (!match) return;
      clearTimeout(timer);
      resolve(match[1]);
    });
    child.once('exit', (code) => {
      clearTimeout(timer);
      reject(new Error(`Stable UI server exited before readiness: ${code}`));
    });
  });
}

async function menuContract() {
  const launcher = require(path.join(ROOT, 'pinokio.js'));
  const makeInfo = ({ running = {}, ready = {}, local = {}, installed = true } = {}) => ({
    running: (name) => Boolean(running[name]),
    ready: (name) => Boolean(ready[name]),
    local: (name) => local[name] || null,
    exists: (name) => name === 'app/env' && installed,
  });

  const idle = await launcher.menu({}, makeInfo());
  assert.equal(idle[0].text, 'Start Stable + New Preview');
  assert.equal(idle[0].href, 'preview.js');
  assert.equal(idle[0].default, true);

  const both = await launcher.menu({}, makeInfo({
    running: { 'start.js': true, 'preview.js': true },
    ready: { 'start.js': true, 'preview.js': true },
    local: {
      'start.js': { url: 'http://127.0.0.1:41001/' },
      'preview.js': { url: 'http://127.0.0.1:41002/' },
    },
  }));
  assert.equal(both[0].text, 'Open Stable Build (Old UI)');
  assert.equal(both[0].href, 'http://127.0.0.1:41001/');
  assert.equal(both[1].text, 'Open New UI Preview (Read-only)');
  assert.equal(both[1].href, 'http://127.0.0.1:41002/');
}

async function proxyContract() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'alexandria-dual-ui-'));
  const staticRoot = path.join(temporary, 'static');
  fs.mkdirSync(staticRoot, { recursive: true });
  fs.writeFileSync(path.join(staticRoot, 'index.html'), '<!doctype html><title>Old UI</title>OLD_UI_MARKER\n');
  fs.writeFileSync(path.join(staticRoot, 'legacy.css'), 'body { display: block; }\n');

  const upstream = http.createServer((request, response) => {
    if (request.url === '/api/projects') {
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end('{"projects":[{"id":"fixture"}]}');
      return;
    }
    if (request.url === '/api/mutate') {
      let body = '';
      request.setEncoding('utf8');
      request.on('data', (chunk) => { body += chunk; });
      request.on('end', () => {
        response.writeHead(201, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify({ method: request.method, body }));
      });
      return;
    }
    response.writeHead(404).end('missing');
  });
  const upstreamPort = await listen(upstream);
  const child = childProcess.spawn(process.execPath, [
    path.join(ROOT, 'stable_ui_server.js'),
    '--static-root', staticRoot,
    '--upstream', `http://127.0.0.1:${upstreamPort}`,
    '--host', '127.0.0.1',
    '--port', '0',
  ], { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'] });

  try {
    const url = await waitForUrl(child);
    const page = await fetch(url);
    assert.equal(page.status, 200);
    assert.match(await page.text(), /OLD_UI_MARKER/);
    assert.equal(page.headers.get('x-alexandria-interface'), 'stable-92c89d8');

    const projects = await fetch(new URL('/api/projects', url));
    assert.equal(projects.status, 200);
    assert.deepEqual(await projects.json(), { projects: [{ id: 'fixture' }] });

    const mutation = await fetch(new URL('/api/mutate', url), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{"save":true}',
    });
    assert.equal(mutation.status, 201);
    assert.deepEqual(await mutation.json(), { method: 'POST', body: '{"save":true}' });
  } finally {
    child.kill('SIGTERM');
    await new Promise((resolve) => child.once('exit', resolve));
    await close(upstream);
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}

async function sourceContract() {
  const start = fs.readFileSync(path.join(ROOT, 'start.js'), 'utf8');
  const preview = fs.readFileSync(path.join(ROOT, 'preview.js'), 'utf8');
  assert.match(start, /stable_ui_server\.js/);
  assert.match(start, /url: "\{\{input\.event\[1\]\}\}"/);
  assert.match(preview, /method: "script\.start"/);
  assert.match(preview, /uri: "start\.js"/);
  assert.match(preview, /b19_t06_live_readonly_scale\.js --serve-only/);
}

async function main() {
  await sourceContract();
  await menuContract();
  await proxyContract();
  process.stdout.write('PINOKIO_DUAL_UI_CONTRACT=PASS\n');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
