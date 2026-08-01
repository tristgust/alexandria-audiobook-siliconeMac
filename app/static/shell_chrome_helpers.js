'use strict';

(() => {
  const FAILURE_COPY = Object.freeze({
    missing: ['Destination unavailable', 'This destination is not installed in this build.', 'information'],
    module: ['Destination could not load', 'The destination module could not be evaluated.', 'error'],
    mount: ['Destination could not start', 'The destination stopped while preparing its workspace.', 'error'],
    cleanup: ['Previous destination could not close', 'Alexandria stopped the transition before opening another workspace.', 'error'],
    network: ['Destination check failed', 'Alexandria could not verify this destination. Check the local service and retry.', 'error'],
    shell: ['Destination failed', 'Alexandria kept the shell available, but this workspace could not open.', 'error'],
  });
  const projectId = (route) => route?.projectId || route?.project?.id || route?.context?.project || '';
  const OPAQUE_PROJECT_ID = /^project_[0-9a-f]{12,}$/i;
  const projectDisplayTitle = (project, fallback = '') => {
    const raw = String(project?.name || project?.source_title || fallback || '').trim();
    if (!raw || OPAQUE_PROJECT_ID.test(raw)) return 'Project workspace';
    const filename = String(project?.source_filename || '').split('/').at(-1) || '';
    const stem = filename.replace(/\.[^.]+$/, '');
    const derived = raw === stem || String(project?.source_title || '').trim() === stem;
    if (!derived) return raw;
    const parts = stem.split(/[_-]+/).filter(Boolean);
    while (parts.length > 1 && /\d/.test(parts[0])) parts.shift();
    const readable = parts.join(' ')
      .replace(/([a-z])([A-Z])/g, '$1 $2')
      .replace(/\s+/g, ' ')
      .trim();
    return readable
      ? readable.replace(/\b\w/g, (letter) => letter.toUpperCase())
      : raw;
  };
  const projectTitle = (route) => projectDisplayTitle(
    route?.project,
    route?.projectTitle || 'Project workspace',
  );
  const preliminaryProjectRoute = (route, titleCache, currentRoute) => {
    if (route.shellMode !== 'project') return route;
    const requestedProjectId = route.context.project || '';
    const cachedTitle = titleCache.get(requestedProjectId)
      || (currentRoute?.projectId === requestedProjectId
        ? currentRoute.projectTitle
        : 'Project workspace');
    return Object.freeze({
      ...route,
      projectId: requestedProjectId,
      projectTitle: cachedTitle || 'Project workspace',
      project: currentRoute?.projectId === requestedProjectId
        ? currentRoute.project
        : null,
    });
  };
  const stageStates = (route) => {
    const order = ['script', 'cast', 'produce', 'export'];
    const current = order.indexOf(route.destination);
    return Object.fromEntries(order.map((name, index) => [name,
      current < 0 ? 'future' : index < current ? 'complete' : index === current ? 'current' : 'future']));
  };
  const projectProgress = (route, stage) => route.project?.stage_summary
    || (route.project?.blocker_count
      ? `${route.project.blocker_count} item${route.project.blocker_count === 1 ? '' : 's'} need attention`
      : `Continue in ${stage[0].toUpperCase()}${stage.slice(1)}`);
  const routeSurface = ({ UI, root, route, subtitle, showHeading = true }) => {
    const owner = document.createElement('article');
    owner.dataset.routeOwner = route.path;
    owner.dataset.page = route.path;
    if (showHeading) {
      const title = UI.pageTitleBlock({
        id: `page-heading-${route.path.replaceAll('/', '-')}`,
        title: route.heading,
        subtitle,
      });
      title.querySelector('h1').dataset.pageHeading = '';
      owner.append(title);
    } else {
      const heading = document.createElement('h1');
      heading.className = 'visually-hidden';
      heading.dataset.pageHeading = '';
      heading.textContent = route.heading;
      owner.append(heading);
    }
    root.replaceChildren(owner);
    return owner;
  };
  const routeLoadingSurface = ({ root, route }) => {
    const owner = document.createElement('article');
    owner.dataset.routeOwner = route.path;
    owner.dataset.page = route.path;
    owner.dataset.routeState = 'loading';
    owner.className = 'route-transition';
    const status = document.createElement('div');
    status.className = 'route-transition__status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    status.setAttribute('aria-atomic', 'true');
    status.setAttribute('aria-busy', 'true');
    status.tabIndex = -1;
    const spinner = document.createElement('span');
    spinner.className = 'route-transition__spinner';
    spinner.setAttribute('aria-hidden', 'true');
    const label = document.createElement('span');
    label.className = 'route-transition__label';
    label.textContent = `Loading ${route.heading}`;
    status.append(spinner, label);
    owner.append(status);
    root.replaceChildren(owner);
    return owner;
  };
  globalThis.AlexandriaShellChromeHelpers = Object.freeze({
    FAILURE_COPY, projectId, projectDisplayTitle, projectTitle,
    preliminaryProjectRoute, stageStates, projectProgress,
    routeSurface, routeLoadingSurface,
  });
})();
