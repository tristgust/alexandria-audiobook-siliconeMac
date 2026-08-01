'use strict';

const childProcess = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith('--')) continue;
    const next = argv[index + 1];
    values[key.slice(2)] = next && !next.startsWith('--') ? argv[++index] : true;
  }
  return values;
}

function resolveConfigPath({ args, python, app }) {
  const candidates = [
    args.config,
    process.env.ALEXANDRIA_CONFIG_PATH,
    path.join(__dirname, 'config.json'),
    path.join(path.dirname(app), 'config.json'),
    path.resolve(path.dirname(python), '..', '..', '..', 'config.json'),
    path.resolve(path.dirname(python), '..', '..', 'config.json'),
  ].filter(Boolean).map((value) => path.resolve(String(value)));
  return candidates.find((candidate) => fs.existsSync(candidate)) || '';
}

function requestJson(url, pathname, timeoutMs = 1200) {
  return new Promise((resolve) => {
    const request = http.get(new URL(pathname, url), (response) => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => { body += chunk; });
      response.on('end', () => {
        if (!response.statusCode || response.statusCode >= 500) {
          resolve(null);
          return;
        }
        try {
          resolve(JSON.parse(body));
        } catch (_error) {
          resolve(null);
        }
      });
    });
    request.setTimeout(timeoutMs, () => request.destroy(new Error('timeout')));
    request.on('error', () => resolve(null));
  });
}

async function probeAlexandria(url, timeoutMs = 1200) {
  const payload = await requestJson(url, '/api/runtime_status', timeoutMs);
  return Number.isInteger(Number(payload?.process_id));
}

async function stopExistingAlexandria(url, timeoutMs = 10000) {
  const status = await requestJson(url, '/api/runtime_status');
  const pid = Number(status?.process_id);
  if (!Number.isInteger(pid) || pid <= 1 || pid === process.pid) {
    throw new Error('Existing Alexandria backend did not report a safe process ID.');
  }
  process.stderr.write(`Stopping stale Alexandria backend process ${pid}.\n`);
  try {
    process.kill(pid, 'SIGTERM');
  } catch (error) {
    if (error?.code !== 'ESRCH') throw error;
  }
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!(await probeAlexandria(url))) return;
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(`Existing Alexandria backend process ${pid} did not stop.`);
}

async function waitForAlexandria(url, child, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await probeAlexandria(url)) return;
    if (child && child.exitCode !== null) {
      throw new Error(`Alexandria backend exited before readiness (${child.exitCode}).`);
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(`Timed out waiting for Alexandria backend at ${url}`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const host = String(args.host || '127.0.0.1');
  const port = Number(args.port || 4200);
  const python = path.resolve(String(args.python || path.join('app', 'env', 'bin', 'python')));
  const app = path.resolve(String(args.app || path.join('app', 'app.py')));
  const config = resolveConfigPath({ args, python, app });
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('--port must be an integer from 1 through 65535');
  }
  const url = new URL(`http://${host}:${port}/`);
  if (await probeAlexandria(url)) {
    await stopExistingAlexandria(url);
  }

  const child = childProcess.spawn(python, [app], {
    cwd: __dirname,
    env: {
      ...process.env,
      ALEXANDRIA_HOST: host,
      ALEXANDRIA_PORT: String(port),
      ...(config ? { ALEXANDRIA_CONFIG_PATH: config } : {}),
    },
    stdio: 'inherit',
  });
  await waitForAlexandria(url, child);
  process.stdout.write(`Alexandria backend ready: ${url.href}\n`);

  const forward = (signal) => {
    if (child.exitCode === null) child.kill(signal);
  };
  process.once('SIGINT', () => forward('SIGINT'));
  process.once('SIGTERM', () => forward('SIGTERM'));
  const code = await new Promise((resolve) => child.once('exit', resolve));
  process.exitCode = Number.isInteger(code) ? code : 0;
}

main().catch((error) => {
  process.stderr.write(`Alexandria backend failed: ${error.message}\n`);
  process.exitCode = 1;
});
