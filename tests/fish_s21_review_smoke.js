'use strict';

const fs = require('fs');
const http = require('http');
const net = require('net');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function argsFrom(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith('--')) continue;
    result[key.slice(2)] = argv[index + 1] && !argv[index + 1].startsWith('--')
      ? argv[++index] : true;
  }
  return result;
}

function required(args, key) {
  if (!args[key] || args[key] === true) throw new Error(`--${key} is required`);
  return path.resolve(String(args[key]));
}

function writeJson(filename, value) {
  fs.mkdirSync(path.dirname(filename), { recursive: true });
  fs.writeFileSync(filename, `${JSON.stringify(value, null, 2)}\n`);
}

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

async function waitForHttp(url) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try {
      if ((await fetch(url)).ok) return;
    } catch (_) { /* retry */ }
    await wait(50);
  }
  throw new Error(`Timed out waiting for ${url}`);
}

class CdpClient {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.pending = new Map();
    this.events = [];
    this.nextId = 1;
    this.opened = new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, { once: true });
      this.socket.addEventListener('error', reject, { once: true });
    });
    this.socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if (!message.id) { this.events.push(message); return; }
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(JSON.stringify(message.error)));
      else pending.resolve(message.result || {});
    });
  }

  async send(method, params = {}) {
    await this.opened;
    const id = this.nextId++;
    const response = new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
    this.socket.send(JSON.stringify({ id, method, params }));
    return response;
  }

  close() { this.socket.close(); }
}

class BrowserSession {
  static async open(url, artifacts, width, height) {
    const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'alexandria-fish-review-'));
    const port = await freePort();
    const browser = spawn(CHROME, [
      '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
      '--disable-background-networking', '--disable-component-update', '--disable-sync',
      '--metrics-recording-only', '--mute-audio', '--remote-allow-origins=*',
      `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, 'about:blank',
    ], { stdio: 'ignore' });
    await waitForHttp(`http://127.0.0.1:${port}/json/version`);
    const response = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent('about:blank')}`, { method: 'PUT' });
    const target = await response.json();
    const session = new BrowserSession(browser, profile, artifacts, new CdpClient(target.webSocketDebuggerUrl));
    await session.client.send('Page.enable');
    await session.client.send('Runtime.enable');
    await session.client.send('Emulation.setDeviceMetricsOverride', {
      width, height, screenWidth: width, screenHeight: height,
      deviceScaleFactor: 1, mobile: width <= 500,
    });
    await session.client.send('Page.navigate', { url });
    return session;
  }

  constructor(browser, profile, artifacts, client) {
    Object.assign(this, { browser, profile, artifacts, client });
  }

  async evaluate(expression) {
    const result = await this.client.send('Runtime.evaluate', {
      expression, returnByValue: true, awaitPromise: true,
    });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result?.value;
  }

  async waitFor(expression) {
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      const value = await this.evaluate(expression);
      if (value) return value;
      await wait(50);
    }
    throw new Error(`Condition did not become true: ${expression}`);
  }

  async screenshot(filename) {
    const result = await this.client.send('Page.captureScreenshot', { format: 'png', fromSurface: true });
    fs.writeFileSync(path.join(this.artifacts, filename), Buffer.from(result.data, 'base64'));
  }

  async close() {
    this.client.close();
    if (this.browser.exitCode === null) {
      this.browser.kill('SIGTERM');
      await new Promise((resolve) => this.browser.once('exit', resolve));
    }
    fs.rmSync(this.profile, { recursive: true, force: true });
  }
}

function serve(root) {
  const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.wav': 'audio/wav', '.json': 'application/json' };
  const server = http.createServer((request, response) => {
    const relative = decodeURIComponent(new URL(request.url, 'http://fixture').pathname).replace(/^\/+/, '') || 'index.html';
    const target = path.resolve(root, relative);
    if (!target.startsWith(`${path.resolve(root)}${path.sep}`) || !fs.existsSync(target) || !fs.statSync(target).isFile()) {
      response.writeHead(404); response.end('Not found'); return;
    }
    response.writeHead(200, { 'content-type': `${types[path.extname(target)] || 'application/octet-stream'}; charset=utf-8` });
    fs.createReadStream(target).pipe(response);
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve({
      url: `http://127.0.0.1:${server.address().port}/`,
      close: () => new Promise((done) => server.close(done)),
    }));
  });
}

