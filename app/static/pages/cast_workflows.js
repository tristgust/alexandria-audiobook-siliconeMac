'use strict';

const UI = globalThis.AlexandriaUI;
const FOCUSABLE = 'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';
const TOOLS = Object.freeze({
  'advanced-character-operations': {
    title: 'Full Cast tasks',
    module: '/static/specialists/advanced_character_operations.js',
    presentation: 'modal',
  },
  'voice-designer': {
    title: 'Voice designer',
    module: '/static/specialists/voice_designer.js',
  },
  'audio-preparer': {
    title: 'Audio preparer',
    module: '/static/specialists/audio_preparer.js',
  },
  'dataset-builder': {
    title: 'Dataset builder',
    module: '/static/specialists/dataset_builder.js',
  },
  'voice-training': {
    title: 'Voice Lab',
    module: '/static/specialists/voice_training.js',
  },
});

export function createCastWorkflows({
  shell, api, signal, projectId, getSelected, routeForTool, onRefresh,
}) {
  let releaseOverlay = null;
  let workflowAbort = null;
  let workflowCleanup = null;
  let returnFocus = null;
  let activeTool = null;
  let refreshOnClose = false;

  const close = ({ restoreFocus = true, refresh = refreshOnClose } = {}) => {
    const release = releaseOverlay;
    workflowAbort?.abort('workflow closed');
    workflowCleanup?.();
    workflowAbort = null;
    workflowCleanup = null;
    releaseOverlay = null;
    activeTool = null;
    const restore = () => {
      if (!restoreFocus) return;
      const target = returnFocus?.isConnected
        ? returnFocus : document.querySelector('[data-cast-more]');
      target?.focus();
    };
    if (refresh) {
      Promise.resolve(onRefresh?.()).finally(() => {
        release?.();
        requestAnimationFrame(restore);
      });
    } else {
      release?.();
      restore();
    }
  };

  const mountTool = async (tool, root, route, layer) => {
    const definition = TOOLS[tool];
    root.replaceChildren(UI.skeleton({ label: `Loading ${definition.title}` }));
    try {
      const module = await import(definition.module);
      if (workflowAbort.signal.aborted) return;
      const workflowShell = {
        ...shell,
        navigate(destination, options = {}) {
          if (destination === route.hash) {
            refreshOnClose = true;
            workflowCleanup?.();
            mountTool(tool, root, route, layer);
            return;
          }
          if (destination === route.context.return || String(destination).startsWith('#/cast')) {
            close();
            return;
          }
          shell.navigate(destination, options);
        },
      };
      workflowCleanup = await module.mount({
        root,
        route,
        shell: workflowShell,
        api,
        signal: workflowAbort.signal,
      });
      if (workflowAbort.signal.aborted) return;
      layer.querySelector('h1, h2, button')?.focus({ preventScroll: true });
    } catch (error) {
      if (workflowAbort?.signal.aborted) return;
      root.replaceChildren(UI.notice({
        tone: 'error',
        title: `${definition.title} could not open`,
        body: String(error?.message || error),
        live: true,
      }));
    }
  };

  const open = (tool, opener = document.activeElement, context = {}) => {
    const definition = TOOLS[tool];
    const selected = getSelected();
    if (!definition || !selected) return;
    close({ restoreFocus: false, refresh: false });
    activeTool = tool;
    refreshOnClose = false;
    returnFocus = opener;
    workflowAbort = new AbortController();
    const abort = () => workflowAbort?.abort(signal.reason);
    if (signal.aborted) abort();
    else signal.addEventListener('abort', abort, { once: true });

    const layer = document.createElement('div');
    layer.className = 'cast-workflow-layer';
    layer.dataset.castWorkflow = tool;
    layer.dataset.presentation = definition.presentation || 'drawer';
    layer.setAttribute('role', 'dialog');
    layer.setAttribute('aria-modal', 'true');
    layer.setAttribute('aria-label', `${definition.title} for ${selected.display_name}`);
    const surface = document.createElement('section');
    surface.className = definition.presentation === 'modal'
      ? 'cast-workflow-drawer cast-workflow-modal'
      : 'cast-workflow-drawer';
    const closeButton = UI.iconButton({
      name: 'close',
      label: `Close ${definition.title}`,
      tooltip: 'Close',
      onClick: () => close(),
    });
    closeButton.classList.add('cast-workflow-drawer__close');
    closeButton.dataset.castWorkflowClose = '';
    const root = document.createElement('div');
    root.className = 'cast-workflow-drawer__content';
    surface.append(closeButton, root);
    layer.append(surface);
    layer.addEventListener('mousedown', (event) => {
      if (event.target === layer) close();
    });
    layer.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = [...layer.querySelectorAll(FOCUSABLE)]
        .filter((node) => node.getBoundingClientRect().width > 0);
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    });
    releaseOverlay = shell.overlay.open(layer);
    const route = routeForTool(`more/${tool}`, context);
    mountTool(tool, root, route, layer);
    requestAnimationFrame(() => closeButton.focus());
  };

  return Object.freeze({
    open,
    close,
    get activeTool() { return activeTool; },
    cleanup() { close({ restoreFocus: false, refresh: false }); },
  });
}
