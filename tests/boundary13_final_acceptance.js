'use strict';

async function waitFor(evaluate, client, wait, expression, attempts = 160) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    let ready = false;
    try {
      ready = await evaluate(client, expression);
    } catch (error) {
      ready = false;
    }
    if (ready) return true;
    await wait(100);
  }
  return false;
}

function readyExpression(name) {
  const expressions = {
    projects: `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'projects'
      && !document.getElementById('project-home-workspace')?.hidden
    ))()`,
    library: `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'library'
      && !document.getElementById('library-content')?.hidden
      && document.querySelectorAll('#library-artifact-list .supporting-list-row').length > 0
    ))()`,
    voices: `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'voices'
      && !document.getElementById('library-content')?.hidden
      && document.querySelectorAll('#library-artifact-list .supporting-list-row').length > 0
    ))()`,
    templates: `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'templates'
      && !document.getElementById('templates-workspace')?.hidden
      && document.querySelectorAll('#template-list [data-template-id]').length > 0
    ))()`,
    settings: `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'settings'
      && !document.getElementById('canonical-settings-form')?.hidden
    ))()`,
    more: `(() => (
      window.AlexandriaNavigation?.current()?.destination === 'more'
      && !window.AlexandriaNavigation?.current()?.context?.tool
      && !document.getElementById('more-content')?.hidden
      && document.querySelectorAll('[data-more-tool]').length >= 8
    ))()`,
    help: `(() => (
      window.AlexandriaNavigation?.current()?.context?.tool === 'help-center'
      && !document.getElementById('help-content')?.hidden
      && document.querySelectorAll('[data-help-topic]').length >= 9
    ))()`,
    maintenance: `(() => (
      window.AlexandriaNavigation?.current()?.context?.tool === 'maintenance'
      && !document.getElementById('canonical-maintenance-workspace')?.hidden
      && !document.getElementById('maintenance-content')?.hidden
    ))()`,
    advanced: `(() => (
      window.AlexandriaNavigation?.current()?.context?.tool === 'advanced-character-operations'
      && getComputedStyle(document.getElementById('speaker-management-tab')).display !== 'none'
      && Boolean(document.querySelector('#speaker-management-tab .workflow-surface'))
    ))()`,
    voiceLab: `(() => (
      window.AlexandriaNavigation?.current()?.context?.tool === 'voice-training'
      && getComputedStyle(document.getElementById('training-tab')).display !== 'none'
      && Boolean(document.querySelector('#training-tab .workflow-surface'))
    ))()`,
  };
  return expressions[name];
}

