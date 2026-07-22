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
    this.socket.addEventListener('message', event => {
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
    const result = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }

  close() {
    this.socket.close();
  }
}

async function wait(milliseconds) {
  await new Promise(resolve => setTimeout(resolve, milliseconds));
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

async function waitFor(client, expression, label, timeout = 15000) {
  const deadline = Date.now() + timeout;
  let lastValue = null;
  while (Date.now() < deadline) {
    lastValue = await evaluate(client, expression);
    if (lastValue) return lastValue;
    await wait(100);
  }
  throw new Error(`Timed out waiting for ${label}; last value: ${JSON.stringify(lastValue)}`);
}

async function main() {
  const port = argument('--port');
  const targetUrl = argument('--url');
  const projectId = argument('--project-id');
  const screenshotPath = argument('--screenshot');
  const endpoint = `http://127.0.0.1:${port}/json/new?${encodeURIComponent(targetUrl)}`;
  const response = await fetch(endpoint, { method: 'PUT' });
  if (!response.ok) throw new Error(`Failed to create target: ${response.status}`);
  const target = await response.json();
  const client = new CdpClient(target.webSocketDebuggerUrl);
  const consoleErrors = [];
  const runtimeErrors = [];
  const networkErrors = [];

  try {
    await client.send('Page.enable');
    await client.send('Runtime.enable');
    await client.send('Log.enable');
    await client.send('Network.enable');
    await client.send('Emulation.setDeviceMetricsOverride', {
      width: 1536,
      height: 1024,
      deviceScaleFactor: 1,
      mobile: false,
    });
    client.socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (message.method === 'Runtime.exceptionThrown') {
        runtimeErrors.push(message.params?.exceptionDetails?.text || 'Runtime exception');
      }
      if (message.method === 'Log.entryAdded' && message.params?.entry?.level === 'error') {
        consoleErrors.push(message.params.entry.text);
      }
      if (message.method === 'Network.responseReceived') {
        const status = Number(message.params?.response?.status || 0);
        const url = message.params?.response?.url || '';
        if (status >= 400 && !(status === 404 && url.endsWith('/cover'))) {
          networkErrors.push({ status, url });
        }
      }
      if (message.method === 'Network.loadingFailed') {
        networkErrors.push({
          status: 0,
          error: message.params?.errorText || null,
          requestId: message.params?.requestId || null,
        });
      }
    });

    await waitFor(
      client,
      `document.readyState === 'complete' && Boolean(window.AlexandriaCanonicalInterface)`,
      'canonical interface'
    );
    await evaluate(client, `(() => {
      window.location.hash = '#/projects';
      window.dispatchEvent(new HashChangeEvent('hashchange'));
      return true;
    })()`);
    const selector = `.project-open-action[data-project-id="${projectId}"]`;
    await waitFor(
      client,
      `Boolean(document.querySelector(${JSON.stringify(selector)}))`,
      'target project row'
    );

    const before = await evaluate(client, `(async () => ({
      runtime: await fetch('/api/runtime_status').then(response => response.json()),
      catalog: await fetch('/api/projects').then(response => response.json()),
      hash: window.location.hash,
    }))()`);
    await evaluate(client, `(() => {
      const button = document.querySelector(${JSON.stringify(selector)});
      if (!button) throw new Error('Target project action disappeared.');
      button.click();
      return true;
    })()`);

    const after = await waitFor(
      client,
      `(async () => {
        const runtime = await fetch('/api/runtime_status').then(response => response.json());
        if (runtime.active_project_id !== ${JSON.stringify(projectId)}) return null;
        if (!window.location.hash.includes('project=${projectId}')) return null;
        const catalog = await fetch('/api/projects').then(response => response.json());
        return {
          runtime,
          catalog,
          hash: window.location.hash,
          title: document.getElementById('canonical-page-title')?.textContent?.trim() || null,
          restartRequiredVisible: document.body.innerText.includes('Restart required'),
          inlineError: document.querySelector('#canonical-shell-live[data-state="error"]')?.textContent?.trim() || null,
        };
      })()`,
      'managed runtime activation',
      20000
    );

    const screenshot = await client.send('Page.captureScreenshot', {
      format: 'png',
      captureBeyondViewport: false,
      fromSurface: true,
    });
    require('fs').writeFileSync(screenshotPath, Buffer.from(screenshot.data, 'base64'));

    const selected = (after.catalog.projects || []).find(item => item.id === projectId) || null;
    const report = {
      beforeActiveProjectId: before.runtime.active_project_id,
      afterActiveProjectId: after.runtime.active_project_id,
      afterActiveProjectRoot: after.runtime.active_project_root,
      projectSwitching: after.runtime.project_switching,
      hash: after.hash,
      pageTitle: after.title,
      catalogCurrentProjectId: after.catalog.current_project_id,
      catalogLastSelectedProjectId: after.catalog.last_selected_project_id,
      selectedProjectActivationState: selected?.activation_state || null,
      selectedProjectCurrent: selected?.current || false,
      restartRequiredVisible: after.restartRequiredVisible,
      inlineError: after.inlineError,
      consoleErrors,
      runtimeErrors,
      networkErrors,
      screenshotPath,
      screenshotBytes: Buffer.from(screenshot.data, 'base64').length,
    };
    if (report.beforeActiveProjectId === projectId) {
      throw new Error('Browser fixture did not start on a different project.');
    }
    if (report.afterActiveProjectId !== projectId) {
      throw new Error('Runtime did not activate the clicked project.');
    }
    if (report.catalogCurrentProjectId !== projectId || report.catalogLastSelectedProjectId !== projectId) {
      throw new Error('Catalog selection and runtime identity diverged.');
    }
    if (!report.hash.includes(`project=${projectId}`)) {
      throw new Error(`Route context was not preserved: ${report.hash}`);
    }
    if (report.projectSwitching !== 'dynamic') {
      throw new Error(`Unexpected project switching contract: ${report.projectSwitching}`);
    }
    if (report.selectedProjectActivationState !== 'current' || !report.selectedProjectCurrent) {
      throw new Error('Selected project was not presented as current.');
    }
    if (report.restartRequiredVisible || report.inlineError) {
      throw new Error('The browser presented a restart requirement or activation error.');
    }
    if (consoleErrors.length || runtimeErrors.length || networkErrors.length) {
      throw new Error(`Browser errors: ${JSON.stringify({ consoleErrors, runtimeErrors, networkErrors })}`);
    }
    process.stdout.write(`PROJECT_ACTIVATION_CDP=${JSON.stringify(report)}\n`);
  } finally {
    client.close();
  }
}

main().catch(error => {
  console.error(error.stack || error);
  process.exit(1);
});
