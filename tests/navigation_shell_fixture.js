'use strict';

const fs = require('fs');
const http = require('http');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const STATIC = path.join(ROOT, 'app', 'static');

function assertion(id, pass, expected, observed) {
  return { id, pass: Boolean(pass), expected, observed };
}

function fixtureModule(name, options = {}) {
  if (options.invalid) return 'this is not valid JavaScript {';
  return `
const PAGE = ${JSON.stringify(name)};
export async function mount({ root, route, shell }) {
  const receipt = globalThis.__shellFixture ||= { events: [] };
  receipt.events.push({ type: 'mount-start', path: route.path });
  if (receipt.delayMountFor === route.path) {
    await new Promise((resolve) => { receipt.releaseMount = resolve; });
  }
  if (${Boolean(options.mountFailure)}) throw new Error('fixture mount rejected');
  const owner = document.createElement('article');
  owner.dataset.routeOwner = route.path;
  owner.dataset.page = route.path;
  const heading = document.createElement('h1');
  heading.id = 'fixture-heading-' + route.path.replaceAll('/', '-');
  heading.dataset.pageHeading = '';
  heading.textContent = route.heading;
  const body = document.createElement('p');
  body.textContent = 'Successful fixture module for ' + PAGE;
  owner.append(heading, body);
  root.replaceChildren(owner);
  if (route.context.project) {
    shell.header.set({
      projectTitle: 'Project Meridian',
      save: { state: 'saved', label: 'Saved' },
      status: { tone: 'success', label: 'Ready' },
      primaryAction: { label: 'Continue' },
      stages: { script: 'complete', cast: route.destination === 'cast' ? 'current' : 'complete',
        produce: route.destination === 'produce' ? 'current' : 'future', export: 'future' },
    });
    const content = document.createElement('p');
    content.textContent = 'Inspector context for Project Meridian';
    shell.inspector.set({ state: 'collapsed', title: 'Project inspector', content });
  }
  shell.player.set({ state: 'active', title: 'Fixture chapter', subtitle: 'Narrator · Take 1' });
  if (route.path === 'projects') {
    const layer = document.createElement('aside');
    layer.dataset.fixtureOverlay = '';
    layer.textContent = 'Transient fixture overlay';
    shell.overlay.open(layer);
  }
  receipt.events.push({ type: 'mount-complete', path: route.path });
  return async () => {
    receipt.events.push({ type: 'cleanup-start', path: route.path });
    if (receipt.failCleanupFor === route.path) {
      receipt.events.push({ type: 'cleanup-failure', path: route.path });
      throw new Error('fixture cleanup rejected');
    }
    receipt.events.push({ type: 'cleanup-complete', path: route.path });
  };
}
`;
}

async function fixtureServer() {
  const control = { delayedHead: null, failPath: null, pendingHeads: new Map() };
  const receipts = [];
  const waiters = new Set();
  const notify = () => {
    for (const waiter of [...waiters]) {
      const value = receipts.find(waiter.predicate);
      if (!value) continue;
      clearTimeout(waiter.timer);
      waiters.delete(waiter);
      waiter.resolve(value);
    }
  };
  const waitForReceipt = (predicate, timeout = 3000) => {
    const found = receipts.find(predicate);
    if (found) return Promise.resolve(found);
    return new Promise((resolve, reject) => {
      const waiter = { predicate, resolve, timer: null };
      waiter.timer = setTimeout(() => {
        waiters.delete(waiter);
        reject(new Error('Timed out waiting for fixture request state.'));
      }, timeout);
      waiters.add(waiter);
    });
  };
  const modules = new Map([
    ['/static/pages/projects.js', fixtureModule('projects')],
    ['/static/pages/cast.js', fixtureModule('cast')],
    ['/static/pages/produce.js', fixtureModule('produce')],
    ['/static/pages/settings.js', fixtureModule('settings')],
    ['/static/specialists/audio_preparer.js', fixtureModule('audio-preparer', { invalid: true })],
    ['/static/specialists/dataset_builder.js', fixtureModule('dataset-builder', { mountFailure: true })],
  ]);
  const mime = (filename) => filename.endsWith('.html') ? 'text/html; charset=utf-8'
    : filename.endsWith('.css') ? 'text/css; charset=utf-8'
      : 'text/javascript; charset=utf-8';
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://fixture.invalid');
    const receipt = { method: request.method, path: url.pathname, aborted: false, completed: false };
    receipts.push(receipt);
    notify();
    request.once('aborted', () => { receipt.aborted = true; notify(); });
    response.once('close', () => {
      control.pendingHeads.delete(url.pathname);
      if (!receipt.completed) receipt.aborted = true;
      notify();
    });
    const finish = (status, body = '', contentType = 'text/plain; charset=utf-8') => {
      if (response.destroyed) return;
      response.writeHead(status, { 'content-type': contentType, 'cache-control': 'no-store' });
      response.end(request.method === 'HEAD' ? '' : body);
      receipt.completed = true;
      notify();
    };
    const send = () => {
      if (url.pathname === control.failPath) return finish(404, 'forced dependency failure');
      if (url.pathname === '/') return finish(200, fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8'), mime('.html'));
      if (url.pathname === '/static/specialists/voice_designer.js') return finish(404, 'fixture module unavailable');
      if (modules.has(url.pathname)) return finish(200, modules.get(url.pathname), mime('.js'));
      if (!url.pathname.startsWith('/static/')) return finish(404, 'not found');
      const relative = url.pathname.slice('/static/'.length);
      const filename = path.resolve(STATIC, relative);
      if (!filename.startsWith(`${STATIC}${path.sep}`)
        || !fs.existsSync(filename) || !fs.statSync(filename).isFile()) return finish(404, 'not found');
      return finish(200, fs.readFileSync(filename), mime(filename));
    };
    if (request.method === 'HEAD' && control.delayedHead === url.pathname) {
      control.pendingHeads.set(url.pathname, send);
    } else send();
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const { port } = server.address();
  return {
    url: `http://127.0.0.1:${port}/`, control, receipts, waitForReceipt,
    releaseHead: (pathname) => control.pendingHeads.get(pathname)?.(),
    close: () => new Promise((resolve) => {
      server.close(resolve);
      server.closeAllConnections?.();
    }),
  };
}

async function settle(session) {
  await session.evaluate(`new Promise((resolve) => requestAnimationFrame(
    () => requestAnimationFrame(() => resolve(true))
  ))`);
}

module.exports = { ROOT, STATIC, assertion, fixtureServer, settle };
