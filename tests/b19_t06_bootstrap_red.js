'use strict';

const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const CHROME_STARTUP_ATTEMPTS = 2;
const CHROME_STARTUP_RETRY_DELAY_MS = 2000;
const CHROME_STDERR_LIMIT = 16384;
const BOOTSTRAP_PATTERNS = [
  { urlPattern: '*canonical_interface.js*', resourceType: 'Script' },
  { urlPattern: '*app_shell.js*', resourceType: 'Script' },
];

function argsFrom(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith('--')) continue;
    result[key.slice(2)] = argv[index + 1] && !argv[index + 1].startsWith('--')
      ? argv[++index]
      : true;
  }
  return result;
}

function required(args, name) {
  const value = args[name];
  if (!value || value === true) throw new Error(`--${name} is required`);
  return String(value);
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
  const address = server.address();
  const port = typeof address === 'object' && address ? address.port : 0;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function waitForHttp(url, timeoutMs = 15000, terminalState = () => null) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    const terminal = terminalState();
    if (terminal) throw terminal;
    try {
      const response = await fetch(url);
      if (response.ok) return;
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await delay(50);
  }
  throw new Error(`Timed out waiting for ${url}: ${lastError}`);
}

async function terminateBrowser(browser) {
  if (!browser || browser.exitCode !== null || browser.signalCode !== null) return;
  const exited = new Promise((resolve) => browser.once('exit', resolve));
  browser.kill('SIGTERM');
  await Promise.race([exited, delay(5000)]);
  if (browser.exitCode === null && browser.signalCode === null) {
    const killed = new Promise((resolve) => browser.once('exit', resolve));
    browser.kill('SIGKILL');
    await Promise.race([killed, delay(2000)]);
  }
}

class CdpClient {
  constructor(webSocketUrl) {
    this.socket = new WebSocket(webSocketUrl);
    this.nextId = 1;
    this.pending = new Map();
    this.events = [];
    this.opened = new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, { once: true });
      this.socket.addEventListener('error', reject, { once: true });
    });
    this.socket.addEventListener('message', (event) => this.receive(event));
  }

  receive(event) {
    const message = JSON.parse(event.data);
    if (!message.id) {
      this.events.push(message);
      return;
    }
    const pending = this.pending.get(message.id);
    if (!pending) return;
    this.pending.delete(message.id);
    if (message.error) pending.reject(new Error(JSON.stringify(message.error)));
    else pending.resolve(message.result || {});
  }

  async send(method, params = {}) {
    await this.opened;
    const id = this.nextId++;
    const result = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }

  async event(method, predicate = () => true, timeoutMs = 15000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const index = this.events.findIndex(
        (item) => item.method === method && predicate(item.params || {}),
      );
      if (index >= 0) return this.events.splice(index, 1)[0].params || {};
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    throw new Error(`Timed out waiting for CDP event ${method}`);
  }

  close() {
    this.socket.close();
  }
}

