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
  const projectTitle = (route) => route?.projectTitle || route?.project?.name
    || route?.project?.source_title || route?.context?.project || 'Project workspace';
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
  const routeSurface = ({ UI, root, route, subtitle }) => {
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
  };
  globalThis.AlexandriaShellChromeHelpers = Object.freeze({
    FAILURE_COPY, projectId, projectTitle, stageStates, projectProgress, routeSurface,
  });
})();
