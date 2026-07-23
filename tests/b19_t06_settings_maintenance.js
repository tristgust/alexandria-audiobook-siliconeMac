'use strict';

const path = require('path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');

const VIEWPORTS = [[1536, 1024], [1440, 1000], [1024, 768], [390, 844]];
const SUPPORT_ROUTES = [
  ['more', 'More'],
  ['more/advanced-character-operations', 'Advanced identity operations'],
  ['more/voice-designer', 'Voice designer'],
  ['more/audio-preparer', 'Audio preparer'],
  ['more/dataset-builder', 'Dataset builder'],
  ['more/voice-training', 'Voice Lab'],
  ['more/maintenance', 'Maintenance'],
  ['more/model-cache', 'Local model cache'],
  ['more/help-center', 'Help Center'],
];

function pageUrl(baseUrl, hash) {
  const target = new URL(baseUrl);
  target.hash = hash;
  return target.href;
}

async function settle(session) {
  await session.evaluate(`new Promise((resolve) => requestAnimationFrame(
    () => requestAnimationFrame(() => resolve(true))
  ))`);
}

async function navigate(session, hash) {
  await session.evaluate(`globalThis.AlexandriaShell.navigate(${JSON.stringify(hash)})`);
  await session.waitFor(`document.body.dataset.shellState === 'ready'`);
  await settle(session);
}

async function snapshot(session) {
  return session.evaluate(`(() => {
    const owner = document.querySelector('[data-route-owner]');
    const heading = owner?.querySelector('[data-page-heading],h1');
    const header = document.querySelector('[data-global-header]:not([hidden])');
    const box = (node) => {
      if (!node) return null;
      const rect = node.getBoundingClientRect();
      return { top: Math.round(rect.top), bottom: Math.round(rect.bottom),
        left: Math.round(rect.left), right: Math.round(rect.right) };
    };
    const text = owner?.innerText || '';
    return {
      hash: location.hash,
      routePath: document.body.dataset.routePath || '',
      routeFailure: document.body.dataset.routeFailure || '',
      owner: owner?.dataset.routeOwner || '',
      viewState: owner?.dataset.viewState || '',
      heading: heading?.textContent?.trim() || '',
      activeId: document.activeElement?.id || '',
      headingId: heading?.id || '',
      headingBox: box(heading),
      headerBox: box(header),
      ownerCount: document.querySelectorAll('[data-route-owner]').length,
      currentMore: Boolean(document.querySelector('[data-route-base="more"][aria-current="page"]')),
      overflow: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
      legacyCount: document.querySelectorAll(
        '[data-tab-panel],#legacy-settings-workspace,#legacy-tab-store,#setup-tab,#characters-tab'
      ).length,
      duplicateAssignment: [...owner?.querySelectorAll('button,a') || []]
        .filter((node) => /assign (?:to )?(?:the )?production voice/i.test(node.textContent || '')).length,
      rawInternal: /\\/Users\\/|cache_dir|config_path|root_dir|snapshot_path|content_base64|\\b[a-f0-9]{64}\\b/i.test(text),
      stateRegion: Boolean(owner?.querySelector('[data-state-region]')),
    };
  })()`);
}

