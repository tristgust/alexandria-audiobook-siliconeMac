'use strict';

const path = require('path');
const { BrowserSession } = require('./b19_t06_bootstrap_red.js');
const { assertion, fixtureServer, settle } = require('./navigation_shell_fixture.js');
const { runVisualScenarios } = require('./navigation_shell_visual_scenarios.js');

async function earlyDependencyContract(artifacts) {
  const fixture = await fixtureServer();
  fixture.control.failPath = '/static/components/notice.js';
  const session = await BrowserSession.open({
    url: fixture.url,
    artifacts: path.join(artifacts, 'early-dependency'),
    width: 1024,
    height: 768,
  });
  try {
    await session.waitFor(`document.readyState === 'complete'`);
    const observed = await session.evaluate(`(() => {
      const fallback = document.querySelector('[data-bootstrap-error]');
      const box = fallback?.getBoundingClientRect();
      return {
        shellState: document.body.dataset.shellState,
        visible: Boolean(fallback && !fallback.hidden && box.width > 0 && box.height > 0),
        heading: fallback?.querySelector('h1')?.textContent || '',
        alert: fallback?.querySelector('[role="alert"]')?.textContent || '',
        focused: document.activeElement === fallback?.querySelector('h1'),
      };
    })()`);
    await session.screenshot('early-classic-dependency-failure.png');
    const assertions = [
      assertion('early-classic-dependency-has-visible-fallback', observed.visible, true, observed),
      assertion('early-classic-dependency-is-truthful', observed.heading.includes('could not start')
        && observed.alert.includes('project files were not changed'), 'truthful recovery copy', observed),
      assertion('early-classic-dependency-focuses-fallback', observed.focused
        && observed.shellState === 'bootstrap-error', true, observed),
    ];
    return { status: assertions.every((item) => item.pass) ? 'PASS' : 'RED', assertions, observed };
  } finally {
    await session.close();
    await fixture.close();
  }
}

