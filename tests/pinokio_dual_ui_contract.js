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
    const timer = setTimeout(() => reject(new Error('Timed out waiting for interface URL')), timeoutMs);
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
      reject(new Error(`Interface server exited before readiness: ${code}`));
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
  assert.equal(idle[0].text, 'Start Alexandria Interfaces');
  assert.equal(idle[0].href, 'start.js');
  assert.equal(idle[0].default, true);

  const both = await launcher.menu({}, makeInfo({
    running: { 'start.js': true, 'preview.js': true },
    local: {
      'start.js': {
        url: 'http://127.0.0.1:41002/',
        new_url: 'http://127.0.0.1:41002/',
        stable_url: 'http://127.0.0.1:41001/',
      },
      'preview.js': { preview_url: 'http://127.0.0.1:41003/' },
    },
  }));
  assert.equal(both[0].text, 'Open New Interface (Writable)');
  assert.equal(both[0].href, 'http://127.0.0.1:41002/');
  assert.equal(both[0].default, true);
  assert.equal(both[1].text, 'Open Stable Build (Old UI)');
  assert.equal(both[1].href, 'http://127.0.0.1:41001/');
  assert.equal(both[2].text, 'Open Read-only QA Preview');
  assert.equal(both[2].href, 'http://127.0.0.1:41003/');
}

async function stableProxyContract() {
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
    const pageText = await page.text();
    assert.match(pageText, /OLD_UI_MARKER/);
    assert.match(pageText, /\/static\/stable_runtime_patch\.js/);
    assert.equal(page.headers.get('x-alexandria-interface'), 'stable-92c89d8');

    const compatibility = await fetch(new URL('/static/stable_runtime_patch.js', url));
    assert.equal(compatibility.status, 200);
    assert.match(await compatibility.text(), /stableManagedImport/);

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

async function backendRuntimeOwnershipContract() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'alexandria-backend-runtime-'));
  const fixture = path.join(temporary, 'fixture-backend.js');
  fs.writeFileSync(fixture, `
    'use strict';
    const http = require('http');
    const host = process.env.ALEXANDRIA_HOST || '127.0.0.1';
    const port = Number(process.env.ALEXANDRIA_PORT);
    const server = http.createServer((request, response) => {
      response.setHeader('Content-Type', 'application/json');
      if (request.url === '/api/projects') {
        response.end('{"projects":[]}');
        return;
      }
      if (request.url === '/api/runtime_status') {
        response.end(JSON.stringify({ process_id: process.pid }));
        return;
      }
      response.statusCode = 404;
      response.end('{"error":"missing"}');
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
    const timer = setTimeout(() => reject(new Error('fixture backend did not start')), 5000);
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
    if (stale.exitCode === null) {
      await new Promise((resolve) => stale.once('exit', resolve));
    }
    const runtime = await fetch(new URL('/api/runtime_status', url));
    const payload = await runtime.json();
    assert.notEqual(payload.process_id, stale.pid, 'stale backend must be replaced');
    assert.equal(child.exitCode, null, 'backend runtime must own the replacement process');
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

async function newProxyContract() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'alexandria-new-ui-'));
  const staticRoot = path.join(temporary, 'static');
  fs.mkdirSync(staticRoot, { recursive: true });
  fs.writeFileSync(path.join(staticRoot, 'index.html'), '<!doctype html><title>New UI</title>NEW_UI_MARKER\n');

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
    path.join(ROOT, 'new_ui_server.js'),
    '--static-root', staticRoot,
    '--upstream', `http://127.0.0.1:${upstreamPort}`,
    '--host', '127.0.0.1',
    '--port', '0',
  ], { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'] });

  try {
    const url = await waitForUrl(child);
    const page = await fetch(url);
    assert.equal(page.status, 200);
    assert.match(await page.text(), /NEW_UI_MARKER/);
    assert.equal(page.headers.get('x-alexandria-interface'), 'new-writable');
    assert.equal(page.headers.get('x-alexandria-mutations'), 'enabled');

    const health = await fetch(new URL('/__alexandria_new_ui__', url));
    assert.equal(health.status, 200);
    assert.equal((await health.json()).mutations, 'enabled');

    const projects = await fetch(new URL('/api/projects', url));
    assert.equal(projects.status, 200);
    assert.deepEqual(await projects.json(), { projects: [{ id: 'fixture' }] });

    const mutation = await fetch(new URL('/api/mutate', url), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{"save":true}',
    });
    assert.equal(mutation.status, 201);
    assert.equal(mutation.headers.get('x-alexandria-mutations'), 'enabled');
    assert.deepEqual(await mutation.json(), { method: 'POST', body: '{"save":true}' });
  } finally {
    child.kill('SIGTERM');
    await new Promise((resolve) => child.once('exit', resolve));
    await close(upstream);
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}

