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
    takeState: {
      currentId: 'take-newest',
      kept: new Set(),
      deleted: new Set(),
    },
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
      const takeFixture = () => produceFixture('takes', control.takeState)
        .chunks.find((item) => item.chunk_id === 'chunk:current-1')?.takes;
      const takeActionPath = url.pathname.match(/^\/api\/produce\/chunks\/([^/]+)\/takes\/(use|keep)$/);
      if (takeActionPath && request.method === 'POST') {
        const action = takeActionPath[2];
        if (action === 'keep') {
          if (receipt.body?.kept) control.takeState.kept.add(receipt.body.take_id);
          else control.takeState.kept.delete(receipt.body?.take_id);
          const take = takeFixture().items.find((item) => item.take_id === receipt.body?.take_id);
          return finish(200, json({ status: 'updated', take, registry_fingerprint: take?.registry_fingerprint }), 'application/json');
        }
        control.takeState.currentId = receipt.body?.take_id || control.takeState.currentId;
        const take = takeFixture().items.find((item) => item.take_id === control.takeState.currentId);
        return finish(200, json({
          status: 'promoted', operation_id: 'fixture_take_promote',
          registry_fingerprint: take?.registry_fingerprint, take,
        }), 'application/json');
      }
      const takeInventoryPath = url.pathname.match(/^\/api\/produce\/chunks\/([^/]+)\/takes$/);
      if (takeInventoryPath && request.method === 'GET') {
          return finish(200, json({ schema_version: 1, chunk_key: 'chunk:current-1', ...takeFixture() }), 'application/json');
      }
      const takeItemPath = url.pathname.match(/^\/api\/produce\/chunks\/([^/]+)\/takes\/([^/]+)(?:\/(delete-impact))?$/);
      if (takeItemPath) {
        const takeId = decodeURIComponent(takeItemPath[2]);
        const action = takeItemPath[3] || null;
        if (action === 'delete-impact' && request.method === 'GET') {
          const take = takeFixture().items.find((item) => item.take_id === takeId);
          const blockers = [];
          if (take?.current) blockers.push({ code: 'current_take', message: 'Current Take cannot be deleted.' });
          if (take?.kept) blockers.push({ code: 'kept_take', message: 'Kept Take cannot be deleted.' });
          if (takeId === 'take-older') blockers.push({ code: 'rendition_parent', message: 'Delete child renditions before deleting their source Take.' });
          return finish(200, json({
            schema_version: 1, chunk_key: 'chunk:current-1', take_id: takeId,
            size_bytes: take?.audio?.size_bytes || 0, blockers,
            safe_to_delete: blockers.length === 0,
            impact_fingerprint: `impact-${takeId}`.padEnd(64, 'd').slice(0, 64),
          }), 'application/json');
        }
        if (request.method === 'DELETE') {
          control.takeState.deleted.add(takeId);
          return finish(200, json({
            status: 'deleted', operation_id: `fixture_delete_${takeId}`,
            registry_fingerprint: 'fixture-take-registry'.padEnd(64, 'b').slice(0, 64),
            deleted_take_ids: [takeId],
          }), 'application/json');
        }
      }
      if (url.pathname === '/api/produce/takes/cleanup-impact' && request.method === 'POST') {
        const candidates = takeFixture().items.filter((take) => (
          !take.current && !take.kept && take.take_id === 'take-incompatible'
        ));
        return finish(200, json({
          schema_version: 1,
          candidate_count: candidates.length,
          candidates,
          reclaimable_bytes: candidates.reduce((sum, take) => sum + Number(take.audio?.size_bytes || 0), 0),
          impact_fingerprint: 'fixture-cleanup-impact'.padEnd(64, 'e').slice(0, 64),
        }), 'application/json');
      }
      if (url.pathname === '/api/produce/takes/cleanup' && request.method === 'POST') {
        control.takeState.deleted.add('take-incompatible');
        return finish(200, json({
          status: 'cleaned', operation_id: 'fixture_take_cleanup',
          registry_fingerprint: 'fixture-take-registry'.padEnd(64, 'b').slice(0, 64),
          deleted_take_ids: ['take-incompatible'],
        }), 'application/json');
      }
      if (url.pathname === '/api/produce/takes/undo' && request.method === 'POST') {
        return finish(200, json({ status: 'undone', restored_take_ids: [] }), 'application/json');
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
      } : url.pathname === '/api/produce' ? produceFixture(
        control.mode.replace('produce-', ''),
        control.takeState,
      )
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