async function settingsHistoryScenario(session) {
  const clickSection = async (mode, headingId) => {
    const exists = await session.evaluate(`(() => {
      const link = document.querySelector('[data-settings-section-link="${mode}"]');
      link?.click();
      return Boolean(link);
    })()`);
    if (exists) {
      await session.waitFor(`location.hash.includes('mode=${mode}')`);
      await settle(session);
    }
    const state = await snapshot(session);
    const section = await session.evaluate(`(() => {
      const heading = document.getElementById(${JSON.stringify(headingId)});
      const header = document.querySelector('[data-global-header]:not([hidden])');
      const headingBox = heading?.getBoundingClientRect();
      const headerBox = header?.getBoundingClientRect();
      return { activeId: document.activeElement?.id || '',
        headingTop: headingBox ? Math.round(headingBox.top) : null,
        headerBottom: headerBox ? Math.round(headerBox.bottom) : null };
    })()`);
    return { exists, state, section };
  };
  const provider = await clickSection('provider', 'settings-provider-heading');
  const accessibility = await clickSection('accessibility', 'settings-accessibility-heading');
  if (!provider.exists || !accessibility.exists) {
    const current = await snapshot(session);
    return {
      provider,
      accessibility,
      back: current,
      forward: current,
      save: { available: false },
      saved: '',
    };
  }
  await session.evaluate('history.back()');
  await session.waitFor(`location.hash.includes('mode=provider')`);
  await settle(session);
  const back = await snapshot(session);
  await session.evaluate('history.forward()');
  await session.waitFor(`location.hash.includes('mode=accessibility')`);
  await settle(session);
  const forward = await snapshot(session);
  const save = await session.evaluate(`(() => {
    const field = document.getElementById('settings-output-language');
    const button = document.querySelector('[data-settings-save]');
    if (!field || !button) return { available: false };
    field.value = 'Swedish';
    field.dispatchEvent(new Event('input', { bubbles: true }));
    button.click();
    return { available: true };
  })()`);
  if (save.available) {
    await session.waitFor(`document.querySelector('[data-settings-save-state]')?.dataset.state === 'saved'`);
  }
  return { provider, accessibility, back, forward, save,
    saved: await session.evaluate(`document.querySelector('[data-settings-save-state]')?.dataset.state || ''`) };
}

async function settingsDeepLinks(session) {
  const results = [];
  for (const [key, pathName, mode] of [
    ['stage_profiles', 'more/maintenance', 'llm-profiles'],
    ['runtime_diagnostics', 'more/maintenance', 'runtime'],
    ['model_cache', 'more/model-cache', ''],
    ['advanced_generation', 'more/maintenance', 'advanced-generation'],
  ]) {
    await navigate(session, '#/settings?mode=advanced');
    const exactReturn = locationHash(await session.evaluate('location.hash'));
    const clicked = await session.evaluate(`(() => {
      const link = document.querySelector('[data-settings-destination="${key}"]');
      link?.click();
      return Boolean(link);
    })()`);
    if (clicked) {
      const modeCondition = mode
        ? ` && location.hash.includes('mode=${mode}')`
        : '';
      await session.waitFor(
        `document.body.dataset.routePath === '${pathName}'${modeCondition}`,
      );
      await settle(session);
    }
    const opened = await snapshot(session);
    const returned = await session.evaluate(`(() => {
      const link = document.querySelector('[data-support-return]');
      link?.click();
      return Boolean(link);
    })()`);
    if (returned) {
      await session.waitFor(`document.body.dataset.routePath === 'settings'`);
      await settle(session);
    }
    results.push({ key, expectedOwner: pathName, clicked, opened, returned, exactReturn,
      afterReturn: await snapshot(session) });
  }
  return results;
}

function locationHash(value) {
  return String(value || '');
}

function assertion(id, pass, expected, observed) {
  return { id, pass: Boolean(pass), expected, observed };
}

