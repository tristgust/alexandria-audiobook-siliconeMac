'use strict';

const FAILURE_COPY = Object.freeze({
  missing: ['Destination unavailable', 'This destination is not installed in this build.', 'information'],
  module: ['Destination could not load', 'The destination module could not be evaluated.', 'error'],
  mount: ['Destination could not start', 'The destination stopped while preparing its workspace.', 'error'],
  cleanup: ['Previous destination could not close', 'Alexandria stopped the transition before opening another workspace.', 'error'],
  network: ['Destination check failed', 'Alexandria could not verify this destination. Check the local service and retry.', 'error'],
  shell: ['Destination failed', 'Alexandria kept the shell available, but this workspace could not open.', 'error'],
});

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
  const projectHeader = required('[data-project-header]');
  const projectGroup = required('[data-nav-group="project"]');
  let player = required('[data-persistent-player]');
  let inspector = required('[data-shell-inspector]');
  let inspectorModel = { state: 'collapsed', title: 'Project inspector', content: null };
  let headerModel = {};
  let currentRoute = null;
  let lastFailure = null;

  function routeSurface(route, subtitle) {
    const owner = document.createElement('article');
    owner.dataset.routeOwner = route.path;
    owner.dataset.page = route.path;
    const title = UI.pageTitleBlock({
      id: `page-heading-${route.path.replaceAll('/', '-')}`,
      title: route.heading,
      subtitle,
    });
    title.querySelector('h1').dataset.pageHeading = '';
    owner.append(title);
    root.replaceChildren(owner);
    return owner;
  }

  function focusTitle() {
    requestAnimationFrame(() => {
      const title = root.querySelector('[data-page-heading]');
      if (!title) return;
      if (scrollY > 0) {
        scrollTo(0, 0);
      } else if (app.dataset.layout !== 'narrow') {
        const header = projectHeader.hidden ? globalHeader : projectHeader;
        const titleBox = title.getBoundingClientRect();
        const headerBottom = header.getBoundingClientRect().bottom;
        if (titleBox.top < headerBottom || titleBox.bottom > innerHeight) scrollTo(0, 0);
      }
      title.tabIndex = -1;
      title.focus({ preventScroll: true });
    });
  }

  function stageStates(route) {
    const order = ['script', 'cast', 'produce', 'export'];
    const current = order.indexOf(route.destination);
    return Object.fromEntries(order.map((name, index) => [name,
      current < 0 ? 'future' : index < current ? 'complete' : index === current ? 'current' : 'future']));
  }

  function setTracker(states = {}) {
    const stages = ['script', 'cast', 'produce', 'export'].map((name) => ({
      label: `${name[0].toUpperCase()}${name.slice(1)}`,
      state: states[name] || 'future',
    }));
    const tracker = UI.stageTracker({ stages, label: 'Project stages' });
    tracker.dataset.stageTracker = '';
    [...tracker.children].forEach((step, index) => { step.dataset.stage = stages[index].label.toLowerCase(); });
    projectHeader.querySelector('.stage-tracker').replaceWith(tracker);
  }

  function renderHeader() {
    const title = projectHeader.querySelector('.project-title');
    title.dataset.shellProjectTitle = '';
    title.textContent = headerModel.projectTitle || currentRoute?.context.project || 'Project workspace';
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

  function updateChrome(route) {
    currentRoute = route;
    const projectMode = route.shellMode === 'project';
    globalHeader.hidden = projectMode;
    projectHeader.hidden = !projectMode;
    projectGroup.hidden = !route.context.project;
    document.body.dataset.destination = route.destination;
    document.body.dataset.routePath = route.path;
    document.body.dataset.routeTool = route.tool || '';
    document.body.dataset.shellMode = route.shellMode;
    document.title = `${route.heading} · Alexandria`;
    globalHeader.querySelector('strong').textContent = route.heading;
    headerModel = {
      projectTitle: route.context.project || 'Project workspace',
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
      link.href = routes.routeForPath(base,
        projectLink && route.context.project ? { project: route.context.project } : {}).hash;
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
        inspectorModel = { ...inspectorModel, state: state === 'collapsed' ? 'collapsed' : 'open' };
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
    inspectorModel = { ...inspectorModel, state: 'collapsed' };
    renderInspector();
    overlay.replaceChildren();
  }

  function openOverlay(node) {
    setInspector({ state: 'collapsed' });
    overlay.replaceChildren(node);
    return () => { if (node.parentElement === overlay) node.remove(); };
  }

  function setPlayer(options = {}) {
    const allowed = ['inactive', 'active', 'loading', 'playing', 'paused', 'failed'];
    const next = UI.persistentPlayer({
      ...options,
      state: allowed.includes(options.state) ? options.state : 'inactive',
    });
    next.dataset.persistentPlayer = '';
    player.replaceWith(next);
    player = next;
  }

  function startRoute(route) {
    clearOverlay();
    updateChrome(route);
    lastFailure = null;
    delete document.body.dataset.routeFailure;
    document.body.dataset.shellState = 'loading';
    routeSurface(route, 'Loading destination…');
  }

  function finishRoute() {
    lastFailure = null;
    document.body.dataset.shellState = 'ready';
    delete document.body.dataset.routeFailure;
    focusTitle();
  }

  function showFailure(route, failure) {
    lastFailure = failure;
    document.body.dataset.routeFailure = failure.kind;
    document.body.dataset.shellState = 'ready';
    if (route.shellMode === 'project') {
      setHeader({ status: { tone: 'error', label: 'Unavailable' }, primaryAction: null });
    }
    const copy = FAILURE_COPY[failure.kind] || FAILURE_COPY.shell;
    const owner = routeSurface(route, copy[1]);
    owner.append(UI.notice({ tone: copy[2], title: copy[0], body: copy[1], live: true }));
    focusTitle();
  }

  window.addEventListener('resize', placeInspector);
  return Object.freeze({
    root,
    startRoute,
    finishRoute,
    showFailure,
    failure: () => lastFailure,
    api: Object.freeze({
      header: Object.freeze({ set: setHeader }),
      overlay: Object.freeze({ open: openOverlay, close: clearOverlay }),
      tracker: Object.freeze({ set: (states) => setHeader({ stages: states }) }),
      inspector: Object.freeze({
        set: setInspector,
        open: () => setInspector({ state: 'open' }),
        close: () => setInspector({ state: 'collapsed' }),
      }),
      player: Object.freeze({ set: setPlayer }),
    }),
  });
}

globalThis.AlexandriaShellChrome = Object.freeze({ createShellChrome });
