'use strict';

const fs = require('fs');
const http = require('http');
const path = require('path');
const { exportFixture, produceFixture } = require('./produce_export_fixture_data.js');

const ROOT = path.resolve(__dirname, '..');
const STATIC = path.join(ROOT, 'app', 'static');
const json = (value) => JSON.stringify(value);

function fixtureServer() {
  const control = {
    mode: 'produce-mixed', requests: [], pending: [], aborted: 0,
    producePlanBehavior: 'normal',
  };
  const projectModule = `export async function mount({root,route}){const n=document.createElement('article');n.dataset.routeOwner='projects';const h=document.createElement('h1');h.dataset.pageHeading='';h.textContent='Project Home';n.append(h);root.replaceChildren(n);}`;
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, 'http://fixture.invalid');
    const receipt = { method: request.method, path: url.pathname, body: null, completed: false };
    control.requests.push(receipt);
    request.once('aborted', () => { if (!receipt.completed) control.aborted += 1; });
    response.once('close', () => { if (!receipt.completed) control.aborted += 1; });
    const finish = (status, body = '', type = 'text/plain; charset=utf-8') => {
      if (response.destroyed || response.writableEnded) return;
      response.writeHead(status, { 'content-type': type, 'cache-control': 'no-store' });
      response.end(request.method === 'HEAD' ? '' : body);
      receipt.completed = true;
    };
    if (url.pathname === '/__fixture/mode') {
      control.mode = url.searchParams.get('value') || control.mode;
      return finish(204);
    }
    if (url.pathname.startsWith('/api/')) {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      if (chunks.length) receipt.body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (control.mode.endsWith('-error')) {
        return finish(500, json({ detail: 'Fixture read failed.' }), 'application/json');
      }
      if (url.pathname === '/api/produce/plan' && control.producePlanBehavior === 'error') {
        return finish(500, json({ detail: 'Fixture audio plan failed.' }), 'application/json');
      }
      const delayed = (control.mode.endsWith('-loading') && request.method === 'GET')
        || (url.pathname === '/api/produce/plan' && control.producePlanBehavior === 'pending');
      const payload = url.pathname === '/api/projects' ? {
        schema_version: 1,
        catalog_fingerprint: 'fixture-catalog',
        current_project_id: 'fixture-project',
        last_selected_project_id: 'fixture-project',
        projects: [{
          id: 'fixture-project', name: 'The Meridian Archive', source_title: 'The Meridian Archive',
          current: true, selected: true,
          current_recommended_stage: control.mode.startsWith('export-') ? 'export' : 'produce',
          stage_summary: control.mode.startsWith('export-')
            ? 'Review publication details.' : 'Review production audio.',
          blocker_count: 0,
        }],
      } : url.pathname === '/api/produce' ? produceFixture(control.mode.replace('produce-', ''))
        : url.pathname === '/api/export' ? exportFixture(control.mode.replace('export-', ''))
          : url.pathname === '/api/produce/plan' ? {
            mode: receipt.body?.mode, indices: [0],
            plan_fingerprint: `fixture-${receipt.body?.mode}`,
            chunks_fingerprint: 'fixture-chunks', blockers: [], safe_to_execute: true,
          } : url.pathname === '/api/export/plan' ? { ...exportFixture('ready').plan, ...receipt.body }
            : { status: url.pathname.endsWith('/cancel') ? 'cancelling' : 'accepted' };
      const send = () => finish(200, json(payload), 'application/json');
      if (delayed) control.pending.push(send); else send();
      return;
    }
    if (url.pathname.startsWith('/fixture-audio/')) return finish(204);
    if (url.pathname === '/static/pages/projects.js') {
      return finish(200, projectModule, 'text/javascript; charset=utf-8');
    }
    const relative = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\/static\//, '');
    const filename = path.resolve(STATIC, relative);
    if (!filename.startsWith(`${STATIC}${path.sep}`)
      || !fs.existsSync(filename) || !fs.statSync(filename).isFile()) return finish(404, 'Not found');
    const type = path.extname(filename) === '.html' ? 'text/html'
      : path.extname(filename) === '.css' ? 'text/css' : 'text/javascript';
    return finish(200, fs.readFileSync(filename), `${type}; charset=utf-8`);
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve({
      server, control, url: `http://127.0.0.1:${server.address().port}/`,
      release: () => control.pending.splice(0).forEach((send) => send()),
      close: () => new Promise((done) => {
        server.close(done);
        server.closeAllConnections?.();
      }),
    }));
  });
}

module.exports = { fixtureServer };
