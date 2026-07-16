'use strict';

const fs = require('fs');

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
    this.listeners = new Map();
    this.opened = new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, { once: true });
      this.socket.addEventListener('error', reject, { once: true });
    });
    this.socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if (!message.id) {
        const handlers = this.listeners.get(message.method) || [];
        handlers.forEach((handler) => handler(message.params || {}));
        return;
      }
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(JSON.stringify(message.error)));
      else pending.resolve(message.result || {});
    });
  }
  on(method, handler) {
    const handlers = this.listeners.get(method) || [];
    handlers.push(handler);
    this.listeners.set(method, handlers);
  }
  async send(method, params = {}) {
    await this.opened;
    const id = this.nextId++;
    const promise = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.socket.send(JSON.stringify({ id, method, params }));
    return promise;
  }
  close() { this.socket.close(); }
}

async function wait(milliseconds) {
  await new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function evaluate(client, expression) {
  const result = await client.send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (result.exceptionDetails) {
    throw new Error(JSON.stringify(result.exceptionDetails));
  }
  return result.result && result.result.value;
}

async function capture(client, screenshotPath) {
  const result = await client.send('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false,
  });
  fs.writeFileSync(screenshotPath, Buffer.from(result.data, 'base64'));
}

async function snapshot(client) {
  return evaluate(client, `(() => {
    const byId = (id) => document.getElementById(id);
    const rect = (element) => {
      if (!element) return null;
      const value = element.getBoundingClientRect();
      return {
        x: Math.round(value.x),
        y: Math.round(value.y),
        width: Math.round(value.width),
        height: Math.round(value.height),
        bottom: Math.round(value.bottom),
        right: Math.round(value.right),
      };
    };
    const panel = byId('character-visual-panel');
    const grid = panel?.querySelector('.visual-workspace-grid');
    const master = panel?.querySelector('.visual-master');
    const detail = byId('character-visual-detail');
    const list = byId('character-visual-list');
    const state = byId('character-visual-status-badge');
    const technical = detail?.querySelector('details');
    const technicalCode = technical?.querySelector('code');
    return {
      readyState: document.readyState,
      viewport: {
        width: window.innerWidth,
        height: window.innerHeight,
      },
      panelDisplay: panel?.style.display ?? null,
      panelRect: rect(panel),
      gridRect: rect(grid),
      masterRect: rect(master),
      detailRect: rect(detail),
      gridColumns: grid ? getComputedStyle(grid).gridTemplateColumns : null,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      enabledChecked: byId('character-visual-enabled')?.checked ?? null,
      enabledDisabled: byId('character-visual-enabled')?.disabled ?? null,
      discoverDisabled: byId('btn-discover-character-visuals')?.disabled ?? null,
      state: state?.textContent?.trim() || null,
      stateTone: state?.dataset?.state || null,
      summary: byId('character-visual-summary')?.textContent?.trim() || null,
      progress: byId('character-visual-progress')?.textContent?.trim() || null,
      selectionCount: byId('character-visual-selection-count')?.textContent?.trim() || null,
      listText: list?.textContent?.trim() || null,
      listHtml: list?.innerHTML || null,
      checkboxCount: list ? list.querySelectorAll('.character-visual-entry').length : 0,
      rowCount: list ? list.querySelectorAll('[data-character-visual-view]').length : 0,
      activeRows: list ? list.querySelectorAll('.is-active').length : 0,
      detailText: detail?.textContent?.trim() || null,
      detailHtml: detail?.innerHTML || null,
      detailPanels: detail ? detail.querySelectorAll('[data-visual-panel]').length : 0,
      technicalOpen: technical?.open ?? null,
      technicalCodeVisible: Boolean(
        technical?.open
        && technicalCode
        && technicalCode.getClientRects().length > 0
      ),
      activeElement: document.activeElement ? {
        tag: document.activeElement.tagName,
        id: document.activeElement.id || null,
        text: document.activeElement.textContent?.trim() || null,
      } : null,
      errorDisplay: byId('character-visual-error')?.style.display ?? null,
    };
  })()`);
}

