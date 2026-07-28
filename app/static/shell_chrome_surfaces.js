'use strict';

function createShellSurfaces({
  app, UI, inspectorSlot, overlay, initialInspector, initialPlayer, getRoute,
}) {
  let inspector = initialInspector;
  let player = initialPlayer;
  let inspectorModel = {
    state: 'hidden', title: 'Project inspector', content: null,
  };

  function inspectorIsInline() {
    if (app.dataset.inspectorLayout) {
      return app.dataset.inspectorLayout === 'inline';
    }
    const token = Number.parseFloat(getComputedStyle(document.documentElement)
      .getPropertyValue('--breakpoint-inspector'));
    return innerWidth >= token;
  }

  function placeInspector() {
    const inline = inspectorIsInline();
    const route = getRoute();
    const inspectorRoute = route?.path === 'script' || route?.path === 'produce';
    const inlineOpen = inline && inspectorRoute && inspectorModel.state !== 'hidden';
    const overlayOpen = !inline && inspectorModel.state === 'open';
    if (inlineOpen) {
      inspector.hidden = false;
      inspector.mountInline(inspectorSlot);
      inspectorSlot.dataset.inspectorState = 'open';
      return;
    }
    if (overlayOpen) {
      inspector.hidden = false;
      inspectorSlot.dataset.inspectorState = 'hidden';
      inspector.mountOverlay(overlay);
      return;
    }
    inspector.mountInline(inspectorSlot);
    inspector.hidden = true;
    inspectorSlot.dataset.inspectorState = 'hidden';
  }

  function renderInspector() {
    const overlayMode = inspectorModel.state === 'open' && !inspectorIsInline();
    const next = UI.shellInspector({
      ...inspectorModel,
      state: overlayMode ? 'overlay' : inspectorModel.state,
      label: inspectorModel.title || 'Project inspector',
      onStateChange: (state) => {
        inspectorModel = {
          ...inspectorModel,
          state: state === 'hidden'
            ? 'hidden' : state === 'collapsed' ? 'collapsed' : 'open',
        };
        placeInspector();
      },
    });
    inspector.replaceWith(next);
    inspector = next;
    placeInspector();
  }

  function setInspector(options = {}) {
    inspectorModel = { ...inspectorModel, ...options };
    renderInspector();
  }

  function clearOverlay() {
    inspectorModel = { ...inspectorModel, state: 'hidden' };
    renderInspector();
    overlay.replaceChildren();
  }

  function openOverlay(node) {
    setInspector({ state: 'hidden' });
    overlay.replaceChildren(node);
    return () => { if (node.parentElement === overlay) node.remove(); };
  }

  function setPlayer(options = {}) {
    const allowed = ['inactive', 'active', 'loading', 'playing', 'paused', 'failed'];
    const next = UI.persistentPlayer({
      ...options,
      state: options.state === 'absent'
        ? 'absent' : allowed.includes(options.state) ? options.state : 'inactive',
    });
    const replacement = next || document.createElement('div');
    replacement.dataset.persistentPlayer = '';
    replacement.hidden = !next;
    player.playerCleanup?.();
    player.replaceWith(replacement);
    player = replacement;
    return replacement;
  }

  window.addEventListener('resize', placeInspector);
  return Object.freeze({
    clearOverlay,
    openOverlay,
    setPlayer,
    inspector: Object.freeze({
      set: setInspector,
      open: () => setInspector({ state: 'open' }),
      close: () => setInspector({ state: 'collapsed' }),
      hide: () => setInspector({ state: 'hidden', content: null }),
    }),
  });
}

globalThis.AlexandriaShellChromeSurfaces = Object.freeze({ createShellSurfaces });
