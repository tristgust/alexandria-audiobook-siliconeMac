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
      if (message.error) {
        pending.reject(new Error(JSON.stringify(message.error)));
      } else {
        pending.resolve(message.result || {});
      }
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

  close() {
    this.socket.close();
  }
}

async function wait(milliseconds) {
  await new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function evaluate(client, expression) {
  const response = await client.send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (response.exceptionDetails) {
    throw new Error(JSON.stringify(response.exceptionDetails));
  }
  return response.result && response.result.value;
}

async function waitFor(client, expression, predicate, timeout = 15000) {
  const deadline = Date.now() + timeout;
  let last = null;
  while (Date.now() < deadline) {
    last = await evaluate(client, expression);
    if (predicate(last)) return last;
    await wait(100);
  }
  throw new Error(`Timed out waiting for browser state; last=${JSON.stringify(last)}`);
}

async function main() {
  const port = argument('--port');
  const targetUrl = argument('--url');
  const ids = JSON.parse(argument('--entry-ids-json'));
  const targetResponse = await fetch(
    `http://127.0.0.1:${port}/json/new?${encodeURIComponent(targetUrl)}`,
    { method: 'PUT' }
  );
  if (!targetResponse.ok) {
    throw new Error(`Failed to create Chrome target: ${targetResponse.status}`);
  }
  const target = await targetResponse.json();
  const client = new CdpClient(target.webSocketDebuggerUrl);

  try {
    await client.send('Page.enable');
    await client.send('Runtime.enable');

    const snapshotExpression = `(() => {
      const byId = (id) => document.getElementById(id);
      const content = byId('character-roster-content');
      const approval = byId('character-roster-approval');
      const discover = byId('btn-discover-character-roster');
      return {
        readyState: document.readyState,
        badge: byId('character-roster-status-badge')?.textContent || null,
        summary: byId('character-roster-summary')?.textContent || null,
        content: content?.textContent || '',
        contentHtml: content?.innerHTML || '',
        approvalDisplay: approval?.style.display ?? null,
        discoverDisplay: discover?.style.display ?? null,
        actionCount: content?.querySelectorAll('[data-roster-action]').length || 0
      };
    })()`;

    const initial = await waitFor(
      client,
      snapshotExpression,
      (value) => value
        && value.readyState === 'complete'
        && value.badge === 'Draft'
        && value.content.includes('Keep Separate')
        && value.content.includes('Merge into')
    );

    const mutation = await evaluate(client, `(async () => {
      async function readJson(response) {
        const payload = await response.json();
        if (!response.ok) {
          const detail = payload.detail || payload;
          const error = new Error(detail.message || JSON.stringify(detail));
          error.code = detail.code || null;
          error.status = response.status;
          throw error;
        }
        return payload;
      }
      async function post(url, body) {
        return readJson(await fetch(url, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body)
        }));
      }
      let draft = await readJson(await fetch('/api/character_roster/draft'));
      const initialFingerprint = draft.draft_fingerprint;
      const renamed = await post('/api/character_roster/draft/action', {
        draft_fingerprint: draft.draft_fingerprint,
        action: 'rename',
        entry_id: ${JSON.stringify(ids[0])},
        value: 'THE SEVENTH DOCTOR',
        display_name: 'The Seventh Doctor',
        preserve_old_as_alias: true
      });
      draft = renamed.draft;
      let stale = null;
      try {
        await post('/api/character_roster/draft/action', {
          draft_fingerprint: initialFingerprint,
          action: 'confirm',
          entry_id: ${JSON.stringify(ids[0])}
        });
      } catch (error) {
        stale = { status: error.status, code: error.code };
      }
      draft = (await post('/api/character_roster/draft/action', {
        draft_fingerprint: draft.draft_fingerprint,
        action: 'keep_separate',
        entry_id: ${JSON.stringify(ids[0])},
        other_entry_id: ${JSON.stringify(ids[1])}
      })).draft;
      draft = (await post('/api/character_roster/draft/action', {
        draft_fingerprint: draft.draft_fingerprint,
        action: 'confirm',
        entry_id: ${JSON.stringify(ids[0])}
      })).draft;
      draft = (await post('/api/character_roster/draft/action', {
        draft_fingerprint: draft.draft_fingerprint,
        action: 'confirm',
        entry_id: ${JSON.stringify(ids[1])}
      })).draft;
      const approved = await post('/api/character_roster/approve', {
        draft_fingerprint: draft.draft_fingerprint,
        acknowledged_unresolved: false
      });
      await refreshCharacterRosterStatus();
      return {
        stale,
        renamedName: renamed.draft.entries.find((entry) => entry.id === ${JSON.stringify(ids[0])})?.canonical_name,
        approvedStatus: approved.roster.status,
        approvedNames: approved.roster.entries.map((entry) => entry.canonical_name),
        reviewActions: approved.roster.review_history.map((item) => item.action),
        duplicateCount: approved.roster.duplicate_candidates.length
      };
    })()`);

    const final = await waitFor(
      client,
      snapshotExpression,
      (value) => value
        && value.badge === 'Approved'
        && value.content.includes('The Seventh Doctor')
        && value.actionCount === 0
        && value.approvalDisplay === 'none'
        && value.discoverDisplay === 'none'
    );

    process.stdout.write(
      `PHASE18C_CDP_RESULT=${JSON.stringify({ initial, mutation, final })}\n`
    );
  } finally {
    client.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
