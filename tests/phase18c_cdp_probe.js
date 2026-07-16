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
    this.opened = new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, { once: true });
      this.socket.addEventListener('error', reject, { once: true });
    });
    this.socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if (!message.id) return;
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
    const response = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.socket.send(JSON.stringify({ id, method, params }));
    return response;
  }
  close() { this.socket.close(); }
}

async function wait(milliseconds) {
  await new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function main() {
  const port = argument('--port');
  const targetUrl = argument('--url');
  const expected = JSON.parse(argument('--expected-json'));
  const actionMode = argument('--action-mode');
  const endpoint = `http://127.0.0.1:${port}/json/new?${encodeURIComponent(targetUrl)}`;
  const response = await fetch(endpoint, { method: 'PUT' });
  if (!response.ok) throw new Error(`Failed to create target: ${response.status}`);
  const target = await response.json();
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
          const content = byId('character-roster-content');
          return {
            readyState: document.readyState,
            badge: byId('character-roster-status-badge')?.textContent || null,
            summary: byId('character-roster-summary')?.textContent || null,
            progress: byId('character-roster-progress')?.textContent || null,
            source: byId('character-roster-source')?.textContent || null,
            counts: byId('character-roster-counts')?.textContent || null,
            contentText: content?.textContent || null,
            contentHtml: content?.innerHTML || null,
            actionCount: content ? content.querySelectorAll('[data-roster-action]').length : 0,
            approvalDisplay: byId('character-roster-approval')?.style.display ?? null,
            approvalText: byId('btn-approve-character-roster')?.textContent.trim() || null,
            discoverDisplay: byId('btn-discover-character-roster')?.style.display ?? null,
            discoverText: byId('btn-discover-character-roster')?.textContent.trim() || null,
            errorDisplay: byId('character-roster-error')?.style.display ?? null
          };
        })()`,
        returnByValue: true,
        awaitPromise: true,
      });
      snapshot = result.result && result.result.value;
      const textMatch = snapshot
        && expected.every((text) => JSON.stringify(snapshot).includes(text));
      const actionMatch = actionMode === 'present'
        ? snapshot && snapshot.actionCount > 0
        : actionMode === 'absent'
          ? snapshot && snapshot.actionCount === 0
          : true;
      const escaped = !snapshot
        || !snapshot.contentHtml
        || (
          !snapshot.contentHtml.includes('<script>alert(1)</script>')
          && !snapshot.contentHtml.includes('<img src=x onerror=alert(1)>')
        );

      if (
        snapshot
        && snapshot.readyState === 'complete'
        && textMatch
        && actionMatch
        && escaped
      ) {
        process.stdout.write(`PHASE18C_CDP_RESULT=${JSON.stringify(snapshot)}\n`);
        return;
      }
      await wait(100);
    }
    throw new Error(
      `Timed out: expected=${JSON.stringify(expected)} `
      + `actionMode=${actionMode} snapshot=${JSON.stringify(snapshot)}`
    );
  } finally {
    client.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
