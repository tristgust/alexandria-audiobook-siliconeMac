const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { firefox } = require('playwright');

const REVIEW_ROOT = path.resolve(
  process.env.ALEXANDRIA_EXPANDED_SAME_SPEAKER_REVIEW_ROOT
    || '/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-e5ffde2d/.omo/evidence/b17-t37-expanded-same-speaker-round/review',
);

function contentType(file) {
  return {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.wav': 'audio/wav',
    '.json': 'application/json; charset=utf-8',
  }[path.extname(file).toLowerCase()] || 'application/octet-stream';
}

const server = http.createServer((request, response) => {
  const requested = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
  const relative = requested === '/' ? 'index.html' : requested.replace(/^\/+/, '');
  const target = path.resolve(REVIEW_ROOT, relative);
  if (!target.startsWith(REVIEW_ROOT + path.sep) && target !== REVIEW_ROOT) {
    response.writeHead(403).end('Forbidden');
    return;
  }
  fs.readFile(target, (error, bytes) => {
    if (error) {
      response.writeHead(404).end('Not found');
      return;
    }
    response.writeHead(200, {
      'Content-Type': contentType(target),
      'Content-Length': bytes.length,
    });
    response.end(bytes);
  });
});

async function closeServer() {
  if (!server.listening) return;
  await new Promise((resolve) => server.close(resolve));
}

(async () => {
  let browser;
  try {
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const port = server.address().port;
    browser = await firefox.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(`page: ${error.message}`));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(`console: ${message.text()}`);
    });
    page.on('requestfailed', (request) => errors.push(`request: ${request.url()} ${request.failure()?.errorText}`));
    await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'domcontentloaded' });
    await page.locator('.review-card').first().waitFor();
    const result = await page.evaluate(async () => {
      const audios = [...document.querySelectorAll('audio')];
      audios.forEach((audio) => audio.load());
      const deadline = Date.now() + 12000;
      while (Date.now() < deadline) {
        if (audios.every((audio) => audio.readyState >= HTMLMediaElement.HAVE_METADATA || audio.error)) break;
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      return audios.map((audio) => ({
        src: new URL(audio.src).pathname,
        readyState: audio.readyState,
        networkState: audio.networkState,
        duration: Number.isFinite(audio.duration) ? audio.duration : null,
        errorCode: audio.error?.code || null,
        errorMessage: audio.error?.message || null,
      }));
    });
    assert.strictEqual(result.length, 27);
    const failed = result.filter((row) => row.errorCode || row.readyState < 1 || !row.duration);
    console.log(JSON.stringify({ audioCount: result.length, failedCount: failed.length, failed, errors }, null, 2));
  } finally {
    if (browser) await browser.close().catch(() => {});
    await closeServer();
  }
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
