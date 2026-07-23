'use strict';

(function exposeApi(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AlexandriaAPI = api;
})(typeof globalThis === 'undefined' ? this : globalThis, () => {
  const DEFAULT_TIMEOUT = 20000;
  const MAX_PATH_LENGTH = 2048;
  const JSON_CONTENT = /(?:application\/json|application\/[^;]+\+json)(?:\s*;|$)/i;

  function failure(kind, status, error, data = null) {
    return Object.freeze({ ok: false, status, kind, error, data });
  }

  function containsTraversal(path) {
    return path.replaceAll('\\', '/').split('/')
      .some((segment) => segment === '..' || segment === '.');
  }

  function apiPath(value) {
    const raw = String(value || '');
    const rawPath = raw.split(/[?#]/, 1)[0];
    if (rawPath.length > MAX_PATH_LENGTH) {
      throw new TypeError('Alexandria API paths cannot exceed 2048 characters.');
    }
    const origin = typeof location === 'object' && location.origin
      ? location.origin : 'http://alexandria.local';
    let url;
    try {
      url = new URL(raw, `${origin}/`);
    } catch (error) {
      if (error instanceof TypeError) {
        throw new TypeError('Alexandria API requests require a valid /api/ path.');
      }
      throw error;
    }
    let decodedPath;
    let expandedRawPath = rawPath;
    try {
      decodedPath = decodeURIComponent(url.pathname);
      while (true) {
        const expanded = decodeURIComponent(expandedRawPath);
        if (expanded === expandedRawPath) break;
        expandedRawPath = expanded;
      }
    } catch (error) {
      if (error instanceof URIError) {
        throw new TypeError('Alexandria API requests cannot contain malformed encoding.');
      }
      throw error;
    }
    const directApiPath = url.pathname === '/api' || url.pathname.startsWith('/api/');
    const decodedApiPath = decodedPath === '/api' || decodedPath.startsWith('/api/');
    if (!raw.startsWith('/') || raw.startsWith('//') || raw.includes('\\')
      || url.origin !== origin || url.username || url.password || url.hash
      || !directApiPath || !decodedApiPath || containsTraversal(decodedPath)
      || containsTraversal(expandedRawPath)) {
      throw new TypeError('Alexandria API requests must use a same-origin /api/ path.');
    }
    return `${url.pathname}${url.search}`;
  }

  async function decode(response) {
    if (response.status === 204) return { data: null };
    const raw = await response.text();
    if (!raw) return { data: null };
    const contentType = response.headers.get('content-type') || '';
    if (!JSON_CONTENT.test(contentType)) return { data: raw };
    try {
      return { data: JSON.parse(raw) };
    } catch (error) {
      if (error instanceof SyntaxError) return { data: raw, malformed: true };
      throw error;
    }
  }

  async function request(pathValue, options = {}) {
    let path;
    try {
      path = apiPath(pathValue);
    } catch (error) {
      if (!(error instanceof TypeError)) throw error;
      return failure('validation', 0, error.message);
    }
    const controller = new AbortController();
    const timeout = Number.isFinite(options.timeout)
      ? Math.max(0, options.timeout) : DEFAULT_TIMEOUT;
    let adapterTimedOut = false;
    const onAbort = () => controller.abort(options.signal?.reason);
    if (options.signal?.aborted) onAbort();
    else options.signal?.addEventListener('abort', onAbort, { once: true });
    const timer = setTimeout(() => {
      adapterTimedOut = true;
      controller.abort(new DOMException('Request timed out', 'TimeoutError'));
    }, timeout);
    const headers = new Headers(options.headers || {});
    let body = options.body;
    const prototype = body != null && typeof body === 'object'
      ? Object.getPrototypeOf(body) : undefined;
    const structured = body != null && typeof body === 'object'
      && (Array.isArray(body) || prototype === Object.prototype || prototype === null);
    if (structured) {
      body = JSON.stringify(body);
      if (!headers.has('content-type')) headers.set('content-type', 'application/json');
    }
    try {
      const response = await fetch(path, {
        method: options.method || 'GET',
        credentials: 'same-origin',
        headers,
        body,
        signal: controller.signal,
      });
      const decoded = await decode(response);
      if (decoded.malformed) {
        return failure('decode', response.status,
          `Malformed JSON response (${response.status})`, decoded.data);
      }
      if (response.ok) return Object.freeze({ ok: true, status: response.status, data: decoded.data });
      const message = decoded.data?.detail || decoded.data?.message
        || (typeof decoded.data === 'string' && decoded.data.trim())
        || response.statusText || 'Request failed';
      return failure('http', response.status, String(message), decoded.data);
    } catch (error) {
      if (adapterTimedOut) return failure('timeout', 0, 'Request timed out');
      if (controller.signal.aborted) return failure('canceled', 0, 'Request canceled');
      return failure('network', 0, String(error?.message || error));
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener('abort', onAbort);
    }
  }

  const method = (name) => (path, body, options = {}) => request(path, {
    ...options,
    method: name,
    ...(body === undefined ? {} : { body }),
  });

  return Object.freeze({
    request,
    get: (path, options) => request(path, options),
    post: method('POST'),
    put: method('PUT'),
    patch: method('PATCH'),
    delete: (path, options) => request(path, { ...options, method: 'DELETE' }),
  });
});
