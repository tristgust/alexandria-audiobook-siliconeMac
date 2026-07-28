'use strict';

const {
  FAILURE_COPY, projectId, projectTitle, stageStates, projectProgress, routeSurface,
} = globalThis.AlexandriaShellChromeHelpers;

const required = (selector) => {
  const node = document.querySelector(selector);
  if (!node) throw new Error(`Canonical shell node missing: ${selector}`);
  return node;
};

function createShellChrome({ UI, routes }) {
  const app = required('[data-app-shell]');
  const root = required('[data-canonical-destination-root]');
  const overlay = required('[data-overlay-root]');
  const inspectorSlot = required('[data-shell-inspector-slot]');
  const globalHeader = required('[data-global-header]');
  const globalEyebrow = required('[data-global-eyebrow]');
  const globalTitle = required('[data-global-title]');
  const globalSubtitle = required('[data-global-subtitle]');
  const globalActions = required('[data-global-actions]');
  const projectHeader = required('[data-project-header]');
  const projectGroup = required('[data-nav-group="project"]');
  const projectContext = required('[data-nav-project-context]');
  const projectContextLink = required('[data-nav-project-link]');
  const projectContextTitle = required('[data-nav-project-title]');
  const projectContextProgress = required('[data-nav-project-progress]');
  let player = required('[data-persistent-player]');
  let inspector = required('[data-shell-inspector]');
  let inspectorModel = { state: 'hidden', title: 'Project inspector', content: null };
  let headerModel = {};
  let globalHeaderModel = {};
  let currentRoute = null;
  let lastFailure = null;

  function focusTitle({ defer = true } = {}) {
    const apply = () => {
      const title = root.querySelector('[data-page-heading]');
      if (!title) return;
      if (!title.id) {
        const path = currentRoute?.path || document.body.dataset.routePath || 'destination';
        title.id = `page-heading-${path.replaceAll('/', '-')}`;
      }
      if (root.scrollTop > 0) root.scrollTo(0, 0);
      title.tabIndex = -1;
      title.focus({ preventScroll: true });
    };
    if (defer) requestAnimationFrame(apply);
    else apply();
  }

  function setTracker(states = {}) {
    const completeStates = new Set(['complete', 'completed', 'accepted', 'approved']);
    const blockedStates = new Set(['blocked', 'failed', 'error', 'needs_attention']);
    const activeStage = currentRoute?.destination;
    const stages = ['script', 'cast', 'produce', 'export'].map((name) => {
      const raw = String(states[name] || 'future').toLowerCase();
      let state = completeStates.has(raw) ? 'complete' : blockedStates.has(raw) ? 'blocked' : 'future';
      if (name === activeStage) state = 'current';
      return { label: `${name[0].toUpperCase()}${name.slice(1)}`, state };
    });
    const tracker = UI.stageTracker({ stages, label: 'Project stages' });
    tracker.dataset.stageTracker = '';
    [...tracker.children].forEach((step, index) => { step.dataset.stage = stages[index].label.toLowerCase(); });
    projectHeader.querySelector('.stage-tracker').replaceWith(tracker);
  }

  function helpAction() {
    const button = UI.iconButton({ name: 'help', label: 'Open Help Center', tooltip: 'Help' });
    button.classList.add('canonical-help-action');
    button.addEventListener('click', () => {
      const route = routes.routeForPath('more/help-center', {
        return: currentRoute?.hash || routes.routeForPath('projects').hash,
      });
      globalThis.AlexandriaShell?.navigate(route.hash);
    });
    return button;
  }

  function renderHeader() {
    const title = projectHeader.querySelector('.project-title');
    title.dataset.shellProjectTitle = '';
    title.replaceChildren(document.createTextNode(headerModel.projectTitle || projectTitle(currentRoute)), UI.icon('chevron'));
    const context = projectHeader.querySelector('.project-context');
    context.querySelector('[data-shell-save]')?.remove();
    const save = UI.inlineSave(headerModel.save || { state: 'saved', label: 'Saved' });
    save.dataset.shellSave = '';
    context.append(save);
    setTracker(headerModel.stages || stageStates(currentRoute || { destination: '' }));
    const actions = projectHeader.querySelector('.header-actions');
    actions.dataset.projectActions = '';
    actions.replaceChildren();
    if (headerModel.status) actions.append(UI.status({ ...headerModel.status, live: true }));
    actions.append(helpAction());
    if (headerModel.primaryAction) actions.append(UI.button({
      ...headerModel.primaryAction,
      variant: 'primary',
      onClick: headerModel.primaryAction.onClick,
    }));
  }

  function setHeader(options = {}) {
    headerModel = { ...headerModel, ...options };
    renderHeader();
  }

  function renderGlobalHeader() {
    globalEyebrow.textContent = globalHeaderModel.eyebrow || 'Alexandria';
    globalTitle.textContent = globalHeaderModel.title || currentRoute?.heading || 'Alexandria';
    globalSubtitle.textContent = globalHeaderModel.subtitle || '';
    globalSubtitle.hidden = !globalHeaderModel.subtitle;
    globalActions.replaceChildren();
    const actions = (Array.isArray(globalHeaderModel.actions)
      ? globalHeaderModel.actions : [globalHeaderModel.actions]).filter((node) => node instanceof Node);
    const primaryIndex = actions.findIndex((node) => node.classList?.contains('ui-button--primary'));
    if (primaryIndex < 0) globalActions.append(...actions, helpAction());
    else {
      actions.forEach((node, index) => {
        if (index === primaryIndex) globalActions.append(helpAction());
        globalActions.append(node);
      });
    }
  }

  function setGlobalHeader(options = {}) {
    globalHeaderModel = { ...globalHeaderModel, ...options };
    renderGlobalHeader();
  }

  function updateChrome(route) {
    currentRoute = route;
    const projectMode = route.shellMode === 'project';
    const activeProjectId = projectId(route);
    const activeProjectTitle = projectTitle(route);
    const projectStage = route.project?.current_recommended_stage || route.destination || 'script';
    globalHeader.hidden = projectMode;
    projectHeader.hidden = !projectMode;
    projectGroup.hidden = false;
    projectContext.hidden = true;
    projectContextTitle.textContent = activeProjectTitle;
    projectContextProgress.textContent = projectProgress(route, projectStage);
    projectContextLink.href = routes.routeForPath(projectStage,
      activeProjectId ? { project: activeProjectId } : {}).hash;
    projectContextLink.setAttribute('aria-label', `Open ${activeProjectTitle}`);
    document.body.dataset.destination = route.destination;
    document.body.dataset.routePath = route.path;
    document.body.dataset.routeTool = route.tool || '';
    document.body.dataset.shellMode = route.shellMode;
    document.title = `${route.heading} · Alexandria`;
    globalHeaderModel = {
      eyebrow: 'Alexandria',
      title: route.heading,
      subtitle: '',
      actions: [],
    };
    renderGlobalHeader();
    headerModel = {
      projectTitle: activeProjectTitle,
      save: { state: 'saved', label: 'Saved' },
      status: { tone: 'information', label: 'Loading' },
      stages: stageStates(route),
      primaryAction: null,
    };
    renderHeader();
    const currentPath = route.path.startsWith('more/') ? 'more' : route.path;
    for (const link of document.querySelectorAll('[data-route-link]')) {
      const base = link.dataset.routeBase || routes.parseHash(link.getAttribute('href')).path;
      const projectLink = link.closest('[data-nav-group="project"]');
      const projectScoped = projectLink && base !== 'projects';
      link.href = routes.routeForPath(base,
        projectScoped && activeProjectId ? { project: activeProjectId } : {}).hash;
      if (base === currentPath) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
  }

  function inspectorIsInline() {
    if (app.dataset.inspectorLayout) return app.dataset.inspectorLayout === 'inline';
    const token = Number.parseFloat(getComputedStyle(document.documentElement)
      .getPropertyValue('--breakpoint-inspector'));
    return innerWidth >= token;
  }

  function placeInspector() {
    const overlayMode = inspectorModel.state === 'open' && !inspectorIsInline();
    if (overlayMode) inspector.mountOverlay(overlay);
    else inspector.mountInline(inspectorSlot);
  }

  function renderInspector() {
    const overlayMode = inspectorModel.state === 'open' && !inspectorIsInline();
    const next = UI.shellInspector({
      ...inspectorModel,
      state: overlayMode ? 'overlay' : inspectorModel.state,
      label: inspectorModel.title || 'Project inspector',
      onStateChange: (state) => {
        inspectorModel = { ...inspectorModel, state: state === 'hidden' ? 'hidden' : state === 'collapsed' ? 'collapsed' : 'open' };
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
    player.replaceWith(replacement);
    player = replacement;
  }

  function startRoute(route) {
    clearOverlay();
    updateChrome(route);
    lastFailure = null;
    delete document.body.dataset.routeFailure;
    document.body.dataset.shellState = 'loading';
    routeSurface({ UI, root, route, subtitle: 'Loading destination…' });
    focusTitle({ defer: false });
  }

  function finishRoute() {
    lastFailure = null;
    document.body.dataset.destination = currentRoute.destination;
    document.body.dataset.shellState = 'ready';
    delete document.body.dataset.routeFailure;
    focusTitle();
  }

  function showFailure(route, failure) {
    lastFailure = failure;
    document.body.dataset.destination = route.destination;
    document.body.dataset.routeFailure = failure.kind;
    document.body.dataset.shellState = 'ready';
    if (route.shellMode === 'project') {
      setHeader({ status: { tone: 'error', label: 'Unavailable' }, primaryAction: null });
    }
    const copy = FAILURE_COPY[failure.kind] || FAILURE_COPY.shell;
    const owner = routeSurface({ UI, root, route, subtitle: copy[1] });
    owner.append(UI.notice({ tone: copy[2], title: copy[0], body: copy[1], live: true }));
    focusTitle();
  }

  window.addEventListener('resize', placeInspector);
  return Object.freeze({
    root,
    startRoute,
    updateRoute: updateChrome,
    finishRoute,
    showFailure,
    focusTitle,
    failure: () => lastFailure,
    api: Object.freeze({
      header: Object.freeze({ set: setHeader }),
      globalHeader: Object.freeze({ set: setGlobalHeader }),
      overlay: Object.freeze({ open: openOverlay, close: clearOverlay }),
      tracker: Object.freeze({ set: (states) => setHeader({ stages: states }) }),
      inspector: Object.freeze({
        set: setInspector,
        open: () => setInspector({ state: 'open' }),
        close: () => setInspector({ state: 'collapsed' }),
        hide: () => setInspector({ state: 'hidden', content: null }),
      }),
      player: Object.freeze({ set: setPlayer }),
    }),
  });
}

globalThis.AlexandriaShellChrome = Object.freeze({ createShellChrome });