async function browserContract(artifacts) {
  const fixture = await fixtureServer();
  const session = await BrowserSession.open({
    url: `${fixture.url}#/projects`, artifacts, width: 1448, height: 1086,
  });
  const assertions = [];
  const snapshots = {};
  const check = (id, pass, expected, observed) => assertions.push(assertion(id, pass, expected, observed));
  try {
    await session.waitFor(`document.readyState === 'complete' && Boolean(globalThis.AlexandriaShell)`);
    await session.waitFor(`Boolean(document.querySelector('[data-route-owner="projects"]'))`);
    const initial = await session.evaluate(`(() => ({
      factories: [...document.querySelectorAll('[data-production-factory]')]
        .map((node) => node.dataset.productionFactory),
      projectGroupHidden: document.querySelector('[data-nav-group="project"]')?.hidden,
      groups: Object.fromEntries([...document.querySelectorAll('[data-nav-group]')].map((group) => [
        group.dataset.navGroup,
        [...group.querySelectorAll('[data-route-link]')].map((node) => node.textContent.trim()),
      ])),
      overlayCount: document.querySelector('[data-overlay-root]')?.childElementCount,
      shellApis: Object.keys(globalThis.AlexandriaShell || {}),
    }))()`);
    snapshots.initial = initial;
    for (const factory of ['appShell', 'navRail', 'globalHeader', 'projectHeader', 'persistentPlayer', 'shellInspector']) {
      check(`factory-${factory}`, initial.factories.includes(factory), factory, initial.factories);
    }
    check('navigation-groups-have-canonical-order', JSON.stringify(initial.groups)
      === JSON.stringify({ project: ['Script', 'Cast', 'Produce', 'Export'],
        global: ['Home', 'Library', 'Voices', 'Templates'], system: ['Settings', 'More'] }),
    'canonical grouped navigation', initial.groups);
    check('project-navigation-hidden-without-context', initial.projectGroupHidden === true, true, initial.projectGroupHidden);
    await session.evaluate(`document.body.tabIndex = -1; document.body.focus()`);
    await session.client.send('Input.dispatchKeyEvent', {
      type: 'rawKeyDown', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9,
    });
    await session.client.send('Input.dispatchKeyEvent', {
      type: 'keyUp', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9,
    });
    const skip = await session.evaluate(`(() => {
      const link = document.querySelector('.skip-link');
      const rect = link.getBoundingClientRect();
      document.body.removeAttribute('tabindex');
      return { focused: document.activeElement === link, width: rect.width, height: rect.height,
        clipped: getComputedStyle(link).clip, focusVisible: link.matches(':focus-visible') };
    })()`);
    check('skip-link-is-visible-when-focused', skip.focused && skip.width > 1
      && skip.height >= 32 && skip.clipped === 'auto' && skip.focusVisible,
    'visible keyboard-focused skip link', skip);
    check('transient-overlay-opened', initial.overlayCount === 1, 1, initial.overlayCount);

    fixture.control.delayedHead = '/static/pages/cast.js';
    await session.evaluate(`globalThis.__castNavigation = AlexandriaShell.navigate('#/cast?project=project_meridian'); true`);
    await fixture.waitForReceipt((item) => item.method === 'HEAD' && item.path === '/static/pages/cast.js');
    const duringCast = await session.evaluate(`(() => ({
      destination: document.body.dataset.destination,
      shellState: document.body.dataset.shellState,
      projectVisible: !document.querySelector('[data-project-header]')?.hidden,
      projectGroupVisible: !document.querySelector('[data-nav-group="project"]')?.hidden,
      overlayCount: document.querySelector('[data-overlay-root]')?.childElementCount,
      title: document.title,
    }))()`);
    snapshots.duringCast = duringCast;
    check('chrome-updates-before-module-fetch', duringCast.destination === 'cast'
      && duringCast.projectVisible && duringCast.projectGroupVisible && duringCast.title.startsWith('Cast'),
    'Cast project chrome during pending HEAD', duringCast);
    check('overlay-clears-at-route-start', duringCast.overlayCount === 0, 0, duringCast.overlayCount);

    await session.evaluate(`AlexandriaShell.navigate('#/produce?project=project_meridian')`);
    await session.waitFor(`Boolean(document.querySelector('[data-route-owner="produce"]'))`);
    await fixture.waitForReceipt((item) => item.method === 'HEAD'
      && item.path === '/static/pages/cast.js' && item.aborted);
    await settle(session);
    const lifecycle = await session.evaluate(`(() => ({
      events: globalThis.__shellFixture?.events || [],
      activeId: document.activeElement?.id || '',
      headingId: document.querySelector('[data-page-heading]')?.id || '',
      inspectorState: document.querySelector('[data-shell-inspector]')?.dataset.state || '',
      playerState: document.querySelector('[data-persistent-player]')?.dataset.state || '',
      primaryActions: document.querySelectorAll('[data-project-actions] .ui-button--primary').length,
      saveText: document.querySelector('[data-project-header] [data-production-factory="inlineSave"]')?.textContent || '',
      statusText: document.querySelector('[data-project-header] [data-production-factory="status"]')?.textContent || '',
    }))()`);
    snapshots.lifecycle = lifecycle;
    const cleanups = lifecycle.events.filter((event) => event.type === 'cleanup-complete' && event.path === 'projects');
    check('successful-cleanup-exactly-once', cleanups.length === 1, 1, cleanups.length);
    check('superseded-delayed-route-never-mounted', !lifecycle.events.some(
      (event) => event.type === 'mount-complete' && event.path === 'cast'), false, lifecycle.events);
    check('delayed-head-request-aborted', true, true, fixture.receipts);
    check('focus-and-project-shell-state', lifecycle.activeId === lifecycle.headingId
      && lifecycle.primaryActions === 1 && lifecycle.saveText === 'Saved'
      && lifecycle.statusText === 'Ready' && lifecycle.playerState === 'active'
      && lifecycle.inspectorState === 'collapsed', 'settled project shell state', lifecycle);
    fixture.control.delayedHead = null;

    await session.evaluate(`(() => {
      const spacer = document.createElement('div'); spacer.dataset.focusSpacer = '';
      spacer.style.height = '2200px'; document.querySelector('[data-route-owner]')?.append(spacer);
      document.querySelector('[data-canonical-destination-root]').scrollTo(0, 900);
    })()`);
    const beforeFocus = await session.evaluate(`({ scrollTop:
      document.querySelector('[data-canonical-destination-root]').scrollTop, headingTop:
      document.querySelector('[data-page-heading]').getBoundingClientRect().top })`);
    await session.evaluate(`AlexandriaShell.navigate('#/settings')`);
    await session.waitFor(`Boolean(document.querySelector('[data-route-owner="settings"]'))`);
    await settle(session);
    const afterFocus = await session.evaluate(`(() => {
      const heading = document.querySelector('[data-page-heading]');
      return { scrollTop: document.querySelector('[data-canonical-destination-root]').scrollTop,
        headingTop: heading.getBoundingClientRect().top,
        headingBottom: heading.getBoundingClientRect().bottom, active: document.activeElement === heading };
    })()`);
    snapshots.scrolledFocus = { before: beforeFocus, after: afterFocus };
    check('scrolled-route-title-is-visible-and-focused', beforeFocus.scrollTop >= 800
      && afterFocus.scrollTop === 0 && afterFocus.headingTop >= 0
      && afterFocus.headingBottom <= 1086 && afterFocus.active, 'visible focused route title', snapshots.scrolledFocus);

    await session.evaluate(`AlexandriaShell.navigate('#/produce?project=project_meridian')`);
    await session.waitFor(`Boolean(document.querySelector('[data-route-owner="produce"]'))`);
    await runVisualScenarios({ session, check, snapshots });

    await session.evaluate(`Object.assign(globalThis.__shellFixture,
      { delayMountFor: 'cast', failCleanupFor: 'cast' })`);
    await session.evaluate(`globalThis.__slowMount = AlexandriaShell.navigate('#/cast?project=project_meridian'); true`);
    await session.waitFor(`globalThis.__shellFixture.events.some(
      (event) => event.type === 'mount-start' && event.path === 'cast')`);
    await session.evaluate(`AlexandriaShell.navigate('#/produce?project=project_meridian')`);
    await session.waitFor(`Boolean(document.querySelector('[data-route-owner="produce"]'))`);
    await session.evaluate(`globalThis.__shellFixture.releaseMount()`);
    await session.waitFor(`globalThis.__shellFixture.events.some(
      (event) => event.type === 'cleanup-failure' && event.path === 'cast')`);
    await session.evaluate(`AlexandriaShell.navigate('#/settings')`);
    await session.waitFor(`Boolean(document.querySelector('[data-route-owner="settings"]'))
      && !document.body.dataset.routeFailure`);
    const staleCleanup = await session.evaluate(`({
      owner: document.querySelector('[data-route-owner]')?.dataset.routeOwner,
      failure: document.body.dataset.routeFailure || '', events: globalThis.__shellFixture.events })`);
    snapshots.staleCleanup = staleCleanup;
    check('stale-cleanup-failure-does-not-poison-later-route', staleCleanup.owner === 'settings'
      && staleCleanup.failure === '', { owner: 'settings', failure: '' }, staleCleanup);
    await session.evaluate(`Object.assign(globalThis.__shellFixture,
      { delayMountFor: '', failCleanupFor: '' })`);

    await session.evaluate(`globalThis.__shellFixture.failCleanupFor = 'settings'`);
    await session.evaluate(`AlexandriaShell.navigate('#/projects')`);
    await session.waitFor(`document.body.dataset.routeFailure === 'cleanup'`);
    const cleanupFailure = await session.evaluate(`({
      kind: document.body.dataset.routeFailure,
      title: document.querySelector('[data-route-owner] .notice__title')?.textContent || '',
      overlayCount: document.querySelector('[data-overlay-root]')?.childElementCount || 0 })`);
    snapshots.cleanupFailure = cleanupFailure;
    check('current-cleanup-failure-is-distinct-visible-and-cleared', cleanupFailure.kind === 'cleanup'
      && cleanupFailure.title.toLowerCase().includes('close') && cleanupFailure.overlayCount === 0,
    'visible cleanup failure with neutral overlay', cleanupFailure);
    await session.evaluate(`globalThis.__shellFixture.failCleanupFor = ''`);

    for (const [hash, kind] of [
      ['#/more/voice-designer', 'missing'],
      ['#/more/audio-preparer', 'module'],
      ['#/more/dataset-builder', 'mount'],
    ]) {
      await session.evaluate(`AlexandriaShell.navigate(${JSON.stringify(hash)})`);
      await session.waitFor(`document.body.dataset.routeFailure === ${JSON.stringify(kind)}`);
      const observed = await session.evaluate(`({ kind: AlexandriaShell.failure()?.kind || '',
        current: [...document.querySelectorAll('[data-route-link][aria-current="page"]')]
          .map((node) => node.dataset.routeBase) })`);
      check(`failure-taxonomy-${kind}`, observed.kind === kind, kind, observed.kind);
      check(`specialist-parent-current-${kind}`, JSON.stringify(observed.current) === JSON.stringify(['more']),
        ['more'], observed.current);
    }
  } catch (error) {
    assertions.push(assertion('browser-scenario-completed', false, 'scenario completes', error.stack || String(error)));
  } finally {
    await session.close();
    await fixture.close();
  }
  return {
    status: assertions.length > 0 && assertions.every((item) => item.pass) ? 'PASS' : 'RED',
    assertions,
    snapshots,
    requests: fixture.receipts,
  };
}

module.exports = { browserContract, earlyDependencyContract };
