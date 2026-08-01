'use strict';

const {
  FAILURE_COPY, projectId, projectTitle, stageStates, projectProgress,
  routeSurface, routeLoadingSurface,
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
  let headerModel = {};
  let globalHeaderModel = {};
  let currentRoute = null;
  let lastFailure = null;
  const createSurfaces = globalThis.AlexandriaShellChromeSurfaces?.createShellSurfaces;
  if (typeof createSurfaces !== 'function') {
    throw new Error('Canonical shell surface helpers are unavailable.');
  }
  const surfaces = createSurfaces({
    app,
    UI,
    inspectorSlot,
    overlay,
    initialInspector: required('[data-shell-inspector]'),
    initialPlayer: required('[data-persistent-player]'),
    getRoute: () => currentRoute,
  });

  function focusTitle({ defer = true } = {}) {
    const apply = () => {
      const title = currentRoute?.shellMode === 'global'
        ? globalTitle
        : root.querySelector('[data-page-heading]')
          || root.querySelector('[data-route-state="loading"] [role="status"]');
      if (!title) return;
      const path = currentRoute?.path || document.body.dataset.routePath || 'destination';
      title.id = `page-heading-${path.replaceAll('/', '-')}`;
      if (root.scrollTop > 0) root.scrollTo(0, 0);
      title.tabIndex = -1;
      title.focus({ preventScroll: true });
    };
    if (defer) requestAnimationFrame(apply);
    else apply();
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
    title.textContent = headerModel.projectTitle || projectTitle(currentRoute);
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

  function renderGlobalHeader() {
    globalEyebrow.textContent = globalHeaderModel.eyebrow || 'Alexandria';
    globalTitle.textContent = globalHeaderModel.title || currentRoute?.heading || 'Alexandria';
    const path = currentRoute?.path || document.body.dataset.routePath || 'destination';
    globalTitle.id = `page-heading-${path.replaceAll('/', '-')}`;
    globalSubtitle.textContent = globalHeaderModel.subtitle || '';
    globalSubtitle.hidden = !globalHeaderModel.subtitle;
    globalActions.replaceChildren();
    const actions = (Array.isArray(globalHeaderModel.actions)
      ? globalHeaderModel.actions : [globalHeaderModel.actions])
      .filter((node) => node instanceof Node);
    const help = UI.iconButton({
      name: 'help',
      label: 'Open Help Center',
      tooltip: 'Help Center',
      onClick: () => {
        const target = routes.routeForPath('more/help-center').hash;
        if (globalThis.AlexandriaShell?.navigate) globalThis.AlexandriaShell.navigate(target);
        else location.hash = target;
      },
    });
    help.classList.add('global-help-action');
    const primaryIndex = actions.findIndex((node) => node.matches?.('.ui-button--primary'));
    if (primaryIndex < 0) globalActions.append(...actions, help);
    else globalActions.append(...actions.slice(0, primaryIndex), help, ...actions.slice(primaryIndex));
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
      status: null,
      stages: stageStates(route),
      primaryAction: null,
    };
    renderHeader();
    const currentPath = route.path.startsWith('more/') ? 'more' : route.path;
    let currentLink = null;
    for (const link of document.querySelectorAll('[data-route-link]')) {
      const base = link.dataset.routeBase || routes.parseHash(link.getAttribute('href')).path;
      const projectLink = link.closest('[data-nav-group="project"]');
      const projectScoped = projectLink && base !== 'projects';
      link.href = routes.routeForPath(base,
        projectScoped && activeProjectId ? { project: activeProjectId } : {}).hash;
      if (base === currentPath) {
        link.setAttribute('aria-current', 'page');
        currentLink = link;
      } else link.removeAttribute('aria-current');
    }
    if (app.dataset.layout === 'narrow' && currentLink) {
      requestAnimationFrame(() => currentLink.scrollIntoView({ block: 'nearest', inline: 'nearest' }));
    }
  }

  function startRoute(route) {
    surfaces.clearOverlay();
    updateChrome(route);
    lastFailure = null;
    delete document.body.dataset.routeFailure;
    document.body.dataset.shellState = 'loading';
    routeLoadingSurface({ root, route });
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
    const owner = routeSurface({
      UI,
      root,
      route,
      subtitle: copy[1],
      showHeading: false,
    });
    owner.append(UI.notice({ tone: copy[2], title: copy[0], body: copy[1], live: true }));
    focusTitle();
  }

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
      overlay: Object.freeze({
        open: surfaces.openOverlay,
        close: surfaces.clearOverlay,
      }),
      tracker: Object.freeze({ set: (states) => setHeader({ stages: states }) }),
      inspector: surfaces.inspector,
      player: Object.freeze({ set: surfaces.setPlayer }),
    }),
  });
}

globalThis.AlexandriaShellChrome = Object.freeze({ createShellChrome });