async function inspect(baseUrl, artifacts, width, height) {
  const folder = path.join(artifacts, `${width}x${height}`);
  fs.mkdirSync(folder, { recursive: true });
  const session = await BrowserSession.open(`${baseUrl}?reviewer=smoke-${width}`, folder, width, height);
  try {
    await session.waitFor(`document.querySelectorAll('.sample-card').length === 16`);
    await session.evaluate(`(() => {
      const card=document.querySelector('.sample-card');
      ['identity_1_to_5','delivery_1_to_5','naturalness_1_to_5'].forEach((field)=>card.querySelector('input[data-never]') || card.querySelector('input[name$="-'+field+'"][value="4"]').click());
      card.querySelector('input[name$="-artifact_severity_1_to_5"][value="1"]').click();
      ['spoken_text_matches_expected','requested_mode_is_clear','approve_for_comparison'].forEach((field)=>card.querySelector('input[name$="-'+field+'"][value="true"]').click());
      card.querySelector('textarea').value='Smoke review';
      card.querySelector('textarea').dispatchEvent(new Event('input',{bubbles:true}));
    })()`);
    await session.waitFor(`document.querySelector('.sample-card')?.classList.contains('complete')`);
    await wait(350);
    const observed = await session.evaluate(`(() => {
      const data=window.FISH_S21_BLIND_DATA;
      const controls=[...document.querySelectorAll('button,input,textarea,audio,summary')];
      const unnamed=controls.filter((node)=>!(node.getAttribute('aria-label')||node.textContent||node.closest('label')?.textContent||'').trim()).length;
      const text=document.body.innerText;
      const forbidden=['IndexTTS2','VoxCPM2','Fish Audio','Chatterbox','s2.1-pro-free','short_5s','fish_optimized'];
      return {
        sampleCount:data.samples.length, styleCount:data.styles.length,
        cards:document.querySelectorAll('.sample-card').length,
        nav:document.querySelectorAll('.style-nav').length,
        progress:document.querySelector('#overall-progress').textContent,
        overflow:Math.max(0,document.documentElement.scrollWidth-innerWidth),
        unnamed, leaks:forbidden.filter((value)=>text.includes(value)||JSON.stringify(data).includes(value)),
        stored:Object.keys(localStorage).some((key)=>key.includes(data.round_id)),
        errors:0,
      };
    })()`);
    observed.errors = session.client.events.filter((event) => event.method === 'Runtime.exceptionThrown'
      || (event.method === 'Runtime.consoleAPICalled' && event.params?.type === 'error')).length;
    const assertions = {
      completePackage: observed.sampleCount === 64 && observed.styleCount === 4,
      currentStyle: observed.cards === 16 && observed.nav === 4,
      reviewSaved: /^1 \/ 64/.test(observed.progress) && observed.stored,
      accessibleControls: observed.unnamed === 0,
      noBlindLeak: observed.leaks.length === 0,
      noOverflow: observed.overflow <= 1,
      noRuntimeErrors: observed.errors === 0,
    };
    await session.screenshot('review.png');
    return { viewport: `${width}x${height}`, observed, assertions,
      status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL' };
  } finally {
    await session.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const reviewRoot = required(args, 'review-root');
  const artifacts = required(args, 'artifacts');
  const fixture = await serve(reviewRoot);
  try {
    const results = [];
    for (const [width, height] of [[1280, 900], [390, 844]]) {
      results.push(await inspect(fixture.url, artifacts, width, height));
    }
    const report = { status: results.every((row) => row.status === 'PASS') ? 'PASS' : 'FAIL', results };
    writeJson(path.join(artifacts, 'report.json'), report);
    process.stdout.write(`FISH_S21_REVIEW_SMOKE=${JSON.stringify(report)}\n`);
    if (report.status !== 'PASS') process.exitCode = 1;
  } finally {
    await fixture.close();
  }
}

main().catch((error) => { console.error(error.stack || error); process.exitCode = 2; });
