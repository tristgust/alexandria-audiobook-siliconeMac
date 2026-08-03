'use strict';

globalThis.AlexandriaShellReady = (async () => {
  const ASSET_VERSION = new URL(import.meta.url).searchParams.get('v') || '';
  const versionedModule = (modulePath) => ASSET_VERSION
    ? `${modulePath}?v=${encodeURIComponent(ASSET_VERSION)}`
    : modulePath;
  const { createShellRuntimeState } = await import(
    versionedModule('/static/shell_runtime_state.js')
  );
  const PAGE_MODULES = Object.freeze({
    projects: '/static/pages/projects.js',
    script: '/static/pages/script.js',
    cast: '/static/pages/cast.js',
    produce: '/static/pages/produce.js',
    export: '/static/pages/export.js',
    library: '/static/pages/library.js',
    voices: '/static/pages/voices.js',
    templates: '/static/pages/templates.js',
    settings: '/static/pages/settings.js',
    more: '/static/pages/more.js',
    'more/advanced-character-operations': '/static/specialists/advanced_character_operations.js',
    'more/voice-designer': '/static/specialists/voice_designer.js',
    'more/audio-preparer': '/static/specialists/audio_preparer.js',
    'more/dataset-builder': '/static/specialists/dataset_builder.js',
    'more/voice-training': '/static/specialists/voice_training.js',
    'more/maintenance': '/static/pages/maintenance.js',
    'more/model-cache': '/static/specialists/model_cache.js',
    'more/help-center': '/static/specialists/help_center.js',
  });
  const routes = globalThis.AlexandriaRoutes;
  const api = globalThis.AlexandriaAPI;
  const UI = globalThis.AlexandriaUI;
  const projectDisplayTitle = globalThis.AlexandriaShellChromeHelpers?.projectDisplayTitle;
  const preliminaryProjectRoute = globalThis.AlexandriaShellChromeHelpers?.preliminaryProjectRoute;
  const createChrome = globalThis.AlexandriaShellChrome?.createShellChrome;
  if (!routes || !api || !UI?.appShell || !createChrome) {
    throw new Error('Canonical shell dependencies are unavailable.');
  }

  class RouteFailure extends Error {
    constructor(kind, message, cause = null) {
      super(message, cause ? { cause } : undefined);
      this.name = 'RouteFailure';
      this.kind = kind;
    }
  }

  const chrome = createChrome({ UI, routes });
  let previewFeedbackTimer = 0;
  if (document.body.dataset.previewMode === 'read-only') {
    const feedback = document.createElement('div');
    feedback.className = 'preview-action-feedback';
    feedback.hidden = true;
    feedback.setAttribute('role', 'status');
    feedback.setAttribute('aria-live', 'polite');
    document.body.append(feedback);
    document.addEventListener('alexandria:preview-blocked', (event) => {
      feedback.textContent = event.detail?.message
        || 'This repair preview is read-only. No project changes were made.';
      feedback.hidden = false;
      clearTimeout(previewFeedbackTimer);
      previewFeedbackTimer = window.setTimeout(() => { feedback.hidden = true; }, 5000);
    });
  }
  let currentController = null;
  let currentCleanup = null;
  let currentRoute = null;
  let pendingNavigation = null;
  let cleanupQueue = Promise.resolve();
  let activation = 0;
  const runtimeState = createShellRuntimeState({
    api,
    assetVersion: ASSET_VERSION,
    projectDisplayTitle,
  });
  const {
    ensureCurrentAssets,
    projectTitleCache,
    rememberProjectCatalog,
    resolveProjectRoute,
  } = runtimeState;

  function queueCleanup(cleanup) {
    const outcome = cleanupQueue.then(async () => {
      if (typeof cleanup !== 'function') return null;
      try {
        await cleanup();
        return null;
      } catch (error) {
        return new RouteFailure('cleanup', 'Page cleanup failed.', error);
      }
    });
    cleanupQueue = outcome.then(() => undefined);
    return outcome;
  }

  async function activateRoute(route) {
    const token = ++activation;
    const preliminaryRoute = preliminaryProjectRoute(route, projectTitleCache, currentRoute);
    currentController?.abort('superseded');
    const cleanup = currentCleanup;
    currentCleanup = null;
    const controller = new AbortController();
    currentController = controller;
    currentRoute = preliminaryRoute;
    chrome.startRoute(preliminaryRoute);

    const [cleanupFailure, effectiveRoute] = await Promise.all([
      queueCleanup(cleanup),
      resolveProjectRoute(route, controller.signal),
    ]);
    if (token !== activation || controller.signal.aborted) return;
    currentRoute = effectiveRoute;
    chrome.updateRoute(effectiveRoute);
    if (cleanupFailure) {
      chrome.showFailure(effectiveRoute, cleanupFailure);
      return;
    }

    try {
      const page = await runtimeState.loadPage(
        versionedModule(PAGE_MODULES[effectiveRoute.path]),
        controller.signal,
        RouteFailure,
      );
      if (token !== activation || controller.signal.aborted) return;
      if (typeof page.mount !== 'function') {
        throw new RouteFailure('module', 'Destination has no mount function.');
      }
      chrome.root.replaceChildren();
      let cleanupResult;
      try {
        const mountResult = page.mount({
          root: chrome.root,
          route: effectiveRoute,
          shell: shellApi,
          api,
          signal: controller.signal,
        });
        chrome.focusTitle({ defer: false });
        cleanupResult = await mountResult;
      } catch (error) {
        throw new RouteFailure('mount', 'Destination mount failed.', error);
      }
      if (token !== activation || controller.signal.aborted) {
        await queueCleanup(cleanupResult);
        return;
      }
      let used = false;
      currentCleanup = typeof cleanupResult === 'function' ? async () => {
        if (used) return;
        used = true;
        await cleanupResult();
      } : null;
      chrome.finishRoute();
    } catch (error) {
      const failure = error instanceof RouteFailure
        ? error : new RouteFailure('shell', 'Unexpected route failure.', error);
      if (token !== activation || controller.signal.aborted || failure.kind === 'canceled') return;
      chrome.showFailure(effectiveRoute, failure);
    }
  }

  async function navigate(value, options = {}) {
    if (!(await ensureCurrentAssets())) return;
    const route = routes.parseHash(value);
    const state = { alexandriaRoute: route.hash };
    if (options.historyMode === 'replace') history.replaceState(state, '', route.hash);
    else history.pushState(state, '', route.hash);
    await activateRoute(route);
  }

  const shellApi = Object.freeze({
    navigate,
    route: () => currentRoute,
    projectCatalog: runtimeState.projectCatalog,
    rememberProjectCatalog,
    failure: chrome.failure,
    routes,
    ...chrome.api,
  });

  function activateLocationRoute() {
    const route = routes.parseHash(location.hash);
    if (route.hash !== location.hash) {
      history.replaceState({ alexandriaRoute: route.hash }, '', route.hash);
    }
    if (pendingNavigation?.hash === route.hash
      || (currentRoute && routes.sameRoute(route, currentRoute)
        && document.body.dataset.shellState === 'ready')) {
      return Promise.resolve();
    }
    const pending = activateRoute(route);
    const navigation = { hash: route.hash, pending };
    pendingNavigation = navigation;
    return pending.finally(() => {
      if (pendingNavigation === navigation) pendingNavigation = null;
    });
  }

  document.addEventListener('click', (event) => {
    const link = event.target.closest('[data-route-link]');
    if (!link || event.defaultPrevented || event.button !== 0
      || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(link.getAttribute('href'));
  });
  window.addEventListener('popstate', activateLocationRoute);
  window.addEventListener('hashchange', activateLocationRoute);
  window.addEventListener('focus', () => { void ensureCurrentAssets(true); });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void ensureCurrentAssets(true);
  });
  globalThis.AlexandriaShell = shellApi;
  await activateLocationRoute();
  return shellApi;
})();
