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
      pinnedId: null,
      pauseAfterMs: 350,
      renditions: [],
      nextRendition: 1,
      kept: new Set(),
      deleted: new Set(),
      undo: new Map(),
    },
    mastering: {
      running: false,
      cancel_requested: false,
      status: 'idle',
      chunk_id: null,
      source_take_id: null,
      completed_count: 0,
      total_count: 7,
      progress_message: null,
      result: null,
      background_job_id: null,
    },
  };
  control.takeState.masteringProcess = control.mastering;
  const snapshotTakeState = () => ({
    currentId: control.takeState.currentId,
    pinnedId: control.takeState.pinnedId,
    pauseAfterMs: control.takeState.pauseAfterMs,
    renditions: JSON.parse(JSON.stringify(control.takeState.renditions)),
    nextRendition: control.takeState.nextRendition,
    kept: new Set(control.takeState.kept),
    deleted: new Set(control.takeState.deleted),
  });
  const restoreTakeState = (value) => {
    control.takeState.currentId = value.currentId;
    control.takeState.pinnedId = value.pinnedId;
    control.takeState.pauseAfterMs = value.pauseAfterMs;
    control.takeState.renditions = JSON.parse(JSON.stringify(value.renditions));
    control.takeState.nextRendition = value.nextRendition;
    control.takeState.kept = new Set(value.kept);
    control.takeState.deleted = new Set(value.deleted);
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
      const masteringPath = url.pathname.match(
        /^\/api\/produce\/chunks\/([^/]+)\/mastering\/(plan|apply)$/,
      );
      if (masteringPath && request.method === 'POST') {
        const action = masteringPath[2];
        const normalizedSettings = {
          ...receipt.body?.settings,
          settings_fingerprint: 'fixture-mastering-settings'.padEnd(64, '5').slice(0, 64),
        };
        const plan = {
          schema_version: 1,
          operation: 'publication_mastering',
          take_id: receipt.body?.take_id,
          source_sha256: receipt.body?.source_sha256,
          record_fingerprint: receipt.body?.record_fingerprint,
          registry_fingerprint: receipt.body?.registry_fingerprint,
          source_order_fingerprint: receipt.body?.source_order_fingerprint,
          settings: normalizedSettings,
          settings_fingerprint: normalizedSettings.settings_fingerprint,
          provenance: {
            schema_version: 1,
            c2pa: { present: false, structural_status: 'not_present', signer_trust: 'not_evaluated' },
            watermark: { present: false, structural_status: 'not_present', ownership_trust: 'not_evaluated' },
            voice_authorization: 'not_evaluated',
            human_approval: 'pending_final_listen',
          },
          dependency_fingerprint: 'fixture-mastering-dependency'.padEnd(64, '6').slice(0, 64),
          plan_fingerprint: 'fixture-mastering-plan'.padEnd(64, '7').slice(0, 64),
          safe_to_execute: true,
          rejected_effects: ['pitch_shift', 'chorus', 'dramatic_reverb', 'voice_transformation', 'arbitrary_multitrack'],
        };
        if (action === 'plan') return finish(200, json(plan), 'application/json');
        const operationId = `fixture_mastering_${control.requests.length}`;
        control.takeState.undo.set(operationId, snapshotTakeState());
        const source = takeFixture().items.find((item) => item.take_id === receipt.body?.take_id);
        const takeId = `rendition-mastered-${control.takeState.nextRendition}`;
        control.takeState.nextRendition += 1;
        const processing = {
          schema_version: 1,
          operation: 'publication_mastering',
          settings: normalizedSettings,
          settings_fingerprint: normalizedSettings.settings_fingerprint,
          source_sha256: source?.audio?.sha256,
          output_sha256: `${takeId.padEnd(64, 'd')}`.slice(0, 64),
          metrics_before: { estimated_loudness_dbfs: -24.2, estimated_true_peak_dbfs: -3.4 },
          metrics_after: { estimated_loudness_dbfs: -20.0, estimated_true_peak_dbfs: -1.0, clipped_sample_count: 0 },
          safeguards: {
            duration_preserved: true, no_clipped_samples: true,
            peak_ceiling_passed: true, non_silent: true,
            normalization_target_passed: true,
          },
          provenance: plan.provenance,
          mastering_plan_fingerprint: plan.plan_fingerprint,
          mastering_dependency_fingerprint: plan.dependency_fingerprint,
          mastering_job_id: 'work_mastering_fixture',
          publication_state: 'published_child_rendition',
        };
        control.takeState.renditions.push({
          takeId,
          sourceTakeId: source?.take_id || receipt.body?.take_id,
          rootTakeId: source?.root_take_id || source?.take_id || receipt.body?.take_id,
          operation: 'publication_mastering',
          settings: normalizedSettings,
          processing,
          durationMs: source?.audio?.duration_ms || 8200,
        });
        control.takeState.currentId = takeId;
        control.takeState.pinnedId = null;
        control.mastering.running = false;
        control.mastering.status = 'succeeded';
        control.mastering.chunk_id = 'chunk:current-1';
        control.mastering.source_take_id = source?.take_id || receipt.body?.take_id;
        control.mastering.completed_count = 7;
        control.mastering.total_count = 7;
        control.mastering.progress_message = 'Mastered child rendition published.';
        control.mastering.background_job_id = 'work_mastering_fixture';
        control.mastering.result = {
          operation_id: operationId,
          registry_fingerprint: 'fixture-take-registry'.padEnd(64, 'b').slice(0, 64),
          take_id: takeId,
          source_take_id: source?.take_id || receipt.body?.take_id,
        };
        return finish(200, json({
          status: 'accepted',
          job: { job_id: 'work_mastering_fixture', domain: 'mastering', state: 'queued' },
          plan,
        }), 'application/json');
      }
      const finalListenPath = url.pathname.match(
        /^\/api\/produce\/chunks\/([^/]+)\/final-listen\/(pin|pause|rendition)$/,
      );
      if (finalListenPath && request.method === 'POST') {
        const action = finalListenPath[2];
        const operationId = `fixture_final_listen_${action}_${control.requests.length}`;
        control.takeState.undo.set(operationId, snapshotTakeState());
        if (action === 'pin') {
          control.takeState.pinnedId = receipt.body?.pinned
            ? receipt.body?.take_id || control.takeState.currentId : null;
          if (control.takeState.pinnedId) control.takeState.kept.add(control.takeState.pinnedId);
          const take = takeFixture().items.find((item) => item.take_id === control.takeState.currentId);
          return finish(200, json({
            status: receipt.body?.pinned ? 'pinned' : 'unpinned',
            operation_id: operationId,
            registry_fingerprint: take?.registry_fingerprint,
            source_order_fingerprint: 's'.repeat(64),
            take,
            produce: produceFixture('takes', control.takeState),
          }), 'application/json');
        }
        if (action === 'pause') {
          control.takeState.pauseAfterMs = receipt.body?.pause_after_ms == null
            ? 350 : Number(receipt.body.pause_after_ms);
          return finish(200, json({
            status: 'updated',
            operation_id: operationId,
            registry_fingerprint: 'fixture-take-registry'.padEnd(64, 'b').slice(0, 64),
            source_order_fingerprint: 's'.repeat(64),
            produce: produceFixture('takes', control.takeState),
          }), 'application/json');
        }
        const source = takeFixture().items.find((item) => item.take_id === receipt.body?.take_id);
        const takeId = `rendition-final-${control.takeState.nextRendition}`;
        control.takeState.nextRendition += 1;
        control.takeState.renditions.push({
          takeId,
          sourceTakeId: source?.take_id || receipt.body?.take_id,
          rootTakeId: source?.root_take_id || source?.take_id || receipt.body?.take_id,
          operation: receipt.body?.operation === 'trim_edges'
            ? 'final_listen_trim_edges' : 'final_listen_split_with_pause',
          settings: receipt.body?.operation === 'trim_edges' ? {
            trim_start_ms: receipt.body?.trim_start_ms,
            trim_end_ms: receipt.body?.trim_end_ms,
          } : {
            split_at_ms: receipt.body?.split_at_ms,
            pause_ms: receipt.body?.pause_ms,
          },
          durationMs: receipt.body?.operation === 'trim_edges' ? 7900 : 8500,
        });
        control.takeState.currentId = takeId;
        control.takeState.pinnedId = null;
        const take = takeFixture().items.find((item) => item.take_id === takeId);
        return finish(200, json({
          status: 'registered',
          operation_id: operationId,
          registry_fingerprint: take?.registry_fingerprint,
          source_order_fingerprint: 's'.repeat(64),
          take,
          processing: take?.processing,
          produce: produceFixture('takes', control.takeState),
        }), 'application/json');
      }
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
        control.takeState.pinnedId = null;
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
        const snapshot = control.takeState.undo.get(receipt.body?.operation_id);
        if (snapshot) {
          restoreTakeState(snapshot);
          control.takeState.undo.delete(receipt.body.operation_id);
        }
        if (String(receipt.body?.operation_id || '').startsWith('fixture_mastering_')) {
          Object.assign(control.mastering, {
            running: false, cancel_requested: false, status: 'idle',
            chunk_id: null, source_take_id: null, completed_count: 0,
            total_count: 7, progress_message: null, result: null,
            background_job_id: null,
          });
        }
        return finish(200, json({
          status: 'undone', restored_take_ids: [],
          registry_fingerprint: 'fixture-take-registry'.padEnd(64, 'b').slice(0, 64),
          produce: produceFixture('takes', control.takeState),
        }), 'application/json');
      }
      const backgroundCancel = url.pathname.match(/^\/api\/background-work\/([^/]+)\/cancel$/);
      if (backgroundCancel && request.method === 'POST') {
        if (backgroundCancel[1] === control.mastering.background_job_id) {
          control.mastering.running = false;
          control.mastering.cancel_requested = true;
          control.mastering.status = 'cancelled';
          control.mastering.progress_message = 'Mastering cancelled; no child was published.';
          control.mastering.result = null;
        }
        return finish(200, json({
          status: 'cancelled',
          job: { job_id: backgroundCancel[1], domain: 'mastering', state: 'cancelled' },
        }), 'application/json');
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