async function navigateSurface({ client, evaluate, wait, surface, width, height }) {
  await client.send('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await evaluate(client, `(() => {
    document.body.classList.remove('rail-open');
    window.AlexandriaNavigation?.navigate(
      ${JSON.stringify(surface.destination)},
      ${JSON.stringify(surface.context || {})},
      { historyMode: 'replace' }
    );
    window.scrollTo(0, 0);
  })()`);
  return waitFor(
    evaluate,
    client,
    wait,
    readyExpression(surface.name),
    surface.name === 'advanced' || surface.name === 'voiceLab' ? 180 : 140,
  );
}

async function collectSurfaceSemantics({ client, evaluate, wait, surface }) {
  await evaluate(client, `document.getElementById('main-content')?.focus({ preventScroll: true })`);
  await client.send('Input.dispatchKeyEvent', {
    type: 'keyDown',
    key: 'Tab',
    code: 'Tab',
    windowsVirtualKeyCode: 9,
  });
  await client.send('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key: 'Tab',
    code: 'Tab',
    windowsVirtualKeyCode: 9,
  });
  await wait(80);

  const dom = await evaluate(client, `(() => {
    const visible = element => {
      if (!element || element.hidden) return false;
      const closed = element.closest('details:not([open])');
      if (closed && element.closest('summary')?.parentElement !== closed) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== 'none'
        && style.visibility !== 'hidden'
        && rect.width > 0
        && rect.height > 0;
    };
    const accessibleName = element => {
      const direct = element.getAttribute('aria-label') || element.getAttribute('title');
      if (direct?.trim()) return direct.trim();
      const labelledBy = (element.getAttribute('aria-labelledby') || '')
        .split(/\\s+/)
        .filter(Boolean)
        .map(id => document.getElementById(id)?.textContent.trim() || '')
        .filter(Boolean)
        .join(' ');
      if (labelledBy) return labelledBy;
      const labels = [...(element.labels || [])]
        .map(label => label.textContent.trim())
        .filter(Boolean)
        .join(' ');
      if (labels) return labels;
      const text = element.textContent?.trim();
      if (text) return text;
      const placeholder = element.getAttribute('placeholder');
      if (placeholder?.trim()) return placeholder.trim();
      const alt = element.getAttribute('alt');
      return alt?.trim() || '';
    };
    const main = document.getElementById('main-content');
    const scope = main || document.body;
    const interactive = [...scope.querySelectorAll(
      'button, a[href], input:not([type="hidden"]), select, textarea, [role="button"], [role="option"], [tabindex]'
    )].filter(visible).filter(element => !element.disabled && element.getAttribute('aria-hidden') !== 'true');
    const unnamed = interactive
      .filter(element => !accessibleName(element))
      .map(element => ({
        tag: element.tagName,
        id: element.id || null,
        role: element.getAttribute('role') || null,
        className: String(element.className || ''),
      }));
    const visibleIds = [...document.querySelectorAll('[id]')].filter(visible);
    const idCounts = new Map();
    visibleIds.forEach(element => idCounts.set(element.id, (idCounts.get(element.id) || 0) + 1));
    const duplicateVisibleIds = [...idCounts.entries()]
      .filter(([, count]) => count > 1)
      .map(([id, count]) => ({ id, count }));
    const listboxes = [...scope.querySelectorAll('[role="listbox"]')].filter(visible).map(listbox => {
      const options = [...listbox.querySelectorAll('[role="option"]')].filter(visible);
      const selected = options.filter(option => option.getAttribute('aria-selected') === 'true');
      const roving = options.filter(option => option.tabIndex === 0);
      const activeDescendant = listbox.getAttribute('aria-activedescendant');
      return {
        id: listbox.id || null,
        optionCount: options.length,
        selectedCount: selected.length,
        rovingCount: roving.length,
        activeDescendant,
        activeDescendantExists: !activeDescendant || Boolean(document.getElementById(activeDescendant)),
        invalidSelectedValues: options
          .map(option => option.getAttribute('aria-selected'))
          .filter(value => value !== 'true' && value !== 'false'),
      };
    });
    const liveRegionElements = [...scope.querySelectorAll('[role="status"], [role="alert"], [aria-live]')];
    const liveRegions = liveRegionElements
      .filter(visible)
      .map(element => ({
        id: element.id || null,
        role: element.getAttribute('role') || null,
        live: element.getAttribute('aria-live') || null,
        text: element.textContent.trim(),
      }));
    const statusWithoutText = [...scope.querySelectorAll(
      '.maintenance-row-state, .more-tool-state, .stage-page-state, .canonical-shell-workflow-state, [role="status"], [role="alert"]'
    )].filter(visible).filter(element => !accessibleName(element)).map(element => element.id || String(element.className || ''));
    const headings = [...scope.querySelectorAll('h1, h2, h3, h4, h5, h6')]
      .filter(visible)
      .map(element => ({ level: Number(element.tagName.slice(1)), text: element.textContent.trim() }));
    const bodyText = scope.innerText || '';
    const absolutePathVisible = /(?:^|\\s)(?:\\/Users\\/|\\/private\\/|\\/tmp\\/|\\/home\\/)/m.test(bodyText);
    const rawFingerprintVisible = /\\b[a-f0-9]{64}\\b/i.test(bodyText);
    const internalIdVisible = /\\b(?:library|project|character|migration|template|operation)_[a-z0-9]{12,}\\b/i.test(bodyText);
    const active = document.activeElement;
    const activeStyle = active ? getComputedStyle(active) : null;
    const activeName = active ? accessibleName(active) : '';
    const activeRect = active?.getBoundingClientRect();
    const focusVisible = Boolean(active && active.matches(':focus-visible'));
    const focusTreatment = Boolean(
      activeStyle
      && (
        Number.parseFloat(activeStyle.outlineWidth) > 0
        || (activeStyle.boxShadow && activeStyle.boxShadow !== 'none')
        || (activeStyle.borderColor && activeStyle.borderColor !== 'rgba(0, 0, 0, 0)')
      )
    );
    return {
      destination: window.AlexandriaNavigation?.current()?.destination || null,
      tool: window.AlexandriaNavigation?.current()?.context?.tool || null,
      hash: window.AlexandriaNavigation?.current()?.hash || window.location.hash,
      pageTitle: document.body.dataset.shellMode === 'project'
        ? document.getElementById('shell-page-title')?.textContent.trim() || null
        : document.getElementById('shell-global-title')?.textContent.trim() || null,
      mainCount: document.querySelectorAll('main').length,
      visibleMainCount: [...document.querySelectorAll('main')].filter(visible).length,
      h1Count: headings.filter(item => item.level === 1).length,
      headings,
      interactiveCount: interactive.length,
      unnamedInteractive: unnamed,
      duplicateVisibleIds,
      listboxes,
      liveRegionCount: liveRegionElements.length,
      visibleLiveRegionCount: liveRegions.length,
      statusWithoutText,
      activeElement: {
        tag: active?.tagName || null,
        id: active?.id || null,
        name: activeName || null,
        focusVisible,
        focusTreatment,
        inViewport: Boolean(
          activeRect
          && activeRect.left >= -1
          && activeRect.right <= window.innerWidth + 1
          && activeRect.top >= -1
          && activeRect.bottom <= window.innerHeight + 1
        ),
      },
      ariaCurrentCount: [...document.querySelectorAll('[aria-current]')].filter(visible).length,
      dialogOpenCount: [...document.querySelectorAll('dialog[open], .modal.show')].filter(visible).length,
      filledPrimaryCount: [...scope.querySelectorAll('.btn-primary, #shell-primary-action')]
        .filter(visible).length,
      fullTransportCount: [...scope.querySelectorAll('audio[controls]')].filter(visible).length,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      absolutePathVisible,
      rawFingerprintVisible,
      internalIdVisible,
      placeholderCopyVisible: bodyText.includes('Status / Blocker') || bodyText.includes('Primary action'),
      restartRequiredVisible: /restart required|restart Alexandria/i.test(bodyText),
    };
  })()`);

  await client.send('Accessibility.enable');
  const tree = await client.send('Accessibility.getFullAXTree');
  const nodes = (tree.nodes || []).filter(node => !node.ignored);
  const roleValue = node => node.role?.value || '';
  const nameValue = node => String(node.name?.value || '').trim();
  const namedRoles = new Set([
    'button',
    'link',
    'textbox',
    'searchbox',
    'combobox',
    'checkbox',
    'radio',
    'switch',
    'slider',
  ]);
  const ax = {
    mainCount: nodes.filter(node => roleValue(node) === 'main').length,
    navigationCount: nodes.filter(node => roleValue(node) === 'navigation').length,
    headingCount: nodes.filter(node => roleValue(node) === 'heading').length,
    unnamedInteractive: nodes
      .filter(node => namedRoles.has(roleValue(node)) && !nameValue(node))
      .slice(0, 20)
      .map(node => ({ role: roleValue(node), nodeId: node.nodeId })),
    pageTitleHeadingFound: nodes.some(
      node => roleValue(node) === 'heading' && nameValue(node) === dom.pageTitle
    ),
  };
  return { name: surface.name, dom, ax };
}

async function inspectLocalizationExpansion({ client, evaluate, wait, writeScreenshot, outputDir }) {
  const surface = { name: 'more', destination: 'more', context: {} };
  await navigateSurface({ client, evaluate, wait, surface, width: 1024, height: 768 });
  await evaluate(client, `(() => {
    document.documentElement.lang = 'sv';
    const translations = [
      'Projektöversikt och pågående arbete',
      'Bibliotek och återanvändbart projektmaterial',
      'Röster och återanvändbara röstresurser',
      'Mallar för projekt och manusgenerering',
      'Inställningar och tillgänglighetsalternativ',
      'Fler avancerade verktyg och systemfunktioner',
    ];
    [...document.querySelectorAll('.alexandria-rail .app-nav-link > span:last-child')]
      .filter(element => element.offsetParent !== null)
      .forEach((element, index) => {
        element.dataset.auditOriginalText = element.textContent;
        element.textContent = translations[index % translations.length];
      });
    const title = document.getElementById('shell-global-title');
    const subtitle = document.getElementById('shell-global-subtitle');
    title.dataset.auditOriginalText = title.textContent;
    subtitle.dataset.auditOriginalText = subtitle.textContent;
    title.textContent = 'Avancerade verktyg och specialiserade arbetsflöden';
    subtitle.textContent = 'Öppna tekniska och specialiserade funktioner utan att förlora det aktuella projektets sammanhang eller återvändningsväg.';
    [...document.querySelectorAll('.more-tool-copy strong')].forEach((element, index) => {
      element.dataset.auditOriginalText = element.textContent;
      element.textContent = element.textContent + ' · specialiserad funktion för granskning och återställning ' + (index + 1);
    });
    [...document.querySelectorAll('.more-tool-copy small')].forEach(element => {
      element.dataset.auditOriginalText = element.textContent;
      element.textContent = element.textContent + ' Den här lokaliserade beskrivningen är avsiktligt längre för att verifiera responsiv textomflödning.';
    });
  })()`);
  await wait(120);
  const report = await evaluate(client, `(() => {
    const visible = element => {
      if (!element || element.hidden) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    const outOfBounds = [...document.querySelectorAll('#main-content button, #main-content input, #main-content select, #main-content textarea')]
      .filter(visible)
      .map(element => ({ element, rect: element.getBoundingClientRect() }))
      .filter(item => item.rect.left < -1 || item.rect.right > window.innerWidth + 1)
      .slice(0, 20)
      .map(item => ({
        tag: item.element.tagName,
        id: item.element.id || null,
        text: item.element.textContent.trim().slice(0, 120),
        left: item.rect.left,
        right: item.rect.right,
      }));
    return {
      lang: document.documentElement.lang,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      pageScrollHeight: document.documentElement.scrollHeight,
      viewport: { width: window.innerWidth, height: window.innerHeight },
      outOfBounds,
      title: document.getElementById('shell-global-title')?.textContent.trim() || null,
      visibleToolCount: [...document.querySelectorAll('[data-more-tool]')].filter(visible).length,
    };
  })()`);
  const screenshotPath = `${outputDir}/boundary13-final-localization-compact.png`;
  report.screenshotBytes = await writeScreenshot(client, screenshotPath);
  report.screenshotPath = screenshotPath;
  return report;
}

async function inspectLegacyRedirects({ client, evaluate, wait }) {
  const origin = await evaluate(client, 'window.location.origin');
  const cases = [
    { alias: '#library', destination: 'library', tool: null, hashPrefix: '#/library' },
    { alias: '#voices', destination: 'voices', tool: null, hashPrefix: '#/voices' },
    { alias: '#designer', destination: 'more', tool: 'voice-designer', hashPrefix: '#/more?' },
    { alias: '#project-recovery', destination: 'more', tool: 'maintenance', hashPrefix: '#/more?' },
    { alias: '#models', destination: 'more', tool: 'model-cache', hashPrefix: '#/more?' },
    { alias: '#help', destination: 'more', tool: 'help-center', hashPrefix: '#/more?' },
    { alias: '#training', destination: 'more', tool: 'voice-training', hashPrefix: '#/more?' },
    { alias: '#settings', destination: 'settings', tool: null, hashPrefix: '#/settings' },
  ];
  const reports = [];
  for (const item of cases) {
    await client.send('Page.navigate', { url: `${origin}/${item.alias}` });
    await waitFor(evaluate, client, wait, `document.readyState === 'complete'`, 160);
    await waitFor(
      evaluate,
      client,
      wait,
      `(() => {
        const route = window.AlexandriaNavigation?.current();
        return route?.destination === ${JSON.stringify(item.destination)}
          && (route?.context?.tool || null) === ${JSON.stringify(item.tool)}
          && window.location.hash.startsWith(${JSON.stringify(item.hashPrefix)});
      })()`,
      180,
    );
    reports.push(await evaluate(client, `(() => {
      const route = window.AlexandriaNavigation?.current();
      return {
        alias: ${JSON.stringify(item.alias)},
        destination: route?.destination || null,
        tool: route?.context?.tool || null,
        hash: route?.hash || null,
        locationHash: window.location.hash,
        canonicalized: !window.location.hash.startsWith(${JSON.stringify(item.alias)}),
        horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      };
    })()`));
  }
  return reports;
}

async function inspectBoundary13FinalAcceptance({
  client,
  evaluate,
  wait,
  writeScreenshot,
  outputDir,
}) {
  const surfaces = [
    { name: 'projects', destination: 'projects', context: {} },
    { name: 'library', destination: 'library', context: {} },
    { name: 'voices', destination: 'voices', context: {} },
    { name: 'templates', destination: 'templates', context: {} },
    { name: 'settings', destination: 'settings', context: {} },
    { name: 'more', destination: 'more', context: {} },
    { name: 'help', destination: 'more', context: { tool: 'help-center', topic: 'project-home' } },
    { name: 'maintenance', destination: 'more', context: { tool: 'maintenance' } },
    { name: 'advanced', destination: 'more', context: { tool: 'advanced-character-operations' } },
    { name: 'voiceLab', destination: 'more', context: { tool: 'voice-training' } },
  ];
  const surfaceReports = [];
  for (const surface of surfaces) {
    const ready = await navigateSurface({
      client,
      evaluate,
      wait,
      surface,
      width: 1536,
      height: 1024,
    });
    const report = await collectSurfaceSemantics({ client, evaluate, wait, surface });
    report.ready = ready;
    surfaceReports.push(report);
  }

  const localization = await inspectLocalizationExpansion({
    client,
    evaluate,
    wait,
    writeScreenshot,
    outputDir,
  });
  const legacyRedirects = await inspectLegacyRedirects({ client, evaluate, wait });

  await client.send('Emulation.setDeviceMetricsOverride', {
    width: 1536,
    height: 1024,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await client.send('Page.navigate', { url: `${await evaluate(client, 'window.location.origin')}/#/more` });
  await waitFor(evaluate, client, wait, readyExpression('more'), 180);
  const screenshotPath = `${outputDir}/boundary13-final-supporting-wide.png`;
  const screenshotBytes = await writeScreenshot(client, screenshotPath);

  return {
    surfaces: surfaceReports,
    localization,
    legacyRedirects,
    screenshotPath,
    screenshotBytes,
  };
}

module.exports = {
  inspectBoundary13FinalAcceptance,
};