class BrowserSession {
  static async open({ url, artifacts, width = 1536, height = 1024, gateBootstrap = false }) {
    if (!fs.existsSync(CHROME)) throw new Error(`Chrome not found: ${CHROME}`);
    fs.mkdirSync(artifacts, { recursive: true });
    const startupAttempts = [];
    for (let attempt = 1; attempt <= CHROME_STARTUP_ATTEMPTS; attempt += 1) {
      const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'alexandria-b19-t06-chrome-'));
      const port = await freePort();
      let launchError = null;
      let browserStderr = '';
      const browser = spawn(CHROME, [
        '--headless=new', '--disable-gpu', '--no-first-run',
        '--no-default-browser-check', '--disable-background-networking',
        '--disable-component-update', '--disable-default-apps', '--disable-sync',
        '--metrics-recording-only', '--mute-audio', '--remote-allow-origins=*',
        `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, 'about:blank',
      ], { stdio: ['ignore', 'ignore', 'pipe'] });
      browser.once('error', (error) => { launchError = error; });
      browser.stderr?.on('data', (chunk) => {
        browserStderr = `${browserStderr}${chunk}`.slice(-CHROME_STDERR_LIMIT);
      });
      try {
        await waitForHttp(`http://127.0.0.1:${port}/json/version`, 15000, () => {
          if (launchError) return new Error(`Chrome spawn failed: ${launchError.message}`);
          if (browser.exitCode !== null || browser.signalCode !== null) {
            return new Error(`Chrome exited before CDP became ready: exit=${browser.exitCode} signal=${browser.signalCode}`);
          }
          return null;
        });
        const response = await fetch(
          `http://127.0.0.1:${port}/json/new?${encodeURIComponent('about:blank')}`,
          { method: 'PUT' },
        );
        if (!response.ok) throw new Error(`Chrome target failed: ${response.status}`);
        const target = await response.json();
        const session = new BrowserSession({
          browser, profile, port, artifacts,
          client: new CdpClient(target.webSocketDebuggerUrl),
          startupAttempts,
          browserStderr: () => browserStderr,
        });
        await session.client.send('Page.enable');
        await session.client.send('Runtime.enable');
        await session.client.send('Network.enable');
        await session.client.send('Emulation.setDeviceMetricsOverride', {
          width, height, deviceScaleFactor: 1, mobile: width <= 500,
          screenWidth: width, screenHeight: height,
        });
        if (gateBootstrap) await session.client.send('Fetch.enable', { patterns: BOOTSTRAP_PATTERNS });
        await session.client.send('Page.navigate', { url });
        startupAttempts.push({
          attempt, status: 'ready', chromePid: browser.pid, debugPort: port,
          profileRemoved: false,
        });
        return session;
      } catch (error) {
        await terminateBrowser(browser);
        fs.rmSync(profile, { recursive: true, force: true });
        startupAttempts.push({
          attempt,
          status: 'failed',
          chromePid: browser.pid || null,
          debugPort: port,
          exitCode: browser.exitCode,
          signalCode: browser.signalCode,
          error: String(error?.message || error),
          stderr: browserStderr,
          profileRemoved: !fs.existsSync(profile),
        });
        writeJson(path.join(artifacts, 'chrome-startup-attempts.json'), { attempts: startupAttempts });
        if (attempt < CHROME_STARTUP_ATTEMPTS) {
          await delay(CHROME_STARTUP_RETRY_DELAY_MS);
          continue;
        }
        throw new Error(`Chrome startup failed after ${CHROME_STARTUP_ATTEMPTS} attempts: ${JSON.stringify(startupAttempts)}`);
      }
    }
    throw new Error('Chrome startup loop ended without a session.');
  }

  constructor({ browser, profile, port, artifacts, client, startupAttempts = [], browserStderr = () => '' }) {
    Object.assign(this, { browser, profile, port, artifacts, client, startupAttempts, browserStderr });
  }

  async evaluate(expression) {
    const result = await this.client.send('Runtime.evaluate', {
      expression, returnByValue: true, awaitPromise: true,
    });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result && result.result.value;
  }

  async waitFor(expression, timeoutMs = 15000) {
    const deadline = Date.now() + timeoutMs;
    let value = null;
    while (Date.now() < deadline) {
      value = await this.evaluate(expression);
      if (value) return value;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    throw new Error(`Condition did not become true: ${expression}; last=${JSON.stringify(value)}`);
  }

  async screenshot(filename) {
    const result = await this.client.send('Page.captureScreenshot', {
      format: 'png', fromSurface: true, captureBeyondViewport: false,
    });
    fs.writeFileSync(path.join(this.artifacts, filename), Buffer.from(result.data, 'base64'));
  }

  async close() {
    this.client.close();
    await terminateBrowser(this.browser);
    fs.rmSync(this.profile, { recursive: true, force: true });
    writeJson(path.join(this.artifacts, 'cleanup.json'), {
      chromePid: this.browser.pid, debugPort: this.port,
      browserExited: this.browser.exitCode !== null || this.browser.signalCode !== null,
      profileRemoved: !fs.existsSync(this.profile),
      startupRetryCount: this.startupAttempts.filter((item) => item.status === 'failed').length,
      startupAttempts: this.startupAttempts.map((item) => (
        item.status === 'ready' ? { ...item, profileRemoved: !fs.existsSync(this.profile) } : item
      )),
      chromeStderr: this.browserStderr(),
    });
  }
}

const FIRST_PAINT_SNAPSHOT = `(() => {
  const prohibited = ['setup-tab','characters-tab','editor-tab','audio-tab','speaker-management-tab'];
  const nodes = prohibited.map((id) => document.getElementById(id)).filter(Boolean);
  const visible = nodes.filter((node) => {
    const style = getComputedStyle(node); const rect = node.getBoundingClientRect();
    return !node.hidden && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  });
  return { readyState: document.readyState, legacyNodeIds: nodes.map((node) => node.id),
    visibleLegacyIds: visible.map((node) => node.id),
    destinationRoots: document.querySelectorAll('#canonical-destination-root,[data-canonical-destination-root]').length,
    bodyText: document.body?.innerText?.trim().slice(0, 500) || '' };
})()`;

async function runBootstrap(url, artifacts) {
  const session = await BrowserSession.open({ url, artifacts, gateBootstrap: true });
  try {
    const paused = await session.client.event('Fetch.requestPaused', ({ request }) => (
      /\/(canonical_interface|app_shell)\.js(?:\?|$)/.test(request.url)
    ));
    const before = await session.evaluate(FIRST_PAINT_SNAPSHOT);
    await session.screenshot('before-bootstrap.png');
    await session.client.send('Fetch.failRequest', { requestId: paused.requestId, errorReason: 'Aborted' });
    await session.client.send('Fetch.disable');
    await session.client.event('Page.loadEventFired');
    const after = await session.evaluate(`(() => ({
      readyState: document.readyState,
      errors: [...document.querySelectorAll('[data-bootstrap-error],[data-shell-error],.canonical-bootstrap-error')]
        .filter((node) => !node.hidden && getComputedStyle(node).display !== 'none')
        .map((node) => node.textContent.trim()),
      bodyText: document.body?.innerText?.trim().slice(0, 500) || ''
    }))()`);
    await session.screenshot('failed-bootstrap.png');
    const assertions = [
      { id: 'legacy-dom-absent-before-bootstrap', pass: before.legacyNodeIds.length === 0, expected: [], observed: before.legacyNodeIds },
      { id: 'one-destination-root-before-bootstrap', pass: before.destinationRoots === 1, expected: 1, observed: before.destinationRoots },
      { id: 'canonical-error-shell-on-bootstrap-failure', pass: after.errors.length === 1, expected: 1, observed: after.errors },
    ];
    return { status: assertions.every((item) => item.pass) ? 'PASS' : 'RED', gatedRequest: paused.request.url, before, after, assertions };
  } finally {
    await session.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required(args, 'artifacts'));
  const report = await runBootstrap(required(args, 'url'), artifacts);
  writeJson(path.join(artifacts, 'report.json'), report);
  process.stdout.write(`B19_T06_BOOTSTRAP=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.stack || error);
    process.exitCode = 2;
  });
}

module.exports = { BrowserSession, argsFrom, required, writeJson };