async function runViewport(baseUrl, artifacts, width, height) {
  const name = `${width}x${height}`;
  const session = await BrowserSession.open({
    url: pageUrl(baseUrl, '/settings?mode=preferences'),
    artifacts: path.join(artifacts, name),
    width,
    height,
  });
  const assertions = [];
  const captures = [];
  try {
    await session.waitFor(`document.readyState === 'complete' && Boolean(globalThis.AlexandriaShell)`);
    await session.waitFor(`document.body.dataset.shellState === 'ready'`);
    const initial = await snapshot(session);
    await session.screenshot('settings.png');
    captures.push('settings.png');
    const history = initial.owner === 'settings' ? await settingsHistoryScenario(session) : null;
    const deepLinks = initial.owner === 'settings' ? await settingsDeepLinks(session) : [];
    assertions.push(
      assertion('settings-direct-owner', initial.owner === 'settings' && !initial.routeFailure,
        'settings direct owner', initial),
      assertion('settings-no-overflow-or-legacy', initial.overflow === 0 && initial.legacyCount === 0,
        { overflow: 0, legacyCount: 0 }, initial),
      assertion('settings-history-focus', Boolean(history)
        && history.provider.exists && history.accessibility.exists
        && history.provider.section.activeId === 'settings-provider-heading'
        && history.accessibility.section.activeId === 'settings-accessibility-heading'
        && history.back.activeId === 'settings-provider-heading'
        && history.forward.activeId === 'settings-accessibility-heading',
      'exact section history and focus restoration', history),
      assertion('settings-save-round-trip', history?.save.available && history.saved === 'saved',
        'existing API save reaches saved state', history?.saved || history),
      assertion('settings-specialist-deep-links', deepLinks.length === 4 && deepLinks.every((item) => (
        item.clicked && item.opened.owner === item.expectedOwner
        && item.opened.activeId.endsWith('-heading')
        && item.returned && item.afterReturn.hash === item.exactReturn
      )), 'four focused deep links with exact return', deepLinks),
    );

    const routes = [];
    for (const [routePath, heading] of SUPPORT_ROUTES) {
      await navigate(session, `#/${routePath}`);
      const state = await snapshot(session);
      routes.push(state);
      const filename = `${routePath.replaceAll('/', '-')}.png`;
      await session.screenshot(filename);
      captures.push(filename);
      assertions.push(
        assertion(`${routePath}-direct-owner`, state.owner === routePath
          && state.heading === heading && !state.routeFailure,
        { owner: routePath, heading }, state),
        assertion(`${routePath}-safe-responsive-state`, state.ownerCount === 1
          && state.overflow === 0 && state.legacyCount === 0 && state.stateRegion
          && !state.rawInternal && state.duplicateAssignment === 0,
        'one safe stateful owner with no overflow or duplicate assignment', state),
        assertion(`${routePath}-more-current`, state.currentMore, true, state.currentMore),
      );
    }
    const helpTopicClicked = await session.evaluate(`(() => {
      const topic = document.querySelectorAll('.help-topic-list [role="option"]')[1];
      topic?.click();
      return Boolean(topic);
    })()`);
    if (helpTopicClicked) {
      await session.waitFor(`location.hash.includes('topic=')`);
      await settle(session);
    }
    const helpTopicState = await snapshot(session);
    await session.screenshot('more-help-center-topic.png');
    captures.push('more-help-center-topic.png');
    assertions.push(assertion(
      'help-topic-navigation',
      helpTopicClicked && helpTopicState.owner === 'more/help-center'
        && helpTopicState.heading === 'Help Center' && !helpTopicState.routeFailure,
      'bundled topic navigation stays in Help Center',
      helpTopicState,
    ));
    const runtimeErrors = session.client.events.filter((event) => (
      event.method === 'Runtime.exceptionThrown'
      || (event.method === 'Runtime.consoleAPICalled' && event.params?.type === 'error')
    )).map((event) => event.params);
    assertions.push(assertion('no-runtime-or-console-errors', runtimeErrors.length === 0, [], runtimeErrors));
    return { viewport: name,
      status: assertions.every((item) => item.pass) ? 'PASS' : 'RED',
      assertions, captures, initial, history, deepLinks, routes };
  } catch (error) {
    assertions.push(assertion('viewport-scenario-completed', false, 'scenario completes',
      error.stack || String(error)));
    return { viewport: name, status: 'RED', assertions, captures };
  } finally {
    await session.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required(args, 'artifacts'));
  const baseUrl = required(args, 'url');
  const viewports = [];
  for (const [width, height] of VIEWPORTS) {
    viewports.push(await runViewport(baseUrl, artifacts, width, height));
  }
  const assertions = viewports.flatMap((item) => item.assertions.map((entry) => ({
    viewport: item.viewport,
    ...entry,
  })));
  const report = {
    status: assertions.length > 0 && assertions.every((item) => item.pass) ? 'PASS' : 'RED',
    viewports,
    assertions,
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  process.stdout.write(`B19_T06_SETTINGS_MAINTENANCE=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