async function sourceContract() {
  const startPath = path.join(ROOT, 'start.js');
  const newPath = path.join(ROOT, 'new.js');
  const previewPath = path.join(ROOT, 'preview.js');
  const start = fs.readFileSync(startPath, 'utf8');
  const newUi = fs.readFileSync(newPath, 'utf8');
  const preview = fs.readFileSync(previewPath, 'utf8');
  const startScript = require(startPath);
  const newScript = require(newPath);
  const previewScript = require(previewPath);
  const eventPatterns = [
    {
      pattern: startScript.run[1].params.on[0].event,
      incomplete: 'Alexandria backend ready: http://127.0.0.1:',
      complete: 'Alexandria backend ready: http://127.0.0.1:4200/',
    },
    {
      pattern: startScript.run[3].params.on[0].event,
      incomplete: 'Alexandria stable interface (92c89d8): http://127.0.0.1:',
      complete: 'Alexandria stable interface (92c89d8): http://127.0.0.1:60306/',
    },
    {
      pattern: startScript.run[5].params.on[0].event,
      incomplete: 'Alexandria new writable interface: http://127.0.0.',
      complete: 'Alexandria new writable interface: http://127.0.0.1:60310/',
    },
    {
      pattern: previewScript.run[1].params.on[0].event,
      incomplete: 'Alexandria read-only repair preview: http://127.0.0.',
      complete: 'Alexandria read-only repair preview: http://127.0.0.1:60314/',
    },
  ];
  for (const { pattern, incomplete, complete } of eventPatterns) {
    const matcher = new RegExp(pattern.slice(1, pattern.lastIndexOf('/')));
    assert.equal(matcher.test(incomplete), false, 'URL parser must not accept an incomplete stream fragment');
    assert.equal(matcher.test(complete), true, 'URL parser must wait for the complete URL');
  }
  assert.match(start, /backend_runtime\.js/);
  assert.match(start, /stable_ui_server\.js/);
  assert.match(start, /new_ui_server\.js/);
  assert.match(start, /stable_url: "\{\{input\.event\[1\]\}\}"/);
  assert.match(start, /new_url: "\{\{input\.event\[1\]\}\}"/);
  assert.match(start, /url: "\{\{input\.event\[1\]\}\}"/);
  assert.match(newUi, /method: "script\.start"/);
  assert.match(newUi, /uri: "start\.js"/);
  assert.doesNotMatch(newUi, /new_ui_server\.js/);
  assert.match(preview, /method: "script\.start"/);
  assert.match(preview, /uri: "start\.js"/);
  assert.match(preview, /b19_t06_live_readonly_scale\.js --serve-only/);
  assert.match(preview, /preview_url: "\{\{input\.event\[1\]\}\}"/);
  assert.doesNotMatch(preview, /\n\s*url: "\{\{input\.event\[1\]\}\}"/);
}

async function main() {
  await sourceContract();
  await menuContract();
  await backendRuntimeOwnershipContract();
  await stableProxyContract();
  await newProxyContract();
  process.stdout.write('PINOKIO_DUAL_UI_CONTRACT=PASS\n');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
