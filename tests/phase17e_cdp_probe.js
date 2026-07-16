'use strict';

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index === -1 || !process.argv[index + 1]) {
    throw new Error(`${name} is required`);
  }
  return process.argv[index + 1];
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
    this.socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (message.error) {
          pending.reject(new Error(JSON.stringify(message.error)));
        } else {
          pending.resolve(message.result || {});
        }
        return;
      }
      if (message.method) this.events.push(message);
    });
  }

  async send(method, params = {}) {
    await this.opened;
    const id = this.nextId;
    this.nextId += 1;
    const response = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.socket.send(JSON.stringify({ id, method, params }));
    return response;
  }

  close() {
    this.socket.close();
  }
}

async function wait(milliseconds) {
  await new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function main() {
  const port = argument('--port');
  const targetUrl = argument('--url');
  const expected = JSON.parse(argument('--expected-json'));
  const endpoint = `http://127.0.0.1:${port}/json/new?${encodeURIComponent(targetUrl)}`;
  const targetResponse = await fetch(endpoint, { method: 'PUT' });
  if (!targetResponse.ok) {
    throw new Error(`Failed to create Chrome target: ${targetResponse.status}`);
  }
  const target = await targetResponse.json();
  const client = new CdpClient(target.webSocketDebuggerUrl);

  try {
    await client.send('Page.enable');
    await client.send('Runtime.enable');

    const deadline = Date.now() + 15000;
    let snapshot = null;

    while (Date.now() < deadline) {
      const result = await client.send('Runtime.evaluate', {
        expression: `(() => {
          const byId = (id) => document.getElementById(id);
          const summary = byId('script-generation-summary');
          const progress = byId('script-generation-progress');
          const button = byId('btn-gen-script');
          const discard = byId('btn-discard-generation-state');
          const reasons = byId('script-generation-reasons');
          const provenance = byId('script-generation-metadata-status');
          return {
            readyState: document.readyState,
            summary: summary ? summary.textContent : null,
            progress: progress ? progress.textContent : null,
            button: button ? button.textContent.trim() : null,
            buttonDisabled: button ? button.disabled : null,
            discardDisplay: discard ? discard.style.display : null,
            reasons: reasons ? reasons.textContent.trim() : null,
            provenance: provenance ? provenance.textContent : null
          };
        })()`,
        returnByValue: true,
        awaitPromise: true,
      });
      snapshot = result.result && result.result.value;
      if (
        snapshot
        && snapshot.readyState === 'complete'
        && expected.every((text) => JSON.stringify(snapshot).includes(text))
      ) {
        process.stdout.write(`PHASE17E_CDP_RESULT=${JSON.stringify(snapshot)}\n`);
        return;
      }
      await wait(100);
    }

    throw new Error(
      `Timed out waiting for expected DOM text ${JSON.stringify(expected)}; `
      + `last snapshot=${JSON.stringify(snapshot)}`
    );
  } finally {
    client.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
