'use strict';

async function captureInitialShell(session) {
  return session.evaluate(`(() => ({
    factories: [...document.querySelectorAll('[data-production-factory]')]
      .map((node) => node.dataset.productionFactory),
    projectGroupHidden: document.querySelector('[data-nav-group="project"]')?.hidden,
    groups: Object.fromEntries([...document.querySelectorAll('[data-nav-group]')].map((group) => [
      group.dataset.navGroup,
      [...group.querySelectorAll('[data-route-link]')].map((node) => node.textContent.trim()),
    ])),
    overlayCount: document.querySelector('[data-overlay-root]')?.childElementCount,
    sidebarIcons: [...document.querySelectorAll('.nav-icon > :first-child')].map((node) => ({
      tag: node.tagName,
      className: node.getAttribute('class') || '',
    })),
    shellApis: Object.keys(globalThis.AlexandriaShell || {}),
  }))()`);
}

async function exerciseLoadingState({ session, fixture, check, snapshots }) {
  fixture.control.delayedHead = '/static/pages/cast.js';
  await session.evaluate(`globalThis.__castNavigation = AlexandriaShell.navigate('#/cast?project=project_meridian'); true`);
  await fixture.waitForReceipt((item) => item.method === 'HEAD' && item.path === '/static/pages/cast.js');
  const observed = await session.evaluate(`(() => {
    const transition = document.querySelector('[data-route-state="loading"]');
    const spinner = transition?.querySelector('.route-transition__spinner');
    const label = transition?.querySelector('.route-transition__label');
    const animatedElementCount = transition
      ? [...transition.querySelectorAll('*')].filter(
        (node) => getComputedStyle(node).animationName !== 'none',
      ).length
      : 0;
    return {
      destination: document.body.dataset.destination,
      shellState: document.body.dataset.shellState,
      projectVisible: !document.querySelector('[data-project-header]')?.hidden,
      projectGroupVisible: !document.querySelector('[data-nav-group="project"]')?.hidden,
      overlayCount: document.querySelector('[data-overlay-root]')?.childElementCount,
      title: document.title,
      projectTitle: document.querySelector('[data-shell-project-title]')?.textContent || '',
      transitionCount: document.querySelectorAll('[data-route-state="loading"]').length,
      visibleLoadingPageTitles: [...document.querySelectorAll('[data-route-state="loading"] .page-title-block')]
        .filter((node) => node.getBoundingClientRect().height > 0).length,
      dotCount: transition?.querySelectorAll('.route-transition__dots').length || 0,
      labelText: label?.textContent || '',
      labelColor: label ? getComputedStyle(label).color : '',
      labelAnimation: label ? getComputedStyle(label).animationName : '',
      spinnerAnimation: spinner ? getComputedStyle(spinner).animationName : '',
      statusOutline: transition
        ? getComputedStyle(transition.querySelector('[role="status"]')).outlineStyle
        : '',
      animatedElementCount,
    };
  })()`);
  snapshots.duringCast = observed;
  check('chrome-updates-before-module-fetch', observed.destination === 'cast'
    && observed.projectVisible && observed.projectGroupVisible && observed.title.startsWith('Characters'),
  'Characters project chrome during pending HEAD', observed);
  check('overlay-clears-at-route-start', observed.overlayCount === 0, 0, observed.overlayCount);
  check('project-id-never-leaks-into-loading-title', observed.projectTitle === 'Project Meridian'
    && !observed.projectTitle.includes('project_meridian'), 'cached human-readable project title', observed);
  check('route-loading-is-one-calm-readable-instrument', observed.transitionCount === 1
    && observed.labelText === 'Loading Characters'
    && observed.labelColor !== 'rgba(0, 0, 0, 0)'
    && observed.visibleLoadingPageTitles === 0
    && observed.dotCount === 0
    && observed.labelAnimation === 'none'
    && observed.spinnerAnimation !== 'none'
    && observed.statusOutline === 'none'
    && observed.animatedElementCount === 1,
  'one animated ring with static readable copy and no redundant header', observed);
  await session.screenshot('route-loading-single-instrument.png');
  await session.client.send('Emulation.setEmulatedMedia', {
    features: [{ name: 'prefers-reduced-motion', value: 'reduce' }],
  });
  const reduced = await session.evaluate(`(() => {
    const spinner = document.querySelector('.route-transition__spinner');
    return {
      spinnerAnimation: spinner ? getComputedStyle(spinner).animationName : '',
      label: document.querySelector('.route-transition__label')?.textContent || '',
    };
  })()`);
  snapshots.reducedMotionLoading = reduced;
  check('route-loading-honors-reduced-motion', reduced.spinnerAnimation === 'none'
    && reduced.label === 'Loading Characters',
  'static readable loading status under reduced motion', reduced);
  await session.client.send('Emulation.setEmulatedMedia', { features: [] });
  return observed;
}

module.exports = { captureInitialShell, exerciseLoadingState };
