'use strict';

const fs = require('fs');
const path = require('path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');
const { fixtureServer } = require('./produce_export_fixture_server.js');

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function externalEvaluate(session, expression) {
  const targets = await fetch(`http://127.0.0.1:${session.port}/json/list`).then(
    (response) => response.json(),
  );
  const target = targets.find((item) => item.type === 'page'
    && item.url.startsWith(session.baseUrl));
  if (!target?.webSocketDebuggerUrl) throw new Error('Fixture page target not found');
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });
  try {
    return await Promise.race([
      new Promise((resolve, reject) => {
        socket.addEventListener('message', (messageEvent) => {
          const message = JSON.parse(messageEvent.data);
          if (message.id !== 1) return;
          if (message.error) reject(new Error(JSON.stringify(message.error)));
          else resolve(message.result?.result?.value);
        });
        socket.send(JSON.stringify({
          id: 1,
          method: 'Runtime.evaluate',
          params: { expression, returnByValue: true },
        }));
      }),
      wait(5000).then(() => { throw new Error('External CDP observation timed out'); }),
    ]);
  } finally {
    socket.close();
  }
}

async function waitForRequest(control, before, pathName) {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const count = control.requests.filter(
      (item) => item.path === pathName && item.completed,
    ).length;
    if (count > before) return count;
    await wait(25);
  }
  return control.requests.filter((item) => item.path === pathName && item.completed).length;
}

function sendPhysicalKey(session, keyName) {
  const space = keyName === 'Space';
  const key = space ? ' ' : 'Enter';
  const code = keyName;
  const virtualKey = space ? 32 : 13;
  const common = {
    key, code, windowsVirtualKeyCode: virtualKey, nativeVirtualKeyCode: virtualKey,
  };
  const events = [{
    type: 'keyDown', ...common,
    ...(space ? {} : { text: '\r', unmodifiedText: '\r' }),
  }, { type: 'keyUp', ...common }];
  events.forEach((params) => session.client.socket.send(JSON.stringify({
    id: session.client.nextId++, method: 'Input.dispatchKeyEvent', params,
  })));
  return events;
}

