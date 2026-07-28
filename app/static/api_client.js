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

  function errorMessage(data, statusText = '') {
    const detail = data?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail.trim();
    if (detail && typeof detail === 'object') {
      const nested = detail.detail;
      const message = detail.message
        || (nested && typeof nested === 'object' ? nested.message : nested)
        || detail.code;
      if (typeof message === 'string' && message.trim()) return message.trim();
      try {
        return JSON.stringify(detail);
      } catch (_error) {
        return 'Request failed';
      }
    }
    if (typeof data?.message === 'string' && data.message.trim()) {
      return data.message.trim();
    }
    if (typeof data === 'string' && data.trim()) return data.trim();
    return statusText || 'Request failed';
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
    const requestMethod = String(options.method || 'GET').toUpperCase();
    const previewNavigation = requestMethod === 'POST'
      && /^\/api\/projects\/[^/]+\/open$/.test(path.split('?', 1)[0]);
    const previewPolicy = globalThis.AlexandriaPreviewPolicy;
    if (previewPolicy?.readOnly
      && !['GET', 'HEAD'].includes(requestMethod)
      && !previewNavigation) {
      const message = 'This is a read-only repair preview. Changes, generation, and exports are disabled.';
      previewPolicy.reportBlocked?.({ method: requestMethod, path, message });
      return failure('preview', 405, message);
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
        method: requestMethod,
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
      return failure(
        'http',
        response.status,
        errorMessage(decoded.data, response.statusText),
        decoded.data,
      );
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