async function main() {
  const port = argument('--port');
  const targetUrl = argument('--url');
  const mode = argument('--mode');
  const width = Number(argument('--width'));
  const height = Number(argument('--height'));
  const screenshotPath = argument('--screenshot');
  const endpoint = `http://127.0.0.1:${port}/json/new?${encodeURIComponent(targetUrl)}`;
  const response = await fetch(endpoint, { method: 'PUT' });
  if (!response.ok) throw new Error(`Failed to create target: ${response.status}`);
  const target = await response.json();
  const client = new CdpClient(target.webSocketDebuggerUrl);
  const runtimeErrors = [];
  const consoleErrors = [];

  client.on('Runtime.exceptionThrown', (params) => {
    runtimeErrors.push(
      params.exceptionDetails?.exception?.description
      || params.exceptionDetails?.text
      || JSON.stringify(params)
    );
  });
  client.on('Runtime.consoleAPICalled', (params) => {
    if (params.type !== 'error') return;
    consoleErrors.push(
      (params.args || []).map((item) => item.value || item.description || '').join(' ')
    );
  });

  try {
    await client.send('Page.enable');
    await client.send('Runtime.enable');
    await client.send('Emulation.setDeviceMetricsOverride', {
      width,
      height,
      deviceScaleFactor: 1,
      mobile: width <= 500,
      screenWidth: width,
      screenHeight: height,
    });

    const deadline = Date.now() + 18000;
    let current = null;
    while (Date.now() < deadline) {
      current = await snapshot(client);
      if (
        current
        && current.readyState === 'complete'
        && current.panelDisplay === ''
        && current.checkboxCount > 0
      ) break;
      await wait(100);
    }
    if (!current || current.checkboxCount < 1) {
      throw new Error(`Visual workspace did not load: ${JSON.stringify(current)}`);
    }

    await evaluate(client, `(() => {
      document.querySelector('[data-tab="script"]')?.click();
      const panel = document.getElementById('character-visual-panel');
      panel?.scrollIntoView({ block: 'start' });
    })()`);
    await wait(150);
    current = await snapshot(client);

    if (mode === 'enable-selection') {
      await evaluate(client, `(() => {
        const enabled = document.getElementById('character-visual-enabled');
        enabled.checked = true;
        enabled.dispatchEvent(new Event('change', { bubbles: true }));
        const checkbox = document.querySelector('.character-visual-entry');
        checkbox.checked = true;
        checkbox.dispatchEvent(new Event('change', { bubbles: true }));
      })()`);
      await wait(120);
      current = await snapshot(client);
      if (
        !current.enabledChecked
        || current.discoverDisabled
        || current.selectionCount !== '1 selected'
      ) {
        throw new Error(`Explicit enable failed: ${JSON.stringify(current)}`);
      }
    } else if (
      mode === 'complete-detail'
      || mode === 'narrow-complete-detail'
    ) {
      if (
        current.rowCount < 1
        || !String(current.state).includes('1 ready')
      ) {
        throw new Error(`Complete visual status missing: ${JSON.stringify(current)}`);
      }
      await evaluate(client, `(() => {
        document.querySelector('[data-character-visual-view]')?.click();
      })()`);
      const detailDeadline = Date.now() + 10000;
      while (Date.now() < detailDeadline) {
        current = await snapshot(client);
        if (
          current.detailText
          && current.detailText.includes('Validated dossier')
          && current.detailPanels === 6
        ) break;
        await wait(100);
      }
      if (
        !current.detailText
        || !current.detailText.includes('Validated dossier')
      ) {
        throw new Error(`Visual detail did not load: ${JSON.stringify(current)}`);
      }
      if (
        current.detailHtml.includes('<script>alert(1)</script>')
        || current.detailHtml.includes('<img src=x onerror=alert(1)>')
        || current.detailHtml.includes('<svg onload=alert(1)>')
      ) {
        throw new Error(`Unsafe visual detail HTML: ${current.detailHtml}`);
      }
      if (
        current.technicalOpen !== false
        || current.technicalCodeVisible !== false
      ) {
        throw new Error(`Technical details are exposed by default: ${JSON.stringify(current)}`);
      }
      if (
        !current.activeElement
        || current.activeElement.tag !== 'H6'
      ) {
        throw new Error(`Inspector focus was not moved to its heading: ${JSON.stringify(current)}`);
      }
      if (mode === 'narrow-complete-detail') {
        await evaluate(client, `(() => {
          document.getElementById('character-visual-detail')?.scrollIntoView({ block: 'start' });
        })()`);
        await wait(120);
        current = await snapshot(client);
        if (
          !current.masterRect
          || !current.detailRect
          || current.detailRect.y < current.masterRect.y
          || current.gridColumns.trim().split(/\s+/).length !== 1
        ) {
          throw new Error(`Narrow layout did not stack: ${JSON.stringify(current)}`);
        }
      } else if (
        !current.masterRect
        || !current.detailRect
        || Math.abs(current.masterRect.y - current.detailRect.y) > 2
        || current.detailRect.x <= current.masterRect.x
      ) {
        throw new Error(`Desktop master/detail layout is not side by side: ${JSON.stringify(current)}`);
      }
    } else if (mode === 'disabled-idle') {
      if (
        current.enabledChecked !== false
        || current.discoverDisabled !== true
        || !String(current.state).includes('Not started')
      ) {
        throw new Error(`Disabled idle state incorrect: ${JSON.stringify(current)}`);
      }
      if (
        current.listHtml.includes('<img src=x onerror=alert(1)>')
        || current.listHtml.includes('<script>alert(1)</script>')
      ) {
        throw new Error(`Unsafe visual status HTML: ${current.listHtml}`);
      }
      if (
        current.listText.includes('character_')
        || current.listText.includes('Observations:')
      ) {
        throw new Error(`Raw implementation detail is visible: ${current.listText}`);
      }
    } else {
      throw new Error(`Unknown mode: ${mode}`);
    }

    if (current.horizontalOverflow) {
      throw new Error(`Visual workspace overflows horizontally: ${JSON.stringify(current)}`);
    }
    if (runtimeErrors.length || consoleErrors.length) {
      throw new Error(JSON.stringify({ runtimeErrors, consoleErrors }));
    }

    await capture(client, screenshotPath);
    process.stdout.write(
      `PHASE18D_VISUAL_CDP_RESULT=${JSON.stringify({
        ...current,
        runtimeErrors,
        consoleErrors,
      })}\n`
    );
  } finally {
    client.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