async function inspectCase(server, artifacts, controlName, keyName) {
  if (!server.external) server.control.mode = 'produce-mixed';
  const folder = path.join(artifacts, `${controlName}-${keyName.toLowerCase()}`);
  const selectedChunk = server.external ? 'chunk%3A0' : 'stale-1';
  const session = await BrowserSession.open({
    url: `${server.url}#/produce?chunk=${selectedChunk}`, artifacts: folder, width: 1024, height: 900,
  });
  session.baseUrl = server.url;
  try {
    await session.waitFor(`document.body.dataset.shellState==='ready'`);
    await session.waitFor(`document.querySelector('[data-produce-page]')?.dataset.pageState==='ready'`);
    if (controlName === 'status') {
      await session.evaluate(`document.querySelector('[data-produce-filter="ready"]')?.click()`);
      await session.waitFor(`document.querySelectorAll('[data-audio-state="ready"]')?.length>=1`);
    }
    const selector = controlName === 'play'
      ? '[data-audio-state="current"] [data-primitive="compact-play"]'
      : '[data-audio-state="ready"] [data-produce-row-action]';
    const focused = await session.evaluate(`(() => {
      globalThis.nativeKeyEvidence={activations:0,events:[]};
      const target=document.querySelector(${JSON.stringify(selector)});
      const capture=(phase)=>(event)=>{
        if(event.target!==target) return;
        globalThis.nativeKeyEvidence.events.push({
          phase,key:event.key,defaultPrevented:event.defaultPrevented,
        });
      };
      document.addEventListener('keydown',capture('capture'),true);
      document.addEventListener('keydown',capture('bubble'));
      document.addEventListener('click',(event)=>{
        if(event.target.closest(${JSON.stringify(selector)})===target) {
          globalThis.nativeKeyEvidence.activations+=1;
        }
      },true);
      target?.focus();
      return document.activeElement===target;
    })()`);
    const generationBefore = server.external ? 0 : server.control.requests.filter(
      (item) => item.path === '/api/produce/generate' && item.completed,
    ).length;
    let pausedPlan = null;
    let pausedGenerate = null;
    if (server.external && controlName === 'status') {
      await session.client.send('Fetch.enable', {
        patterns: [
          { urlPattern: '*api/produce/plan*', requestStage: 'Request' },
          { urlPattern: '*api/produce/generate*', requestStage: 'Request' },
        ],
      });
      pausedPlan = session.client.event(
        'Fetch.requestPaused',
        (params) => params.request?.url?.includes('/api/produce/plan'),
      );
      pausedGenerate = session.client.event(
        'Fetch.requestPaused',
        (params) => params.request?.url?.includes('/api/produce/generate'),
      );
    }
    const inputEvents = sendPhysicalKey(session, keyName);
    await wait(200);
    let interceptedProviderRequest = false;
    let generationAfter = generationBefore;
    if (controlName === 'status' && server.external) {
      const planRequest = await pausedPlan;
      await session.client.send('Fetch.fulfillRequest', {
        requestId: planRequest.requestId,
        responseCode: 200,
        responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
        body: Buffer.from(JSON.stringify({
          safe_to_execute: true,
          plan_fingerprint: 'b19-t06-disposable-plan',
          chunks_fingerprint: 'b19-t06-disposable-chunks',
          selected_chunk_ids: ['chunk:1'],
        })).toString('base64'),
      });
      const generateRequest = await pausedGenerate;
      await session.client.send('Fetch.fulfillRequest', {
        requestId: generateRequest.requestId,
        responseCode: 200,
        responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
        body: Buffer.from(JSON.stringify({ status: 'queued', fixture: true })).toString('base64'),
      });
      interceptedProviderRequest = true;
      generationAfter = generationBefore + 1;
      await session.client.send('Fetch.disable');
    } else if (controlName === 'status') {
      generationAfter = await waitForRequest(
        server.control, generationBefore, '/api/produce/generate',
      );
    }
    const observed = await externalEvaluate(session, `(() => ({
      ...globalThis.nativeKeyEvidence,
      source:document.querySelector('[data-persistent-player]')?.dataset.mediaSource||'',
    }))()`);
    const eventClear = observed.events.length === 2
      && observed.events.every((event) => event.defaultPrevented === false)
      && observed.events.every((event) => event.key === (keyName === 'Space' ? ' ' : 'Enter'));
    const playSourceMatches = server.external
      ? /\/voicelines\/fixture-current\.wav/.test(observed.source)
      : /\/fixture-audio\//.test(observed.source);
    const action = controlName === 'play'
      ? observed.activations === 1 && playSourceMatches
      : observed.activations === 1 && generationAfter - generationBefore === 1;
    return {
      case: `${controlName}-${keyName.toLowerCase()}`,
      status: focused && eventClear && action ? 'PASS' : 'FAIL',
      mechanism: 'CDP Input.dispatchKeyEvent', inputEvents, focused, observed,
      generationRequests: generationAfter - generationBefore,
      interceptedProviderRequest,
      assertions: {
        focused,
        defaultNotPrevented: eventClear,
        nativeActivation: action,
        noProviderCall: !server.external || controlName !== 'status' || interceptedProviderRequest,
      },
    };
  } finally {
    await session.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required(args, 'artifacts'));
  const externalUrl = args.url || '';
  const server = externalUrl
    ? { url: externalUrl.endsWith('/') ? externalUrl : `${externalUrl}/`, external: true }
    : { ...(await fixtureServer()), external: false };
  const results = [];
  try {
    for (const controlName of ['play', 'status']) {
      for (const keyName of ['Enter', 'Space']) {
        results.push(await inspectCase(server, artifacts, controlName, keyName));
      }
    }
  } finally {
    if (!server.external) {
      server.release();
      await server.close();
    }
  }
  const report = {
    status: results.every((item) => item.status === 'PASS') ? 'PASS' : 'FAIL',
    scenario: 'Nested Produce controls remain keyboard-activatable', results,
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  writeJson(path.join(artifacts, 'cleanup.json'), {
    externalServer: server.external,
    serverClosed: server.external ? null : !server.server.listening,
    pendingResponses: server.external ? 0 : server.control.pending.length,
    providerCallsPrevented: results.filter((item) => item.case.startsWith('status-'))
      .every((item) => item.interceptedProviderRequest),
    browserReceipts: fs.readdirSync(artifacts).filter((name) => name !== 'report.json'
      && name !== 'cleanup.json').map((name) => path.join(name, 'cleanup.json')),
  });
  process.stdout.write(`B19_T06_PRODUCE_NESTED_KEYBOARD=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
