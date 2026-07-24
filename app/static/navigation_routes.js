'use strict';

(function exposeRoutes(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AlexandriaRoutes = api;
})(typeof globalThis === 'undefined' ? this : globalThis, () => {
  const route = (destination, heading, shellMode = 'global', tool = null) => (
    Object.freeze({ destination, heading, shellMode, tool })
  );
  const ROUTES = Object.freeze({
    projects: route('projects', 'Project Home'),
    script: route('script', 'Script', 'project'),
    cast: route('cast', 'Characters', 'project'),
    produce: route('produce', 'Produce', 'project'),
    export: route('export', 'Export', 'project'),
    library: route('library', 'Library'),
    voices: route('voices', 'Voices'),
    templates: route('templates', 'Templates'),
    settings: route('settings', 'Settings'),
    more: route('more', 'More'),
    'more/advanced-character-operations': route('more', 'Advanced identity operations', 'global', 'advanced-character-operations'),
    'more/voice-designer': route('more', 'Voice designer', 'global', 'voice-designer'),
    'more/audio-preparer': route('more', 'Audio preparer', 'global', 'audio-preparer'),
    'more/dataset-builder': route('more', 'Dataset builder', 'global', 'dataset-builder'),
    'more/voice-training': route('more', 'Voice Lab', 'global', 'voice-training'),
    'more/maintenance': route('more', 'Maintenance', 'global', 'maintenance'),
    'more/model-cache': route('more', 'Local model cache', 'global', 'model-cache'),
    'more/help-center': route('more', 'Help Center', 'global', 'help-center'),
  });
  const ALIASES = Object.freeze({
    setup: 'projects',
    characters: 'cast',
    'voice-casting': 'cast',
    'voice-projects': 'cast',
    editor: 'produce',
    audio: 'export',
    result: 'export',
    'speaker-management': 'more/advanced-character-operations',
    speakers: 'more/advanced-character-operations',
    designer: 'more/voice-designer',
    preparer: 'more/audio-preparer',
    'dataset-builder': 'more/dataset-builder',
    training: 'more/voice-training',
    'project-recovery': 'more/maintenance',
    recovery: 'more/maintenance',
    models: 'more/model-cache',
    help: 'more/help-center',
    'help-center': 'more/help-center',
  });
  const CONTEXT_KEYS = Object.freeze([
    'project', 'character', 'chunk', 'chapter', 'issue', 'source',
    'mode', 'filter', 'search', 'help', 'topic', 'return',
  ]);
  const CONTEXT_KEY_SET = new Set(CONTEXT_KEYS);
  const MAX_CONTEXT_LENGTH = 512;
  const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f]/;

  function safeValue(value) {
    if (value == null) return null;
    const text = String(value).trim();
    return text && text.length <= MAX_CONTEXT_LENGTH && !CONTROL_CHARACTERS.test(text)
      ? text : null;
  }

  function normalizeContext(value) {
    const source = value && typeof value === 'object' ? value : {};
    const context = {};
    for (const key of CONTEXT_KEYS) {
      const safe = safeValue(source[key]);
      if (safe !== null) context[key] = safe;
    }
    return context;
  }

  function queryContext(rawQuery) {
    const context = {};
    for (const [key, value] of new URLSearchParams(rawQuery)) {
      if (!CONTEXT_KEY_SET.has(key) || key in context) continue;
      const safe = safeValue(value);
      if (safe !== null) context[key] = safe;
    }
    return context;
  }

  function cleanPath(value) {
    const raw = String(value || '').replace(/^#/, '').replace(/^\//, '').split('?')[0];
    try {
      return decodeURIComponent(raw).trim().toLowerCase().replace(/\/+$/, '');
    } catch (_error) {
      return '';
    }
  }

  function serialize(path, context) {
    const params = new URLSearchParams();
    for (const key of CONTEXT_KEYS) {
      if (context[key] !== undefined) params.set(key, context[key]);
    }
    const query = params.toString();
    return `#/${path}${query ? `?${query}` : ''}`;
  }

  function routeForPath(pathValue, contextValue = {}, receipt = {}) {
    const requested = cleanPath(pathValue) || 'projects';
    const aliasUsed = Object.prototype.hasOwnProperty.call(ALIASES, requested)
      ? requested : null;
    const path = aliasUsed ? ALIASES[requested]
      : Object.prototype.hasOwnProperty.call(ROUTES, requested) ? requested : 'projects';
    const definition = ROUTES[path];
    const context = Object.freeze(normalizeContext(contextValue));
    return Object.freeze({
      path,
      destination: definition.destination,
      tool: definition.tool,
      context,
      hash: serialize(path, context),
      title: definition.heading,
      heading: definition.heading,
      shellMode: definition.shellMode,
      aliasUsed: receipt.aliasUsed ?? aliasUsed,
      unrecognized: receipt.unrecognized ?? (path === 'projects' && requested !== 'projects' && !aliasUsed ? requested : null),
    });
  }

  function parseHash(hashValue) {
    const raw = String(hashValue || '').replace(/^#/, '').replace(/^\//, '');
    const question = raw.indexOf('?');
    const requested = cleanPath(question < 0 ? raw : raw.slice(0, question)) || 'projects';
    const context = queryContext(question < 0 ? '' : raw.slice(question + 1));
    const aliasUsed = Object.prototype.hasOwnProperty.call(ALIASES, requested) ? requested : null;
    const unrecognized = !aliasUsed && !Object.prototype.hasOwnProperty.call(ROUTES, requested)
      ? requested : null;
    return routeForPath(requested, context, { aliasUsed, unrecognized });
  }

  function normalizeRoute(value) {
    if (typeof value === 'string') return parseHash(value);
    const source = value && typeof value === 'object' ? value : {};
    if (source.path) return routeForPath(source.path, source.context || source);
    const toolPath = source.destination === 'more' && safeValue(source.tool || source.context?.tool);
    return routeForPath(toolPath ? `more/${source.tool || source.context.tool}` : source.destination, source.context || source);
  }

  function routeForDestination(destination, context = {}) {
    return normalizeRoute({ destination, context, tool: context.tool });
  }

  function withContext(routeValue, changes) {
    const current = normalizeRoute(routeValue);
    return routeForPath(current.path, { ...current.context, ...normalizeContext(changes) });
  }

  function withoutContext(routeValue, keys) {
    const current = normalizeRoute(routeValue);
    const context = { ...current.context };
    for (const key of Array.isArray(keys) ? keys : [keys]) delete context[key];
    return routeForPath(current.path, context);
  }

  function sameRoute(left, right) {
    return normalizeRoute(left).hash === normalizeRoute(right).hash;
  }

  return Object.freeze({
    ROUTES, ALIASES, CONTEXT_KEYS, parseHash, normalizeContext, normalizeRoute,
    routeForPath, routeForDestination, withContext, withoutContext, sameRoute,
  });
});
