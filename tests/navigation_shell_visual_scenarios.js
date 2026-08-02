'use strict';

const { settle } = require('./navigation_shell_fixture.js');

async function runVisualScenarios({ session, check, snapshots }) {
  const viewportStates = [];
  for (const [width, height] of [[1536, 1024], [1440, 1000], [1024, 768], [390, 844]]) {
    await session.client.send('Emulation.setDeviceMetricsOverride', {
      width, height, deviceScaleFactor: 1, mobile: false, screenWidth: width, screenHeight: height,
    });
    await session.evaluate(`scrollTo(0, 0); dispatchEvent(new Event('resize'))`);
    await settle(session);
    const state = await session.evaluate(`(() => {
      const visible = (node) => node && !node.hidden && getComputedStyle(node).display !== 'none'
        && node.getBoundingClientRect().width > 0 && node.getBoundingClientRect().height > 0;
      const name = (node) => node.getAttribute('aria-label') || node.textContent?.trim() || '';
      const controls = [...document.querySelectorAll('button,a[href],input,[tabindex]')]
        .filter(visible).filter((node) => !node.disabled && node.tabIndex >= 0);
      return {
        viewport: innerWidth + 'x' + innerHeight,
        layout: document.querySelector('[data-app-shell]')?.dataset.layout,
        inspectorLayout: document.querySelector('[data-app-shell]')?.dataset.inspectorLayout,
        workflow: [...document.querySelectorAll('[data-stage]')].map((node) => ({
          label: node.textContent.trim(), state: node.dataset.state,
        })),
        projectVisible: visible(document.querySelector('[data-project-header]')),
        projectGroupVisible: visible(document.querySelector('[data-nav-group="project"]')),
        playerState: document.querySelector('[data-persistent-player]')?.dataset.state,
        inspectorState: document.querySelector('[data-shell-inspector]')?.dataset.state,
        h1Count: [...document.querySelectorAll('main h1')].filter(visible).length,
        unlabeledControls: controls.filter((node) => !name(node)).length,
        horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 1,
        scrollY,
      };
    })()`);
    viewportStates.push(state);
    await session.screenshot(`state-matched-shell-b-${width}x${height}.png`);
  }
  snapshots.viewportStates = viewportStates;
  check('four-viewport-state-matched-shell-b', viewportStates.every((state) => {
    const width = Number(state.viewport.split('x')[0]);
    const expectedLayout = width < 640 ? 'narrow' : width < 1200 ? 'compact' : 'wide';
    const expectedInspector = width < 1180 ? 'overlay' : 'inline';
    return state.layout === expectedLayout && state.inspectorLayout === expectedInspector
      && state.projectVisible && state.projectGroupVisible && state.playerState === 'active'
      && state.inspectorState === 'collapsed' && state.h1Count === 1 && state.unlabeledControls === 0
      && !state.horizontalOverflow && state.scrollY === 0
      && JSON.stringify(state.workflow.map((item) => item.label))
        === JSON.stringify(['Script', 'Cast', 'Produce', 'Export'])
      && state.workflow.find((item) => item.label === 'Produce')?.state === 'current';
  }), 'four accessible Shell B captures with token-derived inspector placement', viewportStates);

  await session.client.send('Emulation.setDeviceMetricsOverride', {
    width: 1448, height: 1086, deviceScaleFactor: 1, mobile: false, screenWidth: 1448, screenHeight: 1086,
  });
  await session.evaluate(`dispatchEvent(new Event('resize'))`);
  await settle(session);
  await session.screenshot('shell-b-collapsed.png');
  await session.evaluate('AlexandriaShell.inspector.open()');
  await settle(session);
  const openInspector = await session.evaluate(`(() => ({
    state: document.querySelector('[data-shell-inspector]')?.dataset.state,
    inOverlay: document.querySelector('[data-overlay-root] > [data-shell-inspector]') !== null,
    width: document.querySelector('[data-shell-inspector]')?.getBoundingClientRect().width || 0,
  }))()`);
  snapshots.openInspector = openInspector;
  check('wide-inspector-opens-inline', openInspector.state === 'open'
    && !openInspector.inOverlay && openInspector.width >= 350,
  { state: 'open', inOverlay: false, minimumWidth: 350 }, openInspector);
  await session.screenshot('shell-a-open.png');

  await session.client.send('Emulation.setDeviceMetricsOverride', {
    width: 390, height: 844, deviceScaleFactor: 1, mobile: false, screenWidth: 390, screenHeight: 844,
  });
  await session.evaluate(`scrollTo(0, 0); dispatchEvent(new Event('resize'))`);
  await session.evaluate(`AlexandriaShell.navigate('#/settings')`);
  await session.waitFor(`Boolean(document.querySelector('[data-route-owner="settings"]'))`);
  await settle(session);
  const narrow = await session.evaluate(`(() => ({
    layout: document.querySelector('[data-app-shell]')?.dataset.layout,
    inspectorLayout: document.querySelector('[data-app-shell]')?.dataset.inspectorLayout,
    scrollY,
    activeId: document.activeElement?.id,
    headingId: document.activeElement?.matches?.('[data-page-heading]')
      ? document.activeElement.id : '',
    projectGroupHidden: document.querySelector('[data-nav-group="project"]')?.hidden,
  }))()`);
  snapshots.narrow = narrow;
  check('token-derived-narrow-layout', narrow.layout === 'narrow'
    && narrow.inspectorLayout === 'overlay', { layout: 'narrow', inspector: 'overlay' }, narrow);
  check('narrow-focus-does-not-scroll-document', narrow.scrollY === 0, 0, narrow.scrollY);
  check('narrow-focus-targets-heading', Boolean(narrow.headingId)
    && narrow.activeId === narrow.headingId, narrow.headingId, narrow.activeId);
  check('project-links-remain-visible-after-global-transition', narrow.projectGroupHidden === false,
    false, narrow.projectGroupHidden);
  await session.screenshot('narrow-focus.png');

  await session.evaluate(`AlexandriaShell.navigate('#/projects', { historyMode: 'replace' })`);
  await session.waitFor(`Boolean(document.querySelector('[data-route-owner="projects"]'))`);
  await settle(session);
  await session.evaluate(`(() => {
    const spacer = document.createElement('div'); spacer.dataset.narrowFocusSpacer = '';
    spacer.style.height = '2200px'; document.querySelector('[data-route-owner]')?.append(spacer);
    document.querySelector('[data-canonical-destination-root]').scrollTo(0, 900);
  })()`);
  const narrowBeforeFocus = await session.evaluate(`({ scrollTop:
    document.querySelector('[data-canonical-destination-root]').scrollTop, headingTop:
    document.querySelector('[data-page-heading]').getBoundingClientRect().top })`);
  await session.evaluate(`AlexandriaShell.navigate('#/settings', { historyMode: 'replace' })`);
  await session.waitFor(`Boolean(document.querySelector('[data-route-owner="settings"]'))`);
  await settle(session);
  const narrowAfterFocus = await session.evaluate(`(() => {
    const heading = document.querySelector('[data-page-heading]');
    return { scrollTop: document.querySelector('[data-canonical-destination-root]').scrollTop,
      headingTop: heading.getBoundingClientRect().top,
      headingBottom: heading.getBoundingClientRect().bottom, active: document.activeElement === heading };
  })()`);
  snapshots.narrowScrolledFocus = { before: narrowBeforeFocus, after: narrowAfterFocus };
  check('narrow-scrolled-route-title-is-visible-and-focused', narrowBeforeFocus.scrollTop >= 800
    && narrowAfterFocus.scrollTop === 0 && narrowAfterFocus.headingTop >= 0
    && narrowAfterFocus.headingBottom <= 844 && narrowAfterFocus.active,
  'visible focused narrow route title', snapshots.narrowScrolledFocus);
  await session.screenshot('narrow-scrolled-focus.png');

  await session.evaluate('AlexandriaShell.inspector.open()');
  await settle(session);
  const narrowInspector = await session.evaluate(`(() => ({
    state: document.querySelector('[data-shell-inspector]')?.dataset.state,
    inOverlay: document.querySelector('[data-overlay-root] > [data-shell-inspector]') !== null,
    width: document.querySelector('[data-shell-inspector]')?.getBoundingClientRect().width || 0,
  }))()`);
  snapshots.narrowInspector = narrowInspector;
  check('narrow-inspector-opens-as-overlay', narrowInspector.state === 'overlay'
    && narrowInspector.inOverlay && narrowInspector.width >= 350,
  { state: 'overlay', inOverlay: true, minimumWidth: 350 }, narrowInspector);
  await session.screenshot('narrow-inspector-overlay.png');
  await session.evaluate('AlexandriaShell.inspector.close()');

  await session.evaluate('history.back()');
  await session.waitFor(`location.hash.startsWith('#/produce')
    && Boolean(document.querySelector('[data-route-owner="produce"]'))`);
  await settle(session);
  const back = await session.evaluate(`(() => ({
    hash: location.hash,
    projectVisible: !document.querySelector('[data-project-header]')?.hidden,
    activeId: document.activeElement?.id,
    headingId: document.activeElement?.matches?.('[data-page-heading]')
      ? document.activeElement.id : '',
  }))()`);
  await session.evaluate('history.forward()');
  await session.waitFor(`location.hash === '#/settings'
    && Boolean(document.querySelector('[data-route-owner="settings"]'))`);
  await settle(session);
  const forward = await session.evaluate(`(() => ({
    hash: location.hash,
    globalVisible: !document.querySelector('[data-global-header]')?.hidden,
    activeId: document.activeElement?.id,
    headingId: document.activeElement?.matches?.('[data-page-heading]')
      ? document.activeElement.id : '',
  }))()`);
  snapshots.history = { back, forward };
  check('history-restores-context-chrome-and-focus', back.hash.includes('project=project_meridian')
    && back.projectVisible && Boolean(back.headingId) && back.activeId === back.headingId
    && forward.hash === '#/settings' && forward.globalVisible
    && Boolean(forward.headingId) && forward.activeId === forward.headingId,
  'project Back and global Forward with heading focus', snapshots.history);
}

module.exports = { runVisualScenarios };
