'use strict';

const childProcess = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');

const ROOT = __dirname;
const STABLE_COMMIT = '92c89d84d7d7f8ff711b235457e89f51f9c73de2';
const STABLE_LABEL = STABLE_COMMIT.slice(0, 7);

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

function runGit(args, cwd = ROOT) {
  const result = childProcess.spawnSync('git', args, {
    cwd,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  if (result.status !== 0) {
    throw new Error((result.stderr || result.stdout || `git ${args.join(' ')} failed`).trim());
  }
  return result.stdout.trim();
}

function stableCheckoutRoot() {
  const pinokioHome = path.dirname(path.dirname(ROOT));
  return path.join(pinokioHome, 'cache', `alexandria-stable-ui-${STABLE_LABEL}`);
}

function ensureStableStaticRoot() {
  const checkout = stableCheckoutRoot();
  const index = path.join(checkout, 'app', 'static', 'index.html');
  if (fs.existsSync(index)) {
    const head = runGit(['rev-parse', 'HEAD'], checkout);
    if (head !== STABLE_COMMIT) {
      throw new Error(`Stable UI cache is at ${head.slice(0, 7)}, expected ${STABLE_LABEL}: ${checkout}`);
    }
    return path.dirname(index);
  }

  if (fs.existsSync(checkout)) {
    throw new Error(`Stable UI cache exists but is incomplete: ${checkout}`);
  }
  fs.mkdirSync(path.dirname(checkout), { recursive: true });
  runGit(['worktree', 'add', '--detach', checkout, STABLE_COMMIT]);
  if (!fs.existsSync(index)) {
    throw new Error(`Stable UI checkout did not contain app/static/index.html: ${checkout}`);
  }
  return path.dirname(index);
}

function contentType(filename) {
  return {
    '.css': 'text/css; charset=utf-8',
    '.gif': 'image/gif',
    '.html': 'text/html; charset=utf-8',
    '.ico': 'image/x-icon',
    '.jpeg': 'image/jpeg',
    '.jpg': 'image/jpeg',
    '.js': 'text/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png': 'image/png',
    '.svg': 'image/svg+xml; charset=utf-8',
    '.webp': 'image/webp',
  }[path.extname(filename).toLowerCase()] || 'application/octet-stream';
}

function staticFilename(requestUrl, staticRoot) {
  const pathname = decodeURIComponent(new URL(requestUrl, 'http://localhost').pathname);
  let relative = null;
  if (pathname === '/' || pathname === '/index.html') relative = 'index.html';
  else if (pathname.startsWith('/static/')) relative = pathname.slice('/static/'.length);
  if (!relative) return null;
  const filename = path.resolve(staticRoot, relative);
  if (filename !== staticRoot && !filename.startsWith(`${staticRoot}${path.sep}`)) return null;
  return fs.existsSync(filename) && fs.statSync(filename).isFile() ? filename : null;
}

function staticHeaders(filename, length) {
  return {
    'Cache-Control': 'no-store',
    'Content-Length': length,
    'Content-Type': contentType(filename),
    'X-Alexandria-Interface': `stable-${STABLE_LABEL}`,
  };
}

function serveBuffer(request, response, filename, body) {
  response.writeHead(200, staticHeaders(filename, body.length));
  if (request.method === 'HEAD') response.end();
  else response.end(body);
}

function serveStatic(request, response, staticRoot) {
  const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
  if (pathname === '/static/stable_runtime_patch.js') {
    const compatibility = path.join(ROOT, 'app', 'static', 'stable_runtime_patch.js');
    if (!fs.existsSync(compatibility)) return false;
    serveBuffer(request, response, compatibility, fs.readFileSync(compatibility));
    return true;
  }

  const filename = staticFilename(request.url, staticRoot);
  if (!filename) return false;
  if (path.basename(filename) === 'index.html') {
    const script = '<script src="/static/stable_runtime_patch.js"></script>';
    const original = fs.readFileSync(filename, 'utf8');
    const html = original.includes(script)
      ? original
      : original.includes('</body>')
        ? original.replace('</body>', `  ${script}\n</body>`)
        : `${original}\n${script}\n`;
    serveBuffer(request, response, filename, Buffer.from(html, 'utf8'));
    return true;
  }

  response.writeHead(200, staticHeaders(filename, fs.statSync(filename).size));
  if (request.method === 'HEAD') response.end();
  else fs.createReadStream(filename).pipe(response);
  return true;
}

function proxyRequest(request, response, upstream) {
  const target = new URL(request.url, upstream);
  const headers = { ...request.headers, host: target.host };
  const proxy = http.request(target, {
    method: request.method,
    headers,
  }, (upstreamResponse) => {
    const responseHeaders = {
      ...upstreamResponse.headers,
      'x-alexandria-interface': `stable-${STABLE_LABEL}`,
    };
    response.writeHead(upstreamResponse.statusCode || 502, responseHeaders);
    upstreamResponse.pipe(response);
  });
  proxy.on('error', (error) => {
    if (response.headersSent) response.destroy(error);
    else response.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' }).end(`Stable interface backend unavailable: ${error.message}`);
  });
  request.pipe(proxy);
}

function waitForUpstream(upstream, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const request = http.get(new URL('/api/projects', upstream), (response) => {
        response.resume();
        if (response.statusCode && response.statusCode < 500) resolve();
        else retry(new Error(`HTTP ${response.statusCode}`));
      });
      request.setTimeout(1000, () => request.destroy(new Error('timeout')));
      request.on('error', retry);
    };
    const retry = (error) => {
      if (Date.now() >= deadline) {
        reject(new Error(`Timed out waiting for Alexandria backend at ${upstream}: ${error.message}`));
        return;
      }
      setTimeout(attempt, 100);
    };
    attempt();
  });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const host = String(args.host || '127.0.0.1');
  const port = Number(args.port || 0);
  const upstream = new URL(String(args.upstream || 'http://127.0.0.1:4200'));
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('--port must be an integer from 0 through 65535');
  const staticRoot = args['static-root']
    ? path.resolve(String(args['static-root']))
    : ensureStableStaticRoot();
  if (!fs.existsSync(path.join(staticRoot, 'index.html'))) {
    throw new Error(`Stable static root is missing index.html: ${staticRoot}`);
  }
  await waitForUpstream(upstream);

  const server = http.createServer((request, response) => {
    if (request.url === '/__alexandria_stable_ui__') {
      response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      response.end(`${JSON.stringify({ status: 'ready', commit: STABLE_COMMIT, upstream: upstream.href })}\n`);
      return;
    }
    if (serveStatic(request, response, staticRoot)) return;
    proxyRequest(request, response, upstream);
  });
  server.on('clientError', (error, socket) => {
    socket.end('HTTP/1.1 400 Bad Request\r\n\r\n');
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, resolve);
  });
  const address = server.address();
  const url = `http://${host}:${address.port}/`;
  process.stdout.write(`Alexandria stable interface (${STABLE_LABEL}): ${url}\n`);

  let closing = false;
  const close = () => {
    if (closing) return;
    closing = true;
    server.close(() => process.exit(0));
  };
  process.once('SIGINT', close);
  process.once('SIGTERM', close);
}

main().catch((error) => {
  process.stderr.write(`Alexandria stable interface failed: ${error.message}\n`);
  process.exitCode = 1;
});
