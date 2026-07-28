'use strict';

const fs = require('fs');
const http = require('http');
const path = require('path');
const { handleSpeakerRecoveryApi } = require('./cast_profile_fixture_speaker_recovery.js');
const { handleTaskApi } = require('./cast_profile_fixture_task_routes.js');
const { handleVisualApi } = require('./cast_profile_fixture_visual_routes.js');
const { handleVoiceApi } = require('./cast_profile_fixture_voice_routes.js');

const SILENT_WAV = Buffer.from('UklGRsQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YaAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', 'base64');
const json = (value) => JSON.stringify(value);

function fixtureServer(repoRoot) {
  const staticRoot = path.join(repoRoot, 'app/static');
  const control = {
    mode: 'normal', visual: 'idle', selected: 'cast:clara',
    requests: [], pending: [], aborted: 0, savedVoice: 'Avery', savedConfig: null,
    savedBackend: 'qwen3_base', controlledConfirmations: 0, controlledSaves: 0,
    designedRollbacks: 0, deferPostSaveRefresh: false, deferNextDesignedPreview: false,
    designedPreviewPending: [],
    libraryAssignments: {}, voiceAssignments: 0,
    taskImported: false, rosterDraftApplied: false, rosterApproved: false,
    enrichmentStarted: false, enrichmentReads: 0,
    recoveryActive: false, recoveryUndone: false,
  };
  const projectsModule = `export async function mount({root}){const a=document.createElement('article');a.dataset.projectsPage='';const h=document.createElement('h1');h.dataset.pageHeading='';h.textContent='Project Home';a.append(h);root.replaceChildren(a);}`;
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, 'http://fixture.invalid');
    const receipt = { method: request.method, path: url.pathname, body: null, completed: false };
    control.requests.push(receipt);
    request.once('aborted', () => { if (!receipt.completed) control.aborted += 1; });
    response.once('close', () => { if (!receipt.completed) control.aborted += 1; });
    const finish = (status, value = '', type = 'text/plain; charset=utf-8') => {
      if (response.destroyed || response.writableEnded) return;
      response.writeHead(status, { 'content-type': type, 'cache-control': 'no-store' });
      response.end(request.method === 'HEAD' ? '' : value);
      receipt.completed = true;
    };
    if (url.pathname.startsWith('/api/')) {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      if (chunks.length) {
        const raw = Buffer.concat(chunks);
        const contentType = String(request.headers['content-type'] || '');
        receipt.body = contentType.includes('application/json')
          ? JSON.parse(raw.toString('utf8'))
          : { multipart: true, size: raw.length };
      }
      const context = { control, finish, receipt, request, url };
      if (handleSpeakerRecoveryApi(context) || handleTaskApi(context)
          || handleVoiceApi(context) || handleVisualApi(context)) return;
      return finish(404, json({ detail: 'Fixture endpoint missing.' }), 'application/json');
    }
    if ([
      '/fixture-controlled.wav', '/fixture-reference.wav', '/fixture-preview.wav',
      '/fixture-benny.wav', '/fixture-narrator.wav', '/fixture-doctor.wav', '/fixture-adapter.wav',
      '/fixture-built-in-range.wav', '/fixture-designed-audition.wav',
      '/fixture-designed-a.wav', '/fixture-designed-b.wav',
    ].includes(url.pathname)) {
      return finish(200, SILENT_WAV, 'audio/wav');
    }
    if (url.pathname === '/static/pages/projects.js') return finish(200, projectsModule, 'text/javascript; charset=utf-8');
    const relative = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\/static\//, '');
    const filename = path.resolve(staticRoot, relative);
    if (!filename.startsWith(`${staticRoot}${path.sep}`) || !fs.existsSync(filename)
      || !fs.statSync(filename).isFile()) return finish(404, 'Not found');
    const extension = path.extname(filename);
    const type = extension === '.html' ? 'text/html' : extension === '.css' ? 'text/css' : 'text/javascript';
    return finish(200, fs.readFileSync(filename), `${type}; charset=utf-8`);
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve({
      server, control, url: `http://127.0.0.1:${server.address().port}/`,
      release: () => control.pending.splice(0).forEach((send) => send()),
      releaseDesignedPreview: () => control.designedPreviewPending.shift()?.(),
      close: () => new Promise((done) => {
        server.close(done);
        server.closeAllConnections?.();
      }),
    }));
  });
}

module.exports = { fixtureServer };
