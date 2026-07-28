'use strict';

const fs = require('fs');
const http = require('http');
const path = require('path');

const MIME = Object.freeze({
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.wav': 'audio/wav',
});

function argumentsFrom(values) {
  const args = {};
  for (let index = 0; index < values.length; index += 1) {
    if (!values[index].startsWith('--')) continue;
    const key = values[index].slice(2);
    args[key] = values[index + 1] && !values[index + 1].startsWith('--')
      ? values[++index]
      : true;
  }
  return args;
}

function sendJson(response, status, payload) {
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'x-alexandria-preview': 'read-only',
  });
  response.end(JSON.stringify(payload));
}

function staticFile(staticRoot, pathname) {
  const file = pathname === '/'
    ? path.join(staticRoot, 'index.html')
    : path.join(staticRoot, pathname.replace(/^\/static\//, ''));
  const resolved = path.resolve(file);
  const root = path.resolve(staticRoot);
  if (resolved !== path.join(root, 'index.html') && !resolved.startsWith(`${root}${path.sep}`)) return null;
  return resolved;
}

async function syntheticProjectOpen(requestUrl, response, upstream) {
  const match = requestUrl.pathname.match(/^\/api\/projects\/([^/]+)\/open$/);
  if (!match) return false;
  const projectId = decodeURIComponent(match[1]);
  const catalogResponse = await fetch(new URL('/api/projects', upstream));
  if (!catalogResponse.ok) {
    sendJson(response, 502, { detail: `Could not read project catalog: HTTP ${catalogResponse.status}` });
    return true;
  }
  const catalog = await catalogResponse.json();
  const project = (catalog.projects || []).find((item) => item.id === projectId);
  if (!project) {
    sendJson(response, 404, { detail: 'Project not found in the preview catalog.' });
    return true;
  }
  const destination = project.current_recommended_stage || 'script';
  sendJson(response, 200, {
    preview_read_only: true,
    project,
    activation: { state: 'preview', native_destination: destination },
    native_destination: destination,
  });
  return true;
}

async function proxyApi(request, requestUrl, response, upstream) {
  if (!['GET', 'HEAD'].includes(request.method)) {
    if (request.method === 'POST' && await syntheticProjectOpen(requestUrl, response, upstream)) return;
    sendJson(response, 405, {
      detail: 'This is a read-only reference-fidelity preview. Changes are disabled.',
    });
    return;
  }
  const target = new URL(`${requestUrl.pathname}${requestUrl.search}`, upstream);
  const upstreamResponse = await fetch(target, {
    method: request.method,
    headers: { accept: request.headers.accept || '*/*' },
  });
  const headers = {
    'cache-control': 'no-store',
    'x-alexandria-preview': 'read-only',
  };
  const contentType = upstreamResponse.headers.get('content-type');
  if (contentType) headers['content-type'] = contentType;
  response.writeHead(upstreamResponse.status, headers);
  if (request.method === 'HEAD') response.end();
  else response.end(Buffer.from(await upstreamResponse.arrayBuffer()));
}

async function main() {
  const args = argumentsFrom(process.argv.slice(2));
  const host = String(args.host || '127.0.0.1');
  const port = Number(args.port || 0);
  const upstream = new URL(String(args.upstream || 'http://127.0.0.1:4200'));
  const staticRoot = path.resolve(args['static-root'] || path.join(__dirname, '..', 'app', 'static'));
  const server = http.createServer(async (request, response) => {
    const requestUrl = new URL(request.url, `http://${host}`);
    try {
      if (requestUrl.pathname === '/__preview/status') {
        sendJson(response, 200, {
          ready: true,
          mode: 'read-only-old-build-fidelity',
          upstream: upstream.origin,
        });
        return;
      }
      if (requestUrl.pathname.startsWith('/api/')) {
        await proxyApi(request, requestUrl, response, upstream);
        return;
      }
      if (requestUrl.pathname !== '/' && !requestUrl.pathname.startsWith('/static/')) {
        response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
        response.end('Not found');
        return;
      }
      const file = staticFile(staticRoot, requestUrl.pathname);
      if (!file || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
        response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
        response.end('Not found');
        return;
      }
      let body = fs.readFileSync(file);
      if (requestUrl.pathname === '/') {
        body = Buffer.from(body.toString('utf8').replace(
          '<title>Alexandria Audiobook</title>',
          '<title>Alexandria · Old-build reference preview</title>',
        ));
      }
      response.writeHead(200, {
        'content-type': MIME[path.extname(file)] || 'application/octet-stream',
        'cache-control': 'no-store',
        'x-alexandria-preview': 'read-only',
      });
      if (request.method === 'HEAD') response.end();
      else response.end(body);
    } catch (error) {
      sendJson(response, 502, { detail: String(error?.message || error) });
    }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, resolve);
  });
  const address = server.address();
  process.stdout.write(`Alexandria old-build reference preview: http://${host}:${address.port}\n`);
  const close = () => server.close(() => process.exit(0));
  process.once('SIGINT', close);
  process.once('SIGTERM', close);
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
