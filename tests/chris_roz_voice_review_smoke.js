'use strict';

const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const ROOT_URL = 'http://127.0.0.1:8877';
const ARTIFACTS = '/private/tmp/alexandria-voice-review-qa';
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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
    try { if ((await fetch(url)).ok) return; } catch (_) { /* retry */ }
    await wait(100);
  }
  throw new Error(`Timed out waiting for ${url}`);
}

class Cdp {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.pending = new Map();
    this.events = [];
    this.next = 1;
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
    const id = this.next++;
    const result = new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }
  close() { this.socket.close(); }
}

async function openBrowser(url, width, height) {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'alexandria-voice-review-'));
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
  const client = new Cdp(target.webSocketDebuggerUrl);
  await client.send('Page.enable');
  await client.send('Runtime.enable');
  await client.send('Emulation.setDeviceMetricsOverride', {
    width, height, screenWidth: width, screenHeight: height, deviceScaleFactor: 1, mobile: width <= 500,
  });
  await client.send('Page.navigate', { url });
  return { browser, profile, client };
}

async function evaluate(client, expression) {
  const result = await client.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result?.value;
}

async function waitFor(client, expression) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (await evaluate(client, expression)) return;
    await wait(100);
  }
  throw new Error(`Condition did not become true: ${expression}`);
}

async function closeBrowser(session) {
  session.client.close();
  if (session.browser.exitCode === null) {
    session.browser.kill('SIGTERM');
    await new Promise((resolve) => session.browser.once('exit', resolve));
  }
  fs.rmSync(session.profile, { recursive: true, force: true });
}

async function screenshot(client, filename) {
  const result = await client.send('Page.captureScreenshot', { format: 'png', fromSurface: true });
  fs.mkdirSync(path.dirname(filename), { recursive: true });
  fs.writeFileSync(filename, Buffer.from(result.data, 'base64'));
}

async function inspect(kind, url, width, height) {
  const session = await openBrowser(url, width, height);
  try {
    const dataGlobal = kind === 'reference' ? 'CHRIS_ROZ_REFERENCE_REVIEW' : 'FISH_ROUTER_RETEST';
    const cardSelector = kind === 'reference' ? '.card' : '.candidate';
    await waitFor(session.client, `Boolean(window.${dataGlobal}) && document.querySelectorAll('${cardSelector}').length > 0`);
    const interaction = await evaluate(session.client, `(() => {
      const card=document.querySelector('${cardSelector}');
      for(const select of card.querySelectorAll('select')){select.value='4';select.dispatchEvent(new Event('change',{bubbles:true}));}
      const box=card.querySelector('input[type="checkbox"]');if(box&&!box.checked)box.click();
      const notes=card.querySelector('textarea');notes.value='Smoke review';notes.dispatchEvent(new Event('input',{bubbles:true}));
      return true;
    })()`);
    await wait(300);
    const observed = await evaluate(session.client, `(async()=>{
      const data=window.${dataGlobal};
      const cards=[...document.querySelectorAll('${cardSelector}')];
      const audio=[...document.querySelectorAll('audio')];
      const statuses=await Promise.all(audio.slice(0,6).map(async node=>{try{return (await fetch(node.src)).status}catch(_){return 0}}));
      const body=document.body.innerText;
      const forbidden=${JSON.stringify(kind === 'reference'
        ? ['original_sin','damaged_goods','trial_time_machine','vanguard','jabari_countdown','dread_of_night','target_similarity','identity_margin','segment_index']
        : ['full_alexandria_tag','rich_tag','prompt_mode','reference_model_id','reference_fingerprint','repeat-1','repeat-2'])};
      return {
        title:document.title,
        cards:cards.length,
        audio:audio.length,
        selects:document.querySelectorAll('select').length,
        progress:document.querySelector('#progress')?.textContent||'',
        exportEnabled:!document.querySelector('#export')?.disabled,
        statuses,
        overflow:Math.max(0,document.documentElement.scrollWidth-innerWidth),
        leaks:forbidden.filter(value=>body.includes(value)||JSON.stringify(data).includes(value)),
        stored:Object.keys(localStorage).some(key=>key.includes(data.round_id)),
        meaningfulText:body.length>500,
      };
    })()`);
    observed.runtimeErrors = session.client.events.filter((event) =>
      event.method === 'Runtime.exceptionThrown'
      || (event.method === 'Runtime.consoleAPICalled' && event.params?.type === 'error')).length;
    const expectedCards = kind === 'reference' ? 30 : 24;
    const assertions = {
      pageIdentity: observed.title.toLowerCase().includes(kind === 'reference' ? 'chris and roz' : 'fish'),
      notBlank: observed.meaningfulText,
      correctCandidateCount: observed.cards === expectedCards,
      completeControls: observed.selects === expectedCards * 5,
      audioReachable: observed.audio >= expectedCards && observed.statuses.every((status) => status === 200),
      interactionPersisted: observed.progress.startsWith(`1 of ${expectedCards}`) && observed.stored,
      exportAvailable: observed.exportEnabled,
      noBlindLeak: observed.leaks.length === 0,
      noHorizontalOverflow: observed.overflow <= 1,
      noRuntimeErrors: observed.runtimeErrors === 0,
      interactionRan: interaction === true,
    };
    const folder = path.join(ARTIFACTS, kind, `${width}x${height}`);
    await screenshot(session.client, path.join(folder, 'review.png'));
    return { kind, viewport: `${width}x${height}`, observed, assertions, status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL' };
  } finally {
    await closeBrowser(session);
  }
}

async function main() {
  fs.rmSync(ARTIFACTS, { recursive: true, force: true });
  const cases = [
    ['reference', `${ROOT_URL}/alexandria-chris-roz-final-reference-review-v1/review/`],
    ['fish', `${ROOT_URL}/alexandria-fish-preferred-router-retest-v1/review/`],
  ];
  const results = [];
  for (const [kind, url] of cases) {
    for (const [width, height] of [[1280, 900], [390, 844]]) {
      results.push(await inspect(kind, url, width, height));
    }
  }
  const report = { status: results.every((row) => row.status === 'PASS') ? 'PASS' : 'FAIL', results };
  fs.mkdirSync(ARTIFACTS, { recursive: true });
  fs.writeFileSync(path.join(ARTIFACTS, 'report.json'), `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => { console.error(error.stack || error); process.exitCode = 2; });
